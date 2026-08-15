from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from verify_stock_noninvasion import (
    load_approved_detachment,
    load_approved_stock_indices,
)


class ApprovedStockReportTest(unittest.TestCase):
    def _document(self) -> dict:
        return {
            "generated_by": "tools/build_p2_stock_spill_candidate.py",
            "mode": "pre_gate_approval",
            "ok": True,
            "candidate_rom": {"sha256": "a" * 64},
            "approved_stock_slots": ["0D98", "0EE9"],
            "proof": {
                "union_true_free": True,
                "tail_was_all_ff": True,
                "changed_pointer_indices_exact": True,
                "nonselected_pointers_preserved": True,
                "nonselected_payloads_preserved": True,
                "bank5f_diffs_within_approved_extents": True,
            },
        }

    def _detachment_document(self) -> dict:
        return {
            "generated_by": "tools/build_p2_duplicate_detach_candidate.py",
            "mode": "pre_gate_detachment_approval",
            "ok": True,
            "candidate_rom": {"sha256": "b" * 64},
            "approved_stock_slots": ["0C91"],
            "approved_detachment_ranges": [
                {
                    "logical_start": "590CF0",
                    "logical_end_exclusive": "590CF2",
                    "owner_id": "detach:0C91->090D",
                }
            ],
            "proof": {
                "duplicate_payload_equal_before": True,
                "historical_consumers_accounted": True,
                "all_current_external_refs_retargeted": True,
                "all_current_nested_parents_retargeted": True,
                "detachment_stage_zero_old_refs": True,
                "former_consumer_render_preserved": True,
                "candidate_new_consumers_exact": True,
                "tail_was_all_ff": True,
                "changed_pointer_indices_exact": True,
                "nonselected_pointers_preserved": True,
                "nonselected_payloads_preserved": True,
                "bank5f_diffs_within_approved_extents": True,
                "detachment_diffs_within_approved_extents": True,
            },
        }

    def _retired_document(self) -> dict:
        document = self._detachment_document()
        document["generated_by"] = "tools/build_p2_retired_slot_reclaim_candidate.py"
        document["approved_stock_slots"] = ["0C91", "0338"]
        document["approved_detachment_ranges"].append(
            {
                "logical_start": "75C198",
                "logical_end_exclusive": "75C19B",
                "owner_id": "retired_target:name75:75C198",
            }
        )
        document["retired_slot_reclaim"] = {
            "selected_slots": [
                {
                    "slot": "0338",
                    "original_parent_pointer_equal": True,
                    "original_parent_payload_equal": True,
                    "current_external_count": 0,
                    "current_nested_count": 0,
                    "original_nested_count": 0,
                    "current_raw_pair_hits": 0,
                }
            ],
            "stage_target_records": [
                {
                    "record_id": "name75:75C198",
                    "abs": "75C198",
                    "logical_start": "75C198",
                    "logical_end_exclusive": "75C19B",
                }
            ],
        }
        document["proof"].update(
            {
                "retired_slots_original_parent_pointer_payload_equal": True,
                "retired_slots_current_external_zero": True,
                "retired_slots_current_nested_zero": True,
                "retired_slots_original_nested_zero": True,
                "retired_slots_current_raw_pair_zero": True,
                "retired_slots_historical_consumers_accounted": True,
                "retired_slots_former_render_preserved": True,
                "retired_slots_new_consumers_exact": True,
                "retired_slots_selected_exact": True,
                "retired_stage_target_ranges_exact": True,
            }
        )
        return document

    def _repair_document(self) -> dict:
        document = self._detachment_document()
        document["generated_by"] = (
            "tools/build_p2_slot0208_stage_name_repair_candidate.py"
        )
        document["approved_stock_slots"] = ["0208", "033F", "0C91"]
        document["slot0208_stage_name_repair"] = {
            "shared_slot": "0208",
            "replacement_slot": "033F",
            "hidden_stage_records_after": [
                {"record_abs": "75BD5E", "after_render": "テキサス공역"}
            ],
            "migrated_records": [
                {"record_id": "script:608F2E", "after_token": "F33F"}
            ],
        }
        document["proof"].update(
            {
                "slot0208_restored_to_shared_payload": True,
                "replacement_slot_strong_retired": True,
                "replacement_slot_points_to_existing_oo_payload": True,
                "oo_targets_migrated_exact": True,
                "hidden_stage_name_consumers_restored": True,
                "repair_pointer_changes_exact": True,
                "repair_record_changes_exact": True,
            }
        )
        return document

    def _write(self, root: Path, document: dict) -> Path:
        path = root / "approval.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_valid_report_returns_candidate_bound_non_ff_slots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(Path(directory), self._document())
            indices, sha = load_approved_stock_indices(path)

        self.assertEqual(indices, {0x0D98, 0x0EE9})
        self.assertEqual(sha, "a" * 64)

    def test_missing_proof_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._document()
            document["proof"]["nonselected_payloads_preserved"] = False
            path = self._write(Path(directory), document)
            with self.assertRaisesRegex(SystemExit, "lacks required proof"):
                load_approved_stock_indices(path)

    def test_valid_detachment_report_returns_slot_sha_and_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(Path(directory), self._detachment_document())
            indices, sha, ranges = load_approved_detachment(path)

        self.assertEqual(indices, {0x0C91})
        self.assertEqual(sha, "b" * 64)
        self.assertEqual(ranges, ((0x590CF0, 0x590CF2, "detach:0C91->090D"),))

    def test_detachment_missing_proof_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._detachment_document()
            document["proof"]["former_consumer_render_preserved"] = False
            path = self._write(Path(directory), document)
            with self.assertRaisesRegex(SystemExit, "lacks required proof"):
                load_approved_detachment(path)

    def test_detachment_range_outside_stock_rom_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._detachment_document()
            document["approved_detachment_ranges"][0]["logical_end_exclusive"] = "800001"
            path = self._write(Path(directory), document)
            with self.assertRaisesRegex(SystemExit, "outside stock logical ROM"):
                load_approved_detachment(path)

    def test_cumulative_detachment_requires_inheritance_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._detachment_document()
            document["inherited_approvals"] = {
                "stock_preservation": [
                    {
                        "index": "0D98",
                        "pointer_preserved": True,
                        "payload_preserved": True,
                    }
                ],
                "detachment_ranges_preserved": True,
            }
            path = self._write(Path(directory), document)
            with self.assertRaisesRegex(SystemExit, "lacks required proof"):
                load_approved_detachment(path)

    def test_batch_detachment_generator_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._detachment_document()
            document["generated_by"] = "tools/build_p2_duplicate_batch_candidate.py"
            path = self._write(Path(directory), document)
            indices, sha, ranges = load_approved_detachment(path)

        self.assertEqual(indices, {0x0C91})
        self.assertEqual(sha, "b" * 64)
        self.assertEqual(ranges, ((0x590CF0, 0x590CF2, "detach:0C91->090D"),))

    def test_local_ext3_expansion_generator_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._detachment_document()
            document["generated_by"] = (
                "tools/build_p2_local_ext3_expansion_candidate.py"
            )
            path = self._write(Path(directory), document)
            indices, sha, ranges = load_approved_detachment(path)

        self.assertEqual(indices, {0x0C91})
        self.assertEqual(sha, "b" * 64)
        self.assertEqual(
            ranges,
            ((0x590CF0, 0x590CF2, "detach:0C91->090D"),),
        )

    def test_retired_slot_generator_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(Path(directory), self._retired_document())
            indices, sha, ranges = load_approved_detachment(path)

        self.assertEqual(indices, {0x0C91, 0x0338})
        self.assertEqual(sha, "b" * 64)
        self.assertIn((0x75C198, 0x75C19B, "retired_target:name75:75C198"), ranges)

    def test_retired_slot_missing_proof_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._retired_document()
            document["proof"]["retired_slots_current_raw_pair_zero"] = False
            path = self._write(Path(directory), document)
            with self.assertRaisesRegex(SystemExit, "lacks required proof"):
                load_approved_detachment(path)

    def test_slot0208_repair_generator_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(Path(directory), self._repair_document())
            indices, sha, ranges = load_approved_detachment(path)

        self.assertEqual(indices, {0x0208, 0x033F, 0x0C91})
        self.assertEqual(sha, "b" * 64)
        self.assertEqual(ranges[0][:2], (0x590CF0, 0x590CF2))

    def test_slot0208_repair_missing_proof_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._repair_document()
            document["proof"]["hidden_stage_name_consumers_restored"] = False
            path = self._write(Path(directory), document)
            with self.assertRaisesRegex(SystemExit, "lacks required proof"):
                load_approved_detachment(path)

    def test_valid_cumulative_detachment_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._detachment_document()
            document["approved_stock_slots"] = ["0D98", "0C91"]
            document["inherited_approvals"] = {
                "stock_preservation": [
                    {
                        "index": "0D98",
                        "pointer_preserved": True,
                        "payload_preserved": True,
                    }
                ],
                "detachment_ranges_preserved": True,
            }
            document["proof"].update(
                {
                    "inherited_stock_slots_preserved": True,
                    "inherited_detachment_ranges_preserved": True,
                    "inherited_approval_candidate_matches_parent": True,
                }
            )
            path = self._write(Path(directory), document)
            indices, sha, ranges = load_approved_detachment(path)

        self.assertEqual(indices, {0x0D98, 0x0C91})
        self.assertEqual(sha, "b" * 64)
        self.assertEqual(ranges[0][:2], (0x590CF0, 0x590CF2))

    def test_ff_page_slot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = self._document()
            document["approved_stock_slots"] = ["0F4D"]
            path = self._write(Path(directory), document)
            with self.assertRaisesRegex(SystemExit, "outside non-FF 5F range"):
                load_approved_stock_indices(path)


if __name__ == "__main__":
    unittest.main()
