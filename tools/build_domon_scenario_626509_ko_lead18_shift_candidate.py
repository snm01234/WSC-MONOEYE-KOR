#!/usr/bin/env python3
"""Repair live Master Asia line ``626509`` by shifting off leading ``こ``.

Runtime C probe proved this scenario address is live:
``626501`` = ``……윽！``, ``626509`` = leading ``18``(=``こ``) + Korean portal
``E5 18 3C 20`` → ``이……　멍청한　놈이！！``.

The approved local pattern from ``614F0A`` / early garrod repair applies:
remove only the leading raw ``18``, slide the existing Korean ext3 portal one
byte forward, keep payload extent and terminator ``626518`` fixed, and leave the
following control ``62651A=17 28 01 06`` byte-exact.

Main TIP and live SaveRAM are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/domon_scenario_626509_ko_lead18_shift_candidate.wsc"
OUT_SAVE = ROOT / "sram/domon_scenario_626509_ko_lead18_shift_candidate.sav"
REPORT = ROOT / "out/patch/domon_scenario_626509_ko_lead18_shift_report.json"

MAIN_SHA = "2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483"
EXT = {"stock_count": 3831, "slot_count": 265, "ext_ptr_off": "0000", "ext_seg": "10", "ext_in_expansion": True}
EXT3 = {"num_banks": 16, "exp_seg0": "11"}

ABS = 0x626509
TERM = 0x626518
CURRENT = bytes.fromhex("18E5183C2001010101010101010101")
AFTER = bytes.fromhex("E5183C200101010101010101010101")  # drop lead 18; +1 pad
PAIR_ABS = 0x626501
PAIR_CURRENT = bytes.fromhex("173418E518F299")
FOLLOW_ABS = 0x62651A
FOLLOW_CURRENT = bytes.fromhex("17280106")
EXPECTED_KO = "이……　멍청한　놈이！！"


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rr(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def trim(text: str) -> str:
    return text.rstrip("　 \t")


def main() -> int:
    parent = MAIN.read_bytes()
    save = SAVE.read_bytes()
    if sha(parent) != MAIN_SHA:
        raise RuntimeError("main identity drift")
    if len(save) != 32768:
        raise RuntimeError("SaveRAM size drift")
    if len(AFTER) != len(CURRENT):
        raise RuntimeError("extent mismatch")

    tbl = Tbl.load(TBL_PATH)
    d = make_dictionary_ext3(parent, EXT, EXT3)
    sb = stock_base(parent)

    got, term = rr(parent, ABS)
    if got != CURRENT or term != TERM or parent[sb + TERM] != 0:
        raise RuntimeError(f"target drift {ABS:06X}")
    if rr(parent, PAIR_ABS)[0] != PAIR_CURRENT:
        raise RuntimeError("pair line drift")
    if rr(parent, FOLLOW_ABS)[0] != FOLLOW_CURRENT:
        raise RuntimeError("follow control drift")

    # Prove current leak and repaired semantic without lead 18.
    if trim(d.expand(CURRENT[1:], tbl)) != EXPECTED_KO:
        raise RuntimeError("current Korean portal drift")
    if trim(d.expand(AFTER, tbl)) != EXPECTED_KO:
        raise RuntimeError("shifted portal render mismatch")
    if AFTER[:1] == b"\x18" or b"\x18" == AFTER[0:1]:
        raise RuntimeError("lead 18 still present")
    if AFTER[0:4] != bytes.fromhex("E5183C20"):
        raise RuntimeError("portal must remain exclusive E5183C20")

    cand = bytearray(parent)
    cand[sb + ABS : sb + ABS + len(AFTER)] = AFTER
    allowed = [(sb + ABS, sb + ABS + len(AFTER))]
    checksum = update_ws_checksum(cand)
    allowed.append((len(cand) - 2, len(cand)))
    cb = bytes(cand)
    fd = make_dictionary_ext3(cb, EXT, EXT3)

    ng, nt = rr(cb, ABS)
    if ng != AFTER or nt != TERM or cb[sb + TERM] != 0:
        raise RuntimeError("candidate record/terminator verify failed")
    if trim(fd.expand(ng, tbl)) != EXPECTED_KO:
        raise RuntimeError("candidate expand still wrong")
    if ng[:1] == b"\x18":
        raise RuntimeError("candidate still starts with 18")
    if rr(cb, PAIR_ABS)[0] != PAIR_CURRENT:
        raise RuntimeError("pair line changed")
    if rr(cb, FOLLOW_ABS)[0] != FOLLOW_CURRENT:
        raise RuntimeError("follow control changed")
    # Bytes immediately after terminator through follow control stay exact.
    if cb[sb + TERM : sb + FOLLOW_ABS + len(FOLLOW_CURRENT)] != parent[sb + TERM : sb + FOLLOW_ABS + len(FOLLOW_CURRENT)]:
        raise RuntimeError("post-terminator boundary drift")

    outside = [run for run in diff_runs(parent, cb) if not covered(run, allowed)]
    if outside:
        raise RuntimeError(f"diff escape {outside[:4]}")

    OUT.write_bytes(cb)
    shutil.copy2(SAVE, OUT_SAVE)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "tools/build_domon_scenario_626509_ko_lead18_shift_candidate.py",
        "status": "pending_user_runtime_validation",
        "parent_sha256": sha(parent),
        "candidate_sha256": sha(cb),
        "checksum": f"{checksum:04X}",
        "saveram_sha256": sha(OUT_SAVE.read_bytes()),
        "main_unchanged": sha(MAIN.read_bytes()) == MAIN_SHA,
        "live_saveram_unchanged": SAVE.read_bytes() == save,
        "method": "614F0A-style: delete leading raw 18 only; slide existing exclusive ext3 portal forward; keep extent/terminator/follow-control",
        "live_bind": {
            "line1": {"abs": f"{PAIR_ABS:06X}", "raw": PAIR_CURRENT.hex().upper(), "ko": "……윽！"},
            "line2": {"abs": f"{ABS:06X}", "before": CURRENT.hex().upper(), "after": AFTER.hex().upper(), "ko": EXPECTED_KO},
            "follow_control": {"abs": f"{FOLLOW_ABS:06X}", "raw": FOLLOW_CURRENT.hex().upper()},
            "terminator": f"{TERM:06X}",
        },
        "runtime_gate": [
            "cold boot with paired SaveRAM; do not reuse old .State",
            "line1 ……윽！ unchanged",
            "line2 must be 이……　멍청한　놈이！！ with no leading こ",
            "event must continue past 62651A control without early stop",
        ],
        "unexpected_diff_runs": 0,
        "discarded_wrong_bind": ["5D956C", "5D9747", "domon_ko_leak_ab_*", "domon_runtime_structure_followup_v2/v3 for this scene"],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "rom": str(OUT.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha(cb),
                "checksum": f"{checksum:04X}",
                "save": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
                "after": AFTER.hex().upper(),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
