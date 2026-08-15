#!/usr/bin/env python3
"""Build dual-use kana chart candidate with render-only Hangul substitution.

Runtime evidence:
- 75:B889..B8BF is dual-use raw kana/index data. Replacing each zstring with an
  E518 ext3 portal breaks appreciation-mode BGM initialization.
- Restoring only these nine raw records fixes the BGM corruption.

This candidate therefore keeps the pre-kana raw bytes byte-exact for all data
consumers and modifies only the common glyph-store hook.  When the text walker
is currently consuming physical bank F5 (logical bank75) and its source cursor
matches one of the kana chart character positions, the store helper substitutes
only the displayed glyph index with the corresponding tagged Hangul glyph.
No chart/data byte is translated in ROM.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import Tbl, stock_base, text_code_to_glyph_index, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
GOOD = ROOT / "out/patch/backup/20260815_012352_pre_encyclopedia_kana_index/monoeye_ko_expanded.wsc"
CATALOG = ROOT / "data/encyclopedia_kana_index_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/appreciation_bgm_kana_chart_render_hook_candidate.wsc"
OUT_SAVE = ROOT / "sram/appreciation_bgm_kana_chart_render_hook_candidate.sav"
REPORT = ROOT / "out/patch/appreciation_bgm_kana_chart_render_hook_report.json"

EXPECTED_MAIN_SHA = "d925ee07b4ea844a1bd89deff52bc88ec21e04bf8ab5a0df8d9fecdd651bd8b1"
STORE_CAVE = 0x7AFFCA
STORE_WRAPPER = bytes.fromhex("9A37FA00D0C3")  # lcall D000:FA37 ; ret
HELPER = 0x7DFA37
HELPER_SEG = 0xD000
TABLE_BANK_PHYSICAL = 0xF5
TAG_FLAG = 0x19FF
GLYPH_BUFFER = 0x1A6E
HANGUL_BASE = 0x0820
HANGUL_END = 0x0D64


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel8(buf: bytearray, at: int, target: int) -> None:
    disp = target - (at + 2)
    if not -128 <= disp <= 127:
        raise RuntimeError(f"rel8 out of range {disp}")
    buf[at + 1] = disp & 0xFF


def build_helper(entries: list[tuple[int, int]]) -> tuple[bytes, int]:
    """Return (helper+table, table logical offset within bank7D)."""
    code = bytearray()
    patches_fallback: list[int] = []

    # Preserve registers that the stock store cave did not clobber.
    code += b"\x50\x51\x57"  # push ax,cx,di
    # Only source pointers in the banked ROM window can be chart data.
    code += b"\x81\x7E\xFA\x00\x30"  # cmp word [bp-6],3000
    p = len(code); code += b"\x75\x00"; patches_fallback.append(p)
    code += b"\xE4\xC3"  # in al,C3 (current mapped physical bank)
    code += b"\x3C" + bytes([TABLE_BANK_PHYSICAL])
    p = len(code); code += b"\x75\x00"; patches_fallback.append(p)
    code += b"\x8B\x46\xF8"  # mov ax,[bp-8] current source cursor after glyph

    # Table address patched once code size is known.
    mov_di_at = len(code)
    code += b"\xBF\x00\x00"
    code += b"\xB9" + struct.pack("<H", len(entries))
    loop_at = len(code)
    code += b"\x2E\x3B\x05"  # cmp ax,cs:[di]
    found_jz = len(code); code += b"\x74\x00"
    code += b"\x83\xC7\x04"  # add di,4
    loop_insn = len(code); code += b"\xE2\x00"
    fallback_jmp = len(code); code += b"\xEB\x00"

    found = len(code)
    code += b"\x2E\x8B\x75\x02"  # mov si,cs:[di+2]
    code += b"\xC6\x06" + struct.pack("<H", TAG_FLAG) + b"\x00"
    found_store_jmp = len(code); code += b"\xEB\x00"

    fallback = len(code)
    # Exact behavior of current 7A:FFCA store cave.
    code += b"\x80\x3E" + struct.pack("<H", TAG_FLAG) + b"\x01"
    jne_store = len(code); code += b"\x75\x00"
    code += b"\x81\xFE" + struct.pack("<H", HANGUL_BASE)
    jb_clear = len(code); code += b"\x72\x00"
    code += b"\x81\xFE" + struct.pack("<H", HANGUL_END)
    jae_clear = len(code); code += b"\x73\x00"
    code += b"\x81\xCE\x00\x80"  # or si,8000
    jmp_store = len(code); code += b"\xEB\x00"
    clear = len(code)
    code += b"\xC6\x06" + struct.pack("<H", TAG_FLAG) + b"\x00"

    store = len(code)
    code += b"\x89\xB7" + struct.pack("<H", GLYPH_BUFFER)
    code += b"\x5F\x59\x58\xCB"  # pop di,cx,ax; retf

    table_off = (HELPER & 0xFFFF) + len(code)
    struct.pack_into("<H", code, mov_di_at + 1, table_off)
    for p in patches_fallback:
        rel8(code, p, fallback)
    rel8(code, found_jz, found)
    rel8(code, loop_insn, loop_at)
    rel8(code, fallback_jmp, fallback)
    rel8(code, found_store_jmp, store)
    rel8(code, jne_store, store)
    rel8(code, jb_clear, clear)
    rel8(code, jae_clear, clear)
    rel8(code, jmp_store, store)

    for after_ptr, tagged_index in entries:
        code += struct.pack("<HH", after_ptr, tagged_index)
    return bytes(code), table_off


def main() -> int:
    parent = bytearray(MAIN.read_bytes())
    good = GOOD.read_bytes()
    before_save = SAVE.read_bytes()
    if sha(parent) != EXPECTED_MAIN_SHA:
        raise RuntimeError("main TIP identity drifted")
    if len(parent) != len(good) or len(parent) != 0x1000000:
        raise RuntimeError("ROM size mismatch")
    sb = stock_base(parent); gsb = stock_base(good)
    tbl = Tbl.load(TBL_PATH)
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))

    # Build display substitution table while restoring raw dual-use records.
    entries: list[tuple[int, int]] = []
    records: list[dict[str, object]] = []
    for row in catalog["records"]:
        logical = int(row["abs"], 16)
        n = int(row["payload_len"])
        raw = bytes(good[gsb + logical : gsb + logical + n])
        parent_before = bytes(parent[sb + logical : sb + logical + n])
        parent[sb + logical : sb + logical + n] = raw

        chars: list[tuple[str, int, int]] = []
        i = 0
        while i < len(raw):
            b = raw[i]
            if b >= 0xE0:
                if i + 1 >= len(raw):
                    raise RuntimeError(f"truncated two-byte chart code at {logical+i:06X}")
                code = (b << 8) | raw[i + 1]
                step = 2
            else:
                code = b
                step = 1
            chars.append((tbl.decode_char(code), i, step))
            i += step
        jp = "".join(ch for ch, _i, _s in chars)
        ko = str(row["ko"])
        if jp != str(row["jp"]):
            raise RuntimeError(f"chart decode drift at {logical:06X}: {jp!r}")
        if len(chars) != len(ko):
            raise RuntimeError(f"chart visual length mismatch at {logical:06X}")

        substitutions = []
        for (src_ch, pos, step), dst_ch in zip(chars, ko):
            if src_ch == dst_ch:
                continue
            code = tbl.char_to_code.get(dst_ch)
            if code is None:
                raise RuntimeError(f"missing Hangul TBL char {dst_ch!r}")
            glyph = text_code_to_glyph_index(code)
            if not (HANGUL_BASE <= glyph < HANGUL_END):
                raise RuntimeError(f"Hangul glyph outside current tagged range: {dst_ch} {glyph:04X}")
            after_ptr = (logical + pos + step) & 0xFFFF
            tagged = glyph | 0x8000
            entries.append((after_ptr, tagged))
            substitutions.append({"source": src_ch, "display": dst_ch, "after_ptr": f"{after_ptr:04X}", "glyph": f"{glyph:04X}"})
        records.append({
            "abs": row["abs"], "jp": row["jp"], "ko": ko,
            "restored_hex": raw.hex().upper(), "was_hex": parent_before.hex().upper(),
            "substitutions": substitutions,
        })

    # No duplicate post-character source cursors should exist in this contiguous chart.
    ptrs = [p for p, _g in entries]
    if len(ptrs) != len(set(ptrs)):
        raise RuntimeError("duplicate chart source cursor")

    # Current store cave must be the known current implementation before wrapping it.
    expected_store = bytes.fromhex("803EFF1901751781FE2008720C81FE640D730681CE0080EB05C606FF190089B76E1AC3")
    actual_store = bytes(parent[sb + STORE_CAVE : sb + STORE_CAVE + len(expected_store)])
    if actual_store != expected_store:
        raise RuntimeError(f"store cave drifted: {actual_store.hex().upper()}")
    parent[sb + STORE_CAVE : sb + STORE_CAVE + len(STORE_WRAPPER)] = STORE_WRAPPER

    helper, table_off = build_helper(entries)
    cave_start = sb + HELPER
    cave_before = bytes(parent[cave_start : cave_start + len(helper)])
    if any(b != 0xFF for b in cave_before):
        raise RuntimeError("7D:FA37 helper cave is not free")
    parent[cave_start : cave_start + len(helper)] = helper

    update_ws_checksum(parent)
    OUT.write_bytes(parent)
    shutil.copyfile(SAVE, OUT_SAVE)

    changed = [i for i, (a, b) in enumerate(zip(MAIN.read_bytes(), parent)) if a != b]
    report = {
        "ok": True,
        "candidate": str(OUT.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha(parent),
        "checksum": f"{parent[-2] | (parent[-1] << 8):04X}",
        "parent_sha256": EXPECTED_MAIN_SHA,
        "strategy": "restore_dual_use_raw_chart_plus_render_only_glyph_substitution",
        "raw_chart_preserved": True,
        "chart_bank_physical": f"{TABLE_BANK_PHYSICAL:02X}",
        "store_wrapper": {"logical": "7A:FFCA", "hex": STORE_WRAPPER.hex().upper()},
        "helper": {"logical": "7D:FA37", "bytes": len(helper), "table_off": f"7D:{table_off:04X}", "entries": len(entries)},
        "records": records,
        "non_checksum_changed_bytes": sum(i < len(parent) - 2 for i in changed),
        "save_sha256": sha(OUT_SAVE.read_bytes()),
        "live_save_unchanged": SAVE.read_bytes() == before_save,
        "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
        "promotion": "blocked_pending_runtime",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
