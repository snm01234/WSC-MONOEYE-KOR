#!/usr/bin/env python3
"""Discover how text-bank record offsets are referenced across the ROM."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import BANK_SIZE, find_rom, load_rom, read_encoded_z, slice_bank


def list_record_offsets(rom: bytearray, segment: int) -> list[int]:
    bank = slice_bank(rom, segment)
    offsets = []
    cursor = 0
    while cursor < len(bank):
        if bank[cursor] == 0:
            cursor += 1
            continue
        _payload, terminator = read_encoded_z(bank, cursor, len(bank) - cursor)
        offsets.append(cursor)
        cursor = terminator + 1
    return offsets


def scan_refs_for_offsets(
    rom: bytearray,
    segment: int,
    offsets: list[int],
    *,
    search_lo: int = 0x40,
    search_hi: int = 0x7F,
) -> dict:
    wanted = set(offsets)
    hits = {
        "off16": [],
        "off16_seg8": [],
        "off16_00_seg8": [],
        "seg8_off16": [],
        "abs24_le": [],
        "abs24_be": [],
    }

    for sseg in range(search_lo, search_hi + 1):
        if sseg == segment:
            continue
        base = sseg * BANK_SIZE
        bank = rom[base : base + BANK_SIZE]
        i = 0
        while i < BANK_SIZE - 1:
            off = bank[i] | (bank[i + 1] << 8)
            if off in wanted:
                # off16 alone
                hits["off16"].append(
                    {
                        "at": f"{base + i:06X}",
                        "search_seg": f"{sseg:02X}",
                        "off": f"{off:04X}",
                        "ctx": bank[max(0, i - 2) : i + 6].hex(" "),
                    }
                )
                # off16 + seg
                if i + 2 < BANK_SIZE and bank[i + 2] == segment:
                    hits["off16_seg8"].append(
                        {
                            "at": f"{base + i:06X}",
                            "search_seg": f"{sseg:02X}",
                            "off": f"{off:04X}",
                            "ctx": bank[i : i + 4].hex(" "),
                        }
                    )
                # off16 + 00 + seg
                if (
                    i + 3 < BANK_SIZE
                    and bank[i + 2] == 0x00
                    and bank[i + 3] == segment
                ):
                    hits["off16_00_seg8"].append(
                        {
                            "at": f"{base + i:06X}",
                            "search_seg": f"{sseg:02X}",
                            "off": f"{off:04X}",
                            "ctx": bank[i : i + 5].hex(" "),
                        }
                    )
                # seg + off16
                if i >= 1 and bank[i - 1] == segment:
                    hits["seg8_off16"].append(
                        {
                            "at": f"{base + i - 1:06X}",
                            "search_seg": f"{sseg:02X}",
                            "off": f"{off:04X}",
                            "ctx": bank[i - 1 : i + 3].hex(" "),
                        }
                    )
            i += 1

        # absolute 24-bit forms
        i = 0
        while i < BANK_SIZE - 2:
            # LE: off_lo off_hi seg
            off = bank[i] | (bank[i + 1] << 8)
            seg = bank[i + 2]
            if seg == segment and off in wanted:
                hits["abs24_le"].append(
                    {
                        "at": f"{base + i:06X}",
                        "search_seg": f"{sseg:02X}",
                        "off": f"{off:04X}",
                        "ctx": bank[i : i + 4].hex(" "),
                    }
                )
            # BE-ish: seg off_hi off_lo ? uncommon
            seg = bank[i]
            off = bank[i + 2] | (bank[i + 1] << 8)
            if seg == segment and off in wanted:
                hits["abs24_be"].append(
                    {
                        "at": f"{base + i:06X}",
                        "search_seg": f"{sseg:02X}",
                        "off": f"{off:04X}",
                        "ctx": bank[i : i + 4].hex(" "),
                    }
                )
            i += 1

    # Deduplicate and summarize
    summary = {}
    for kind, rows in hits.items():
        # collapse identical addresses
        uniq = {}
        for row in rows:
            uniq[row["at"]] = row
        rows = list(uniq.values())
        by_off = Counter(row["off"] for row in rows)
        summary[kind] = {
            "count": len(rows),
            "unique_offsets_hit": len(by_off),
            "top_offsets": by_off.most_common(12),
            "samples": rows[:20],
        }
    return summary


def analyze_opening_chain(rom: bytearray) -> dict:
    """Focus on early bank 60 dialogue that the seed patch uses."""
    targets = [0x0005, 0x0019, 0x0027, 0x0036, 0x004E, 0x0067, 0x0070, 0x01C5]
    return scan_refs_for_offsets(rom, 0x60, targets)


def analyze_candidate_tables(rom: bytearray) -> list[dict]:
    """Validate pointer-table candidates from find_script_ptrs against real records."""
    report_path = ROOT / "out" / "script_ptrs.json"
    if not report_path.exists():
        return []
    report = json.loads(report_path.read_text(encoding="utf-8"))
    validated = []
    for hit in report.get("pointer_table_candidates", [])[:30]:
        kind = hit["kind"]
        table_at = int(hit["table_at"], 16)
        length = hit["length"]
        if kind == "off16_into_bank":
            tseg = int(hit["target_seg"], 16)
            records = set(list_record_offsets(rom, tseg))
            matched = 0
            sample = []
            for i in range(min(length, 64)):
                abs_i = table_at + i * 2
                if abs_i + 1 >= len(rom):
                    break
                off = rom[abs_i] | (rom[abs_i + 1] << 8)
                ok = off in records
                matched += int(ok)
                if len(sample) < 8:
                    sample.append({"off": f"{off:04X}", "record": ok})
            validated.append(
                {
                    **hit,
                    "record_match": matched,
                    "record_match_ratio": round(matched / max(1, min(length, 64)), 3),
                    "sample_checks": sample,
                }
            )
        elif kind == "off16_seg8":
            matched = 0
            sample = []
            for i in range(min(length, 64)):
                abs_i = table_at + i * 4
                if abs_i + 2 >= len(rom):
                    break
                off = rom[abs_i] | (rom[abs_i + 1] << 8)
                tseg = rom[abs_i + 2]
                if not (0x60 <= tseg <= 0x6F):
                    continue
                records = set(list_record_offsets(rom, tseg))
                ok = off in records
                matched += int(ok)
                if len(sample) < 8:
                    sample.append(
                        {"off": f"{off:04X}", "seg": f"{tseg:02X}", "record": ok}
                    )
            validated.append(
                {
                    **hit,
                    "record_match": matched,
                    "sample_checks": sample,
                }
            )
    validated.sort(key=lambda row: (-row.get("record_match", 0), -row.get("length", 0)))
    return validated


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "spill_pointer_analysis.json")
    args = ap.parse_args()

    rom = load_rom(args.rom or find_rom(ROOT))
    opening = analyze_opening_chain(rom)

    # Broader: first 200 records of bank 60
    bank60 = list_record_offsets(rom, 0x60)[:200]
    bank60_refs = scan_refs_for_offsets(rom, 0x60, bank60)

    tables = analyze_candidate_tables(rom)

    # Cross-bank sequential script hypothesis: many games store next-record
    # relative distances only inside event bytecode. Search for event banks
    # that contain dense off16 runs into bank 60 records.
    dense = []
    records60 = set(list_record_offsets(rom, 0x60))
    for sseg in range(0x50, 0x80):
        if sseg in (0x5F, 0x60):
            continue
        base = sseg * BANK_SIZE
        bank = rom[base : base + BANK_SIZE]
        i = 0
        while i < BANK_SIZE - 4:
            run = []
            j = i
            prev = -1
            while j + 1 < BANK_SIZE:
                off = bank[j] | (bank[j + 1] << 8)
                if off not in records60:
                    break
                if prev >= 0 and not (0 < off - prev <= 0x80):
                    break
                run.append(off)
                prev = off
                j += 2
            if len(run) >= 8:
                dense.append(
                    {
                        "table_at": f"{base + i:06X}",
                        "search_seg": f"{sseg:02X}",
                        "length": len(run),
                        "first": f"{run[0]:04X}",
                        "last": f"{run[-1]:04X}",
                    }
                )
                i = j
            else:
                i += 2
    dense.sort(key=lambda row: -row["length"])

    report = {
        "opening_targets": opening,
        "bank60_first200": {
            kind: {
                "count": data["count"],
                "unique_offsets_hit": data["unique_offsets_hit"],
                "top_offsets": data["top_offsets"],
                "samples": data["samples"][:8],
            }
            for kind, data in bank60_refs.items()
        },
        "validated_tables": tables[:20],
        "dense_off16_runs_into_bank60": dense[:20],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = args.out.with_suffix(".md")
    lines = [
        "# Spill pointer format analysis",
        "",
        "## Opening dialogue refs (bank 60)",
    ]
    for kind, data in opening.items():
        lines.append(
            f"- `{kind}`: count={data['count']} unique_offsets={data['unique_offsets_hit']}"
        )
        for sample in data["samples"][:5]:
            lines.append(
                f"  - `{sample['at']}` off=`{sample['off']}` ctx=`{sample['ctx']}`"
            )
    lines += ["", "## Dense off16 runs into bank 60 records", ""]
    for row in dense[:10]:
        lines.append(
            f"- `{row['table_at']}` seg={row['search_seg']} len={row['length']} "
            f"{row['first']}..{row['last']}"
        )
    lines += ["", "## Validated pointer-table candidates", ""]
    for row in tables[:10]:
        lines.append(
            f"- `{row['table_at']}` kind={row['kind']} len={row['length']} "
            f"record_match={row.get('record_match')}"
        )
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    print(f"Wrote {md}")
    for kind, data in opening.items():
        print(f"{kind}: {data['count']} hits / {data['unique_offsets_hit']} offsets")


if __name__ == "__main__":
    main()
