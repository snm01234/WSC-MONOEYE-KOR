#!/usr/bin/env python3
"""Merge and validate source-grounded dialogue readability rewrites.

The result is a single review/apply catalog for two policies:
1. semantic rewrites for legacy 2x20-cell groups that required >=3 deleted
   spaces; and
2. word-boundary-only reflow when the legacy split landed inside a Korean
   token even though the whole token can move to row 2.

This script does not modify a ROM.
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from hangul_marker import marker_code
from monoeye_rom import Tbl
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

WORKLIST = ROOT / "out/script/dialogue_readability_worklist.json"
BATCH_GLOB = str(ROOT / "data/dialogue_readability_batches/output_*.json")
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT_JSON = ROOT / "out/script/dialogue_readability_changes.json"
OUT_MD = ROOT / "out/script/dialogue_readability_changes.md"
OUT_VALIDATION = ROOT / "out/script/dialogue_readability_validation.json"
LIMIT = 20
JP_RE = re.compile(r"[ぁ-んァ-ン一-龥]")


class MergeError(RuntimeError):
    pass


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cells(text: str) -> int:
    return len(text.replace("<E62F>", ""))


def md_cell(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


def validate_text(text: str, tbl: Tbl, label: str) -> tuple[str, int, int]:
    normalized = normalize_ko_text(str(text))
    width = cells(normalized)
    if width > LIMIT:
        raise MergeError(f"{label}: over {LIMIT} cells ({width}): {normalized!r}")
    if JP_RE.search(normalized):
        raise MergeError(f"{label}: visible Japanese remains: {normalized!r}")
    encoded = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if encoded is None or b"\x00" in encoded:
        raise MergeError(f"{label}: not safely encodable: {normalized!r}")
    return normalized, width, len(encoded)


def paired(values: list[str]) -> str:
    return " / ".join(values)


def main() -> int:
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    semantic = work.get("semantic_rewrite_groups") or []
    boundary = work.get("word_boundary_reflow_only_groups") or []
    expected = {str(g["group_id"]): g for g in semantic}

    batch_paths = [Path(p) for p in sorted(glob.glob(BATCH_GLOB))]
    if len(batch_paths) != 15:
        raise MergeError(f"expected 15 rewrite output batches, found {len(batch_paths)}")

    provided: dict[str, tuple[dict[str, Any], str]] = {}
    duplicate_ids: list[str] = []
    for path in batch_paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for group in doc.get("groups") or []:
            gid = str(group.get("group_id") or "")
            if gid in provided:
                duplicate_ids.append(gid)
            provided[gid] = (group, str(path.relative_to(ROOT)).replace("\\", "/"))

    missing = sorted(set(expected) - set(provided))
    extra = sorted(set(provided) - set(expected))
    if duplicate_ids or missing or extra:
        raise MergeError(
            f"rewrite ID coverage failure duplicates={duplicate_ids[:20]} "
            f"missing={missing[:20]} extra={extra[:20]}"
        )

    merged: list[dict[str, Any]] = []
    record_count = 0
    max_cells = 0
    max_encoded = 0
    encoded_total = 0

    for source_group in semantic:
        gid = str(source_group["group_id"])
        out_group, batch_path = provided[gid]
        rows = out_group.get("rows")
        if not isinstance(rows, list) or len(rows) != 2:
            raise MergeError(f"{gid}: expected exactly two output rows")
        checked: list[str] = []
        widths: list[int] = []
        encoded_lengths: list[int] = []
        for i, row in enumerate(rows):
            text, width, encoded_len = validate_text(str(row), tbl, f"{gid} row {i + 1}")
            checked.append(text)
            widths.append(width)
            encoded_lengths.append(encoded_len)
            max_cells = max(max_cells, width)
            max_encoded = max(max_encoded, encoded_len)
            encoded_total += encoded_len
        records = source_group.get("records") or []
        if len(records) != 2:
            raise MergeError(f"{gid}: source group does not have two records")
        record_count += 2
        merged.append({
            "group_id": gid,
            "scope": source_group.get("scope"),
            "classification": "semantic_rewrite_required",
            "legacy_spaces_removed": source_group.get("legacy_spaces_removed"),
            "legacy_split_inside_word": bool(source_group.get("legacy_split_inside_word")),
            "addresses": [str(r["abs"]).upper() for r in records],
            "source_jp_rows": [str(r.get("source_jp") or "") for r in records],
            "pre20cell_ko_rows": [str(x) for x in source_group.get("pre20cell_rows") or []],
            "legacy_dense_rows": [str(x) for x in source_group.get("legacy_after_rows") or []],
            "current_main_rows": [str(x) for x in source_group.get("current_main_rows") or []],
            "after_rows": checked,
            "after_cells": widths,
            "after_encoded_bytes": encoded_lengths,
            "change_summary": str(out_group.get("change_summary") or "원문 의미를 유지하며 2×20셀용 자연스러운 한국어로 재구성"),
            "review_source": batch_path,
        })

    for source_group in boundary:
        gid = str(source_group["group_id"])
        rows = source_group.get("word_boundary_reflow")
        if not isinstance(rows, list) or len(rows) != 2:
            raise MergeError(f"{gid}: missing word-boundary reflow")
        checked: list[str] = []
        widths: list[int] = []
        encoded_lengths: list[int] = []
        for i, row in enumerate(rows):
            text, width, encoded_len = validate_text(str(row), tbl, f"{gid} boundary row {i + 1}")
            checked.append(text)
            widths.append(width)
            encoded_lengths.append(encoded_len)
            max_cells = max(max_cells, width)
            max_encoded = max(max_encoded, encoded_len)
            encoded_total += encoded_len
        records = source_group.get("records") or []
        if len(records) != 2:
            raise MergeError(f"{gid}: boundary group does not have two records")
        record_count += 2
        merged.append({
            "group_id": gid,
            "scope": source_group.get("scope"),
            "classification": "word_boundary_reflow_only",
            "legacy_spaces_removed": source_group.get("legacy_spaces_removed"),
            "legacy_split_inside_word": True,
            "addresses": [str(r["abs"]).upper() for r in records],
            "source_jp_rows": [str(r.get("source_jp") or "") for r in records],
            "pre20cell_ko_rows": [str(x) for x in source_group.get("pre20cell_rows") or []],
            "legacy_dense_rows": [str(x) for x in source_group.get("legacy_after_rows") or []],
            "current_main_rows": [str(x) for x in source_group.get("current_main_rows") or []],
            "after_rows": checked,
            "after_cells": widths,
            "after_encoded_bytes": encoded_lengths,
            "change_summary": "20셀 경계에서 단어가 잘리던 줄바꿈을 단어 단위로 다음 행에 넘김; 어휘와 의미는 변경하지 않음",
            "review_source": "out/script/dialogue_readability_worklist.json",
        })

    all_addresses = [addr for g in merged for addr in g["addresses"]]
    if len(all_addresses) != len(set(all_addresses)):
        raise MergeError("duplicate record address in merged catalog")

    summary = {
        "semantic_rewrite_groups": len(semantic),
        "word_boundary_reflow_groups": len(boundary),
        "total_groups": len(merged),
        "total_records": record_count,
        "max_after_cells": max_cells,
        "max_encoded_bytes": max_encoded,
        "encoded_bytes_total": encoded_total,
        "id_coverage_ok": True,
        "address_unique_ok": True,
        "width_ok": max_cells <= LIMIT,
        "encoding_ok": True,
    }
    catalog = {
        "schema_version": 1,
        "generated_by": "tools/merge_dialogue_readability_rewrites.py",
        "policy": work.get("policy") or {},
        "current_tip": work.get("current_tip") or {},
        "summary": summary,
        "groups": merged,
    }
    OUT_JSON.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md = [
        "# Dialogue readability rewrite catalog",
        "",
        "## Policy",
        "",
        "- Each rendered dialogue row must be 20 cells or fewer.",
        "- If a word would be split at the 20-cell boundary, move the whole word to row 2 when possible.",
        "- Legacy 2-row space-only reflow that deleted 3 or more spaces is rewritten from the Japanese source instead of being made denser.",
        "- The table records the Japanese source, pre-20-cell Korean, dense legacy result, final rewrite, and the reason for the change.",
        "",
        "## Summary",
        "",
        f"- Semantic rewrite groups: {summary['semantic_rewrite_groups']}",
        f"- Word-boundary-only groups: {summary['word_boundary_reflow_groups']}",
        f"- Total groups / records: {summary['total_groups']} / {summary['total_records']}",
        f"- Maximum final row width: {summary['max_after_cells']} cells",
        "",
        "## Changes",
        "",
        "| Group | Address | Type | Japanese source | Pre-20cell Korean | Dense legacy | Final 2×20 Korean | Cells | Change |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for g in merged:
        md.append(
            "| " + " | ".join([
                md_cell(g["group_id"]),
                md_cell(" / ".join(g["addresses"])),
                md_cell(g["classification"]),
                md_cell(paired(g["source_jp_rows"])),
                md_cell(paired(g["pre20cell_ko_rows"])),
                md_cell(paired(g["legacy_dense_rows"])),
                md_cell(paired(g["after_rows"])),
                md_cell(" / ".join(str(x) for x in g["after_cells"])),
                md_cell(g["change_summary"]),
            ]) + " |"
        )
    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")

    validation = {
        "schema_version": 1,
        "generated_by": "tools/merge_dialogue_readability_rewrites.py",
        "ok": True,
        "worklist_sha256": sha_file(WORKLIST),
        "batch_outputs": [
            {
                "path": str(p.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha_file(p),
            }
            for p in batch_paths
        ],
        "catalog": {
            "path": str(OUT_JSON.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha_file(OUT_JSON),
        },
        "summary": summary,
    }
    OUT_VALIDATION.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(OUT_JSON)
    print(OUT_MD)
    print(OUT_VALIDATION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
