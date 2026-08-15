#!/usr/bin/env python3
"""Build a candidate that repairs the hidden bank-75 ``배치`` consumers.

The P2 ``too_short`` duplicate-detachment pass reclaimed stock dictionary slot
``0021`` (original/current Korean rendering: ``배치``) for ``티탄즈가`` after
retargeting the consumers known at that time to the byte-identical keeper slot
``0573``.  Three intermission/UI records in the sequential bank-75 table were
outside that old scan and still contain ``F021``:

* 75:B4A5 ``配置``
* 75:B4A8 ``現在の配置`` (token at 75:B4AB)
* 75:B860 ``配置中``

They therefore render as ``티탄즈가``, ``現在の티탄즈가`` and ``티탄즈가中``;
the narrow UI clips the long word to forms such as ``티탄``.  This candidate
retargets only those three two-byte tokens from ``F021`` to ``F573``.  The four
approved short dialogue records keep using ``F021`` and continue to render
``티탄즈가``.  No dictionary pointer/payload, record length, terminator, runtime
code, ext3 data, or SaveRAM content is changed.

Candidate only.  It never overwrites the main TIP or main SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import (
    BANK_SIZE,
    SEG_DICT,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/ui_placement_token_repair_candidate.wsc"
OUT_SAVE = ROOT / "sram/ui_placement_token_repair_candidate.sav"
OUT_REPORT = ROOT / "out/patch/ui_placement_token_repair_report.json"

EXPECTED_MAIN_SHA256 = "971665a2fa5d571dd04500b520fb41bcc7d4929e571ca2632c9253a4e51b35ae"
ROM_SIZE = 16_777_216
RECLAIMED_SLOT = 0x0021
PLACEMENT_KEEPER_SLOT = 0x0573
EXPECTED_DIALOGUE_TOKEN_ABS = (0x61AC6C, 0x6292D0, 0x62938F, 0x63F686)

UI_RECORDS = (
    {
        "record_abs": 0x75B4A5,
        "token_abs": 0x75B4A5,
        "before_payload": bytes.fromhex("F021"),
        "after_payload": bytes.fromhex("F573"),
        "before_render": "티탄즈가",
        "after_render": "배치",
        "source_meaning": "配置",
    },
    {
        "record_abs": 0x75B4A8,
        "token_abs": 0x75B4AB,
        "before_payload": bytes.fromhex("F32005F021"),
        "after_payload": bytes.fromhex("F32005F573"),
        "before_render": "現在の티탄즈가",
        "after_render": "現在の배치",
        "source_meaning": "現在の配置",
    },
    {
        "record_abs": 0x75B860,
        "token_abs": 0x75B860,
        "before_payload": bytes.fromhex("F02152"),
        "after_payload": bytes.fromhex("F57352"),
        "before_render": "티탄즈가中",
        "after_render": "배치中",
        "source_meaning": "配置中",
    },
)


class BuildError(RuntimeError):
    """Raised when the current TIP no longer matches the bounded repair proof."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size": len(data),
        "sha256": sha256(data),
    }


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def diff_runs(before: bytes, after: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(before):
        if before[cursor] == after[cursor]:
            cursor += 1
            continue
        start = cursor
        while cursor < len(before) and before[cursor] != after[cursor]:
            cursor += 1
        rows.append(
            {
                "start": f"{start:06X}",
                "end_exclusive": f"{cursor:06X}",
                "length": cursor - start,
                "before_hex": before[start:cursor].hex().upper(),
                "after_hex": after[start:cursor].hex().upper(),
            }
        )
    return rows


def render_record(
    rom: bytes,
    dictionary: Any,
    tbl: Tbl,
    logical: int,
) -> tuple[bytes, str]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=64)
    if got is None:
        raise BuildError(f"unreadable bank-75 UI record: {logical:06X}")
    payload = bytes(got[0])
    rendered = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
    return payload, rendered


def ref_token_sites(
    rom: bytes,
    slot: int,
) -> tuple[int, ...]:
    refs = external_occurrence_map(rom, ext3_aware=True, wanted={slot})
    return tuple(sorted(int(str(row["token_abs"]), 16) for row in refs.get(slot, [])))


def main() -> int:
    parent = MAIN.read_bytes()
    if len(parent) != ROM_SIZE:
        raise BuildError(f"main TIP is not 16 MiB: {len(parent)}")
    parent_sha = sha256(parent)
    if parent_sha != EXPECTED_MAIN_SHA256:
        raise BuildError(
            f"main TIP identity drifted: {parent_sha} != {EXPECTED_MAIN_SHA256}"
        )
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != 32_768:
        raise BuildError("live main 32 KiB SaveRAM is missing")
    parent_save = MAIN_SAVE.read_bytes()

    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    tbl = Tbl.load(TBL_PATH)
    parent_dict = make_dictionary_ext3(parent, ext_meta, ext3_meta)

    reclaimed_payload = bytes(parent_dict.raw_entry(RECLAIMED_SLOT))
    keeper_payload = bytes(parent_dict.raw_entry(PLACEMENT_KEEPER_SLOT))
    reclaimed_render = parent_dict.expand(reclaimed_payload, tbl).rstrip("\u3000 \t")
    keeper_render = parent_dict.expand(keeper_payload, tbl).rstrip("\u3000 \t")
    if reclaimed_render != "티탄즈가":
        raise BuildError(f"slot 0021 no longer renders 티탄즈가: {reclaimed_render!r}")
    if keeper_render != "배치":
        raise BuildError(f"slot 0573 no longer renders 배치: {keeper_render!r}")

    ui_token_abs = tuple(int(row["token_abs"]) for row in UI_RECORDS)
    expected_before_sites = tuple(sorted(EXPECTED_DIALOGUE_TOKEN_ABS + ui_token_abs))
    before_sites = ref_token_sites(parent, RECLAIMED_SLOT)
    if before_sites != expected_before_sites:
        raise BuildError(
            "slot 0021 external consumer set drifted: "
            f"{[f'{value:06X}' for value in before_sites]}"
        )

    before_rows: list[dict[str, Any]] = []
    for row in UI_RECORDS:
        logical = int(row["record_abs"])
        payload, rendered = render_record(parent, parent_dict, tbl, logical)
        if payload != row["before_payload"]:
            raise BuildError(
                f"UI payload drifted at {logical:06X}: {payload.hex().upper()}"
            )
        if rendered != row["before_render"]:
            raise BuildError(
                f"defective UI render drifted at {logical:06X}: {rendered!r}"
            )
        before_rows.append(
            {
                "record_abs": f"{logical:06X}",
                "token_abs": f"{int(row['token_abs']):06X}",
                "source_meaning": str(row["source_meaning"]),
                "before_payload_hex": payload.hex().upper(),
                "before_render": rendered,
            }
        )

    candidate = bytearray(parent)
    sb = stock_base(candidate)
    old_token = bytes(token_from_dict_index(RECLAIMED_SLOT))
    new_token = bytes(token_from_dict_index(PLACEMENT_KEEPER_SLOT))
    target_file_positions: list[int] = []
    for row in UI_RECORDS:
        logical = int(row["token_abs"])
        physical = sb + logical
        if bytes(candidate[physical : physical + 2]) != old_token:
            raise BuildError(f"hidden placement token drifted: {logical:06X}")
        candidate[physical : physical + 2] = new_token
        target_file_positions.extend((physical, physical + 1))

    checksum = update_ws_checksum(candidate)
    final = bytes(candidate)
    final_dict = make_dictionary_ext3(final, ext_meta, ext3_meta)

    # The repair must be a consumer retarget only.  Dictionary bank 5F and the
    # two involved pointer/payload pairs stay byte-identical.
    dict_start = sb + SEG_DICT * BANK_SIZE
    dict_end = dict_start + BANK_SIZE
    if parent[dict_start:dict_end] != final[dict_start:dict_end]:
        raise BuildError("bank 5F changed during the placement-token repair")
    for slot in (RECLAIMED_SLOT, PLACEMENT_KEEPER_SLOT):
        if parent_dict.ptrs[slot] != final_dict.ptrs[slot]:
            raise BuildError(f"dictionary pointer changed for slot {slot:04X}")
        if bytes(parent_dict.raw_entry(slot)) != bytes(final_dict.raw_entry(slot)):
            raise BuildError(f"dictionary payload changed for slot {slot:04X}")

    after_sites = ref_token_sites(final, RECLAIMED_SLOT)
    if after_sites != tuple(sorted(EXPECTED_DIALOGUE_TOKEN_ABS)):
        raise BuildError(
            "slot 0021 did not retain exactly the four approved dialogue consumers: "
            f"{[f'{value:06X}' for value in after_sites]}"
        )

    keeper_before_sites = ref_token_sites(parent, PLACEMENT_KEEPER_SLOT)
    keeper_after_sites = ref_token_sites(final, PLACEMENT_KEEPER_SLOT)
    expected_keeper_after = tuple(sorted(keeper_before_sites + ui_token_abs))
    if keeper_after_sites != expected_keeper_after:
        raise BuildError("slot 0573 consumer set did not gain exactly the three UI tokens")

    after_rows: list[dict[str, Any]] = []
    for row in UI_RECORDS:
        logical = int(row["record_abs"])
        payload, rendered = render_record(final, final_dict, tbl, logical)
        if payload != row["after_payload"]:
            raise BuildError(
                f"repaired UI payload mismatch at {logical:06X}: {payload.hex().upper()}"
            )
        if rendered != row["after_render"]:
            raise BuildError(
                f"repaired UI render mismatch at {logical:06X}: {rendered!r}"
            )
        after_rows.append(
            {
                "record_abs": f"{logical:06X}",
                "token_abs": f"{int(row['token_abs']):06X}",
                "source_meaning": str(row["source_meaning"]),
                "after_payload_hex": payload.hex().upper(),
                "after_render": rendered,
            }
        )

    changed_positions = {
        index for index, (before, after) in enumerate(zip(parent, final)) if before != after
    }
    target_positions = set(target_file_positions)
    checksum_positions = {len(final) - 2, len(final) - 1}
    if not target_positions.issubset(changed_positions):
        raise BuildError("one or more bounded token bytes were not changed")
    if not changed_positions.issubset(target_positions | checksum_positions):
        unexpected = sorted(changed_positions - target_positions - checksum_positions)
        raise BuildError(
            "unexpected candidate changes: "
            + ", ".join(f"{value:06X}" for value in unexpected[:20])
        )

    atomic_write(OUT_ROM, final)
    atomic_write(OUT_SAVE, parent_save)

    report = {
        "generated_by": "tools/build_ui_placement_token_repair_candidate.py",
        "status": "accepted_static_visual_pending",
        "accepted": True,
        "published": False,
        "cause": {
            "summary": (
                "P2 too_short duplicate detachment reclaimed slot 0021 for 티탄즈가 "
                "before the sequential bank-75 UI table was included in the consumer scan"
            ),
            "reclaimed_slot": f"{RECLAIMED_SLOT:04X}",
            "reclaimed_render": reclaimed_render,
            "placement_keeper_slot": f"{PLACEMENT_KEEPER_SLOT:04X}",
            "placement_keeper_render": keeper_render,
            "missed_ui_consumers": len(UI_RECORDS),
        },
        "parent_rom": identity(MAIN, parent),
        "candidate_rom": identity(OUT_ROM, final),
        "candidate_save": identity(OUT_SAVE, parent_save),
        "save_policy": "copied_current_main_saveram_without_hash_gate",
        "checksum": f"{checksum:04X}",
        "records_before": before_rows,
        "records_after": after_rows,
        "consumer_proof": {
            "slot_0021_before": [f"{value:06X}" for value in before_sites],
            "slot_0021_after": [f"{value:06X}" for value in after_sites],
            "slot_0573_before_count": len(keeper_before_sites),
            "slot_0573_after_count": len(keeper_after_sites),
            "approved_titans_dialogue_consumers_preserved": len(after_sites),
            "hidden_ui_consumers_retargeted": len(UI_RECORDS),
        },
        "invariants": {
            "bank5f_byte_identical": True,
            "dictionary_pointers_byte_identical": True,
            "dictionary_payloads_byte_identical": True,
            "record_lengths_unchanged": True,
            "terminators_unchanged": True,
            "runtime_code_unchanged": True,
            "ext3_data_unchanged": True,
            "main_tip_unchanged": True,
            "main_saveram_unchanged": True,
        },
        "changed_byte_count": len(changed_positions),
        "diff_runs": diff_runs(parent, final),
        "visual_check": {
            "screen": "intermission speed/unit-status UI",
            "expected": ["배치", "現在の배치", "배치中"],
            "blocking_for_promotion": True,
        },
    }
    atomic_write(
        OUT_REPORT,
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )

    print(
        json.dumps(
            {
                "candidate": str(OUT_ROM),
                "sha256": report["candidate_rom"]["sha256"],
                "checksum": report["checksum"],
                "records_repaired": len(UI_RECORDS),
                "slot_0021_remaining_consumers": len(after_sites),
                "published": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
