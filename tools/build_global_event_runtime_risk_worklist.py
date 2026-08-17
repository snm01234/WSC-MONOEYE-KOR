#!/usr/bin/env python3
"""Build a deterministic priority worklist from the global event-risk audit.

This tool is read-only with respect to ROMs.  It classifies structural suspects
and measures which exact4 records can already be restored with two existing
native dictionary tokens while preserving the currently rendered Korean text.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, load_rom  # noqa: E402

AUDIT = ROOT / "out/patch/global_event_runtime_risk_v3.json"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/global_event_runtime_risk_priority_worklist.json"


def native_pair_solutions(dictionary, tbl, text: str, rendered_map: dict[str, list[int]]) -> list[dict]:
    out = []
    for split in range(1, len(text)):
        left, right = text[:split], text[split:]
        if left not in rendered_map or right not in rendered_map:
            continue
        for a in rendered_map[left]:
            for b in rendered_map[right]:
                out.append({"left_index": f"{a:04X}", "right_index": f"{b:04X}", "left": left, "right": right})
    return out


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    rom = bytes(load_rom(MAIN))
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    tbl = Tbl.load(TBL)
    rendered_map: dict[str, list[int]] = defaultdict(list)
    for index in range(dictionary.count):
        text = dictionary.expand_index(index, tbl)
        if text:
            rendered_map[text].append(index)

    exact = audit["structural_suspects"]["exact4_source_two_native_to_direct_ext3"]
    control18 = audit["structural_suspects"]["control18_source_two_native_to_direct_ext3"]
    exact_rows = []
    for row in exact:
        body = bytes.fromhex(row["candidate_body_hex"])
        text = dictionary.expand(body, tbl)
        sols = native_pair_solutions(dictionary, tbl, text, rendered_map)
        if row["route"] == "scenario_first" and row["status"] == "active" and row["next_control"] == "1728":
            priority = "P1_active_first_double_nul_to_1728"
        elif row["route"] == "scenario_first" and row["status"] == "active" and row["next_lead"] == "08":
            priority = "P2_active_first_double_nul_to_08xx"
        else:
            priority = "P3_quarantine_continuation"
        exact_rows.append({**row, "rendered_text": text, "priority": priority, "native_pair_solutions": sols[:32], "native_pair_solvable": bool(sols)})

    cont_rows = []
    for row in control18:
        body = bytes.fromhex(row["candidate_body_hex"])
        text = dictionary.expand(body[1:], tbl) if body[:1] == b"\x18" else dictionary.expand(body, tbl)
        sols = native_pair_solutions(dictionary, tbl, text, rendered_map)
        cont_rows.append({**row, "rendered_text": text, "priority": "P0_control18_review_only", "native_pair_solutions": sols[:32], "native_pair_solvable": bool(sols)})

    counts = Counter(r["priority"] for r in exact_rows)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_global_event_runtime_risk_worklist.py",
        "policy": {
            "runtime_gate": "STAGE22t E51D v3 is user-runtime-confirmed and promoted. Continue only as small stage/bundle candidates; never bulk-write the full worklist.",
            "P0": "review caller/history first; 624305 and 6335A6 were already false target hypotheses",
            "P1": "active staged recovery class; prefer exact Original-body restoration when current dictionary already renders the same Korean, then other existing-native-pair solutions; require exact boundary/control preservation and user runtime validation per batch",
            "P2": "second batch; inspect nearby 08xx->17xx control chain before candidate build",
            "P3": "quarantine continuation; no automatic write without caller evidence",
            "unsolved": "leave direct ext3 unchanged unless runtime evidence justifies a sparse portal helper",
        },
        "counts": {
            "exact4_total": len(exact_rows),
            "exact4_native_pair_solvable": sum(r["native_pair_solvable"] for r in exact_rows),
            "exact4_native_pair_unsolved": sum(not r["native_pair_solvable"] for r in exact_rows),
            **dict(counts),
            "control18_total": len(cont_rows),
            "control18_native_pair_solvable": sum(r["native_pair_solvable"] for r in cont_rows),
        },
        "bank_distribution": dict(Counter(r["address"][:2] for r in exact_rows)),
        "exact4": exact_rows,
        "control18": cont_rows,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"counts": report["counts"], "bank_distribution": report["bank_distribution"], "out": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
