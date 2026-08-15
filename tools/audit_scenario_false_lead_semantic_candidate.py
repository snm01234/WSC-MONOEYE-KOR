#!/usr/bin/env python3
"""Audit semantic scenario ``18=こ`` false-lead fixes and recurrence scope."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
SPEC = ROOT / "data/scenario_false_lead_semantic_followup_ko.json"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tip", type=Path, required=True)
    ap.add_argument("--tbl", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))["contracts"]
    natural = []
    offset1_total = 0
    for row in contracts:
        if row.get("family") != "scenario_bundle" or row.get("line_role") != "continuation":
            continue
        source = str(row.get("source_payload_hex", ""))
        baseline = str(row.get("baseline_payload_hex", ""))
        original = str(row.get("original_japanese", ""))
        if source.startswith("18") and baseline.startswith("18E518"):
            offset1_total += 1
            # Semantic incompleteness after stripping the lead is proven only
            # for the stutter punctuation or こんど fragment found in this ROM.
            if original.startswith("こ、") or original.startswith("こん"):
                natural.append({"abs": row["address"], "jp": original, "baseline": row.get("baseline_text", "")})

    expected = {row["abs"] for row in spec["targets"]}
    found = {row["abs"] for row in natural}
    rom = args.tip.read_bytes()
    tbl = Tbl.load(args.tbl)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)
    fixed = []
    failures = []
    for row in spec["targets"]:
        logical = int(row["abs"], 16)
        got = read_encoded_z_safe(rom, sb + logical, max_len=128)
        if got is None:
            failures.append({"abs": row["abs"], "reason": "unreadable"})
            continue
        raw = bytes(got[0])
        rendered = strip_pad(dictionary.expand(raw, tbl))
        ok = raw[:1] != b"\x18" and rendered == row["after"] and not any(
            "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff" for ch in rendered
        )
        item = {"abs": row["abs"], "raw": raw.hex().upper(), "rendered": rendered, "ok": ok}
        fixed.append(item)
        if not ok:
            failures.append(item)

    status = "clean" if found == expected and not failures else "violations_found"
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_scenario_false_lead_semantic_candidate.py",
        "status": status,
        "tip_sha256": sha(rom),
        "counts": {
            "scenario_offset1_continuations_reviewed": offset1_total,
            "semantic_false_lead_candidates": len(natural),
            "catalog_targets": len(expected),
            "fixed": sum(bool(row["ok"]) for row in fixed),
            "failures": len(failures),
            "unresolved_semantic_candidates": len(found - expected),
            "catalog_without_structural_evidence": len(expected - found),
        },
        "semantic_candidates": natural,
        "fixed_targets": fixed,
        "failures": failures,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0 if status == "clean" else 1


if __name__ == "__main__":
    raise SystemExit(main())
