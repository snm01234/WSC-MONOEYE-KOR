import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "out/script/main_translation_llm_review/results/MR0001_reviewed.csv"
MANIFEST = ROOT / "out/script/main_translation_llm_review/results/MR0001_result_manifest.json"


def main() -> None:
    rows = list(csv.DictReader(RESULT.open(encoding="utf-8-sig", newline="")))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert len(rows) == 60
    assert len({row["bundle_id"] for row in rows}) == 30
    assert all(row["new_translation_source"] == "llm" for row in rows)
    assert all(row["new_review_status"] == "llm_retranslated_structural_hold" for row in rows)
    assert all(row["apply_status"] == "not_applied_structural_preclear" for row in rows)
    assert all(row["proposed_ko"] for row in rows)
    assert all(len(row["proposed_ko"]) <= 20 for row in rows)
    assert all(not any("ぁ" <= char <= "ヿ" for char in row["proposed_ko"]) for row in rows)
    assert manifest["semantic_review"] == "complete"
    assert manifest["structural_status"] == "hold"
    assert manifest["canonical_sheet_changed"] is False
    assert manifest["rom_changed"] is False
    assert manifest["saveram_changed"] is False
    print("MR0001 review result: OK")


if __name__ == "__main__":
    main()
