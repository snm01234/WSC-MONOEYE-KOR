#!/usr/bin/env python3
"""
Build a larger translation batch for capacity testing.

Takes dialogue rows from dialogue_db.json and fills Korean placeholders using
the Hangul glyph inventory (or seed translations when abs matches).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def placeholder_ko(jp: str, syllables: str, index: int) -> str:
    """Build a deterministic Hangul stand-in of similar visible length."""
    target = max(2, min(len(jp), 24))
    chars = []
    base = index * 7
    for offset in range(target):
        chars.append(syllables[(base + offset) % len(syllables)])
    # Keep common game punctuation when present on the JP side.
    for mark in ("！", "？", "。", "、", "……"):
        if mark in jp and mark not in "".join(chars):
            chars.append(mark)
            break
    return "".join(chars)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument(
        "--db",
        type=Path,
        default=ROOT / "out" / "script" / "dialogue_db.json",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data" / "translations_seed.json",
    )
    ap.add_argument(
        "--char-map",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_char_map.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "translations_batch.json",
    )
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit("dialogue_db.json missing; run extract_script.py")

    db = json.loads(args.db.read_text(encoding="utf-8"))
    seed_lines = []
    seed_by_abs = {}
    if args.seed.exists():
        seed_lines = json.loads(args.seed.read_text(encoding="utf-8"))["lines"]
        seed_by_abs = {row["abs"].upper(): row["ko"] for row in seed_lines}

    syllables = "가나다라마바사아자차카타파하거너더러머버서어저"
    if args.char_map.exists():
        mapping = json.loads(args.char_map.read_text(encoding="utf-8"))
        new_chars = [ch for ch in mapping.get("new_chars", []) if "가" <= ch <= "힣"]
        if len(new_chars) >= 8:
            syllables = "".join(new_chars)

    lines = []
    for row in db["dialogue"]:
        if len(lines) >= args.count:
            break
        abs_hex = f"{row['abs']:06X}" if isinstance(row["abs"], int) else str(row["abs"]).upper()
        body_hex = row.get("body_hex") or ""
        body_len = len(body_hex.split()) if body_hex.strip() else 0
        if body_len < 2:
            continue
        if len(body_hex) > 2000:
            continue
        ko = seed_by_abs.get(abs_hex)
        if ko is None:
            ko = placeholder_ko(row["jp"], syllables, len(lines))
        lines.append({"abs": abs_hex, "jp": row["jp"], "ko": ko})

    payload = {
        "description": (
            f"Capacity-test batch ({len(lines)} lines). "
            "Seed abs keep real KO; others use Hangul placeholders."
        ),
        "lines": lines,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
