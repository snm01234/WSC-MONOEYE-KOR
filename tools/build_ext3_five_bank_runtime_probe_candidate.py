#!/usr/bin/env python3
"""Build the phase-1 five-bank E5 18 runtime-only probe candidate.

The promoted bank59 TIP already uses the validated page-0 alias range in bank
0x21.  This candidate replaces only the 126-byte one-bank leaf with the proven
123-byte generalized leaf, keeps all 27 bank21 strings byte-exact, copies four
already validated strings into banks 0x22..0x25, and redirects the next four
A Baoa Qu dialogue records to pages 1..4.

No new token, parser, WRAM state, translation, stock slot, or SaveRAM layout is
introduced.  Candidate only: never overwrites the main TIP or main SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
import build_ext3_bank21_probe_candidate as one
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import BANK_SIZE, Tbl, le16, load_rom, stock_base, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BANK59_REPORT = ROOT / "out/patch/bank59_opening_batch01_report.json"
OUT_ROM = ROOT / "out/patch/ext3_five_bank_runtime_probe_candidate.wsc"
OUT_SAVE = ROOT / "sram/ext3_five_bank_runtime_probe_candidate.sav"
OUT_REPORT = ROOT / "out/patch/ext3_five_bank_runtime_probe_report.json"

EXPECTED_MAIN_SHA256 = "0e060c6ab73d62acdf307afd9ddcc8cbf5853365b9f22196c52497937c23ea89"
ROM_SIZE = 16_777_216
POINTER_COUNT = 0x1000
EMPTY_AT = POINTER_COUNT * 2
PAGE_LOCAL_BASE = 0x0600
FIRST_EXPANSION_BANK = 0x21

# The first line remains page0/bank21.  The following four visible lines are
# redirected in order to page1..4 while retaining their exact Korean payloads.
PROBES = (
    {"abs": "59001B", "local": 0x0002, "page": 1, "bank": 0x22},
    {"abs": "590030", "local": 0x0003, "page": 2, "bank": 0x23},
    {"abs": "590038", "local": 0x0004, "page": 3, "bank": 0x24},
    {"abs": "590049", "local": 0x0005, "page": 4, "bank": 0x25},
)


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"JSON root must be object: {path}")
    return value


def raw_phrase(bank: bytes, local: int) -> tuple[int, bytes]:
    if not 0 <= local < POINTER_COUNT:
        raise BuildError(f"local outside pointer table: {local:04X}")
    pointer = le16(bank, local * 2)
    if not 0 <= pointer < BANK_SIZE:
        raise BuildError(f"pointer outside bank: local={local:04X} ptr={pointer:04X}")
    end = pointer
    while end < BANK_SIZE and bank[end] != 0:
        end += 1
    if end >= BANK_SIZE:
        raise BuildError(f"unterminated phrase: local={local:04X}")
    return pointer, bank[pointer:end]


def alias_token(page: int, local: int) -> bytes:
    if not 0 <= page < five.PAGE_COUNT:
        raise BuildError(f"alias page outside range: {page}")
    if not 1 <= local < (five.LOCAL_END_EXCLUSIVE - five.LOCAL_START):
        raise BuildError(f"alias local outside range: {local:04X}")
    raw = (page << 12) | PAGE_LOCAL_BASE | local
    if (raw & 0xFF) == 0:
        raise BuildError(f"unsafe alias token with zero low byte: {raw:04X}")
    return bytes((0xE5, 0x18, (raw >> 8) & 0xFF, raw & 0xFF))


def format_probe_bank(raw: bytes, local: int) -> tuple[bytes, dict[str, Any]]:
    if not raw or b"\x00" in raw:
        raise BuildError("probe phrase is empty or contains an embedded NUL")
    bank = bytearray([0xFF] * BANK_SIZE)
    for index in range(POINTER_COUNT):
        struct.pack_into("<H", bank, index * 2, EMPTY_AT)
    bank[EMPTY_AT] = 0
    phrase_at = EMPTY_AT + 1
    phrase_end = phrase_at + len(raw)
    if phrase_end + 1 > BANK_SIZE:
        raise BuildError("probe phrase does not fit expansion bank")
    struct.pack_into("<H", bank, local * 2, phrase_at)
    bank[phrase_at:phrase_end] = raw
    bank[phrase_end] = 0
    return bytes(bank), {
        "pointer_count": POINTER_COUNT,
        "empty_at": f"{EMPTY_AT:04X}",
        "local": f"{local:04X}",
        "pointer": f"{phrase_at:04X}",
        "phrase_end_exclusive": f"{phrase_end:04X}",
        "phrase_bytes": len(raw),
        "phrase_sha256": sha256(raw),
        "phrase_room_after": BANK_SIZE - (phrase_end + 1),
    }


def in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


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
                "start": f"{start:07X}",
                "end_exclusive": f"{cursor:07X}",
                "length": cursor - start,
                "before_hex": before[start:min(cursor, start + 32)].hex().upper(),
                "after_hex": after[start:min(cursor, start + 32)].hex().upper(),
            }
        )
    return rows


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.is_file():
        raise BuildError("main SaveRAM is missing")
    main_save = MAIN_SAVE.read_bytes()
    main_save_sha = sha256(main_save)

    bank59 = load_object(BANK59_REPORT)
    if str((bank59.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_MAIN_SHA256:
        raise BuildError("bank59 report does not describe the promoted main TIP")
    applied = {
        str(row["abs"]).upper(): row
        for row in (bank59.get("applied") or [])
        if isinstance(row, dict) and "abs" in row
    }

    sb = stock_base(parent)
    current_leaf = one.build_bank21_leaf()
    generalized_leaf = five.build_five_bank_leaf()
    if len(current_leaf) != 126 or len(generalized_leaf) != 123:
        raise BuildError("runtime leaf size proof drifted")
    current_leaf_start = sb + one.FREE_CAVE_START
    if parent[current_leaf_start:current_leaf_start + len(current_leaf)] != current_leaf:
        raise BuildError("validated one-bank leaf drifted")
    if parent[sb + one.LEAF:sb + one.LEAF + 6] != (
        one.far_jmp(one.FREE_CAVE_START & 0xFFFF, one.EXT_CAVE_SEG) + b"\x90"
    ):
        raise BuildError("validated leaf hook drifted")
    if sha256(parent[sb + one.WALKER1_START:sb + one.WALKER2_START]) != one.EXPECTED_WALKER1_SHA256:
        raise BuildError("accepted walker1 drifted")
    if sha256(parent[sb + one.WALKER2_START:sb + one.FREE_CAVE_START]) != one.EXPECTED_WALKER2_SHA256:
        raise BuildError("accepted walker2 drifted")
    old_leaf = parent[sb + one.OLD_LEAF_START:sb + one.OLD_LEAF_END]
    if sha256(old_leaf) != one.EXPECTED_OLD_LEAF_SHA256:
        raise BuildError("accepted old leaf body drifted")

    bank21_start = FIRST_EXPANSION_BANK * BANK_SIZE
    bank21 = parent[bank21_start:bank21_start + BANK_SIZE]
    if len(bank21) != BANK_SIZE or bank21[EMPTY_AT] != 0:
        raise BuildError("promoted bank21 layout drifted")
    for segment in range(0x22, 0x26):
        start = segment * BANK_SIZE
        if not all(byte == 0xFF for byte in parent[start:start + BANK_SIZE]):
            raise BuildError(f"expansion bank {segment:02X} is not empty")

    ext_meta = load_ext_meta(one.EXT_META_PATH)
    ext3_meta = load_ext_meta(one.EXT3_META_PATH)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    tbl = Tbl.load(one.TBL_PATH)

    probe_rows: list[dict[str, Any]] = []
    target_ranges: list[tuple[int, int]] = []
    candidate_banks: dict[int, bytes] = {}
    for spec in PROBES:
        address = str(spec["abs"]).upper()
        row = applied.get(address)
        if row is None:
            raise BuildError(f"probe address missing from bank59 report: {address}")
        local = int(spec["local"])
        page = int(spec["page"])
        segment = int(spec["bank"])
        if segment != FIRST_EXPANSION_BANK + page:
            raise BuildError(f"page/bank mismatch for {address}")
        if int(str(row["local"]), 16) != local:
            raise BuildError(f"bank59 local mismatch for {address}")

        logical = int(address, 16)
        file_start = sb + logical
        before_payload = bytes.fromhex(str(row["after_payload"]))
        prefix = bytes.fromhex(str(row["prefix_hex"]))
        if parent[file_start:file_start + len(before_payload)] != before_payload:
            raise BuildError(f"promoted payload drifted: {address}")
        body_start = file_start + len(prefix)
        source_token = alias_token(0, local)
        target_token = alias_token(page, local)
        if parent[body_start:body_start + 4] != source_token:
            raise BuildError(f"page0 source token drifted: {address}")

        pointer, phrase = raw_phrase(bank21, local)
        rendered = dictionary.expand(phrase, tbl)
        if rendered != str(row["ko"]):
            raise BuildError(f"bank21 phrase rendering differs from approved text: {address}")
        target_bank, bank_meta = format_probe_bank(phrase, local)
        candidate_banks[segment] = target_bank
        target_ranges.append((body_start, body_start + 4))
        probe_rows.append(
            {
                "abs": address,
                "logical": logical,
                "file_start": file_start,
                "body_start": body_start,
                "source_page": 0,
                "source_bank": "21",
                "source_local": f"{local:04X}",
                "source_pointer": f"{pointer:04X}",
                "source_token": source_token.hex().upper(),
                "target_page": page,
                "target_bank": f"{segment:02X}",
                "target_token": target_token.hex().upper(),
                "ko": str(row["ko"]),
                "raw_phrase": phrase,
                "bank_meta": bank_meta,
            }
        )

    if set(candidate_banks) != {0x22, 0x23, 0x24, 0x25}:
        raise BuildError("not all four expansion probe banks were prepared")

    candidate = bytearray(parent)
    candidate[current_leaf_start:current_leaf_start + len(generalized_leaf)] = generalized_leaf
    candidate[
        current_leaf_start + len(generalized_leaf):current_leaf_start + len(current_leaf)
    ] = b"\xFF" * (len(current_leaf) - len(generalized_leaf))
    for segment, bank in candidate_banks.items():
        start = segment * BANK_SIZE
        candidate[start:start + BANK_SIZE] = bank
    for row in probe_rows:
        body_start = int(row["body_start"])
        candidate[body_start:body_start + 4] = bytes.fromhex(str(row["target_token"]))

    checksum = update_ws_checksum(candidate)

    allowed = [
        (current_leaf_start, current_leaf_start + len(current_leaf)),
        *((segment * BANK_SIZE, (segment + 1) * BANK_SIZE) for segment in range(0x22, 0x26)),
        *target_ranges,
        (len(parent) - 2, len(parent)),
    ]
    changed = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    unaccounted = [offset for offset in changed if not in_ranges(offset, allowed)]
    if unaccounted:
        raise BuildError(f"unaccounted changed bytes: {unaccounted[:32]}")

    if candidate[bank21_start:bank21_start + BANK_SIZE] != bank21:
        raise BuildError("promoted bank21 changed")
    if candidate[sb + one.WALKER1_START:sb + one.FREE_CAVE_START] != parent[
        sb + one.WALKER1_START:sb + one.FREE_CAVE_START
    ]:
        raise BuildError("accepted walkers changed")
    if candidate[sb + one.OLD_LEAF_START:sb + one.OLD_LEAF_END] != parent[
        sb + one.OLD_LEAF_START:sb + one.OLD_LEAF_END
    ]:
        raise BuildError("accepted old leaf body changed")
    if candidate[sb + one.LEAF:sb + one.LEAF + 6] != parent[sb + one.LEAF:sb + one.LEAF + 6]:
        raise BuildError("leaf hook changed")
    if candidate[sb + one.SITE1:sb + one.SITE1 + 5] != parent[sb + one.SITE1:sb + one.SITE1 + 5]:
        raise BuildError("site1 hook changed")
    if candidate[sb + one.SITE2_FIXED:sb + one.SITE2_FIXED + 5] != parent[
        sb + one.SITE2_FIXED:sb + one.SITE2_FIXED + 5
    ]:
        raise BuildError("site2 hook changed")
    for segment in range(0x11, 0x21):
        start = segment * BANK_SIZE
        if candidate[start:start + BANK_SIZE] != parent[start:start + BANK_SIZE]:
            raise BuildError(f"accepted ext3 bank {segment:02X} changed")
    stock_start = sb + 0x5F0000
    if candidate[stock_start:stock_start + BANK_SIZE] != parent[stock_start:stock_start + BANK_SIZE]:
        raise BuildError("stock dictionary bank changed")

    page_hits = {page: five.scan_range_hits(bytes(candidate), page) for page in range(five.PAGE_COUNT)}
    expected_target_hits = {int(row["target_page"]): int(row["body_start"]) for row in probe_rows}
    if len(page_hits[0]) != 23:
        raise BuildError(f"unexpected page0 reference count: {len(page_hits[0])}")
    for page in range(1, five.PAGE_COUNT):
        if page_hits[page] != [expected_target_hits[page]]:
            raise BuildError(f"page{page} probe reference mismatch: {page_hits[page]}")

    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(candidate)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)

    report_probes: list[dict[str, Any]] = []
    for row in probe_rows:
        report_probes.append(
            {
                key: value
                for key, value in row.items()
                if key not in {"raw_phrase", "file_start", "body_start", "logical"}
            }
            | {
                "raw_phrase_hex": bytes(row["raw_phrase"]).hex().upper(),
                "raw_phrase_sha256": sha256(bytes(row["raw_phrase"])),
                "record_file_start": f"{int(row['file_start']):07X}",
                "body_file_start": f"{int(row['body_start']):07X}",
            }
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ext3_five_bank_runtime_probe_candidate.py",
        "status": "candidate_static_verified",
        "ok": True,
        "phase": 1,
        "parent": {
            "path": str(MAIN.relative_to(ROOT)),
            "size": len(parent),
            "sha256": sha256(parent),
        },
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "size": len(candidate),
            "sha256": sha256(candidate),
            "checksum": f"{checksum:04X}",
        },
        "save": {
            "main_path": str(MAIN_SAVE.relative_to(ROOT)),
            "candidate_path": str(OUT_SAVE.relative_to(ROOT)),
            "main_sha256_at_build": main_save_sha,
            "candidate_sha256_at_build": sha256(OUT_SAVE.read_bytes()),
            "copied_byte_exact_at_build": OUT_SAVE.read_bytes() == main_save,
            "policy": "candidate SaveRAM is mutable test-only state and must never be promoted back to main",
        },
        "runtime": {
            "existing_token": "E5 18 xx yy",
            "new_token_added": False,
            "new_parser_added": False,
            "new_wram_state_added": False,
            "one_bank_leaf_length": len(current_leaf),
            "one_bank_leaf_sha256": sha256(current_leaf),
            "five_bank_leaf_length": len(generalized_leaf),
            "five_bank_leaf_sha256": sha256(generalized_leaf),
            "leaf_address": f"{one.FREE_CAVE_START:06X}",
            "leaf_tail_cleared_to_ff": len(current_leaf) - len(generalized_leaf),
            "leaf_hook_byte_exact": True,
            "walkers_byte_exact": True,
            "old_leaf_body_byte_exact": True,
            "page_rule": "page 0..4 and local 0600..0FFF -> bank 21+page and local-0600",
            "page_reference_counts": {str(page): len(hits) for page, hits in page_hits.items()},
        },
        "probe_sequence": [
            {
                "order": 1,
                "abs": "590005",
                "page": 0,
                "bank": "21",
                "ko": "……생각보다　연방군이　적구나。",
                "role": "existing promoted page0 control",
            },
            *[
                {
                    "order": index + 2,
                    "abs": row["abs"],
                    "page": row["target_page"],
                    "bank": row["target_bank"],
                    "ko": row["ko"],
                    "role": "copied runtime page probe",
                }
                for index, row in enumerate(report_probes)
            ],
        ],
        "probes": report_probes,
        "invariance": {
            "bank21_all_27_strings_byte_exact": True,
            "banks11_20_byte_exact": True,
            "stock_dictionary_bank_byte_exact": True,
            "target_payload_lengths_unchanged": True,
            "target_prefixes_and_padding_unchanged": True,
            "target_terminators_unchanged": True,
            "unaccounted_changed_bytes": len(unaccounted),
            "main_rom_untouched": sha256(MAIN.read_bytes()) == EXPECTED_MAIN_SHA256,
            "main_save_untouched": sha256(MAIN_SAVE.read_bytes()) == main_save_sha,
        },
        "diff_runs": diff_runs(parent, bytes(candidate)),
        "test_requirements": [
            "At the A Baoa Qu opening, confirm the first five Korean lines appear in order without glyph corruption.",
            "The five lines exercise page0/bank21 followed by page1/bank22, page2/bank23, page3/bank24, and page4/bank25.",
            "Advance through the remaining opening dialogue and enter battle without Event Error, freeze, skipped text, or data corruption.",
            "Save with the candidate SaveRAM, fully close the emulator, restart, reload, and confirm normal progress.",
            "Never copy the candidate .sav back over monoeye_ko_expanded.sav.",
        ],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "runtime": report["runtime"],
        "probe_sequence": report["probe_sequence"],
        "unaccounted_changed_bytes": len(unaccounted),
        "report": str(OUT_REPORT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
