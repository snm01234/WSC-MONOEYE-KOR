#!/usr/bin/env python3
"""Build a test candidate for mixed spirit/ID effect lines and overflowing quotes.

Rewrites leftover Japanese/Korean stew in the status-screen effect box and
ID-command activation quotes that exceed 20 cells onto new private ext3 slots.
Record length, prefix, NUL, the live main TIP, and live SaveRAM stay fixed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from apply_safe_unit import padded_token_payload
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, verify_non_target_invariance
from hangul_marker import marker_code
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    is_ff_page_index,
    write_ext3_slots_guarded,
)
from monoeye_rom import Tbl, dict_token_safe_in_zstring, load_rom, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CATALOG = ROOT / "data/spirit_mental_cmd_mixed_and_quote_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/spirit_mental_cmd_mixed_quote_candidate.wsc"
OUT_SAVE = ROOT / "sram/spirit_mental_cmd_mixed_quote_candidate.sav"
REPORT = ROOT / "out/patch/spirit_mental_cmd_mixed_quote_candidate_report.json"

EXPECTED_MAIN = "b0438b51f0a6f5fab94418433f3fac6c551ab1ee8f434627ee4e42816928216a"
EXPECTED_APPLIED = 54
EXPECTED_UNIQUE = 47
MAX_CELLS = 20
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256(data),
    }


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


def prepare_rows(parent: bytes, tbl: Tbl) -> list[dict[str, Any]]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if str(catalog.get("parent_tip_sha256") or "").lower() != EXPECTED_MAIN:
        raise BuildError("catalog parent identity drifted")
    sb = stock_base(parent)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in catalog.get("records") or []:
        address = str(source.get("abs") or "").upper()
        if address in seen:
            raise BuildError(f"duplicate catalog address {address}")
        seen.add(address)
        logical = int(address, 16)
        payload = bytes.fromhex(str(source.get("current_payload_hex") or ""))
        payload_len = int(source.get("payload_len") or -1)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        if payload_len != len(payload) or payload_len < 4:
            raise BuildError(f"payload length drifted at {address}")
        if not payload.startswith(prefix):
            raise BuildError(f"prefix mismatch at {address}")
        if b"\x00" in payload:
            raise BuildError(f"interior NUL at {address}")
        current = parent[sb + logical : sb + logical + payload_len]
        if current != payload:
            raise BuildError(f"parent payload drifted at {address}")
        if parent[sb + logical + payload_len] != 0:
            raise BuildError(f"terminator drifted at {address}")
        ko = normalize_ko_text(str(source.get("ko") or ""))
        if not ko or any(is_japanese_character(ch) for ch in ko) or len(ko) > MAX_CELLS:
            raise BuildError(f"invalid Korean at {address}: {ko!r}")
        if payload_len - len(prefix) < 4:
            raise BuildError(f"body cannot hold ext3 token at {address}")
        rows.append(
            {
                "abs": address,
                "kind": str(source.get("kind") or ""),
                "logical": logical,
                "ko": ko,
                "encoded": encode_phrase(ko, tbl),
                "prefix": prefix,
                "payload": payload,
                "payload_len": payload_len,
                "before": str(source.get("before") or ""),
                "jp": str(source.get("jp") or ""),
                "before_cells": int(source.get("before_cells") or 0),
                "after_cells": len(ko),
            }
        )
    rows.sort(key=lambda row: int(row["logical"]))
    if len(rows) != EXPECTED_APPLIED:
        raise BuildError(f"catalog count drifted: {len(rows)}")
    if len({row["ko"] for row in rows}) != EXPECTED_UNIQUE:
        raise BuildError("unique phrase count drifted")
    return rows


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN:
        raise BuildError("live main TIP identity drifted")
    main_save = MAIN_SAVE.read_bytes()
    if len(main_save) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 metadata is not installed")
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    rows = prepare_rows(parent, tbl)

    unique: dict[bytes, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["encoded"], row)
    if len(unique) != EXPECTED_UNIQUE:
        raise BuildError(f"encoded unique count drifted: {len(unique)}")

    union = build_reference_union(
        original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    free_by_bank: dict[int, list[int]] = defaultdict(list)
    for index in inventory.ext3_free:
        if not dict_token_safe_in_zstring(index) or is_ff_page_index(index):
            continue
        # Prefer non-alias locals (< 0x0600) so battle HUD and offline expand
        # agree.  Recoa's old 09B0 lived on the alias page and decoded empty.
        _seg, local = bank_local_for_index(index)
        if local >= 0x0600:
            continue
        seg, _local = bank_local_for_index(index)
        free_by_bank[seg - EXP3_SEG0].append(index)
    for values in free_by_bank.values():
        values.sort()
    room = {int(bank): int(value) for bank, value in inventory.ext3_bank_room.items()}

    encoded_to_ext3: dict[bytes, int] = {}
    ext3_slot_payload: dict[int, bytes] = {}
    for encoded, sample in sorted(unique.items(), key=lambda item: item[1]["logical"]):
        need = len(encoded) + 1
        chosen_bank = next(
            (
                bank
                for bank in sorted(room, key=lambda value: (-room[value], value))
                if room.get(bank, 0) >= need and free_by_bank.get(bank)
            ),
            None,
        )
        if chosen_bank is None:
            raise BuildError(f"no ext3 room for {sample['ko']!r}")
        index = free_by_bank[chosen_bank].pop(0)
        room[chosen_bank] -= need
        encoded_to_ext3[encoded] = index
        ext3_slot_payload[index] = encoded
    for row in rows:
        row["slot"] = encoded_to_ext3[row["encoded"]]

    scratch = bytearray(parent)
    ext3_write, _guard = write_ext3_slots_guarded(
        scratch,
        ext3_slot_payload,
        union=union,
        num_banks=num_banks,
        allow_aux_consumers=True,
        justification=(
            "candidate-bound status-screen spirit/ID effect mixed-JP repair "
            "and 20-cell ID-quote compaction; new private ext3 only"
        ),
    )
    sb = stock_base(scratch)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        token = token_from_ext3_index(int(row["slot"]), num_banks=num_banks)
        if token[:2] != b"\xE5\x18":
            raise BuildError(f"refusing non-ext3 token at {row['abs']}: {token.hex()}")
        replacement = padded_token_payload(row["prefix"], token, row["payload"])
        at = sb + int(row["logical"])
        scratch[at : at + row["payload_len"]] = replacement
        scratch[at + row["payload_len"]] = 0
        target_extents.append((at, at + row["payload_len"]))
        applied.append(
            {
                "abs": row["abs"],
                "kind": row["kind"],
                "ko": row["ko"],
                "before": row["before"],
                "jp": row["jp"],
                "slot": f"{int(row['slot']):05X}",
                "payload_len": row["payload_len"],
                "before_cells": row["before_cells"],
                "after_cells": row["after_cells"],
                "new_payload_hex": replacement.hex().upper(),
            }
        )

    checksum = update_ws_checksum(scratch)
    candidate = bytes(scratch)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    failures: list[dict[str, Any]] = []
    for row in rows:
        start = sb + int(row["logical"])
        payload = candidate[start : start + row["payload_len"]]
        prefix = row["prefix"]
        body = payload[len(prefix) :]
        actual = candidate_dictionary.expand(body, tbl).rstrip("\u3000 \t")
        if (
            actual != row["ko"]
            or len(actual) > MAX_CELLS
            or candidate[start + row["payload_len"]] != 0
        ):
            failures.append({"abs": row["abs"], "expected": row["ko"], "actual": actual})

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(row["logical"]) for row in rows},
    )
    runs = diff_runs(parent, candidate)
    allowed = target_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed) and not (0x110000 <= lo < 0x210000)
    ]
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    ok = (
        not failures
        and not invariance.get("failures")
        and not unaccounted
        and runtime_exact
        and checksum is not None
    )
    report = {
        "ok": ok,
        "promotion_allowed": False,
        "parent": identity(MAIN, parent),
        "checksum": f"{checksum:04X}",
        "applied_count": len(applied),
        "mixed_count": sum(1 for row in applied if row["kind"] == "effect_desc"),
        "quote_count": sum(1 for row in applied if row["kind"] == "id_quote"),
        "ext3_unique": len(unique),
        "max_after_cells": max(row["after_cells"] for row in rows),
        "applied": applied,
        "failures": failures,
        "invariance": {
            "checked": invariance.get("checked"),
            "failure_count": len(invariance.get("failures") or []),
            "failures": (invariance.get("failures") or [])[:20],
        },
        "unaccounted_diff_runs": unaccounted,
        "runtime_banks_7A_7F_exact": runtime_exact,
        "ext3_write": ext3_write,
        "marker_code": f"{marker_code():04X}",
        "main_untouched": True,
        "live_save_untouched": True,
    }
    if not ok:
        atomic_json(REPORT, report)
        raise BuildError("candidate failed gates")
    atomic_bytes(OUT_ROM, candidate)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    report["candidate"] = identity(OUT_ROM)
    report["save"] = identity(OUT_SAVE)
    report["save_matches_main"] = sha256(OUT_SAVE.read_bytes()) == sha256(main_save)
    if not report["save_matches_main"]:
        raise BuildError("candidate SaveRAM is not byte-exact with main")
    if sha256(MAIN.read_bytes()) != EXPECTED_MAIN:
        raise BuildError("live main TIP was modified")
    if sha256(MAIN_SAVE.read_bytes()) != sha256(main_save):
        raise BuildError("live SaveRAM was modified")
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "sha256": report["candidate"]["sha256"],
                "checksum": report["checksum"],
                "applied": report["applied_count"],
                "unique": report["ext3_unique"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
