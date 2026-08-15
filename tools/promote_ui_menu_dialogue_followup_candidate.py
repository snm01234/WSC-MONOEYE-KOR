#!/usr/bin/env python3
"""Promote the statically accepted 2026-08-02 UI/menu/dialogue candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
BACKUP = PATCH / "backup"
TIP = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "ui_menu_dialogue_followup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/ui_menu_dialogue_followup_candidate.sav"
BUILD_REPORT = PATCH / "ui_menu_dialogue_followup_report.json"
AUDIT = PATCH / "ui_menu_dialogue_followup_audit.json"
GATES = PATCH / "ui_menu_dialogue_followup_gate_summary.json"
PROMOTION_REPORT = PATCH / "ui_menu_dialogue_followup_promotion_report.json"

OLD_TIP_SHA = "1161d11c5286d353f7bc9db1ba879284641c5ea3ed8c8101383761f7b97ed77a"
CANDIDATE_SHA = "70f53cbc34559366e856aedcd793fb5fde33c0c2199fd7166aa055fc89d5e677"
SAVE_SHA = "98acf8cfe76c128297acffcf3d8c6d2a4e9ffeaf7b5011236960647e3db09863"
BUILD_REPORT_SHA = "5afa8d40a5cc015fa89f4002936898523ca33bbce16c77e9228adc59461f6e8e"
AUDIT_SHA = "e731b417572d218948bb2043d33de41a3fe67d7e53ed0fa573be66be922f8e0f"
GATES_SHA = "166ce1190fbe2a7d1a467d71f4ad99db7467ac105d47bc514e35e6715aaf633d"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def require(path: Path, *, size: int | None = None, sha256: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing required file: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drifted for {rel(path)}")
    if sha256 is not None:
        actual = sha256_file(path)
        if actual != sha256:
            raise PromotionError(
                f"SHA drifted for {rel(path)}: expected {sha256}, got {actual}"
            )


def load_json(path: Path) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise PromotionError(f"JSON root is not an object: {rel(path)}")
    return document


def validate() -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, sha256=OLD_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, sha256=CANDIDATE_SHA)
    require(MAIN_SAVE, size=SAVE_SIZE, sha256=SAVE_SHA)
    require(CANDIDATE_SAVE, size=SAVE_SIZE, sha256=SAVE_SHA)
    require(BUILD_REPORT, sha256=BUILD_REPORT_SHA)
    require(AUDIT, sha256=AUDIT_SHA)
    require(GATES, sha256=GATES_SHA)

    build = load_json(BUILD_REPORT)
    audit = load_json(AUDIT)
    gates = load_json(GATES)
    if build.get("ok") is not True or build.get("status") != "candidate_static_verified":
        raise PromotionError("build report is not accepted")
    if ((build.get("candidate_rom") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("build report candidate SHA mismatch")
    counts = build.get("counts") or {}
    if counts.get("records") != 43 or counts.get("shared_dictionary") != 2:
        raise PromotionError("build target counts drifted")
    verification = build.get("verification") or {}
    if verification.get("decode_failures") or verification.get("unaccounted_changed_bytes") != 0:
        raise PromotionError("build verification is incomplete")
    if any(not row.get("ok") for row in verification.get("consumer_checks") or []):
        raise PromotionError("dictionary consumer proof failed")

    if audit.get("ok") is not True:
        raise PromotionError("independent audit failed")
    audit_counts = audit.get("counts") or {}
    if audit_counts != {
        "target_records": 43,
        "target_exact": 43,
        "target_failures": 0,
        "shared_dictionary": 2,
        "shared_exact": 2,
        "japanese_residuals_in_targets": 0,
    }:
        raise PromotionError("independent audit counts drifted")
    general = audit.get("general_compact3") or {}
    if general.get("payload_hex") != "E519F6" or general.get("ok") is not True:
        raise PromotionError("범용 compact3 proof failed")
    if any(not row.get("ok") for row in audit.get("dialogue_prefix") or []):
        raise PromotionError("dialogue prefix proof failed")

    if gates.get("ok") is not True or gates.get("accepted_static") is not True:
        raise PromotionError("gate summary is not accepted")
    checks = gates.get("checks") or {}
    for name in (
        "builder_static_proof",
        "independent_target_audit",
        "record_structure",
        "false_segmented_pointer",
        "python_compile",
    ):
        if (checks.get(name) or {}).get("ok") is not True:
            raise PromotionError(f"blocking gate failed: {name}")
    legacy_smoke = checks.get("legacy_smoke") or {}
    if legacy_smoke.get("blocking") is not False or legacy_smoke.get("candidate_specific_ok") is not True:
        raise PromotionError("legacy smoke interpretation drifted")
    if any(legacy_smoke.get(key) is not True for key in ("jagd_ok", "opening_required_ok", "hangul_ok")):
        raise PromotionError("legacy smoke anchors failed")
    legacy_nondialogue = checks.get("legacy_nondialogue") or {}
    if legacy_nondialogue.get("blocking") is not False or legacy_nondialogue.get("candidate_specific_ok") is not True:
        raise PromotionError("legacy nondialogue interpretation drifted")
    if any(
        legacy_nondialogue.get(key) is not True
        for key in ("marker_records_ok", "length_terminator_ok", "nested_dictionary_detachment_ok")
    ):
        raise PromotionError("legacy nondialogue structural checks failed")

    return {
        "build_counts": dict(counts),
        "audit_counts": dict(audit_counts),
        "candidate_sha256": CANDIDATE_SHA,
        "save_sha256": SAVE_SHA,
    }


def copy_verified(source: Path, destination: Path, expected_sha: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if sha256_file(destination) != expected_sha:
        raise PromotionError(f"backup verification failed: {rel(destination)}")


def atomic_promote() -> None:
    temporary = TIP.with_name(f".{TIP.name}.ui-menu-dialogue-promote.tmp")
    temporary.unlink(missing_ok=True)
    with CANDIDATE.open("rb") as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    if temporary.stat().st_size != ROM_SIZE or sha256_file(temporary) != CANDIDATE_SHA:
        temporary.unlink(missing_ok=True)
        raise PromotionError("temporary promoted ROM failed verification")
    os.replace(temporary, TIP)


def write_report(document: Mapping[str, Any]) -> None:
    temporary = PROMOTION_REPORT.with_name(f".{PROMOTION_REPORT.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, PROMOTION_REPORT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    validation = validate()
    if not args.commit:
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "ok": True,
                    "tip": identity(TIP),
                    "candidate": identity(CANDIDATE),
                    "save": identity(MAIN_SAVE),
                    "validation": validation,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_rom = BACKUP / f"monoeye_ko_expanded.before_ui_menu_dialogue_{stamp}.wsc"
    backup_save = BACKUP / f"monoeye_ko_expanded.before_ui_menu_dialogue_{stamp}.sav"
    copy_verified(TIP, backup_rom, OLD_TIP_SHA)
    copy_verified(MAIN_SAVE, backup_save, SAVE_SHA)

    atomic_promote()
    require(TIP, size=ROM_SIZE, sha256=CANDIDATE_SHA)
    require(MAIN_SAVE, size=SAVE_SIZE, sha256=SAVE_SHA)

    CANDIDATE.unlink()
    CANDIDATE_SAVE.unlink()
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_ui_menu_dialogue_followup_candidate.py",
        "mode": "commit",
        "ok": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": {
            "size": ROM_SIZE,
            "sha256": OLD_TIP_SHA,
        },
        "new_tip": identity(TIP),
        "main_save": identity(MAIN_SAVE),
        "backup_rom": identity(backup_rom),
        "backup_save": identity(backup_save),
        "validation": validation,
        "candidate_duplicates_removed": True,
        "evidence": {
            "build_report": identity(BUILD_REPORT),
            "audit": identity(AUDIT),
            "gate_summary": identity(GATES),
        },
    }
    write_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
