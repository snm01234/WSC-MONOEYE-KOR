#!/usr/bin/env python3
"""Audit the Garrod native-stock/page-boundary population on the current TIP.

This is a read-only guard.  It does not rewrite a ROM, dictionary entry, or
SaveRAM.  The guard combines the proven Garrod predecessor native-grammar
rule with the current TIP identity (including the independent 5997BF fix):

* a source record that was exactly two native dictionary tokens must still be
  represented by two native tokens (with only existing ``01`` padding);
* the exact four-byte ``E5 18`` portal shape immediately before a double-NUL
  and an ``18`` record head is an audit-only mixed-grammar population;
* the Garrod block itself is byte-pinned, including both NUL boundaries and
  the following ``08 2B`` control;
* static shape is never treated as proof that a mixed record can be rewritten.

The broad family scan is intentionally wider than the historical 63-row
candidate manifest.  It binds every matching record back to the pristine
source and reports any boundary or grammar drift before a future builder can
touch the block.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, read_encoded_z_safe, stock_base  # noqa: E402

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_SOURCE = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_OUT = ROOT / "out/patch/garrod_native_stock_guard_report.json"

EXPECTED_TARGET_SHA = (
    "9402f7efc1c557746015eb6352799a79f7f66febf1eb0ad4039734028a16a9f2"
)
EXPECTED_SOURCE_SHA = (
    "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
)
PREFIX = bytes.fromhex("173418")
EXT3_HEAD = bytes.fromhex("E518")

# This is the exact recurrence shape left after the 45 [dict, dict] records
# were restored to native two-token grammar.  The 18 records are intentionally
# mixed [2,1,1] or [1,2,1] source grammars and therefore remain review-only.
EXPECTED_MIXED_EXT3_ADDRESSES = {
    0x6017F3,
    0x604B01,
    0x605877,
    0x6073DD,
    0x60A8AC,
    0x60D866,
    0x60F20B,
    0x611C2E,
    0x6121A4,
    0x614211,
    0x61C9EC,
    0x621E60,
    0x627B1A,
    0x634F62,
    0x6357BA,
    0x63592C,
    0x635CD2,
    0x638154,
}

GARROD_ANCHORS: dict[int, dict[str, Any]] = {
    0x61E234: {
        "source_payload": bytes.fromhex("173418FD4BF191"),
        "target_payload": bytes.fromhex("173418F184F191"),
        "source_terminator": 0x61E23B,
        "target_terminator": 0x61E23B,
        "role": "predecessor_native_two_token_page_boundary",
    },
    0x61E23D: {
        "source_payload": bytes.fromhex("18F19114E0F51907FD60F8EE15"),
        "target_payload": bytes.fromhex("18F2B801010101010101010101"),
        "source_terminator": 0x61E24A,
        "target_terminator": 0x61E24A,
        "role": "record_head_continuation",
    },
    0x61E24B: {
        "source_payload": bytes.fromhex("F475FCAE90F0326B2418F18D061D"),
        "target_payload": bytes.fromhex("F2C5010101010101010101010101"),
        "source_terminator": 0x61E259,
        "target_terminator": 0x61E259,
        "role": "bare_continuation",
    },
}

# Address-level pins avoid treating a generic "next control" heuristic as a
# sufficient proof.  61E23B/61E23C are the double-NUL; 61E24A and 61E259 are
# record terminators; 61E25A is the separator and 61E25B/61E25C are 08 2B.
GARROD_BYTE_PINS = {
    0x61E23B: 0x00,
    0x61E23C: 0x00,
    0x61E24A: 0x00,
    0x61E259: 0x00,
    0x61E25A: 0x00,
    0x61E25B: 0x08,
    0x61E25C: 0x2B,
}

# Keep the independently promoted 5997BF record visible to the combined
# report.  This is not part of the Garrod bank-60..63 scan.
GOD_GUNDAM_PINS = {
    "payload": bytes.fromhex("171C18E51808920101010101010101010101010101"),
    "terminator": 0x5997D4,
    "separator": 0x5997D5,
    "next_control": bytes.fromhex("084B"),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes) -> dict[str, Any]:
    return {"path": str(path.resolve()), "size": len(data), "sha256": sha(data)}


def read_record(rom: bytes, logical: int, *, max_len: int = 512) -> tuple[bytes, int] | None:
    base = stock_base(rom)
    got = read_encoded_z_safe(rom, base + logical, max_len=max_len)
    if got is None:
        return None
    return bytes(got[0]), int(got[1]) - base


def unit_kinds(body: bytes) -> list[str]:
    """Parse only the stock unit grammar used by the source comparison."""
    kinds: list[str] = []
    i = 0
    while i < len(body):
        value = body[i]
        if 0xF0 <= value <= 0xFF and i + 1 < len(body):
            kinds.append("dict")
            i += 2
        elif 0xE0 <= value <= 0xE7 and i + 1 < len(body):
            kinds.append("glyph2")
            i += 2
        else:
            kinds.append("char1")
            i += 1
    return kinds


def exact_native_two_token(body: bytes) -> bool:
    """Return true for exactly [dict, dict], with no padding."""
    return unit_kinds(body) == ["dict", "dict"] and len(body) == 4


def current_native_two_token_with_padding(body: bytes) -> bool:
    """Return true for [dict, dict] followed only by existing 01 padding."""
    if len(body) < 4 or not exact_native_two_token(body[:4]):
        return False
    return all(value == 0x01 for value in body[4:])


def exact_ext3_portal(body: bytes) -> bool:
    return len(body) == 4 and body[:2] == EXT3_HEAD


def scan_families(target: bytes, source: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """Scan all four scenario banks and bind each family to source bytes."""
    target_base = stock_base(target)
    source_base = stock_base(source)
    rows: list[dict[str, Any]] = []
    scan_errors: list[str] = []

    for bank in range(0x60, 0x64):
        lo = bank << 16
        hi = lo + 0x10000
        logical = lo
        while logical < hi:
            current = read_record(target, logical)
            if current is None:
                # Bank tails can contain non-zstring data.  The previous
                # record terminator is the safe point at which to stop.
                break
            current_payload, current_term = current
            if (
                not current_payload.startswith(PREFIX)
                or current_term + 2 >= hi
                or target[target_base + current_term + 1] != 0
                or target[target_base + current_term + 2] != 0x18
            ):
                logical = current_term + 1
                continue

            current_second_start = current_term + 2
            current_second = read_record(target, current_second_start)
            if current_second is None:
                scan_errors.append(f"{logical:06X}: missing current 18-head record")
                logical = current_term + 1
                continue
            current_second_payload, current_second_term = current_second
            current_third = current_second_term + 1
            if current_third >= hi or target[target_base + current_third] == 0:
                logical = current_term + 1
                continue

            source_first = read_record(source, logical)
            source_second = read_record(source, current_second_start)
            source_payload = source_first[0] if source_first else b""
            source_term = source_first[1] if source_first else None
            source_second_payload = source_second[0] if source_second else b""
            source_second_term = source_second[1] if source_second else None
            current_body = current_payload[3:]
            source_body = source_payload[3:] if source_payload.startswith(PREFIX) else b""
            source_kinds = unit_kinds(source_body)
            binding_reasons: list[str] = []
            if source_first is None or not source_payload.startswith(PREFIX):
                binding_reasons.append("source_prefix_missing")
            if source_term != current_term:
                binding_reasons.append("first_terminator_drift")
            if source_term is not None:
                if source[source_base + source_term + 1] != 0:
                    binding_reasons.append("source_separator_nul_drift")
                if source[source_base + source_term + 2] != 0x18:
                    binding_reasons.append("source_18_head_drift")
            if source_second is None:
                binding_reasons.append("source_second_missing")
            elif source_second_term != current_second_term:
                binding_reasons.append("second_terminator_drift")

            rows.append(
                {
                    "logical": logical,
                    "bank": bank,
                    "current_payload_hex": current_payload.hex().upper(),
                    "source_payload_hex": source_payload.hex().upper(),
                    "current_terminator": current_term,
                    "source_terminator": source_term,
                    "current_second_start": current_second_start,
                    "current_second_terminator": current_second_term,
                    "source_second_terminator": source_second_term,
                    "current_third_start": current_third,
                    "source_kinds": source_kinds,
                    "source_exact_native_two_token": exact_native_two_token(source_body),
                    "current_native_two_token_with_padding": current_native_two_token_with_padding(
                        current_body
                    ),
                    "current_exact_ext3_portal": exact_ext3_portal(current_body),
                    "current_exact_ext3_risk_shape": (
                        len(current_payload) == 7 and exact_ext3_portal(current_body)
                    ),
                    "binding_reasons": binding_reasons,
                }
            )
            logical = current_term + 1

    return rows, scan_errors


def check_anchors(target: bytes, source: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    target_base = stock_base(target)
    source_base = stock_base(source)
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for logical, expected in sorted(GARROD_ANCHORS.items()):
        target_record = read_record(target, logical)
        source_record = read_record(source, logical)
        target_payload = target_record[0] if target_record else b""
        source_payload = source_record[0] if source_record else b""
        target_term = target_record[1] if target_record else None
        source_term = source_record[1] if source_record else None
        row = {
            "logical": f"{logical:06X}",
            "role": expected["role"],
            "source_payload_hex": source_payload.hex().upper(),
            "expected_source_payload_hex": expected["source_payload"].hex().upper(),
            "target_payload_hex": target_payload.hex().upper(),
            "expected_target_payload_hex": expected["target_payload"].hex().upper(),
            "source_terminator": None if source_term is None else f"{source_term:06X}",
            "expected_source_terminator": f"{expected['source_terminator']:06X}",
            "target_terminator": None if target_term is None else f"{target_term:06X}",
            "expected_target_terminator": f"{expected['target_terminator']:06X}",
            "source_exact": source_payload == expected["source_payload"]
            and source_term == expected["source_terminator"],
            "target_exact": target_payload == expected["target_payload"]
            and target_term == expected["target_terminator"],
        }
        rows.append(row)
        if not row["source_exact"]:
            issues.append(f"Garrod source anchor drift at {logical:06X}")
        if not row["target_exact"]:
            issues.append(f"Garrod target anchor drift at {logical:06X}")

    pin_mismatches: list[dict[str, Any]] = []
    for logical, expected_value in sorted(GARROD_BYTE_PINS.items()):
        actual = target[target_base + logical]
        if actual != expected_value:
            pin_mismatches.append(
                {
                    "logical": f"{logical:06X}",
                    "expected": f"{expected_value:02X}",
                    "actual": f"{actual:02X}",
                }
            )
    if pin_mismatches:
        issues.append("Garrod NUL/separator/control byte pin drift")
    return rows, issues + ([{"byte_pin_mismatches": pin_mismatches}] if pin_mismatches else [])


def check_god_gundam_scope(target: bytes) -> tuple[dict[str, Any], list[str]]:
    record = read_record(target, 0x5997BF)
    base = stock_base(target)
    payload = record[0] if record else b""
    term = record[1] if record else None
    separator = target[base + GOD_GUNDAM_PINS["separator"]]
    next_control = target[
        base + GOD_GUNDAM_PINS["separator"] + 1 : base + GOD_GUNDAM_PINS["separator"] + 3
    ]
    result = {
        "logical": "5997BF",
        "payload_hex": payload.hex().upper(),
        "expected_payload_hex": GOD_GUNDAM_PINS["payload"].hex().upper(),
        "terminator": None if term is None else f"{term:06X}",
        "expected_terminator": f"{GOD_GUNDAM_PINS['terminator']:06X}",
        "separator": f"{separator:02X}",
        "next_control": next_control.hex().upper(),
        "payload_exact": payload == GOD_GUNDAM_PINS["payload"],
        "boundary_exact": term == GOD_GUNDAM_PINS["terminator"]
        and separator == 0
        and next_control == GOD_GUNDAM_PINS["next_control"],
    }
    issues: list[str] = []
    if not result["payload_exact"] or not result["boundary_exact"]:
        issues.append("5997BF cross-scope TIP pin drift")
    return result, issues


def build_report(
    target_path: Path,
    source_path: Path,
    *,
    expected_target_sha: str | None = EXPECTED_TARGET_SHA,
) -> dict[str, Any]:
    target = bytes(load_rom(target_path))
    source = bytes(load_rom(source_path))
    target_id = identity(target_path, target)
    source_id = identity(source_path, source)
    rows, scan_errors = scan_families(target, source)
    anchors, anchor_issues = check_anchors(target, source)
    god_scope, god_issues = check_god_gundam_scope(target)

    source_native_rows = [row for row in rows if row["source_exact_native_two_token"]]
    source_native_drift = [
        row
        for row in source_native_rows
        if not row["current_native_two_token_with_padding"]
    ]
    ext3_rows = [row for row in rows if row["current_exact_ext3_risk_shape"]]
    ext3_native_source = [row for row in ext3_rows if row["source_exact_native_two_token"]]
    ext3_mixed_source = [row for row in ext3_rows if not row["source_exact_native_two_token"]]
    ext3_addresses = {int(row["logical"]) for row in ext3_rows}
    binding_failures = [row for row in rows if row["binding_reasons"]]

    issues: list[str] = []
    if expected_target_sha and target_id["sha256"] != expected_target_sha:
        issues.append("current TIP SHA-256 differs from the bound promotion baseline")
    if source_id["sha256"] != EXPECTED_SOURCE_SHA:
        issues.append("pristine source SHA-256 differs from the bound source baseline")
    issues.extend(scan_errors)
    issues.extend(f"family binding drift at {row['logical']:06X}" for row in binding_failures)
    issues.extend(anchor_issues)
    issues.extend(god_issues)
    if source_native_drift:
        issues.append("source exact native-two-token family was converted to non-native grammar")
    if ext3_native_source:
        issues.append("exact E5 18 risk shape remains over an original native-two-token body")
    if ext3_addresses != EXPECTED_MIXED_EXT3_ADDRESSES:
        issues.append("mixed E5 18 risk population differs from the reviewed 18-address baseline")
    if any(row["source_exact_native_two_token"] for row in ext3_mixed_source):
        issues.append("mixed-risk classification is inconsistent with source grammar")

    source_kind_counts = Counter(",".join(row["source_kinds"]) for row in ext3_rows)
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_garrod_native_stock_guard.py",
        "status": "pass" if not issues else "fail",
        "target": target_id,
        "source": source_id,
        "scope": {
            "banks": [f"{bank:02X}" for bank in range(0x60, 0x64)],
            "family_shape": "17 34 18 -> double-NUL -> 18-head record -> bare continuation",
            "garrod_protected_range": "61E234-61E25C inclusive",
            "garrod_byte_pin_meaning": {
                "double_nul": ["61E23B", "61E23C"],
                "continuation_terminators": ["61E24A", "61E259"],
                "separator": "61E25A",
                "next_control": "61E25B-61E25C = 08 2B",
            },
        },
        "counts": {
            "structural_families": len(rows),
            "source_exact_native_two_token": len(source_native_rows),
            "source_exact_native_two_token_current_non_native": len(source_native_drift),
            "current_exact_ext3_risk_shape": len(ext3_rows),
            "current_ext3_source_exact_native_two_token": len(ext3_native_source),
            "current_ext3_source_mixed_grammar": len(ext3_mixed_source),
            "family_binding_failures": len(binding_failures),
            "scan_errors": len(scan_errors),
        },
        "current_ext3_risk_population": [
            {
                "logical": f"{row['logical']:06X}",
                "source_kinds": row["source_kinds"],
                "source_payload_hex": row["source_payload_hex"],
                "current_payload_hex": row["current_payload_hex"],
                "terminator": f"{row['current_terminator']:06X}",
                "second_start": f"{row['current_second_start']:06X}",
                "second_terminator": f"{row['current_second_terminator']:06X}",
                "third_start": f"{row['current_third_start']:06X}",
                "disposition": "review_only_no_auto_native_rewrite",
            }
            for row in sorted(ext3_rows, key=lambda item: item["logical"])
        ],
        "source_native_drift": [
            {
                "logical": f"{row['logical']:06X}",
                "source_kinds": row["source_kinds"],
                "source_payload_hex": row["source_payload_hex"],
                "current_payload_hex": row["current_payload_hex"],
            }
            for row in sorted(source_native_drift, key=lambda item: item["logical"])
        ],
        "source_kind_counts_in_current_ext3_risk": dict(sorted(source_kind_counts.items())),
        "garrod_anchors": anchors,
        "god_gundam_5997bf_cross_scope": god_scope,
        "guards": {
            "target_sha_bound": expected_target_sha is None
            or target_id["sha256"] == expected_target_sha,
            "expected_target_sha256": expected_target_sha or "",
            "source_sha_bound": source_id["sha256"] == EXPECTED_SOURCE_SHA,
            "all_family_bound_to_source": not binding_failures,
            "native_two_token_grammar_preserved": not source_native_drift,
            "exact_ext3_risk_has_no_native_two_token_source": not ext3_native_source,
            "mixed_grammar_not_auto_rewritten": len(ext3_mixed_source) == 18,
            "mixed_risk_population_matches_review_baseline": ext3_addresses
            == EXPECTED_MIXED_EXT3_ADDRESSES,
            "garrod_anchors_byte_exact": all(row["target_exact"] for row in anchors),
            "garrod_nul_separator_control_pins_exact": not any(
                issue for issue in anchor_issues if isinstance(issue, dict)
            ),
            "god_gundam_5997bf_cross_scope_exact": god_scope["payload_exact"]
            and god_scope["boundary_exact"],
            "consumer_support_not_inferred_from_static_shape": True,
        },
        "issues": issues,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    report = build_report(args.target, args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "target_sha256": report["target"]["sha256"],
                "counts": report["counts"],
                "issues": report["issues"],
                "report": str(args.out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
