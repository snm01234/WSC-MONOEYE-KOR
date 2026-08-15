#!/usr/bin/env python3
"""Spill-relocate fixed UI strings in bank 5F and patch LE16 pointer table."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hangul_marker import resolve_marker  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    find_rom,
    load_rom,
    read_encoded_z,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

# Spill into trailing FF padding of bank 5F (after dict/ext spill).
# NEVER use 0x3200–0x3662 — that overlaps the UI pointer table.
# Floor only — actual start is max(floor, end of live dict phrases).
SPILL_FLOOR = 0x5FADC4
SPILL_END = 0x5FFFF0
PTR_SCAN_START = 0x5F3400
PTR_SCAN_END = 0x5F3660
DICT_SPILL_FLOOR = 0x5F99BA


def discover_spill_start(rom: bytes | bytearray) -> int:
    """Place UI spill after any dictionary spill phrases already written."""
    d = Dictionary(rom)
    cursor = DICT_SPILL_FLOOR
    for p in d.ptrs:
        abs_p = 0x5F0000 + (p & 0xFFFF)
        if abs_p < DICT_SPILL_FLOOR or abs_p >= SPILL_END:
            continue
        end = abs_p
        while end < SPILL_END and rom[end] != 0:
            end += 1
        end += 1
        cursor = max(cursor, end)
    return max(SPILL_FLOOR, cursor)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--out-rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--base-rom", type=Path, default=None)
    ap.add_argument("--strings", type=Path, default=ROOT / "data/ui_spill_ko.json")
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument("--out-report", type=Path, default=ROOT / "out/patch/ui_spill_report.json")
    ap.add_argument(
        "--marker",
        type=lambda s: int(s, 16),
        default=None,
        help="override the installed marker (normally leave unset)",
    )
    args = ap.parse_args()

    spec = json.loads(args.strings.read_text(encoding="utf-8"))
    marker = args.marker or resolve_marker(
        spec.get("marker"), source=str(args.strings.name)
    )
    rom = bytearray(load_rom(args.rom))
    base = load_rom(args.base_rom) if args.base_rom else load_rom(find_rom(ROOT))
    tbl = Tbl.load(args.tbl)
    d_base = Dictionary(base)

    # Build pointer map: off16 -> list of pointer abs in bank 5F table region
    ptr_map: dict[int, list[int]] = {}
    for p in range(PTR_SCAN_START, PTR_SCAN_END, 2):
        off = rom[p] | (rom[p + 1] << 8)
        if 0x2E00 <= off < 0x3600:
            ptr_map.setdefault(off, []).append(p)

    spill_start = discover_spill_start(rom)
    cursor = spill_start
    applied = []
    skipped = []

    for row in spec["lines"]:
        abs_off = int(row["abs"], 16)
        if (abs_off >> 16) != 0x5F:
            skipped.append({"abs": row["abs"], "reason": "not_bank5F"})
            continue
        jp_expect = row["jp"]
        ko = normalize_ko_text(row["ko"])
        raw, _ = read_encoded_z(base, abs_off)
        got = d_base.expand(raw, tbl)
        if got != jp_expect:
            skipped.append(
                {"abs": row["abs"], "reason": "jp_mismatch", "expect": jp_expect, "got": got}
            )
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if enc is None:
            skipped.append({"abs": row["abs"], "reason": "encode_fail", "ko": ko})
            continue
        blob = enc + b"\x00"
        off16 = abs_off & 0xFFFF
        ptrs = ptr_map.get(off16, [])
        if not ptrs:
            # try inplace if fits
            if len(blob) <= len(raw) + 1:
                span = len(raw) + 1
                rom[abs_off : abs_off + span] = blob + bytes(span - len(blob))
                applied.append(
                    {
                        "abs": row["abs"],
                        "mode": "inplace",
                        "jp": jp_expect,
                        "ko": ko,
                        "ptrs": 0,
                    }
                )
            else:
                skipped.append({"abs": row["abs"], "reason": "no_pointer", "ko": ko})
            continue
        if cursor + len(blob) > SPILL_END:
            skipped.append({"abs": row["abs"], "reason": "spill_full", "ko": ko})
            continue
        new_off = cursor & 0xFFFF
        rom[cursor : cursor + len(blob)] = blob
        for p in ptrs:
            rom[p] = new_off & 0xFF
            rom[p + 1] = (new_off >> 8) & 0xFF
        # blank old string with 00 to avoid stale reads
        rom[abs_off : abs_off + len(raw) + 1] = bytes(len(raw) + 1)
        applied.append(
            {
                "abs": row["abs"],
                "mode": "spill",
                "new_abs": f"{cursor:06X}",
                "jp": jp_expect,
                "ko": ko,
                "ptrs": len(ptrs),
                "ptr_sample": [f"{p:06X}" for p in ptrs[:4]],
            }
        )
        cursor += len(blob)

    report = {
        "spill_start": f"{spill_start:06X}",
        "spill_end": f"{SPILL_END:06X}",
        "spill_used": cursor - spill_start,
        "applied": len(applied),
        "skipped": len(skipped),
        "applied_rows": applied,
        "skipped_rows": skipped,
        "checksum": f"{update_ws_checksum(rom):04X}",
    }
    args.out_rom.write_bytes(rom)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"UI spill OK | applied={report['applied']} skipped={report['skipped']} "
        f"spill_used={report['spill_used']} checksum={report['checksum']}"
    )
    for r in applied[:25]:
        print(
            f"  {r['mode']:7s} @{r['abs']} -> {r.get('new_abs','')} "
            f"ptrs={r['ptrs']} {r['jp']} => {r['ko']}"
        )
    reasons = {}
    for s in skipped:
        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
    print("skipped:", reasons)
    print(f"Wrote {args.out_rom}")


if __name__ == "__main__":
    main()
