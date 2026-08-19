"""Regression tests for the single runtime dialogue contract."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from dialogue_runtime_contracts import (  # noqa: E402
    _payload_marker_recursive,
    physical_widths,
    semantic_widths,
    structural_prefix,
    voice_decision,
)
from expand_dictionary import payload_has_hangul_marker  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl, load_rom  # noqa: E402


class DialogueRuntimeContractTests(unittest.TestCase):
    def test_bare_18_is_visible_but_17_xx_18_is_control(self) -> None:
        bare = bytes.fromhex("18F191")
        prefix, body, kind = split_prefix_body(bare)
        self.assertEqual(prefix, b"")
        self.assertEqual(body, bare)
        self.assertEqual(kind, "dialogue")

        tagged = bytes.fromhex("173418F191")
        prefix, body, kind = split_prefix_body(tagged)
        self.assertEqual(prefix, bytes.fromhex("173418"))
        self.assertEqual(body, bytes.fromhex("F191"))
        self.assertEqual(kind, "dialogue")
        self.assertEqual(structural_prefix(bare, role="continuation").body, bare)

    def test_visible_and_metadata_anchors_are_opposite(self) -> None:
        # These two screen/raw-byte anchors are the current explicit runtime-visible
        # overrides. Older 5D01F4/5D5D58/5EBB7A rows are intentionally quarantined.
        for address in (0x5DC23D, 0x5E9885):
            decision = voice_decision(b"\x00", address)
            self.assertEqual(decision.route, "battle_body_only")
            self.assertEqual(decision.prefix, b"")
        metadata = voice_decision(bytes.fromhex("35E5183A43"), 0x5D7084)
        self.assertEqual(metadata.route, "battle_tagged")
        self.assertEqual(metadata.prefix, bytes.fromhex("35"))

    def test_ext3_capability_is_route_specific(self) -> None:
        self.assertFalse(voice_decision(b"", 0x5D01F4).ext3_supported)
        self.assertTrue(voice_decision(bytes.fromhex("35"), 0x5D7084).ext3_supported)

    def test_marker_guard_rejects_tbl_only_hangul(self) -> None:
        rom = bytes(load_rom(ROOT / "out/patch/monoeye_ko_expanded.wsc"))
        dictionary = make_dictionary_ext3(
            rom,
            load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
            load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
        )
        tbl = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")
        raw_without_marker = bytes.fromhex("E75A")  # TBL renders 나
        self.assertEqual(dictionary.expand(raw_without_marker, tbl), "나")
        self.assertFalse(payload_has_hangul_marker(raw_without_marker))
        self.assertFalse(_payload_marker_recursive(raw_without_marker, dictionary))
        self.assertEqual(marker_code(), 0xEC8D)

    def test_physical_width_counts_padding_and_e62f_splits(self) -> None:
        rendered = "가나다<E62F>라마\u3000\u3000"
        self.assertEqual(physical_widths(rendered), [3, 4])
        self.assertEqual(semantic_widths(rendered), [3, 2])

    def test_generated_main_contract_is_current(self) -> None:
        manifest = json.loads(
            (ROOT / "out/script/dialogue_runtime_contracts.json").read_text(encoding="utf-8")
        )
        tip = (ROOT / "out/patch/monoeye_ko_expanded.wsc").read_bytes()
        self.assertEqual(
            manifest["baseline_target"]["sha256"],
            hashlib.sha256(tip).hexdigest(),
        )
        by_address = {row["address"]: row for row in manifest["contracts"]}
        for address in ("5DC23D", "5E9885"):
            self.assertEqual(by_address[address]["route"], "battle_body_only")
            self.assertEqual(by_address[address]["confidence"], "runtime-proven")
        self.assertEqual(by_address["5D7084"]["metadata_hex"], "35")
        for address in ("61E234", "62663E", "627FB5"):
            self.assertEqual(by_address[address]["route"], "scenario_first")
            self.assertEqual(by_address[address]["confidence"], "runtime-proven")
            self.assertFalse(by_address[address]["decoder"]["ext3"])


if __name__ == "__main__":
    unittest.main()
