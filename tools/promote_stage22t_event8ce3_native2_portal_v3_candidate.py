#!/usr/bin/env python3
"""Promote the user-runtime-approved STAGE22t event8CE3 v3 candidate.

Fail-closed promotion:
- verify current main and candidate identities;
- verify v3 global/battle/terminology reports are green for promotion blockers;
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
CANDIDATE = PATCH / "stage22t_uso_katejina_event8ce3_native2_portal_v3_candidate.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
GLOBAL_AUDIT = PATCH / "global_event_runtime_risk_v3.json"
BATTLE_AUDIT = PATCH / "stage22t_uso_katejina_event8ce3_native2_portal_v3_battle_audit.json"
TERM_AUDIT = PATCH / "stage22t_uso_katejina_event8ce3_native2_portal_v3_terminology_audit.json"
RUNTIME_CONTRACTS = PATCH / "stage22t_v3_runtime_contracts.json"
REPORT = PATCH / "stage22t_uso_katejina_event8ce3_native2_portal_v3_promotion_report.json"

MAIN_SHA = "f68b3261beecc32047d17952e36bc2b891cd5d66410f9fc9293487571a0fc8e2"
CANDIDATE_SHA = "fbd7ad5f36d1248aab27b9a3a1e90b4ef2ec0676567b6bb42b76979e3c9b3260"
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


def main() -> int:
    main_before = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = LIVE_SAVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise PromotionError("current main TIP identity drifted")
    if len(candidate) != ROM_SIZE or sha(candidate) != CANDIDATE_SHA:
        raise PromotionError("v3 candidate identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise PromotionError("live SaveRAM missing or wrong size")

    ga = load_json(GLOBAL_AUDIT)
    counts = ga.get("counts") or {}
    blocker = ga.get("promotion_blockers_for_current_v3") or {}
    usage = (ga.get("safer_2byte_portal_pool") or {}).get("current_E51D_semantic_usage_on_parent") or {}
    if counts.get("terminator_drift") != 0:
        raise PromotionError("global audit: terminator drift present")
    if counts.get("unsafe_ext3_zero_middle") != 0:
        raise PromotionError("global audit: unsafe ext3 middle-NUL present")
    if counts.get("event_bank_unknown_diff_runs") != 0:
        raise PromotionError("global audit: unknown event-bank diff present")
    if blocker.get("E51D_nested_dictionary_collision") is not False:
        raise PromotionError("global audit: E51D collision blocker present")
    if any(int(usage.get(k, -1)) != 0 for k in ("script", "aux", "name75", "native_dictionary", "ext3_phrase")):
        raise PromotionError(f"global audit: E51D semantic ownership is not zero: {usage}")

    ba = load_json(BATTLE_AUDIT)
    if ba.get("ok") is not True or int((ba.get("counts") or {}).get("failures", -1)) != 0:
        raise PromotionError("battle regression audit is not green")

    ta = load_json(TERM_AUDIT)
    # terminology audit schema has changed historically; accept only zero-hit reports.
    if ta.get("status") not in (None, "clean"):
        raise PromotionError("terminology audit not clean")
    for key in ("active_source_hits", "dictionary_hits", "five_bank_dictionary_hits", "rendered_record_hits"):
        if key in ta:
            value = ta[key]
            hit_count = len(value) if isinstance(value, list) else int(value)
            if hit_count != 0:
                raise PromotionError(f"terminology audit hit: {key}={value}")

    rc = load_json(RUNTIME_CONTRACTS)
    rows = [r for r in rc.get("contracts", []) if r.get("address") == "638CD5"]
    if len(rows) != 1:
        raise PromotionError("runtime contract 638CD5 missing/duplicated")
    row = rows[0]
    if not (
        row.get("status") == "active"
        and row.get("confidence") == "runtime-proven"
        and row.get("route") == "scenario_first"
        and (row.get("decoder") or {}).get("ext3") is False
        and row.get("baseline_body_hex") == "F191E51D"
    ):
        raise PromotionError(f"runtime contract 638CD5 not locked: {row}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_stage22t_event8ce3_native2_portal_v3"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / MAIN.name
    backup.write_bytes(main_before)
    if sha(backup.read_bytes()) != MAIN_SHA:
        raise PromotionError("backup verification failed")

    # ROM-only promotion. Do not touch the live SaveRAM.
    shutil.copyfile(CANDIDATE, MAIN)
    main_after = MAIN.read_bytes()
    save_after = LIVE_SAVE.read_bytes()
    if sha(main_after) != CANDIDATE_SHA:
        raise PromotionError("promoted main verification failed")
    if save_after != save_before:
        # Restore main before failing; SaveRAM was not written by this script.
        MAIN.write_bytes(main_before)
        raise PromotionError("live SaveRAM changed during promotion")

    result = {
        "ok": True,
        "status": "promoted",
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "user_runtime_evidence": {
            "stage22t_event_error_12288_36067": "PASS",
            "dialogue_after_638CD5": "PASS",
            "following_uso_dialogue": "PASS",
            "following_katejina_event": "PASS",
        },
        "before": {"main_sha256": MAIN_SHA, "save_sha256": sha(save_before)},
        "after": {"main_sha256": CANDIDATE_SHA, "save_sha256": sha(save_after)},
        "backup": str(backup.relative_to(ROOT)).replace("\\", "/"),
        "candidate": str(CANDIDATE.relative_to(ROOT)).replace("\\", "/"),
        "gates": {
            "terminator_drift": 0,
            "unsafe_ext3_zero_middle": 0,
            "event_bank_unknown_diff_runs": 0,
            "E51D_semantic_consumers": usage,
            "battle_failures": 0,
            "terminology_clean": True,
            "runtime_contract_638CD5": "active/runtime-proven/ext3-false/F191E51D",
        },
    }
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
