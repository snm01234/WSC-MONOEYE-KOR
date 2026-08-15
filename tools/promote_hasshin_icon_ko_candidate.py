#!/usr/bin/env python3
"""Promote the statically verified 발진 icon candidate into the main TIP."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_henkei_icon_ko_candidate as common  # noqa: E402

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "hasshin_icon_ko_candidate.wsc"
BUILD_REPORT = PATCH / "hasshin_icon_ko_candidate_report.json"
PROMOTION_REPORT = PATCH / "hasshin_icon_ko_candidate_promotion_report.json"
POST_AUDIT = PATCH / "hasshin_icon_ko_candidate_postpromotion_audit.json"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checksum(data: bytes) -> dict[str, object]:
    computed = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    return {"computed": f"{computed:04X}", "stored": f"{stored:04X}", "valid": computed == stored}


def main() -> int:
    for path, size in ((TIP, ROM_SIZE), (CANDIDATE, ROM_SIZE), (SAVE, SAVE_SIZE)):
        if not path.is_file() or path.stat().st_size != size:
            raise PromotionError(f"missing or size-drifted input: {path}")
    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    if not report.get("ok") or not report.get("diff", {}).get("allowlist_clean"):
        raise PromotionError("candidate report is not clean")
    if digest(parent) != report["parent"]["sha256"]:
        raise PromotionError("current main no longer matches candidate parent")
    if digest(candidate) != report["candidate"]["sha256"]:
        raise PromotionError("candidate SHA drift")
    if not all(report.get("guards", {}).values()):
        raise PromotionError("candidate build guard failed")
    physical = int(report["patch"]["physical"], 16)
    length = int(report["patch"]["bytes"])
    source_sha = report["patch"]["source_sha256"]
    target_sha = report["patch"]["target_sha256"]
    if digest(parent[physical : physical + length]) != source_sha:
        raise PromotionError("source icon drift")
    if digest(candidate[physical : physical + length]) != target_sha:
        raise PromotionError("candidate icon drift")
    candidate_checksum = checksum(candidate)
    if not candidate_checksum["valid"] or candidate_checksum["stored"] != report["candidate"]["ws_checksum"]:
        raise PromotionError("candidate checksum invalid")

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_hasshin_icon_ko"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if digest(backup_rom.read_bytes()) != digest(parent):
        raise PromotionError("rollback backup mismatch")

    try:
        common.atomic_bytes(TIP, candidate)
        promoted = TIP.read_bytes()
        checks = {
            "tip_matches_candidate": digest(promoted) == digest(candidate),
            "checksum_valid": bool(checksum(promoted)["valid"]),
            "target_icon_exact": digest(promoted[physical : physical + length]) == target_sha,
            "rollback_rom_exact": digest(backup_rom.read_bytes()) == digest(parent),
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
        }
        if not all(checks.values()):
            raise PromotionError(f"post-promotion audit failed: {checks}")
    except Exception:
        common.atomic_bytes(TIP, parent)
        raise

    report["status"] = "promoted_to_current_main"
    report["promotion"] = "promoted"
    report["promoted_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    common.atomic_json(BUILD_REPORT, report)
    audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_hasshin_icon_ko_candidate.py",
        "ok": True,
        "tip": common.identity(TIP),
        "tip_checksum": checksum(TIP.read_bytes()),
        "rollback_rom": common.identity(backup_rom),
        "checks": checks,
    }
    common.atomic_json(POST_AUDIT, audit)
    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_hasshin_icon_ko_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": report["promoted_at"],
        "old_tip": {"path": common.rel(TIP), "size": len(parent), "sha256": digest(parent)},
        "new_tip": common.identity(TIP),
        "backup_rom": common.identity(backup_rom),
        "postpromotion_checks": checks,
        "main_saveram_policy": "live main SaveRAM remained byte-identical",
    }
    common.atomic_json(PROMOTION_REPORT, promotion)
    print(json.dumps(promotion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
