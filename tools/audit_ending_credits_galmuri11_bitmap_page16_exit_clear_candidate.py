#!/usr/bin/env python3
"""Independently audit the Galmuri11 page-16-exit clear candidate."""
from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT = (
    ROOT
    / "out/patch/ending_credits_galmuri11_bitmap_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.wsc"
)
PARENT_SAVE = (
    ROOT
    / "out/patch/ending_credits_galmuri11_bitmap_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_test.sav"
)
CANDIDATE_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_page16_exit_clear_candidate"
CANDIDATE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.wsc"
)
CANDIDATE_SAVE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.sav"
)
BUILD_REPORT = CANDIDATE_DIR / "ending_credits_galmuri11_bitmap_page16_exit_clear_report.json"
REPORT = CANDIDATE_DIR / "ending_credits_galmuri11_bitmap_page16_exit_clear_audit.json"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"

EXPECTED_PARENT_SHA256 = "5f92d13d7ec071f133971dfeab3135151d98975f875363fa9a91d32fe70f713e"
EXPECTED_CANDIDATE_SHA256 = "f3198ca1f29b3d4584c49186f1a02274046912feebed530c56d5fb4c852bcf77"
EXPECTED_MAIN_SHA256 = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
EXPECTED_SAVE_SHA256 = "7b13d69096d51d579129b3a6b12c89875675723c3bf43a7ca9085e34143f7f3a"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ATLAS_BASE = 0x500000
RECORD = struct.Struct("<BBBBHHHHHH")

IDLE_SITE = 0xFECA6E
PRELOAD_SITE = 0xFECA71
PAGE_RELOAD_SITE = 0xFECB0E
PAGE16_EXIT_SITE = 0xFED1CA
HELPER_PHYS = 0xFEFD5D
HELPER_END = 0xFEFD83

EXPECTED_STOCK_BG_CLEAR = bytes.fromhex(
    "B8F62150B8180050B8200050B80030BB000033C933D29AC77C008083C406"
)
EXPECTED_PRELOAD_HELPER = bytes.fromhex("C7040000E40024FEE600E918CD")
EXPECTED_RANGES = {
    16: (100, 8, 0x001, 0x064),
    17: (28, 8, 0x06C, 0x087),
    18: (40, 8, 0x091, 0x0B8),
    19: (43, 8, 0x051, 0x07B),
    20: (39, 10, 0x091, 0x0B7),
    21: (26, 6, 0x0BD, 0x0D6),
}


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


def ranges_overlap(first_a: int, count_a: int, first_b: int, count_b: int) -> bool:
    return max(first_a, first_b) <= min(
        first_a + count_a - 1, first_b + count_b - 1
    )


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
    if not checksum_ok or stored != 0x217A:
        raise AuditError(f"candidate checksum invalid: {stored:04X}/{computed:04X}")

    helper = candidate[HELPER_PHYS:HELPER_END]
    if helper[: len(EXPECTED_STOCK_BG_CLEAR)] != EXPECTED_STOCK_BG_CLEAR:
        raise AuditError("page16-exit helper does not match stock 7CC7 BG clear")
    if helper[len(EXPECTED_STOCK_BG_CLEAR) : len(EXPECTED_STOCK_BG_CLEAR) + 5] != bytes.fromhex(
        "833E061B03"
    ):
        raise AuditError("page16-exit helper does not restore cmp [1B06],3")
    if jump_target(0xFD5D + len(helper) - 3, helper[-3:]) != 0xD1CF:
        raise AuditError("page16-exit helper does not resume at D1CF")

    code_checks = {
        "idle_branch_target": jump_target(0xCA6E, candidate[IDLE_SITE : IDLE_SITE + 3]),
        "preload_redirect_target": jump_target(
            0xCA71, candidate[PRELOAD_SITE : PRELOAD_SITE + 3]
        ),
        "reload_redirect_target": jump_target(
            0xCB0E, candidate[PAGE_RELOAD_SITE : PAGE_RELOAD_SITE + 3]
        ),
        "page16_exit_target": jump_target(
            0xD1CA, candidate[PAGE16_EXIT_SITE : PAGE16_EXIT_SITE + 3]
        ),
    }
    expected_targets = {
        "idle_branch_target": 0xCBD1,
        "preload_redirect_target": 0xFD50,
        "reload_redirect_target": 0xFD1E,
        "page16_exit_target": 0xFD5D,
    }
    if code_checks != expected_targets:
        raise AuditError(f"code target mismatch: {code_checks}")
    if candidate[PAGE16_EXIT_SITE + 3 : PAGE16_EXIT_SITE + 5] != b"\x90\x90":
        raise AuditError("page16-exit redirect padding drifted")
    if candidate[0xFECB06:0xFECB0E] != bytes.fromhex("9A8E7E008083C40C"):
        raise AuditError("stock writer fall-through drifted")
    if candidate[0xFEFD1E:0xFEFD2E] != parent[0xFEFD1E:0xFEFD2E]:
        raise AuditError("one-shot reload stub changed")
    if candidate[0xFEFD50 : 0xFEFD50 + len(EXPECTED_PRELOAD_HELPER)] != EXPECTED_PRELOAD_HELPER:
        raise AuditError("preload BG-off helper changed")
    if candidate[0xFED4F1:0xFED4F6] != parent[0xFED4F1:0xFED4F6]:
        raise AuditError("page20 overlay site changed")
    if candidate[0xFED5C0:0xFED5C4] != parent[0xFED5C0:0xFED5C4]:
        raise AuditError("page21 overlay site changed")
    if candidate[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise AuditError("shared overlay implementation changed")
    if candidate[0xFED16B:0xFED1A7] != parent[0xFED16B:0xFED1A7]:
        raise AuditError("stock cinematic-entry map clears changed")
    if candidate[0xFED16B:0xFED189] != EXPECTED_STOCK_BG_CLEAR:
        raise AuditError("stock pre-loop BG clear drifted")

    range_rows = []
    consecutive = []
    for page, expected in EXPECTED_RANGES.items():
        record = RECORD.unpack_from(candidate, ATLAS_BASE + page * RECORD.size)
        got = (record[4], record[8], record[9], record[9] + record[4] - 1)
        if page >= 17:
            parent_record = RECORD.unpack_from(parent, ATLAS_BASE + page * RECORD.size)
            if (
                record[0],
                record[1],
                record[2],
                record[3],
                record[4],
                record[8],
            ) != (
                parent_record[0],
                parent_record[1],
                parent_record[2],
                parent_record[3],
                parent_record[4],
                parent_record[8],
            ):
                raise AuditError(f"page {page} non-range atlas fields changed")
            if (record[2], record[3]) != (13, 5):
                raise AuditError(f"page {page} is no longer a 5-row bar")
        if got != expected:
            raise AuditError(f"page {page} range drifted: {got}")
        range_rows.append(
            {
                "page": page,
                "ntiles": record[4],
                "palette": record[8],
                "first_tile": f"{record[9]:03X}",
                "last_tile": f"{record[9] + record[4] - 1:03X}",
            }
        )
    for previous, current in zip(range(16, 21), range(17, 22)):
        prev = RECORD.unpack_from(candidate, ATLAS_BASE + previous * RECORD.size)
        cur = RECORD.unpack_from(candidate, ATLAS_BASE + current * RECORD.size)
        overlap = ranges_overlap(prev[9], prev[4], cur[9], cur[4])
        if overlap:
            raise AuditError(f"pages {previous}->{current} still overlap")
        consecutive.append(
            {
                "previous": previous,
                "current": current,
                "previous_range": f"{prev[9]:03X}-{prev[9] + prev[4] - 1:03X}",
                "current_range": f"{cur[9]:03X}-{cur[9] + cur[4] - 1:03X}",
                "overlap": False,
            }
        )

    # Graphics/tilemaps stay byte-exact; only three first_tile words may move.
    parent_atlas = bytearray(parent[ATLAS_BASE : ATLAS_BASE + 0x10000])
    candidate_atlas = bytearray(candidate[ATLAS_BASE : ATLAS_BASE + 0x10000])
    for page in (17, 18, 21):
        offset = page * RECORD.size + 14
        parent_atlas[offset : offset + 2] = b"\x00\x00"
        candidate_atlas[offset : offset + 2] = b"\x00\x00"
    if parent_atlas != candidate_atlas:
        raise AuditError("atlas payload besides cinematic first_tile changed")

    allowed = (
        (ATLAS_BASE + 17 * RECORD.size + 14, ATLAS_BASE + 17 * RECORD.size + 16),
        (ATLAS_BASE + 18 * RECORD.size + 14, ATLAS_BASE + 18 * RECORD.size + 16),
        (ATLAS_BASE + 21 * RECORD.size + 14, ATLAS_BASE + 21 * RECORD.size + 16),
        (IDLE_SITE, IDLE_SITE + 3),
        (PAGE16_EXIT_SITE, PAGE16_EXIT_SITE + 5),
        (HELPER_PHYS, HELPER_END),
    )
    diffs = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    outside = [
        i
        for i in diffs
        if not any(lo <= i < hi for lo, hi in allowed)
        and i not in {ROM_SIZE - 2, ROM_SIZE - 1}
    ]
    if outside:
        raise AuditError(f"candidate diff leaked to {outside[0]:08X}")

    report_sha = build.get("candidate", {}).get("sha256")
    if not build.get("ok") or report_sha != candidate_sha:
        raise AuditError("build report does not bind the audited candidate")
    if MAIN.read_bytes() != main_tip or LIVE_SAVE.read_bytes() != live_save:
        raise AuditError("main TIP or live SaveRAM changed during audit")

    audit = {
        "schema_version": 1,
        "generated_by": (
            "tools/audit_ending_credits_galmuri11_bitmap_page16_exit_clear_candidate.py"
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
            "targets": {key: f"{value:04X}" for key, value in code_checks.items()},
            "page16_exit_clear": {
                "helper_exact_to_stock_bg_fill": True,
                "shape": "18x32 at 3000",
                "fill": "21F6",
                "resume": "D1CF",
            },
            "idle_upload_suppressed": True,
            "reload_fallthrough_one_shot": True,
            "preload_guard_preserved": True,
            "stock_entry_clear_preserved": True,
            "shared_overlay_preserved": True,
        },
        "atlas": {
            "page17_bar_only": True,
            "graphics_byte_exact_to_parent": True,
            "pages": range_rows,
            "consecutive_page_pairs": consecutive,
        },
        "diff": {
            "changed_bytes": len(diffs),
            "outside_declared_ranges": 0,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "capture the first visible page16-to-17 transition frame",
                "confirm Tom Create is absent before page17 art is visible",
                "inspect pages17-21 bars and upper art",
            ],
        },
        "promotion": "blocked_pending_user_runtime_validation",
    }
    atomic_json(REPORT, audit)
    print(
        json.dumps(
            {
                "ok": audit["ok"],
                "status": audit["status"],
                "candidate": audit["candidate"],
                "code": audit["code"],
                "atlas": {
                    "page17_bar_only": True,
                    "consecutive_page_pairs": consecutive,
                },
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
