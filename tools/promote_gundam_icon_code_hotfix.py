#!/usr/bin/env python3
"""Atomically promote the Gundam duplicate-TBL raw-code hotfix.

The terminology promotion rendered correctly but flattened several distinct raw
UI tile codes that all decode to the audit placeholder ``█``.  This hotfix is
strictly ROM-only: it restores the 15 verified raw bytes, recomputes the
WonderSwan checksum, and proves that TBL/maps/SaveRAM are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL = PATCH / "hangul_patch_pad3.tbl"
CHAR_MAP = PATCH / "hangul_char_map.json"
PAD3_MAP = PATCH / "hangul_char_map_pad3.json"
REFERENCE_DIR = PATCH / "backup/20260808_210528_pre_gundam_terminology"
REFERENCE_TIP = REFERENCE_DIR / "monoeye_ko_expanded.wsc"
REFERENCE_TBL = REFERENCE_DIR / "hangul_patch_pad3.tbl"
APPROVAL = PATCH / "gundam_icon_code_hotfix_user_validation.json"
REPORT = PATCH / "gundam_icon_code_hotfix_promotion_report.json"
POST_TERM = PATCH / "gundam_icon_code_hotfix_post_terminology_audit.json"
POST_RAW = PATCH / "gundam_icon_code_hotfix_post_raw_code_audit.json"
POST_FALSE = PATCH / "gundam_icon_code_hotfix_post_false_segptr.json"

EXPECTED_PARENT = "2fa34b87f1c975291c8bd60afa7df7fd4a92983fb84296f6216e01ad1f5fafef"
EXPECTED_TARGET = "b192ad1ed2e24b709bfa14e5ae7d72405e58a3eac8ae746f41864961148d2746"
EXPECTED_REFERENCE = "be5cdb102a589faecd487780b99d3c30dd358e938e66cdb5aeb76ebcc8f4959c"
EXPECTED_SAVE = "8954611a8870bc5456accbeed0bb525ca2372bd5425ec274a75baf34d3bd5a01"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# (file offset, expected current byte, corrected byte)
PATCHES = (
    (0x01C939A, 0xC5, 0xC9),
    (0x01FE5B4, 0xE6, 0xE7),
    (0x01FE5B5, 0xC5, 0x36),
    (0x01FE5C1, 0xE6, 0xE7),
    (0x01FE5C2, 0xC5, 0x36),
    (0x01FE668, 0xC5, 0xC9),
    (0x01FE669, 0xE6, 0xE7),
    (0x01FE66A, 0xC5, 0x36),
    (0x01FE674, 0xE6, 0xE7),
    (0x01FE675, 0xC5, 0x36),
    (0x01FE8F7, 0xE6, 0xE7),
    (0x01FE8F8, 0xC5, 0x36),
    (0x01FE924, 0xE6, 0xE7),
    (0x01FE925, 0xC5, 0x36),
    (0x01FF76D, 0xC5, 0xC9),
)


def sha_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict:
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def require(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"invalid JSON root: {path}")
    return value


def checksum_ok(data: bytes | bytearray) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def update_checksum(data: bytearray) -> int:
    value = sum(data[:-2]) & 0xFFFF
    data[-2:] = value.to_bytes(2, "little")
    return value


def atomic_bytes(path: Path, data: bytes, tag: str) -> None:
    tmp = path.with_name(f".{path.name}.{tag}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main() -> int:
    for path in (TIP, SAVE, TBL, CHAR_MAP, PAD3_MAP, REFERENCE_TIP, REFERENCE_TBL, APPROVAL):
        require(path.is_file(), f"missing required file: {path}")
    require(TIP.stat().st_size == ROM_SIZE, "main TIP size drifted")
    require(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drifted")
    require(sha(TIP) == EXPECTED_PARENT, "main TIP parent identity drifted")
    require(sha(SAVE) == EXPECTED_SAVE, "live SaveRAM identity drifted")
    require(sha(REFERENCE_TIP) == EXPECTED_REFERENCE, "reference pre-terminology TIP drifted")

    approval = load_json(APPROVAL)
    require(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    require(str(approval.get("parent_tip_sha256") or "").lower() == EXPECTED_PARENT, "approval parent mismatch")
    require(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_TARGET, "approval target mismatch")

    before = {
        "tip": ident(TIP),
        "save": ident(SAVE),
        "tbl": ident(TBL),
        "char_map": ident(CHAR_MAP),
        "pad3_map": ident(PAD3_MAP),
    }

    parent = bytearray(TIP.read_bytes())
    candidate = bytearray(parent)
    applied = []
    for offset, expected, corrected in PATCHES:
        actual = candidate[offset]
        require(actual == expected, f"raw-code source drift at {offset:07X}: {actual:02X} != {expected:02X}")
        candidate[offset] = corrected
        applied.append({"offset": f"{offset:07X}", "before": f"{expected:02X}", "after": f"{corrected:02X}"})

    checksum = update_checksum(candidate)
    require(checksum_ok(candidate), "candidate WonderSwan checksum invalid")
    require(sha_bytes(candidate) == EXPECTED_TARGET, f"hotfix candidate identity drifted: {sha_bytes(candidate)}")

    diffs = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    expected_diff = {row[0] for row in PATCHES} | {ROM_SIZE - 2, ROM_SIZE - 1}
    require(set(diffs) == expected_diff, f"unexpected byte-diff set: {[f'{x:07X}' for x in diffs]}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_gundam_icon_code_hotfix"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_tip = backup_dir / TIP.name
    shutil.copy2(TIP, backup_tip)
    require(sha(backup_tip) == EXPECTED_PARENT, "hotfix backup verification failed")

    try:
        atomic_bytes(TIP, bytes(candidate), "gundam_icon_code_hotfix")
        require(sha(TIP) == EXPECTED_TARGET, "promoted TIP is not exact hotfix candidate")
        require(checksum_ok(TIP.read_bytes()), "promoted TIP checksum invalid")
        require(ident(SAVE) == before["save"], "SaveRAM changed during hotfix")
        require(ident(TBL) == before["tbl"], "TBL changed during ROM-only hotfix")
        require(ident(CHAR_MAP) == before["char_map"], "char map changed during ROM-only hotfix")
        require(ident(PAD3_MAP) == before["pad3_map"], "pad3 map changed during ROM-only hotfix")

        subprocess.run([
            sys.executable, str(ROOT / "tools/audit_gundam_terminology_standard.py"),
            "--tip", str(TIP), "--tbl", str(TBL), "--out", str(POST_TERM),
        ], cwd=ROOT, check=True)
        term = load_json(POST_TERM)
        require(term.get("status") == "clean", "terminology post-audit failed")
        term_counts = term.get("counts") or {}
        require(int(term_counts.get("active_source_hits", -1)) == 0, "active source terminology residual")
        require(int(term_counts.get("dictionary_hits", -1)) == 0, "dictionary terminology residual")
        require(int(term_counts.get("rendered_record_hits", -1)) == 0, "rendered terminology residual")

        subprocess.run([
            sys.executable, str(ROOT / "tools/audit_ambiguous_tbl_code_preservation.py"),
            "--reference-tip", str(REFERENCE_TIP),
            "--candidate-tip", str(TIP),
            "--reference-tbl", str(REFERENCE_TBL),
            "--candidate-tbl", str(TBL),
            "--out", str(POST_RAW),
        ], cwd=ROOT, check=True)
        raw = load_json(POST_RAW)
        require(raw.get("status") == "clean", "ambiguous raw-code post-audit failed")
        require(int((raw.get("counts") or {}).get("mismatches", -1)) == 0, "raw-code mismatches remain")

        subprocess.run([
            sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"),
            "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE),
        ], cwd=ROOT, check=True)
        false = load_json(POST_FALSE)
        require(false.get("ok") is True and int(false.get("sites_found", -1)) == 0, "false segmented-pointer gate failed")

        marker = subprocess.run([sys.executable, str(ROOT / "tools/hangul_marker.py")], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip().upper()
        require(marker == "EC8D", f"installed marker changed: {marker}")
    except Exception:
        atomic_bytes(TIP, backup_tip.read_bytes(), "gundam_icon_code_hotfix_rollback")
        raise

    after = {
        "tip": ident(TIP),
        "save": ident(SAVE),
        "tbl": ident(TBL),
        "char_map": ident(CHAR_MAP),
        "pad3_map": ident(PAD3_MAP),
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_gundam_icon_code_hotfix.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_for_user_runtime_validation",
        "before": before,
        "after": after,
        "backup": ident(backup_tip),
        "backup_dir": str(backup_dir.relative_to(ROOT)).replace("\\", "/"),
        "raw_byte_patches": applied,
        "raw_byte_patch_count": len(applied),
        "total_changed_bytes_including_checksum": len(diffs),
        "checksum": f"{checksum:04X}",
        "postpromotion": {
            "terminology_audit": str(POST_TERM.relative_to(ROOT)).replace("\\", "/"),
            "raw_code_audit": str(POST_RAW.relative_to(ROOT)).replace("\\", "/"),
            "false_segmented_pointer_audit": str(POST_FALSE.relative_to(ROOT)).replace("\\", "/"),
            "terminology_counts": term_counts,
            "raw_code_mismatches": int((raw.get("counts") or {}).get("mismatches", -1)),
            "false_segmented_pointer_sites": int(false.get("sites_found", -1)),
            "installed_marker": marker,
            "saveram_unchanged": after["save"] == before["save"],
            "tbl_and_maps_unchanged": after["tbl"] == before["tbl"] and after["char_map"] == before["char_map"] and after["pad3_map"] == before["pad3_map"],
        },
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
