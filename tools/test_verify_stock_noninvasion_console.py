from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import patch

from verify_stock_noninvasion import print_gate


class VerifyStockNoninvasionConsoleTest(unittest.TestCase):
    def test_print_gate_is_cp949_safe(self) -> None:
        buffer = io.BytesIO()
        stdout = io.TextIOWrapper(buffer, encoding="cp949", errors="strict")

        with patch.object(sys, "stdout", stdout):
            print_gate(
                {
                    "target": "candidate.wsc",
                    "counts": {
                        "diff_bytes": 1,
                        "runs": 1,
                        "intended_bytes": 1,
                        "unintended_bytes": 0,
                        "unintended_runs": 0,
                    },
                    "unintended_by_bank": {},
                    "dict_5f_pointer_gate": {
                        "ok": True,
                        "legacy_ok": True,
                        "legacy_gate_min_match": None,
                        "pointers_match_original": 1,
                        "pointer_count": 1,
                        "semantic": {
                            "pointers_moved": 1,
                            "accounted": 1,
                            "curated_ui_indices": 1,
                            "dialogue_baseline": "0 stored dialogue moves",
                            "opening_safe_indices": 0,
                            "unaccounted_count": 0,
                            "unaccounted": [],
                        },
                    },
                    "out_of_band_dialogue_writes": {
                        "bytes": 0,
                        "runs": 0,
                        "sites": [],
                    },
                    "ok": True,
                    "failures": [],
                }
            )
            stdout.flush()

        output = buffer.getvalue().decode("cp949")
        self.assertIn("60-69", output)
        self.assertIn("-> ok", output)
        self.assertIn("RESULT          : PASS", output)


if __name__ == "__main__":
    unittest.main()
