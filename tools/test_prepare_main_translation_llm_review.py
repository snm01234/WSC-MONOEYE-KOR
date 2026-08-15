from __future__ import annotations

import csv
import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out/script/main_translation_llm_review"


class MainTranslationReviewPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = json.loads((OUT / "plan.json").read_text(encoding="utf-8"))

    def test_plan_is_current_tip_bound_and_green(self) -> None:
        main = ROOT / "out/patch/monoeye_ko_expanded.wsc"
        digest = hashlib.sha256(main.read_bytes()).hexdigest()
        self.assertEqual(self.plan["summary"]["main_tip_sha256"], digest)
        self.assertTrue(self.plan["overall_ok"])
        self.assertTrue(all(self.plan["gates"].values()))

    def test_population_reconciles(self) -> None:
        summary = self.plan["summary"]
        self.assertEqual(summary["source_sheet_rows"], 33304)
        self.assertEqual(summary["runtime_contract_rows_in_sheet"], 14378)
        self.assertEqual(summary["semantic_target_rows"], 14373)
        self.assertEqual(summary["evidence_exempt_rows"], 5)
        self.assertEqual(summary["structural_preclear_rows"], 7616)
        self.assertEqual(summary["excluded_rows"], 18614)

    def test_all_future_result_fields_are_blank(self) -> None:
        with (OUT / "targets.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 14373)
        for row in rows:
            self.assertFalse(row["proposed_ko"].strip())
            self.assertFalse(row["reviewer_notes"].strip())
            self.assertFalse(row["new_translation_source"].strip())
            self.assertFalse(row["new_review_status"].strip())

    def test_structural_gate_blocks_unresolved_semantic_batches(self) -> None:
        with (OUT / "batch_index.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        counts = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        self.assertEqual(counts["ready_for_llm_review"], 1)
        self.assertEqual(counts["blocked_pending_structural_preclear"], 244)

    def test_exempt_rows_have_both_evidence_classes_and_no_risk(self) -> None:
        with (OUT / "inventory.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = [
                row for row in csv.DictReader(handle)
                if row.get("disposition") == "evidence_exempt_no_quality_flag"
            ]
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertEqual(row["explicit_llm_provenance"], "yes")
            self.assertEqual(row["completed_review_evidence"], "yes")
            self.assertFalse(row["quality_flags"].strip())


if __name__ == "__main__":
    unittest.main()
