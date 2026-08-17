#!/usr/bin/env python3
"""Promote the runtime-approved mixed-exact4 + Emma terminology candidate.

Fail-closed promotion:
* verify current main and candidate SHA-256;
* verify pre-promotion audit reports are clean;
* preserve live SaveRAM byte-exact;
* back up current main under out/patch/backup/<timestamp>_pre_.../;
* atomically replace ROM only;
* write a promotion report.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "global_scenario_mixed_exact4_59_ema_candidate.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BUILD_REPORT = PATCH / "global_scenario_mixed_exact4_59_ema_report.json"
TERMINOLOGY = PATCH / "global_scenario_mixed_exact4_59_ema_terminology_audit.json"
SPEAKER = PATCH / "global_scenario_mixed_exact4_59_ema_speaker_audit.json"
RISK = PATCH / "global_scenario_mixed_exact4_59_ema_risk_audit.json"
OUT_REPORT = PATCH / "global_scenario_mixed_exact4_59_ema_promotion_report.json"

EXPECTED_MAIN_SHA = "714200ffdcad34d01c12c8f560b8ca71163c165803e5e9894feb30f523e166c6"
EXPECTED_CANDIDATE_SHA = "cfb90aaa7af2b9336fb63c70a8e7ec760ac51425d80017d5daf82e6118d86bca"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, payload: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def main() -> int:
    main_before = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = LIVE_SAVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha(main_before) != EXPECTED_MAIN_SHA:
        raise RuntimeError(f"main identity drifted: {sha(main_before)}")
    if len(candidate) != ROM_SIZE or sha(candidate) != EXPECTED_CANDIDATE_SHA:
        raise RuntimeError(f"candidate identity drifted: {sha(candidate)}")
    if len(save_before) != SAVE_SIZE:
        raise RuntimeError(f"live SaveRAM size drifted: {len(save_before)}")

    build = load_json(BUILD_REPORT)
    if not build.get("ok"):
        raise RuntimeError("build report not OK")
    if int((build.get("scope") or {}).get("rendered_residual_엠마", -1)) != 0:
        raise RuntimeError("build report still has 엠마")

    term = load_json(TERMINOLOGY)
    counts = term.get("counts") or {}
    # audit_gundam_terminology_standard reports hit lists directly in many revisions.
    for key in ("active_source_hits", "dictionary_hits", "five_bank_dictionary_hits", "rendered_record_hits"):
        value = term.get(key, counts.get(key, []))
        if isinstance(value, list):
            n = len(value)
        elif isinstance(value, dict):
            n = len(value)
        else:
            n = int(value or 0)
        if n != 0:
            raise RuntimeError(f"terminology audit not clean: {key}={n}")

    speaker = load_json(SPEAKER)
    sc = speaker.get("counts") or {}
    if int(sc.get("japanese_or_mixed_remaining", -1)) != 0 or int(sc.get("over_20", -1)) != 0:
        raise RuntimeError(f"speaker/control audit not clean: {sc}")

    risk = load_json(RISK)
    rc = risk.get("counts") or {}
    required_zero = [
        "scenario_boundary_drift",
        "exact4_mixed_control_adjacent",
        "exact4_anchor_source_F191081D",
        "exact4_contains_context_sensitive_pair",
        "exact4_next_08",
        "exact4_next_17",
        "current_083400_changed",
        "speaker_collision_current_mixed_remaining",
    ]
    bad = {k: int(rc.get(k, -1)) for k in required_zero if int(rc.get(k, -1)) != 0}
    if bad:
        raise RuntimeError(f"risk audit blockers: {bad}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_global_scenario_mixed_exact4_59_ema"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / MAIN.name
    shutil.copy2(MAIN, backup)
    if backup.read_bytes() != main_before:
        raise RuntimeError("backup verification failed")

    atomic_write(MAIN, candidate)
    main_after = MAIN.read_bytes()
    save_after = LIVE_SAVE.read_bytes()
    if main_after != candidate or sha(main_after) != EXPECTED_CANDIDATE_SHA:
        raise RuntimeError("post-promotion main != candidate")
    if save_after != save_before:
        raise RuntimeError("live SaveRAM changed during promotion")

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_global_scenario_mixed_exact4_59_ema_candidate.py",
        "ok": True,
        "promoted_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "main_before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": len(main_before), "sha256": sha(main_before)},
        "candidate": {"path": "out/patch/global_scenario_mixed_exact4_59_ema_candidate.wsc", "size": len(candidate), "sha256": sha(candidate)},
        "main_after": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": len(main_after), "sha256": sha(main_after)},
        "backup": {"path": str(backup.relative_to(ROOT)).replace("\\", "/"), "size": len(main_before), "sha256": sha(backup.read_bytes())},
        "live_saveram": {"path": "sram/monoeye_ko_expanded.sav", "size": len(save_after), "sha256_before": sha(save_before), "sha256_after": sha(save_after), "byte_exact_preserved": save_after == save_before},
        "runtime_evidence": {
            "mixed_exact4_review": {
                "60B400_F191081D_STAGE4": "user runtime PASS",
                "6184FD_08xx": "user runtime PASS",
                "61AA81_1728": "user runtime PASS"
            },
            "emma_standardization": "equal-size five dictionary phrase edits; static whole-game terminology audit clean"
        },
        "prepromotion_audits": {
            "terminology": str(TERMINOLOGY.relative_to(ROOT)).replace("\\", "/"),
            "speaker_control": str(SPEAKER.relative_to(ROOT)).replace("\\", "/"),
            "risk": str(RISK.relative_to(ROOT)).replace("\\", "/")
        }
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
