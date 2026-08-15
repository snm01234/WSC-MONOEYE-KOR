#!/usr/bin/env python3
"""Promote the user-verified bank-75 ``배치`` token repair to the main TIP.

The promotion contract is ROM-only.  The current live
``monoeye_ko_expanded.sav`` remains untouched and is not restored or pinned to a
historic hash.  Before replacement, the current TIP is copied to a timestamped
rollback directory and verified.  The candidate is then atomically installed,
re-audited at its final path, and the redundant candidate ROM/SaveRAM pair is
removed.
"""
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

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from analyze_p2_duplicate_detachment import external_occurrence_map  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_ui_placement_token_repair_candidate import (  # noqa: E402
    EXPECTED_DIALOGUE_TOKEN_ABS,
    EXT3_META_PATH,
    EXT_META_PATH,
    PLACEMENT_KEEPER_SLOT,
    RECLAIMED_SLOT,
    TBL_PATH,
    UI_RECORDS,
)
from monoeye_rom import BANK_SIZE, SEG_DICT, Tbl, read_encoded_z_safe, stock_base  # noqa: E402

TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "ui_placement_token_repair_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/ui_placement_token_repair_candidate.sav"
BUILD_REPORT = PATCH / "ui_placement_token_repair_report.json"
AUDIT_REPORT = PATCH / "ui_placement_token_repair_audit.json"
POSTPROMOTION_AUDIT = PATCH / "ui_placement_token_repair_postpromotion_audit.json"
PROMOTION_REPORT = PATCH / "ui_placement_token_repair_promotion_report.json"

PARENT_SHA = "971665a2fa5d571dd04500b520fb41bcc7d4929e571ca2632c9253a4e51b35ae"
CANDIDATE_SHA = "31acde8c486b5ba13bc00b74ae019444608051478c5e0b874516e74f4cab8eb6"
ROM_SIZE = 16_777_216


class PromotionError(RuntimeError):
    """Raised when validation or the atomic promotion transaction fails."""


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PromotionError(f"missing report: {rel(path)}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionError(f"invalid report root: {rel(path)}")
    return value


def require_rom(path: Path, expected_sha: str) -> None:
    if not path.is_file() or path.stat().st_size != ROM_SIZE:
        raise PromotionError(f"invalid ROM: {rel(path)}")
    actual = digest(path)
    if actual != expected_sha:
        raise PromotionError(f"ROM SHA drifted for {rel(path)}: {actual}")


def token_sites(rom: bytes, slot: int) -> tuple[int, ...]:
    refs = external_occurrence_map(rom, ext3_aware=True, wanted={slot})
    return tuple(sorted(int(str(row["token_abs"]), 16) for row in refs.get(slot, [])))


def render_record(rom: bytes, dictionary: Any, tbl: Tbl, logical: int) -> tuple[bytes, str]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=64)
    if got is None:
        raise PromotionError(f"unreadable post-promotion record: {logical:06X}")
    payload = bytes(got[0])
    rendered = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
    return payload, rendered


def validate() -> dict[str, Any]:
    require_rom(TIP, PARENT_SHA)
    require_rom(CANDIDATE, CANDIDATE_SHA)
    if not TIP_SAVE.is_file() or TIP_SAVE.stat().st_size != 32_768:
        raise PromotionError("live main 32 KiB SaveRAM is missing")
    if not CANDIDATE_SAVE.is_file() or CANDIDATE_SAVE.stat().st_size != 32_768:
        raise PromotionError("candidate same-stem 32 KiB SaveRAM is missing")

    build = load_json(BUILD_REPORT)
    audit = load_json(AUDIT_REPORT)
    if build.get("accepted") is not True or build.get("published") is not False:
        raise PromotionError("build report is not accepted/unpublished")
    if ((build.get("parent_rom") or {}).get("sha256")) != PARENT_SHA:
        raise PromotionError("build parent binding mismatch")
    if ((build.get("candidate_rom") or {}).get("sha256")) != CANDIDATE_SHA:
        raise PromotionError("build candidate binding mismatch")
    if build.get("changed_byte_count") != 8:
        raise PromotionError("build changed-byte count mismatch")
    cause = build.get("cause") or {}
    if cause.get("reclaimed_slot") != "0021" or cause.get("placement_keeper_slot") != "0573":
        raise PromotionError("build slot binding mismatch")
    proof = build.get("consumer_proof") or {}
    if proof.get("approved_titans_dialogue_consumers_preserved") != 4:
        raise PromotionError("approved 티탄즈가 consumer count mismatch")
    if proof.get("hidden_ui_consumers_retargeted") != 3:
        raise PromotionError("hidden UI consumer count mismatch")
    invariants = build.get("invariants") or {}
    required_invariants = (
        "bank5f_byte_identical",
        "dictionary_pointers_byte_identical",
        "dictionary_payloads_byte_identical",
        "record_lengths_unchanged",
        "terminators_unchanged",
        "runtime_code_unchanged",
        "ext3_data_unchanged",
        "main_tip_unchanged",
        "main_saveram_unchanged",
    )
    failed = [name for name in required_invariants if invariants.get(name) is not True]
    if failed:
        raise PromotionError("build invariant failure: " + ", ".join(failed))

    if audit.get("accepted") is not True or audit.get("published") is not False:
        raise PromotionError("independent audit is not accepted/unpublished")
    if audit.get("parent_sha256") != PARENT_SHA:
        raise PromotionError("audit parent binding mismatch")
    if audit.get("candidate_sha256") != CANDIDATE_SHA:
        raise PromotionError("audit candidate binding mismatch")
    checks = audit.get("checks") or {}
    failed_checks = [name for name, passed in checks.items() if passed is not True]
    if failed_checks:
        raise PromotionError("independent audit failure: " + ", ".join(failed_checks))
    if len(audit.get("repaired_records") or []) != 3:
        raise PromotionError("independent audit repair count mismatch")
    if len(audit.get("preserved_titans_dialogue_tokens") or []) != 4:
        raise PromotionError("independent audit dialogue count mismatch")

    return {
        "parent_sha256": PARENT_SHA,
        "candidate_sha256": CANDIDATE_SHA,
        "runtime_verified_by_user": ["배치"],
        "repaired_ui_records": 3,
        "preserved_titans_dialogue_consumers": 4,
        "changed_bytes_including_checksum": 8,
        "saveram_policy": "mutable_live_test_data_left_untouched",
    }


def atomic_replace_tip() -> None:
    temporary = TIP.with_name(f".{TIP.name}.ui-placement-promote.tmp")
    temporary.unlink(missing_ok=True)
    with CANDIDATE.open("rb") as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    require_rom(temporary, CANDIDATE_SHA)
    os.replace(temporary, TIP)


def postpromotion_audit(parent_backup: Path) -> dict[str, Any]:
    require_rom(TIP, CANDIDATE_SHA)
    before = parent_backup.read_bytes()
    final = TIP.read_bytes()
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    tbl = Tbl.load(TBL_PATH)
    before_dict = make_dictionary_ext3(before, ext_meta, ext3_meta)
    final_dict = make_dictionary_ext3(final, ext_meta, ext3_meta)
    sb = stock_base(final)

    dict_start = sb + SEG_DICT * BANK_SIZE
    dict_end = dict_start + BANK_SIZE
    if before[dict_start:dict_end] != final[dict_start:dict_end]:
        raise PromotionError("post-promotion bank 5F is not byte-identical")
    for slot in (RECLAIMED_SLOT, PLACEMENT_KEEPER_SLOT):
        if before_dict.ptrs[slot] != final_dict.ptrs[slot]:
            raise PromotionError(f"post-promotion slot {slot:04X} pointer changed")
        if bytes(before_dict.raw_entry(slot)) != bytes(final_dict.raw_entry(slot)):
            raise PromotionError(f"post-promotion slot {slot:04X} payload changed")

    final_0021 = token_sites(final, RECLAIMED_SLOT)
    if final_0021 != tuple(sorted(EXPECTED_DIALOGUE_TOKEN_ABS)):
        raise PromotionError("post-promotion slot 0021 consumer proof failed")

    records: list[dict[str, Any]] = []
    for row in UI_RECORDS:
        logical = int(row["record_abs"])
        payload, rendered = render_record(final, final_dict, tbl, logical)
        if payload != row["after_payload"] or rendered != row["after_render"]:
            raise PromotionError(f"post-promotion UI render failed at {logical:06X}")
        records.append(
            {
                "record_abs": f"{logical:06X}",
                "token_abs": f"{int(row['token_abs']):06X}",
                "payload_hex": payload.hex().upper(),
                "render": rendered,
                "ok": True,
            }
        )

    changed = {
        index for index, (old, new) in enumerate(zip(before, final)) if old != new
    }
    expected = {
        sb + int(row["token_abs"]) + delta
        for row in UI_RECORDS
        for delta in (0, 1)
    }
    allowed = expected | {len(final) - 2, len(final) - 1}
    if not expected.issubset(changed) or not changed.issubset(allowed):
        raise PromotionError("post-promotion bounded diff proof failed")

    audit = {
        "generated_by": "tools/promote_ui_placement_token_repair_candidate.py",
        "ok": True,
        "tip": identity(TIP),
        "rollback_source": identity(parent_backup),
        "checks": {
            "tip_matches_verified_candidate": True,
            "bounded_three_token_retarget": True,
            "only_checksum_outside_targets": True,
            "bank5f_byte_identical": True,
            "slot0021_pointer_payload_preserved": True,
            "slot0573_pointer_payload_preserved": True,
            "slot0021_four_dialogue_consumers_preserved": True,
            "three_ui_records_render_as_placement": True,
        },
        "changed_byte_count": len(changed),
        "changed_positions": [f"{value:06X}" for value in sorted(changed)],
        "records": records,
        "slot0021_consumers": [f"{value:06X}" for value in final_0021],
    }
    atomic_json(POSTPROMOTION_AUDIT, audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    validation = validate()
    if not args.commit:
        print(
            json.dumps(
                {"mode": "dry_run", "ok": True, "validation": validation},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_ui_placement_token_repair"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    require_rom(backup_rom, PARENT_SHA)

    candidate_before_cleanup = identity(CANDIDATE)
    main_save_before = {
        "path": rel(TIP_SAVE),
        "size": TIP_SAVE.stat().st_size,
        "action": "left_untouched",
    }

    atomic_replace_tip()
    require_rom(TIP, CANDIDATE_SHA)
    final_audit = postpromotion_audit(backup_rom)

    CANDIDATE.unlink()
    candidate_save_removed = False
    if CANDIDATE_SAVE.exists():
        CANDIDATE_SAVE.unlink()
        candidate_save_removed = True

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_ui_placement_token_repair_candidate.py",
        "mode": "commit",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_tip": {"size": ROM_SIZE, "sha256": PARENT_SHA},
        "new_tip": identity(TIP),
        "backup_rom": identity(backup_rom),
        "validation": validation,
        "postpromotion_audit": identity(POSTPROMOTION_AUDIT),
        "postpromotion_checks": final_audit["checks"],
        "candidate_before_cleanup": candidate_before_cleanup,
        "candidate_rom_removed": True,
        "candidate_save_removed": candidate_save_removed,
        "main_saveram": main_save_before,
        "evidence": {
            "build_report": identity(BUILD_REPORT),
            "independent_audit": identity(AUDIT_REPORT),
            "postpromotion_audit": identity(POSTPROMOTION_AUDIT),
        },
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
