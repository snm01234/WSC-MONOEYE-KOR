#!/usr/bin/env python3
"""Independent accounting audit for the main-carryover translation test ROM."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
BASE = ROOT / "out/patch/main_translation_rebase_candidate.wsc"
BASE_AUDIT = ROOT / "out/patch/main_translation_rebase_candidate_audit.json"
BASE_REPORT = ROOT / "out/patch/main_translation_rebase_candidate_report.json"
MAIN_CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
CAND_CONTRACTS = ROOT / "out/script/main_translation_rebase_candidate_contracts.json"
ROM = ROOT / "out/patch/main_translation_rebase_maincarry_candidate.wsc"
SAVE = ROOT / "sram/main_translation_rebase_maincarry_candidate.sav"
BASE_SAVE = ROOT / "sram/main_translation_rebase_candidate.sav"
REPORT = ROOT / "out/patch/main_translation_rebase_maincarry_candidate_report.json"
OUT = ROOT / "out/patch/main_translation_rebase_maincarry_candidate_audit.json"
EXPECTED_MAIN_SHA = "9468c74624483a7b4a3b55c896d5277b73afc1b80fd5270e8e4b9d69fbfa197a"
EXPECTED_CANDIDATE_SHA = "a1386fcf205d6281a3bc63d47ac15098faf824ccc932eb7c7d1794e2f23bd10d"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    failures: list[dict[str, object]] = []
    main_bytes = MAIN.read_bytes()
    base = BASE.read_bytes()
    rom = ROM.read_bytes()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    base_report = json.loads(BASE_REPORT.read_text(encoding="utf-8"))
    base_audit = json.loads(BASE_AUDIT.read_text(encoding="utf-8"))
    main_contracts = {
        str(row["address"]): row
        for row in json.loads(MAIN_CONTRACTS.read_text(encoding="utf-8"))["contracts"]
    }
    candidate_contracts = {
        str(row["address"]): row
        for row in json.loads(CAND_CONTRACTS.read_text(encoding="utf-8"))["contracts"]
    }

    if sha(main_bytes) != EXPECTED_MAIN_SHA:
        failures.append({"kind": "main_identity", "actual": sha(main_bytes)})
    if sha(base) != EXPECTED_CANDIDATE_SHA or sha(rom) != EXPECTED_CANDIDATE_SHA:
        failures.append({"kind": "candidate_identity", "base": sha(base), "maincarry": sha(rom)})
    if rom != base:
        failures.append({"kind": "maincarry_not_byte_exact_to_base_candidate"})
    if SAVE.read_bytes() != BASE_SAVE.read_bytes():
        failures.append({"kind": "saveram_not_exact_to_base_candidate"})
    if not base_audit.get("ok") or base_audit.get("candidate_sha256") != EXPECTED_CANDIDATE_SHA:
        failures.append({"kind": "base_candidate_audit_not_green"})

    carried = report.get("carryover") or {}
    skipped = base_report.get("skipped") or {}
    applied = base_report.get("applied") or {}
    final_rows = int(base_report["review"]["final_review_rows"])
    if set(carried) != set(skipped):
        failures.append({
            "kind": "carryover_population_mismatch",
            "missing": sorted(set(skipped) - set(carried))[:20],
            "extra": sorted(set(carried) - set(skipped))[:20],
        })
    if len(applied) + len(carried) != final_rows:
        failures.append({
            "kind": "coverage_mismatch",
            "applied": len(applied),
            "carried": len(carried),
            "final": final_rows,
        })

    contract_mismatches = []
    for address in carried:
        m = main_contracts[address]
        c = candidate_contracts[address]
        if m.get("baseline_body_hex") != c.get("baseline_body_hex") or m.get("baseline_text") != c.get("baseline_text"):
            contract_mismatches.append(address)
    if contract_mismatches:
        failures.append({"kind": "carryover_contract_drift", "count": len(contract_mismatches), "sample": contract_mismatches[:20]})

    stored = int.from_bytes(rom[-2:], "little")
    computed = sum(rom[:-2]) & 0xFFFF
    if stored != computed:
        failures.append({"kind": "checksum", "stored": stored, "computed": computed})

    payload = {
        "schema_version": 1,
        "generated_by": "tools/audit_main_translation_rebase_maincarry_candidate.py",
        "ok": not failures,
        "main_sha256": sha(main_bytes),
        "candidate_sha256": sha(rom),
        "byte_exact_to_base_rebase_candidate": rom == base,
        "saveram_exact_to_base_rebase_candidate": SAVE.read_bytes() == BASE_SAVE.read_bytes(),
        "base_candidate_audit_ok": bool(base_audit.get("ok")),
        "checksum": {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "ok": stored == computed},
        "physically_retranslated_rows": len(applied),
        "main_carryover_rows": len(carried),
        "final_review_rows_accounted": len(applied) + len(carried),
        "carryover_contract_mismatches": len(contract_mismatches),
        "failures": failures,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
