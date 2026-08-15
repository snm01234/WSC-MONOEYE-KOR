#!/usr/bin/env python3
"""Audit current-TIP displayed dialogue for screen-width risk.

This is a read-only audit.  It intentionally scans the vetted, runtime-facing
review population instead of walking every apparent zstring in banks 60-6F;
those banks contain non-text/data regions that produce large false strings under
a naive sequential scan.

The WonderSwan framebuffer is 224 pixels wide.  The normal dialogue glyph path
uses fixed 8-pixel cells, so 28 cells is the absolute full-frame single-row
maximum before margins/window chrome.  The project also has a historical
26-cell translation guard for bank-59 event dialogue.  Neither value is treated
as a universal runtime line width: actual callers of the text renderer can pass
screen-specific field limits.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

DEFAULT_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_SHEET = ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv"
DEFAULT_JSON = ROOT / "out/patch/dialogue_screen_width_audit.json"
DEFAULT_CSV = ROOT / "out/script/dialogue_screen_width_watchlist.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

GLYPH_CELL_PX = 8
FRAMEBUFFER_WIDTH_PX = 224
PHYSICAL_FULL_FRAME_CELLS = FRAMEBUFFER_WIDTH_PX // GLYPH_CELL_PX  # 28
LEGACY_EVENT_GUARD_CELLS = 26
CONDITIONAL_FIXED_FIELD_CELLS = 25
WATCHLIST_MIN_CELLS = 24
WATCHLIST_MIN_GROWTH = 8

# Static renderer evidence in the stock-mapped half of the expanded ROM.
# 7A:078B compares current glyph count against DI (the caller-supplied limit).
# 7A:0846 compares against [BP+0A] while padding short strings to that width.
RENDERER_LIMIT_COMPARE = 0x7A078B
RENDERER_LIMIT_COMPARE_BYTES = bytes.fromhex("3BC77D15")
RENDERER_PAD_COMPARE = 0x7A0846
RENDERER_PAD_COMPARE_BYTES = bytes.fromhex("3B460A7CDB")
# Verified call example: push 0x19 (=25 cells), text pointer, x=2, then A000:07AC.
FIXED_25_CALL = 0x7C9073
FIXED_25_CALL_BYTES = bytes.fromhex(
    "B8190050BB7704B82E5B50538D46F08CD3B9020032F69AAC0700A0"
)
TEXT_RENDER_FAR_CALL = bytes.fromhex("9AAC0700A0")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def visual_cells(text: str) -> int:
    return sum(1 for ch in text if ch not in "\r\n")


def trim_render_padding(text: str) -> str:
    # 01 padding decodes as U+3000 in the current TBL.  Only trailing padding is
    # removed; inter-word full-width spaces are real visible cells.
    return text.rstrip("\u3000 \t")


def load_rows(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(10_000_000)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def decode_current_rows(rom: bytes, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    sb = stock_base(rom)
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    decoded: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["abs"], 16)
        capacity = int(row.get("payload_capacity") or 0)
        got = read_encoded_z_safe(
            rom,
            sb + logical,
            max_len=max(256, capacity + 64),
        )
        if got is None:
            raise RuntimeError(f"current TIP zstring unreadable at {logical:06X}")
        payload = bytes(got[0])
        expected_prefix = bytes.fromhex(row.get("prefix_hex") or "")
        if expected_prefix and payload.startswith(expected_prefix):
            body = payload[len(expected_prefix) :]
            decode_mode = "prefix_preserved"
        else:
            # Some later battle-voice repairs replace the complete source
            # payload with one alias token whose phrase is the visible body.
            body = payload
            decode_mode = "whole_payload"
        current_text = trim_render_padding(dictionary.expand(body, tbl))
        cells = visual_cells(current_text)
        source_cells = visual_cells(row.get("original_jp") or "")
        decoded.append(
            {
                "abs": row["abs"].upper(),
                "scope": row.get("scope") or "",
                "classification": row.get("classification") or "",
                "prefix_hex": row.get("prefix_hex") or "",
                "decode_mode": decode_mode,
                "payload_len": len(payload),
                "current_text": current_text,
                "sheet_ko": row.get("ko") or "",
                "sheet_exact": current_text == (row.get("ko") or ""),
                "source_jp": row.get("original_jp") or "",
                "source_cells": source_cells,
                "cells": cells,
                "growth_cells": cells - source_cells,
                "render_px": cells * GLYPH_CELL_PX,
                "physical_full_frame_excess_cells": max(cells - PHYSICAL_FULL_FRAME_CELLS, 0),
                "physical_full_frame_excess_px": max(
                    cells * GLYPH_CELL_PX - FRAMEBUFFER_WIDTH_PX, 0
                ),
                "legacy_26_excess_cells": max(cells - LEGACY_EVENT_GUARD_CELLS, 0),
                "legacy_26_excess_px": max(
                    (cells - LEGACY_EVENT_GUARD_CELLS) * GLYPH_CELL_PX, 0
                ),
                "conditional_25_excess_cells": max(cells - CONDITIONAL_FIXED_FIELD_CELLS, 0),
                "conditional_25_excess_px": max(
                    (cells - CONDITIONAL_FIXED_FIELD_CELLS) * GLYPH_CELL_PX, 0
                ),
            }
        )
    return decoded


def group_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact_lengths = Counter(row["cells"] for row in rows)
    scopes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        s = scopes[row["scope"]]
        s["records"] += 1
        if row["cells"] > PHYSICAL_FULL_FRAME_CELLS:
            s["gt_28_physical_full_frame"] += 1
        if row["cells"] > LEGACY_EVENT_GUARD_CELLS:
            s["gt_26_legacy_guard"] += 1
        if row["cells"] == LEGACY_EVENT_GUARD_CELLS:
            s["eq_26_legacy_guard"] += 1
        if row["cells"] > CONDITIONAL_FIXED_FIELD_CELLS:
            s["gt_25_conditional_field"] += 1
        if row["cells"] >= WATCHLIST_MIN_CELLS:
            s["ge_24_near_edge"] += 1
        if row["growth_cells"] >= WATCHLIST_MIN_GROWTH:
            s["growth_ge_8"] += 1
    return {
        "records": len(rows),
        "sheet_exact": sum(bool(row["sheet_exact"]) for row in rows),
        "sheet_different": sum(not bool(row["sheet_exact"]) for row in rows),
        "max_cells": max((row["cells"] for row in rows), default=0),
        "gt_28_physical_full_frame": sum(
            row["cells"] > PHYSICAL_FULL_FRAME_CELLS for row in rows
        ),
        "gt_26_legacy_guard": sum(
            row["cells"] > LEGACY_EVENT_GUARD_CELLS for row in rows
        ),
        "eq_26_legacy_guard": sum(
            row["cells"] == LEGACY_EVENT_GUARD_CELLS for row in rows
        ),
        "gt_25_conditional_field": sum(
            row["cells"] > CONDITIONAL_FIXED_FIELD_CELLS for row in rows
        ),
        "ge_24_near_edge": sum(row["cells"] >= WATCHLIST_MIN_CELLS for row in rows),
        "growth_ge_8": sum(row["growth_cells"] >= WATCHLIST_MIN_GROWTH for row in rows),
        "exact_length_distribution_20_plus": {
            str(k): exact_lengths[k] for k in sorted(exact_lengths) if k >= 20
        },
        "by_scope": {scope: dict(values) for scope, values in sorted(scopes.items())},
    }


def renderer_evidence(rom: bytes) -> dict[str, Any]:
    sb = stock_base(rom)
    stock = rom[sb : sb + 0x800000]
    fixed_25_actual = stock[FIXED_25_CALL : FIXED_25_CALL + len(FIXED_25_CALL_BYTES)]
    return {
        "renderer_entry": "A000:07AC",
        "far_call_xrefs_exact_pattern": stock.count(TEXT_RENDER_FAR_CALL),
        "glyph_limit_compare": {
            "logical": f"{RENDERER_LIMIT_COMPARE:06X}",
            "expected_hex": RENDERER_LIMIT_COMPARE_BYTES.hex().upper(),
            "actual_hex": stock[
                RENDERER_LIMIT_COMPARE : RENDERER_LIMIT_COMPARE
                + len(RENDERER_LIMIT_COMPARE_BYTES)
            ].hex().upper(),
            "exact": stock[
                RENDERER_LIMIT_COMPARE : RENDERER_LIMIT_COMPARE
                + len(RENDERER_LIMIT_COMPARE_BYTES)
            ]
            == RENDERER_LIMIT_COMPARE_BYTES,
            "meaning": "cmp current_glyph_count, caller_limit; skip append when count >= limit",
        },
        "short_string_padding_compare": {
            "logical": f"{RENDERER_PAD_COMPARE:06X}",
            "expected_hex": RENDERER_PAD_COMPARE_BYTES.hex().upper(),
            "actual_hex": stock[
                RENDERER_PAD_COMPARE : RENDERER_PAD_COMPARE
                + len(RENDERER_PAD_COMPARE_BYTES)
            ].hex().upper(),
            "exact": stock[
                RENDERER_PAD_COMPARE : RENDERER_PAD_COMPARE
                + len(RENDERER_PAD_COMPARE_BYTES)
            ]
            == RENDERER_PAD_COMPARE_BYTES,
            "meaning": "compare glyph count with [BP+0A] while padding short strings",
        },
        "verified_25_cell_call": {
            "logical_start": f"{FIXED_25_CALL:06X}",
            "expected_hex": FIXED_25_CALL_BYTES.hex().upper(),
            "actual_hex": fixed_25_actual.hex().upper(),
            "exact": fixed_25_actual == FIXED_25_CALL_BYTES,
            "meaning": "push 0x19 (25 cells), then call A000:07AC with x=2",
        },
    }


def build_watchlist(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        row
        for row in rows
        if row["cells"] >= WATCHLIST_MIN_CELLS
        or row["growth_cells"] >= WATCHLIST_MIN_GROWTH
    ]
    return sorted(
        selected,
        key=lambda row: (
            -row["physical_full_frame_excess_cells"],
            -row["legacy_26_excess_cells"],
            -row["cells"],
            -row["growth_cells"],
            row["abs"],
        ),
    )


def write_watchlist(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "abs",
        "scope",
        "classification",
        "prefix_hex",
        "decode_mode",
        "cells",
        "render_px",
        "source_cells",
        "growth_cells",
        "physical_full_frame_excess_cells",
        "physical_full_frame_excess_px",
        "legacy_26_excess_cells",
        "legacy_26_excess_px",
        "conditional_25_excess_cells",
        "conditional_25_excess_px",
        "sheet_exact",
        "source_jp",
        "current_text",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fields})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()

    rom = args.rom.read_bytes()
    rows = load_rows(args.sheet)
    decoded = decode_current_rows(rom, rows)
    watchlist = build_watchlist(decoded)
    report = {
        "generated_by": "tools/audit_dialogue_screen_width.py",
        "mode": "read_only_current_tip_width_audit",
        "rom": str(args.rom.relative_to(ROOT)),
        "rom_size": len(rom),
        "rom_sha256": sha256(rom),
        "sheet": str(args.sheet.relative_to(ROOT)),
        "geometry": {
            "glyph_cell_px": GLYPH_CELL_PX,
            "framebuffer_width_px": FRAMEBUFFER_WIDTH_PX,
            "physical_full_frame_cells": PHYSICAL_FULL_FRAME_CELLS,
            "legacy_bank59_builder_guard_cells": LEGACY_EVENT_GUARD_CELLS,
            "conditional_fixed_field_cells": CONDITIONAL_FIXED_FIELD_CELLS,
        },
        "renderer_evidence": renderer_evidence(rom),
        "semantics": {
            "physical_gt_28": (
                "The visible text alone is wider than the complete 224px framebuffer if rendered "
                "as one unwrapped 8px-cell row. Runtime controls/reflow must still be checked."
            ),
            "legacy_gt_26": (
                "Exceeds the historical 26-cell bank59 translation guard. This is a project policy "
                "breach/risk indicator, not proof that every screen uses a 26-cell field."
            ),
            "conditional_gt_25": (
                "Would be clipped by a 25-cell renderer field. Static renderer inspection found at "
                "least one real 25-cell call, but this audit does not claim that every row uses it."
            ),
            "growth_ge_8": (
                "Korean visible width grew by at least eight cells versus the source row and deserves "
                "manual layout review even when it remains below a coarse global threshold."
            ),
            "scope_note": (
                "Population is the vetted 1,893-row runtime-facing reviewed set. A naive whole-bank "
                "zstring walk is intentionally rejected because it misclassifies binary/data areas."
            ),
        },
        "summary": group_counts(decoded),
        "physical_gt_28_rows": [
            row for row in decoded if row["cells"] > PHYSICAL_FULL_FRAME_CELLS
        ],
        "legacy_gt_26_rows": [
            row for row in decoded if row["cells"] > LEGACY_EVENT_GUARD_CELLS
        ],
        "legacy_eq_26_rows": [
            row for row in decoded if row["cells"] == LEGACY_EVENT_GUARD_CELLS
        ],
        "sheet_differences": [row for row in decoded if not row["sheet_exact"]],
        "watchlist_count": len(watchlist),
        "watchlist_csv": str(args.out_csv.relative_to(ROOT)),
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_watchlist(args.out_csv, watchlist)

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"watchlist={len(watchlist)} -> {args.out_csv}")
    print(f"report -> {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
