#!/usr/bin/env python3
"""Build a single-variable probe for the Diana/Phil event cursor corruption.

Runtime state evidence from Beetle WonderSwan shows RAM 0x01FE is 0x19C3 just
before advancing past 6019B7, then becomes 0x3F3D and 0x3F57 while unrelated
bank60 data is rendered.  The previous repair restored the top-level source
shape [dict, dict, dict, char], but its second token (02D6 = '　님') is itself
nested (F04B + glyph), unlike the pristine direct-token 034F = 'さま'.

This probe changes only that structural difference:
- rewrite proven-unreachable stock slot 0C5E in-place to direct bytes for '　님'
  (01 EC8D E811), with no nested dictionary token;
- retarget only the second stock token of 6019B7 from F2D6 to FC5E;
- keep 6019B7 extent/terminator, 6019C1 double-NUL, and the following 17 28
  event-control bytes byte-exact.

The parent candidate and main TIP are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_stage17t_global_20cell_followup_candidate import active_dictionary  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base, token_from_dict_index, update_ws_checksum  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT = PATCH / "stage18_event_terminology_ui_followup_candidate.wsc"
PARENT_TBL = PATCH / "stage18_event_terminology_ui_followup_candidate.tbl"
PARENT_SAVE = ROOT / "sram/stage18_event_terminology_ui_followup_candidate.sav"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT = PATCH / "diana_state_cursor_direct_stock_probe.wsc"
OUT_TBL = PATCH / "diana_state_cursor_direct_stock_probe.tbl"
OUT_SAVE = ROOT / "sram/diana_state_cursor_direct_stock_probe.sav"
REPORT = PATCH / "diana_state_cursor_direct_stock_probe_report.json"

EXPECTED_PARENT_SHA = "1ce84f3edfd4733d2f06f9679501561be36f51f09b3c947746ffd37f432106e8"
EXPECTED_TBL_SHA = "9d31d1fd1a5022b54f83866d285f4d656461b30d4046ef2b6261a9441abca914"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
TARGET = 0x6019B7
TARGET_TERM = 0x6019C1
TARGET_BEFORE = bytes.fromhex("173418F8E7F2D6F60C03")
DIRECT_SLOT = 0x0C5E
DIRECT_SLOT_OLD = bytes.fromhex("F41335F350")
DIRECT_SLOT_NEW = bytes.fromhex("01EC8DE811")  # full-width space + EC8D + 님
EXPECTED_FOLLOW = bytes.fromhex("00001728010600082600")


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def record_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=64)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def main() -> int:
    parent = PARENT.read_bytes()
    tbl_bytes = PARENT_TBL.read_bytes()
    save = PARENT_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent identity drift: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError(f"TBL identity drift: {sha(tbl_bytes)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"paired SaveRAM size drift: {len(save)}")

    tbl = Tbl.load(PARENT_TBL)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = active_dictionary(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)

    payload, term = record_at(parent, TARGET)
    if payload != TARGET_BEFORE or term != TARGET_TERM:
        raise BuildError(f"6019B7 drift: payload={payload.hex().upper()} term={term:06X}")
    if parent[sb + TARGET_TERM : sb + TARGET_TERM + len(EXPECTED_FOLLOW)] != EXPECTED_FOLLOW:
        raise BuildError("6019C1 double-NUL/follow-control drift")

    old_raw = bytes(dictionary.raw_entry(DIRECT_SLOT))
    if old_raw != DIRECT_SLOT_OLD:
        raise BuildError(f"0C5E raw drift: {old_raw.hex().upper()}")
    entry_abs = int(dictionary.entry_abs(DIRECT_SLOT))
    ptr = dictionary.ptrs[DIRECT_SLOT]
    aliases = [i for i, value in enumerate(dictionary.ptrs) if value == ptr]
    interiors = [i for i, value in enumerate(dictionary.ptrs) if ptr < value <= ptr + len(old_raw)]
    if aliases != [DIRECT_SLOT] or interiors:
        raise BuildError(f"0C5E storage alias hazard aliases={aliases} interiors={interiors}")

    wanted = {DIRECT_SLOT}
    external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(dictionary, wanted=wanted, ext3_aware=True)
    raw_hits = _raw_pair_hits(parent, [DIRECT_SLOT])
    if external.get(DIRECT_SLOT) or nested.get(DIRECT_SLOT) or raw_hits.get(DIRECT_SLOT):
        raise BuildError("0C5E is no longer runtime-unreachable")

    # Bind the structural hypothesis: parent 02D6 is nested, new 0C5E is direct.
    if bytes(dictionary.raw_entry(0x02D6)) != bytes.fromhex("F04BE811"):
        raise BuildError("02D6 nested-token proof drift")
    if bytes(dictionary.raw_entry(0x060C)) != bytes.fromhex("F19103"):
        raise BuildError("060C proof drift")

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # Rewrite exactly the 5-byte dead phrase storage; its following NUL is kept.
    candidate[entry_abs : entry_abs + len(DIRECT_SLOT_NEW)] = DIRECT_SLOT_NEW
    allowed.append((entry_abs, entry_abs + len(DIRECT_SLOT_NEW)))

    direct_token = token_from_dict_index(DIRECT_SLOT)
    if direct_token != bytes.fromhex("FC5E"):
        raise BuildError(f"unexpected 0C5E token {direct_token.hex().upper()}")
    after = bytes.fromhex("173418F8E7") + direct_token + bytes.fromhex("F60C03")
    if len(after) != len(payload):
        raise BuildError("6019B7 extent would change")
    candidate[sb + TARGET : sb + TARGET + len(after)] = after
    allowed.append((sb + TARGET, sb + TARGET + len(after)))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)

    final_dict = active_dictionary(result, ext_meta, ext3_meta)
    final_payload, final_term = record_at(result, TARGET)
    if final_payload != after or final_term != TARGET_TERM:
        raise BuildError("6019B7 final record verification failed")
    if result[sb + TARGET_TERM : sb + TARGET_TERM + len(EXPECTED_FOLLOW)] != EXPECTED_FOLLOW:
        raise BuildError("6019C1/follow control changed")
    if bytes(final_dict.raw_entry(DIRECT_SLOT)) != DIRECT_SLOT_NEW:
        raise BuildError("0C5E direct fragment write failed")
    if final_dict.expand_index(DIRECT_SLOT, tbl) != "　님":
        raise BuildError(f"0C5E render mismatch: {final_dict.expand_index(DIRECT_SLOT, tbl)!r}")
    if final_dict.expand(final_payload[3:], tbl) != "디아나　님……！！":
        raise BuildError(f"6019B7 render mismatch: {final_dict.expand(final_payload[3:], tbl)!r}")

    final_external = external_occurrence_map(result, ext3_aware=True, wanted=wanted)
    final_nested = nested_occurrence_map(final_dict, wanted=wanted, ext3_aware=True)
    final_raw = _raw_pair_hits(result, [DIRECT_SLOT])
    expected_token_abs = TARGET + 5
    external_pos = sorted(int(str(x["token_abs"]), 16) for x in final_external.get(DIRECT_SLOT, []))
    raw_pos = sorted(int(str(x["token_abs"]), 16) for x in final_raw.get(DIRECT_SLOT, []))
    if external_pos != [expected_token_abs] or raw_pos != [expected_token_abs] or final_nested.get(DIRECT_SLOT):
        raise BuildError(
            f"0C5E final reference proof failed ext={external_pos} raw={raw_pos} nested={final_nested.get(DIRECT_SLOT)}"
        )

    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:8]}")
    stored = int.from_bytes(result[-2:], "little")
    if stored != (sum(result[:-2]) & 0xFFFF) or stored != checksum:
        raise BuildError("checksum validation failed")

    atomic_bytes(OUT, result)
    atomic_bytes(OUT_TBL, tbl_bytes)
    atomic_bytes(OUT_SAVE, save)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_diana_state_cursor_direct_stock_probe.py",
        "status": "runtime_test_pending",
        "parent": identity(PARENT, parent),
        "candidate": {**identity(OUT, result), "checksum": f"{checksum:04X}"},
        "tbl": identity(OUT_TBL, tbl_bytes),
        "saveram": identity(OUT_SAVE, save),
        "savestate_evidence": {
            "state0_ram_01FE": "19C3",
            "state1_ram_01FE": "3F3D",
            "state2_ram_01FE": "3F57",
            "state0_far_segment_0200": "3000",
            "interpretation": "cursor is correct at 6019C3 before advance, then jumps into unrelated bank60 data at 603F3D/603F57",
        },
        "probe": {
            "target": "6019B7",
            "before_hex": payload.hex().upper(),
            "after_hex": after.hex().upper(),
            "visible_text": "디아나　님……！！",
            "before_second_token": "F2D6 -> stock 02D6 raw F04BE811 (nested)",
            "after_second_token": "FC5E -> stock 0C5E raw 01EC8DE811 (direct, no nested token)",
            "source_shape_goal": "preserve top-level [dict,dict,dict,char] while making the second token direct like pristine 034F",
        },
        "guards": {
            "6019B7_extent_preserved": True,
            "6019C1_double_nul_and_follow_control_preserved": True,
            "0C5E_parent_external_zero": True,
            "0C5E_parent_nested_zero": True,
            "0C5E_parent_raw_pair_zero": True,
            "0C5E_unique_storage": True,
            "0C5E_final_only_consumer_6019BC": True,
            "unexpected_diff_runs": 0,
        },
        "diff": {
            "runs": len(runs),
            "bytes": sum(b - a for a, b in runs),
        },
        "runtime_gate": [
            "Load the same event before '디아나 님……！！'.",
            "After advancing, no unrelated/glitch dialogue should appear.",
            "If a new state is saved immediately after the advance, RAM 0x01FE must not become 0x3F3D/0x3F57; it should follow the intended 6019C3 control path.",
        ],
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
