#!/usr/bin/env python3
"""Emit semantic review results for MR0004 while retaining structural hold."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from write_mr0001_review_result import glossary_ids

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "out/script/main_translation_llm_review/batches/MR0004.csv"
OUT_DIR = ROOT / "out/script/main_translation_llm_review/results"
OUT = OUT_DIR / "MR0004_reviewed.csv"
MANIFEST = OUT_DIR / "MR0004_result_manifest.json"

TRANSLATIONS = {
    "600C02": "정말이지、　쓸모없는　남자네……",
    "600C17": "곤란하면　긴가남을　막든가",
    "600C2A": "에우고를　붙잡든가　할　것이지",
    "600C41": "그、　그런　말을　들어도　나는……",
    "600C5C": "그　남자에게　뭔가　기대한다는　건",
    "600C6E": "헛된　일이다。",
    "600C7D": "이미　긴가남을　풀어놓는",
    "600C8E": "역할은　해줬으니……",
    "600CA7": "……그걸로　만족해야지。",
    "600CBB": "……늦었군。",
    "600CC7": "티탄즈　쪽은　괜찮은가？",
    "600CD6": "……올바가　잘해주고　있다。",
    "600CE8": "이제　곧　시작될　거다。",
    "600CFF": "그럼、　이　녀석은　용도　폐기인가？",
    "600D14": "……히익！！",
    "600D1F": "저、　저、　저、　저、",
    "600D2A": "이　몸을、　죽이려는　거냐！？",
    "600D41": "뭐라고……",
    "600D49": "죽는　건　너　하나만이　아니다。",
    "600D58": "머지않아　더　많은　사람이",
    "600D66": "네　곁으로　갈　거다……",
    "600D74": "……외롭지는　않을　거다。",
    "600D91": "……그럼、　작별이다。",
    "600DA9": "그럼……",
    "600DB1": "뒷일은　맡기마、　아인　레비。",
    "600DDB": "……자、　그럼。",
    "600DE5": "에우고　녀석들에게는",
    "600DED": "조금　더　우리와　놀아줘야겠지。",
    "600E0A": "……아인인가！！",
    "600E1E": "아앗！　큰일이다……！！",
    "600E39": "큰일이다！！",
    "600E40": "턴에이가　당하면　턴　X가……！！",
    "600E58": "방해꾼은　사라졌나！！",
    "600E66": "하하하핫！",
    "600E78": "이　무슨　일이란　말인가……！",
    "600E80": "턴에이가　당하면　턴　X가……！！",
    "600E98": "방해꾼은　사라졌나！！",
    "600EA6": "하하하핫！",
    "600EB8": "으오오옷！　이、　이런　곳에서……",
    "600ED4": "필、　필　소령님！？",
    "600EE3": "그、　그럴　수가……！！",
    "600EF5": "아앗！！",
    "600EFD": "필　소령님！！",
    "600F19": "로라！",
    "600F20": "내게로　돌아오는　거다！",
    "600F38": "그만두세요！！",
    "600F40": "……저는　남자라고요！",
    "600F50": "그래서　어쨌다는　거냐！！",
    "600F64": "자네는　내　실버　퀸으로서",
    "600F74": "나와　함께　세계를……",
    "600F83": "……제정신이　아니군요！！",
    "600F93": "저는　로라도",
    "600F9C": "당신의　인형도　아니에요！！",
    "600FAE": "로라！　나는……！！",
    "600FC4": "……우와아악！",
    "600FCF": "이제　끝장이야、　이건！！",
    "600FE3": "……………",
    "600FFB": "총원　퇴함하라！",
    "601004": "윌게임을　포기한다！！",
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
        "schema_version": 1, "batch_id": "MR0004",
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
