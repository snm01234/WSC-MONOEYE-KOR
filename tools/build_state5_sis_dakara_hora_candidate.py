#!/usr/bin/env python3
"""Patch the state5 mixed JP/KO Sis line that the extractor classified as speaker.

RetroArch ``monoeye_ko_expanded.state5`` shows
``だから、ほらっ、暗い顔しない！！`` over ``미소、　미소！``.

The Korean second line is already ``63B473`` / ``E518A0EA``.  The Japanese first
line is not ``63B3AE`` (that portal already expands to Korean).  It lives inside
the following zstring, which starts with speaker ``08 E1`` whose trail ``00``
made ``split_prefix_body`` treat the whole record as a speaker blob:

    63B45D  08 E1 00 17 1C 18  F8 25 07 F6 56 07 E1 C3 04 E0 AC F7 4E F0 44  00
            speaker+window     だから、ほらっ、暗い顔しない！！

This candidate keeps the 6-byte prefix and the ``63B473`` continuation, writes
one new free ext3 phrase, and does not touch the main TIP or live SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import (  # noqa: E402
    covered,
    diff_runs,
    encode_phrase,
    phrase_cursor,
    verify_non_target_invariance,
)
from mixed_residual_classification import is_japanese_character  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text  # noqa: E402
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index  # noqa: E402
from structured_token_write_guard import (  # noqa: E402
    PROTECTED_TABLES,
    classify_structured_token_site,
    validate_protected_table,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/state5_sis_dakara_hora_candidate.wsc"
OUT_SAVE = ROOT / "sram/state5_sis_dakara_hora_candidate.sav"
REPORT = ROOT / "out/patch/state5_sis_dakara_hora_candidate_report.json"

EXPECTED_PARENT = "f704abc849f9cd096b9f5948d901caa4725effa9986870f0690c9fd3a3a02382"
EXPECTED_ORIGINAL = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768
PREFERRED_SEGMENTS = (0x1F, 0x20, 0x1E, 0x1D, 0x1C)
RECORD_START = 0x63B45D
PREFIX = bytes.fromhex("08E100171C18")
EXPECTED_PAYLOAD = bytes.fromhex("08E100171C18F82507F65607E1C304E0ACF74EF044")
JP = "だから、ほらっ、暗い顔しない！！"
KO = "자、　어두운　표정　짓지　마！"
MAX_CELLS = 20


class BuildError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": digest(data),
    }


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def choose_slot(inventory: Any, encoded: bytes) -> tuple[int, int, int]:
    needed = len(encoded) + 1
    for segment in PREFERRED_SEGMENTS:
        free = [
            index
            for index in inventory.ext3_free
            if bank_local_for_index(index)[0] == segment
        ]
        room = int(inventory.ext3_bank_room.get(segment - EXP3_SEG0, 0))
        if free and room >= needed:
            return segment, int(free[0]), room
    raise BuildError("no free ext3 slot with phrase room")


def main() -> int:
    parent = MAIN.read_bytes()
    live_save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or digest(parent) != EXPECTED_PARENT:
        raise BuildError(f"main TIP identity drifted: {digest(parent)}")
    if len(original) != ORIGINAL_SIZE or digest(original) != EXPECTED_ORIGINAL:
        raise BuildError("pristine ROM identity drifted")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    ko = normalize_ko_text(KO)
    if not ko or len(ko) > MAX_CELLS or any(is_japanese_character(ch) for ch in ko):
        raise BuildError(f"invalid Korean: {ko!r}")
    if not EXPECTED_PAYLOAD.startswith(PREFIX):
        raise BuildError("prefix/payload shape drift")
    capacity = len(EXPECTED_PAYLOAD) - len(PREFIX)
    if capacity < 4:
        raise BuildError("body cannot hold an ext3 portal")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    base = stock_base(parent)
    got = read_encoded_z_safe(parent, base + RECORD_START, max_len=128)
    if got is None:
        raise BuildError("unreadable target record")
    payload, terminator = bytes(got[0]), int(got[1])
    if payload != EXPECTED_PAYLOAD:
        raise BuildError(
            f"parent payload drift: {payload.hex().upper()} != {EXPECTED_PAYLOAD.hex().upper()}"
        )
    if terminator != base + RECORD_START + len(payload) or parent[terminator] != 0:
        raise BuildError("terminator drift")
    before = parent_dictionary.expand(payload[len(PREFIX) :], tbl).rstrip("\u3000 \t")
    if before != JP:
        raise BuildError(f"source Japanese drift: {before!r}")
    structure = classify_structured_token_site(
        parent, RECORD_START + len(PREFIX), length=capacity
    )
    if structure is not None:
        raise BuildError(f"target overlaps structured data: {structure}")

    encoded = encode_phrase(ko, tbl)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    segment, index, room = choose_slot(inventory, encoded)
    slot_payload = {index: encoded}

    candidate = bytearray(parent)
    cursor_before = phrase_cursor(bytes(slice_expansion_bank(parent, segment)))
    write_info, guard = write_ext3_slots_guarded(
        candidate,
        slot_payload,
        union=union,
        num_banks=num_banks,
        justification="state5 hidden 08E1+171C18 first-line was extractor-invisible Japanese",
    )
    if int(write_info.get("written") or 0) != 1:
        raise BuildError("ext3 writer did not write the new phrase")

    token = token_from_ext3_index(index, num_banks=num_banks)
    if token[2] == 0xFF:
        raise BuildError("refusing FF-page ext3 token")
    replacement = token + (b"\x01" * (capacity - len(token)))
    body_start = base + RECORD_START + len(PREFIX)
    candidate[body_start : body_start + capacity] = replacement
    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    after_got = read_encoded_z_safe(candidate_bytes, base + RECORD_START, max_len=128)
    if after_got is None:
        raise BuildError("candidate target unreadable")
    after_payload, after_term = bytes(after_got[0]), int(after_got[1])
    rendered = candidate_dictionary.expand(after_payload[len(PREFIX) :], tbl).rstrip(
        "\u3000 \t"
    )
    smile = read_encoded_z_safe(candidate_bytes, base + 0x63B473, max_len=64)
    if smile is None:
        raise BuildError("미소 continuation unreadable")
    smile_text = candidate_dictionary.expand(bytes(smile[0]), tbl).rstrip("\u3000 \t")
    if rendered != ko or any(is_japanese_character(ch) for ch in rendered):
        raise BuildError(f"target still Japanese or mismatch: {rendered!r}")
    if smile_text != "미소、　미소！":
        raise BuildError(f"미소 continuation drifted: {smile_text!r}")
    if (
        after_payload[: len(PREFIX)] != PREFIX
        or len(after_payload) != len(EXPECTED_PAYLOAD)
        or after_term != terminator
        or candidate_bytes[after_term] != 0
    ):
        raise BuildError("prefix/capacity/terminator not preserved")

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={RECORD_START},
    )
    protected = [validate_protected_table(candidate_bytes, table) for table in PROTECTED_TABLES]
    cursor_after = phrase_cursor(bytes(slice_expansion_bank(candidate_bytes, segment)))
    bank_file = segment * BANK_SIZE
    _seg, local = bank_local_for_index(index)
    allowed = [
        (body_start, body_start + capacity),
        (bank_file + local * 2, bank_file + local * 2 + 2),
        (bank_file + cursor_before, bank_file + cursor_after),
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"file_start": f"{left:08X}", "file_end_exclusive": f"{right:08X}"}
        for left, right in runs
        if not covered((left, right), allowed)
    ]
    other_banks_unchanged = all(
        bytes(slice_expansion_bank(parent, seg))
        == bytes(slice_expansion_bank(candidate_bytes, seg))
        for seg in range(EXP3_SEG0, EXP3_SEG0 + num_banks)
        if seg != segment
    )
    runtime_unchanged = (
        parent[base + 0x7A0600 : base + 0x7A1000]
        == candidate_bytes[base + 0x7A0600 : base + 0x7A1000]
    )
    checks = {
        "target_renders_korean": rendered == ko,
        "smile_continuation_unchanged": smile_text == "미소、　미소！",
        "prefix_capacity_terminator_preserved": True,
        "non_target_invariance": invariance.get("ok") is True,
        "diffs_bounded": not unaccounted,
        "protected_structured_tables_exact": all(row.get("ok") is True for row in protected),
        "other_ext3_banks_unchanged": other_banks_unchanged,
        "runtime_hooks_unchanged": runtime_unchanged,
        "main_tip_unchanged": MAIN.read_bytes() == parent,
        "live_saveram_unchanged": MAIN_SAVE.read_bytes() == live_save,
    }
    if not all(checks.values()):
        raise BuildError(
            json.dumps(
                {
                    "checks": checks,
                    "unaccounted": unaccounted,
                    "invariance": invariance,
                    "rendered": rendered,
                    "smile": smile_text,
                },
                ensure_ascii=False,
            )
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_state5_sis_dakara_hora_candidate.py",
        "ok": True,
        "published": False,
        "status": "candidate_static_verified_pending_user_runtime_test",
        "parent": identity(MAIN, parent),
        "original": identity(ORIGINAL, original),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "live_save_copied": identity(MAIN_SAVE, live_save),
        "checksum": f"{checksum:04X}",
        "guard": guard.as_dict(),
        "ext3_write": write_info,
        "allocation": {
            "segment": f"{segment:02X}",
            "ext3_index": f"{index:05X}",
            "token_hex": token.hex().upper(),
            "phrase_room_before": room,
        },
        "record": {
            "abs": f"{RECORD_START:06X}",
            "prefix_hex": PREFIX.hex().upper(),
            "body_capacity": capacity,
            "jp": JP,
            "before": before,
            "ko": ko,
            "old_body_hex": EXPECTED_PAYLOAD[len(PREFIX) :].hex().upper(),
            "new_body_hex": replacement.hex().upper(),
            "continuation_abs": "63B473",
            "continuation_text": smile_text,
            "note": "08E1 trail 00 hid 17 1C 18 Japanese from split_prefix_body; 63B3AE is a different earlier line",
        },
        "checks": checks,
        "diff": {
            "changed_bytes": sum(right - left for left, right in runs),
            "runs": len(runs),
        },
        "promotion": "blocked_pending_visual_verification",
    }
    atomic_json(REPORT, report)
    print(json.dumps({"ok": True, "rom": str(OUT_ROM), "sha256": digest(candidate_bytes)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
