#!/usr/bin/env python3
"""Verify protected structured tables and reject token-like table mutations.

The target is compared with an explicit known-good reference.  Every protected
bank-5C u16 table must remain byte-identical and strictly ascending.  When an
optional parent is supplied, any parent-to-target byte change that overlaps a
known or inferred monotonic u16 run is reported as an unsafe structured write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from monoeye_rom import stock_base
from structured_token_write_guard import (
    PROTECTED_TABLES,
    classify_structured_token_site,
    logical_slice,
    validate_protected_table,
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size": len(data),
        "sha256": digest(data),
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def diff_runs(left: bytes, right: bytes) -> list[tuple[int, int]]:
    if len(left) != len(right):
        raise ValueError("ROM sizes differ")
    rows: list[tuple[int, int]] = []
    start: int | None = None
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = index
        elif a == b and start is not None:
            rows.append((start, index))
            start = None
    if start is not None:
        rows.append((start, len(left)))
    return rows


def logical_for_file(data: bytes, file_offset: int) -> int | None:
    base = stock_base(data)
    logical = file_offset - base
    if 0 <= logical < 0x800000:
        return logical
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    reference = args.reference.read_bytes()
    target = args.target.read_bytes()
    if len(reference) not in (8_388_608, 16_777_216):
        raise SystemExit("reference is not an 8 MiB or 16 MiB ROM")
    if len(target) not in (8_388_608, 16_777_216):
        raise SystemExit("target is not an 8 MiB or 16 MiB ROM")

    table_reports: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for table in PROTECTED_TABLES:
        reference_bytes = logical_slice(
            reference,
            table.logical_start,
            table.logical_end_exclusive - table.logical_start,
        )
        target_bytes = logical_slice(
            target,
            table.logical_start,
            table.logical_end_exclusive - table.logical_start,
        )
        validation = validate_protected_table(target, table)
        exact = target_bytes == reference_bytes
        row = {
            **validation,
            "reference_exact": exact,
            "reference_sha256": digest(reference_bytes),
            "target_sha256": digest(target_bytes),
        }
        table_reports.append(row)
        if not exact or validation.get("ok") is not True:
            issues.append(
                {
                    "type": "protected_table_mismatch",
                    "table": table.name,
                    "logical_start": f"{table.logical_start:06X}",
                    "logical_end_exclusive": f"{table.logical_end_exclusive:06X}",
                    "reference_exact": exact,
                    "validation": validation,
                }
            )

    parent_report: dict[str, Any] | None = None
    if args.parent is not None:
        parent = args.parent.read_bytes()
        runs = diff_runs(parent, target)
        structured_changes: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        for file_start, file_end in runs:
            for file_offset in range(file_start, file_end):
                logical = logical_for_file(parent, file_offset)
                if logical is None:
                    continue
                # Test both possible two-byte starts that include this byte.
                for token_abs in (logical - 1, logical):
                    if token_abs < 0 or token_abs + 2 > 0x800000:
                        continue
                    key = (token_abs, token_abs + 2)
                    if key in seen:
                        continue
                    classification = classify_structured_token_site(parent, token_abs)
                    if classification is None:
                        continue
                    seen.add(key)
                    structured_changes.append(
                        {
                            "logical_start": f"{token_abs:06X}",
                            "logical_end_exclusive": f"{token_abs + 2:06X}",
                            "before_hex": logical_slice(parent, token_abs, 2).hex().upper(),
                            "after_hex": logical_slice(target, token_abs, 2).hex().upper(),
                            "classification": classification,
                        }
                    )
        parent_report = {
            "identity": identity(args.parent, parent),
            "diff_runs": len(runs),
            "structured_changes": structured_changes,
        }
        if structured_changes:
            issues.extend(
                {"type": "parent_to_target_structured_change", **row}
                for row in structured_changes
            )

    result = {
        "schema_version": 1,
        "generated_by": "tools/verify_structured_token_tables.py",
        "ok": not issues,
        "inputs": {
            "reference": identity(args.reference, reference),
            "target": identity(args.target, target),
            "parent": parent_report,
        },
        "protected_tables": table_reports,
        "issues": issues,
        "issue_count": len(issues),
    }
    atomic_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
