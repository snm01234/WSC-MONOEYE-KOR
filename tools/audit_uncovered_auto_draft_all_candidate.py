#!/usr/bin/env python3
"""Independent static audit for the 1,893-row uncovered auto-draft ROM.

This audit treats the sheet, current main TIP, and generated candidate as
independent inputs. It does not approve translation quality and never promotes
or modifies a ROM/SaveRAM.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_character_five_bank_batch02_candidate import (
    FIRST_BANK,
    PAGES,
    read_phrase,
)
from build_encyclopedia_ms_batch01_candidate import payload_at
from build_remaining_dialogue_candidate import diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text

MAIN = (
    ROOT
    / "out/patch/backup/20260804_211641_pre_uncovered_auto_draft_all/monoeye_ko_expanded.wsc"
)
LIVE_TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/uncovered_auto_draft_all_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/uncovered_auto_draft_all_candidate.sav"
SHEET = ROOT / "out/script/uncovered_translation_sheet_auto_draft.csv"
MANIFEST = ROOT / "out/patch/uncovered_auto_draft_batch_manifest.json"
TRANSLATION_REPORT = ROOT / "out/patch/uncovered_auto_draft_translation_report.json"
BUILD_REPORT = ROOT / "out/patch/uncovered_auto_draft_all_candidate_report.json"
STRUCTURE_PARENT = ROOT / "out/patch/uncovered_auto_draft_all_structure_parent.json"
STRUCTURE_CANDIDATE = ROOT / "out/patch/uncovered_auto_draft_all_structure_candidate.json"
FALSE_SEGPTR = ROOT / "out/patch/uncovered_auto_draft_all_false_segptr.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/uncovered_auto_draft_all_candidate_audit.json"

EXPECTED_MAIN = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
EXPECTED_TARGETS = 1_893
EXPECTED_DRAFT = 1_858
EXPECTED_PRESERVED = 35
EXPECTED_BATCHES = 49
EXPECTED_SHORT = 27
DRAFT_WORKFLOWS = {"draft_auto", "draft_llm_literal"}
EXPECTED_ALLOWED_PHYSICAL_BANKS = {
    *range(0x21, 0x26),
    0xD9,
    0xDC,
    0xDD,
    0xDE,
    0xDF,
    0xF7,
    0xFF,
}


class AuditError(RuntimeError):
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


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def token_page_local(token: bytes) -> tuple[int, int]:
    if len(token) != 4 or token[:2] != b"\xE5\x18":
        raise AuditError(f"not E5 18 alias token: {token.hex().upper()}")
    raw = (token[2] << 8) | token[3]
    page = raw >> 12
    local = (raw & 0x0FFF) - 0x0600
    if not 0 <= page < PAGES or not 1 <= local < 0x0A00 or (local & 0xFF) == 0:
        raise AuditError(f"unsafe alias token page={page} local={local:04X}")
    return page, local


def normalized_structure_issues(document: dict[str, Any]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            row.get("abs"),
            row.get("kind"),
            row.get("orig_terminator"),
            row.get("target_terminator"),
            row.get("delta"),
        )
        for row in document.get("first_issues") or []
    )


def main() -> int:
    main = bytes(load_rom(MAIN))
    candidate = bytes(load_rom(CANDIDATE))
    main_save = MAIN_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    live_tip = LIVE_TIP.read_bytes()
    build = load_object(BUILD_REPORT)
    expected_candidate = str((build.get("candidate") or {}).get("sha256") or "").lower()
    expected_checksum = str(build.get("checksum") or (build.get("diff") or {}).get("checksum") or "").upper()
    if not expected_candidate or len(expected_candidate) != 64:
        raise AuditError("build report missing candidate sha256")
    if not expected_checksum:
        raise AuditError("build report missing checksum")
    if sha256(main) != EXPECTED_MAIN:
        raise AuditError("sheet-bound parent TIP identity drifted")
    if sha256(candidate) != expected_candidate:
        raise AuditError("candidate identity drifted from build report")

    manifest = load_object(MANIFEST)
    translation = load_object(TRANSLATION_REPORT)
    structure_parent = load_object(STRUCTURE_PARENT)
    structure_candidate = load_object(STRUCTURE_CANDIDATE)
    false_segptr = load_object(FALSE_SEGPTR)

    with SHEET.open(encoding="utf-8-sig", newline="") as stream:
        rows = [dict(row) for row in csv.DictReader(stream)]
    if len(rows) != EXPECTED_TARGETS or len({str(row.get("abs") or "").upper() for row in rows}) != EXPECTED_TARGETS:
        raise AuditError("sheet population is not 1,893 unique addresses")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    main_dictionary = make_dictionary_ext3(main, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(main)

    target_failures: list[dict[str, Any]] = []
    target_logicals: set[int] = set()
    target_by_batch: Counter[str] = Counter()
    target_by_scope: Counter[str] = Counter()
    provenance: Counter[str] = Counter()
    strategy_counts: Counter[str] = Counter()
    alias_token_phrases: dict[tuple[int, int], set[str]] = defaultdict(set)
    alias_phrase_tokens: dict[str, set[tuple[int, int]]] = defaultdict(set)
    alias_target_locals: dict[int, set[int]] = {page: set() for page in range(PAGES)}
    short_indices: dict[str, int] = {}

    for source in sorted(rows, key=lambda row: int(str(row["abs"]), 16)):
        address = str(source["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        capacity = int(source["payload_capacity"])
        body_capacity = int(source["body_capacity"])
        expected_parent = bytes.fromhex(str(source["current_payload_hex"]))
        expected = normalize_ko_text(str(source.get("ko") or ""))
        before_payload, before_term = payload_at(main, logical)
        after_payload, after_term = payload_at(candidate, logical)
        reasons: list[str] = []

        if before_payload != expected_parent:
            reasons.append("parent_payload_not_bound")
        if len(before_payload) != capacity or len(after_payload) != capacity:
            reasons.append("payload_length_changed")
        if body_capacity != capacity - len(prefix):
            reasons.append("sheet_boundary_invalid")
        if before_term != after_term or main[before_term] != 0 or candidate[after_term] != 0:
            reasons.append("terminator_changed")
        if after_payload[: len(prefix)] != prefix:
            reasons.append("prefix_changed")
        actual = candidate_dictionary.expand(after_payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        if actual != expected:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_residual")

        workflow = str(source.get("workflow_status") or "")
        translation_source = str(source.get("translation_source") or "")
        review_status = str(source.get("review_status") or "")
        if workflow in DRAFT_WORKFLOWS:
            provenance["fresh_auto_draft"] += 1
            if review_status != "unreviewed_draft":
                reasons.append("draft_provenance_mismatch")
            if workflow == "draft_auto" and translation_source != "google_translate_fresh_draft":
                reasons.append("draft_provenance_mismatch")
            if workflow == "draft_llm_literal" and translation_source != "llm":
                reasons.append("draft_provenance_mismatch")
        elif workflow in {"candidate_pending", "approved"}:
            provenance["preserved_approved_or_candidate"] += 1
            if translation_source != "llm" or review_status != "approved":
                reasons.append("preserved_provenance_mismatch")
        else:
            reasons.append("unsupported_workflow_status")

        body = after_payload[len(prefix) :]
        if body_capacity >= 4:
            try:
                page, local = token_page_local(body[:4])
                alias_target_locals[page].add(local)
                alias_token_phrases[(page, local)].add(expected)
                alias_phrase_tokens[expected].add((page, local))
                bank_start = (FIRST_BANK + page) * BANK_SIZE
                bank = candidate[bank_start : bank_start + BANK_SIZE]
                pointer = int.from_bytes(bank[local * 2 : local * 2 + 2], "little")
                phrase_raw = read_phrase(bank, pointer)
                phrase_actual = candidate_dictionary.expand(phrase_raw, tbl).rstrip("\u3000 \t")
                if phrase_actual != expected:
                    reasons.append("alias_phrase_render_mismatch")
                if body[4:] != b"\x01" * (body_capacity - 4):
                    reasons.append("alias_padding_mismatch")
                strategy_counts["alias"] += 1
            except Exception as exc:
                reasons.append(f"alias_decode_error:{type(exc).__name__}:{exc}")
        else:
            if body_capacity != 3 or len(body) != 3 or not 0xF0 <= body[0] <= 0xFF:
                reasons.append("short_token_shape_invalid")
            else:
                index = ((body[0] - 0xF0) << 8) | body[1]
                short_indices[address] = index
                if body[2:] != b"\x01":
                    reasons.append("short_padding_mismatch")
                phrase_actual = candidate_dictionary.expand(
                    bytes(candidate_dictionary.raw_entry(index)), tbl
                ).rstrip("\u3000 \t")
                if phrase_actual != expected:
                    reasons.append("stock_phrase_render_mismatch")
                strategy_counts["stock"] += 1

        target_logicals.add(logical)
        target_by_batch[str(source.get("batch_id") or "")] += 1
        target_by_scope[str(source.get("scope") or "")] += 1
        if reasons:
            target_failures.append(
                {
                    "abs": address,
                    "expected": expected,
                    "actual": actual,
                    "reasons": reasons,
                }
            )

    manifest_counts = {
        str(batch["batch_id"]): int(batch["records"])
        for batch in manifest.get("batches") or []
    }
    alias_collision_failures = [
        {
            "page": page,
            "local": f"{local:04X}",
            "phrases": sorted(phrases),
        }
        for (page, local), phrases in sorted(alias_token_phrases.items())
        if len(phrases) != 1
    ]
    alias_duplicate_phrase_failures = [
        {"phrase": phrase, "tokens": sorted([f"{page}:{local:04X}" for page, local in tokens])}
        for phrase, tokens in sorted(alias_phrase_tokens.items())
        if len(tokens) != 1
    ]

    invariance = verify_non_target_invariance(
        main,
        candidate,
        before_dictionary=main_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_logicals,
    )
    runs = diff_runs(main, candidate)
    changed_physical_banks = sorted({left // BANK_SIZE for left, _right in runs})
    unexpected_changed_banks = sorted(
        set(changed_physical_banks) - EXPECTED_ALLOWED_PHYSICAL_BANKS
    )

    page_checks: dict[str, Any] = {}
    parent_hits = {page: five.scan_range_hits(main, page) for page in range(PAGES)}
    candidate_hits = {page: five.scan_range_hits(candidate, page) for page in range(PAGES)}
    for page in range(PAGES):
        expected_increment = sum(
            1
            for source in rows
            if int(source["body_capacity"]) >= 4
            and token_page_local(
                payload_at(candidate, int(source["abs"], 16))[0][
                    len(bytes.fromhex(source["prefix_hex"])) :
                ][:4]
            )[0]
            == page
        )
        page_checks[str(page)] = {
            "physical_bank": f"{FIRST_BANK + page:02X}",
            "parent_references": len(parent_hits[page]),
            "candidate_references": len(candidate_hits[page]),
            "expected_candidate_references": len(parent_hits[page]) + expected_increment,
            "target_unique_locals": len(alias_target_locals[page]),
            "ok": len(candidate_hits[page]) == len(parent_hits[page]) + expected_increment,
        }

    runtime_exact = (
        main[sb + 0x7A0000 : sb + 0x7B0000]
        == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and main[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    old_ext3_exact = all(
        main[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        == candidate[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    checksum_copy = bytearray(candidate)
    checksum_value = update_ws_checksum(checksum_copy)
    checksum_hex = f"{checksum_value:04X}"

    structure_exact = (
        int(structure_parent.get("issues") or -1) == 27
        and int(structure_candidate.get("issues") or -1) == 27
        and structure_parent.get("by_kind") == structure_candidate.get("by_kind")
        and normalized_structure_issues(structure_parent)
        == normalized_structure_issues(structure_candidate)
    )
    build_checks = build.get("checks") or {}
    live_tip_sha = str((build.get("live_tip_at_build") or {}).get("sha256") or "").lower()
    checks = {
        "parent_identity_exact": sha256(main) == EXPECTED_MAIN,
        "candidate_identity_exact": sha256(candidate) == expected_candidate,
        "sheet_exactly_1893_unique": len(rows) == len(target_logicals) == EXPECTED_TARGETS,
        "draft_rows_exactly_1858": provenance["fresh_auto_draft"] == EXPECTED_DRAFT,
        "preserved_rows_exactly_35": provenance["preserved_approved_or_candidate"] == EXPECTED_PRESERVED,
        "all_49_batches_exact": dict(target_by_batch) == manifest_counts and len(target_by_batch) == EXPECTED_BATCHES,
        "short_rows_exactly_27": len(short_indices) == EXPECTED_SHORT,
        "all_targets_exact": not target_failures,
        "alias_tokens_do_not_collide": not alias_collision_failures,
        "each_alias_phrase_uses_one_token": not alias_duplicate_phrase_failures,
        "all_page_reference_counts_exact": all(info["ok"] for info in page_checks.values()),
        "non_target_invariance": invariance.get("ok") is True,
        "changed_physical_banks_expected_only": not unexpected_changed_banks,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "checksum_exact": bytes(checksum_copy) == candidate and checksum_hex == expected_checksum,
        "candidate_saveram_exact_snapshot": len(candidate_save) == 32768 and candidate_save == main_save,
        "build_report_bound_and_clean": (
            build.get("ok") is True
            and build.get("promotion_allowed") is False
            and str((build.get("candidate") or {}).get("sha256") or "").lower()
            == expected_candidate
            and all(bool(value) for value in build_checks.values())
        ),
        "translation_report_nonpromotable": (
            translation.get("ok") is True
            and translation.get("promotion_allowed") is False
            and int((translation.get("counts") or {}).get("fresh_auto_draft") or -1)
            == EXPECTED_DRAFT
        ),
        "structure_regression_zero": structure_exact,
        "false_segmented_pointer_zero": (
            false_segptr.get("ok") is True
            and int(false_segptr.get("sites_found") or 0) == 0
        ),
        "parent_backup_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_MAIN,
        "live_tip_unchanged_since_build": (
            not live_tip_sha or sha256(live_tip) == live_tip_sha
        ),
        "live_saveram_unchanged": MAIN_SAVE.read_bytes() == main_save,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_uncovered_auto_draft_all_candidate.py",
        "read_only": True,
        "ok": ok,
        "canonical": False,
        "promotion_allowed": False,
        "status": (
            "all_batches_static_verified_unreviewed_draft_pending_user_runtime_test"
            if ok
            else "failed"
        ),
        "warning": "Translation quality is not approved: 1,858 rows remain unreviewed drafts.",
        "inputs": {
            "parent": identity(MAIN, main),
            "live_tip": identity(LIVE_TIP, live_tip),
            "candidate": identity(CANDIDATE, candidate),
            "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
            "sheet": identity(SHEET),
            "manifest": identity(MANIFEST),
            "translation_report": identity(TRANSLATION_REPORT),
            "build_report": identity(BUILD_REPORT),
            "structure_parent": identity(STRUCTURE_PARENT),
            "structure_candidate": identity(STRUCTURE_CANDIDATE),
            "false_segptr": identity(FALSE_SEGPTR),
        },
        "counts": {
            "targets": len(rows),
            "batches": len(target_by_batch),
            "fresh_auto_draft": provenance["fresh_auto_draft"],
            "preserved_approved_or_candidate": provenance["preserved_approved_or_candidate"],
            "alias_records": strategy_counts["alias"],
            "stock_records": strategy_counts["stock"],
            "short_indices": len(short_indices),
            "target_failures": len(target_failures),
            "alias_collision_failures": len(alias_collision_failures),
            "alias_duplicate_phrase_failures": len(alias_duplicate_phrase_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "diff_runs": len(runs),
            "unexpected_changed_banks": len(unexpected_changed_banks),
            "structure_parent_issues": int(structure_parent.get("issues") or 0),
            "structure_candidate_issues": int(structure_candidate.get("issues") or 0),
            "false_segmented_pointer_writes": int(false_segptr.get("sites_found") or 0),
        },
        "batch_counts": dict(sorted(target_by_batch.items())),
        "scope_counts": dict(sorted(target_by_scope.items())),
        "page_checks": page_checks,
        "short_indices": {
            address: f"{index:04X}" for address, index in sorted(short_indices.items())
        },
        "changed_physical_banks": [f"{bank:02X}" for bank in changed_physical_banks],
        "unexpected_changed_physical_banks": [
            f"{bank:02X}" for bank in unexpected_changed_banks
        ],
        "checksum": {
            "expected": expected_checksum,
            "recomputed": checksum_hex,
            "stored_bytes": candidate[-2:].hex().upper(),
        },
        "checks": checks,
        "target_failures": target_failures,
        "alias_collision_failures": alias_collision_failures,
        "alias_duplicate_phrase_failures": alias_duplicate_phrase_failures,
        "invariance": invariance,
        "promotion": "blocked_unreviewed_draft_and_pending_runtime_validation",
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "status": report["status"],
                "counts": report["counts"],
                "scope_counts": report["scope_counts"],
                "changed_physical_banks": report["changed_physical_banks"],
                "checksum": report["checksum"],
                "checks": checks,
                "out": str(OUT.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not ok:
        raise AuditError("uncovered auto-draft candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
