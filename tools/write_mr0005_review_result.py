#!/usr/bin/env python3
"""Emit semantic review results for MR0005 while retaining structural hold."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from write_mr0001_review_result import glossary_ids

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "out/script/main_translation_llm_review/batches/MR0005.csv"
OUT_DIR = ROOT / "out/script/main_translation_llm_review/results"
OUT = OUT_DIR / "MR0005_reviewed.csv"
MANIFEST = OUT_DIR / "MR0005_result_manifest.json"

TRANSLATIONS = {
    "601017": "……뭐라고！？",
    "601021": "무슨　속셈인가、　미하엘　대령！",
    "601031": "멋대로　배를　내리면　안　된다！",
    "601050": "나는　더는　도련님을",
    "60105C": "따라갈　수　없다고　말씀드렸습니다！",
    "601076": "네、　네놈들……！",
    "601083": "꾸몄구나！！",
    "601095": "저에게는　음모를　꾸밀",
    "6010A2": "용기도　지혜도　없습니다！",
    "6010D1": "승무원은　내　지시를　따라라！",
    "6010E1": "윌게임을　포기한다！",
    "6010F3": "……기、　기다려！！",
    "601110": "이제　이　배는　끝장이다！",
    "601122": "도망쳐、　도망쳐！！",
    "60113E": "부하에게까지　버림받고……",
    "60114C": "나는……",
    "601170": "하하하하핫！！",
    "601179": "이젠　이렇게　된　이상……！！",
    "60118B": "저건！？",
    "601193": "사이코　건담！？",
    "6011A3": "결국　의지할　건",
    "6011B0": "나　하나뿐……",
    "6011B8": "이　블랙　돌로　모든　것을　파괴하고、",
    "6011CB": "내　왕국을　세워　보이겠다！！",
    "6011E3": "로라는　어째서",
    "6011ED": "내　곁에　있으려　하지　않는　거지！",
    "601200": "나는……　나는아아앗！！",
    "601218": "그렇게　남자가　좋다면",
    "601226": "치마라도　입으세요！！",
    "60123F": "로라아아앗！！",
    "601258": "………………",
    "601269": "로라……",
    "601271": "나의…　실버　퀸……",
    "601298": "……구엔　라인포드。",
    "6012A5": "어리석은……！",
    "6012B6": "……아인！！",
    "6012CC": "또　너냐！！",
    "6012D9": "적당히　좀　해、　거슬린다고！！",
    "6012F3": "……사라져　버려！！",
    "601327": "그게　어쨌다는　거야！！",
    "601334": "이　데스파다도",
    "60133C": "파워업했다고！！",
    "601347": "너　따위에게　질까　보냐！！",
    "601361": "……아니。",
    "60136B": "이제　두　번　다시……",
    "60137F": "나는！",
    "601387": "네놈에게　당하지　않겠다！！",
    "601394": "가짜　힘으로　싸우는　네놈은！！",
    "6013AF": "끝이다、　아인！！",
    "6013C6": "어、　어째서……！？",
    "6013DC": "그저　올드타입에게",
    "6013E5": "어째서　내가　이렇게……",
    "6013FA": "결판을　낼　때가　온　모양이군、",
    "60140E": "……아인。",
    "60141D": "이　자식！……건방지다고！！",
    "601434": "후후……　도망쳐도　소용없어",
    "60144E": "아직이다！",
    "601455": "아직　죽을　수는……　없어！！",
    "60146F": "당、　당하는　건가……！？",
    "601485": "무사하십니까、　마스터。",
}


def main() -> None:
    rows = list(csv.DictReader(SOURCE.open(encoding="utf-8-sig", newline="")))
    missing = sorted(set(r["abs"] for r in rows) - set(TRANSLATIONS))
    extra = sorted(set(TRANSLATIONS) - set(r["abs"] for r in rows))
    if missing or extra:
        raise SystemExit(f"mapping mismatch: missing={missing} extra={extra}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    for field in ["source_model", "reviewed_at", "glossary_ids", "apply_status"]:
        if field not in fieldnames:
            fieldnames.append(field)
    out_rows = []
    for row in rows:
        out = dict(row)
        out.update({
            "source_model": "GPT-5.6 current Codex model (Luna unavailable in this runtime)",
            "reviewed_at": date.today().isoformat(),
            "glossary_ids": glossary_ids(row["source_jp"]),
            "proposed_ko": TRANSLATIONS[row["abs"]],
            "reviewer_notes": "일본어 원문과 실제 bundle 문맥 기준 재번역. runtime contract quarantine은 해제하지 않음.",
            "new_translation_source": "llm",
            "new_review_status": "llm_retranslated_structural_hold",
            "apply_status": "not_applied_structural_preclear",
        })
        out_rows.append(out)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)
    manifest = {
        "schema_version": 1, "batch_id": "MR0005",
        "source_batch": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "result": str(OUT.relative_to(ROOT)).replace("\\", "/"),
        "rows": len(out_rows), "bundles": len({r["bundle_id"] for r in out_rows}),
        "semantic_review": "complete", "structural_status": "hold", "apply_status": "not_applied",
        "main_tip_sha256": rows[0]["main_tip_sha256"],
        "source_body_sha256_set": sorted({r["source_body_sha256"] for r in rows}),
        "source_model": "GPT-5.6 current Codex model (Luna unavailable in this runtime)",
        "translation_source": "llm", "review_status": "llm_retranslated_structural_hold",
        "reason": "continuation rows remain runtime-contract quarantine until structural preclear",
        "canonical_sheet_changed": False, "rom_changed": False, "saveram_changed": False,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(OUT), "manifest": str(MANIFEST), "rows": len(out_rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
