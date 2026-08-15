#!/usr/bin/env python3
"""Promote the user-validated uncovered LLM-reviewed candidate to the main TIP.

The live SaveRAM is never replaced. The current main ROM is backed up and
verified before an atomic ROM-only replacement. On post-promotion failure the
ROM is rolled back from the verified backup.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "uncovered_llm_reviewed_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/uncovered_llm_reviewed_candidate.sav"
BUILD = PATCH / "uncovered_llm_reviewed_candidate_report.json"
PRE_AUDIT = PATCH / "uncovered_llm_reviewed_postcheck.json"
PRE_FALSE = PATCH / "uncovered_llm_reviewed_false_segptr.json"
APPROVAL = PATCH / "uncovered_llm_reviewed_user_validation.json"
POST_FALSE = PATCH / "uncovered_llm_reviewed_postpromotion_false_segptr.json"
POST_AUDIT = PATCH / "uncovered_llm_reviewed_postpromotion_audit.json"
REPORT = PATCH / "uncovered_llm_reviewed_promotion_report.json"

EXPECTED_PARENT = "46d6d6a984ec7696428ade90f5ea1e191f218e568242e2439f7347a6004b9729"
EXPECTED_CANDIDATE = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid JSON root: {path}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def main() -> int:
    for path in (TIP, SAVE, CANDIDATE, CANDIDATE_SAVE, BUILD, PRE_AUDIT, PRE_FALSE, APPROVAL):
        require(path.is_file(), f"missing required file: {path}")

    require(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main TIP parent identity drifted")
    require(CANDIDATE.stat().st_size == ROM_SIZE and sha(CANDIDATE) == EXPECTED_CANDIDATE, "candidate identity drifted")
    require(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drifted")
    require(CANDIDATE_SAVE.read_bytes() == SAVE.read_bytes(), "candidate SaveRAM no longer matches live SaveRAM")

    build = load(BUILD)
    counts = build.get("counts") or {}
    verification = build.get("verification") or {}
    require(build.get("ok") is True, "candidate build report is not clean")
    require(int(counts.get("reviewed_sheet_rows", -1)) == 1893, "reviewed sheet row count drifted")
    require(int(counts.get("new_delta_targets", -1)) == 116, "delta target count drifted")
    require(int(counts.get("target_failures", -1)) == 0, "target failures remain")
    for key in (
        "all_target_renders_exact",
        "all_target_terminators_exact",
        "all_target_structure_exact_except_portal_index",
    ):
        require(verification.get(key) is True, f"candidate verification failed: {key}")
    require(int(verification.get("reviewed_portal_mismatches", -1)) == 0, "reviewed portal mismatches remain")
    require(int(verification.get("unaccounted_changed_bytes", -1)) == 0, "unaccounted candidate bytes remain")
    require(str(verification.get("candidate_sha256") or "").lower() == EXPECTED_CANDIDATE, "build report candidate binding mismatch")

    pre = load(PRE_AUDIT)
    pre_counts = pre.get("counts") or {}
    require(pre.get("ok") is True, "prepromotion postcheck is not clean")
    require(int(pre_counts.get("mismatches", -1)) == 0, "prepromotion reviewed mismatch remains")
    require(int(pre_counts.get("stale_bad_terms", -1)) == 0, "prepromotion stale bad terms remain")
    require(int(pre_counts.get("false_segmented_pointer_sites", -1)) == 0, "prepromotion false-segptr remains")
    require(str((pre.get("inputs") or {}).get("candidate_sha256") or "").lower() == EXPECTED_CANDIDATE, "prepromotion audit candidate binding mismatch")

    pre_false = load(PRE_FALSE)
    require(pre_false.get("ok") is True and int(pre_false.get("sites_found", -1)) == 0, "prepromotion false-segptr report failed")
    require(str((((pre_false.get("inputs") or {}).get("target") or {}).get("sha256") or "")).lower() == EXPECTED_CANDIDATE, "prepromotion false-segptr binding mismatch")

    approval = load(APPROVAL)
    require(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user approval missing")
    require(str(approval.get("parent_tip_sha256") or "").lower() == EXPECTED_PARENT, "approval parent binding mismatch")
    require(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CANDIDATE, "approval candidate binding mismatch")

    candidate_bytes = CANDIDATE.read_bytes()
    require(checksum_ok(candidate_bytes), "candidate WonderSwan checksum invalid")
    save_before = ident(SAVE)
    candidate_ident = ident(CANDIDATE)
    candidate_save_ident = ident(CANDIDATE_SAVE)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_uncovered_llm_reviewed"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copyfile(TIP, backup_rom)
    require(sha(backup_rom) == EXPECTED_PARENT, "rollback backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.uncovered_llm_reviewed.{os.getpid()}.tmp")
    shutil.copyfile(CANDIDATE, staged)
    require(sha(staged) == EXPECTED_CANDIDATE, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        require(TIP.read_bytes() == candidate_bytes and sha(TIP) == EXPECTED_CANDIDATE, "promoted TIP is not byte-exact candidate")
        require(checksum_ok(TIP.read_bytes()), "promoted WonderSwan checksum invalid")
        require(ident(SAVE) == save_before, "live SaveRAM changed during promotion")

        subprocess.run([
            sys.executable,
            str(ROOT / "tools/scan_false_segptr_writes.py"),
            "--target", str(TIP),
            "--lo-bank", "0x5D",
            "--hi-bank", "0x69",
            "--out", str(POST_FALSE),
        ], cwd=ROOT, check=True)
        post_false = load(POST_FALSE)
        require(post_false.get("ok") is True and int(post_false.get("sites_found", -1)) == 0, "postpromotion false-segptr gate failed")
        require(str((((post_false.get("inputs") or {}).get("target") or {}).get("sha256") or "")).lower() == EXPECTED_CANDIDATE, "postpromotion false-segptr binding mismatch")
    except Exception:
        rollback_stage = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copyfile(backup_rom, rollback_stage)
        os.replace(rollback_stage, TIP)
        raise

    after = ident(TIP)
    save_after = ident(SAVE)
    post_audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_uncovered_llm_reviewed_candidate.py",
        "ok": True,
        "checks": {
            "promoted_tip_exact_candidate": TIP.read_bytes() == candidate_bytes,
            "promoted_tip_sha_exact": after["sha256"] == EXPECTED_CANDIDATE,
            "wonder_swan_checksum_exact": checksum_ok(TIP.read_bytes()),
            "live_saveram_unchanged": save_after == save_before,
            "prepromotion_build_clean": build.get("ok") is True,
            "prepromotion_review_audit_clean": pre.get("ok") is True,
            "prepromotion_review_mismatch_zero": int(pre_counts.get("mismatches", -1)) == 0,
            "prepromotion_stale_bad_terms_zero": int(pre_counts.get("stale_bad_terms", -1)) == 0,
            "postpromotion_false_segmented_pointer_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
        },
        "before_backup": ident(backup_rom),
        "after": after,
        "live_saveram": save_after,
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "postpromotion_false_segptr": ident(POST_FALSE),
    }
    require(all(post_audit["checks"].values()), "postpromotion audit failed")
    atomic_json(POST_AUDIT, post_audit)

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_uncovered_llm_reviewed_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_validated_uncovered_llm_reviewed",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup_rom),
        "live_saveram_unchanged": save_after,
        "source_candidate_before_cleanup": candidate_ident,
        "source_candidate_saveram_before_cleanup": candidate_save_ident,
        "build_report": ident(BUILD),
        "prepromotion_postcheck": ident(PRE_AUDIT),
        "prepromotion_false_segmented_pointer_report": ident(PRE_FALSE),
        "user_validation": ident(APPROVAL),
        "postpromotion_audit": ident(POST_AUDIT),
        "postpromotion_false_segmented_pointer_report": ident(POST_FALSE),
        "validated_scope": approval.get("validated_scope") or [],
        "counts": counts,
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    atomic_json(REPORT, report)

    cleanup = []
    reclaimed = 0
    for path in (CANDIDATE, CANDIDATE_SAVE):
        if path.exists():
            size = path.stat().st_size
            path.unlink()
            cleanup.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size
    report["cleanup"] = {"files": cleanup, "reclaimed_bytes": reclaimed}
    atomic_json(REPORT, report)

    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
