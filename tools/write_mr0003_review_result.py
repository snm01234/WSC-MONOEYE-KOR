#!/usr/bin/env python3
"""Emit semantic review results for MR0003 while retaining structural hold."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from write_mr0001_review_result import glossary_ids

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "out/script/main_translation_llm_review/batches/MR0003.csv"
OUT_DIR = ROOT / "out/script/main_translation_llm_review/results"
OUT = OUT_DIR / "MR0003_reviewed.csv"
MANIFEST = OUT_DIR / "MR0003_result_manifest.json"

TRANSLATIONS = {
    "600805": "여기서　무사히　빠져나가면",
    "600812": "얼마든지　설명해　주지。",
    "60081B": "……자、　와라！",
    "60082B": "앗！　기、　기다리게나……",
    "600840": "브라이트　중령、　그의　말이　맞다。",
    "600851": "지금은　여기서　벗어나는　게　급선무다。",
    "60085F": "……자、　디아나　님、　이쪽입니다。",
    "600871": "……브라이트　함장。",
    "600880": "……아、　아아。",
    "600894": "……이쪽이다。",
    "60089D": "이　앞에　밖으로　나갈　해치가　있다。",
    "6008B0": "라디시의　승무원들도",
    "6008B9": "거기서　기다리고　있다。",
    "6008CB": "……여기는！！",
    "6008E0": "……기다려　주세요、　여러분！",
    "6008F3": "……디아나　여왕！　어디로！？",
    "600909": "제게　생각이　있습니다！！",
    "60091A": "브라이트　함장은　승무원들을",
    "600927": "모아　두십시오！",
    "60093D": "이건……！！",
    "60094C": "어떤가、　해리、　움직일　수　있겠나？",
    "600962": "……핫。",
    "60096B": "동력계는　무사한　모양입니다。",
    "600984": "브라이트　함장은　승무원　유도를。",
    "60099A": "배치가　끝나면　발진시키겠습니다。",
    "6009B3": "……뭐지！？",
    "6009C6": "……헨켄　중령！！",
    "6009DD": "브라이트　중령……　자네인가！？",
    "6009EF": "디아나　여왕도　함께입니다！",
    "600A11": "네오　지온의",
    "600A1B": "마슈마　세로가　도와주었습니다。",
    "600A32": "……네오　지온！？",
    "600A3F": "그건　대체……",
    "600A48": "자세한　사정은　저도　아직……",
    "600A59": "……아니、　어쨌든、",
    "600A63": "지금은　적을　물리치는　게　먼저입니다。",
    "600A72": "그쪽과　합류하겠습니다。",
    "600A84": "그럼、　브라이트　중령。",
    "600A90": "자네에게　넬　아가마를　돌려주겠네。",
    "600AA9": "……토레스、　상황은！",
    "600ABC": "현재　긴가남　함대의",
    "600AC8": "모빌슈트　부대와　교전　중입니다。",
    "600AD7": "디아나　여왕！",
    "600AE1": "그쪽은　괜찮으십니까！？",
    "600AFA": "……네。",
    "600B04": "라디시의　모빌슈트는　이미",
    "600B0E": "이쪽으로　옮겨　두었습니다。",
    "600B21": "함선을　전진시켜라！！",
    "600B2D": "에우고를　지원한다！",
    "600B3C": "……핫！！",
    "600B61": "호엘즈、　전진합니다！！",
    "600B7B": "아그리파　녀석……！",
    "600B84": "디아나　님을　업신여기다니！",
    "600B96": "……본때를　보여주마！",
    "600BAD": "……좋아！",
    "600BB6": "이제부터　반격에　나선다！！",
    "600BCB": "오오……　이　무슨　일인가！！",
    "600BD8": "하필이면、　이　폰　시티에서",
    "600BE7": "전투가　시작되다니！！",
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
        "schema_version": 1,
        "batch_id": "MR0003",
        "source_batch": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "result": str(OUT.relative_to(ROOT)).replace("\\", "/"),
        "rows": len(out_rows),
        "bundles": len({r["bundle_id"] for r in out_rows}),
        "semantic_review": "complete",
        "structural_status": "hold",
        "apply_status": "not_applied",
        "main_tip_sha256": rows[0]["main_tip_sha256"],
        "source_body_sha256_set": sorted({r["source_body_sha256"] for r in rows}),
        "source_model": "GPT-5.6 current Codex model (Luna unavailable in this runtime)",
        "translation_source": "llm",
        "review_status": "llm_retranslated_structural_hold",
        "reason": "continuation rows remain runtime-contract quarantine until structural preclear",
        "canonical_sheet_changed": False,
        "rom_changed": False,
        "saveram_changed": False,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": str(OUT), "manifest": str(MANIFEST), "rows": len(out_rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
