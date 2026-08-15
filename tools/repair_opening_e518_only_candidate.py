#!/usr/bin/env python3
"""Rollback opening stock-invasion repair, then fix only opening E5 18 sites.

Keeps the later sample54 audio repair. Does NOT restore shared-stock invasion
slots or retarget the other opening lines.

Hardening vs the previous spill: free-slot selection also scans bank-75 UI
label table ``0x75B000–0x75C000`` (``verify_nondialogue_text.UI_TABLE_RANGES``),
which ``NAME75_RANGES`` omits — that gap let ``07B6``/``명중`` get overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from apply_opening_safe_slots import OPENING_HI, OPENING_LO, safe_slots
from apply_safe_unit import padded_token_payload
from expand_dictionary import (
    NAME75_RANGES,
    build_dict_token_locs,
    iter_dict_indices,
    write_dictionary_slots_spill,
    _walk_zstring_range,
)
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
PRE_OPENING = (
    ROOT
    / "out/patch/backup/20260804_222532_pre_opening_stock_invasion_repair"
    / "monoeye_ko_expanded.wsc"
)
POST_OPENING = (
    ROOT
    / "out/patch/backup/20260804_223929_pre_id_command_audio_sample54_table_repair"
    / "monoeye_ko_expanded.wsc"
)
SEED = ROOT / "data/translations_seed_hook96.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
JP_TBL = ROOT / "data/monoeye.tbl"
OUT_ROM = ROOT / "out/patch/opening_e518_only_candidate.wsc"
OUT_SAVE = ROOT / "sram/opening_e518_only_candidate.sav"
REPORT = ROOT / "out/patch/opening_e518_only_report.json"

EXPECTED_TIP = "6cf6184e7e989c02b25b3e61fc28fc9e9eca354c20726b5efc7eec10918dce05"
EXPECTED_PRE = "9d5607ec320829ca0dc2dd8247fe2ca7da9040edef2cea4aa8fbd16f139ef358"
EXPECTED_POST_OPENING = "ed44538a78491a1bd93022930ff6c3ec67da0b03b9e5fb5666dd1ef4df05b692"

# Opening E5 18 mis-renders only.
E518_SITES = ("604251", "604317")
SEED_KO = {
    "604251": "３개월　뒤、공국군은",
    "604317": "３일　싸움　끝、연방군　승리。",
}

# Battle/system UI labels below NAME75_RANGES (see verify_nondialogue_text).
UI_TABLE_RANGES = ((0x75B000, 0x75C000),)

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


def require_sha(path: Path, expected: str) -> bytes:
    data = bytes(load_rom(path)) if path.suffix.lower() == ".wsc" else path.read_bytes()
    actual = sha256(data)
    if actual != expected:
        raise RepairError(f"{path}: expected {expected}, got {actual}")
    return data


def hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def ui_table_touched_indices(rom: bytes, stock_count: int) -> set[int]:
    """Stock indices referenced from bank-75 UI label table."""
    touched: set[int] = set()
    dictionary = Dictionary(rom)
    for lo, hi in UI_TABLE_RANGES:
        for _logical, payload, _kind in _walk_zstring_range(
            rom, lo, hi, region="name75_ui", max_len=64
        ):
            for index in iter_dict_indices(payload):
                if 0 <= index < stock_count:
                    touched.add(index)
                    # nested stock children
                    for child in iter_dict_indices(dictionary.raw_entry(index)):
                        if 0 <= child < stock_count:
                            touched.add(child)
    return touched


def ui_safe_slots(orig: bytes, tip: bytes, stock_count: int) -> list[int]:
    """safe_slots plus UI table + refuse tip Hangul payloads (localized UI)."""
    base = safe_slots(orig, tip, stock_count)
    ui_hit = ui_table_touched_indices(orig, stock_count) | ui_table_touched_indices(
        tip, stock_count
    )
    tip_d = Dictionary(tip)
    tbl = Tbl.load(TBL)
    out: list[int] = []
    for index in base:
        if index in ui_hit:
            continue
        text = tip_d.expand_index(index, tbl)
        # Localized short/long Hangul already on tip — do not steal for opening.
        if hangul(text):
            continue
        out.append(index)
    return out


def revert_opening_invasion(current: bytes, pre: bytes, post_opening: bytes) -> tuple[bytearray, dict[str, Any]]:
    """Undo opening-repair bytes while preserving later sample54 edits."""
    if not (len(current) == len(pre) == len(post_opening) == ROM_SIZE):
        raise RepairError("ROM size mismatch for revert")
    out = bytearray(current)
    reverted = 0
    kept_later = 0
    for i, (a, b, c) in enumerate(zip(pre, post_opening, current)):
        if a == b:
            continue
        # Opening repair changed this byte.
        if c == b:
            out[i] = a
            reverted += 1
        else:
            kept_later += 1
    return out, {
        "opening_bytes_reverted": reverted,
        "opening_bytes_kept_due_to_later_edit": kept_later,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit-candidate", action="store_true")
    args = parser.parse_args()

    tip = require_sha(MAIN, EXPECTED_TIP)
    pre = require_sha(PRE_OPENING, EXPECTED_PRE)
    post_opening = require_sha(POST_OPENING, EXPECTED_POST_OPENING)
    save = MAIN_SAVE.read_bytes()
    if len(save) != SAVE_SIZE:
        raise RepairError("SaveRAM size drift")

    candidate, revert_stats = revert_opening_invasion(tip, pre, post_opening)
    # Checksum will be rewritten after e518 edits; clear drift from revert first.
    update_ws_checksum(candidate)

    # Sanity: 명중 restored, E5 18 back on the two sites.
    tbl = Tbl.load(TBL)
    sb = stock_base(candidate)
    d0 = Dictionary(bytes(candidate))
    if d0.expand_index(0x07B6, tbl).rstrip("\u3000") != "명중":
        raise RepairError(
            f"07B6 not restored to 명중 after revert: {d0.expand_index(0x07B6, tbl)!r}"
        )
    hit_ui = read_encoded_z_safe(candidate, sb + 0x75B411, max_len=64)
    if not hit_ui:
        raise RepairError("75B411 missing after revert")
    if d0.expand(split_prefix_body(hit_ui[0])[1], tbl).rstrip("\u3000") != "명중":
        raise RepairError("75B411 not 명중 after revert")

    targets: list[dict[str, Any]] = []
    for abs_hex in E518_SITES:
        logical = int(abs_hex, 16)
        got = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if not got:
            raise RepairError(f"missing opening record {abs_hex}")
        payload = got[0]
        prefix, body, _ = split_prefix_body(payload)
        if b"\xE5\x18" not in body:
            raise RepairError(f"expected E5 18 at {abs_hex}, body={body.hex()}")
        ko = normalize_ko_text(SEED_KO[abs_hex])
        if len(prefix) + TOKEN_LEN > len(payload):
            raise RepairError(f"no room for 2-byte token at {abs_hex}")
        if len(payload) - len(prefix) - TOKEN_LEN > MAX_PAD:
            raise RepairError(f"pad too large at {abs_hex}")
        before = d0.expand(body, tbl).rstrip("\u3000")
        targets.append(
            {
                "abs": abs_hex,
                "logical": logical,
                "prefix": prefix,
                "payload": payload,
                "before": before,
                "ko": ko,
            }
        )

    orig = bytes(load_rom(ORIGINAL))
    orig_d = Dictionary(orig)
    pool = ui_safe_slots(orig, bytes(candidate), orig_d.stock_count)
    if len(pool) < len(targets):
        raise RepairError(f"need {len(targets)} UI-safe slots, have {len(pool)}")

    # Prefer higher free indices (early stock more often holds UI nouns).
    chosen = pool[-len(targets) :]
    slot_payload: dict[int, bytes] = {}
    planned: list[dict[str, Any]] = []
    for row, slot in zip(targets, chosen):
        encoded = try_encode_ko_text(
            row["ko"], tbl, hangul_marker_code=HANGUL_MARKER, hangul_marker_mode="run"
        )
        if encoded is None or b"\x00" in encoded:
            raise RepairError(f"encode failed for {row['abs']}: {row['ko']!r}")
        slot_payload[slot] = encoded
        planned.append({**row, "slot": slot, "token": token_from_dict_index(slot).hex().upper()})

    write_dictionary_slots_spill(candidate, slot_payload)
    ext_meta = load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json")
    ext3_meta = load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json")
    dictionary = make_dictionary_ext3(bytes(candidate), ext_meta, ext3_meta)

    applied: list[dict[str, Any]] = []
    for row in planned:
        logical = row["logical"]
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
            raise RepairError(f"E5 18 still present at {row['abs']}")
        applied.append(
            {
                "abs": row["abs"],
                "slot": f"{row['slot']:04X}",
                "token": row["token"],
                "before": row["before"],
                "after": row["ko"],
                "ok": True,
            }
        )

    dictionary = make_dictionary_ext3(bytes(candidate), ext_meta, ext3_meta)
    # Final gates
    if dictionary.expand_index(0x07B6, tbl).rstrip("\u3000") != "명중":
        raise RepairError("07B6 drifted away from 명중")
    hit_ui = read_encoded_z_safe(candidate, sb + 0x75B411, max_len=64)
    if dictionary.expand(split_prefix_body(hit_ui[0])[1], tbl).rstrip("\u3000") != "명중":
        raise RepairError("75B411 drifted away from 명중")
    for abs_hex in E518_SITES:
        got = read_encoded_z_safe(candidate, sb + int(abs_hex, 16), max_len=256)
        text = dictionary.expand(split_prefix_body(got[0])[1], tbl).rstrip("\u3000")
        if text != SEED_KO[abs_hex]:
            raise RepairError(f"final opening mismatch at {abs_hex}: {text!r}")
        if b"\xE5\x18" in got[0]:
            raise RepairError(f"final E5 18 at {abs_hex}")

    # Sample54 marker: primary trampoline area should still differ from pre-opening
    # (spot-check sample54 backup identity is not required here; checksum update only).
    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    report = {
        "schema_version": 1,
        "generated_by": "tools/repair_opening_e518_only_candidate.py",
        "ok": True,
        "canonical": False,
        "promotion_allowed": False,
        "parent": identity(MAIN, tip),
        "inputs": {
            "pre_opening_backup": identity(PRE_OPENING, pre),
            "post_opening_backup": identity(POST_OPENING, post_opening),
        },
        "revert_opening_invasion": revert_stats,
        "counts": {
            "e518_repairs": len(applied),
            "ui_safe_pool_remaining": len(pool) - len(planned),
            "invasion_slot_restores": 0,
            "opening_retargets_other": 0,
        },
        "checksum": {
            "before": f"{ws_header(tip)['checksum']:04X}",
            "after": f"{checksum:04X}",
        },
        "applied_opening": applied,
        "guards": {
            "name75_ranges": [f"{lo:06X}-{hi:06X}" for lo, hi in NAME75_RANGES],
            "ui_table_ranges": [f"{lo:06X}-{hi:06X}" for lo, hi in UI_TABLE_RANGES],
            "refuse_tip_hangul_payload": True,
        },
        "checks": {
            "opening_e518_sites_exact": True,
            "no_opening_e518_on_targets": True,
            "hit_label_75B411_is_myeongjung": True,
            "slot_07B6_is_myeongjung": True,
            "no_shared_invasion_restore": True,
        },
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
            "size": len(candidate_bytes),
            "sha256": sha256(candidate_bytes),
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
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "revert": revert_stats,
                "applied": applied,
                "checksum": report["checksum"],
                "candidate_sha256": report["candidate"]["sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"report -> {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
