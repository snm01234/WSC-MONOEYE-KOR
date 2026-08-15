#!/usr/bin/env python3
"""Promote the user-approved battle-dialogue false-lead cleanup candidate."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "battle_dialogue_false_lead_cleanup_candidate.wsc"
BUILD = PATCH / "battle_dialogue_false_lead_cleanup_report.json"
AUDIT = PATCH / "battle_dialogue_false_lead_cleanup_audit.json"
FALSE_SEGPTR = PATCH / "battle_dialogue_false_lead_cleanup_false_segptr.json"
APPROVAL = PATCH / "battle_dialogue_false_lead_cleanup_user_validation.json"
REPORT = PATCH / "battle_dialogue_false_lead_cleanup_promotion_report.json"
POST_AUDIT = PATCH / "battle_dialogue_false_lead_cleanup_postpromotion_audit.json"
EXPECTED_PARENT = "bac5e179ae496dd2b70912da0b1987b2dc6f7551e9f4d9de2d48c8c2152f7c88"
EXPECTED_CANDIDATE = "56b1ed5b81d9878bed01383f68abfffc876ad04eea5dd1d4d29525c833c83898"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TARGETS = 264
EXPECTED_OVERRIDES = 57
EXPECTED_PROTECTED = 3356


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


def fail(message: str) -> None:
    raise SystemExit(message)


def atomic_json(path: Path, document: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main() -> int:
    for path in (TIP, SAVE, CANDIDATE, BUILD, AUDIT, FALSE_SEGPTR, APPROVAL):
        if not path.is_file():
            fail(f"missing: {path}")
    if TIP.stat().st_size != ROM_SIZE or sha(TIP) != EXPECTED_PARENT:
        fail("main TIP parent identity drifted")
    if SAVE.stat().st_size != SAVE_SIZE:
        fail("live SaveRAM size drifted")
    if CANDIDATE.stat().st_size != ROM_SIZE or sha(CANDIDATE) != EXPECTED_CANDIDATE:
        fail("candidate identity drifted")

    build = load(BUILD)
    audit = load(AUDIT)
    false_segptr = load(FALSE_SEGPTR)
    approval = load(APPROVAL)

    if build.get("ok") is not True:
        fail("build report not clean")
    build_candidate = ((build.get("outputs") or {}).get("candidate_rom") or {}).get("sha256")
    if str(build_candidate or "").lower() != EXPECTED_CANDIDATE:
        fail("build report candidate binding mismatch")

    checks = audit.get("checks") or {}
    counts = audit.get("counts") or {}
    if audit.get("ok") is not True or not checks or not all(bool(v) for v in checks.values()):
        fail("independent audit not clean")
    if int(counts.get("safe_targets") or 0) != EXPECTED_TARGETS:
        fail("safe target count drifted")
    if int(counts.get("safe_targets_exact") or 0) != EXPECTED_TARGETS:
        fail("safe target exact count drifted")
    if int(counts.get("fullbody_overrides") or 0) != EXPECTED_OVERRIDES:
        fail("full-body override count drifted")
    if int(counts.get("protected_or_unresolved_rows") or 0) != EXPECTED_PROTECTED:
        fail("protected/unresolved population drifted")
    if int(counts.get("protected_or_unresolved_changed", -1)) != 0:
        fail("protected/unresolved row changed")
    if int(counts.get("unexpected_changed_bytes", -1)) != 0:
        fail("diff confinement gate failed")

    if false_segptr.get("ok") is not True or int(false_segptr.get("sites_found") or 0) != 0:
        fail("false segmented-pointer gate failed")
    target_info = (false_segptr.get("inputs") or {}).get("target") or {}
    if str(target_info.get("sha256") or "").lower() != EXPECTED_CANDIDATE:
        fail("false-segptr report candidate binding mismatch")

    if approval.get("approved") is not True or approval.get("promotion_authorized") is not True:
        fail("user approval missing")
    if str(approval.get("candidate_sha256") or "").lower() != EXPECTED_CANDIDATE:
        fail("user approval candidate binding mismatch")
    if str(approval.get("parent_tip_sha256") or "").lower() != EXPECTED_PARENT:
        fail("user approval parent binding mismatch")

    candidate_bytes = CANDIDATE.read_bytes()
    if not checksum_ok(candidate_bytes):
        fail("candidate WonderSwan checksum invalid")

    save_before = ident(SAVE)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_battle_dialogue_false_lead_cleanup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copyfile(TIP, backup_rom)
    if sha(backup_rom) != EXPECTED_PARENT:
        fail("backup ROM verification failed")

    tmp = TIP.with_name(f".{TIP.name}.battle_false_lead.{os.getpid()}.tmp")
    shutil.copyfile(CANDIDATE, tmp)
    if sha(tmp) != EXPECTED_CANDIDATE:
        tmp.unlink(missing_ok=True)
        fail("staged TIP verification failed")
    os.replace(tmp, TIP)

    after = ident(TIP)
    save_after = ident(SAVE)
    if after["sha256"] != EXPECTED_CANDIDATE:
        fail("promoted TIP verification failed")
    if save_after != save_before:
        fail("live SaveRAM changed during promotion")
    promoted_bytes = TIP.read_bytes()
    if not checksum_ok(promoted_bytes):
        fail("promoted TIP checksum invalid")
    if promoted_bytes != candidate_bytes:
        fail("promoted TIP is not byte-exact candidate")

    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_dialogue_false_lead_cleanup_candidate.py",
        "read_only_after_promotion": True,
        "ok": True,
        "checks": {
            "promoted_tip_exact_candidate": promoted_bytes == candidate_bytes,
            "promoted_tip_sha_exact": after["sha256"] == EXPECTED_CANDIDATE,
            "wonder_swan_checksum_exact": checksum_ok(promoted_bytes),
            "live_saveram_unchanged": save_after == save_before,
            "prepromotion_independent_audit_clean": audit.get("ok") is True and all(bool(v) for v in checks.values()),
            "false_segmented_pointer_zero": false_segptr.get("ok") is True and int(false_segptr.get("sites_found") or 0) == 0,
            "safe_targets_exact": int(counts.get("safe_targets_exact") or 0) == EXPECTED_TARGETS,
            "protected_unresolved_unchanged": int(counts.get("protected_or_unresolved_changed", -1)) == 0,
            "unexpected_changed_bytes_zero": int(counts.get("unexpected_changed_bytes", -1)) == 0,
        },
        "before_backup": ident(backup_rom),
        "after": after,
        "source_candidate": ident(CANDIDATE),
        "live_saveram": save_after,
        "counts": {
            "safe_targets": EXPECTED_TARGETS,
            "fullbody_overrides": EXPECTED_OVERRIDES,
            "protected_or_unresolved_rows": EXPECTED_PROTECTED,
            "false_segmented_pointer_sites": int(false_segptr.get("sites_found") or 0),
        },
        "checksum": promoted_bytes[-2:].hex().upper(),
    }
    if not all(post["checks"].values()):
        fail("post-promotion audit failed")
    atomic_json(POST_AUDIT, post)

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_dialogue_false_lead_cleanup_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_validated_battle_dialogue_false_lead_cleanup_candidate",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup_rom),
        "live_saveram_unchanged": save_after,
        "source_candidate": ident(CANDIDATE),
        "build_report": ident(BUILD),
        "independent_audit": ident(AUDIT),
        "false_segmented_pointer_report": ident(FALSE_SEGPTR),
        "user_validation": ident(APPROVAL),
        "postpromotion_audit": ident(POST_AUDIT),
        "validated_scope": approval.get("validated_scope") or [],
        "counts": post["counts"],
        "checksum": promoted_bytes[-2:].hex().upper(),
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
