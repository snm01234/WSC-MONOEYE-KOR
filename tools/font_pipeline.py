#!/usr/bin/env python3
"""
Analyze segment-40 font layout and generate Hangul 16x16 4bpp glyphs.

Pipeline:
  1) Detect bpp / glyph stride in bank 40
  2) Dump a glyph atlas PNG
  3) Render Hangul syllables into the same 16x16 4bpp format
  4) Optionally patch glyphs into a working ROM copy
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    SEG_FONT,
    find_rom,
    glyph_16x16_from_4bpp,
    glyph_16x16_to_4bpp,
    load_rom,
    patch_bank,
    slice_bank,
)

# Empirically: bank 40 starts with 0x20 zero bytes, 0x20 FF bytes, then glyphs.
FONT_DATA_START = 0x40
GLYPH_STRIDE_4BPP_16 = 128  # 4 tiles × 32 bytes


def score_4bpp_16(bank: bytes, start: int, stride: int = 128, samples: int = 64) -> float:
    """Higher score => more glyph-like (mixed but structured pixels)."""
    score = 0.0
    for i in range(samples):
        off = start + i * stride
        if off + stride > len(bank):
            break
        chunk = bank[off : off + stride]
        # Prefer non-flat tiles
        uniq = len(set(chunk))
        if uniq < 3:
            score -= 1
            continue
        try:
            canvas = glyph_16x16_from_4bpp(chunk)
        except Exception:
            score -= 2
            continue
        flat = [p for row in canvas for p in row]
        nonzero = sum(1 for p in flat if p)
        if 8 <= nonzero <= 200:
            score += 1
        # Prefer low-ish max index (fonts often use few palette entries)
        if max(flat) <= 3:
            score += 0.25
    return score


def detect_layout(bank: bytes) -> dict:
    candidates = []
    for start in (0x00, 0x20, 0x40, 0x80):
        for stride, label in ((128, "16x16_4bpp"), (64, "16x16_2bpp_or_8x8_4bpp_x2"), (32, "8x8_4bpp")):
            if label.startswith("16x16_4bpp"):
                s = score_4bpp_16(bank, start, 128)
            else:
                # crude entropy score
                s = 0.0
                for i in range(32):
                    off = start + i * stride
                    if off + stride > len(bank):
                        break
                    uniq = len(set(bank[off : off + stride]))
                    s += 1 if uniq > 4 else -0.5
            # Prefer the known padded start (0x40) when scores are close
            if start == FONT_DATA_START and stride == 128:
                s += 5
            candidates.append({"start": start, "stride": stride, "label": label, "score": s})
    candidates.sort(key=lambda c: -c["score"])
    best = candidates[0]
    # Hard preference documented by ROM inspection
    chosen = {
        "start": FONT_DATA_START,
        "stride": GLYPH_STRIDE_4BPP_16,
        "label": "16x16_4bpp",
        "score": next(
            c["score"]
            for c in candidates
            if c["start"] == FONT_DATA_START and c["stride"] == 128
        ),
        "auto_best": best,
    }
    glyph_count = max(0, (BANK_SIZE - chosen["start"]) // chosen["stride"])
    return {
        "best": {**chosen, "glyph_count_estimate": glyph_count},
        "candidates": candidates[:8],
        "notes": (
            "WonderSwan Color text fonts in this title are treated as "
            "16×16 glyphs packed as four 8×8 4bpp tiles (TL,TR,BL,BR), 128 bytes each. "
            "Data begins at 0x40 after zero/FF padding."
        ),
    }


def canvas_to_image(canvas: Sequence[Sequence[int]], scale: int = 1) -> Image.Image:
    h = len(canvas)
    w = len(canvas[0])
    img = Image.new("P", (w, h))
    # Simple 16-color grayscale-ish palette
    palette = []
    for i in range(16):
        v = int(i * 255 / 15)
        palette.extend([v, v, v])
    palette.extend([0] * (768 - len(palette)))
    img.putpalette(palette)
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), canvas[y][x] & 0xF)
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    return img.convert("RGB")


def dump_atlas(
    bank: bytes,
    out_path: Path,
    *,
    start: int = FONT_DATA_START,
    stride: int = GLYPH_STRIDE_4BPP_16,
    cols: int = 32,
    rows: int = 32,
    scale: int = 2,
) -> int:
    count = min(cols * rows, (len(bank) - start) // stride)
    cell = 16 * scale
    atlas = Image.new("RGB", (cols * cell, rows * cell), (0, 0, 0))
    dumped = 0
    for i in range(count):
        off = start + i * stride
        chunk = bank[off : off + stride]
        if stride != 128:
            continue
        canvas = glyph_16x16_from_4bpp(chunk)
        glyph = canvas_to_image(canvas, scale=scale)
        x = (i % cols) * cell
        y = (i // cols) * cell
        atlas.paste(glyph, (x, y))
        dumped += 1
    atlas.save(out_path)
    return dumped


def find_system_font() -> str | None:
    root = Path(__file__).resolve().parents[1]
    candidates = [
        # 8px Hangul pixel font (best batchim readability at compact 8x8).
        str(root / "assets" / "fonts" / "Galmuri7.ttf"),
        r"C:\Windows\Fonts\gulim.ttc",
        r"C:\Windows\Fonts\batang.ttc",
        r"C:\Windows\Fonts\malgun.ttf",
        r"C:\Windows\Fonts\msgothic.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def render_hangul_glyph(
    ch: str,
    font_path: str,
    *,
    size: int = 14,
    ink: int = 15,
) -> List[List[int]]:
    """Render one character into a 16x16 pixel-index canvas."""
    img = Image.new("L", (16, 16), 0)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, size=size)
    # Center roughly
    bbox = draw.textbbox((0, 0), ch, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(0, (16 - tw) // 2 - bbox[0])
    y = max(0, (16 - th) // 2 - bbox[1])
    draw.text((x, y), ch, fill=255, font=font)
    canvas = [[0] * 16 for _ in range(16)]
    for yy in range(16):
        for xx in range(16):
            v = img.getpixel((xx, yy))
            canvas[yy][xx] = ink if v > 64 else 0
    return canvas


def build_hangul_bank(
    syllables: Sequence[str],
    font_path: str,
    *,
    start_index: int = 0x100,
    base_bank: bytes | None = None,
) -> Tuple[bytearray, dict]:
    """
    Place Hangul glyphs into a font bank copy starting at glyph index start_index.
    Index is relative to FONT_DATA_START / 128.
    """
    bank = bytearray(base_bank if base_bank is not None else b"\x00" * BANK_SIZE)
    mapping = {}
    for n, ch in enumerate(syllables):
        idx = start_index + n
        off = FONT_DATA_START + idx * GLYPH_STRIDE_4BPP_16
        if off + GLYPH_STRIDE_4BPP_16 > BANK_SIZE:
            raise ValueError(f"Font bank full at syllable {n} ({ch})")
        canvas = render_hangul_glyph(ch, font_path)
        bank[off : off + GLYPH_STRIDE_4BPP_16] = glyph_16x16_to_4bpp(canvas)
        # Map to extended code space E1xx-style demo codes starting at E100+n
        # Real patching should assign codes via TBL redesign.
        code = 0xE100 + (n % 0x700)
        mapping[ch] = {"glyph_index": idx, "offset": off, "suggested_code": f"{code:04X}"}
    return bank, mapping


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    ap.add_argument(
        "--hangul",
        type=str,
        default="가나다라마바사아자차카타파하한글패치테스트",
        help="Syllables to render into a demo Hangul font bank",
    )
    ap.add_argument("--patch-rom", action="store_true", help="Write patched ROM with demo Hangul glyphs")
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    rom = load_rom(args.rom or find_rom(ROOT))
    bank = slice_bank(rom, SEG_FONT)
    layout = detect_layout(bank)
    (out / "font_layout.json").write_text(json.dumps(layout, indent=2), encoding="utf-8")
    print("Layout detection:", json.dumps(layout["best"]))

    start = layout["best"]["start"] if layout["best"]["stride"] == 128 else FONT_DATA_START
    stride = 128
    atlas_path = out / "font_atlas_40.png"
    n = dump_atlas(bank, atlas_path, start=start, stride=stride, cols=32, rows=32, scale=2)
    print(f"Wrote {atlas_path} ({n} glyphs)")

    # Dump a few individual glyphs
    glyph_dir = out / "glyphs"
    glyph_dir.mkdir(exist_ok=True)
    for i in range(min(32, n)):
        chunk = bank[start + i * stride : start + (i + 1) * stride]
        img = canvas_to_image(glyph_16x16_from_4bpp(chunk), scale=4)
        img.save(glyph_dir / f"glyph_{i:04X}.png")

    font_path = find_system_font()
    hangul_report = {"font": font_path, "glyphs": {}}
    if not font_path:
        print("WARNING: No system Hangul font found; skipping Hangul generation")
    else:
        syllables = list(dict.fromkeys(args.hangul))  # unique, keep order
        new_bank, mapping = build_hangul_bank(syllables, font_path, start_index=0x100, base_bank=bank)
        hangul_report["glyphs"] = mapping
        (out / "bank_40_font_hangul_demo.bin").write_bytes(new_bank)
        # Preview strip
        strip = Image.new("RGB", (16 * 4 * len(syllables), 16 * 4), (0, 0, 0))
        for i, ch in enumerate(syllables):
            off = mapping[ch]["offset"]
            canvas = glyph_16x16_from_4bpp(new_bank[off : off + 128])
            strip.paste(canvas_to_image(canvas, scale=4), (i * 64, 0))
        strip_path = out / "hangul_demo_strip.png"
        strip.save(strip_path)
        print(f"Wrote Hangul demo bank + {strip_path}")

        # Round-trip encode/decode check on first Hangul glyph
        ch0 = syllables[0]
        off0 = mapping[ch0]["offset"]
        rt = glyph_16x16_to_4bpp(glyph_16x16_from_4bpp(new_bank[off0 : off0 + 128]))
        hangul_report["roundtrip_bytes_match"] = rt == bytes(new_bank[off0 : off0 + 128])

        if args.patch_rom:
            patched = bytearray(rom)
            patch_bank(patched, SEG_FONT, new_bank)
            out_rom = out / "monoeye_hangul_font_demo.wsc"
            out_rom.write_bytes(patched)
            print(f"Wrote {out_rom}")

    (out / "hangul_font_map.json").write_text(
        json.dumps(hangul_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Design notes for full Korean patch
    notes = out / "font_pipeline.md"
    notes.write_text(
        "\n".join(
            [
                "# Font pipeline (segment 40)",
                "",
                f"- Detected layout: start=`{start:#x}`, stride=`{stride}` (16×16 4bpp).",
                f"- Estimated glyphs in bank: `{(BANK_SIZE - start) // stride}`.",
                "- Tile order: TL, TR, BL, BR (each 8×8 4bpp = 32 bytes).",
                "",
                "## Hangul strategy",
                "1. Inventory syllables actually used in the translation.",
                "2. Assign each syllable a code in the unused / reclaimed TBL space "
                "(prefer extending `E0–E7` pages or replacing unused kanji).",
                "3. Render 16×16 bitmaps (Malgun/Gulim) → 4bpp tiles via `glyph_16x16_to_4bpp`.",
                "4. Patch glyph bytes at `0x40 + index*128` in segment 40 "
                "(spill into 41+ if needed).",
                "5. Update the code→glyph index routine (trace via RAM `016AE`).",
                "",
                "## Demo",
                f"- Generated demo glyphs for: {args.hangul}",
                f"- Mapping: `out/hangul_font_map.json`",
                f"- Atlas: `out/font_atlas_40.png`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {notes}")


if __name__ == "__main__":
    main()
