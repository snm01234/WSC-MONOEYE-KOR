#!/usr/bin/env python3
"""Write the first LLM review result batch without touching the canonical sheet.

The batch is intentionally emitted as a structural-hold result: semantic review
is complete, but continuation records remain blocked by the runtime contract
preclear.  This keeps translation work auditable without promoting unsafe bytes.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "out/script/main_translation_llm_review/batches/MR0001.csv"
OUT_DIR = ROOT / "out/script/main_translation_llm_review/results"
OUT = OUT_DIR / "MR0001_reviewed.csv"
MANIFEST = OUT_DIR / "MR0001_result_manifest.json"
GLOSSARY = ROOT / "data/main_translation_glossary_ko.json"


# Full-width spaces follow the current sheet's display convention.  Text is
# translated from source_jp; current_ko is retained only as a defect reference.
TRANSLATIONS = {
    "600005": "……뭐지？",
    "600019": "분명　긴가남의　군세가",
    "600027": "기다리고　있을　줄　알았는데……",
    "600036": "……우리와　적대할　생각은　없는　건가？",
    "60004E": "설마、　항복이라도　하겠다는　건가……？",
    "600067": "브라이트　함장님！",
    "600070": "문레이스　측에서　통신입니다！！",
    "60007E": "아그리파　멘테너라는　인물이",
    "60008A": "우리와의　회담을　요구하고　있습니다！！",
    "60009C": "아그리파라고！？",
    "6000A8": "……알고　계십니까？",
    "6000B8": "문레이스　정무를　맡은　가문입니다。",
    "6000D0": "긴가남의　배후에　그자가　있었다니……",
    "6000E5": "……그래서、",
    "6000ED": "어떻게　하시겠습니까、　디아나　여왕？",
    "600105": "……제안에　응하도록　하죠。",
    "600119": "아그리파　멘테너의　진의를",
    "600125": "확인해야　합니다。",
    "60013A": "……알겠습니다。",
    "60014E": "헨켄　중령님！",
    "600157": "넬　아가마를　부탁합니다。",
    "600165": "저는　라디시로　디아나　여왕을",
    "600171": "아그리파에게　직접　모셔　가겠습니다！",
    "600182": "……라디시로？",
    "600190": "넬　아가마는",
    "60019A": "만일에　대비해　여기　남겨　두겠습니다。",
    "6001A9": "그동안의　지휘는　헨켄　중령、",
    "6001B7": "당신에게　맡기고　싶습니다。",
    "6001C5": "……음、　알겠네。",
    "6001DC": "잠깐、　브라이트　함장。",
    "6001ED": "해리　중위？",
    "600201": "디아나　님께서　가신다면",
    "60020F": "저도　동행해야겠지요。",
    "600222": "……어쩔　수　없군。",
    "60022E": "그럼、　해리　중위의　동행을　허가한다。",
    "600244": "……알겠다！",
    "600258": "당신이　아그리파　공이군요。",
    "600265": "디아나　여왕을　모셔　왔습니다。",
    "60027B": "오、　이거　참　디아나　폐하！",
    "600289": "무사히　돌아오셨군요！",
    "60029D": "……아그리파　멘테너！！",
    "6002AA": "그대에게　따져　물을　것이　있다！！",
    "6002C7": "어째서　김　긴가남을　움직였나！",
    "6002DA": "그자가　나서면、　전란이　더욱　커진다",
    "6002F1": "그건　그대도　알고　있을　텐데！？",
    "600308": "우리　문레이스의　평화를　지키려면",
    "600319": "어쩔　수　없는　일이었습니다。",
    "600326": "게다가　긴가남의　제멋대로인　방식에는",
    "600338": "저도　정말　곤란을　겪고　있었습니다……",
    "60034E": "……윽！！",
    "600358": "그런　애매한　태도가",
    "600365": "사태를　더욱　악화시키고　있지　않나！！",
    "600382": "……라디시가！？",
    "600396": "……긴가남의　부하인가！？",
    "6003AA": "디아나　님、　조심하십시오！！",
    "6003C3": "잘　들어라、　에우고라는　놈들！！",
    "6003D7": "이　자리에서　순순히　항복하면　된다！",
    "6003EA": "그렇지　않으면、　이　자리에　있는",
    "6003F7": "네놈들의　지휘관을　처형하겠다！！",
    "60040B": "……뭐라고！！",
}


def glossary_ids(source: str) -> str:
    """Return current glossary ids whose JP key or alias occurs in source.

    The glossary is the single source of truth for official spellings and
    orthographic variants.  Keeping this lookup data-driven prevents the
    review result writer from drifting from ``main_translation_glossary_ko``.
    """
    glossary = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    matched = []
    for entry in glossary.get("entries", []):
        terms = [entry.get("jp", ""), *(entry.get("aliases") or [])]
        if any(term and term in source for term in terms):
            matched.append(entry["id"])
    return ";".join(sorted(set(matched)))


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
        proposed = TRANSLATIONS[row["abs"]]
        out = dict(row)
        out.update(
            {
                "source_model": "GPT-5.6 current Codex model (Luna unavailable in this runtime)",
                "reviewed_at": date.today().isoformat(),
                "glossary_ids": glossary_ids(row["source_jp"]),
                "proposed_ko": proposed,
                "reviewer_notes": (
                    "일본어 원문 기준 문맥 재번역. continuation 행은 앞 행과 한 문장으로 읽었으며, "
                    "현재 런타임 계약의 구조 보류를 해제하지 않음."
                ),
                "new_translation_source": "llm",
                "new_review_status": "llm_retranslated_structural_hold",
                "apply_status": "not_applied_structural_preclear",
            }
        )
        out_rows.append(out)
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(out_rows)

    manifest = {
        "schema_version": 1,
        "batch_id": "MR0001",
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
