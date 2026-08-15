#!/usr/bin/env python3
"""Analyze Hangul glyph code-space budget for the Mono-Eye font table."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hangul_allocator import (  # noqa: E402
    EXTENDED_END,
    HANGUL_PRIMARY_END,
    HANGUL_PRIMARY_START,
    allocate_hangul_codes,
    scan_extended_code_usage,
)
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    compact_font_file_offset,
    find_rom,
    load_rom,
    text_code_to_glyph_index,
)


def collect_hangul_from_sources() -> set[str]:
    chars: set[str] = set()

    def add_text(text: str) -> None:
        for ch in text.replace(" ", "　"):
            if "가" <= ch <= "힣":
                chars.add(ch)

    seed = ROOT / "data" / "translations_seed.json"
    if seed.exists():
        payload = json.loads(seed.read_text(encoding="utf-8"))
        for line in payload.get("lines", []):
            add_text(line.get("ko", ""))

    cache = ROOT / "out" / "script" / "excel_translate_cache.json"
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
        entries = raw.get("entries", raw)
        if isinstance(entries, dict):
            for ko in entries.values():
                if isinstance(ko, str):
                    add_text(ko)

    batch = ROOT / "data" / "translations_batch.json"
    if batch.exists():
        payload = json.loads(batch.read_text(encoding="utf-8"))
        for line in payload.get("lines", []):
            add_text(line.get("ko", ""))

    map_path = ROOT / "out" / "patch" / "hangul_char_map.json"
    if map_path.exists():
        mapping = json.loads(map_path.read_text(encoding="utf-8"))
        for ch in mapping.get("new_chars", []):
            if "가" <= ch <= "힣":
                chars.add(ch)
    return chars


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "font_budget_report.json")
    ap.add_argument("--tbl", type=Path, default=ROOT / "data" / "monoeye.tbl")
    ap.add_argument("--rom", type=Path, default=None)
    args = ap.parse_args()

    primary_cap = HANGUL_PRIMARY_END - HANGUL_PRIMARY_START + 1
    chars = sorted(collect_hangul_from_sources())
    used = len(chars)

    rom = load_rom(args.rom or find_rom(ROOT))
    base_tbl = Tbl.load(args.tbl)
    usage = scan_extended_code_usage(rom, Dictionary(rom))
    alloc = allocate_hangul_codes(chars, base_tbl, usage)

    report = {
        "primary_range": f"{HANGUL_PRIMARY_START:04X}-{HANGUL_PRIMARY_END:04X}",
        "primary_capacity": primary_cap,
        "extended_cap": f"{EXTENDED_END:04X}",
        "unique_hangul_seen_so_far": used,
        "primary_remaining": primary_cap - used,
        "fills_primary_ratio": round(used / primary_cap, 4) if primary_cap else 0,
        "allocator_pool_counts": alloc.pool_counts,
        "allocator_overflow": len(alloc.overflow_chars),
        "reclaimed_jp_slots": len(alloc.reused_jp_codes),
        "glyph_index_start": text_code_to_glyph_index(HANGUL_PRIMARY_START),
        "file_offset_first": f"0x{compact_font_file_offset(HANGUL_PRIMARY_START):06X}",
        "notes": [
            "Primary Hangul window is E740–E7FF (192 slots).",
            "Overflow uses unused/unassigned E000–E73D codes, then E800–EFFF.",
            "is_kanji_lead accepts E0–EF; F0–FE remain dictionary tokens.",
            "Rerun after full sheet fill; use build_hangul_font.py --by-frequency.",
        ],
        "sample_chars": "".join(chars[:80]),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = args.out.with_suffix(".md")
    md.write_text(
        "\n".join(
            [
                "# Hangul font budget",
                "",
                f"- Primary range: `{report['primary_range']}` (**{primary_cap}** slots)",
                f"- Unique Hangul seen so far: **{used}**",
                f"- Primary remaining (naive): **{primary_cap - used}**",
                f"- Allocator pools: `{alloc.pool_counts}`",
                f"- Allocator overflow: **{len(alloc.overflow_chars)}**",
                f"- Reclaimed JP slots: **{len(alloc.reused_jp_codes)}**",
                "",
                *report["notes"],
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
