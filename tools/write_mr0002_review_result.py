#!/usr/bin/env python3
"""Emit semantic review results for MR0002 while retaining structural hold."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from write_mr0001_review_result import glossary_ids

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "out/script/main_translation_llm_review/batches/MR0002.csv"
OUT_DIR = ROOT / "out/script/main_translation_llm_review/results"
OUT = OUT_DIR / "MR0002_reviewed.csv"
MANIFEST = OUT_DIR / "MR0002_result_manifest.json"

TRANSLATIONS = {
    "600424": "……움직이지　마라！！",
    "600434": "아그리파！",
    "60043C": "이게　어떻게　된　일입니까！！",
    "60044B": "오오……！！",
    "600455": "이、　이런　짓을　긴가남에게는……！",
    "60046D": "이런　일은　명령하지　않았다！",
    "600481": "저、　저자가　멋대로　꾸민　일입니다！！",
    "60049F": "이런　자가　문레이스의　지도자라고",
    "6004AD": "행세하고　있으니、　웃음이　나지　않나。",
    "6004C4": "……네놈은！！",
    "6004DB": "이렇게　직접　만나는　건",
    "6004E5": "처음이었던가、　브라이트　중령。",
    "6004F4": "사실　크와트로　대위라도　와줬다면",
    "600507": "인질의　가치도　더　높아졌을　텐데……",
    "60052C": "어이쿠、　너무　움직이지　말게。",
    "60053B": "난　사격이　별로　능숙하지　않거든……",
    "600547": "손이　미끄러져　머리에　맞을지도",
    "60055E": "모르니까　말이야、　후후후。",
    "600572": "긴가남　함대、　디아나　카운터의",
    "600581": "모빌슈트　부대、　전개　중！！",
    "600591": "음……！！",
    "6005A0": "……곤란하군。",
    "6005A9": "우릴　옭아맬　셈인가……！",
    "6005B5": "하지만　응전하려　해도",
    "6005C2": "브라이트　중령을　방패로　잡혀서는……！",
    "6005D9": "……뭐지！？",
    "6005EC": "……이쪽이다！！",
    "6005FC": "너、　너희는……！？",
    "60060C": "……됐으니、　빨리！！",
    "60061E": "디아나　여왕……？",
    "600634": "……갑시다。",
    "600647": "……헨켄　함장님！",
    "600652": "백색　궁전에서　통신입니다！！",
    "600661": "브라이트　함장　일행이",
    "600669": "회견장에서　탈출했다고　합니다！！",
    "600678": "……뭐라고！！　정말인가！？",
    "60068E": "궁전에서　위병에게　쫓기고　있습니다！",
    "6006A4": "그렇다면……",
    "6006B7": "……전　부대、　제１종　전투배치！！",
    "6006C7": "브라이트　중령　일행을　구출한다！",
    "6006D4": "넬　아가마는",
    "6006DC": "전속력으로　백색　궁전을　향해라！！",
    "6006EC": "마더　뱅가드는",
    "6006F4": "넬　아가마를　엄호하라！！",
    "600706": "여기는　마더　뱅가드！",
    "600712": "알겠습니다！！",
    "600722": "……좋아、　알겠나！！",
    "600735": "넬　아가마를",
    "60073F": "이　건물까지　전진시켜라！",
    "60074C": "브라이트　중령　일행은　여기에　있다！！",
    "600762": "……로랑　군！！",
    "60076D": "턴　X는　자네에게　맡기마！",
    "600782": "예、　옛！　해보겠습니다！！",
    "600799": "추격자는　따돌린　모양이군……",
    "6007AB": "………………",
    "6007B7": "……대체　무슨　일이지？",
    "6007C1": "자네는　네오　지온　사람이지？",
    "6007CF": "왜　우리를　돕는　거지？",
    "6007F0": "하만　칸의！？",
    "6007FC": "그건　대체……",
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
        "batch_id": "MR0002",
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
