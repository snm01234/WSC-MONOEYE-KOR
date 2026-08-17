#!/usr/bin/env python3
"""Build a compact menu-help follow-up from the user-tested spill candidate.

Scope (2026-08-16):
* Preserve the verified `배속` private-spill repair.
* Fix nine `목록` title routes that still depend on reused stock slot 005E,
  which now renders `그건`.
* Compact every menu-help record ending in `표시합니다` or `선택합니다`
  across EARLY_UI + data/ui_spill_ko.json.  Each verified record already has a
  first ext3 token that renders the complete requested Korean phrase, so all
  trailing zero-width filler tokens and visible 0x01 bytes are removed and the
  record terminates immediately after that first ext3 token.

This is candidate-only.  The current main TIP and live main SaveRAM are never
modified.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_menu_help_weapon_padding_candidate import EARLY_UI  # noqa: E402
from build_menu_help_supply_status_spill_followup_candidate import (  # noqa: E402
    ASSIGN_TITLE_POINTERS,
    TARGETS as PREV_HELP_TARGETS,
    TARGET_POINTERS as PREV_HELP_POINTERS,
    choose_safe_ext3_slots,
    diff_runs,
    identity,
    payload_at,
    pointer_hits_in_table,
    read_le16_logical,
    render_payload,
    sha,
    write_le16_logical,
)
from build_remaining_dialogue_candidate import encode_phrase  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_3byte_dict_token import bank_local_for_index, token_from_ext3_index  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT_ROM = PATCH / "menu_help_supply_status_spill_followup_candidate.wsc"
PARENT_SAVE = ROOT / "sram/menu_help_supply_status_spill_followup_candidate.sav"
MAIN_ROM = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META_PATH = PATCH / "exp_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"
UI_SPILL = ROOT / "data/ui_spill_ko.json"
OUT_ROM = PATCH / "menu_help_list_select_compact_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/menu_help_list_select_compact_followup_candidate.sav"
REPORT = PATCH / "menu_help_list_select_compact_followup_candidate_report.json"

EXPECTED_PARENT_SHA = "462e5f7e812546d49ee21f5b979c7dec8d3f8deada5e738d8026db5778cc4a3c"
EXPECTED_MAIN_SHA = "f62f14d15f3d76ad2eb33e2b55531ab4781d230cb63bcc2348bbd703d8c39be3"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
LIST_STOCK_SLOT = 0x005E
# This duplicate unit-help record was made unreachable by the parent spill
# candidate when all duplicate routes were retargeted to 5F2831.
LIST_SPILL_LOGICAL = 0x5F2843
EXPECTED_TARGETS = 60
EXPECTED_LIST_TITLES = 9
EXPECTED_NONLIST = 51
EXPECTED_VISIBLE_01_BEFORE = 83


class BuildError(RuntimeError):
    pass


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def load_scope() -> list[tuple[int, str, str]]:
    merged: OrderedDict[int, tuple[int, str, str]] = OrderedDict()
    for logical, jp, ko in EARLY_UI:
        merged[logical] = (logical, jp, ko)
    spec = json.loads(UI_SPILL.read_text(encoding="utf-8-sig"))
    for row in spec.get("lines") or []:
        logical = int(str(row["abs"]), 16)
        item = (logical, str(row["jp"]), str(row["ko"]))
        if logical in merged and merged[logical][2] != item[2]:
            raise BuildError(f"scope disagreement at {logical:06X}")
        merged[logical] = item
    selected = [
        row for row in merged.values()
        if row[2] == "목록" or row[2].endswith("표시합니다") or row[2].endswith("선택합니다")
    ]
    selected.sort(key=lambda row: row[0])
    return selected


def main() -> int:
    parent = PARENT_ROM.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    main_before = MAIN_ROM.read_bytes()
    main_save_before = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent identity drifted: {sha(parent)}")
    if len(parent_save) != SAVE_SIZE:
        raise BuildError("parent SaveRAM missing/wrong size")
    if len(main_before) != ROM_SIZE or sha(main_before) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if len(main_save_before) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing/wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 metadata unavailable")
    parent_dict = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    stock_dict = Dictionary(parent)

    scope = load_scope()
    list_rows = [row for row in scope if row[2] == "목록"]
    compact_rows = [row for row in scope if row[2] != "목록"]
    if (len(scope), len(list_rows), len(compact_rows)) != (
        EXPECTED_TARGETS, EXPECTED_LIST_TITLES, EXPECTED_NONLIST
    ):
        raise BuildError(
            f"target population drifted: total={len(scope)} list={len(list_rows)} compact={len(compact_rows)}"
        )

    old_list_token = token_from_dict_index(LIST_STOCK_SLOT)
    old_list_raw = bytes(stock_dict.raw_entry(LIST_STOCK_SLOT))
    old_list_render = parent_dict.expand(old_list_token, tbl)
    if old_list_render.rstrip("　 \t") == "목록":
        raise BuildError("stock slot 005E unexpectedly renders 목록 again")

    rows_before: list[dict[str, Any]] = []
    visible_01_before = 0
    for logical, _jp, ko in scope:
        raw, term = payload_at(parent, logical, max_len=96)
        visible_01_before += raw.count(0x01)
        hits = pointer_hits_in_table(parent, logical)
        if ko == "목록":
            if raw != old_list_token + b"\x01":
                raise BuildError(f"list title shape drifted at {logical:06X}: {raw.hex().upper()}")
            if len(hits) != 1:
                raise BuildError(f"list title pointer ownership drift at {logical:06X}: {hits}")
            first_render = None
        else:
            if len(raw) < 4 or raw[:2] != bytes.fromhex("E518"):
                raise BuildError(f"non-list target is not ext3-first at {logical:06X}: {raw.hex().upper()}")
            first_render = parent_dict.expand(raw[:4], tbl)
            if first_render != ko:
                raise BuildError(
                    f"first ext3 token is not complete text at {logical:06X}: {first_render!r} != {ko!r}"
                )
        rows_before.append({
            "abs": f"{logical:06X}",
            "ko": ko,
            "payload_hex": raw.hex().upper(),
            "payload_len": len(raw),
            "visible_01": raw.count(0x01),
            "pointer_hits": [f"{x:06X}" for x in hits],
            "first_token_render": first_render,
            "term_logical": f"{term - stock_base(parent):06X}",
        })
    if visible_01_before != EXPECTED_VISIBLE_01_BEFORE:
        raise BuildError(f"visible 0x01 population drifted: {visible_01_before}")

    # The previous candidate deliberately left 5F2843 unreachable after
    # retargeting duplicate unit-help routes to 5F2831.  Reuse only that proven
    # dead record extent as a private list spill; no dictionary stock slot is
    # touched.
    if pointer_hits_in_table(parent, LIST_SPILL_LOGICAL):
        raise BuildError("planned list spill record is reachable again")
    spill_old, _spill_term = payload_at(parent, LIST_SPILL_LOGICAL, max_len=96)
    spill_span = len(spill_old) + 1
    if spill_span < 5:
        raise BuildError("planned list spill record is too short")

    list_encoded = encode_phrase("목록", tbl)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    alloc_seg, slots, alloc_info = choose_safe_ext3_slots(
        parent, union, ext_meta, ext3_meta, 1, len(list_encoded) + 1
    )
    list_index = slots[0]
    candidate = bytearray(parent)
    ext3_info, ext3_guard = write_ext3_slots_guarded(
        candidate, {list_index: list_encoded}, union=union, num_banks=num_banks
    )
    if int(ext3_info.get("written") or 0) != 1:
        raise BuildError("private 목록 ext3 phrase was not written")
    list_token = token_from_ext3_index(list_index, num_banks=num_banks)

    base = stock_base(candidate)
    changed_record_extents: list[tuple[int, int]] = []
    pointer_extents: list[tuple[int, int]] = []

    # Compact 51 already-correct help records to exactly ext3-token + NUL.
    compacted: list[dict[str, Any]] = []
    for logical, _jp, ko in compact_rows:
        old, _term = payload_at(parent, logical, max_len=96)
        active = old[:4]
        span = len(old) + 1
        start = base + logical
        candidate[start : start + span] = active + b"\x00" + bytes(span - 5)
        changed_record_extents.append((start, start + span))
        compacted.append({
            "abs": f"{logical:06X}",
            "ko": ko,
            "old_len": len(old),
            "new_payload_hex": active.hex().upper(),
            "removed_bytes": len(old) - 4,
            "removed_visible_01": old.count(0x01),
        })

    # Install one private list spill record, then retarget all nine list-title
    # pointer entries directly to it.  Stock slot 005E remains byte-exact.
    spill_start = base + LIST_SPILL_LOGICAL
    candidate[spill_start : spill_start + spill_span] = (
        list_token + b"\x00" + bytes(spill_span - 5)
    )
    changed_record_extents.append((spill_start, spill_start + spill_span))

    list_retargets: list[dict[str, Any]] = []
    for logical, _jp, _ko in list_rows:
        hits = pointer_hits_in_table(parent, logical)
        pointer = hits[0]
        old_off = read_le16_logical(parent, pointer)
        write_le16_logical(candidate, pointer, LIST_SPILL_LOGICAL & 0xFFFF)
        pointer_extents.append((base + pointer, base + pointer + 2))
        list_retargets.append({
            "source_abs": f"{logical:06X}",
            "pointer": f"{pointer:06X}",
            "old_off16": f"{old_off:04X}",
            "new_abs": f"{LIST_SPILL_LOGICAL:06X}",
        })

    checksum = update_ws_checksum(candidate)
    final = bytes(candidate)
    final_dict = make_dictionary_ext3(final, ext_meta, ext3_meta)
    failures: list[dict[str, Any]] = []

    # Every compact help record must now terminate immediately after four bytes,
    # render exactly, and contain zero visible padding.
    visible_01_after = 0
    for logical, _jp, ko in compact_rows:
        raw, term = payload_at(final, logical, max_len=96)
        actual = final_dict.expand(raw, tbl)
        visible_01_after += raw.count(0x01)
        if len(raw) != 4 or raw.count(0x01) or actual != ko or term - stock_base(final) != logical + 4:
            failures.append({
                "abs": f"{logical:06X}", "reason": "compact_route",
                "payload": raw.hex().upper(), "render": actual,
                "term": f"{term - stock_base(final):06X}",
            })

    # Every list route must bypass 005E and resolve through the private spill.
    for logical, _jp, ko in list_rows:
        pointer = pointer_hits_in_table(parent, logical)[0]
        off16 = read_le16_logical(final, pointer)
        active = 0x5F0000 | off16
        raw, term = payload_at(final, active, max_len=32)
        actual = final_dict.expand(raw, tbl)
        visible_01_after += raw.count(0x01)
        if active != LIST_SPILL_LOGICAL or raw != list_token or actual != ko or raw.count(0x01):
            failures.append({
                "source_abs": f"{logical:06X}", "reason": "list_route",
                "pointer": f"{pointer:06X}", "active": f"{active:06X}",
                "payload": raw.hex().upper(), "render": actual,
                "term": f"{term - stock_base(final):06X}",
            })

    # The broken stock slot is deliberately not repaired/reused: this candidate
    # removes the menu's dependency on it.
    stock_after = Dictionary(final)
    if bytes(stock_after.raw_entry(LIST_STOCK_SLOT)) != old_list_raw:
        failures.append({"reason": "stock_005E_changed"})

    # Preserve the user-verified assignment fix.
    for _source, pointer in ASSIGN_TITLE_POINTERS.items():
        off16 = read_le16_logical(final, pointer)
        raw, _term = payload_at(final, 0x5F0000 | off16, max_len=32)
        actual = render_payload(final_dict, tbl, raw)
        if actual != "배속":
            failures.append({"reason": "assignment_regression", "pointer": f"{pointer:06X}", "render": actual})

    # Preserve all 30 routes from the prior status/supply follow-up.
    for logical, _jp, ko, _group in PREV_HELP_TARGETS:
        pointer = PREV_HELP_POINTERS[logical]
        off16 = read_le16_logical(final, pointer)
        raw, _term = payload_at(final, 0x5F0000 | off16, max_len=96)
        actual = render_payload(final_dict, tbl, raw)
        if actual != ko:
            failures.append({
                "reason": "previous_help_regression", "source_abs": f"{logical:06X}",
                "pointer": f"{pointer:06X}", "render": actual,
            })

    # Static diff gate: only selected records, nine list pointers, one ext3 bank,
    # and the WonderSwan checksum/header tail may change.
    allowed = list(changed_record_extents) + list(pointer_extents)
    allowed.append((alloc_seg * BANK_SIZE, (alloc_seg + 1) * BANK_SIZE))
    allowed.append((len(final) - 0x20, len(final)))
    unaccounted: list[dict[str, str]] = []
    for lo, hi in diff_runs(parent, final):
        if not any(a <= lo and hi <= b for a, b in allowed):
            unaccounted.append({"start": f"{lo:08X}", "end": f"{hi:08X}"})
    if unaccounted:
        failures.append({"reason": "unaccounted_diff_runs", "runs": unaccounted})

    if sha(MAIN_ROM.read_bytes()) != EXPECTED_MAIN_SHA or MAIN_SAVE.read_bytes() != main_save_before:
        failures.append({"reason": "main_tip_or_saveram_changed"})

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_menu_help_list_select_compact_followup_candidate.py",
        "ok": not failures,
        "published": False,
        "status": "static_verified_pending_user_runtime_visual_test" if not failures else "failed",
        "parent": identity(PARENT_ROM, parent),
        "candidate": identity(OUT_ROM, final),
        "checksum": f"{checksum:04X}",
        "counts": {
            "targets": len(scope),
            "list_titles": len(list_rows),
            "compact_help_records": len(compact_rows),
            "visible_0x01_before": visible_01_before,
            "visible_0x01_after_active_routes": visible_01_after,
        },
        "diagnosis": {
            "list_stock_slot": f"{LIST_STOCK_SLOT:04X}",
            "list_stock_slot_raw_hex": old_list_raw.hex().upper(),
            "list_stock_slot_render": old_list_render,
            "list_stock_slot_reused_by_candidate": False,
            "list_private_spill_abs": f"{LIST_SPILL_LOGICAL:06X}",
            "all_nonlist_first_ext3_tokens_render_complete_text": True,
            "wide_blank_after_text_note": (
                "If a large white area remains after compact token+NUL records, it is the fixed-width help window, "
                "not string padding. Removing it requires a separate window-geometry change."
            ),
        },
        "allocation": {
            "ext3_segment": f"{alloc_seg:02X}",
            "list_ext3_index": f"{list_index:05X}",
            "list_token_hex": list_token.hex().upper(),
            "guard": ext3_guard.as_dict(),
            "allocation": alloc_info,
        },
        "verification": {
            "all_51_help_records_token_plus_nul": not any(f.get("reason") == "compact_route" for f in failures),
            "all_9_list_routes_render_exact": not any(f.get("reason") == "list_route" for f in failures),
            "active_visible_0x01_zero": visible_01_after == 0,
            "stock_005E_untouched": not any(f.get("reason") == "stock_005E_changed" for f in failures),
            "assignment_fix_preserved": not any(f.get("reason") == "assignment_regression" for f in failures),
            "previous_30_help_routes_preserved": not any(f.get("reason") == "previous_help_regression" for f in failures),
            "diffs_bounded": not unaccounted,
            "main_tip_unchanged": sha(MAIN_ROM.read_bytes()) == EXPECTED_MAIN_SHA,
            "main_saveram_untouched": MAIN_SAVE.read_bytes() == main_save_before,
        },
        "before": rows_before,
        "compacted": compacted,
        "list_retargets": list_retargets,
        "failures": failures,
        "runtime_gate": [
            "목록 메뉴 제목/설명이 더 이상 '그건'으로 표시되지 않고 '목록'으로 표시",
            "표시합니다/선택합니다 계열에서 실제 문자열 trailing filler/0x01이 사라졌는지 확인",
            "문자열 종료 후에도 남는 넓은 흰 영역이 동일하면 고정 폭 도움말 창 영역으로 판정",
            "배속 메뉴 정상 표시 유지",
            "메뉴 이동/상태창/목록/개조/도감 진입과 복귀 안정성 확인",
        ],
        "promotion": "blocked_pending_user_runtime_visual_verification",
    }
    if failures:
        atomic_json(REPORT, report)
        raise BuildError(f"candidate verification failed: {failures[:3]}")

    atomic_bytes(OUT_ROM, final)
    shutil.copyfile(PARENT_SAVE, OUT_SAVE)
    # Refresh identities after files exist.
    report["candidate"] = identity(OUT_ROM)
    report["candidate_save"] = identity(OUT_SAVE)
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "candidate_save": report["candidate_save"],
        "checksum": report["checksum"],
        "counts": report["counts"],
        "diagnosis": report["diagnosis"],
        "verification": report["verification"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
