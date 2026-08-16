#!/usr/bin/env python3
"""Build the 최후의 승리자 bank59 dialogue follow-up candidate.

The live main TIP is the parent and is never modified.  Every target currently
uses one direct E5 18 ext3 portal.  Instead of growing/repacking an old ext3
bank, this builder allocates a new *true-free* ext3 slot in a bank with measured
phrase room and replaces only the four-byte E5 18 token in the source record.
The route length, prefix, padding, terminator and all neighboring control bytes
remain byte-exact.  Every Korean replacement is capped at 20 visible cells.
"""
from __future__ import annotations

import json
import os
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from apply_name75_ko import ext3_bank_room  # noqa: E402
from build_dialogue_20cell_candidate import encode, ext3_index  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_stage16t_event_followup_candidate import (  # noqa: E402
    BuildError,
    identity,
    original_body_text,
    payload_at,
    sha,
    strip_pad,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from patch_3byte_dict_token import (  # noqa: E402
    INDEX_BASE,
    list_free_ext3_indices,
    token_from_ext3_index,
    write_ext3_dictionary_slots,
)

PATCH = ROOT / "out/patch"
PARENT = PATCH / "monoeye_ko_expanded.wsc"
PARENT_TBL = PATCH / "hangul_patch_pad3.tbl"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
SPEC = ROOT / "data/final_winner_stage_followup_ko.json"
OUT_ROM = PATCH / "final_winner_stage_followup_candidate.wsc"
OUT_TBL = PATCH / "final_winner_stage_followup_candidate.tbl"
OUT_SAVE = ROOT / "sram/final_winner_stage_followup_candidate.sav"
REPORT = PATCH / "final_winner_stage_followup_candidate_report.json"

EXPECTED_PARENT_SHA = "d5fb5d338875f9a5ff1071f04c3b042fcff1a3f38142aae09b6bf9e44ad0fac5"
EXPECTED_TBL_SHA = "5a6189f77e198dc3fd0b891778eb80512ba60537a74cf9b4353db2bcc520f47a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# The active runtime still applies the historical five-page E5 18 alias rule
# even though the old exact-leaf hash detector no longer recognizes the later
# composite runtime.  For pages 0..4, raw locals >= 0x0600 are therefore NOT
# ordinary ext3 slots: runtime redirects them into physical banks 0x21..0x25.
# Never allocate a new generic dialogue phrase in this range.
RUNTIME_ALIAS_PAGE_COUNT = 5
RUNTIME_ALIAS_LOCAL_START = 0x0600


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def allocate_true_free_slots(
    parent: bytes,
    encoded_rows: list[dict[str, Any]],
    *,
    num_banks: int,
) -> tuple[dict[int, bytes], list[dict[str, Any]]]:
    room = ext3_bank_room(parent, num_banks)
    # Scan live E5 18 portals once.  Re-running a 16 MiB bytes.count() for every
    # empty dictionary slot is needlessly quadratic and used to make this guard
    # time out.  Membership in this one-pass set is the same zero-occurrence
    # proof for a candidate free token.
    used_tokens: set[bytes] = set()
    cursor = 0
    while True:
        hit = parent.find(b"\xE5\x18", cursor)
        if hit < 0:
            break
        if hit + 4 <= len(parent):
            used_tokens.add(parent[hit : hit + 4])
        cursor = hit + 2

    free_by_bank: dict[int, list[int]] = defaultdict(list)
    for index in list_free_ext3_indices(parent, num_banks=num_banks):
        off = index - INDEX_BASE
        page, local = off >> 12, off & 0x0FFF
        if page < RUNTIME_ALIAS_PAGE_COUNT and local >= RUNTIME_ALIAS_LOCAL_START:
            continue
        token = token_from_ext3_index(index, num_banks=num_banks)
        # "empty pointer" alone is not enough: 5967F3 is a real example of an
        # empty-looking slot whose token still occurs in a live record.  Only a
        # token absent from the one-pass live-token set is considered truly free.
        if token in used_tokens:
            continue
        bank_index = (index - INDEX_BASE) >> 12
        free_by_bank[bank_index].append(index)

    slot_payload: dict[int, bytes] = {}
    assignments: list[dict[str, Any]] = []
    # Largest phrases first avoids stranding the remaining phrase room.
    for row in sorted(encoded_rows, key=lambda item: (-len(item["encoded"]), item["logical"])):
        need = len(row["encoded"]) + 1
        candidates = [
            (room.get(bank_index, 0) - need, bank_index)
            for bank_index in sorted(free_by_bank)
            if free_by_bank[bank_index] and room.get(bank_index, 0) >= need
        ]
        if not candidates:
            raise BuildError(f"no true-free ext3 slot with {need} phrase bytes for {row['abs']}")
        # Best fit: consume the tightest bank that still fits.
        _remain, bank_index = min(candidates)
        index = free_by_bank[bank_index].pop(0)
        room[bank_index] -= need
        slot_payload[index] = row["encoded"]
        assignments.append({**row, "new_index": index, "new_bank_index": bank_index})
    return slot_payload, assignments


def main() -> int:
    parent = PARENT.read_bytes()
    tbl_bytes = PARENT_TBL.read_bytes()
    save = PARENT_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"main parent identity drifted: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError("active TBL identity drifted")
    # SaveRAM is live user validation state by project policy.  Bind the
    # candidate to the current 32 KiB snapshot, never to an old fixed hash.
    if len(save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drifted: {len(save)}")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    rows = list(spec.get("ext3_rewrites") or [])
    if spec.get("stage") != "최후의 승리자" or len(rows) != 34:
        raise BuildError("stage spec identity/count drifted")
    if any(len(str(row.get("ko") or "")) > 20 for row in rows):
        raise BuildError("source spec contains a >20-cell replacement")

    tbl = Tbl.load(PARENT_TBL)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 bank metadata missing")
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)

    prepared: list[dict[str, Any]] = []
    old_tokens: set[bytes] = set()
    for row in rows:
        logical = int(row["abs"], 16)
        raw, term = payload_at(parent, logical)
        pos = raw.find(b"\xE5\x18")
        if pos not in (0, 1, 3) or raw.find(b"\xE5\x18", pos + 2) >= 0:
            raise BuildError(f"{logical:06X} is not a single direct E5 18 record")
        old_token = raw[pos : pos + 4]
        if len(old_token) != 4 or parent.count(old_token) != 1:
            raise BuildError(f"{logical:06X} old ext3 token is not private")
        if old_token in old_tokens:
            raise BuildError(f"duplicate old token in target set at {logical:06X}")
        old_tokens.add(old_token)
        old_index = ext3_index(old_token)
        if old_index is None:
            raise BuildError(f"cannot decode old ext3 index at {logical:06X}")
        jp = original_body_text(original, original_dictionary, tbl, logical, pos)
        if jp != row["jp"]:
            raise BuildError(f"JP source drift {logical:06X}: {jp!r} != {row['jp']!r}")
        encoded = encode(str(row["ko"]), tbl)
        prepared.append(
            {
                "abs": row["abs"],
                "logical": logical,
                "jp": row["jp"],
                "ko": row["ko"],
                "encoded": encoded,
                "old_token": old_token,
                "old_index": old_index,
                "token_pos": pos,
                "record_hex_before": raw.hex().upper(),
                "terminator": term,
            }
        )

    slot_payload, assignments = allocate_true_free_slots(
        parent, prepared, num_banks=num_banks
    )
    unsafe_alias_assignments = []
    for job in assignments:
        off = int(job["new_index"]) - INDEX_BASE
        page, local = off >> 12, off & 0x0FFF
        if page < RUNTIME_ALIAS_PAGE_COUNT and local >= RUNTIME_ALIAS_LOCAL_START:
            unsafe_alias_assignments.append((job["abs"], page, local))
    if unsafe_alias_assignments:
        raise BuildError(f"runtime alias-range allocation escaped filter: {unsafe_alias_assignments[:8]}")
    candidate = bytearray(parent)
    write_info = write_ext3_dictionary_slots(candidate, slot_payload, num_banks=num_banks)
    if int(write_info.get("skipped_overflow") or 0) or int(write_info.get("written") or 0) != len(assignments):
        raise BuildError(f"ext3 write incomplete: {write_info}")

    allowed: list[tuple[int, int]] = []
    change_rows: list[dict[str, Any]] = []
    sb = stock_base(candidate)
    for job in assignments:
        logical = int(job["logical"])
        pos = int(job["token_pos"])
        new_index = int(job["new_index"])
        new_token = token_from_ext3_index(new_index, num_banks=num_banks)
        before_raw, before_term = payload_at(parent, logical)
        file_pos = sb + logical + pos
        candidate[file_pos : file_pos + 4] = new_token
        after_raw, after_term = payload_at(candidate, logical)
        if before_term != after_term or len(before_raw) != len(after_raw):
            raise BuildError(f"record extent/terminator drift at {logical:06X}")
        if after_raw[:pos] != before_raw[:pos] or after_raw[pos + 4 :] != before_raw[pos + 4 :]:
            raise BuildError(f"non-token record bytes changed at {logical:06X}")
        allowed.append((file_pos, file_pos + 4))
        change_rows.append(
            {
                "abs": job["abs"],
                "jp": job["jp"],
                "ko": job["ko"],
                "cells": len(job["ko"]),
                "token_pos": pos,
                "old_token": job["old_token"].hex().upper(),
                "old_index": f"{int(job['old_index']):05X}",
                "new_token": new_token.hex().upper(),
                "new_index": f"{new_index:05X}",
                "record_before": before_raw.hex().upper(),
                "record_after": after_raw.hex().upper(),
                "terminator": f"{after_term:06X}",
            }
        )

    final_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    focused_render: dict[str, str] = {}
    for row in change_rows:
        logical = int(row["abs"], 16)
        raw, _term = payload_at(candidate, logical)
        pos = int(row["token_pos"])
        rendered = strip_pad(final_dictionary.expand(raw[pos:], tbl))
        if rendered != row["ko"]:
            raise BuildError(f"final render mismatch {logical:06X}: {rendered!r} != {row['ko']!r}")
        focused_render[row["abs"]] = rendered

    # Allow only the new slot pointer and newly appended phrase bytes for each
    # allocation.  No old dictionary storage is touched.
    for index, encoded in slot_payload.items():
        seg, local = final_dictionary._ext3_bank_local(index)
        bank = slice_expansion_bank(candidate, seg)
        ptr = struct.unpack_from("<H", bank, local * 2)[0]
        allowed.append((seg * BANK_SIZE + local * 2, seg * BANK_SIZE + local * 2 + 2))
        allowed.append((seg * BANK_SIZE + ptr, seg * BANK_SIZE + ptr + len(encoded) + 1))

    if focused_render.get("5967F3") != "오라버니……？":
        raise BuildError("Relena 5967F3 honorific correction missing")
    if focused_render.get("59689D") != "이건、그러기　위한　싸움입니다！":
        raise BuildError("59689D semantic correction missing")

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:10]}")
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum verification failed")
    if PARENT.read_bytes() != parent:
        raise BuildError("live main changed while building candidate")

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_TBL, tbl_bytes)
    atomic_bytes(OUT_SAVE, save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_final_winner_stage_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified",
        "promotion_performed": False,
        "stage": "최후의 승리자",
        "inputs": {
            "parent_main": identity(PARENT, parent),
            "active_tbl": identity(PARENT_TBL, tbl_bytes),
            "live_saveram_snapshot": identity(PARENT_SAVE, save),
            "spec": identity(SPEC),
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, result),
            "candidate_tbl": identity(OUT_TBL, tbl_bytes),
            "candidate_saveram": identity(OUT_SAVE, save),
        },
        "allocation": {
            "strategy": "runtime_non_alias_true_free_ext3_slot_plus_same_length_record_token_swap",
            "written_slots": len(slot_payload),
            "writer": write_info,
        },
        "changes": {"ext3_token_rewrites": sorted(change_rows, key=lambda row: row["abs"])},
        "focused_render": focused_render,
        "checks": {
            "live_main_untouched": PARENT.read_bytes() == parent,
            "record_extent_and_terminators_preserved": True,
            "record_non_token_bytes_preserved": True,
            "old_tokens_private": True,
            "new_tokens_true_free_before_write": True,
            "new_tokens_outside_runtime_alias_range": not unsafe_alias_assignments,
            "runtime_alias_rule_assumed": {
                "pages": RUNTIME_ALIAS_PAGE_COUNT,
                "local_start": f"{RUNTIME_ALIAS_LOCAL_START:04X}",
                "reason": "active composite runtime keeps the 5-page alias mapping after the old exact hash detector became stale",
            },
            "all_replacements_max_20_cells": max(len(row["ko"]) for row in rows) <= 20,
            "relena_honorific_corrected": focused_render.get("5967F3") == "오라버니……？",
            "diff_allowlist_clean": not unexpected,
            "candidate_tbl_exact_parent": OUT_TBL.read_bytes() == tbl_bytes,
            "candidate_saveram_exact_snapshot": OUT_SAVE.read_bytes() == save,
            "ws_checksum_valid": True,
        },
        "diff": {
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "changed_runs": len(runs),
            "unexpected_runs": len(unexpected),
        },
        "ws_checksum": f"{checksum:04X}",
        "runtime_validation_targets": [
            "59665B: (강해졌구나……리리나) 대신 지구권/자비가 ID커맨드 문구가 출력되지 않는지",
            "5966AF/5966BD: 이미 세계는 평화를 향해 / 나아가기 시작했습니다！！가 정상 연속되고 운명/개척 ID커맨드 문구가 끼지 않는지",
            "59673B-59678F: 리리나의 히이로/젝스 대화가 잘리지 않고 정상 진행되는지",
            "59676E/59677E: 티탄즈라는 비열한 조직 / 생크 킹덤의 수치 문장이 끝까지 보이는지",
            "5967F3: 리리나가 형이 아니라 오라버니……？라고 표시되는지",
            "596807-5968BF: 젝스 설명 대사의 20셀 초과 잘림과 의미 왜곡이 없는지",
            "596DD3-596E78: 후반 바톤 계열 대사가 20셀 내에서 자연스럽게 보이는지",
            "이벤트 진행, 초상/스프라이트, 제어문 노출에 회귀가 없는지",
        ],
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["outputs"]["candidate_rom"],
        "rewrites": len(change_rows),
        "max_cells": max(len(row["ko"]) for row in rows),
        "allocation": report["allocation"],
        "diff": report["diff"],
        "checksum": report["ws_checksum"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
