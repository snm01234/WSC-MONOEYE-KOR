#!/usr/bin/env python3
"""Build a manifest-grounded inventory of battle dialogue and ID-command output.

The raw script database contains graphics/control data that can decode as kana,
so it is not a safe translation population.  This scanner instead starts from
the Original-ROM-derived reviewed population in ``main_p1_base_manifest.json``
and adds only maintained battle/ID catalog addresses.

For every live-TIP record it evaluates both the complete payload and, where the
leading code unit was historically ambiguous, a one-code-unit-stripped body.
That separates:

* real Japanese/mixed bodies that need translation;
* Korean bodies with one potentially visible Japanese leading glyph, which
  require the runtime barcode probe before the prefix is removed;
* already clean records;
* short/shared-token and non-target 5C records that remain quarantined.

Read-only with respect to ROM and SaveRAM.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import looks_like_jp
from find_aux_text_tables import coherent
from measure_aux_prefix_rule import code_units
from mixed_residual_classification import (
    core_character_count,
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import Dictionary, Tbl, find_rom, load_rom, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MANIFEST = ROOT / "out/patch/main_p1_base_manifest.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_JSON = ROOT / "out/patch/current_tip_battle_id_output_inventory.json"
OUT_CSV = ROOT / "out/script/current_tip_battle_id_output_inventory.csv"
ACTIONABLE_CSV = ROOT / "out/script/current_tip_battle_id_actionable.csv"
PROBE_CSV = ROOT / "out/script/current_tip_battle_id_prefix_probe.csv"
TRANSLATION_CSV = ROOT / "out/script/current_tip_battle_id_translation_queue.csv"

REFERENCE_CATALOGS = (
    ROOT / "data/battle_dialogue_prefix_cleanup_ko.json",
    ROOT / "data/battle_id_command_followup_ko.json",
    ROOT / "data/battle_ui_action_labels_ko.json",
    ROOT / "data/id_indirect_ui_activation_ko.json",
    ROOT / "data/mixed_residual_translations.json",
    ROOT / "data/scouting_map_postbattle_dialogue_ko.json",
    ROOT / "data/ui_battle_terms_ko.json",
)
EXACT_SCOPE_CATALOGS = {
    "data/battle_dialogue_prefix_cleanup_ko.json",
    "data/battle_id_command_followup_ko.json",
    "data/battle_ui_action_labels_ko.json",
    "data/id_indirect_ui_activation_ko.json",
    "data/scouting_map_postbattle_dialogue_ko.json",
    "data/ui_battle_terms_ko.json",
}
TARGET_BANKS = {0x59, 0x5C, 0x5D, 0x5E, 0x60, 0x61, 0x62, 0x63}
INCLUDE_EXCLUSION_PREFIXES = (
    "excluded_prefix_unprovable",
    "excluded_shared_token_body_capacity",
    "excluded_aux_below_core_threshold",
    "excluded_non_linguistic_fragment",
)
ID_TERMS = (
    "ＩＤ", "ID", "コマンド", "ミノフスキ", "間接", "射撃", "散布",
    "커맨드", "미노프스키", "산포", "살포", "간접", "사격",
)
SPACE_RE = re.compile(r"[\s\u3000]+")
REPEAT_RE = re.compile(r"(.)\1{5,}")

FIELDS = [
    "abs", "bank", "scope", "manifest_status", "manifest_reason",
    "classification", "auto_fix_ready", "probe_needed", "prefix_hex",
    "payload_capacity", "body_capacity", "original_full_text",
    "original_body_text", "current_full_text", "current_body_text",
    "current_stripped_text", "japanese_count", "hangul_count", "core_count",
    "suggested_ko", "suggested_source", "source_group_size",
    "source_group_korean_variants", "original_payload_hex",
    "current_payload_hex", "source_body_sha256", "current_body_sha256", "notes",
]


class InventoryError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve())
    return {"path": shown, "size": len(payload), "sha256": sha(payload)}


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InventoryError(f"JSON root must be object: {path}")
    return value


def normalize_text(value: str) -> str:
    return SPACE_RE.sub(" ", value.strip())


def valid_korean(value: str) -> bool:
    return bool(value.strip()) and hangul_character_count(value) > 0 and japanese_character_count(value) == 0


def iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from iter_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from iter_dicts(nested)


def catalog_path(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def load_catalog_references() -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]], dict[int, dict[str, str]]]:
    by_address: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    by_source: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    exact_scope: dict[int, dict[str, str]] = {}
    for path in REFERENCE_CATALOGS:
        if not path.is_file():
            continue
        shown = catalog_path(path)
        for row in iter_dicts(load_object(path)):
            ko = str(row.get("ko") or "").strip()
            if not valid_korean(ko):
                continue
            review = str(row.get("review_status") or "approved")
            if review not in {"approved", "", "not_needed_false_mixed"}:
                continue
            source = str(
                row.get("jp") or row.get("source_text") or row.get("original")
                or row.get("original_jp") or ""
            ).strip()
            address = str(
                row.get("abs") or row.get("record_start") or row.get("logical") or ""
            ).replace("0x", "").replace("0X", "").strip().upper()
            ref = {"ko": ko, "source": source, "catalog": shown}
            if address and all(ch in "0123456789ABCDEF" for ch in address):
                address = f"{int(address, 16):06X}"
                by_address[address].append(ref)
                if shown in EXACT_SCOPE_CATALOGS:
                    exact_scope[int(address, 16)] = {
                        "catalog": shown,
                        "category": str(row.get("category") or row.get("scope") or "maintained_battle_id"),
                        "prefix_hex": str(row.get("prefix_hex") or "").replace(" ", "").upper(),
                    }
            if source:
                by_source[normalize_text(source)].append(ref)
    return by_address, by_source, exact_scope


def unique_reference(refs: Iterable[dict[str, str]]) -> tuple[str, str] | None:
    grouped: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for ref in refs:
        ko = str(ref.get("ko") or "").strip()
        if valid_korean(ko):
            grouped[normalize_text(ko)].append(ref)
    if len(grouped) != 1:
        return None
    rows = next(iter(grouped.values()))
    return rows[0]["ko"], rows[0]["catalog"]


def read_original_z(original: bytes, logical: int, max_len: int = 256) -> bytes | None:
    got = read_encoded_z_safe(original, stock_base(original) + logical, max_len=max_len)
    return bytes(got[0]) if got else None


def fixed_payload(rom: bytes, logical: int, capacity: int) -> tuple[bytes, bool]:
    base = stock_base(rom)
    start = base + logical
    end = start + capacity
    if start < 0 or end >= len(rom):
        return b"", False
    return bytes(rom[start:end]), rom[end] == 0


def decode(dictionary: Any, payload: bytes, tbl: Tbl) -> str:
    return dictionary.expand(payload, tbl).rstrip("\u3000 \t")


def first_unit_len(payload: bytes) -> int:
    units = code_units(payload)
    return units[0][1] if units else 0


def sentence_like(text: str) -> bool:
    if not text or "<BAD" in text or "ロ助" in text or REPEAT_RE.search(text):
        return False
    core = core_character_count(text)
    return core >= 3 and (coherent(text) or looks_like_jp(text))


def scope_for(logical: int, original: str, current: str, exact: dict[int, dict[str, str]]) -> str:
    if logical in exact:
        category = exact[logical]["category"].lower()
        if "id" in category or "command" in category or "shoot" in category or "indirect" in category:
            return "id_command"
        return "battle_dialogue"
    bank = logical >> 16
    combined = f"{original} {current}"
    if any(term in combined for term in ID_TERMS):
        return "id_command"
    if bank in {0x5D, 0x5E}:
        return "battle_voice"
    if bank == 0x59:
        return "event_dialogue"
    if bank == 0x5C:
        return "bank5c_nonbattle_review"
    if bank in {0x60, 0x61, 0x62, 0x63}:
        return "script_dialogue"
    return "other"


def target_scope(scope: str) -> bool:
    return scope in {"battle_dialogue", "battle_voice", "id_command", "event_dialogue", "script_dialogue"}


def collect_manifest_records(manifest: dict[str, Any], exact: dict[int, dict[str, str]], original: bytes) -> dict[int, dict[str, Any]]:
    population = manifest.get("population") or {}
    records: dict[int, dict[str, Any]] = {}
    for status, rows in (("included", population.get("included") or []), ("excluded", population.get("excluded") or [])):
        for row in rows:
            logical = int(row.get("logical_address") or int(str(row.get("abs") or "0"), 16))
            if (logical >> 16) not in TARGET_BANKS:
                continue
            reason = str(row.get("reason") or "")
            if status == "excluded" and not reason.startswith(INCLUDE_EXCLUSION_PREFIXES):
                continue
            boundary = row.get("boundary") or {}
            capacity = int(boundary.get("payload_capacity") or 0)
            payload = read_original_z(original, logical)
            if not payload or not capacity:
                continue
            if len(payload) != capacity:
                # Boundaries, not a fresh NUL walk, remain authoritative.
                base = stock_base(original) + logical
                payload = bytes(original[base : base + capacity])
            manifest_prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
            exact_meta = exact.get(logical)
            exact_prefix = bytes.fromhex(str((exact_meta or {}).get("prefix_hex") or ""))
            records[logical] = {
                "logical": logical,
                "capacity": capacity,
                "original_payload": payload,
                "manifest_status": status,
                "manifest_reason": reason,
                "manifest_prefix": exact_prefix or manifest_prefix,
                "record_id": str(row.get("record_id") or f"manifest:{logical:06X}"),
            }
    for logical, meta in exact.items():
        if logical in records:
            records[logical]["exact_scope"] = meta
            continue
        payload = read_original_z(original, logical)
        if payload:
            records[logical] = {
                "logical": logical,
                "capacity": len(payload),
                "original_payload": payload,
                "manifest_status": "exact_catalog",
                "manifest_reason": meta["category"],
                "manifest_prefix": bytes.fromhex(str(meta.get("prefix_hex") or "")),
                "record_id": f"exact:{logical:06X}",
                "exact_scope": meta,
            }
    return records


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    parser.add_argument("--actionable-csv", type=Path, default=ACTIONABLE_CSV)
    parser.add_argument("--probe-csv", type=Path, default=PROBE_CSV)
    parser.add_argument("--translation-csv", type=Path, default=TRANSLATION_CSV)
    args = parser.parse_args(argv)

    tip = bytes(load_rom(args.tip))
    original_path = find_rom(ROOT)
    original = bytes(load_rom(original_path))
    manifest = load_object(MANIFEST)
    tbl = Tbl.load(TBL_PATH)
    od = Dictionary(original)
    cd = make_dictionary_ext3(tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    by_address, by_source, exact_scope = load_catalog_references()
    records = collect_manifest_records(manifest, exact_scope, original)

    decoded: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for logical, source in sorted(records.items()):
        original_payload = bytes(source["original_payload"])
        current_payload, terminator_ok = fixed_payload(tip, logical, int(source["capacity"]))
        if not current_payload or not terminator_ok:
            skipped.append({"abs": f"{logical:06X}", "reason": "fixed boundary or terminator mismatch"})
            continue
        prefix = bytes(source.get("manifest_prefix") or b"")
        prefix_ok = bool(prefix) and original_payload.startswith(prefix) and current_payload.startswith(prefix)
        body_offset = len(prefix) if prefix_ok else 0
        ambiguous = str(source["manifest_reason"]).startswith("excluded_prefix_unprovable:ambiguous_leading_byte")
        # Included 5D/5E records historically lacked trusted prefix evidence;
        # evaluate a single voice-id unit without assuming it is non-printing.
        if (logical >> 16) in {0x5D, 0x5E} and not prefix:
            ambiguous = True
        unit = first_unit_len(current_payload) if ambiguous else 0
        try:
            original_full = decode(od, original_payload, tbl)
            current_full = decode(cd, current_payload, tbl)
            original_body = decode(od, original_payload[body_offset:], tbl)
            current_body = decode(cd, current_payload[body_offset:], tbl)
            original_stripped = decode(od, original_payload[unit:], tbl) if unit else ""
            current_stripped = decode(cd, current_payload[unit:], tbl) if unit else ""
        except Exception as exc:  # noqa: BLE001
            skipped.append({"abs": f"{logical:06X}", "reason": f"decode:{type(exc).__name__}"})
            continue
        scope = scope_for(logical, original_body, current_body, exact_scope)
        decoded.append({
            **source,
            "abs": f"{logical:06X}",
            "bank": f"{logical >> 16:02X}",
            "current_payload": current_payload,
            "prefix": prefix if prefix_ok else b"",
            "body_offset": body_offset,
            "ambiguous": ambiguous,
            "unit": unit,
            "original_full": original_full,
            "current_full": current_full,
            "original_body": original_body,
            "current_body": current_body,
            "original_stripped": original_stripped,
            "current_stripped": current_stripped,
            "scope": scope,
        })

    # Unanimous Korean render of the same Original source can safely seed a
    # duplicate, but conflicting variants never auto-patch.
    current_by_source: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    source_sizes: collections.Counter[str] = collections.Counter()
    for row in decoded:
        key = normalize_text(str(row["original_body"]))
        if key:
            source_sizes[key] += 1
        for candidate in (str(row["current_body"]), str(row["current_stripped"])):
            if key and valid_korean(candidate):
                current_by_source[key].append({"ko": candidate, "catalog": "current_tip_unanimous_duplicate"})

    output: list[dict[str, Any]] = []
    counts: collections.Counter[str] = collections.Counter()
    by_scope_counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for row in decoded:
        logical = int(row["logical"])
        current_body = str(row["current_body"])
        current_full = str(row["current_full"])
        current_stripped = str(row["current_stripped"])
        original_body = str(row["original_body"])
        display_text = current_body
        source_text = original_body
        probe_needed = False
        notes: list[str] = []

        body_jp = japanese_character_count(current_body)
        body_ko = hangul_character_count(current_body)
        full_jp = japanese_character_count(current_full)
        stripped_jp = japanese_character_count(current_stripped) if row["unit"] else 0
        stripped_ko = hangul_character_count(current_stripped) if row["unit"] else 0

        classification = "clean_korean_or_nontext"
        if row["ambiguous"] and row["unit"]:
            if full_jp > 0 and stripped_jp == 0 and stripped_ko > 0:
                classification = "visible_leading_glyph_probe"
                probe_needed = True
                display_text = current_stripped
                source_text = str(row["original_stripped"])
            elif stripped_jp > 0:
                display_text = current_stripped
                source_text = str(row["original_stripped"])
                classification = "true_mixed_body" if stripped_ko else (
                    "untranslated_body_sentence" if sentence_like(source_text) else "jp_body_fragment_review"
                )
            elif full_jp > 0:
                classification = "ambiguous_full_payload_japanese_probe"
                probe_needed = True
            elif stripped_ko > 0 or body_ko > 0:
                classification = "clean_korean_body"
        else:
            if body_jp and body_ko:
                classification = "true_mixed_body"
            elif body_jp:
                classification = "untranslated_body_sentence" if sentence_like(source_text) else "jp_body_fragment_review"
            elif body_ko:
                classification = "clean_korean_body"

        if str(row["manifest_reason"]).startswith("excluded_non_linguistic_fragment"):
            classification = "short_control_fragment_quarantine"
            probe_needed = False
        elif not target_scope(str(row["scope"])):
            classification = "non_target_scope_quarantine"
            probe_needed = False

        key = normalize_text(source_text)
        refs: list[dict[str, str]] = []
        refs.extend(by_address.get(str(row["abs"]), []))
        refs.extend(by_source.get(key, []))
        refs.extend(current_by_source.get(key, []))
        suggestion = unique_reference(refs)
        suggested_ko = suggestion[0] if suggestion else ""
        suggested_source = suggestion[1] if suggestion else ""
        korean_variants = sorted({normalize_text(ref["ko"]) for ref in current_by_source.get(key, [])})

        actionable_class = classification in {"true_mixed_body", "untranslated_body_sentence", "jp_body_fragment_review"}
        auto_fix = actionable_class and bool(suggested_ko) and not probe_needed
        if actionable_class and not suggested_ko:
            notes.append("new Korean translation required")
        if probe_needed:
            notes.append("runtime barcode evidence required before changing the leading code unit")
        if len(korean_variants) > 1:
            notes.append("duplicate Korean variants conflict")
        if str(row["manifest_reason"]).startswith("excluded_shared_token_body_capacity"):
            notes.append("shared/short token: candidate must verify all consumers")
        if classification == "jp_body_fragment_review":
            notes.append("short fragment: screen or consumer evidence required")

        body_bytes = bytes(row["current_payload"])[int(row["unit"]):] if row["ambiguous"] and row["unit"] else bytes(row["current_payload"])[int(row["body_offset"]):]
        source_bytes = bytes(row["original_payload"])[int(row["unit"]):] if row["ambiguous"] and row["unit"] else bytes(row["original_payload"])[int(row["body_offset"]):]
        out = {
            "abs": row["abs"],
            "bank": row["bank"],
            "scope": row["scope"],
            "manifest_status": row["manifest_status"],
            "manifest_reason": row["manifest_reason"],
            "classification": classification,
            "auto_fix_ready": "yes" if auto_fix else "no",
            "probe_needed": "yes" if probe_needed else "no",
            "prefix_hex": bytes(row["prefix"]).hex().upper(),
            "payload_capacity": row["capacity"],
            "body_capacity": len(body_bytes),
            "original_full_text": row["original_full"],
            "original_body_text": source_text,
            "current_full_text": current_full,
            "current_body_text": display_text,
            "current_stripped_text": current_stripped,
            "japanese_count": japanese_character_count(display_text),
            "hangul_count": hangul_character_count(display_text),
            "core_count": core_character_count(display_text),
            "suggested_ko": suggested_ko,
            "suggested_source": suggested_source,
            "source_group_size": source_sizes.get(key, 0),
            "source_group_korean_variants": " | ".join(korean_variants),
            "original_payload_hex": bytes(row["original_payload"]).hex().upper(),
            "current_payload_hex": bytes(row["current_payload"]).hex().upper(),
            "source_body_sha256": sha(source_bytes),
            "current_body_sha256": sha(body_bytes),
            "notes": "; ".join(notes),
        }
        output.append(out)
        counts[classification] += 1
        by_scope_counts[str(row["scope"])][classification] += 1

    output.sort(key=lambda item: int(str(item["abs"]), 16))
    actionable = [row for row in output if row["classification"] in {"true_mixed_body", "untranslated_body_sentence", "jp_body_fragment_review"}]
    translation_queue = [row for row in actionable if row["auto_fix_ready"] != "yes"]
    auto_fix = [row for row in actionable if row["auto_fix_ready"] == "yes"]
    probe = [row for row in output if row["probe_needed"] == "yes"]

    write_csv(args.out_csv, output)
    write_csv(args.actionable_csv, actionable)
    write_csv(args.probe_csv, probe)
    write_csv(args.translation_csv, translation_queue)
    report = {
        "schema_version": 2,
        "generated_by": "tools/build_battle_id_output_inventory.py",
        "read_only": True,
        "ok": True,
        "tip": identity(args.tip, tip),
        "original": identity(original_path, original),
        "manifest": identity(MANIFEST),
        "strategy": {
            "population": "reviewed Original-derived manifest plus maintained battle/ID exact addresses",
            "translation": "patch only Japanese/mixed body after a trusted split or a runtime-proven split",
            "duplicate_propagation": "only one unanimous Korean render/reference may auto-seed a duplicate",
            "prefix": "single-code-unit leading glyph remains probe-only; never assume it is control data",
            "short_shared": "quarantine unless all consumers and token ownership are verified",
        },
        "counts": {
            "records_collected": len(records),
            "records_decoded": len(decoded),
            "records_skipped": len(skipped),
            "actionable_bodies": len(actionable),
            "auto_fix_ready": len(auto_fix),
            "new_translation_required": len(translation_queue),
            "runtime_prefix_probe": len(probe),
            "by_classification": dict(sorted(counts.items())),
            "by_scope": {scope: dict(sorted(counter.items())) for scope, counter in sorted(by_scope_counts.items())},
        },
        "outputs": {
            "master_csv": str(args.out_csv.relative_to(ROOT)).replace("\\", "/"),
            "actionable_csv": str(args.actionable_csv.relative_to(ROOT)).replace("\\", "/"),
            "translation_queue_csv": str(args.translation_csv.relative_to(ROOT)).replace("\\", "/"),
            "prefix_probe_csv": str(args.probe_csv.relative_to(ROOT)).replace("\\", "/"),
        },
        "auto_fix_ready": auto_fix,
        "translation_queue_sample": translation_queue[:100],
        "prefix_probe_sample": probe[:100],
        "skipped": skipped,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "counts": report["counts"], "out": str(args.out_json.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
