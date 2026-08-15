#!/usr/bin/env python3
"""
Extract dialogue from text banks 60–6F into a translation database.

Splits structural control prefixes from printable bodies, expands dictionary
tokens, and writes JSONL + CSV for translators.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    find_rom,
    is_dict_token,
    is_kanji_lead,
    load_rom,
    read_encoded_z,
)
from script_translation_scope import script_graphics_reason  # noqa: E402

TEXT_SEG_FIRST = 0x60
TEXT_SEG_LAST = 0x6F


@dataclass
class ScriptRecord:
    id: str
    abs: int
    seg: int
    offset: int
    prefix_hex: str
    body_hex: str
    jp: str
    ko: str = ""
    notes: str = ""
    kind: str = "dialogue"  # dialogue | speaker | control | other


def split_prefix_body(payload: bytes) -> Tuple[bytes, bytes, str]:
    """
    Return (prefix, body, kind).

    Structural patterns (record head only):
      08 xx …            speaker / actor blocks (may lack 18)
      [01]               optional indent / fullwidth-space control
      17 xx [08 xx…] 18  window + dialogue

    A bare ``18`` is deliberately *not* a generic dialogue marker.  In the
    game table it is also the printable Japanese glyph ``こ`` and several
    continuation/body-only records legitimately start with that code unit.
    ``18`` is consumed only after an independently parsed ``08``/``17``
    control chain.  Route-specific callers that really use a standalone
    marker must describe it in ``dialogue_runtime_contracts.py`` instead of
    teaching this extraction helper another address-blind heuristic.

    Opening narration often uses: 08 xx 01 17 xx 18 <text>.
    Without consuming the 01, the 17/18 controls are misclassified as
    body bytes and decode as garbage kana (「がらこ」).
    """
    if not payload:
        return b"", b"", "other"

    i = 0
    prefix = bytearray()
    kind = "other"

    # Leading speaker tags: one or more 08 xx
    while i + 1 < len(payload) and payload[i] == 0x08:
        prefix.extend(payload[i : i + 2])
        i += 2
        kind = "speaker"

    # Optional indent byte before window/dialogue controls.
    if i < len(payload) and payload[i] == 0x01:
        # Only treat as control when a 17/18 window follows; otherwise it is
        # a real ideographic-space glyph at body start.
        if i + 1 < len(payload) and payload[i + 1] in (0x17, 0x18):
            prefix.append(0x01)
            i += 1

    # Window / event controls: 17 xx (xx may itself start nested 08)
    while i + 1 < len(payload) and payload[i] == 0x17:
        prefix.append(payload[i])
        prefix.append(payload[i + 1])
        i += 2
        kind = "control"
        # Some forms: 17 28 08 xx …
        if i + 1 < len(payload) and payload[i] == 0x08:
            prefix.extend(payload[i : i + 2])
            i += 2

    # Dialogue marker.  A standalone 18 is printable text; only a preceding
    # structural control chain makes this byte unambiguously non-visible.
    if prefix and i < len(payload) and payload[i] == 0x18:
        prefix.append(0x18)
        i += 1
        kind = "dialogue"
        return bytes(prefix), bytes(payload[i:]), kind

    # No 18: if we only ate 08-tags, treat remainder as other/control blob
    if prefix:
        return bytes(prefix), bytes(payload[i:]), kind

    # Bare text / continuation (starts with glyph or dict token)
    if is_dict_token(payload[0]) or is_kanji_lead(payload[0]) or payload[0] <= 0xDF:
        return b"", bytes(payload), "dialogue"

    return b"", bytes(payload), "other"


_CTRL_RE = re.compile(r"<[0-9A-Fa-f]+>")


def looks_like_jp(text: str) -> bool:
    if not text or _CTRL_RE.fullmatch(text):
        return False
    # At least one CJK / kana
    return any(
        "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" or ch in "……！？"
        for ch in text
    )


def extract_records(rom: bytearray, tbl: Tbl, dictionary: Dictionary) -> List[ScriptRecord]:
    records: List[ScriptRecord] = []
    for seg in range(TEXT_SEG_FIRST, TEXT_SEG_LAST + 1):
        base = seg * BANK_SIZE
        bank = rom[base : base + BANK_SIZE]
        i = 0
        seq = 0
        while i < BANK_SIZE:
            if bank[i] == 0:
                i += 1
                continue
            start = i
            # Speaker/control records are structurally ``08 actor_id 00``.
            # If actor_id happens to be F0-FF, the generic encoded-z reader
            # mistakes it for a dictionary lead and consumes the structural
            # NUL as the token trail. That swallowed the immediately following
            # dialogue record (e.g. 60497E after ``08 F0 00``). Honor the
            # control-record boundary before applying text-token semantics.
            if start + 2 < BANK_SIZE and bank[start] == 0x08 and bank[start + 2] == 0:
                payload = bytes(bank[start : start + 2])
                terminator = start + 2
            else:
                payload, terminator = read_encoded_z(bank, start, BANK_SIZE - start)
            i = terminator
            prefix, body, kind = split_prefix_body(payload)
            jp = dictionary.expand(body, tbl) if body else ""
            # Skip empty / non-linguistic bodies for the translation sheet
            if kind == "dialogue" and not looks_like_jp(jp) and not body:
                i += 1
                continue
            rid = f"{seg:02X}_{start:04X}_{seq:04d}"
            records.append(
                ScriptRecord(
                    id=rid,
                    abs=base + start,
                    seg=seg,
                    offset=start,
                    prefix_hex=" ".join(f"{b:02X}" for b in prefix),
                    body_hex=" ".join(f"{b:02X}" for b in body),
                    jp=jp,
                    kind=kind,
                )
            )
            seq += 1
            i += 1
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--tbl", type=Path, default=ROOT / "data" / "monoeye.tbl")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "out" / "script")
    args = ap.parse_args()

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    rom = load_rom(args.rom or find_rom(ROOT))
    tbl = Tbl.load(args.tbl)
    dictionary = Dictionary(rom)
    records = extract_records(rom, tbl, dictionary)

    excluded_graphics = [
        r for r in records if script_graphics_reason(r.abs) is not None
    ]
    dialogue = [
        r
        for r in records
        if r.kind == "dialogue"
        and looks_like_jp(r.jp)
        and script_graphics_reason(r.abs) is None
    ]
    jsonl_path = out / "script_all.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    csv_path = out / "translation_sheet.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["id", "abs", "kind", "prefix_hex", "jp", "ko", "notes", "body_hex"],
        )
        w.writeheader()
        for r in dialogue:
            w.writerow(
                {
                    "id": r.id,
                    "abs": f"{r.abs:06X}",
                    "kind": r.kind,
                    "prefix_hex": r.prefix_hex,
                    "jp": r.jp,
                    "ko": "",
                    "notes": "",
                    "body_hex": r.body_hex,
                }
            )

    # Compact JSON DB for tooling
    db = {
        "rom": str(args.rom or find_rom(ROOT).name),
        "record_count": len(records),
        "dialogue_count": len(dialogue),
        "dialogue": [asdict(r) for r in dialogue],
    }
    db_path = out / "dialogue_db.json"
    db_path.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

    # Preview first lines
    preview = out / "dialogue_preview.txt"
    with preview.open("w", encoding="utf-8") as f:
        for r in dialogue[:80]:
            f.write(f"[{r.id}] @{r.abs:06X}  {r.prefix_hex}\n")
            f.write(f"  JP: {r.jp}\n")
            f.write(f"  RAW: {r.body_hex}\n\n")

    stats = {
        "records": len(records),
        "dialogue": len(dialogue),
        "excluded_script_graphics_block": len(excluded_graphics),
        "by_kind": {},
    }
    for r in records:
        stats["by_kind"][r.kind] = stats["by_kind"].get(r.kind, 0) + 1
    (out / "extract_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Records: {len(records)}  dialogue: {len(dialogue)}")
    print(f"Wrote {jsonl_path}")
    print(f"Wrote {csv_path}")
    print(f"Wrote {db_path}")
    print(f"Wrote {preview}")
    for r in dialogue[:8]:
        try:
            print(f"  @{r.abs:06X} {r.jp}")
        except UnicodeEncodeError:
            print(f"  @{r.abs:06X} {r.jp.encode('unicode_escape').decode()}")


if __name__ == "__main__":
    main()
