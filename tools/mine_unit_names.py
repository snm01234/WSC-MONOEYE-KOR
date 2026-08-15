#!/usr/bin/env python3
"""Mine bank-5F dictionary + non-dialogue banks for unit/MS/pilot name candidates."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    find_rom,
    load_rom,
    read_encoded_z_safe,
    slice_bank,
)

# Katakana-heavy / known MS vocabulary markers
MS_HINT = re.compile(
    r"ガンダム|ザク|ジム|ゲルググ|ドム|ゴッグ|ズゴック|アッガイ|ズゴック|"
    r"シャア|アムロ|ジオング|サザビー|ニュー|ν|νガンダム|ハイザック|"
    r"メッサー|キュベレイ|キュベレイ|Ζ|ΖΖ|ダブルゼータ|百式|百式|"
    r"モビル|ユニット|戦艦|戦艦|パイロット|兵器|武器|機体|装甲|"
    r"ビーム|ライフル|メガ|キャノン|バズーカ|サーベル|シールド|"
    r"連邦|ジオン|サイド|コロニー|ミノフスキー|"
    r"グフ|リック|ド・ダイ|ビグロ|ブラウ|ブラウ|"
    r"ハンブラビ|ガルス|ガルス|ギャプラン|ハンマ|ハンマ|"
    r"サイコ|デビル|モノアイ|ウェドナ|ブラ－ド|シグ"
)

KATA_RATIO = re.compile(r"[\u30A0-\u30FFー－]+")
JP_CTRL = re.compile(r"[ぁ-ん]|です|ます|した|いる|する|ない|こと|もの")


def looks_like_name(plain: str) -> bool:
    if not plain or len(plain) < 2 or len(plain) > 28:
        return False
    if "<" in plain or ">" in plain:
        return False
    # dialogue-ish
    if JP_CTRL.search(plain) and not MS_HINT.search(plain):
        return False
    kata = "".join(KATA_RATIO.findall(plain))
    # mostly katakana / short kanji labels
    if len(kata) >= 2 and len(kata) / max(1, len(re.sub(r"[\s　]", "", plain))) >= 0.55:
        return True
    if MS_HINT.search(plain):
        return True
    # short kanji labels (2-6 chars) without sentence punctuation
    if re.fullmatch(r"[\u4e00-\u9fffー－々]+", plain) and 2 <= len(plain) <= 8:
        return True
    return False


def main() -> None:
    rom = load_rom(ROOT / "out/patch/monoeye_ko_expanded.wsc")
    # Prefer stock dict from original for clean JP text
    try:
        base = load_rom(find_rom(ROOT))
    except Exception:
        base = rom
    tbl_path = ROOT / "out/patch/hangul_patch.tbl"
    if not tbl_path.exists():
        tbl_path = ROOT / "data/monoeye.tbl"
    tbl = Tbl.load(tbl_path)
    d = Dictionary(base)

    names: list[dict] = []
    for idx in range(d.count):
        plain = d.expand_index(idx, tbl)
        if not looks_like_name(plain):
            continue
        names.append({"index": f"{idx:04X}", "jp": plain, "len": len(plain)})

    # Dedup by jp keeping first index
    by_jp: dict[str, dict] = {}
    for row in names:
        by_jp.setdefault(row["jp"], row)

    # Categorize
    cats = Counter()
    categorized: dict[str, list[dict]] = {
        "ms_mech": [],
        "pilot_person": [],
        "weapon_term": [],
        "ship_org": [],
        "generic_label": [],
        "other_name": [],
    }
    for jp, row in sorted(by_jp.items(), key=lambda kv: kv[1]["index"]):
        if re.search(
            r"ガンダム|ザク|ジム|ゲルググ|ドム|グフ|ジオング|サザビー|キュベレイ|"
            r"ハイザック|メッサー|百式|Ζ|ズゴック|アッガイ|ゴッグ|リック|"
            r"モビル|サイコ|デビル|モノアイ|ハロ",
            jp,
        ):
            cat = "ms_mech"
        elif re.search(r"ビーム|ライフル|メガ|キャノン|バズーカ|サーベル|シールド|兵器|武器", jp):
            cat = "weapon_term"
        elif re.search(r"戦艦|コロニー|連邦|サイド|ジオン|公国|ユニット|機体|パイロット", jp):
            cat = "ship_org"
        elif re.fullmatch(r"[\u30A0-\u30FFー－]{2,12}", jp):
            # pure katakana — likely pilot/MS/place
            if re.search(r"ガンダム|ザク|ジム|ドム|グフ", jp):
                cat = "ms_mech"
            else:
                cat = "pilot_person"
        elif re.fullmatch(r"[\u4e00-\u9fffー－々]{2,8}", jp):
            cat = "generic_label"
        else:
            cat = "other_name"
        categorized[cat].append(row)
        cats[cat] += 1

    out_dir = ROOT / "out" / "script"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "source_rom": "original via find_rom (dict text)",
        "dict_count": d.count,
        "name_candidates": len(by_jp),
        "categories": dict(cats),
        "by_category": categorized,
    }
    (out_dir / "unit_name_dict_candidates.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Human-readable dump
    lines = [f"# dict name candidates: {len(by_jp)}", ""]
    for cat, rows in categorized.items():
        lines.append(f"## {cat} ({len(rows)})")
        for row in rows[:200]:
            lines.append(f"{row['index']}\t{row['jp']}")
        if len(rows) > 200:
            lines.append(f"... +{len(rows)-200} more")
        lines.append("")
    (out_dir / "unit_name_dict_candidates.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(f"dict names={len(by_jp)} cats={dict(cats)}")
    print(f"wrote {out_dir / 'unit_name_dict_candidates.json'}")

    # Scan non-dialogue banks for encoded zstrings that look like names
    # Banks often holding data: 50-5E, 70-79 (avoid 5F dict body, 60-6F dialogue)
    scan_banks = list(range(0x50, 0x5F)) + list(range(0x70, 0x7A))
    bank_hits: list[dict] = []
    seen_plain: set[str] = set()
    for bank in scan_banks:
        data = slice_bank(base, bank)
        i = 0
        while i < len(data) - 2:
            # heuristic: start of plausible encoded text
            b0 = data[i]
            if b0 == 0 or b0 == 0xFF:
                i += 1
                continue
            abs_off = (bank << 16) | i
            try:
                raw, nxt = read_encoded_z_safe(base, abs_off, max_len=48)
            except Exception:
                i += 1
                continue
            if not raw or len(raw) < 2 or len(raw) > 40:
                i += 1
                continue
            # expand via dictionary
            try:
                plain = d.expand(raw, tbl)
            except Exception:
                i += 1
                continue
            if plain in seen_plain:
                i = max(i + 1, nxt - ((bank << 16)) if False else i + 1)
                i += 1
                continue
            if looks_like_name(plain) and MS_HINT.search(plain):
                seen_plain.add(plain)
                bank_hits.append(
                    {
                        "abs": f"{abs_off:06X}",
                        "bank": f"{bank:02X}",
                        "jp": plain,
                        "raw_len": len(raw),
                    }
                )
            i += 1
            if len(bank_hits) > 5000:
                break
        if len(bank_hits) > 5000:
            break

    (out_dir / "unit_name_bank_hits.json").write_text(
        json.dumps(
            {"hit_count": len(bank_hits), "hits": bank_hits[:2000]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"bank hits (MS_HINT)={len(bank_hits)} sample:")
    for h in bank_hits[:30]:
        print(f"  @{h['abs']} {h['jp']}")


if __name__ == "__main__":
    main()
