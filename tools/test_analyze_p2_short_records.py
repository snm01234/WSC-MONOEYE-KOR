from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analyze_p2_short_records import (
    P2AnalysisError,
    build_exact_phrase_index,
    build_true_free_plan,
    load_reviewed_values,
    make_rewrite_payload,
    parse_short_reason,
)


class _DictionaryFixture:
    count = 4

    def __init__(self) -> None:
        self._raw = {
            0: b"a",
            1: b"b",
            2: b"c",
            3: b"d",
        }
        self._expanded = {
            b"a": "좋아！",
            b"b": "좋아！\u3000",
            b"c": "젠장！",
            b"d": "",
        }

    def raw_entry(self, index: int) -> bytes:
        return self._raw[index]

    def expand(self, raw: bytes, _tbl: object) -> str:
        return self._expanded[raw]


class AnalyzeP2ShortRecordsTest(unittest.TestCase):
    def test_parse_short_reason_preserves_measured_slot_and_detail(self) -> None:
        parsed = parse_short_reason(
            "excluded_shared_token_body_capacity:body=3,slot=06B7,"
            "two_byte_index_space_saturated"
        )

        self.assertEqual(parsed.body_span, 3)
        self.assertEqual(parsed.slot, 0x06B7)
        self.assertEqual(parsed.detail, "two_byte_index_space_saturated")

    def test_parse_short_reason_accepts_missing_slot_and_rejects_other_reasons(self) -> None:
        parsed = parse_short_reason(
            "excluded_shared_token_body_capacity:body=2,slot=none"
        )
        self.assertEqual(parsed.body_span, 2)
        self.assertIsNone(parsed.slot)
        self.assertIsNone(parsed.detail)

        with self.assertRaisesRegex(P2AnalysisError, "malformed short-record reason"):
            parse_short_reason("excluded_prefix_unprovable:body_too_short")

    def test_reviewed_values_merge_duplicates_and_reject_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = {
                "entries": {
                    "script:600100": {"ko": "좋아！"},
                    "script:600200": {"ko": "젠장！"},
                }
            }
            second = {"entries": {"script:600100": {"ko": "좋아！"}}}
            (root / "001.json").write_text(
                json.dumps(first, ensure_ascii=False), encoding="utf-8"
            )
            (root / "002.json").write_text(
                json.dumps(second, ensure_ascii=False), encoding="utf-8"
            )

            values, sources = load_reviewed_values(root)

            self.assertEqual(
                values,
                {
                    "script:600100": "좋아！",
                    "script:600200": "젠장！",
                },
            )
            self.assertEqual(len(sources), 2)

            conflict = {"entries": {"script:600100": {"ko": "알겠습니다！"}}}
            (root / "003.json").write_text(
                json.dumps(conflict, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(P2AnalysisError, "conflicting reviewed values"):
                load_reviewed_values(root)

    def test_exact_phrase_index_deduplicates_text_but_keeps_slots(self) -> None:
        with patch(
            "analyze_p2_short_records.dict_token_safe_in_zstring",
            lambda _index: True,
        ):
            index = build_exact_phrase_index(_DictionaryFixture(), object())

        self.assertEqual(index["좋아！"], (0, 1))
        self.assertEqual(index["젠장！"], (2,))
        self.assertNotIn("", index)

    def test_true_free_plan_uses_highest_impact_phrases(self) -> None:
        rows = []
        values = {}
        for offset, phrase in enumerate(("가", "가", "가", "나", "다", "다")):
            logical = 0x600100 + offset * 8
            record_id = f"script:{logical:06X}"
            rows.append(
                {
                    "record_id": record_id,
                    "logical_address": logical,
                    "region": "script",
                    "prefix_hex": "18",
                    "boundary": {
                        "payload_capacity": 4,
                        "terminator_offset": logical + 4,
                    },
                }
            )
            values[record_id] = phrase

        with patch(
            "analyze_p2_short_records.try_encode_ko_text",
            side_effect=lambda text, *_args, **_kwargs: text.encode("utf-8"),
        ):
            allocations, record_plan = build_true_free_plan(
                rows,
                values,
                excluded_record_ids=set(),
                slots=(0x0EFB, 0x0EFD),
                tbl=object(),
            )

        self.assertEqual([row["target_ko"] for row in allocations], ["가", "다"])
        self.assertEqual([row["record_count"] for row in allocations], [3, 2])
        self.assertEqual(len(record_plan), 5)
        self.assertEqual({row["slot"] for row in record_plan}, {"0EFB", "0EFD"})

    def test_rewrite_payload_preserves_prefix_and_terminator_room(self) -> None:
        body2 = make_rewrite_payload(b"\x18", 0x0EFE, 3)
        body3 = make_rewrite_payload(b"\x18", 0x0EFE, 4)

        self.assertEqual(body2[:1], b"\x18")
        self.assertEqual(body3[:1], b"\x18")
        self.assertEqual(len(body2), 3)
        self.assertEqual(len(body3), 4)
        self.assertEqual(body3[-1], 0x01)
        self.assertEqual(body2[1:3], body3[1:3])

        with self.assertRaisesRegex(P2AnalysisError, "body must be 2 or 3 bytes"):
            make_rewrite_payload(b"", 0x0EFE, 4)


if __name__ == "__main__":
    unittest.main()
