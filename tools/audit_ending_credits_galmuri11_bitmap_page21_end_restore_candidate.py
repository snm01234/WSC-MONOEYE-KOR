#!/usr/bin/env python3
"""Independently audit the page21 END-restore candidate."""
from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT = (
    ROOT
    / "out/patch/ending_credits_galmuri11_bitmap_page16_exit_clear_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.wsc"
)
PARENT_SAVE = (
    ROOT
    / "out/patch/ending_credits_galmuri11_bitmap_page16_exit_clear_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.sav"
)
BITMAP = (
    ROOT
    / "out/patch/ending_credits_galmuri11_bitmap_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.wsc"
)
CANDIDATE_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_page21_end_restore_candidate"
CANDIDATE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page21_end_restore_test.wsc"
)
CANDIDATE_SAVE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page21_end_restore_test.sav"
)
BUILD_REPORT = CANDIDATE_DIR / "ending_credits_galmuri11_bitmap_page21_end_restore_report.json"
REPORT = CANDIDATE_DIR / "ending_credits_galmuri11_bitmap_page21_end_restore_audit.json"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"

EXPECTED_PARENT_SHA256 = "f3198ca1f29b3d4584c49186f1a02274046912feebed530c56d5fb4c852bcf77"
EXPECTED_BITMAP_SHA256 = "5f92d13d7ec071f133971dfeab3135151d98975f875363fa9a91d32fe70f713e"
EXPECTED_CANDIDATE_SHA256 = (
    "6ca50bb617b290619ebb47696aec4446fd1b7c59407e20e36726a54a122d1e0e"
)
EXPECTED_MAIN_SHA256 = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
EXPECTED_SAVE_SHA256 = "7b13d69096d51d579129b3a6b12c89875675723c3bf43a7ca9085e34143f7f3a"
EXPECTED_CHECKSUM = 0x214E

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ATLAS_BASE = 0x500000
RECORD = struct.Struct("<BBBBHHHHHH")
PAGE21_OFFSET = ATLAS_BASE + 21 * RECORD.size + 14
END_SITE = 0xFED652
EXPECTED_STOCK_END = bytes.fromhex("C706561B000F")


class AuditError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict:
    raw = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def ws_checksum(rom: bytes) -> tuple[int, int, bool]:
    stored = struct.unpack_from("<H", rom, len(rom) - 2)[0]
    computed = sum(rom[:-2]) & 0xFFFF
    return stored, computed, stored == computed


def jump_target(ip: int, instruction: bytes) -> int:
    if len(instruction) != 3 or instruction[0] not in (0xE8, 0xE9):
        raise AuditError(f"not a near call/jump: {instruction.hex()}")
    displacement = struct.unpack_from("<h", instruction, 1)[0]
    return (ip + 3 + displacement) & 0xFFFF


def main() -> int:
    required = (
        PARENT,
        PARENT_SAVE,
        BITMAP,
        CANDIDATE,
        CANDIDATE_SAVE,
        BUILD_REPORT,
        MAIN,
        LIVE_SAVE,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise AuditError(f"missing inputs: {missing}")

    parent = PARENT.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    bitmap = BITMAP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    main_tip = MAIN.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))

    if sha256(parent) != EXPECTED_PARENT_SHA256:
        raise AuditError("parent identity drifted")
    if sha256(bitmap) != EXPECTED_BITMAP_SHA256:
        raise AuditError("Bitmap identity drifted")
    candidate_sha = sha256(candidate)
    if EXPECTED_CANDIDATE_SHA256 is not None and candidate_sha != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("candidate identity drifted")
    if sha256(main_tip) != EXPECTED_MAIN_SHA256:
        raise AuditError("main TIP identity drifted")
    if sha256(candidate_save) != EXPECTED_SAVE_SHA256 or candidate_save != parent_save:
        raise AuditError("paired SaveRAM identity drifted")
    stored, computed, checksum_ok = ws_checksum(candidate)
    if not checksum_ok:
        raise AuditError(f"candidate checksum invalid: {stored:04X}/{computed:04X}")
    if EXPECTED_CHECKSUM is not None and stored != EXPECTED_CHECKSUM:
        raise AuditError(f"candidate checksum drifted: {stored:04X}")

    rec = RECORD.unpack_from(candidate, ATLAS_BASE + 21 * RECORD.size)
    bitmap_rec = RECORD.unpack_from(bitmap, ATLAS_BASE + 21 * RECORD.size)
    if rec[9] != 0x091 or rec[9] != bitmap_rec[9]:
        raise AuditError(f"page21 first_tile not restored: {rec[9]:03X}")
    if (rec[2], rec[3], rec[4], rec[8]) != (13, 5, 26, 6):
        raise AuditError("page21 bar contract drifted")
    if candidate[END_SITE : END_SITE + 6] != EXPECTED_STOCK_END:
        raise AuditError("D652 is not stock")
    if jump_target(0xD1CA, candidate[0xFED1CA:0xFED1CD]) != 0xFD5D:
        raise AuditError("page16-exit redirect lost")
    if jump_target(0xCA6E, candidate[0xFECA6E:0xFECA71]) != 0xCBD1:
        raise AuditError("idle overlay suppression lost")
    rec17 = RECORD.unpack_from(candidate, ATLAS_BASE + 17 * RECORD.size)
    if rec17[9] != 0x06C:
        raise AuditError("page17 16-to-17 range lost")
    if candidate[0xFEFD83:0xFEFDCF] != parent[0xFEFD83:0xFEFDCF]:
        raise AuditError("END helper cave changed")

    allowed = ((PAGE21_OFFSET, PAGE21_OFFSET + 2),)
    diffs = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    outside = [
        i
        for i in diffs
        if not any(lo <= i < hi for lo, hi in allowed)
        and i not in {ROM_SIZE - 2, ROM_SIZE - 1}
    ]
    if outside:
        raise AuditError(f"candidate diff leaked to {outside[0]:08X}")
    if not build.get("ok") or build.get("candidate", {}).get("sha256") != candidate_sha:
        raise AuditError("build report does not bind the audited candidate")
    if MAIN.read_bytes() != main_tip or LIVE_SAVE.read_bytes() != live_save:
        raise AuditError("main TIP or live SaveRAM changed during audit")

    audit = {
        "schema_version": 1,
        "generated_by": (
            "tools/audit_ending_credits_galmuri11_bitmap_page21_end_restore_candidate.py"
        ),
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "parent": identity(PARENT, parent),
        "bitmap_reference": identity(BITMAP, bitmap),
        "candidate": {
            **identity(CANDIDATE, candidate),
            "ws_checksum_stored": f"{stored:04X}",
            "ws_checksum_computed": f"{computed:04X}",
            "ws_checksum_valid": checksum_ok,
        },
        "paired_saveram": identity(CANDIDATE_SAVE, candidate_save),
        "code": {
            "page16_exit_preserved": True,
            "idle_suppressed": True,
            "page17_range": "06C-087",
            "page21_first_tile": "091",
            "page21_matches_bitmap_first_tile": True,
            "stock_D652": True,
            "no_end_helper": True,
        },
        "diff": {"changed_bytes": len(diffs), "outside_declared_ranges": 0},
        "promotion": "blocked_pending_user_runtime_validation",
    }
    atomic_json(REPORT, audit)
    print(
        json.dumps(
            {
                "ok": audit["ok"],
                "candidate": audit["candidate"],
                "code": audit["code"],
                "promotion": audit["promotion"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(f"AUDIT FAILED: {exc}")
        raise SystemExit(1)
