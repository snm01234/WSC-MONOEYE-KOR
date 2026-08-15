#!/usr/bin/env python3
"""Build one candidate containing every remaining character-encyclopedia row.

Parent: promoted five-bank runtime TIP.  All 596 records with at least four
payload bytes use only the already user-validated E5 18 aliases in physical
banks 21..25.  The seven 2-3 byte records use an existing exact stock phrase or
an independently proven unreachable retired stock slot.  Runtime code, old ext3
banks 11..20, the main TIP, and live SaveRAM are never modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_encyclopedia_character_five_bank_batch02_candidate import (
    FIRST_BANK,
    PAGES,
    alias_token,
    inspect_bank,
    read_phrase,
)
from build_encyclopedia_ms_batch01_candidate import exact_slots, payload_at
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import (
    covered,
    diff_runs,
    encode_phrase,
    verify_non_target_invariance,
)
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CATALOG = ROOT / "data/encyclopedia_character_batch01_ko.json"
RESIDUAL = ROOT / "out/patch/encyclopedia_character_current_residual_audit.json"
CATALOG_VALIDATION = ROOT / "out/patch/encyclopedia_character_current_catalog_validation.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/encyclopedia_character_all_remaining_candidate.wsc"
OUT_SAVE = ROOT / "sram/encyclopedia_character_all_remaining_candidate.sav"
REPORT = ROOT / "out/patch/encyclopedia_character_all_remaining_report.json"

EXPECTED_PARENT_SHA256 = "05a2124e5402c8e8f46b1ca7af3c131748a034939af7c6381cdbb4242d43f65e"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TARGETS = 603
EXPECTED_EXT3_RECORDS = 596
EXPECTED_SHORT_RECORDS = 7
EXPECTED_EXT3_UNIQUE = 579
EXPECTED_SHORT_EXACT = 2
EXPECTED_SHORT_NEW = 5
EXPECTED_KIM = "5C096F"


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"JSON root must be object: {path}")
    return value


def load_rows(parent: bytes, tbl: Tbl) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    catalog = load_object(CATALOG)
    residual = load_object(RESIDUAL)
    validation = load_object(CATALOG_VALIDATION)
    if validation.get("ok") is not True:
        raise BuildError("current character catalog validation did not pass")
    if str((validation.get("tip") or {}).get("sha256", "")).lower() != EXPECTED_PARENT_SHA256:
        raise BuildError("catalog validation is not bound to current parent TIP")
    provenance = catalog.get("provenance") or {}
    if not (
        provenance.get("translation_source") == "llm"
        and provenance.get("review_status") == "approved"
        and provenance.get("legacy_machine_translation_used") is False
    ):
        raise BuildError("character catalog provenance is not approved")

    catalog_rows = [dict(row) for row in catalog.get("lines") or []]
    catalog_by_abs = {str(row.get("abs") or "").upper(): row for row in catalog_rows}
    residual_rows = [
        dict(row)
        for row in residual.get("records") or []
        if row.get("status") in {"japanese_residual", "name_alias_mismatch"}
    ]
    if len(residual_rows) != EXPECTED_TARGETS:
        raise BuildError(f"remaining population drifted: {len(residual_rows)}")

    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sorted(residual_rows, key=lambda row: int(str(row["abs"]), 16)):
        address = str(source.get("abs") or "").upper()
        if address in seen or address not in catalog_by_abs:
            raise BuildError(f"duplicate or uncatalogued residual row: {address}")
        seen.add(address)
        line = catalog_by_abs[address]
        if line.get("translation_source") != "llm" or line.get("review_status") != "approved":
            raise BuildError(f"line provenance drifted at {address}")
        jp = str(line.get("jp") or "")
        ko = normalize_ko_text(str(line.get("ko") or ""))
        encoded = encode_phrase(ko, tbl)
        if str(source.get("jp") or "") != jp:
            raise BuildError(f"Japanese source mismatch at {address}")
        if not ko or any(is_japanese_character(character) for character in ko):
            raise BuildError(f"invalid Korean target at {address}: {ko!r}")
        if int(line.get("visual_cells") or -1) != len(ko) or len(ko) > 13:
            raise BuildError(f"visual width failure at {address}: {ko!r}")
        if int(line.get("encoded_bytes") or -1) != len(encoded) or b"\x00" in encoded:
            raise BuildError(f"encoding evidence mismatch at {address}")
        logical = int(address, 16)
        payload, terminator = payload_at(parent, logical)
        expected_payload = bytes.fromhex(str(source.get("current_payload_hex") or ""))
        if payload != expected_payload or len(payload) != int(source.get("payload_len") or -1):
            raise BuildError(f"parent payload drifted at {address}")
        if terminator != stock_base(parent) + logical + len(payload) or parent[terminator] != 0:
            raise BuildError(f"terminator drifted at {address}")
        prepared.append(
            {
                "abs": address,
                "logical": logical,
                "jp": jp,
                "current": source.get("current"),
                "ko": ko,
                "encoded": encoded,
                "payload": payload,
                "payload_len": len(payload),
            }
        )

    ext3_rows = [row for row in prepared if int(row["payload_len"]) >= 4]
    short_rows = [row for row in prepared if int(row["payload_len"]) < 4]
    if len(ext3_rows) != EXPECTED_EXT3_RECORDS or len(short_rows) != EXPECTED_SHORT_RECORDS:
        raise BuildError("ext3/short population drifted")
    if EXPECTED_KIM not in {row["abs"] for row in short_rows}:
        raise BuildError("Kim short record is missing from the target population")
    if len({row["ko"] for row in ext3_rows}) != EXPECTED_EXT3_UNIQUE:
        raise BuildError("ext3 unique phrase population drifted")
    return ext3_rows, short_rows


def allocate_ext3(
    parent: bytes,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    states = {page: inspect_bank(parent, page) for page in range(PAGES)}
    phrase_order: list[str] = []
    encoded_by_phrase: dict[str, bytes] = {}
    for row in rows:
        phrase = str(row["ko"])
        if phrase not in encoded_by_phrase:
            phrase_order.append(phrase)
            encoded_by_phrase[phrase] = bytes(row["encoded"])

    # Reuse is allowed only when raw phrase bytes are exactly identical.
    exact_existing: dict[bytes, dict[str, Any]] = {}
    for page, state in states.items():
        bank = state["bank"]
        for local in sorted(state["used_before"]):
            pointer = int.from_bytes(bank[local * 2:local * 2 + 2], "little")
            exact_existing.setdefault(
                read_phrase(bank, pointer),
                {"page": page, "segment": int(state["segment"]), "local": local, "pointer": pointer},
            )

    assignments: dict[str, dict[str, Any]] = {}
    next_page = 0
    for phrase in phrase_order:
        encoded = encoded_by_phrase[phrase]
        if encoded in exact_existing:
            info = dict(exact_existing[encoded])
            info.update({"encoded": encoded, "token": alias_token(int(info["page"]), int(info["local"])), "reused": True})
            assignments[phrase] = info
            continue
        page = next_page % PAGES
        next_page += 1
        state = states[page]
        if not state["free"]:
            raise BuildError(f"bank{int(state['segment']):02X} alias slots exhausted")
        local = int(state["free"].pop(0))
        pointer = int(state["cursor"])
        end = pointer + len(encoded)
        if end + 1 > BANK_SIZE:
            raise BuildError(f"bank{int(state['segment']):02X} phrase storage exhausted")
        bank = state["bank"]
        struct.pack_into("<H", bank, local * 2, pointer)
        bank[pointer:end] = encoded
        bank[end] = 0
        state["cursor"] = end + 1
        assignments[phrase] = {
            "page": page,
            "segment": int(state["segment"]),
            "local": local,
            "pointer": pointer,
            "encoded": encoded,
            "token": alias_token(page, local),
            "reused": False,
        }
    return assignments, states


def main() -> int:
    parent = bytes(load_rom(MAIN))
    main_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise BuildError("main SaveRAM is missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    ext3_rows, short_rows = load_rows(parent, tbl)

    assignments, states = allocate_ext3(parent, ext3_rows)
    candidate = bytearray(parent)
    ext3_pointer_extents: list[tuple[int, int]] = []
    ext3_phrase_extents: list[tuple[int, int]] = []
    for page, state in states.items():
        start = int(state["start"])
        candidate[start:start + BANK_SIZE] = state["bank"]
        new_locals = {
            int(info["local"])
            for info in assignments.values()
            if int(info["page"]) == page and not bool(info["reused"])
        }
        ext3_pointer_extents.extend(
            (start + local * 2, start + local * 2 + 2) for local in sorted(new_locals)
        )
        if int(state["cursor"]) > int(state["cursor_before"]):
            ext3_phrase_extents.append(
                (start + int(state["cursor_before"]), start + int(state["cursor"]))
            )

    short_phrases = {str(row["ko"]) for row in short_rows}
    exact = exact_slots(parent_dictionary, tbl, short_phrases)
    reusable = {phrase: slots for phrase, slots in exact.items() if slots}
    new_short_phrases = sorted(short_phrases - set(reusable))
    retired = current_strong_retired_slots(original, parent, parent_dictionary)
    selected_retired = retired[: len(new_short_phrases)]
    if len(reusable) != EXPECTED_SHORT_EXACT or len(selected_retired) != EXPECTED_SHORT_NEW:
        raise BuildError("short exact/retired allocation population drifted")
    selected_set = set(selected_retired)
    current_external = external_occurrence_map(parent, ext3_aware=True, wanted=selected_set)
    current_nested = nested_occurrence_map(parent_dictionary, wanted=selected_set, ext3_aware=True)
    current_raw = _raw_pair_hits(parent, selected_retired)
    if any(current_external.get(i) or current_nested.get(i) or current_raw.get(i) for i in selected_retired):
        raise BuildError("selected retired stock slot is still reachable")

    stock_assignment = {phrase: min(slots) for phrase, slots in reusable.items()}
    stock_payloads: dict[int, bytes] = {}
    for phrase, index in zip(new_short_phrases, selected_retired):
        stock_assignment[phrase] = index
        stock_payloads[index] = encode_phrase(phrase, tbl)

    stock_cursor_before = _stock_phrase_cursor(candidate)
    pointers_before = list(Dictionary(candidate).ptrs)
    pointers_written, stock_cursor_after = write_dictionary_slots_spill(
        candidate,
        stock_payloads,
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=_working_two_byte_external_refs(bytes(candidate)),
    )
    pointers_after = list(Dictionary(candidate).ptrs)
    if pointers_after != pointers_written:
        raise BuildError("stock writer pointer result differs from ROM")
    changed_pointer_indices = {
        index
        for index, (before, after) in enumerate(zip(pointers_before, pointers_after))
        if before != after
    }
    if changed_pointer_indices != set(stock_payloads):
        raise BuildError("stock pointer change set differs from selected retired slots")

    sb = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in ext3_rows + short_rows:
        logical = int(row["logical"])
        capacity = int(row["payload_len"])
        phrase = str(row["ko"])
        if capacity >= 4:
            info = assignments[phrase]
            token = bytes(info["token"])
            strategy = "five_bank_e518_alias_reuse" if info["reused"] else "five_bank_e518_alias_new"
            allocation = {
                "page": int(info["page"]),
                "physical_bank": f"{int(info['segment']):02X}",
                "local": f"{int(info['local']):04X}",
                "pointer": f"{int(info['pointer']):04X}",
            }
        else:
            index = int(stock_assignment[phrase])
            token = token_from_dict_index(index)
            strategy = "existing_exact_stock" if phrase in reusable else "strong_retired_stock"
            allocation = {"stock_index": f"{index:04X}"}
        replacement = token + b"\x01" * (capacity - len(token))
        if len(replacement) != capacity:
            raise BuildError(f"replacement length drift at {logical:06X}")
        start = sb + logical
        candidate[start:start + capacity] = replacement
        target_extents.append((start, start + capacity))
        applied.append(
            {
                "abs": f"{logical:06X}",
                "jp": row["jp"],
                "before": row["current"],
                "after": phrase,
                "payload_len": capacity,
                "strategy": strategy,
                **allocation,
                "token_hex": token.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    target_failures: list[dict[str, Any]] = []
    for row in applied:
        logical = int(row["abs"], 16)
        payload, terminator = payload_at(candidate_bytes, logical)
        rendered = candidate_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if rendered != row["after"]:
            reasons.append("render_mismatch")
        if any(is_japanese_character(character) for character in rendered):
            reasons.append("japanese_residual")
        if len(payload) != int(row["payload_len"]):
            reasons.append("payload_length_changed")
        if terminator != sb + logical + int(row["payload_len"]) or candidate_bytes[terminator] != 0:
            reasons.append("terminator_changed")
        if reasons:
            target_failures.append(
                {"abs": row["abs"], "expected": row["after"], "actual": rendered, "reasons": reasons}
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in ext3_rows + short_rows},
    )

    candidate_external = external_occurrence_map(candidate_bytes, ext3_aware=True, wanted=selected_set)
    candidate_nested = nested_occurrence_map(candidate_dictionary, wanted=selected_set, ext3_aware=True)
    expected_new_sites = {
        int(row["stock_index"], 16): {row["abs"]}
        for row in applied
        if row["strategy"] == "strong_retired_stock"
    }
    retired_reference_failures: list[dict[str, Any]] = []
    for index in selected_retired:
        actual_sites = {str(ref.get("record_abs") or "").upper() for ref in candidate_external.get(index, [])}
        if actual_sites != expected_new_sites.get(index, set()) or candidate_nested.get(index):
            retired_reference_failures.append(
                {
                    "stock_index": f"{index:04X}",
                    "expected_sites": sorted(expected_new_sites.get(index, set())),
                    "actual_sites": sorted(actual_sites),
                    "nested": candidate_nested.get(index, []),
                }
            )

    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (
            stock_bank_file + DICT_PTR_START + index * 2,
            stock_bank_file + DICT_PTR_START + index * 2 + 2,
        )
        for index in stock_payloads
    ]
    stock_phrase_extent = (stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after)
    runs = diff_runs(parent, candidate_bytes)
    allowed = (
        target_extents
        + ext3_pointer_extents
        + ext3_phrase_extents
        + stock_pointer_extents
        + [stock_phrase_extent, (len(parent) - 2, len(parent))]
    )
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]

    runtime_exact = (
        parent[sb + 0x7A0000:sb + 0x7B0000]
        == candidate_bytes[sb + 0x7A0000:sb + 0x7B0000]
        and parent[sb + 0x7F0000:sb + 0x800000 - 2]
        == candidate_bytes[sb + 0x7F0000:sb + 0x800000 - 2]
    )
    old_ext3_exact = all(
        parent[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        == candidate_bytes[segment * BANK_SIZE:(segment + 1) * BANK_SIZE]
        for segment in range(0x11, 0x21)
    )
    page_hits_parent = {page: five.scan_range_hits(parent, page) for page in range(PAGES)}
    page_hits_candidate = {page: five.scan_range_hits(candidate_bytes, page) for page in range(PAGES)}
    expected_page_counts = {
        page: len(page_hits_parent[page])
        + sum(int(row.get("page", -1)) == page for row in applied if row["strategy"].startswith("five_bank"))
        for page in range(PAGES)
    }
    page_counts_exact = all(
        len(page_hits_candidate[page]) == expected_page_counts[page] for page in range(PAGES)
    )

    kim_row = next(row for row in applied if row["abs"] == EXPECTED_KIM)
    checks = {
        "parent_tip_exact": sha256(parent) == EXPECTED_PARENT_SHA256,
        "targets_603": len(applied) == EXPECTED_TARGETS,
        "ext3_records_596": len(ext3_rows) == EXPECTED_EXT3_RECORDS,
        "short_records_7": len(short_rows) == EXPECTED_SHORT_RECORDS,
        "ext3_unique_579": len(assignments) == EXPECTED_EXT3_UNIQUE,
        "short_exact_2": len(reusable) == EXPECTED_SHORT_EXACT,
        "short_new_retired_5": len(stock_payloads) == EXPECTED_SHORT_NEW,
        "kim_uses_existing_exact_stock": (
            kim_row["strategy"] == "existing_exact_stock"
            and kim_row.get("stock_index") == "03BF"
            and kim_row["after"] == "킴"
        ),
        "all_targets_render_exact": not target_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "retired_stock_references_exact": not retired_reference_failures,
        "page_reference_counts_exact": page_counts_exact,
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_PARENT_SHA256,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == main_save,
    }
    ok = all(checks.values())
    if not ok:
        print(
            json.dumps(
                {
                    "checks": checks,
                    "target_failures": target_failures[:20],
                    "invariance": invariance,
                    "retired_reference_failures": retired_reference_failures,
                    "unaccounted": unaccounted[:20],
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        raise BuildError("all-remaining character candidate verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    bank_reports: list[dict[str, Any]] = []
    for page, state in states.items():
        new_infos = [
            info for info in assignments.values()
            if int(info["page"]) == page and not bool(info["reused"])
        ]
        refs_added = sum(
            int(row.get("page", -1)) == page
            for row in applied
            if row["strategy"].startswith("five_bank")
        )
        bank_reports.append(
            {
                "page": page,
                "physical_bank": f"{int(state['segment']):02X}",
                "new_slots": len(new_infos),
                "new_record_references": refs_added,
                "reference_count_before": len(page_hits_parent[page]),
                "reference_count_after": len(page_hits_candidate[page]),
                "cursor_before": f"{int(state['cursor_before']):04X}",
                "cursor_after": f"{int(state['cursor']):04X}",
                "phrase_bytes_added": int(state["cursor"]) - int(state["cursor_before"]),
                "phrase_room_after": BANK_SIZE - int(state["cursor"]),
            }
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_encyclopedia_character_all_remaining_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_independent_audit_and_user_visual_test",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {
            **identity(OUT_SAVE),
            "policy": "mutable test-only SaveRAM; never promote to main",
        },
        "source_catalog": identity(CATALOG),
        "source_residual": identity(RESIDUAL),
        "source_validation": identity(CATALOG_VALIDATION),
        "counts": {
            "targets": len(applied),
            "ext3_records": len(ext3_rows),
            "short_stock_records": len(short_rows),
            "ext3_unique_phrases": len(assignments),
            "ext3_existing_phrase_reuse": sum(bool(info["reused"]) for info in assignments.values()),
            "short_unique_phrases": len(short_phrases),
            "short_existing_exact_phrases": len(reusable),
            "short_new_retired_phrases": len(stock_payloads),
            "target_failures": len(target_failures),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "allocation": {
            "runtime": "existing user-validated E5 18 five-bank aliases only",
            "banks": bank_reports,
            "stock_cursor_before": f"{stock_cursor_before:04X}",
            "stock_cursor_after": f"{stock_cursor_after:04X}",
            "stock_phrase_bytes_added": stock_cursor_after - stock_cursor_before,
            "selected_retired_slots": [f"{index:04X}" for index in selected_retired],
            "existing_exact_slots": {
                phrase: [f"{index:04X}" for index in slots]
                for phrase, slots in sorted(reusable.items())
            },
        },
        "kim": kim_row,
        "verification": {
            "checks": checks,
            "target_failures": target_failures,
            "non_target_invariance": invariance,
            "retired_reference_failures": retired_reference_failures,
            "unaccounted_diff_runs": unaccounted,
        },
        "diff": {
            "changed_bytes": sum(right - left for left, right in runs),
            "runs": len(runs),
            "checksum": f"{checksum:04X}",
        },
        "applied": sorted(applied, key=lambda row: int(row["abs"], 16)),
        "test_scope": {
            "encyclopedia": "all 603 records that remained on the promoted parent TIP",
            "required_spot_checks": [
                "5C096F キム -> 킴",
                "5C0B37..5C0B85 Gihren Zabi name and full description",
                "last remaining catalog entry",
            ],
            "runtime": "page navigation, return to menu, battle transition, save, full restart and reload",
        },
        "promotion": "blocked_pending_independent_audit_and_user_visual_verification",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["candidate"],
                "counts": report["counts"],
                "banks": report["allocation"]["banks"],
                "short": {
                    "existing_exact_slots": report["allocation"]["existing_exact_slots"],
                    "selected_retired_slots": report["allocation"]["selected_retired_slots"],
                },
                "kim": report["kim"],
                "diff": report["diff"],
                "report": str(REPORT.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
