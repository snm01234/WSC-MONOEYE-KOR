#!/usr/bin/env python3
"""Independently audit the Galmuri11 END tile-release candidate."""
from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT = (
    ROOT
    / "out/patch/ending_credits_galmuri11_bitmap_end_boundary_clear_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_boundary_clear_test.wsc"
)
PARENT_SAVE = (
    ROOT
    / "out/patch/ending_credits_galmuri11_bitmap_end_boundary_clear_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_boundary_clear_test.sav"
)
CANDIDATE_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_end_tile_release_candidate"
CANDIDATE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_tile_release_test.wsc"
)
CANDIDATE_SAVE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_tile_release_test.sav"
)
BUILD_REPORT = CANDIDATE_DIR / "ending_credits_galmuri11_bitmap_end_tile_release_report.json"
REPORT = CANDIDATE_DIR / "ending_credits_galmuri11_bitmap_end_tile_release_audit.json"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"

EXPECTED_PARENT_SHA256 = "ad8ea25e1c36e34bb79feef8e5b15e0c4d65479f5b6762733944c8949bfa06bf"
EXPECTED_CANDIDATE_SHA256 = "c0621438ce0979833ff26938102821f7a5f0c60e651ebb678a842cf8d616558e"
EXPECTED_MAIN_SHA256 = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
EXPECTED_SAVE_SHA256 = "7b13d69096d51d579129b3a6b12c89875675723c3bf43a7ca9085e34143f7f3a"
EXPECTED_CHECKSUM = 0xF6C9

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
END_SITE = 0xFED652
HELPER_PHYS = 0xFEFD83
HELPER_END = 0xFEFDCF
EXPECTED_END_CALL = bytes.fromhex("E82E27909090")
EXPECTED_STOCK_BG_CLEAR = bytes.fromhex(
    "B8F62150B8180050B8200050B80030BB000033C933D29AC77C008083C406"
)
TILE_ZERO = bytes.fromhex("33C08EC0BF0040B9C004FCF3ABBFA057B9A001F3AB")


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


def main() -> int:
    required = (
        PARENT,
        PARENT_SAVE,
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
    candidate = CANDIDATE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    main_tip = MAIN.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))

    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise AuditError("parent identity drifted")
    candidate_sha = sha256(candidate)
    if len(candidate) != ROM_SIZE:
        raise AuditError("candidate size drifted")
    if EXPECTED_CANDIDATE_SHA256 is not None and candidate_sha != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("candidate identity drifted")
    if len(main_tip) != ROM_SIZE or sha256(main_tip) != EXPECTED_MAIN_SHA256:
        raise AuditError("main TIP identity drifted")
    if (
        len(candidate_save) != SAVE_SIZE
        or sha256(candidate_save) != EXPECTED_SAVE_SHA256
        or candidate_save != parent_save
    ):
        raise AuditError("paired SaveRAM identity drifted")
    stored, computed, checksum_ok = ws_checksum(candidate)
    if not checksum_ok:
        raise AuditError(f"candidate checksum invalid: {stored:04X}/{computed:04X}")
    if EXPECTED_CHECKSUM is not None and stored != EXPECTED_CHECKSUM:
        raise AuditError(f"candidate checksum drifted: {stored:04X}")

    helper = candidate[HELPER_PHYS:HELPER_END]
    if helper[:9] != bytes.fromhex("9C5053515256571E06"):
        raise AuditError("helper prologue drifted")
    if helper[9:39] != EXPECTED_STOCK_BG_CLEAR:
        raise AuditError("map clear drifted")
    if helper[39:60] != TILE_ZERO:
        raise AuditError("tile zero drifted")
    if helper[60:66] != bytes.fromhex("C706561B000F"):
        raise AuditError("1B56 write drifted")
    if helper[-10:] != bytes.fromhex("071F5F5E5A595B589DC3"):
        raise AuditError("helper epilogue drifted")
    if candidate[END_SITE : END_SITE + 6] != EXPECTED_END_CALL:
        raise AuditError("END call site changed")
    if candidate[0xFEFD5D:0xFEFD83] != parent[0xFEFD5D:0xFEFD83]:
        raise AuditError("page16-exit helper changed")
    if candidate[0x500000:0x510000] != parent[0x500000:0x510000]:
        raise AuditError("atlas changed")
    if candidate[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise AuditError("shared overlay changed")

    allowed = ((HELPER_PHYS, HELPER_END),)
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
            "tools/audit_ending_credits_galmuri11_bitmap_end_tile_release_candidate.py"
        ),
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "parent": identity(PARENT, parent),
        "candidate": {
            **identity(CANDIDATE, candidate),
            "ws_checksum_stored": f"{stored:04X}",
            "ws_checksum_computed": f"{computed:04X}",
            "ws_checksum_valid": checksum_ok,
        },
        "paired_saveram": identity(CANDIDATE_SAVE, candidate_save),
        "code": {
            "end_call_preserved": True,
            "map_clear_preserved": True,
            "zero_tiles": ["000-04B", "0BD-0D6"],
            "page16_exit_preserved": True,
            "atlas_preserved": True,
        },
        "diff": {"changed_bytes": len(diffs), "outside_declared_ranges": 0},
        "runtime_validation": {
            "status": "pending",
            "required": [
                "first END frame has no 제작/반다이 fragments",
            ],
        },
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
