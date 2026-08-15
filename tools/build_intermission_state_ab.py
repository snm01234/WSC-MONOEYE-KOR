#!/usr/bin/env python3
"""Build a controlled intermission ROM/savestate A/B pair.

A and B use byte-identical ROMs containing the Korean intermission overlay tiles.
Only the savestate differs:

* A keeps the original, stale Japanese tiles serialized in ``Core.bin``.
* B replaces the aligned serialized tile slots with the Korean ROM tile bytes.

The Cygne state exposes ``Core.bin.zst`` inside a normal ZIP container.  This tool
uses BizHawk's bundled ``libzstd.dll`` directly so it does not need a separately
installed Python zstandard package.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TILE_BYTES = 32
EXPANDED_SIZE = 0x1000000
STOCK_SIZE = 0x800000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Zstd:
    """Small checked wrapper around BizHawk's libzstd."""

    def __init__(self, dll: Path):
        self.dll_path = dll
        self.lib = ctypes.CDLL(str(dll))

        self.lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
        self.lib.ZSTD_isError.restype = ctypes.c_uint
        self.lib.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
        self.lib.ZSTD_getErrorName.restype = ctypes.c_char_p

        self.lib.ZSTD_decompress.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        self.lib.ZSTD_decompress.restype = ctypes.c_size_t

        self.lib.ZSTD_compressBound.argtypes = [ctypes.c_size_t]
        self.lib.ZSTD_compressBound.restype = ctypes.c_size_t
        self.lib.ZSTD_compress.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        self.lib.ZSTD_compress.restype = ctypes.c_size_t

    def _checked(self, result: int, operation: str) -> int:
        if self.lib.ZSTD_isError(result):
            raw = self.lib.ZSTD_getErrorName(result)
            name = raw.decode("utf-8", errors="replace") if raw else "unknown error"
            raise RuntimeError(f"{operation} failed: {name}")
        return int(result)

    def decompress(self, data: bytes) -> bytes:
        src = ctypes.create_string_buffer(data)
        # Cygne's current Core.bin is about 108 KiB. Start generously and grow
        # only when libzstd reports that the destination is too small.
        capacity = max(1024 * 1024, len(data) * 16)
        for _ in range(6):
            dst = ctypes.create_string_buffer(capacity)
            result = self.lib.ZSTD_decompress(dst, capacity, src, len(data))
            if not self.lib.ZSTD_isError(result):
                return dst.raw[: int(result)]
            raw = self.lib.ZSTD_getErrorName(result)
            name = raw.decode("utf-8", errors="replace") if raw else "unknown error"
            if "Destination buffer is too small" not in name:
                self._checked(result, "ZSTD_decompress")
            capacity *= 2
        raise RuntimeError("ZSTD_decompress failed after growing the output buffer")

    def compress(self, data: bytes, level: int = 3) -> bytes:
        src = ctypes.create_string_buffer(data)
        capacity = int(self.lib.ZSTD_compressBound(len(data)))
        dst = ctypes.create_string_buffer(capacity)
        result = self._checked(
            self.lib.ZSTD_compress(dst, capacity, src, len(data), level),
            "ZSTD_compress",
        )
        return dst.raw[:result]


def read_state_core(path: Path, zstd: Zstd) -> tuple[bytes, str]:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        for name in ("Core.bin.zst", "Core.bin"):
            if name not in names:
                continue
            data = zf.read(name)
            return (zstd.decompress(data) if name.endswith(".zst") else data), name
    raise ValueError(f"{path} has no Core.bin.zst or Core.bin")


def write_state_with_core(
    source: Path,
    output: Path,
    core_name: str,
    core: bytes,
    zstd: Zstd,
    level: int,
) -> int:
    replacement = zstd.compress(core, level) if core_name.endswith(".zst") else core
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as zin, zipfile.ZipFile(output, "w") as zout:
        for info in zin.infolist():
            payload = replacement if info.filename == core_name else zin.read(info.filename)
            zout.writestr(info, payload)
    return len(replacement)


def all_hits(haystack: bytes, needle: bytes) -> list[int]:
    hits: list[int] = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return hits
        hits.append(found)
        start = found + 1


def choose_alignment(core: bytes, originals: dict[int, bytes]) -> tuple[int, dict[int, int]]:
    """Find the 32-byte residue giving exactly one hit for every target tile."""
    hits_by_tile = {address: all_hits(core, tile) for address, tile in originals.items()}
    candidates: list[tuple[int, dict[int, int]]] = []
    for residue in range(TILE_BYTES):
        placements: dict[int, int] = {}
        for address, hits in hits_by_tile.items():
            aligned = [off for off in hits if off % TILE_BYTES == residue]
            if len(aligned) != 1:
                break
            placements[address] = aligned[0]
        if len(placements) == len(originals):
            candidates.append((residue, placements))
    if len(candidates) != 1:
        summary = {
            f"{address:06X}": [f"{off:06X}" for off in hits]
            for address, hits in hits_by_tile.items()
            if len(hits) != 1
        }
        raise ValueError(
            f"expected one full-coverage tile alignment, found {len(candidates)}; "
            f"non-unique raw hits={summary}"
        )
    residue, placements = candidates[0]
    if len(set(placements.values())) != len(placements):
        raise ValueError("two source tiles resolved to the same Core.bin slot")
    return residue, placements


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock-rom", type=Path, default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc")
    ap.add_argument(
        "--patched-rom",
        type=Path,
        default=ROOT / "out/patch/intermission_state_ab/A_intermission_ko_stock_vram.wsc",
    )
    ap.add_argument(
        "--source-state",
        type=Path,
        default=(
            ROOT
            / "BizHawk-2.11.1-win-x64/WonderSwan/State"
            / "monoeye ko expanded.Cygne/Mednafen.QuickSave1.State"
        ),
    )
    ap.add_argument(
        "--tile-list",
        type=Path,
        default=ROOT / "out/patch/intermission_state_ab/intermission_glyph_tiles.json",
    )
    ap.add_argument(
        "--zstd-dll",
        type=Path,
        default=ROOT / "BizHawk-2.11.1-win-x64/dll/libzstd.dll",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "out/patch/intermission_state_ab",
    )
    ap.add_argument("--zstd-level", type=int, default=3)
    args = ap.parse_args(argv)

    for path in (args.stock_rom, args.patched_rom, args.source_state, args.tile_list, args.zstd_dll):
        if not path.exists():
            raise SystemExit(f"missing: {path}")

    stock = args.stock_rom.read_bytes()
    patched = args.patched_rom.read_bytes()
    if len(stock) != STOCK_SIZE:
        raise SystemExit(f"stock ROM size is {len(stock):#x}, expected {STOCK_SIZE:#x}")
    if len(patched) not in (STOCK_SIZE, EXPANDED_SIZE):
        raise SystemExit(f"patched ROM size is {len(patched):#x}")
    patched_base = STOCK_SIZE if len(patched) == EXPANDED_SIZE else 0

    manifest = json.loads(args.tile_list.read_text(encoding="utf-8"))
    if int(manifest.get("tile_bytes", 0)) != TILE_BYTES:
        raise SystemExit(f"tile list does not declare tile_bytes={TILE_BYTES}")
    addresses = [int(value, 16) for value in manifest["tiles"]]
    if len(addresses) != len(set(addresses)):
        raise SystemExit("tile list contains duplicate addresses")

    originals: dict[int, bytes] = {}
    replacements: dict[int, bytes] = {}
    for address in addresses:
        old = stock[address : address + TILE_BYTES]
        new = patched[patched_base + address : patched_base + address + TILE_BYTES]
        if len(old) != TILE_BYTES or len(new) != TILE_BYTES:
            raise SystemExit(f"tile outside ROM: {address:06X}")
        if old == new:
            raise SystemExit(f"approved tile was not changed in patched ROM: {address:06X}")
        originals[address] = old
        replacements[address] = new

    zstd = Zstd(args.zstd_dll)
    core_a, core_name = read_state_core(args.source_state, zstd)
    alignment, placements = choose_alignment(core_a, originals)

    core_b = bytearray(core_a)
    rows = []
    for address in sorted(addresses):
        core_off = placements[address]
        if bytes(core_b[core_off : core_off + TILE_BYTES]) != originals[address]:
            raise RuntimeError(f"Core.bin tile changed before replacement at {core_off:06X}")
        core_b[core_off : core_off + TILE_BYTES] = replacements[address]
        rows.append(
            {
                "rom_tile": f"{address:06X}",
                "core_offset": f"{core_off:06X}",
                "old_sha256": sha256_bytes(originals[address]),
                "new_sha256": sha256_bytes(replacements[address]),
            }
        )
    core_b_bytes = bytes(core_b)

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    rom_a = out / "A_intermission_ko_stock_vram.wsc"
    rom_b = out / "B_intermission_ko_patched_vram.wsc"
    if args.patched_rom.resolve() != rom_a.resolve():
        shutil.copy2(args.patched_rom, rom_a)
    shutil.copy2(rom_a, rom_b)

    state_a = out / "A_stock_vram.State"
    state_b = out / "B_patched_vram.State"
    shutil.copy2(args.source_state, state_a)
    compressed_b = write_state_with_core(
        args.source_state,
        state_b,
        core_name,
        core_b_bytes,
        zstd,
        args.zstd_level,
    )

    # Reopen the generated state. This catches malformed ZIP/Zstandard output and
    # proves every aligned slot contains B, not A.
    verify_b, verify_name = read_state_core(state_b, zstd)
    if verify_name != core_name or verify_b != core_b_bytes:
        raise RuntimeError("generated B state does not round-trip to the patched Core.bin")
    for address, core_off in placements.items():
        actual = verify_b[core_off : core_off + TILE_BYTES]
        if actual != replacements[address]:
            raise RuntimeError(f"B verification failed at Core.bin {core_off:06X}")

    report = {
        "purpose": "A/B isolates serialized intermission VRAM; A and B ROMs are byte-identical",
        "source_state": str(args.source_state),
        "core_member": core_name,
        "core_bytes": len(core_a),
        "tile_bytes": TILE_BYTES,
        "tile_count": len(addresses),
        "derived_core_alignment_mod_32": f"0x{alignment:02X}",
        "a": {
            "rom": str(rom_a),
            "rom_sha256": sha256_file(rom_a),
            "state": str(state_a),
            "state_sha256": sha256_file(state_a),
            "core_sha256": sha256_bytes(core_a),
            "meaning": "Korean ROM, original serialized Japanese VRAM",
        },
        "b": {
            "rom": str(rom_b),
            "rom_sha256": sha256_file(rom_b),
            "state": str(state_b),
            "state_sha256": sha256_file(state_b),
            "core_sha256": sha256_bytes(core_b_bytes),
            "compressed_core_bytes": compressed_b,
            "meaning": f"same Korean ROM, {len(addresses)} serialized VRAM tile slots replaced with Korean bytes",
        },
        "roms_byte_identical": rom_a.read_bytes() == rom_b.read_bytes(),
        "state_core_changed_bytes": sum(a != b for a, b in zip(core_a, core_b_bytes)),
        "replacements": rows,
    }
    report_path = out / "intermission_state_ab_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"tile alignment : Core.bin offset % 32 = 0x{alignment:02X}")
    print(f"tiles replaced : {len(addresses)}")
    print(f"Core bytes diff: {report['state_core_changed_bytes']}")
    print(f"A ROM == B ROM : {report['roms_byte_identical']}")
    print(f"A state        : {state_a}")
    print(f"B state        : {state_b}")
    print(f"report         : {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
