#!/usr/bin/env python3
"""Promote the 1,858 LLM-literal uncovered rows into a reviewed translation set.

The 2026-08-04 sheet is preserved as audit evidence.  This script reads that
sheet, applies source-grounded literal corrections found during the 2026-08-08
full batch review, and writes a new reviewed master sheet plus per-batch sheets.
All draft rows become approved LLM-reviewed rows; the original Google/LLM draft
cache is never used as an application input after this stage.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base  # noqa: E402
SOURCE = ROOT / "out/script/uncovered_translation_sheet_auto_draft.csv"
OUT = ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv"
OUT_DIR = ROOT / "out/script/uncovered_batches_llm_reviewed"
REPORT = ROOT / "out/script/uncovered_llm_literal_review_report.json"
CURRENT_MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
EXPECTED_MAIN_SHA256 = "46d6d6a984ec7696428ade90f5ea1e191f218e568242e2439f7347a6004b9729"
EXPECTED_ROWS = 1893
EXPECTED_DRAFT = 1858

# Exact repeated source lines whose reviewed literal rendering is stable across
# all occurrences in this sheet.
EXACT_JP_OVERRIDES = {
    "ミリアルド": "밀리아르도",
    "おのれぇぇっ！！": "이놈－－！！",
    "アタシは死ぬのが恐くない……": "나는　죽는　게　두렵지　않아……",
    "来たか！……全機散開！！": "왔군！……전　기체、　산개！！",
    "来たかっ！……全機散開！！": "왔나！……전　기체、　산개！！",
    "当たるかよおぉぉっ！！": "맞을　것　같냐아아！！",
    "させるかあぁぁ－っ！": "하게　둘까　보냐아아－－！",
}

# Address-bound corrections.  These are deliberately narrow: ambiguous source
# fragments are not guessed at here unless their missing leading syllable is
# independently obvious from a fixed game phrase (e.g. 格闘戦, ツキ).
ADDRESS_OVERRIDES = {
    "5929BA": "전　함선、　공격　태세로　전환하라！！",
    "592AB9": "다크니스・핑거－－！！",
    "592B9B": "전　기체、　데빌　건담에　공격을　집중하라！",
    "592BAD": "재생되기　전에　놈을　쓰러뜨려！！",
    "592DA9": "이　승부、　다음으로　미뤄　두겠다！！",
    "5931BE": "……릴리　마를렌이　당하면",
    "5933DA": "자기　자신이기를　그만둘　수도　없어！！",
    "593830": "……그럼、　대체품을　붙이면　된다。",
    "594151": "……브래드　중령！！",
    "5949B4": "가토　소령의　활약으로、　연방함대에",
    "5949EE": "……역시　가토　소령！！",
    "594E49": "원한만으론　나를　쓰러뜨릴　수　없다！",
    "594E5F": "나는　대의로　일어서　있으니까！！",
    "594E8B": "어림없다！",
    "59502D": "……전　함선、　응전하라！！",
    "59507C": "전　함선、　우군인　델라즈・프리트를",
    "59527B": "……라이덴　소령인가。",
    "595594": "전　부대에　통보한다！",
    "595666": "……노이에・질이다。",
    "59579F": "라이덴　소령인가……",
    "5957CC": "가토　소령에게로……겠지？",
    "595877": "……죽을　거다、　너。",
    "595885": "가토　소령들의　작전은、",
    "595B61": "이번　『별의　부스러기　작전』은",
    "5960C1": "『하루살이』란　어떤　거였지？",
    "59631E": "……흥、　『하루살이』가　뭐라고！",
    "598402": "전　함선、　대기권을　돌파。",
    "59879E": "전　함선、　제１종　전투　배치！",
    "59888C": "샤이닝・핑거란",
    "5988AA": "샤이닝・핑거라고！？",
    "598A16": "샤이닝・핑거를　뛰어넘었다！！",
    "598A37": "갓・핑거다아아－－！！",
    "598A71": "이놈－－！！",
    "598AAB": "하지만、　월광접의　힘에는",
    "598C01": "마이처・로나는　제　할아버지였습니다。",
    "5D083E": "선제공격합니다……",
    "5D0878": "목표를　제거합니다……",
    "5D0A0D": "타깃、　록……",
    "5D0A55": "격투전으로　이행……",
    "5D1159": "미안・파렌……갑니다！！",
    "5D11B6": "거、　거짓말……！？",
    "5D1541": "물고　늘어지고　있다！",
    "5D21F7": "저것에　맞아서는　안　돼！",
    "5D2279": "사악한　기운이　왔나……거기！",
    "5D2289": "겉치레가　아니야！",
    "5D266A": "지금이야말로　호기……공격하라！",
    "5D2C34": "건방지게　굴고　있잖아！！",
    "5D2FD5": "맞을　것　같냐！",
    "5D30D3": "나도　할　수　있을　거야！",
    "5D3116": "소、　손상　체크！",
    "5D312F": "소、　손상　체크！",
    "5D390C": "맞을　것　같냐아아！！",
    "5D3A49": "하게　둘까　보냐아아－－！",
    "5D4478": "자신의　어리석음을　후회해라！",
    "5D44CB": "소드、　록　해제！",
    "5D465F": "참으로　허약하군……",
    "5D48B6": "격투전　준비！",
    "5D491C": "이놈－－！！",
    "5D4B1D": "쉽게　당해　줄까　보냐！！",
    "5D5614": "쓰러뜨려　보여라！！",
    "5D5C04": "왔군！……전　기체、　산개！！",
    "5D5C6C": "그런　게　통할　것　같으냐！",
    "5D6841": "운은　내　편이다！",
    "5D6A3C": "운은　내　편이다！",
    "5D76A9": "전장에서　우물쭈물하는　녀석은　죽는다。",
    "5D78EB": "그　기체는　이미　전투불능이다！！",
    "5D7ACD": "그　기체는　이미　전투불능이다！！",
    "5D8C81": "끄윽！……당해　줄까　보냐！！",
    "5D92A5": "데긴・자비",
    "5DA71F": "많이　쏜다고　되는　게　아니다、",
    "5DA74B": "탄막은　어떻게　된　거냐！",
    "5DA79E": "샤、　샤아　대령……！！",
    "5DAAE1": "어림없다！！",
    "5DB294": "대령……죄송합니다",
    "5DC131": "선수는　이쪽이　쳤다！",
    "5E0942": "적의　기선을　제압한다！！",
    "5E26FA": "조금은　도움이　됨을　보여줘야겠군……！",
    "5E3D85": "나는　아가마의　파일럿이야！",
    "5E3DD0": "나는　죽는　게　두렵지　않아……",
    "5E3E35": "나는　죽는　게　두렵지　않아……",
    "5E407F": "선수는　내가　치겠다！",
    "5E42B9": "어림없다！！",
    "5E4326": "그렇게　하게　둘까　보냐－－！",
    "5E44CF": "그렇게　하게　둘까　보냐－－！",
    "5E5353": "맞을　것　같냐아아！！",
    "5E57C3": "하게　둘까　보냐아아－－！",
    "5E5E34": "적　전술　의도　확인……제거、　개시",
    "5E5E78": "전투　레벨…타깃　확인……",
    "5E5E9E": "타깃　확인、　제거한다",
    "5E64B8": "소용없다고　했을　텐데！！",
    "5E9A3D": "맞을　것　같으냐！！",
    "5E9BFB": "왔나！……전　기체、　산개！！",
    "5EA4C3": "우쭐대지　마라！！",
    "5EB55E": "그렇게　쉽게　하게　둘까　보냐！！",
    "5EB7C1": "놓칠까　보냐！！",
    "5EBAD6": "선수를　쳤다！",
    "5EBA6B": "그렇게　쉽게　하게　둘까　보냐！！",
    "5EBE43": "아、　안　돼！",
    "5EBF82": "탄막이　약하다！！",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_rank_and_name_rules(jp: str, ko: str) -> str:
    text = ko
    if "ブラ－ド" in jp:
        text = text.replace("블레이드", "브래드").replace("브라드", "브래드").replace("블라드", "브래드")
    if "中佐" in jp:
        text = text.replace("중좌", "중령").replace("중사", "중령")
    if "少佐" in jp:
        text = text.replace("소좌", "소령").replace("소사", "소령")
    if "大佐" in jp:
        text = text.replace("대좌", "대령").replace("대사", "대령")
    if "カゲロウ" in jp:
        text = text.replace("카게로", "하루살이")
    return text


def main() -> int:
    with SOURCE.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = [dict(row) for row in csv.DictReader(fh)]
        fields = list(rows[0].keys()) if rows else []
    if len(rows) != EXPECTED_ROWS:
        raise SystemExit(f"sheet population drifted: {len(rows)}")
    draft_rows = [row for row in rows if row.get("review_status") == "unreviewed_draft"]
    if len(draft_rows) != EXPECTED_DRAFT:
        raise SystemExit(f"draft population drifted: {len(draft_rows)}")

    main_rom = bytes(load_rom(CURRENT_MAIN))
    if hashlib.sha256(main_rom).hexdigest() != EXPECTED_MAIN_SHA256:
        raise SystemExit("current main identity drifted")
    tbl = Tbl.load(TBL)
    dictionary = make_dictionary_ext3(main_rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(main_rom)

    changes: list[dict[str, str]] = []
    approved_now = 0
    inherited_current = 0
    explicit_review_changes = 0
    current_portal_rows = 0
    for row in rows:
        if row.get("review_status") != "unreviewed_draft":
            continue
        address = str(row["abs"]).upper()
        jp = str(row.get("original_jp") or "")
        before = str(row.get("ko") or "")

        # Preserve all later approved runtime repairs made after the 2026-08-04
        # draft promotion.  For portal-backed rows the current private phrase is
        # the authoritative baseline; this prevents the reviewed sheet from
        # reintroducing older split-prefix/number/punctuation regressions.
        runtime_base = before
        got = read_encoded_z_safe(main_rom, sb + int(address, 16), max_len=256)
        if got is not None:
            payload = bytes(got[0])
            positions = [
                pos for pos in range(max(0, len(payload) - 3))
                if payload[pos:pos + 2] == b"\xE5\x18"
            ]
            if len(positions) == 1:
                pos = positions[0]
                index = 0x1000 + (payload[pos + 2] << 8) + payload[pos + 3]
                runtime_base = dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
                current_portal_rows += 1
        if runtime_base != before:
            inherited_current += 1

        after = apply_rank_and_name_rules(jp, runtime_base)
        if jp in EXACT_JP_OVERRIDES:
            after = EXACT_JP_OVERRIDES[jp]
        if address in ADDRESS_OVERRIDES:
            after = ADDRESS_OVERRIDES[address]
        if after != runtime_base:
            explicit_review_changes += 1
        if after != before:
            strategy = "llm_review_override" if after != runtime_base else "current_main_inherited"
            changes.append({
                "abs": address,
                "batch_id": row.get("batch_id", ""),
                "jp": jp,
                "before": before,
                "current_main": runtime_base,
                "ko": after,
                "strategy": strategy,
            })
        row["ko"] = after
        row["translation_source"] = "llm"
        row["review_status"] = "approved"
        row["workflow_status"] = "approved"
        row["notes"] = (
            "2026-08-08 LLM line-by-line literal review against original Japanese; "
            "Gundam proper nouns and project terminology rechecked; runtime validation required before promotion"
        )
        approved_now += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    batches: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        batches.setdefault(str(row.get("batch_id") or "UNKNOWN"), []).append(row)
    for batch_id, batch_rows in batches.items():
        with (OUT_DIR / f"{batch_id}.csv").open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(batch_rows)

    counts = Counter(row.get("review_status", "") for row in rows)
    report = {
        "schema_version": 1,
        "generated_by": "tools/review_uncovered_llm_literal_batches.py",
        "source": {"path": str(SOURCE.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(SOURCE)},
        "output": {"path": str(OUT.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(OUT)},
        "counts": {
            "rows": len(rows),
            "draft_rows_reviewed_now": approved_now,
            "reviewed_text_changes_vs_legacy_sheet": len(changes),
            "current_portal_rows": current_portal_rows,
            "current_main_inherited_rows": inherited_current,
            "explicit_llm_review_changes_vs_current_main": explicit_review_changes,
            "approved_total": counts["approved"],
            "unreviewed_remaining": counts["unreviewed_draft"],
            "batches": len(batches),
        },
        "changes": changes,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print("reviewed sheet:", OUT)
    print("report:", REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
