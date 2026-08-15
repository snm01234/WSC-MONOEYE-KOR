#!/usr/bin/env python3
"""Repair opening-window stock invasion without rewriting shared words in place.

History (PATCH_PROGRESS / apply_opening_safe_slots.py):
  The opening renderer (`0x6040A5–0x604570`) does not walk the ext3 hook, so
  narration was localized with 2-byte stock tokens. Several of those stock
  indices still had aux/name75 consumers (e.g. 0132 障壁, 014D 有利). Overwriting
  the shared phrase poisoned battle/UI text, and two opening bodies still carry
  illegal E5 18 tokens that expand to the wrong short words 추궁/수완.

This candidate builder:
  1. Finds opening records that either contain E5 18 or reference a stock slot
     whose tip payload differs from Original AND still has aux/name75 refs.
  2. Moves those opening phrases onto guard-approved free stock slots (spill).
  3. Restores the invaded stock payloads from the Original ROM.
  4. Never writes E5 18 into the opening window.

Does not modify the live main TIP unless --commit is used with an explicit
candidate path (default writes a side candidate only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from apply_opening_safe_slots import (
    OPENING_HI,
    OPENING_LO,
    OPENING_TAIL_FALLBACK_LO,
    safe_slots,
)
from apply_safe_unit import padded_token_payload
from expand_dictionary import build_dict_token_locs, iter_dict_indices, write_dictionary_slots_spill
from extract_script import split_prefix_body
from hangul_marker import marker_code
from monoeye_rom import (
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    ws_header,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SEED = ROOT / "data/translations_seed_hook96.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
JP_TBL = ROOT / "data/monoeye.tbl"
OUT_ROM = ROOT / "out/patch/opening_stock_invasion_repair_candidate.wsc"
OUT_SAVE = ROOT / "sram/opening_stock_invasion_repair_candidate.sav"
REPORT = ROOT / "out/patch/opening_stock_invasion_repair_report.json"

HANGUL_MARKER = marker_code()
TOKEN_LEN = 2
MAX_PAD = 32
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class RepairError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha256(payload),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def load_seed() -> dict[str, str]:
    data = json.loads(SEED.read_text(encoding="utf-8"))
    lines = data["lines"] if isinstance(data, dict) else data
    return {
        str(row["abs"]).upper(): normalize_ko_text(str(row.get("ko") or ""))
        for row in lines
        if str(row.get("abs") or "")
    }


def collect_targets(
    tip: bytes,
    orig: bytes,
    dictionary: Any,
    orig_dictionary: Dictionary,
) -> tuple[list[dict[str, Any]], list[int]]:
    sb = stock_base(tip)
    stock_count = orig_dictionary.stock_count
    aux_tip = build_dict_token_locs(tip, regions=("aux", "name75"))
    aux_orig = build_dict_token_locs(orig, regions=("aux", "name75"))
    seed = load_seed()
    tbl = Tbl.load(TBL)

    rewritten_sites: dict[int, list[str]] = defaultdict(list)
    e518_sites: list[str] = []
    records: dict[str, dict[str, Any]] = {}

    cursor = OPENING_LO
    while cursor <= OPENING_HI:
        got = read_encoded_z_safe(tip, sb + cursor, max_len=256)
        if not got:
            cursor += 1
            continue
        payload = got[0]
        prefix, body, _kind = split_prefix_body(payload)
        abs_hex = f"{cursor:06X}"
        render = dictionary.expand(body, tbl).rstrip("\u3000")
        records[abs_hex] = {
            "abs": abs_hex,
            "prefix": prefix,
            "body": body,
            "payload": payload,
            "render": render,
            "logical": cursor,
        }
        if b"\xE5\x18" in body:
            e518_sites.append(abs_hex)
        for index in iter_dict_indices(body):
            if not 0 <= index < stock_count:
                continue
            if bytes(dictionary.raw_entry(index)) != bytes(orig_dictionary.raw_entry(index)):
                rewritten_sites[index].append(abs_hex)
        cursor = (got[1] - sb) + 1

    invasion_slots = sorted(
        index
        for index, sites in rewritten_sites.items()
        if index in aux_tip or index in aux_orig
    )
    need_sites = set(e518_sites)
    for index in invasion_slots:
        need_sites.update(rewritten_sites[index])

    targets: list[dict[str, Any]] = []
    for abs_hex in sorted(need_sites):
        if OPENING_TAIL_FALLBACK_LO <= int(abs_hex, 16) <= OPENING_HI:
            raise RepairError(f"refusing width-sensitive opening tail site {abs_hex}")
        row = records[abs_hex]
        ko = seed.get(abs_hex) or (normalize_ko_text(row["render"]) if hangul(row["render"]) else "")
        if not ko or not hangul(ko):
            raise RepairError(f"no Hangul target for {abs_hex}: render={row['render']!r}")
        if len(row["prefix"]) + TOKEN_LEN > len(row["payload"]):
            raise RepairError(f"no room for 2-byte token at {abs_hex}")
        if len(row["payload"]) - len(row["prefix"]) - TOKEN_LEN > MAX_PAD:
            raise RepairError(f"pad would exceed MAX_PAD at {abs_hex}")
        targets.append(
            {
                "abs": abs_hex,
                "logical": row["logical"],
                "prefix": row["prefix"],
                "payload": row["payload"],
                "before": row["render"],
                "ko": ko,
                "reason": "e518_illegal" if abs_hex in e518_sites else "shared_stock_invasion",
            }
        )
    return targets, invasion_slots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit-candidate", action="store_true")
    args = parser.parse_args()

    tip = bytes(load_rom(MAIN))
    orig = bytes(load_rom(ORIGINAL))
    save = MAIN_SAVE.read_bytes()
    if len(tip) != ROM_SIZE or len(save) != SAVE_SIZE:
        raise RepairError("main tip/saveram size drift")

    tbl = Tbl.load(TBL)
    jp_tbl = Tbl.load(JP_TBL)
    ext_meta = load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json")
    ext3_meta = load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json")
    parent_dictionary = make_dictionary_ext3(tip, ext_meta, ext3_meta)
    orig_dictionary = Dictionary(orig)
    targets, invasion_slots = collect_targets(tip, orig, parent_dictionary, orig_dictionary)

    pool = safe_slots(orig, tip, orig_dictionary.stock_count)
    if len(pool) < len(targets):
        raise RepairError(f"need {len(targets)} safe slots, only {len(pool)} available")

    candidate = bytearray(tip)
    sb = stock_base(candidate)
    slot_payload: dict[int, bytes] = {}
    planned: list[dict[str, Any]] = []
    for row, slot in zip(targets, pool):
        encoded = try_encode_ko_text(
            row["ko"], tbl, hangul_marker_code=HANGUL_MARKER, hangul_marker_mode="run"
        )
        if encoded is None or b"\x00" in encoded:
            raise RepairError(f"encode failed for {row['abs']}: {row['ko']!r}")
        slot_payload[slot] = encoded
        planned.append({**row, "slot": slot, "token": token_from_dict_index(slot).hex().upper()})

    write_dictionary_slots_spill(candidate, slot_payload)
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)

    applied: list[dict[str, Any]] = []
    for row in planned:
        logical = int(row["logical"])
        got = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if not got:
            raise RepairError(f"record vanished at {row['abs']}")
        original = got[0]
        prefix, _body, _ = split_prefix_body(original)
        token = token_from_dict_index(row["slot"])
        payload = padded_token_payload(prefix, token, original)
        candidate[sb + logical : sb + logical + len(payload)] = payload
        decoded = dictionary.expand(split_prefix_body(payload)[1], tbl).rstrip("\u3000")
        if decoded != row["ko"]:
            raise RepairError(f"decode mismatch at {row['abs']}: {decoded!r} != {row['ko']!r}")
        if b"\xE5\x18" in payload:
            raise RepairError(f"E5 18 leaked back into opening at {row['abs']}")
        applied.append(
            {
                "abs": row["abs"],
                "slot": f"{row['slot']:04X}",
                "token": row["token"],
                "before": row["before"],
                "after": row["ko"],
                "reason": row["reason"],
                "ok": True,
            }
        )

    # Restore invaded shared stock phrases from Original after opening retarget.
    restore_payloads = {
        index: bytes(orig_dictionary.raw_entry(index)) for index in invasion_slots
    }
    write_dictionary_slots_spill(
        candidate,
        restore_payloads,
        allow_aux_consumers=True,
        locs=build_dict_token_locs(bytes(candidate), regions=("script", "name75", "aux")),
    )
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)

    restored: list[dict[str, Any]] = []
    for index in invasion_slots:
        tip_text = parent_dictionary.expand(bytes(parent_dictionary.raw_entry(index)), tbl)
        now = dictionary.expand(bytes(dictionary.raw_entry(index)), tbl)
        orig_text = orig_dictionary.expand(bytes(orig_dictionary.raw_entry(index)), jp_tbl)
        if bytes(dictionary.raw_entry(index)) != bytes(orig_dictionary.raw_entry(index)):
            raise RepairError(f"failed to restore stock {index:04X}")
        restored.append(
            {
                "slot": f"{index:04X}",
                "before_ko_or_mixed": tip_text,
                "restored_jp": orig_text,
                "ok": True,
            }
        )

    # Opening targets must still render the dedicated Hangul.
    for row in applied:
        logical = int(row["abs"], 16)
        got = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if not got:
            raise RepairError(f"opening record missing after restore at {row['abs']}")
        actual = dictionary.expand(split_prefix_body(got[0])[1], tbl).rstrip("\u3000")
        if actual != row["after"]:
            raise RepairError(f"post-restore opening drift at {row['abs']}: {actual!r}")

    # Invaded aux consumers must no longer expand to the stolen Hangul story lines.
    aux_locs = build_dict_token_locs(bytes(candidate), regions=("aux", "name75"))
    aux_failures: list[dict[str, Any]] = []
    stolen_phrases = {row["after"] for row in applied}
    for index in invasion_slots:
        for ref in aux_locs.get(index, [])[:20]:
            abs_hex = f"{ref.abs:06X}"
            got = read_encoded_z_safe(candidate, sb + ref.abs, max_len=256)
            if not got:
                continue
            text = dictionary.expand(split_prefix_body(got[0])[1], tbl).rstrip("\u3000")
            if text in stolen_phrases or any(
                len(phrase) >= 4 and phrase == text for phrase in stolen_phrases
            ):
                aux_failures.append({"abs": abs_hex, "slot": f"{index:04X}", "text": text})
    if aux_failures:
        raise RepairError(f"aux still shows stolen opening phrases: {aux_failures[:5]}")

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    report = {
        "schema_version": 1,
        "generated_by": "tools/repair_opening_stock_invasion_candidate.py",
        "ok": True,
        "canonical": False,
        "promotion_allowed": False,
        "parent": identity(MAIN, tip),
        "candidate": identity(OUT_ROM, candidate_bytes) if args.commit_candidate else {
            "path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
            "size": len(candidate_bytes),
            "sha256": sha256(candidate_bytes),
        },
        "history_refs": [
            "PATCH_PROGRESS.md §오프닝 창은 ext3 훅을 경유하지 않는다 / apply_opening_safe_slots.py",
            "tools/apply_opening_safe_slots.py",
            "tools/patch_opening_narration.py (폐기: 공유 슬롯 하이재킹)",
        ],
        "counts": {
            "opening_retargets": len(applied),
            "e518_repairs": sum(1 for row in applied if row["reason"] == "e518_illegal"),
            "invasion_slot_restores": len(restored),
            "safe_slots_remaining_after_plan": len(pool) - len(planned),
            "aux_stolen_phrase_failures": len(aux_failures),
        },
        "checksum": {
            "before": f"{ws_header(tip)['checksum']:04X}",
            "after": f"{checksum:04X}",
        },
        "applied_opening": applied,
        "restored_slots": restored,
        "checks": {
            "opening_exact": True,
            "no_opening_e518": True,
            "invaded_slots_restored_to_original": True,
            "aux_no_stolen_opening_phrase": not aux_failures,
        },
    }

    if args.commit_candidate:
        atomic_bytes(OUT_ROM, candidate_bytes)
        atomic_bytes(OUT_SAVE, save)
        report["candidate"] = identity(OUT_ROM)
        report["candidate_save"] = {
            **identity(OUT_SAVE),
            "policy": "test-only snapshot of current main SaveRAM; never promote SaveRAM",
        }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("ok", "counts", "checksum", "candidate")}, ensure_ascii=False, indent=2))
    print(f"report -> {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
