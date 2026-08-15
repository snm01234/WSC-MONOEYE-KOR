#!/usr/bin/env python3
"""Independent audit for ext3_zero_middle_rehome_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import detect_ext3_alias_page_count, load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import _walk_zstring_range  # noqa: E402
from mixed_residual_reference_union import _reference_scopes, iter_token_refs_with_offsets  # noqa: E402
from monoeye_rom import (  # noqa: E402
    EXT3_INDEX_BASE,
    Tbl,
    dict_token_safe_in_zstring,
    read_encoded_z_safe,
    stock_base,
)
from patch_3byte_dict_token import list_free_ext3_indices  # noqa: E402

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/ext3_zero_middle_rehome_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/ext3_zero_middle_rehome_candidate.sav"
REPORT = ROOT / "out/patch/ext3_zero_middle_rehome_audit.json"
BUILD_REPORT = ROOT / "out/patch/ext3_zero_middle_rehome_report.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_PARENT = "30313f387660c4d09ce139a7fc4d0ce14962321d2df49ea1914021c9d2109f24"
EXPECTED_CANDIDATE = "0656db10b4146b03fd1d3d38dfaaf9fade33ab71bf9cd1f37a5b76fd27f1f606"
QUARANTINE_LO = 0x62D650
QUARANTINE_HI = 0x630000
ANCHORS = (0x610005, 0x610025, 0x61004A, 0x61005F)


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unsafe(index: int) -> bool:
    raw = index - EXT3_INDEX_BASE
    return index >= EXT3_INDEX_BASE and ((raw >> 8) & 0xFF) == 0 and (raw & 0xFF) != 0


def scan(rom: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for region, lo, hi, max_len in _reference_scopes():
        for logical, payload, kind in _walk_zstring_range(rom, lo, hi, region=region, max_len=max_len):
            for index, length, offset in iter_token_refs_with_offsets(payload, ext3_aware=True):
                if length == 4 and unsafe(index):
                    rows.append(
                        {
                            "record_abs": logical,
                            "token_abs": logical + offset,
                            "index": index,
                            "region": region,
                            "kind": kind,
                        }
                    )
    return sorted(rows, key=lambda row: int(row["token_abs"]))


def main() -> int:
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    if sha(parent) != EXPECTED_PARENT:
        raise AuditError("parent identity drifted")
    if sha(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("candidate identity drifted")
    if CANDIDATE_SAVE.stat().st_size != 32_768:
        raise AuditError("candidate SaveRAM missing/wrong size")

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    tbl = Tbl.load(TBL)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    base = stock_base(parent)

    before = scan(parent)
    after = scan(candidate)
    after_live = [row for row in after if not (QUARANTINE_LO <= int(row["token_abs"]) < QUARANTINE_HI)]
    after_quarantine = [row for row in after if QUARANTINE_LO <= int(row["token_abs"]) < QUARANTINE_HI]

    anchor_rows: list[dict[str, Any]] = []
    for logical in ANCHORS:
        p = read_encoded_z_safe(parent, base + logical, max_len=256)
        c = read_encoded_z_safe(candidate, base + logical, max_len=256)
        if p is None or c is None:
            raise AuditError(f"anchor unreadable: {logical:06X}")
        p_payload = bytes(p[0])
        c_payload = bytes(c[0])
        p_pos = p_payload.find(bytes.fromhex("E51800"))
        c_pos = c_payload.find(bytes.fromhex("E518"))
        if p_pos < 0 or c_pos < 0:
            raise AuditError(f"anchor token missing: {logical:06X}")
        before_token = p_payload[p_pos:p_pos + 4]
        after_token = c_payload[c_pos:c_pos + 4]
        before_render = parent_dictionary.expand(p_payload, tbl)
        after_render = candidate_dictionary.expand(c_payload, tbl)
        anchor_rows.append(
            {
                "record_abs": f"{logical:06X}",
                "before_token": before_token.hex().upper(),
                "after_token": after_token.hex().upper(),
                "before_render": before_render,
                "after_render": after_render,
                "destination_payload_bytes_nonzero": after_token[2] != 0 and after_token[3] != 0,
                "render_exact": before_render == after_render,
            }
        )

    free = list_free_ext3_indices(candidate, num_banks=16)
    unsafe_free = [
        index
        for index in free
        if ((index - EXT3_INDEX_BASE) >> 8) & 0xFF == 0
        or ((index - EXT3_INDEX_BASE) & 0xFF) == 0
    ]

    q0 = base + QUARANTINE_LO
    q1 = base + QUARANTINE_HI
    checks = {
        "build_report_ok": bool(build.get("ok")),
        "parent_unsafe_refs_587": len(before) == 587,
        "candidate_unsafe_refs_only_quarantine_14": len(after) == 14 and len(after_quarantine) == 14,
        "candidate_unsafe_live_refs_zero": len(after_live) == 0,
        "quarantine_block_byte_exact": parent[q0:q1] == candidate[q0:q1],
        "anchors_have_nonzero_payload_bytes": all(row["destination_payload_bytes_nonzero"] for row in anchor_rows),
        "anchors_static_render_byte_semantics_unchanged": all(row["render_exact"] for row in anchor_rows),
        "five_bank_alias_runtime_present": detect_ext3_alias_page_count(candidate) == 5,
        "guard_rejects_ext3_middle_nul": not dict_token_safe_in_zstring(0x1001),
        "guard_rejects_ext3_trailing_nul": not dict_token_safe_in_zstring(0x1100),
        "guard_accepts_ext3_nonzero_pair": dict_token_safe_in_zstring(0x1101),
        "allocator_free_list_has_no_embedded_nul_indices": not unsafe_free,
        "runtime_7A_byte_exact": parent[base + 0x7A0000:base + 0x7B0000] == candidate[base + 0x7A0000:base + 0x7B0000],
        "runtime_7F_code_byte_exact_except_checksum": parent[base + 0x7F0000:base + 0x7FFFFE] == candidate[base + 0x7F0000:base + 0x7FFFFE],
        "candidate_save_size_32k": CANDIDATE_SAVE.stat().st_size == 32_768,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_ext3_zero_middle_rehome_candidate.py",
        "ok": ok,
        "parent_sha256": sha(parent),
        "candidate_sha256": sha(candidate),
        "counts": {
            "unsafe_before": len(before),
            "unsafe_after": len(after),
            "unsafe_after_outside_quarantine": len(after_live),
            "unsafe_after_quarantine": len(after_quarantine),
            "unsafe_free_indices_after_guard": len(unsafe_free),
        },
        "anchors": anchor_rows,
        "checks": checks,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
