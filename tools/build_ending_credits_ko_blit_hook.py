#!/usr/bin/env python3
"""Plant Korean credit atlases and a diagnostic wait hook.

Force-page-0 diagnostic: after ``74:A783`` wait, always blit the first Korean
page with CPU memcpy from expansion bank 50. Hook code stays in ``7F:FF18``.

Main TIP and live SaveRAM are not touched. Not for promotion.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "atlas_mod", ROOT / "tools" / "build_ending_credits_ko_page_atlas.py"
)
atlas_mod = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(atlas_mod)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC = ROOT / "data/ending_credits_ko.json"
PREVIEWS = ROOT / "out/patch/ending_credits_ko_previews"
OUT = ROOT / "out/patch/ending_credits_ko_force_p0_candidate.wsc"
OUT_SAVE = ROOT / "sram/ending_credits_ko_force_p0_candidate.sav"
REPORT = ROOT / "out/patch/ending_credits_ko_force_p0_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXP_BANK = 0x50
TRAMP_OFF = 0x7FFF18
TRAMP_SEG = 0xF000
TRAMP_CAVE_END = 0x7FFFF0  # stock FF is 7F:FF18-FFEF
SITE_OFF = 0x74A783
SITE_EXPECT = bytes.fromhex("9A33050080")  # lcall 8000:0533
CODE_OFF = 0xE400  # unused in this diagnostic; overlay lives in 7F
HEADER = 16
RECORD = 16
COLS = 28


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict:
    raw = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class Asm:
    def __init__(self, origin: int) -> None:
        self.origin = origin
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.rel8: list[tuple[int, str]] = []

    @property
    def ip(self) -> int:
        return self.origin + len(self.buf)

    def emit(self, data: bytes | bytearray | list[int]) -> None:
        self.buf.extend(bytes(data))

    def label(self, name: str) -> None:
        self.labels[name] = self.ip

    def jcc8(self, opcode: int, name: str) -> None:
        self.emit([opcode, 0])
        self.rel8.append((len(self.buf) - 1, name))

    def patch(self) -> bytes:
        for off, name in self.rel8:
            if name not in self.labels:
                raise BuildError(f"missing label {name}")
            rel = self.labels[name] - (self.origin + off + 1)
            if not -128 <= rel <= 127:
                raise BuildError(f"rel8 {name} out of range ({rel})")
            self.buf[off] = rel & 0xFF
        return bytes(self.buf)


def pal_for_slot(slot: int, cinematic: bool) -> int:
    if cinematic:
        return 6 if slot >= 21 else 10
    return 8 if slot >= 10 else 2


def first_tile_for(cinematic: bool) -> int:
    return 0x80 if cinematic else 1


def build_overlay() -> bytes:
    """Always blit atlas page 0 via CPU copy. Runs in bank 7F (CS=F000).

    ROM1 is already bank 50. Atlas record 0 is at 3000:0010.
    """
    a = Asm(0)  # origin patched later; use relative-only jumps
    a.origin = 0
    a.emit(b"\xB8\x00\x30\x8E\xD8")  # mov ax,3000; mov ds,ax
    a.emit(b"\x33\xC0\x8E\xC0\xFC")  # xor ax,ax; mov es,ax; cld
    a.emit(b"\xBE\x10\x00")  # mov si,16  record 0
    a.emit(b"\x8B\x4C\x04")  # mov cx,[si+4] ntiles
    a.emit(b"\xE3")  # jcxz
    a.rel8.append((len(a.buf), "done"))
    a.emit(b"\x00")
    a.emit(b"\x8B\x54\x0E")  # mov dx,[si+14] first_tile
    a.emit(b"\x8B\x44\x08")  # mov ax,[si+8] gfx_off
    a.emit(b"\x56")  # push si
    a.emit(b"\x8B\xF0")  # mov si,ax
    a.emit(b"\x8B\xFA")  # mov di,dx
    a.emit(b"\xC1\xE7\x05")  # shl di,5
    a.emit(b"\x81\xC7\x00\x40")  # add di,4000
    a.emit(b"\xC1\xE1\x04")  # shl cx,4  words
    a.emit(b"\xF3\xA5")  # rep movsw
    a.emit(b"\x5E")  # pop si
    a.emit(b"\x8A\x44\x02")  # mov al,[si+2] row0
    a.emit(b"\x32\xE4")  # xor ah,ah
    a.emit(b"\x8A\x4C\x03")  # mov cl,[si+3] nrows
    a.emit(b"\x32\xED")  # xor ch,ch
    a.emit(b"\x8B\x54\x0C")  # mov dx,[si+12] pal
    a.emit(b"\xC1\xE2\x09")  # shl dx,9
    a.emit(b"\x8B\x5C\x0E")  # mov bx,[si+14] first_tile
    a.emit(b"\x8B\xF8")  # mov di,ax
    a.emit(b"\xC1\xE7\x06")  # shl di,6
    a.emit(b"\x81\xC7\x00\x30")  # add di,3000
    a.emit(b"\x8B\x74\x06")  # mov si,[si+6] map_off
    a.label("row")
    a.emit(b"\x51\x57")  # push cx, di
    a.emit(b"\xB9\x1C\x00")  # mov cx,28
    a.label("col")
    a.emit(b"\xAD")  # lodsw
    a.emit(b"\x25\xFF\x01")  # and ax,01FF
    a.emit(b"\x03\xC3")  # add ax,bx
    a.emit(b"\x09\xD0")  # or ax,dx
    a.emit(b"\xAB")  # stosw
    a.emit(b"\xE2")  # loop col
    a.rel8.append((len(a.buf), "col"))
    a.emit(b"\x00")
    a.emit(b"\x5F")  # pop di
    a.emit(b"\x83\xC7\x40")  # add di,64
    a.emit(b"\x59")  # pop cx
    a.emit(b"\xE2")  # loop row
    a.rel8.append((len(a.buf), "row"))
    a.emit(b"\x00")
    a.label("done")
    a.emit(b"\xC3")  # near ret
    return a.patch()


def build_trampoline(overlay_off: int | None) -> bytes:
    """Far trampoline in bank 7F. Near-calls overlay after the stock wait."""
    out = bytearray()
    out += bytes.fromhex("9A33050080")  # original wait
    out += b"\x9C\xFA"  # pushf; cli
    out += b"\x50\x53\x51\x52\x56\x57\x1E\x06"
    out += b"\xE4\xC3\x50"  # in al,C3; push ax
    out += b"\xB0" + bytes([EXP_BANK])
    out += bytes.fromhex("9AB5DE0080")
    call_at = len(out)
    out += b"\xE8\x00\x00"  # call overlay (patched)
    out += b"\x58"
    out += bytes.fromhex("9AB5DE0080")
    out += b"\x07\x1F\x5F\x5E\x5A\x59\x5B\x58\x9D\xCB"
    if overlay_off is not None:
        tramp_ip = TRAMP_OFF & 0xFFFF
        disp = overlay_off - (tramp_ip + call_at + 3)
        if not -32768 <= disp <= 32767:
            raise BuildError(f"overlay near-call out of range {disp}")
        struct.pack_into("<H", out, call_at + 1, disp & 0xFFFF)
    return bytes(out)


def plant_atlas(pages: list[dict]) -> tuple[bytes, list[dict]]:
    table_off = HEADER
    table_size = len(pages) * RECORD
    cursor = table_off + table_size
    records = []
    blobs = []
    summaries = []
    for page in pages:
        slot = page["slot"]
        png = PREVIEWS / f"slot{slot:02d}_ko.png"
        if not png.is_file():
            raise BuildError(f"missing preview {png}")
        img = Image.open(png)
        if img.size != (224, 144):
            raise BuildError(f"{png.name} size {img.size}")
        cinematic = bool(page.get("art"))
        row0 = atlas_mod.BAR_ROW0 if cinematic else 0
        nrows = atlas_mod.BAR_ROWS if cinematic else atlas_mod.ROWS
        tilemap, gfx, ntiles = atlas_mod.page_atlas(img, row0, nrows)
        pal = pal_for_slot(slot, cinematic)
        first = first_tile_for(cinematic)
        map_off = cursor
        gfx_off = cursor + len(tilemap)
        cursor = gfx_off + len(gfx)
        if cursor > CODE_OFF:
            raise BuildError(f"atlas collides with hook at {CODE_OFF:#x} (cursor {cursor:#x})")
        rec = struct.pack(
            "<BBBBHHHHHH",
            slot,
            1 if cinematic else 0,
            row0,
            nrows,
            ntiles,
            map_off,
            gfx_off,
            COLS,
            pal,
            first,
        )
        records.append(rec)
        blobs.append(tilemap + gfx)
        summaries.append(
            {
                "slot": slot,
                "cinematic": cinematic,
                "row0": row0,
                "nrows": nrows,
                "ntiles": ntiles,
                "pal": pal,
                "first_tile": first,
                "map_off": f"{map_off:04X}",
                "gfx_off": f"{gfx_off:04X}",
            }
        )
    header = struct.pack(
        "<4sHHHHHH",
        b"ECKO",
        2,
        len(pages),
        table_off,
        cursor,
        EXP_BANK,
        0,
    )
    payload = header + b"".join(records) + b"".join(blobs)
    if len(payload) != cursor:
        raise BuildError(f"payload size {len(payload)} != cursor {cursor}")
    return payload, summaries


def main() -> int:
    if not MAIN.is_file() or not SAVE.is_file():
        raise BuildError("missing parent ROM or SaveRAM")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    parent = MAIN.read_bytes()
    save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE:
        raise BuildError(f"parent is not 16 MiB: {len(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"SaveRAM size {len(save)}")
    base = stock_base(parent)
    if base != 0x800000:
        raise BuildError(f"unexpected stock base {base:#x}")

    overlay = build_overlay()
    tramp_len = len(build_trampoline(None))
    overlay_ip = (TRAMP_OFF & 0xFFFF) + tramp_len
    tramp = build_trampoline(overlay_ip)
    cave = tramp + overlay
    tramp_file = base + TRAMP_OFF
    site_file = base + SITE_OFF
    if parent[site_file : site_file + 5] != SITE_EXPECT:
        raise BuildError(
            f"site {SITE_OFF:06X} is {parent[site_file:site_file+5].hex()} not {SITE_EXPECT.hex()}"
        )
    if tramp_file + len(cave) > base + TRAMP_CAVE_END:
        raise BuildError(f"7F cave overflow {len(cave)}B")
    if any(b != 0xFF for b in parent[tramp_file : tramp_file + len(cave)]):
        raise BuildError("trampoline cave 7F:FF18 is not free FF")
    exp_off = EXP_BANK * 0x10000
    if any(b != 0xFF for b in parent[exp_off : exp_off + 0x10000]):
        raise BuildError(f"expansion bank {EXP_BANK:02X} is not empty FF")

    atlas, summaries = plant_atlas(spec["pages"])
    candidate = bytearray(parent)
    candidate[exp_off : exp_off + len(atlas)] = atlas
    candidate[tramp_file : tramp_file + len(cave)] = cave
    site_lcall = b"\x9A" + struct.pack("<HH", TRAMP_OFF & 0xFFFF, TRAMP_SEG)
    candidate[site_file : site_file + 5] = site_lcall
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    if MAIN.read_bytes() != parent:
        raise BuildError("parent ROM mutated")
    if SAVE.read_bytes() != save:
        raise BuildError("live SaveRAM mutated")

    atomic_bytes(OUT, result)
    OUT_SAVE.parent.mkdir(parents=True, exist_ok=True)
    tmp_save = OUT_SAVE.with_name(f".{OUT_SAVE.name}.{os.getpid()}.tmp")
    shutil.copyfile(SAVE, tmp_save)
    os.replace(tmp_save, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_credits_ko_blit_hook.py",
        "ok": True,
        "status": "force_page0_diagnostic",
        "note": (
            "Diagnostic: after the ending wait, always blit Korean page 0 "
            "(제작 / (주) 뱅가드) with CPU memcpy from expansion bank 50. "
            "Hook code lives in 7F:FF18 (same window as the Hangul hook). "
            "If this is still all Japanese, 74:A783 is not on the live path."
        ),
        "parent": identity(MAIN, parent),
        "candidate": {**identity(OUT, result), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(OUT_SAVE),
        "hook": {
            "site": "74:A783",
            "site_was": SITE_EXPECT.hex(),
            "trampoline": "7F:FF18 CS=F000",
            "overlay": f"7F:{overlay_ip:04X} near-call, CPU memcpy",
            "overlay_bytes": len(overlay),
            "trampoline_bytes": len(tramp),
            "dma": "rep movsw (not A000:0000)",
            "page_id": "forced page 0",
        },
        "atlas": {
            "bank": f"{EXP_BANK:02X}",
            "bytes": len(atlas),
            "pages": summaries,
        },
        "guards": {
            "main_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": SAVE.read_bytes() == save,
            "expansion_was_ff": True,
            "site_was_stock_wait": True,
        },
        "promotion": "blocked_pending_playtest",
        "playtest": [
            "Load ending_credits_ko_force_p0_candidate.wsc + paired SaveRAM",
            "Reach ending from gameplay (do not load a credits savestate)",
            "Every credit page should show 제작 / (주) 뱅가드 if the wait hook fires",
        ],
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("ok", "status", "hook", "promotion")
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
