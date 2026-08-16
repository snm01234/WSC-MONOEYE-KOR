#!/usr/bin/env python3
"""Build STAGE18 control-text + Rona terminology follow-up candidate.

Parent main TIP is never modified. The candidate:
- standardizes 베라 로나 / 마이처 로나 in active dictionary consumers;
- repairs STAGE18 6002F1 by dropping the proven visible leading 18=こ and
  retargeting the line to a new non-alias ext3 phrase;
- preserves record extent, terminator, following control and live SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta  # noqa: E402
from build_dialogue_20cell_candidate import alias_bank_cursor, encode  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_stage17t_global_20cell_followup_candidate import (  # noqa: E402
    active_dictionary,
    allocate_unique_phrases,
)
from monoeye_rom import BANK_SIZE, Tbl, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from patch_3byte_dict_token import token_from_ext3_index, write_ext3_dictionary_slots  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC = ROOT / "data/stage18_terminology_control_followup_ko.json"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT = PATCH / "stage18_terminology_control_followup_candidate.wsc"
OUT_TBL = PATCH / "stage18_terminology_control_followup_candidate.tbl"
OUT_SAVE = ROOT / "sram/stage18_terminology_control_followup_candidate.sav"
REPORT = PATCH / "stage18_terminology_control_followup_candidate_report.json"

EXPECTED_PARENT_SHA = "d6b3caa433f174348e885c1eced9dae64a5ac8976a67ae0363a31d5cbe541f2e"
EXPECTED_TBL_SHA = "5a6189f77e198dc3fd0b891778eb80512ba60537a74cf9b4353db2bcc520f47a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha(data)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=512)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def trim(text: str) -> str:
    return text.rstrip("\u3000 \t")


def find_alias_free_gap(bank: bytes, need: int) -> tuple[int, int]:
    """Find an unreferenced gap without repacking live alias phrases."""
    intervals: list[tuple[int, int]] = []
    seen_ptrs: set[int] = set()
    for local in range(0x1000):
        ptr = struct.unpack_from("<H", bank, local * 2)[0]
        if not (0x2000 <= ptr < BANK_SIZE) or ptr in seen_ptrs:
            continue
        seen_ptrs.add(ptr)
        end = bank.find(b"\x00", ptr)
        if end < 0:
            raise BuildError(f"unterminated alias phrase while finding gap: {ptr:04X}")
        intervals.append((ptr, end + 1))
    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    gaps: list[tuple[int, int]] = []
    cursor = 0x2000
    for start, end in merged:
        if start > cursor and start - cursor >= need:
            gaps.append((cursor, start - cursor))
        cursor = max(cursor, end)
    if BANK_SIZE - cursor >= need:
        gaps.append((cursor, BANK_SIZE - cursor))
    if not gaps:
        raise BuildError(f"alias bank has no unreferenced gap for {need} bytes")
    return min(gaps, key=lambda item: (item[1], item[0]))


def main() -> int:
    parent = MAIN.read_bytes()
    tbl_bytes = TBL_PATH.read_bytes()
    save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"main identity drift: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError("active TBL identity drift")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drift: {len(save)}")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("parent_main_sha256") != EXPECTED_PARENT_SHA:
        raise BuildError("spec parent SHA drift")
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks != 16:
        raise BuildError(f"ext3 bank count drift: {num_banks}")
    parent_dict = active_dictionary(parent, ext_meta, ext3_meta)
    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # 1) STAGE18 6002F1: remove visible leading 18=こ and retarget to a
    # dedicated non-alias phrase. Record extent and terminator stay fixed.
    srow = spec["scenario_rewrite"]
    logical = int(srow["abs"], 16)
    before, term = payload_at(parent, logical)
    expected_before = bytes.fromhex(srow["before_payload_hex"])
    if before != expected_before or term != int(srow["terminator"], 16):
        raise BuildError(f"STAGE18 target drift at {logical:06X}")
    if trim(parent_dict.expand(before, tbl)) != srow["before_render"]:
        raise BuildError("STAGE18 current visible leak signature drift")
    next_addr = int(srow["next_address"], 16)
    sb = stock_base(parent)
    next_control = bytes.fromhex(srow["next_control_hex"])
    if parent[sb + next_addr : sb + next_addr + len(next_control)] != next_control:
        raise BuildError("STAGE18 following control drift")
    after_text = str(srow["after"])
    if len(after_text) > int(spec.get("cell_limit") or 20):
        raise BuildError("STAGE18 replacement exceeds 20 cells")
    stage_encoded = encode(after_text, tbl)
    slot_payload, encoded_to_index = allocate_unique_phrases(
        parent, {stage_encoded: after_text}, num_banks=num_banks
    )
    write_info = write_ext3_dictionary_slots(candidate, slot_payload, num_banks=num_banks)
    if int(write_info.get("written") or 0) != 1 or int(write_info.get("skipped_overflow") or 0):
        raise BuildError(f"new STAGE18 ext3 write failed: {write_info}")
    new_index = encoded_to_index[stage_encoded]
    off = new_index - 0x1000
    page, local = off >> 12, off & 0x0FFF
    if page < 5 and local >= 0x0600:
        raise BuildError(f"unsafe alias-range slot allocated: {new_index:05X}")
    new_token = token_from_ext3_index(new_index, num_banks=num_banks)
    after_payload = new_token + b"\x01" * (len(before) - len(new_token))
    if len(after_payload) != len(before):
        raise BuildError("STAGE18 record extent mismatch")
    candidate[sb + logical : sb + logical + len(after_payload)] = after_payload
    allowed.append((sb + logical, sb + logical + len(after_payload)))

    # 2) Ordinary/extended dictionary entries: in-place same-or-shorter rewrite.
    dict_changes: list[dict[str, Any]] = []
    for row in spec.get("dictionary_rewrites") or []:
        index = int(row["index"], 16)
        before_text = trim(parent_dict.expand_index(index, tbl))
        if before_text != row["before"]:
            raise BuildError(f"dictionary source drift {index:05X}: {before_text!r}")
        raw_before = parent_dict.raw_entry(index)
        raw_after = encode(str(row["after"]), tbl)
        if len(raw_after) > len(raw_before):
            raise BuildError(f"dictionary expansion would grow {index:05X}")
        abs_off = parent_dict.entry_abs(index)
        span = len(raw_before) + 1
        replacement = raw_after + b"\x00" * (span - len(raw_after))
        candidate[abs_off : abs_off + span] = replacement
        allowed.append((abs_off, abs_off + span))
        dict_changes.append({
            "index": f"{index:05X}",
            "entry_abs": f"{abs_off:07X}",
            "before": row["before"],
            "after": row["after"],
            "old_bytes": len(raw_before),
            "new_bytes": len(raw_after),
        })

    # 3) Five-page alias dictionary entries. Two fit in-place; the encyclopedia
    # phrase grows, so that one is repointed to new room in the same physical bank.
    alias_changes: list[dict[str, Any]] = []
    for row in spec.get("alias_dictionary_rewrites") or []:
        seg = int(row["segment"], 16)
        local_idx = int(row["local"], 16)
        expected_ptr = int(row["expected_pointer"], 16)
        bank_start = seg * BANK_SIZE
        bank = bytearray(candidate[bank_start : bank_start + BANK_SIZE])
        ptr = struct.unpack_from("<H", bank, local_idx * 2)[0]
        if ptr != expected_ptr:
            raise BuildError(f"alias pointer drift {seg:02X}:{local_idx:04X}: {ptr:04X}")
        end = bank.find(b"\x00", ptr)
        if end < 0:
            raise BuildError(f"alias phrase unterminated {seg:02X}:{local_idx:04X}")
        raw_before = bytes(bank[ptr:end])
        before_text = trim(parent_dict.expand(raw_before, tbl))
        if before_text != row["before"]:
            raise BuildError(f"alias source drift {seg:02X}:{local_idx:04X}: {before_text!r}")
        raw_after = encode(str(row["after"]), tbl)
        mode = str(row["mode"])
        if mode == "in_place":
            if len(raw_after) > len(raw_before):
                raise BuildError(f"alias in-place growth {seg:02X}:{local_idx:04X}")
            span = len(raw_before) + 1
            bank[ptr : ptr + span] = raw_after + b"\x00" * (span - len(raw_after))
            allowed.append((bank_start + ptr, bank_start + ptr + span))
            new_ptr = ptr
        elif mode == "repoint":
            need = len(raw_after) + 1
            cursor, gap_size = find_alias_free_gap(bytes(bank), need)
            bank[cursor : cursor + len(raw_after)] = raw_after
            bank[cursor + len(raw_after)] = 0
            struct.pack_into("<H", bank, local_idx * 2, cursor)
            allowed.append((bank_start + local_idx * 2, bank_start + local_idx * 2 + 2))
            allowed.append((bank_start + cursor, bank_start + cursor + need))
            new_ptr = cursor
        else:
            raise BuildError(f"unknown alias rewrite mode: {mode}")
        candidate[bank_start : bank_start + BANK_SIZE] = bank
        alias_changes.append({
            "segment": f"{seg:02X}",
            "local": f"{local_idx:04X}",
            "old_pointer": f"{ptr:04X}",
            "new_pointer": f"{new_ptr:04X}",
            "mode": mode,
            "before": row["before"],
            "after": row["after"],
            "old_bytes": len(raw_before),
            "new_bytes": len(raw_after),
        })

    # Allow the new generic ext3 slot pointer and phrase bytes.
    interim = bytes(candidate)
    interim_dict = active_dictionary(interim, ext_meta, ext3_meta)
    for index, encoded in slot_payload.items():
        seg, local_idx = interim_dict._ext3_bank_local(index)
        if interim_dict._ext3_is_alias(index):
            raise BuildError(f"new STAGE18 slot became alias-mapped: {index:05X}")
        bank_start = seg * BANK_SIZE
        ptr = int.from_bytes(interim[bank_start + local_idx * 2 : bank_start + local_idx * 2 + 2], "little")
        allowed.append((bank_start + local_idx * 2, bank_start + local_idx * 2 + 2))
        allowed.append((bank_start + ptr, bank_start + ptr + len(encoded) + 1))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum verification failed")
    unexpected = [run for run in diff_runs(parent, result) if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:12]}")

    final_dict = active_dictionary(result, ext_meta, ext3_meta)
    final_payload, final_term = payload_at(result, logical)
    if final_term != term or len(final_payload) != len(before) or final_payload[:1] == b"\x18":
        raise BuildError("STAGE18 final record structure failed")
    if trim(final_dict.expand(final_payload, tbl)) != after_text:
        raise BuildError(f"STAGE18 final render mismatch: {trim(final_dict.expand(final_payload, tbl))!r}")
    if result[sb + next_addr : sb + next_addr + len(next_control)] != next_control:
        raise BuildError("STAGE18 follow control changed")
    for row in spec.get("dictionary_rewrites") or []:
        if trim(final_dict.expand_index(int(row["index"], 16), tbl)) != row["after"]:
            raise BuildError(f"final dictionary render mismatch {row['index']}")
    for row in spec.get("alias_dictionary_rewrites") or []:
        seg = int(row["segment"], 16)
        local_idx = int(row["local"], 16)
        bank_start = seg * BANK_SIZE
        ptr = int.from_bytes(result[bank_start + local_idx * 2 : bank_start + local_idx * 2 + 2], "little")
        end = result.find(b"\x00", bank_start + ptr)
        if end < 0:
            raise BuildError(f"final alias unterminated {seg:02X}:{local_idx:04X}")
        raw = result[bank_start + ptr:end]
        if trim(final_dict.expand(raw, tbl)) != row["after"]:
            raise BuildError(f"final alias render mismatch {seg:02X}:{local_idx:04X}")
    if MAIN.read_bytes() != parent:
        raise BuildError("main TIP changed during candidate build")
    if SAVE.read_bytes() != save:
        raise BuildError("live SaveRAM changed during candidate build")

    atomic_bytes(OUT, result)
    atomic_bytes(OUT_TBL, tbl_bytes)
    atomic_bytes(OUT_SAVE, save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage18_terminology_control_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_validation_required",
        "promotion_performed": False,
        "inputs": {
            "main": identity(MAIN, parent),
            "tbl": identity(TBL_PATH, tbl_bytes),
            "live_saveram_snapshot": identity(SAVE, save),
            "spec": identity(SPEC),
        },
        "outputs": {
            "candidate_rom": identity(OUT, result),
            "candidate_tbl": identity(OUT_TBL, tbl_bytes),
            "candidate_saveram": identity(OUT_SAVE, save),
        },
        "stage18": {
            "address": srow["abs"],
            "before_payload": before.hex().upper(),
            "after_payload": final_payload.hex().upper(),
            "before_render": srow["before_render"],
            "after_render": after_text,
            "new_ext3_index": f"{new_index:05X}",
            "new_ext3_token": new_token.hex().upper(),
            "terminator": srow["terminator"],
            "next_control_preserved": True,
        },
        "terminology": {
            "dictionary_rewrites": dict_changes,
            "alias_dictionary_rewrites": alias_changes,
        },
        "allocation": {
            "strategy": "one runtime-non-alias true-free ext3 phrase for 6002F1; terminology dictionaries rewritten in-place except one alias repoint",
            "writer": write_info,
        },
        "checks": {
            "main_untouched": MAIN.read_bytes() == parent,
            "live_saveram_untouched": SAVE.read_bytes() == save,
            "stage18_max_20_cells": len(after_text) <= 20,
            "stage18_leading_18_removed": final_payload[:1] != b"\x18",
            "stage18_extent_preserved": len(final_payload) == len(before),
            "stage18_terminator_preserved": final_term == term,
            "stage18_follow_control_preserved": True,
            "new_stage18_slot_non_alias": not final_dict._ext3_is_alias(new_index),
            "terminology_dictionary_entries_verified": True,
            "terminology_alias_entries_verified": True,
            "unexpected_diff_runs": len(unexpected) == 0,
            "ws_checksum_valid": True,
        },
        "diff": {
            "changed_bytes": sum(hi - lo for lo, hi in diff_runs(parent, result)),
            "changed_runs": len(diff_runs(parent, result)),
            "unexpected_runs": len(unexpected),
        },
        "ws_checksum": f"{checksum:04X}",
        "runtime_validation_targets": [
            "STAGE18 디아나: 그가 나서면 전란은 더 커진다。 다음 줄이 '그것은 그대도 잘 알고 있을 터인데！'로 표시되고 선두 こ가 없어야 함",
            "베라 로나 / 마이처 로나가 시나리오, name75, 도감 계열에서 통일되어 표시되는지",
            "6002F1 뒤 이벤트가 정상 진행되고 초상/제어문 회귀가 없는지",
        ],
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["outputs"]["candidate_rom"],
        "stage18": report["stage18"],
        "terminology_counts": {
            "dictionary": len(dict_changes),
            "alias": len(alias_changes),
        },
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
