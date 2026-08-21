#!/usr/bin/env python3
"""Build an experimental Galmuri11 16×16 Hangul blitter test ROM.

Copies the current main TIP, then:

1. Bakes every mapped Hangul syllable as a native 16×16 Galmuri11 glyph into
   expansion banks 0x01/0x02 (64 B stock ``027C`` nibble-strip). Banks chosen
   because pad3 already proves low expansion AL (``00``) maps correctly; prior
   ``26``/``28`` pools stayed invisible while dest writefill worked.
2. Tagged path: map pool bank → stock ``8000:7C91`` DMA 64 B → restore ``C0``
   → ``7A:05BB``. Untagged JP/UI keep ``7A:052B``.

Main TIP and live SaveRAM are never modified. Output is test-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    bank_al_expansion,
    bank_al_stock,
    load_rom,
    stock_base,
    update_ws_checksum,
    ws_header,
)
from patch_font_hangul_hook import (  # noqa: E402
    CODE_SEG_7A,
    EXT_CAVE,
    EXT_CAVE_SEG,
    HANGUL_PRIMARY_BUDGET,
    PRIMARY_RETURN,
    PRIMARY_SITE,
    TAG_BIT,
    far_jmp,
    patch_rel8,
)
from patch_pad3_expansion import BANK_MAP_OFF, BANK_MAP_SEG  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CHAR_MAP = ROOT / "out/patch/hangul_char_map_pad3.json"
FONT_PATH = ROOT / "assets/fonts/galmuri_tmp/Galmuri11.ttf"
OUT_ROM = ROOT / "out/patch/galmuri11_16x16_blitter_test_candidate.wsc"
OUT_SAVE = ROOT / "sram/galmuri11_16x16_blitter_test_candidate.sav"
REPORT = ROOT / "out/patch/galmuri11_16x16_blitter_test_candidate_report.json"
PREVIEW_DIR = ROOT / "out/patch/galmuri11_16x16_preview"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
BASE_INDEX = 0x820
GLYPH_BANK0 = 0x01  # tip-empty; AL=01 same family as proven pad3 AL=00
GLYPH_BANK1 = 0x02
SLOTS_PER_BANK = 1024  # 1024 * 64 = 65536
GLYPH_BYTES = 64
CELL = 16
HELPER_BANK = 0x7F
HELPER_SEG = EXT_CAVE_SEG
HELPER_OFF = 0xFF18
HELPER_MAX = 0xFFFF0 - 0xFFF18  # 216
RESTORE_OFF = 0xFCF1  # existing pad3 bank-restore helper on tip
GLYPH_END = 0x05BB  # skip doubler; advance VRAM cell
DMA_COPY_OFF = 0x7C91  # stock far routine: AX=off BX=seg CX=dst DX=len → ports 40-48
TAG_FLAG = 0x19FE  # WRAM sticky bank-restore flag used by pad2/pad3
HELPER_COPY_POLICY = "movsw"  # wrappers may select "lut_mask" for runtime style preservation


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sab(rom: bytes | bytearray, logical: int) -> int:
    return stock_base(rom) + logical


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def pack_glyph_16x16(canvas: Sequence[Sequence[int]]) -> bytes:
    """Pack 16×16 canvas as stock doubler VRAM layout (64 B).

    Matches ``7A:027C`` default LUT ``[0x00,0xCC,0x00,·,0x33]`` via ``035A``/``052B``:
    four 16 B strips at dest+0/+10/+20/+30 (screen rows 0–3, 4–7, 8–11, 12–15).
    Each strip: plane0 then plane1, four 4×2 chunks left→right; one byte = hi
    nibble (upper row) | lo nibble (lower row), MSB = leftmost of the 4 px.
    """
    if len(canvas) != 16 or any(len(row) != 16 for row in canvas):
        raise BuildError("glyph canvas must be 16×16")
    out = bytearray()
    for qi in range(4):
        base = qi * 4
        chunk = bytearray(16)
        for half in range(2):
            for part in range(4):
                b0 = b1 = 0
                for col in range(4):
                    bit = 3 - col
                    pix = int(canvas[base + half * 2][part * 4 + col]) & 3
                    pix2 = int(canvas[base + half * 2 + 1][part * 4 + col]) & 3
                    if pix & 1:
                        b0 |= 1 << (4 + bit)
                    if pix2 & 1:
                        b0 |= 1 << bit
                    if pix & 2:
                        b1 |= 1 << (4 + bit)
                    if pix2 & 2:
                        b1 |= 1 << bit
                chunk[half * 8 + part] = b0
                chunk[half * 8 + 4 + part] = b1
        out.extend(chunk)
    return bytes(out)


def unpack_glyph_16x16(data: bytes) -> list[list[int]]:
    """Inverse of pack_glyph_16x16 (stock nibble-strip layout)."""
    if len(data) != GLYPH_BYTES:
        raise BuildError("glyph payload must be 64 bytes")
    canvas = [[0] * 16 for _ in range(16)]
    for qi in range(4):
        chunk = data[qi * 16 : (qi + 1) * 16]
        base = qi * 4
        for half in range(2):
            for part in range(4):
                b0 = chunk[half * 8 + part]
                b1 = chunk[half * 8 + 4 + part]
                b0h, b0l = b0 >> 4, b0 & 0xF
                b1h, b1l = b1 >> 4, b1 & 0xF
                for col in range(4):
                    bit = 3 - col
                    canvas[base + half * 2][part * 4 + col] = ((b0h >> bit) & 1) | (
                        ((b1h >> bit) & 1) << 1
                    )
                    canvas[base + half * 2 + 1][part * 4 + col] = ((b0l >> bit) & 1) | (
                        ((b1l >> bit) & 1) << 1
                    )
    return canvas


def render_galmuri11_16(ch: str, font: ImageFont.FreeTypeFont) -> list[list[int]]:
    """Rasterize one Hangul syllable with Galmuri11 into a 16×16 binary cell."""
    img = Image.new("L", (CELL, CELL), 0)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), ch, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (CELL - w) // 2 - bbox[0]
    y = (CELL - h) // 2 - bbox[1]
    draw.text((x, y), ch, fill=255, font=font)
    return [[3 if img.getpixel((cx, cy)) >= 128 else 0 for cx in range(CELL)] for cy in range(CELL)]


def load_hangul_slots(char_map_path: Path) -> list[tuple[int, str]]:
    document = json.loads(char_map_path.read_text(encoding="utf-8"))
    mapping = document.get("mapping") or {}
    rows: list[tuple[int, str]] = []
    for ch, info in mapping.items():
        if not ("가" <= ch <= "힣"):
            continue
        if not isinstance(info, dict):
            continue
        glyph_index = info.get("glyph_index")
        if glyph_index is None:
            continue
        slot = int(glyph_index) - BASE_INDEX
        if slot < 0:
            raise BuildError(f"negative hangul slot for {ch!r}")
        rows.append((slot, ch))
    rows.sort(key=lambda item: item[0])
    # unique by slot (keep first)
    seen: set[int] = set()
    unique: list[tuple[int, str]] = []
    for slot, ch in rows:
        if slot in seen:
            continue
        seen.add(slot)
        unique.append((slot, ch))
    if not unique:
        raise BuildError("no hangul padding slots found in char map")
    if unique[-1][0] >= SLOTS_PER_BANK * 2:
        raise BuildError(f"slot {unique[-1][0]} exceeds two-bank pool")
    return unique


def glyph_file_offset(slot: int) -> int:
    bank = GLYPH_BANK0 if slot < SLOTS_PER_BANK else GLYPH_BANK1
    local = slot if slot < SLOTS_PER_BANK else slot - SLOTS_PER_BANK
    return (bank << 16) | (local * GLYPH_BYTES)


def build_primary_cave(helper_off: int, helper_seg: int | None = None) -> bytes:
    """Tagged → 16×16 helper; untagged stock shl/add → 052B. Includes tip restore call."""
    target_seg = HELPER_SEG if helper_seg is None else helper_seg
    out = bytearray()
    out += b"\x9A" + struct.pack("<HH", RESTORE_OFF, EXT_CAVE_SEG)  # existing restore
    out += b"\xF7\xC3" + struct.pack("<H", TAG_BIT)  # test bx,8000
    jz_at = len(out)
    out += b"\x74\x00"
    out += far_jmp(helper_off & 0xFFFF, target_seg)
    normal = len(out)
    out += b"\xC1\xE3\x04"  # shl bx,4
    out += b"\x03\xD3"  # add dx,bx
    out += far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)
    patch_rel8(out, jz_at, normal)
    if len(out) > HANGUL_PRIMARY_BUDGET:
        raise BuildError(f"primary cave {len(out)}B > budget {HANGUL_PRIMARY_BUDGET}")
    return bytes(out)


def build_16x16_helper(base_index: int) -> bytes:
    """Copy 64 B from expansion glyph pool into VRAM; skip stock doubler.

    Bank switch mirrors pad2/pad3 (OUT C3 + sticky ``DS:19FE``). ``DS`` stays on
    the caller's WRAM segment while the sticky flag is written; only the
    ``rep movsw`` window temporarily sets ``DS=3000``.
    """
    out = bytearray()
    out += b"\x81\xE3\xFF\x7F"  # and bx,7fff
    out += b"\x81\xEB" + struct.pack("<H", base_index)  # sub bx,base
    out += b"\x56\x57\x1E"  # push si di ds (WRAM)

    out += b"\x81\xFB" + struct.pack("<H", SLOTS_PER_BANK)
    jae_at = len(out)
    out += b"\x73\x00"

    out += b"\xB0" + bytes([bank_al_expansion(GLYPH_BANK0) & 0xFF])
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xC6\x06" + struct.pack("<H", TAG_FLAG) + b"\x01"  # [19FE]=1 while DS=WRAM
    out += b"\x8B\xC3"
    out += b"\xC1\xE0\x06"
    out += b"\x8B\xF0"
    jmp_copy_at = len(out)
    out += b"\xEB\x00"

    bank1_at = len(out)
    out += b"\x81\xEB" + struct.pack("<H", SLOTS_PER_BANK)
    out += b"\xB0" + bytes([bank_al_expansion(GLYPH_BANK1) & 0xFF])
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xC6\x06" + struct.pack("<H", TAG_FLAG) + b"\x01"
    out += b"\x8B\xC3"
    out += b"\xC1\xE0\x06"
    out += b"\x8B\xF0"

    copy_at = len(out)
    out += b"\xB8\x00\x30"  # mov ax,3000
    out += b"\x8E\xD8"  # mov ds,ax
    out += b"\xC4\x7E\xF8"  # les di,[bp-8]
    out += b"\xB9\x20\x00"
    out += b"\xFC\xF3\xA5"  # rep movsw
    out += b"\x1F"  # pop ds → WRAM before sticky clear / bank restore
    out += b"\xB0" + bytes([bank_al_stock(0x40) & 0xFF])
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xC6\x06" + struct.pack("<H", TAG_FLAG) + b"\x00"
    out += b"\x5F\x5E"  # pop di si
    out += far_jmp(GLYPH_END & 0xFFFF, CODE_SEG_7A)

    patch_rel8(out, jae_at, bank1_at)
    patch_rel8(out, jmp_copy_at, copy_at)
    if len(out) > HELPER_MAX:
        raise BuildError(f"helper {len(out)}B exceeds cave {HELPER_MAX}")
    return bytes(out)


def write_preview(path: Path, canvas: Sequence[Sequence[int]], scale: int = 8) -> None:
    img = Image.new("L", (CELL * scale, CELL * scale), 0)
    for y in range(CELL):
        for x in range(CELL):
            if canvas[y][x]:
                for dy in range(scale):
                    for dx in range(scale):
                        img.putpixel((x * scale + dx, y * scale + dy), 255)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--font", type=Path, default=FONT_PATH)
    parser.add_argument("--font-px", type=int, default=11)
    parser.add_argument("--out-rom", type=Path, default=OUT_ROM)
    parser.add_argument("--out-save", type=Path, default=OUT_SAVE)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()

    if not args.tip.is_file():
        raise BuildError(f"missing tip: {args.tip}")
    if not args.font.is_file():
        raise BuildError(f"missing font: {args.font}")
    if not CHAR_MAP.is_file():
        raise BuildError(f"missing char map: {CHAR_MAP}")

    parent = bytes(load_rom(args.tip))
    if len(parent) != ROM_SIZE:
        raise BuildError("tip must be 16 MiB")
    save_snapshot = TIP_SAVE.read_bytes()
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing/wrong size")

    slots = load_hangul_slots(CHAR_MAP)
    font = ImageFont.truetype(str(args.font), size=args.font_px)

    candidate = bytearray(parent)
    # Ensure glyph banks start clean for our pool.
    for bank in (GLYPH_BANK0, GLYPH_BANK1):
        start = bank << 16
        candidate[start : start + 0x10000] = b"\xFF" * 0x10000

    baked: list[dict[str, Any]] = []
    preview_chars = ("가", "한", "글", "출", "확", "브", "드", "명", "중", "공")
    for slot, ch in slots:
        canvas = render_galmuri11_16(ch, font)
        payload = pack_glyph_16x16(canvas)
        if unpack_glyph_16x16(payload) != canvas:
            raise BuildError(f"nibble-strip round-trip failed for {ch!r} slot {slot}")
        off = glyph_file_offset(slot)
        candidate[off : off + GLYPH_BYTES] = payload
        ink = sum(1 for row in canvas for value in row if value)
        baked.append({"slot": slot, "ch": ch, "offset": f"{off:06X}", "ink": ink})
        if ch in preview_chars:
            write_preview(PREVIEW_DIR / f"{slot:04d}_{ch}.png", canvas)

    helper = build_16x16_helper(BASE_INDEX)
    primary = build_primary_cave(HELPER_OFF)

    # Install helper + primary (do not touch ext_dict at FC8C+).
    helper_abs = sab(candidate, (HELPER_BANK << 16) | HELPER_OFF)
    if not all(b == 0xFF for b in parent[helper_abs : helper_abs + HELPER_MAX]):
        raise BuildError(
            f"helper cave {HELPER_BANK:02X}:{HELPER_OFF:04X} is not free FF on tip"
        )
    candidate[helper_abs : helper_abs + len(helper)] = helper
    if len(helper) < HELPER_MAX:
        candidate[helper_abs + len(helper) : helper_abs + HELPER_MAX] = b"\xFF" * (
            HELPER_MAX - len(helper)
        )

    primary_abs = sab(candidate, EXT_CAVE)
    candidate[primary_abs : primary_abs + len(primary)] = primary
    if len(primary) < HANGUL_PRIMARY_BUDGET:
        candidate[
            primary_abs + len(primary) : primary_abs + HANGUL_PRIMARY_BUDGET
        ] = b"\xFF" * (HANGUL_PRIMARY_BUDGET - len(primary))

    # Primary site must remain the tip trampoline into FC4E.
    site = bytes(candidate[sab(candidate, PRIMARY_SITE) : sab(candidate, PRIMARY_SITE) + 3])
    if site[0] != 0xE9:
        raise BuildError("primary site trampoline missing on tip-derived image")

    checksum = update_ws_checksum(candidate)
    out_bytes = bytes(candidate)

    # Static checks
    checks = {
        "main_tip_bytes_unchanged": sha256(args.tip.read_bytes()) == sha256(parent),
        "primary_len_within_budget": len(primary) <= HANGUL_PRIMARY_BUDGET,
        "helper_len_within_cave": len(helper) <= HELPER_MAX,
        "untagged_path_returns_052b": primary.endswith(far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)),
        "helper_skips_to_05bb": helper.endswith(far_jmp(GLYPH_END & 0xFFFF, CODE_SEG_7A)),
        "all_slots_baked": len(baked) == len(slots),
        "glyph_bank0_slots_used": any(row["slot"] < SLOTS_PER_BANK for row in baked),
        "glyph_bank1_slots_used": any(row["slot"] >= SLOTS_PER_BANK for row in baked),
        "nibble_strip_roundtrip_ok": True,
        "ext3_banks_21_25_untouched": all(
            parent[b << 16 : (b + 1) << 16] == out_bytes[b << 16 : (b + 1) << 16]
            for b in range(0x21, 0x26)
        ),
        "glyph_banks_are_adjacent_low_expansion": (
            0 <= GLYPH_BANK0 <= 0x0E and GLYPH_BANK1 == GLYPH_BANK0 + 1
        ),
        "helper_copy_policy_valid": (
            (HELPER_COPY_POLICY == "movsw" and b"\xF3\xA5" in helper)
            or (
                HELPER_COPY_POLICY == "lut_mask"
                and b"\xAC\x8A\xE0\x22\xC6\xF6\xD4\x22\xE2\x0A\xC4\xAA" in helper
            )
        ),
    }
    if not all(checks.values()):
        raise BuildError(json.dumps({"checks": checks}, ensure_ascii=False))

    atomic_bytes(args.out_rom, out_bytes)
    shutil.copy2(TIP_SAVE, args.out_save)
    if sha256(args.out_save.read_bytes()) != sha256(save_snapshot):
        raise BuildError("test SaveRAM snapshot drifted")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_galmuri11_16x16_blitter_test_candidate.py",
        "ok": True,
        "status": "experimental_test_candidate",
        "promotion_allowed": False,
        "warning": (
            "Experimental Hangul-only 16×16 path using stock 027C nibble-strip VRAM "
            "layout. JP/UI untagged path is preserved statically; runtime must still "
            "confirm glyphs, bank restore, and mixed JP+KO dialogue."
        ),
        "main_tip": identity(args.tip, parent),
        "candidate": identity(args.out_rom, out_bytes),
        "candidate_save": {
            **identity(args.out_save),
            "policy": "test-only current main SaveRAM snapshot; never promote",
        },
        "font": {
            "path": str(args.font.relative_to(ROOT)).replace("\\", "/"),
            "px": args.font_px,
            "sha256": sha256(args.font.read_bytes()),
        },
        "checksum": f"{checksum:04X}",
        "ws_checksum": f"{ws_header(out_bytes)['checksum']:04X}",
        "hooks": {
            "primary_site": f"{PRIMARY_SITE:06X}",
            "primary_cave": f"{EXT_CAVE:06X}",
            "primary_len": len(primary),
            "helper": f"{HELPER_BANK:02X}{HELPER_OFF:04X}",
            "helper_len": len(helper),
            "skip_doubler_return": f"{GLYPH_END:06X}",
            "base_index": f"{BASE_INDEX:04X}",
            "copy_policy": HELPER_COPY_POLICY,
        },
        "glyph_pool": {
            "banks": [f"{GLYPH_BANK0:02X}", f"{GLYPH_BANK1:02X}"],
            "bytes_per_glyph": GLYPH_BYTES,
            "layout": "stock_027C_nibble_strip_4x16B",
            "slots_baked": len(baked),
            "slot_min": baked[0]["slot"],
            "slot_max": baked[-1]["slot"],
        },
        "checks": checks,
        "preview_dir": str(PREVIEW_DIR.relative_to(ROOT)).replace("\\", "/"),
        "sample_glyphs": [row for row in baked if row["ch"] in preview_chars],
    }
    atomic_json(args.report, report)
    print(json.dumps({k: report[k] for k in report if k != "sample_glyphs"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
