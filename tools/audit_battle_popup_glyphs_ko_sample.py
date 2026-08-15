#!/usr/bin/env python3
"""Independent static audit for the Galmuri7 Korean battle-popup sample."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
DEFAULT_CANDIDATE = ROOT / "out/patch/battle_popup_glyphs_ko_galmuri7_sample.wsc"
DEFAULT_PAIRED_SAVE = ROOT / "sram/battle_popup_glyphs_ko_galmuri7_sample.sav"
DEFAULT_SPEC = ROOT / "data/battle_popup_glyph_translations_ko.json"
DEFAULT_BUILD_REPORT = ROOT / "out/patch/battle_popup_glyphs_ko_galmuri7_sample_report.json"
DEFAULT_OUT = ROOT / "out/patch/battle_popup_glyphs_ko_galmuri7_sample_audit.json"

ROM_SIZE = 16 * 1024 * 1024
STOCK_SIZE = 8 * 1024 * 1024
SAVE_SIZE = 32 * 1024
BASE = 0x800000
TILE_BYTES = 0x20
EXPECTED_TRANSLATIONS = {
    "Ｉフィールド": "I-필드",
    "ＩＦキャンセラー": "IF캔슬러",
    "Ｆバリア": "F배리어",
    "Ｐディフェンサー": "P디펜서",
    "ビームコート": "빔코트",
    "バイオフィールド": "바이오필드",
    "分身": "분신",
    "クリティカル!": "크리티컬!",
    "ミス!": "미스!",
    "月光蝶": "월광접",
    "光発動": "빛발동",
}


class AuditError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def h(value: str | int) -> int:
    return value if isinstance(value, int) else int(value, 16)


def parse_source_ids(rom: bytes, start: int) -> tuple[list[int], int, list[int]]:
    word = int.from_bytes(rom[start : start + 2], "little")
    if not word & 0x8000:
        raise AuditError(f"{start:06X}: source list flag missing")
    count = word & 0x7FFF
    mapping = (start & 0xFF0000) | int.from_bytes(rom[start + 4 : start + 6], "little")
    cursor = start + 6
    ids: list[int] = []
    lengths: list[int] = []
    for _ in range(count):
        item = int.from_bytes(rom[cursor : cursor + 2], "little")
        cursor += 2
        if item & 0x4000:
            length = int.from_bytes(rom[cursor : cursor + 2], "little")
            cursor += 2
            first = item & 0x3FFF
            ids.extend(range(first, first + length))
            lengths.append(length)
        else:
            ids.append(item)
            lengths.append(1)
    if cursor != mapping:
        raise AuditError(f"{start:06X}: source list ends {cursor:06X}, mapping starts {mapping:06X}")
    return ids, mapping, lengths


def encode_tile(rows: list[list[int]]) -> bytes:
    if len(rows) != 8 or any(len(row) != 8 for row in rows):
        raise AuditError("invalid tile geometry")
    out = bytearray()
    for row in rows:
        for x in range(0, 8, 2):
            out.append((row[x] << 4) | row[x + 1])
    return bytes(out)


def independent_target_patterns(row: dict[str, Any], font: ImageFont.FreeTypeFont, fs: dict[str, Any]) -> dict[int, bytes]:
    cells = int(row["cells"])
    width = cells * 8
    mask = Image.new("L", (width, 16), 0)
    draw = ImageDraw.Draw(mask)
    for placement in row["layout"]["placements"]:
        if len(placement) not in (2, 3):
            raise AuditError(f"{row['id']}: invalid placement")
        cell_value, text = placement[0], placement[1]
        cell = int(cell_value)
        bbox = font.getbbox(text)
        glyph = Image.new("L", (max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1])), 0)
        ImageDraw.Draw(glyph).text((-bbox[0], -bbox[1]), text, font=font, fill=255)
        glyph = glyph.point(lambda value: 255 if value >= 128 else 0)
        if glyph.width > 8:
            raise AuditError(f"{row['id']}: glyph does not fit")
        x = cell * 8 + (8 - glyph.width) // 2
        y = int(placement[2]) if len(placement) == 3 else int(fs["glyph_top"])
        if y + glyph.height > 16:
            raise AuditError(f"{row['id']}: glyph exceeds popup height")
        draw.bitmap((x, y), glyph, fill=255)

    pixels = [[0] * width for _ in range(16)]
    px = mask.load()
    coords = [(x, y) for y in range(16) for x in range(width) if px[x, y] >= 128]
    dx, dy = (int(value) for value in fs["shadow_offset"])
    for x, y in coords:
        sx, sy = x + dx, y + dy
        if 0 <= sx < width and 0 <= sy < 16:
            pixels[sy][sx] = int(fs["shadow_index"])
    for x, y in coords:
        pixels[y][x] = int(fs["foreground_index"])

    desired: dict[int, bytes] = {}
    for cell, pair in enumerate(row["cell_tiles"]):
        for half, source_value in enumerate(pair):
            tile = encode_tile([
                line[cell * 8 : cell * 8 + 8]
                for line in pixels[half * 8 : half * 8 + 8]
            ])
            if source_value is None:
                if tile != bytes(TILE_BYTES):
                    raise AuditError(f"{row['id']}: ink in unavailable tile")
                continue
            source_id = h(source_value)
            if source_id in desired and desired[source_id] != tile:
                raise AuditError(f"{row['id']}: shared tile conflict")
            desired[source_id] = tile
    return desired


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--stock", type=Path, default=DEFAULT_STOCK)
    parser.add_argument("--save", type=Path, default=DEFAULT_SAVE)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--paired-save", type=Path, default=DEFAULT_PAIRED_SAVE)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--build-report", type=Path, default=DEFAULT_BUILD_REPORT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    parent = args.parent.read_bytes()
    stock = args.stock.read_bytes()
    save = args.save.read_bytes()
    candidate = args.candidate.read_bytes()
    paired = args.paired_save.read_bytes()
    if (len(parent), len(stock), len(save), len(candidate), len(paired)) != (
        ROM_SIZE, STOCK_SIZE, SAVE_SIZE, ROM_SIZE, SAVE_SIZE,
    ):
        raise AuditError("input size mismatch")
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    build_report = json.loads(args.build_report.read_text(encoding="utf-8"))
    font_path = ROOT / spec["font"]["path"]
    font = ImageFont.truetype(str(font_path), int(spec["font"]["size"]))
    atlas = h(spec["atlas"]["logical_base"])
    pool_first = h(spec["atlas"]["pool_first_tile_id"])
    pool_last = h(spec["atlas"]["pool_last_tile_id"])
    pool_lo = atlas + pool_first * TILE_BYTES
    pool_hi = atlas + (pool_last + 1) * TILE_BYTES

    translations = {row["jp"]: row["ko"] for row in spec["records"]}
    record_results = []
    allowed: list[tuple[int, int]] = [(BASE + pool_lo, BASE + pool_hi), (ROM_SIZE - 2, ROM_SIZE)]
    all_runtime_patterns_match = True
    all_tails_preserved = True
    all_pool_ids_bounded = True
    all_parent_sources_stock_exact = True
    for row in spec["records"]:
        start = h(row["record_start"])
        end = h(row["record_end_exclusive"])
        original_ids, original_source_end, original_lengths = parse_source_ids(stock, start)
        patched_ids, patched_source_end, patched_lengths = parse_source_ids(candidate[BASE:], start)
        if original_source_end != patched_source_end:
            raise AuditError(f"{row['id']}: mapping pointer moved")
        expected = independent_target_patterns(row, font, spec["font"])
        expected_sequence = [expected[tile_id] for tile_id in original_ids]
        actual_sequence = [
            candidate[BASE + atlas + tile_id * TILE_BYTES : BASE + atlas + (tile_id + 1) * TILE_BYTES]
            for tile_id in patched_ids
        ]
        sequence_ok = actual_sequence == expected_sequence
        tail_ok = candidate[BASE + original_source_end : BASE + end] == parent[BASE + original_source_end : BASE + end]
        ids_ok = all(pool_first <= tile_id <= pool_last for tile_id in patched_ids)
        source_stock_ok = parent[BASE + start : BASE + end] == stock[start:end]
        all_runtime_patterns_match &= sequence_ok
        all_tails_preserved &= tail_ok
        all_pool_ids_bounded &= ids_ok
        all_parent_sources_stock_exact &= source_stock_ok
        allowed.append((BASE + start, BASE + original_source_end))
        record_results.append({
            "id": row["id"],
            "jp": row["jp"],
            "ko": row["ko"],
            "original_group_lengths": original_lengths,
            "patched_group_lengths": patched_lengths,
            "local_tile_count": len(patched_ids),
            "runtime_patterns_match_independent_render": sequence_ok,
            "mapping_and_animation_tail_preserved": tail_ok,
            "patched_ids_within_4F_94": ids_ok,
            "parent_record_stock_exact": source_stock_ok,
        })

    runs = diff_runs(parent, candidate)
    unexpected = [
        (start, end) for start, end in runs
        if not any(lo <= start and end <= hi for lo, hi in allowed)
    ]
    checks = {
        "translations_match_user_pronunciation_request": translations == EXPECTED_TRANSLATIONS,
        "all_11_runtime_source_sequences_match_independent_galmuri7_render": all_runtime_patterns_match,
        "all_mapping_animation_tails_byte_exact": all_tails_preserved,
        "all_patched_tile_ids_within_documented_pool": all_pool_ids_bounded,
        "all_parent_records_stock_exact": all_parent_sources_stock_exact,
        "documented_pool_geometry_exact": pool_lo == 0x107F52 and pool_hi == 0x108812,
        "candidate_matches_build_report": sha256(candidate) == build_report["candidate"]["sha256"],
        "paired_saveram_exact_live_copy": paired == save,
        "rom_saveram_stems_match": args.candidate.stem == args.paired_save.stem,
        "diff_allowlist_clean": not unexpected,
        "checksum_valid": (sum(candidate[:-2]) & 0xFFFF) == int.from_bytes(candidate[-2:], "little"),
        "parent_unchanged": sha256(args.parent.read_bytes()) == sha256(parent),
        "live_saveram_unchanged": sha256(args.save.read_bytes()) == sha256(save),
    }
    result = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_popup_glyphs_ko_sample.py",
        "ok": all(checks.values()),
        "candidate": {"path": str(args.candidate), "sha256": sha256(candidate), "size": len(candidate)},
        "paired_saveram": {"path": str(args.paired_save), "sha256": sha256(paired), "size": len(paired)},
        "records": record_results,
        "diff": {
            "runs_including_checksum": len(runs),
            "changed_bytes_including_checksum": sum(end - start for start, end in runs),
            "unexpected_runs": [{"start": f"{s:06X}", "end_exclusive": f"{e:06X}"} for s, e in unexpected],
        },
        "checks": checks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": result["ok"],
        "candidate_sha256": result["candidate"]["sha256"],
        "records": len(record_results),
        "unexpected_diff_runs": len(unexpected),
        "changed_bytes": result["diff"]["changed_bytes_including_checksum"],
    }, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
