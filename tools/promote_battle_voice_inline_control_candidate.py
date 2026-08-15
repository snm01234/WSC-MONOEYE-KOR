#!/usr/bin/env python3
"""Promote battle-voice E62F inline-control candidate to main TIP (ROM only)."""
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

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Tbl, load_rom, stock_base, ws_header

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/battle_voice_inline_control_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/battle_voice_inline_control_candidate.sav"
REPORT = ROOT / "out/patch/battle_voice_inline_control_candidate_report.json"
PROMOTION = ROOT / "out/patch/battle_voice_inline_control_promotion_report.json"
BACKUP_ROOT = ROOT / "out/patch/backup"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_OLD = "4e779568af535f25319595049c559165dbbaac96e67c4c5799a4b99163674e0a"
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
    if report.get("ok") is not True or report.get("promotion_allowed") is not True:
        raise SystemExit("build report not ready for promotion")
    expected_cand = str(report["candidate"]["sha256"]).lower()
    if sha256(TIP) != EXPECTED_OLD:
        raise SystemExit(f"tip SHA drift: {sha256(TIP)}")
    if sha256(CANDIDATE) != expected_cand:
        raise SystemExit("candidate SHA drift vs report")
    if TIP.stat().st_size != ROM_SIZE or CANDIDATE.stat().st_size != ROM_SIZE:
        raise SystemExit("ROM size drift")
    if TIP_SAVE.stat().st_size != SAVE_SIZE:
        raise SystemExit("SaveRAM size drift")

    rom = bytes(load_rom(CANDIDATE))
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    sb = stock_base(rom)
    sample = next(row for row in report["applied"] if row["abs"] == "5DA6E5")
    payload = rom[sb + 0x5DA6E5 : sb + 0x5DA6E5 + int(sample["payload_capacity"])]
    prefix = bytes.fromhex(sample["prefix_hex"])
    if not payload.startswith(prefix):
        raise SystemExit("candidate 5DA6E5 prefix drifted")
    text = dictionary.expand(payload[len(prefix) :], tbl).rstrip("\u3000 \t")
    if text != sample["after"]:
        raise SystemExit(f"candidate 5DA6E5 render drifted: {text!r}")
    if len(report.get("applied") or []) != 268:
        raise SystemExit("applied count drifted")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}_pre_battle_voice_inline_control"
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
        "generated_by": "tools/promote_battle_voice_inline_control_candidate.py",
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
        "counts": {
            "applied": len(report["applied"]),
            "unique_phrases": report["counts"]["unique_phrases"],
            "boundary_review_required": report["counts"]["boundary_review_required"],
        },
        "notes": [
            "Applied 268 approved battle-voice E62F inline-control translations",
            "Prefix/E62F count/terminators preserved; Hangul via five-bank E5 18 + ext3",
            "SaveRAM left untouched",
        ],
    }
    PROMOTION.write_text(
        json.dumps(promotion, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for path in (CANDIDATE, CANDIDATE_SAVE):
        if path.exists():
            path.unlink()

    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
