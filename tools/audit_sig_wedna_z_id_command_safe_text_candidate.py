#!/usr/bin/env python3
"""Independent static audit for the Sig Wedna(Z) ID-command safe-text candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import encode_phrase  # noqa: E402
from mixed_residual_classification import is_japanese_character  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    build_free_slot_inventory,
    build_reference_union,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    ws_header,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/sig_wedna_z_id_command_safe_text_v2_ko.json"
CANDIDATE = ROOT / "out/patch/sig_wedna_z_id_command_safe_text_v2_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/sig_wedna_z_id_command_safe_text_v2_candidate.sav"
BUILD_REPORT = ROOT / "out/patch/sig_wedna_z_id_command_safe_text_v2_report.json"
OUT = ROOT / "out/patch/sig_wedna_z_id_command_safe_text_v2_candidate_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_PARENT_SHA256 = "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c"
EXPECTED_CANDIDATE_SHA256 = "191038bd214b5d232d6d534d8ad24adec23ff143d3f0871277d18f952ee316b1"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
FORBIDDEN = (bytes.fromhex("E518"), bytes.fromhex("E62F"))


class AuditError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": digest(payload),
    }


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise AuditError("ROM size changed")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def covered(run: tuple[int, int], allowed: Iterable[tuple[int, int]]) -> bool:
    left, right = run
    return any(begin <= left and right <= end for begin, end in allowed)


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    candidate = CANDIDATE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT_SHA256:
        raise AuditError("main TIP identity drifted")
    if len(candidate) != ROM_SIZE or digest(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("candidate identity drifted")
    if len(candidate_save) != SAVE_SIZE:
        raise AuditError("candidate SaveRAM size is invalid")
    if build.get("ok") is not True or (build.get("candidate") or {}).get("sha256") != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("build report is not bound to the candidate")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    before_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    after_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    parent_free = set(inventory.ext_free)

    record_checks: list[dict[str, Any]] = []
    record_extents: list[tuple[int, int]] = []
    pointer_extents: list[tuple[int, int]] = []
    phrase_extents: list[tuple[int, int]] = []
    used_phrase_ranges: list[tuple[int, int]] = []
    stock_count = int(ext_meta["stock_count"])
    ext_ptr_off = int(str(ext_meta["ext_ptr_off"]), 16)
    ext_bank_file = int(str(ext_meta["ext_seg"]), 16) * BANK_SIZE

    for row in spec.get("records") or []:
        logical = int(str(row["record_start"]), 16)
        prefix = bytes.fromhex(str(row["prefix_hex"]))
        expected_parent_payload = bytes.fromhex(str(row["expected_payload_hex"]))
        body_capacity = int(row["body_capacity"])
        phrase = str(row["ko"])
        index = int(str(row["dictionary_index"]), 16)
        token = token_from_dict_index(index)
        expected_body = token + b"\x01" * (body_capacity - len(token))
        before_got = read_encoded_z_safe(parent, sb + logical, max_len=128)
        after_got = read_encoded_z_safe(candidate, sb + logical, max_len=128)
        if before_got is None or after_got is None:
            raise AuditError(f"unreadable target at {logical:06X}")
        before_payload, before_term = bytes(before_got[0]), int(before_got[1])
        after_payload, after_term = bytes(after_got[0]), int(after_got[1])
        encoded_phrase = encode_phrase(phrase, tbl)
        rendered_before = before_dictionary.expand(before_payload[len(prefix):], tbl).rstrip("\u3000 \t")
        rendered_after = after_dictionary.expand(after_payload[len(prefix):], tbl).rstrip("\u3000 \t")
        raw_after = after_dictionary.raw_entry(index)
        entry_abs = after_dictionary.entry_abs(index)
        entry_end = entry_abs + len(raw_after) + 1
        used_phrase_ranges.append((entry_abs, entry_end))
        pointer_start = ext_bank_file + ext_ptr_off + (index - stock_count) * 2
        pointer_extents.append((pointer_start, pointer_start + 2))
        body_start = sb + logical + len(prefix)
        record_extents.append((body_start, body_start + body_capacity))
        ok = (
            before_payload == expected_parent_payload
            and after_payload == prefix + expected_body
            and before_term == after_term
            and parent[before_term] == 0
            and candidate[after_term] == 0
            and index in parent_free
            and raw_after == encoded_phrase
            and rendered_after == phrase.rstrip("\u3000 \t")
            and not any(is_japanese_character(character) for character in rendered_after)
            and not any(sequence in after_payload[len(prefix):] for sequence in FORBIDDEN)
            and token[0] == 0xFF
        )
        record_checks.append(
            {
                "record_start": f"{logical:06X}",
                "ok": ok,
                "parent_text": rendered_before,
                "candidate_text": rendered_after,
                "expected_text": phrase,
                "prefix_hex": prefix.hex().upper(),
                "normal_ext_index": f"{index:04X}",
                "token_hex": token.hex().upper(),
                "parent_slot_union_free": index in parent_free,
                "entry_abs": f"{entry_abs:08X}",
                "entry_payload_sha256": digest(raw_after),
                "terminator_preserved": before_term == after_term,
                "forbidden_portals_absent": not any(
                    sequence in after_payload[len(prefix):] for sequence in FORBIDDEN
                ),
            }
        )

    if used_phrase_ranges:
        phrase_extents.append((min(left for left, _ in used_phrase_ranges), max(right for _, right in used_phrase_ranges)))
    allowed = record_extents + pointer_extents + phrase_extents + [(len(parent) - 2, len(parent))]
    runs = diff_runs(parent, candidate)
    unaccounted = [
        {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    target_record_bytes = set()
    for left, right in record_extents:
        target_record_bytes.update(range(left, right))
    non_target_stock_changes = sum(
        1
        for file_index in range(stock_base(parent), len(parent) - 2)
        if parent[file_index] != candidate[file_index] and file_index not in target_record_bytes
    )
    runtime_start = stock_base(parent) + 0x7A0600
    runtime_end = stock_base(parent) + 0x7A1000
    runtime_unchanged = parent[runtime_start:runtime_end] == candidate[runtime_start:runtime_end]
    checksum_valid = int(ws_header(candidate)["checksum"]) == sum(candidate[:-2]) & 0xFFFF

    checks = {
        "candidate_identity_exact": digest(candidate) == EXPECTED_CANDIDATE_SHA256,
        "three_target_records": len(record_checks) == 3,
        "all_target_checks_pass": all(row["ok"] for row in record_checks),
        "all_target_slots_union_free_in_parent": all(row["parent_slot_union_free"] for row in record_checks),
        "target_ext3_and_compact3_absent": all(row["forbidden_portals_absent"] for row in record_checks),
        "all_diff_runs_bounded": not unaccounted,
        "non_target_stock_changes_zero": non_target_stock_changes == 0,
        "runtime_hook_unchanged": runtime_unchanged,
        "candidate_saveram_size_valid": len(candidate_save) == SAVE_SIZE,
        "wonder_swan_checksum_valid": checksum_valid,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 2,
        "generated_by": "tools/audit_sig_wedna_z_id_command_safe_text_candidate.py",
        "read_only": True,
        "ok": ok,
        "parent": identity(MAIN, parent),
        "candidate": identity(CANDIDATE, candidate),
        "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
        "spec": identity(SPEC),
        "build_report": identity(BUILD_REPORT),
        "checks": checks,
        "counts": {
            "targets": len(record_checks),
            "target_failures": sum(not row["ok"] for row in record_checks),
            "diff_runs": len(runs),
            "changed_bytes": sum(right - left for left, right in runs),
            "unaccounted_diff_runs": len(unaccounted),
            "non_target_stock_changes": non_target_stock_changes,
        },
        "target_checks": record_checks,
        "diff_runs": [
            {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
            for left, right in runs
        ],
        "unaccounted_diff_runs": unaccounted,
        "interpretation": {
            "static_result": "the two-line first and continuation records plus the one-line variant now use ordinary two-byte FF-page dictionary tokens",
            "runtime_result": "pending user reproduction test with Sig Wedna(Z) spirit/ID command",
            "promotion": "blocked_pending_user_runtime_validation",
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "ok": ok,
                "candidate": report["candidate"],
                "checks": checks,
                "counts": report["counts"],
                "out": str(OUT.relative_to(ROOT)).replace("\\", "/"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
