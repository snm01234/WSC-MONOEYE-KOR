#!/usr/bin/env python3
"""
Statically verify Data Crystal TBL against the in-ROM dictionary.

Full runtime confirmation still needs an emulator watch on RAM 016AE;
this script validates that dictionary phrases decode to coherent Japanese
and that encode→decode round-trips for mapped characters.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from monoeye_rom import Dictionary, Tbl, encode_plaintext, find_rom, load_rom  # noqa: E402

# Hand-checked dictionary entries (index -> expected Japanese)
GOLDEN = {
    1: "軍隊",
    13: "ムリ",
    21: "全体",
    24: "機体",
}

# Characters that must exist as 1-byte codes in a working TBL
REQUIRED_1BYTE = {
    0x04: "い",
    0x05: "の",
    0x06: "な",
    0x0A: "。",
    0x1D: "？",
    0x2E: "ラ",
    0x39: "戦",
    0x3F: "ム",
    0x50: "人",
    0x54: "隊",
    0x80: "機",
    0x86: "全",
    0x8F: "軍",
    0xBB: "体",
}


def is_mostly_cjk(s: str) -> bool:
    if not s:
        return False
    ok = sum(1 for ch in s if not ch.startswith("<") and ch not in "[]")
    # Heuristic: decoded string without many unknown markers
    return s.count("<") <= max(1, len(s) // 8)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--tbl", type=Path, default=ROOT / "data" / "monoeye.tbl")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "tbl_verify.json")
    args = ap.parse_args()

    tbl = Tbl.load(args.tbl)
    rom = load_rom(args.rom or find_rom(ROOT))
    d = Dictionary(rom)

    report: dict = {
        "tbl_path": str(args.tbl),
        "tbl_entries": len(tbl.code_to_char),
        "dict_entries": d.count,
        "ram_note": (
            "Runtime cross-check: set a write breakpoint on WonderSwan RAM "
            "0x016AE (intermediate text / glyph index buffer) and 0x0F47E "
            "(text pointer) in mednafen; compare streamed codes to this TBL."
        ),
        "required_1byte": {},
        "golden": {},
        "roundtrip_failures": [],
        "dict_stats": {},
    }

    # Required 1-byte mappings
    for code, ch in REQUIRED_1BYTE.items():
        got = tbl.code_to_char.get(code)
        report["required_1byte"][f"{code:02X}"] = {
            "expected": ch,
            "got": got,
            "ok": got == ch,
        }

    # Golden dictionary phrases
    for idx, expected in GOLDEN.items():
        got = d.expand_index(idx, tbl)
        report["golden"][str(idx)] = {
            "expected": expected,
            "got": got,
            "raw": " ".join(f"{b:02X}" for b in d.raw_entry(idx)),
            "ok": got == expected,
        }

    # Round-trip every unique TBL character that is a single glyph
    for ch, code in list(tbl.char_to_code.items()):
        if not ch or ch.startswith("?"):
            continue
        try:
            encoded = encode_plaintext(ch, tbl)
            # Decode without dictionary
            if len(encoded) == 1:
                decoded = tbl.decode_char(encoded[0])
            else:
                decoded = tbl.decode_char((encoded[0] << 8) | encoded[1])
            if decoded != ch:
                report["roundtrip_failures"].append(
                    {"char": ch, "code": f"{code:04X}", "decoded": decoded}
                )
        except Exception as exc:  # noqa: BLE001
            report["roundtrip_failures"].append({"char": ch, "error": str(exc)})

    # Dictionary decode quality sample
    samples = []
    unknown = 0
    cjkish = 0
    for i in range(d.count):
        text = d.expand_index(i, tbl)
        if "<" in text:
            unknown += 1
        if is_mostly_cjk(text):
            cjkish += 1
        if i < 50:
            samples.append({"index": i, "text": text})
    report["dict_stats"] = {
        "entries": d.count,
        "with_unknown_markers": unknown,
        "mostly_clean": cjkish,
        "sample_first_50": samples,
    }

    # Frequency of lead bytes in raw dictionary (sanity for E0-E7 / F0-FE)
    lead_hist: Counter = Counter()
    for raw in d.all_raw_entries():
        i = 0
        while i < len(raw):
            b = raw[i]
            lead_hist[b] += 1
            if 0xE0 <= b <= 0xE7 or 0xF0 <= b <= 0xFE:
                i += 2
            else:
                i += 1
    report["dict_lead_byte_top"] = {
        f"{b:02X}": n for b, n in lead_hist.most_common(32)
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Console summary
    req_ok = all(v["ok"] for v in report["required_1byte"].values())
    gold_ok = all(v["ok"] for v in report["golden"].values())
    print(f"TBL entries: {report['tbl_entries']}")
    print(f"Required 1-byte mappings: {'PASS' if req_ok else 'FAIL'}")
    for k, v in report["required_1byte"].items():
        if not v["ok"]:
            print(f"  FAIL {k}: expected {v['expected']!r} got {v['got']!r}")
    print(f"Golden dictionary phrases: {'PASS' if gold_ok else 'FAIL'}")
    for k, v in report["golden"].items():
        mark = "OK" if v["ok"] else "FAIL"
        print(f"  [{k}] {mark}: {v['got']!r} (expected {v['expected']!r})")
    print(f"Round-trip failures: {len(report['roundtrip_failures'])}")
    print(
        f"Dict clean-ish entries: {cjkish}/{d.count} "
        f"(unknown markers in {unknown})"
    )
    print(f"Wrote {args.out}")
    print(report["ram_note"])

    # Also emit a verified subset TBL for tooling
    verified = ROOT / "data" / "monoeye_verified.tbl"
    lines = ["# Verified core mappings + full Data Crystal dump", f"# source={args.tbl}"]
    # Keep full table but annotate golden-checked codes
    for code, ch in sorted(tbl.code_to_char.items()):
        if code <= 0xFF:
            lines.append(f"{code:02X}={ch}")
        else:
            lines.append(f"{code:04X}={ch}")
    verified.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {verified}")

    if not req_ok or not gold_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
