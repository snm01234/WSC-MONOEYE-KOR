#!/usr/bin/env python3
"""
Build Hangul TBL + compact font records for translation characters.

Confirmed by the renderer at ROM 7A:0403:
  glyph_index = code                    (code < E000)
  glyph_index = code - DF20             (code >= E000)
  record = segment_40:0440 + index * 16

Each record is an 8x8 2bpp bitmap. The game expands it to 16x16 on screen.

Slot allocation (see hangul_allocator.py):
  1) E740–E7FF          — primary Hangul window
  2) unused/unassigned E000–E73D — safe JP reclaim
  3) E800–EFFF          — extended pages (length walker: lead >= E0 is 2-byte;
                          F0–FE remain dictionary tokens and are never allocated)
  4) optional rare JP recycle (--recycle-rare N)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections import Counter
from typing import Dict, List, Set

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    compact_font_file_offset,
    decode_compact_font_record,
    encode_compact_font_record,
    find_rom,
    load_rom,
    text_code_to_glyph_index,
)
from font_pipeline import find_system_font  # noqa: E402
from hangul_allocator import (  # noqa: E402
    EXTENDED_END,
    HANGUL_PRIMARY_END,
    HANGUL_PRIMARY_START,
    allocate_hangul_codes,
    hangul_by_frequency,
    scan_extended_code_usage,
)
from translation_source_policy import assert_translation_source_allowed  # noqa: E402
from patch_font_hangul_hook import (  # noqa: E402
    PAD1_FILE,
    PAD1_SLOTS,
    PAD2_BANK,
    PAD2_FILE,
    PAD2_OFF,
    PAD2_SEG,
    PAD2_SLOTS,
    PAD_TOTAL_SLOTS,
    pad_file_offset,
)

HANGUL_CODE_START = HANGUL_PRIMARY_START


def _hangul_has_batchim(ch: str) -> bool:
    code = ord(ch)
    if not (0xAC00 <= code <= 0xD7A3):
        return False
    return ((code - 0xAC00) % 28) != 0


def _thin_binary_glyph(
    pixels: List[List[int]], *, target_ink: int = 15
) -> List[List[int]]:
    """Drop weakest edge pixels until ink ~= target (keeps strokes connected)."""
    grid = [row[:] for row in pixels]
    def ink_count() -> int:
        return sum(1 for row in grid for v in row if v)

    def neighbors4(y: int, x: int) -> int:
        n = 0
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < 8 and 0 <= nx < 8 and grid[ny][nx]:
                n += 1
        return n

    while ink_count() > target_ink:
        edge: List[tuple[int, int, int]] = []
        for y in range(8):
            for x in range(8):
                if not grid[y][x]:
                    continue
                n = neighbors4(y, x)
                if n <= 2:
                    edge.append((n, y, x))
        if not edge:
            break
        edge.sort()  # peel lowest-connectivity first
        _, y, x = edge[0]
        grid[y][x] = 0
    return grid


def render_compact_glyph(ch: str, font_path: str) -> List[List[int]]:
    """Rasterize to 8x8 compact 2bpp.

    Prefer Galmuri7 (true 8px Hangul pixel font) for batchim readability.
    Binary 0/3 only (mid-levels stipple on this blitter). Soft-thin toward
    ink≈15 so weight stays closer to JP without breaking stems.
    """
    root = Path(__file__).resolve().parents[1]
    galmuri = root / "assets" / "fonts" / "Galmuri7.ttf"
    use_galmuri = galmuri.exists() and ("가" <= ch <= "힣")
    path = str(galmuri) if use_galmuri else font_path

    if use_galmuri:
        # Pixel-perfect: Galmuri7 is designed for an 8px em cell.
        size = 8
        image = Image.new("L", (8, 8), 0)
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(path, size=size)
        bbox = draw.textbbox((0, 0), ch, font=font)
        draw.text((-bbox[0], -bbox[1]), ch, fill=255, font=font)
        pixels = [
            [3 if image.getpixel((xx, yy)) >= 128 else 0 for xx in range(8)]
            for yy in range(8)
        ]
        # Never thin Galmuri: edge-peel turns 드→느, strips batchim bars, etc.
        return pixels

    # Fallback: gulim/malgun path (non-Hangul or missing Galmuri).
    large = Image.new("L", (64, 64), 0)
    draw = ImageDraw.Draw(large)
    try:
        font = ImageFont.truetype(path, size=50, index=0)
    except TypeError:
        font = ImageFont.truetype(path, size=50)
    bbox = draw.textbbox((0, 0), ch, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = (64 - width) // 2 - bbox[0]
    y = (64 - height) // 2 - bbox[1] - 1
    draw.text((x, y), ch, fill=255, font=font)
    small = large.resize((8, 8), Image.Resampling.BOX)
    peak = max(small.getpixel((xx, yy)) for yy in range(8) for xx in range(8))
    if peak <= 0:
        return [[0] * 8 for _ in range(8)]
    thr = max(8, int(peak * 0.32))
    pixels = [
        [3 if small.getpixel((xx, yy)) >= thr else 0 for xx in range(8)]
        for yy in range(8)
    ]
    target = 17 if _hangul_has_batchim(ch) else 15
    return _thin_binary_glyph(pixels, target_ink=target)


def render_preview(pixels: List[List[int]], scale: int = 10) -> Image.Image:
    shades = (255, 170, 85, 0)
    image = Image.new("L", (8, 8), 255)
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            image.putpixel((x, y), shades[value])
    return image.resize((8 * scale, 8 * scale), Image.Resampling.NEAREST).convert(
        "RGB"
    )


def collect_chars(texts: List[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for text in texts:
        for ch in text.replace(" ", "　"):
            if ch not in seen:
                seen.add(ch)
                ordered.append(ch)
    return ordered


def collect_chars_by_frequency(texts: List[str]) -> List[str]:
    """Hangul first by frequency (desc), then remaining unique chars in first-seen order."""
    counts: Counter[str] = Counter()
    first_seen: List[str] = []
    seen: Set[str] = set()
    for text in texts:
        for ch in text.replace(" ", "　"):
            counts[ch] += 1
            if ch not in seen:
                seen.add(ch)
                first_seen.append(ch)
    hangul = hangul_by_frequency(texts)
    others = [ch for ch in first_seen if not ("가" <= ch <= "힣")]
    return hangul + others


def load_translation_texts(path: Path) -> List[str]:
    if path.suffix.lower() == ".csv":
        import csv

        csv.field_size_limit(10_000_000)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [row.get("ko") or "" for row in csv.DictReader(handle)]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "lines" in payload:
        return [line.get("ko") or "" for line in payload["lines"]]
    if isinstance(payload, dict) and "entries" in payload:
        return [str(v) for v in payload["entries"].values()]
    if isinstance(payload, dict):
        return [str(v) for k, v in payload.items() if k != "engine"]
    raise SystemExit(f"Unsupported translations format: {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--tbl", type=Path, default=ROOT / "data" / "monoeye.tbl")
    ap.add_argument(
        "--translations",
        type=Path,
        default=ROOT / "data" / "translations_seed.json",
    )
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "patch")
    ap.add_argument(
        "--by-frequency",
        action="store_true",
        default=True,
        help="Assign most frequent Hangul first (default on)",
    )
    ap.add_argument(
        "--no-by-frequency",
        action="store_false",
        dest="by_frequency",
        help="Assign Hangul in first-seen order instead of frequency",
    )
    ap.add_argument(
        "--recycle-rare",
        type=int,
        default=0,
        metavar="N",
        help="Also recycle JP codes with usage 1..N (default 0 = off)",
    )
    ap.add_argument(
        "--primary-only",
        action="store_true",
        default=True,
        help="Only use E740–E7FF (default). Avoids New Game crashes from glyph reclaim/E8.",
    )
    ap.add_argument(
        "--allow-extended-font",
        action="store_false",
        dest="primary_only",
        help="Allow JP-slot reclaim + E800+ glyph pages (experimental; may crash New Game)",
    )
    ap.add_argument(
        "--code-end",
        type=lambda s: int(s, 16),
        default=EXTENDED_END,
        help="Hard stop for extended pool (default EFFF). Kept for compatibility.",
    )
    ap.add_argument(
        "--seed-priority",
        type=Path,
        default=ROOT / "data" / "translations_seed.json",
        help="Prefer Hangul from this seed file when packing the primary window",
    )
    ap.add_argument(
        "--text-safe",
        action="store_true",
        help="Only assign Hangul to E740–E7FF codes unused by original JP script/dict "
        "(WARNING: those slots are often nonempty UI glyphs — crashes after narration).",
    )
    ap.add_argument(
        "--tail-pad-safe",
        action="store_true",
        help="Write Hangul into bank40 trailing FF padding (usage==0). "
        "Does NOT display — font pages are E0–E7 only.",
    )
    ap.add_argument(
        "--e7-blank-safe",
        action="store_true",
        help="Write Hangul only into blank (00/FF) glyph slots in E000–E7FF (~8). "
        "Displayable without overwriting nonempty UI glyphs.",
    )
    ap.add_argument(
        "--padding-store",
        action="store_true",
        help="Assign consecutive E740+ codes but store pixels in bank40/3F FF padding "
        "(40:F9F8 then 3F:C5CE). Pair with patch_font_hangul_hook.py — "
        "does not overwrite stock glyphs.",
    )
    ap.add_argument(
        "--padding-max",
        type=int,
        default=PAD_TOTAL_SLOTS,
        help=(
            f"Max Hangul glyphs in padding store "
            f"(default {PAD1_SLOTS}+{PAD2_SLOTS}={PAD_TOTAL_SLOTS}; "
            "contiguous codes stop before marker)"
        ),
    )
    ap.add_argument(
        "--padding-base-code",
        type=lambda s: int(s, 16),
        default=HANGUL_PRIMARY_START,
        help="First visible Hangul code for padding-store (default E740)",
    )
    ap.add_argument(
        "--padding-marker-code",
        type=lambda s: int(s, 16),
        default=0xE3DB,
        help="Unused two-byte marker prefixed before padding-store Hangul (default E3DB)",
    )
    args = ap.parse_args()

    assert_translation_source_allowed(
        args.translations,
        role="Hangul font character collection",
    )
    if args.seed_priority:
        assert_translation_source_allowed(
            args.seed_priority,
            role="Hangul font seed priority",
        )

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    base_tbl = Tbl.load(args.tbl)
    texts = load_translation_texts(args.translations)
    # Seed Hangul first so opening always fits in the 192-slot safe window.
    seed_hangul: List[str] = []
    if args.seed_priority and args.seed_priority.exists():
        seed_texts = load_translation_texts(args.seed_priority)
        seed_hangul = hangul_by_frequency(seed_texts)
    chars = (
        collect_chars_by_frequency(texts) if args.by_frequency else collect_chars(texts)
    )
    hangul_chars = [ch for ch in chars if "가" <= ch <= "힣"]
    if seed_hangul:
        rest = [ch for ch in hangul_chars if ch not in set(seed_hangul)]
        hangul_chars = seed_hangul + rest
    other_chars = [ch for ch in chars if not ("가" <= ch <= "힣")]

    font_path = find_system_font()
    if not font_path:
        raise SystemExit("No Hangul system font found (malgun/gulim/batang)")

    rom = bytearray(load_rom(args.rom or find_rom(ROOT)))
    dictionary = Dictionary(rom)
    usage = scan_extended_code_usage(rom, dictionary)

    PAD_SLOTS = PAD_TOTAL_SLOTS

    if args.padding_store:
        # Consecutive codes; pixels go to padding. A marker prefix lets the
        # decoder tag only Korean-produced indices, preserving stock UI reads
        # that happen to share the same numeric glyph index.
        if not 0xE000 <= args.padding_base_code <= 0xEFFF:
            raise ValueError("--padding-base-code must be E000-EFFF")
        if not 0xE000 <= args.padding_marker_code <= 0xEFFF:
            raise ValueError("--padding-marker-code must be E000-EFFF")
        if 0xF000 <= args.padding_marker_code <= 0xFEFF:
            raise ValueError("--padding-marker-code cannot use dictionary token space")
        if usage[args.padding_marker_code] != 0:
            raise ValueError(
                f"Marker {args.padding_marker_code:04X} is used "
                f"{usage[args.padding_marker_code]} times in stock text"
            )
        # Keep codes contiguous for blitter slot=index-BASE math.
        # Marker may sit below the Hangul window (preferred) or above it.
        if args.padding_marker_code < args.padding_base_code:
            contiguous_cap = 0xF000 - args.padding_base_code
        else:
            contiguous_cap = args.padding_marker_code - args.padding_base_code
            if contiguous_cap <= 0:
                raise ValueError("padding marker collides with --padding-base-code")
        n = min(len(hangul_chars), args.padding_max, PAD_SLOTS, contiguous_cap)
        if args.padding_base_code + n - 1 > 0xEFFF:
            raise ValueError("padding-store code range exceeds EFFF")
        if args.padding_base_code <= args.padding_marker_code < args.padding_base_code + n:
            raise ValueError("padding marker overlaps assigned Hangul code range")
        chosen = hangul_chars[:n]
        overflow_pre = hangul_chars[n:]
        char_to_code_alloc = {
            ch: args.padding_base_code + i for i, ch in enumerate(chosen)
        }
        from hangul_allocator import AllocResult

        alloc = AllocResult(
            char_to_code=char_to_code_alloc,
            overflow_chars=overflow_pre,
            pool_counts={"padding_marked_codes": len(chosen)},
            reused_jp_codes=[],
        )
    else:
        alloc = allocate_hangul_codes(
            hangul_chars,
            base_tbl,
            usage,
            rom=rom,
            recycle_rare_max_usage=args.recycle_rare,
            primary_only=(
                args.primary_only
                and not args.text_safe
                and not args.tail_pad_safe
                and not args.e7_blank_safe
            ),
            text_safe_primary=args.text_safe,
            tail_pad_safe=args.tail_pad_safe,
            e7_blank_safe=args.e7_blank_safe,
        )
    # Honor --code-end by dropping assignments past the hard stop.
    if args.code_end < EXTENDED_END:
        filtered = {
            ch: code for ch, code in alloc.char_to_code.items() if code <= args.code_end
        }
        overflow = [ch for ch in hangul_chars if ch not in filtered]
        overflow.extend(alloc.overflow_chars)
        from hangul_allocator import AllocResult

        pool_counts: Counter = Counter()
        for ch, code in filtered.items():
            if HANGUL_PRIMARY_START <= code <= HANGUL_PRIMARY_END:
                pool_counts["primary_E740"] += 1
            elif code < HANGUL_PRIMARY_START:
                pool_counts["safe_reclaim_E000"] += 1
            else:
                pool_counts["extended_E800"] += 1
        alloc = AllocResult(
            char_to_code=filtered,
            overflow_chars=list(dict.fromkeys(overflow)),
            pool_counts=dict(pool_counts),
            reused_jp_codes=sorted(
                c for c in filtered.values() if c < HANGUL_PRIMARY_START
            ),
        )

    code_to_char = dict(base_tbl.code_to_char)
    char_to_code = dict(base_tbl.char_to_code)
    code_to_char[0x01] = "　"
    char_to_code["　"] = 0x01
    char_to_code[" "] = 0x01

    mapping: Dict[str, dict] = {}
    new_chars: List[str] = []
    overflow_chars: List[str] = list(alloc.overflow_chars)

    for ch in other_chars:
        if ch in char_to_code and not (ch.isascii() and ch.isalpha()):
            mapping[ch] = {
                "code": f"{char_to_code[ch]:04X}",
                "reuse": True,
                "file_offset": None,
            }

    for ch, code in alloc.char_to_code.items():
        # Drop previous JP char mapping when reclaiming a code slot.
        old = code_to_char.get(code)
        if old is not None and old != ch and char_to_code.get(old) == code:
            del char_to_code[old]

        pixels = render_compact_glyph(ch, font_path)
        record = encode_compact_font_record(pixels)
        if args.padding_store:
            slot = code - args.padding_base_code
            if slot >= PAD_SLOTS:
                raise RuntimeError(f"Padding slot overflow for {ch} slot={slot}")
            off = pad_file_offset(slot)
            # Never touch stock glyph bytes at compact_font_file_offset(code).
            rom[off : off + 16] = record
            pool = (
                "padding_store"
                if slot < PAD1_SLOTS
                else f"padding_store_bank{PAD2_BANK:02X}"
            )
        else:
            off = compact_font_file_offset(code)
            rom[off : off + 16] = record
            pool = (
                "primary_E740"
                if HANGUL_PRIMARY_START <= code <= HANGUL_PRIMARY_END
                else "safe_reclaim_E000"
                if code < HANGUL_PRIMARY_START
                else "extended_E800"
            )
        code_to_char[code] = ch
        char_to_code[ch] = code
        mapping[ch] = {
            "code": f"{code:04X}",
            "reuse": False,
            "pool": pool,
            "glyph_index": text_code_to_glyph_index(code),
            "file_offset": off,
            "stock_glyph_untouched": bool(args.padding_store),
        }
        new_chars.append(ch)

    if args.padding_store:
        # Decodes to an empty string during static verification. At runtime the
        # marker is consumed by the 7A:073B dispatch hook and never reaches the
        # glyph-index buffer.
        code_to_char[args.padding_marker_code] = ""

    overflow_chars.extend(ch for ch in hangul_chars if ch not in alloc.char_to_code)
    overflow_chars = list(dict.fromkeys(overflow_chars))

    assigned_codes = sorted(alloc.char_to_code.values())
    code_lo = assigned_codes[0] if assigned_codes else HANGUL_PRIMARY_START
    code_hi = assigned_codes[-1] if assigned_codes else HANGUL_PRIMARY_START

    tbl_path = out / "hangul_patch.tbl"
    lines = [
        "# Mono-Eye Hangul patch TBL (JP base + Hangul via primary/reclaim/E8+)"
    ]
    for code, ch in sorted(code_to_char.items()):
        lines.append(f"{code:02X}={ch}" if code <= 0xFF else f"{code:04X}={ch}")
    tbl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    map_path = out / "hangul_char_map.json"
    map_obj = {
        "font": font_path,
        "code_start": f"{code_lo:04X}",
        "code_end": f"{code_hi:04X}",
        "primary_range": f"{HANGUL_PRIMARY_START:04X}-{HANGUL_PRIMARY_END:04X}",
        "extended_cap": f"{args.code_end:04X}",
        "glyph_formula": "file=0x400440+(code-0xDF20)*16",
        "record_format": "8x8 packed 2bpp, four low-to-high pixels per byte",
        "allocator": {
            "by_frequency": args.by_frequency,
            "recycle_rare_max_usage": args.recycle_rare,
            "pool_counts": alloc.pool_counts,
            "reused_jp_codes": [f"{c:04X}" for c in alloc.reused_jp_codes],
            "padding_store": bool(args.padding_store),
        },
        "new_char_count": len(new_chars),
        "overflow_count": len(overflow_chars),
        "overflow_chars": overflow_chars,
        "mapping": mapping,
    }
    if args.padding_store and new_chars:
        n_chars = len(new_chars)
        map_obj["padding_store"] = {
            "base_code": f"{args.padding_base_code:04X}",
            "count": n_chars,
            "pad_file_offset": f"{PAD1_FILE:06X}",
            "pad1_slots": min(n_chars, PAD1_SLOTS),
            "pad2_file_offset": f"{PAD2_FILE:06X}",
            "pad2_slots": max(0, n_chars - PAD1_SLOTS),
            "pad2_bank": f"{PAD2_BANK:02X}",
            "pad2_seg_hypothesis": f"{PAD2_SEG:04X}",
            "pad_total_slots": PAD_TOTAL_SLOTS,
            "marker_code": f"{args.padding_marker_code:04X}",
            "marker_strategy": "prefix_each_hangul_and_tag_1A6E_high_bit",
            "hook": "tools/patch_font_hangul_hook.py",
            "note": (
                "Stock UI glyph reads remain untagged; marked Hangul uses "
                f"bank40:F9F8 then bank{PAD2_BANK:02X}:{PAD2_OFF:04X} "
                f"(CX={PAD2_SEG:04X} hypothesis)"
            ),
        }
        map_obj["glyph_formula"] = (
            f"codes {args.padding_base_code:04X}+; marker "
            f"{args.padding_marker_code:04X}; "
            f"slots0-{PAD1_SLOTS - 1}@0x{PAD1_FILE:06X}; "
            f"slots{PAD1_SLOTS}+@0x{PAD2_FILE:06X}"
        )
    map_path.write_text(
        json.dumps(map_obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if new_chars:
        preview_n = min(len(new_chars), 256)
        strip = Image.new("RGB", (80 * preview_n, 80), "white")
        for i, ch in enumerate(new_chars[:preview_n]):
            off = mapping[ch]["file_offset"]
            glyph = render_preview(
                decode_compact_font_record(bytes(rom[off : off + 16]))
            )
            strip.paste(glyph, (i * 80, 0))
        strip.save(out / "hangul_glyphs.png")

    (out / "rom_font_only.wsc").write_bytes(rom)

    touched_segs = sorted(
        {
            mapping[ch]["file_offset"] // BANK_SIZE
            for ch in new_chars
            if mapping[ch].get("file_offset") is not None
        }
    )
    for seg in touched_segs:
        start = seg * BANK_SIZE
        (out / f"bank_{seg:02X}_font.bin").write_bytes(rom[start : start + BANK_SIZE])

    print(f"Unique chars: {len(chars)}  Hangul glyphs: {len(new_chars)}")
    print(f"Pool counts: {alloc.pool_counts}")
    if assigned_codes:
        print(f"Code span: {code_lo:04X}–{code_hi:04X}")
    if alloc.reused_jp_codes:
        print(f"Reclaimed JP codes: {len(alloc.reused_jp_codes)}")
    if overflow_chars:
        print(f"OVERFLOW {len(overflow_chars)} Hangul chars (no slots left)")
    print(f"Touched font segments: {[f'{s:02X}' for s in touched_segs]}")
    print(f"Wrote {tbl_path}")
    print(f"Wrote {map_path}")
    print(f"Wrote {out / 'rom_font_only.wsc'}")
    if overflow_chars and not args.primary_only:
        sys.exit(2)
    if overflow_chars and args.primary_only:
        print(
            f"NOTE: {len(overflow_chars)} Hangul chars omitted under --primary-only "
            "(safe for New Game; use --allow-extended-font only after emulator validation)"
        )


if __name__ == "__main__":
    main()
