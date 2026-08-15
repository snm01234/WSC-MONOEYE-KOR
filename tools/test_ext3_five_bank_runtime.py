#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
import build_ext3_five_bank_runtime_probe_candidate as build
from apply_ext_dict_unit import (
    detect_ext3_alias_page_count,
    load_ext_meta,
    make_dictionary_ext3,
)
from extract_script import split_prefix_body
from monoeye_rom import BANK_SIZE, Tbl, le16, load_rom, read_encoded_z_safe, stock_base


class FiveBankRuntimeTests(unittest.TestCase):
    def test_generalized_leaf_identity(self) -> None:
        leaf = five.build_five_bank_leaf()
        self.assertEqual(len(leaf), 123)
        self.assertEqual(
            hashlib.sha256(leaf).hexdigest(),
            "199936d8cc33388f57711012ab1eb5f4c0b024be0f5d3e0b095a7892c48c6bf0",
        )

    def test_alias_tokens_scan_on_all_pages(self) -> None:
        data = bytearray(b"\xFF" * 64)
        expected: dict[int, int] = {}
        for page in range(five.PAGE_COUNT):
            position = 3 + page * 8
            data[position:position + 4] = build.alias_token(page, page + 1)
            expected[page] = position
        for page in range(five.PAGE_COUNT):
            self.assertEqual(five.scan_range_hits(bytes(data), page), [expected[page]])

    def test_alias_token_boundaries(self) -> None:
        self.assertEqual(build.alias_token(0, 1).hex().upper(), "E5180601")
        self.assertEqual(build.alias_token(1, 2).hex().upper(), "E5181602")
        self.assertEqual(build.alias_token(2, 3).hex().upper(), "E5182603")
        self.assertEqual(build.alias_token(3, 4).hex().upper(), "E5183604")
        self.assertEqual(build.alias_token(4, 5).hex().upper(), "E5184605")
        with self.assertRaises(build.BuildError):
            build.alias_token(5, 1)
        with self.assertRaises(build.BuildError):
            build.alias_token(0, 0)
        with self.assertRaises(build.BuildError):
            build.alias_token(0, 0x100)

    def test_probe_bank_layout(self) -> None:
        raw = b"\x12\x34\x56"
        local = 5
        bank, meta = build.format_probe_bank(raw, local)
        self.assertEqual(len(bank), BANK_SIZE)
        self.assertEqual(le16(bank, 0), build.EMPTY_AT)
        self.assertEqual(le16(bank, local * 2), build.EMPTY_AT + 1)
        self.assertEqual(bank[build.EMPTY_AT], 0)
        self.assertEqual(bank[build.EMPTY_AT + 1:build.EMPTY_AT + 4], raw)
        self.assertEqual(bank[build.EMPTY_AT + 4], 0)
        self.assertEqual(meta["local"], "0005")

    def test_offline_decoder_detects_runtime_aliases(self) -> None:
        main = bytes(load_rom(build.MAIN))
        candidate = bytes(load_rom(build.OUT_ROM))
        promotion = json.loads(
            (ROOT / "out/patch/ext3_five_bank_runtime_probe_promotion_report.json")
            .read_text(encoding="utf-8")
        )
        backup = bytes(load_rom(ROOT / str(promotion["backup_rom"]["path"])))
        self.assertEqual(detect_ext3_alias_page_count(backup), 1)
        self.assertEqual(detect_ext3_alias_page_count(main), 5)
        self.assertEqual(detect_ext3_alias_page_count(candidate), 5)

        ext_meta = load_ext_meta(build.one.EXT_META_PATH)
        ext3_meta = load_ext_meta(build.one.EXT3_META_PATH)
        dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        tbl = Tbl.load(build.one.TBL_PATH)
        sb = stock_base(candidate)
        expected = {
            "590005": "……생각보다　연방군이　적구나。",
            "59001B": "솔라　레이의　위력인가……",
            "590030": "들어라！",
            "590038": "충용한　지온　병사들이여！！",
            "590049": "이제　지구연방군　함대의　절반은",
        }
        for address, text in expected.items():
            got = read_encoded_z_safe(candidate, sb + int(address, 16), max_len=256)
            self.assertIsNotNone(got)
            assert got is not None
            _, body, _ = split_prefix_body(bytes(got[0]))
            self.assertEqual(dictionary.expand(body, tbl).rstrip("\u3000 \t"), text)


if __name__ == "__main__":
    unittest.main()
