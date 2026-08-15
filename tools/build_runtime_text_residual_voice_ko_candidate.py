#!/usr/bin/env python3
"""Apply residual voice-sheet Korean (excluding 不要 junk) onto the voice-proven test ROM.

Base: out/patch/runtime_text_id_scenario_voice_proven_candidate.wsc
Population: runtime_text_residual_voice_sheet.csv quarantine/JP rows minus
不要/欠番/不用 fragments and nonlinguistic EE-control garbage.
ID-bundle residuals are already clean on the base ROM and are only rechecked.

Storage:
- body_capacity >= 4 → five-bank E5 18 alias token + 0x01 pad (prefix preserved)
- body_capacity 2..3 → live/retired stock two-byte token
No main TIP write. SaveRAM is a paired snapshot of the parent test ROM.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five  # noqa: E402
from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_broad_japanese_residuals import current_strong_retired_slots  # noqa: E402
from build_battle_id_output_inventory import normalize_text, valid_korean  # noqa: E402
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3  # noqa: E402
from build_encyclopedia_ms_batch01_candidate import exact_slots  # noqa: E402
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor  # noqa: E402
from build_remaining_dialogue_candidate import (  # noqa: E402
    covered,
    diff_runs,
    encode_phrase,
)
from build_remaining_dialogue_candidate import BuildError as EncodeBuildError  # noqa: E402
from expand_dictionary import write_dictionary_slots_spill  # noqa: E402
from mixed_residual_classification import (  # noqa: E402
    hangul_character_count,
    is_japanese_character,
    japanese_character_count,
)
from mixed_residual_reference_union import _working_two_byte_external_refs  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
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
NEW_VOICE_CATALOG = ROOT / "data/runtime_text_residual_new_ko_voice_batch01.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/runtime_text_id_scenario_voice_proven_candidate.wsc"
OUT_SAVE = ROOT / "sram/runtime_text_id_scenario_voice_proven_candidate.sav"
REPORT = ROOT / "out/patch/runtime_text_residual_voice_ko_candidate_report.json"
BACKUP_DIR = ROOT / "out/patch/backup"

EXPECTED_BASE = "5919fbf7bb25d692ca0593a63961fe34148a69ba9c0ef7b5a04b153b1c7414c4"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
TAG_RE = re.compile(r"<[^>]+>")


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


def visible_has_japanese(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in TAG_RE.sub("", text))


def is_junk_body(text: str) -> bool:
    value = text.strip()
    if value in {"不要", "欠番", "不用", ""}:
        return True
    if "不要" in value or "不用" in value:
        return True
    if "<EE" in value:
        return True
    return False


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_dicts(nested)


def load_project_ko_map() -> dict[str, str]:
    output: dict[str, str] = {}
    for path in sorted((ROOT / "data").glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        curated = path.name.endswith("_ko.json")
        for row in walk_dicts(document):
            source = str(
                row.get("jp")
                or row.get("source_text")
                or row.get("original_text")
                or row.get("original_jp")
                or row.get("original")
                or ""
            ).strip()
            ko = normalize_ko_text(str(row.get("ko") or ""))
            review = str(row.get("review_status") or "").strip()
            trusted = curated or review in {
                "approved",
                "user_verified",
                "not_needed_false_mixed",
            }
            if (
                source
                and ko
                and valid_korean(ko)
                and trusted
                and japanese_character_count(ko) == 0
            ):
                output.setdefault(normalize_text(source), ko)
    return output


def acceptable_ko(text: str) -> bool:
    value = normalize_ko_text(text)
    if not value or visible_has_japanese(value):
        return False
    if valid_korean(value):
        return True
    # Nonlinguistic ellipsis / reaction rows may be punctuation-only.
    return japanese_character_count(value) == 0 and hangul_character_count(value) == 0


def load_new_voice_catalog() -> dict[str, str]:
    document = json.loads(NEW_VOICE_CATALOG.read_text(encoding="utf-8"))
    if document.get("translation_source") != "llm" or document.get("review_status") != "approved":
        raise BuildError("voice batch catalog is not approved")
    output: dict[str, str] = {}
    for row in document.get("entries") or []:
        jp = str(row.get("jp") or "")
        ko = normalize_ko_text(str(row.get("ko") or ""))
        if not jp or not acceptable_ko(ko):
            raise BuildError(f"invalid voice catalog row: {jp!r} -> {ko!r}")
        previous = output.get(jp)
        if previous is not None and previous != ko:
            raise BuildError(f"conflicting voice catalog jp: {jp!r}")
        output[jp] = ko
    return output


def resolve_ko(jp: str, suggested: str, project: dict[str, str], fresh: dict[str, str]) -> tuple[str, str]:
    if jp in fresh:
        return fresh[jp], "data/runtime_text_residual_new_ko_voice_batch01.json"
    sug = normalize_ko_text(suggested)
    if sug and acceptable_ko(sug):
        return sug, "sheet_suggested_ko"
    key = normalize_text(jp)
    if key in project:
        return project[key], "project_catalog"
    for width in (1, 2):
        if len(jp) > width:
            rest = jp[width:]
            rest_key = normalize_text(rest)
            if rest_key in project:
                return project[rest_key], f"project_catalog_strip_{width}"
            if rest in fresh:
                return fresh[rest], f"voice_batch_strip_{width}"
    raise BuildError(f"missing Korean for {jp!r}")


def load_voice_targets(
    parent: bytes,
    tbl: Tbl,
    project: dict[str, str],
    fresh: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    sb = stock_base(parent)
    with VOICE_SHEET.open(encoding="utf-8-sig", newline="") as handle:
        sources = list(csv.DictReader(handle))
    ext3_rows: list[dict[str, Any]] = []
    short_rows: list[dict[str, Any]] = []
    skipped_clean = 0
    skipped_junk = 0
    for source in sources:
        classification = source.get("classification") or ""
        jp = source.get("original_body") or ""
        if classification == "placeholder_or_empty" or is_junk_body(jp):
            skipped_junk += 1
            continue
        if int(source.get("japanese_count") or 0) <= 0 and classification != "mixed_shared_dictionary_or_partial_patch":
            continue
        if classification not in {
            "voice_boundary_unproven_quarantine",
            "mixed_shared_dictionary_or_partial_patch",
            "unchanged_japanese_record",
            "japanese_residual_after_partial_patch",
        }:
            continue
        logical = int(source["record_start"], 16)
        prefix = bytes.fromhex(source.get("prefix_hex") or "")
        body_capacity = int(source["body_capacity"])
        payload = bytes.fromhex(source["original_payload_hex"])
        if body_capacity < 2:
            skipped_junk += 1
            continue
        if len(payload) != len(prefix) + body_capacity:
            raise BuildError(f"sheet capacity mismatch at {logical:06X}")
        current = bytes(parent[sb + logical : sb + logical + len(payload)])
        if len(current) != len(payload):
            raise BuildError(f"payload OOB at {logical:06X}")
        if parent[sb + logical + len(payload)] != 0:
            raise BuildError(f"terminator missing at {logical:06X}")
        if prefix and not current.startswith(prefix):
            # Prefix may already have been consumed by a prior proven cleanup.
            prefix = b""
            body_capacity = len(current)
        ko, source_name = resolve_ko(jp, source.get("suggested_ko") or "", project, fresh)
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
            "body_capacity": len(current) - len(prefix),
            "translation_source": source_name,
            "before_payload_hex": current.hex().upper(),
        }
        if row["body_capacity"] >= 4:
            ext3_rows.append(row)
        else:
            short_rows.append(row)

    # Second pass: drop rows whose live render is already clean Korean.
    dictionary = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))

    def live_has_jp(row: dict[str, Any]) -> bool:
        payload = bytes.fromhex(row["before_payload_hex"])
        body = payload[row["prefix_len"] :]
        try:
            rendered = dictionary.expand(body, tbl)
        except Exception:
            return True
        return visible_has_japanese(rendered)

    filtered_ext3 = []
    filtered_short = []
    for row in ext3_rows:
        if live_has_jp(row):
            filtered_ext3.append(row)
        else:
            skipped_clean += 1
    for row in short_rows:
        if live_has_jp(row):
            filtered_short.append(row)
        else:
            skipped_clean += 1

    stats = {
        "sheet_rows": len(sources),
        "skipped_junk": skipped_junk,
        "skipped_already_clean": skipped_clean,
        "ext3_targets": len(filtered_ext3),
        "short_targets": len(filtered_short),
    }
    return filtered_ext3, filtered_short, stats


def verify_id_already_clean(parent: bytes, tbl: Tbl) -> dict[str, Any]:
    dictionary = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(parent)
    remaining = 0
    samples: list[dict[str, str]] = []
    with ID_SHEET.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if (row.get("original_body") or "").strip() == "不要":
            continue
        if int(row.get("japanese_count") or 0) <= 0:
            continue
        logical = int(row["record_start"], 16)
        payload = bytes.fromhex(row["original_payload_hex"])
        current = bytes(parent[sb + logical : sb + logical + len(payload)])
        prefix = bytes.fromhex(row.get("prefix_hex") or "")
        body = current[len(prefix) :] if current.startswith(prefix) else current
        try:
            rendered = dictionary.expand(body, tbl)
        except Exception:
            rendered = ""
        if visible_has_japanese(rendered):
            remaining += 1
            if len(samples) < 8:
                samples.append({"abs": f"{logical:06X}", "render": rendered[:80]})
    return {"id_jp_remaining_on_base": remaining, "samples": samples}


def main() -> int:
    base = BASE.read_bytes()
    save = BASE_SAVE.read_bytes()
    if len(base) != ROM_SIZE or sha(base) != EXPECTED_BASE:
        raise BuildError("voice-proven base identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("paired SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    project = load_project_ko_map()
    fresh = load_new_voice_catalog()
    id_status = verify_id_already_clean(base, tbl)
    if id_status["id_jp_remaining_on_base"] != 0:
        raise BuildError(f"unexpected ID JP remaining on base: {id_status}")

    original = bytes(load_rom(find_rom(ROOT)))
    parent_dictionary = make_dictionary_ext3(base, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    ext3_rows, short_rows, stats = load_voice_targets(base, tbl, project, fresh)
    if not ext3_rows and not short_rows:
        raise BuildError("no voice targets selected")

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
    exact = exact_slots(parent_dictionary, tbl, short_phrases) if short_phrases else {}
    reusable = {phrase: slots for phrase, slots in exact.items() if slots}
    new_short_phrases = sorted(short_phrases - set(reusable))
    selected_retired: list[int] = []
    stock_payloads: dict[int, bytes] = {}
    stock_assignment: dict[str, int] = {phrase: min(slots) for phrase, slots in reusable.items()}
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
    dictionary_after_prep = make_dictionary_ext3(
        bytes(candidate), load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    for row in ext3_rows + short_rows:
        phrase = str(row["ko"])
        if int(row["body_capacity"]) >= 4:
            info = assignments[phrase]
            token = bytes(info["token"])
            strategy = "five_bank_e518_alias_reuse" if info["reused"] else "five_bank_e518_alias_new"
            allocation: dict[str, Any] = {
                "page": int(info["page"]),
                "physical_bank": f"{int(info['segment']):02X}",
                "local": f"{int(info['local']):04X}",
                "pointer": f"{int(info['pointer']):04X}",
            }
        else:
            index = stock_assignment[phrase]
            token = token_from_dict_index(index)
            strategy = (
                "stock_exact_reuse"
                if phrase in reusable
                else "strong_retired_stock_spill"
            )
            allocation = {"stock_index": f"{index:04X}"}
        if len(token) > int(row["body_capacity"]):
            raise BuildError(f"token longer than body at {row['abs']}")
        replacement_body = token + b"\x01" * (int(row["body_capacity"]) - len(token))
        replacement = bytes(row["prefix"]) + replacement_body
        if len(replacement) != int(row["payload_capacity"]):
            raise BuildError(f"replacement size drifted at {row['abs']}")
        start = sb + int(row["logical"])
        end = start + len(replacement)
        before = bytes(candidate[start:end])
        if before.hex().upper() != row["before_payload_hex"]:
            raise BuildError(f"live payload drifted before write at {row['abs']}")
        candidate[start:end] = replacement
        target_extents.append((start, end))
        rendered = dictionary_after_prep.expand(replacement_body[: len(token)], tbl)
        # After alias/stock write, rebuild dictionary for accurate render of new phrases.
        applied.append(
            {
                "abs": row["abs"],
                "jp": row["jp"],
                "ko": phrase,
                "strategy": strategy,
                "translation_source": row["translation_source"],
                "prefix_hex": bytes(row["prefix"]).hex().upper(),
                "before_hex": before.hex().upper(),
                "after_hex": replacement.hex().upper(),
                "allocation": allocation,
                "token_render": rendered,
            }
        )

    # Final dictionary for render verification of written tokens.
    final_dictionary = make_dictionary_ext3(
        bytes(candidate), load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    for row in applied:
        after = bytes.fromhex(row["after_hex"])
        prefix = bytes.fromhex(row["prefix_hex"])
        body = after[len(prefix) :]
        if body.startswith(b"\xE5\x18"):
            token = body[:4]
        else:
            token = body[:2]
        if len(token) > len(body):
            raise BuildError(f"token longer than body at {row['abs']}")
        if any(byte != 0x01 for byte in body[len(token) :]):
            raise BuildError(f"non-pad residue at {row['abs']}")
        rendered = normalize_ko_text(final_dictionary.expand(token, tbl).rstrip("\u3000 \t"))
        if rendered != row["ko"] or visible_has_japanese(rendered):
            raise BuildError(f"post-write render mismatch at {row['abs']}: {rendered!r}")
        row["token_render"] = rendered

    from monoeye_rom import DICT_PTR_START, SEG_DICT

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

    # Backup previous test ROM then overwrite the named candidate.
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{stamp}_pre_residual_voice_ko"
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASE, backup / BASE.name)
    shutil.copy2(BASE_SAVE, backup / BASE_SAVE.name)

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save)

    report = {
        "schema_version": 1,
        "ok": True,
        "generated_by": "tools/build_runtime_text_residual_voice_ko_candidate.py",
        "inputs": {
            "base": identity(BASE, base),
            "voice_sheet": identity(VOICE_SHEET),
            "id_sheet": identity(ID_SHEET),
            "voice_catalog": identity(NEW_VOICE_CATALOG),
        },
        "outputs": {
            "rom": identity(OUT_ROM, candidate_bytes),
            "save": identity(OUT_SAVE, save),
            "backup": str(backup.relative_to(ROOT)).replace("\\", "/"),
        },
        "id_status": id_status,
        "selection": stats,
        "counts": {
            "applied": len(applied),
            "unique_ko": len({row["ko"] for row in applied}),
            "ext3_new": sum(1 for row in applied if row["strategy"] == "five_bank_e518_alias_new"),
            "ext3_reuse": sum(1 for row in applied if row["strategy"] == "five_bank_e518_alias_reuse"),
            "stock_reuse": sum(1 for row in applied if row["strategy"] == "stock_exact_reuse"),
            "stock_spill": sum(1 for row in applied if row["strategy"] == "strong_retired_stock_spill"),
            "diff_runs": len(runs),
            "diff_bytes": sum(hi - lo for lo, hi in runs),
        },
        "alias_pages": {
            str(page): {
                "cursor_before": f"{int(state['cursor_before']):04X}",
                "cursor_after": f"{int(state['cursor']):04X}",
                "phrase_bytes_added": int(state["cursor"]) - int(state["cursor_before"]),
            }
            for page, state in states.items()
        },
        "stock": {
            "retired_slots": [f"{index:04X}" for index in selected_retired],
            "cursor_before": f"{stock_cursor_before:04X}",
            "cursor_after": f"{stock_cursor_after:04X}",
        },
        "sample_applied": applied[:20],
    }
    atomic_json(REPORT, report)
    print(json.dumps({"ok": True, "counts": report["counts"], "rom": report["outputs"]["rom"]}, ensure_ascii=False, indent=2))
    return 0


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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, EncodeBuildError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
