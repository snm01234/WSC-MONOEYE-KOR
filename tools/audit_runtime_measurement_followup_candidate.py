#!/usr/bin/env python3
"""Independent acceptance audit for runtime_measurement_followup_candidate.wsc.

This combines the independent reports used as promotion gates and directly
checks the two UI/name-table families that the generic 20-cell dialogue audit
does not cover (GP03 呐喊/돌격 and キャラ・ス－ン/캐라・슨).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/runtime_measurement_followup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/runtime_measurement_followup_candidate.sav"
REPORT = ROOT / "out/patch/runtime_measurement_followup_report.json"
WIDTH = ROOT / "out/patch/runtime_measurement_followup_width_audit.json"
LEADS = ROOT / "out/patch/runtime_measurement_followup_false_lead_audit.json"
SEGPTR = ROOT / "out/patch/runtime_measurement_followup_false_segptr.json"
TERMS = ROOT / "out/patch/runtime_measurement_followup_gundam_terminology_audit.json"
SPEC = ROOT / "data/runtime_measurement_followup_ko.json"
NAME_BASE = ROOT / "data/name75_base_ko.json"
PART_BASE = ROOT / "data/name_part_residual_ko.json"
UNIT_NAMES = ROOT / "data/unit_names_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/runtime_measurement_followup_acceptance_audit.json"
EXPECTED_MAIN_SHA = "48320a9336346bf6c6b230b7199426197a7a6321a16d4caed9989aa29c6d9c13"
EXPECTED_CANDIDATE_SHA = "8a53737d209ff695fdcd78c0f46f9e61eff9a15d8c4f01b0f387e8dd05488af2"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def main() -> int:
    failures: list[str] = []
    main = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    if sha(main) != EXPECTED_MAIN_SHA:
        failures.append(f"main_sha:{sha(main)}")
    if sha(candidate) != EXPECTED_CANDIDATE_SHA:
        failures.append(f"candidate_sha:{sha(candidate)}")
    if CANDIDATE_SAVE.read_bytes() != MAIN_SAVE.read_bytes():
        failures.append("candidate_save_not_byte_exact_live_main")

    build = json.loads(REPORT.read_text(encoding="utf-8"))
    width = json.loads(WIDTH.read_text(encoding="utf-8"))
    leads = json.loads(LEADS.read_text(encoding="utf-8"))
    segptr = json.loads(SEGPTR.read_text(encoding="utf-8"))
    terms = json.loads(TERMS.read_text(encoding="utf-8"))
    spec = json.loads(SPEC.read_text(encoding="utf-8"))

    counts = build.get("counts") or {}
    expected_counts = {
        "records": 253,
        "battle_width_records": 124,
        "battle_width_unique_sources": 101,
        "duplicate_lead_ledger_records": 70,
        "duplicate_lead_reintroduced_repaired": 64,
        "judau_name_records": 50,
        "chara_name_records": 7,
        "compact3_records": 1,
        "stock_dictionary_exact_rewrites": 1,
        "terminator_changes": 0,
        "unexpected_diff_offsets": 0,
    }
    for key, value in expected_counts.items():
        if int(counts.get(key, -1)) != value:
            failures.append(f"build_count:{key}={counts.get(key)!r}!={value}")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        failures.append("build_report_candidate_sha")
    target_rows = list(build.get("targets") or [])
    if any(row.get("rendered_after") != row.get("desired_norm") for row in target_rows):
        failures.append("build_report_render_mismatch")

    if not width.get("ok") or not width.get("width_ok") or not width.get("terminology_ok"):
        failures.append("width_audit_not_ok")
    if width.get("offenders"):
        failures.append(f"width_offenders:{len(width['offenders'])}")
    if width.get("terminology_residuals"):
        failures.append(f"terminology_residuals:{len(width['terminology_residuals'])}")
    pop = width.get("population") or {}
    if int(pop.get("offender_records", -1)) != 0 or int(pop.get("max_line_cells", -1)) != 20:
        failures.append(f"width_population:{pop}")
    battle_pop = (pop.get("by_scope") or {}).get("battle_voice") or {}
    if int(battle_pop.get("records", -1)) != 9783 or int(battle_pop.get("over_20_records", -1)) != 0:
        failures.append(f"battle_width_population:{battle_pop}")

    lead_counts = leads.get("counts") or {}
    if not leads.get("ok") or int(lead_counts.get("total_guarded_leads", -1)) != 335 or int(lead_counts.get("reintroduced", -1)) != 0:
        failures.append(f"visible_lead_guard:{lead_counts}")
    if not segptr.get("ok") or int(segptr.get("sites_found", -1)) != 0:
        failures.append(f"false_segptr:{segptr.get('sites_found')}")
    term_counts = terms.get("counts") or {}
    if terms.get("status") != "clean" or any(int(term_counts.get(k, -1)) != 0 for k in ("active_source_hits", "dictionary_hits", "rendered_record_hits")):
        failures.append(f"gundam_terminology:{term_counts}")

    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        candidate,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    if dictionary.expand_index(0x006F, tbl) != "돌격":
        failures.append(f"weapon_006F:{dictionary.expand_index(0x006F, tbl)!r}")

    sb = stock_base(candidate)
    name_checks = {"75D882": "캐라・슨", "75D8AC": "캐라・슨이다！"}
    rendered_names: dict[str, str] = {}
    for address, expected in name_checks.items():
        got = read_encoded_z_safe(candidate, sb + int(address, 16), max_len=64)
        if got is None:
            failures.append(f"name_unreadable:{address}")
            continue
        rendered = strip_pad(dictionary.expand(bytes(got[0]), tbl))
        rendered_names[address] = rendered
        if rendered != expected:
            failures.append(f"name_render:{address}:{rendered!r}")

    # Active canonical source mirrors must agree with the candidate so a later
    # rebuild cannot silently revive the old forms before this final gate runs.
    name_base = json.loads(NAME_BASE.read_text(encoding="utf-8"))
    name_bases = name_base.get("bases") or {}
    if name_bases.get("キャラ・ス－ン") != "캐라・슨" or name_bases.get("キャラ・ス－ンだ！") != "캐라・슨이다！":
        failures.append("name75_base_source_not_synchronized")
    part_base = json.loads(PART_BASE.read_text(encoding="utf-8"))
    part_rows = list(part_base.get("part76_local") or []) + list(part_base.get("shared_slots") or []) + list(part_base.get("name75_local") or [])
    # name_part_residual_ko.json is a flat object with target arrays in current
    # revisions; fall back to a recursive string check if the exact row shape changes.
    text_part = PART_BASE.read_text(encoding="utf-8")
    if '"index": "006F", "jp": "呐喊", "ko": "돌격"' not in text_part:
        failures.append("name_part_006F_source_not_synchronized")
    unit_doc = json.loads(UNIT_NAMES.read_text(encoding="utf-8"))
    unit_text = json.dumps(unit_doc, ensure_ascii=False)
    if '"jp": "キャラ", "ko": "캐라", "cat": "pilot"' not in unit_text:
        # JSON pretty formatting makes raw substring brittle; inspect objects too.
        found = False
        def walk(obj: Any) -> None:
            nonlocal found
            if isinstance(obj, dict):
                if obj.get("jp") == "キャラ" and obj.get("cat") == "pilot" and obj.get("ko") == "캐라":
                    found = True
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)
        walk(unit_doc)
        if not found:
            failures.append("unit_name_chara_source_not_synchronized")

    result = {
        "schema_version": 1,
        "generated_by": "tools/audit_runtime_measurement_followup_candidate.py",
        "ok": not failures,
        "main_sha256": sha(main),
        "candidate_sha256": sha(candidate),
        "candidate_save_sha256": sha(CANDIDATE_SAVE.read_bytes()),
        "counts": expected_counts,
        "width_population": pop,
        "visible_lead_counts": lead_counts,
        "weapon_006F": dictionary.expand_index(0x006F, tbl),
        "name75": rendered_names,
        "failures": failures,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
