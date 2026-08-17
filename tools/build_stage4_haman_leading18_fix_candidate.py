#!/usr/bin/env python3
"""Build a narrow STAGE4 Haman `こ뜻입니까！？` continuation fix candidate.

Runtime evidence:
    60BB34  그건 샤아 대령님을 좋아한다는
    60BB48  こ뜻입니까！？   <- wrong visible 0x18=TBL `こ`

Extraction/translation evidence already marks 60BB48 as prefix_hex=18 with the
visible Korean body `뜻입니까！？`.  The current payload is six bytes:

    18 E5 18 72 3C 01

Following the already-promoted 6002F1 precedent, remove only the unresolved
leading control byte and keep the same ext3 phrase plus one extra 01 pad:

    E5 18 72 3C 01 01

Record extent, terminator, following NULs/controls, runtime hooks, dictionaries,
and live SaveRAM are untouched.
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

from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_ROM = PATCH / "stage4_haman_leading18_fix_candidate.wsc"
OUT_SAVE = ROOT / "sram/stage4_haman_leading18_fix_candidate.sav"
OUT_REPORT = PATCH / "stage4_haman_leading18_fix_report.json"

EXPECTED_MAIN_SHA = "cfb90aaa7af2b9336fb63c70a8e7ec760ac51425d80017d5daf82e6118d86bca"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
TARGET = 0x60BB48
TERM = 0x60BB4E
NEXT = 0x60BB50
BEFORE = bytes.fromhex("18E518723C01")
AFTER = bytes.fromhex("E518723C0101")


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def ident(path: Path, data: bytes) -> dict[str, Any]:
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "size": len(data), "sha256": sha(data)}


def main() -> int:
    parent = bytes(load_rom(MAIN))
    live_save = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main identity drifted: {sha(parent)}")
    if len(live_save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drifted: {len(live_save)}")

    sb = stock_base(parent)
    if parent[sb + TARGET:sb + TARGET + len(BEFORE)] != BEFORE:
        got = parent[sb + TARGET:sb + TARGET + len(BEFORE)]
        raise BuildError(f"target payload drifted: {got.hex().upper()}")
    if parent[sb + TERM] != 0 or parent[sb + TERM:sb + NEXT] != b"\x00\x00":
        raise BuildError("target terminator/double-NUL boundary drifted")

    # Capture surrounding bytes as a hard structural baseline.
    guard_lo = TARGET - 16
    guard_hi = NEXT + 16
    before_guard = parent[sb + guard_lo:sb + guard_hi]

    out = bytearray(parent)
    out[sb + TARGET:sb + TARGET + len(AFTER)] = AFTER
    update_ws_checksum(out)
    candidate = bytes(out)

    if candidate[sb + TARGET:sb + TARGET + len(AFTER)] != AFTER:
        raise BuildError("target write failed")
    if candidate[sb + TERM:sb + NEXT] != b"\x00\x00":
        raise BuildError("candidate changed terminator/double-NUL")

    # Only target six bytes and checksum may differ inside the guarded scene.
    after_guard = candidate[sb + guard_lo:sb + guard_hi]
    rel_target = TARGET - guard_lo
    for i, (a, b) in enumerate(zip(before_guard, after_guard)):
        if a == b:
            continue
        if not (rel_target <= i < rel_target + len(AFTER)):
            raise BuildError(f"unexpected scene diff at {guard_lo + i:06X}")

    changed = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    allowed = set(range(sb + TARGET, sb + TARGET + len(AFTER))) | {len(candidate) - 2, len(candidate) - 1}
    unexpected = [i for i in changed if i not in allowed]
    if unexpected:
        raise BuildError(f"unexpected whole-ROM diffs: {[hex(x) for x in unexpected[:16]]}")

    checksum_ok = int(ws_header(candidate)["checksum"]) == (sum(candidate[:-2]) & 0xFFFF)
    if not checksum_ok:
        raise BuildError("checksum invalid")
    if MAIN.read_bytes() != parent or LIVE_SAVE.read_bytes() != live_save:
        raise BuildError("main or live SaveRAM changed during build")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage4_haman_leading18_fix_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "input": {"main": ident(MAIN, parent), "save": ident(LIVE_SAVE, live_save)},
        "output": {"rom": ident(OUT_ROM, candidate), "save": ident(OUT_SAVE, live_save), "checksum": f"{ws_header(candidate)['checksum']:04X}"},
        "target": {
            "address": f"{TARGET:06X}",
            "before": BEFORE.hex().upper(),
            "after": AFTER.hex().upper(),
            "terminator": f"{TERM:06X}",
            "double_nul": f"{TERM:06X}-{NEXT - 1:06X}",
            "expected_before_runtime": "こ뜻입니까！？",
            "expected_after_runtime": "뜻입니까！？",
            "method": "drop_unresolved_leading_18_keep_existing_ext3_phrase_preserve_extent",
            "precedent": "6002F1 promoted visible-leading-18 cleanup",
        },
        "checks": {
            "record_extent_preserved": True,
            "terminator_double_nul_preserved": True,
            "surrounding_scene_bytes_preserved": True,
            "runtime_code_unchanged": True,
            "dictionary_unchanged": True,
            "checksum_valid": checksum_ok,
            "main_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save,
        },
        "promotion": "blocked_pending_runtime_confirmation_of_60BB48",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
