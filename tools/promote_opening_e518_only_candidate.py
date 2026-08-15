#!/usr/bin/env python3
"""Promote opening_e518_only candidate to main TIP (ROM only)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base, ws_header
from extract_script import split_prefix_body

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/opening_e518_only_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/opening_e518_only_candidate.sav"
REPORT = ROOT / "out/patch/opening_e518_only_report.json"
PROMOTION = ROOT / "out/patch/opening_e518_only_promotion_report.json"
BACKUP_ROOT = ROOT / "out/patch/backup"

EXPECTED_OLD = "6cf6184e7e989c02b25b3e61fc28fc9e9eca354c20726b5efc7eec10918dce05"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha256(path),
    }


def atomic_copy(src: Path, dst: Path) -> None:
    temporary = dst.with_name(f".{dst.name}.promote.tmp")
    shutil.copy2(src, temporary)
    os.replace(temporary, dst)


def main() -> int:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report.get("ok") is not True:
        raise SystemExit("build report not ok")
    expected_cand = report["candidate"]["sha256"]
    if sha256(TIP) != EXPECTED_OLD:
        raise SystemExit(f"tip SHA drift: {sha256(TIP)}")
    if sha256(CANDIDATE) != expected_cand:
        raise SystemExit("candidate SHA drift vs report")
    if TIP.stat().st_size != ROM_SIZE or CANDIDATE.stat().st_size != ROM_SIZE:
        raise SystemExit("ROM size drift")
    if TIP_SAVE.stat().st_size != SAVE_SIZE:
        raise SystemExit("SaveRAM size drift")

    # Preflight gates on candidate
    rom = bytes(load_rom(CANDIDATE))
    tbl = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")
    d = Dictionary(rom)
    sb = stock_base(rom)
    if d.expand_index(0x07B6, tbl).rstrip("\u3000") != "명중":
        raise SystemExit("candidate 07B6 is not 명중")
    hit = read_encoded_z_safe(rom, sb + 0x75B411, max_len=64)
    if d.expand(split_prefix_body(hit[0])[1], tbl).rstrip("\u3000") != "명중":
        raise SystemExit("candidate 75B411 is not 명중")
    for row in report["applied_opening"]:
        got = read_encoded_z_safe(rom, sb + int(row["abs"], 16), max_len=256)
        text = d.expand(split_prefix_body(got[0])[1], tbl).rstrip("\u3000")
        if text != row["after"]:
            raise SystemExit(f"candidate opening drift at {row['abs']}")
        if b"\xE5\x18" in got[0]:
            raise SystemExit(f"candidate still has E5 18 at {row['abs']}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}_pre_opening_e518_only"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / TIP_SAVE.name
    shutil.copy2(TIP, backup_rom)
    shutil.copy2(TIP_SAVE, backup_save)
    if sha256(backup_rom) != EXPECTED_OLD:
        raise SystemExit("backup tip SHA mismatch")

    save_before = sha256(TIP_SAVE)
    atomic_copy(CANDIDATE, TIP)
    if sha256(TIP) != expected_cand:
        atomic_copy(backup_rom, TIP)
        raise SystemExit("promoted tip SHA mismatch; rolled back")
    if sha256(TIP_SAVE) != save_before:
        raise SystemExit("SaveRAM changed unexpectedly")

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_opening_e518_only_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": identity(backup_rom),
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "backup_saveram_snapshot": identity(backup_save),
        "main_saveram_unchanged": True,
        "main_saveram_sha256": save_before,
        "checksum": f"{ws_header(bytes(load_rom(TIP)))['checksum']:04X}",
        "build_report": str(REPORT.relative_to(ROOT)).replace("\\", "/"),
        "notes": [
            "Rolled back opening stock-invasion repair while keeping sample54 audio fix",
            "Repaired only opening E5 18 sites 604251/604317 onto UI-safe free stock",
            "Restored battle UI 명중 at 75B411/07B6",
        ],
    }
    PROMOTION.write_text(json.dumps(promotion, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Cleanup candidate pair (keep reports)
    for path in (CANDIDATE, CANDIDATE_SAVE):
        if path.exists():
            path.unlink()

    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
