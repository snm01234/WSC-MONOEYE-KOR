#!/usr/bin/env python3
"""Promote the UI75 walker-noise rollback to the main TIP.

ROM-only.  Live SaveRAM is preserved.  Bank59 titles and name75 tails stay.
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
from monoeye_rom import Tbl, le16, load_rom, slice_expansion_bank, stock_base
from patch_3byte_dict_token import bank_local_for_index

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "ui75_nonsentence_rollback_candidate.wsc"
BUILD = PATCH / "ui75_nonsentence_rollback_candidate_report.json"
AUDIT = PATCH / "ui75_nonsentence_rollback_candidate_audit.json"
CATALOG = ROOT / "data/ui75_nonsentence_rollback.json"
BANK59_CATALOG = ROOT / "data/bank59_enc5c_name75_ko.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CONTRACT = SCRIPT / "dialogue_runtime_contracts.json"
SAFETY = PATCH / "dialogue_runtime_contract_candidate_safety.json"
PROMOTION = PATCH / "ui75_nonsentence_rollback_promotion_report.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_MAIN = "3eb5b66d0ba5b0d22ff39275039b95ab720e39743ebc61aedc544c066908de21"
EXPECTED_CAND = "2cb645e4bb700db4c111041f8cfbb9c65b8a0b937b8877fe9f76cc92ed3a1dda"
EXPECTED_CHECKSUM = "6350"
EXPECTED_RESTORE = bytes.fromhex("017701F1C2")
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EMPTY_PTR = 0x2000


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def checksum_pair(data: bytes) -> tuple[str, bool]:
    stored = int.from_bytes(data[-2:], "little")
    computed = sum(data[:-2]) & 0xFFFF
    return f"{stored:04X}", stored == computed


def verify_restored(rom: bytes) -> None:
    catalog = load(CATALOG)
    record = dict(catalog.get("record") or {})
    sb = stock_base(rom)
    logical = int(str(record["abs"]), 16)
    payload = rom[sb + logical : sb + logical + 5]
    req(payload == EXPECTED_RESTORE, f"75B2DD not restored: {payload.hex().upper()}")
    req(rom[sb + logical + 5] == 0, "75B2DD terminator moved")
    req(rom.find(bytes.fromhex("E51833F8")) < 0, "ext3 portal still present")
    slot = int(str(record["ext3_slot"]), 16)
    seg, local = bank_local_for_index(slot)
    req(le16(slice_expansion_bank(rom, seg), local * 2) == EMPTY_PTR, "slot 043F8 not emptied")
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    restored = dictionary.expand(payload, tbl)
    req(restored == "\u3000ウ\u3000이동", f"unexpected restore render: {restored!r}")
    bank59 = load(BANK59_CATALOG)
    kept = 0
    for row in bank59.get("records") or []:
        address = str(row.get("abs") or "").upper()
        req(address != "75B2DD", "bank59 catalog still contains rolled-back UI75 row")
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        payload_n = int(row["payload_len"])
        body = rom[sb + int(address, 16) : sb + int(address, 16) + payload_n]
        actual = dictionary.expand(body[len(prefix) :], tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        req(actual == expected, f"{address} render drifted: {actual!r}")
        kept += 1
    req(kept == 39, f"kept record count drifted: {kept}")


def run_checked(*args: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)


def main() -> int:
    for path in (TIP, SAVE, CAND, BUILD, AUDIT, CATALOG, BANK59_CATALOG, ORIGINAL):
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
    req(int(audit.get("kept_bank59_name75_records") or -1) == 39, "kept record count drifted")
    req(not audit.get("render_failures"), "audit still has render failures")
    req(not audit.get("failures"), "audit still has failures")
    req(audit.get("runtime_banks_7A_7F_exact") is True, "runtime bank invariance failed")
    verify_restored(cand)

    save_before = sha(SAVE)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_ui75_nonsentence_rollback"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_MAIN, "rollback backup verification failed")
    atomic_json(
        backup_dir / "backup_manifest.json",
        {
            "schema_version": 1,
            "generated_by": "tools/promote_ui75_nonsentence_rollback_candidate.py",
            "reason": "pre_ui75_nonsentence_rollback",
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
        verify_restored(promoted)
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

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_ui75_nonsentence_rollback_candidate.py",
        "ok": True,
        "promoted": True,
        "user_requested_promotion": True,
        "status": "promoted_ui75_nonsentence_rollback",
        "pre_main_sha256": EXPECTED_MAIN,
        "post_main_sha256": EXPECTED_CAND,
        "checksum": EXPECTED_CHECKSUM,
        "restored_abs": "75B2DD",
        "restored_payload_hex": EXPECTED_RESTORE.hex().upper(),
        "restored_render": "\u3000ウ\u3000이동",
        "ext3_slot_emptied": "043F8",
        "kept_bank59_name75_records": 39,
        "backup": identity(backup),
        "new_tip": identity(TIP),
        "live_saveram_before": save_before,
        "live_saveram_after": sha(SAVE),
        "saveram_preserved": sha(SAVE) == save_before,
        "runtime_contract_hard_failures": 0,
        "notes": [
            "Rolled back UI75 walker-noise 75B2DD only",
            "Bank59 titles and name75 data-tail sentences were kept",
            "攻/분 glyph remap and map-name padding were kept",
            "SaveRAM left untouched",
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
                "restored": "75B2DD",
                "backup": report["backup"]["path"],
                "save_unchanged": True,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
