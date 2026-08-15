#!/usr/bin/env python3
"""Inventory the three runtime text families still leaking Japanese.

Families:
1. Bank-5C ID-command bundles: metadata + prefixed first line + continuation.
2. Explicit 17 34 18 dialogue records in bank 59 and script banks 60-6F.
3. Dense battle-voice zstring runs in banks 5D/5E.  Only screen-proven exact
   prefixes or explicit 01/02 format bytes are excluded; ordinary first glyphs
   remain part of the translated sentence.

The scanner is read-only.  It uses Original-ROM boundaries, current-main
rendering, maintained Korean catalogs, and unanimous current canonical
translations.  No raw dictionary-token rewrite is proposed.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_battle_id_output_inventory import (  # noqa: E402
    load_catalog_references,
    normalize_text,
    sentence_like,
    unique_reference,
    valid_korean,
)
from expand_dictionary import NAME75_RANGES, _walk_zstring_range  # noqa: E402
from measure_aux_prefix_rule import code_units  # noqa: E402
from mixed_residual_classification import (  # noqa: E402
    core_character_count,
    hangul_character_count,
    japanese_character_count,
)
from dialogue_runtime_contracts import voice_prefix as contract_voice_prefix  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    find_rom,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_JSON = ROOT / "out/patch/runtime_text_residual_families_report.json"
OUT_ID = ROOT / "out/script/runtime_text_residual_id_bundle_sheet.csv"
OUT_DIALOGUE = ROOT / "out/script/runtime_text_residual_prefixed_dialogue_sheet.csv"
OUT_VOICE = ROOT / "out/script/runtime_text_residual_voice_sheet.csv"
VOICE_UNCOVERED_AUDIT = ROOT / "out/patch/current_tip_battle_voice_uncovered_residual_audit.json"

PREFIX = bytes.fromhex("173418")
ID_META = bytes.fromhex("01060008")
SCREEN_VOICE_PREFIXES = {
    0x5D014E: bytes.fromhex("02F191"),
    0x5D0211: bytes.fromhex("02F191"),
    0x5D03ED: bytes.fromhex("02F191"),
}
VOICE_DUPLICATE_PROVEN_KO = {
    0x5D1E3E: "결국、가치관이　다른　듯하군……",
    0x5E6586: "죄송합니다、라이덴　소좌！",
    0x5E65A7: "당할　수　있겠나！",
}
VOICE_DUPLICATE_PROVEN = set(VOICE_DUPLICATE_PROVEN_KO)
SCREEN_ADDRESSES = {
    0x5C977F: "id_command_johnny_line1_a",
    0x5C9794: "id_command_johnny_line2_a",
    0x5C97AE: "id_command_johnny_line1_b",
    0x5C97C0: "id_command_johnny_line2_b",
    0x5960E4: "scenario_kagerou_question",
    0x5D014E: "battle_voice_muda_1",
    0x5D0211: "battle_voice_muda_2",
    0x5D03ED: "battle_voice_muda_3",
}
FIELDS = [
    "family", "record_start", "bundle_start", "line_role", "bank",
    "prefix_hex", "body_capacity", "original_body", "current_body",
    "classification", "japanese_count", "hangul_count", "suggested_ko",
    "suggested_source", "translation_ready", "storage_strategy",
    "screen_evidence", "original_payload_hex", "current_payload_hex", "notes",
]


class AnalyzeError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def decode(dictionary: Any, payload: bytes, tbl: Tbl) -> str:
    return dictionary.expand(payload, tbl).rstrip("\u3000 \t")


def first_unit_len(payload: bytes) -> int:
    units = code_units(payload)
    return units[0][1] if units else 0


def current_payload(tip: bytes, logical: int, capacity: int) -> bytes | None:
    start = stock_base(tip) + logical
    end = start + capacity
    if start < 0 or end >= len(tip) or tip[end] != 0:
        return None
    return bytes(tip[start:end])


def make_row(
    *,
    family: str,
    record_start: int,
    original_payload: bytes,
    current: bytes,
    body_offset: int,
    current_body_offset: int | None = None,
    bundle_start: int | None = None,
    line_role: str = "",
    notes: str = "",
) -> dict[str, Any]:
    current_offset = body_offset if current_body_offset is None else current_body_offset
    return {
        "family": family,
        "record_start_int": record_start,
        "record_start": f"{record_start:06X}",
        "bundle_start": f"{bundle_start:06X}" if bundle_start is not None else "",
        "line_role": line_role,
        "bank": f"{record_start >> 16:02X}",
        "prefix_hex": current[:current_offset].hex().upper(),
        "body_capacity": len(original_payload) - body_offset,
        "original_payload": original_payload,
        "current_payload": current,
        "original_body_payload": original_payload[body_offset:],
        "current_body_payload": current[current_offset:],
        "original_body_offset": body_offset,
        "current_body_offset": current_offset,
        "notes": notes,
    }


def enumerate_id_bundles(original: bytes, tip: bytes) -> tuple[list[dict[str, Any]], int]:
    sb = stock_base(original)
    st = stock_base(tip)
    lo = sb + 0x5C0000
    hi = sb + 0x5D0000
    rows: list[dict[str, Any]] = []
    bundles = 0
    cursor = lo
    while True:
        found = original.find(ID_META, cursor, hi)
        if found < 0:
            break
        logical = found - sb
        cursor = found + 1
        if found + 9 >= hi or original[found + 6 : found + 9] != PREFIX:
            continue
        line1 = read_encoded_z_safe(original, found + 6, max_len=128)
        if line1 is None:
            continue
        line2_file = int(line1[1]) + 1
        line2 = read_encoded_z_safe(original, line2_file, max_len=128)
        if line2 is None:
            continue
        line1_logical = logical + 6
        line2_logical = line2_file - sb
        cur1 = read_encoded_z_safe(tip, st + line1_logical, max_len=128)
        cur2 = read_encoded_z_safe(tip, st + line2_logical, max_len=128)
        if cur1 is None or cur2 is None:
            continue
        bundles += 1
        rows.append(
            make_row(
                family="id_command_bundle",
                record_start=line1_logical,
                original_payload=bytes(line1[0]),
                current=bytes(cur1[0]),
                body_offset=len(PREFIX),
                bundle_start=logical,
                line_role="first",
                notes="ID bundle first line; preserve 17 34 18 prefix",
            )
        )
        if bytes(line2[0]):
            rows.append(
                make_row(
                    family="id_command_bundle",
                    record_start=line2_logical,
                    original_payload=bytes(line2[0]),
                    current=bytes(cur2[0]),
                    body_offset=0,
                    bundle_start=logical,
                    line_role="continuation",
                    notes="ID bundle continuation; no 17 34 18 prefix",
                )
            )
    return rows, bundles


def enumerate_prefixed_dialogue(original: bytes, tip: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ranges = [(0x590000, 0x5A0000)] + [
        (bank << 16, (bank + 1) << 16) for bank in range(0x60, 0x70)
    ]
    for lo, hi in ranges:
        for logical, payload, _kind in _walk_zstring_range(
            original, lo, hi, region="script" if lo >= 0x600000 else "aux", max_len=256
        ):
            if not bytes(payload).startswith(PREFIX) or len(payload) <= len(PREFIX):
                continue
            current = current_payload(tip, logical, len(payload))
            if current is None or not current.startswith(PREFIX):
                continue
            rows.append(
                make_row(
                    family="prefixed_dialogue",
                    record_start=logical,
                    original_payload=bytes(payload),
                    current=current,
                    body_offset=len(PREFIX),
                    notes="explicit dialogue prefix; Original boundary is authoritative",
                )
            )
    return rows


def plausible_voice_body(text: str) -> bool:
    value = text.strip("\u3000 \t")
    if not value or "<BAD" in value:
        return False
    if value == "不要":
        return True
    if japanese_character_count(value) > 0 and core_character_count(value) >= 1:
        return True
    return sentence_like(value)


def voice_prefix(payload: bytes, logical: int) -> tuple[bytes, str]:
    """Compatibility wrapper around the single runtime contract.

    Do not add address/byte heuristics here.  The manifest builder, residual
    analyzer, and safety gate must all see the same role decision.
    """
    return contract_voice_prefix(payload, logical)


def enumerate_voice_runs(original: bytes, tip: bytes, od: Dictionary, tbl: Tbl) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted_rows: list[dict[str, Any]] = []
    run_reports: list[dict[str, Any]] = []
    for bank in (0x5D, 0x5E):
        all_rows: list[dict[str, Any]] = []
        for logical, payload, _kind in _walk_zstring_range(
            original, bank << 16, (bank + 1) << 16, region="aux", max_len=128
        ):
            payload = bytes(payload)
            prefix, boundary_reason = voice_prefix(payload, logical)
            body_offset = len(prefix)
            if body_offset >= len(payload):
                continue
            try:
                body_text = decode(od, payload[body_offset:], tbl)
            except Exception:
                continue
            current = current_payload(tip, logical, len(payload))
            if current is None:
                continue
            all_rows.append(
                {
                    "logical": logical,
                    "payload": payload,
                    "current": current,
                    "body_offset": body_offset,
                    "prefix": prefix,
                    "boundary_reason": boundary_reason,
                    "end": logical + len(payload) + 1,
                    "plausible": plausible_voice_body(body_text),
                    "body_text": body_text,
                }
            )

        runs: list[list[dict[str, Any]]] = []
        current_run: list[dict[str, Any]] = []
        for row in all_rows:
            if current_run and row["logical"] - current_run[-1]["end"] > 5:
                runs.append(current_run)
                current_run = []
            current_run.append(row)
        if current_run:
            runs.append(current_run)

        for run in runs:
            plausible = [row for row in run if row["plausible"]]
            sentence_count = sum(
                sentence_like(str(row["body_text"])) for row in plausible
            )
            accepted = (
                len(run) >= 6
                and len(plausible) / len(run) >= 0.70
                and sentence_count >= 3
            )
            run_reports.append(
                {
                    "bank": f"{bank:02X}",
                    "start": f"{run[0]['logical']:06X}",
                    "end_exclusive": f"{run[-1]['end']:06X}",
                    "records": len(run),
                    "plausible_records": len(plausible),
                    "sentence_records": sentence_count,
                    "accepted": accepted,
                }
            )
            if not accepted:
                continue
            for row in plausible:
                original_payload = bytes(row["payload"])
                current = bytes(row["current"])
                body_offset = int(row["body_offset"])
                original_prefix = bytes(row["prefix"])
                prefix_preserved = bool(original_prefix) and current.startswith(original_prefix)
                current_offset = body_offset if prefix_preserved else 0
                accepted_rows.append(
                    make_row(
                        family="voice_tagged_run",
                        record_start=int(row["logical"]),
                        original_payload=original_payload,
                        current=current,
                        body_offset=body_offset,
                        current_body_offset=current_offset,
                        notes=(
                            str(row["boundary_reason"])
                            + ("; prefix preserved in current payload" if prefix_preserved else "; current payload judged as whole-record text")
                        ),
                    )
                )
    return accepted_rows, run_reports


def iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from iter_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from iter_dicts(nested)


def load_approved_project_references() -> dict[str, list[dict[str, str]]]:
    refs: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    explicit_trusted = {
        "mixed_residual_translations.json",
        "aux_false_prefix_cleanup_ko.json",
    }
    for path in sorted((ROOT / "data").glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        curated_path = path.name.endswith("_ko.json") or path.name in explicit_trusted
        for row in iter_dicts(document):
            source = str(
                row.get("jp") or row.get("source_text") or row.get("original_text")
                or row.get("original_jp") or row.get("original") or ""
            ).strip()
            ko = str(row.get("ko") or "").strip()
            review = str(row.get("review_status") or "").strip()
            trusted = curated_path or review in {
                "approved", "user_verified", "not_needed_false_mixed"
            }
            if source and valid_korean(ko) and trusted:
                refs[normalize_text(source)].append(
                    {"ko": ko, "catalog": str(path.relative_to(ROOT)).replace("\\", "/")}
                )
    return refs


def load_name75_current_references(
    original: bytes,
    tip: bytes,
    od: Dictionary,
    cd: Any,
    tbl: Tbl,
) -> dict[str, list[dict[str, str]]]:
    refs: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for lo, hi in NAME75_RANGES:
        for logical, payload, _kind in _walk_zstring_range(
            original, lo, hi, region="name75", max_len=64
        ):
            current = current_payload(tip, logical, len(payload))
            if current is None:
                continue
            try:
                jp = decode(od, bytes(payload), tbl)
                ko = decode(cd, current, tbl)
            except Exception:
                continue
            if valid_korean(ko):
                refs[normalize_text(jp)].append(
                    {"ko": ko, "catalog": "current_main_name75_canonical"}
                )
    return refs


def storage_strategy(capacity: int) -> str:
    if capacity >= 4:
        return "private_ext3_in_place"
    if capacity >= 2:
        return "reuse_or_true_free_two_byte_token"
    return "glyph_or_table_specific_method"


def finalize_rows(
    rows: list[dict[str, Any]],
    *,
    od: Dictionary,
    cd: Any,
    tbl: Tbl,
    catalog_by_source: dict[str, list[dict[str, str]]],
    name75_refs: dict[str, list[dict[str, str]]],
    placeholder_addresses: set[int],
) -> list[dict[str, Any]]:
    current_refs: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    decoded: list[dict[str, Any]] = []
    for row in rows:
        try:
            original_body = decode(od, bytes(row["original_body_payload"]), tbl)
            current_body = decode(cd, bytes(row["current_body_payload"]), tbl)
        except Exception as exc:  # noqa: BLE001
            row["decode_error"] = type(exc).__name__
            continue
        row["original_body"] = original_body
        row["current_body"] = current_body
        decoded.append(row)
        if valid_korean(current_body):
            current_refs[normalize_text(original_body)].append(
                {"ko": current_body, "catalog": "current_main_unanimous_duplicate"}
            )
        if row["family"] == "voice_tagged_run" and int(row["original_body_offset"]) == 0:
            original_payload = bytes(row["original_payload"])
            current_payload_bytes = bytes(row["current_payload"])
            unit = first_unit_len(original_payload)
            if 0 < unit < len(original_payload) and current_payload_bytes.startswith(original_payload[:unit]):
                try:
                    stripped_current = decode(cd, current_payload_bytes[unit:], tbl)
                except Exception:
                    stripped_current = ""
                if valid_korean(stripped_current):
                    current_refs[normalize_text(original_body)].append(
                        {
                            "ko": stripped_current,
                            "catalog": "current_main_visible_leading_glyph_cleanup",
                        }
                    )

    output: list[dict[str, Any]] = []
    for row in decoded:
        original_body = str(row["original_body"])
        current_body = str(row["current_body"])
        key = normalize_text(original_body)
        jp_count = japanese_character_count(current_body)
        ko_count = hangul_character_count(current_body)
        original_full = decode(od, bytes(row["original_payload"]), tbl)
        placeholder = (
            normalize_text(original_body) in {"不要", ""}
            or normalize_text(original_full) in {"不要", ""}
            or int(row["record_start_int"]) in placeholder_addresses
        )
        record_start_int = int(row["record_start_int"])
        screen = SCREEN_ADDRESSES.get(record_start_int, "")
        duplicate_proven = record_start_int in VOICE_DUPLICATE_PROVEN
        if placeholder:
            classification = "placeholder_or_empty"
        elif jp_count and ko_count:
            classification = "mixed_shared_dictionary_or_partial_patch"
        elif jp_count:
            classification = (
                "unchanged_japanese_record"
                if bytes(row["original_body_payload"]) == bytes(row["current_body_payload"])
                else "japanese_residual_after_partial_patch"
            )
        elif ko_count:
            classification = "clean_korean"
        else:
            classification = "nonlinguistic_or_punctuation"
        if (
            row["family"] == "voice_tagged_run"
            and jp_count > 0
            and not screen
            and not duplicate_proven
            and not placeholder
        ):
            classification = "voice_boundary_unproven_quarantine"

        refs: list[dict[str, str]] = []
        refs.extend(catalog_by_source.get(key, []))
        refs.extend(name75_refs.get(key, []))
        refs.extend(current_refs.get(key, []))
        suggestion = unique_reference(refs)
        suggested_ko = suggestion[0] if suggestion else ""
        suggested_source = suggestion[1] if suggestion else ""
        if duplicate_proven:
            suggested_ko = VOICE_DUPLICATE_PROVEN_KO[record_start_int]
            suggested_source = "data/runtime_text_voice_duplicate_proven_ko.json"
        translation_ready = (
            bool(suggested_ko)
            and jp_count > 0
            and not placeholder
            and classification != "voice_boundary_unproven_quarantine"
        )
        notes = str(row.get("notes") or "")
        if screen:
            notes = (notes + "; screen-proven residual").strip("; ")
        if classification == "voice_boundary_unproven_quarantine":
            notes = (
                notes
                + "; diagnostic-only: visible text boundary is not runtime-proven; never auto-patch"
            ).strip("; ")
        elif duplicate_proven and jp_count > 0:
            notes = (notes + "; exact clean duplicate payload proves cleanup").strip("; ")
        elif jp_count > 0 and not suggested_ko:
            notes = (notes + "; reviewed Korean translation required").strip("; ")
        output.append(
            {
                "family": row["family"],
                "record_start": row["record_start"],
                "bundle_start": row["bundle_start"],
                "line_role": row["line_role"],
                "bank": row["bank"],
                "prefix_hex": row["prefix_hex"],
                "body_capacity": row["body_capacity"],
                "original_body": original_body,
                "current_body": current_body,
                "classification": classification,
                "japanese_count": jp_count,
                "hangul_count": ko_count,
                "suggested_ko": suggested_ko,
                "suggested_source": suggested_source,
                "translation_ready": "yes" if translation_ready else "no",
                "storage_strategy": storage_strategy(int(row["body_capacity"])),
                "screen_evidence": screen,
                "original_payload_hex": bytes(row["original_payload"]).hex().upper(),
                "current_payload_hex": bytes(row["current_payload"]).hex().upper(),
                "notes": notes,
            }
        )
    return output


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
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
    parser.add_argument("--out-id", type=Path, default=OUT_ID)
    parser.add_argument("--out-dialogue", type=Path, default=OUT_DIALOGUE)
    parser.add_argument("--out-voice", type=Path, default=OUT_VOICE)
    args = parser.parse_args(argv)

    tip = bytes(load_rom(args.tip))
    original_path = find_rom(ROOT)
    original = bytes(load_rom(original_path))
    tbl = Tbl.load(TBL_PATH)
    od = Dictionary(original)
    cd = make_dictionary_ext3(tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    _by_address, catalog_by_source, _exact = load_catalog_references()
    approved_project_refs = load_approved_project_references()
    for key, values in approved_project_refs.items():
        catalog_by_source[key].extend(values)
    name75_refs = load_name75_current_references(original, tip, od, cd, tbl)

    placeholder_addresses: set[int] = set()
    if VOICE_UNCOVERED_AUDIT.is_file():
        historical_voice_audit = json.loads(
            VOICE_UNCOVERED_AUDIT.read_text(encoding="utf-8")
        )
        placeholder_addresses = {
            int(str(row["abs"]), 16)
            for row in historical_voice_audit.get("placeholder_or_template") or []
            if row.get("abs")
        }

    id_rows, bundle_count = enumerate_id_bundles(original, tip)
    dialogue_rows = enumerate_prefixed_dialogue(original, tip)
    voice_rows, voice_runs = enumerate_voice_runs(original, tip, od, tbl)
    all_rows = id_rows + dialogue_rows + voice_rows
    finalized = finalize_rows(
        all_rows,
        od=od,
        cd=cd,
        tbl=tbl,
        catalog_by_source=catalog_by_source,
        name75_refs=name75_refs,
        placeholder_addresses=placeholder_addresses,
    )

    by_family = {
        "id_command_bundle": [row for row in finalized if row["family"] == "id_command_bundle"],
        "prefixed_dialogue": [row for row in finalized if row["family"] == "prefixed_dialogue"],
        "voice_tagged_run": [row for row in finalized if row["family"] == "voice_tagged_run"],
    }
    write_csv(args.out_id, by_family["id_command_bundle"])
    write_csv(args.out_dialogue, by_family["prefixed_dialogue"])
    write_csv(args.out_voice, by_family["voice_tagged_run"])

    residuals = [
        row for row in finalized
        if int(row["japanese_count"]) > 0
        and row["classification"] not in {
            "placeholder_or_empty",
            "voice_boundary_unproven_quarantine",
        }
    ]
    screen_rows = [row for row in finalized if row["screen_evidence"]]
    counts_by_family: dict[str, Any] = {}
    for family, rows in by_family.items():
        classes = collections.Counter(str(row["classification"]) for row in rows)
        family_residuals = [
            row for row in rows
            if int(row["japanese_count"]) > 0
            and row["classification"] not in {
                "placeholder_or_empty",
                "voice_boundary_unproven_quarantine",
            }
        ]
        family_storage = collections.Counter(
            str(row["storage_strategy"]) for row in family_residuals
        )
        family_ready_storage = collections.Counter(
            str(row["storage_strategy"])
            for row in family_residuals
            if row["translation_ready"] == "yes"
        )
        counts_by_family[family] = {
            "records": len(rows),
            "residuals": len(family_residuals),
            "translation_ready": sum(row["translation_ready"] == "yes" for row in family_residuals),
            "new_translation_required": sum(row["translation_ready"] != "yes" for row in family_residuals),
            "by_classification": dict(sorted(classes.items())),
            "residuals_by_storage": dict(sorted(family_storage.items())),
            "translation_ready_by_storage": dict(sorted(family_ready_storage.items())),
        }

    all_storage = collections.Counter(str(row["storage_strategy"]) for row in residuals)
    ready_storage = collections.Counter(
        str(row["storage_strategy"])
        for row in residuals
        if row["translation_ready"] == "yes"
    )
    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_runtime_text_residual_families.py",
        "read_only": True,
        "ok": True,
        "inputs": {
            "tip": identity(args.tip, tip),
            "original": identity(original_path, original),
            "tbl": identity(TBL_PATH),
        },
        "grammar": {
            "id_command": "01 06 00 08 xx 00 + (17 34 18 first line) + 00 + continuation + 00",
            "prefixed_dialogue": "Original-bound NUL record beginning 17 34 18 in bank 59 or 60-6F",
            "voice": "diagnostic-only dense 5D/5E walk; only screen-proven or exact-duplicate-proven addresses may become patch targets; every other Japanese result is quarantined",
            "raw_token_pair_rewrite_forbidden": True,
        },
        "counts": {
            "id_bundles": bundle_count,
            "voice_runs_accepted": sum(bool(row["accepted"]) for row in voice_runs),
            "historical_voice_placeholder_addresses": len(placeholder_addresses),
            "approved_project_translation_keys": len(approved_project_refs),
            "all_records": len(finalized),
            "voice_boundary_unproven_quarantine": sum(
                row["classification"] == "voice_boundary_unproven_quarantine"
                for row in finalized
            ),
            "all_residuals": len(residuals),
            "all_translation_ready": sum(row["translation_ready"] == "yes" for row in residuals),
            "all_new_translation_required": sum(row["translation_ready"] != "yes" for row in residuals),
            "residuals_by_storage": dict(sorted(all_storage.items())),
            "translation_ready_by_storage": dict(sorted(ready_storage.items())),
            "by_family": counts_by_family,
        },
        "screen_evidence": screen_rows,
        "voice_run_inventory": voice_runs,
        "outputs": {
            "id_bundle_csv": identity(args.out_id),
            "prefixed_dialogue_csv": identity(args.out_dialogue),
            "voice_csv": identity(args.out_voice),
        },
        "next_strategy": [
            "Patch whole record bodies with private ext3 or a proven reusable canonical token; never localize shared dictionary slots.",
            "Treat ID first and continuation lines as one transactional bundle and verify both NUL terminators.",
            "Use Original-derived 17 34 18 boundaries for scenario/event dialogue even when current bytes are unchanged but shared dictionary rendering is mixed.",
            "Never build from the broad voice diagnostic population. Promote voice fixes only with screen evidence or an exact clean duplicate payload at the same source text.",
            "Bind every candidate to structured-table, false-segmented-pointer, non-target-invariance, and user runtime gates before promotion."
        ],
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    try:
        shown_report = str(args.out_json.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown_report = str(args.out_json.resolve())
    print(json.dumps({"ok": True, "counts": report["counts"], "screen_evidence": screen_rows, "report": shown_report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
