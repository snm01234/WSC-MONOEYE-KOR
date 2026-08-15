#!/usr/bin/env python3
"""Promote the stacked term-unify + bank59/name75/UI75 candidate to the main TIP.

ROM-only.  Live SaveRAM is preserved.  Intermediate test ROMs from this
workstream are removed after a successful install.
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from dialogue_runtime_contracts import audit_manifest, build_manifest
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, load_rom, stock_base
from normalize_ko_text import normalize_ko_text

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "bank59_enc5c_name75_candidate.wsc"
BUILD = PATCH / "bank59_enc5c_name75_candidate_report.json"
AUDIT = PATCH / "bank59_enc5c_name75_candidate_audit.json"
CATALOG = ROOT / "data/bank59_enc5c_name75_ko.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CONTRACT = SCRIPT / "dialogue_runtime_contracts.json"
SAFETY = PATCH / "dialogue_runtime_contract_candidate_safety.json"
PROMOTION = PATCH / "bank59_enc5c_name75_promotion_report.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_MAIN = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"
EXPECTED_CAND = "3eb5b66d0ba5b0d22ff39275039b95ab720e39743ebc61aedc544c066908de21"
EXPECTED_APPLIED = 40
EXPECTED_CHECKSUM = "6451"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

CLEANUP_ROMS = (
    PATCH / "bank59_enc5c_name75_candidate.wsc",
    ROOT / "sram/bank59_enc5c_name75_candidate.sav",
    PATCH / "term_unify_round2_candidate.wsc",
    ROOT / "sram/term_unify_round2_candidate.sav",
    PATCH / "term_unify_militia_the_o_candidate.wsc",
    ROOT / "sram/term_unify_militia_the_o_candidate.sav",
    PATCH / "weapon_enc_width13_candidate.wsc",
    ROOT / "sram/weapon_enc_width13_candidate.sav",
)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def req(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def atomic_bytes(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def atomic_json(path: Path, doc: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def checksum_pair(data: bytes) -> tuple[str, bool]:
    stored = int.from_bytes(data[-2:], "little")
    computed = sum(data[:-2]) & 0xFFFF
    return f"{stored:04X}", stored == computed


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def verify_catalog_renders(rom: bytes) -> None:
    catalog = load(CATALOG)
    rows = list(catalog.get("records") or [])
    req(len(rows) == EXPECTED_APPLIED, f"catalog count drifted: {len(rows)}")
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    sb = stock_base(rom)
    for row in rows:
        address = str(row["abs"]).upper()
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        payload_len = int(row["payload_len"])
        payload = rom[sb + int(address, 16) : sb + int(address, 16) + payload_len]
        req(payload[: len(prefix)] == prefix, f"{address} prefix drifted")
        actual = dictionary.expand(payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(row["ko"])).rstrip("\u3000 \t")
        req(actual == expected, f"{address} render mismatch: {actual!r} != {expected!r}")
        req(not any(is_japanese_character(ch) for ch in actual), f"{address} Japanese residual")
        req(rom[sb + int(address, 16) + payload_len] == 0, f"{address} terminator moved")


def run_checked(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)


def main() -> int:
    for path in (TIP, SAVE, CAND, BUILD, AUDIT, CATALOG, ORIGINAL):
        req(path.is_file(), f"missing required artifact: {path}")
    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drifted")

    cand = CAND.read_bytes()
    checksum, exact = checksum_pair(cand)
    req(exact and checksum == EXPECTED_CHECKSUM, f"candidate checksum invalid: {checksum}")

    build = load(BUILD)
    audit = load(AUDIT)
    req(build.get("ok") is True, "build report not ok")
    req(audit.get("ok") is True, "independent audit not ok")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build SHA drift")
    req(str((audit.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "audit SHA drift")
    req(str((build.get("main") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "build main SHA drift")
    req(int(build.get("applied_count") or -1) == EXPECTED_APPLIED, "applied count drifted")
    req(not audit.get("render_failures"), "audit still has render failures")
    req(not audit.get("invariance_failures"), "audit still has invariance failures")
    req(audit.get("runtime_banks_7A_7F_exact") is True, "runtime bank invariance failed")
    verify_catalog_renders(cand)

    save_before = sha(SAVE)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_bank59_enc5c_name75"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(
        backup_dir / "backup_manifest.json",
        {
            "schema_version": 1,
            "generated_by": "tools/promote_bank59_enc5c_name75_candidate.py",
            "reason": "pre_bank59_enc5c_name75",
            "main_sha256": EXPECTED_MAIN,
            "candidate_sha256": EXPECTED_CAND,
            "saveram_sha256": save_before,
        },
    )

    atomic_bytes(TIP, cand)
    try:
        req(sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(sha(SAVE) == save_before, "live SaveRAM changed during promotion")
        promoted = bytes(load_rom(TIP))
        verify_catalog_renders(promoted)
        stored, computed_ok = checksum_pair(promoted)
        req(computed_ok and stored == EXPECTED_CHECKSUM, "promoted checksum invalid")

        original = bytes(load_rom(ORIGINAL))
        manifest = build_manifest(original, promoted, target_path=TIP)
        safety = audit_manifest(promoted, manifest, target_path=TIP)
        req(
            safety.get("ok") is True
            and int((safety.get("counts") or {}).get("hard_failures", -1)) == 0,
            "postpromotion runtime contract audit failed",
        )
        atomic_json(CONTRACT, manifest)
        atomic_json(SAFETY, safety)
        run_checked("tools/test_dialogue_runtime_contracts.py")
        run_checked("tools/test_dialogue_runtime_safety_gate.py")
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    cleanup: list[dict[str, Any]] = []
    reclaimed = 0
    for path in CLEANUP_ROMS:
        if path.is_file():
            size = path.stat().st_size
            rel = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
            path.unlink()
            cleanup.append({"path": rel, "bytes": size})
            reclaimed += size

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_bank59_enc5c_name75_candidate.py",
        "ok": True,
        "promoted": True,
        "user_requested_promotion": True,
        "status": "promoted_term_unify_and_bank59_enc5c_name75",
        "pre_main_sha256": EXPECTED_MAIN,
        "post_main_sha256": EXPECTED_CAND,
        "checksum": EXPECTED_CHECKSUM,
        "applied": EXPECTED_APPLIED,
        "backup": identity(backup),
        "new_tip": identity(TIP),
        "live_saveram_before": save_before,
        "live_saveram_after": sha(SAVE),
        "saveram_preserved": sha(SAVE) == save_before,
        "runtime_contract_hard_failures": 0,
        "cleanup": {"files": cleanup, "reclaimed_bytes": reclaimed},
        "notes": [
            "Stacked candidate includes weapon/encyclopedia width-13, term unify (militia/Zabine/The O/Tolgus, Relena/Suono), bank59 titles/dialogue, name75 data-tail, and one UI75 mixed cleanup",
            "BADDICT records were not translated",
            "SaveRAM left untouched",
            "Workstream test ROM/SaveRAM pairs removed after install",
        ],
    }
    req(report["saveram_preserved"] is True, "SaveRAM not preserved")
    atomic_json(PROMOTION, report)
    print(
        json.dumps(
            {
                "ok": True,
                "main_sha256": EXPECTED_CAND,
                "checksum": EXPECTED_CHECKSUM,
                "applied": EXPECTED_APPLIED,
                "backup": report["backup"]["path"],
                "save_unchanged": True,
                "cleanup_reclaimed_bytes": reclaimed,
                "cleanup_files": len(cleanup),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
