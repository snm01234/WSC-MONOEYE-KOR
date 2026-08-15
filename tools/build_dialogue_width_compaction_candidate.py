#!/usr/bin/env python3
"""Build a meaning-preserving dialogue-width compaction candidate.

The seven target records are the current screen-width audit's >25-cell set.
Every target already points to a private E5 18 ext3 phrase.  This builder proves
that each phrase has exactly the intended runtime consumer, no nested dictionary
parent, no physical pointer alias, and no pointer into the phrase body.  The
shorter Korean phrase is then written in place; record bytes, record prefixes,
record terminators, ext3 pointers, stock dictionary pointers, and runtime code
remain byte-identical.  Only phrase storage plus the WonderSwan checksum may
change.

Candidate only: the main TIP is never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from mixed_residual_reference_union import build_reference_union  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    le16,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/dialogue_width_compaction_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/dialogue_width_compaction_candidate.wsc"
OUT_SAVE = ROOT / "sram/dialogue_width_compaction_candidate.sav"
OUT_REPORT = ROOT / "out/patch/dialogue_width_compaction_report.json"

EXPECTED_MAIN_SHA256 = "59dd896c6bf415c24f12b179beb5fa2794ec1c80c8de0591dfc5579047e01375"
EXPECTED_TARGETS = 7
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def visual_cells(text: str) -> int:
    return sum(1 for ch in text if ch not in "\r\n")


def encode_phrase(text: str, tbl: Tbl) -> bytes:
    normalized = normalize_ko_text(text)
    payload = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or not payload or b"\x00" in payload:
        raise BuildError(f"cannot safely encode target text: {text!r}")
    return payload


def ext3_index(body: bytes) -> int | None:
    if len(body) < 4 or body[:2] != b"\xE5\x18":
        return None
    return 0x1000 + (body[2] << 8) + body[3]


def ext3_storage_proof(rom: bytes, dictionary: Any, index: int) -> dict[str, Any]:
    # Mirror the live dictionary's alias-aware physical mapping.  Logical early
    # ext3 pages can map high locals into physical banks 21-25.
    seg, local = dictionary._ext3_bank_local(index)
    base = (seg & 0x7F) * BANK_SIZE
    ptr = le16(rom, base + local * 2)
    raw = bytes(dictionary.raw_entry(index))
    aliases: list[int] = []
    interior: list[int] = []
    for other_local in range(0x1000):
        other_ptr = le16(rom, base + other_local * 2)
        if other_ptr == ptr:
            aliases.append(other_local)
        elif ptr < other_ptr <= ptr + len(raw):
            interior.append(other_local)
    return {
        "index": f"{index:05X}",
        "physical_segment": f"{seg:02X}",
        "physical_local": f"{local:03X}",
        "ptr": f"{ptr:04X}",
        "entry_abs": int(dictionary.entry_abs(index)),
        "old_len": len(raw),
        "physical_pointer_aliases": [f"{seg:02X}:{value:03X}" for value in aliases],
        "physical_interior_pointer_entries": [
            f"{seg:02X}:{value:03X}" for value in interior
        ],
        "storage_ok": aliases == [local] and not interior,
    }


def target_accounted(consumer: int, target: int) -> bool:
    # Reference-union scanning can return the enclosing record start while the
    # target address is the body/record start after a small control prefix.
    return 0 <= target - consumer <= 8


def changed_runs(before: bytes, after: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pos = 0
    while pos < len(before):
        if before[pos] == after[pos]:
            pos += 1
            continue
        start = pos
        while pos < len(before) and before[pos] != after[pos]:
            pos += 1
        rows.append(
            {
                "start": f"{start:06X}",
                "end": f"{pos:06X}",
                "length": pos - start,
                "before_hex": before[start : min(pos, start + 24)].hex().upper(),
                "after_hex": after[start : min(pos, start + 24)].hex().upper(),
            }
        )
    return rows


def in_allowed(offset: int, intervals: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in intervals)


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.exists() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live main SaveRAM missing or wrong size")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if str(spec.get("parent_tip_sha256") or "").lower() != EXPECTED_MAIN_SHA256:
        raise BuildError("width compaction spec is not bound to the current main TIP")
    max_cells = int(spec.get("max_visual_cells") or 0)
    rows = list(spec.get("records") or [])
    if len(rows) != EXPECTED_TARGETS:
        raise BuildError(f"target count drifted: expected {EXPECTED_TARGETS}, got {len(rows)}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(
        original,
        parent,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    sb = stock_base(parent)

    candidate = bytearray(parent)
    allowed_intervals: list[tuple[int, int]] = []
    prepared: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    seen_targets: set[int] = set()

    for row in sorted(rows, key=lambda item: int(item["abs"], 16)):
        logical = int(row["abs"], 16)
        if logical in seen_targets:
            raise BuildError(f"duplicate target address {logical:06X}")
        seen_targets.add(logical)

        got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        if got is None:
            raise BuildError(f"unreadable parent record {logical:06X}")
        payload, terminator = bytes(got[0]), int(got[1])
        prefix, body, kind = split_prefix_body(payload)
        current = strip_pad(dictionary.expand(body, tbl))
        before = normalize_ko_text(str(row.get("before") or ""))
        after = normalize_ko_text(str(row.get("after") or ""))
        if current != before:
            raise BuildError(
                f"parent render drift at {logical:06X}: expected {before!r}, got {current!r}"
            )
        if not after:
            raise BuildError(f"empty replacement at {logical:06X}")
        before_cells = visual_cells(before)
        after_cells = visual_cells(after)
        if after_cells >= before_cells:
            raise BuildError(
                f"replacement is not shorter at {logical:06X}: {before_cells} -> {after_cells}"
            )
        if after_cells > max_cells:
            raise BuildError(
                f"replacement exceeds {max_cells} cells at {logical:06X}: {after_cells}"
            )

        index = ext3_index(body)
        if index is None:
            raise BuildError(f"target no longer begins with private ext3 portal: {logical:06X}")
        if index in seen_indices:
            raise BuildError(f"two targets unexpectedly share ext3 slot {index:05X}")
        seen_indices.add(index)

        consumers = {int(item.abs) for item in union.consumers_for(index)}
        unexpected = sorted(value for value in consumers if not target_accounted(value, logical))
        target_seen = any(target_accounted(value, logical) for value in consumers)
        nested = sorted(union.parents_of(index))
        if unexpected or not target_seen or nested:
            raise BuildError(
                f"ext3 slot {index:05X} is not private to {logical:06X}: "
                f"consumers={sorted(consumers)!r} nested={nested!r}"
            )

        proof = ext3_storage_proof(parent, dictionary, index)
        if not proof["storage_ok"]:
            raise BuildError(f"ext3 storage alias/interior pointer at {logical:06X}: {proof}")
        encoded = encode_phrase(after, tbl)
        if len(encoded) > int(proof["old_len"]):
            raise BuildError(
                f"compacted phrase unexpectedly grows storage at {logical:06X}: "
                f"{len(encoded)} > {proof['old_len']}"
            )
        if strip_pad(dictionary.expand(encoded, tbl)) != after:
            raise BuildError(f"encoded replacement does not round-trip at {logical:06X}")

        entry = int(proof["entry_abs"])
        candidate[entry : entry + len(encoded)] = encoded
        candidate[entry + len(encoded)] = 0
        allowed_intervals.append((entry, entry + int(proof["old_len"]) + 1))
        prepared.append(
            {
                "abs": f"{logical:06X}",
                "scope": row.get("scope"),
                "source_jp": row.get("source_jp"),
                "reason": row.get("reason"),
                "kind": kind,
                "prefix_hex": prefix.hex().upper(),
                "parent_payload_hex": payload.hex().upper(),
                "parent_terminator": f"{terminator - sb:06X}",
                "before": before,
                "after": after,
                "before_cells": before_cells,
                "after_cells": after_cells,
                "cells_saved": before_cells - after_cells,
                "ext3_index": f"{index:05X}",
                "consumer_abs": [f"{value:06X}" for value in sorted(consumers)],
                "nested_parents": [f"{value:05X}" for value in nested],
                "old_phrase_len": int(proof["old_len"]),
                "new_phrase_len": len(encoded),
                "storage": proof,
            }
        )

    checksum = update_ws_checksum(candidate)
    allowed_intervals.append((len(parent) - 2, len(parent)))
    result = bytes(candidate)
    dictionary_after = make_dictionary_ext3(result, ext_meta, ext3_meta)

    target_checks: list[dict[str, Any]] = []
    for row in prepared:
        logical = int(row["abs"], 16)
        before_got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        after_got = read_encoded_z_safe(result, sb + logical, max_len=256)
        if before_got is None or after_got is None:
            raise BuildError(f"target became unreadable at {logical:06X}")
        before_payload, before_term = bytes(before_got[0]), int(before_got[1])
        after_payload, after_term = bytes(after_got[0]), int(after_got[1])
        _, after_body, _ = split_prefix_body(after_payload)
        rendered = strip_pad(dictionary_after.expand(after_body, tbl))
        check = {
            "abs": row["abs"],
            "record_bytes_unchanged": before_payload == after_payload,
            "terminator_unchanged": before_term == after_term,
            "rendered": rendered,
            "expected": row["after"],
            "render_exact": rendered == row["after"],
            "cells": visual_cells(rendered),
        }
        check["ok"] = all(
            (
                check["record_bytes_unchanged"],
                check["terminator_unchanged"],
                check["render_exact"],
                check["cells"] <= max_cells,
            )
        )
        if not check["ok"]:
            raise BuildError(f"post-build target verification failed: {check}")
        target_checks.append(check)

    unexpected_diff_offsets = [
        offset
        for offset, (before_byte, after_byte) in enumerate(zip(parent, result))
        if before_byte != after_byte and not in_allowed(offset, allowed_intervals)
    ]
    if unexpected_diff_offsets:
        raise BuildError(
            "candidate changed bytes outside private phrase storage/checksum: "
            + repr([f"{value:06X}" for value in unexpected_diff_offsets[:20]])
        )

    OUT_ROM.write_bytes(result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_width_compaction_candidate.py",
        "ok": True,
        "parent": {
            "path": str(MAIN.relative_to(ROOT)),
            "size": len(parent),
            "sha256": sha256(parent),
        },
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "size": len(result),
            "sha256": sha256(result),
            "ws_checksum": f"{checksum:04X}",
        },
        "candidate_save": {
            "path": str(OUT_SAVE.relative_to(ROOT)),
            "size": OUT_SAVE.stat().st_size,
            "sha256": sha256(OUT_SAVE.read_bytes()),
            "source": str(MAIN_SAVE.relative_to(ROOT)),
        },
        "strategy": (
            "seven private ext3 phrase payloads rewritten in place; all target record bytes, "
            "terminators, ext3 pointers, stock pointers and runtime code remain unchanged"
        ),
        "max_visual_cells": max_cells,
        "counts": {
            "targets": len(prepared),
            "all_at_or_below_limit": sum(row["after_cells"] <= max_cells for row in prepared),
            "total_cells_before": sum(row["before_cells"] for row in prepared),
            "total_cells_after": sum(row["after_cells"] for row in prepared),
            "total_cells_saved": sum(row["cells_saved"] for row in prepared),
            "record_bytes_changed": sum(
                not row["record_bytes_unchanged"] for row in target_checks
            ),
            "terminators_changed": sum(not row["terminator_unchanged"] for row in target_checks),
            "unexpected_diff_offsets": len(unexpected_diff_offsets),
        },
        "targets": prepared,
        "target_checks": target_checks,
        "diff_runs": changed_runs(parent, result),
        "reference_union": union.summary(),
        "promotion_status": "candidate_only_pending_user_runtime_validation",
    }
    OUT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["candidate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
