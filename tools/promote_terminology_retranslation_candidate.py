#!/usr/bin/env python3
"""Promote the user-validated terminology retranslation candidate to the main TIP."""
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
CANDIDATE = PATCH / "terminology_retranslation_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/terminology_retranslation_candidate.sav"
BUILD = PATCH / "terminology_retranslation_report.json"
PRE_POSTCHECK = PATCH / "terminology_retranslation_postcheck.json"
PRE_FALSE = PATCH / "terminology_retranslation_false_segptr.json"
APPROVAL = PATCH / "terminology_retranslation_user_validation.json"
POST_REEXTRACT = PATCH / "terminology_retranslation_postpromotion_reextract.json"
POST_REEXTRACT_CSV = PATCH / "terminology_retranslation_postpromotion_reextract.csv"
POST_FALSE = PATCH / "terminology_retranslation_postpromotion_false_segptr.json"
POST_AUDIT = PATCH / "terminology_retranslation_postpromotion_audit.json"
REPORT = PATCH / "terminology_retranslation_promotion_report.json"
EXPECTED_PARENT = "64ade267ea6f5153e0d19bbdc308ed3f07b1da0891fcb485cc70dcd3100b2464"
EXPECTED_CANDIDATE = "46d6d6a984ec7696428ade90f5ea1e191f218e568242e2439f7347a6004b9729"
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


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, document: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    for path in (TIP, SAVE, CANDIDATE, CANDIDATE_SAVE, BUILD, PRE_POSTCHECK, PRE_FALSE, APPROVAL):
        require(path.is_file(), f"missing required file: {path}")

    require(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main TIP parent identity drifted")
    require(CANDIDATE.stat().st_size == ROM_SIZE and sha(CANDIDATE) == EXPECTED_CANDIDATE, "candidate identity drifted")
    require(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drifted")
    require(CANDIDATE_SAVE.read_bytes() == SAVE.read_bytes(), "candidate SaveRAM no longer matches live SaveRAM")

    build = load(BUILD)
    verification = build.get("verification") or {}
    counts = build.get("counts") or {}
    require(build.get("ok") is True, "build report is not clean")
    require(str(verification.get("candidate_sha256") or "").lower() == EXPECTED_CANDIDATE, "build report candidate binding mismatch")
    require(int(counts.get("translation_records", -1)) == 69, "translation record count drifted")
    require(int(counts.get("target_failures", -1)) == 0 and int(counts.get("bad_target_residuals", -1)) == 0, "build residual/failure gate failed")
    for key in ("record_bodies_unchanged", "terminators_unchanged", "all_target_renders_exact", "all_shared_dictionary_renders_exact", "all_private_ext3_proofs_ok"):
        require(verification.get(key) is True, f"build verification failed: {key}")
    require(int(verification.get("unaccounted_changed_bytes", -1)) == 0, "unaccounted build bytes remain")

    pre_post = load(PRE_POSTCHECK)
    pre_counts = pre_post.get("counts") or {}
    require(int(pre_counts.get("targets", -1)) == 0, "candidate postcheck still has terminology targets")
    for key in ("brad", "rank_lieutenant_colonel", "rank_major", "rank_colonel", "camille", "kagero_mayfly"):
        require(int(pre_counts.get(key, -1)) == 0, f"candidate postcheck residual remains: {key}")

    pre_false = load(PRE_FALSE)
    false_target = ((pre_false.get("inputs") or {}).get("target") or {}).get("sha256")
    require(pre_false.get("ok") is True and int(pre_false.get("sites_found", -1)) == 0, "candidate false-segptr gate failed")
    require(str(false_target or "").lower() == EXPECTED_CANDIDATE, "candidate false-segptr binding mismatch")

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
    backup_dir = PATCH / "backup" / f"{stamp}_pre_terminology_retranslation"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copyfile(TIP, backup_rom)
    require(sha(backup_rom) == EXPECTED_PARENT, "backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.terminology_retranslation.{os.getpid()}.tmp")
    shutil.copyfile(CANDIDATE, staged)
    require(sha(staged) == EXPECTED_CANDIDATE, "staged TIP verification failed")
    os.replace(staged, TIP)

    try:
        promoted = TIP.read_bytes()
        require(promoted == candidate_bytes and sha(TIP) == EXPECTED_CANDIDATE, "promoted TIP is not byte-exact candidate")
        require(checksum_ok(promoted), "promoted TIP checksum invalid")
        require(ident(SAVE) == save_before, "live SaveRAM changed during promotion")

        subprocess.run([
            sys.executable,
            str(ROOT / "tools/extract_machine_translation_terminology_targets.py"),
            "--tip", str(TIP),
            "--out-json", str(POST_REEXTRACT),
            "--out-csv", str(POST_REEXTRACT_CSV),
        ], cwd=ROOT, check=True)
        post_extract = load(POST_REEXTRACT)
        post_counts = post_extract.get("counts") or {}
        require(int(post_counts.get("targets", -1)) == 0, "post-promotion terminology reextract found targets")
        for key in ("brad", "rank_lieutenant_colonel", "rank_major", "rank_colonel", "camille", "kagero_mayfly"):
            require(int(post_counts.get(key, -1)) == 0, f"post-promotion residual remains: {key}")

        subprocess.run([
            sys.executable,
            str(ROOT / "tools/scan_false_segptr_writes.py"),
            "--target", str(TIP),
            "--lo-bank", "0x5D",
            "--hi-bank", "0x69",
            "--out", str(POST_FALSE),
        ], cwd=ROOT, check=True)
        post_false = load(POST_FALSE)
        post_false_target = ((post_false.get("inputs") or {}).get("target") or {}).get("sha256")
        require(post_false.get("ok") is True and int(post_false.get("sites_found", -1)) == 0, "post-promotion false-segptr gate failed")
        require(str(post_false_target or "").lower() == EXPECTED_CANDIDATE, "post-promotion false-segptr binding mismatch")
    except Exception:
        rollback_stage = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copyfile(backup_rom, rollback_stage)
        os.replace(rollback_stage, TIP)
        raise

    after = ident(TIP)
    save_after = ident(SAVE)
    post_audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_terminology_retranslation_candidate.py",
        "ok": True,
        "checks": {
            "promoted_tip_exact_candidate": TIP.read_bytes() == candidate_bytes,
            "promoted_tip_sha_exact": after["sha256"] == EXPECTED_CANDIDATE,
            "wonder_swan_checksum_exact": checksum_ok(TIP.read_bytes()),
            "live_saveram_unchanged": save_after == save_before,
            "prepromotion_build_clean": build.get("ok") is True,
            "prepromotion_terminology_targets_zero": int(pre_counts.get("targets", -1)) == 0,
            "postpromotion_terminology_targets_zero": int((load(POST_REEXTRACT).get("counts") or {}).get("targets", -1)) == 0,
            "postpromotion_false_segmented_pointer_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
        },
        "before_backup": ident(backup_rom),
        "after": after,
        "live_saveram": save_after,
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "postpromotion_reextract": ident(POST_REEXTRACT),
        "postpromotion_false_segptr": ident(POST_FALSE),
    }
    require(all(post_audit["checks"].values()), "post-promotion audit failed")
    atomic_json(POST_AUDIT, post_audit)

    cleanup = []
    reclaimed = 0
    for path in (CANDIDATE, CANDIDATE_SAVE):
        if path.exists():
            size = path.stat().st_size
            path.unlink()
            cleanup.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_terminology_retranslation_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_user_validated_terminology_retranslation",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup_rom),
        "live_saveram_unchanged": save_after,
        "source_candidate_before_cleanup": candidate_ident,
        "source_candidate_saveram_before_cleanup": candidate_save_ident,
        "build_report": ident(BUILD),
        "prepromotion_postcheck": ident(PRE_POSTCHECK),
        "prepromotion_false_segmented_pointer_report": ident(PRE_FALSE),
        "user_validation": ident(APPROVAL),
        "postpromotion_audit": ident(POST_AUDIT),
        "postpromotion_reextract": ident(POST_REEXTRACT),
        "postpromotion_false_segmented_pointer_report": ident(POST_FALSE),
        "validated_scope": approval.get("validated_scope") or [],
        "counts": counts,
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "cleanup": {"files": cleanup, "reclaimed_bytes": reclaimed},
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
