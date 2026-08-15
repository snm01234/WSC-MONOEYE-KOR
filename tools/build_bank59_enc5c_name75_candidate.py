#!/usr/bin/env python3
"""Build bank59 title/dialogue + name75 data-tail + UI75 mixed candidate.

Parent is the current test ROM ``term_unify_round2_candidate.wsc``.  Records with
a body of at least four bytes use true-free ext3 slots; 2/3-byte bodies use
strong retired non-FF stock slots.  Prefix, payload length and NUL stay fixed.
The live main TIP and SaveRAM are never written.
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

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from apply_safe_unit import padded_token_payload
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_p2_stock_spill_candidate import _stock_phrase_cursor
from build_remaining_dialogue_candidate import (
    covered,
    diff_runs,
    encode_phrase,
    verify_non_target_invariance,
)
from expand_dictionary import NAME75_STRUCTURED_RANGES, write_dictionary_slots_spill
from hangul_marker import marker_code
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    is_ff_page_index,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = ROOT / "out/patch/term_unify_round2_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CATALOG = ROOT / "data/bank59_enc5c_name75_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/bank59_enc5c_name75_candidate.wsc"
OUT_SAVE = ROOT / "sram/bank59_enc5c_name75_candidate.sav"
REPORT = ROOT / "out/patch/bank59_enc5c_name75_candidate_report.json"

EXPECTED_PARENT = "3d8701a7d43cc551155d9eddaa692886ea763ead8b65a293520c78e0e2be41c3"
EXPECTED_MAIN = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"
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


def load_catalog() -> dict[str, Any]:
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("catalog root must be object")
    if str(value.get("parent_tip_sha256") or "").lower() != EXPECTED_PARENT:
        raise BuildError("catalog parent identity drifted")
    return value


def prepare_rows(parent: bytes, tbl: Tbl) -> list[dict[str, Any]]:
    catalog = load_catalog()
    sb = stock_base(parent)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in catalog.get("records") or []:
        address = str(source.get("abs") or "").upper()
        if address in seen:
            raise BuildError(f"duplicate catalog address {address}")
        seen.add(address)
        logical = int(address, 16)
        if any(lo <= logical < hi for lo, hi in NAME75_STRUCTURED_RANGES):
            raise BuildError(
                f"catalog targets structured terrain descriptor bytes at {address}"
            )
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload = bytes.fromhex(str(source.get("current_payload_hex") or ""))
        payload_len = int(source.get("payload_len") or -1)
        if payload_len != len(payload):
            raise BuildError(f"payload length drifted at {address}")
        if b"\x00" in payload:
            raise BuildError(f"interior NUL at {address}")
        if not payload.startswith(prefix):
            raise BuildError(f"prefix mismatch at {address}")
        body_len = payload_len - len(prefix)
        if body_len < 2:
            raise BuildError(f"body too small at {address}")
        current = parent[sb + logical : sb + logical + payload_len]
        if current != payload:
            raise BuildError(f"parent payload drifted at {address}")
        if parent[sb + logical + payload_len] != 0:
            raise BuildError(f"terminator drifted at {address}")
        jp = str(source.get("jp") or "")
        if "<BADDICT" in jp.upper():
            raise BuildError(f"BADDICT records are excluded at {address}")
        ko = normalize_ko_text(str(source.get("ko") or ""))
        if not ko or any(is_japanese_character(ch) for ch in ko):
            raise BuildError(f"invalid Korean at {address}: {ko!r}")
        encoded = encode_phrase(ko, tbl)
        strategy = "retired_stock" if body_len < 4 else "ext3"
        rows.append(
            {
                "abs": address,
                "logical": logical,
                "region": str(source.get("region") or ""),
                "jp": str(source.get("jp") or ""),
                "ko": ko,
                "encoded": encoded,
                "prefix": prefix,
                "payload": payload,
                "payload_len": payload_len,
                "body_len": body_len,
                "strategy": strategy,
            }
        )
    rows.sort(key=lambda row: int(row["logical"]))
    if not rows:
        raise BuildError("catalog has no records")
    return rows


def main() -> int:
    parent = bytes(load_rom(PARENT))
    main_rom = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_PARENT:
        raise BuildError("parent test ROM identity drifted")
    if len(main_rom) != ROM_SIZE or sha256(main_rom) != EXPECTED_MAIN:
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

    long_rows = [row for row in rows if row["strategy"] == "ext3"]
    short_rows = [row for row in rows if row["strategy"] == "retired_stock"]

    unique_short: dict[bytes, dict[str, Any]] = {}
    for row in short_rows:
        unique_short.setdefault(row["encoded"], row)
    unique_long: dict[bytes, dict[str, Any]] = {}
    for row in long_rows:
        unique_long.setdefault(row["encoded"], row)

    encoded_to_retired: dict[bytes, int] = {}
    retired_slot_payload: dict[int, bytes] = {}
    if unique_short:
        retired = [
            index
            for index in current_strong_retired_slots(original, parent, parent_dictionary)
            if dict_token_safe_in_zstring(index) and not is_ff_page_index(index)
        ]
        if len(retired) < len(unique_short):
            raise BuildError(
                f"need {len(unique_short)} strong retired slots, found {len(retired)}"
            )
        for index, encoded in zip(retired, unique_short):
            encoded_to_retired[encoded] = index
            retired_slot_payload[index] = encoded
        for row in short_rows:
            row["slot"] = encoded_to_retired[row["encoded"]]

    union = build_reference_union(
        original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    free_by_bank: dict[int, list[int]] = defaultdict(list)
    for index in inventory.ext3_free:
        if not dict_token_safe_in_zstring(index):
            continue
        seg, _local = bank_local_for_index(index)
        free_by_bank[seg - EXP3_SEG0].append(index)
    for values in free_by_bank.values():
        values.sort()
    room = {int(bank): int(value) for bank, value in inventory.ext3_bank_room.items()}

    encoded_to_ext3: dict[bytes, int] = {}
    ext3_slot_payload: dict[int, bytes] = {}
    for encoded, sample in sorted(
        unique_long.items(), key=lambda item: item[1]["logical"]
    ):
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
    for row in long_rows:
        row["slot"] = encoded_to_ext3[row["encoded"]]

    scratch = bytearray(parent)
    stock_cursor_before = _stock_phrase_cursor(parent)
    if retired_slot_payload:
        _ptrs, stock_cursor_after = write_dictionary_slots_spill(
            scratch,
            retired_slot_payload,
            allow_aux_consumers=True,
        )
    else:
        stock_cursor_after = stock_cursor_before
    ext3_write, _guard = write_ext3_slots_guarded(
        scratch,
        ext3_slot_payload,
        union=union,
        num_banks=num_banks,
        allow_aux_consumers=True,
        justification="candidate-bound bank59 title and name75 data-tail localization",
    )

    sb = stock_base(scratch)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        if row["strategy"] == "retired_stock":
            token = token_from_dict_index(int(row["slot"]))
        else:
            token = token_from_ext3_index(int(row["slot"]), num_banks=num_banks)
        replacement = padded_token_payload(row["prefix"], token, row["payload"])
        if len(replacement) != row["payload_len"]:
            raise BuildError(f"padded payload length drifted at {row['abs']}")
        at = sb + int(row["logical"])
        scratch[at : at + row["payload_len"]] = replacement
        scratch[at + row["payload_len"]] = 0
        target_extents.append((at, at + row["payload_len"]))
        applied.append(
            {
                "abs": row["abs"],
                "region": row["region"],
                "jp": row["jp"],
                "ko": row["ko"],
                "strategy": row["strategy"],
                "slot": (
                    f"{int(row['slot']):04X}"
                    if row["strategy"] == "retired_stock"
                    else f"{int(row['slot']):05X}"
                ),
                "payload_len": row["payload_len"],
                "body_len": row["body_len"],
                "prefix_hex": row["prefix"].hex().upper(),
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
        actual = candidate_dictionary.expand(payload[len(row["prefix"]) :], tbl).rstrip(
            "\u3000 \t"
        )
        expected = row["ko"].rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload[: len(row["prefix"])] != row["prefix"]:
            reasons.append("prefix_changed")
        if actual != expected:
            reasons.append("render_mismatch")
        if any(is_japanese_character(ch) for ch in actual):
            reasons.append("japanese_residual")
        if candidate[start + row["payload_len"]] != 0:
            reasons.append("terminator_changed")
        if reasons:
            failures.append(
                {
                    "abs": row["abs"],
                    "expected": expected,
                    "actual": actual,
                    "reasons": reasons,
                }
            )

    if retired_slot_payload:
        selected = set(retired_slot_payload)
        current_external = external_occurrence_map(
            parent, ext3_aware=True, wanted=selected
        )
        current_nested = nested_occurrence_map(
            parent_dictionary, wanted=selected, ext3_aware=True
        )
        current_raw = _raw_pair_hits(parent, list(selected))
        if any(
            current_external.get(i) or current_nested.get(i) or current_raw.get(i)
            for i in selected
        ):
            raise BuildError("selected retired stock slot is still reachable")

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
    # Dictionary/ext3 bank writes are large; classify unaccounted later via region.
    unaccounted = [
        {"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed)
        and not (
            0x110000 <= lo < 0x210000
            or 0x5F0000 <= lo < 0x600000
        )
    ]
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000]
        == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    main_unchanged = sha256(MAIN.read_bytes()) == EXPECTED_MAIN
    save_unchanged = sha256(MAIN_SAVE.read_bytes()) == sha256(main_save)

    ok = (
        not failures
        and not invariance.get("failures")
        and not unaccounted
        and runtime_exact
        and main_unchanged
        and save_unchanged
        and checksum is not None
    )
    report = {
        "ok": ok,
        "parent": identity(PARENT, parent),
        "main": identity(MAIN, main_rom),
        "candidate": identity(OUT_ROM, candidate) if ok else None,
        "checksum": f"{checksum:04X}",
        "applied_count": len(applied),
        "ext3_records": len(long_rows),
        "retired_records": len(short_rows),
        "ext3_unique": len(unique_long),
        "retired_unique": len(unique_short),
        "applied": applied,
        "failures": failures,
        "invariance": {
            "checked": invariance.get("checked"),
            "failure_count": len(invariance.get("failures") or []),
            "failures": (invariance.get("failures") or [])[:20],
        },
        "unaccounted_diff_runs": unaccounted,
        "runtime_banks_7A_7F_exact": runtime_exact,
        "main_unchanged": main_unchanged,
        "main_save_unchanged": save_unchanged,
        "ext3_write": ext3_write,
        "stock_phrase_cursor": {
            "before": f"{stock_cursor_before:04X}",
            "after": f"{stock_cursor_after:04X}",
        },
        "marker_code": f"{marker_code():04X}",
    }
    if not ok:
        atomic_json(REPORT, report)
        raise BuildError(f"candidate failed gates: {json.dumps({k: report[k] for k in ('failures','unaccounted_diff_runs','runtime_banks_7A_7F_exact','main_unchanged')}, ensure_ascii=True)}")

    atomic_bytes(OUT_ROM, candidate)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    report["candidate"] = identity(OUT_ROM)
    report["save"] = identity(OUT_SAVE)
    report["save_matches_main"] = sha256(OUT_SAVE.read_bytes()) == sha256(main_save)
    if not report["save_matches_main"]:
        raise BuildError("candidate SaveRAM is not byte-exact with main")
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "rom": str(OUT_ROM),
                "sha256": report["candidate"]["sha256"],
                "checksum": report["checksum"],
                "applied": report["applied_count"],
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
