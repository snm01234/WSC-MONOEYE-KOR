"""Read-only helpers for normalized main-TIP baseline metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _parse_hex(value: Any, *, label: str) -> int:
    try:
        return int(value, 16) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"invalid {label} in baseline metadata: {value!r}") from exc


def load_stock_approved_ranges(
    path: Path | None,
) -> tuple[tuple[int, int, str, bytes], ...]:
    """Load logical ranges plus their locked current-baseline bytes."""

    if path is None:
        return ()
    if not path.exists():
        raise SystemExit(f"missing baseline metadata: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid baseline metadata: {path}: {exc}") from exc

    rows = payload.get("stock_approved_ranges")
    if not isinstance(rows, list):
        raise SystemExit(f"baseline metadata lacks stock_approved_ranges: {path}")

    out: list[tuple[int, int, str, bytes]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit("baseline stock approval row is not an object")
        if row.get("kind") != "record_body":
            raise SystemExit(
                "baseline stock approval may contain only record_body ranges"
            )
        start = _parse_hex(
            row.get("start", row.get("logical_start")), label="stock range start"
        )
        end = _parse_hex(
            row.get("end", row.get("logical_end")), label="stock range end"
        )
        owner = str(row.get("owner_id") or "")
        if not owner:
            raise SystemExit("baseline stock approval row lacks owner_id")
        if not (0 <= start < end <= 0x800000):
            raise SystemExit(
                f"baseline stock range outside logical stock space: "
                f"{start:06X}-{end:06X}"
            )
        if not (0x50 <= (start >> 16) <= 0x5E):
            raise SystemExit(
                f"baseline stock range outside normalized aux scope: "
                f"{start:06X}-{end:06X}"
            )
        try:
            baseline = bytes.fromhex(str(row.get("baseline_hex") or ""))
        except ValueError as exc:
            raise SystemExit("invalid baseline_hex for " + owner) from exc
        if len(baseline) != end - start:
            raise SystemExit(
                f"baseline_hex length mismatch for {owner}: "
                f"{len(baseline)} != {end - start}"
            )
        out.append((start, end, owner, baseline))

    ordered = tuple(sorted(out))
    for previous, current in zip(ordered, ordered[1:]):
        if previous[1] > current[0]:
            raise SystemExit(
                "overlapping baseline stock approval ranges: "
                f"{previous[0]:06X}-{previous[1]:06X} and "
                f"{current[0]:06X}-{current[1]:06X}"
            )
    return ordered


__all__ = ["load_stock_approved_ranges"]
