#!/usr/bin/env python3
"""Build one digest-locked P1 prefix batch without touching a ROM.

A batch may contain one or more vetted bank-5C continuation-text blocks. Every
selected row is rebound to the Original ROM payload, NUL terminator, and its
own block extent. All other records stay out of the batch.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from mixed_residual_discovery import _counts, validate_manifest_digest
from mixed_residual_models import deterministic_json_sha256
from monoeye_rom import load_rom, stock_base


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(path: Path) -> dict[str, Any]:
    value = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "size": len(value),
        "sha256": _sha256_bytes(value),
    }


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _retarget(row: dict[str, Any], *, included: bool, reason: str) -> None:
    row["included"] = included
    row["source_classification"] = "mixed" if included else "excluded"
    row["reason"] = reason
    row["target_sha256"] = deterministic_json_sha256(
        {
            "record_id": row["record_id"],
            "source_sha256": row["source_sha256"],
            "source_classification": row["source_classification"],
            "included": row["included"],
            "reason": row["reason"],
            "annotations": row.get("annotations") or [],
        }
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-manifest", type=Path, required=True)
    ap.add_argument("--blocks", type=Path, required=True)
    ap.add_argument("--original-rom", type=Path, required=True)
    ap.add_argument(
        "--block-start",
        dest="block_starts",
        action="append",
        help="vetted bank-5C block start; repeat for a cumulative batch",
    )
    ap.add_argument(
        "--batch-name",
        default=None,
        help="manifest batch label; derived from block starts when omitted",
    )
    ap.add_argument(
        "--exclude-record",
        dest="excluded_records",
        action="append",
        default=[],
        help=(
            "record id to keep out of a selected block after a downstream "
            "fail-closed check; repeat as needed"
        ),
    )
    ap.add_argument("--out-proof", type=Path, required=True)
    ap.add_argument("--out-manifest", type=Path, required=True)
    args = ap.parse_args()

    base = json.loads(args.base_manifest.read_text(encoding="utf-8"))
    if not validate_manifest_digest(base):
        raise SystemExit("base manifest digest is invalid")
    blocks_doc = json.loads(args.blocks.read_text(encoding="utf-8"))
    block_starts = args.block_starts or ["5C0019"]
    if len(set(block_starts)) != len(block_starts):
        raise SystemExit("duplicate --block-start values are not allowed")
    blocks_by_start = {
        str(row.get("start")): row for row in blocks_doc.get("blocks", [])
    }
    selected_blocks: list[dict[str, Any]] = []
    for block_start in block_starts:
        block = blocks_by_start.get(block_start)
        if block is None:
            raise SystemExit(f"block {block_start} not found")
        if block.get("bank") != "5C" or int(block.get("coherent") or 0) < 2:
            raise SystemExit("P1 batch requires coherent bank-5C text blocks")
        selected_blocks.append(block)

    original = bytes(load_rom(args.original_rom))
    original_identity = _identity(args.original_rom)
    locked_original = (base.get("inputs") or {}).get("original_rom") or {}
    if (
        original_identity["size"] != locked_original.get("size")
        or original_identity["sha256"] != locked_original.get("sha256")
    ):
        raise SystemExit("Original ROM does not match the base manifest")

    block_by_record_id: dict[str, dict[str, Any]] = {}
    for block in selected_blocks:
        for row in block.get("targets", []):
            record_id = f"aux:{row['abs']}"
            if record_id in block_by_record_id:
                raise SystemExit(f"record {record_id} appears in multiple selected blocks")
            block_by_record_id[record_id] = block
    block_ids = set(block_by_record_id)
    decisions = list((base.get("population") or {}).get("included") or []) + list(
        (base.get("population") or {}).get("excluded") or []
    )
    excluded_record_ids = set(args.excluded_records)
    if len(excluded_record_ids) != len(args.excluded_records):
        raise SystemExit("duplicate --exclude-record values are not allowed")
    unknown_exclusions = excluded_record_ids - block_ids
    if unknown_exclusions:
        raise SystemExit(
            "excluded records are outside the selected blocks: "
            + ", ".join(sorted(unknown_exclusions))
        )
    selected_source = [
        row
        for row in decisions
        if row.get("record_id") in block_ids
        and row.get("record_id") not in excluded_record_ids
        and row.get("reason")
        == "excluded_prefix_unprovable:ambiguous_leading_byte"
    ]
    if not selected_source:
        raise SystemExit("no ambiguous-leading records found in the selected block")

    proof_rows: list[dict[str, Any]] = []
    selected_ids = {str(row["record_id"]) for row in selected_source}
    sb = stock_base(original)
    for row in sorted(selected_source, key=lambda item: int(item["logical_address"])):
        block = block_by_record_id[str(row["record_id"])]
        boundary = row.get("boundary") or {}
        start = int(row["logical_address"])
        capacity = int(boundary.get("payload_capacity") or 0)
        terminator = int(boundary.get("terminator_offset") or 0)
        payload = original[sb + start : sb + start + capacity]
        if start >> 16 != 0x5C or terminator != start + capacity:
            raise SystemExit(f"invalid bank/boundary for {row['record_id']}")
        if original[sb + terminator] != 0:
            raise SystemExit(f"missing Original terminator for {row['record_id']}")
        if _sha256_bytes(payload) != row.get("original_payload_sha256"):
            raise SystemExit(f"Original payload drift for {row['record_id']}")
        proof: dict[str, Any] = {
            "schema_version": 1,
            "proof_kind": "aux_bank5c_continuation_zstring",
            "record_id": row["record_id"],
            "logical_address": start,
            "bank": "5C",
            "prefix_bytes": 0,
            "original_payload_sha256": row["original_payload_sha256"],
            "payload_capacity": capacity,
            "terminator_offset": terminator,
            "original_rule": "nul_terminated_continuation_text_no_record_prefix",
            "block_start": str(block["start"]),
            "block_end_exclusive": str(block["end_exclusive"]),
        }
        proof["proof_sha256"] = deterministic_json_sha256(proof)
        proof_rows.append(proof)

    total_ambiguous = sum(
        row.get("reason") == "excluded_prefix_unprovable:ambiguous_leading_byte"
        for row in decisions
    )
    selected_block_summaries = [
        {
            "bank": block["bank"],
            "start": block["start"],
            "end": block["end"],
            "end_exclusive": block["end_exclusive"],
            "coherent_records": block["coherent"],
        }
        for block in selected_blocks
    ]
    proof_document: dict[str, Any] = {
        "schema_version": 2,
        "generated_by": "tools/build_main_p1_prefix_batch.py",
        "read_only": True,
        "scope": "P1 bank-5C vetted continuation-text blocks",
        "base_manifest": _identity(args.base_manifest),
        "blocks_source": _identity(args.blocks),
        "original_rom": original_identity,
        "selected_blocks": selected_block_summaries,
        "deliberately_excluded_records": sorted(excluded_record_ids),
        "selected_count": len(proof_rows),
        "records": proof_rows,
        "remaining_ambiguous_not_selected": total_ambiguous - len(proof_rows),
        "ok": True,
    }
    if len(selected_block_summaries) == 1:
        proof_document["block"] = selected_block_summaries[0]
    proof_document["proof_set_sha256"] = deterministic_json_sha256(proof_rows)
    _write_json(args.out_proof, proof_document)

    by_id = {row["record_id"]: row for row in proof_rows}
    rows: list[dict[str, Any]] = []
    for source in decisions:
        row = copy.deepcopy(source)
        record_id = str(row["record_id"])
        if record_id in selected_ids:
            row["p1_text_initial_proof"] = by_id[record_id]
            _retarget(row, included=True, reason="mixed_hangul_and_japanese")
        else:
            _retarget(row, included=False, reason="excluded_p1_batch_not_selected")
        rows.append(row)
    rows.sort(key=lambda row: (str(row.get("region")), int(row["logical_address"])))
    included = [row for row in rows if row["included"]]
    excluded = [row for row in rows if not row["included"]]

    population = copy.deepcopy(base["population"])
    population["included"] = included
    population["excluded"] = excluded
    population["target_ids"] = [row["record_id"] for row in included]
    population["counts"] = _counts(rows)
    population["target_set_sha256"] = deterministic_json_sha256(
        [row["target_sha256"] for row in included]
    )
    population["followup_batch"] = args.batch_name or (
        "p1_bank5c_" + "_".join(block_starts)
    )

    result = copy.deepcopy(base)
    result["generated_by"] = "tools/build_main_p1_prefix_batch.py"
    result["population"] = population
    result.setdefault("tool_inputs", {})["p1_text_initial_proof"] = _identity(
        args.out_proof
    )
    result["p1_source_manifest"] = {
        "path": str(args.base_manifest.resolve()),
        "manifest_sha256": base.get("manifest_sha256"),
    }
    result.pop("manifest_sha256", None)
    result["manifest_sha256"] = deterministic_json_sha256(result)
    _write_json(args.out_manifest, result)
    print(
        json.dumps(
            {
                "selected": len(included),
                "records": population["target_ids"],
                "proof": str(args.out_proof),
                "manifest": str(args.out_manifest),
                "manifest_sha256": result["manifest_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
