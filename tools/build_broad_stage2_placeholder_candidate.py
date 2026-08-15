#!/usr/bin/env python3
"""Build the cumulative broad stage-2D unused-placeholder candidate."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_broad_stage2_dialogue_voice_candidate import atomic_bytes, atomic_json, digest, exact_slots, identity, payload_at
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, verify_non_target_invariance
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import BANK_SIZE, DICT_PTR_START, SEG_DICT, Dictionary, Tbl, stock_base, token_from_dict_index, update_ws_checksum
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/broad_stage2_title_ui_candidate.wsc"
SOURCE_AUDIT = ROOT / "out/patch/broad_stage2_title_ui_residual_audit.json"
CATALOG = ROOT / "data/broad_stage2_placeholder_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/broad_stage2_placeholder_candidate.wsc"
OUT_SAVE = ROOT / "sram/broad_stage2_placeholder_candidate.sav"
REPORT = ROOT / "out/patch/broad_stage2_placeholder_report.json"

EXPECTED_MAIN_SHA = "0cdafa9f9293af71766a24e89efc4e60782bf2196b681f882f908426e39390ed"
EXPECTED_PARENT_SHA = "72d8b5571422b2a7c9b6d1f4d4875cba5881226139155aa32d4360f1647037f3"
EXPECTED_SOURCE_SHA = "a5515d99e161ca899dbe1922e5c05795168b5e0dde12552f00f15a764e176065"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_ROWS = 149


class BuildError(RuntimeError):
    pass


def load_rows(parent: bytes) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    source = json.loads(SOURCE_AUDIT.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if source.get("ok") is not True or digest(SOURCE_AUDIT.read_bytes()) != EXPECTED_SOURCE_SHA:
        raise BuildError("source residual audit is not accepted")
    source_rows = []
    for bucket in (source.get("records") or {}).values():
        source_rows.extend(dict(row) for row in (bucket or []))
    by_abs = {str(row.get("abs") or "").upper(): row for row in source_rows}
    rows = []
    seen: set[str] = set()
    for item in catalog.get("lines") or []:
        address = str(item.get("abs") or "").upper()
        if address in seen:
            raise BuildError(f"duplicate address {address}")
        seen.add(address)
        source_row = by_abs.get(address)
        if source_row is None or str(source_row.get("region") or "") != "aux":
            raise BuildError(f"source aux row missing at {address}")
        if str(source_row.get("current_text") or "") not in {"不要", "不用"}:
            raise BuildError(f"unexpected source placeholder at {address}")
        if str(item.get("record_id") or "") != str(source_row.get("record_id") or ""):
            raise BuildError(f"record id mismatch at {address}")
        if str(item.get("jp") or "") != str(source_row.get("current_text") or ""):
            raise BuildError(f"JP mismatch at {address}")
        if str(item.get("body_hex") or "").upper() != str(source_row.get("body_hex") or "").upper():
            raise BuildError(f"body mismatch at {address}")
        if int(source_row.get("body_capacity") or 0) != 2:
            raise BuildError(f"body capacity mismatch at {address}")
        row = dict(source_row)
        row["logical"] = int(address, 16)
        row["ko"] = normalize_ko_text(str(item.get("ko") or ""))
        rows.append(row)
    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != EXPECTED_ROWS or {str(row["ko"]) for row in rows} != {"미사용"}:
        raise BuildError("placeholder catalog population/value drifted")
    return source, catalog, rows


def main() -> int:
    main_bytes = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    parent = PARENT.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(main_bytes) != ROM_SIZE or digest(main_bytes) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA:
        raise BuildError("title/UI parent identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("live main SaveRAM invalid")

    source, catalog, rows = load_rows(parent)
    base = stock_base(parent)
    for row in rows:
        logical = int(row["logical"])
        payload, terminator = payload_at(parent, logical)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        body = bytes.fromhex(str(row.get("body_hex") or ""))
        if payload != prefix + body or len(body) != 2 or terminator != base + logical + len(payload):
            raise BuildError(f"parent binding failed at {logical:06X}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    exact = exact_slots(parent_dictionary, tbl, {"미사용"}).get("미사용") or []
    stock_payloads: dict[int, bytes] = {}
    if exact:
        stock_index = min(exact)
        strategy = "existing_exact_stock"
    else:
        retired = current_strong_retired_slots(original, parent, parent_dictionary)
        if not retired:
            raise BuildError("no strong-retired stock slot available")
        stock_index = retired[0]
        selected = {stock_index}
        if external_occurrence_map(parent, ext3_aware=True, wanted=selected).get(stock_index) or nested_occurrence_map(parent_dictionary, wanted=selected, ext3_aware=True).get(stock_index) or _raw_pair_hits(parent, [stock_index]).get(stock_index):
            raise BuildError("selected retired stock slot remains reachable")
        stock_payloads[stock_index] = encode_phrase("미사용", tbl)
        strategy = "strong_retired_stock"

    candidate = bytearray(parent)
    stock_cursor_before = _stock_phrase_cursor(candidate)
    pointers_before = list(Dictionary(candidate).ptrs)
    if stock_payloads:
        pointers_written, stock_cursor_after = write_dictionary_slots_spill(candidate, stock_payloads, spill_start=SPILL_FLOOR, allow_aux_consumers=False, locs=_working_two_byte_external_refs(bytes(candidate)))
        if list(Dictionary(candidate).ptrs) != pointers_written:
            raise BuildError("stock writer result mismatch")
        changed = {index for index, (before, after) in enumerate(zip(pointers_before, pointers_written)) if before != after}
        if changed != {stock_index}:
            raise BuildError("stock pointer change set mismatch")
    else:
        stock_cursor_after = stock_cursor_before

    token = token_from_dict_index(stock_index)
    target_extents = []
    applied = []
    for row in rows:
        logical = int(row["logical"])
        prefix_len = int(row.get("prefix_bytes") or 0)
        start = base + logical + prefix_len
        candidate[start:start + 2] = token
        target_extents.append((start, start + 2))
        applied.append({"record_id": row["record_id"], "abs": f"{logical:06X}", "jp": row["current_text"], "after": "미사용", "body_capacity": 2, "strategy": strategy, "stock_index": f"{stock_index:04X}", "token_hex": token.hex().upper()})

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    failures = []
    for row in rows:
        logical = int(row["logical"])
        payload, terminator = payload_at(candidate_bytes, logical)
        prefix_len = int(row.get("prefix_bytes") or 0)
        rendered = candidate_dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        if rendered != "미사용" or any(is_japanese_character(c) for c in rendered) or candidate_bytes[terminator] != 0:
            failures.append({"abs": f"{logical:06X}", "actual": rendered})
    invariance = verify_non_target_invariance(parent, candidate_bytes, before_dictionary=parent_dictionary, after_dictionary=candidate_dictionary, tbl=tbl, excluded={int(row["logical"]) for row in rows})

    stock_bank_file = stock_base(parent) + SEG_DICT * BANK_SIZE
    allowed = list(target_extents)
    if stock_payloads:
        allowed.extend([
            (stock_bank_file + DICT_PTR_START + stock_index * 2, stock_bank_file + DICT_PTR_START + stock_index * 2 + 2),
            (stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after),
        ])
    allowed.append((len(parent) - 2, len(parent)))
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [{"start": f"{left:08X}", "end_exclusive": f"{right:08X}"} for left, right in runs if not covered((left, right), allowed)]

    ok = not failures and invariance.get("ok") is True and not unaccounted and digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA and MAIN_SAVE.read_bytes() == main_save
    if not ok:
        raise BuildError("placeholder candidate verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_broad_stage2_placeholder_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_visual_test",
        "main_tip": identity(MAIN, main_bytes),
        "parent_title_ui_candidate": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "source_audit": identity(SOURCE_AUDIT),
        "source_catalog": identity(CATALOG),
        "counts": {"targets": len(rows), "fuyou": sum(row["current_text"] == "不要" for row in rows), "fuyou_variant": sum(row["current_text"] == "不用" for row in rows), "unique_korean_phrases": 1, "new_retired_stock_phrases": len(stock_payloads), "target_failures": len(failures), "non_target_failures": int(invariance.get("failure_count") or 0), "unaccounted_diff_runs": len(unaccounted)},
        "allocation": {"strategy": strategy, "stock_index": f"{stock_index:04X}", "stock_cursor_before": f"{stock_cursor_before:04X}", "stock_cursor_after": f"{stock_cursor_after:04X}", "stock_phrase_bytes": stock_cursor_after-stock_cursor_before},
        "verification": {"all_targets_render_exact": not failures, "target_japanese_residuals_zero": not failures, "non_target_invariance": invariance, "diffs_bounded": not unaccounted, "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA, "main_saveram_untouched": MAIN_SAVE.read_bytes() == main_save, "candidate_saveram_matches_live_at_build": OUT_SAVE.read_bytes() == main_save, "prefix_length_terminator_preserved": True},
        "diff": {"changed_bytes_from_parent": sum(right-left for left,right in runs), "runs": len(runs), "checksum": f"{checksum:04X}"},
        "records": applied,
        "promotion": "blocked_pending_independent_audit_and_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(json.dumps({"ok": True, "candidate": report["candidate"], "counts": report["counts"], "allocation": report["allocation"], "diff": report["diff"], "report": str(REPORT.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
