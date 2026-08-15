#!/usr/bin/env python3
"""Build a readability worklist for the promoted 20-cell dialogue patch.

Goals:
* detect legacy space_only_reflow groups whose first/second row boundary cuts a
  Korean word or phrase token even though the whole token can be moved to the
  next row;
* quarantine 2-row groups that had to delete >=3 visible spaces to fit 40
  cells.  Those groups must be rewritten from the Japanese source rather than
  made denser;
* bind the review list to the current promoted main TIP while retaining the
  original pre-20cell Korean and Japanese source rows for semantic review.

This tool does not modify the ROM.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
OLD_WORKLIST = ROOT / "out/script/dialogue_20cell_worklist.json"
OLD_REPORT = ROOT / "out/patch/dialogue_20cell_report.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3 = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/script/dialogue_readability_worklist.json"
LIMIT = 20
SPACE_RE = re.compile(r"[ \u3000]+")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm_spaces(text: str) -> str:
    return SPACE_RE.sub("\u3000", text.strip(" \u3000"))


def count_spaces(text: str) -> int:
    return sum(ch in {" ", "\u3000"} for ch in text)


def nospace(text: str) -> str:
    return text.replace(" ", "").replace("\u3000", "")


def word_wrap_two_rows(texts: list[str], limit: int = LIMIT) -> list[str] | None:
    """Wrap at an existing word boundary without deleting internal spaces.

    The separator chosen as the row break consumes no display cell.  All other
    normalized inter-word spaces are retained.  This intentionally refuses to
    split inside a token; callers may then choose semantic rewriting instead.
    """
    stream = norm_spaces("\u3000".join(norm_spaces(x) for x in texts if norm_spaces(x)))
    if not stream:
        return ["", ""]
    words = stream.split("\u3000")
    if any(len(word) > limit for word in words):
        return None
    for cut in range(1, len(words)):
        left = "\u3000".join(words[:cut])
        right = "\u3000".join(words[cut:])
        if len(left) <= limit and len(right) <= limit:
            return [left, right]
    return None


def legacy_split_inside_word(texts: list[str], after: list[str]) -> bool:
    """Return True when the legacy row boundary lands inside a source token."""
    if len(texts) != 2 or len(after) != 2:
        return False
    words = norm_spaces("\u3000".join(norm_spaces(x) for x in texts)).split("\u3000")
    boundaries: set[int] = set()
    cursor = 0
    for word in words[:-1]:
        cursor += len(nospace(word))
        boundaries.add(cursor)
    first_nonspace = len(nospace(after[0]))
    return first_nonspace not in boundaries


def decode_current(rom: bytes, dictionary, tbl: Tbl, abs_hex: str) -> dict[str, Any]:
    logical = int(abs_hex, 16)
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise RuntimeError(f"unreadable current record {abs_hex}")
    payload, term = bytes(got[0]), int(got[1])
    prefix, body, kind = split_prefix_body(payload)
    rendered = dictionary.expand(body, tbl).rstrip("\u3000 \t")
    return {
        "render": rendered,
        "cells": len(rendered.replace("<E62F>", "")),
        "payload_hex": payload.hex().upper(),
        "prefix_hex": prefix.hex().upper(),
        "body_hex": body.hex().upper(),
        "kind": kind,
        "terminator": f"{term - sb:06X}",
    }


def main() -> int:
    rom = TIP.read_bytes()
    tbl = Tbl.load(TBL)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT), load_ext_meta(EXT3))
    work = json.loads(OLD_WORKLIST.read_text(encoding="utf-8"))
    report = json.loads(OLD_REPORT.read_text(encoding="utf-8"))
    report_by_abs = {str(row["abs"]).upper(): row for row in report.get("targets") or []}

    rewrite_groups: list[dict[str, Any]] = []
    word_reflow_groups: list[dict[str, Any]] = []
    already_changed_groups: list[dict[str, Any]] = []
    legacy_split_groups = 0

    for group in work.get("groups") or []:
        if group.get("mode") != "reflow_current_nonspace_exact":
            continue
        records = group.get("records") or []
        if len(records) != 2:
            continue
        legacy_after = [str(report_by_abs[str(r["abs"]).upper()]["after"]) for r in records]
        before_stream = "\u3000".join(norm_spaces(str(r["current"])) for r in records)
        legacy_stream = "\u3000".join(norm_spaces(x) for x in legacy_after)
        removed = count_spaces(before_stream) - count_spaces(legacy_stream)
        split_inside = legacy_split_inside_word([str(r["current"]) for r in records], legacy_after)
        if split_inside:
            legacy_split_groups += 1

        current_rows = [decode_current(rom, dictionary, tbl, str(r["abs"]).upper()) for r in records]
        current_render = [str(x["render"]) for x in current_rows]
        changed_since_20cell = current_render != legacy_after
        word_after = word_wrap_two_rows([str(r["current"]) for r in records])

        item = {
            "group_id": group["group_id"],
            "scope": group["scope"],
            "line_limit": LIMIT,
            "capacity": 40,
            "legacy_spaces_removed": removed,
            "legacy_split_inside_word": split_inside,
            "pre20cell_rows": [str(r["current"]) for r in records],
            "legacy_after_rows": legacy_after,
            "current_main_rows": current_render,
            "current_main_cells": [int(x["cells"]) for x in current_rows],
            "word_boundary_reflow": word_after,
            "records": [
                {
                    "abs": str(r["abs"]).upper(),
                    "source_jp": str(r.get("source_jp") or ""),
                    "current_source_ko": str(r["current"]),
                    "legacy_after": legacy_after[i],
                    "current_main": current_render[i],
                    "current_payload_hex": current_rows[i]["payload_hex"],
                    "current_prefix_hex": current_rows[i]["prefix_hex"],
                    "current_body_hex": current_rows[i]["body_hex"],
                    "current_terminator": current_rows[i]["terminator"],
                }
                for i, r in enumerate(records)
            ],
        }

        if changed_since_20cell:
            item["classification"] = "already_manually_changed_after_20cell"
            already_changed_groups.append(item)
        elif removed >= 3:
            item["classification"] = "semantic_rewrite_required"
            rewrite_groups.append(item)
        elif split_inside and word_after is not None and word_after != legacy_after:
            item["classification"] = "word_boundary_reflow_only"
            word_reflow_groups.append(item)

    doc = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_readability_worklist.py",
        "policy": {
            "line_limit": LIMIT,
            "two_row_capacity": 40,
            "semantic_rewrite_threshold_removed_spaces": 3,
            "word_boundary_rule": "do not split a source Korean token at the 20-cell row edge when the whole token can move to row 2",
            "rewrite_rule": "groups that needed >=3 removed spaces are retranslated/rephrased from source_jp; do not solve them by further space deletion",
        },
        "current_tip": {
            "path": str(TIP.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(rom),
        },
        "legacy_20cell_parent_sha256": report.get("parent", {}).get("sha256"),
        "legacy_20cell_candidate_sha256": report.get("candidate", {}).get("sha256"),
        "summary": {
            "legacy_two_row_auto_groups": sum(
                1 for g in work.get("groups") or []
                if g.get("mode") == "reflow_current_nonspace_exact" and len(g.get("records") or []) == 2
            ),
            "legacy_split_inside_word_groups": legacy_split_groups,
            "semantic_rewrite_groups": len(rewrite_groups),
            "semantic_rewrite_records": sum(len(x["records"]) for x in rewrite_groups),
            "word_boundary_reflow_only_groups": len(word_reflow_groups),
            "already_changed_after_20cell_groups": len(already_changed_groups),
        },
        "semantic_rewrite_groups": rewrite_groups,
        "word_boundary_reflow_only_groups": word_reflow_groups,
        "already_changed_after_20cell_groups": already_changed_groups,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(doc["summary"], ensure_ascii=False, indent=2))
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
