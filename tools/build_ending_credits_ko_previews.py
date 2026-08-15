#!/usr/bin/env python3
"""Render ending-credit Korean previews (음차 names) at native 224x144.

Does not patch the main TIP. Writes PNGs so layout/width can be judged before
the credit font loader is found. Two-column pages use 12 px advance; single-column
and cinematic bars use 16 px cells to match the Japanese kanji cell size.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "data" / "ending_credits_ko.json"
NATIVE = ROOT / "out" / "patch" / "ending_credits"
OUT = ROOT / "out" / "patch" / "ending_credits_ko_previews"
FONT11 = ROOT / "assets" / "fonts" / "galmuri_tmp" / "Galmuri11.ttf"
FONT11C = ROOT / "assets" / "fonts" / "galmuri_tmp" / "Galmuri11-Condensed.ttf"
SCREEN_W, SCREEN_H = 224, 144
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
VREF = "갱"


def is_wide(ch: str) -> bool:
    if ch == " ":
        return False
    o = ord(ch)
    if 0xAC00 <= o <= 0xD7A3:
        return True
    if 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return True
    if o > 0x2E80:
        return True
    return False


class CellFont:
    def __init__(self, path: Path, size: int, cell: int):
        if not path.is_file():
            raise FileNotFoundError(path)
        self.font = ImageFont.truetype(str(path), size=size)
        self.cell = cell
        probe = Image.new("L", (size * 3, size * 3), 0)
        draw = ImageDraw.Draw(probe)
        self.top = draw.textbbox((0, 0), VREF, font=self.font)[1]
        self.size = size

    def advance(self, ch: str) -> int:
        if ch == " ":
            return max(4, self.cell // 2)
        return self.cell if is_wide(ch) else max(6, self.cell // 2)

    def line_width(self, text: str) -> int:
        return sum(self.advance(ch) for ch in text)

    def blit_line(self, img: Image.Image, text: str, x: int, y: int) -> None:
        draw = ImageDraw.Draw(img)
        cx = x
        for ch in text:
            adv = self.advance(ch)
            if ch != " ":
                glyph = Image.new("L", (adv + 2, self.cell + 2), 0)
                gd = ImageDraw.Draw(glyph)
                bbox = gd.textbbox((0, 0), ch, font=self.font)
                gw = bbox[2] - bbox[0]
                gh = bbox[3] - bbox[1]
                ox = max(0, (adv - gw) // 2 - bbox[0])
                oy = max(0, (self.cell - gh) // 2 - bbox[1])
                gd.text((ox, oy), ch, fill=255, font=self.font)
                rgb = Image.new("RGB", glyph.size, WHITE)
                img.paste(rgb, (cx, y), glyph)
            cx += adv


def center_x(width: int) -> int:
    return max(0, (SCREEN_W - width) // 2)


def new_black() -> Image.Image:
    return Image.new("RGB", (SCREEN_W, SCREEN_H), BLACK)


def load_art(slot: int) -> Image.Image:
    path = NATIVE / f"slot{slot:02d}_native.png"
    img = Image.open(path).convert("RGB")
    if img.size != (SCREEN_W, SCREEN_H):
        img = img.resize((SCREEN_W, SCREEN_H), Image.NEAREST)
    return img


def black_bar(img: Image.Image, y0: int) -> None:
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, y0, SCREEN_W - 1, SCREEN_H - 1), fill=BLACK)


def render_center_stack(page: dict, font: CellFont) -> Image.Image:
    img = new_black()
    lines = [row["ko"] for row in page["lines"]]
    gap = 8
    total = len(lines) * font.cell + gap * (len(lines) - 1)
    y = max(0, (SCREEN_H - total) // 2)
    for text in lines:
        w = font.line_width(text)
        font.blit_line(img, text, center_x(w), y)
        y += font.cell + gap
    return img


def render_two_col(page: dict, font: CellFont) -> Image.Image:
    img = new_black()
    cols = page["columns"]
    col_w = SCREEN_W // 2
    header_y = 16
    name_y = header_y + font.cell + 10
    for i, col in enumerate(cols):
        header = col["header"]["ko"]
        names = [n["ko"] for n in col["names"]]
        x0 = i * col_w
        hw = font.line_width(header)
        font.blit_line(img, header, x0 + max(0, (col_w - hw) // 2), header_y)
        y = name_y
        for name in names:
            nw = font.line_width(name)
            font.blit_line(img, name, x0 + max(0, (col_w - nw) // 2), y)
            y += font.cell + 6
    return img


def render_header_two_col(page: dict, header_font: CellFont, name_font: CellFont) -> Image.Image:
    img = new_black()
    header = page["header"]["ko"]
    hw = header_font.line_width(header)
    header_y = 8
    header_font.blit_line(img, header, center_x(hw), header_y)
    cols = page["columns"]
    col_w = SCREEN_W // 2
    y0 = header_y + header_font.cell + 10
    left_n = max(len(c) for c in cols)
    for i, col in enumerate(cols):
        x0 = i * col_w
        y = y0
        for row in col:
            nw = name_font.line_width(row["ko"])
            name_font.blit_line(img, row["ko"], x0 + max(0, (col_w - nw) // 2), y)
            y += name_font.cell + 6
        _ = left_n
    if page.get("footer"):
        footer = page["footer"]["ko"]
        fw = header_font.line_width(footer)
        header_font.blit_line(img, footer, center_x(fw), SCREEN_H - header_font.cell - 8)
    return img


def pick_bar_font(page: dict, font16: CellFont, font12: CellFont) -> CellFont:
    if page["kind"] == "bar_lr":
        lw = font16.line_width(page["left"]["ko"])
        rw = font16.line_width(page["right"]["ko"])
        if lw + rw + 24 <= SCREEN_W:
            return font16
        return font12
    return font12


def render_bar(page: dict, font: CellFont) -> Image.Image:
    img = load_art(page["slot"]) if page.get("art") else new_black()
    bar_y = 104
    black_bar(img, bar_y)
    y = bar_y + max(0, (SCREEN_H - bar_y - font.cell) // 2)
    if page["kind"] == "bar_lr":
        left, right = page["left"]["ko"], page["right"]["ko"]
        font.blit_line(img, left, 8, y)
        rw = font.line_width(right)
        font.blit_line(img, right, SCREEN_W - 8 - rw, y)
        return img
    left = page["left"]["ko"]
    font.blit_line(img, left, 8, y)
    rights = [r["ko"] for r in page["right"]]
    block_h = len(rights) * font.cell + 2 * (len(rights) - 1)
    ry = bar_y + max(0, (SCREEN_H - bar_y - block_h) // 2)
    max_rw = max(font.line_width(t) for t in rights)
    rx = SCREEN_W - 8 - max_rw
    for text in rights:
        font.blit_line(img, text, rx, ry)
        ry += font.cell + 2
    return img


def width_report(page: dict, font16: CellFont, font12: CellFont) -> list[str]:
    issues = []
    kind = page["kind"]
    slot = page["slot"]

    def check(label: str, text: str, font: CellFont, limit: int) -> None:
        w = font.line_width(text)
        if w > limit:
            issues.append(f"slot{slot:02d} {label} {text!r} width={w} > {limit}")

    if kind == "center_stack":
        for row in page["lines"]:
            check("line", row["ko"], font16, SCREEN_W)
    elif kind == "two_col":
        for col in page["columns"]:
            check("header", col["header"]["ko"], font12, SCREEN_W // 2 - 4)
            for n in col["names"]:
                check("name", n["ko"], font12, SCREEN_W // 2 - 4)
    elif kind in {"header_two_col", "header_two_col_footer"}:
        check("header", page["header"]["ko"], font16, SCREEN_W)
        for col in page["columns"]:
            for n in col:
                check("name", n["ko"], font12, SCREEN_W // 2 - 4)
        if page.get("footer"):
            check("footer", page["footer"]["ko"], font16, SCREEN_W)
    elif kind == "bar_lr":
        lw = font16.line_width(page["left"]["ko"])
        rw = font16.line_width(page["right"]["ko"])
        if lw + rw + 24 > SCREEN_W:
            lw = font12.line_width(page["left"]["ko"])
            rw = font12.line_width(page["right"]["ko"])
        if lw + rw + 24 > SCREEN_W:
            issues.append(
                f"slot{slot:02d} bar {page['left']['ko']!r}+{page['right']['ko']!r} "
                f"lw={lw} rw={rw}"
            )
    elif kind == "bar_left_right_stack":
        check("left", page["left"]["ko"], font12, 80)
        for r in page["right"]:
            check("right", r["ko"], font12, 140)
    return issues


def main() -> int:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    font_path = FONT11C if FONT11C.is_file() else FONT11
    if not font_path.is_file():
        raise SystemExit(f"missing font: {font_path}")
    font16 = CellFont(font_path, 11, 16)
    font12 = CellFont(font_path, 11, 12)
    OUT.mkdir(parents=True, exist_ok=True)
    issues: list[str] = []
    for page in spec["pages"]:
        issues.extend(width_report(page, font16, font12))
        kind = page["kind"]
        if kind == "center_stack":
            img = render_center_stack(page, font16)
        elif kind == "two_col":
            img = render_two_col(page, font12)
        elif kind in {"header_two_col", "header_two_col_footer"}:
            img = render_header_two_col(page, font16, font12)
        elif kind in {"bar_lr", "bar_left_right_stack"}:
            img = render_bar(page, pick_bar_font(page, font16, font12))
        else:
            raise SystemExit(f"unknown kind {kind}")
        slot = page["slot"]
        img.save(OUT / f"slot{slot:02d}_ko.png")
        img.resize((SCREEN_W * 3, SCREEN_H * 3), Image.NEAREST).save(
            OUT / f"slot{slot:02d}_ko_x3.png"
        )
        print(f"slot{slot:02d} {kind}")
    report = OUT / "width_issues.txt"
    report.write_text("\n".join(issues) + ("\n" if issues else "ok\n"), encoding="utf-8")
    print("issues", len(issues), "wrote", OUT)
    for line in issues:
        print(" ", line)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
