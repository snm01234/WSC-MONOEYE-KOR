#!/usr/bin/env python3
"""Audit meaningful Japanese dialogue/prose still visible in the current TIP.

This is intentionally narrower than a raw character-class scan.  Banks 52, 56,
5A, 5B and 76 contain tables/binary that decode to kana-like garbage, so those
records are counted as structural noise rather than translation targets.

The audit combines four proven text populations:

* short script dialogue left by the pre-opening/P2 capacity passes;
* bank-59 mission dialogue in the Original-derived vetted aux population;
* bank-5D/5E battle voice bodies after their one-code-unit voice id;
* bank-5C profile/ability prose fragments.

It also verifies that every already-applied aux record is currently free of
Japanese text, checks reviewed Korean values for every remaining target, and
measures current ext3/stock capacity.  It never writes a ROM or SaveRAM.
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from apply_name75_ko import ext3_bank_room
from build_p2_stock_spill_candidate import _stock_phrase_cursor
from extract_script import split_prefix_body
from hangul_marker import marker_code
from measure_aux_prefix_rule import code_units
from mixed_residual_classification import (
    core_character_count,
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import Dictionary, Tbl, dict_token_safe_in_zstring, load_rom, read_encoded_z_safe, stock_base
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import list_free_ext3_indices

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
AUX_POPULATION_REPORT = ROOT / "out/patch/current_tip_aux_sentence_rate_post_aux_promotion.json"
AUX_APPLY_REPORT = ROOT / "out/patch/main_p1_prefix_batch25_all_remaining_ui_clean/aux_ko_report.json"
FALSE_PREFIX_SPEC = ROOT / "data/aux_false_prefix_cleanup_ko.json"
PROMOTED_AUX_CLEANUP_POST = ROOT / "out/patch/aux_duplicate_false_prefix_cleanup_postpromotion_audit.json"
SCRIPT_COVERAGE = ROOT / "out/patch/preopening_ext3_coverage_report.json"
OUT = ROOT / "out/patch/current_untranslated_dialogue_audit.json"

# These were false text records in the old pre-opening quality sheet.  Their
# byte patterns belong to data/control structures, not displayed sentences.
SCRIPT_NON_TEXT = {
    0x603C2E,
    0x603C79,
    0x603C9F,
    0x603E3E,
    0x603E4C,
    0x603EA9,
    0x603EB7,
    0x603F25,
    0x603F64,
}

MEANINGFUL_AUX_BANKS = {0x59: "mission_dialogue", 0x5C: "description_fragment", 0x5D: "battle_voice", 0x5E: "battle_voice"}
NOISE_BANKS = {0x52, 0x56, 0x5A, 0x5B, 0x76}


class AuditError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256(payload)}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be an object: {path}")
    return value


def load_reviewed_values() -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for name in sorted(glob.glob(str(ROOT / "data/mixed_residual_values/*.json"))):
        document = load_json(Path(name))
        entries = document.get("entries") or {}
        if not isinstance(entries, dict):
            continue
        for record_id, row in entries.items():
            if isinstance(row, Mapping) and str(row.get("ko") or "").strip():
                out[str(record_id)] = row
    return out


def render_record(rom: bytes, dictionary: Any, tbl: Tbl, logical: int, *, max_len: int = 256) -> tuple[bytes, str]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=max_len)
    if got is None:
        raise AuditError(f"unreadable record: {logical:06X}")
    payload = bytes(got[0])
    return payload, dictionary.expand(payload, tbl)


def aux_body(payload: bytes, bank: int) -> tuple[bytes, bytes, str]:
    if bank == 0x59:
        prefix, body, _kind = split_prefix_body(payload)
        return prefix, body, "script_grammar"
    if bank in (0x5D, 0x5E):
        units = code_units(payload)
        prefix_len = units[0][1] if units else 0
        return payload[:prefix_len], payload[prefix_len:], "one_code_unit_voice_id"
    return b"", payload, "full_record"


def classify_text(text: str) -> dict[str, int | str]:
    stripped = text.rstrip("\u3000 \t")
    japanese = japanese_character_count(stripped)
    hangul = hangul_character_count(stripped)
    core = core_character_count(stripped)
    classification = "mixed" if japanese and hangul else "jp_only" if japanese else "ko_only" if hangul else "no_text"
    return {"text": stripped, "japanese": japanese, "hangul": hangul, "core": core, "classification": classification}


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


def encoded_storage(texts: set[str], tbl: Tbl) -> tuple[int, list[str]]:
    total = 0
    failures: list[str] = []
    for text in sorted(texts):
        normalized = normalize_ko_text(text)
        payload = try_encode_ko_text(
            normalized,
            tbl,
            hangul_marker_code=marker_code(),
            hangul_marker_mode="run",
        )
        if payload is None or b"\x00" in payload:
            failures.append(text)
            continue
        total += len(payload) + 1
    return total, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, default=TIP)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise AuditError("refusing to write a ROM")

    current = bytes(load_rom(args.rom))
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    dictionary = make_dictionary_ext3(current, ext_meta, ext3_meta)
    values = load_reviewed_values()

    # Verify all records previously declared applied remain free of Japanese.
    apply_report = load_json(AUX_APPLY_REPORT)
    false_spec = load_json(FALSE_PREFIX_SPEC)
    cleanup_post = load_json(PROMOTED_AUX_CLEANUP_POST)
    if cleanup_post.get("ok") is not True:
        raise AuditError("promoted aux cleanup post-audit did not pass")
    false_targets = {int(str(row["abs"]), 16) for row in false_spec.get("targets") or []}
    already_fixed = {int(str(row["abs"]), 16) for row in false_spec.get("already_fixed") or []}
    promoted_text_initial_cleanups = {
        int(str(row["abs"]), 16)
        for row in ((cleanup_post.get("targets") or {}).get("target_checks") or [])
        if row.get("ok") is True
    }
    applied_rows = {int(str(row["abs"]), 16): row for row in apply_report.get("applied") or []}
    applied_residuals: list[dict[str, Any]] = []
    for logical, row in sorted(applied_rows.items()):
        payload, _full = render_record(current, dictionary, tbl, logical, max_len=256)
        prefix_len = (
            0
            if logical in false_targets
            or logical in already_fixed
            or logical in promoted_text_initial_cleanups
            else int(row.get("prefix_bytes") or 0)
        )
        rendered = dictionary.expand(payload[prefix_len:], tbl)
        classified = classify_text(rendered)
        if int(classified["japanese"]):
            applied_residuals.append({"abs": f"{logical:06X}", **classified})
    if applied_residuals:
        raise AuditError(f"Japanese returned in {len(applied_residuals)} applied aux records")

    # Script records from the bounded pre-opening coverage population.
    coverage = load_json(SCRIPT_COVERAGE)
    script_rows: list[dict[str, Any]] = []
    script_noise: list[dict[str, Any]] = []
    for source in coverage.get("remaining_rows") or []:
        logical = int(str(source["abs"]), 16)
        payload, _full = render_record(current, dictionary, tbl, logical)
        prefix, body, kind = split_prefix_body(payload)
        classified = classify_text(dictionary.expand(body, tbl))
        if not int(classified["japanese"]):
            continue
        row = {
            "record_id": f"script:{logical:06X}",
            "abs": f"{logical:06X}",
            "kind": kind,
            "prefix_hex": prefix.hex().upper(),
            "body_capacity": len(body),
            "payload_hex": payload.hex().upper(),
            "source_text": classified["text"],
            "ko": str(source.get("ko") or ""),
            "translation_ready": bool(str(source.get("ko") or "").strip()),
        }
        if logical in SCRIPT_NON_TEXT:
            script_noise.append(row)
        else:
            script_rows.append(row)

    # Current vetted aux population, excluding already-applied records.
    population_report = load_json(AUX_POPULATION_REPORT)
    bound_sha = str(((population_report.get("inputs") or {}).get("working_rom") or {}).get("sha256") or "")
    if bound_sha != sha256(current):
        raise AuditError(f"aux population report is bound to {bound_sha}, current ROM is {sha256(current)}")

    aux_rows: list[dict[str, Any]] = []
    noise_counts: collections.Counter[str] = collections.Counter()
    noise_samples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for population_row in (population_report.get("population") or {}).get("records") or []:
        logical = int(str(population_row["abs"]), 16)
        if logical in applied_rows:
            continue
        bank = logical >> 16
        payload, _full = render_record(current, dictionary, tbl, logical, max_len=128)
        prefix, body, prefix_rule = aux_body(payload, bank)
        classified = classify_text(dictionary.expand(body, tbl))
        if not int(classified["japanese"]) or int(classified["core"]) < 6:
            continue
        record_id = f"aux:{logical:06X}"
        reviewed = values.get(record_id) or {}
        row = {
            "record_id": record_id,
            "abs": f"{logical:06X}",
            "bank": f"{bank:02X}",
            "category": MEANINGFUL_AUX_BANKS.get(bank, "structural_noise"),
            "prefix_rule": prefix_rule,
            "prefix_hex": prefix.hex().upper(),
            "body_capacity": len(body),
            "payload_hex": payload.hex().upper(),
            "source_text": classified["text"],
            "japanese_chars": classified["japanese"],
            "hangul_chars": classified["hangul"],
            "ko": str(reviewed.get("ko") or ""),
            "translation_ready": bool(str(reviewed.get("ko") or "").strip()),
        }
        if bank in MEANINGFUL_AUX_BANKS:
            aux_rows.append(row)
        else:
            noise_counts[f"{bank:02X}"] += 1
            if len(noise_samples[f"{bank:02X}"]) < 3:
                noise_samples[f"{bank:02X}"].append(row)

    meaningful = script_rows + aux_rows
    missing_translations = [row["record_id"] for row in meaningful if not row["translation_ready"]]
    if missing_translations:
        raise AuditError("reviewed Korean missing for: " + ", ".join(missing_translations))

    category_counts = collections.Counter(str(row.get("category") or "script_dialogue") for row in meaningful)
    # Script rows do not carry category above.
    category_counts["script_dialogue"] += len(script_rows)
    category_counts.pop("script_dialogue" if not script_rows else "", None)

    short_rows = [row for row in meaningful if int(row["body_capacity"]) < 4]
    ext3_rows = [row for row in meaningful if int(row["body_capacity"]) >= 4]
    short_phrases = {str(row["ko"]) for row in short_rows}
    ext3_phrases = {str(row["ko"]) for row in ext3_rows}

    exact_slots: dict[str, list[str]] = {text: [] for text in short_phrases}
    for index in range(dictionary.count):
        try:
            rendered = dictionary.expand(bytes(dictionary.raw_entry(index)), tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        if rendered in exact_slots:
            exact_slots[rendered].append(f"{index:04X}")
    exact_reuse = {text: slots for text, slots in exact_slots.items() if slots}
    new_short_phrases = short_phrases - set(exact_reuse)

    ext3_bytes, ext3_encoding_failures = encoded_storage(ext3_phrases, tbl)
    stock_bytes, stock_encoding_failures = encoded_storage(new_short_phrases, tbl)
    if ext3_encoding_failures or stock_encoding_failures:
        raise AuditError("one or more reviewed translations are not encodable")

    num_banks = int(ext3_meta["num_banks"])
    room_by_bank = ext3_bank_room(current, num_banks)
    free_ext3 = list_free_ext3_indices(current, num_banks=num_banks)
    stock_cursor = _stock_phrase_cursor(current)
    strong_retired = current_strong_retired_slots(original, current, dictionary)

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_current_untranslated_dialogue.py",
        "read_only": True,
        "ok": True,
        "current_tip": identity(args.rom, current),
        "counts": {
            "meaningful_untranslated_records": len(meaningful),
            "dialogue_and_voice_subtotal": len(script_rows)
            + sum(1 for row in aux_rows if row["category"] in {"mission_dialogue", "battle_voice"}),
            "description_fragments": sum(1 for row in aux_rows if row["category"] == "description_fragment"),
            "script_dialogue": len(script_rows),
            "mission_dialogue_bank59": sum(1 for row in aux_rows if row["category"] == "mission_dialogue"),
            "battle_voice_bank5d5e": sum(1 for row in aux_rows if row["category"] == "battle_voice"),
            "already_applied_aux_clean": len(applied_rows),
            "already_applied_aux_japanese_residuals": len(applied_residuals),
            "excluded_script_data_rows": len(script_noise),
            "excluded_aux_noise_sentence_like": sum(noise_counts.values()),
            "translations_ready": sum(bool(row["translation_ready"]) for row in meaningful),
            "translations_missing": len(missing_translations),
        },
        "patch_plan": {
            "direct_ext3_records": len(ext3_rows),
            "direct_ext3_unique_phrases": len(ext3_phrases),
            "direct_ext3_phrase_bytes_required_including_nul": ext3_bytes,
            "short_token_records": len(short_rows),
            "short_unique_phrases": len(short_phrases),
            "existing_exact_phrase_reuse": len(exact_reuse),
            "new_stock_phrases_required": len(new_short_phrases),
            "new_stock_phrase_bytes_required_including_nul": stock_bytes,
            "exact_reuse_slots": exact_reuse,
        },
        "capacity": {
            "ext3_free_slots": len(free_ext3),
            "ext3_total_phrase_room": sum(room_by_bank.values()),
            "ext3_room_by_bank": {f"{index:02X}": room for index, room in room_by_bank.items()},
            "stock_tail_cursor": f"{stock_cursor:04X}",
            "stock_tail_room": 0x10000 - stock_cursor,
            "strong_retired_stock_slots": len(strong_retired),
            "strong_retired_sample": [f"{index:04X}" for index in strong_retired[:32]],
            "capacity_sufficient": (
                len(free_ext3) >= len(ext3_phrases)
                and sum(room_by_bank.values()) >= ext3_bytes
                and len(strong_retired) >= len(new_short_phrases)
                and (0x10000 - stock_cursor) >= stock_bytes
            ),
        },
        "records": {
            "script_dialogue": script_rows,
            "mission_dialogue": [row for row in aux_rows if row["category"] == "mission_dialogue"],
            "battle_voice": [row for row in aux_rows if row["category"] == "battle_voice"],
            "description_fragments": [row for row in aux_rows if row["category"] == "description_fragment"],
        },
        "excluded_noise": {
            "script_rows": script_noise,
            "aux_counts_by_bank": dict(sorted(noise_counts.items())),
            "aux_samples_by_bank": dict(sorted(noise_samples.items())),
            "policy": "banks 52/56/5A/5B/76 are not patch targets without a separate runtime/table proof",
        },
        "recommendation": {
            "additional_patch_possible": True,
            "recommended_batches": [
                "batch A: 88 records with body capacity >=4 via local ext3 replacement",
                "batch B: 20 short records via 5 exact two-byte-token reuses plus 11 new guarded retired stock phrases",
            ],
            "promotion_policy": "candidate-only build, static gates, then user visual verification before main TIP promotion",
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "counts": report["counts"], "patch_plan": report["patch_plan"], "capacity": report["capacity"], "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
