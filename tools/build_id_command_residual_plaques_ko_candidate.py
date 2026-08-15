#!/usr/bin/env python3
"""Build a current-TIP test ROM localizing the eight residual bank-4C plaques."""

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

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/id_command_residual_plaques_ko.json"
OUT = ROOT / "out/patch/id_command_residual_plaques_ko_candidate.wsc"
OUT_SAVE = ROOT / "sram/id_command_residual_plaques_ko_candidate.sav"
REPORT = ROOT / "out/patch/id_command_residual_plaques_ko_candidate_report.json"
PREVIEWS = ROOT / "out/patch/id_command_residual_plaques_ko_candidate_previews"

EXPECTED_MAIN_SHA256 = "cef2d40d7a0568e3add4025d8ebc6f5e6340f0a2b545a5f88decc6d28e3375f5"
EXPECTED_SAVE_SHA256 = "697826d2e0d506ae441526706dc6b289c91bc28a7d49cffda713390685367ae1"
EXPECTED_STOCK_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXPECTED_TARGETS = {
    0x4C4A74,
    0x4C4BB4,
    0x4C50F4,
    0x4C53B4,
    0x4CBEAA,
    0x4CC32A,
    0x4CE86A,
    0x4CE9EA,
}
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
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
    atomic_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def size_for(storage: str) -> tuple[int, int, int]:
    if storage == "body_40x16_plus_shared_right_cap":
        return 48, 5, 320
    if storage == "body_32x16_plus_shared_right_cap":
        return 40, 4, 256
    if storage == "body_32x16_plus_shared_both_caps":
        return 48, 4, 256
    if storage == "full_40x16":
        return 40, 5, 320
    if storage == "sparse_40x16_insert_shared_mid_column":
        return 48, 5, 320
    if storage == "full_48x16":
        return 48, 6, 384
    raise BuildError(f"unsupported storage: {storage}")


def localize_display(
    source: list[list[int]],
    text: str,
    font: ImageFont.FreeTypeFont,
    stroke: int,
    zone: tuple[int, int],
    tone: str,
) -> tuple[list[list[int]], dict[str, Any]]:
    width = len(source[0])
    x0, x1 = zone
    if not (0 <= x0 < x1 <= width):
        raise BuildError(f"invalid {width}x16 zone: {zone}")
    target = [row[:] for row in source]
    for y in range(1, 15):
        for x in range(x0, x1):
            target[y][x] = 0xC
    outline = 0xA if tone == "down" else 0xF
    for y in (0, 15):
        for x in range(max(x0, 6), x1):
            target[y][x] = outline
    outer, inner = base.make_masks(text, font, stroke)
    if outer.width > x1 - x0 or outer.height > 14:
        raise BuildError(
            f"{width}x16 text does not fit: {text!r} mask={outer.width}x{outer.height}"
        )
    dx = x0 + ((x1 - x0) - outer.width) // 2
    dy = 1 + (14 - outer.height) // 2
    outer_px, inner_px = outer.load(), inner.load()
    for y in range(outer.height):
        for x in range(outer.width):
            if outer_px[x, y]:
                target[dy + y][dx + x] = outline
            if inner_px[x, y]:
                target[dy + y][dx + x] = 0xE
    changed = [
        (x, y)
        for y in range(16)
        for x in range(width)
        if target[y][x] != source[y][x]
    ]
    if not changed:
        raise BuildError(f"{width}x16 localization is a no-op: {text}")
    if any(x < x0 or x >= x1 for x, y in changed):
        raise BuildError(f"{width}x16 pixels changed outside zone: {text}")
    return target, {
        "zone": [x0, 1, x1, 15],
        "font_size": font.size,
        "glyph_mask": [outer.width, outer.height],
        "draw_origin": [dx, dy],
        "changed_pixel_count": len(changed),
        "changed_pixel_bbox": [
            min(x for x, _ in changed),
            min(y for _, y in changed),
            max(x for x, _ in changed) + 1,
            max(y for _, y in changed) + 1,
        ],
        "outline_index": f"{outline:X}",
        "side_regions_preserved": all(
            target[y][:x0] == source[y][:x0]
            and target[y][x1:] == source[y][x1:]
            for y in range(16)
        ),
    }


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
        draw.text(
            (3, y + image_h + 2),
            f"{row['jp']}  {row['logical']:06X}",
            font=label_font,
            fill="white",
        )
        draw.text(
            (max_w + 3, y + image_h + 2),
            row["ko"],
            font=label_font,
            fill="white",
        )
        pair = Image.new(
            "RGB", (before.width + after.width, image_h), (20, 20, 20)
        )
        pair.paste(before, (0, 0))
        pair.paste(after, (before.width, 0))
        pair_path = out_dir / f"{index + 1:02d}_{row['logical']:06X}.png"
        pair.save(pair_path)
        individuals.append(rel(pair_path))
    sheet_path = out_dir / "all_8_before_after.png"
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
    parser.add_argument("--parent", type=Path, default=MAIN)
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
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP drift: {sha256(parent)}")
    if len(save) != SAVE_SIZE or sha256(save) != EXPECTED_SAVE_SHA256:
        raise BuildError(f"live SaveRAM drift: {sha256(save)}")
    if sha256(stock) != EXPECTED_STOCK_SHA256:
        raise BuildError("stock ROM drift")
    stock_base = base.stock_base(parent)
    if stock_base != 0x800000:
        raise BuildError(f"unexpected stock base: {stock_base:#x}")

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if spec.get("parent_sha256", "").lower() != EXPECTED_MAIN_SHA256:
        raise BuildError("spec parent binding drift")
    rows = list(spec.get("plaques") or [])
    if len(rows) != 8 or {int(row["logical"], 16) for row in rows} != EXPECTED_TARGETS:
        raise BuildError("residual plaque inventory mismatch")
    font_path = ROOT / spec["font"]
    if not font_path.is_file():
        raise BuildError(f"missing font: {font_path}")
    stroke = int(spec["stroke_width"])

    # These result badges use three distinct compositions: a stored body with
    # an embedded left edge plus an external right cap, a complete plaque, or
    # a pure text body between external left/right caps.
    cap_source = base.decode_grid(
        bytes(
            stock[0x4C4654 : 0x4C4654 + base.FULL_BYTES]
        ),
        6,
        2,
    )
    shared_left_cap = [line[0:8] for line in cap_source]
    common_right_edge = [line[40:48] for line in cap_source]
    pursuit_source = base.decode_grid(
        bytes(parent[stock_base + 0x4CC32A : stock_base + 0x4CC32A + 320]),
        5,
        2,
    )
    pursuit_shared_top_logical = 0x4CB80A
    pursuit_shared_bottom_logical = 0x4CB8AA
    pursuit_shared_top = base.decode_grid(
        bytes(parent[stock_base + pursuit_shared_top_logical : stock_base + pursuit_shared_top_logical + 32]),
        1,
        1,
    )
    pursuit_shared_bottom = base.decode_grid(
        bytes(parent[stock_base + pursuit_shared_bottom_logical : stock_base + pursuit_shared_bottom_logical + 32]),
        1,
        1,
    )
    pursuit_shared_stock_top = bytes(stock[pursuit_shared_top_logical : pursuit_shared_top_logical + 32])
    pursuit_shared_stock_bottom = bytes(stock[pursuit_shared_bottom_logical : pursuit_shared_bottom_logical + 32])
    pursuit_shared_main_top = bytes(parent[stock_base + pursuit_shared_top_logical : stock_base + pursuit_shared_top_logical + 32])
    pursuit_shared_main_bottom = bytes(parent[stock_base + pursuit_shared_bottom_logical : stock_base + pursuit_shared_bottom_logical + 32])
    if pursuit_shared_main_top == pursuit_shared_stock_top or pursuit_shared_main_bottom == pursuit_shared_stock_bottom:
        raise BuildError("追撃 shared 撃 column is unexpectedly stock-exact; expected localized ↑電撃 alias in current main")
    if not all(pursuit_source[y][32:40] == common_right_edge[y] for y in range(16)):
        raise BuildError("追撃! final private tile pair is not the validated common right edge")

    candidate = bytearray(parent)
    target_intervals: list[tuple[int, int]] = []
    manifest: list[dict[str, Any]] = []
    preview_rows: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["logical"], 16)
        width, columns, size = size_for(row["storage"])
        physical = stock_base + logical
        source_raw = bytes(parent[physical : physical + size])
        stock_raw = bytes(stock[logical : logical + size])
        if source_raw != stock_raw:
            raise BuildError(f"source is not stock-exact at {logical:06X}")
        source_body = base.decode_grid(source_raw, columns, 2)
        uses_shared_right_cap = "plus_shared_right_cap" in row["storage"]
        uses_shared_both_caps = "plus_shared_both_caps" in row["storage"]
        uses_sparse_mid = row["storage"] == "sparse_40x16_insert_shared_mid_column"
        if uses_sparse_mid:
            if logical != 0x4CC32A:
                raise BuildError(f"unexpected sparse-mid target at {logical:06X}")
            if int(row.get("shared_display_column", -1)) != 3:
                raise BuildError("追撃 shared display column drift")
            if int(str(row.get("shared_top_logical")), 16) != pursuit_shared_top_logical:
                raise BuildError("追撃 shared top source drift")
            if int(str(row.get("shared_bottom_logical")), 16) != pursuit_shared_bottom_logical:
                raise BuildError("追撃 shared bottom source drift")
            source_pixels = []
            for y in range(16):
                shared = pursuit_shared_top[y] if y < 8 else pursuit_shared_bottom[y - 8]
                private_cols = [source_body[y][x : x + 8] for x in range(0, 40, 8)]
                source_pixels.append(
                    private_cols[0] + private_cols[1] + private_cols[2]
                    + shared
                    + private_cols[3] + private_cols[4]
                )
        elif uses_shared_both_caps:
            source_pixels = [
                shared_left_cap[y] + source_body[y] + common_right_edge[y]
                for y in range(16)
            ]
        elif uses_shared_right_cap:
            source_pixels = [
                source_body[y] + common_right_edge[y] for y in range(16)
            ]
        else:
            source_pixels = source_body
        font = ImageFont.truetype(str(font_path), int(row["font_size"]))
        target_pixels, details = localize_display(
            source_pixels,
            str(row["text"]),
            font,
            stroke,
            tuple(int(value) for value in row["zone"]),
            str(row["tone"]),
        )
        if uses_shared_right_cap:
            stored_width = columns * 8
            if not all(
                target_pixels[y][stored_width : stored_width + 8]
                == common_right_edge[y]
                for y in range(16)
            ):
                raise BuildError(f"shared right cap changed at {logical:06X}")
            details["shared_right_cap_preserved"] = True
            details["source_right_outer_pixels_preserved"] = True
            details["stored_body_geometry"] = f"{stored_width}x16"
            details["display_geometry"] = f"{width}x16"
        elif uses_shared_both_caps:
            stored_width = columns * 8
            if not all(
                target_pixels[y][0:8] == shared_left_cap[y]
                and target_pixels[y][8 + stored_width : 16 + stored_width]
                == common_right_edge[y]
                for y in range(16)
            ):
                raise BuildError(f"shared left/right caps changed at {logical:06X}")
            details["shared_left_cap_preserved"] = True
            details["shared_right_cap_preserved"] = True
            details["stored_body_geometry"] = f"{stored_width}x16"
            details["display_geometry"] = f"{width}x16"
        elif uses_sparse_mid:
            if not all(
                target_pixels[y][24:32]
                == (pursuit_shared_top[y] if y < 8 else pursuit_shared_bottom[y - 8])
                for y in range(16)
            ):
                raise BuildError("추격! layout is not byte/pixel compatible with the shared ↑電撃 column")
            if not all(
                target_pixels[y][0:8] == source_pixels[y][0:8]
                and target_pixels[y][40:48] == source_pixels[y][40:48]
                for y in range(16)
            ):
                raise BuildError("追撃 private outer edge columns changed")
            details["sparse_runtime_display"] = "48x16 private columns 0,1,2,4,5 + shared column 3"
            details["shared_mid_column_preserved"] = True
            details["shared_mid_display_x"] = [24, 32]
            details["shared_mid_top_logical"] = f"{pursuit_shared_top_logical:06X}"
            details["shared_mid_bottom_logical"] = f"{pursuit_shared_bottom_logical:06X}"
            details["shared_mid_main_differs_from_stock"] = True
            details["private_outer_columns_preserved"] = True
            details["stored_body_geometry"] = "5x2 private tiles / 40x16 packed"
            details["display_geometry"] = "48x16"
        preserve_x = row.get("preserve_arrow_through_x")
        if preserve_x is not None:
            prefix = int(preserve_x) + 1
            if not all(
                target_pixels[y][:prefix] == source_pixels[y][:prefix]
                for y in range(16)
            ):
                raise BuildError(f"direction arrow changed at {logical:06X}")
            details["arrow_prefix_preserved_through_x"] = int(preserve_x)
        if uses_sparse_mid:
            private_target = [line[0:24] + line[32:48] for line in target_pixels]
            target_raw = base.encode_grid(private_target, columns, 2)
        else:
            target_raw = (
                base.encode_grid(
                    [line[: columns * 8] for line in target_pixels], columns, 2
                )
                if uses_shared_right_cap
                else (
                    base.encode_grid(
                        [line[8 : 8 + columns * 8] for line in target_pixels],
                        columns,
                        2,
                    )
                    if uses_shared_both_caps
                    else base.encode_grid(target_pixels, columns, 2)
                )
            )
        if target_raw == source_raw:
            raise BuildError(f"encoded target is a no-op at {logical:06X}")
        candidate[physical : physical + size] = target_raw
        target_intervals.append((physical, physical + size))
        manifest.append(
            {
                "logical": f"{logical:06X}",
                "logical_range": f"{logical:06X}-{logical + size - 1:06X}",
                "physical_range": f"{physical:06X}-{physical + size - 1:06X}",
                "storage": row["storage"],
                "display_geometry": f"{width}x16",
                "jp": row["jp"],
                "ko": row["ko"],
                "source_sha256": sha256(source_raw),
                "target_sha256": sha256(target_raw),
                **details,
            }
        )
        preview_rows.append(
            {
                **row,
                "logical": logical,
                "before_pixels": source_pixels,
                "after_pixels": target_pixels,
            }
        )

    checksum = base.update_ws_checksum(candidate)
    allowed = merged(target_intervals + [(len(candidate) - 2, len(candidate))])
    result = bytes(candidate)
    runs = base.diff_runs(parent, result)
    unexpected = [
        (start, end)
        for start, end in runs
        if not any(lo <= start and end <= hi for lo, hi in allowed)
    ]
    if unexpected:
        raise BuildError(f"diff outside target allowlist: {unexpected}")
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum invalid")

    previews = render_previews(preview_rows, args.preview_dir, font_path)
    atomic_bytes(args.out, result)
    args.out_save.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.save, args.out_save)
    candidate_check = args.out.read_bytes()
    paired_save = args.out_save.read_bytes()
    checks = {
        "parent_hash_bound": sha256(parent) == EXPECTED_MAIN_SHA256,
        "live_saveram_hash_bound": sha256(save) == EXPECTED_SAVE_SHA256,
        "all_eight_sources_stock_exact": len(manifest) == 8,
        "pursuit_sparse_runtime_48x16": any(
            row["logical"] == "4CC32A"
            and row["storage"] == "sparse_40x16_insert_shared_mid_column"
            and row["display_geometry"] == "48x16"
            and row.get("shared_mid_column_preserved") is True
            for row in manifest
        ),
        "pursuit_shared_alias_unchanged": (
            candidate_check[stock_base + pursuit_shared_top_logical : stock_base + pursuit_shared_top_logical + 32]
            == parent[stock_base + pursuit_shared_top_logical : stock_base + pursuit_shared_top_logical + 32]
            and candidate_check[stock_base + pursuit_shared_bottom_logical : stock_base + pursuit_shared_bottom_logical + 32]
            == parent[stock_base + pursuit_shared_bottom_logical : stock_base + pursuit_shared_bottom_logical + 32]
        ),
        "pursuit_common_right_edge_exact": all(
            pursuit_source[y][32:40] == common_right_edge[y] for y in range(16)
        ),
        "all_right_cap_compositions_preserved": all(
            row.get("shared_right_cap_preserved") is True
            and row.get("side_regions_preserved") is True
            for row in manifest
            if "plus_shared_right_cap" in row["storage"]
        ),
        "both_cap_composition_preserved": all(
            row.get("shared_left_cap_preserved") is True
            and row.get("shared_right_cap_preserved") is True
            and row.get("side_regions_preserved") is True
            for row in manifest
            if "plus_shared_both_caps" in row["storage"]
        ),
        "corrected_seal_translation": any(
            row["logical"] == "4C4A74"
            and row["jp"] == "封印!"
            and row["ko"] == "봉인!"
            for row in manifest
        ),
        "corrected_shield_is_32x16_body": any(
            row["logical"] == "4C4BB4"
            and row["logical_range"] == "4C4BB4-4C4CB3"
            and row["jp"] == "盾!"
            for row in manifest
        ),
        "corrected_preemptive_is_32x16_body": any(
            row["logical"] == "4CE9EA"
            and row["logical_range"] == "4CE9EA-4CEAE9"
            and row["jp"] == "先制"
            and row["ko"] == "선제"
            for row in manifest
        ),
        "all_targets_changed": all(
            row["source_sha256"] != row["target_sha256"] for row in manifest
        ),
        "diff_allowlist_clean": not unexpected,
        "candidate_reread_exact": candidate_check == result,
        "candidate_checksum_valid": (
            (sum(candidate_check[:-2]) & 0xFFFF)
            == int.from_bytes(candidate_check[-2:], "little")
        ),
        "paired_saveram_exact": paired_save == save,
        "parent_unchanged_on_disk": args.parent.read_bytes() == parent,
        "live_saveram_unchanged_on_disk": args.save.read_bytes() == save,
        "runtime_bank_7a_exact": (
            candidate_check[0xFA0000:0xFB0000] == parent[0xFA0000:0xFB0000]
        ),
        "runtime_bank_7f_exact_except_checksum": (
            candidate_check[0xFF0000:0xFFFFFE] == parent[0xFF0000:0xFFFFFE]
        ),
    }
    if not all(checks.values()):
        raise BuildError(f"post-build checks failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_id_command_residual_plaques_ko_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_pending_user_runtime_test",
        "scope": "eight additional bank-4C plaques localized from current main TIP with corrected 封印 and sparse 追撃 runtime alias handling",
        "geometry_correction": {
            "shield": (
                "4C4BB4-4C4CB3 is 盾!, not 失敗!; it is a 32x16 body plus "
                "the shared right cap appended, displayed as 40x16."
            ),
            "seal": (
                "The user runtime capture proves 4C4A74 is 封印!, not 捕獲!; "
                "the Korean target is therefore 봉인!."
            ),
            "pursuit": (
                "The user stock/main 6x captures prove a 48x16 sparse plaque. "
                "Stored 4CC32A-4CC469 supplies display columns 0,1,2,4,5 while "
                "display column 3 reuses 4CB80A/4CB8AA from ↑電撃. The promoted "
                "↑전격 localization changed exactly that shared column, which is why only "
                "the 撃 area became corrupted. The 추격! layout is drawn at font 12/x=8; "
                "its x=24..31 column is byte/pixel-exact with the already-localized shared "
                "column, so only the ten private pursuit tiles are patched."
            ),
            "preemptive": (
                "4CE9EA-4CEAE9 is the exact 32x16 先制 text body, not 反撃. "
                "It is displayed between external left/right caps; bytes beginning "
                "at 4CEAEA are unrelated."
            ),
        },
        "parent": identity(args.parent, parent),
        "stock": identity(args.stock, stock),
        "live_saveram": identity(args.save, save),
        "spec": identity(args.spec),
        "font": identity(font_path),
        "candidate": {
            **identity(args.out, candidate_check),
            "ws_checksum": f"{checksum:04X}",
        },
        "paired_saveram": identity(args.out_save, paired_save),
        "targets": manifest,
        "counts": {
            "total": len(manifest),
            "body_40x16_plus_shared_right_cap": sum(
                row["storage"] == "body_40x16_plus_shared_right_cap"
                for row in manifest
            ),
            "body_32x16_plus_shared_right_cap": sum(
                row["storage"] == "body_32x16_plus_shared_right_cap"
                for row in manifest
            ),
            "body_32x16_plus_shared_both_caps": sum(
                row["storage"] == "body_32x16_plus_shared_both_caps"
                for row in manifest
            ),
            "full_40x16": sum(row["storage"] == "full_40x16" for row in manifest),
            "sparse_40x16_insert_shared_mid_column": sum(
                row["storage"] == "sparse_40x16_insert_shared_mid_column"
                for row in manifest
            ),
            "full_48x16": sum(row["storage"] == "full_48x16" for row in manifest),
            "changed_bytes_including_checksum": sum(end - start for start, end in runs),
            "diff_runs_including_checksum": len(runs),
        },
        "diff": {
            "allowlist": [
                {"start": f"{start:08X}", "end_exclusive": f"{end:08X}"}
                for start, end in allowed
            ],
            "runs": [
                {"start": f"{start:08X}", "end_exclusive": f"{end:08X}"}
                for start, end in runs
            ],
            "unexpected": unexpected,
        },
        "previews": previews,
        "checks": checks,
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(args.report, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
