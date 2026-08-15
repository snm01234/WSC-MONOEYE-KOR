#!/usr/bin/env python3
"""Analyze dictionary / script capacity for a full Korean translation."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_DATA_START,
    DICT_PTR_START,
    Dictionary,
    SEG_DICT,
    find_rom,
    load_rom,
    slice_bank,
)
from expand_dictionary import referenced_dict_closure  # noqa: E402


def body_bytes(hex_str: str) -> bytes:
    if not hex_str.strip():
        return b""
    return bytes(int(part, 16) for part in hex_str.split())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "capacity_report.json")
    args = ap.parse_args()

    rom = load_rom(args.rom or find_rom(ROOT))
    dictionary = Dictionary(rom)
    db_path = ROOT / "out" / "script" / "dialogue_db.json"
    if not db_path.exists():
        raise SystemExit("Run tools/extract_script.py first")
    db = json.loads(db_path.read_text(encoding="utf-8"))
    dialogue = db["dialogue"]

    dial_abs = {
        row["abs"] if isinstance(row["abs"], int) else int(row["abs"], 16)
        for row in dialogue
    }
    keep = referenced_dict_closure(rom, dictionary, exclude_script_abs=dial_abs)
    free_if_all = [index for index in range(dictionary.count) if index not in keep]

    bodies = []
    short = 0
    huge = 0
    for row in dialogue:
        body = body_bytes(row.get("body_hex", ""))
        if len(body) > 500:
            huge += 1
            continue
        bodies.append(len(body))
        if len(body) < 2:
            short += 1

    unique_jp = len({row["jp"] for row in dialogue})
    ptr_bytes = dictionary.count * 2
    phrase_budget = BANK_SIZE - DICT_DATA_START - ptr_bytes
    needed_bytes = sum(len(dictionary.raw_entry(i)) + 1 for i in keep)
    bank5f = slice_bank(rom, SEG_DICT)
    free_after_ptrs = BANK_SIZE - (DICT_PTR_START + ptr_bytes)

    text_pad = {}
    for seg in range(0x60, 0x70):
        bank = slice_bank(rom, seg)
        pad = 0
        for value in reversed(bank):
            if value in (0x00, 0xFF):
                pad += 1
            else:
                break
        text_pad[f"{seg:02X}"] = pad

    report = {
        "dict_count": dictionary.count,
        "max_dict_token_index": 0xEFF,
        "appendable_slots": 0xEFF + 1 - dictionary.count,
        "dialogue_lines": len(dialogue),
        "unique_jp": unique_jp,
        "bodies_shorter_than_2": short,
        "bodies_huge_skipped": huge,
        "body_len_avg": round(sum(bodies) / len(bodies), 2) if bodies else 0,
        "free_slots_if_all_dialogue_patched": len(free_if_all),
        "kept_jp_dict_entries": len(keep),
        "kept_jp_phrase_bytes": needed_bytes,
        "phrase_budget_if_ptrs_at_end": phrase_budget,
        "ko_phrase_budget_bytes": phrase_budget - needed_bytes,
        "free_bytes_after_stock_ptrs": free_after_ptrs,
        "text_bank_trailing_pad": text_pad,
        "text_bank_trailing_pad_total": sum(text_pad.values()),
        "limits": {
            "dict_tokens_addressable": 0xEFF + 1,
            "note": (
                "Full unique-line dump exceeds bank 5F. "
                "Expanded pipeline reclaims unused dict slots, "
                "rebuilds phrases, and compresses Korean with shared dict phrases "
                "so most lines stay within original record sizes."
            ),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = args.out.with_suffix(".md")
    lines = [
        "# Translation capacity report",
        "",
        f"- Dictionary entries: **{report['dict_count']}** / max token index `0xEFF`",
        f"- Dialogue lines: **{report['dialogue_lines']}** (unique JP **{report['unique_jp']}**)",
        f"- Slots reclaimable if all dialogue rewritten: **{report['free_slots_if_all_dialogue_patched']}**",
        f"- KO phrase budget after keeping nested JP dict: "
        f"**{report['ko_phrase_budget_bytes']}** bytes",
        f"- Bodies shorter than 2 bytes (cannot hold dict token): **{short}**",
        "",
        report["limits"]["note"],
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote {args.out}")
    print(f"Wrote {md}")


if __name__ == "__main__":
    main()
