#!/usr/bin/env python3
"""Broad read-only audit of Japanese residue on proven Mono-Eye text surfaces.

This revisits records that older passes deliberately excluded for being short,
Japanese-only, below the sentence threshold, out of the normal dialogue band,
or prefix-ambiguous.  It also scans the bank-75 UI table that is outside the
legacy name table.  Record boundaries come from Original-derived evidence; the
current TIP only supplies bytes and the active ext3 dictionary.

The report separates:

* tier A: proven display records with an existing reviewed Korean value;
* tier B: proven display records that still need a Korean value;
* tier C: structurally ambiguous fragments/data that must not be auto-patched.

No ROM or SaveRAM is written.
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from apply_name75_ko import ext3_bank_room
from build_p2_stock_spill_candidate import _stock_phrase_cursor
from expand_dictionary import NAME75_STRUCTURED_RANGES, _walk_zstring_range
from extract_script import split_prefix_body
from hangul_marker import marker_code
from measure_aux_prefix_rule import code_units
from mixed_residual_classification import (
    core_character_count,
    hangul_character_count,
    is_hangul_character,
    is_japanese_character,
    japanese_character_count,
)
from monoeye_rom import (
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import list_free_ext3_indices

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
BASE_MANIFEST = ROOT / "out/patch/main_p1_base_manifest.json"
OUT = ROOT / "out/patch/broad_japanese_residual_audit.json"

# Exclusions worth revisiting because they can hold visible labels, names,
# interjections, kanji-only terms, and other short strings.
WATCH_REASON_PREFIXES = (
    "excluded_name75_japanese_only",
    "excluded_aux_below_core_threshold",
    "excluded_shared_token_body_capacity",
    "excluded_prefix_unprovable",
    "excluded_outside_dialogue_band",
    "excluded_non_linguistic_fragment:single_kana",
)

# Bank-75 UI/stage-name zstrings omitted by legacy NAME75_RANGES.
UI75_START = 0x75B000
UI75_END = 0x75C000

# Known data tail in the legacy name-table range.  Rows at/after this boundary
# are retained as tier C evidence but never auto-selected.
NAME75_DATA_TAIL = 0x75E630

# Proven text-bearing aux banks.
AUX_TEXT_BANKS = {0x59, 0x5C, 0x5D, 0x5E}


class AuditError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256_bytes(payload)}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_abs(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip().replace(":", "")
    if text.lower().startswith("0x"):
        text = text[2:]
    try:
        return int(text, 16)
    except ValueError:
        return None


def iter_objects(value: Any, *, parent_key: str | None = None) -> Iterable[tuple[str | None, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        yield parent_key, value
        for key, child in value.items():
            if isinstance(child, Mapping):
                yield from iter_objects(child, parent_key=str(key))
            elif isinstance(child, list):
                for item in child:
                    yield from iter_objects(item, parent_key=str(key))
    elif isinstance(value, list):
        for item in value:
            yield from iter_objects(item, parent_key=parent_key)


def reviewed_ko(text: str) -> bool:
    """Reject glyph maps, quarantined fragments, and undecodable placeholders."""
    if not text or "�" in text:
        return False
    if any(0xE000 <= ord(ch) <= 0xF8FF for ch in text):
        return False
    return any(is_hangul_character(ch) for ch in text)


def add_translation(
    target: dict[Any, set[tuple[str, str]]],
    key: Any,
    ko: Any,
    source: str,
) -> None:
    if key is None or not isinstance(ko, str) or not ko.strip():
        return
    normalized = normalize_ko_text(ko)
    if reviewed_ko(normalized):
        target.setdefault(key, set()).add((normalized, source))


def load_translation_catalog() -> tuple[
    dict[int, set[tuple[str, str]]],
    dict[str, set[tuple[str, str]]],
    dict[str, set[tuple[str, str]]],
]:
    by_abs: dict[int, set[tuple[str, str]]] = {}
    by_record: dict[str, set[tuple[str, str]]] = {}
    by_jp: dict[str, set[tuple[str, str]]] = {}

    for name in sorted(glob.glob(str(ROOT / "data/**/*.json"), recursive=True)):
        path = Path(name)
        try:
            doc = load_json(path)
        except Exception:
            continue
        source = str(path.relative_to(ROOT)).replace("\\", "/")
        for parent_key, row in iter_objects(doc):
            ko = row.get("ko")
            if not isinstance(ko, str) or not ko.strip():
                continue
            logical = parse_abs(row.get("abs"))
            add_translation(by_abs, logical, ko, source)
            record_id = row.get("record_id")
            if isinstance(record_id, str):
                add_translation(by_record, record_id, ko, source)
            if parent_key and (parent_key.startswith("aux:") or parent_key.startswith("script:") or parent_key.startswith("name75:")):
                add_translation(by_record, parent_key, ko, source)
            jp = row.get("jp")
            # ko_ui_overrides is a glyph/ASCII substitution table rather than a
            # semantic whole-term translation catalog.  Quarantine data is also
            # evidence only.  Neither may create an exact-text translation.
            if (
                isinstance(jp, str)
                and jp
                and source not in {"data/ko_ui_overrides.json", "data/_quarantine_fragments.json"}
            ):
                add_translation(by_jp, jp.rstrip("\u3000 \t"), ko, source)

    ordered_pairs = (
        (ROOT / "out/script/aux_text_ordered.json", "texts", ROOT / "data/aux_text_ko_values.json", "ordered_ko"),
        (ROOT / "out/script/aux_body_ordered.json", "texts", ROOT / "data/aux_body_ko_values.json", "ordered_ko"),
        (ROOT / "out/script/name75_bases_ordered.json", "bases", ROOT / "data/name75_base_ko_values.json", "ordered_ko"),
        (ROOT / "out/script/name75_unmatched_ordered.json", "unmatched", ROOT / "data/name75_base_ko_values.json", "unmatched_ko"),
    )
    for jp_path, jp_key, ko_path, ko_key in ordered_pairs:
        if not jp_path.is_file() or not ko_path.is_file():
            continue
        jp_doc = load_json(jp_path)
        ko_doc = load_json(ko_path)
        jp_values = jp_doc.get(jp_key) or []
        ko_values = ko_doc.get(ko_key) or []
        if len(jp_values) != len(ko_values):
            continue
        source = str(ko_path.relative_to(ROOT)).replace("\\", "/")
        for jp, ko in zip(jp_values, ko_values):
            if isinstance(jp, str):
                add_translation(by_jp, jp.rstrip("\u3000 \t"), ko, source)

    return by_abs, by_record, by_jp


def choose_translation(
    logical: int,
    record_id: str,
    texts: Iterable[str],
    by_abs: Mapping[int, set[tuple[str, str]]],
    by_record: Mapping[str, set[tuple[str, str]]],
    by_jp: Mapping[str, set[tuple[str, str]]],
) -> dict[str, Any]:
    candidates: set[tuple[str, str, str]] = set()
    for ko, source in by_abs.get(logical, set()):
        candidates.add((ko, source, "address"))
    for ko, source in by_record.get(record_id, set()):
        candidates.add((ko, source, "record_id"))
    for text in texts:
        stripped = text.rstrip("\u3000 \t")
        for ko, source in by_jp.get(stripped, set()):
            candidates.add((ko, source, "exact_text"))
    unique_values = sorted({item[0] for item in candidates})
    return {
        "ready": len(unique_values) == 1,
        "ambiguous": len(unique_values) > 1,
        "ko": unique_values[0] if len(unique_values) == 1 else "",
        "values": unique_values,
        "evidence": [
            {"ko": ko, "source": source, "match": match}
            for ko, source, match in sorted(candidates)
        ],
    }


def extract_body(payload: bytes, region: str, logical: int, prefix_hex: str) -> tuple[bytes, bytes, str, bool]:
    if prefix_hex:
        try:
            prefix = bytes.fromhex(prefix_hex)
        except ValueError:
            prefix = b""
        if prefix and payload.startswith(prefix):
            return prefix, payload[len(prefix):], "manifest_prefix", True
    bank = logical >> 16
    if region == "script":
        prefix, body, kind = split_prefix_body(payload)
        return prefix, body, f"script_{kind}", kind == "dialogue"
    if region == "aux" and bank == 0x59:
        prefix, body, kind = split_prefix_body(payload)
        return prefix, body, f"aux59_{kind}", kind == "dialogue"
    if region == "aux" and bank in (0x5D, 0x5E):
        units = code_units(payload)
        prefix_len = units[0][1] if units else 0
        return payload[:prefix_len], payload[prefix_len:], "voice_one_code_unit", bool(prefix_len)
    return b"", payload, "full_record", True


def text_shape(text: str) -> str:
    stripped = text.rstrip("\u3000 \t")
    kana = sum("\u3040" <= ch <= "\u30ff" and ch != "・" for ch in stripped)
    kanji = sum("\u4e00" <= ch <= "\u9fff" for ch in stripped)
    hangul = sum(is_hangul_character(ch) for ch in stripped)
    if kanji and not kana and not hangul:
        return "kanji_only"
    if kana and not kanji and not hangul:
        return "kana_only"
    if hangul and (kana or kanji):
        return "mixed_ko_jp"
    if kana or kanji:
        return "japanese_mixed_script"
    return "other"


def current_strong_retired_slots(original: bytes, current: bytes, current_dictionary: Any) -> list[int]:
    original_dictionary = Dictionary(original)
    wanted = {
        index
        for index in range(min(original_dictionary.stock_count, 0xF00))
        if dict_token_safe_in_zstring(index)
    }
    original_external = external_occurrence_map(original, ext3_aware=False, wanted=wanted)
    current_external = external_occurrence_map(current, ext3_aware=True, wanted=wanted)
    original_nested = nested_occurrence_map(original_dictionary, wanted=wanted, ext3_aware=False)
    current_nested = nested_occurrence_map(current_dictionary, wanted=wanted, ext3_aware=True)
    preliminary: list[int] = []
    for index in sorted(wanted):
        if current_external.get(index) or current_nested.get(index) or original_nested.get(index):
            continue
        if not original_external.get(index):
            continue
        try:
            same = (
                original_dictionary.ptrs[index] == current_dictionary.ptrs[index]
                and bytes(original_dictionary.raw_entry(index)) == bytes(current_dictionary.raw_entry(index))
            )
        except Exception:
            continue
        if same:
            preliminary.append(index)
    raw_hits = _raw_pair_hits(current, preliminary)
    return [index for index in preliminary if not raw_hits.get(index)]


def tier_for(row: Mapping[str, Any]) -> tuple[str, str]:
    logical = int(row["logical_address"])
    reason = str(row.get("legacy_reason") or "")
    region = str(row["region"])
    extraction_trusted = bool(row["extraction_trusted"])
    translation = row["translation"]
    core = int(row["core_count"])
    body_capacity = int(row["body_capacity"])

    if region == "name75" and NAME75_DATA_TAIL <= logical < 0x75FE93:
        return "C", "legacy_name75_data_tail"
    if region == "name75" and body_capacity == 1:
        return "C", "single_byte_name_fragment"
    if str(row.get("source_population")) == "ui75_walker":
        if body_capacity < 2 or core < 2:
            return "C", "ui75_short_fragment"
        if not translation["ready"]:
            return "C", "ui75_requires_catalog_or_screen_evidence"
    if reason.startswith("excluded_non_linguistic_fragment") and core <= 1:
        return "C", "single_kana_or_fragment"
    if reason.startswith("excluded_prefix_unprovable") and not extraction_trusted:
        return "C", "prefix_not_proven"
    if not extraction_trusted:
        return "C", "body_extraction_not_proven"
    if body_capacity <= 0:
        return "C", "empty_body"
    if translation["ambiguous"]:
        return "B", "translation_catalog_conflict"
    if translation["ready"]:
        return "A", "reviewed_translation_ready"
    return "B", "reviewed_translation_missing"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise AuditError("refusing to write a ROM")

    current = bytes(load_rom(args.tip))
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    dictionary = make_dictionary_ext3(current, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)
    by_abs, by_record, by_jp = load_translation_catalog()

    manifest = load_json(BASE_MANIFEST)
    population = manifest.get("population") or {}
    manifest_rows = list(population.get("included") or []) + list(population.get("excluded") or [])

    candidates: dict[int, dict[str, Any]] = {}
    sb = stock_base(current)
    osb = stock_base(original)

    def add_row(
        *,
        logical: int,
        record_id: str,
        region: str,
        payload_capacity: int,
        original_text: str,
        legacy_reason: str,
        prefix_hex: str,
        source_population: str,
    ) -> None:
        if any(lo <= logical < hi for lo, hi in NAME75_STRUCTURED_RANGES):
            return
        if logical in candidates:
            return
        got = read_encoded_z_safe(current, sb + logical, max_len=max(256, payload_capacity + 8))
        if got is None:
            return
        current_payload = bytes(got[0])
        if payload_capacity and len(current_payload) != payload_capacity:
            # The exact Original boundary is the safety contract.  Keep the row
            # as tier C evidence rather than decoding across a moved terminator.
            body = b""
            prefix = b""
            rule = "boundary_drift"
            trusted = False
            current_text = ""
        else:
            prefix, body, rule, trusted = extract_body(current_payload, region, logical, prefix_hex)
            try:
                current_text = dictionary.expand(body, tbl).rstrip("\u3000 \t")
            except Exception:
                current_text = ""
                trusted = False
                rule = "decode_failed"
        if not current_text or not any(is_japanese_character(ch) for ch in current_text):
            return
        translation = choose_translation(
            logical,
            record_id,
            (original_text, current_text),
            by_abs,
            by_record,
            by_jp,
        )
        row: dict[str, Any] = {
            "record_id": record_id,
            "abs": f"{logical:06X}",
            "logical_address": logical,
            "region": region,
            "bank": f"{logical >> 16:02X}",
            "source_population": source_population,
            "legacy_reason": legacy_reason,
            "original_text": original_text.rstrip("\u3000 \t"),
            "current_text": current_text,
            "shape": text_shape(current_text),
            "japanese_count": japanese_character_count(current_text),
            "hangul_count": hangul_character_count(current_text),
            "core_count": core_character_count(current_text),
            "payload_capacity": len(current_payload),
            "prefix_bytes": len(prefix),
            "prefix_hex": prefix.hex().upper(),
            "body_capacity": len(body),
            "body_hex": body.hex().upper(),
            "extraction_rule": rule,
            "extraction_trusted": trusted,
            "translation": translation,
        }
        tier, tier_reason = tier_for(row)
        row["tier"] = tier
        row["tier_reason"] = tier_reason
        candidates[logical] = row

    for source in manifest_rows:
        reason = str(source.get("reason") or "")
        region = str(source.get("region") or "")
        logical = int(source.get("logical_address") or parse_abs(source.get("abs")) or 0)
        if logical <= 0:
            continue
        watch = any(reason.startswith(prefix) for prefix in WATCH_REASON_PREFIXES)
        # Also catch any current Japanese residue in previously included proven
        # records.  These should normally be zero after prior promotions.
        if not watch and source.get("included") is not True:
            continue
        if region == "aux" and (logical >> 16) not in AUX_TEXT_BANKS:
            continue
        # Outside-dialogue script rows in banks 64-6F are unit/event/data
        # tables.  The only historically proven visible out-of-band script
        # population is the pre-opening block in bank 60.
        if (
            region == "script"
            and reason.startswith("excluded_outside_dialogue_band")
            and not (0x600000 <= logical < 0x6040A5)
        ):
            continue
        boundary = source.get("boundary") or {}
        add_row(
            logical=logical,
            record_id=str(source.get("record_id") or f"{region}:{logical:06X}"),
            region=region,
            payload_capacity=int(boundary.get("payload_capacity") or 0),
            original_text=str(source.get("rendered_source_text") or source.get("source_text") or ""),
            legacy_reason=reason,
            prefix_hex=str(source.get("prefix_hex") or ""),
            source_population="main_p1_base_manifest",
        )

    # Add the omitted bank-75 UI table.  Boundaries are walked from Original.
    for logical, original_payload, _kind in _walk_zstring_range(
        original, UI75_START, UI75_END, region="name75", max_len=64
    ):
        try:
            original_text = original_dictionary.expand(original_payload, tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        add_row(
            logical=logical,
            record_id=f"ui75:{logical:06X}",
            region="name75_ui",
            payload_capacity=len(original_payload),
            original_text=original_text,
            legacy_reason="new_scope_ui75_table",
            prefix_hex="",
            source_population="ui75_walker",
        )

    rows = sorted(candidates.values(), key=lambda row: int(row["logical_address"]))
    counts_by_tier = collections.Counter(str(row["tier"]) for row in rows)
    counts_by_region = collections.Counter(str(row["region"]) for row in rows)
    counts_by_shape = collections.Counter(str(row["shape"]) for row in rows)
    counts_by_reason = collections.Counter(str(row["legacy_reason"]).split(":", 1)[0] for row in rows)
    short_rows = [row for row in rows if int(row["body_capacity"]) < 4]
    one_byte_rows = [row for row in rows if int(row["body_capacity"]) == 1]
    tier_a = [row for row in rows if row["tier"] == "A"]
    tier_b = [row for row in rows if row["tier"] == "B"]
    tier_c = [row for row in rows if row["tier"] == "C"]

    # Capacity estimate for translation-ready tier A records.
    ext3_rows = [row for row in tier_a if int(row["body_capacity"]) >= 4]
    token_rows = [row for row in tier_a if 2 <= int(row["body_capacity"]) < 4]
    direct_rows = [row for row in tier_a if int(row["body_capacity"]) == 1]
    enc_failures: list[str] = []
    encoded_by_text: dict[str, bytes] = {}
    for row in tier_a:
        text = str((row.get("translation") or {}).get("ko") or "")
        if text in encoded_by_text:
            continue
        encoded = try_encode_ko_text(
            text,
            tbl,
            hangul_marker_code=marker_code(),
            hangul_marker_mode="run",
        )
        if encoded is None or b"\x00" in encoded:
            enc_failures.append(text)
        else:
            encoded_by_text[text] = bytes(encoded)

    # Existing exact dictionary phrases can satisfy short records without a new
    # retired slot.
    short_texts = {str(row["translation"]["ko"]) for row in token_rows}
    exact_slots: dict[str, list[str]] = {text: [] for text in short_texts}
    for index in range(dictionary.count):
        try:
            rendered = dictionary.expand(bytes(dictionary.raw_entry(index)), tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        if rendered in exact_slots:
            exact_slots[rendered].append(f"{index:04X}")
    exact_reuse = {text: slots for text, slots in exact_slots.items() if slots}
    new_stock_texts = short_texts - set(exact_reuse)

    num_banks = int(ext3_meta.get("num_banks") or 0)
    free_ext3 = list_free_ext3_indices(current, num_banks=num_banks)
    room_by_bank = ext3_bank_room(current, num_banks)
    stock_cursor = _stock_phrase_cursor(current)
    strong_retired = current_strong_retired_slots(original, current, dictionary)

    ext3_unique = {str(row["translation"]["ko"]) for row in ext3_rows}
    ext3_bytes = sum(len(encoded_by_text[text]) + 1 for text in ext3_unique if text in encoded_by_text)
    stock_bytes = sum(len(encoded_by_text[text]) + 1 for text in new_stock_texts if text in encoded_by_text)

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_broad_japanese_residuals.py",
        "read_only": True,
        "ok": not enc_failures,
        "inputs": {
            "tip": identity(args.tip, current),
            "original": identity(ORIGINAL, original),
            "base_manifest": identity(BASE_MANIFEST),
            "tbl": identity(TBL_PATH),
        },
        "scope": {
            "manifest_watch_reason_prefixes": list(WATCH_REASON_PREFIXES),
            "aux_text_banks": [f"{bank:02X}" for bank in sorted(AUX_TEXT_BANKS)],
            "ui75_range": [f"{UI75_START:06X}", f"{UI75_END:06X}"],
            "name75_data_tail_start": f"{NAME75_DATA_TAIL:06X}",
            "policy": "Original-derived boundaries; no raw character-class expansion outside proven scopes",
        },
        "counts": {
            "japanese_residual_records": len(rows),
            "tier_a_translation_ready": len(tier_a),
            "tier_b_translation_needed_or_conflicted": len(tier_b),
            "tier_c_ambiguous_or_data": len(tier_c),
            "short_body_under_4": len(short_rows),
            "one_byte_body": len(one_byte_rows),
            "kanji_only": counts_by_shape.get("kanji_only", 0),
            "kana_only": counts_by_shape.get("kana_only", 0),
            "mixed_ko_jp": counts_by_shape.get("mixed_ko_jp", 0),
            "by_tier": dict(sorted(counts_by_tier.items())),
            "by_region": dict(sorted(counts_by_region.items())),
            "by_shape": dict(sorted(counts_by_shape.items())),
            "by_legacy_reason_base": dict(sorted(counts_by_reason.items())),
        },
        "patch_plan": {
            "tier_a_ext3_records": len(ext3_rows),
            "tier_a_short_token_records": len(token_rows),
            "tier_a_one_byte_records_separate_method": len(direct_rows),
            "tier_a_ext3_unique_phrases": len(ext3_unique),
            "tier_a_ext3_phrase_bytes_including_nul": ext3_bytes,
            "tier_a_short_unique_phrases": len(short_texts),
            "tier_a_short_exact_existing_phrases": len(exact_reuse),
            "tier_a_short_new_stock_phrases": len(new_stock_texts),
            "tier_a_short_new_stock_bytes_including_nul": stock_bytes,
            "exact_reuse_slots": exact_reuse,
            "encoding_failures": sorted(set(enc_failures)),
        },
        "capacity": {
            "ext3_free_slots": len(free_ext3),
            "ext3_phrase_room": sum(room_by_bank.values()),
            "ext3_room_by_bank": {f"{index:02X}": room for index, room in sorted(room_by_bank.items())},
            "stock_tail_cursor": f"{stock_cursor:04X}",
            "stock_tail_room": 0x10000 - stock_cursor,
            "strong_retired_stock_slots": len(strong_retired),
            "strong_retired_sample": [f"{index:04X}" for index in strong_retired[:40]],
            "tier_a_capacity_sufficient": (
                not enc_failures
                and len(free_ext3) >= len(ext3_unique)
                and sum(room_by_bank.values()) >= ext3_bytes
                and len(strong_retired) >= len(new_stock_texts)
                and (0x10000 - stock_cursor) >= stock_bytes
            ),
        },
        "recommendation": {
            "next_batch": "tier A only: ext3 records first, then guarded short-token records",
            "one_byte_policy": "do not patch with dictionary tokens; require glyph/table-specific proof",
            "tier_b_policy": "prepare reviewed Korean values before candidate generation",
            "tier_c_policy": "screen/pointer evidence required; never auto-patch",
            "promotion_policy": "candidate-only, static gates, user visual verification, then explicit promotion",
        },
        "records": {
            "tier_a": tier_a,
            "tier_b": tier_b,
            "tier_c": tier_c,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "counts": report["counts"], "patch_plan": report["patch_plan"], "capacity": report["capacity"], "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
