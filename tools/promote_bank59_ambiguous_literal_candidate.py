#!/usr/bin/env python3
"""Promote bank59 ambiguous literal test candidate to the main TIP (ROM only).

User-validated: no runtime issues reported for the 15 applied Gundam-literal lines.
The 1-byte orphan ``な`` at 594715 remains unpatched (capacity). SaveRAM is left
untouched. Candidate ROM/SaveRAM pair is removed after a successful install.
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
CANDIDATE = ROOT / "out/patch/bank59_ambiguous_literal_test_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/bank59_ambiguous_literal_test_candidate.sav"
REPORT = ROOT / "out/patch/bank59_ambiguous_literal_test_candidate_report.json"
PROMOTION = ROOT / "out/patch/bank59_ambiguous_literal_promotion_report.json"
BACKUP_ROOT = ROOT / "out/patch/backup"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_OLD = "525acad1b9b8b8487fd47b6581897150fcc4da7ed2cd81a7c8c37112f267bc09"
EXPECTED_CANDIDATE = "30313f387660c4d09ce139a7fc4d0ce14962321d2df49ea1914021c9d2109f24"
EXPECTED_CHECKSUM = "63ED"
EXPECTED_APPLIED = 15
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
SAMPLE_ABS = ("59271A", "596FB1", "597000", "598AC4", "594866")


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
    if not CANDIDATE.is_file():
        raise SystemExit(f"missing candidate: {CANDIDATE}")
    if sha256(TIP) != EXPECTED_OLD:
        raise SystemExit(f"tip SHA drift: {sha256(TIP)}")
    if sha256(CANDIDATE) != EXPECTED_CANDIDATE:
        raise SystemExit("candidate SHA drift")
    if str(report["candidate"]["sha256"]).lower() != EXPECTED_CANDIDATE:
        raise SystemExit("build report candidate SHA drift")
    if TIP.stat().st_size != ROM_SIZE or CANDIDATE.stat().st_size != ROM_SIZE:
        raise SystemExit("ROM size drift")
    if TIP_SAVE.stat().st_size != SAVE_SIZE:
        raise SystemExit("SaveRAM size drift")
    applied = list(report.get("applied") or [])
    if len(applied) != EXPECTED_APPLIED:
        raise SystemExit(f"applied count drifted: {len(applied)}")
    if not all(report.get("checks", {}).values()):
        raise SystemExit("build checks not all true")
    if str(report.get("checksum") or "").upper() != EXPECTED_CHECKSUM:
        raise SystemExit("candidate checksum drifted")

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
        if text != str(sample["after"]).rstrip("\u3000 \t"):
            raise SystemExit(f"candidate {address} render drifted: {text!r}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}_pre_bank59_ambiguous_literal"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / TIP_SAVE.name
    shutil.copy2(TIP, backup_rom)
    shutil.copy2(TIP_SAVE, backup_save)
    if sha256(backup_rom) != EXPECTED_OLD:
        raise SystemExit("backup tip SHA mismatch")

    save_before = sha256(TIP_SAVE)
    atomic_copy(CANDIDATE, TIP)
    if sha256(TIP) != EXPECTED_CANDIDATE:
        atomic_copy(backup_rom, TIP)
        raise SystemExit("promoted tip SHA mismatch; rolled back")
    if sha256(TIP_SAVE) != save_before:
        raise SystemExit("SaveRAM changed unexpectedly")

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_bank59_ambiguous_literal_candidate.py",
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
            "skipped_capacity": int((report.get("counts") or {}).get("skipped_capacity") or 0),
            "ext3_records": int((report.get("counts") or {}).get("ext3_records") or 0),
            "changed_bytes": int((report.get("counts") or {}).get("changed_bytes") or 0),
        },
        "skipped_retained": report.get("skipped") or [],
        "notes": [
            "Promoted 15/16 bank59 ambiguous_review_only Gundam-literal Korean lines",
            "594715 lone な left unchanged (1-byte orphan, not part of neighbor sentences)",
            "SaveRAM left untouched",
            "Candidate ROM/SaveRAM removed after install",
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
