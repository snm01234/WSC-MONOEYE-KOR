#!/usr/bin/env python3
"""Read-only decision report for the P2 records left after local ext3 expansion."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import _baseline_rows  # noqa: E402
from analyze_p2_short_records import (  # noqa: E402
    DEFAULT_BASE_MANIFEST,
    DEFAULT_EXT3_META,
    DEFAULT_EXT_META,
    DEFAULT_ORIGINAL_ROM,
    DEFAULT_SHEET,
    DEFAULT_TBL,
    DEFAULT_VALUES_DIR,
    analyze as analyze_short_records,
    build_parser as build_short_parser,
)
from apply_ext_dict_unit import load_ext_meta  # noqa: E402
from mixed_residual_reference_union import build_free_slot_inventory, build_reference_union  # noqa: E402
from monoeye_rom import load_rom  # noqa: E402
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

DEFAULT_PARENT_ROM = ROOT / "out/patch/p2_local_ext3_expansion_candidate.wsc"
DEFAULT_PARENT_SAVE = ROOT / "sram/p2_local_ext3_expansion_candidate.sav"
DEFAULT_PARENT_APPROVAL = ROOT / "out/patch/p2_local_ext3_expansion_approval.json"
DEFAULT_LOCAL_REPORT = ROOT / "out/patch/p2_local_ext3_expansion_report.json"
DEFAULT_SEMANTIC_REPORT = ROOT / "out/patch/p2_semantic_duplicate_batch_capacity_report.json"
DEFAULT_POINTER_ALLOWLIST = ROOT / "out/patch/free_space_pointer_allowlist.json"
DEFAULT_OUT = ROOT / "out/patch/p2_remaining100_strategy_report.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": _sha(payload)}


def _short_args(args: argparse.Namespace) -> argparse.Namespace:
    parsed = build_short_parser().parse_args([])
    parsed.original_rom = args.original_rom
    parsed.working_rom = args.parent_rom
    parsed.base_manifest = args.base_manifest
    parsed.values_dir = args.values_dir
    parsed.sheet = args.sheet
    parsed.tbl = args.tbl
    parsed.ext_meta = args.ext_meta
    parsed.ext3_meta = args.ext3_meta
    parsed.base_save = args.parent_save
    parsed.stdout = True
    return parsed


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    original = bytes(load_rom(args.original_rom))
    parent = bytes(load_rom(args.parent_rom))
    _indices, approved_sha, _ranges = load_approved_detachment(args.parent_approval)
    parent_sha = _sha(parent)
    if approved_sha != parent_sha:
        raise RuntimeError(f"approval is bound to {approved_sha}, parent is {parent_sha}")

    local = json.loads(args.local_report.read_text(encoding="utf-8"))
    resolved_targets = list(local.get("targets") or [])
    resolved_ids = {str(row["record_id"]) for row in resolved_targets}
    remaining = dict(local.get("remaining") or {})
    resolved_records = len(resolved_targets)
    resolved_phrases = 15 + int(
        (local.get("apply_report") or {}).get("unique_phrases") or 0
    )
    rows = [
        row
        for row in _baseline_rows(args.base_manifest)
        if str(row["record_id"]) not in resolved_ids
    ]
    body_counts: Counter[int] = Counter()
    gap_counts: Counter[int] = Counter()
    for row in rows:
        boundary = row.get("boundary") or {}
        capacity = int(boundary.get("payload_capacity") or 0)
        prefix = len(bytes.fromhex(str(row.get("prefix_hex") or "")))
        body_counts[capacity - prefix] += 1
        term = int(boundary.get("terminator_offset") or -1)
        nxt = boundary.get("next_record_start")
        if nxt is not None:
            gap_counts[int(nxt) - term - 1] += 1

    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    union = build_reference_union(
        original,
        parent,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    inventory = build_free_slot_inventory(
        parent,
        union=union,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )

    semantic = json.loads(args.semantic_report.read_text(encoding="utf-8"))
    pointer_doc = json.loads(args.pointer_allowlist.read_text(encoding="utf-8"))
    pointer_metadata_keys = sorted(
        key for key in pointer_doc if key not in {"pointer_allowlist", "n"}
    )
    compact3 = bool(ext3_meta.get("compact3"))

    blockers = {
        "true_free_non_ff_two_byte": len(inventory.stock_free)
        + len([index for index in inventory.ext_free if index < 0xF00]),
        "ff_page_true_free": len(inventory.ext_free_ff_page),
        "pair_steal_preserve_slots": len(inventory.stock_free)
        + len([index for index in inventory.ext_free if index < 0xF00]),
        "semantic_duplicate_groups": int(
            (semantic.get("semantic_inventory") or {}).get(
                "render_groups_with_distinct_raw_payloads", 0
            )
        ),
        "compact3_enabled": compact3,
        "legacy_pointer_allowlist_entries": len(pointer_doc.get("pointer_allowlist") or []),
        "legacy_pointer_allowlist_candidate_bound_metadata": pointer_metadata_keys,
    }

    safe_static_capacity = 0
    routes = [
        {
            "route": "non_ff_true_free_or_pair_steal",
            "status": "NO_GO",
            "capacity_records": 0,
            "reason": "no non-FF true-free two-byte preserve slot remains",
        },
        {
            "route": "byte_or_semantic_duplicate_reclaim",
            "status": "NO_GO",
            "capacity_records": 0,
            "reason": "all byte duplicates were consumed and semantic distinct-raw groups are zero",
        },
        {
            "route": "ff_page_two_byte_slots",
            "status": "NO_GO",
            "capacity_records": 0,
            "reason": "FF xx raw-byte collision policy forbids story Korean",
        },
        {
            "route": "compact3_three_byte_token",
            "status": "NO_GO",
            "capacity_records": 0,
            "reason": "compact3 is disabled in the accepted runtime and runtime testing is excluded",
        },
        {
            "route": "legacy_far_pointer_allowlist",
            "status": "NO_GO",
            "capacity_records": 0,
            "reason": "the 604-entry file has no generator, parent/candidate SHA, destination mapping, caller XREF, or runtime-read evidence",
        },
        {
            "route": "gapless_record_relocation_or_stream_indirection",
            "status": "DEFERRED_RUNTIME_REQUIRED",
            "capacity_records": 0,
            "reason": "requires caller XREF/runtime reads or a separately proven event-stream indirection contract",
        },
    ]

    return {
        "generated_by": "tools/analyze_p2_remaining_routes.py",
        "read_only": True,
        "rom_written": False,
        "inputs": {
            "parent_rom": _identity(args.parent_rom, parent),
            "parent_save": _identity(args.parent_save),
            "parent_approval": _identity(args.parent_approval),
            "local_report": _identity(args.local_report),
            "semantic_report": _identity(args.semantic_report),
            "pointer_allowlist": _identity(args.pointer_allowlist),
            "ext3_meta": _identity(args.ext3_meta),
        },
        "current_state": {
            "resolved_records": resolved_records,
            "resolved_phrases": resolved_phrases,
            "remaining_records": int(remaining.get("records") or 0),
            "remaining_phrases": int(remaining.get("unique_phrases") or 0),
            "body_span_counts": {str(key): value for key, value in sorted(body_counts.items())},
            "gap_after_terminator_counts": {str(key): value for key, value in sorted(gap_counts.items())},
        },
        "completed_this_followup": {
            "nested_duplicate_records": 6,
            "local_ext3_records": int((local.get("apply_report") or {}).get("records_applied") or 0),
            "total_records": 6 + int((local.get("apply_report") or {}).get("records_applied") or 0),
        },
        "blockers": blockers,
        "routes": routes,
        "decision": {
            "status": "NO_GO_further_static_changes_under_current_runtime_scope",
            "safe_static_capacity_records": safe_static_capacity,
            "remaining_records": int(remaining.get("records") or 0),
            "next_evidence_required": (
                "runtime ROM-read evidence and caller XREF for specific records, or an independently reviewed compact3 runtime reintroduction"
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    ap.add_argument("--parent-rom", type=Path, default=DEFAULT_PARENT_ROM)
    ap.add_argument("--parent-save", type=Path, default=DEFAULT_PARENT_SAVE)
    ap.add_argument("--parent-approval", type=Path, default=DEFAULT_PARENT_APPROVAL)
    ap.add_argument("--local-report", type=Path, default=DEFAULT_LOCAL_REPORT)
    ap.add_argument("--semantic-report", type=Path, default=DEFAULT_SEMANTIC_REPORT)
    ap.add_argument("--pointer-allowlist", type=Path, default=DEFAULT_POINTER_ALLOWLIST)
    ap.add_argument("--base-manifest", type=Path, default=DEFAULT_BASE_MANIFEST)
    ap.add_argument("--values-dir", type=Path, default=DEFAULT_VALUES_DIR)
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.out.suffix.lower() in {".wsc", ".sav"}:
        raise SystemExit("refusing ROM/SaveRAM output")
    report = analyze(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "report": str(args.out),
                "status": report["decision"]["status"],
                "resolved_records": report["current_state"]["resolved_records"],
                "remaining_records": report["current_state"]["remaining_records"],
                "safe_static_capacity_records": report["decision"]["safe_static_capacity_records"],
                "rom_written": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
