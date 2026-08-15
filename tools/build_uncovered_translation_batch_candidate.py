#!/usr/bin/env python3
"""Build one approved uncovered-text batch as a cumulative candidate.

Only direct E5 18 five-bank aliases are accepted here. Short-body batches stay
blocked until a separately reviewed exact/retired-token allocation is supplied.
The current TIP and SaveRAM are never modified.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3, atomic_bytes, atomic_json, sha256
from build_encyclopedia_character_five_bank_batch02_candidate import PAGES
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
MANIFEST = ROOT / "out/patch/uncovered_translation_batch_manifest.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
PENDING_BASE = ROOT / "out/patch/next_stage_event_id_indirect_candidate.wsc"
MAIN_SHA256 = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
PENDING_BASE_SHA256 = "99ddfa32a81317e448b168fd4ae0a22b1dfbfd47542b26dfcda544e7e1b8b4ed"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"JSON root must be object: {path}")
    return value


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve())
    return {"path": shown, "size": len(data), "sha256": sha256(data)}


def batch_path(batch_id: str, suffix: str) -> Path:
    directory = ROOT / "sram" if suffix.endswith(".sav") else ROOT / "out/patch"
    return directory / f"uncovered_batch_{batch_id}_{suffix}"


def resolve_parent(manifest: dict[str, Any], batch_id: str) -> tuple[Path, str, str | None]:
    batches = list(manifest.get("batches") or [])
    position = next((i for i, row in enumerate(batches) if row.get("batch_id") == batch_id), None)
    if position is None:
        raise BuildError(f"unknown batch: {batch_id}")
    if position == 0:
        raise BuildError("C000 is the already-built pending base candidate, not a buildable batch")
    if position == 1:
        return PENDING_BASE, PENDING_BASE_SHA256, "C000"
    previous = str(batches[position - 1].get("batch_id") or "")
    path = batch_path(previous, "candidate.wsc")
    if not path.exists():
        raise BuildError(f"previous cumulative candidate is missing: {path}")
    return path, sha256(path.read_bytes()), previous


def load_rows(sheet_path: Path, expected_batch: str, parent: bytes, tbl: Tbl) -> list[dict[str, Any]]:
    with sheet_path.open(encoding="utf-8-sig", newline="") as stream:
        sources = [dict(row) for row in csv.DictReader(stream)]
    if not sources or any(row.get("batch_id") != expected_batch for row in sources):
        raise BuildError("batch sheet identity drifted")

    sb = stock_base(parent)
    prepared: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda row: int(str(row["abs"]), 16)):
        address = str(source["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source["payload_capacity"])
        body_capacity = int(source["body_capacity"])
        expected_payload = bytes.fromhex(str(source["current_payload_hex"]))
        expected_body_digest = str(source["source_body_sha256"]).lower()
        if payload_capacity != len(expected_payload) or body_capacity != payload_capacity - len(prefix):
            raise BuildError(f"sheet boundary drifted at {address}")
        if body_capacity < 4:
            raise BuildError(f"short body requires separate allocation review at {address}")
        current = parent[sb + logical : sb + logical + payload_capacity]
        if current != expected_payload or not current.startswith(prefix):
            raise BuildError(f"parent payload drifted at {address}")
        if parent[sb + logical + payload_capacity] != 0:
            raise BuildError(f"terminator drifted at {address}")
        if sha256(current[len(prefix):]) != expected_body_digest:
            raise BuildError(f"body digest drifted at {address}")
        if source.get("translation_source") != "llm" or source.get("review_status") != "approved":
            raise BuildError(f"translation is not approved at {address}")
        ko = normalize_ko_text(str(source.get("ko") or ""))
        if not ko or any(is_japanese_character(ch) for ch in ko):
            raise BuildError(f"invalid Korean target at {address}: {ko!r}")
        encoded = encode_phrase(ko, tbl)
        if not encoded or b"\x00" in encoded:
            raise BuildError(f"invalid encoded phrase at {address}")
        prepared.append({
            "abs": address,
            "logical": logical,
            "scope": str(source.get("scope") or ""),
            "gap": str(source.get("gap") or ""),
            "jp": str(source.get("original_jp") or ""),
            "before": str(source.get("current_text") or ""),
            "ko": ko,
            "encoded": encoded,
            "prefix": prefix,
            "prefix_len": len(prefix),
            "payload_capacity": payload_capacity,
            "body_capacity": body_capacity,
        })
    return prepared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", default="E001")
    args = parser.parse_args(argv)
    batch_id = args.batch_id.upper()

    manifest = load_object(MANIFEST)
    if manifest.get("ok") is not True or str((manifest.get("main_tip") or {}).get("sha256") or "").lower() != MAIN_SHA256:
        raise BuildError("batch manifest is not bound to the current main TIP")
    batch = next((dict(row) for row in manifest.get("batches") or [] if row.get("batch_id") == batch_id), None)
    if batch is None:
        raise BuildError(f"batch not found: {batch_id}")
    if batch.get("status") != "approved_ready" or int(batch.get("approved_records") or 0) != int(batch.get("records") or -1):
        raise BuildError(f"batch is not fully approved: {batch_id}")
    if batch.get("requires_direct_ext3_only") is not True:
        raise BuildError(f"batch includes short bodies and needs separate allocation review: {batch_id}")

    main = MAIN.read_bytes()
    if len(main) != ROM_SIZE or sha256(main) != MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    parent_path, expected_parent_sha, parent_batch = resolve_parent(manifest, batch_id)
    parent = bytes(load_rom(parent_path))
    if len(parent) != ROM_SIZE or sha256(parent) != expected_parent_sha:
        raise BuildError("parent candidate identity drifted")
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    sheet_path = ROOT / str(batch["sheet"])
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    rows = load_rows(sheet_path, batch_id, parent, tbl)
    if len(rows) != int(batch["records"]):
        raise BuildError("batch row count drifted")
    assignments, states = allocate_ext3(parent, rows)

    candidate = bytearray(parent)
    pointer_extents: list[tuple[int, int]] = []
    phrase_extents: list[tuple[int, int]] = []
    for page, state in states.items():
        start = int(state["start"])
        candidate[start : start + BANK_SIZE] = state["bank"]
        new_locals = {
            int(info["local"])
            for info in assignments.values()
            if int(info["page"]) == page and not bool(info["reused"])
        }
        pointer_extents.extend((start + local * 2, start + local * 2 + 2) for local in sorted(new_locals))
        if int(state["cursor"]) > int(state["cursor_before"]):
            phrase_extents.append((start + int(state["cursor_before"]), start + int(state["cursor"])))

    sb = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        info = assignments[row["ko"]]
        token = bytes(info["token"])
        replacement = token + b"\x01" * (row["body_capacity"] - len(token))
        body_start = sb + row["logical"] + row["prefix_len"]
        candidate[body_start : body_start + row["body_capacity"]] = replacement
        target_extents.append((body_start, body_start + row["body_capacity"]))
        applied.append({
            "abs": row["abs"], "scope": row["scope"], "gap": row["gap"],
            "jp": row["jp"], "before": row["before"], "after": row["ko"],
            "prefix_hex": row["prefix"].hex().upper(),
            "payload_capacity": row["payload_capacity"], "body_capacity": row["body_capacity"],
            "strategy": "five_bank_e518_alias_reuse" if info["reused"] else "five_bank_e518_alias_new",
            "page": int(info["page"]), "physical_bank": f"{int(info['segment']):02X}",
            "local": f"{int(info['local']):04X}", "pointer": f"{int(info['pointer']):04X}",
            "token_hex": token.hex().upper(),
        })

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    target_failures: list[dict[str, Any]] = []
    for row in rows:
        start = sb + row["logical"]
        payload = candidate_bytes[start : start + row["payload_capacity"]]
        actual = candidate_dictionary.expand(payload[row["prefix_len"]:], tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload[:row["prefix_len"]] != row["prefix"]: reasons.append("prefix_changed")
        if actual != row["ko"]: reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual): reasons.append("japanese_residual")
        if candidate_bytes[start + row["payload_capacity"]] != 0: reasons.append("terminator_changed")
        if reasons:
            target_failures.append({"abs": row["abs"], "expected": row["ko"], "actual": actual, "reasons": reasons})

    invariance = verify_non_target_invariance(
        parent, candidate_bytes,
        before_dictionary=parent_dictionary, after_dictionary=candidate_dictionary,
        tbl=tbl, excluded={row["logical"] for row in rows},
    )
    runs = diff_runs(parent, candidate_bytes)
    allowed = target_extents + pointer_extents + phrase_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [{"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}"} for lo, hi in runs if not covered((lo, hi), allowed)]
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate_bytes[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2] == candidate_bytes[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    old_ext3_exact = all(parent[s * BANK_SIZE:(s + 1) * BANK_SIZE] == candidate_bytes[s * BANK_SIZE:(s + 1) * BANK_SIZE] for s in range(0x11, 0x21))
    page_hits_parent = {p: five.scan_range_hits(parent, p) for p in range(PAGES)}
    page_hits_candidate = {p: five.scan_range_hits(candidate_bytes, p) for p in range(PAGES)}
    expected_page_counts = {p: len(page_hits_parent[p]) + sum(row["page"] == p for row in applied) for p in range(PAGES)}
    checks = {
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == MAIN_SHA256,
        "parent_candidate_exact": sha256(parent) == expected_parent_sha,
        "batch_fully_approved": len(rows) == int(batch["approved_records"]),
        "all_targets_render_exact": not target_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "page_reference_counts_exact": all(len(page_hits_candidate[p]) == expected_page_counts[p] for p in range(PAGES)),
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
    }
    if not all(checks.values()):
        raise BuildError(json.dumps({"checks": checks, "target_failures": target_failures, "unaccounted": unaccounted}, ensure_ascii=False))

    out_rom = batch_path(batch_id, "candidate.wsc")
    out_save = batch_path(batch_id, "candidate.sav")
    report_path = batch_path(batch_id, "report.json")
    atomic_bytes(out_rom, candidate_bytes)
    atomic_bytes(out_save, save_snapshot)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_uncovered_translation_batch_candidate.py",
        "ok": True,
        "status": "cumulative_candidate_static_verified_pending_independent_audit_and_user_visual_test",
        "batch_id": batch_id,
        "parent_batch": parent_batch,
        "main_tip": identity(MAIN, main),
        "parent": identity(parent_path, parent),
        "candidate": identity(out_rom, candidate_bytes),
        "candidate_save": {**identity(out_save, save_snapshot), "policy": "test-only current main SaveRAM snapshot; never promote"},
        "sheet": identity(sheet_path),
        "counts": {
            "new_targets": len(rows), "new_unique_phrases": len(assignments),
            "target_failures": len(target_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "checks": checks,
        "applied": applied,
        "diff_from_parent": {
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "runs": len(runs), "checksum": f"{checksum:04X}",
        },
        "test_scope": [
            f"batch {batch_id}: " + ", ".join(batch.get("gaps") or []),
            "all lines display fully in Korean and advance without event errors",
            "save, full emulator restart, and reload",
        ],
    }
    atomic_json(report_path, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
