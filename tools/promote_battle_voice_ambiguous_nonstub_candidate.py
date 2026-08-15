#!/usr/bin/env python3
"""Promote ambiguous non-stub battle-voice test candidate to main TIP (ROM only).

Promotion was initially gated off for the test candidate. This script is the
explicit user-approved TIP transaction: backup current TIP, install candidate,
leave SaveRAM untouched, then remove the candidate pair.
"""
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
CANDIDATE = ROOT / "out/patch/battle_voice_ambiguous_nonstub_test_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/battle_voice_ambiguous_nonstub_test_candidate.sav"
REPORT = ROOT / "out/patch/battle_voice_ambiguous_nonstub_test_candidate_report.json"
PROMOTION = ROOT / "out/patch/battle_voice_ambiguous_nonstub_promotion_report.json"
BACKUP_ROOT = ROOT / "out/patch/backup"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_OLD = "0668ad254ad7cd91d6efc0110546488ddcdd2c5cce04f1dd034c85a9e4169c4e"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_APPLIED = 175
SAMPLE_ABS = ("5D8EC4", "5E058A", "5E0C30", "5E29D7", "5D3D8F")


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
    if not CANDIDATE.exists():
        raise SystemExit(f"missing candidate: {CANDIDATE}")
    expected_cand = str(report["candidate"]["sha256"]).lower()
    if sha256(TIP) != EXPECTED_OLD:
        raise SystemExit(f"tip SHA drift: {sha256(TIP)}")
    if sha256(CANDIDATE) != expected_cand:
        raise SystemExit("candidate SHA drift vs report")
    if TIP.stat().st_size != ROM_SIZE or CANDIDATE.stat().st_size != ROM_SIZE:
        raise SystemExit("ROM size drift")
    if TIP_SAVE.stat().st_size != SAVE_SIZE:
        raise SystemExit("SaveRAM size drift")
    applied = list(report.get("applied") or [])
    if len(applied) != EXPECTED_APPLIED:
        raise SystemExit(f"applied count drifted: {len(applied)}")
    if not all(report.get("checks", {}).values()):
        raise SystemExit("build checks not all true")

    rom = bytes(load_rom(CANDIDATE))
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)
    by_abs = {str(row["abs"]).upper(): row for row in applied}
    for address in SAMPLE_ABS:
        sample = by_abs[address]
        payload = rom[sb + int(address, 16) : sb + int(address, 16) + int(sample["payload_capacity"])]
        prefix = bytes.fromhex(sample["prefix_hex"])
        if not payload.startswith(prefix):
            raise SystemExit(f"candidate {address} prefix drifted")
        text = dictionary.expand(payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        if text != sample["after"]:
            raise SystemExit(f"candidate {address} render drifted: {text!r}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}_pre_battle_voice_ambiguous_nonstub"
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
        "generated_by": "tools/promote_battle_voice_ambiguous_nonstub_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "user_requested_promotion": True,
        "build_had_promotion_allowed_false": report.get("promotion_allowed") is False,
        "old_tip": identity(backup_rom),
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "backup_saveram_snapshot": identity(backup_save),
        "main_saveram_unchanged": True,
        "main_saveram_sha256": save_before,
        "checksum": f"{ws_header(bytes(load_rom(TIP)))['checksum']:04X}",
        "build_report": str(REPORT.relative_to(ROOT)).replace("\\", "/"),
        "counts": {
            "applied": len(applied),
            "ext3_records": report["counts"]["ext3_records"],
            "short_stock_records": report["counts"]["short_stock_records"],
            "boundary_review_required": report["counts"]["boundary_review_required"],
        },
        "notes": [
            "Promoted ambiguous non-stub battle-voice translations (exclude 不要/欠番/不用)",
            "175 records: 163 five-bank E5 18 + 12 short stock",
            "User explicitly requested TIP promotion of test candidate",
            "SaveRAM left untouched",
        ],
    }
    PROMOTION.write_text(json.dumps(promotion, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for path in (CANDIDATE, CANDIDATE_SAVE):
        if path.exists():
            path.unlink()

    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
