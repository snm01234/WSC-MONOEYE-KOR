#!/usr/bin/env python3
"""Build a current-main delta candidate for the reviewed uncovered dialogue set.

The legacy 2026-08-04 parent is never rebuilt.  Only rows whose 2026-08-08
approved LLM review differs from the current main private E5 18 phrase are
retargeted.  Existing phrase slots are never modified: each reviewed phrase is
reused byte-exact when already present or allocated into a fresh ext3 alias
slot, and only the 4-byte E5 18 portal at the target record is changed.

This isolates the review from all later battle-structure and terminology fixes.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3  # noqa: E402
from build_remaining_dialogue_candidate import diff_runs, encode_phrase  # noqa: E402
from monoeye_rom import BANK_SIZE, Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SHEET = ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv"
REVIEW = ROOT / "out/script/uncovered_llm_literal_review_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/uncovered_llm_reviewed_candidate.wsc"
OUT_SAVE = ROOT / "sram/uncovered_llm_reviewed_candidate.sav"
OUT_REPORT = ROOT / "out/patch/uncovered_llm_reviewed_candidate_report.json"
EXPECTED_MAIN_SHA256 = "46d6d6a984ec7696428ade90f5ea1e191f218e568242e2439f7347a6004b9729"
EXPECTED_REVIEWED_ROWS = 1893
EXPECTED_TARGETS = 116
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve())
    return {"path": shown, "size": len(payload), "sha256": sha256(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def merged(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(a, b) for a, b in out]


def in_intervals(offset: int, intervals: list[tuple[int, int]]) -> bool:
    return any(a <= offset < b for a, b in intervals)


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("current main identity drifted")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    with SHEET.open("r", encoding="utf-8-sig", newline="") as fh:
        sheet_rows = [dict(row) for row in csv.DictReader(fh)]
    if len(sheet_rows) != EXPECTED_REVIEWED_ROWS:
        raise BuildError("reviewed sheet population drifted")
    if any(row.get("translation_source") != "llm" or row.get("review_status") != "approved" for row in sheet_rows):
        raise BuildError("reviewed sheet contains non-approved provenance")
    by_abs = {str(row["abs"]).upper(): row for row in sheet_rows}

    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    if int((review.get("counts") or {}).get("unreviewed_remaining", -1)) != 0:
        raise BuildError("review report still has unreviewed rows")
    review_targets = [row for row in review.get("changes") or [] if row.get("strategy") == "llm_review_override"]
    if len(review_targets) != EXPECTED_TARGETS:
        raise BuildError(f"review target count drifted: {len(review_targets)}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)

    targets: list[dict[str, Any]] = []
    for review_row in sorted(review_targets, key=lambda row: int(str(row["abs"]), 16)):
        address = str(review_row["abs"]).upper()
        sheet = by_abs.get(address)
        if sheet is None or sheet.get("ko") != review_row.get("ko"):
            raise BuildError(f"review/sheet binding mismatch at {address}")
        logical = int(address, 16)
        got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        if got is None:
            raise BuildError(f"current main record unreadable at {address}")
        payload, terminator = bytes(got[0]), int(got[1])
        positions = [pos for pos in range(max(0, len(payload) - 3)) if payload[pos:pos + 2] == b"\xE5\x18"]
        if len(positions) != 1:
            raise BuildError(f"target does not have exactly one E5 18 portal at {address}: {positions}")
        portal_offset = positions[0]
        old_token = payload[portal_offset:portal_offset + 4]
        old_index = 0x1000 + (old_token[2] << 8) + old_token[3]
        current = dictionary.expand_index(old_index, tbl).rstrip("\u3000 \t")
        expected_current = str(review_row.get("current_main") or "")
        if current != expected_current:
            raise BuildError(f"current phrase drifted at {address}: {current!r} != {expected_current!r}")
        ko = str(sheet["ko"])
        if ko == current:
            raise BuildError(f"review target is already satisfied at {address}")
        encoded = encode_phrase(ko, tbl)
        if not encoded or b"\x00" in encoded:
            raise BuildError(f"target cannot encode at {address}: {ko!r}")
        targets.append({
            "abs": address,
            "logical": logical,
            "jp": sheet.get("original_jp", ""),
            "current": current,
            "ko": ko,
            "encoded": encoded,
            "payload": payload,
            "terminator": terminator,
            "portal_offset": portal_offset,
            "old_index": old_index,
            "old_token": old_token,
        })

    assignments, states = allocate_ext3(parent, targets)
    candidate = bytearray(parent)
    allowed_intervals: list[tuple[int, int]] = []
    allocation_rows: list[dict[str, Any]] = []

    # Copy only the allocator's ext3 banks, then record exact new pointer/phrase
    # extents in the diff allowlist.
    for page, state in states.items():
        start = int(state["start"])
        candidate[start:start + BANK_SIZE] = state["bank"]
        new_locals = {
            int(info["local"])
            for info in assignments.values()
            if int(info["page"]) == page and not bool(info["reused"])
        }
        for local in sorted(new_locals):
            allowed_intervals.append((start + local * 2, start + local * 2 + 2))
        if int(state["cursor"]) > int(state["cursor_before"]):
            allowed_intervals.append((start + int(state["cursor_before"]), start + int(state["cursor"])))

    # Retarget only the private E5 18 token in each reviewed record. Prefix,
    # suffix/padding, record length, and terminator stay byte-exact.
    for row in targets:
        info = assignments[row["ko"]]
        token = bytes(info["token"])
        if len(token) != 4 or token[:2] != b"\xE5\x18":
            raise BuildError(f"allocator returned invalid token for {row['abs']}")
        start = sb + int(row["logical"]) + int(row["portal_offset"])
        before = bytes(candidate[start:start + 4])
        if before != row["old_token"]:
            raise BuildError(f"portal drift before write at {row['abs']}")
        candidate[start:start + 4] = token
        allowed_intervals.append((start + 2, start + 4))
        allocation_rows.append({
            "abs": row["abs"],
            "old_index": f"{int(row['old_index']):05X}",
            "new_token": token.hex().upper(),
            "page": int(info["page"]),
            "segment": f"{int(info['segment']):02X}",
            "local": f"{int(info['local']):03X}",
            "pointer": f"{int(info['pointer']):04X}",
            "reused_existing_phrase": bool(info["reused"]),
            "ko": row["ko"],
        })

    checksum = update_ws_checksum(candidate)
    allowed_intervals.append((len(parent) - 2, len(parent)))
    result = bytes(candidate)
    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)

    # Target-level structure and render proof.
    target_checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in targets:
        after_got = read_encoded_z_safe(result, sb + int(row["logical"]), max_len=256)
        if after_got is None:
            failures.append({"abs": row["abs"], "reason": "unreadable_after"})
            continue
        after_payload, after_term = bytes(after_got[0]), int(after_got[1])
        pos = int(row["portal_offset"])
        token = after_payload[pos:pos + 4]
        if token[:2] != b"\xE5\x18":
            rendered = "<missing portal>"
        else:
            index = 0x1000 + (token[2] << 8) + token[3]
            rendered = result_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        before_payload = bytes(row["payload"])
        structure_exact = (
            len(after_payload) == len(before_payload)
            and after_payload[:pos] == before_payload[:pos]
            and after_payload[pos:pos + 2] == b"\xE5\x18"
            and after_payload[pos + 4:] == before_payload[pos + 4:]
        )
        check = {
            "abs": row["abs"],
            "jp": row["jp"],
            "before": row["current"],
            "ko": row["ko"],
            "rendered": rendered,
            "portal_offset": pos,
            "record_structure_exact_except_portal_index": structure_exact,
            "terminator_exact": after_term == int(row["terminator"]),
            "ok": rendered == row["ko"] and structure_exact and after_term == int(row["terminator"]),
        }
        target_checks.append(check)
        if not check["ok"]:
            failures.append(check)
    if failures:
        raise BuildError("target verification failed: " + json.dumps(failures[:10], ensure_ascii=False))

    # Full reviewed-draft portal parity: all 1,858 rows that were reviewed now
    # must either render their reviewed phrase or be one of the 27 legacy short
    # metadata rows without a private portal. This confirms no reviewed portal
    # row was missed.
    portal_parity = 0
    portal_mismatch: list[dict[str, str]] = []
    nonportal_reviewed = 0
    for sheet in sheet_rows:
        if "2026-08-08 LLM line-by-line literal review" not in str(sheet.get("notes") or ""):
            continue
        logical = int(str(sheet["abs"]), 16)
        got = read_encoded_z_safe(result, sb + logical, max_len=256)
        if got is None:
            portal_mismatch.append({"abs": sheet["abs"], "reason": "unreadable"})
            continue
        payload = bytes(got[0])
        positions = [pos for pos in range(max(0, len(payload) - 3)) if payload[pos:pos + 2] == b"\xE5\x18"]
        if len(positions) != 1:
            nonportal_reviewed += 1
            continue
        pos = positions[0]
        token = payload[pos:pos + 4]
        index = 0x1000 + (token[2] << 8) + token[3]
        rendered = result_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        expected = str(sheet["ko"])
        if rendered == expected:
            portal_parity += 1
        else:
            portal_mismatch.append({"abs": sheet["abs"], "rendered": rendered, "expected": expected})
    if portal_mismatch:
        raise BuildError("reviewed portal parity failed: " + json.dumps(portal_mismatch[:10], ensure_ascii=False))

    allowed = merged(allowed_intervals)
    runs = diff_runs(parent, result)
    unaccounted: list[int] = []
    for run in runs:
        start, end = int(run[0]), int(run[1])
        for offset in range(start, end):
            if not in_intervals(offset, allowed):
                unaccounted.append(offset)
                if len(unaccounted) >= 50:
                    break
        if len(unaccounted) >= 50:
            break
    if unaccounted:
        raise BuildError("unaccounted diff bytes: " + ", ".join(f"{value:06X}" for value in unaccounted[:20]))

    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_uncovered_llm_reviewed_delta_candidate.py",
        "ok": True,
        "status": "candidate_static_verified",
        "main_tip_modified": False,
        "inputs": {
            "main": identity(MAIN, parent),
            "reviewed_sheet": identity(SHEET),
            "review_report": identity(REVIEW),
        },
        "counts": {
            "reviewed_sheet_rows": len(sheet_rows),
            "new_delta_targets": len(targets),
            "unique_reviewed_phrases": len({row["ko"] for row in targets}),
            "unique_target_tokens": len({row["new_token"] for row in allocation_rows}),
            "unique_new_phrase_tokens": len({row["new_token"] for row in allocation_rows if not row["reused_existing_phrase"]}),
            "unique_reused_phrase_tokens": len({row["new_token"] for row in allocation_rows if row["reused_existing_phrase"]}),
            "allocator_reused_target_rows": sum(bool(row["reused_existing_phrase"]) for row in allocation_rows),
            "allocator_new_target_rows": sum(not bool(row["reused_existing_phrase"]) for row in allocation_rows),
            "reviewed_portal_rows_exact": portal_parity,
            "reviewed_nonportal_short_rows": nonportal_reviewed,
            "target_failures": len(failures),
        },
        "verification": {
            "all_target_renders_exact": all(row["ok"] for row in target_checks),
            "all_target_terminators_exact": all(row["terminator_exact"] for row in target_checks),
            "all_target_structure_exact_except_portal_index": all(row["record_structure_exact_except_portal_index"] for row in target_checks),
            "reviewed_portal_mismatches": len(portal_mismatch),
            "unaccounted_changed_bytes": len(unaccounted),
            "diff_runs": len(runs),
            "diff_bytes": sum(end - start for start, end in runs),
            "checksum": f"{checksum:04X}",
            "candidate_sha256": sha256(result),
        },
        "allocations": allocation_rows,
        "targets": target_checks,
        "diff_sample": [
            {"start": f"{start:06X}", "end": f"{end:06X}", "length": end - start}
            for start, end in runs[:160]
        ],
        "candidate_rom": identity(OUT_ROM, result),
        "candidate_save": identity(OUT_SAVE),
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["verification"], ensure_ascii=False, indent=2))
    print("candidate:", OUT_ROM)
    print("report:", OUT_REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
