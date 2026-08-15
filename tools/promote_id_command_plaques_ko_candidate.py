#!/usr/bin/env python3
"""Promote the runtime-approved Korean ID-plaque candidate to the main TIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"D:\monoeye")
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "id_command_plaques_ko_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/id_command_plaques_ko_candidate.sav"
SPEC = ROOT / "data/id_command_plaque_translations_ko.json"
BUILD_REPORT = PATCH / "id_command_plaques_ko_candidate_report.json"
RUNTIME_AUDIT = PATCH / "id_command_plaques_ko_candidate_runtime_audit.json"
PROMOTION_REPORT = PATCH / "id_command_plaques_ko_promotion_report.json"
POST_AUDIT = PATCH / "id_command_plaques_ko_postpromotion_audit.json"

EXPECTED_TIP_SHA = "87bd754d3f4af65f3d02a274d94e962e0bf2f0313c491096407dfc9c8d1a4f93"
EXPECTED_CANDIDATE_SHA = "9ba9804dac603d84efe75bff6efecfebd2b55ef7bd602671c375f97791f61d75"
EXPECTED_MAIN_SAVE_SHA = "589f47d18cbe245e544f62a92542eedaed87895794aaf072b3071d7442cde4a4"
EXPECTED_CANDIDATE_SAVE_SHA = "d32ce79c6f7fbe9825449a03ca47f3849b97c0658881bf31885baad077717602"
EXPECTED_SPEC_SHA = "8b7e2bed74a42ee56aa0fdb0645dea26e0db4c21f452918c4d5a0fcf06686765"
EXPECTED_BUILD_REPORT_SHA = "076805ed6167a8e96f9328fe1beb114c944e36577a271effccd8aa660ec1e805"
EXPECTED_RUNTIME_AUDIT_SHA = "eae74194198fc45c0404af6395397b031a3cb94c8b0b9f3fa8791212970ab30d"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_CHECKSUM = "5DB7"


class PromotionError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if sha is not None and digest(path).lower() != sha.lower():
        raise PromotionError(f"SHA-256 drift: {path}")


def checksum(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    computed = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    return {"computed": f"{computed:04X}", "stored": f"{stored:04X}", "valid": computed == stored}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def atomic_copy(source: Path, target: Path, expected_size: int, expected_sha: str) -> None:
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle, 1024 * 1024)
        target_handle.flush()
        os.fsync(target_handle.fileno())
    require(temporary, size=expected_size, sha=expected_sha)
    os.replace(temporary, target)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate() -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, sha=EXPECTED_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE_SHA)
    require(TIP_SAVE, size=SAVE_SIZE, sha=EXPECTED_MAIN_SAVE_SHA)
    require(CANDIDATE_SAVE, size=SAVE_SIZE, sha=EXPECTED_CANDIDATE_SAVE_SHA)
    require(SPEC, sha=EXPECTED_SPEC_SHA)
    require(BUILD_REPORT, sha=EXPECTED_BUILD_REPORT_SHA)
    require(RUNTIME_AUDIT, sha=EXPECTED_RUNTIME_AUDIT_SHA)

    candidate_checksum = checksum(CANDIDATE)
    if not candidate_checksum["valid"] or candidate_checksum["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum mismatch: {candidate_checksum}")

    build = load_json(BUILD_REPORT)
    runtime = load_json(RUNTIME_AUDIT)
    if build.get("ok") is not True or not all((build.get("checks") or {}).values()):
        raise PromotionError("candidate build report did not pass every gate")
    if runtime.get("ok") is not True or not all((runtime.get("checks") or {}).values()):
        raise PromotionError("candidate runtime audit did not pass every gate")
    if (build.get("candidate") or {}).get("sha256", "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build report candidate binding mismatch")
    if (runtime.get("candidate") or {}).get("sha256", "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("runtime report candidate binding mismatch")
    if (runtime.get("post_state_rom_reload") or {}).get("mismatch_pixels") != 0:
        raise PromotionError("runtime pixel-exact proof missing")
    if (runtime.get("post_state_rom_reload") or {}).get("pixels_compared") != 768:
        raise PromotionError("runtime proof geometry mismatch")

    spec = load_json(SPEC)
    plaques = spec.get("plaques") or []
    preserved = [row for row in plaques if row.get("action") == "preserve_source"]
    if len(plaques) != 24 or len(preserved) != 1:
        raise PromotionError("translation spec inventory mismatch")
    if preserved[0].get("logical") != "4C44D4" or preserved[0].get("ko") != "↑LEVEL":
        raise PromotionError("↑LEVEL preservation contract mismatch")

    return {
        "current_tip": identity(TIP),
        "approved_candidate": identity(CANDIDATE),
        "candidate_checksum": candidate_checksum,
        "main_saveram": identity(TIP_SAVE),
        "runtime_modified_candidate_saveram": identity(CANDIDATE_SAVE),
        "spec": identity(SPEC),
        "build_report": identity(BUILD_REPORT),
        "runtime_audit": identity(RUNTIME_AUDIT),
        "build_checks": build["checks"],
        "runtime_checks": runtime["checks"],
        "runtime_pixel_proof": runtime["post_state_rom_reload"],
        "promotion_scope": "WSC TIP only; main SaveRAM and runtime-modified candidate SaveRAM remain immutable",
    }


def post_audit(
    backup_rom: Path,
    main_save_before: dict[str, Any],
    candidate_save_before: dict[str, Any],
) -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, sha=EXPECTED_CANDIDATE_SHA)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE_SHA)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_TIP_SHA)
    tip_checksum = checksum(TIP)
    checks = {
        "main_tip_matches_approved_candidate": digest(TIP) == EXPECTED_CANDIDATE_SHA,
        "main_tip_checksum_valid": tip_checksum["valid"] and tip_checksum["stored"] == EXPECTED_CHECKSUM,
        "rollback_rom_preserved": digest(backup_rom) == EXPECTED_TIP_SHA,
        "main_saveram_unchanged": identity(TIP_SAVE) == main_save_before,
        "runtime_candidate_saveram_unchanged": identity(CANDIDATE_SAVE) == candidate_save_before,
        "candidate_rom_unchanged": digest(CANDIDATE) == EXPECTED_CANDIDATE_SHA,
        "build_report_unchanged": digest(BUILD_REPORT) == EXPECTED_BUILD_REPORT_SHA,
        "runtime_audit_unchanged": digest(RUNTIME_AUDIT) == EXPECTED_RUNTIME_AUDIT_SHA,
    }
    if not all(checks.values()):
        raise PromotionError(f"post-promotion audit failed: {checks}")
    result = {
        "schema_version": 1,
        "generated_by": "tools/promote_id_command_plaques_ko_candidate.py",
        "ok": True,
        "main_tip": identity(TIP),
        "main_tip_checksum": tip_checksum,
        "approved_candidate": identity(CANDIDATE),
        "rollback_rom": identity(backup_rom),
        "main_saveram_before": main_save_before,
        "main_saveram_after": identity(TIP_SAVE),
        "runtime_candidate_saveram_before": candidate_save_before,
        "runtime_candidate_saveram_after": identity(CANDIDATE_SAVE),
        "checks": checks,
    }
    atomic_json(POST_AUDIT, result)
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_id_command_plaques_ko"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require(backup_rom, size=ROM_SIZE, sha=EXPECTED_TIP_SHA)

    old_tip = identity(TIP)
    main_save_before = identity(TIP_SAVE)
    candidate_save_before = identity(CANDIDATE_SAVE)
    try:
        atomic_copy(CANDIDATE, TIP, ROM_SIZE, EXPECTED_CANDIDATE_SHA)
        post = post_audit(backup_rom, main_save_before, candidate_save_before)
    except Exception:
        atomic_copy(backup_rom, TIP, ROM_SIZE, EXPECTED_TIP_SHA)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_id_command_plaques_ko_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": old_tip,
        "new_tip": identity(TIP),
        "approved_candidate": identity(CANDIDATE),
        "rollback_rom": identity(backup_rom),
        "validation": validation,
        "postpromotion_audit": identity(POST_AUDIT),
        "postpromotion_checks": post["checks"],
        "saveram_policy": "main and runtime-modified candidate SaveRAM files were preserved byte-identically",
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
