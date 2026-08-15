#!/usr/bin/env python3
"""Authoritative fail-closed audit for runtime dialogue contracts.

This entry point intentionally contains no legacy prefix, structure-inventory,
or baseline-failure implementation.  It rebuilds the machine-readable contract
from the exact target and delegates every decision to
``dialogue_runtime_contracts``.

The retired heuristic audits are quarantined separately.  Do not add a
fallback to them here: an unresolved record belongs in the contract's
``quarantine`` population and must remain byte-exact.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from dialogue_runtime_contracts import (  # noqa: E402
    DEFAULT_MANIFEST,
    audit_manifest,
    build_manifest,
    write_manifest,
)
from monoeye_rom import find_rom, load_rom  # noqa: E402

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/dialogue_runtime_safety_gate.json"
ROM_SIZE = 16_777_216


def audit(
    target: bytes,
    original: bytes,
    *,
    target_path: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, object]:
    """Rebuild and audit the contract bound to ``target``.

    A previous target's manifest is never accepted as input.  Regeneration is
    part of the audit so a stale snapshot cannot authorize a promotion.
    """
    contract = build_manifest(original, target, target_path=target_path)
    write_manifest(manifest_path, contract)
    return audit_manifest(target, contract, target_path=target_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)

    target = bytes(load_rom(args.target))
    if len(target) != ROM_SIZE:
        raise SystemExit(f"target size drifted: {len(target)}")
    original = bytes(load_rom(find_rom(ROOT)))
    report = audit(
        target,
        original,
        target_path=args.target,
        manifest_path=args.manifest,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "counts": report["counts"],
                "report": str(args.out),
                "manifest": str(args.manifest),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
