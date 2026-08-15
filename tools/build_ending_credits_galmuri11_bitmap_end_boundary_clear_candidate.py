#!/usr/bin/env python3
"""Build the Galmuri11 Bitmap END-boundary BG-clear test ROM.

Page 16-to-17 is already cleared at the cinematic-entry boundary.  Page 21's
Korean ``제작 / 반다이`` bar still remains live while the asynchronous END
transition is registered at ``7E:D652``.  Stock only wipes BG ``3000`` later at
``7E:D67F``, after that task can already upload END graphics over the bar.

This candidate keeps the page16-exit ROM byte-exact except for a near-call at
``D652`` that fills the full 18x32 BG map with ``21F6`` before END starts.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import update_ws_checksum  # noqa: E402


PARENT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_page16_exit_clear_candidate"
PARENT_ROM = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.wsc"
)
PARENT_SAVE = (
    PARENT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page16_exit_clear_test.sav"
)
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_DIR = ROOT / "out/patch/ending_credits_galmuri11_bitmap_end_boundary_clear_candidate"
OUT_ROM = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_boundary_clear_test.wsc"
)
OUT_SAVE = (
    OUT_DIR
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_end_boundary_clear_test.sav"
)
REPORT = OUT_DIR / "ending_credits_galmuri11_bitmap_end_boundary_clear_report.json"

EXPECTED_MAIN_SHA256 = (
    "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
)
EXPECTED_PARENT_SHA256 = (
    "f3198ca1f29b3d4584c49186f1a02274046912feebed530c56d5fb4c852bcf77"
)
EXPECTED_SAVE_SHA256 = (
    "7b13d69096d51d579129b3a6b12c89875675723c3bf43a7ca9085e34143f7f3a"
)

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

END_SITE = 0xFED652
HELPER_PHYS = 0xFEFD83
HELPER_IP = 0xFD83
HELPER_END = 0xFEFDBA

EXPECTED_END_SITE = bytes.fromhex("C706561B000F")  # mov word [1B56],0F00
EXPECTED_STOCK_BG_CLEAR = bytes.fromhex(
    "B8F62150B8180050B8200050B80030BB000033C933D29AC77C008083C406"
)
EXPECTED_PAGE16_HELPER = bytes.fromhex(
    "B8F62150B8180050B8200050B80030BB000033C933D29AC77C008083C406"
    "833E061B03E94CD4"
)


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict:
    raw = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def ws_checksum_valid(rom: bytes) -> bool:
    stored = struct.unpack_from("<H", rom, len(rom) - 2)[0]
    return stored == (sum(rom[:-2]) & 0xFFFF)


def near_call(src_ip: int, dst_ip: int) -> bytes:
    displacement = (dst_ip - (src_ip + 3)) & 0xFFFF
    return b"\xE8" + struct.pack("<H", displacement)


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise BuildError("diff inputs have different sizes")
    return [index for index, (a, b) in enumerate(zip(before, after)) if a != b]


def build_end_boundary_helper() -> bytes:
    """Clear BG 3000, perform the replaced D652 write, and near-return."""
    body = bytearray()
    body += bytes.fromhex("9C5053515256571E06")  # pushf, AX/BX/CX/DX/SI/DI/DS/ES
    body += EXPECTED_STOCK_BG_CLEAR
    body += EXPECTED_END_SITE
    body += bytes.fromhex("071F5F5E5A595B589DC3")  # restore, near ret
    if len(body) != HELPER_END - HELPER_PHYS:
        raise BuildError(f"unexpected END helper size: {len(body)}")
    return bytes(body)


def main() -> int:
    required = (PARENT_ROM, PARENT_SAVE, MAIN, LIVE_SAVE)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise BuildError(f"missing inputs: {missing}")

    parent = PARENT_ROM.read_bytes()
    parent_save = PARENT_SAVE.read_bytes()
    main_before = MAIN.read_bytes()
    live_save_before = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError(f"page16-exit parent drifted: {len(parent)} {sha256(parent)}")
    if len(main_before) != ROM_SIZE or sha256(main_before) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP drifted: {len(main_before)} {sha256(main_before)}")
    if len(parent_save) != SAVE_SIZE or sha256(parent_save) != EXPECTED_SAVE_SHA256:
        raise BuildError("paired SaveRAM identity drifted")
    if len(live_save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")
    if not ws_checksum_valid(parent):
        raise BuildError("page16-exit parent checksum invalid")

    if parent[END_SITE : END_SITE + 6] != EXPECTED_END_SITE:
        raise BuildError("END boundary site drifted")
    if parent[0xFEFD5D : 0xFEFD83] != EXPECTED_PAGE16_HELPER:
        raise BuildError("page16-exit helper drifted")
    if parent[0xFED67F : 0xFED67F + len(EXPECTED_STOCK_BG_CLEAR)] != EXPECTED_STOCK_BG_CLEAR:
        raise BuildError("stock post-END BG clear drifted")
    if any(byte != 0xFF for byte in parent[HELPER_PHYS:HELPER_END]):
        raise BuildError("END helper cave is no longer free FF")

    helper = build_end_boundary_helper()
    site = near_call(0xD652, HELPER_IP) + b"\x90\x90\x90"
    if len(site) != 6:
        raise BuildError("END site replacement size drifted")

    candidate = bytearray(parent)
    candidate[END_SITE : END_SITE + 6] = site
    candidate[HELPER_PHYS:HELPER_END] = helper

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if not ws_checksum_valid(result):
        raise BuildError("candidate checksum update failed")

    allowed = set(range(END_SITE, END_SITE + 6))
    allowed.update(range(HELPER_PHYS, HELPER_END))
    allowed.update({ROM_SIZE - 2, ROM_SIZE - 1})
    diffs = changed_offsets(parent, result)
    outside = [offset for offset in diffs if offset not in allowed]
    if outside:
        raise BuildError(f"candidate diff leaked to {outside[0]:08X}")

    if result[0xFEFD5D:0xFEFD83] != EXPECTED_PAGE16_HELPER:
        raise BuildError("page16-exit helper changed")
    if result[0xFECA6E:0xFECA71] != parent[0xFECA6E:0xFECA71]:
        raise BuildError("idle branch changed")
    if result[0xFED1CA:0xFED1CF] != parent[0xFED1CA:0xFED1CF]:
        raise BuildError("page16-exit redirect changed")
    if result[0xFFFF18:0xFFFFCC] != parent[0xFFFF18:0xFFFFCC]:
        raise BuildError("shared overlay changed")
    if result[0x500000:0x510000] != parent[0x500000:0x510000]:
        raise BuildError("atlas changed")
    if result[0xFED67F : 0xFED67F + len(EXPECTED_STOCK_BG_CLEAR)] != EXPECTED_STOCK_BG_CLEAR:
        raise BuildError("stock post-END BG clear changed")
    if MAIN.read_bytes() != main_before or LIVE_SAVE.read_bytes() != live_save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")
    if PARENT_ROM.read_bytes() != parent or PARENT_SAVE.read_bytes() != parent_save:
        raise BuildError("parent inputs changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_copy(PARENT_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": (
            "tools/build_ending_credits_galmuri11_bitmap_end_boundary_clear_candidate.py"
        ),
        "ok": True,
        "status": "candidate_static_verified_runtime_pending",
        "purpose": (
            "clear the page21 Korean BG bar before the asynchronous END "
            "transition can reinterpret 제작/반다이 tiles"
        ),
        "main_tip": {**identity(MAIN, main_before), "unchanged": True},
        "parent": identity(PARENT_ROM, parent),
        "candidate": {
            **identity(OUT_ROM, result),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(OUT_SAVE),
        "fixes": {
            "end_boundary_clear": {
                "site": "7E:D652 -> near call 7E:FD83",
                "timing": "after page21 hold 01C2, before END transition task registration",
                "routine": "stock 8000:7CC7",
                "destination": "BG map 3000",
                "shape": "18 rows x 32 cells",
                "fill_entry": "21F6 stable bank-1 blank",
                "preserves_replaced_instruction": "mov word [1B56],0F00",
                "registers_and_flags_preserved": True,
                "helper_bytes": len(helper),
            }
        },
        "preserved": {
            "page16_exit_clear": True,
            "idle_overlay_suppressed": True,
            "cinematic_first_tile_ranges": True,
            "galmuri11_bitmap_graphics": True,
            "shared_overlay_byte_exact": True,
            "stock_post_end_clear_at_D67F": True,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
            "paired_saveram_byte_exact": True,
        },
        "diff": {
            "changed_bytes": len(diffs),
            "declared_ranges": [
                "FED652-FED657 END boundary call",
                "FEFD83-FEFDB9 END 7CC7 helper",
                "FFFFFE-FFFFFF checksum",
            ],
            "outside_declared_ranges": 0,
        },
        "runtime_validation": {
            "status": "pending",
            "required": [
                "page16-to-17 Tom Create residue remains gone",
                "page21 제작/반다이 disappears before END graphics become visible",
                "END has no 제작/반다이 tile fragments",
            ],
            "savestate_note": (
                "Start from the paired SaveRAM and replay the ending. Old savestates "
                "restore stale VRAM and cannot validate the END boundary."
            ),
        },
        "promotion": "blocked_pending_user_runtime_validation",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "status": report["status"],
                "candidate": report["candidate"],
                "paired_saveram": report["paired_saveram"],
                "fixes": report["fixes"],
                "diff": report["diff"],
                "promotion": report["promotion"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
