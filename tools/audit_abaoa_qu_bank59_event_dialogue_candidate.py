#!/usr/bin/env python3
"""Independent static audit for the A Baoa Qu bank59 dialogue candidate."""
from __future__ import annotations

import hashlib
import json
import sys
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
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/abaoa_qu_bank59_event_dialogue_candidate.sav"
WORKLIST = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_worklist.json"
CATALOG = ROOT / "data/abaoa_qu_bank59_event_dialogue_ko.json"
BUILD_REPORT = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_candidate_audit.json"

EXPECTED_PARENT = "186d6d04859146420f4ae826a38a0d35377633d54587046c30046125cbceb241"
EXPECTED_CANDIDATE = "fbfdd1fb231d1e684729db79ed42007c13a48db3c81cab4155fede29bba1b973"
EXPECTED_TARGETS = 257
EXPECTED_UNIQUE = 250
EXPECTED_NEW_LOCALS = [50, 50, 50, 50, 50]
EXPECTED_PARENT_PAGE_COUNTS = [141, 118, 119, 122, 123]
EXPECTED_CANDIDATE_PAGE_COUNTS = [195, 168, 169, 174, 174]
SAVE_SIZE = 32_768


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.relative_to(ROOT)), "size": len(payload), "sha256": sha256(payload)}


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def token_page_local(token: bytes) -> tuple[int, int]:
    if len(token) != 4 or token[:2] != b"\xE5\x18":
        raise AuditError(f"not E5 18 token: {token.hex().upper()}")
    raw = (token[2] << 8) | token[3]
    page = raw >> 12
    local = (raw & 0x0FFF) - 0x0600
    if not 0 <= page < PAGES or not 1 <= local < 0x0A00 or (local & 0xFF) == 0:
        raise AuditError(f"unsafe alias page/local: page={page} local={local:04X}")
    return page, local


def main() -> int:
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    if sha256(parent) != EXPECTED_PARENT:
        raise AuditError("parent identity drifted")
    if sha256(candidate) != EXPECTED_CANDIDATE:
        raise AuditError("candidate identity drifted")

    worklist = load_object(WORKLIST)
    catalog = load_object(CATALOG)
    build = load_object(BUILD_REPORT)
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)

    source_rows = [dict(row) for row in worklist.get("records") or []]
    catalog_rows = [dict(row) for row in catalog.get("lines") or []]
    source_by_abs = {str(row.get("abs") or "").upper(): row for row in source_rows}
    catalog_by_abs = {str(row.get("abs") or "").upper(): row for row in catalog_rows}
    if not (
        len(source_rows)
        == len(source_by_abs)
        == len(catalog_rows)
        == len(catalog_by_abs)
        == EXPECTED_TARGETS
    ):
        raise AuditError("target population is not unique")
    if set(source_by_abs) != set(catalog_by_abs):
        raise AuditError("catalog address set differs from worklist")

    target_failures: list[dict[str, Any]] = []
    target_logicals: set[int] = set()
    target_extents: list[tuple[int, int]] = []
    phrase_to_token: dict[str, tuple[int, int]] = {}
    token_to_phrase: dict[tuple[int, int], str] = {}
    target_locals: dict[int, set[int]] = {page: set() for page in range(PAGES)}

    for address in sorted(source_by_abs, key=lambda value: int(value, 16)):
        source = source_by_abs[address]
        line = catalog_by_abs[address]
        logical = int(address, 16)
        expected = normalize_ko_text(str(line.get("ko") or ""))
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source.get("payload_capacity") or -1)
        body_capacity = int(source.get("body_capacity") or -1)
        before_payload = parent[sb + logical : sb + logical + payload_capacity]
        after_payload = candidate[sb + logical : sb + logical + payload_capacity]
        reasons: list[str] = []
        if before_payload != bytes.fromhex(str(source.get("current_payload_hex") or "")):
            reasons.append("parent_payload_not_bound")
        if payload_capacity != len(prefix) + body_capacity or body_capacity < 4:
            reasons.append("invalid_boundary")
        if before_payload[: len(prefix)] != prefix or after_payload[: len(prefix)] != prefix:
            reasons.append("prefix_changed")
        if parent[sb + logical + payload_capacity] != 0 or candidate[sb + logical + payload_capacity] != 0:
            reasons.append("terminator_changed")

        after_body = after_payload[len(prefix) :]
        rendered = candidate_dictionary.expand(after_body, tbl).rstrip("\u3000 \t")
        if rendered != expected:
            reasons.append("render_mismatch")
        if any(is_japanese_character(character) for character in rendered):
            reasons.append("japanese_residual")
        try:
            page, local = token_page_local(after_body[:4])
        except AuditError as exc:
            reasons.append(str(exc))
            page = local = -1
        if after_body[4:] != b"\x01" * (body_capacity - 4):
            reasons.append("padding_mismatch")
        if page >= 0:
            segment = FIRST_BANK + page
            bank = candidate[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
            pointer = int.from_bytes(bank[local * 2 : local * 2 + 2], "little")
            raw = read_phrase(bank, pointer)
            phrase_rendered = candidate_dictionary.expand(raw, tbl).rstrip("\u3000 \t")
            if phrase_rendered != expected:
                reasons.append("bank_phrase_render_mismatch")
            previous_token = phrase_to_token.get(expected)
            if previous_token is not None and previous_token != (page, local):
                reasons.append("same_phrase_uses_multiple_tokens")
            previous_phrase = token_to_phrase.get((page, local))
            if previous_phrase is not None and previous_phrase != expected:
                reasons.append("token_aliases_different_phrases")
            phrase_to_token[expected] = (page, local)
            token_to_phrase[(page, local)] = expected
            target_locals[page].add(local)

        target_logicals.add(logical)
        body_start = sb + logical + len(prefix)
        target_extents.append((body_start, body_start + body_capacity))
        if reasons:
            target_failures.append(
                {"abs": address, "expected": expected, "actual": rendered, "reasons": reasons}
            )

    parent_page_hits = {page: five.scan_range_hits(parent, page) for page in range(PAGES)}
    candidate_page_hits = {page: five.scan_range_hits(candidate, page) for page in range(PAGES)}
    new_local_counts: list[int] = []
    pointer_extents: list[tuple[int, int]] = []
    phrase_extents: list[tuple[int, int]] = []
    bank_checks: dict[str, dict[str, Any]] = {}
    for page in range(PAGES):
        segment = FIRST_BANK + page
        start = segment * BANK_SIZE
        parent_bank = parent[start : start + BANK_SIZE]
        candidate_bank = candidate[start : start + BANK_SIZE]
        old_used: set[int] = set()
        new_used: set[int] = set()
        old_tail = 0x2001
        new_tail = 0x2001
        for local in range(0x1000):
            old_pointer = int.from_bytes(parent_bank[local * 2 : local * 2 + 2], "little")
            new_pointer = int.from_bytes(candidate_bank[local * 2 : local * 2 + 2], "little")
            if old_pointer != 0x2000:
                old_used.add(local)
                old_tail = max(old_tail, old_pointer + len(read_phrase(parent_bank, old_pointer)) + 1)
            if new_pointer != 0x2000:
                new_used.add(local)
                new_tail = max(new_tail, new_pointer + len(read_phrase(candidate_bank, new_pointer)) + 1)
        new_locals = new_used - old_used
        new_local_counts.append(len(new_locals))
        pointer_extents.extend(
            (start + local * 2, start + local * 2 + 2) for local in sorted(new_locals)
        )
        if new_tail > old_tail:
            phrase_extents.append((start + old_tail, start + new_tail))
        bank_checks[f"{segment:02X}"] = {
            "new_locals": len(new_locals),
            "target_locals": len(target_locals[page]),
            "target_locals_match": new_locals == target_locals[page],
            "old_pointer_entries_exact": all(
                parent_bank[local * 2 : local * 2 + 2]
                == candidate_bank[local * 2 : local * 2 + 2]
                for local in range(0x1000)
                if local not in new_locals
            ),
            "old_phrase_area_exact": parent_bank[0x2000:old_tail]
            == candidate_bank[0x2000:old_tail],
            "tail_after_new_exact": parent_bank[new_tail:] == candidate_bank[new_tail:],
            "old_tail": f"{old_tail:04X}",
            "new_tail": f"{new_tail:04X}",
        }

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded=target_logicals,
    )

    runs = diff_runs(parent, candidate)
    allowed = target_extents + pointer_extents + phrase_extents + [
        (len(parent) - 2, len(parent))
    ]
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000]
        == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    old_ext3_exact = all(
        parent[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        == candidate[segment * BANK_SIZE : (segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    checksum_probe = bytearray(candidate)
    expected_checksum = update_ws_checksum(checksum_probe)
    checksum_exact = bytes(checksum_probe) == candidate

    screenshot_expected = {
        "5905C3": "전에　전투가　끝났을　때、",
        "5905D2": "시그가　뭔가　말하려다　말았지？",
        "59074E": "아、아니야！！　나는……",
    }
    screenshot_exact = all(
        normalize_ko_text(str(catalog_by_abs[address]["ko"])) == expected
        for address, expected in screenshot_expected.items()
    )

    checks = {
        "parent_identity_exact": sha256(parent) == EXPECTED_PARENT,
        "candidate_identity_exact": sha256(candidate) == EXPECTED_CANDIDATE,
        "build_report_bound": (
            build.get("ok") is True
            and str((build.get("parent") or {}).get("sha256")) == EXPECTED_PARENT
            and str((build.get("candidate") or {}).get("sha256")) == EXPECTED_CANDIDATE
        ),
        "targets_257": len(source_rows) == EXPECTED_TARGETS,
        "unique_phrases_250": len(phrase_to_token) == EXPECTED_UNIQUE,
        "all_targets_exact": not target_failures,
        "screenshot_anchors_exact": screenshot_exact,
        "new_locals_50_each": new_local_counts == EXPECTED_NEW_LOCALS,
        "parent_page_counts_exact": [len(parent_page_hits[p]) for p in range(PAGES)]
        == EXPECTED_PARENT_PAGE_COUNTS,
        "candidate_page_counts_exact": [len(candidate_page_hits[p]) for p in range(PAGES)]
        == EXPECTED_CANDIDATE_PAGE_COUNTS,
        "selected_bank_changes_exact": all(
            row["target_locals_match"]
            and row["old_pointer_entries_exact"]
            and row["old_phrase_area_exact"]
            and row["tail_after_new_exact"]
            for row in bank_checks.values()
        ),
        "non_target_invariance": invariance.get("ok") is True,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "checksum_exact": checksum_exact,
        "candidate_saveram_present_and_sized": (
            CANDIDATE_SAVE.is_file() and CANDIDATE_SAVE.stat().st_size == SAVE_SIZE
        ),
        "main_tip_unchanged": sha256(PARENT.read_bytes()) == EXPECTED_PARENT,
    }
    ok = all(checks.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_abaoa_qu_bank59_event_dialogue_candidate.py",
        "read_only": True,
        "ok": ok,
        "status": "candidate_static_verified_pending_user_visual_test" if ok else "audit_failed",
        "parent": identity(PARENT, parent),
        "candidate": identity(CANDIDATE, candidate),
        "counts": {
            "targets": len(source_rows),
            "unique_tokens": len(token_to_phrase),
            "target_failures": len(target_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "diff_runs": len(runs),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "runtime": {
            "parent_page_counts": {str(p): len(parent_page_hits[p]) for p in range(PAGES)},
            "candidate_page_counts": {str(p): len(candidate_page_hits[p]) for p in range(PAGES)},
            "new_local_counts": new_local_counts,
            "banks": bank_checks,
        },
        "screenshot_anchors": screenshot_expected,
        "checksum": f"{expected_checksum:04X}",
        "checks": checks,
        "target_failures": target_failures,
        "non_target_invariance": invariance,
        "unaccounted_diff_runs": unaccounted,
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
                "runtime": report["runtime"],
                "checks": checks,
                "out": str(OUT.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    if not ok:
        raise AuditError("A Baoa Qu candidate static audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
