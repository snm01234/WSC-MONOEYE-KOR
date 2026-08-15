from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from structured_token_write_guard import (
    StructuredTokenWriteError,
    classify_structured_token_site,
    guard_external_token_write,
    validate_protected_table,
    PROTECTED_TABLES,
)


ROM_SIZE = 8_388_608


class StructuredTokenWriteGuardTest(unittest.TestCase):
    def _rom(self) -> bytearray:
        return bytearray(b"\xFF" * ROM_SIZE)

    def test_known_bank5c_table_site_is_blocked(self) -> None:
        rom = self._rom()
        table = PROTECTED_TABLES[0]
        values = [
            0x85BF, 0x85D2, 0x85E8, 0x85F5, 0x8609, 0x8629, 0x8636,
            0x864A, 0x8660, 0x866D, 0x8681, 0x86A1, 0x86BD, 0x86D1,
            0x86E7, 0x8703, 0x8716, 0x8734, 0x8741, 0x8754, 0x8765,
            0x8774, 0x8784, 0x879A, 0x87A7, 0x87BB, 0x87D1, 0x87DE,
            0x87F5, 0x880D, 0x881F, 0x8836, 0x8849,
        ]
        for index, value in enumerate(values):
            start = table.logical_start + index * 2
            rom[start : start + 2] = value.to_bytes(2, "little")

        report = validate_protected_table(rom, table)
        self.assertTrue(report["ok"])
        with self.assertRaisesRegex(StructuredTokenWriteError, "structured data"):
            guard_external_token_write(
                rom,
                token_abs=0x5CB5C2,
                before=bytes.fromhex("F585"),
                after=bytes.fromhex("F573"),
                region="aux",
                kind="zstring",
            )

    def test_unknown_monotonic_u16_table_is_blocked(self) -> None:
        rom = self._rom()
        start = 0x590200
        values = [0x1200, 0x1210, 0x1225, 0x1230, 0x1248, 0x1260, 0x1272]
        for index, value in enumerate(values):
            logical = start + index * 2
            rom[logical : logical + 2] = value.to_bytes(2, "little")
        token_abs = start + 3 * 2
        classification = classify_structured_token_site(rom, token_abs)
        self.assertIsNotNone(classification)
        self.assertIn("monotonic_u16_run", classification)
        with self.assertRaises(StructuredTokenWriteError):
            guard_external_token_write(
                rom,
                token_abs=token_abs,
                before=values[3].to_bytes(2, "little"),
                after=b"\xF5\x73",
                region="aux",
                kind="zstring",
            )

    def test_short_nonmonotonic_text_payload_is_allowed(self) -> None:
        rom = self._rom()
        logical = 0x590300
        payload = bytes.fromhex("173418F58501")
        rom[logical : logical + len(payload)] = payload
        guard_external_token_write(
            rom,
            token_abs=logical + 3,
            before=bytes.fromhex("F585"),
            after=bytes.fromhex("F573"),
            region="script",
            kind="dialogue",
        )


if __name__ == "__main__":
    unittest.main()
