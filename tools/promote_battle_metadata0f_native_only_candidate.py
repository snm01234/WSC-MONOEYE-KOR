#!/usr/bin/env python3
"""Promote the user-authorized metadata=0F 35-record native-only battle fix."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from dialogue_runtime_contracts import audit_manifest, build_manifest
from monoeye_rom import load_rom

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
TIP = PATCH / "monoeye_ko_expanded.wsc"
CAND = PATCH / "battle_metadata0f_native_only_candidate.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = PATCH / "battle_metadata0f_native_only_candidate_report.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CONTRACT = SCRIPT / "dialogue_runtime_contracts.json"
SAFETY = PATCH / "dialogue_runtime_contract_candidate_safety.json"
PROMOTION = PATCH / "battle_metadata0f_native_only_promotion_report.json"
EXPECTED_MAIN = "b6192a05fbfc37dc021ff2ccc9f1ee89ee50c0375c6ddfe807edc381f20e0662"
EXPECTED_CAND = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def atomic_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main() -> int:
    main = TIP.read_bytes()
    cand = CAND.read_bytes()
    save = SAVE.read_bytes()
    if len(main) != ROM_SIZE or sha_bytes(main) != EXPECTED_MAIN:
        raise RuntimeError(f"main identity drifted: {sha_bytes(main)}")
    if len(cand) != ROM_SIZE or sha_bytes(cand) != EXPECTED_CAND:
        raise RuntimeError(f"candidate identity drifted: {sha_bytes(cand)}")
    if len(save) != SAVE_SIZE:
        raise RuntimeError("live SaveRAM size drifted")
    stored = int.from_bytes(cand[-2:], "little")
    computed = sum(cand[:-2]) & 0xFFFF
    if stored != computed:
        raise RuntimeError("candidate checksum invalid")
    build = json.loads(REPORT.read_text(encoding="utf-8"))
    counts = build.get("counts") or {}
    if (
        int(counts.get("targets", -1)) != 35
        or int(counts.get("unique_phrases", -1)) != 26
        or int(counts.get("top_slots", -1)) != 26
        or int(counts.get("helper_slots", -1)) != 63
        or int(counts.get("runtime_contract_hard_failures", -1)) != 0
        or int(counts.get("unaccounted_diff_runs", -1)) != 0
    ):
        raise RuntimeError(f"candidate report gate failed: {counts}")

    save_before = sha_bytes(save)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_battle_metadata0f_native_only"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    if sha(backup) != EXPECTED_MAIN:
        raise RuntimeError("rollback backup verification failed")
    atomic_json(backup_dir / "backup_manifest.json", {
        "schema_version": 1,
        "reason": "pre_battle_metadata0f_native_only",
        "main_sha256": EXPECTED_MAIN,
        "candidate_sha256": EXPECTED_CAND,
        "saveram_sha256": save_before,
    })

    # ROM-only promotion.  Live SaveRAM is intentionally untouched.
    atomic_bytes(TIP, cand)
    if sha(TIP) != EXPECTED_CAND:
        raise RuntimeError("postpromotion TIP identity mismatch")
    if sha(SAVE) != save_before:
        raise RuntimeError("live SaveRAM changed during ROM-only promotion")

    # Refresh the canonical runtime contract and safety artifact to the new TIP.
    original = bytes(load_rom(ORIGINAL))
    promoted = bytes(load_rom(TIP))
    manifest = build_manifest(original, promoted, target_path=TIP)
    safety = audit_manifest(promoted, manifest, target_path=TIP)
    if not safety.get("ok") or int((safety.get("counts") or {}).get("hard_failures", -1)) != 0:
        raise RuntimeError("postpromotion runtime contract audit failed")
    atomic_json(CONTRACT, manifest)
    atomic_json(SAFETY, safety)

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_battle_metadata0f_native_only_candidate.py",
        "status": "promoted",
        "user_authorized": True,
        "pre_main_sha256": EXPECTED_MAIN,
        "post_main_sha256": EXPECTED_CAND,
        "checksum": f"{stored:04X}",
        "targets": 35,
        "unique_phrases": 26,
        "top_native_slots": 26,
        "helper_native_slots": 63,
        "helper_depth": int(counts.get("helper_depth", -1)),
        "runtime_contract_hard_failures": int(safety["counts"]["hard_failures"]),
        "backup": str(backup.relative_to(ROOT)).replace("\\", "/"),
        "saveram_sha256_before": save_before,
        "saveram_sha256_after": sha(SAVE),
        "saveram_preserved": sha(SAVE) == save_before,
    }
    atomic_json(PROMOTION, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
