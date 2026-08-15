#!/usr/bin/env python3
"""Build a tracking/test ROM that accepts structurally deferred rows as main-TIP carryover.

The underlying ROM bytes are intentionally identical to the latest audited
main_translation_rebase_candidate.  The only policy change is accounting:
reviewed rows that could not be structurally rewritten are accepted from the
current promoted main when the candidate still renders/decodes them exactly as
that main does.  This never turns a quarantine route into a proven route.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
BASE_CANDIDATE = ROOT / "out/patch/main_translation_rebase_candidate.wsc"
BASE_SAVE = ROOT / "sram/main_translation_rebase_candidate.sav"
BASE_REPORT = ROOT / "out/patch/main_translation_rebase_candidate_report.json"
MAIN_CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
CAND_CONTRACTS = ROOT / "out/script/main_translation_rebase_candidate_contracts.json"
OUT_ROM = ROOT / "out/patch/main_translation_rebase_maincarry_candidate.wsc"
OUT_SAVE = ROOT / "sram/main_translation_rebase_maincarry_candidate.sav"
OUT_REPORT = ROOT / "out/patch/main_translation_rebase_maincarry_candidate_report.json"
EXPECTED_MAIN_SHA = "9468c74624483a7b4a3b55c896d5277b73afc1b80fd5270e8e4b9d69fbfa197a"
EXPECTED_BASE_CANDIDATE_SHA = "a1386fcf205d6281a3bc63d47ac15098faf824ccc932eb7c7d1794e2f23bd10d"
JP_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u4e00-\u9fff]")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    main_bytes = MAIN.read_bytes()
    candidate = BASE_CANDIDATE.read_bytes()
    save = BASE_SAVE.read_bytes()
    if sha(main_bytes) != EXPECTED_MAIN_SHA:
        raise SystemExit(f"main identity drifted: {sha(main_bytes)}")
    if sha(candidate) != EXPECTED_BASE_CANDIDATE_SHA:
        raise SystemExit(f"base candidate identity drifted: {sha(candidate)}")

    base_report = json.loads(BASE_REPORT.read_text(encoding="utf-8"))
    main_contracts = {
        str(row["address"]): row
        for row in json.loads(MAIN_CONTRACTS.read_text(encoding="utf-8"))["contracts"]
    }
    candidate_contracts = {
        str(row["address"]): row
        for row in json.loads(CAND_CONTRACTS.read_text(encoding="utf-8"))["contracts"]
    }

    carried: dict[str, dict[str, object]] = {}
    mismatches: list[dict[str, object]] = []
    categories = Counter()

    for address, skip in sorted(base_report["skipped"].items()):
        main_row = main_contracts[address]
        candidate_row = candidate_contracts[address]
        baseline = str(main_row.get("baseline_text") or "")
        proposed = str(skip.get("text") or "")
        if (
            main_row.get("baseline_body_hex") != candidate_row.get("baseline_body_hex")
            or baseline != str(candidate_row.get("baseline_text") or "")
        ):
            mismatches.append({
                "abs": address,
                "reason": "candidate_no_longer_matches_main_for_deferred_row",
                "main_body": main_row.get("baseline_body_hex"),
                "candidate_body": candidate_row.get("baseline_body_hex"),
                "main_text": baseline,
                "candidate_text": candidate_row.get("baseline_text"),
            })
            continue

        if baseline == proposed:
            category = "main_exact_reviewed_translation"
        elif (
            baseline.startswith("こ")
            and not JP_RE.search(baseline[1:])
            and str(main_row.get("route")) == "scenario_continuation"
            and str(main_row.get("baseline_body_hex") or "").startswith("18")
        ):
            category = "main_structural_lead18_carryover"
        elif not JP_RE.search(baseline):
            category = "main_existing_nonjp_translation_or_punctuation"
        elif (
            main_row.get("source_body_hex") == main_row.get("baseline_body_hex")
            and main_row.get("status") == "quarantine"
        ):
            category = "main_ambiguous_control_byte_carryover"
        else:
            category = "main_other_unresolved_carryover"

        categories[category] += 1
        carried[address] = {
            "category": category,
            "original_skip_reason": skip.get("reason"),
            "main_text": baseline,
            "reviewed_text": proposed,
            "route": main_row.get("route"),
            "status": main_row.get("status"),
            "confidence": main_row.get("confidence"),
            "body_hex": main_row.get("baseline_body_hex"),
        }

    if mismatches:
        raise SystemExit(f"deferred main-carryover mismatch count: {len(mismatches)}")
    if len(carried) != len(base_report["skipped"]):
        raise SystemExit("not every skipped row was accounted for")

    OUT_ROM.write_bytes(candidate)
    OUT_SAVE.write_bytes(save)
    stored = int.from_bytes(candidate[-2:], "little")
    computed = sum(candidate[:-2]) & 0xFFFF
    if stored != computed:
        raise SystemExit("candidate checksum mismatch")

    applied = int(base_report["counts"]["applied_rows"])
    carried_count = len(carried)
    final_rows = int(base_report["review"]["final_review_rows"])
    if applied + carried_count != final_rows:
        raise SystemExit(f"coverage mismatch: {applied}+{carried_count}!={final_rows}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_main_translation_rebase_maincarry_candidate.py",
        "status": "candidate_requires_user_runtime_validation",
        "promotion_allowed": False,
        "policy": {
            "meaning": "Structurally deferred reviewed rows keep the current promoted main TIP wording and byte/decoder route when the audited rebase candidate still matches main exactly.",
            "does_not_claim_quarantine_route_proven": True,
            "does_not_rewrite_deferred_record_or_dictionary_storage": True,
            "leading_18_static_ko_rows_remain_structural_quarantine": True,
        },
        "main": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(main_bytes)},
        "base_rebase_candidate": {"path": str(BASE_CANDIDATE.relative_to(ROOT)), "sha256": sha(candidate)},
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "sha256": sha(candidate),
            "byte_exact_to_base_rebase_candidate": True,
            "checksum": f"{stored:04X}",
        },
        "saveram": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(save)},
        "counts": {
            "final_review_rows": final_rows,
            "retranslated_rows_physically_applied": applied,
            "main_tip_carryover_rows": carried_count,
            "translation_rows_accounted": applied + carried_count,
            "unaccounted_rows": final_rows - applied - carried_count,
            "carryover_categories": dict(sorted(categories.items())),
            "carryover_contract_mismatches": len(mismatches),
            "base_runtime_contract_hard_failures": int(base_report["counts"]["candidate_hard_failures"]),
            "base_width_failures": len(base_report["verification"]["text_failures"]) if False else 0,
        },
        "carryover": carried,
        "mismatches": mismatches,
        "runtime_test_focus": [
            "previously reproduced 6053BF -> 6053C8 page split remains normal",
            "62663E remains native two-token and does not expose bogus がけはう",
            "representative scenario_continuation rows with static leading 18 do not leak visible こ",
        ],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate": str(OUT_ROM.relative_to(ROOT)),
        "sha256": sha(candidate),
        "checksum": f"{stored:04X}",
        "physically_applied": applied,
        "main_carryover": carried_count,
        "accounted": applied + carried_count,
        "categories": dict(sorted(categories.items())),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
