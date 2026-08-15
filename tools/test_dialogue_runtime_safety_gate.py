"""Regression tests for the contract-only dialogue safety entry point."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from audit_dialogue_runtime_safety_gate import audit  # noqa: E402
from monoeye_rom import find_rom, load_rom  # noqa: E402


class RuntimeSafetyGateTests(unittest.TestCase):
    def test_candidate_contract_gate_has_no_baseline_failures(self) -> None:
        gate = json.loads(
            (ROOT / "out/patch/dialogue_runtime_contract_candidate_safety.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["counts"]["hard_failures"], 0)

    def test_entry_point_rebuilds_exact_target_contract(self) -> None:
        target_path = ROOT / "out/patch/dialogue_runtime_contract_candidate.wsc"
        target = bytes(load_rom(target_path))
        original = bytes(load_rom(find_rom(ROOT)))
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "contract.json"
            report = audit(
                target,
                original,
                target_path=target_path,
                manifest_path=manifest_path,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertTrue(report["ok"])
        self.assertEqual(report["counts"]["hard_failures"], 0)
        self.assertEqual(
            manifest["baseline_target"]["sha256"],
            report["target"]["sha256"],
        )

    def test_legacy_20cell_cli_is_quarantined(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/audit_dialogue_20cell_candidate.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("quarantined", result.stdout)

    def test_legacy_evidence_matrix_cli_is_quarantined(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/audit_dialogue_runtime_evidence_matrix.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("quarantined", result.stdout)

    def test_contract_gate_source_contains_no_legacy_fallback(self) -> None:
        source = (ROOT / "tools/audit_dialogue_runtime_safety_gate.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("battle_dialogue_structure_inventory.csv", source)
        self.assertNotIn("Legacy heuristic audit", source)
        self.assertNotIn("APPROVED_COMPACT3", source)


if __name__ == "__main__":
    unittest.main()
