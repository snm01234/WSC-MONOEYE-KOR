#!/usr/bin/env python3
"""Build the visual follow-up for residual ID-command plaques from the validated residual candidate."""

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

PARENT = ROOT / "out/patch/id_command_residual_plaques_ko_candidate.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/id_command_residual_plaques_ko_followup.json"
OUT = ROOT / "out/patch/id_command_residual_plaques_ko_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/id_command_residual_plaques_ko_followup_candidate.sav"
REPORT = ROOT / "out/patch/id_command_residual_plaques_ko_followup_candidate_report.json"
PREVIEWS = ROOT / "out/patch/id_command_residual_plaques_ko_followup_candidate_previews"

EXPECTED_PARENT_SHA256 = "3ffbb11f18643ad029dcd869bd26f0b38b6ee2e1274ac489e12f3f4d4e553029"
EXPECTED_STOCK_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXPECTED_PARENT_RAW = {
    0x4C4A74: (320, "360569c3aa869a239b65b5a82f292266ade582c2842aec857ea2cd55ba0fca5a"),
    0x4C4BB4: (256, "e07e0166b077d69514e42cea3b021bb7147445ba9b3eba596914101ed2afb9ec"),
    0x4CC32A: (320, "f89f0e92d41dc57a8eb002b3de8441938ccd836c957ba0949149a230b91a3017"),
    0x4CC52A: (384, "1e82ad40b994ded5fddad367f252937506bc3e26dfd9e835f51a0af7ac13cea1"),
}
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
BACKGROUND = 0xC
INK = 0xE
OUTLINE = 0xF
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


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha256(payload)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def clear_interior(pixels: list[list[int]], x0: int, x1: int) -> None:
    width = len(pixels[0])
    if not (0 <= x0 < x1 <= width):
        raise BuildError(f"invalid clear zone {x0}:{x1} for width {width}")
    for y in range(1, 15):
        for x in range(x0, x1):
            pixels[y][x] = BACKGROUND
    for y in (0, 15):
        for x in range(x0, x1):
            pixels[y][x] = OUTLINE


def draw_text_at(
    pixels: list[list[int]],
    text: str,
    font: ImageFont.FreeTypeFont,
    stroke: int,
    x: int,
    y: int,
) -> dict[str, Any]:
    outer, inner = base.make_masks(text, font, stroke)
    if x < 0 or y < 0 or x + outer.width > len(pixels[0]) or y + outer.height > len(pixels):
        raise BuildError(f"text {text!r} does not fit at {x},{y}: {outer.size}")
    outer_px, inner_px = outer.load(), inner.load()
    for yy in range(outer.height):
        for xx in range(outer.width):
            if outer_px[xx, yy]:
                pixels[y + yy][x + xx] = OUTLINE
            if inner_px[xx, yy]:
                pixels[y + yy][x + xx] = INK
    return {"glyph_mask": [outer.width, outer.height], "draw_origin": [x, y]}


def render(pixels: list[list[int]], scale: int = 8) -> Image.Image:
    width = len(pixels[0])
    image = Image.new("RGB", (width, 16))
    dst = image.load()
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            dst[x, y] = LIVE_PALETTE.get(value, (value * 17,) * 3)
    return image.resize((width * scale, 16 * scale), Image.Resampling.NEAREST)


def render_previews(rows: list[dict[str, Any]], out_dir: Path, font_path: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = 8
    max_w = 48 * scale
    image_h = 16 * scale
    label_h = 24
    cell_h = image_h + label_h
    sheet = Image.new("RGB", (max_w * 2, cell_h * len(rows)), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.truetype(str(font_path), 14)
    individuals: list[str] = []
    for index, row in enumerate(rows):
        before = render(row["before_pixels"], scale)
        after = render(row["after_pixels"], scale)
        y = index * cell_h
        sheet.paste(before, (0, y))
        sheet.paste(after, (max_w, y))
        draw.text((3, y + image_h + 2), f"before {row['label']}", font=label_font, fill="white")
        draw.text((max_w + 3, y + image_h + 2), f"after {row['label']}", font=label_font, fill="white")
        pair = Image.new("RGB", (before.width + after.width, image_h), (20, 20, 20))
        pair.paste(before, (0, 0))
        pair.paste(after, (before.width, 0))
        pair_path = out_dir / f"{index + 1:02d}_{row['logical']:06X}_{row['name']}.png"
        pair.save(pair_path)
        individuals.append(rel(pair_path))
    sheet_path = out_dir / "all_4_before_after.png"
    sheet.save(sheet_path)
    return {"comparison_sheet": rel(sheet_path), "individual_pairs": individuals}


def merged(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=PARENT)
    parser.add_argument("--save", type=Path, default=SAVE)
    parser.add_argument("--stock", type=Path, default=STOCK)
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--out-save", type=Path, default=OUT_SAVE)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--preview-dir", type=Path, default=PREVIEWS)
    args = parser.parse_args(argv)

    if args.out.stem != args.out_save.stem:
        raise BuildError("test ROM and SaveRAM stems must match")
    parent = args.parent.read_bytes()
    save = args.save.read_bytes()
    stock = args.stock.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError(f"follow-up parent drift: {sha256(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"unexpected live SaveRAM size: {len(save)}")
    if sha256(stock) != EXPECTED_STOCK_SHA256:
        raise BuildError("stock ROM drift")
    stock_base = base.stock_base(parent)
    if stock_base != 0x800000:
        raise BuildError(f"unexpected stock base: {stock_base:#x}")

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if spec.get("parent_sha256", "").lower() != EXPECTED_PARENT_SHA256:
        raise BuildError("spec parent binding drift")
    rows = {row["name"]: row for row in spec.get("targets", [])}
    if set(rows) != {"seal", "pursuit", "shield", "hp_recovery"}:
        raise BuildError(f"follow-up target inventory drift: {sorted(rows)}")
    font_path = ROOT / spec["font"]
    if not font_path.is_file():
        raise BuildError(f"missing font: {font_path}")
    stroke = int(spec["stroke_width"])

    for logical, (size, expected_hash) in EXPECTED_PARENT_RAW.items():
        raw = parent[stock_base + logical : stock_base + logical + size]
        if sha256(raw) != expected_hash:
            raise BuildError(f"parent target drift at {logical:06X}: {sha256(raw)}")

    success = base.decode_grid(stock[0x4C4654 : 0x4C4654 + 384], 6, 2)
    common_right_edge = [line[40:48] for line in success]
    shared_top_logical = 0x4CB80A
    shared_bottom_logical = 0x4CB8AA
    shared_top_raw = bytes(parent[stock_base + shared_top_logical : stock_base + shared_top_logical + 32])
    shared_bottom_raw = bytes(parent[stock_base + shared_bottom_logical : stock_base + shared_bottom_logical + 32])
    shared_top = base.decode_grid(shared_top_raw, 1, 1)
    shared_bottom = base.decode_grid(shared_bottom_raw, 1, 1)

    candidate = bytearray(parent)
    target_intervals: list[tuple[int, int]] = []
    manifest: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []

    # 봉인!: preserve cap/outline structure, but clear two more interior pixels on the left.
    row = rows["seal"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    raw = bytes(parent[physical : physical + 320])
    body = base.decode_grid(raw, 5, 2)
    before = [body[y] + common_right_edge[y] for y in range(16)]
    after = [line[:] for line in before]
    x0, x1 = map(int, row["clear_zone"])
    clear_interior(after, x0, x1)
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    details = draw_text_at(after, row["text"], font, stroke, *map(int, row["draw_origin"]))
    if not all(after[y][:6] == before[y][:6] and after[y][40:] == before[y][40:] for y in range(16)):
        raise BuildError("봉인! rounded caps changed")
    if not all(after[y][6:8] == [BACKGROUND, BACKGROUND] for y in range(1, 15)):
        raise BuildError("봉인! widened left clear zone is not clean")
    target_raw = base.encode_grid([line[:40] for line in after], 5, 2)
    candidate[physical : physical + 320] = target_raw
    target_intervals.append((physical, physical + 320))
    manifest.append({
        "name": "seal", "logical": f"{logical:06X}", "range": f"{logical:06X}-{logical+319:06X}",
        "before_sha256": sha256(raw), "after_sha256": sha256(target_raw), "clear_zone": [x0, x1],
        "left_cap_preserved_through_x": 5, "shared_right_cap_preserved_from_x": 40,
        "left_residual_strip_x6_x7_clean": True, **details,
    })
    previews.append({"name": "seal", "logical": logical, "label": "봉인!", "before_pixels": before, "after_pixels": after})

    # 추격!: same Korean draw origin so the shared 전격 column remains pixel-exact; clear x=6..7 only additionally.
    row = rows["pursuit"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    raw = bytes(parent[physical : physical + 320])
    private = base.decode_grid(raw, 5, 2)
    before = []
    for y in range(16):
        shared = shared_top[y] if y < 8 else shared_bottom[y - 8]
        cols = [private[y][x : x + 8] for x in range(0, 40, 8)]
        before.append(cols[0] + cols[1] + cols[2] + shared + cols[3] + cols[4])
    after = [line[:] for line in before]
    x0, x1 = map(int, row["clear_zone"])
    clear_interior(after, x0, x1)
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    details = draw_text_at(after, row["text"], font, stroke, *map(int, row["draw_origin"]))
    for y in range(16):
        expected_shared = shared_top[y] if y < 8 else shared_bottom[y - 8]
        if after[y][24:32] != expected_shared:
            raise BuildError("추격! shared x=24..31 no longer matches the localized 전격 alias")
    if not all(after[y][:6] == before[y][:6] and after[y][40:] == before[y][40:] for y in range(16)):
        raise BuildError("추격! outer rounded columns changed")
    if not all(after[y][6:8] == [BACKGROUND, BACKGROUND] for y in range(1, 15)):
        raise BuildError("추격! widened left clear zone is not clean")
    private_after = [line[0:24] + line[32:48] for line in after]
    target_raw = base.encode_grid(private_after, 5, 2)
    candidate[physical : physical + 320] = target_raw
    target_intervals.append((physical, physical + 320))
    manifest.append({
        "name": "pursuit", "logical": f"{logical:06X}", "range": f"{logical:06X}-{logical+319:06X}",
        "before_sha256": sha256(raw), "after_sha256": sha256(target_raw), "clear_zone": [x0, x1],
        "left_cap_preserved_through_x": 5, "right_edge_preserved_from_x": 40,
        "left_residual_strip_x6_x7_clean": True, "shared_mid_column_pixel_exact": True,
        "shared_top_logical": f"{shared_top_logical:06X}", "shared_bottom_logical": f"{shared_bottom_logical:06X}", **details,
    })
    previews.append({"name": "pursuit", "logical": logical, "label": "추격!", "before_pixels": before, "after_pixels": after})

    # 방패: remove the punctuation and use the same bold 12 px treatment as 공격/방어/이동.
    row = rows["shield"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    raw = bytes(parent[physical : physical + 256])
    body = base.decode_grid(raw, 4, 2)
    before = [body[y] + common_right_edge[y] for y in range(16)]
    after = [line[:] for line in before]
    x0, x1 = map(int, row["clear_zone"])
    clear_interior(after, x0, x1)
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    outer, _ = base.make_masks(row["text"], font, stroke)
    draw_x = x0 + ((x1 - x0) - outer.width) // 2
    draw_y = 1 + (14 - outer.height) // 2
    details = draw_text_at(after, row["text"], font, stroke, draw_x, draw_y)
    if row["text"] != "방패" or "!" in row["text"]:
        raise BuildError("방패 target unexpectedly contains punctuation")
    if not all(after[y][:6] == before[y][:6] and after[y][32:] == before[y][32:] for y in range(16)):
        raise BuildError("방패 rounded cap regions changed")
    target_raw = base.encode_grid([line[:32] for line in after], 4, 2)
    candidate[physical : physical + 256] = target_raw
    target_intervals.append((physical, physical + 256))
    manifest.append({
        "name": "shield", "logical": f"{logical:06X}", "range": f"{logical:06X}-{logical+255:06X}",
        "before_sha256": sha256(raw), "after_sha256": sha256(target_raw), "clear_zone": [x0, x1],
        "text": row["text"], "font_size": int(row["font_size"]), "exclamation_removed": True,
        "left_cap_preserved_through_x": 5, "shared_right_cap_preserved_from_x": 32, **details,
    })
    previews.append({"name": "shield", "logical": logical, "label": "방패", "before_pixels": before, "after_pixels": after})

    # HP회복: rebuild from stock so HP itself is byte/pixel-exact to the Japanese original.
    row = rows["hp_recovery"]
    logical = int(row["logical"], 16)
    physical = stock_base + logical
    raw_parent = bytes(parent[physical : physical + 384])
    raw_stock = bytes(stock[logical : logical + 384])
    before = base.decode_grid(raw_parent, 6, 2)
    stock_pixels = base.decode_grid(raw_stock, 6, 2)
    after = [line[:] for line in stock_pixels]
    x0, x1 = map(int, row["clear_zone"])
    clear_interior(after, x0, x1)
    for y in range(16):
        after[y][40:48] = common_right_edge[y]
    font = ImageFont.truetype(str(font_path), int(row["font_size"]))
    details = draw_text_at(after, row["text"], font, stroke, *map(int, row["draw_origin"]))
    if not all(after[y][:14] == stock_pixels[y][:14] for y in range(16)):
        raise BuildError("HP stock region x=0..13 changed")
    if not all(after[y][40:48] == common_right_edge[y] for y in range(16)):
        raise BuildError("HP회복 clean right cap reconstruction failed")
    target_raw = base.encode_grid(after, 6, 2)
    candidate[physical : physical + 384] = target_raw
    target_intervals.append((physical, physical + 384))
    manifest.append({
        "name": "hp_recovery", "logical": f"{logical:06X}", "range": f"{logical:06X}-{logical+383:06X}",
        "before_sha256": sha256(raw_parent), "stock_sha256": sha256(raw_stock), "after_sha256": sha256(target_raw),
        "clear_zone": [x0, x1], "rendered_text": row["text"], "font_size": int(row["font_size"]),
        "stock_hp_region_x0_x13_exact": True, "common_right_cap_restored_x40_x47": True, **details,
    })
    previews.append({"name": "hp_recovery", "logical": logical, "label": "HP + 회복", "before_pixels": before, "after_pixels": after})

    checksum = base.update_ws_checksum(candidate)
    result = bytes(candidate)
    allowed = merged(target_intervals + [(len(result) - 2, len(result))])
    runs = base.diff_runs(parent, result)
    unexpected = [
        (start, end) for start, end in runs
        if not any(lo <= start and end <= hi for lo, hi in allowed)
    ]
    if unexpected:
        raise BuildError(f"diff outside follow-up allowlist: {unexpected}")
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum invalid")

    preview_info = render_previews(previews, args.preview_dir, font_path)
    atomic_bytes(args.out, result)
    args.out_save.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.save, args.out_save)
    reread = args.out.read_bytes()
    paired_save = args.out_save.read_bytes()

    checks = {
        "parent_hash_bound": sha256(parent) == EXPECTED_PARENT_SHA256,
        "stock_hash_bound": sha256(stock) == EXPECTED_STOCK_SHA256,
        "latest_live_saveram_size_valid": len(save) == SAVE_SIZE,
        "target_count_4": len(manifest) == 4,
        "seal_left_residual_strip_clean": next(m for m in manifest if m["name"] == "seal")["left_residual_strip_x6_x7_clean"],
        "pursuit_left_residual_strip_clean": next(m for m in manifest if m["name"] == "pursuit")["left_residual_strip_x6_x7_clean"],
        "pursuit_shared_alias_unchanged": (
            reread[stock_base + shared_top_logical : stock_base + shared_top_logical + 32] == shared_top_raw
            and reread[stock_base + shared_bottom_logical : stock_base + shared_bottom_logical + 32] == shared_bottom_raw
        ),
        "shield_exclamation_removed": next(m for m in manifest if m["name"] == "shield")["exclamation_removed"],
        "hp_stock_region_exact": next(m for m in manifest if m["name"] == "hp_recovery")["stock_hp_region_x0_x13_exact"],
        "diff_allowlist_clean": not unexpected,
        "candidate_reread_exact": reread == result,
        "candidate_checksum_valid": (sum(reread[:-2]) & 0xFFFF) == int.from_bytes(reread[-2:], "little"),
        "paired_saveram_is_latest_live_copy": paired_save == save,
        "parent_unchanged_on_disk": args.parent.read_bytes() == parent,
        "live_saveram_unchanged_on_disk": args.save.read_bytes() == save,
    }
    if not all(checks.values()):
        raise BuildError(f"post-build checks failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_id_command_residual_plaques_ko_followup_candidate.py",
        "ok": True,
        "status": "followup_candidate_static_verified_pending_user_runtime_test",
        "scope": "visual follow-up for 봉인!/추격! left residual removal, 방패 punctuation/font cleanup, and stock-structured HP+회복 reconstruction",
        "parent": identity(args.parent, parent),
        "stock": identity(args.stock, stock),
        "live_saveram": identity(args.save, save),
        "spec": identity(args.spec),
        "font": identity(font_path),
        "candidate": {**identity(args.out, reread), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(args.out_save, paired_save),
        "targets": manifest,
        "diff": {
            "allowlist": [{"start": f"{s:08X}", "end_exclusive": f"{e:08X}"} for s, e in allowed],
            "runs": [{"start": f"{s:08X}", "end_exclusive": f"{e:08X}"} for s, e in runs],
            "unexpected": unexpected,
            "changed_bytes_including_checksum": sum(e - s for s, e in runs),
            "run_count_including_checksum": len(runs),
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
