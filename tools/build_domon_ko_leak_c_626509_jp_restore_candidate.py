#!/usr/bin/env python3
"""Confirm live source for Master Asia ``こい　멍청한　놈이！！``.

A/B proved the battle-voice family at ``5D956C``/``5D9747`` is not live: both
probes still showed the Korean mixed line.  Static expand of scenario bank62
matches the on-screen two-line box exactly:

- ``626501`` = ``……윽！``
- ``626509`` = leading ``18``(=``こ``) + ``E5 18 3C 20...`` → ``이……　멍청한　놈이！！``

This candidate restores only ``626509`` to the pristine Japanese payload.
Everything else, including ``626501`` and the discarded 5D family, stays
byte-exact to the current main TIP.  Main TIP / live SaveRAM are never written.
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

from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/domon_ko_leak_c_626509_jp_restore_candidate.wsc"
OUT_SAVE = ROOT / "sram/domon_ko_leak_c_626509_jp_restore_candidate.sav"
REPORT = ROOT / "out/patch/domon_ko_leak_c_626509_jp_restore_report.json"

MAIN_SHA = "2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483"
ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"

ABS = 0x626509
TERM = 0x626518
CURRENT = bytes.fromhex("18E5183C2001010101010101010101")
ORIGINAL_PAYLOAD = bytes.fromhex("1807F362F19114F0812005F879F044")
PAIR_ABS = 0x626501
PAIR_CURRENT = bytes.fromhex("173418E518F299")


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rr(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    save = SAVE.read_bytes()
    if sha(parent) != MAIN_SHA or sha(original) != ORIGINAL_SHA:
        raise RuntimeError("ROM identity drift")
    if len(save) != 32768:
        raise RuntimeError("SaveRAM size drift")

    tbl = Tbl.load(TBL_PATH)
    od = Dictionary(original, stock_count=3831)
    sb = stock_base(parent)

    got, term = rr(parent, ABS)
    if got != CURRENT or term != TERM:
        raise RuntimeError(f"parent target drift {ABS:06X}")
    pair, _ = rr(parent, PAIR_ABS)
    if pair != PAIR_CURRENT:
        raise RuntimeError(f"pair line drift {PAIR_ABS:06X}")
    og, oterm = rr(original, ABS)
    if og != ORIGINAL_PAYLOAD or oterm != TERM or len(og) != len(CURRENT):
        raise RuntimeError("original target drift")

    body_jp = od.expand(og[1:], tbl)
    with_lead = "こ" + body_jp

    cand = bytearray(parent)
    cand[sb + ABS : sb + ABS + len(ORIGINAL_PAYLOAD)] = ORIGINAL_PAYLOAD
    allowed = [(sb + ABS, sb + ABS + len(ORIGINAL_PAYLOAD))]
    checksum = update_ws_checksum(cand)
    allowed.append((len(cand) - 2, len(cand)))
    cb = bytes(cand)

    ng, nt = rr(cb, ABS)
    if ng != ORIGINAL_PAYLOAD or nt != TERM or cb[sb + TERM] != 0:
        raise RuntimeError("candidate verify failed")
    pair2, _ = rr(cb, PAIR_ABS)
    if pair2 != PAIR_CURRENT:
        raise RuntimeError("pair line must stay byte-exact")
    # Discarded battle-voice family must remain main-identical on this probe.
    for a, expect in (
        (0x5D956C, bytes.fromhex("4AE518378701010101010101010101")),
        (0x5D9747, bytes.fromhex("4AE518378701010101010101010101")),
    ):
        g, _ = rr(cb, a)
        if g != expect:
            raise RuntimeError(f"unexpected 5D family change {a:06X}")

    outside = [run for run in diff_runs(parent, cb) if not covered(run, allowed)]
    if outside:
        raise RuntimeError(f"diff escape {outside[:4]}")

    OUT.write_bytes(cb)
    shutil.copy2(SAVE, OUT_SAVE)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "tools/build_domon_ko_leak_c_626509_jp_restore_candidate.py",
        "status": "pending_user_runtime_validation",
        "parent_sha256": sha(parent),
        "candidate_sha256": sha(cb),
        "checksum": f"{checksum:04X}",
        "saveram_sha256": sha(OUT_SAVE.read_bytes()),
        "main_unchanged": sha(MAIN.read_bytes()) == MAIN_SHA,
        "live_saveram_unchanged": SAVE.read_bytes() == save,
        "diagnosis": {
            "ab_result": "A/B both still showed Korean こい line; 5D956C/5D9747 family is not live",
            "live_pair": {
                "line1_abs": f"{PAIR_ABS:06X}",
                "line1_raw": PAIR_CURRENT.hex().upper(),
                "line1_ko": "……윽！",
                "line2_abs": f"{ABS:06X}",
                "line2_raw_main": CURRENT.hex().upper(),
                "line2_ko_with_lead_18": "こ이……　멍청한　놈이！！",
            },
            "why": "leading raw 18 renders as こ; body E5183C20 is the Korean portal; A/B never touched 626509",
        },
        "patch": {
            "abs": f"{ABS:06X}",
            "before": CURRENT.hex().upper(),
            "after": ORIGINAL_PAYLOAD.hex().upper(),
            "terminator": f"{TERM:06X}",
            "expected_body_jp": body_jp,
            "expected_with_visible_lead_18": with_lead,
        },
        "runtime_gate": [
            "cold boot with paired SaveRAM; do not reuse old .State",
            "line1 should still be ……윽！",
            "line2 must change away from こい……　멍청한　놈이！！ to Japanese 、この……うつけものがぁっ！！ (leading こ from 18 may still show)",
            "if Korean mixed line remains unchanged, 626509 is still not live; next check 62607C",
        ],
        "unexpected_diff_runs": 0,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rom": str(OUT.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(cb), "checksum": f"{checksum:04X}", "save": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/")}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
