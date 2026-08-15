#!/usr/bin/env python3
"""Rehome unsafe E5 18 00 yy text tokens to five-bank alias slots.

The four user screenshots at 61:0005/0025/004A/005F expose a structural
property that the original ext3 allocator did not guard: although the patched
text decoder treats ``E5 18 xx yy`` atomically, another event/string boundary
consumer can still observe the interior bytes.  When ``xx == 00`` the token
contains an embedded NUL; the following ``yy`` then survives as a one-byte
stock glyph (3A=や, 3B=ち, 3C=ロ, 3D=ク in the reported scene).

This production-shaped *candidate* does not change translations or runtime
code.  It copies each live unsafe phrase byte-for-byte into the already
promoted five-bank alias range (whose raw high byte is always non-zero) and
retargets only proven external text consumers.  The known bank-62 event/
graphics block 62:D650-62:FFFF stays quarantined and byte-exact.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import detect_ext3_alias_page_count, load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import _walk_zstring_range  # noqa: E402
from mixed_residual_reference_union import _reference_scopes, iter_token_refs_with_offsets  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    EXT3_INDEX_BASE,
    Tbl,
    le16,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)
from patch_3byte_dict_token import token_from_ext3_index  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/ext3_zero_middle_rehome_candidate.wsc"
OUT_SAVE = ROOT / "sram/ext3_zero_middle_rehome_candidate.sav"
REPORT = ROOT / "out/patch/ext3_zero_middle_rehome_report.json"

EXPECTED_PARENT_SHA256 = "30313f387660c4d09ce139a7fc4d0ce14962321d2df49ea1914021c9d2109f24"
EXPECTED_ALIAS_PAGES = 5
EXPECTED_EXTERNAL_UNSAFE = 587
EXPECTED_QUARANTINE = 14
EXPECTED_REPAIR_REFS = 573
EXPECTED_REPAIR_INDICES = 253
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

ALIAS_SEG0 = 0x21
ALIAS_RAW_LOCAL_START = 0x0600
ALIAS_PHYSICAL_LOCAL_END = 0x0A00
POINTER_COUNT = 0x1000
EMPTY_AT = POINTER_COUNT * 2
QUARANTINE_LO = 0x62D650
QUARANTINE_HI = 0x630000

SCREEN_ANCHORS = (
    (0x610005, 0x610008, 0x103A, "3A", "や"),
    (0x610025, 0x610026, 0x103B, "3B", "ち"),
    (0x61004A, 0x61004D, 0x103C, "3C", "ロ"),
    (0x61005F, 0x610062, 0x103D, "3D", "ク"),
)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
        display = str(rel).replace("\\", "/")
    except ValueError:
        display = str(path.resolve())
    return {"path": display, "size": len(data), "sha256": sha(data)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def payload_at(bank: bytes | bytearray, pointer: int) -> bytes:
    if not 0 <= pointer < BANK_SIZE:
        raise BuildError(f"alias pointer outside bank: {pointer:04X}")
    end = bytes(bank).find(b"\x00", pointer)
    if end < 0:
        raise BuildError(f"unterminated alias payload: {pointer:04X}")
    return bytes(bank[pointer:end])


def token_raw_hi(index: int) -> int:
    return ((index - EXT3_INDEX_BASE) >> 8) & 0xFF


def token_raw_lo(index: int) -> int:
    return (index - EXT3_INDEX_BASE) & 0xFF


def unsafe_middle_nul(index: int) -> bool:
    return index >= EXT3_INDEX_BASE and token_raw_hi(index) == 0 and token_raw_lo(index) != 0


def scan_external(rom: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for region, lo, hi, max_len in _reference_scopes():
        for logical, payload, kind in _walk_zstring_range(
            rom, lo, hi, region=region, max_len=max_len
        ):
            for index, length, offset in iter_token_refs_with_offsets(payload, ext3_aware=True):
                if length != 4 or not unsafe_middle_nul(index):
                    continue
                token_abs = logical + offset
                rows.append(
                    {
                        "region": region,
                        "kind": kind,
                        "record_abs": logical,
                        "token_abs": token_abs,
                        "index": index,
                        "token_hex": payload[offset:offset + 4].hex().upper(),
                        "quarantine": QUARANTINE_LO <= token_abs < QUARANTINE_HI,
                    }
                )
    rows.sort(key=lambda row: (int(row["token_abs"]), int(row["index"])))
    return rows


class AliasRawAllocator:
    """Allocate byte-exact phrases only in the promoted banks 21..25 alias."""

    def __init__(self, candidate: bytearray):
        self.candidate = candidate
        self.banks: list[bytearray] = []
        self.cursors: list[int] = []
        self.free_locals: list[list[int]] = []
        self.allocations: list[dict[str, Any]] = []
        self.changed_ranges: list[tuple[int, int]] = []
        self.page_allocations = [0] * EXPECTED_ALIAS_PAGES

        for page in range(EXPECTED_ALIAS_PAGES):
            segment = ALIAS_SEG0 + page
            start = segment * BANK_SIZE
            bank = bytearray(candidate[start:start + BANK_SIZE])
            if len(bank) != BANK_SIZE or bank[EMPTY_AT] != 0:
                raise BuildError(f"alias bank layout drifted: {segment:02X}")
            cursor = EMPTY_AT + 1
            for local in range(POINTER_COUNT):
                pointer = le16(bank, local * 2)
                if not 0 <= pointer < BANK_SIZE:
                    continue
                phrase = payload_at(bank, pointer)
                if phrase:
                    cursor = max(cursor, pointer + len(phrase) + 1)
            free: list[int] = []
            for local in range(1, ALIAS_PHYSICAL_LOCAL_END):
                if (local & 0xFF) == 0:
                    continue
                pointer = le16(bank, local * 2)
                if 0 <= pointer < BANK_SIZE and not payload_at(bank, pointer):
                    free.append(local)
            self.banks.append(bank)
            self.cursors.append(cursor)
            self.free_locals.append(free)

    def allocate_raw(self, source_index: int, encoded: bytes) -> tuple[bytes, dict[str, Any]]:
        if not encoded or b"\x00" in encoded:
            raise BuildError(f"unsafe/empty source phrase: {source_index:04X}")
        required = len(encoded) + 1
        candidates = [
            page
            for page in range(EXPECTED_ALIAS_PAGES)
            if self.free_locals[page] and self.cursors[page] + required <= BANK_SIZE
        ]
        if not candidates:
            raise BuildError(f"five-bank alias capacity exhausted at {source_index:04X}")
        page = min(candidates, key=lambda p: (self.page_allocations[p], self.cursors[p], p))
        physical_local = self.free_locals[page].pop(0)
        raw_local = ALIAS_RAW_LOCAL_START + physical_local
        raw_hi = (page << 4) | ((raw_local >> 8) & 0x0F)
        raw_lo = raw_local & 0xFF
        if raw_hi == 0 or raw_lo == 0:
            raise BuildError(f"allocator selected embedded-NUL alias token: page={page} local={raw_local:04X}")

        segment = ALIAS_SEG0 + page
        bank = self.banks[page]
        pointer = self.cursors[page]
        end = pointer + len(encoded)
        struct.pack_into("<H", bank, physical_local * 2, pointer)
        bank[pointer:end] = encoded
        bank[end] = 0
        self.cursors[page] = end + 1
        self.page_allocations[page] += 1

        index = EXT3_INDEX_BASE + page * 0x1000 + raw_local
        token = token_from_ext3_index(index, num_banks=16)
        if token[2] == 0 or token[3] == 0:
            raise BuildError(f"destination token contains NUL: {token.hex().upper()}")
        bank_file = segment * BANK_SIZE
        self.changed_ranges.append((bank_file + physical_local * 2, bank_file + physical_local * 2 + 2))
        self.changed_ranges.append((bank_file + pointer, bank_file + end + 1))
        row = {
            "source_index": f"{source_index:04X}",
            "destination_index": f"{index:05X}",
            "token_hex": token.hex().upper(),
            "page": page,
            "segment": f"{segment:02X}",
            "physical_local": f"{physical_local:04X}",
            "raw_local": f"{raw_local:04X}",
            "pointer": f"{pointer:04X}",
            "phrase_bytes": len(encoded),
            "phrase_sha256": sha(encoded),
        }
        self.allocations.append(row)
        return token, row

    def commit(self) -> None:
        for page, bank in enumerate(self.banks):
            segment = ALIAS_SEG0 + page
            start = segment * BANK_SIZE
            self.candidate[start:start + BANK_SIZE] = bank

    def summary(self) -> dict[str, Any]:
        return {
            "allocations": len(self.allocations),
            "phrase_bytes": sum(int(row["phrase_bytes"]) for row in self.allocations),
            "banks": [
                {
                    "page": page,
                    "segment": f"{ALIAS_SEG0 + page:02X}",
                    "new_slots": self.page_allocations[page],
                    "cursor_after": f"{self.cursors[page]:04X}",
                    "room_after": BANK_SIZE - self.cursors[page],
                    "free_slots_after": len(self.free_locals[page]),
                }
                for page in range(EXPECTED_ALIAS_PAGES)
            ],
        }


def diff_runs(left: bytes, right: bytes) -> list[tuple[int, int]]:
    if len(left) != len(right):
        raise BuildError("ROM size drift")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for pos, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = pos
        elif a == b and start is not None:
            runs.append((start, pos))
            start = None
    if start is not None:
        runs.append((start, len(left)))
    return runs


def main() -> int:
    parent = MAIN.read_bytes()
    live_save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA256:
        raise BuildError("main TIP identity drifted")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")
    if detect_ext3_alias_page_count(parent) != EXPECTED_ALIAS_PAGES:
        raise BuildError("promoted five-page alias runtime not detected")

    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    tbl = Tbl.load(TBL)
    base = stock_base(parent)

    refs = scan_external(parent)
    if len(refs) != EXPECTED_EXTERNAL_UNSAFE or len({row["index"] for row in refs}) != 255:
        raise BuildError(
            f"unsafe population drifted: refs={len(refs)} unique={len({row['index'] for row in refs})}"
        )
    quarantine = [row for row in refs if row["quarantine"]]
    repair = [row for row in refs if not row["quarantine"]]
    repair_indices = sorted({int(row["index"]) for row in repair})
    if len(quarantine) != EXPECTED_QUARANTINE:
        raise BuildError(f"quarantine count drifted: {len(quarantine)}")
    if len(repair) != EXPECTED_REPAIR_REFS or len(repair_indices) != EXPECTED_REPAIR_INDICES:
        raise BuildError(f"repair population drifted: refs={len(repair)} indices={len(repair_indices)}")
    if sorted(set(range(0x1001, 0x1100)) - set(repair_indices)) != [0x1002, 0x1010]:
        raise BuildError("expected quarantine-only slots 1002/1010 drifted")

    refs_by_token_abs = {int(row["token_abs"]): row for row in refs}
    anchor_rows: list[dict[str, Any]] = []
    for record_abs, token_abs, expected_index, low_hex, glyph in SCREEN_ANCHORS:
        row = refs_by_token_abs.get(token_abs)
        if row is None or int(row["index"]) != expected_index or row["token_hex"] != f"E51800{low_hex}":
            raise BuildError(f"screen anchor drifted at {token_abs:06X}")
        got = read_encoded_z_safe(parent, base + record_abs, max_len=256)
        if got is None:
            raise BuildError(f"screen anchor record unreadable: {record_abs:06X}")
        anchor_rows.append(
            {
                "record_abs": f"{record_abs:06X}",
                "token_abs": f"{token_abs:06X}",
                "before_token": row["token_hex"],
                "source_index": f"{expected_index:04X}",
                "leaked_low_byte": low_hex,
                "leaked_glyph": glyph,
                "static_render_before": parent_dictionary.expand(bytes(got[0]), tbl),
            }
        )

    candidate = bytearray(parent)
    allocator = AliasRawAllocator(candidate)
    mapping: dict[int, bytes] = {}
    mapping_meta: dict[int, dict[str, Any]] = {}
    for source_index in repair_indices:
        raw = bytes(parent_dictionary.raw_entry(source_index))
        token, meta = allocator.allocate_raw(source_index, raw)
        mapping[source_index] = token
        mapping_meta[source_index] = meta
    allocator.commit()

    changed_token_sites: list[dict[str, Any]] = []
    for row in repair:
        logical = int(row["token_abs"])
        source_index = int(row["index"])
        file_abs = base + logical
        before = bytes(candidate[file_abs:file_abs + 4])
        expected = bytes.fromhex(str(row["token_hex"]))
        if before != expected:
            raise BuildError(f"target token drift at {logical:06X}: {before.hex()} != {expected.hex()}")
        after = mapping[source_index]
        if after[2] == 0 or after[3] == 0:
            raise BuildError(f"unsafe destination at {logical:06X}: {after.hex()}")
        candidate[file_abs:file_abs + 4] = after
        changed_token_sites.append(
            {
                "record_abs": f"{int(row['record_abs']):06X}",
                "token_abs": f"{logical:06X}",
                "region": row["region"],
                "kind": row["kind"],
                "source_index": f"{source_index:04X}",
                "destination_index": mapping_meta[source_index]["destination_index"],
                "before_token": before.hex().upper(),
                "after_token": after.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    # Independent-in-builder structural checks.
    after_refs = scan_external(candidate_bytes)
    after_unsafe_repair = [row for row in after_refs if not row["quarantine"]]
    after_quarantine = [row for row in after_refs if row["quarantine"]]
    phrase_failures: list[dict[str, Any]] = []
    for source_index, token in mapping.items():
        dest_index = EXT3_INDEX_BASE + ((token[2] << 8) | token[3])
        src = bytes(parent_dictionary.raw_entry(source_index))
        dst = bytes(candidate_dictionary.raw_entry(dest_index))
        if src != dst:
            phrase_failures.append(
                {
                    "source_index": f"{source_index:04X}",
                    "destination_index": f"{dest_index:05X}",
                    "source_sha256": sha(src),
                    "destination_sha256": sha(dst),
                }
            )

    anchor_failures: list[dict[str, Any]] = []
    for anchor in anchor_rows:
        record_abs = int(anchor["record_abs"], 16)
        token_abs = int(anchor["token_abs"], 16)
        got = read_encoded_z_safe(candidate_bytes, base + record_abs, max_len=256)
        if got is None:
            anchor_failures.append({"record_abs": anchor["record_abs"], "reason": "unreadable"})
            continue
        after_token = bytes(candidate_bytes[base + token_abs:base + token_abs + 4])
        render_after = candidate_dictionary.expand(bytes(got[0]), tbl)
        source_index = int(anchor["source_index"], 16)
        anchor["after_token"] = after_token.hex().upper()
        anchor["destination_index"] = mapping_meta[source_index]["destination_index"]
        anchor["static_render_after"] = render_after
        if (
            after_token[2] == 0
            or after_token[3] == 0
            or render_after != anchor["static_render_before"]
        ):
            anchor_failures.append(
                {
                    "record_abs": anchor["record_abs"],
                    "after_token": after_token.hex().upper(),
                    "before_render": anchor["static_render_before"],
                    "after_render": render_after,
                }
            )

    # The quarantined event/graphics block and all runtime code must remain exact.
    q0 = base + QUARANTINE_LO
    q1 = base + QUARANTINE_HI
    runtime_ranges = (
        (base + 0x7A0000, base + 0x7B0000),
        # The WonderSwan checksum occupies the last two bytes of stock bank 7F.
        # Exclude only those header bytes from the runtime-code equality check.
        (base + 0x7F0000, base + 0x7FFFFE),
    )
    standard_ext3_ranges = tuple((seg * BANK_SIZE, (seg + 1) * BANK_SIZE) for seg in range(0x11, 0x21))
    checks = {
        "parent_identity_exact": sha(parent) == EXPECTED_PARENT_SHA256,
        "screen_anchor_low_bytes_are_3A_3D": [row["leaked_low_byte"] for row in anchor_rows] == ["3A", "3B", "3C", "3D"],
        "repair_reference_count_exact": len(repair) == EXPECTED_REPAIR_REFS,
        "repair_source_index_count_exact": len(repair_indices) == EXPECTED_REPAIR_INDICES,
        "all_repair_refs_retargeted": len(changed_token_sites) == EXPECTED_REPAIR_REFS,
        "all_destination_tokens_have_no_embedded_nul": all(bytes.fromhex(row["after_token"])[2] != 0 and bytes.fromhex(row["after_token"])[3] != 0 for row in changed_token_sites),
        "no_unsafe_external_refs_outside_quarantine_after": len(after_unsafe_repair) == 0,
        "quarantine_reference_count_preserved": len(after_quarantine) == EXPECTED_QUARANTINE,
        "quarantine_block_byte_exact": candidate_bytes[q0:q1] == parent[q0:q1],
        "phrase_copy_byte_exact": not phrase_failures,
        "screen_anchor_static_render_exact": not anchor_failures,
        "runtime_banks_7A_7F_byte_exact": all(candidate_bytes[lo:hi] == parent[lo:hi] for lo, hi in runtime_ranges),
        "standard_ext3_banks_11_20_byte_exact": all(candidate_bytes[lo:hi] == parent[lo:hi] for lo, hi in standard_ext3_ranges),
        "alias_page_count_preserved": detect_ext3_alias_page_count(candidate_bytes) == EXPECTED_ALIAS_PAGES,
        "save_snapshot_is_current_size": len(live_save) == SAVE_SIZE,
    }
    ok = all(checks.values())
    if not ok:
        raise BuildError(f"candidate self-audit failed: {[name for name, value in checks.items() if not value]}")

    runs = diff_runs(parent, candidate_bytes)
    changed_bytes = sum(end - start for start, end in runs)
    after_bank_counts = Counter(int(row["token_abs"]) >> 16 for row in after_refs)
    before_bank_counts = Counter(int(row["token_abs"]) >> 16 for row in refs)

    atomic_bytes(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ext3_zero_middle_rehome_candidate.py",
        "ok": ok,
        "promotion_allowed": False,
        "reason": "E5 18 00 yy contains an interior NUL; reported trailing glyphs equal yy exactly",
        "parent": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": identity(OUT_SAVE),
        "checksum": f"{checksum:04X}",
        "evidence": {
            "screen_anchors": anchor_rows,
            "unsafe_token_form": "E5 18 00 yy",
            "reported_low_byte_mapping": {"3A": "や", "3B": "ち", "3C": "ロ", "3D": "ク"},
            "external_refs_before": len(refs),
            "external_unique_indices_before": len({int(row["index"]) for row in refs}),
            "by_logical_bank_before": {f"{bank:02X}": count for bank, count in sorted(before_bank_counts.items())},
            "quarantine_range": [f"{QUARANTINE_LO:06X}", f"{QUARANTINE_HI:06X}"],
            "quarantine_refs": len(quarantine),
        },
        "repair": {
            "retargeted_refs": len(changed_token_sites),
            "rehosted_unique_phrases": len(mapping),
            "source_indices": [f"{index:04X}" for index in repair_indices],
            "alias_allocator": allocator.summary(),
            "changed_token_sites": changed_token_sites,
            "allocations": allocator.allocations,
        },
        "post_scan": {
            "unsafe_external_refs_total": len(after_refs),
            "unsafe_external_refs_outside_quarantine": len(after_unsafe_repair),
            "unsafe_external_refs_quarantined": len(after_quarantine),
            "by_logical_bank": {f"{bank:02X}": count for bank, count in sorted(after_bank_counts.items())},
        },
        "diff": {
            "runs": len(runs),
            "bytes": changed_bytes,
            "sample": [[f"{start:07X}", f"{end:07X}"] for start, end in runs[:40]],
        },
        "checks": checks,
        "failures": {"phrase_copy": phrase_failures, "anchors": anchor_failures},
        "next_gate": "User emulator validation at 61:0005 onward. Do not promote until the four reported trailing glyphs and subsequent stage dialogue are clean.",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": ok,
        "candidate": str(OUT_ROM.relative_to(ROOT)),
        "sha256": sha(candidate_bytes),
        "checksum": f"{checksum:04X}",
        "retargeted_refs": len(changed_token_sites),
        "rehosted_unique_phrases": len(mapping),
        "unsafe_after_outside_quarantine": len(after_unsafe_repair),
        "quarantine_preserved": len(after_quarantine),
        "diff_runs": len(runs),
        "diff_bytes": changed_bytes,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
