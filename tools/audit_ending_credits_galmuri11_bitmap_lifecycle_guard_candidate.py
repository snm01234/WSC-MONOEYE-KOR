#!/usr/bin/env python3
"""Independently audit the Galmuri11 ending-credit lifecycle candidate."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PARENT = (
    ROOT
    / "out/patch/ending_credits_galmuri11_bitmap_full_bg_clear_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_full_bg_clear_test.wsc"
)
PARENT_SAVE = (
    ROOT
    / "out/patch/ending_credits_galmuri11_bitmap_full_bg_clear_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_full_bg_clear_test.sav"
)
CANDIDATE_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_lifecycle_guard_candidate"
CANDIDATE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_lifecycle_guard_test.wsc"
)
CANDIDATE_SAVE = (
    CANDIDATE_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_lifecycle_guard_test.sav"
)
BUILD_REPORT = CANDIDATE_DIR / "ending_credits_galmuri11_bitmap_lifecycle_guard_report.json"
REPORT = CANDIDATE_DIR / "ending_credits_galmuri11_bitmap_lifecycle_guard_audit.json"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PAGE17_PREVIEW = (
    ROOT / "out/patch/ending_credits_galmuri11_bitmap_candidate/previews/slot17_ko.png"
)
ATLAS_MODULE = ROOT / "tools/build_ending_credits_ko_page_atlas.py"

EXPECTED_PARENT_SHA256 = "c59b749249b62562d227436a654c23ff9b5c223f7486e8a95301f8692b4dea1d"
EXPECTED_CANDIDATE_SHA256 = "afede10eb01f112653f033024455f56fb349c103e05ece081acf4109e9431e30"
EXPECTED_MAIN_SHA256 = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
EXPECTED_SAVE_SHA256 = "7b13d69096d51d579129b3a6b12c89875675723c3bf43a7ca9085e34143f7f3a"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ATLAS_BASE = 0x500000
ATLAS_SIZE = 0x10000
HEADER = struct.Struct("<4sHHHHHH")
RECORD = struct.Struct("<BBBBHHHHHH")
PAGE_COUNT = 21

IDLE_SITE = 0xFECA6E
PRELOAD_SITE = 0xFECA71
PAGE_RELOAD_SITE = 0xFECB0E
END_SITE = 0xFED652
PRELOAD_HELPER = 0xFEFD50
END_HELPER = 0xFEFD60
HELPER_END = 0xFEFD98

EXPECTED_PRELOAD_HELPER = bytes.fromhex("C7040000E40024FEE600E918CD")
EXPECTED_END_HELPER = bytes.fromhex(
    "9C5053515256571E06"
    "B8F62150B8060050B8200050"
    "B80030BB000033C9BA0C00"
    "9AC77C008083C406"
    "C706561B000F"
    "071F5F5E5A595B589DC3"
)
EXPECTED_RANGES = {
    17: (28, 8, 0x06C, 0x087),
    18: (40, 8, 0x091, 0x0B8),
    19: (43, 8, 0x051, 0x07B),
    20: (39, 10, 0x091, 0x0B7),
    21: (26, 6, 0x0BD, 0x0D6),
}


class AuditError(RuntimeError):
    pass


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AuditError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ATLAS_MOD = load_module("ending_lifecycle_audit_atlas", ATLAS_MODULE)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict:
    raw = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def ws_checksum(rom: bytes) -> tuple[int, int, bool]:
    stored = struct.unpack_from("<H", rom, len(rom) - 2)[0]
    computed = sum(rom[:-2]) & 0xFFFF
    return stored, computed, stored == computed


def jump_target(ip: int, instruction: bytes) -> int:
    if len(instruction) != 3 or instruction[0] not in (0xE8, 0xE9):
        raise AuditError(f"not a near call/jump: {instruction.hex()}")
    displacement = struct.unpack_from("<h", instruction, 1)[0]
    return (ip + 3 + displacement) & 0xFFFF


def atlas_pages(rom: bytes) -> tuple[tuple, list[dict]]:
    atlas = rom[ATLAS_BASE : ATLAS_BASE + ATLAS_SIZE]
    header = HEADER.unpack_from(atlas, 0)
    rows = []
    for page in range(1, PAGE_COUNT + 1):
        record = RECORD.unpack_from(atlas, page * RECORD.size)
        map_size = record[3] * record[7] * 2
        gfx_size = record[4] * 32
        rows.append(
            {
                "page": page,
                "record": record,
                "tilemap": atlas[record[5] : record[5] + map_size],
                "gfx": atlas[record[6] : record[6] + gfx_size],
            }
        )
    return header, rows


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def main() -> int:
    required = (
        PARENT,
        PARENT_SAVE,
        CANDIDATE,
        CANDIDATE_SAVE,
        BUILD_REPORT,
        MAIN,
        LIVE_SAVE,
        PAGE17_PREVIEW,
        ATLAS_MODULE,
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
    if len(candidate) != ROM_SIZE or sha256(candidate) != EXPECTED_CANDIDATE_SHA256:
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
    if not checksum_ok or stored != 0x1394:
        raise AuditError(f"candidate checksum invalid: {stored:04X}/{computed:04X}")

    code_checks = {
        "idle_branch_target": jump_target(0xCA6E, candidate[IDLE_SITE : IDLE_SITE + 3]),
        "preload_redirect_target": jump_target(
            0xCA71, candidate[PRELOAD_SITE : PRELOAD_SITE + 3]
        ),
        "reload_redirect_target": jump_target(
            0xCB0E, candidate[PAGE_RELOAD_SITE : PAGE_RELOAD_SITE + 3]
        ),
        "end_call_target": jump_target(0xD652, candidate[END_SITE : END_SITE + 3]),
    }
    expected_targets = {
        "idle_branch_target": 0xCBD1,
        "preload_redirect_target": 0xFD50,
        "reload_redirect_target": 0xFD1E,
        "end_call_target": 0xFD60,
    }
    if code_checks != expected_targets:
        raise AuditError(f"lifecycle target mismatch: {code_checks}")
    if candidate[END_SITE + 3 : END_SITE + 6] != b"\x90\x90\x90":
        raise AuditError("END call padding drifted")
    if candidate[PRELOAD_HELPER : PRELOAD_HELPER + len(EXPECTED_PRELOAD_HELPER)] != EXPECTED_PRELOAD_HELPER:
        raise AuditError("preload guard bytes drifted")
    if candidate[END_HELPER : END_HELPER + len(EXPECTED_END_HELPER)] != EXPECTED_END_HELPER:
        raise AuditError("END cleanup helper bytes drifted")
    if END_HELPER + len(EXPECTED_END_HELPER) != HELPER_END:
        raise AuditError("END helper does not end at reserved boundary")

    # The new idle branch must be the only semantic split.  The actual reload
    # still falls through from the stock screen writer into the existing hook.
    if candidate[0xFECB06:0xFECB0E] != bytes.fromhex("9A8E7E008083C40C"):
        raise AuditError("stock writer fall-through drifted")
    if candidate[0xFEFD1E:0xFEFD2E] != parent[0xFEFD1E:0xFEFD2E]:
        raise AuditError("one-shot reload stub changed")
    if candidate[0xFED4F1:0xFED4F6] != parent[0xFED4F1:0xFED4F6]:
        raise AuditError("page20 overlay site changed")
    if candidate[0xFED5C0:0xFED5C4] != parent[0xFED5C0:0xFED5C4]:
        raise AuditError("page21 overlay site changed")
    if candidate[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise AuditError("shared overlay implementation changed")
    if candidate[0xFED67F:0xFED6BB] != parent[0xFED67F:0xFED6BB]:
        raise AuditError("stock post-transition safety clear changed")
    if candidate[0xFED16B:0xFED1A7] != parent[0xFED16B:0xFED1A7]:
        raise AuditError("stock cinematic-entry map clears changed")

    parent_header, parent_pages = atlas_pages(parent)
    candidate_header, candidate_pages = atlas_pages(candidate)
    if parent_header[:4] != (b"ECKO", 2, PAGE_COUNT, HEADER.size):
        raise AuditError(f"parent atlas header drifted: {parent_header}")
    if candidate_header != (b"ECKO", 2, PAGE_COUNT, HEADER.size, 62_424, 0x50, 0):
        raise AuditError(f"candidate atlas header drifted: {candidate_header}")

    image = Image.open(PAGE17_PREVIEW).convert("RGB")
    expected_map17, expected_gfx17, expected_ntiles17 = ATLAS_MOD.page_atlas(image, 13, 5)
    atlas_checks = []
    for old, new in zip(parent_pages, candidate_pages):
        page = new["page"]
        old_record = old["record"]
        record = new["record"]
        if page == 17:
            if (
                record[0],
                record[1],
                record[2],
                record[3],
                record[4],
                record[7],
                record[8],
                record[9],
            ) != (17, 1, 13, 5, expected_ntiles17, 28, 8, 0x06C):
                raise AuditError(f"page17 ownership record mismatch: {record}")
            if new["tilemap"] != expected_map17 or new["gfx"] != expected_gfx17:
                raise AuditError("page17 bar-only bytes do not match source preview")
            if new["gfx"] != old["gfx"]:
                raise AuditError("page17 Galmuri11 graphics changed")
        else:
            stable_fields = (0, 1, 2, 3, 4, 7, 8, 9)
            if tuple(record[i] for i in stable_fields) != tuple(
                old_record[i] for i in stable_fields
            ):
                raise AuditError(f"page {page} stable atlas fields changed")
            if new["tilemap"] != old["tilemap"] or new["gfx"] != old["gfx"]:
                raise AuditError(f"page {page} atlas payload changed")
        atlas_checks.append(
            {
                "page": page,
                "row0": record[2],
                "nrows": record[3],
                "ntiles": record[4],
                "first_tile": f"{record[9]:03X}",
                "last_tile": f"{record[9] + record[4] - 1:03X}",
            }
        )

    for page, expected in EXPECTED_RANGES.items():
        record = candidate_pages[page - 1]["record"]
        got = (record[4], record[8], record[9], record[9] + record[4] - 1)
        if got != expected:
            raise AuditError(f"page {page} range changed: {got}")

    # The diff is fail-closed: only the atlas, three lifecycle code regions,
    # and the WonderSwan checksum may differ from the tested parent.
    allowed = (
        (ATLAS_BASE, ATLAS_BASE + ATLAS_SIZE),
        (IDLE_SITE, IDLE_SITE + 3),
        (END_SITE, END_SITE + 6),
        (PRELOAD_HELPER, HELPER_END),
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

    if not build.get("ok") or build.get("candidate", {}).get("sha256") != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("build report does not bind the audited candidate")
    if MAIN.read_bytes() != main_tip or LIVE_SAVE.read_bytes() != live_save:
        raise AuditError("main TIP or live SaveRAM changed during audit")

    audit = {
        "schema_version": 1,
        "generated_by": "tools/audit_ending_credits_galmuri11_bitmap_lifecycle_guard_candidate.py",
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
        "paired_saveram_parent": identity(PARENT_SAVE, parent_save),
        "code": {
            "targets": {key: f"{value:04X}" for key, value in code_checks.items()},
            "idle_upload_suppressed": True,
            "reload_fallthrough_one_shot": True,
            "preload_guard_exact": True,
            "end_cleanup": {
                "helper_exact": True,
                "stock_fill": "8000:7CC7",
                "shape": "row 12, 6x32",
                "fill": "21F6",
                "registers_and_flags_preserved": True,
            },
            "stock_entry_clear_preserved": True,
            "stock_post_end_clear_preserved": True,
            "shared_overlay_preserved": True,
        },
        "atlas": {
            "header": {
                "version": candidate_header[1],
                "records": candidate_header[2],
                "used_bytes": candidate_header[4],
                "free_bytes": ATLAS_SIZE - candidate_header[4],
            },
            "page17_bar_only_source_exact": True,
            "page17_galmuri11_graphics_byte_exact_to_parent": True,
            "other_twenty_pages_byte_exact_after_offset_normalization": True,
            "cinematic_ranges_exact": True,
            "pages": atlas_checks,
        },
        "diff": {
            "changed_bytes": len(diffs),
            "outside_declared_ranges": len(outside),
        },
        "immutability": {
            "main_tip": identity(MAIN, main_tip),
            "live_saveram": identity(LIVE_SAVE, live_save),
            "main_tip_unchanged_during_audit": MAIN.read_bytes() == main_tip,
            "live_saveram_unchanged_during_audit": LIVE_SAVE.read_bytes() == live_save,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "fresh replay with the paired SaveRAM, not an old savestate",
                "capture the first visible page16-to-17 transition frame",
                "inspect pages17-21 bars and upper art",
                "capture the first END frame and confirm no bottom fragments",
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
                    key: audit["atlas"][key]
                    for key in (
                        "header",
                        "page17_bar_only_source_exact",
                        "page17_galmuri11_graphics_byte_exact_to_parent",
                        "other_twenty_pages_byte_exact_after_offset_normalization",
                        "cinematic_ranges_exact",
                    )
                },
                "diff": audit["diff"],
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
        print(f"error: {exc}")
        raise SystemExit(1)
