#!/usr/bin/env python3
"""Read-only Japanese leftover scan of the current test ROM vs Original.

Walks Original-derived zstring boundaries.  Structural prefixes (がせこ etc.)
and one-code-unit battle voice IDs are stripped before scoring.  Katakana
middle dot is not counted as Japanese.  Does not write a ROM or SaveRAM.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import _walk_zstring_range
from extract_script import split_prefix_body
from measure_aux_prefix_rule import code_units
from mixed_residual_classification import (
    core_character_count,
    defect_annotations,
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base

TEST = ROOT / "out/patch/term_unify_round2_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/test_rom_jp_residual_scan.json"

TAG_RE = re.compile(r"<[0-9A-Fa-f]{2,4}>")
REPEAT_RE = re.compile(r"(.)\1{5,}")
CONTROL_LEADS = ("がせこ", "がけは")
HIRA = re.compile(r"[\u3041-\u309f]")
KATA = re.compile(r"[\u30a0-\u30ff]")
REGIONS = (
    ("scenario_60_63", 0x600000, 0x640000, "script"),
    ("script_64_6F", 0x640000, 0x700000, "script"),
    ("mission_59", 0x590000, 0x5A0000, "aux59"),
    ("encyclopedia_5C", 0x5C0000, 0x5C7900, "aux"),
    ("battle_5D", 0x5D0000, 0x5E0000, "voice"),
    ("battle_5E", 0x5E0000, 0x5F0000, "voice"),
    ("name75", 0x75C000, 0x75E800, "name75"),
    ("ui75", 0x75B000, 0x75C000, "name75"),
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def visible(text: str) -> str:
    body = TAG_RE.sub(" ", text).replace("█", "").rstrip("\u3000 \t")
    for lead in CONTROL_LEADS:
        if body.startswith(lead):
            body = body[len(lead) :].lstrip("\u3000 ")
    if body[:1] in {"こ", "は"} and hangul_character_count(body[1:]) > 0:
        body = body[1:].lstrip("\u3000 ")
    return body


def kana_count(text: str) -> int:
    return sum(
        1
        for ch in text
        if ch != "・" and ("\u3040" <= ch <= "\u309f" or "\u30a0" <= ch <= "\u30ff")
    )


def display_jp(row: dict[str, Any]) -> bool:
    text = str(row["text"])
    if row["hangul"] or row["japanese"] < 2 or row["core"] < 4:
        return False
    if REPEAT_RE.search(text):
        return False
    hira = len(HIRA.findall(text))
    kata = len([ch for ch in text if KATA.match(ch) and ch != "・"])
    if hira >= 2:
        return True
    if kata >= 3 and hira == 0:
        return True
    return False


def split_body(payload: bytes, kind: str) -> tuple[bytes, bytes]:
    if kind == "voice":
        units = code_units(payload)
        prefix_len = units[0][1] if units else 0
        return payload[:prefix_len], payload[prefix_len:]
    if kind in {"script", "aux59"}:
        prefix, body, _name = split_prefix_body(payload)
        return prefix, body
    return b"", payload


def classify(text: str) -> dict[str, Any]:
    body = visible(text)
    japanese = japanese_character_count(body)
    hangul = hangul_character_count(body)
    core = core_character_count(body)
    if japanese and hangul:
        shape = "mixed"
    elif japanese:
        shape = "jp_only"
    elif hangul:
        shape = "ko_only"
    else:
        shape = "no_text"
    return {
        "text": body[:80],
        "japanese": japanese,
        "hangul": hangul,
        "kana": kana_count(body),
        "core": core,
        "shape": shape,
        "defects": list(defect_annotations(body)),
    }


def bucket(logical: int) -> str:
    return f"{logical >> 16:02X}xxxx"


def main() -> int:
    test = bytes(load_rom(TEST))
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    test_d = make_dictionary_ext3(test, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    orig_d = Dictionary(original)
    test_sb = stock_base(test)
    orig_sb = stock_base(original)

    counts: dict[str, collections.Counter] = {
        name: collections.Counter() for name, *_ in REGIONS
    }
    bank_counts: dict[str, collections.Counter] = {
        "jp_only": collections.Counter(),
        "mixed": collections.Counter(),
        "same_as_original_jp": collections.Counter(),
    }
    samples: dict[str, dict[str, list[dict[str, Any]]]] = {
        name: {
            "jp_display": [],
            "mixed_kana": [],
            "mixed_kanji_only": [],
            "same_as_original_jp": [],
        }
        for name, *_ in REGIONS
    }
    totals = collections.Counter()

    for name, start, end, kind in REGIONS:
        for logical, _orig, _rec_kind in _walk_zstring_range(
            original, start, end, region=name, max_len=256
        ):
            got_t = read_encoded_z_safe(test, test_sb + logical, max_len=256)
            got_o = read_encoded_z_safe(original, orig_sb + logical, max_len=256)
            if not got_t or not got_o:
                continue
            _pref_t, body_t = split_body(bytes(got_t[0]), kind)
            _pref_o, body_o = split_body(bytes(got_o[0]), kind)
            try:
                cur = classify(test_d.expand(body_t, tbl))
                src = classify(orig_d.expand(body_o, tbl))
            except Exception:
                continue
            totals["records"] += 1
            counts[name]["records"] += 1
            shape = cur["shape"]
            counts[name][shape] += 1
            jp_display = display_jp(cur)
            mixed_kana = shape == "mixed" and int(cur["kana"]) >= 1
            mixed_kanji = shape == "mixed" and int(cur["kana"]) == 0
            if jp_display:
                totals["jp_display"] += 1
                counts[name]["jp_display"] += 1
                bank_counts["jp_only"][bucket(logical)] += 1
                if len(samples[name]["jp_display"]) < 8:
                    samples[name]["jp_display"].append(
                        {
                            "abs": f"{logical:06X}",
                            "current": cur["text"],
                            "original": src["text"],
                            "jp": cur["japanese"],
                            "kana": cur["kana"],
                        }
                    )
            if mixed_kana:
                totals["mixed_kana"] += 1
                counts[name]["mixed_kana"] += 1
                bank_counts["mixed"][bucket(logical)] += 1
                if len(samples[name]["mixed_kana"]) < 8:
                    samples[name]["mixed_kana"].append(
                        {
                            "abs": f"{logical:06X}",
                            "current": cur["text"],
                            "original": src["text"],
                            "jp": cur["japanese"],
                            "kana": cur["kana"],
                            "ko": cur["hangul"],
                            "defects": cur["defects"],
                        }
                    )
            if mixed_kanji:
                totals["mixed_kanji_only"] += 1
                counts[name]["mixed_kanji_only"] += 1
            same_jp = (
                display_jp(src)
                and jp_display
                and cur["text"] == src["text"]
            )
            if same_jp:
                totals["same_as_original_jp"] += 1
                counts[name]["same_as_original_jp"] += 1
                bank_counts["same_as_original_jp"][bucket(logical)] += 1
                if len(samples[name]["same_as_original_jp"]) < 8:
                    samples[name]["same_as_original_jp"].append(
                        {"abs": f"{logical:06X}", "text": cur["text"]}
                    )
            if cur["defects"]:
                for defect in cur["defects"]:
                    counts[name][f"defect_{defect}"] += 1
                    totals[f"defect_{defect}"] += 1

    payload = {
        "ok": True,
        "test_rom": {"path": "out/patch/term_unify_round2_candidate.wsc", "sha256": sha(test)},
        "original_sha256": sha(original),
        "policy": {
            "prefix": "split_prefix_body for script/bank59; one code-unit voice id for 5D/5E",
            "japanese": "hiragana/katakana/kanji; middle-dot excluded",
            "control": "structural prefix plus leftover lead こ/は/がせこ stripped when Hangul follows",
            "jp_display": "jp_only after control strip, core>=4, jp>=2, hiragana prose or katakana name",
            "mixed_kana": "Hangul plus kana after control strip",
        },
        "totals": dict(totals),
        "by_region": {name: dict(counter) for name, counter in counts.items()},
        "by_bank_page": {key: dict(counter) for key, counter in bank_counts.items()},
        "samples": samples,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "records": totals["records"],
                "jp_display": totals["jp_display"],
                "mixed_kana": totals["mixed_kana"],
                "mixed_kanji_only": totals["mixed_kanji_only"],
                "same_as_original_jp": totals["same_as_original_jp"],
                "out": str(OUT),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
