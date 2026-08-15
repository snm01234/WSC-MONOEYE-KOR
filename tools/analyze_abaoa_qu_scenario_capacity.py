#!/usr/bin/env python3
"""Measure reviewed script translation capacity from the A Baoa Qu anchor onward.

Read-only.  This deliberately does not raw-scan text banks because event/control
records can decode as kana-like garbage.  The population is restricted to the
reviewed script entries in data/mixed_residual_translations.json and uses the
Original-derived fixed record boundaries in main_p1_base_manifest.json.

The report is a storage/fit analysis only.  It does not approve legacy wording
for insertion; final application must use the latest accepted translation data.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from hangul_marker import marker_code
from monoeye_rom import Tbl, load_rom, stock_base
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TRANSLATIONS = ROOT / "data/mixed_residual_translations.json"
MANIFEST = ROOT / "out/patch/main_p1_base_manifest.json"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/abaoa_qu_scenario_capacity.json"

EXPECTED_MAIN_SHA256 = "b24b11934f4508c91aae8a3b38ab256d6fdc51228a6dd81c99b7b5733864c825"
ANCHOR = 0x60B57E
BANK21_USABLE_TOKENS = 2550
BANK21_PHRASE_START = 0x2001
BANK21_PHRASE_ROOM = 0x10000 - BANK21_PHRASE_START


class AnalysisError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def walk_manifest_records(root: Any) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    stack = [root]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            address = value.get("abs")
            boundary = value.get("boundary")
            if (
                value.get("region") == "script"
                and isinstance(address, str)
                and isinstance(boundary, dict)
                and "payload_capacity" in boundary
            ):
                logical = int(address, 16)
                previous = result.get(logical)
                if previous is not None and previous != value:
                    raise AnalysisError(f"duplicate manifest record {logical:06X}")
                result[logical] = value
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return result


def strip_padding(text: str) -> str:
    # 0x01 is the ideographic-space glyph in text bodies and is used as fixed
    # record padding by prior passes.  Only trailing padding is ignored.
    return text.rstrip("\u3000")


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
        for char in text
    )


def classify_current(current: str, target: str) -> str:
    if current == target:
        return "exact_target"
    if has_japanese(current):
        return "japanese_residual"
    if not current:
        return "empty_or_control"
    return "translated_different"


def main() -> int:
    rom = bytes(load_rom(MAIN))
    if sha256(rom) != EXPECTED_MAIN_SHA256:
        raise AnalysisError("main TIP identity drifted")
    sb = stock_base(rom)

    translation_doc = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
    manifest_doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = walk_manifest_records(manifest_doc)
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )

    population = [
        row
        for row in translation_doc.get("entries", [])
        if row.get("region") == "script"
        and int(str(row.get("abs") or "0"), 16) >= ANCHOR
    ]
    population.sort(key=lambda row: int(str(row["abs"]), 16))
    if not population:
        raise AnalysisError("no reviewed script translations at/after anchor")

    rows: list[dict[str, Any]] = []
    unique_encoded: dict[str, bytes] = {}
    duplicate_addresses: dict[str, list[str]] = defaultdict(list)
    counts: Counter[str] = Counter()
    body_capacities: Counter[int] = Counter()

    for source in population:
        address = int(str(source["abs"]), 16)
        record = manifest.get(address)
        if record is None:
            rows.append(
                {
                    "abs": f"{address:06X}",
                    "record_id": source.get("record_id"),
                    "status": "manifest_missing",
                }
            )
            counts["manifest_missing"] += 1
            continue

        boundary = record["boundary"]
        capacity = int(boundary["payload_capacity"])
        payload = rom[sb + address:sb + address + capacity]
        if len(payload) != capacity:
            raise AnalysisError(f"record outside ROM {address:06X}")

        manifest_prefix = bytes.fromhex(str(record.get("prefix_hex") or ""))
        prefix_source = "manifest"
        if manifest_prefix and payload.startswith(manifest_prefix):
            prefix = manifest_prefix
            body = payload[len(prefix):]
            kind = "manifest_prefix"
        elif not manifest_prefix:
            prefix, body, kind = split_prefix_body(payload)
            prefix_source = "current_split"
        else:
            prefix, body, kind = split_prefix_body(payload)
            prefix_source = "fallback_current_split"
            counts["prefix_mismatch"] += 1

        body_capacity = len(body)
        body_capacities[body_capacity] += 1
        current_rendered = strip_padding(dictionary.expand(body, tbl))
        target = normalize_ko_text(str(source.get("ko") or ""))
        encoded = try_encode_ko_text(
            target,
            tbl,
            hangul_marker_code=marker_code(),
            hangul_marker_mode="run",
        )
        current_class = classify_current(current_rendered, target)
        counts[current_class] += 1
        counts["review_status_" + str(source.get("review_status") or "missing")] += 1

        if encoded is None or not encoded or b"\x00" in encoded:
            encoding_status = "encode_failure"
            counts[encoding_status] += 1
        else:
            encoding_status = "encodable"
            counts[encoding_status] += 1
            if target not in unique_encoded:
                unique_encoded[target] = encoded
            elif unique_encoded[target] != encoded:
                raise AnalysisError(f"non-deterministic encoding for {target!r}")
            duplicate_addresses[target].append(f"{address:06X}")

        token_fit = body_capacity >= 4
        if token_fit:
            counts["body_at_least_4"] += 1
        else:
            counts["body_under_4"] += 1

        needs_change = current_class != "exact_target"
        if needs_change:
            counts["needs_change"] += 1
            if token_fit and encoding_status == "encodable":
                counts["needs_change_bank21_token_eligible"] += 1
            elif body_capacity < 4:
                counts["needs_change_short_record"] += 1
            else:
                counts["needs_change_other_blocked"] += 1
        else:
            counts["already_exact"] += 1

        rows.append(
            {
                "abs": f"{address:06X}",
                "record_id": source.get("record_id"),
                "payload_capacity": capacity,
                "prefix_hex": prefix.hex().upper(),
                "prefix_source": prefix_source,
                "kind": kind,
                "body_capacity": body_capacity,
                "body_hex": body.hex().upper(),
                "current_text": current_rendered,
                "target_text": target,
                "current_class": current_class,
                "review_status": source.get("review_status"),
                "reviewer": source.get("reviewer"),
                "encoding_status": encoding_status,
                "encoded_bytes": len(encoded) if encoded is not None else None,
                "token_fits": token_fit,
                "needs_change": needs_change,
            }
        )

    all_unique_phrase_bytes = sum(len(payload) + 1 for payload in unique_encoded.values())
    needed_targets = {
        row["target_text"]
        for row in rows
        if row.get("needs_change")
        and row.get("encoding_status") == "encodable"
        and row.get("token_fits")
    }
    needed_unique_phrase_bytes = sum(
        len(unique_encoded[text]) + 1 for text in needed_targets
    )

    short_rows = [
        row
        for row in rows
        if row.get("needs_change") and isinstance(row.get("body_capacity"), int)
        and int(row["body_capacity"]) < 4
    ]
    eligible_rows = [
        row
        for row in rows
        if row.get("needs_change")
        and row.get("encoding_status") == "encodable"
        and row.get("token_fits")
    ]
    japanese_rows = [row for row in rows if row.get("current_class") == "japanese_residual"]
    different_rows = [row for row in rows if row.get("current_class") == "translated_different"]

    capacity = {
        "bank21_usable_tokens": BANK21_USABLE_TOKENS,
        "bank21_phrase_room": BANK21_PHRASE_ROOM,
        "all_reviewed_unique_phrases": len(unique_encoded),
        "all_reviewed_phrase_bytes_including_nul": all_unique_phrase_bytes,
        "needed_token_eligible_records": len(eligible_rows),
        "needed_token_eligible_unique_phrases": len(needed_targets),
        "needed_phrase_bytes_including_nul": needed_unique_phrase_bytes,
        "token_slots_after_needed": BANK21_USABLE_TOKENS - len(needed_targets),
        "phrase_room_after_needed": BANK21_PHRASE_ROOM - needed_unique_phrase_bytes,
        "token_capacity_sufficient": len(needed_targets) <= BANK21_USABLE_TOKENS,
        "phrase_capacity_sufficient": needed_unique_phrase_bytes <= BANK21_PHRASE_ROOM,
        "capacity_sufficient_for_token_eligible_population": (
            len(needed_targets) <= BANK21_USABLE_TOKENS
            and needed_unique_phrase_bytes <= BANK21_PHRASE_ROOM
        ),
        "short_records_require_separate_stock_or_composition_route": len(short_rows),
    }

    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_abaoa_qu_scenario_capacity.py",
        "read_only": True,
        "ok": counts["manifest_missing"] == 0 and counts["encode_failure"] == 0,
        "inputs": {
            "main": {
                "path": str(MAIN.relative_to(ROOT)),
                "sha256": sha256(rom),
            },
            "translations": str(TRANSLATIONS.relative_to(ROOT)),
            "manifest": str(MANIFEST.relative_to(ROOT)),
            "anchor": f"{ANCHOR:06X}",
            "anchor_label": "A Baoa Qu scenario entry",
            "population_policy": (
                "reviewed mixed_residual_translations script entries only; "
                "no raw kana/CJK scan of event banks"
            ),
            "translation_policy": (
                "capacity sizing only; wording is not automatically approved for insertion. "
                "Final builds must use the latest accepted non-legacy translation source."
            ),
        },
        "counts": {
            "reviewed_script_entries_at_or_after_anchor": len(population),
            **dict(sorted(counts.items())),
            "unique_target_phrases": len(unique_encoded),
            "unique_needed_token_eligible_phrases": len(needed_targets),
        },
        "body_capacity_distribution": {
            str(key): value for key, value in sorted(body_capacities.items())
        },
        "capacity": capacity,
        "samples": {
            "japanese_residual": japanese_rows[:40],
            "translated_different": different_rows[:40],
            "short_needs_change": short_rows[:80],
            "token_eligible_needs_change": eligible_rows[:40],
            "duplicate_target_phrases": [
                {"target_text": text, "addresses": addresses}
                for text, addresses in sorted(
                    duplicate_addresses.items(),
                    key=lambda item: (-len(item[1]), item[0]),
                )
                if len(addresses) > 1
            ][:80],
        },
        "records": rows,
        "recommendation": {
            "bank21": (
                "Use the verified E5 18 bank21 alias for body-capacity >=4 records "
                "only after emulator probe approval."
            ),
            "short_records": (
                "Handle body-capacity <4 records separately with exact existing-token "
                "composition or union-proven retired stock slots; never overflow records."
            ),
            "save_safety": (
                "Promote ROM only after testing. Never promote or copy candidate SaveRAM "
                "back to the main SaveRAM."
            ),
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "counts": report["counts"],
        "capacity": capacity,
        "out": str(OUT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
