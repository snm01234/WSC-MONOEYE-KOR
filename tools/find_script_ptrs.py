#!/usr/bin/env python3
"""
Locate dialogue/script pointer tables and classify control-byte patterns
in segment 60+ text banks.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    bank_offset,
    find_rom,
    le16,
    load_rom,
    tokenize_script_payload,
)

TEXT_SEG_START = 0x60
TEXT_SEG_END = 0x6F  # inclusive scan window for text-like banks
PROG_SEG_START = 0x7A
PROG_SEG_END = 0x7F


def scan_pointer_tables(
    rom: bytearray,
    target_segs: range,
    search_segs: range,
    *,
    min_run: int = 8,
    max_delta: int = 0x400,
) -> List[dict]:
    """
    Find ascending LE16 offset runs whose companion bank byte equals a text segment,
    or plain ascending offsets that land on non-zero text payloads in a fixed bank.
    """
    hits: List[dict] = []

    # Pattern A: offset:segment far-ish pairs (off_lo off_hi seg 00?) spaced by 4
    for seg in search_segs:
        base = seg * BANK_SIZE
        bank = rom[base : base + BANK_SIZE]
        i = 0
        while i < BANK_SIZE - 8:
            run = []
            j = i
            prev = -1
            while j + 3 < BANK_SIZE:
                off = bank[j] | (bank[j + 1] << 8)
                tseg = bank[j + 2]
                pad = bank[j + 3]
                if tseg not in target_segs:
                    break
                if prev >= 0 and not (0 < off - prev <= max_delta):
                    break
                # Prefer near-zero pad, but allow small values
                if pad not in (0x00, 0xFF) and pad > 0x10:
                    break
                run.append({"offset": off, "segment": tseg, "entry_at": base + j})
                prev = off
                j += 4
            if len(run) >= min_run:
                hits.append(
                    {
                        "kind": "off16_seg8",
                        "table_at": f"{base + i:06X}",
                        "search_seg": f"{seg:02X}",
                        "length": len(run),
                        "first": run[0],
                        "last": run[-1],
                        "sample": run[:5],
                    }
                )
                i = j
            else:
                i += 2

    # Pattern B: pure LE16 ascending offsets into a known text bank (relative)
    for tseg in target_segs:
        tbase = tseg * BANK_SIZE
        for seg in search_segs:
            base = seg * BANK_SIZE
            bank = rom[base : base + BANK_SIZE]
            i = 0
            while i < BANK_SIZE - 4:
                run = []
                j = i
                prev = -1
                while j + 1 < BANK_SIZE:
                    off = bank[j] | (bank[j + 1] << 8)
                    abs_off = tbase + off
                    if abs_off >= len(rom):
                        break
                    if prev >= 0 and not (0 < off - prev <= max_delta):
                        break
                    # Must point at a plausible record start (non-empty until 00)
                    if rom[abs_off] == 0:
                        break
                    run.append({"offset": off, "abs": abs_off, "entry_at": base + j})
                    prev = off
                    j += 2
                if len(run) >= min_run:
                    # Score: how many pointed records look like text (have F0-FE or E0-E7)
                    score = 0
                    for ent in run[:32]:
                        payload = []
                        p = ent["abs"]
                        for _ in range(32):
                            if rom[p] == 0:
                                break
                            payload.append(rom[p])
                            p += 1
                        if any(0xF0 <= b <= 0xFE or 0xE0 <= b <= 0xE7 for b in payload):
                            score += 1
                    if score >= max(3, min_run // 4):
                        hits.append(
                            {
                                "kind": "off16_into_bank",
                                "target_seg": f"{tseg:02X}",
                                "table_at": f"{base + i:06X}",
                                "search_seg": f"{seg:02X}",
                                "length": len(run),
                                "score": score,
                                "first": run[0],
                                "last": run[-1],
                                "sample": run[:5],
                            }
                        )
                    i = j
                else:
                    i += 2

    # Deduplicate overlapping starts; drop the known dictionary pointer table
    filtered = []
    for h in hits:
        if h.get("table_at") == f"{bank_offset(SEG_DICT) + DICT_PTR_START:06X}":
            continue
        if h.get("search_seg") == f"{SEG_DICT:02X}" and h.get("kind") == "off16_into_bank":
            # 5F:7BCC is the compression dictionary, not a script table
            continue
        filtered.append(h)
    filtered.sort(key=lambda h: (-h["length"], h.get("score", 0), h["table_at"]))
    return filtered[:50]


def analyze_text_stream(rom: bytearray, tbl: Tbl, dictionary: Dictionary) -> dict:
    """Analyze null-separated records in banks 60-6F for control patterns."""
    lead_hist: Counter = Counter()
    bigram_hist: Counter = Counter()
    record_prefixes: Counter = Counter()
    samples: List[dict] = []

    for seg in range(TEXT_SEG_START, TEXT_SEG_END + 1):
        base = seg * BANK_SIZE
        bank = rom[base : base + BANK_SIZE]
        i = 0
        while i < BANK_SIZE:
            if bank[i] == 0:
                i += 1
                continue
            start = i
            while i < BANK_SIZE and bank[i] != 0:
                i += 1
            payload = bytes(bank[start:i])
            if not payload:
                continue
            lead_hist[payload[0]] += 1
            if len(payload) >= 2:
                bigram_hist[(payload[0], payload[1])] += 1
            # Structural prefix guess: 08 xx / 17 xx / 18 ...
            prefix = []
            j = 0
            while j < len(payload) and j < 6:
                b = payload[j]
                if b in (0x08, 0x17, 0x18) or (prefix and b < 0x04):
                    prefix.append(f"{b:02X}")
                    j += 1
                    # 08/17 often take a parameter byte
                    if b in (0x08, 0x17) and j < len(payload):
                        prefix.append(f"{payload[j]:02X}")
                        j += 1
                    continue
                break
            if prefix:
                record_prefixes[" ".join(prefix)] += 1

            if len(samples) < 40 and (0xF0 <= payload[0] <= 0xFE or 0x18 in payload[:4]):
                # Find text-ish region after structural bytes
                body_start = 0
                for k in range(min(6, len(payload))):
                    if payload[k] == 0x18:
                        body_start = k + 1
                        break
                body = payload[body_start:]
                decoded = dictionary.expand(body, tbl) if body else ""
                samples.append(
                    {
                        "abs": f"{base + start:06X}",
                        "raw": " ".join(f"{b:02X}" for b in payload[:40]),
                        "prefix": " ".join(prefix),
                        "decoded_body": decoded,
                    }
                )
            i += 1

    # Control-code hypothesis document
    conventions = {
        "00": "Record / string terminator",
        "08 xx": "Likely speaker / portrait / actor id (xx = parameter). Note: 08 also = 'は' in TBL when inside glyph stream.",
        "17 xx": "Likely window / box / event control with parameter",
        "18": "Likely start-of-printable dialogue body marker (also 'こ' in TBL)",
        "1D": "？ (question mark glyph) when in text body",
        "E0-E7 xx": "Two-byte kanji / extended glyph",
        "F0-FE yy": "Dictionary reference index ((lead-F0)<<8|yy)",
        "FF": "High frequency in text banks — treat as control or unused dict page; verify in debugger",
        "ambiguity": (
            "Bytes 08/17/18 are both structural controls at record heads AND "
            "kana glyphs inside expanded text. Distinguish by position: "
            "leading structural prefix vs post-0x18 body."
        ),
    }

    return {
        "lead_byte_top": {f"{b:02X}": n for b, n in lead_hist.most_common(24)},
        "bigram_top": {
            f"{a:02X} {b:02X}": n for (a, b), n in bigram_hist.most_common(24)
        },
        "prefix_top": dict(record_prefixes.most_common(30)),
        "samples": samples,
        "control_conventions": conventions,
    }


def crossref_dict_ptrs_in_program(rom: bytearray) -> List[dict]:
    """Search program banks for the constant 7BCC (dict pointer table start)."""
    needle = bytes([0xCC, 0x7B])  # LE of 0x7BCC
    found = []
    for seg in range(PROG_SEG_START, PROG_SEG_END + 1):
        base = seg * BANK_SIZE
        bank = bytes(rom[base : base + BANK_SIZE])
        start = 0
        while True:
            idx = bank.find(needle, start)
            if idx < 0:
                break
            found.append({"abs": f"{base + idx:06X}", "context": bank[idx : idx + 8].hex(" ")})
            start = idx + 1
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--tbl", type=Path, default=ROOT / "data" / "monoeye.tbl")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "script_ptrs.json")
    args = ap.parse_args()

    rom = load_rom(args.rom or find_rom(ROOT))
    tbl = Tbl.load(args.tbl)
    dictionary = Dictionary(rom)

    print("Scanning pointer tables...")
    ptr_hits = scan_pointer_tables(
        rom,
        range(TEXT_SEG_START, TEXT_SEG_END + 1),
        range(0x50, 0x80),
        min_run=8,
    )
    print(f"  pointer-table candidates: {len(ptr_hits)}")

    print("Analyzing text stream control patterns...")
    stream = analyze_text_stream(rom, tbl, dictionary)

    print("Searching program banks for dict ptr constant 7BCC...")
    dict_refs = crossref_dict_ptrs_in_program(rom)
    print(f"  hits: {len(dict_refs)}")

    report = {
        "pointer_table_candidates": ptr_hits,
        "text_stream": stream,
        "program_refs_to_7BCC": dict_refs,
        "dict_ptr_table": {
            "segment": "5F",
            "start": "7BCC",
            "end": "99B9",
            "count": dictionary.count,
            "status": "confirmed",
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Human-readable summary
    summary = ROOT / "out" / "script_ptrs.md"
    lines = [
        "# Script pointer & control-code findings",
        "",
        "## Confirmed dictionary pointers",
        f"- Table `5F:7BCC–99B9`, {dictionary.count} entries (LE16 offsets into seg 5F).",
        "",
        "## Control-code conventions (seg 60+ records)",
    ]
    for k, v in stream["control_conventions"].items():
        lines.append(f"- `{k}`: {v}")
    lines += ["", "## Top record prefixes", ""]
    for pref, n in list(stream["prefix_top"].items())[:15]:
        lines.append(f"- `{pref}` × {n}")
    lines += ["", "## Pointer table candidates (top 10)", ""]
    for hit in ptr_hits[:10]:
        lines.append(
            f"- `{hit['table_at']}` kind={hit['kind']} len={hit['length']} "
            f"search_seg={hit.get('search_seg')} score={hit.get('score', '-')}"
        )
    lines += ["", "## Sample decoded records", ""]
    for s in stream["samples"][:12]:
        lines.append(f"- `{s['abs']}` prefix=`{s['prefix']}`")
        lines.append(f"  - raw: `{s['raw']}`")
        lines.append(f"  - body: {s['decoded_body']}")
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    print(f"Wrote {summary}")


if __name__ == "__main__":
    main()
