#!/usr/bin/env python3
"""Build a paired test ROM/SaveRAM with all 24 ID-command plaques in Korean."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402


DEFAULT_PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
DEFAULT_SPEC = ROOT / "data/id_command_plaque_translations_ko.json"
DEFAULT_OUT = ROOT / "out/patch/id_command_plaques_ko_candidate.wsc"
DEFAULT_OUT_SAVE = ROOT / "sram/id_command_plaques_ko_candidate.sav"
DEFAULT_REPORT = ROOT / "out/patch/id_command_plaques_ko_candidate_report.json"
DEFAULT_PREVIEW = ROOT / "out/patch/id_command_plaques_ko_candidate_previews"
EXPECTED_PARENT_SHA256 = "87bd754d3f4af65f3d02a274d94e962e0bf2f0313c491096407dfc9c8d1a4f93"
EXPECTED_SAVE_SHA256 = "589f47d18cbe245e544f62a92542eedaed87895794aaf072b3071d7442cde4a4"
ROM_SIZE = 16 * 1024 * 1024
SAVE_SIZE = 32 * 1024
TILE_BYTES = 0x20
FULL_BYTES = 12 * TILE_BYTES
BODY_BYTES = 10 * TILE_BYTES
SHARED_CAP_TOP = 0x4C44D4
SHARED_CAP_BOTTOM = 0x4C4594
EXPECTED_LOGICALS = {
    0x4C44D4, 0x4C4654, 0x4C48F4, 0x4C5234, 0x4C54F4, 0x4C5634,
    0x4C57B4, 0x4C5914, 0x4C5A54, 0x4C5BD4, 0x4C5D54, 0x4C5E94,
    0x4CB38A, 0x4CB62A, 0x4CB7AA, 0x4CB8EA, 0x4CBA2A, 0x4CBBAA,
    0x4CBD2A, 0x4CC02A, 0x4CC1AA, 0x4CC52A, 0x4CE56A, 0x4CE6EA,
}
LIVE_PALETTE = {
    0x0: (0, 0, 0),
    0xA: (136, 136, 136),
    0xB: (170, 255, 187),
    0xC: (68, 255, 68),
    0xD: (0, 204, 0),
    0xE: (0, 68, 0),
    0xF: (255, 255, 255),
}


class BuildError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(temporary, target)


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    raw = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def decode_tiles(raw: bytes, count: int) -> list[list[list[int]]]:
    if len(raw) != count * TILE_BYTES:
        raise BuildError(f"wrong packed-4bpp byte count: {len(raw)} != {count * TILE_BYTES}")
    tiles: list[list[list[int]]] = []
    for tile_index in range(count):
        block = raw[tile_index * TILE_BYTES : (tile_index + 1) * TILE_BYTES]
        tile: list[list[int]] = []
        for y in range(8):
            row: list[int] = []
            for pair in range(4):
                value = block[y * 4 + pair]
                row.extend((value >> 4, value & 0xF))
            tile.append(row)
        tiles.append(tile)
    return tiles


def decode_grid(raw: bytes, cols: int, rows: int) -> list[list[int]]:
    tiles = decode_tiles(raw, cols * rows)
    pixels = [[0] * (cols * 8) for _ in range(rows * 8)]
    for index, tile in enumerate(tiles):
        ox = (index % cols) * 8
        oy = (index // cols) * 8
        for y in range(8):
            pixels[oy + y][ox : ox + 8] = tile[y]
    return pixels


def encode_grid(pixels: list[list[int]], cols: int, rows: int) -> bytes:
    if len(pixels) != rows * 8 or any(len(row) != cols * 8 for row in pixels):
        raise BuildError("pixel grid geometry mismatch")
    out = bytearray()
    for tile_row in range(rows):
        for tile_col in range(cols):
            for y in range(8):
                source = pixels[tile_row * 8 + y]
                for x in range(0, 8, 2):
                    left = source[tile_col * 8 + x]
                    right = source[tile_col * 8 + x + 1]
                    if not (0 <= left <= 0xF and 0 <= right <= 0xF):
                        raise BuildError("palette index outside 4bpp range")
                    out.append((left << 4) | right)
    return bytes(out)


def compose_body(parent: bytes, base: int, raw: bytes) -> list[list[int]]:
    body = decode_grid(raw, 5, 2)
    top = decode_grid(parent[base + SHARED_CAP_TOP : base + SHARED_CAP_TOP + TILE_BYTES], 1, 1)[0:8]
    bottom = decode_grid(parent[base + SHARED_CAP_BOTTOM : base + SHARED_CAP_BOTTOM + TILE_BYTES], 1, 1)[0:8]
    cap = top + bottom
    return [cap[y] + body[y] for y in range(16)]


def make_masks(
    text: str,
    font: ImageFont.FreeTypeFont,
    stroke_width: int,
    letter_spacing: int = 0,
) -> tuple[Image.Image, Image.Image]:
    if letter_spacing < 0:
        raise BuildError("letter spacing must be non-negative")
    if letter_spacing and len(text) > 1:
        parts = [make_masks(ch, font, stroke_width, 0) for ch in text]
        advances = [
            max(1, outer.width - 2 * stroke_width) + letter_spacing
            for outer, _ in parts[:-1]
        ]
        width = sum(advances) + parts[-1][0].width
        height = max(outer.height for outer, _ in parts)
        outer_line = Image.new("L", (width, height), 0)
        inner_line = Image.new("L", (width, height), 0)
        x = 0
        for index, (outer_part, inner_part) in enumerate(parts):
            y = (height - outer_part.height) // 2
            outer_line.paste(outer_part, (x, y), outer_part)
            inner_line.paste(inner_part, (x, y), inner_part)
            if index < len(advances):
                x += advances[index]
        return outer_line, inner_line
    left, top, right, bottom = font.getbbox(text, stroke_width=stroke_width)
    width, height = right - left, bottom - top
    outer = Image.new("L", (width, height), 0)
    inner = Image.new("L", (width, height), 0)
    ImageDraw.Draw(outer).text(
        (-left, -top), text, font=font, fill=255, stroke_width=stroke_width, stroke_fill=255
    )
    inner_left, inner_top, inner_right, inner_bottom = font.getbbox(text)
    ImageDraw.Draw(inner).text(
        (-left, -top), text, font=font, fill=255
    )
    if inner_right <= inner_left or inner_bottom <= inner_top:
        raise BuildError(f"empty glyph mask for {text!r}")
    outer = outer.point(lambda value: 255 if value >= 128 else 0)
    inner = inner.point(lambda value: 255 if value >= 128 else 0)
    return outer, inner


def localize_pixels(
    source: list[list[int]],
    row: dict[str, Any],
    font_paths: dict[str, Path],
    font_sizes: dict[str, int],
    stroke_width: int,
) -> tuple[list[list[int]], dict[str, Any]]:
    pixels = [line[:] for line in source]
    layout = row["layout"]
    if "zone" in row:
        zone = tuple(int(value) for value in row["zone"])
        if len(zone) != 2:
            raise BuildError(f"zone must have two coordinates: {row['zone']!r}")
    elif layout == "directional":
        # The first Japanese glyph begins at x=14 and overlaps the arrow's
        # former visual budget.  The arrow itself ends at x=13.  Clearing from
        # x=14 removes every residual stroke and centring in this wider zone
        # moves the Korean pair four pixels left.
        zone = (14, 44)
    else:
        zone = (5, 43)
    x0, x1 = zone
    for y in range(1, 15):
        for x in range(x0, x1):
            pixels[y][x] = 0xC
    outline = 0xA if row["tone"] == "down" else 0xF
    for y in (0, 15):
        for x in range(max(x0, 6), min(x1, 42)):
            pixels[y][x] = outline

    font_role = row.get("font_role", "default")
    if font_role not in font_paths:
        raise BuildError(f"unknown font role {font_role!r} for {row['ko']}")
    font_size = int(row.get("font_size", font_sizes[layout]))
    letter_spacing = int(row.get("letter_spacing", 0))
    font = ImageFont.truetype(str(font_paths[font_role]), size=font_size)
    outer, inner = make_masks(row["text"], font, stroke_width, letter_spacing)
    if outer.width > x1 - x0 or outer.height > 14:
        raise BuildError(
            f"{row['ko']} does not fit {layout}: mask={outer.width}x{outer.height}, "
            f"zone={x1 - x0}x14"
        )
    draw_x = x0 + ((x1 - x0) - outer.width) // 2
    draw_y = 1 + (14 - outer.height) // 2
    outer_px, inner_px = outer.load(), inner.load()
    for y in range(outer.height):
        for x in range(outer.width):
            if outer_px[x, y]:
                pixels[draw_y + y][draw_x + x] = outline
            if inner_px[x, y]:
                pixels[draw_y + y][draw_x + x] = 0xE

    changed = [(x, y) for y in range(16) for x in range(48) if pixels[y][x] != source[y][x]]
    if not changed:
        raise BuildError(f"plaque did not change: {row['ko']}")
    changed_bbox = [
        min(x for x, _ in changed), min(y for _, y in changed),
        max(x for x, _ in changed) + 1, max(y for _, y in changed) + 1,
    ]
    return pixels, {
        "zone": [x0, 1, x1, 15],
        "font_role": font_role,
        "font_size": font_size,
        "letter_spacing": letter_spacing,
        "glyph_mask": {"width": outer.width, "height": outer.height},
        "draw_origin": [draw_x, draw_y],
        "changed_pixel_count": len(changed),
        "changed_pixel_bbox": changed_bbox,
        "outline_index": f"{outline:X}",
        "ink_index": "E",
    }


def render_plaque(pixels: list[list[int]], scale: int = 6) -> Image.Image:
    image = Image.new("RGB", (48, 16))
    target = image.load()
    for y, row in enumerate(pixels):
        for x, value in enumerate(row):
            target[x, y] = LIVE_PALETTE.get(value, (value * 17,) * 3)
    return image.resize((48 * scale, 16 * scale), Image.Resampling.NEAREST)


def render_previews(rows: list[dict[str, Any]], out_dir: Path, font_path: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = 6
    label_font = ImageFont.truetype(str(font_path), 14)
    cell_w = 48 * scale
    cell_h = 16 * scale + 24
    sheet = Image.new("RGB", (cell_w * 2, cell_h * len(rows)), (22, 22, 22))
    draw = ImageDraw.Draw(sheet)
    individual: list[str] = []
    for index, row in enumerate(rows):
        before = render_plaque(row["before_pixels"], scale)
        after = render_plaque(row["after_pixels"], scale)
        y = index * cell_h
        sheet.paste(before, (0, y))
        sheet.paste(after, (cell_w, y))
        draw.text((4, y + 16 * scale + 2), f"{row['jp']}  {row['logical']:06X}", font=label_font, fill="white")
        draw.text((cell_w + 4, y + 16 * scale + 2), row["ko"], font=label_font, fill="white")
        pair = Image.new("RGB", (cell_w * 2, 16 * scale), (22, 22, 22))
        pair.paste(before, (0, 0))
        pair.paste(after, (cell_w, 0))
        path = out_dir / f"{index + 1:02d}_{row['logical']:06X}.png"
        pair.save(path)
        individual.append(rel(path))
    sheet_path = out_dir / "all_24_before_after.png"
    sheet.save(sheet_path)
    return {"comparison_sheet": rel(sheet_path), "individual_pairs": individual}


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def in_allowlist(start: int, end: int, allowed: list[tuple[int, int]]) -> bool:
    return any(lo <= start and end <= hi for lo, hi in allowed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--stock", type=Path, default=DEFAULT_STOCK)
    parser.add_argument("--save", type=Path, default=DEFAULT_SAVE)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--out-save", type=Path, default=DEFAULT_OUT_SAVE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--preview-dir", type=Path, default=DEFAULT_PREVIEW)
    parser.add_argument("--allow-parent-drift", action="store_true")
    args = parser.parse_args(argv)

    if args.out.stem != args.out_save.stem:
        raise BuildError("test ROM and SaveRAM stems must match")
    parent = args.parent.read_bytes()
    stock = args.stock.read_bytes()
    save = args.save.read_bytes()
    if len(parent) != ROM_SIZE or len(save) != SAVE_SIZE:
        raise BuildError("unexpected parent ROM or SaveRAM size")
    parent_sha = sha256(parent)
    save_sha = sha256(save)
    if not args.allow_parent_drift and parent_sha != EXPECTED_PARENT_SHA256:
        raise BuildError(f"parent SHA-256 drift: {parent_sha}")
    if not args.allow_parent_drift and save_sha != EXPECTED_SAVE_SHA256:
        raise BuildError(f"live SaveRAM SHA-256 drift: {save_sha}")
    base = stock_base(parent)
    if base != 0x800000:
        raise BuildError(f"unexpected stock base for 16 MiB parent: {base:#x}")

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    rows = spec["plaques"]
    logicals = {int(row["logical"], 16) for row in rows}
    if len(rows) != 24 or logicals != EXPECTED_LOGICALS:
        raise BuildError("translation spec must contain the exact 24-plaque inventory")
    font_paths = {
        "default": ROOT / spec["font"]["path"],
        "readability": ROOT / spec["font"]["readability_path"],
    }
    for font_role, font_path in font_paths.items():
        if not font_path.is_file():
            raise BuildError(f"missing {font_role} font: {font_path}")
    font_sizes = {
        "directional": int(spec["font"]["directional_size"]),
        "result": int(spec["font"]["result_size"]),
        "three_hangul": int(spec["font"]["three_hangul_size"]),
        "mixed_ascii": int(spec["font"]["mixed_ascii_size"]),
    }
    stroke_width = int(spec["font"]["stroke_width"])

    candidate = bytearray(parent)
    preview_rows: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []
    localized_count = 0
    preserved_count = 0
    for row in rows:
        logical = int(row["logical"], 16)
        storage = row["storage"]
        size = BODY_BYTES if storage == "body_plus_shared_cap" else FULL_BYTES
        physical = base + logical
        source_raw = parent[physical : physical + size]
        stock_raw = stock[logical : logical + size]
        if source_raw != stock_raw:
            raise BuildError(f"plaque source drift at {logical:06X}")
        if storage == "body_plus_shared_cap":
            source_pixels = compose_body(parent, base, source_raw)
        elif storage == "full_48x16":
            source_pixels = decode_grid(source_raw, 6, 2)
        else:
            raise BuildError(f"unknown storage mode: {storage}")
        action = row.get("action", "localize")
        if action == "preserve_source":
            target_pixels = [line[:] for line in source_pixels]
            target_raw = source_raw
            layout = {
                "preserved": True,
                "changed_pixel_count": 0,
                "changed_pixel_bbox": None,
            }
            preserved_count += 1
        elif action == "localize":
            target_pixels, layout = localize_pixels(
                source_pixels, row, font_paths, font_sizes, stroke_width
            )
            if row["layout"] == "directional":
                arrow_prefix_exact = all(
                    target_pixels[y][0:14] == source_pixels[y][0:14]
                    for y in range(16)
                )
                if not arrow_prefix_exact:
                    raise BuildError(f"direction arrow changed at {logical:06X}")
                layout["arrow_prefix_x0_13_preserved"] = True
            target_raw = (
                encode_grid([line[8:48] for line in target_pixels], 5, 2)
                if storage == "body_plus_shared_cap"
                else encode_grid(target_pixels, 6, 2)
            )
            if target_raw == source_raw:
                raise BuildError(f"encoded bytes unchanged at {logical:06X}")
            candidate[physical : physical + size] = target_raw
            allowed.append((physical, physical + size))
            localized_count += 1
        else:
            raise BuildError(f"unknown action {action!r} at {logical:06X}")
        preview_rows.append({
            **row, "logical": logical, "before_pixels": source_pixels, "after_pixels": target_pixels,
        })
        manifest.append({
            "logical": f"{logical:06X}",
            "physical": f"{physical:06X}-{physical + size - 1:06X}",
            "bytes": size,
            "storage": storage,
            "jp": row["jp"],
            "ko": row["ko"],
            "action": action,
            "layout": row["layout"],
            "tone": row["tone"],
            "source_sha256": sha256(source_raw),
            "target_sha256": sha256(target_raw),
            **layout,
        })

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    runs = diff_runs(parent, candidate)
    unexpected = [(start, end) for start, end in runs if not in_allowlist(start, end, allowed)]
    changed_asset_ranges = {
        (start, end) for start, end in allowed[:-1]
        if parent[start:end] != candidate[start:end]
    }
    if len(changed_asset_ranges) != localized_count or unexpected:
        raise BuildError(
            f"allowlist audit failed: changed assets={len(changed_asset_ranges)}, unexpected={unexpected}"
        )
    if (sum(candidate[:-2]) & 0xFFFF) != int.from_bytes(candidate[-2:], "little"):
        raise BuildError("WonderSwan checksum did not validate")

    previews = render_previews(preview_rows, args.preview_dir, font_paths["default"])
    atomic_bytes(args.out, bytes(candidate))
    atomic_copy(args.save, args.out_save)
    candidate_check = args.out.read_bytes()
    paired_save = args.out_save.read_bytes()
    checks = {
        "parent_sha256_bound": parent_sha == EXPECTED_PARENT_SHA256 or args.allow_parent_drift,
        "parent_unchanged_on_disk": sha256(args.parent.read_bytes()) == parent_sha,
        "live_saveram_unchanged_on_disk": sha256(args.save.read_bytes()) == save_sha,
        "paired_saveram_exact_copy": paired_save == save,
        "rom_and_saveram_stems_match": args.out.stem == args.out_save.stem,
        "all_24_source_assets_stock_exact": all(
            parent[base + logical : base + logical + (BODY_BYTES if next(r for r in rows if int(r['logical'], 16) == logical)['storage'] == 'body_plus_shared_cap' else FULL_BYTES)]
            == stock[logical : logical + (BODY_BYTES if next(r for r in rows if int(r['logical'], 16) == logical)['storage'] == 'body_plus_shared_cap' else FULL_BYTES)]
            for logical in EXPECTED_LOGICALS
        ),
        "all_24_assets_reviewed": len(manifest) == 24,
        "all_localized_assets_changed": len(changed_asset_ranges) == localized_count == 23,
        "level_source_preserved": candidate[base + 0x4C44D4 : base + 0x4C44D4 + FULL_BYTES]
        == parent[base + 0x4C44D4 : base + 0x4C44D4 + FULL_BYTES],
        "all_direction_arrow_prefixes_preserved": all(
            row.get("action") != "localize"
            or row.get("layout") != "directional"
            or row.get("arrow_prefix_x0_13_preserved") is True
            for row in manifest
        ),
        "diff_allowlist_clean": not unexpected,
        "candidate_reread_exact": candidate_check == bytes(candidate),
        "candidate_checksum_valid": (sum(candidate_check[:-2]) & 0xFFFF) == int.from_bytes(candidate_check[-2:], "little"),
    }
    if not all(checks.values()):
        raise BuildError(f"post-build verification failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_id_command_plaques_ko_candidate.py",
        "ok": True,
        "scope": "24 reviewed 48x16 OBJ ID-command plaques: 23 Korean-localized, ↑LEVEL preserved",
        "parent": identity(args.parent, parent),
        "stock": identity(args.stock, stock),
        "live_saveram": identity(args.save, save),
        "candidate": {
            **identity(args.out, candidate_check),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(args.out_save, paired_save),
        "spec": identity(args.spec),
        "fonts": {role: identity(path) for role, path in font_paths.items()},
        "translations": manifest,
        "counts": {
            "plaques": len(manifest),
            "localized": localized_count,
            "preserved_source": preserved_count,
            "body_plus_shared_cap": sum(row["storage"] == "body_plus_shared_cap" for row in rows),
            "full_48x16": sum(row["storage"] == "full_48x16" for row in rows),
            "diff_runs_including_checksum": len(runs),
            "changed_bytes_including_checksum": sum(end - start for start, end in runs),
        },
        "diff_allowlist": [
            {"start": f"{start:06X}", "end_exclusive": f"{end:06X}"} for start, end in allowed
        ],
        "unexpected_diff_runs": [
            {"start": f"{start:06X}", "end_exclusive": f"{end:06X}"} for start, end in unexpected
        ],
        "previews": previews,
        "runtime_status": {
            "observed_live_asset": "↑명중 candidate reload is verified by the separate runtime audit",
            "remaining": "22 other localized plaques need distinct reachable states for individual runtime A/B",
        },
        "checks": checks,
    }
    atomic_bytes(
        args.report,
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "paired_saveram": report["paired_saveram"],
        "counts": report["counts"],
        "checks": checks,
        "comparison_sheet": previews["comparison_sheet"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
