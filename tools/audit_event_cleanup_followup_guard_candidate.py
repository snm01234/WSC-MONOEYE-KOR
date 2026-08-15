#!/usr/bin/env python3
"""Independent static audit for event_cleanup_followup_guard_candidate.wsc."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT = PATCH / "event_cleanup_gato_5d1e3e_candidate.wsc"
CANDIDATE = PATCH / "event_cleanup_followup_guard_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
INVENTORY = ROOT / "legacy/release_core_20260815/out/script/battle_dialogue_structure_inventory.csv"
OUT = PATCH / "event_cleanup_followup_guard_audit.json"

EXPECTED_PARENT_SHA = "CA4867914852328E0EB4E184A9F27BD831E5EAE3F61B4A94C253D702A3A43DAB"
EXPECTED_CANDIDATE_SHA = "C8EE51BE9C5E33DFD88E7565453FF031A931AAF4948D9CD4AEE35A7EC6892E86"
FALSE_LEAD_TEXT = {
    0x5D3122: (bytes.fromhex("E7BAF50D01010101010101"), "모니터、어디냐！？"),
    0x5D313B: (bytes.fromhex("E7BAF50D01010101010101"), "모니터、어디냐！？"),
}
META = {0x5E6586: 0x90, 0x5E65A7: 0x90}
PROTECTED_TEXT = {0x5D870B: 0x5D886F, 0x5DB42B: 0x5DB650}
ORPHANS = (0x594715, 0x60F3A6, 0x6106EF, 0x61165D, 0x638F52)
NEW_ORPHANS = ORPHANS
CONTROL_LO, CONTROL_HI = 0x610552, 0x610567


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def z(data: bytes, sb: int, logical: int) -> bytes:
    got = read_encoded_z_safe(data, sb + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable record {logical:06X}")
    return bytes(got[0])


def checksum_valid(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def main() -> int:
    parent = PARENT.read_bytes()
    cand = CANDIDATE.read_bytes()
    original = ORIGINAL.read_bytes()
    sb = stock_base(parent)
    so = stock_base(original)

    with INVENTORY.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by = {int(r["record_start"], 16): r for r in rows}

    tbl = Tbl.load(PATCH / "hangul_patch_pad3.tbl")
    jp_tbl = Tbl.load(ROOT / "data/monoeye_verified.tbl")
    dp = make_dictionary_ext3(parent, load_ext_meta(PATCH / "ext_dictionary_meta.json"), load_ext_meta(PATCH / "ext3_dictionary_meta.json"))
    dc = make_dictionary_ext3(cand, load_ext_meta(PATCH / "ext_dictionary_meta.json"), load_ext_meta(PATCH / "ext3_dictionary_meta.json"))
    do = Dictionary(original)

    checks: dict[str, bool] = {}
    checks["parent_sha_exact"] = sha(parent) == EXPECTED_PARENT_SHA
    checks["candidate_sha_exact"] = sha(cand) == EXPECTED_CANDIDATE_SHA
    checks["same_size"] = len(parent) == len(cand) == 16_777_216
    checks["checksum_valid"] = checksum_valid(cand)

    false_lead_rows = []
    for logical, (expected_payload, expected_render) in FALSE_LEAD_TEXT.items():
        after = z(cand, sb, logical)
        rendered = dc.expand(after, tbl).rstrip("　 ")
        false_lead_rows.append({
            "abs": f"{logical:06X}",
            "payload_hex": after.hex().upper(),
            "render": rendered,
        })
        checks[f"{logical:06X}_false_lead_fixed"] = after == expected_payload
        checks[f"{logical:06X}_render_exact"] = rendered == expected_render

    meta_rows = []
    for logical, meta in META.items():
        before = z(parent, sb, logical)
        after = z(cand, sb, logical)
        orig = z(original, so, logical)
        body_before = before[:4]
        meta_rows.append({
            "abs": f"{logical:06X}",
            "before": before.hex().upper(),
            "after": after.hex().upper(),
            "body_before_render": dp.expand(body_before, tbl).rstrip("　 "),
            "body_after_render": dc.expand(after[1:5], tbl).rstrip("　 "),
            "original_render": do.expand(orig, jp_tbl),
        })
        checks[f"{logical:06X}_metadata_restored"] = after[:1] == bytes([meta])
        checks[f"{logical:06X}_body_token_preserved"] = after[1:5] == body_before
        checks[f"{logical:06X}_render_preserved"] = dp.expand(body_before, tbl).rstrip("　 ") == dc.expand(after[1:5], tbl).rstrip("　 ")
        checks[f"{logical:06X}_extent_preserved"] = len(after) == len(before)

    short_restored = []
    short_partition: dict[str, int] = {}
    short_failures = []
    short_two_byte_protected = []
    short_two_byte_failures = []
    for row in rows:
        if (
            row.get("classification") == "battle_voice_structured"
            and row.get("safe_structure_exact") == "yes"
            and row.get("action") == "quarantine"
            and row.get("reason") == "short/fixed body capacity < 4"
        ):
            logical = int(row["record_start"], 16)
            auth = bytes.fromhex(row.get("authoritative_structure_hex", ""))
            if len(auth) == 1:
                before = z(parent, sb, logical)
                after = z(cand, sb, logical)
                if before[:1] != auth:
                    rendered = dc.expand(after[1:3], tbl).rstrip("　 ") if len(after) >= 3 else ""
                    short_partition[rendered] = short_partition.get(rendered, 0) + 1
                    short_restored.append(f"{logical:06X}")
                    if not (
                        after[:1] == auth
                        and len(before) == 3
                        and before[2] == 1
                        and after[1:3] == before[:2]
                        and len(after) == len(before) == 3
                    ):
                        short_failures.append(f"{logical:06X}")
            elif len(auth) == 2:
                before = z(parent, sb, logical)
                after = z(cand, sb, logical)
                if before[:2] != auth:
                    short_two_byte_protected.append(f"{logical:06X}")
                    if after != before:
                        short_two_byte_failures.append(f"{logical:06X}")

    checks["short_fixed_metadata_restored_count_3499"] = len(short_restored) == 3499
    checks["short_fixed_metadata_restore_failures_zero"] = not short_failures
    checks["short_fixed_render_partition_exact"] = short_partition == {
        "미사용": 3430, "크리스": 33, "버니": 33,
        "이런　곳에서": 1, "티파": 1, "레코아": 1,
    }
    checks["short_fixed_two_byte_text_starts_protected_15"] = len(short_two_byte_protected) == 15
    checks["short_fixed_two_byte_text_start_changes_zero"] = not short_two_byte_failures

    protected = []
    for logical, duplicate in PROTECTED_TEXT.items():
        orig = z(original, so, logical)
        orig_dup = z(original, so, duplicate)
        current = z(cand, sb, logical)
        row = by[duplicate]
        protected.append({
            "abs": f"{logical:06X}",
            "duplicate": f"{duplicate:06X}",
            "original_render": do.expand(orig, jp_tbl),
            "duplicate_classification": row["classification"],
            "duplicate_reason": row["reason"],
        })
        checks[f"{logical:06X}_original_exact_duplicate"] = orig == orig_dup
        checks[f"{logical:06X}_kept_whole_E518"] = current.startswith(b"\xE5\x18")
        checks[f"{logical:06X}_duplicate_runtime_text_initial"] = row["classification"] == "text_initial_exception" and "runtime-proven" in row["reason"]

    orphan_rows = []
    for logical in ORPHANS:
        o = original[so + logical - 2 : so + logical + 4]
        c = cand[sb + logical - 2 : sb + logical + 4]
        orphan_rows.append({"abs": f"{logical:06X}", "original": o.hex().upper(), "candidate": c.hex().upper()})
        checks[f"{logical:06X}_original_family"] = o == b"\x00\x00\x06\x00\x17\x28"
        # The already-validated Karama title was length-preservingly rewritten
        # before this pass, so bytes immediately *before* 61:06EF are title
        # padding rather than the original double-NUL.  The family postcondition
        # is the record/control contract itself: 01 00 | 17 28.
        checks[f"{logical:06X}_candidate_blank_family"] = cand[sb + logical : sb + logical + 4] == b"\x01\x00\x17\x28"

    checks["61055C_control_window_byte_exact"] = cand[sb + CONTROL_LO : sb + CONTROL_HI] == parent[sb + CONTROL_LO : sb + CONTROL_HI]

    changed = [i for i, (a, b) in enumerate(zip(parent, cand)) if a != b]
    non_checksum = [i for i in changed if i < len(cand) - 2]
    allowed = {sb + x for x in NEW_ORPHANS}
    for logical in FALSE_LEAD_TEXT:
        before, after = z(parent, sb, logical), z(cand, sb, logical)
        allowed.update(sb + logical + i for i, (a, b) in enumerate(zip(before, after)) if a != b)
    for logical in META:
        before, after = z(parent, sb, logical), z(cand, sb, logical)
        allowed.update(sb + logical + i for i, (a, b) in enumerate(zip(before, after)) if a != b)
    for h in short_restored:
        logical = int(h, 16)
        before, after = z(parent, sb, logical), z(cand, sb, logical)
        allowed.update(sb + logical + i for i, (a, b) in enumerate(zip(before, after)) if a != b)
    checks["nonchecksum_delta_exact"] = set(non_checksum) == allowed

    remaining = []
    for logical in (*PROTECTED_TEXT.keys(), *META.keys()):
        if z(cand, sb, logical).startswith(b"\xE5\x18"):
            remaining.append(f"{logical:06X}")
    checks["remaining_whole_E518_are_only_protected_text_initial"] = remaining == ["5D870B", "5DB42B"]

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_event_cleanup_followup_guard_candidate.py",
        "ok": all(checks.values()),
        "checks": checks,
        "false_lead_rows": false_lead_rows,
        "metadata_rows": meta_rows,
        "short_fixed": {
            "restored_count": len(short_restored),
            "render_partition": short_partition,
            "restore_failures": short_failures,
            "two_byte_text_starts_protected": short_two_byte_protected,
            "two_byte_text_start_change_failures": short_two_byte_failures,
        },
        "protected_text_initial": protected,
        "orphan_family": orphan_rows,
        "candidate_vs_parent_nonchecksum_diff_count": len(non_checksum),
        "candidate_vs_parent_nonchecksum_offset_sample": [f"{i - sb:06X}" for i in non_checksum[:40]],
        "remaining_whole_E518_quarantine_set": remaining,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
