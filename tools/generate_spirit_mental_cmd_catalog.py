#!/usr/bin/env python3
"""Freeze mixed spirit/ID effect lines and overflowing ID-quote lines.

Discovers leftover Japanese/Korean stew in the ID-command effect box
``5CBBB8-5CD748`` (the status-screen description HUD that prefixes ``소모 N``)
and ID-command activation quotes whose Korean exceeds 20 cells per line.
Does not write a ROM.  The live main TIP SHA is pinned by the caller.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import _walk_zstring_range
from hangul_marker import marker_code
from mixed_residual_classification import hangul_character_count, is_japanese_character
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
BUNDLE_CSV = ROOT / "out/script/runtime_text_residual_id_bundle_sheet.csv"
OUT = ROOT / "data/spirit_mental_cmd_mixed_and_quote_ko.json"

EFFECT_RANGE = (0x5CBBB8, 0x5CD749)
PREFIX = bytes.fromhex("173418")
MAX_CELLS = 20
EXPECTED_MAIN = "b0438b51f0a6f5fab94418433f3fac6c551ab1ee8f434627ee4e42816928216a"

# Compact Korean matching the already-promoted 5CBBB8 family.  Stored effect
# lines do not include the HUD ``소모 N`` prefix.  Keys are Original-ROM JP.
JP_TO_KO = {
    "次の戦闘で自分の攻撃力と命中が上昇します": "다음전투　자신　공격력과　명중이　상승",
    "次の戦闘で敵スタックの戦闘力が減少します": "다음전투　적스택　전투력이　감소",
    "次の戦闘で敵スタックの攻撃力が減少します": "다음전투　적스택　공격력이　감소",
    "自分の回避とスタック移動力が上昇します": "자신　회피와　스택이동력　상승",
    "次の戦闘でスタックの攻撃力と命中が上昇": "다음전투　스택　공격력과　명중　상승",
    "自分のＨＰを完全に回復します": "자신　ＨＰ를　완전　회복",
    "同じ相手に対して２回連続で攻撃します": "같은　상대　２회　연속　공격",
    "ガンダムＸのサテライトキャノンを展開": "건담Ｘ　새틀라이트캐논　전개",
    "次の戦闘で攻撃力上昇。ただし敵は倒せない": "다음전투　공격력상승。격파불가",
    "次の戦闘で自分の攻撃力が上昇します": "다음전투　자신　공격력　상승",
    "次戦闘で自分の戦闘力上昇＆敵防御力低下": "다음전투　전투력상승＆적방어저하",
    "次の戦闘で自分の攻撃力、命中、回避が上昇": "다음전투　공격・명중・회피　상승",
    "次戦闘で相手の防御力と特殊防御を無効化": "다음전투　적　방어・특수방어　무효",
    "加減・抜け・捕獲ＵＰ": "가감・이탈・포획　상승",
    "泣いてパワ－アップ……？": "울어서　파워업……？",
}

QUOTE_TO_KO = {
    "있지　않으면　살아　있는　기분이　안　들어": "있지　않으면　살아있는　기분이　안　들어",
    "지금의　나는　불화와　다툼을　뿌리는　여신……": "지금의　나는　불화・다툼의　여신……",
    "그러기에　네놈이　한심한　머저리　바보　제자라는　거다！！": "그러기에　넌　바보란　말이다！！",
    "지구의　사악한　속박을　내　정의의　검으로！": "지구　사악한　속박을　정의의　검으로！",
    "내　목숨을　빨아들여……그리고、이기는　거야！": "목숨을　빨아들여……그리고　이겨！",
    "장난치지　마라！　전혀　상대도　안　된다！！": "장난치지　마라！상대도　안　된다！！",
    "이　그레미에게　반드시　미소　지어　줄　것이다……": "그레미에게　반드시　미소　지어　주리……",
    "우리는　살인자가　아니니까요……되도록이면": "우린　살인자가　아니니까……되도록이면",
    "전투의　결정적　요인이　아니라는　걸　가르쳐　주마！": "전투의　결정적　요인이　아님을　보여라！",
    "나는　틀림없이、제멋대로인　자의　독선에　맞서": "난　틀림없이、제멋대로인　독선에　맞서",
    "흰　늑대를　만나면、싸우지　말고　도망쳐라！": "흰　늑대면　싸우지　말고　도망쳐라！",
    "아홉　가지　지세와　천지풍수의　흐름이　있다！！": "아홉　지세와　천지풍수　흐름이　있다！！",
    "아무리　괴로운　운명이라도　끝까지　싸운다！": "괴로운　운명이라도　끝까지　싸운다！",
    "나는　잃고　싶지　않아…소중한　사람들을！": "잃고　싶지　않아…소중한　사람들을！",
    "목숨을　건　싸움은……실수를　청산하는　것에　불과해": "목숨　건　싸움은……실수　청산일　뿐",
    "하지　않기　위해서라도、우리가　일어선다！！": "않기　위해서라도、우리가　일어선다！！",
    "중력에　영혼이　사로잡힌　자　따윈　두렵지　않다！": "중력에　묶인　영혼　따위　두렵지　않다！",
    "자신의　운명은　스스로　개척하는　게　나다！！": "운명은　스스로　개척하는　게　나다！！",
    "자비가를　따르는　선택받은　젊은이들이여！": "자비가를　따르는　선택받은　젊은이여！",
    "지구권을　우리　자비가의　것으로　만드는　것이다！！": "지구권을　자비가의　것으로　만든다！！",
    "무리인　건　안다！하지만、이　정도는　해야": "무린　줄　안다！하지만　이　정도는　해야",
    "나　같은　사람이　해적이라　놀랐나요……？": "나　같은　사람이　해적이라　놀랐나……？",
    "가슴의　장미에　걸고　너를　쓰러뜨리겠다！": "가슴의　장미에　걸고　너를　쓰러뜨린다！",
    "부디　이　어린　양에게　지혜와　용기를　주소서": "어린　양에게　지혜와　용기를　주소서",
    "살이　뼈에서　떨어져　나갈　때까지　싸운다！！": "살이　뼈에서　떨어질　때까지　싸운다！！",
    "당신을　쓰러뜨리지　않으면、샤아가……죽어！": "당신을　안　쓰러뜨리면、샤아가……죽어！",
    "나는　여자야！그래서　지금　여기　있어！！": "나는　여자야！그래서　여기　있어！！",
    "지금　나는　여자로서　무척　충족되어　있어": "지금　나는　여자로서　충족되어　있어",
    "사람　목숨을　소중히　여기지　않는　자라면": "사람　목숨을　소중히　안　여기는　자라면",
    "나를　이길　수　있다고、진심으로　생각해！？": "날　이길　수　있다고、진심인가！？",
    "하늘을　떨어뜨리는　자는……내가　말살한다！": "하늘　떨어뜨리는　자……내가　말살한다！",
    "올바른　귀족이　지배하는　아름다운　세계를！": "올바른　귀족의　아름다운　세계를！",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def has_jp(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in text)


def encode_ok(text: str, tbl: Tbl) -> bool:
    encoded = try_encode_ko_text(
        text, tbl, hangul_marker_code=marker_code(), hangul_marker_mode="run"
    )
    return encoded is not None and bool(encoded) and b"\x00" not in encoded


def split_prefix(payload: bytes) -> tuple[bytes, bytes]:
    if payload.startswith(PREFIX):
        return PREFIX, payload[len(PREFIX) :]
    return b"", payload


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    digest = sha256(parent)
    if digest != EXPECTED_MAIN:
        raise SystemExit(f"main SHA drifted: {digest}")
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    original_dictionary = make_dictionary_ext3(original, {}, None)
    sb = stock_base(parent)
    orig_sb = stock_base(original)

    mixed_rows: list[dict[str, Any]] = []
    unmapped_mixed: list[dict[str, Any]] = []
    for logical, payload, _kind in _walk_zstring_range(
        parent, EFFECT_RANGE[0], EFFECT_RANGE[1], region="aux", max_len=64
    ):
        text = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        if not text or not has_jp(text) or hangul_character_count(text) == 0:
            continue
        orig_got = read_encoded_z_safe(original, orig_sb + logical, max_len=64)
        orig_text = (
            original_dictionary.expand(orig_got[0], tbl).rstrip("\u3000 \t")
            if orig_got
            else ""
        )
        ko = JP_TO_KO.get(orig_text)
        if ko is None:
            unmapped_mixed.append(
                {"abs": f"{logical:06X}", "current": text, "jp": orig_text}
            )
            continue
        ko = normalize_ko_text(ko)
        if not ko or len(ko) > MAX_CELLS or has_jp(ko) or not encode_ok(ko, tbl):
            raise SystemExit(f"invalid mixed KO at {logical:06X}: {ko!r}")
        mixed_rows.append(
            {
                "kind": "effect_desc",
                "abs": f"{logical:06X}",
                "prefix_hex": "",
                "payload_len": len(payload),
                "current_payload_hex": payload.hex().upper(),
                "jp": orig_text,
                "before": text,
                "ko": ko,
                "before_cells": len(text),
                "after_cells": len(ko),
            }
        )
    mixed_rows.sort(key=lambda row: int(row["abs"], 16))
    if unmapped_mixed:
        raise SystemExit(
            "unmapped mixed effect lines: "
            + json.dumps(unmapped_mixed, ensure_ascii=False)
        )

    quote_rows: list[dict[str, Any]] = []
    unmapped_quotes: list[dict[str, Any]] = []
    seen: set[str] = set()
    with BUNDLE_CSV.open(encoding="utf-8", newline="") as handle:
        for item in csv.DictReader(handle):
            address = str(item.get("record_start") or "").upper()
            if not address or address in seen:
                continue
            seen.add(address)
            logical = int(address, 16)
            got = read_encoded_z_safe(parent, sb + logical, max_len=128)
            if got is None:
                continue
            payload, _term = got
            prefix, body = split_prefix(payload)
            text = dictionary.expand(body, tbl).rstrip("\u3000 \t")
            csv_ko = normalize_ko_text(str(item.get("suggested_ko") or item.get("current_body") or ""))
            # Alias-page ext3 (local >= 0x0600) can expand empty offline while
            # the battle HUD still paints the CSV Korean.  Trust the sheet when
            # the live body is blank but the payload matches.
            if (not text or not hangul_character_count(text)) and hangul_character_count(csv_ko):
                current_hex = str(item.get("current_payload_hex") or "").replace(" ", "").upper()
                if current_hex and payload.hex().upper() == current_hex:
                    text = csv_ko
            if len(text) <= MAX_CELLS:
                continue
            if not hangul_character_count(text):
                continue
            ko = QUOTE_TO_KO.get(text)
            if ko is None:
                unmapped_quotes.append(
                    {
                        "abs": address,
                        "role": item.get("line_role"),
                        "current": text,
                        "jp": item.get("original_body"),
                        "cells": len(text),
                    }
                )
                continue
            ko = normalize_ko_text(ko)
            if not ko or len(ko) > MAX_CELLS or has_jp(ko) or not encode_ok(ko, tbl):
                raise SystemExit(f"invalid quote KO at {address}: {ko!r}")
            quote_rows.append(
                {
                    "kind": "id_quote",
                    "abs": address,
                    "bundle_start": str(item.get("bundle_start") or "").upper(),
                    "role": str(item.get("line_role") or ""),
                    "prefix_hex": prefix.hex().upper(),
                    "payload_len": len(payload),
                    "current_payload_hex": payload.hex().upper(),
                    "jp": str(item.get("original_body") or ""),
                    "before": text,
                    "ko": ko,
                    "before_cells": len(text),
                    "after_cells": len(ko),
                }
            )
    quote_rows.sort(key=lambda row: int(row["abs"], 16))
    if unmapped_quotes:
        raise SystemExit(
            "unmapped overflowing quotes: "
            + json.dumps(unmapped_quotes, ensure_ascii=False)
        )

    records = mixed_rows + quote_rows
    catalog = {
        "schema_version": 1,
        "description": (
            "Status-screen spirit/ID effect help mixed JP/KO leftovers compacted "
            "to ≤20 cells (HUD 소모 N prefix is not stored), plus ID-command "
            "activation quotes overflowing the 2×20 battle box."
        ),
        "parent_tip_sha256": digest,
        "max_effect_cells": MAX_CELLS,
        "effect_range": ["5CBBB8", "5CD748"],
        "mixed_count": len(mixed_rows),
        "quote_count": len(quote_rows),
        "unique_after": len({row["ko"] for row in records}),
        "records": records,
    }
    OUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "out": str(OUT.relative_to(ROOT)).replace("\\", "/"),
                "mixed": len(mixed_rows),
                "quotes": len(quote_rows),
                "unique": catalog["unique_after"],
                "parent": digest,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
