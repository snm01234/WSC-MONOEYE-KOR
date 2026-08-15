#!/usr/bin/env python3
"""Plan Hangul code-space overflow beyond E740–E7FF (192 slots)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_font_budget import collect_hangul_from_sources  # noqa: E402
from build_hangul_font import HANGUL_CODE_START  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    compact_font_file_offset,
    find_rom,
    load_rom,
    slice_bank,
    text_code_to_glyph_index,
)


PRIMARY_END = 0xE7FF
# Experimental extension: same glyph formula continues for codes >= E000.
EXTENDED_END = 0xEEFF


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "font_overflow_plan.json")
    args = ap.parse_args()

    chars = collect_hangul_from_sources()
    primary_cap = PRIMARY_END - HANGUL_CODE_START + 1
    need = len(chars)
    overflow = max(0, need - primary_cap)

    rom = load_rom(find_rom(ROOT))
    # Probe whether glyph records after E7FF look unused (zeros/FF).
    free_after = 0
    blocked = []
    for code in range(PRIMARY_END + 1, min(EXTENDED_END, PRIMARY_END + 1 + overflow + 64)):
        off = compact_font_file_offset(code)
        if off // BANK_SIZE != 0x40:
            blocked.append({"code": f"{code:04X}", "reason": "leaves bank 40"})
            break
        rec = bytes(rom[off : off + 16])
        if rec in (b"\x00" * 16, b"\xFF" * 16):
            free_after += 1
        else:
            blocked.append(
                {
                    "code": f"{code:04X}",
                    "reason": "nonempty glyph record",
                    "offset": f"0x{off:06X}",
                    "sample": rec[:8].hex(" "),
                }
            )
            if len(blocked) >= 8:
                break

    plan = {
        "unique_hangul": need,
        "primary_range": f"{HANGUL_CODE_START:04X}-{PRIMARY_END:04X}",
        "primary_capacity": primary_cap,
        "overflow_needed": overflow,
        "extension_candidate_range": f"{PRIMARY_END+1:04X}-{EXTENDED_END:04X}",
        "free_looking_records_probed": free_after,
        "blocked_samples": blocked,
        "runtime_notes": [
            "Glyph index formula at 7A:0610 / 7A:0768 is: if code>=E000 then code-DF20.",
            "No hard E7FF ceiling was found next to that formula, but script lead-byte parsing may still be E0-E7 only.",
            "Before using E8xx in-game, verify the text decoder accepts lead>=E8 or patch is_kanji_lead equivalent.",
            "Alternative if E8 is rejected: recycle rare JP glyphs inside E000-E73D, or dual fonts by scene.",
        ],
        "recommended_next_steps": [
            "Keep assigning E740-E7FF to the highest-frequency Hangul first.",
            "Add a frequency-ranked glyph allocator in build_hangul_font.py.",
            "Prototype one E8xx glyph in a test ROM and check BizHawk rendering.",
            "If E8xx fails, implement JP-slot recycling for rare kanji.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    md = args.out.with_suffix(".md")
    md.write_text(
        "\n".join(
            [
                "# Hangul font overflow plan",
                "",
                f"- Unique Hangul seen: **{need}**",
                f"- Primary slots `E740-E7FF`: **{primary_cap}**",
                f"- Overflow needed: **{overflow}**",
                f"- Free-looking records after E7FF (probe): **{free_after}**",
                "",
                "## Recommended next steps",
                *[f"- {step}" for step in plan["recommended_next_steps"]],
                "",
                "## Runtime notes",
                *[f"- {note}" for note in plan["runtime_notes"]],
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
