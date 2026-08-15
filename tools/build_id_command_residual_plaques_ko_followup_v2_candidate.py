#!/usr/bin/env python3
"""Build residual ID-command plaque follow-up v2 with symmetric clean text margins/caps."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_id_command_plaques_ko_candidate as base  # noqa: E402

PARENT = ROOT / "out/patch/id_command_residual_plaques_ko_followup_candidate.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/id_command_residual_plaques_ko_followup_v2.json"
OUT = ROOT / "out/patch/id_command_residual_plaques_ko_followup_v2_candidate.wsc"
OUT_SAVE = ROOT / "sram/id_command_residual_plaques_ko_followup_v2_candidate.sav"
REPORT = ROOT / "out/patch/id_command_residual_plaques_ko_followup_v2_candidate_report.json"
PREVIEWS = ROOT / "out/patch/id_command_residual_plaques_ko_followup_v2_candidate_previews"

EXPECTED_PARENT_SHA256 = "2d03a635b1db344e12f39dc19cf0307c112749870065032aced6593358e507af"
EXPECTED_STOCK_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
BACKGROUND = 0xC
INK = 0xE
BRIGHT_OUTLINE = 0xF
DOWN_OUTLINE = 0xA

TARGET_SIZES = {
    0x4C4A74: 320,
    0x4C4BB4: 256,
    0x4C50F4: 320,
    0x4C53B4: 320,
    0x4CBEAA: 384,
    0x4CC32A: 320,
    0x4CE86A: 384,
    0x4CE9EA: 256,
    0x4CC52A: 384,
}

LIVE_PALETTE = {
    0x0: (0, 0, 0),
    0xA: (80, 136, 80),
    0xB: (170, 255, 187),
    0xC: (68, 255, 68),
    0xD: (0, 204, 0),
    0xE: (0, 68, 0),
    0xF: (255, 255, 255),
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    raw = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(raw), "sha256": sha(raw)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def clear_zone(pixels: list[list[int]], x0: int, x1: int, outline: int) -> None:
    if not (0 <= x0 < x1 <= len(pixels[0])):
        raise BuildError(f"bad clear zone {x0}:{x1}")
    for y in range(1, 15):
        for x in range(x0, x1):
            pixels[y][x] = BACKGROUND
    for y in (0, 15):
        for x in range(x0, x1):
            pixels[y][x] = outline


def draw_text(
    pixels: list[list[int]],
    text: str,
    font: ImageFont.FreeTypeFont,
    stroke: int,
    x: int,
    y: int,
    outline: int,
) -> dict[str, Any]:
    outer, inner = base.make_masks(text, font, stroke)
    if x < 0 or y < 0 or x + outer.width > len(pixels[0]) or y + outer.height > 16:
        raise BuildError(f"{text!r} mask {outer.size} does not fit at {x},{y}")
    op, ip = outer.load(), inner.load()
    for yy in range(outer.height):
        for xx in range(outer.width):
            if op[xx, yy]:
                pixels[y + yy][x + xx] = outline
            if ip[xx, yy]:
                pixels[y + yy][x + xx] = INK
    return {"glyph_mask": [outer.width, outer.height], "draw_origin": [x, y]}


def render(pixels: list[list[int]], scale: int = 8) -> Image.Image:
    img = Image.new("RGB", (len(pixels[0]), 16))
    dst = img.load()
    for y, row in enumerate(pixels):
        for x, v in enumerate(row):
            dst[x, y] = LIVE_PALETTE.get(v, (v * 17,) * 3)
    return img.resize((len(pixels[0]) * scale, 16 * scale), Image.Resampling.NEAREST)


def render_previews(rows: list[dict[str, Any]], out_dir: Path, font_path: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = 8
    w = 48 * scale
    image_h = 16 * scale
    label_h = 24
    cell_h = image_h + label_h
    sheet = Image.new("RGB", (w * 2, cell_h * len(rows)), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.truetype(str(font_path), 14)
    individuals: list[str] = []
    for i, row in enumerate(rows):
        before = render(row["before"], scale)
        after = render(row["after"], scale)
        y = i * cell_h
        sheet.paste(before, (0, y))
        sheet.paste(after, (w, y))
        draw.text((3, y + image_h + 2), f"before {row['label']}", font=label_font, fill="white")
        draw.text((w + 3, y + image_h + 2), f"after {row['label']}", font=label_font, fill="white")
        pair = Image.new("RGB", (before.width + after.width, image_h), (20, 20, 20))
        pair.paste(before, (0, 0))
        pair.paste(after, (before.width, 0))
        path = out_dir / f"{i + 1:02d}_{row['logical']:06X}_{row['name']}.png"
        pair.save(path)
        individuals.append(rel(path))
    sheet_path = out_dir / "all_9_before_after.png"
    sheet.save(sheet_path)
    return {"comparison_sheet": rel(sheet_path), "individual_pairs": individuals}


def merged(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def count_raw_hits(data: bytes, needle: bytes, start: int, end: int) -> list[int]:
    hits: list[int] = []
    pos = start
    while True:
        found = data.find(needle, pos, end)
        if found < 0:
            break
        hits.append(found)
        pos = found + 1
    return hits


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parent", type=Path, default=PARENT)
    ap.add_argument("--save", type=Path, default=SAVE)
    ap.add_argument("--stock", type=Path, default=STOCK)
    ap.add_argument("--spec", type=Path, default=SPEC)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--out-save", type=Path, default=OUT_SAVE)
    ap.add_argument("--report", type=Path, default=REPORT)
    ap.add_argument("--preview-dir", type=Path, default=PREVIEWS)
    args = ap.parse_args(argv)

    if args.out.stem != args.out_save.stem:
        raise BuildError("test ROM and SaveRAM stems must match")
    parent = args.parent.read_bytes()
    stock = args.stock.read_bytes()
    save = args.save.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError(f"parent drift: {sha(parent)}")
    if sha(stock) != EXPECTED_STOCK_SHA256:
        raise BuildError("stock drift")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"unexpected SaveRAM size: {len(save)}")
    stock_base = base.stock_base(parent)
    if stock_base != 0x800000:
        raise BuildError(f"unexpected stock base: {stock_base:#x}")

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if spec.get("parent_sha256", "").lower() != EXPECTED_PARENT_SHA256:
        raise BuildError("spec parent binding drift")
    rows = {row["name"]: row for row in spec["targets"]}
    expected_names = {"seal", "shield", "sure_hit", "evade", "move_down", "pursuit", "penetrate", "preemptive", "hp_recovery"}
    if set(rows) != expected_names:
        raise BuildError(f"target inventory drift: {sorted(rows)}")
    font_path = ROOT / spec["font"]
    stroke = int(spec["stroke_width"])
    if not font_path.is_file():
        raise BuildError(f"missing font: {font_path}")

    # Current Korean 성공! is the clean canonical bright result plaque. Unlike the old
    # residual builders, do not compose external caps from the Japanese stock asset.
    success = base.decode_grid(parent[stock_base + 0x4C4654 : stock_base + 0x4C4654 + 384], 6, 2)
    clean_left = [line[0:8] for line in success]
    clean_right = [line[40:48] for line in success]
    if not all(clean_left[y][6:8] == ([BRIGHT_OUTLINE, BRIGHT_OUTLINE] if y in (0, 15) else [BACKGROUND, BACKGROUND]) for y in range(16)):
        raise BuildError("current 성공! left cap is not clean")
    if not all(clean_right[y][0:2] == ([BRIGHT_OUTLINE, BRIGHT_OUTLINE] if y in (0, 15) else [BACKGROUND, BACKGROUND]) for y in range(16)):
        raise BuildError("current 성공! right cap is not clean")

    dirty_right_top = bytes(stock[0x4C46F4 : 0x4C46F4 + 32])
    dirty_right_bottom = bytes(stock[0x4C47B4 : 0x4C47B4 + 32])
    active_start = stock_base + 0x4C0000
    active_end = stock_base + 0x4D0000
    dirty_hits_before = {
        "top": [f"{x:08X}" for x in count_raw_hits(parent, dirty_right_top, active_start, active_end)],
        "bottom": [f"{x:08X}" for x in count_raw_hits(parent, dirty_right_bottom, active_start, active_end)],
    }

    shared_top_addr = stock_base + 0x4CB80A
    shared_bottom_addr = stock_base + 0x4CB8AA
    shared_top_raw = bytes(parent[shared_top_addr : shared_top_addr + 32])
    shared_bottom_raw = bytes(parent[shared_bottom_addr : shared_bottom_addr + 32])
    shared_top = base.decode_grid(shared_top_raw, 1, 1)
    shared_bottom = base.decode_grid(shared_bottom_raw, 1, 1)

    candidate = bytearray(parent)
    manifests: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    intervals: list[tuple[int, int]] = []

    def commit(name: str, logical: int, size: int, before_raw: bytes, after_raw: bytes, before_pixels: list[list[int]], after_pixels: list[list[int]], details: dict[str, Any]) -> None:
        physical = stock_base + logical
        candidate[physical : physical + size] = after_raw
        intervals.append((physical, physical + size))
        manifests.append({
            "name": name,
            "logical": f"{logical:06X}",
            "logical_range": f"{logical:06X}-{logical + size - 1:06X}",
            "before_sha256": sha(before_raw),
            "after_sha256": sha(after_raw),
            "changed": before_raw != after_raw,
            **details,
        })
        previews.append({"name": name, "logical": logical, "label": rows[name]["text"], "before": before_pixels, "after": after_pixels})

    # 40x16 stored bodies with embedded left edge and externally composed clean right cap.
    for name in ("seal", "sure_hit", "evade"):
        row = rows[name]
        logical = int(row["logical"], 16)
        physical = stock_base + logical
        before_raw = bytes(parent[physical : physical + 320])
        body = base.decode_grid(before_raw, 5, 2)
        before = [body[y] + clean_right[y] for y in range(16)]
        after = [line[:] for line in before]
        for y in range(16):
            after[y][0:8] = clean_left[y]
            after[y][40:48] = clean_right[y]
        clear_zone(after, 6, 40, BRIGHT_OUTLINE)
        font = ImageFont.truetype(str(font_path), int(row["font_size"]))
        details = draw_text(after, row["text"], font, stroke, 8, 1, BRIGHT_OUTLINE)
        for y in range(16):
            after[y][0:8] = clean_left[y]
            after[y][40:48] = clean_right[y]
        after_raw = base.encode_grid([line[:40] for line in after], 5, 2)
        commit(name, logical, 320, before_raw, after_raw, before, after, {
            "clear_zone": [6, 40],
            "canonical_left_cap": True,
            "external_canonical_right_cap": True,
            "left_inner_strip_clean": True,
            "right_inner_strip_clean": True,
            **details,
        })

    # Shield has a 32px stored body and 8px external right cap. Text uses all x=6..31.
    row = rows["shield"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    before_raw = bytes(parent[physical : physical + 256])
    body = base.decode_grid(before_raw, 4, 2)
    before = [body[y] + clean_right[y] for y in range(16)]
    after = [line[:] for line in before]
    clear_zone(after, 6, 32, BRIGHT_OUTLINE)
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    outer, _ = base.make_masks(row["text"], font, stroke)
    dx = 6 + (26 - outer.width) // 2
    dy = 1 + (14 - outer.height) // 2
    details = draw_text(after, row["text"], font, stroke, dx, dy, BRIGHT_OUTLINE)
    for y in range(16):
        after[y][0:6] = before[y][0:6]
        after[y][32:40] = clean_right[y]
    after_raw = base.encode_grid([line[:32] for line in after], 4, 2)
    commit("shield", logical, 256, before_raw, after_raw, before, after, {
        "clear_zone": [6, 32],
        "text": "방패",
        "exclamation_removed": True,
        "left_border_preserved_through_x": 5,
        "external_canonical_right_cap": True,
        **details,
    })

    # Down movement: preserve arrow, redraw only Korean body, scrub the first two pixels of the right edge.
    row = rows["move_down"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    before_raw = bytes(parent[physical : physical + 384])
    before = base.decode_grid(before_raw, 6, 2)
    move_before_pixels = [line[:] for line in before]
    after = [line[:] for line in before]
    clean_down_right = [line[40:48] for line in before]
    for y in range(16):
        clean_down_right[y][0:2] = [DOWN_OUTLINE, DOWN_OUTLINE] if y in (0, 15) else [BACKGROUND, BACKGROUND]
    clear_zone(after, 13, 42, DOWN_OUTLINE)
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    details = draw_text(after, row["text"], font, stroke, 13, 1, DOWN_OUTLINE)
    for y in range(16):
        after[y][0:13] = before[y][0:13]
        after[y][40:48] = clean_down_right[y]
    after_raw = base.encode_grid(after, 6, 2)
    commit("move_down", logical, 384, before_raw, after_raw, before, after, {
        "clear_zone": [13, 42],
        "arrow_x0_x12_preserved": True,
        "right_inner_strip_x40_x41_clean": True,
        **details,
    })

    # Pursuit sparse layout: rebuild the private edge columns with canonical caps while the shared 전격 column stays exact.
    row = rows["pursuit"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    before_raw = bytes(parent[physical : physical + 320])
    private = base.decode_grid(before_raw, 5, 2)
    before: list[list[int]] = []
    for y in range(16):
        shared = shared_top[y] if y < 8 else shared_bottom[y - 8]
        cols = [private[y][x : x + 8] for x in range(0, 40, 8)]
        before.append(cols[0] + cols[1] + cols[2] + shared + cols[3] + cols[4])
    after = [line[:] for line in before]
    clear_zone(after, 6, 42, BRIGHT_OUTLINE)
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    details = draw_text(after, row["text"], font, stroke, 8, 1, BRIGHT_OUTLINE)
    for y in range(16):
        after[y][0:8] = clean_left[y]
        after[y][40:48] = clean_right[y]
        expected_shared = shared_top[y] if y < 8 else shared_bottom[y - 8]
        if after[y][24:32] != expected_shared:
            raise BuildError("pursuit shared 전격 column mismatch after redraw")
    private_after = [line[0:24] + line[32:48] for line in after]
    after_raw = base.encode_grid(private_after, 5, 2)
    commit("pursuit", logical, 320, before_raw, after_raw, before, after, {
        "clear_zone": [6, 42],
        "canonical_left_cap": True,
        "canonical_private_right_cap": True,
        "shared_mid_column_exact": True,
        **details,
    })

    # Penetration is a full bright 48px plaque; replace both edge tiles with canonical clean caps.
    row = rows["penetrate"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    before_raw = bytes(parent[physical : physical + 384])
    before = base.decode_grid(before_raw, 6, 2)
    after = [line[:] for line in before]
    clear_zone(after, 6, 42, BRIGHT_OUTLINE)
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    details = draw_text(after, row["text"], font, stroke, 8, 1, BRIGHT_OUTLINE)
    for y in range(16):
        after[y][0:8] = clean_left[y]
        after[y][40:48] = clean_right[y]
    after_raw = base.encode_grid(after, 6, 2)
    commit("penetrate", logical, 384, before_raw, after_raw, before, after, {
        "clear_zone": [6, 42],
        "canonical_left_cap": True,
        "canonical_right_cap": True,
        **details,
    })

    # Preemptive uses external clean caps on both sides; only its exact 32px text body is stored.
    row = rows["preemptive"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    before_raw = bytes(parent[physical : physical + 256])
    body = base.decode_grid(before_raw, 4, 2)
    before = [clean_left[y] + body[y] + clean_right[y] for y in range(16)]
    after = [line[:] for line in before]
    clear_zone(after, 8, 40, BRIGHT_OUTLINE)
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    outer, _ = base.make_masks(row["text"], font, stroke)
    dx = 8 + (32 - outer.width) // 2
    dy = 1 + (14 - outer.height) // 2
    details = draw_text(after, row["text"], font, stroke, dx, dy, BRIGHT_OUTLINE)
    for y in range(16):
        after[y][0:8] = clean_left[y]
        after[y][40:48] = clean_right[y]
    after_raw = base.encode_grid([line[8:40] for line in after], 4, 2)
    commit("preemptive", logical, 256, before_raw, after_raw, before, after, {
        "clear_zone": [8, 40],
        "external_canonical_left_cap": True,
        "external_canonical_right_cap": True,
        **details,
    })

    # HP recovery: preserve the original HP section exactly, redraw only 회복, use clean current Korean right cap.
    row = rows["hp_recovery"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    before_raw = bytes(parent[physical : physical + 384])
    before = base.decode_grid(before_raw, 6, 2)
    stock_pixels = base.decode_grid(stock[logical : logical + 384], 6, 2)
    after = [line[:] for line in stock_pixels]
    clear_zone(after, 14, 42, BRIGHT_OUTLINE)
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    details = draw_text(after, row["text"], font, stroke, 14, 1, BRIGHT_OUTLINE)
    for y in range(16):
        after[y][0:14] = stock_pixels[y][0:14]
        after[y][40:48] = clean_right[y]
    after_raw = base.encode_grid(after, 6, 2)
    commit("hp_recovery", logical, 384, before_raw, after_raw, before, after, {
        "clear_zone": [14, 42],
        "stock_hp_x0_x13_exact": True,
        "canonical_right_cap": True,
        **details,
    })

    checksum = base.update_ws_checksum(candidate)
    result = bytes(candidate)
    allowed = merged(intervals + [(len(result) - 2, len(result))])
    runs = base.diff_runs(parent, result)
    unexpected = [(s, e) for s, e in runs if not any(lo <= s and e <= hi for lo, hi in allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff: {unexpected}")
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum invalid")

    dirty_hits_after = {
        "top": [f"{x:08X}" for x in count_raw_hits(result, dirty_right_top, active_start, active_end)],
        "bottom": [f"{x:08X}" for x in count_raw_hits(result, dirty_right_bottom, active_start, active_end)],
    }
    if dirty_hits_after["top"] or dirty_hits_after["bottom"]:
        raise BuildError(f"contaminated stock success right-cap copies remain active: {dirty_hits_after}")

    preview_info = render_previews(previews, args.preview_dir, font_path)
    atomic_bytes(args.out, result)
    args.out_save.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.save, args.out_save)
    reread = args.out.read_bytes()
    paired_save = args.out_save.read_bytes()

    success_before = parent[stock_base + 0x4C4654 : stock_base + 0x4C4654 + 384]
    success_after = reread[stock_base + 0x4C4654 : stock_base + 0x4C4654 + 384]
    checks = {
        "parent_hash_bound": sha(parent) == EXPECTED_PARENT_SHA256,
        "stock_hash_bound": sha(stock) == EXPECTED_STOCK_SHA256,
        "latest_live_saveram_size_valid": len(save) == SAVE_SIZE,
        "success_canonical_caps_unchanged": success_after == success_before,
        "active_dirty_stock_right_cap_top_copies_removed": not dirty_hits_after["top"],
        "active_dirty_stock_right_cap_bottom_copies_removed": not dirty_hits_after["bottom"],
        "pursuit_shared_alias_unchanged": (
            reread[shared_top_addr : shared_top_addr + 32] == shared_top_raw
            and reread[shared_bottom_addr : shared_bottom_addr + 32] == shared_bottom_raw
        ),
        "hp_stock_x0_x13_exact": all(
            base.decode_grid(reread[stock_base + 0x4CC52A : stock_base + 0x4CC52A + 384], 6, 2)[y][0:14] == stock_pixels[y][0:14]
            for y in range(16)
        ),
        "move_arrow_x0_x12_exact": all(
            base.decode_grid(reread[stock_base + 0x4CBEAA : stock_base + 0x4CBEAA + 384], 6, 2)[y][0:13] == move_before_pixels[y][0:13]
            for y in range(16)
        ),
        "diff_allowlist_clean": not unexpected,
        "candidate_reread_exact": reread == result,
        "checksum_valid": (sum(reread[:-2]) & 0xFFFF) == int.from_bytes(reread[-2:], "little"),
        "paired_saveram_latest_live_exact": paired_save == save,
        "parent_unchanged": args.parent.read_bytes() == parent,
        "live_saveram_unchanged": args.save.read_bytes() == save,
    }
    if not all(checks.values()):
        raise BuildError(f"post-build checks failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_id_command_residual_plaques_ko_followup_v2_candidate.py",
        "ok": True,
        "status": "followup_v2_static_verified_pending_user_runtime_test",
        "scope": "all eight residual plaque previews plus HP recovery: symmetric clean Korean margins and removal of contaminated stock right-cap aliases",
        "parent": identity(args.parent, parent),
        "stock": identity(args.stock, stock),
        "live_saveram": identity(args.save, save),
        "spec": identity(args.spec),
        "font": identity(font_path),
        "candidate": {**identity(args.out, reread), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(args.out_save, paired_save),
        "dirty_stock_success_right_cap_aliases": {"before": dirty_hits_before, "after": dirty_hits_after},
        "targets": manifests,
        "counts": {
            "targets": len(manifests),
            "changed_targets": sum(bool(m["changed"]) for m in manifests),
            "changed_bytes_including_checksum": sum(e - s for s, e in runs),
            "diff_runs_including_checksum": len(runs),
        },
        "diff": {
            "allowlist": [{"start": f"{s:08X}", "end_exclusive": f"{e:08X}"} for s, e in allowed],
            "runs": [{"start": f"{s:08X}", "end_exclusive": f"{e:08X}"} for s, e in runs],
            "unexpected": [{"start": f"{s:08X}", "end_exclusive": f"{e:08X}"} for s, e in unexpected],
        },
        "previews": preview_info,
        "checks": checks,
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(args.report, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
