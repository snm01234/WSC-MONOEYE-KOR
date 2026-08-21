#!/usr/bin/env python3
"""Correct-cell POC: Galmuri11Bitmap Condensed in the dialogue's real 8x16 cell.

The dialogue blitter does NOT produce a 16x16 glyph. In WSC 4bpp mode the
0x40-byte destination is two 8x8 4bpp tiles, i.e. one 8x16 glyph cell.

For current binary Hangul the compact source is planar 8x8 (plane0 == plane1).
Stock 7A:052B feeds each 8-bit source plane-row through 7A:027C. Each call emits
exactly one 4-byte 4bpp output row. Because plane0 == plane1, each source row is
therefore emitted twice vertically: stock Galmuri7 is 8x8 -> 8x16, not 16x16.

This POC keeps the real 8-pixel width. Galmuri11Bitmap Condensed @16 naturally
fits within 7x11 pixels; its content is stretched vertically only to 14 pixels
and centered in the 8x16 cell. Every native output row is encoded by the exact
stock 027C LUT transform. Thus the generated 4bpp row bytes are always bytes the
original renderer itself can generate; no invented colour combinations are
introduced.

Three 64-byte variants (style bases 0/6/12) are precomputed per glyph. Runtime
only selects the stock style and copies the matching 64 bytes.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import struct
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import build_galmuri11_16x16_blitter_test_candidate as legacy  # noqa: E402
from monoeye_rom import bank_al_stock, load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402
from patch_font_hangul_hook import CODE_SEG_7A, EXT_CAVE, HANGUL_PRIMARY_BUDGET, PRIMARY_SITE, far_jmp, patch_rel8  # noqa: E402
from patch_pad3_expansion import BANK_MAP_OFF, BANK_MAP_SEG  # noqa: E402

MAIN = ROOT / 'out/patch/monoeye_ko_expanded.wsc'
SAVE = ROOT / 'sram/monoeye_ko_expanded.sav'
TBL = ROOT / 'out/patch/hangul_patch_pad3.tbl'
FONT = ROOT / 'assets/fonts/galmuri_tmp/Galmuri11Bitmap-Condensed-2.40.3.ttf'
OUT = ROOT / 'out/patch/dialogue_galmuri11bitmap_condensed_8x16_exactlut_poc_candidate.wsc'
OUT_SAVE = ROOT / 'sram/dialogue_galmuri11bitmap_condensed_8x16_exactlut_poc_candidate.sav'
REPORT = ROOT / 'out/patch/dialogue_galmuri11bitmap_condensed_8x16_exactlut_poc_report.json'
PREVIEW = ROOT / 'out/patch/dialogue_galmuri11bitmap_condensed_8x16_exactlut_poc_preview'
EXPECTED_MAIN = '5805cef4e4089990a012db233f1e3aee9f49fc60474bcd11ea5ab6483370906a'
EXPECTED_LUT = bytes.fromhex('201002000100002000000200001000000100')
LUT_STOCK_FILE = 0x75F600
BASE_INDEX = 0x820
SLOTS_PER_BANK = 1024
GLYPH_BYTES = 64
MODE_BANKS = {0: (0x02, 0x03), 6: (0x04, 0x05), 12: (0x06, 0x07)}
HELPER_BANK = 0x7D
HELPER_SEG = 0xD000
HELPER_OFF = 0xFAA5
HELPER_MAX = 216
TAG_FLAG = 0x19FE
GLYPH_END = 0x05BB
TARGET_CONTENT_H = 14


class BuildError(RuntimeError):
    pass


def sha_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def sab(rom: bytes | bytearray, logical: int) -> int:
    return stock_base(rom) + logical


def slots() -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for line in TBL.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^([0-9A-Fa-f]{4})=(.*)$', line)
        if not m:
            continue
        code, ch = int(m.group(1), 16), m.group(2)
        if len(ch) != 1 or not ('가' <= ch <= '힣') or code < 0xE000:
            continue
        slot = (code - 0xDF20) - BASE_INDEX
        if slot >= 0:
            rows.append((slot, ch))
    rows.sort()
    if len(rows) != 1345 or rows[0][0] != 0 or rows[-1][0] != 1349:
        raise BuildError(f'Hangul map drift n={len(rows)} range={rows[0][0]}..{rows[-1][0]}')
    if len({slot for slot, _ in rows}) != len(rows):
        raise BuildError('duplicate Hangul slot')
    return rows


def ex027c(cl: int, lut: bytes, base_off: int) -> bytes:
    out = bytearray()
    for _ in range(4):
        out.append(lut[base_off + (cl & 1)] | lut[base_off + (cl & 2) + 2])
        cl >>= 2
    return bytes(out)


def row_bits(row: list[int]) -> int:
    if len(row) != 8:
        raise BuildError('8-pixel row required')
    value = 0
    for x, pixel in enumerate(row):
        if pixel:
            value |= 1 << x
    return value


def pack_native_8x16(mask: list[list[int]], lut: bytes, base_off: int) -> bytes:
    """Encode native 8x16 binary rows using the exact stock 027C row transform."""
    if len(mask) != 16 or any(len(row) != 8 for row in mask):
        raise BuildError('mask must be 8x16')
    payload = b''.join(ex027c(row_bits(row), lut, base_off) for row in mask)
    if len(payload) != 64:
        raise BuildError('8x16 payload must be 64 bytes')
    return payload


def encode_compact_planar(mask8: list[list[int]]) -> bytes:
    """Binary 8x8 -> current compact Hangul record (identical p0/p1 planes)."""
    out = bytearray()
    for row in mask8:
        bits = row_bits(row)
        out += bytes((bits, bits))
    return bytes(out)


def stock_expand(src16: bytes, lut: bytes, base_off: int) -> bytes:
    return b''.join(ex027c(byte, lut, base_off) for byte in src16)


def doubled_vertical(mask8: list[list[int]]) -> list[list[int]]:
    return [row[:] for row in mask8 for _ in (0, 1)]


def selftest(lut: bytes) -> dict[str, Any]:
    rng = random.Random(0x8_16_027C)
    cases = 0
    for base_off in (0, 6, 12):
        for _ in range(128):
            mask8 = [[rng.randrange(2) for _x in range(8)] for _y in range(8)]
            expected = stock_expand(encode_compact_planar(mask8), lut, base_off)
            got = pack_native_8x16(doubled_vertical(mask8), lut, base_off)
            if got != expected:
                raise BuildError(f'8x16 stock-equivalence failed base={base_off}')
            cases += 1
    return {'cases': cases, 'bases': [0, 6, 12], 'stock_galmuri7_path_byte_exact': True}


def render_condensed(ch: str, font: ImageFont.FreeTypeFont, target_h: int = TARGET_CONTENT_H) -> list[list[int]]:
    """Render native-width Condensed then stretch only its vertical content."""
    image = Image.new('L', (8, 16), 0)
    draw = ImageDraw.Draw(image)
    bbox = draw.textbbox((0, 0), ch, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if w > 8:
        raise BuildError(f'{ch!r} exceeds 8px width: {w}')
    x = (8 - w) // 2 - bbox[0]
    y = (16 - h) // 2 - bbox[1]
    draw.text((x, y), ch, font=font, fill=255)
    base = [[1 if image.getpixel((x, y)) >= 128 else 0 for x in range(8)] for y in range(16)]
    ys = [y for y, row in enumerate(base) if any(row)]
    if not ys:
        return base
    y0, y1 = min(ys), max(ys)
    src = base[y0:y1 + 1]
    stretched: list[list[int]] = []
    for dy in range(target_h):
        sy = min(len(src) - 1, (dy * len(src)) // target_h)
        stretched.append(src[sy][:])
    out = [[0] * 8 for _ in range(16)]
    top = (16 - target_h) // 2
    for i, row in enumerate(stretched):
        out[top + i] = row
    return out


def glyph_offset(mode_base: int, slot: int) -> int:
    low, high = MODE_BANKS[mode_base]
    if slot < SLOTS_PER_BANK:
        return (low << 16) + slot * GLYPH_BYTES
    return (high << 16) + (slot - SLOTS_PER_BANK) * GLYPH_BYTES


def build_helper() -> bytes:
    """Tagged glyph -> select original style mode, copy precomputed 64-byte 8x16 cell."""
    out = bytearray()
    out += b'\x50\x53\x51\x52\x56\x57\x1E'  # save ax bx cx dx si di ds
    out += b'\x81\xE3\xFF\x7F'  # and bx,7fff
    out += b'\x81\xEB' + struct.pack('<H', BASE_INDEX)
    out += b'\x53'  # save slot
    out += b'\xC4\x5E\xFC'  # les bx,[bp-4] style structure
    out += b'\x26\xF7\x07\x00\x01'  # test style 0100
    j100 = len(out); out += b'\x75\x00'
    out += b'\x26\xF7\x07\x00\x02'  # test style 0200
    j200 = len(out); out += b'\x75\x00'
    out += b'\xB0\x02'  # mode0 low bank
    jready0 = len(out); out += b'\xEB\x00'
    m100 = len(out); out += b'\xB0\x04'
    jready1 = len(out); out += b'\xEB\x00'
    m200 = len(out); out += b'\xB0\x06'
    ready = len(out)
    patch_rel8(out, j100, m100); patch_rel8(out, j200, m200)
    patch_rel8(out, jready0, ready); patch_rel8(out, jready1, ready)
    out += b'\x5B'  # slot
    out += b'\x81\xFB\x00\x04'  # >=1024?
    jb = len(out); out += b'\x72\x00'
    out += b'\x81\xEB\x00\x04\xFE\xC0'  # slot-=1024 ; bank++
    small = len(out); patch_rel8(out, jb, small)
    out += b'\x9A' + struct.pack('<HH', BANK_MAP_OFF, BANK_MAP_SEG)
    out += b'\xC6\x06' + struct.pack('<H', TAG_FLAG) + b'\x01'
    out += b'\x8B\xC3\xC1\xE0\x06\x8B\xF0'  # si=slot*64
    out += b'\xB8\x00\x30\x8E\xD8'  # ds=3000
    out += b'\xC4\x7E\xF8'  # es:di=[bp-8]
    out += b'\xB9\x20\x00\xFC\xF3\xA5'  # 64 bytes
    out += b'\x1F'  # restore WRAM DS
    out += b'\xB0' + bytes([bank_al_stock(0x40)])
    out += b'\x9A' + struct.pack('<HH', BANK_MAP_OFF, BANK_MAP_SEG)
    out += b'\xC6\x06' + struct.pack('<H', TAG_FLAG) + b'\x00'
    out += b'\x5F\x5E\x5A\x59\x5B\x58'
    out += far_jmp(GLYPH_END, CODE_SEG_7A)
    if len(out) > HELPER_MAX:
        raise BuildError(f'helper {len(out)} > {HELPER_MAX}')
    return bytes(out)


def write_preview(path: Path, mask: list[list[int]], scale: int = 8) -> None:
    image = Image.new('RGB', (8 * scale, 16 * scale), 'white')
    for y, row in enumerate(mask):
        for x, value in enumerate(row):
            if value:
                for dy in range(scale):
                    for dx in range(scale):
                        image.putpixel((x * scale + dx, y * scale + dy), (0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main() -> int:
    if sha(MAIN) != EXPECTED_MAIN:
        raise BuildError(f'main drift {sha(MAIN)}')
    parent = bytes(load_rom(MAIN))
    save = SAVE.read_bytes()
    if len(parent) != 16_777_216 or len(save) != 32_768:
        raise BuildError('size drift')
    lut_at = stock_base(parent) + LUT_STOCK_FILE
    lut = parent[lut_at:lut_at + 18]
    if lut != EXPECTED_LUT or parent.count(EXPECTED_LUT) != 1:
        raise BuildError(f'LUT drift/ambiguity {lut.hex()} count={parent.count(EXPECTED_LUT)}')
    equivalence = selftest(lut)

    # Six low expansion banks are empty on the current main and dedicated to this POC.
    for bank in range(0x02, 0x08):
        if any(value != 0xFF for value in parent[bank << 16:(bank + 1) << 16]):
            raise BuildError(f'expansion bank {bank:02X} occupied')
    helper_at = 0x800000 + (HELPER_BANK << 16) + HELPER_OFF
    if any(value != 0xFF for value in parent[helper_at:helper_at + HELPER_MAX]):
        raise BuildError('helper cave occupied')

    rows = slots()
    font = ImageFont.truetype(str(FONT), size=16)
    out = bytearray(parent)
    for bank in range(0x02, 0x08):
        out[bank << 16:(bank + 1) << 16] = b'\xFF' * 0x10000

    hashes: dict[bytes, str] = {}
    collisions: list[tuple[str, str]] = []
    inks: list[int] = []
    sample_chars = {'가','한','글','모','빌','슈','트','부','대','출','격','명'}
    for slot, ch in rows:
        mask = render_condensed(ch, font)
        key = bytes(sum(mask, []))
        if key in hashes and hashes[key] != ch:
            collisions.append((hashes[key], ch))
        else:
            hashes[key] = ch
        inks.append(sum(sum(row) for row in mask))
        for mode_base in (0, 6, 12):
            payload = pack_native_8x16(mask, lut, mode_base)
            at = glyph_offset(mode_base, slot)
            out[at:at + 64] = payload
        if ch in sample_chars:
            write_preview(PREVIEW / f'{slot:04d}_{ch}.png', mask)

    if collisions:
        raise BuildError(f'8x16 glyph collisions: {collisions[:20]}')

    helper = build_helper()
    primary = legacy.build_primary_cave(HELPER_OFF, HELPER_SEG)
    out[helper_at:helper_at + len(helper)] = helper
    out[helper_at + len(helper):helper_at + HELPER_MAX] = b'\xFF' * (HELPER_MAX - len(helper))
    primary_at = sab(out, EXT_CAVE)
    out[primary_at:primary_at + len(primary)] = primary
    out[primary_at + len(primary):primary_at + HANGUL_PRIMARY_BUDGET] = b'\xFF' * (HANGUL_PRIMARY_BUDGET - len(primary))
    if out[sab(out, PRIMARY_SITE)] != 0xE9:
        raise BuildError('primary trampoline missing')

    checksum = update_ws_checksum(out)
    payload = bytes(out)
    checks = {
        'main_unchanged': MAIN.read_bytes() == parent,
        'save_unchanged': SAVE.read_bytes() == save,
        'lut_unique_exact': parent.count(EXPECTED_LUT) == 1,
        'stock_8x8_to_8x16_equivalence': equivalence['stock_galmuri7_path_byte_exact'],
        'all_1345_glyphs_unique': len(collisions) == 0,
        'primary_budget': len(primary) <= HANGUL_PRIMARY_BUDGET,
        'helper_budget': len(helper) <= HELPER_MAX,
        'existing_expansion_00_01_untouched': all(parent[b << 16:(b + 1) << 16] == payload[b << 16:(b + 1) << 16] for b in (0, 1)),
        'ext3_21_25_untouched': all(parent[b << 16:(b + 1) << 16] == payload[b << 16:(b + 1) << 16] for b in range(0x21, 0x26)),
        'checksum_valid': int(ws_header(payload)['checksum']) == (sum(payload[:-2]) & 0xFFFF),
    }
    if not all(checks.values()):
        raise BuildError(str(checks))

    legacy.atomic_bytes(OUT, payload)
    legacy.atomic_bytes(OUT_SAVE, save)
    report = {
        'schema_version': 1,
        'generated_by': Path(__file__).name,
        'ok': True,
        'status': 'experimental_test_candidate',
        'promotion_allowed': False,
        'root_cause': 'dialogue cell is 8x16 (two 8x8 4bpp tiles / 64B), not 16x16',
        'parent': {'path': 'out/patch/monoeye_ko_expanded.wsc', 'sha256': EXPECTED_MAIN},
        'candidate': {'path': str(OUT.relative_to(ROOT)).replace('\\','/'), 'sha256': sha_bytes(payload), 'checksum': f'{checksum:04X}'},
        'save': {'path': str(OUT_SAVE.relative_to(ROOT)).replace('\\','/'), 'sha256': sha(OUT_SAVE)},
        'font': {'path': str(FONT.relative_to(ROOT)).replace('\\','/'), 'source_px': 16, 'native_max_width': 7, 'native_content_height': 11, 'output_cell': '8x16', 'content_height': TARGET_CONTENT_H, 'horizontal_resample': 'none', 'vertical_resample': 'nearest/content-only'},
        'glyph_metrics': {'count': len(rows), 'collisions': 0, 'mean_ink': round(sum(inks)/len(inks), 3), 'min_ink': min(inks), 'max_ink': max(inks)},
        'stock_lut': {'stock_file_offset': f'{LUT_STOCK_FILE:06X}', 'expanded_abs': f'{lut_at:08X}', 'bytes': lut.hex().upper(), 'mode_bases': [0,6,12]},
        'transform': {'method': 'one native 8px row -> exact stock 027C 4-byte 4bpp row', 'selftest': equivalence, 'colour_policy': 'only stock-027C-producible row bytes; no synthesized colour combinations'},
        'storage': {'mode0_banks': ['02','03'], 'mode0100_banks': ['04','05'], 'mode0200_banks': ['06','07'], 'bytes_per_glyph_variant': 64, 'variants_per_glyph': 3},
        'hooks': {'primary': '7F:FC4E', 'primary_len': len(primary), 'helper': f'{HELPER_BANK:02X}:{HELPER_OFF:04X}', 'helper_len': len(helper)},
        'checks': checks,
        'remaining_gate': 'user visual validation; compare colour set and recognition against current Galmuri7 on same dialogue',
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
