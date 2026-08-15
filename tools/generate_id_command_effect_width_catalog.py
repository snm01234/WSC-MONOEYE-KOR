#!/usr/bin/env python3
"""Generate the frozen ID-command effect width catalog from the live TIP."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import _walk_zstring_range
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from hangul_marker import marker_code

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
INV = ROOT / "out/patch/id_command_effect_width_inventory.json"
OUT = ROOT / "data/id_command_effect_width_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_MAIN = "2cb645e4bb700db4c111041f8cfbb9c65b8a0b937b8877fe9f76cc92ed3a1dda"
MAX_CELLS = 20
RANGE = (0x5CBBB8, 0x5CD749)

EXACT = {
    "다음　전투에서　임의의　적에게　공격을　집중할　수　있습니다": "다음전투　임의　적에게　공격　집중",
    "다음　전투에서　ＨＰ가　５０％　이하인　적을　반드시　격파": "다음전투　ＨＰ５０％이하　적　격파",
    "다음　전투에서　ＨＰ가　５０％　이하인　상대를　격파": "다음전투　ＨＰ５０％이하　상대　격파",
    "이동력과　반응이　상승하고、적을　통과할　수　있게　됩니다": "이동력・반응　상승、적　통과　가능",
    "스택　이동력이　상승하고、적을　통과할　수　있습니다": "스택　이동력　상승、적　통과　가능",
    "다음　전투에서　완전명중과　완전회피의　효과를　얻습니다": "다음전투　완전명중・완전회피",
    "다음　전투에서　공격력이　증대하고、공격이　반드시　명중": "다음전투　공격력　증대、필중",
    "다음　전투에서　산개를　봉인하고、상대의　방어를　무효화": "다음전투　산개　봉인、방어　무효",
    "다음　전투에서　자신의　전투력　상승＆적　방어력　저하": "다음전투　전투력상승＆적방어저하",
    "같은　상대에게　２회　연속으로　공격합니다": "같은　상대　２회　연속　공격",
    "다음　전투에서　자신의　공격력과　명중이　대폭　상승": "다음전투　공격・명중　대폭상승",
    "자신의　능력을　저하시키고、우군　전원의　ＨＰ　회복": "자신　능력저하、우군　ＨＰ회복",
    "１턴　동안、　자신의　공격력、　명중、　회피가　상승": "１턴간　공격・명중・회피　상승",
    "１턴　동안、　자신의　공격력、　방어、　반응이　상승": "１턴간　공격・방어・반응　상승",
    "다음　전투에서　자신의　공격력、명중、회피가　상승": "다음전투　공격・명중・회피　상승",
    "다음　전투에서　적의　방어력과　특수방어를　무효화": "다음전투　적　방어・특수방어　무효",
    "다음　전투에서　적의　공격력、방어、반응이　감소": "다음전투　적　공격・방어・반응　감소",
    "모든　ＩＤ효과를　소거하고、아군의　ＨＰ를　회복": "전ＩＤ효과　소거、아군　ＨＰ회복",
    "１턴　동안、　선제　공격이　발생하기　쉬워집니다": "１턴간　선제공격　발생　쉬움",
    "근접공격력　３배。단　반드시　반격을　받는다": "근접공격　３배。단　반격받음",
    "소속되어　있는　군　전체의　전투력을　상승": "소속군　전체　전투력　상승",
}

GENERIC = (
    ("다음　전투에서　", "다음전투　"),
    ("１턴　동안、　", "１턴간　"),
    ("３Ｔ　동안、", "３턴간　"),
    ("지휘　범위　내의　", "지휘범위　"),
    ("지휘　범위　내　", "지휘범위　"),
    ("적　스택의　", "적스택　"),
    ("자신의　", "자신　"),
    ("상대의　", "상대　"),
    ("상대에게　", "상대　"),
    ("상대를　", "상대　"),
    ("스택의　", "스택　"),
    ("공격을　", "공격　"),
    ("방어를　", "방어　"),
    ("회피를　", "회피　"),
    ("산개를　", "산개　"),
    ("반격을　", "반격　"),
    ("효과를　얻습니다", "효과"),
    ("상승시킵니다", "상승"),
    ("상승합니다", "상승"),
    ("감소합니다", "감소"),
    ("봉인합니다", "봉인"),
    ("회복합니다", "회복"),
    ("회복시킵니다", "회복"),
    ("증대합니다", "증대"),
    ("회피합니다", "회피"),
    ("명중합니다", "명중"),
    ("받습니다", "받음"),
    ("집중합니다", "집중"),
    ("격파하지　않습니다", "격파하지　않음"),
    ("강제적으로　산개시킵니다", "강제　산개"),
    ("반드시　명중", "필중"),
    ("반드시　회피", "필회"),
    ("대폭　상승", "대폭상승"),
    ("할　수　있습니다", "가능"),
    ("할　수　있게　됩니다", "가능"),
)


def compact(text: str) -> str:
    if text in EXACT:
        return normalize_ko_text(EXACT[text])
    value = text
    for src, dst in GENERIC:
        value = value.replace(src, dst)
    while "　　" in value:
        value = value.replace("　　", "　")
    return normalize_ko_text(value.rstrip("\u3000 \t"))


def main() -> int:
    rom = bytes(load_rom(MAIN))
    from hashlib import sha256 as _sha

    digest = _sha(rom).hexdigest()
    if digest != EXPECTED_MAIN:
        raise SystemExit(f"main SHA drifted: {digest}")
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    sb = stock_base(rom)
    records = []
    failures = []
    for logical, payload, _kind in _walk_zstring_range(
        rom, RANGE[0], RANGE[1], region="aux", max_len=64
    ):
        if not (8 <= len(payload) <= 40):
            continue
        before = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        if len(before) <= MAX_CELLS:
            continue
        if any(is_japanese_character(ch) for ch in before):
            continue
        after = compact(before)
        encoded = try_encode_ko_text(
            after, tbl, hangul_marker_code=marker_code(), hangul_marker_mode="run"
        )
        if (
            not after
            or len(after) > MAX_CELLS
            or after == before
            or encoded is None
            or b"\x00" in encoded
            or any(is_japanese_character(ch) for ch in after)
        ):
            failures.append({"abs": f"{logical:06X}", "before": before, "after": after})
            continue
        if rom[sb + logical : sb + logical + len(payload)] != payload:
            raise SystemExit(f"payload read mismatch {logical:06X}")
        if rom[sb + logical + len(payload)] != 0:
            raise SystemExit(f"terminator missing {logical:06X}")
        records.append(
            {
                "abs": f"{logical:06X}",
                "region": "aux",
                "prefix_hex": "",
                "payload_len": len(payload),
                "current_payload_hex": payload.hex().upper(),
                "before": before,
                "ko": after,
                "before_cells": len(before),
                "after_cells": len(after),
            }
        )
    if failures:
        (ROOT / "out/patch/id_command_effect_width_failures.json").write_text(
            json.dumps(failures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        raise SystemExit(f"compact failures: {len(failures)}")
    unique = sorted({row["ko"] for row in records})
    catalog = {
        "schema_version": 1,
        "description": "ID-command effect help lines compacted to the 20-cell Japanese box. Combined with 소모+2-digit SP+space the visible line stays at 25 cells.",
        "parent_tip_sha256": EXPECTED_MAIN,
        "max_effect_cells": MAX_CELLS,
        "dynamic_prefix_cells": 5,
        "max_combined_cells": 25,
        "range": ["5CBBB8", "5CD748"],
        "unique_after": len(unique),
        "records": records,
    }
    OUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "records": len(records),
                "unique_after": len(unique),
                "max_after_cells": max(row["after_cells"] for row in records),
                "out": str(OUT),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
