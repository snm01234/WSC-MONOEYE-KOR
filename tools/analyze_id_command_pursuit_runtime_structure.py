#!/usr/bin/env python3
"""Recheck the 追撃! plaque from the user's stock/main runtime captures.

The two supplied captures are exact 6x nearest-neighbour WonderSwan frames.
After reducing the 48x16 plaque to native pixels and quantizing to the measured
OBJ palette, display tile columns 1, 2, and 4 are byte-identical between stock
and main while column 3 (both top and bottom 8x8 tiles) changes.  Columns 0 and
5 contain transparent edge pixels and therefore naturally differ with the map
background.

This script keeps the quantized 32-byte tile payloads from those captures as
read-only evidence and ranks their closest ROM sources.  It does not patch ROM.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"

EXPECTED_MAIN_SHA = "cef2d40d7a0568e3add4025d8ebc6f5e6340f0a2b545a5f88decc6d28e3375f5"
PURSUIT = 0x4CC32A
BODY_BYTES = 0x140

# Quantized native 8x8 tiles from the user's two 6x captures.  The plaque is
# 48x16 on screen.  Keys are (display_column, row), row 0=top, 1=bottom.
CURRENT = {
    (1, 0): "ffffffffcccccceeeecccefefeecefeeefeeffffefeefeeeeeeefeeeffeeffff",
    (2, 0): "ffffffffccccccccccccceeeeecccecffcecceecefecccefefecccefffecccee",
    (3, 0): "ffffffffffffffffeeeffeefeeeeeeeffeeffeefeeeeeeefeefeeeefeffffeef",
    (4, 0): "ffffffffeeccccccfecccceefeeecceccfceccefeeeeccefffecccefceecccef",
    (1, 1): "efeefeeeefeeffffefeefeeeefeefeeeefeefffffefeeeeeceefffffeeeeeeee",
    (2, 1): "eeeccecfffecceeeefecccceefecccceffeccceeeeeeececffcdeceeeeeeefff",
    (3, 1): "ffffffffeeeeeeefeeeeeeeffffffeefccccfeefccccffffccccccccffffffff",
    (4, 1): "ffecccefeeeccceceeccccdececccccdeeeeccdeffceccefeeeeccecffffffde",
}
STOCK_EDGE_CAPTURE = {
    (0, 0): "aabbcbffabcffbccacfbccceafbccccecfcccccebbccccccfbccccccfbccccce",
    (5, 0): "ffbc0000ccbffc00eeccbfc0cecccbf0feccccfcfeccccbbfeccccbffeccccbf",
    (0, 1): "fbcccccefbccccccbbcccccccfccccccbfbcccccacfbccceaacffbce0000cbfe",
    (5, 1): "feccccbfceccccbfedccccbbdcccccfcedcccbf0feccbfc0febffc00edbc0000",
}
STOCK_CAPTURE = {
    (1, 0): "ffffffffcccccceeeecccefefeecefeeefeeffffefeefeeeeeeefeeeffeeffff",
    (2, 0): "ffffffffccccccccccccceeeeecccecffcecceecefecccefefecccefffecccee",
    (3, 0): "ffffffffeeeceeeeefeeefffffffefeecfccefeeefefefeeffffeefeefeeeeef",
    (4, 0): "ffffffffeeccccccfecccceefeeecceccfceccefeeeeccefffecccefceecccef",
    (1, 1): "efeefeeeefeeffffefeefeeeefeefeeeefeefffffefeeeeeceefffffeeeeeeee",
    (2, 1): "eeeccecfffecceeeefecccceefecccceffeccceeeeeeececffcdeceeeeeeefff",
    (3, 1): "fffefefeeceeeeeeeeeefeeecfffffffeeeefeeeffffffffeeeefeeefffeeedf",
    (4, 1): "ffecccefeeeccceceeccccdececccccdeeeeccdeffceccefeeeeccecffffffde",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nibble_distance(a: bytes, b: bytes) -> int:
    return sum(((x >> 4) != (y >> 4)) + ((x & 15) != (y & 15)) for x, y in zip(a, b))


def best_hits(data: bytes, target: bytes, logical_base: int, count: int = 12) -> list[dict]:
    best: list[tuple[int, int]] = []
    for off in range(0, len(data) - len(target) + 1):
        dist = nibble_distance(data[off : off + len(target)], target)
        if len(best) < count:
            best.append((dist, off))
            best.sort()
        elif dist < best[-1][0]:
            best[-1] = (dist, off)
            best.sort()
    return [{"distance_nibbles": d, "logical": f"{logical_base + off:06X}"} for d, off in best]


def expected_source_for_display_tile(col: int, row: int) -> int:
    """48x16 display = external left cap + stored 5x2 pursuit body."""
    if not 1 <= col <= 5:
        raise ValueError(col)
    body_col = col - 1
    tile_index = body_col + (5 if row else 0)
    return PURSUIT + tile_index * 0x20


def main() -> int:
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    stock = STOCK.read_bytes()
    main = MAIN.read_bytes()
    base = len(main) - len(stock)
    if base != 0x800000:
        raise RuntimeError(f"unexpected stock base {base:#x}")
    if sha(main) != EXPECTED_MAIN_SHA:
        raise RuntimeError(f"main drift: {sha(main)}")

    stock_bank = stock[0x4C0000:0x4D0000]
    main_bank = main[base + 0x4C0000 : base + 0x4D0000]
    pursuit_stock = stock[PURSUIT : PURSUIT + BODY_BYTES]
    pursuit_main = main[base + PURSUIT : base + PURSUIT + BODY_BYTES]

    rows = []
    for key in sorted(STOCK_CAPTURE):
        col, row = key
        s_cap = bytes.fromhex(STOCK_CAPTURE[key])
        m_cap = bytes.fromhex(CURRENT[key])
        expected = expected_source_for_display_tile(col, row)
        expected_stock = stock[expected : expected + 0x20]
        expected_main = main[base + expected : base + expected + 0x20]
        rows.append(
            {
                "display_tile": [col, row],
                "capture_stock_equals_current": s_cap == m_cap,
                "expected_pursuit_source": f"{expected:06X}",
                "expected_source_main_stock_exact": expected_stock == expected_main,
                "stock_capture_vs_expected_source_distance": nibble_distance(s_cap, expected_stock),
                "current_capture_vs_expected_source_distance": nibble_distance(m_cap, expected_main),
                "stock_capture_best_stock_hits": best_hits(stock_bank, s_cap, 0x4C0000, 8),
                "current_capture_best_main_hits": best_hits(main_bank, m_cap, 0x4C0000, 8),
            }
        )

    changed_columns = sorted(
        {col for (col, row) in STOCK_CAPTURE if STOCK_CAPTURE[(col, row)] != CURRENT[(col, row)]}
    )
    edge_rows = []
    for key in sorted(STOCK_EDGE_CAPTURE):
        target = bytes.fromhex(STOCK_EDGE_CAPTURE[key])
        edge_rows.append({
            "display_tile": list(key),
            "stock_capture_best_stock_hits": best_hits(stock_bank, target, 0x4C0000, 8),
        })

    report = {
        "schema_version": 1,
        "read_only": True,
        "main_sha256": sha(main),
        "pursuit_source": {
            "logical": "4CC32A-4CC469",
            "bytes": BODY_BYTES,
            "main_equals_stock": pursuit_main == pursuit_stock,
        },
        "runtime_capture_geometry": {
            "stock_capture_bbox_scaled": [361, 120, 649, 216],
            "main_capture_bbox_scaled": [649, 336, 937, 432],
            "scale": 6,
            "native_display": [48, 16],
            "inference": "the prior 40x16 display classification is impossible; 追撃! is visibly 48x16",
        },
        "capture_tile_comparison": {
            "opaque_display_columns_checked": [1, 2, 3, 4],
            "changed_columns": changed_columns,
            "only_column_3_changed_top_and_bottom": changed_columns == [3],
            "meaning": "a single 8x16 runtime tile column changed; this is structural corruption, not background readability",
        },
        "corrected_storage_model": {
            "display": "48x16 = external shared left cap 8x16 + stored 40x16 body",
            "stored_body": "4CC32A-4CC469, 5x2 tiles",
            "display_column_3_expected_source_top": f"{expected_source_for_display_tile(3, 0):06X}",
            "display_column_3_expected_source_bottom": f"{expected_source_for_display_tile(3, 1):06X}",
        },
        "tiles": rows,
        "stock_black_edge_tiles": edge_rows,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
