#!/usr/bin/env python3
"""
Build a bisection set for the new-game "event error 257 / 2049".

READ-ONLY with respect to the tip: every candidate is written to ``out/patch/ab/``
and the tip itself is never modified.

Static scans came back clean (script record lengths and terminators in bank 60 are
byte-for-byte intact, and no unintended stock bytes remain), and no emulator is
available on this machine, so the cause has to be isolated by manual test. Each
candidate removes exactly one change group, so a single boot tells us which group
is responsible.

Candidates, in the order they are worth testing:

``cand1_no_oob``    current tip, but the 3 bytes at ``60:11BA`` put back to the
                    relocated far pointer ``30 ef 06``. Those bytes were restored
                    to the original ``62 84 2e`` as an "out-of-band" fix; if they
                    are a live relocation pointer rather than prose, restoring
                    them sends the walker to the wrong record. Smallest possible
                    delta, so test this first.
``cand2_pre_repair`` the tip as it was *before* any repair this session (the state
                    originally reported: intermission UI broken, battle entry
                    black). If this reaches the opening and the tip does not, the
                    cause is in the repair chain rather than in the ext3 work.
``cand3_no_rehome``  after the stock-invasion repair and the 5F pointer restore,
                    but before the ext3 re-homing pass.
``cand4_pre_ext3``   the tip from before the whole ext3 session — the oldest known
                    reference point.

Each candidate keeps a valid WonderSwan checksum, and the report lists the
checksum per candidate so the running ROM can be identified from the header.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
AB = ROOT / "out/patch/ab"
DEFAULT_OUT = ROOT / "out/patch/event_error_bisect.json"

OOB_SITE = 0x6011BA
OOB_ORIGINAL = bytes.fromhex("62842e")   # what the repair restored
OOB_RELOCATED = bytes.fromhex("30ef06")  # expansion far pointer (seg 0x30)


def emit_patched_tip(dest: Path) -> dict:
    rom = bytearray(load_rom(TIP))
    sb = stock_base(rom)
    at = sb + OOB_SITE
    before = bytes(rom[at : at + 3])
    rom[at : at + 3] = OOB_RELOCATED
    cs = update_ws_checksum(rom)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(rom)
    return {
        "name": dest.stem,
        "path": str(dest),
        "derived_from": str(TIP),
        "change": f"{OOB_SITE:06X}: {before.hex()} -> {OOB_RELOCATED.hex()}",
        "checksum": f"{cs:04X}",
        "unchanged": before == OOB_RELOCATED,
    }


def emit_undo_stock_repair(dest: Path, pre_repair: Path) -> dict:
    """Current tip, with only the stock-invasion repair sites put back.

    That repair rewrote 300 bytes in banks 51-6F, including a 256-byte table at
    ``6B:2477`` and single bytes in the scenario/event banks ``6E``/``6F``. Those
    are the only bytes this session changed outside the dialogue/dictionary data,
    so they are the prime suspects for a failure the game reports as an *event*
    error rather than as wrong text.
    """
    if not pre_repair.exists():
        return {"name": dest.stem, "path": None, "error": f"missing {pre_repair}"}
    rom = bytearray(load_rom(TIP))
    pre = bytes(load_rom(pre_repair))
    sb, sp = stock_base(rom), stock_base(pre)

    report = ROOT / "out/patch/repair_stock_invasion_report.json"
    if not report.exists():
        return {"name": dest.stem, "path": None, "error": f"missing {report}"}
    data = json.loads(report.read_text(encoding="utf-8"))
    target_entry = next(
        (t for t in data["targets"] if Path(t["target"]).name == TIP.name), None
    )
    if target_entry is None:
        return {"name": dest.stem, "path": None, "error": "tip not in repair report"}

    undone: List[dict] = []
    for site in target_entry["sites"]:
        logical = int(site["logical"], 16)
        n = site["len"]
        want = pre[sp + logical : sp + logical + n]
        have = bytes(rom[sb + logical : sb + logical + n])
        if have == want:
            continue
        rom[sb + logical : sb + logical + n] = want
        undone.append({"site": site["site"], "len": n})

    cs = update_ws_checksum(rom)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(rom)
    return {
        "name": dest.stem,
        "path": str(dest),
        "derived_from": str(TIP),
        "change": f"undid {len(undone)} stock-invasion repair sites "
        f"({sum(u['len'] for u in undone)} B) using {pre_repair.name}",
        "sites_undone": undone,
        "checksum": f"{cs:04X}",
    }


def emit_copy(src: Path, dest: Path, note: str) -> dict:
    if not src.exists():
        return {"name": dest.stem, "path": None, "error": f"missing source {src}"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return {
        "name": dest.stem,
        "path": str(dest),
        "derived_from": str(src),
        "change": note,
        "checksum": f"{ws_header(load_rom(dest))['checksum']:04X}",
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pre-repair",
        type=Path,
        default=ROOT / "out/patch/backup/20260726_222810/monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--pre-rehome",
        type=Path,
        default=ROOT / "out/patch/backup/20260726_233043/monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--pre-ext3", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    if not TIP.exists():
        raise SystemExit(f"missing tip: {TIP}")

    cands: List[Dict] = [
        emit_undo_stock_repair(AB / "cand5_no_stock_repair.wsc", args.pre_repair),
        emit_patched_tip(AB / "cand1_no_oob.wsc"),
        emit_copy(
            args.pre_repair,
            AB / "cand2_pre_repair.wsc",
            "tip before the stock-invasion repair (originally reported state)",
        ),
        emit_copy(
            args.pre_rehome,
            AB / "cand3_no_rehome.wsc",
            "after stock repair + 5F pointer restore, before the ext3 re-homing pass",
        ),
        emit_copy(
            args.pre_ext3,
            AB / "cand4_pre_ext3.wsc",
            "tip from before the whole ext3 session",
        ),
    ]

    tip_cs = f"{ws_header(load_rom(TIP))['checksum']:04X}"
    report = {
        "generated_by": "tools/make_event_error_bisect.py",
        "tip": {"path": str(TIP), "checksum": tip_cs},
        "symptom": "new game shows event error 257 (0x0101) / 2049 (0x0801) instead "
        "of the opening narration",
        "static_scans_clean": [
            "tools/scan_script_record_structure.py 600000-60FFFF: 9,090 records, "
            "0 terminator/length differences vs the original",
            "tools/verify_stock_noninvasion.py: 0 unintended stock bytes",
        ],
        "no_emulator_on_this_machine": True,
        "test_order": [c["name"] for c in cands],
        "candidates": cands,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"tip checksum {tip_cs}")
    for c in cands:
        if c.get("path"):
            print(f"  {c['name']:18s} checksum {c['checksum']}  {c['change']}")
        else:
            print(f"  {c['name']:18s} {c.get('error')}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
