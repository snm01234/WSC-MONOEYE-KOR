#!/usr/bin/env python3
"""Finalize the exhaustive 5D/5E restored-lead classification.

The previous structure repair restored the first original code unit on 3,821
battle-voice records. Runtime screenshots proved that some of those code units
are visible sentence text, not speaker/portrait metadata. This analysis is
read-only for ROM/SaveRAM and divides every repaired row into:

* safe_text_lead: may be removed in the production candidate;
* protected_control: prior runtime/manual proof says the byte is real metadata;
* unresolved_one_byte: structurally ambiguous, never auto-written;
* non_japanese_lead: not part of the reported Japanese-lead population.

A key format fact is enforced: every previously proven real speaker/control ID
in this population is exactly one byte. Therefore a Japanese-rendering
multi-byte first code unit cannot be one of those IDs and is safe text. One-byte
text rows need independent runtime/duplicate/local-structure review and are
listed explicitly below.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LEAK = ROOT / "out/script/battle_dialogue_restored_lead_leakage_candidates.csv"
INVENTORY = ROOT / "out/script/battle_dialogue_structure_inventory.csv"
OUT_SAFE = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
OUT_AMBIG = ROOT / "out/script/battle_dialogue_false_lead_ambiguous.csv"
OUT_REPORT = ROOT / "out/patch/battle_dialogue_false_lead_structure_analysis.json"
EXPECTED_TIP = "bac5e179ae496dd2b70912da0b1987b2dc6f7551e9f4d9de2d48c8c2152f7c88"
EXPECTED_REPAIRED = 3821
EXPECTED_MULTIBYTE_SAFE = 201
EXPECTED_ONE_BYTE_SAFE = 63
EXPECTED_PROTECTED = 277

SCREEN_PROVEN = {
    "5D0C39", "5D11C6", "5D1449", "5D5D58", "5EBB7A",
}
DUPLICATE_PROVEN = {"5DA754", "5E5744", "5E576E"}

# Exhaustively reviewed one-byte rows. Each row is outside the prior proven
# control set and has sentence-boundary evidence from the original text plus
# same-speaker/local structure; the three duplicate rows additionally have a
# clean full-text peer. Obvious control concatenations such as ツ+ターゲット,
# キ+ミリアルド, バ+よーし, etc. are deliberately absent.
REVIEWED_ONE_BYTE_TEXT = set("""
5D01C8 5D0901 5D0AB2 5D0ACA 5D0C39 5D11C6 5D1449 5D151C 5D266A
5D3122 5D313B 5D3F4D 5D408A 5D48C6 5D5E2F 5D61D8 5D6841 5D6A3C
5D6AA0 5D71BC 5D78BC 5D7A9E 5D7E7C 5D85B7 5D895C 5D8CC6 5D9B79
5DA754 5DB6DE 5DB8F0 5DC275 5E1774 5E1810 5E209D 5E24B7 5E26FA
5E291D 5E323E 5E3D91 5E3DA4 5E3DFB 5E3E11 5E3E62 5E47C2 5E4F5F
5E5744 5E576E 5E5EBA 5E5EDF 5E5FE1 5E63D1 5E94A3 5E95A8 5E9B9A
5E9DCC 5E9DE0 5EA4AC 5EA731 5EAD0D 5EAF37 5EB554 5EBB7A 5EBDF2
""".split())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def main() -> int:
    if sha(TIP) != EXPECTED_TIP:
        raise SystemExit("main TIP identity drifted")
    with INVENTORY.open(encoding="utf-8-sig", newline="") as handle:
        inventory = list(csv.DictReader(handle))
    repaired = [row for row in inventory if row.get("action") == "repair"]
    if len(repaired) != EXPECTED_REPAIRED:
        raise SystemExit(f"repaired population drifted: {len(repaired)}")
    with LEAK.open(encoding="utf-8-sig", newline="") as handle:
        leak = list(csv.DictReader(handle))
    by_abs = {row["abs"]: row for row in leak}

    protected = {
        row["abs"] for row in leak
        if row["classification"] in {"proven_control_metadata", "manual_reviewed_control_metadata"}
    }
    if len(protected) != EXPECTED_PROTECTED:
        raise SystemExit(f"protected control population drifted: {len(protected)}")
    protected_multibyte = [a for a in protected if len(bytes.fromhex(by_abs[a]["lead_hex"])) != 1]
    if protected_multibyte:
        raise SystemExit("known real control unexpectedly uses multiple bytes: " + ",".join(protected_multibyte))

    multibyte = {
        row["abs"] for row in leak
        if len(bytes.fromhex(row["lead_hex"])) > 1
        and row["classification"] in {
            "high_confidence_multibyte_visible_text_lead",
            "runtime_screen_proven_visible_text",
        }
    }
    if len(multibyte) != EXPECTED_MULTIBYTE_SAFE:
        raise SystemExit(f"multi-byte safe population drifted: {len(multibyte)}")
    if len(REVIEWED_ONE_BYTE_TEXT) != EXPECTED_ONE_BYTE_SAFE:
        raise SystemExit(f"reviewed one-byte set drifted: {len(REVIEWED_ONE_BYTE_TEXT)}")
    if (multibyte | REVIEWED_ONE_BYTE_TEXT) & protected:
        raise SystemExit("safe target overlaps protected controls")
    missing = sorted(REVIEWED_ONE_BYTE_TEXT - set(by_abs))
    if missing:
        raise SystemExit("reviewed address missing from leakage inventory: " + ",".join(missing))
    for address in REVIEWED_ONE_BYTE_TEXT:
        if len(bytes.fromhex(by_abs[address]["lead_hex"])) != 1:
            raise SystemExit(f"reviewed one-byte target changed width: {address}")

    safe_ids = multibyte | REVIEWED_ONE_BYTE_TEXT
    if not SCREEN_PROVEN <= safe_ids:
        raise SystemExit("runtime screen anchor missing from safe set")
    if not DUPLICATE_PROVEN <= safe_ids:
        raise SystemExit("duplicate proof missing from safe set")

    safe_rows: list[dict[str, Any]] = []
    ambiguous_rows: list[dict[str, Any]] = []
    for row in leak:
        address = row["abs"]
        if address in safe_ids:
            if address in SCREEN_PROVEN:
                evidence = "runtime_screen_proven"
            elif address in DUPLICATE_PROVEN:
                evidence = "clean_full_text_duplicate_plus_structure"
            elif address in multibyte:
                evidence = "multibyte_text_codeunit_no_known_control_width_match"
            else:
                evidence = "reviewed_one_byte_sentence_boundary_plus_local_structure"
            out = dict(row)
            out["final_disposition"] = "safe_text_lead"
            out["evidence"] = evidence
            safe_rows.append(out)
        elif address in protected:
            out = dict(row)
            out["final_disposition"] = "protected_control"
            out["evidence"] = row["classification"]
            ambiguous_rows.append(out)
        else:
            out = dict(row)
            out["final_disposition"] = "unresolved_one_byte"
            out["evidence"] = "insufficient independent proof; no auto-write"
            ambiguous_rows.append(out)

    leak_abs = set(by_abs)
    non_japanese = [row for row in repaired if row["record_start"] not in leak_abs]
    if len(safe_rows) != EXPECTED_MULTIBYTE_SAFE + EXPECTED_ONE_BYTE_SAFE:
        raise SystemExit(f"safe population drifted: {len(safe_rows)}")
    if len(safe_rows) + len(ambiguous_rows) + len(non_japanese) != EXPECTED_REPAIRED:
        raise SystemExit("final partition does not cover every repaired record")

    fields = list(leak[0].keys()) + ["final_disposition", "evidence"]
    write_csv(OUT_SAFE, safe_rows, fields)
    write_csv(OUT_AMBIG, ambiguous_rows, fields)

    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_battle_dialogue_false_lead_structure.py",
        "read_only_rom": True,
        "ok": True,
        "tip_sha256": sha(TIP),
        "format_facts": {
            "previously_proven_real_control_rows": len(protected),
            "proven_real_control_width_bytes": [1],
            "multi_byte_text_leads_safe": len(multibyte),
            "reviewed_one_byte_text_leads_safe": len(REVIEWED_ONE_BYTE_TEXT),
        },
        "counts": {
            "previous_repair_records": len(repaired),
            "safe_text_lead": len(safe_rows),
            "safe_multibyte": len(multibyte),
            "safe_one_byte": len(REVIEWED_ONE_BYTE_TEXT),
            "protected_control": len(protected),
            "unresolved_one_byte": len(ambiguous_rows) - len(protected),
            "non_japanese_lead": len(non_japanese),
        },
        "runtime_screen_anchors": sorted(SCREEN_PROVEN),
        "duplicate_proven": sorted(DUPLICATE_PROVEN),
        "outputs": {
            "safe_targets_csv": str(OUT_SAFE.relative_to(ROOT)).replace("\\", "/"),
            "ambiguous_csv": str(OUT_AMBIG.relative_to(ROOT)).replace("\\", "/"),
        },
        "write_gate": "only safe_text_lead rows may be changed; protected and unresolved rows are byte-exact guards",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
