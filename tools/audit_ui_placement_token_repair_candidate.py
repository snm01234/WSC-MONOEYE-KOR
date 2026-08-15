#!/usr/bin/env python3
"""Independently audit the bounded bank-75 placement-token repair candidate."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_ui_placement_token_repair_candidate import (
    EXPECTED_DIALOGUE_TOKEN_ABS,
    EXPECTED_MAIN_SHA256,
    EXT3_META_PATH,
    EXT_META_PATH,
    MAIN,
    OUT_REPORT,
    OUT_ROM,
    OUT_SAVE,
    PLACEMENT_KEEPER_SLOT,
    RECLAIMED_SLOT,
    ROM_SIZE,
    TBL_PATH,
    UI_RECORDS,
)
from monoeye_rom import BANK_SIZE, SEG_DICT, Tbl, read_encoded_z_safe, stock_base

OUT_AUDIT = ROOT / "out/patch/ui_placement_token_repair_audit.json"


class AuditError(RuntimeError):
    """Raised when the candidate violates the bounded repair contract."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def render_record(rom: bytes, dictionary: Any, tbl: Tbl, logical: int) -> tuple[bytes, str]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=64)
    if got is None:
        raise AuditError(f"unreadable record {logical:06X}")
    payload = bytes(got[0])
    return payload, dictionary.expand(payload, tbl).rstrip("\u3000 \t")


def token_sites(rom: bytes, slot: int) -> tuple[int, ...]:
    refs = external_occurrence_map(rom, ext3_aware=True, wanted={slot})
    return tuple(sorted(int(str(row["token_abs"]), 16) for row in refs.get(slot, [])))


def main() -> int:
    parent = MAIN.read_bytes()
    candidate = OUT_ROM.read_bytes()
    candidate_save = OUT_SAVE.read_bytes()
    report = json.loads(OUT_REPORT.read_text(encoding="utf-8"))

    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise AuditError("parent TIP identity drifted")
    if len(candidate) != ROM_SIZE:
        raise AuditError("candidate is not 16 MiB")
    if len(candidate_save) != 32_768:
        raise AuditError("candidate same-stem SaveRAM is not 32 KiB")
    if report.get("accepted") is not True or report.get("published") is not False:
        raise AuditError("build report state is not accepted/unpublished")
    if str((report.get("candidate_rom") or {}).get("sha256") or "") != sha256(candidate):
        raise AuditError("candidate hash does not match the build report")
    if str((report.get("parent_rom") or {}).get("sha256") or "") != sha256(parent):
        raise AuditError("parent hash does not match the build report")

    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    tbl = Tbl.load(TBL_PATH)
    parent_dict = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dict = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)

    expected_changed = {
        sb + int(row["token_abs"]) + delta
        for row in UI_RECORDS
        for delta in (0, 1)
    }
    allowed_changed = expected_changed | {len(candidate) - 2, len(candidate) - 1}
    changed = {
        index
        for index, (before, after) in enumerate(zip(parent, candidate))
        if before != after
    }
    if not expected_changed.issubset(changed):
        raise AuditError("not all three UI tokens changed")
    unexpected = changed - allowed_changed
    if unexpected:
        raise AuditError(
            "candidate has out-of-contract changes: "
            + ", ".join(f"{value:06X}" for value in sorted(unexpected)[:20])
        )

    dict_start = sb + SEG_DICT * BANK_SIZE
    dict_end = dict_start + BANK_SIZE
    if parent[dict_start:dict_end] != candidate[dict_start:dict_end]:
        raise AuditError("bank 5F is not byte-identical")
    for slot in (RECLAIMED_SLOT, PLACEMENT_KEEPER_SLOT):
        if parent_dict.ptrs[slot] != candidate_dict.ptrs[slot]:
            raise AuditError(f"slot {slot:04X} pointer changed")
        if bytes(parent_dict.raw_entry(slot)) != bytes(candidate_dict.raw_entry(slot)):
            raise AuditError(f"slot {slot:04X} payload changed")

    after_0021 = token_sites(candidate, RECLAIMED_SLOT)
    if after_0021 != tuple(sorted(EXPECTED_DIALOGUE_TOKEN_ABS)):
        raise AuditError(
            "slot 0021 consumer set is not the four approved dialogue sites: "
            + ", ".join(f"{value:06X}" for value in after_0021)
        )

    repaired_rows: list[dict[str, Any]] = []
    for row in UI_RECORDS:
        logical = int(row["record_abs"])
        parent_payload, parent_render = render_record(parent, parent_dict, tbl, logical)
        final_payload, final_render = render_record(candidate, candidate_dict, tbl, logical)
        if parent_payload != row["before_payload"] or parent_render != row["before_render"]:
            raise AuditError(f"parent defect proof drifted at {logical:06X}")
        if final_payload != row["after_payload"] or final_render != row["after_render"]:
            raise AuditError(f"candidate repair proof failed at {logical:06X}")
        if len(parent_payload) != len(final_payload):
            raise AuditError(f"record length changed at {logical:06X}")
        repaired_rows.append(
            {
                "record_abs": f"{logical:06X}",
                "token_abs": f"{int(row['token_abs']):06X}",
                "before": parent_render,
                "after": final_render,
                "length_preserved": True,
            }
        )

    dialogue_rows: list[dict[str, Any]] = []
    for token_abs in EXPECTED_DIALOGUE_TOKEN_ABS:
        if candidate[sb + token_abs : sb + token_abs + 2] != bytes.fromhex("F021"):
            raise AuditError(f"approved 티탄즈가 token changed at {token_abs:06X}")
        dialogue_rows.append(
            {
                "token_abs": f"{token_abs:06X}",
                "token_hex": "F021",
                "preserved": True,
            }
        )

    audit = {
        "generated_by": "tools/audit_ui_placement_token_repair_candidate.py",
        "accepted": True,
        "published": False,
        "parent_sha256": sha256(parent),
        "candidate_sha256": sha256(candidate),
        "candidate_save_sha256": sha256(candidate_save),
        "changed_byte_count": len(changed),
        "changed_positions": [f"{value:06X}" for value in sorted(changed)],
        "checks": {
            "bounded_three_token_retarget": True,
            "only_checksum_outside_targets": True,
            "bank5f_byte_identical": True,
            "slot0021_pointer_payload_preserved": True,
            "slot0573_pointer_payload_preserved": True,
            "slot0021_four_dialogue_consumers_preserved": True,
            "three_ui_records_render_as_placement": True,
            "record_lengths_preserved": True,
            "same_stem_saveram_present": True,
        },
        "repaired_records": repaired_rows,
        "preserved_titans_dialogue_tokens": dialogue_rows,
        "visual_check": {
            "required": True,
            "screen": "intermission speed/unit-status UI",
            "expected_visible_word": "배치",
        },
    }
    atomic_write(
        OUT_AUDIT,
        (json.dumps(audit, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
