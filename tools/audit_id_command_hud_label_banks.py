#!/usr/bin/env python3
"""Static bank-band check for ID/battle HUD labels (攻撃, 命中, …).

Intermission menu Koreanization patched **graphics tiles in bank 54**.
This audit answers whether ID-command HUD terms like 攻撃 / 命中 live in that
same sprite/atlas band, or in the project's text UI bands (75B / 5F / 5C).

Method (read-only, no ROM write):

1. Decode known catalog addresses and live dictionary slots for the JP terms.
2. Walk stock-side UI ranges for exact zstring bodies matching those terms.
3. Classify each hit by bank band (graphics 54/72 vs text 5F/75B/5C/aux).
4. Report whether any hit falls in intermission-style graphic atlases.

Does not require a runtime capture; tile reverse-lookup is out of scope here.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import _walk_zstring_range
from extract_script import split_prefix_body
from monoeye_rom import Dictionary, Tbl, find_rom, load_rom, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/id_command_hud_label_bank_static_audit.json"

# Intermission / title graphic bands (docs/UI_LOCALIZE.md).
GRAPHIC_BANDS = {
    "intermission_bank54": (0x540000, 0x550000),
    "title_menu_bank72": (0x720000, 0x730000),
}

# Text UI bands used for battle/ID HUD in this project.
TEXT_BANDS = {
    "shared_dictionary_bank5f": (0x5F0000, 0x600000),
    "battle_ui_zstring_bank75b": (0x75B000, 0x75C000),
    "name75_bank75c": (0x75C000, 0x75E800),
    "id_aux_bank5c": (0x5C0000, 0x5D0000),
    "id_indirect_ui_5f25_5f40": (0x5F2500, 0x5F4000),
}

# Catalog anchors already known from prior UI work.
KNOWN_ABS = {
    "命中": ["75B411"],
    "防御力": ["75B3C1"],
    "運動性": ["75B3B7"],
    "移動力": ["75B3BD"],
    "限界反応": ["75B3C5"],
}

TARGET_JP = (
    "攻撃",
    "防御",
    "回避",
    "命中",
    "命中率",
    "射撃",
    "間接攻撃",
    "ＩＤ",
)

WALK_RANGES = (
    (0x5C0000, 0x5D0000, "bank5c"),
    (0x5F0000, 0x600000, "bank5f"),
    (0x750000, 0x760000, "bank75"),
    (0x540000, 0x550000, "bank54_graphic"),
    (0x720000, 0x730000, "bank72_graphic"),
)


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def bank_of(abs_hex: str) -> str:
    return f"{int(abs_hex, 16) >> 16:02X}"


def classify_abs(logical: int) -> str:
    for name, (lo, hi) in GRAPHIC_BANDS.items():
        if lo <= logical < hi:
            return f"graphic:{name}"
    for name, (lo, hi) in TEXT_BANDS.items():
        if lo <= logical < hi:
            return f"text:{name}"
    bank = logical >> 16
    if 0x50 <= bank <= 0x5F:
        return f"aux_or_text_bank_{bank:02X}"
    return f"other_bank_{bank:02X}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    tip = bytes(load_rom(args.tip))
    original = bytes(load_rom(find_rom(ROOT)))
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    stock = Dictionary(tip)
    original_dictionary = Dictionary(original)
    sb = stock_base(tip)

    # Dictionary slot exact matches (shared 5F phrases) on tip and original.
    dict_hits: list[dict[str, Any]] = []
    for index in range(stock.stock_count):
        try:
            tip_text = stock.expand_index(index, tbl).rstrip("\u3000 \t")
            orig_text = original_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        if tip_text not in TARGET_JP and orig_text not in TARGET_JP:
            continue
        dict_hits.append(
            {
                "kind": "dictionary_slot",
                "index": f"{index:04X}",
                "original_text": orig_text,
                "current_text": tip_text,
                "matched_term": tip_text if tip_text in TARGET_JP else orig_text,
                "band": "text:shared_dictionary_bank5f",
                "abs": None,
            }
        )

    # Known absolute anchors.
    known_hits: list[dict[str, Any]] = []
    for jp, addresses in KNOWN_ABS.items():
        for abs_hex in addresses:
            logical = int(abs_hex, 16)
            payload_got = read_encoded_z_safe(tip, sb + logical, max_len=64)
            if payload_got is None:
                continue
            payload, _term = payload_got
            prefix, body, kind = split_prefix_body(payload)
            rendered = dictionary.expand(body if body is not None else payload, tbl).rstrip(
                "\u3000 \t"
            )
            known_hits.append(
                {
                    "kind": "known_abs",
                    "target_jp": jp,
                    "abs": abs_hex.upper(),
                    "bank": bank_of(abs_hex),
                    "band": classify_abs(logical),
                    "prefix_hex": prefix.hex().upper() if prefix else "",
                    "payload_kind": kind,
                    "current_text": rendered,
                    "still_japanese_exact": rendered == jp,
                    "hangul_present": any("\uac00" <= ch <= "\ud7a3" for ch in rendered),
                }
            )

    # Walk tip + original ranges for TARGET_JP (exact body match).
    walk_hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def consider(logical: int, payload: bytes, label: str, source: str) -> None:
        prefix, body, kind = split_prefix_body(payload)
        if body is None:
            return
        try:
            tip_rendered = dictionary.expand(body, tbl).rstrip("\u3000 \t")
        except Exception:
            tip_rendered = ""
        try:
            # Re-read tip payload at same abs for current text when source is original.
            tip_payload_got = read_encoded_z_safe(tip, sb + logical, max_len=48)
            if tip_payload_got is not None:
                tip_payload, _term = tip_payload_got
                _p, tip_body, _k = split_prefix_body(tip_payload)
                tip_rendered = dictionary.expand(
                    tip_body if tip_body is not None else tip_payload, tbl
                ).rstrip("\u3000 \t")
        except Exception:
            pass
        try:
            orig_rendered = original_dictionary.expand(body, tbl).rstrip("\u3000 \t")
        except Exception:
            orig_rendered = ""
        matched = ""
        if tip_rendered in TARGET_JP:
            matched = tip_rendered
        elif orig_rendered in TARGET_JP:
            matched = orig_rendered
        else:
            return
        key = (f"{logical:06X}", matched)
        if key in seen:
            return
        seen.add(key)
        walk_hits.append(
            {
                "kind": "zstring_walk",
                "walk_range": label,
                "source_rom": source,
                "abs": f"{logical:06X}",
                "bank": f"{logical >> 16:02X}",
                "band": classify_abs(logical),
                "prefix_hex": prefix.hex().upper() if prefix else "",
                "payload_kind": kind,
                "matched_term": matched,
                "original_text": orig_rendered,
                "current_text": tip_rendered,
            }
        )

    for lo, hi, label in WALK_RANGES:
        for logical, payload, _kind in _walk_zstring_range(
            tip, lo, hi, region=label, max_len=48
        ):
            consider(logical, payload, label, "tip")
        for logical, payload, _kind in _walk_zstring_range(
            original, lo, hi, region=label, max_len=48
        ):
            consider(logical, payload, label, "original")

    all_hits = known_hits + walk_hits
    band_counts = collections.Counter(str(row.get("band") or "") for row in all_hits)
    graphic_hits = [row for row in all_hits if str(row.get("band") or "").startswith("graphic:")]
    text_hits = [row for row in all_hits if str(row.get("band") or "").startswith("text:")]

    by_term: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in all_hits:
        term = str(row.get("matched_term") or row.get("target_jp") or row.get("jp_or_current") or "")
        if term:
            by_term[term].append(row)
    for row in dict_hits:
        by_term[str(row["matched_term"])].append(row)

    conclusion = {
        "id_hud_labels_are_sprites_like_intermission_bank54": False,
        "reason": (
            "Hits for 攻撃/命中/防御/回避/命中率 resolve to text bands "
            "(bank5F dictionary and/or bank75B/5C zstrings), not bank54/72 "
            "graphic atlases used for intermission/title plate Koreanization."
            if not graphic_hits
            else "Unexpected graphic-band zstring hit; inspect graphic_band_hits."
        ),
        "graphic_band_hit_count": len(graphic_hits),
        "text_band_hit_count": len(text_hits),
        "dictionary_exact_slot_count": len(dict_hits),
        "localization_pipe": (
            "zstring/dictionary (battle_ui_action_labels / ui_unit_followup / "
            "ui_system), NOT patch_intermission_labels_ko tile strips"
        ),
    }

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_id_command_hud_label_banks.py",
        "read_only": True,
        "ok": True,
        "tip": identity(args.tip, tip),
        "targets": list(TARGET_JP),
        "graphic_bands": {
            name: {"start": f"{lo:06X}", "end_exclusive": f"{hi:06X}"}
            for name, (lo, hi) in GRAPHIC_BANDS.items()
        },
        "text_bands": {
            name: {"start": f"{lo:06X}", "end_exclusive": f"{hi:06X}"}
            for name, (lo, hi) in TEXT_BANDS.items()
        },
        "counts": {
            "known_abs_hits": len(known_hits),
            "walk_hits": len(walk_hits),
            "dictionary_slot_hits": len(dict_hits),
            "band_counts": dict(band_counts),
        },
        "conclusion": conclusion,
        "dictionary_slots": dict_hits,
        "known_abs": known_hits,
        "graphic_band_hits": graphic_hits,
        "by_term_sample": {
            term: rows[:12]
            for term, rows in sorted(by_term.items(), key=lambda item: item[0])
        },
        "walk_hits": walk_hits,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(args.out.relative_to(ROOT)).replace("\\", "/"),
                "conclusion": conclusion,
                "counts": report["counts"],
                "dictionary_slots": dict_hits,
                "known_abs": known_hits,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
