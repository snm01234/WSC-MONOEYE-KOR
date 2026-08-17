#!/usr/bin/env python3
"""Promote the user-runtime-approved 220-record global event native rehome candidate.

Fail-closed promotion:
- verify current main and candidate identities;
- verify candidate structural/battle/terminology reports are green;
- verify the two user-runtime representative gates are recorded by this promotion;
- preserve live SaveRAM byte-exact;
- backup the current main ROM;
- replace ROM only;
- emit a promotion report.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "global_event_native_rehome_220_candidate.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
STRUCT_AUDIT = PATCH / "global_event_native_rehome_220_audit.json"
BATTLE_AUDIT = PATCH / "global_event_native_rehome_220_battle_audit.json"
TERM_AUDIT = PATCH / "global_event_native_rehome_220_terminology_audit.json"
RUNTIME_CONTRACTS = PATCH / "global_event_native_rehome_220_runtime_contracts.json"
BUILD_REPORT = PATCH / "global_event_native_rehome_220_report.json"
REPORT = PATCH / "global_event_native_rehome_220_promotion_report.json"

MAIN_SHA = "fbd7ad5f36d1248aab27b9a3a1e90b4ef2ec0676567b6bb42b76979e3c9b3260"
CANDIDATE_SHA = "714200ffdcad34d01c12c8f560b8ca71163c165803e5e9894feb30f523e166c6"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise PromotionError(f"missing required report: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def terminology_zero(report: dict) -> bool:
    if report.get("status") not in (None, "clean"):
        return False
    counts = report.get("counts") or {}
    for key in ("active_source_hits", "dictionary_hits", "five_bank_dictionary_hits", "rendered_record_hits"):
        if key in counts and int(counts[key]) != 0:
            return False
        if key in report:
            value = report[key]
            n = len(value) if isinstance(value, list) else int(value)
            if n != 0:
                return False
    return True


def main() -> int:
    main_before = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = LIVE_SAVE.read_bytes()

    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise PromotionError("current main TIP identity drifted")
    if len(candidate) != ROM_SIZE or sha(candidate) != CANDIDATE_SHA:
        raise PromotionError("220 candidate identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise PromotionError("live SaveRAM missing or wrong size")

    build = load_json(BUILD_REPORT)
    counts = build.get("counts") or {}
    expected_build = {
        "targets": 220,
        "direct_native_pair": 155,
        "event_safe_parameterized": 65,
        "unique_nested_ext3_helpers": 58,
    }
    for key, expected in expected_build.items():
        if int(counts.get(key, -1)) != expected:
            raise PromotionError(f"build report mismatch: {key}={counts.get(key)!r}")

    audit = load_json(STRUCT_AUDIT)
    ac = audit.get("counts") or {}
    if audit.get("ok") is not True:
        raise PromotionError("220 structural audit not green")
    expected_audit = {
        "targets": 220,
        "direct_native_pair": 155,
        "event_safe_parameterized": 65,
        "top_level_exact4_risk_remaining": 0,
        "render_failures": 0,
        "event_bank_unknown_diff": 0,
        "failures": 0,
    }
    for key, expected in expected_audit.items():
        if int(ac.get(key, -1)) != expected:
            raise PromotionError(f"structural audit mismatch: {key}={ac.get(key)!r}")
    if audit.get("stage22_fixed_portal") != "PASS":
        raise PromotionError("STAGE22 fixed portal static gate not PASS")

    battle = load_json(BATTLE_AUDIT)
    if battle.get("ok") is not True or int((battle.get("counts") or {}).get("failures", -1)) != 0:
        raise PromotionError("battle regression audit not green")

    term = load_json(TERM_AUDIT)
    if not terminology_zero(term):
        raise PromotionError("terminology audit not clean")

    contracts = load_json(RUNTIME_CONTRACTS)
    by_addr = {row.get("address"): row for row in contracts.get("contracts", [])}
    # Runtime-proven original fixed portal remains locked.
    stage22 = by_addr.get("638CD5") or {}
    if not (
        stage22.get("status") == "active"
        and stage22.get("confidence") == "runtime-proven"
        and (stage22.get("decoder") or {}).get("ext3") is False
        and stage22.get("baseline_body_hex") == "F191E51D"
    ):
        raise PromotionError(f"638CD5 contract drifted: {stage22}")
    # Representative parameterized route confirmed by user at Gato scene.
    gato = by_addr.get("61035E") or {}
    if not (
        gato.get("status") == "active"
        and gato.get("route") == "scenario_first"
        and (gato.get("decoder") or {}).get("ext3") is False
        and str(gato.get("baseline_body_hex") or "").startswith("E51D")
        and len(str(gato.get("baseline_body_hex") or "")) == 8
    ):
        raise PromotionError(f"61035E parameterized contract drifted: {gato}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_global_event_native_rehome_220"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / MAIN.name
    backup.write_bytes(main_before)
    if sha(backup.read_bytes()) != MAIN_SHA:
        raise PromotionError("backup verification failed")

    # ROM-only promotion. Never copy candidate SaveRAM onto the live SaveRAM.
    shutil.copyfile(CANDIDATE, MAIN)
    main_after = MAIN.read_bytes()
    save_after = LIVE_SAVE.read_bytes()
    if len(main_after) != ROM_SIZE or sha(main_after) != CANDIDATE_SHA:
        MAIN.write_bytes(main_before)
        raise PromotionError("promoted main verification failed; main restored")
    if save_after != save_before:
        MAIN.write_bytes(main_before)
        raise PromotionError("live SaveRAM changed during promotion; main restored")

    result = {
        "ok": True,
        "status": "promoted",
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "user_runtime_evidence": {
            "parameterized_E51D_Gato_61035E_area": "PASS",
            "stage22t_Uso_Katejina_638CD5_area": "PASS",
            "note": "User judged these representative runtime gates sufficient for promotion; remaining matrix rows were not individually required.",
        },
        "before": {
            "main_sha256": MAIN_SHA,
            "live_save_sha256": sha(save_before),
        },
        "after": {
            "main_sha256": CANDIDATE_SHA,
            "live_save_sha256": sha(save_after),
        },
        "candidate": str(CANDIDATE.relative_to(ROOT)).replace("\\", "/"),
        "backup": str(backup.relative_to(ROOT)).replace("\\", "/"),
        "gates": {
            "targets": 220,
            "direct_native_pair": 155,
            "event_safe_parameterized": 65,
            "top_level_exact4_risk_remaining": 0,
            "render_failures": 0,
            "event_bank_unknown_diff": 0,
            "battle_failures": 0,
            "terminology_clean": True,
            "stage22_fixed_portal": "PASS",
            "runtime_contract_638CD5": "runtime-proven/F191E51D/ext3-false",
            "runtime_contract_61035E": f"{gato.get('baseline_body_hex')}/ext3-false",
        },
    }
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
