"""Regression tests for the current-TIP Garrod native-stock guard."""
from __future__ import annotations

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_garrod_native_stock_guard import (
    DEFAULT_SOURCE,
    DEFAULT_TARGET,
    EXPECTED_MIXED_EXT3_ADDRESSES,
    build_report,
    exact_native_two_token,
    unit_kinds,
)


class GarrodNativeStockGuardTests(unittest.TestCase):
    def test_native_two_token_parser_is_strict(self) -> None:
        self.assertEqual(unit_kinds(bytes.fromhex("FD4BF191")), ["dict", "dict"])
        self.assertTrue(exact_native_two_token(bytes.fromhex("FD4BF191")))
        self.assertFalse(exact_native_two_token(bytes.fromhex("F8E78B03")))
        self.assertFalse(exact_native_two_token(bytes.fromhex("FD4BF19101")))

    def test_current_tip_guard_passes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = build_report(root / DEFAULT_TARGET.relative_to(root), root / DEFAULT_SOURCE.relative_to(root))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["counts"]["structural_families"], 837)
        self.assertEqual(report["counts"]["source_exact_native_two_token"], 63)
        self.assertEqual(report["counts"]["source_exact_native_two_token_current_non_native"], 0)
        self.assertEqual(report["counts"]["current_exact_ext3_risk_shape"], 18)
        self.assertEqual(report["counts"]["current_ext3_source_exact_native_two_token"], 0)
        self.assertEqual(report["counts"]["current_ext3_source_mixed_grammar"], 18)
        self.assertEqual(
            {int(row["logical"], 16) for row in report["current_ext3_risk_population"]},
            EXPECTED_MIXED_EXT3_ADDRESSES,
        )

    def test_garrod_and_5997bf_scope_pins_are_exact(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = build_report(root / DEFAULT_TARGET.relative_to(root), root / DEFAULT_SOURCE.relative_to(root))
        self.assertTrue(report["guards"]["garrod_anchors_byte_exact"])
        self.assertTrue(report["guards"]["garrod_nul_separator_control_pins_exact"])
        self.assertTrue(report["guards"]["god_gundam_5997bf_cross_scope_exact"])
        self.assertEqual(report["issues"], [])


if __name__ == "__main__":
    unittest.main()
