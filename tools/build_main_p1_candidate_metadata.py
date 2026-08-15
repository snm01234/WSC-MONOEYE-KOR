#!/usr/bin/env python3
"""Prepare candidate-only metadata for a resolved P1 plan.

This does not modify a ROM or the canonical P0 metadata.  It extends the P0
expected-byte ranges with the P1 plan's record bodies and builds a dedicated UI
allowlist report containing both already accepted residual rewrites and the
legacy aux rewrites.  The transaction will write its new P1 report beside that
merged allowlist before gates run.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from monoeye_rom import load_rom, stock_base


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-meta", type=Path, required=True)
    ap.add_argument("--baseline-rom", type=Path, required=True)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--canonical-residual-report", type=Path, required=True)
    ap.add_argument("--aux-report", type=Path, required=True)
    ap.add_argument("--out-baseline-meta", type=Path, required=True)
    ap.add_argument(
        "--ui-evidence-dir",
        type=Path,
        help=(
            "copy the accepted *_report.json evidence set into the candidate UI "
            "directory before writing the merged allowlist"
        ),
    )
    ap.add_argument("--out-ui-allowlist", type=Path, required=True)
    args = ap.parse_args()

    metadata = json.loads(args.baseline_meta.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    baseline = bytes(load_rom(args.baseline_rom))
    sb = stock_base(baseline)
    expected_tip = (metadata.get("current_tip") or {}).get("sha256")
    if expected_tip != _sha256(args.baseline_rom.read_bytes()):
        raise SystemExit("baseline ROM does not match P0 metadata")

    ranges = list(metadata.get("stock_approved_ranges") or [])
    existing = {(str(row.get("owner_id")), str(row.get("start"))) for row in ranges}
    added: list[dict[str, Any]] = []
    for target in plan.get("targets") or []:
        if target.get("status") != "resolved":
            continue
        record_start = int(str(target["abs"]), 16)
        prefix = int(target.get("prefix_bytes") or 0)
        payload_capacity = int(target["payload_capacity"])
        start = record_start + prefix
        end = record_start + payload_capacity
        owner = str(target["record_id"])
        key = (owner, f"{start:06X}")
        if key in existing:
            raise SystemExit(f"P1 range already exists in baseline metadata: {owner}")
        prior_value = baseline[sb + start : sb + end]
        if len(prior_value) != end - start:
            raise SystemExit(f"baseline range outside ROM: {owner}")
        try:
            expected_value = bytes.fromhex(str(target["new_body_hex"]))
        except (KeyError, ValueError) as exc:
            raise SystemExit(f"invalid planned body for {owner}") from exc
        if len(expected_value) != end - start:
            raise SystemExit(f"planned body length mismatch for {owner}")
        row = {
            "start": f"{start:06X}",
            "end": f"{end:06X}",
            "kind": "record_body",
            "owner_id": owner,
            # The verifier compares the candidate bytes to this exact value.
            # Existing P0 ranges contain their already-accepted TIP bytes; a
            # new P1 range must contain the resolved plan's candidate body.
            "baseline_hex": expected_value.hex().upper(),
            "baseline_sha256": _sha256(expected_value),
            "prior_tip_hex": prior_value.hex().upper(),
            "prior_tip_sha256": _sha256(prior_value),
        }
        ranges.append(row)
        added.append(row)
        existing.add(key)
    if not added:
        raise SystemExit("resolved P1 plan contains no record ranges")

    result = copy.deepcopy(metadata)
    result["generated_by"] = "tools/build_main_p1_candidate_metadata.py"
    result["status"] = "p1_candidate_metadata"
    result["stock_approved_ranges"] = sorted(
        ranges, key=lambda row: int(str(row["start"]), 16)
    )
    result["p1_candidate_extension"] = {
        "baseline_rom_sha256": expected_tip,
        "source_p0_metadata": str(args.baseline_meta.resolve()),
        "source_plan": str(args.plan.resolve()),
        "added_record_body_ranges": len(added),
        "owners": [row["owner_id"] for row in added],
    }
    _write(args.out_baseline_meta, result)

    aux = json.loads(args.aux_report.read_text(encoding="utf-8"))
    residual = json.loads(args.canonical_residual_report.read_text(encoding="utf-8"))
    if not aux.get("ok") or not residual.get("ok"):
        raise SystemExit("canonical aux/residual report is not accepted")
    merged = copy.deepcopy(aux)
    rows = list(aux.get("applied") or []) + list(residual.get("applied") or [])
    by_extent: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["abs"]).upper(), int(row["payload_len"]))
        by_extent[key] = row
    merged["generated_by"] = "tools/build_main_p1_candidate_metadata.py"
    merged["ok"] = True
    merged["applied"] = [
        by_extent[key]
        for key in sorted(by_extent, key=lambda item: (int(item[0], 16), item[1]))
    ]
    merged["p1_candidate_allowlist"] = {
        "legacy_aux_records": len(aux.get("applied") or []),
        "accepted_residual_records": len(residual.get("applied") or []),
        "merged_unique_extents": len(merged["applied"]),
        "note": "The P1 transaction report adds the new batch separately.",
    }

    copied_ui_reports = 0
    if args.ui_evidence_dir is not None:
        if not args.ui_evidence_dir.is_dir():
            raise SystemExit(f"UI evidence directory does not exist: {args.ui_evidence_dir}")
        destination = args.out_ui_allowlist.parent
        destination.mkdir(parents=True, exist_ok=True)
        for source in sorted(args.ui_evidence_dir.glob("*_report.json")):
            if source.name == args.out_ui_allowlist.name:
                continue
            shutil.copy2(source, destination / source.name)
            copied_ui_reports += 1
        if copied_ui_reports == 0:
            raise SystemExit(f"UI evidence directory has no report files: {args.ui_evidence_dir}")

    _write(args.out_ui_allowlist, merged)

    print(
        json.dumps(
            {
                "baseline_ranges": len(result["stock_approved_ranges"]),
                "p1_ranges_added": len(added),
                "ui_allowlist_extents": len(merged["applied"]),
                "ui_evidence_reports_copied": copied_ui_reports,
                "baseline_meta": str(args.out_baseline_meta),
                "ui_allowlist": str(args.out_ui_allowlist),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
