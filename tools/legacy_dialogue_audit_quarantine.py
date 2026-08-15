#!/usr/bin/env python3
"""Shared fail-closed tombstone for retired dialogue heuristic audits."""
from __future__ import annotations

from pathlib import Path

MESSAGE = """This dialogue audit is quarantined and must not be used.

The retired audit infers text/metadata/control roles from byte shape and stale
inventories.  That model caused visible Japanese leads and false control
characters.  Use these authoritative entry points instead:

  python tools/audit_dialogue_runtime_safety_gate.py --target <rom> --out <json>
  python -m unittest tools.test_dialogue_runtime_contracts tools.test_dialogue_runtime_safety_gate

Unresolved records must be represented as byte-exact quarantine entries in
tools/dialogue_runtime_contracts.py; do not revive a baseline allowlist.
"""


def block(path: str | Path) -> None:
    name = Path(path).name
    raise RuntimeError(f"{name}: {MESSAGE}")


def cli(path: str | Path) -> int:
    name = Path(path).name
    print(f"{name}: {MESSAGE}")
    return 2
