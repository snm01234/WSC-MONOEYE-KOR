#!/usr/bin/env python3
"""Build the third ID-plaque readability/residual-cleanup test candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_id_command_plaques_ko_candidate as base  # noqa: E402


DEFAULT_PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
DEFAULT_SPEC = ROOT / "data/id_command_plaque_v3_adjustments_ko.json"
DEFAULT_OUT = ROOT / "out/patch/id_command_plaques_ko_v3_candidate.wsc"
DEFAULT_OUT_SAVE = ROOT / "sram/id_command_plaques_ko_v3_candidate.sav"
DEFAULT_REPORT = ROOT / "out/patch/id_command_plaques_ko_v3_candidate_report.json"
DEFAULT_PREVIEWS = ROOT / "out/patch/id_command_plaques_ko_v3_candidate_previews"
EXPECTED_PARENT_SHA256 = "9ba9804dac603d84efe75bff6efecfebd2b55ef7bd602671c375f97791f61d75"
EXPECTED_SAVE_SHA256 = "589f47d18cbe245e544f62a92542eedaed87895794aaf072b3071d7442cde4a4"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_LOGICALS = {
    0x4C5234,
    0x4C5A54,
    0x4C5BD4,
    0x4CBA2A,
    0x4CBBAA,
    0x4CBD2A,
    0x4CE56A,
}


class BuildError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    raw = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def restore_bright_plaque_edges(pixels: list[list[int]]) -> None:
    """Remove side glyph residue while retaining the rounded 48x16 frame."""
    c, b, f = 0xC, 0xB, 0xF
    columns = {
        4: [c, f, c, c, c, c, c, c, c, c, c, c, c, c, f, c],
        5: [b, b, c, c, c, c, c, c, c, c, c, c, c, c, b, b],
        42: [b, b, c, c, c, c, c, c, c, c, c, c, c, c, b, b],
        43: [c, f, c, c, c, c, c, c, c, c, c, c, c, c, f, c],
        44: [0, f, b, c, c, c, c, c, c, c, c, c, c, b, f, 0],
    }
    for x, values in columns.items():
        for y, value in enumerate(values):
            pixels[y][x] = value


def changed_pixel_details(before: list[list[int]], after: list[list[int]]) -> dict[str, Any]:
    changed = [(x, y) for y in range(16) for x in range(48) if before[y][x] != after[y][x]]
    if not changed:
        return {"changed_pixel_count": 0, "changed_pixel_bbox": None}
    return {
        "changed_pixel_count": len(changed),
        "changed_pixel_bbox": [
            min(x for x, _ in changed),
            min(y for _, y in changed),
            max(x for x, _ in changed) + 1,
            max(y for _, y in changed) + 1,
        ],
    }


def paste_text_mask(
    pixels: list[list[int]],
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int,
    outline: int = 0xF,
    ink: int = 0xE,
) -> dict[str, Any]:
    outer, inner = base.make_masks(text, font, 1, 0)
    y = 1 + (14 - outer.height) // 2
    if x < 0 or x + outer.width > 48 or y < 1 or y + outer.height > 15:
        raise BuildError(f"text mask does not fit: {text} at {x},{y} size={outer.size}")
    outer_px = outer.load()
    inner_px = inner.load()
    for yy in range(outer.height):
        for xx in range(outer.width):
            if outer_px[xx, yy]:
                pixels[y + yy][x + xx] = outline
            if inner_px[xx, yy]:
                pixels[y + yy][x + xx] = ink
    return {"text": text, "origin": [x, y], "mask": [outer.width, outer.height]}


def redraw(
    source: list[list[int]],
    adjustment: dict[str, Any],
    font_path: Path,
    arrow_templates: dict[str, list[list[int]]],
) -> tuple[list[list[int]], dict[str, Any]]:
    mode = adjustment["mode"]
    if mode == "edge_cleanup":
        target = [row[:] for row in source]
        restore_bright_plaque_edges(target)
        return target, {"edge_columns_restored": [4, 5, 42, 43, 44]}

    if mode == "directional_redraw":
        row = {
            "layout": "directional",
            "tone": adjustment["tone"],
            "text": adjustment["text"],
            "ko": adjustment["label"],
            "zone": adjustment["zone"],
            "font_size": 12,
        }
        target, details = base.localize_pixels(
            source,
            row,
            {"default": font_path},
            {"directional": 12},
            1,
        )
        template = arrow_templates[adjustment["tone"]]
        for y in range(16):
            target[y][0:15] = template[y][0:15]
        canonical_exact = all(target[y][0:15] == template[y][0:15] for y in range(16))
        if not canonical_exact:
            raise BuildError(f"canonical arrow copy failed for {adjustment['label']}")
        details["arrow_prefix_x0_14_canonicalized"] = True
        details["arrow_template"] = "↑명중" if adjustment["tone"] == "bright" else "↓명중"
        return target, details

    if mode == "bold_redraw":
        layout = adjustment["layout"]
        row = {
            "layout": layout,
            "tone": "bright",
            "text": adjustment["text"],
            "ko": adjustment["label"],
            "font_size": adjustment["font_size"],
            "letter_spacing": adjustment["letter_spacing"],
        }
        target, details = base.localize_pixels(
            source,
            row,
            {"default": font_path},
            {layout: int(adjustment["font_size"])},
            1,
        )
        if adjustment.get("edge_cleanup"):
            restore_bright_plaque_edges(target)
            details["edge_columns_restored"] = [4, 5, 42, 43, 44]
        return target, details

    if mode == "hp_split_redraw":
        target = [row[:] for row in source]
        for y in range(1, 15):
            for x in range(5, 43):
                target[y][x] = 0xC
        for y in (0, 15):
            for x in range(6, 42):
                target[y][x] = 0xF
        restore_bright_plaque_edges(target)
        font = ImageFont.truetype(str(font_path), size=int(adjustment["font_size"]))
        hp = paste_text_mask(
            target,
            adjustment["hp_text"],
            font,
            int(adjustment["hp_x"]),
        )
        recovery = paste_text_mask(
            target,
            adjustment["recovery_text"],
            font,
            int(adjustment["recovery_x"]),
        )
        return target, {
            "font_role": "default_bold",
            "font_size": int(adjustment["font_size"]),
            "split_layout": {"hp": hp, "recovery": recovery},
            "edge_columns_restored": [4, 5, 42, 43, 44],
        }

    raise BuildError(f"unknown adjustment mode: {mode}")


def render_previews(rows: list[dict[str, Any]], out_dir: Path, font_path: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = 7
    width = 48 * scale
    image_h = 16 * scale
    label_h = 24
    cell_h = image_h + label_h
    sheet = Image.new("RGB", (width * 2, cell_h * len(rows)), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.truetype(str(font_path), 14)
    individuals: list[str] = []
    for index, row in enumerate(rows):
        before = base.render_plaque(row["before_pixels"], scale)
        after = base.render_plaque(row["after_pixels"], scale)
        y = index * cell_h
        sheet.paste(before, (0, y))
        sheet.paste(after, (width, y))
        draw.text((3, y + image_h + 2), f"before {row['logical']:06X}", font=label_font, fill="white")
        draw.text((width + 3, y + image_h + 2), row["label"], font=label_font, fill="white")
        pair = Image.new("RGB", (width * 2, image_h), (20, 20, 20))
        pair.paste(before, (0, 0))
        pair.paste(after, (width, 0))
        path = out_dir / f"{index + 1:02d}_{row['logical']:06X}.png"
        pair.save(path)
        individuals.append(rel(path))
    sheet_path = out_dir / "all_7_before_after.png"
    sheet.save(sheet_path)
    return {"comparison_sheet": rel(sheet_path), "individual_pairs": individuals}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--save", type=Path, default=DEFAULT_SAVE)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--out-save", type=Path, default=DEFAULT_OUT_SAVE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--preview-dir", type=Path, default=DEFAULT_PREVIEWS)
    args = parser.parse_args(argv)

    if args.out.stem != args.out_save.stem:
        raise BuildError("test ROM and SaveRAM stems must match")
    parent = args.parent.read_bytes()
    save = args.save.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP parent drift")
    if len(save) != SAVE_SIZE or sha256(save) != EXPECTED_SAVE_SHA256:
        raise BuildError("main SaveRAM drift")
    parent_base = base.stock_base(parent)
    if parent_base != 0x800000:
        raise BuildError(f"unexpected parent stock base: {parent_base:#x}")

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    adjustments = spec.get("adjustments") or []
    logicals = {int(row["logical"], 16) for row in adjustments}
    if len(adjustments) != 7 or logicals != EXPECTED_LOGICALS:
        raise BuildError("v3 adjustment inventory mismatch")
    if spec.get("parent_sha256", "").lower() != EXPECTED_PARENT_SHA256:
        raise BuildError("spec parent binding mismatch")
    font_path = ROOT / spec["font"]
    if not font_path.is_file():
        raise BuildError(f"missing font: {font_path}")

    candidate = bytearray(parent)
    up_body = bytes(
        parent[
            parent_base + 0x4C54F4 : parent_base + 0x4C54F4 + base.BODY_BYTES
        ]
    )
    arrow_templates = {
        "bright": base.compose_body(parent, parent_base, up_body),
        "down": base.decode_grid(
            bytes(
                parent[
                    parent_base + 0x4C5634 :
                    parent_base + 0x4C5634 + base.FULL_BYTES
                ]
            ),
            6,
            2,
        ),
    }
    allowed: list[tuple[int, int]] = []
    manifest: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    for adjustment in adjustments:
        logical = int(adjustment["logical"], 16)
        physical = parent_base + logical
        source_raw = bytes(parent[physical : physical + base.FULL_BYTES])
        source_pixels = base.decode_grid(source_raw, 6, 2)
        target_pixels, details = redraw(
            source_pixels,
            adjustment,
            font_path,
            arrow_templates,
        )
        target_raw = base.encode_grid(target_pixels, 6, 2)
        if target_raw == source_raw:
            raise BuildError(f"adjustment produced no byte change at {logical:06X}")
        candidate[physical : physical + base.FULL_BYTES] = target_raw
        allowed.append((physical, physical + base.FULL_BYTES))
        pixel_details = changed_pixel_details(source_pixels, target_pixels)
        manifest.append({
            "logical": f"{logical:06X}",
            "physical": f"{physical:06X}-{physical + base.FULL_BYTES - 1:06X}",
            "label": adjustment["label"],
            "mode": adjustment["mode"],
            "source_sha256": sha256(source_raw),
            "target_sha256": sha256(target_raw),
            **details,
            **pixel_details,
        })
        previews.append({
            "logical": logical,
            "label": adjustment["label"],
            "before_pixels": source_pixels,
            "after_pixels": target_pixels,
        })

    checksum = base.update_ws_checksum(candidate)
    allowed_with_checksum = allowed + [(len(candidate) - 2, len(candidate))]
    runs = base.diff_runs(parent, candidate)
    unexpected = [
        (start, end)
        for start, end in runs
        if not base.in_allowlist(start, end, allowed_with_checksum)
    ]
    changed_ranges = sum(parent[start:end] != candidate[start:end] for start, end in allowed)
    if unexpected or changed_ranges != 7:
        raise BuildError(f"diff allowlist failure: changed={changed_ranges}, unexpected={unexpected}")
    if (sum(candidate[:-2]) & 0xFFFF) != int.from_bytes(candidate[-2:], "little"):
        raise BuildError("candidate checksum invalid")

    preview_report = render_previews(previews, args.preview_dir, font_path)
    base.atomic_bytes(args.out, bytes(candidate))
    base.atomic_copy(args.save, args.out_save)
    reread = args.out.read_bytes()
    paired_save = args.out_save.read_bytes()
    checks = {
        "parent_sha256_bound": sha256(parent) == EXPECTED_PARENT_SHA256,
        "parent_unchanged_on_disk": sha256(args.parent.read_bytes()) == EXPECTED_PARENT_SHA256,
        "main_saveram_unchanged_on_disk": sha256(args.save.read_bytes()) == EXPECTED_SAVE_SHA256,
        "paired_saveram_exact_copy": paired_save == save,
        "rom_and_saveram_stems_match": args.out.stem == args.out_save.stem,
        "all_7_adjustments_changed": changed_ranges == 7,
        "direction_arrow_prefixes_canonicalized": all(
            row["mode"] != "directional_redraw"
            or row.get("arrow_prefix_x0_14_canonicalized") is True
            for row in manifest
        ),
        "diff_allowlist_clean": not unexpected,
        "candidate_reread_exact": reread == bytes(candidate),
        "candidate_checksum_valid": (sum(reread[:-2]) & 0xFFFF) == int.from_bytes(reread[-2:], "little"),
    }
    if not all(checks.values()):
        raise BuildError(f"post-build audit failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_id_command_plaques_ko_v3_candidate.py",
        "ok": True,
        "scope": "seven active ID-plaque adjustments after restoring 발묶기 and HP회복 to the promoted parent; test candidate only",
        "parent": identity(args.parent, parent),
        "main_saveram": identity(args.save, save),
        "candidate": {**identity(args.out, reread), "ws_checksum": f"{checksum:04X}"},
        "paired_saveram": identity(args.out_save, paired_save),
        "spec": identity(args.spec),
        "font": identity(font_path),
        "adjustments": manifest,
        "counts": {
            "adjustments": len(manifest),
            "diff_runs_including_checksum": len(runs),
            "changed_bytes_including_checksum": sum(end - start for start, end in runs),
        },
        "diff_allowlist": [
            {"start": f"{start:06X}", "end_exclusive": f"{end:06X}"}
            for start, end in allowed_with_checksum
        ],
        "unexpected_diff_runs": unexpected,
        "previews": preview_report,
        "checks": checks,
    }
    base.atomic_bytes(
        args.report,
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "paired_saveram": report["paired_saveram"],
        "counts": report["counts"],
        "checks": checks,
        "comparison_sheet": preview_report["comparison_sheet"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
