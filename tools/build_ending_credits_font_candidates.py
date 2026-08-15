#!/usr/bin/env python3
"""Render non-destructive Galmuri font candidates for Korean ending credits.

The current promoted previews use the vector Galmuri11-Condensed face at 11 px.
This comparison keeps all text, placement, cell advances, and cinematic artwork
unchanged while rendering three pixel-oriented Galmuri alternatives.  No ROM,
atlas, main preview, or SaveRAM is modified.
"""
from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE_MODULE = ROOT / "tools/build_ending_credits_ko_previews.py"
SPEC = ROOT / "data/ending_credits_ko.json"
FONT_DIR = ROOT / "assets/fonts/galmuri_tmp"
OUT = ROOT / "out/patch/ending_credits_font_candidates"

SHOW_SLOTS = (0, 17, 18, 19, 20, 21)
SCREEN_W, SCREEN_H = 224, 144


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    filename: str
    size: int
    rationale: str

    @property
    def path(self) -> Path:
        return FONT_DIR / self.filename


VARIANTS = (
    Variant(
        "current_condensed_vector",
        "현재: Galmuri11 Condensed",
        "Galmuri11-Condensed.ttf",
        11,
        "현재 기준선",
    ),
    Variant(
        "bitmap11_regular",
        "후보 A: Galmuri11 Bitmap",
        "Galmuri11Bitmap-Regular-2.40.3.ttf",
        16,
        "픽셀 그리드에 맞는 정규 폭 획으로 모음 내부 공간을 보존",
    ),
    Variant(
        "bitmap11_bold",
        "후보 B: Galmuri11 Bitmap Bold",
        "Galmuri11Bitmap-Bold-2.40.3.ttf",
        16,
        "굵은 픽셀 획으로 1배율 가독성 강화",
    ),
    Variant(
        "bitmap9_regular",
        "후보 C: Galmuri9 Bitmap",
        "Galmuri9Bitmap-Regular-2.40.3.ttf",
        12,
        "작은 픽셀 글꼴로 모음 획 사이 간격을 넓힘",
    ),
)


class CandidateError(RuntimeError):
    pass


def load_renderer():
    spec = importlib.util.spec_from_file_location("ending_preview_renderer", SOURCE_MODULE)
    if spec is None or spec.loader is None:
        raise CandidateError(f"cannot load {SOURCE_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def render_page(renderer, page: dict, font16, font12) -> Image.Image:
    kind = page["kind"]
    if kind == "center_stack":
        return renderer.render_center_stack(page, font16)
    if kind == "two_col":
        return renderer.render_two_col(page, font12)
    if kind in {"header_two_col", "header_two_col_footer"}:
        return renderer.render_header_two_col(page, font16, font12)
    if kind in {"bar_lr", "bar_left_right_stack"}:
        return renderer.render_bar(page, renderer.pick_bar_font(page, font16, font12))
    raise CandidateError(f"unknown page kind {kind}")


def glyph_bbox_report(variant: Variant, texts: list[str]) -> dict:
    font = ImageFont.truetype(str(variant.path), size=variant.size)
    draw = ImageDraw.Draw(Image.new("L", (64, 64), 0))
    chars = sorted({ch for text in texts for ch in text if ch.strip()})
    rows = []
    for ch in chars:
        bbox = draw.textbbox((0, 0), ch, font=font)
        rows.append(
            {
                "char": ch,
                "bbox": list(bbox),
                "width": bbox[2] - bbox[0],
                "height": bbox[3] - bbox[1],
            }
        )
    return {
        "max_width": max(row["width"] for row in rows),
        "max_height": max(row["height"] for row in rows),
        "over_cell12": [row for row in rows if row["width"] > 12],
        "glyphs": rows,
    }


def make_contact_sheet(images: dict[str, dict[int, Image.Image]]) -> Image.Image:
    header_h = 30
    label_w = 62
    cols = len(VARIANTS)
    rows = len(SHOW_SLOTS)
    sheet = Image.new(
        "RGB",
        (label_w + cols * SCREEN_W, header_h + rows * SCREEN_H),
        (24, 24, 28),
    )
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.load_default()
    for col, variant in enumerate(VARIANTS):
        x = label_w + col * SCREEN_W
        draw.rectangle((x, 0, x + SCREEN_W - 1, header_h - 1), fill=(42, 42, 48))
        draw.text((x + 5, 8), variant.label, font=label_font, fill=(240, 240, 240))
    for row, slot in enumerate(SHOW_SLOTS):
        y = header_h + row * SCREEN_H
        draw.rectangle((0, y, label_w - 1, y + SCREEN_H - 1), fill=(42, 42, 48))
        draw.text((8, y + 62), f"slot {slot:02d}", font=label_font, fill=(240, 240, 240))
        for col, variant in enumerate(VARIANTS):
            sheet.paste(images[variant.key][slot], (label_w + col * SCREEN_W, y))
    return sheet


def make_text_crop_sheet(images: dict[str, dict[int, Image.Image]]) -> Image.Image:
    # Native-resolution crops keep the exact 1x pixels visible while reducing
    # the amount of repeated cinematic artwork in the comparison.
    header_h = 30
    label_w = 62
    crop_h = 48
    cols = len(VARIANTS)
    rows = len(SHOW_SLOTS)
    sheet = Image.new(
        "RGB",
        (label_w + cols * SCREEN_W, header_h + rows * crop_h),
        (24, 24, 28),
    )
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.load_default()
    for col, variant in enumerate(VARIANTS):
        x = label_w + col * SCREEN_W
        draw.rectangle((x, 0, x + SCREEN_W - 1, header_h - 1), fill=(42, 42, 48))
        draw.text((x + 5, 8), variant.label, font=label_font, fill=(240, 240, 240))
    for row, slot in enumerate(SHOW_SLOTS):
        y = header_h + row * crop_h
        source_y = 48 if slot == 0 else 96
        draw.rectangle((0, y, label_w - 1, y + crop_h - 1), fill=(42, 42, 48))
        draw.text((8, y + 18), f"slot {slot:02d}", font=label_font, fill=(240, 240, 240))
        for col, variant in enumerate(VARIANTS):
            crop = images[variant.key][slot].crop((0, source_y, SCREEN_W, source_y + crop_h))
            sheet.paste(crop, (label_w + col * SCREEN_W, y))
    return sheet


def main() -> int:
    renderer = load_renderer()
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    pages = spec["pages"]
    page_by_slot = {int(page["slot"]): page for page in pages}
    if any(slot not in page_by_slot for slot in SHOW_SLOTS):
        raise CandidateError("comparison slot is missing from ending-credit spec")
    for variant in VARIANTS:
        if not variant.path.is_file():
            raise CandidateError(f"missing font {variant.path}")

    texts: list[str] = []
    for page in pages:
        encoded = json.dumps(page, ensure_ascii=False)
        # Collect Korean strings without coupling this comparison to page kinds.
        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get("ko"), str):
                    texts.append(value["ko"])
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(page)
        _ = encoded

    images: dict[str, dict[int, Image.Image]] = {}
    summaries = []
    for variant in VARIANTS:
        font16 = renderer.CellFont(variant.path, variant.size, 16)
        font12 = renderer.CellFont(variant.path, variant.size, 12)
        variant_dir = OUT / variant.key
        variant_dir.mkdir(parents=True, exist_ok=True)
        images[variant.key] = {}
        width_issues: list[str] = []
        unique_tiles = {}
        for page in pages:
            width_issues.extend(renderer.width_report(page, font16, font12))
            image = render_page(renderer, page, font16, font12)
            slot = int(page["slot"])
            image.save(variant_dir / f"slot{slot:02d}.png")
            if slot in SHOW_SLOTS:
                images[variant.key][slot] = image
            # Count unique 8x8 text-area tiles as an early atlas-pressure signal.
            row0 = 13 if page.get("art") else 0
            rows = 5 if page.get("art") else 18
            tiles = set()
            raw = image.convert("RGB")
            for row in range(row0, row0 + rows):
                for col in range(28):
                    tile = raw.crop((col * 8, row * 8, col * 8 + 8, row * 8 + 8))
                    tiles.add(tile.tobytes())
            unique_tiles[slot] = len(tiles)
        bbox = glyph_bbox_report(variant, texts)
        summaries.append(
            {
                "key": variant.key,
                "label": variant.label,
                "font": str(variant.path.relative_to(ROOT)).replace("\\", "/"),
                "size": variant.size,
                "rationale": variant.rationale,
                "width_issues": width_issues,
                "max_glyph_width": bbox["max_width"],
                "max_glyph_height": bbox["max_height"],
                "glyphs_over_12px": bbox["over_cell12"],
                "unique_tiles_by_slot": {str(k): v for k, v in unique_tiles.items()},
            }
        )

    OUT.mkdir(parents=True, exist_ok=True)
    contact = make_contact_sheet(images)
    contact.save(OUT / "ending_credits_font_candidates_full.png")
    contact.resize((contact.width * 2, contact.height * 2), Image.Resampling.NEAREST).save(
        OUT / "ending_credits_font_candidates_full_x2.png"
    )
    crops = make_text_crop_sheet(images)
    crops.save(OUT / "ending_credits_font_candidates_text_crops.png")
    crops.resize((crops.width * 3, crops.height * 3), Image.Resampling.NEAREST).save(
        OUT / "ending_credits_font_candidates_text_crops_x3.png"
    )
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_credits_font_candidates.py",
        "ok": True,
        "status": "visual_candidates_only_no_rom_changes",
        "comparison_slots": list(SHOW_SLOTS),
        "variants": summaries,
        "outputs": {
            "full": str((OUT / "ending_credits_font_candidates_full.png").relative_to(ROOT)).replace("\\", "/"),
            "full_x2": str((OUT / "ending_credits_font_candidates_full_x2.png").relative_to(ROOT)).replace("\\", "/"),
            "text_crops": str((OUT / "ending_credits_font_candidates_text_crops.png").relative_to(ROOT)).replace("\\", "/"),
            "text_crops_x3": str((OUT / "ending_credits_font_candidates_text_crops_x3.png").relative_to(ROOT)).replace("\\", "/"),
        },
        "guards": {
            "roms_written": 0,
            "main_previews_modified": False,
            "saveram_modified": False,
        },
    }
    atomic_json(OUT / "ending_credits_font_candidates_report.json", report)
    print(json.dumps({
        "ok": True,
        "status": report["status"],
        "outputs": report["outputs"],
        "variants": [
            {
                "key": row["key"],
                "width_issues": len(row["width_issues"]),
                "max_glyph_width": row["max_glyph_width"],
                "over_12px": len(row["glyphs_over_12px"]),
            }
            for row in summaries
        ],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CandidateError as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
