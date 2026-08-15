#!/usr/bin/env python3
"""Clear remaining voice quarantine JP stubs and refresh residual sheets.

Applies to the current voice-proven+voice-ko test ROM:
- 欠番 → 결번
- *不要 / *不用 → 미사용
- other leftover JP with body_capacity >= 2 → 미사용
- body_capacity == 1 → single-byte middle-dot (・) to remove JP glyphs

ID-bundle unchanged_japanese_record rows are verified live (already clean on this
ROM) and residual CSVs are regenerated from the patched candidate.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_broad_japanese_residuals import current_strong_retired_slots  # noqa: E402
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3  # noqa: E402
from build_encyclopedia_ms_batch01_candidate import exact_slots  # noqa: E402
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase  # noqa: E402
from expand_dictionary import write_dictionary_slots_spill  # noqa: E402
from mixed_residual_classification import is_japanese_character, japanese_character_count  # noqa: E402
from mixed_residual_reference_union import _working_two_byte_external_refs  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_START,
    Dictionary,
    SEG_DICT,
    Tbl,
    find_rom,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text  # noqa: E402
from structured_token_write_guard import PROTECTED_TABLES, validate_protected_table  # noqa: E402

BASE = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate.wsc"
BASE_SAVE = ROOT / "sram/runtime_text_id_scenario_voice_proven_candidate.sav"
VOICE_SHEET = ROOT / "out/script/runtime_text_residual_voice_sheet.csv"
ID_SHEET = ROOT / "out/script/runtime_text_residual_id_bundle_sheet.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = BASE
OUT_SAVE = BASE_SAVE
REPORT = ROOT / "out/patch/runtime_text_residual_quarantine_stub_report.json"
BACKUP_DIR = ROOT / "out/patch/backup"

EXPECTED_BASE = "4512a5d3f49fc69f72eb9b0918cb2861672e41f3fe29614385af4646b221151d"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ONE_BYTE_CLEAR = bytes([0x2A])  # ・


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve())
    return {"path": shown, "size": len(data), "sha256": sha(data)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def merge_like(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged: list[list[int]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def visible_has_japanese(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in text)


def stub_ko(jp: str) -> str:
    value = jp.strip()
    if value == "欠番":
        return "결번"
    if "不要" in value or "不用" in value or value == "不要":
        return "미사용"
    return "미사용"


def verify_id_unchanged_already_clean(parent: bytes, tbl: Tbl) -> dict[str, Any]:
    dictionary = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(parent)
    with ID_SHEET.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    targets = [row for row in rows if row.get("classification") == "unchanged_japanese_record"]
    remaining = 0
    samples: list[dict[str, str]] = []
    for row in targets:
        logical = int(row["record_start"], 16)
        payload = bytes.fromhex(row["original_payload_hex"])
        current = bytes(parent[sb + logical : sb + logical + len(payload)])
        prefix = bytes.fromhex(row.get("prefix_hex") or "")
        body = current[len(prefix) :] if prefix and current.startswith(prefix) else current
        try:
            rendered = dictionary.expand(body, tbl)
        except Exception:
            rendered = ""
        if visible_has_japanese(rendered):
            remaining += 1
            if len(samples) < 10:
                samples.append(
                    {
                        "abs": f"{logical:06X}",
                        "jp": row.get("original_body") or "",
                        "render": rendered[:80],
                    }
                )
    return {
        "sheet_unchanged_japanese_rows": len(targets),
        "live_jp_remaining": remaining,
        "samples": samples,
    }


def load_stub_targets(parent: bytes, tbl: Tbl) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    dictionary = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(parent)
    with VOICE_SHEET.open(encoding="utf-8-sig", newline="") as handle:
        sources = list(csv.DictReader(handle))
    ext3_rows: list[dict[str, Any]] = []
    short_rows: list[dict[str, Any]] = []
    one_byte_rows: list[dict[str, Any]] = []
    skipped_clean = 0
    for source in sources:
        if source.get("classification") != "voice_boundary_unproven_quarantine":
            continue
        logical = int(source["record_start"], 16)
        prefix = bytes.fromhex(source.get("prefix_hex") or "")
        payload = bytes.fromhex(source["original_payload_hex"])
        current = bytes(parent[sb + logical : sb + logical + len(payload)])
        if len(current) != len(payload) or parent[sb + logical + len(payload)] != 0:
            raise BuildError(f"voice stub boundary drifted at {logical:06X}")
        if prefix and not current.startswith(prefix):
            prefix = b""
        body = current[len(prefix) :]
        try:
            rendered = dictionary.expand(body, tbl)
        except Exception:
            rendered = ""
        if not visible_has_japanese(rendered):
            skipped_clean += 1
            continue
        jp = source.get("original_body") or rendered
        body_capacity = len(body)
        if body_capacity <= 0:
            continue
        if body_capacity == 1:
            one_byte_rows.append(
                {
                    "abs": f"{logical:06X}",
                    "logical": logical,
                    "jp": jp,
                    "ko": "・",
                    "prefix": prefix,
                    "prefix_len": len(prefix),
                    "payload_capacity": len(current),
                    "body_capacity": 1,
                    "before_payload_hex": current.hex().upper(),
                    "strategy": "one_byte_middot_clear",
                }
            )
            continue
        ko = normalize_ko_text(stub_ko(jp))
        encoded = encode_phrase(ko, tbl)
        row = {
            "abs": f"{logical:06X}",
            "logical": logical,
            "jp": jp,
            "ko": ko,
            "encoded": encoded,
            "prefix": prefix,
            "prefix_len": len(prefix),
            "payload_capacity": len(current),
            "body_capacity": body_capacity,
            "before_payload_hex": current.hex().upper(),
            "translation_source": "quarantine_stub_map",
        }
        if body_capacity >= 4:
            ext3_rows.append(row)
        else:
            short_rows.append(row)
    stats = {
        "quarantine_sheet_rows": sum(
            1 for row in sources if row.get("classification") == "voice_boundary_unproven_quarantine"
        ),
        "skipped_already_clean": skipped_clean,
        "ext3_targets": len(ext3_rows),
        "short_targets": len(short_rows),
        "one_byte_targets": len(one_byte_rows),
        "stub_ko_counts": dict(
            Counter(row["ko"] for row in ext3_rows + short_rows + one_byte_rows)
        ),
    }
    return ext3_rows, short_rows, one_byte_rows, stats


def main() -> int:
    base = BASE.read_bytes()
    save = BASE_SAVE.read_bytes()
    if len(base) != ROM_SIZE or sha(base) != EXPECTED_BASE:
        raise BuildError("base identity drifted; expected current voice-ko candidate")
    if len(save) != SAVE_SIZE:
        raise BuildError("paired SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    id_status = verify_id_unchanged_already_clean(base, tbl)
    if id_status["live_jp_remaining"] != 0:
        raise BuildError(f"ID unchanged rows still have JP on live ROM: {id_status}")

    original = bytes(load_rom(find_rom(ROOT)))
    parent_dictionary = make_dictionary_ext3(base, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    ext3_rows, short_rows, one_byte_rows, stats = load_stub_targets(base, tbl)
    if not (ext3_rows or short_rows or one_byte_rows):
        raise BuildError("no quarantine stub targets")

    # Prefer stock tokens for stub phrases whenever they fit capacity.
    stock_capacity_rows: list[dict[str, Any]] = []
    alias_rows: list[dict[str, Any]] = []
    stub_phrases = {str(row["ko"]) for row in ext3_rows + short_rows}
    exact = exact_slots(parent_dictionary, tbl, stub_phrases) if stub_phrases else {}
    reusable = {phrase: slots for phrase, slots in exact.items() if slots}
    for row in ext3_rows:
        phrase = str(row["ko"])
        if phrase in reusable and len(token_from_dict_index(min(reusable[phrase]))) <= int(row["body_capacity"]):
            stock_capacity_rows.append(row)
        else:
            alias_rows.append(row)
    ext3_rows = alias_rows
    short_rows = short_rows + stock_capacity_rows
    stats["ext3_targets"] = len(ext3_rows)
    stats["short_targets"] = len(short_rows)
    stats["stock_preferred_from_ext3_capacity"] = len(stock_capacity_rows)

    assignments, states = allocate_ext3(base, ext3_rows) if ext3_rows else ({}, {})
    candidate = bytearray(base)
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
        pointer_extents.extend(
            (start + local * 2, start + local * 2 + 2) for local in sorted(new_locals)
        )
        if int(state["cursor"]) > int(state["cursor_before"]):
            phrase_extents.append(
                (start + int(state["cursor_before"]), start + int(state["cursor"]))
            )

    short_phrases = {str(row["ko"]) for row in short_rows}
    new_short_phrases = sorted(short_phrases - set(reusable))
    selected_retired: list[int] = []
    stock_payloads: dict[int, bytes] = {}
    stock_assignment: dict[str, int] = {
        phrase: min(slots) for phrase, slots in reusable.items() if phrase in short_phrases
    }
    if new_short_phrases:
        retired = current_strong_retired_slots(original, base, parent_dictionary)
        selected_retired = retired[: len(new_short_phrases)]
        if len(selected_retired) != len(new_short_phrases):
            raise BuildError("insufficient strong-retired stock slots")
        selected_set = set(selected_retired)
        current_external = external_occurrence_map(base, ext3_aware=True, wanted=selected_set)
        current_nested = nested_occurrence_map(
            parent_dictionary, wanted=selected_set, ext3_aware=True
        )
        current_raw = _raw_pair_hits(base, selected_retired)
        if any(
            current_external.get(i) or current_nested.get(i) or current_raw.get(i)
            for i in selected_retired
        ):
            raise BuildError("selected retired stock slot is still reachable")
        for phrase, index in zip(new_short_phrases, selected_retired):
            stock_assignment[phrase] = index
            stock_payloads[index] = encode_phrase(phrase, tbl)

    pointers_before = list(Dictionary(candidate).ptrs)
    stock_cursor_before = _stock_phrase_cursor(candidate)
    if stock_payloads:
        pointers_written, stock_cursor_after = write_dictionary_slots_spill(
            candidate,
            stock_payloads,
            spill_start=SPILL_FLOOR,
            allow_aux_consumers=False,
            locs=_working_two_byte_external_refs(bytes(candidate)),
        )
    else:
        pointers_written = list(Dictionary(candidate).ptrs)
        stock_cursor_after = stock_cursor_before
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

    sb = stock_base(base)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []

    for row in one_byte_rows:
        replacement = bytes(row["prefix"]) + ONE_BYTE_CLEAR
        start = sb + int(row["logical"])
        end = start + len(replacement)
        before = bytes(candidate[start:end])
        if before.hex().upper() != row["before_payload_hex"]:
            raise BuildError(f"live payload drifted before write at {row['abs']}")
        candidate[start:end] = replacement
        target_extents.append((start, end))
        applied.append(
            {
                "abs": row["abs"],
                "jp": row["jp"],
                "ko": row["ko"],
                "strategy": row["strategy"],
                "before_hex": before.hex().upper(),
                "after_hex": replacement.hex().upper(),
            }
        )

    for row in ext3_rows + short_rows:
        phrase = str(row["ko"])
        if phrase in stock_assignment and len(token_from_dict_index(stock_assignment[phrase])) <= int(row["body_capacity"]):
            index = stock_assignment[phrase]
            token = token_from_dict_index(index)
            strategy = (
                "stock_exact_reuse"
                if phrase in reusable
                else "strong_retired_stock_spill"
            )
            allocation = {"stock_index": f"{index:04X}"}
        elif int(row["body_capacity"]) >= 4:
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
            raise BuildError(f"no storage strategy for short stub at {row['abs']}")
        if len(token) > int(row["body_capacity"]):
            raise BuildError(f"token longer than body at {row['abs']}: {len(token)}>{row['body_capacity']}")
        replacement_body = token + b"\x01" * (int(row["body_capacity"]) - len(token))
        replacement = bytes(row["prefix"]) + replacement_body
        start = sb + int(row["logical"])
        end = start + len(replacement)
        before = bytes(candidate[start:end])
        if before.hex().upper() != row["before_payload_hex"]:
            raise BuildError(f"live payload drifted before write at {row['abs']}")
        candidate[start:end] = replacement
        target_extents.append((start, end))
        applied.append(
            {
                "abs": row["abs"],
                "jp": row["jp"],
                "ko": phrase,
                "strategy": strategy,
                "allocation": allocation,
                "before_hex": before.hex().upper(),
                "after_hex": replacement.hex().upper(),
            }
        )

    final_dictionary = make_dictionary_ext3(
        bytes(candidate), load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    by_abs = {row["abs"]: row for row in ext3_rows + short_rows + one_byte_rows}
    for row in applied:
        meta = by_abs[row["abs"]]
        after = bytes.fromhex(row["after_hex"])
        prefix = bytes(meta["prefix"])
        body = after[len(prefix) :]
        if row.get("strategy") == "one_byte_middot_clear":
            token = body
        elif body.startswith(b"\xE5\x18"):
            token = body[:4]
        else:
            token = body[:2]
        rendered = normalize_ko_text(final_dictionary.expand(token, tbl).rstrip("\u3000 \t"))
        expected = normalize_ko_text(str(row["ko"]))
        if rendered != expected or visible_has_japanese(rendered):
            raise BuildError(f"post-write render mismatch at {row['abs']}: {rendered!r} != {expected!r}")
        row["token_render"] = rendered

    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    stock_pointer_extents = [
        (
            stock_bank_file + DICT_PTR_START + index * 2,
            stock_bank_file + DICT_PTR_START + index * 2 + 2,
        )
        for index in selected_retired
    ]
    stock_phrase_extent = (
        [(stock_bank_file + stock_cursor_before, stock_bank_file + stock_cursor_after)]
        if stock_cursor_after > stock_cursor_before
        else []
    )
    allowed = merge_like(
        target_extents
        + pointer_extents
        + phrase_extents
        + stock_pointer_extents
        + stock_phrase_extent
        + [(len(base) - 2, len(base))]
    )
    candidate_ba = bytearray(candidate)
    checksum_value = update_ws_checksum(candidate_ba)
    candidate_bytes = bytes(candidate_ba)
    runs = diff_runs(base, candidate_bytes)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(
            "non-target edits: "
            + ", ".join(f"{lo:06X}-{hi:06X}" for lo, hi in unexpected[:12])
        )
    for table in PROTECTED_TABLES:
        result = validate_protected_table(candidate_bytes, table)
        if not result.get("expected_exact"):
            raise BuildError(f"protected table drifted: {table.name}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{stamp}_pre_quarantine_stub"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE, backup / BASE.name)
    shutil.copy2(BASE_SAVE, backup / BASE_SAVE.name)
    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save)

    # Refresh residual sheets from the patched candidate so ID unchanged disappears.
    import analyze_runtime_text_residual_families as analyze

    analyze_argv = [
        "analyze_runtime_text_residual_families.py",
        "--tip",
        str(OUT_ROM),
        "--out-json",
        str(ROOT / "out/patch/runtime_text_residual_families_report.json"),
        "--out-id",
        str(ID_SHEET),
        "--out-dialogue",
        str(ROOT / "out/script/runtime_text_residual_prefixed_dialogue_sheet.csv"),
        "--out-voice",
        str(VOICE_SHEET),
    ]
    old_argv = sys.argv
    try:
        sys.argv = analyze_argv
        rc = analyze.main()
    finally:
        sys.argv = old_argv
    if rc != 0:
        raise BuildError("post residual sheet refresh failed")

    report = {
        "schema_version": 1,
        "ok": True,
        "generated_by": "tools/build_runtime_text_residual_quarantine_stub_candidate.py",
        "inputs": {"base": identity(BASE, base), "voice_sheet_before": identity(VOICE_SHEET)},
        "outputs": {
            "rom": identity(OUT_ROM, candidate_bytes),
            "save": identity(OUT_SAVE, save),
            "backup": str(backup.relative_to(ROOT)).replace("\\", "/"),
            "checksum": f"{checksum_value:04X}",
        },
        "id_unchanged_japanese_status": id_status,
        "selection": stats,
        "counts": {
            "applied": len(applied),
            "diff_runs": len(runs),
            "diff_bytes": sum(hi - lo for lo, hi in runs),
            "by_strategy": dict(Counter(row["strategy"] for row in applied)),
        },
        "sample_applied": applied[:20],
    }
    atomic_json(REPORT, report)
    print(json.dumps({"ok": True, "counts": report["counts"], "id": id_status, "rom": report["outputs"]["rom"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
