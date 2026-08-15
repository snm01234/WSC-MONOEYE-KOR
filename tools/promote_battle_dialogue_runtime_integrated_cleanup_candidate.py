#!/usr/bin/env python3
"""Promote the user-validated integrated battle-dialogue runtime cleanup candidate."""
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
CANDIDATE = PATCH / "battle_dialogue_runtime_integrated_cleanup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/battle_dialogue_runtime_integrated_cleanup_candidate.sav"
SRAM_MIRROR = ROOT / "sram/battle_dialogue_runtime_integrated_cleanup_candidate.sav"
SHORT_PARENT = PATCH / "battle_dialogue_short_fixed_structure_repair_candidate.wsc"
SHORT_PARENT_SAVE = ROOT / "sram/battle_dialogue_short_fixed_structure_repair_candidate.sav"
SHORT_SRAM = ROOT / "sram/battle_dialogue_short_fixed_structure_repair_candidate.sav"
BUILD = PATCH / "battle_dialogue_runtime_integrated_cleanup_report.json"
AUDIT = PATCH / "battle_dialogue_runtime_integrated_cleanup_audit.json"
FALSE_SEGPTR = PATCH / "battle_dialogue_runtime_integrated_cleanup_false_segptr.json"
APPROVAL = PATCH / "battle_dialogue_runtime_integrated_cleanup_user_validation.json"
REPORT = PATCH / "battle_dialogue_runtime_integrated_cleanup_promotion_report.json"
POST_AUDIT = PATCH / "battle_dialogue_runtime_integrated_cleanup_postpromotion_audit.json"
POST_FALSE = PATCH / "battle_dialogue_runtime_integrated_cleanup_postpromotion_false_segptr.json"
EXPECTED_PARENT = "56b1ed5b81d9878bed01383f68abfffc876ad04eea5dd1d4d29525c833c83898"
EXPECTED_CANDIDATE = "64ade267ea6f5153e0d19bbdc308ed3f07b1da0891fcb485cc70dcd3100b2464"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TARGETS = 356
EXPECTED_BATTLE_RECORDS = 9783
EXPECTED_NONTARGET = 9428
EXPECTED_SHORT_METADATA = 104


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"invalid JSON root: {path}")
    return value


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, document: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    required = (TIP, SAVE, CANDIDATE, CANDIDATE_SAVE, SRAM_MIRROR, BUILD, AUDIT, FALSE_SEGPTR, APPROVAL)
    for path in required:
        if not path.is_file():
            fail(f"missing: {path}")
    if TIP.stat().st_size != ROM_SIZE or sha(TIP) != EXPECTED_PARENT:
        fail("main TIP parent identity drifted")
    if CANDIDATE.stat().st_size != ROM_SIZE or sha(CANDIDATE) != EXPECTED_CANDIDATE:
        fail("candidate identity drifted")
    if SAVE.stat().st_size != SAVE_SIZE:
        fail("live SaveRAM size drifted")
    if CANDIDATE_SAVE.read_bytes() != SAVE.read_bytes() or SRAM_MIRROR.read_bytes() != SAVE.read_bytes():
        fail("candidate SaveRAM pair no longer matches current live SaveRAM")

    build = load(BUILD)
    audit = load(AUDIT)
    false_segptr = load(FALSE_SEGPTR)
    approval = load(APPROVAL)
    if build.get("ok") is not True:
        fail("build report not clean")
    if str((((build.get("outputs") or {}).get("candidate_rom") or {}).get("sha256") or "")).lower() != EXPECTED_CANDIDATE:
        fail("build report candidate binding mismatch")
    gates = audit.get("gates") or {}
    counts = audit.get("counts") or {}
    if audit.get("ok") is not True or not gates or not all(bool(v) for v in gates.values()):
        fail("independent audit not clean")
    expected_counts = {
        "battle_records": EXPECTED_BATTLE_RECORDS,
        "targets": EXPECTED_TARGETS,
        "non_target_battle_records": EXPECTED_NONTARGET,
        "target_failures": 0,
        "non_target_battle_changes": 0,
        "stage1_short_metadata": EXPECTED_SHORT_METADATA,
        "stage1_short_metadata_changes": 0,
        "unaccounted_diff_runs": 0,
        "false_segmented_pointer_writes": 0,
    }
    for key, value in expected_counts.items():
        if int(counts.get(key, -1)) != value:
            fail(f"audit count drifted: {key}")
    if false_segptr.get("ok") is not True or int(false_segptr.get("sites_found", -1)) != 0:
        fail("false segmented-pointer gate failed")
    false_target = ((false_segptr.get("inputs") or {}).get("target") or {}).get("sha256")
    if str(false_target or "").lower() != EXPECTED_CANDIDATE:
        fail("false-segptr candidate binding mismatch")
    if approval.get("approved") is not True or approval.get("promotion_authorized") is not True:
        fail("user approval missing")
    if str(approval.get("candidate_sha256") or "").lower() != EXPECTED_CANDIDATE:
        fail("user approval candidate binding mismatch")
    if str(approval.get("parent_tip_sha256") or "").lower() != EXPECTED_PARENT:
        fail("user approval parent binding mismatch")

    candidate_bytes = CANDIDATE.read_bytes()
    if not checksum_ok(candidate_bytes):
        fail("candidate WonderSwan checksum invalid")
    candidate_ident = ident(CANDIDATE)
    candidate_save_ident = ident(CANDIDATE_SAVE)
    sram_ident = ident(SRAM_MIRROR)
    build_ident = ident(BUILD)
    audit_ident = ident(AUDIT)
    false_ident = ident(FALSE_SEGPTR)
    approval_ident = ident(APPROVAL)
    save_before = ident(SAVE)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_battle_dialogue_runtime_integrated_cleanup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copyfile(TIP, backup_rom)
    if sha(backup_rom) != EXPECTED_PARENT:
        fail("backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.runtime_integrated.{os.getpid()}.tmp")
    shutil.copyfile(CANDIDATE, staged)
    if sha(staged) != EXPECTED_CANDIDATE:
        staged.unlink(missing_ok=True)
        fail("staged TIP verification failed")
    os.replace(staged, TIP)

    promoted = TIP.read_bytes()
    after = ident(TIP)
    save_after = ident(SAVE)
    if after["sha256"] != EXPECTED_CANDIDATE or promoted != candidate_bytes:
        fail("promoted TIP is not byte-exact candidate")
    if save_after != save_before:
        fail("live SaveRAM changed during promotion")
    if not checksum_ok(promoted):
        fail("promoted TIP checksum invalid")

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/scan_false_segptr_writes.py"),
            "--target", str(TIP),
            "--lo-bank", "0x5D",
            "--hi-bank", "0x69",
            "--out", str(POST_FALSE),
        ],
        cwd=ROOT,
        check=True,
    )
    post_false = load(POST_FALSE)
    post_false_target = ((post_false.get("inputs") or {}).get("target") or {}).get("sha256")
    if post_false.get("ok") is not True or int(post_false.get("sites_found", -1)) != 0:
        fail("post-promotion false segmented-pointer gate failed")
    if str(post_false_target or "").lower() != EXPECTED_CANDIDATE:
        fail("post-promotion false-segptr main binding mismatch")

    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_dialogue_runtime_integrated_cleanup_candidate.py",
        "ok": True,
        "checks": {
            "promoted_tip_exact_candidate": promoted == candidate_bytes,
            "promoted_tip_sha_exact": after["sha256"] == EXPECTED_CANDIDATE,
            "wonder_swan_checksum_exact": checksum_ok(promoted),
            "live_saveram_unchanged": save_after == save_before,
            "prepromotion_independent_audit_clean": audit.get("ok") is True and all(bool(v) for v in gates.values()),
            "target_render_exact": bool(gates.get("target_render_exact")),
            "target_terminators_exact": bool(gates.get("target_terminators_exact")),
            "portrait_speaker_stage1_metadata_exact": bool(gates.get("portrait_speaker_stage1_metadata_exact")),
            "non_target_battle_structure_exact": bool(gates.get("non_target_battle_structure_exact")),
            "whole_rom_diff_confined": bool(gates.get("whole_rom_diff_confined")),
            "postpromotion_false_segmented_pointer_zero": int(post_false.get("sites_found", -1)) == 0,
        },
        "before_backup": ident(backup_rom),
        "after": after,
        "live_saveram": save_after,
        "prepromotion_counts": counts,
        "checksum": f"{int.from_bytes(promoted[-2:], 'little'):04X}",
        "postpromotion_false_segptr": ident(POST_FALSE),
    }
    if not all(post["checks"].values()):
        fail("post-promotion audit failed")
    atomic_json(POST_AUDIT, post)

    cleanup_candidates = [
        CANDIDATE,
        CANDIDATE_SAVE,
        SRAM_MIRROR,
        SHORT_PARENT,
        SHORT_PARENT_SAVE,
        SHORT_SRAM,
    ]
    cleanup = []
    reclaimed = 0
    for path in cleanup_candidates:
        if not path.exists():
            continue
        size = path.stat().st_size
        path.unlink()
        reclaimed += size
        cleanup.append({"path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"), "bytes": size})

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_dialogue_runtime_integrated_cleanup_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_validated_battle_dialogue_runtime_integrated_cleanup",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup_rom),
        "live_saveram_unchanged": save_after,
        "source_candidate_before_cleanup": candidate_ident,
        "source_candidate_saveram_before_cleanup": candidate_save_ident,
        "sram_mirror_before_cleanup": sram_ident,
        "build_report": build_ident,
        "independent_audit": audit_ident,
        "prepromotion_false_segmented_pointer_report": false_ident,
        "user_validation": approval_ident,
        "postpromotion_audit": ident(POST_AUDIT),
        "postpromotion_false_segmented_pointer_report": ident(POST_FALSE),
        "validated_scope": approval.get("validated_scope") or [],
        "counts": counts,
        "checksum": f"{int.from_bytes(promoted[-2:], 'little'):04X}",
        "cleanup": {"files": cleanup, "reclaimed_bytes": reclaimed},
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
