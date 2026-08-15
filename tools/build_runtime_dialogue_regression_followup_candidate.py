#!/usr/bin/env python3
"""Build the 2026-08-09 dialogue/runtime regression follow-up candidate.

Goals bound to user runtime captures:

* broadly re-read the scenario run beginning at 617585 and rebind the lines
  that still contain legacy MT or cross-record 20-cell semantic spill;
* fix the 60610A/606112 duplicated phrase and 618812/618834 Haman/Judau spill;
* treat AD=死 as runtime-visible text at 5EAB36/5EB6B2/5EC27C and remove it;
* correct the 5D71AD/5D71BC artillery pair;
* cover the previously-uninventoried live bank-5F battle-voice block, including
  the screenshot-proven 5F0591 ``だめだっ！ / 避けられない！！`` case.

Every rewritten record keeps its original extent, control prefix and NUL
terminator.  Text is rebound only to reference-union-proven true-free private
slots.  The live main TIP is never modified; SaveRAM is copied only after all
post-build guards pass.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_dialogue_20cell_candidate import load_battle_prefixes, strip_pad, visible_lines
from extract_script import split_prefix_body
from hangul_marker import marker_code
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    COMPACT3_INDEX_BASE,
    COMPACT3_INDEX_END,
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_compact3_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/runtime_dialogue_regression_followup_ko.json"
BANK5F_SPEC = ROOT / "data/bank5f_runtime_battle_voice_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
JP_TBL_PATH = ROOT / "data/monoeye_verified.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/runtime_dialogue_regression_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/runtime_dialogue_regression_followup_candidate.sav"
OUT_REPORT = ROOT / "out/patch/runtime_dialogue_regression_followup_report.json"
SRAM_MIRROR = ROOT / "sram/runtime_dialogue_regression_followup_candidate.sav"
EXPECTED_MAIN_SHA = "8a53737d209ff695fdcd78c0f46f9e61eff9a15d8c4f01b0f387e8dd05488af2"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
LINE_LIMIT = 20
BANK5F_PREFIXES = {0xA1, 0x9B, 0x8A}


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(text: str, tbl: Tbl) -> tuple[str, bytes]:
    normalized = normalize_ko_text(text)
    payload = try_encode_ko_text(
        normalized,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or b"\x00" in payload:
        raise BuildError(f"cannot encode {text!r}")
    return normalized, payload


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def covered(off: int, intervals: list[tuple[int, int]]) -> bool:
    return any(a <= off < b for a, b in intervals)


def has_japanese(text: str) -> bool:
    return any(
        ("ぁ" <= ch <= "ヿ") or ("一" <= ch <= "龯")
        for ch in text
    )


def source_and_prefix(
    original: bytes,
    address: str,
    category: str,
    prefix_mode: str,
    battle_prefixes: dict[str, bytes],
    jp_dictionary: Dictionary,
    jp_tbl: Tbl,
) -> tuple[str, bytes]:
    payload, _term = payload_at(original, int(address, 16))
    prefix = b""
    if category == "scenario":
        prefix, body, _kind = split_prefix_body(payload)
        payload = bytes(body)
    elif category == "bank5f":
        if payload and payload[0] in BANK5F_PREFIXES:
            prefix = payload[:1]
            payload = payload[1:]
    elif category == "battle":
        if prefix_mode == "preserve_battle_prefix":
            prefix = battle_prefixes.get(address, b"")
            if not prefix or not payload.startswith(prefix):
                raise BuildError(f"expected battle prefix missing at source {address}")
            payload = payload[len(prefix):]
        elif prefix_mode != "visible_full":
            raise BuildError(f"unknown battle prefix mode {prefix_mode!r} at {address}")
    return jp_dictionary.expand(payload, jp_tbl), bytes(prefix)


def current_prefix(
    parent: bytes,
    address: str,
    category: str,
    prefix_mode: str,
    source_prefix: bytes,
) -> tuple[bytes, str]:
    payload, _term = payload_at(parent, int(address, 16))
    if category == "scenario":
        prefix, _body, kind = split_prefix_body(payload)
        return bytes(prefix), f"split_prefix_body:{kind}"
    if category == "bank5f":
        if payload and payload[0] in BANK5F_PREFIXES:
            return payload[:1], "bank5f_runtime_control_prefix"
        if source_prefix:
            raise BuildError(f"bank5f control prefix disappeared at {address}")
        return b"", "bank5f_body_only"
    if category == "battle":
        if prefix_mode == "visible_full":
            return b"", "runtime_visible_full_record"
        if prefix_mode == "preserve_battle_prefix":
            if not source_prefix or not payload.startswith(source_prefix):
                raise BuildError(f"battle prefix drift at {address}")
            return source_prefix, "preserve_battle_prefix"
    raise BuildError(f"unhandled prefix category={category} mode={prefix_mode} at {address}")


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"current main TIP identity drifted: {sha(parent)}")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live 32 KiB SaveRAM missing")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    bank5f = json.loads(BANK5F_SPEC.read_text(encoding="utf-8"))
    if str(spec.get("parent_sha256") or "").lower() != EXPECTED_MAIN_SHA:
        raise BuildError("spec parent SHA drifted")
    bank5f_targets = bank5f.get("targets") or {}
    if len(bank5f_targets) != 75:
        raise BuildError(f"bank5f target population drifted: {len(bank5f_targets)} != 75")

    tbl = Tbl.load(TBL_PATH)
    jp_tbl = Tbl.load(JP_TBL_PATH)
    jp_dictionary = Dictionary(original)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 16)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    battle_prefixes = load_battle_prefixes()
    sb = stock_base(parent)

    targets: dict[str, dict[str, Any]] = {}
    for address, row in (spec.get("scenario_targets") or {}).items():
        targets[address.upper()] = {
            "abs": address.upper(),
            "category": "scenario",
            "prefix_mode": "scenario",
            "source_jp": str(row["source_jp"]),
            "desired": str(row["after"]),
            "reason": str(row["reason"]),
        }
    for address, row in (spec.get("battle_targets") or {}).items():
        key = address.upper()
        if key in targets:
            raise BuildError(f"duplicate target {key}")
        targets[key] = {
            "abs": key,
            "category": "battle",
            "prefix_mode": str(row["prefix_mode"]),
            "source_jp": str(row["source_jp"]),
            "desired": str(row["after"]),
            "reason": str(row["reason"]),
        }
    for address, row in bank5f_targets.items():
        key = address.upper()
        if key in targets:
            raise BuildError(f"duplicate target {key}")
        targets[key] = {
            "abs": key,
            "category": "bank5f",
            "prefix_mode": "bank5f",
            "source_jp": str(row["source_jp"]),
            "desired": str(row["after"]),
            "reason": "live bank-5F battle voice retranslation / Japanese-runtime recurrence prevention",
        }

    prepared: list[dict[str, Any]] = []
    source_failures: list[dict[str, str]] = []
    for address, row in sorted(targets.items()):
        source_text, source_prefix = source_and_prefix(
            original,
            address,
            str(row["category"]),
            str(row["prefix_mode"]),
            battle_prefixes,
            jp_dictionary,
            jp_tbl,
        )
        if source_text != row["source_jp"]:
            source_failures.append({
                "abs": address,
                "expected": str(row["source_jp"]),
                "decoded_source": source_text,
            })
            continue

        payload, terminator = payload_at(parent, int(address, 16))
        prefix, prefix_basis = current_prefix(
            parent,
            address,
            str(row["category"]),
            str(row["prefix_mode"]),
            source_prefix,
        )
        body = payload[len(prefix):]
        if len(body) < 3:
            raise BuildError(f"record body too short at {address}: {len(body)}")
        strategy = "compact3" if len(body) == 3 else "ext3"
        desired_norm, encoded = encode(str(row["desired"]), tbl)
        line_cells = [len(line) for line in visible_lines(desired_norm)]
        if line_cells and max(line_cells) > LINE_LIMIT:
            raise BuildError(
                f"target exceeds {LINE_LIMIT} cells at {address}: {line_cells} {desired_norm!r}"
            )
        before = strip_pad(d_parent.expand(body, tbl))
        prepared.append({
            **row,
            "logical": int(address, 16),
            "prefix": prefix,
            "prefix_hex": prefix.hex().upper(),
            "prefix_basis": prefix_basis,
            "payload_len": len(payload),
            "body_len": len(body),
            "before_payload_hex": payload.hex().upper(),
            "before": before,
            "desired_norm": desired_norm,
            "encoded": encoded,
            "line_cells": line_cells,
            "terminator": terminator,
            "terminator_logical": terminator - sb,
            "strategy": strategy,
        })
    if source_failures:
        raise BuildError(f"source guard failures: {source_failures[:10]}")

    # Allocate only reference-union-proven true-free private slots, deduplicating
    # identical desired phrases while keeping every script record private from
    # unrelated dialogue.
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    room = {int(bank): int(value) for bank, value in inventory.ext3_bank_room.items()}
    free_all = sorted(int(index) for index in inventory.ext3_free)

    compact_unique: dict[bytes, dict[str, Any]] = {}
    ext3_unique: dict[bytes, dict[str, Any]] = {}
    for row in prepared:
        (compact_unique if row["strategy"] == "compact3" else ext3_unique).setdefault(row["encoded"], row)

    compact_free = [
        index for index in range(COMPACT3_INDEX_BASE + 1, COMPACT3_INDEX_END + 1)
        if union.is_true_free(index)
    ]
    if len(compact_free) < len(compact_unique):
        raise BuildError(
            f"compact3 capacity exhausted: need {len(compact_unique)} found {len(compact_free)}"
        )

    slot_payload: dict[int, bytes] = {}
    encoded_to_slot: dict[bytes, int] = {}
    for index, (phrase, sample) in zip(
        compact_free,
        sorted(compact_unique.items(), key=lambda kv: int(kv[1]["logical"])),
    ):
        encoded_to_slot[phrase] = index
        slot_payload[index] = phrase
        bank = bank_local_for_index(index)[0] - EXP3_SEG0
        room[bank] = room.get(bank, 0) - (len(phrase) + 1)
        if room[bank] < 0:
            raise BuildError(f"compact3 bank room exhausted for {sample['abs']}")

    compact_reserved = set(range(COMPACT3_INDEX_BASE, COMPACT3_INDEX_END + 1))
    free_by_bank: dict[int, list[int]] = defaultdict(list)
    for index in free_all:
        if index in compact_reserved or index in slot_payload:
            continue
        segment, _local = bank_local_for_index(index)
        free_by_bank[segment - EXP3_SEG0].append(index)
    for values in free_by_bank.values():
        values.sort()

    for phrase, sample in sorted(ext3_unique.items(), key=lambda kv: int(kv[1]["logical"])):
        need = len(phrase) + 1
        choices = [
            bank for bank in sorted(room, key=lambda b: (-room[b], b))
            if room.get(bank, 0) >= need and free_by_bank.get(bank)
        ]
        if not choices:
            raise BuildError(f"no ext3 room for {sample['abs']} {sample['desired_norm']!r}")
        bank = choices[0]
        index = free_by_bank[bank].pop(0)
        room[bank] -= need
        encoded_to_slot[phrase] = index
        slot_payload[index] = phrase

    for row in prepared:
        row["slot"] = encoded_to_slot[row["encoded"]]

    candidate = bytearray(parent)
    ext3_write, ext3_guard = write_ext3_slots_guarded(
        candidate,
        slot_payload,
        union=union,
        num_banks=num_banks,
        justification="2026-08-09 runtime dialogue regression screenshots + bank5F live voice discovery",
    )
    if not ext3_guard.ok:
        raise BuildError(f"ext3 guard failed: {ext3_guard.as_dict()}")

    allowed: list[tuple[int, int]] = []
    for index in slot_payload:
        seg, _local = bank_local_for_index(index)
        allowed.append((seg * BANK_SIZE, (seg + 1) * BANK_SIZE))

    for row in prepared:
        body_len = int(row["body_len"])
        slot = int(row["slot"])
        if row["strategy"] == "compact3":
            token = token_from_compact3_index(slot)
            if len(token) != 3 or body_len != 3:
                raise BuildError(f"compact3 token/body mismatch at {row['abs']}")
            new_body = token
        else:
            token = token_from_ext3_index(slot, num_banks=num_banks)
            if len(token) != 4 or body_len < 4:
                raise BuildError(f"ext3 token/body mismatch at {row['abs']}")
            new_body = token + b"\x01" * (body_len - 4)
        start = sb + int(row["logical"]) + len(row["prefix"])
        candidate[start:start + body_len] = new_body
        if candidate[int(row["terminator"])] != 0:
            raise BuildError(f"terminator moved at {row['abs']}")
        row["slot_hex"] = f"{slot:05X}" if row["strategy"] == "ext3" else f"{slot:04X}"
        row["new_body_hex"] = new_body.hex().upper()
        allowed.append((start, start + body_len))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    for row in prepared:
        before_payload, before_term = payload_at(parent, int(row["logical"]))
        after_payload, after_term = payload_at(result, int(row["logical"]))
        prefix = bytes(row["prefix"])
        if prefix and not after_payload.startswith(prefix):
            failures.append({"abs": row["abs"], "reason": "prefix_changed"})
            continue
        after_body = after_payload[len(prefix):]
        rendered = strip_pad(d_result.expand(after_body, tbl))
        reasons: list[str] = []
        if len(after_payload) != len(before_payload):
            reasons.append("record_length_changed")
        if after_term != before_term:
            reasons.append("terminator_changed")
        if rendered != row["desired_norm"]:
            reasons.append(f"render_mismatch:{rendered!r}")
        line_cells = [len(line) for line in visible_lines(rendered)]
        if line_cells and max(line_cells) > LINE_LIMIT:
            reasons.append(f"over_{LINE_LIMIT}:{line_cells}")
        if row["category"] in {"battle", "bank5f"} and has_japanese(rendered):
            reasons.append("japanese_residual")
        if reasons:
            failures.append({"abs": row["abs"], "reasons": reasons})
        row["rendered_after"] = rendered
        row["rendered_line_cells"] = line_cells

    intervals = merge_intervals(allowed)
    unexpected = [
        off for off, (a, b) in enumerate(zip(parent, result))
        if a != b and not covered(off, intervals)
    ]
    if failures or unexpected:
        raise BuildError(
            f"post-build verification failed failures={failures[:10]} unexpected={unexpected[:20]}"
        )

    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_ROM.with_name(f".{OUT_ROM.name}.{os.getpid()}.tmp")
    tmp.write_bytes(result)
    os.replace(tmp, OUT_ROM)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    SRAM_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MAIN_SAVE, SRAM_MIRROR)

    counts = {
        "records": len(prepared),
        "scenario_records": sum(r["category"] == "scenario" for r in prepared),
        "battle_5d5e_records": sum(r["category"] == "battle" for r in prepared),
        "bank5f_live_voice_records": sum(r["category"] == "bank5f" for r in prepared),
        "compact3_records": sum(r["strategy"] == "compact3" for r in prepared),
        "unique_phrase_slots": len(slot_payload),
        "terminator_changes": 0,
        "unexpected_diff_offsets": 0,
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_runtime_dialogue_regression_followup_candidate.py",
        "ok": True,
        "parent": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(parent), "size": len(parent)},
        "candidate": {"path": str(OUT_ROM.relative_to(ROOT)), "sha256": sha(result), "size": len(result), "ws_checksum": f"{checksum:04X}"},
        "candidate_save": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(OUT_SAVE.read_bytes()), "size": OUT_SAVE.stat().st_size},
        "sram_mirror": {"path": str(SRAM_MIRROR.relative_to(ROOT)), "sha256": sha(SRAM_MIRROR.read_bytes()), "size": SRAM_MIRROR.stat().st_size},
        "scenario_review_range": spec.get("scenario_review_range"),
        "counts": counts,
        "ext3_guard": ext3_guard.as_dict(),
        "ext3_write": ext3_write,
        "targets": [
            {k: v for k, v in row.items() if k not in {"encoded", "prefix"}}
            for row in prepared
        ],
        "allowed_intervals": [[a, b] for a, b in intervals],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    print(json.dumps(report["candidate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
