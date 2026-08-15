#!/usr/bin/env python3
"""
Install a provenance-tagged Hangul font hook without stealing stock UI glyphs.

Marked KO encoding:
  E3DB E740  E3DB E741 ...

Design (2026-07-14 revision):
  1) Marker dispatch (dict recurse 7A:073C AND parser-B 7A:0818) consumes E3DB
     and sets WRAM flag DS:19FF=1.
  2) Glyph store at 7A:07A0 ORs bit15 into SI when the flag is set, then clears it.
  3) Primary blitter 7A:0521 redirects bit15-tagged indices:
       slot < 96  → bank40 pad  CX=3000 DX=F9F8+(slot*16)
       slot >= 96 → bank41 pad  CX=4000 DX=E4F4+((slot-96)*16)
     Untagged indices keep stock UI reads at CX=3000 DX=0440+index*16.
     Address model: file = CX*16 + DX + 0x3D0000 (see PAD2_* comments).

Cave layout (7A only has 75 FF bytes at FFB5):
  7A:FFB5  far trampoline → F000:FC4E (bank 7F code window)
  7A:FFBA  marker dispatch + glyph-store caves (near-callable from 7A)
  7F:FC4C  sample 54 next-sample terminator (must remain FFFF)
  7F:FC4E  dual-pad primary (returns far A000:052B)
  7F:FC8C  reserved for ext_dict helper (P1) — Hangul primary must stay ≤62B
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import load_rom, update_ws_checksum  # noqa: E402

# Primary blitter. DX=0440, CX=3000 and BX=glyph index on entry.
PRIMARY_SITE = 0x7A0521
PRIMARY_SITE_LEN = 10
PRIMARY_RETURN = 0x7A052B
PRIMARY_EXPECT = bytes.fromhex("D1E3D1E3D1E3D1E303D3")

# Dict-expansion recurse: mov cx,dx; mov dx,di; call 06CE.
DISPATCH_SITE = 0x7A073C
DISPATCH_SITE_LEN = 7
DISPATCH_EXPECT = bytes.fromhex("8BCA8BD7E88BFF")

# Parser B (main text entry): mov cx,dx; mov dx,[bp+0A]; call 06CE.
PARSER_B_CALL = 0x7A0818
PARSER_B_CALL_LEN = 3
PARSER_B_EXPECT = bytes.fromhex("E8B3FE")

# Glyph index store: mov [bx+1A6E], si  (bx=(count-1)*2)
STORE_SITE = 0x7A07A0
STORE_SITE_LEN = 4
STORE_EXPECT = bytes.fromhex("89B76E1A")

LEAF_DECODER = 0x7A06CE

# Near-callable caves must stay in bank 7A (same CS as call sites).
MAIN_CAVE = 0x7AFFB5
MAIN_CAVE_MAX = 75
# Dual-pad primary begins immediately after the stock PCM sample table.
# 7F:FC4C-4D are the sample-54 next-sample terminator and must remain FFFF.
EXT_CAVE = 0x7FFC4E
EXT_CAVE_MAX = 194
EXT_CAVE_SEG = 0xF000
# ext_dict helper (P1) is fixed at 7F:FC8C; primary may occupy FC4E-FC8B.
HANGUL_PRIMARY_BUDGET = 62
# Bank 7A executes as CS=A000 (far calls target A000:07AC etc.).
CODE_SEG_7A = 0xA000

# Legacy tiny cave — cleared/unused in this revision.
DISPATCH_CAVE = 0x7A4722
DISPATCH_CAVE_MAX = 14

PAD1_OFF = 0xF9F8
PAD1_SLOTS = 96
PAD1_FILE = 0x40F9F8
# Blitter far-read (LES at 7A:053C) uses real-mode phys = CX*16 + DX, and on
# this cart the ROM file offset is phys + 0x3D0000 (calibrated from stock
# CX=3000 DX=0440 → file 40:0440). NOT (bank-0x10)<<8.
#   pad1 40:F9F8 → CX=3000 DX=F9F8
#   pad2 41:E4F4 → CX=4000 DX=E4F4
#   pad2 3F:C5CE → CX=2000 DX=C5CE
# Old wrong CX=3100/2F00 actually read 40:F4F4 / 40:B5CE (garbage).
PAD2_BANK = 0x41
PAD2_OFF = 0xE4F4
PAD2_SEG = 0x4000  # file 41E4F4 = 4000*16 + E4F4 + 3D0000
PAD2_SLOTS = 432  # stock FF run 41:E4F4 len=6924
PAD2_FILE = (PAD2_BANK << 16) | PAD2_OFF  # 0x41E4F4
PAD_TOTAL_SLOTS = PAD1_SLOTS + PAD2_SLOTS  # 528
PAD2_BANK3F_FILE = 0x3FC5CE
PAD2_BANK3F_OFF = 0xC5CE
PAD2_BANK3F_SEG = 0x2000  # file 3FC5CE = 2000*16 + C5CE + 3D0000
PAD2_BANK3F_SLOTS = 931

# Back-compat alias used by older verify/tools.
PAD_OFF = PAD1_OFF

TAG_BIT = 0x8000
# Free WRAM address; lives safely away from glyph index buffer 1A6E to avoid collision.
TAG_FLAG = 0x19FF


def rel16(from_addr: int, to_addr: int) -> int:
    return (to_addr - from_addr) & 0xFFFF


def near_call(from_addr: int, to_addr: int) -> bytes:
    return b"\xE8" + struct.pack("<H", rel16(from_addr + 3, to_addr))


def near_jmp(from_addr: int, to_addr: int) -> bytes:
    return b"\xE9" + struct.pack("<H", rel16(from_addr + 3, to_addr))


def far_jmp(offset: int, segment: int) -> bytes:
    return b"\xEA" + struct.pack("<HH", offset & 0xFFFF, segment & 0xFFFF)


def patch_rel8(buf: bytearray, at: int, target_off: int) -> None:
    disp = target_off - (at + 2)
    if not -128 <= disp <= 127:
        raise RuntimeError(f"rel8 out of range: {disp}")
    buf[at + 1] = disp & 0xFF


def pad_file_offset(slot: int) -> int:
    """File offset for padding-store glyph slot (0-based)."""
    if slot < PAD1_SLOTS:
        return PAD1_FILE + slot * 16
    return PAD2_FILE + (slot - PAD1_SLOTS) * 16


def runtime_pad_file_offset(tagged_index: int, base_index: int) -> int:
    """Resolve bit15-tagged glyph index to a ROM file offset."""
    slot = (tagged_index & 0x7FFF) - base_index
    return pad_file_offset(slot)


def build_primary_cave(
    base_index: int,
    *,
    pad2_off: int | None = None,
    pad2_seg: int | None = None,
) -> bytes:
    """Redirect only bit-15-tagged BX indices; dual padding (pad1 + pad2)."""
    dx0 = PAD2_OFF if pad2_off is None else pad2_off
    cx0 = PAD2_SEG if pad2_seg is None else pad2_seg
    out = bytearray()

    out += b"\xF7\xC3" + struct.pack("<H", TAG_BIT)  # test bx,8000
    jz_normal_at = len(out)
    out += b"\x74\x00"

    out += b"\x80\xE7\x7F"  # and bh,7f  (same as and bx,7fff; saves 1 byte)
    out += b"\x81\xEB" + struct.pack("<H", base_index)  # sub bx,BASE
    out += b"\x81\xFB" + struct.pack("<H", PAD1_SLOTS)  # cmp bx,96
    jae_pad2_at = len(out)
    out += b"\x73\x00"

    out += b"\xC1\xE3\x04"  # shl bx,4
    out += b"\xBA" + struct.pack("<H", PAD1_OFF)  # mov dx,F9F8
    out += b"\x03\xD3"  # add dx,bx
    out += far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)

    pad2_at = len(out)
    out += b"\x81\xEB" + struct.pack("<H", PAD1_SLOTS)  # sub bx,96
    out += b"\xC1\xE3\x04"  # shl bx,4
    out += b"\xBA" + struct.pack("<H", dx0)  # mov dx,pad2_off
    out += b"\x03\xD3"  # add dx,bx
    out += b"\xB9" + struct.pack("<H", cx0)  # mov cx,pad2_seg
    out += far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)

    normal_off = len(out)
    out += b"\xC1\xE3\x04"
    out += b"\x03\xD3"
    out += far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)

    patch_rel8(out, jz_normal_at, normal_off)
    patch_rel8(out, jae_pad2_at, pad2_at)
    return bytes(out)


def build_dispatch_cave(marker_code: int, cave_addr: int) -> bytes:
    """
    CX = current code on entry.
    Marker: set WRAM flag DS:TAG_FLAG=1 and return without decoding.
    Else: call leaf decoder 06CE.
    """
    out = bytearray()
    out += b"\x81\xF9" + struct.pack("<H", marker_code)  # cmp cx,marker
    jne_decode_at = len(out)
    out += b"\x75\x00"
    out += b"\xC6\x06" + struct.pack("<H", TAG_FLAG) + b"\x01"  # mov byte ptr [TAG_FLAG],1
    out += b"\xC3"  # consume marker
    decode_off = len(out)
    call_at = len(out)
    out += b"\xE8\x00\x00"
    out += b"\xC3"

    patch_rel8(out, jne_decode_at, decode_off)
    struct.pack_into(
        "<H",
        out,
        call_at + 1,
        rel16(cave_addr + call_at + 3, LEAF_DECODER),
    )
    return bytes(out)


def build_store_cave(base_index: int, count: int) -> bytes:
    """
    Tag Hangul glyph indices at the only ROM-wide 1A6E writer (7A:07A0).

    Sticky mode: while DS:TAG_FLAG=1, Hangul-range indices (base_index ..
    base_index+count) keep bit15 and the flag stays set so a single marker
    covers a whole Hangul run. Any non-Hangul glyph clears the flag.
    """
    end = base_index + count
    out = bytearray()
    out += b"\x80\x3E" + struct.pack("<H", TAG_FLAG) + b"\x01"  # cmp [TAG_FLAG],1
    jne_store_at = len(out)
    out += b"\x75\x00"  # jne store

    out += b"\x81\xFE" + struct.pack("<H", base_index)  # cmp si,base
    jb_clear_at = len(out)
    out += b"\x72\x00"  # jb clear

    out += b"\x81\xFE" + struct.pack("<H", end)  # cmp si,end
    jae_clear_at = len(out)
    out += b"\x73\x00"  # jae clear

    out += b"\x81\xCE\x00\x80"  # or si,8000  (keep flag sticky)
    jmp_store_at = len(out)
    out += b"\xEB\x00"  # jmp store

    clear_off = len(out)
    out += b"\xC6\x06" + struct.pack("<H", TAG_FLAG) + b"\x00"  # mov [TAG_FLAG],0

    store_off = len(out)
    out += b"\x89\xB7" + struct.pack("<H", 0x1A6E)  # mov [bx+1A6E],si
    out += b"\xC3"

    patch_rel8(out, jne_store_at, store_off)
    patch_rel8(out, jb_clear_at, clear_off)
    patch_rel8(out, jae_clear_at, clear_off)
    patch_rel8(out, jmp_store_at, store_off)
    return bytes(out)


def verify_stock_sites(rom: bytearray) -> None:
    if bytes(rom[PRIMARY_SITE : PRIMARY_SITE + PRIMARY_SITE_LEN]) != PRIMARY_EXPECT:
        raise RuntimeError("Unexpected primary site")
    if bytes(rom[DISPATCH_SITE : DISPATCH_SITE + DISPATCH_SITE_LEN]) != DISPATCH_EXPECT:
        raise RuntimeError("Unexpected dict dispatch site")
    if bytes(rom[PARSER_B_CALL : PARSER_B_CALL + PARSER_B_CALL_LEN]) != PARSER_B_EXPECT:
        raise RuntimeError("Unexpected parser-B call site")
    if bytes(rom[STORE_SITE : STORE_SITE + STORE_SITE_LEN]) != STORE_EXPECT:
        raise RuntimeError("Unexpected glyph store site")
    main = bytes(rom[MAIN_CAVE : MAIN_CAVE + MAIN_CAVE_MAX])
    if not all(b == 0xFF for b in main):
        raise RuntimeError(f"Main cave {MAIN_CAVE:06X} is not stock FF")
    ext = bytes(rom[EXT_CAVE : EXT_CAVE + EXT_CAVE_MAX])
    if not all(b == 0xFF for b in ext):
        raise RuntimeError(f"Ext cave {EXT_CAVE:06X} is not stock FF")


def apply_hook(
    rom: bytearray,
    *,
    base_index: int,
    count: int,
    marker_code: int,
) -> dict:
    verify_stock_sites(rom)

    if count > PAD_TOTAL_SLOTS:
        raise RuntimeError(
            f"Hangul count {count} exceeds pad capacity "
            f"{PAD1_SLOTS}+{PAD2_SLOTS}={PAD_TOTAL_SLOTS}"
        )

    primary = build_primary_cave(base_index)
    if len(primary) > HANGUL_PRIMARY_BUDGET:
        raise RuntimeError(
            f"Hangul primary cave {len(primary)}B exceeds P1 budget "
            f"{HANGUL_PRIMARY_BUDGET}B (ext_dict helper fixed at 7F:FC8C)"
        )
    if len(primary) > EXT_CAVE_MAX:
        raise RuntimeError(f"Ext primary overflow: {len(primary)}>{EXT_CAVE_MAX}")

    trampoline = far_jmp(EXT_CAVE & 0xFFFF, EXT_CAVE_SEG)
    dispatch_addr = MAIN_CAVE + len(trampoline)
    dispatch = build_dispatch_cave(marker_code, dispatch_addr)
    store_addr = dispatch_addr + len(dispatch)
    store = build_store_cave(base_index, count)

    main_total = len(trampoline) + len(dispatch) + len(store)
    if main_total > MAIN_CAVE_MAX:
        raise RuntimeError(f"Main cave overflow: {main_total}>{MAIN_CAVE_MAX}")

    rom[EXT_CAVE : EXT_CAVE + len(primary)] = primary
    rom[MAIN_CAVE : MAIN_CAVE + len(trampoline)] = trampoline
    rom[dispatch_addr : dispatch_addr + len(dispatch)] = dispatch
    rom[store_addr : store_addr + len(store)] = store

    rom[PRIMARY_SITE : PRIMARY_SITE + PRIMARY_SITE_LEN] = (
        near_jmp(PRIMARY_SITE, MAIN_CAVE) + b"\x90" * (PRIMARY_SITE_LEN - 3)
    )
    rom[DISPATCH_SITE : DISPATCH_SITE + DISPATCH_SITE_LEN] = b"\x8B\xCA\x8B\xD7" + near_call(
        DISPATCH_SITE + 4, dispatch_addr
    )
    rom[PARSER_B_CALL : PARSER_B_CALL + PARSER_B_CALL_LEN] = near_call(
        PARSER_B_CALL, dispatch_addr
    )
    rom[STORE_SITE : STORE_SITE + STORE_SITE_LEN] = near_call(STORE_SITE, store_addr) + b"\x90"

    return {
        "strategy": "store_flag_tag_dual_pad",
        "primary_site": f"{PRIMARY_SITE:06X}",
        "dispatch_site": f"{DISPATCH_SITE:06X}",
        "parser_b_call": f"{PARSER_B_CALL:06X}",
        "store_site": f"{STORE_SITE:06X}",
        "main_cave": f"{MAIN_CAVE:06X}",
        "ext_cave": f"{EXT_CAVE:06X}",
        "ext_cave_seg": f"{EXT_CAVE_SEG:04X}",
        "primary_cave_len": len(primary),
        "hangul_primary_budget": HANGUL_PRIMARY_BUDGET,
        "trampoline_len": len(trampoline),
        "dispatch_cave_len": len(dispatch),
        "store_cave_len": len(store),
        "base_index": base_index,
        "base_code": f"{base_index + 0xDF20:04X}",
        "count": count,
        "marker_code": f"{marker_code:04X}",
        "pad1_off": f"{PAD1_OFF:04X}",
        "pad1_slots": PAD1_SLOTS,
        "pad2_bank": f"{PAD2_BANK:02X}",
        "pad2_seg": f"{PAD2_SEG:04X}",
        "pad2_off": f"{PAD2_OFF:04X}",
        "pad2_slots": PAD2_SLOTS,
        "pad_total_slots": PAD_TOTAL_SLOTS,
        "pad_off": f"{PAD1_OFF:04X}",
        "stock_ui_rule": "untagged indices always use original 40:0440 table",
        "hangul_rule": (
            f"tagged slot<{PAD1_SLOTS} → 40:F9F8; "
            f"slot>={PAD1_SLOTS} → CX={PAD2_SEG:04X} {PAD2_BANK:02X}:{PAD2_OFF:04X} "
            f"(bank{PAD2_BANK:02X} window hypothesis); "
            "marker is sticky across Hangul runs"
        ),
    }


def upgrade_store_cave_sticky(
    rom: bytearray,
    *,
    base_index: int,
    count: int,
) -> dict:
    """
    Rewrite the installed store cave to sticky-marker semantics without
    reinstalling trampoline/dispatch (works on already-hooked ROMs).
    """
    if rom[STORE_SITE] != 0xE8:
        raise RuntimeError("Store site is not a near call (hook not installed?)")
    rel = struct.unpack_from("<H", rom, STORE_SITE + 1)[0]
    store_addr = (STORE_SITE + 3 + rel) & 0xFFFF
    store_abs = (STORE_SITE & 0xFF0000) | store_addr
    new_store = build_store_cave(base_index, count)
    # Keep any leftover bytes as NOP if the new cave is shorter; reject growth
    # past MAIN_CAVE_MAX from MAIN_CAVE.
    cave_end = MAIN_CAVE + MAIN_CAVE_MAX
    if store_abs + len(new_store) > cave_end:
        raise RuntimeError(
            f"Sticky store cave does not fit "
            f"({store_abs:06X}+{len(new_store)} > {cave_end:06X})"
        )
    # Preserve bytes after the old store until we know old length; overwrite
    # with new store and NOP-pad a small trailer for clarity.
    rom[store_abs : store_abs + len(new_store)] = new_store
    return {
        "store_abs": f"{store_abs:06X}",
        "store_cave_len": len(new_store),
        "base_index": base_index,
        "count": count,
        "mode": "sticky",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--map",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_char_map.json",
    )
    args = ap.parse_args()

    mapping = json.loads(args.map.read_text(encoding="utf-8"))
    pad = mapping.get("padding_store") or {}
    base_code = int(pad["base_code"], 16)
    count = int(pad["count"])
    marker_code = int(pad["marker_code"], 16)

    rom = bytearray(load_rom(args.rom))
    report = apply_hook(
        rom,
        base_index=base_code - 0xDF20,
        count=count,
        marker_code=marker_code,
    )
    report["checksum"] = f"{update_ws_checksum(rom):04X}"
    args.out.write_bytes(rom)
    rep_path = args.out.with_suffix(".hook.json")
    rep_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"Installed dual-pad Hangul hook: marker={marker_code:04X}, "
        f"codes={base_code:04X}-{base_code + count - 1:04X} "
        f"(pad1={min(count, PAD1_SLOTS)}, "
        f"pad2={max(0, count - PAD1_SLOTS)})"
    )
    print(
        f"Caves: ext_primary {report['primary_cave_len']}, "
        f"main trampoline+dispatch+store "
        f"{report['trampoline_len']}+{report['dispatch_cave_len']}"
        f"+{report['store_cave_len']}/{MAIN_CAVE_MAX}"
    )
    print(f"Wrote {args.out}")
    print(f"Wrote {rep_path}")


if __name__ == "__main__":
    main()
