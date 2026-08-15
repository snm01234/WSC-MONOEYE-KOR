#!/usr/bin/env python3
"""Read-only scan: mixed KO/JP spirit/ID effect lines + 20/40-cell ID quotes."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import AUX_TOKEN_BANKS, _walk_zstring_range
from mixed_residual_classification import hangul_character_count, is_japanese_character
from monoeye_rom import Tbl, load_rom, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/_spirit_cmd_recoa_scan.json"

EFFECT_RANGE = (0x5CBBB8, 0x5CD749)
BUNDLE_RANGE = (0x5C8000, 0x5CBBB8)
PREFIX = bytes.fromhex("173418")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def jp_chars(text: str) -> str:
    return "".join(ch for ch in text if is_japanese_character(ch))


def main() -> int:
    rom = bytes(load_rom(MAIN))
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)

    mixed_effect = []
    mixed_other_aux = []
    effect_clean = []
    for bank in AUX_TOKEN_BANKS:
        start = bank << 16
        for logical, payload, _kind in _walk_zstring_range(
            rom, start, start + 0x10000, region="aux", max_len=128
        ):
            text = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
            if not text:
                continue
            jp = hangul = 0
            for ch in text:
                if is_japanese_character(ch):
                    jp += 1
                elif "\uac00" <= ch <= "\ud7a3":
                    hangul += 1
            if jp and hangul:
                row = {
                    "abs": f"{logical:06X}",
                    "bank": f"{bank:02X}",
                    "payload_len": len(payload),
                    "cells": len(text),
                    "jp_count": jp,
                    "hangul_count": hangul,
                    "jp_chars": jp_chars(text),
                    "text": text,
                    "payload_hex": payload[:24].hex().upper(),
                    "in_effect_range": EFFECT_RANGE[0] <= logical < EFFECT_RANGE[1],
                }
                if row["in_effect_range"]:
                    mixed_effect.append(row)
                else:
                    mixed_other_aux.append(row)
            elif EFFECT_RANGE[0] <= logical < EFFECT_RANGE[1] and hangul and not jp:
                effect_clean.append(
                    {
                        "abs": f"{logical:06X}",
                        "cells": len(text),
                        "text": text,
                    }
                )

    # ID-command activation quotes: 17 34 18 first + optional continuation.
    quotes = []
    for logical, payload, _kind in _walk_zstring_range(
        rom, BUNDLE_RANGE[0], EFFECT_RANGE[1], region="aux", max_len=128
    ):
        if payload.startswith(PREFIX):
            body = payload[len(PREFIX) :]
            text = dictionary.expand(body, tbl).rstrip("\u3000 \t")
            quotes.append(
                {
                    "abs": f"{logical:06X}",
                    "role": "first",
                    "payload_len": len(payload),
                    "body_len": len(body),
                    "cells": len(text),
                    "text": text,
                    "jp_count": sum(is_japanese_character(ch) for ch in text),
                    "hangul": hangul_character_count(text),
                }
            )
        elif quotes and quotes[-1]["role"] in ("first", "continuation"):
            # continuation candidates: immediately after previous terminator
            prev = int(quotes[-1]["abs"], 16)
            prev_end = prev + quotes[-1]["payload_len"] + 1
            if logical == prev_end:
                text = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
                quotes.append(
                    {
                        "abs": f"{logical:06X}",
                        "role": "continuation",
                        "payload_len": len(payload),
                        "body_len": len(payload),
                        "cells": len(text),
                        "text": text,
                        "jp_count": sum(is_japanese_character(ch) for ch in text),
                        "hangul": hangul_character_count(text),
                        "first_abs": quotes[-1]["abs"]
                        if quotes[-1]["role"] == "first"
                        else quotes[-1].get("first_abs"),
                    }
                )

    overflow_lines = [q for q in quotes if q["cells"] > 20 and q["hangul"]]
    pairs = []
    by_first: dict[str, list] = defaultdict(list)
    for q in quotes:
        key = q["abs"] if q["role"] == "first" else str(q.get("first_abs") or "")
        if key:
            by_first[key].append(q)
    pair_overflow = []
    for first_abs, rows in by_first.items():
        first = next((r for r in rows if r["role"] == "first"), None)
        cont = [r for r in rows if r["role"] == "continuation"]
        if not first:
            continue
        total = first["cells"] + sum(c["cells"] for c in cont)
        line_over = first["cells"] > 20 or any(c["cells"] > 20 for c in cont)
        if line_over or (cont and total > 40):
            pair_overflow.append(
                {
                    "first_abs": first_abs,
                    "total_cells": total,
                    "line_over": line_over,
                    "lines": [
                        {"abs": r["abs"], "role": r["role"], "cells": r["cells"], "text": r["text"]}
                        for r in rows
                    ],
                }
            )

    needle = "사상이나"
    recoa = [q for q in quotes if needle in q["text"] or "신념이" in q["text"] or "한계에" in q["text"]]
    diana_needles = [r for r in mixed_effect + mixed_other_aux if "次の" in r["text"] or "命中" in r["text"] or "상승합니다" in r["text"]]

    sample_effect = None
    got = None
    from monoeye_rom import read_encoded_z_safe

    got = read_encoded_z_safe(rom, sb + 0x5CBBB8, max_len=128)
    if got:
        payload, _term = got
        sample_effect = {
            "abs": "5CBBB8",
            "payload_hex": payload.hex().upper(),
            "text": dictionary.expand(payload, tbl),
            "cells": len(dictionary.expand(payload, tbl).rstrip("\u3000 \t")),
        }

    report = {
        "rom_sha256": sha256(rom),
        "rom_size": len(rom),
        "sample_5CBBB8": sample_effect,
        "mixed_effect_count": len(mixed_effect),
        "mixed_other_aux_count": len(mixed_other_aux),
        "effect_clean_count": len(effect_clean),
        "effect_clean_max_cells": max((r["cells"] for r in effect_clean), default=0),
        "mixed_effect": mixed_effect[:80],
        "mixed_effect_unique": sorted({r["text"] for r in mixed_effect}),
        "mixed_other_aux_spiritish": [
            r
            for r in mixed_other_aux
            if any(
                tok in r["text"]
                for tok in ("次の", "命中", "上昇", "상승", "전투", "소모", "1Ｔ", "１Ｔ", "します", "します")
            )
        ][:80],
        "overflow_quote_lines": overflow_lines[:80],
        "overflow_quote_line_count": len(overflow_lines),
        "pair_overflow_count": len(pair_overflow),
        "pair_overflow": pair_overflow[:80],
        "recoa": recoa,
        "diana_needles": diana_needles[:40],
        "effect_clean_over20": [r for r in effect_clean if r["cells"] > 20][:40],
        "effect_clean_sample": effect_clean[:15],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "rom_sha256",
        "mixed_effect_count",
        "mixed_other_aux_count",
        "effect_clean_count",
        "effect_clean_max_cells",
        "overflow_quote_line_count",
        "pair_overflow_count",
        "sample_5CBBB8",
        "recoa",
        "mixed_effect_unique",
    )}, ensure_ascii=False, indent=2))
    print("other_spiritish", len(report["mixed_other_aux_spiritish"]))
    print("effect_clean_over20", len(report["effect_clean_over20"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
