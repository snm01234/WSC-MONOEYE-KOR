#!/usr/bin/env python3
"""Independent audit for the 567 legacy one-row space-only dialogue rewrites.

The legacy 20-cell pass compacted some singleton scenario records by deleting
inter-word spaces until the text fit one 20-cell row.  This audit is deliberately
separate from the builder: it reconstructs the exact singleton population from
the old worklist, loads the seven source-grounded rewrite batches, decodes the
candidate ROM, and fails closed on coverage, render, width, spacing, Japanese
residue, or word-width regressions.
"""
from __future__ import annotations

import argparse
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

WORKLIST = ROOT / "out/script/dialogue_20cell_worklist.json"
BATCHES = tuple(ROOT / f"data/dialogue_singleton_rewrite_batch{i:03d}.json" for i in range(1, 8))
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_ROM = ROOT / "out/patch/dialogue_readability_candidate.wsc"
DEFAULT_OUT = ROOT / "out/patch/dialogue_readability_singleton_audit.json"
EXPECTED = 567
LIMIT = 20
JP_RE = re.compile(r"[ぁ-んァ-ン一-龥]")
SPACE_RE = re.compile(r"[ \u3000]+")
ANCHORS = {
    "612C19": "보통　사람은　할　수　없는　일이지。",
    "612F98": "제게　맡겨　주시겠습니까？",
    "613062": "자네도　그걸　모르는　건　아니겠지？",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expected_population() -> dict[str, dict[str, str]]:
    doc = json.loads(WORKLIST.read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    for group in doc.get("groups") or []:
        records = group.get("records") or []
        if group.get("mode") != "reflow_current_nonspace_exact" or len(records) != 1:
            continue
        address = str(records[0]["abs"]).upper()
        auto = list(group.get("auto_after") or [])
        if len(auto) != 1 or address in out:
            raise RuntimeError(f"legacy singleton worklist shape drift at {address}")
        out[address] = {
            "before": str(auto[0]),
            "jp": str(records[0].get("source_jp") or ""),
        }
    if len(out) != EXPECTED:
        raise RuntimeError(f"legacy singleton population {len(out)} != {EXPECTED}")
    return out


def target_population() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in BATCHES:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for raw_address, raw_text in (doc.get("targets") or {}).items():
            address = str(raw_address).upper()
            if address in out:
                raise RuntimeError(f"duplicate singleton target {address}")
            out[address] = str(raw_text)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    rom = args.rom.read_bytes()
    expected = expected_population()
    targets = target_population()
    coverage_ok = set(expected) == set(targets)

    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)
    failures: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    for address in sorted(set(expected) | set(targets), key=lambda x: int(x, 16)):
        if address not in expected or address not in targets:
            failures.append({"abs": address, "reason": "coverage_mismatch"})
            continue
        got = read_encoded_z_safe(rom, sb + int(address, 16), max_len=256)
        if got is None:
            failures.append({"abs": address, "reason": "unreadable"})
            continue
        payload = bytes(got[0])
        prefix, body, _kind = split_prefix_body(payload)
        rendered = dictionary.expand(body, tbl).rstrip("\u3000 \t")
        desired = targets[address]
        words = [part for part in SPACE_RE.split(rendered.strip(" \u3000")) if part]
        word_max = max((len(word) for word in words), default=0)
        reasons: list[str] = []
        if rendered != desired:
            reasons.append("render_mismatch")
        if len(rendered.replace("<E62F>", "")) > LIMIT:
            reasons.append("over_20")
        if JP_RE.search(rendered.replace("<E62F>", "")):
            reasons.append("japanese_remains")
        if len(rendered) >= 17 and not any(ch in rendered for ch in {" ", "\u3000"}):
            reasons.append("dense_no_spacing")
        if word_max > LIMIT:
            reasons.append("word_over_20")
        if reasons:
            failures.append({"abs": address, "reasons": reasons, "rendered": rendered, "desired": desired})
        rows.append({
            "abs": address,
            "source_jp": expected[address]["jp"],
            "legacy_compacted": expected[address]["before"],
            "rendered": rendered,
            "cells": len(rendered.replace("<E62F>", "")),
            "max_word_cells": word_max,
            "prefix_hex": prefix.hex().upper(),
        })

    anchors = {
        address: next((row["rendered"] for row in rows if row["abs"] == address), None)
        for address in ANCHORS
    }
    anchor_ok = anchors == ANCHORS
    if not anchor_ok:
        failures.append({"reason": "screenshot_anchor_mismatch", "expected": ANCHORS, "got": anchors})

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_dialogue_singleton_rewrites.py",
        "ok": coverage_ok and anchor_ok and not failures,
        "rom": {"path": str(args.rom), "size": len(rom), "sha256": sha(rom)},
        "counts": {
            "expected": len(expected),
            "targets": len(targets),
            "decoded": len(rows),
            "failures": len(failures),
            "over_20": sum(row["cells"] > LIMIT for row in rows),
            "dense_no_spacing_17plus": sum(
                row["cells"] >= 17 and not any(ch in row["rendered"] for ch in {" ", "\u3000"})
                for row in rows
            ),
            "max_cells": max((row["cells"] for row in rows), default=0),
            "max_word_cells": max((row["max_word_cells"] for row in rows), default=0),
        },
        "anchors": anchors,
        "failures": failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "counts": report["counts"], "anchors": anchors}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
