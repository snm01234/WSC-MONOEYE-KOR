#!/usr/bin/env python3
"""Build the 2026-08-09 runtime measurement/translation follow-up candidate.

This candidate is intentionally detached from shared translated phrases:

* every targeted script/name75 record with >=4 body bytes is rebound to a
  union-proven true-free E5 18 ext3 slot while preserving record extent,
  prefix bytes and terminator;
* the one 3-byte Judau/Chara-name record is rebound to a dedicated compact3
  slot, avoiding a global rewrite of the shared ``캬라`` stock token;
* stock dictionary index 006F (呐喊 -> 함성) is rewritten in place to 돌격 only
  after proving its payload length is unchanged;
* all 124 currently measured >20-cell battle voices are source-grounded and
  constrained to <=20 visible cells.

The live main TIP is never modified.  The current 32 KiB SaveRAM is copied next
 to the candidate only after all post-build guards pass.
"""
from __future__ import annotations

import csv
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
from audit_dialogue_20cell_candidate import load_battle_prefixes, strip_pad
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
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_compact3_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import (
    EXP3_SEG0,
    bank_local_for_index,
    token_from_ext3_index,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/runtime_measurement_followup_ko.json"
BASELINE = ROOT / "out/patch/runtime_measurement_followup_width_baseline.json"
DUPLICATE_LEADS = ROOT / "out/script/battle_dialogue_duplicate_lead_stock_rehome_targets.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/runtime_measurement_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/runtime_measurement_followup_candidate.sav"
OUT_REPORT = ROOT / "out/patch/runtime_measurement_followup_report.json"
SRAM_MIRROR = ROOT / "sram/runtime_measurement_followup_candidate.sav"
EXPECTED_MAIN_SHA = "48320a9336346bf6c6b230b7199426197a7a6321a16d4caed9989aa29c6d9c13"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
LIMIT = 20


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


def target_prefix(address: str, payload: bytes, battle_prefixes: dict[str, bytes]) -> tuple[bytes, str]:
    if address.startswith(("5D", "5E")):
        prefix = battle_prefixes.get(address, b"")
        # The integrated cleanup already converted many formerly-prefixed
        # records to body-only form.  The width auditor deliberately treats a
        # non-matching historical prefix as absent; mirror that exact rule here
        # instead of reintroducing the removed source/control code unit.
        if prefix and not payload.startswith(prefix):
            return b"", "battle_prefix_already_removed"
        return prefix, "battle_prefix_inventory"
    if address.startswith(("60", "61", "62", "63")):
        prefix, _body, kind = split_prefix_body(payload)
        return bytes(prefix), f"split_prefix_body:{kind}"
    return b"", "body_only"


def add_target(
    targets: dict[str, dict[str, Any]],
    *,
    address: str,
    desired: str,
    source_jp: str,
    category: str,
    reason: str,
) -> None:
    address = address.upper()
    incoming = {
        "abs": address,
        "desired": desired,
        "source_jp": source_jp,
        "category": category,
        "reason": reason,
    }
    old = targets.get(address)
    if old is not None:
        if normalize_ko_text(str(old["desired"])) != normalize_ko_text(desired):
            raise BuildError(
                f"conflicting target {address}: {old['desired']!r} != {desired!r}"
            )
        old["category"] = f"{old['category']}+{category}"
        old["reason"] = f"{old['reason']}; {reason}"
        return
    targets[address] = incoming


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"current main TIP identity drifted: {sha(parent)}")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live 32 KiB SaveRAM missing")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    if str(spec.get("parent_main_sha256") or "").lower() != EXPECTED_MAIN_SHA:
        raise BuildError("spec parent SHA drifted")
    baseline_sha = str((baseline.get("input") or {}).get("sha256") or (baseline.get("rom") or {}).get("sha256") or "").lower()
    if baseline_sha and baseline_sha != EXPECTED_MAIN_SHA:
        raise BuildError(f"baseline parent SHA drifted: {baseline_sha}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 16)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    battle_prefixes = load_battle_prefixes()
    sb = stock_base(parent)

    # ------------------------------------------------------------------
    # Build one source-grounded target set.
    # ------------------------------------------------------------------
    targets: dict[str, dict[str, Any]] = {}
    battle_offenders = [
        r for r in (baseline.get("offenders") or [])
        if r.get("scope") == "battle_voice"
    ]
    width_map: dict[str, str] = dict(spec.get("battle_width_rewrites") or {})
    unique_sources = {str(r["source_jp"]) for r in battle_offenders}
    if len(battle_offenders) != 124 or len(unique_sources) != 101:
        raise BuildError(
            f"battle width population drifted: records={len(battle_offenders)} sources={len(unique_sources)}"
        )
    if set(width_map) != unique_sources:
        raise BuildError(
            f"battle width source coverage mismatch missing={sorted(unique_sources-set(width_map))[:5]} "
            f"extra={sorted(set(width_map)-unique_sources)[:5]}"
        )
    for row in battle_offenders:
        add_target(
            targets,
            address=str(row["abs"]),
            desired=width_map[str(row["source_jp"])],
            source_jp=str(row["source_jp"]),
            category="battle_width",
            reason="measured current battle voice exceeds 20 visible cells",
        )

    # Reapply the complete 70-row duplicate-lead cleanup that was previously
    # runtime-validated and promoted.  Only rows whose visible lead has actually
    # reappeared in this parent need a record rewrite.  If a row is already in
    # the width set, keep the newer <=20 wording and merely annotate it.
    with DUPLICATE_LEADS.open(encoding="utf-8-sig", newline="") as handle:
        duplicate_rows = list(csv.DictReader(handle))
    if len(duplicate_rows) != 70:
        raise BuildError(f"duplicate-lead ledger drifted: {len(duplicate_rows)}")
    duplicate_reintroduced = 0
    for dup in duplicate_rows:
        address = str(dup["abs"]).upper()
        payload, _term = payload_at(parent, int(address, 16))
        lead = bytes.fromhex(str(dup["removed_lead_hex"]))
        if not payload.startswith(lead):
            continue
        duplicate_reintroduced += 1
        if address in targets:
            targets[address]["category"] = f"{targets[address]['category']}+duplicate_lead_recurrence"
            targets[address]["reason"] = f"{targets[address]['reason']}; reapply previously runtime-validated duplicate-lead cleanup"
        else:
            # The old duplicate-lead CSV proves the *structure* only.  Its
            # stored Korean render predates later terminology corrections
            # (e.g. 오르바 -> 올바), so preserve the current main TIP's body
            # text after the duplicated lead instead of reviving stale wording.
            current_body = strip_pad(d_parent.expand(payload[len(lead):], tbl))
            add_target(
                targets,
                address=address,
                desired=current_body,
                source_jp="previously_runtime_validated_duplicate_lead",
                category="duplicate_lead_recurrence",
                reason="remove reintroduced visible first word while preserving current translated body",
            )
    if duplicate_reintroduced != 64:
        raise BuildError(f"duplicate-lead recurrence population drifted: {duplicate_reintroduced} != 64")

    terminology = list(baseline.get("terminology_residuals") or [])
    name_counts = {"judau": 0, "chara": 0}
    for row in terminology:
        reasons = set(row.get("reasons") or [])
        source = str(row.get("source_jp") or "")
        current = str(row.get("current_text") or "")
        if "judau_name_mistransliteration" in reasons:
            rule = spec["proper_name_rules"]["judau"]
            if rule["source_contains"] not in source or rule["before"] not in current:
                raise BuildError(f"Judau rule evidence drift at {row['abs']}")
            desired = current.replace(rule["before"], rule["after"])
            add_target(
                targets,
                address=str(row["abs"]), desired=desired, source_jp=source,
                category="proper_name_judau", reason="주도 -> 쥬도 source-bound standardization",
            )
            name_counts["judau"] += 1
        if "chara_name_mistransliteration" in reasons:
            rule = spec["proper_name_rules"]["chara"]
            if rule["source_contains"] not in source or rule["before"] not in current:
                raise BuildError(f"Chara rule evidence drift at {row['abs']}")
            desired = current.replace(rule["before"], rule["after"])
            add_target(
                targets,
                address=str(row["abs"]), desired=desired, source_jp=source,
                category="proper_name_chara", reason="캬라 -> 캐라 source-bound standardization",
            )
            name_counts["chara"] += 1
    if name_counts != {"judau": 50, "chara": 7}:
        raise BuildError(f"proper-name population drifted: {name_counts}")

    for address, row in (spec.get("direct_targets") or {}).items():
        add_target(
            targets,
            address=address,
            desired=str(row["after"]),
            source_jp=str(row["source_jp"]),
            category="direct_runtime_followup",
            reason=str(row["reason"]),
        )

    # Ensure every non-name terminology regression reported by the baseline is
    # explicitly covered by either the width target set or direct targets.
    required_reason = {
        "kato_machine_translation_residual",
        "duplicated_haman",
        "cross_record_tail_leak",
        "visible_japanese_lead",
    }
    uncovered = [
        {"abs": r["abs"], "reasons": r["reasons"]}
        for r in terminology
        if required_reason.intersection(r.get("reasons") or [])
        and str(r["abs"]).upper() not in targets
    ]
    if uncovered:
        raise BuildError(f"terminology regression target uncovered: {uncovered}")

    prepared: list[dict[str, Any]] = []
    for address, row in sorted(targets.items()):
        logical = int(address, 16)
        payload, terminator = payload_at(parent, logical)
        prefix, prefix_basis = target_prefix(address, payload, battle_prefixes)
        body = payload[len(prefix):]
        desired_norm, encoded = encode(str(row["desired"]), tbl)
        cells = len(desired_norm.replace("<E62F>", ""))
        if cells > LIMIT and ("battle_width" in str(row["category"]) or address in {"630695", "63CFEA"}):
            raise BuildError(f"target still exceeds {LIMIT} cells at {address}: {cells} {desired_norm!r}")
        before = strip_pad(d_parent.expand(body, tbl))
        strategy = "ext3" if len(body) >= 4 else "compact3" if len(body) == 3 else "unsupported"
        if strategy == "unsupported":
            raise BuildError(f"body too short at {address}: {len(body)}")
        prepared.append({
            **row,
            "logical": logical,
            "prefix": prefix,
            "prefix_hex": prefix.hex().upper(),
            "prefix_basis": prefix_basis,
            "payload_len": len(payload),
            "body_len": len(body),
            "before_payload_hex": payload.hex().upper(),
            "before": before,
            "desired_norm": desired_norm,
            "encoded": encoded,
            "cells": cells,
            "terminator": terminator,
            "terminator_logical": terminator - sb,
            "strategy": strategy,
        })

    compact_rows = [r for r in prepared if r["strategy"] == "compact3"]
    if [r["abs"] for r in compact_rows] != ["61E6D7"]:
        raise BuildError(f"unexpected compact3 target set: {[r['abs'] for r in compact_rows]}")

    # ------------------------------------------------------------------
    # Allocate only true-free slots.  Dedupe identical desired phrases.
    # ------------------------------------------------------------------
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    room = {int(bank): int(value) for bank, value in inventory.ext3_bank_room.items()}
    free_all = sorted(int(index) for index in inventory.ext3_free)

    compact_unique: dict[bytes, dict[str, Any]] = {}
    ext3_unique: dict[bytes, dict[str, Any]] = {}
    for row in prepared:
        if row["strategy"] == "compact3":
            compact_unique.setdefault(row["encoded"], row)
        else:
            ext3_unique.setdefault(row["encoded"], row)

    compact_free = [
        index for index in range(COMPACT3_INDEX_BASE + 1, COMPACT3_INDEX_END + 1)
        if union.is_true_free(index)
    ]
    if len(compact_free) < len(compact_unique):
        raise BuildError(f"compact3 capacity exhausted: need {len(compact_unique)} found {len(compact_free)}")

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
        justification="2026-08-09 runtime screenshot + full battle 20-cell measurement follow-up",
    )
    if not ext3_guard.ok:
        raise BuildError(f"ext3 guard failed: {ext3_guard.as_dict()}")

    allowed: list[tuple[int, int]] = []
    for index in slot_payload:
        seg, _local = bank_local_for_index(index)
        allowed.append((seg * BANK_SIZE, (seg + 1) * BANK_SIZE))

    # ------------------------------------------------------------------
    # Rebind target records without changing record extents/terminators.
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # GP03 Dendrobium part label: exact-length stock phrase rewrite.
    # ------------------------------------------------------------------
    stock_rows: list[dict[str, Any]] = []
    for raw_index, target in (spec.get("stock_dictionary_targets") or {}).items():
        index = int(raw_index, 16)
        before_text = d_parent.expand_index(index, tbl)
        if before_text != target["before"]:
            raise BuildError(f"stock slot {index:04X} text drifted: {before_text!r}")
        normalized, encoded = encode(str(target["after"]), tbl)
        old_raw = bytes(d_parent.raw_entry(index))
        if len(encoded) != len(old_raw):
            raise BuildError(
                f"stock slot {index:04X} replacement must be exact length: {len(encoded)} != {len(old_raw)}"
            )
        entry_abs = int(d_parent.entry_abs(index))
        candidate[entry_abs:entry_abs + len(old_raw)] = encoded
        if candidate[entry_abs + len(old_raw)] != 0:
            raise BuildError(f"stock slot terminator drift at {index:04X}")
        allowed.append((entry_abs, entry_abs + len(old_raw)))
        stock_rows.append({
            "index": f"{index:04X}",
            "source_jp": target["source_jp"],
            "before": before_text,
            "after": normalized,
            "entry_abs": entry_abs,
            "old_hex": old_raw.hex().upper(),
            "new_hex": encoded.hex().upper(),
            "reason": target["reason"],
        })

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)

    # ------------------------------------------------------------------
    # Post-build exact render/extent/terminator verification.
    # ------------------------------------------------------------------
    failures: list[dict[str, Any]] = []
    for row in prepared:
        before_payload, before_term = payload_at(parent, int(row["logical"]))
        after_payload, after_term = payload_at(result, int(row["logical"]))
        prefix = bytes(row["prefix"])
        after_body = after_payload[len(prefix):]
        rendered = strip_pad(d_result.expand(after_body, tbl))
        reasons: list[str] = []
        if len(after_payload) != len(before_payload):
            reasons.append("record_length_changed")
        if after_term != before_term:
            reasons.append("terminator_changed")
        if prefix and after_payload[:len(prefix)] != prefix:
            reasons.append("prefix_changed")
        if rendered != row["desired_norm"]:
            reasons.append(f"render_mismatch:{rendered!r}")
        if "battle_width" in str(row["category"]) and len(rendered.replace("<E62F>", "")) > LIMIT:
            reasons.append("battle_over_20")
        if reasons:
            failures.append({"abs": row["abs"], "reasons": reasons})
        row["rendered_after"] = rendered

    for stock_row in stock_rows:
        index = int(stock_row["index"], 16)
        rendered = d_result.expand_index(index, tbl)
        if rendered != stock_row["after"]:
            failures.append({"slot": stock_row["index"], "reason": f"stock_render:{rendered!r}"})

    intervals = merge_intervals(allowed)
    unexpected = [
        off for off, (a, b) in enumerate(zip(parent, result))
        if a != b and not covered(off, intervals)
    ]
    if failures or unexpected:
        raise BuildError(
            f"post-build verification failed failures={failures[:10]} unexpected={unexpected[:20]}"
        )

    # Save only after every guard passes.
    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_ROM.with_name(f".{OUT_ROM.name}.{os.getpid()}.tmp")
    tmp.write_bytes(result)
    os.replace(tmp, OUT_ROM)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    SRAM_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MAIN_SAVE, SRAM_MIRROR)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_runtime_measurement_followup_candidate.py",
        "ok": True,
        "parent": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(parent), "size": len(parent)},
        "candidate": {"path": str(OUT_ROM.relative_to(ROOT)), "sha256": sha(result), "size": len(result), "ws_checksum": f"{checksum:04X}"},
        "candidate_save": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(OUT_SAVE.read_bytes()), "size": OUT_SAVE.stat().st_size},
        "sram_mirror": {"path": str(SRAM_MIRROR.relative_to(ROOT)), "sha256": sha(SRAM_MIRROR.read_bytes()), "size": SRAM_MIRROR.stat().st_size},
        "counts": {
            "records": len(prepared),
            "battle_width_records": sum("battle_width" in str(r["category"]) for r in prepared),
            "battle_width_unique_sources": len(unique_sources),
            "duplicate_lead_ledger_records": len(duplicate_rows),
            "duplicate_lead_reintroduced_repaired": duplicate_reintroduced,
            "judau_name_records": name_counts["judau"],
            "chara_name_records": name_counts["chara"],
            "compact3_records": len(compact_rows),
            "unique_phrase_slots": len(slot_payload),
            "stock_dictionary_exact_rewrites": len(stock_rows),
            "terminator_changes": 0,
            "unexpected_diff_offsets": 0,
        },
        "ext3_guard": ext3_guard.as_dict(),
        "ext3_write": ext3_write,
        "stock_dictionary": stock_rows,
        "targets": [
            {k: v for k, v in row.items() if k not in {"encoded", "prefix"}}
            for row in prepared
        ],
        "allowed_intervals": [[a, b] for a, b in intervals],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["candidate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
