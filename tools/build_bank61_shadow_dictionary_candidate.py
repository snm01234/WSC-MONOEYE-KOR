#!/usr/bin/env python3
"""Build a bank61-native 2-byte shadow-dictionary candidate.

Purpose
-------
Some bank61 scenario/continuation records use the game's native 0/1-byte
prefix grammar. Replacing their original body with a 4-byte ``E5 18 xx yy``
portal is not runtime-equivalent on every event path; live evidence at
611DF8/611E05 exposes raw portal bytes (``こ``/``み``).

This candidate keeps those records in the *native two-byte dictionary-token
shape*.  While the source ROM1 bank is stock bank61 (AL=E1), the patched leaf
checks an expansion shadow dictionary first.  A missing shadow pointer falls
back to the existing stock dictionary, so all pre-existing bank61 native tokens
retain their old meaning.

Storage
-------
The 12-bit native index is split into four 0x400-slot groups.  Group N uses
expansion bank 0x26+N.  Each bank contains:

    0000-07FF   1024 LE16 shadow pointers (FFFF = no shadow override)
    0800-FFFF   phrase pool

Only current-bank61-unused native indices are allocated.  FF-page indices are
not used for new shadow entries; allocations are restricted to 000-0EFF and
trail byte != 00.

The build also carries forward the approved weapon terminology correction
``카논 -> 캐논`` for the four active cannon weapon names.  The earlier wrong
bank59 E006 width hypothesis is deliberately *not* applied because this build
starts from the current promoted main TIP.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_terminology_retranslation_candidate import (  # noqa: E402
    consumer_abs_set,
    diff_runs,
    encode,
    ext3_storage_proof,
    in_intervals,
    inplace_phrase,
    merged,
)
from extract_script import split_prefix_body  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    _working_two_byte_external_refs,
    build_reference_union,
)
from monoeye_rom import (  # noqa: E402
    Tbl,
    bank_al_expansion,
    bank_al_stock,
    dict_index_from_ext3_token,
    dict_index_from_token,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    load_rom,
    patch_expansion_bank,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
MANIFEST = ROOT / "out/patch/main_p1_base_manifest.json"

OUT_ROM = ROOT / "out/patch/bank61_shadow_dictionary_candidate.wsc"
OUT_SAVE = ROOT / "sram/bank61_shadow_dictionary_candidate.sav"
OUT_REPORT = ROOT / "out/patch/bank61_shadow_dictionary_report.json"
OUT_TARGETS = ROOT / "out/script/bank61_shadow_dictionary_targets.json"

EXPECTED_MAIN = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

SOURCE_LOGICAL_BANK = 0x61
SOURCE_BANK_AL = bank_al_stock(SOURCE_LOGICAL_BANK)  # E1
SHADOW_SEG0 = 0x26
SHADOW_GROUPS = 4
SHADOW_SLOTS_PER_GROUP = 0x400
SHADOW_PTR_BYTES = SHADOW_SLOTS_PER_GROUP * 2  # 0x800
SHADOW_POOL_START = SHADOW_PTR_BYTES
SHADOW_SENTINEL = 0xFFFF

# Current accepted fixed-bank 7F runtime has an all-FF tail after the alias leaf.
HOOK_SITE = 0x7FFF0D
HOOK_EXPECT = bytes.fromhex("558BEC83EC08EAD40600A0")
SHADOW_CAVE = 0x7FFF18
SHADOW_CAVE_END = 0x7FFFF0
SEG_7F = 0xF000
SEG_7A = 0xA000
BANK_HELPER_SEG = 0x8000
BANK_SAVE_OFF = 0xDEB2
BANK_MAP_OFF = 0xDEB5
FAD0_OFF = 0xFAD0
LEAF_FALLBACK = 0x06E2
PHRASE_STREAM = 0x0743

# Active weapon records / current private ext3 slots.
CANNON = {
    0x75C3D3: (0x0FFAA, "메가　카논　포", "메가　캐논　포"),
    0x75C7B2: (0x0FF3E, "배부　빔　카논", "배부　빔　캐논"),
    0x75C7E5: (0x0FF38, "빔　카논", "빔　캐논"),
    0x75CBC7: (0x0FECF, "메가　카논", "메가　캐논"),
}


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def far_call(off: int, seg: int) -> bytes:
    return b"\x9A" + struct.pack("<HH", off & 0xFFFF, seg & 0xFFFF)


def far_jump(off: int, seg: int) -> bytes:
    return b"\xEA" + struct.pack("<HH", off & 0xFFFF, seg & 0xFFFF)


def _patch_rel8(buf: bytearray, at: int, target: int) -> None:
    disp = target - (at + 2)
    if not -128 <= disp <= 127:
        raise BuildError(f"rel8 out of range: {disp}")
    buf[at + 1] = disp & 0xFF


def build_shadow_leaf_handler() -> bytes:
    """Leaf prologue + bank61 shadow lookup + stock fallback.

    Entry is the non-ext3 branch of the accepted leaf at 7F:FF0D.  The frame and
    saved-register layout is byte-compatible with stock 7A:06CE, so fallback can
    jump directly to 7A:06E2 and the shadow-hit path can enter stock phrase loop
    7A:0743 with the saved source bank on the stack.
    """
    out = bytearray()
    # Stock leaf prologue / register saves through SI=CX, DI=DX.
    out += bytes.fromhex(
        "55 8B EC 83 EC 08 "  # push bp; mov bp,sp; sub sp,8
        "51 52 56 57 "        # push cx,dx,si,di
        "89 46 FC 89 5E FE "  # save caller ax,bx
        "8B F1 8B FA"         # si=cx token, di=dx
    )
    # Non-dictionary glyph path: use untouched stock leaf from 06E2 onward.
    out += b"\x81\xFE\x00\xF0"  # cmp si,F000
    jb_stock = len(out)
    out += b"\x72\x00"

    # Determine the pre-map ROM1 source bank.  Only stock bank61 (E1) shadows.
    out += far_call(BANK_SAVE_OFF, BANK_HELPER_SEG)
    out += b"\x3C" + bytes([SOURCE_BANK_AL])  # cmp al,E1
    jne_stock = len(out)
    out += b"\x75\x00"
    out += b"\x50"  # saved source bank; stock 074C will pop/restore on hit

    # Native token F0xx..FFxx -> 12-bit index.  Group=index>>10, local=index&3FF.
    out += b"\x89\xF3"                    # mov bx,si
    out += b"\x81\xEB\x00\xF0"          # sub bx,F000
    out += b"\x89\xD8"                    # mov ax,bx
    out += b"\xB1\x0A\xD3\xE8"          # mov cl,10; shr ax,cl
    out += b"\x81\xE3\xFF\x03"          # and bx,03FF
    out += b"\xD1\xE3"                    # shl bx,1 (pointer table offset)
    out += b"\x04" + bytes([bank_al_expansion(SHADOW_SEG0)])  # add al,26
    out += b"\x53"                         # preserve table offset across DEB5
    out += far_call(BANK_MAP_OFF, BANK_HELPER_SEG)
    out += b"\xBB\x00\x30\x8E\xC3"      # es=3000
    out += b"\x5B"                         # pop bx
    out += b"\x26\x8B\x07"                # mov ax,es:[bx]
    out += b"\x3D\xFF\xFF"                # cmp ax,FFFF
    je_miss = len(out)
    out += b"\x74\x00"

    # Shadow hit: convert ES:AX to far ptr and enter stock phrase stream.
    out += far_call(FAD0_OFF, BANK_HELPER_SEG)
    out += b"\x89\x46\xF8\x89\x5E\xFA"
    out += far_jump(PHRASE_STREAM, SEG_7A)

    # Shadow miss after bank26..29 map: restore source bank, then stock lookup.
    miss = len(out)
    out += b"\x58"  # source bank
    out += far_call(BANK_MAP_OFF, BANK_HELPER_SEG)
    stock = len(out)
    out += far_jump(LEAF_FALLBACK, SEG_7A)

    _patch_rel8(out, jb_stock, stock)
    _patch_rel8(out, jne_stock, stock)
    _patch_rel8(out, je_miss, miss)
    if SHADOW_CAVE + len(out) > SHADOW_CAVE_END:
        raise BuildError(f"shadow handler overflow: {len(out)} bytes")
    return bytes(out)


def manifest_rows() -> list[dict[str, Any]]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pop = data.get("population") or {}
    rows = list(pop.get("excluded") or []) + list(pop.get("included") or [])
    return [row for row in rows if isinstance(row, dict)]


def record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def iter_native_indices(body: bytes) -> Iterable[int]:
    i = 0
    while i < len(body):
        if i + 3 < len(body) and is_ext3_magic(body[i], body[i + 1]):
            i += 4
            continue
        lead = body[i]
        if is_dict_token(lead) and i + 1 < len(body):
            yield dict_index_from_token(lead, body[i + 1])
            i += 2
            continue
        if is_kanji_lead(lead) and i + 1 < len(body):
            i += 2
            continue
        i += 1


def collect_bank61_records(parent: bytes, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        logical = int(row.get("logical_address") or 0)
        if row.get("region") != "script" or not (0x610000 <= logical < 0x620000):
            continue
        if logical in seen:
            continue
        seen.add(logical)
        payload, term = record(parent, logical)
        prefix, body, kind = split_prefix_body(payload)
        out.append(
            {
                "logical": logical,
                "payload": payload,
                "terminator": term,
                "prefix": bytes(prefix),
                "body": bytes(body),
                "kind": kind,
                "manifest_prefix": bytes.fromhex(str(row.get("prefix_hex") or "")),
                "manifest_reason": str(row.get("reason") or ""),
            }
        )
    return sorted(out, key=lambda item: int(item["logical"]))


def build_shadow_banks(
    unique_payloads: list[bytes],
    available_by_group: dict[int, list[int]],
) -> tuple[dict[bytes, int], dict[int, bytes], dict[int, dict[str, Any]]]:
    """Assign each phrase to a free native token and format expansion banks."""
    banks: dict[int, bytearray] = {}
    cursors: dict[int, int] = {}
    slots: dict[int, list[int]] = {}
    for group in range(SHADOW_GROUPS):
        bank = bytearray([0xFF] * 0x10000)
        # FFFF sentinel table is already present in all-FF bank.
        banks[group] = bank
        cursors[group] = SHADOW_POOL_START
        slots[group] = list(available_by_group.get(group) or [])

    assigned: dict[bytes, int] = {}
    group_stats: dict[int, dict[str, Any]] = {
        g: {"expansion_bank": f"{SHADOW_SEG0 + g:02X}", "phrases": 0, "payload_bytes": 0, "cursor_end": SHADOW_POOL_START}
        for g in range(SHADOW_GROUPS)
    }

    # Deterministic first-fit by descending payload length reduces fragmentation;
    # tie-break by raw bytes for reproducibility.
    for payload in sorted(unique_payloads, key=lambda blob: (-len(blob), blob)):
        need = len(payload) + 1
        chosen: tuple[int, int] | None = None
        for group in range(SHADOW_GROUPS):
            if not slots[group]:
                continue
            if cursors[group] + need <= 0x10000:
                chosen = (group, slots[group].pop(0))
                break
        if chosen is None:
            raise BuildError(f"shadow capacity exhausted for payload len={len(payload)}")
        group, index = chosen
        local = index & (SHADOW_SLOTS_PER_GROUP - 1)
        cursor = cursors[group]
        bank = banks[group]
        bank[cursor : cursor + len(payload)] = payload
        bank[cursor + len(payload)] = 0
        struct.pack_into("<H", bank, local * 2, cursor)
        cursors[group] = cursor + need
        assigned[payload] = index
        st = group_stats[group]
        st["phrases"] = int(st["phrases"]) + 1
        st["payload_bytes"] = int(st["payload_bytes"]) + need
        st["cursor_end"] = cursors[group]

    return assigned, {SHADOW_SEG0 + g: bytes(banks[g]) for g in range(SHADOW_GROUPS)}, group_stats


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN:
        raise BuildError("main TIP identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    sb = stock_base(parent)
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    rows = manifest_rows()
    bank61 = collect_bank61_records(parent, rows)

    # Accepted runtime identity and free fixed-bank tail.
    hook_file = sb + HOOK_SITE
    cave_file = sb + SHADOW_CAVE
    if parent[hook_file : hook_file + len(HOOK_EXPECT)] != HOOK_EXPECT:
        raise BuildError(
            f"shadow hook identity drifted: {parent[hook_file:hook_file+len(HOOK_EXPECT)].hex().upper()}"
        )
    if any(value != 0xFF for value in parent[cave_file : sb + SHADOW_CAVE_END]):
        raise BuildError("7F:FF18-FFEF is not an all-FF cave")
    for seg in range(SHADOW_SEG0, SHADOW_SEG0 + SHADOW_GROUPS):
        bank = bytes(slice_expansion_bank(parent, seg))
        if any(value != 0xFF for value in bank):
            raise BuildError(f"expansion bank {seg:02X} is not free")

    # Direct native indices already consumed anywhere in current bank61 may not
    # be shadowed.  Use the ext3-aware whole-script walker, not only manifest
    # rows, so a non-manifest zstring cannot acquire a new meaning accidentally.
    # Nested dictionary references are safe: while expanding a phrase ROM1 is
    # the dictionary/shadow bank rather than E1, so the hook falls back.
    whole_script_refs = _working_two_byte_external_refs(parent, regions=("script",))
    native_used = {
        int(index)
        for index, refs in whole_script_refs.items()
        if any(0x610000 <= int(ref.abs) < 0x620000 for ref in refs)
    }
    manifest_native_used: set[int] = set()
    for item in bank61:
        manifest_native_used.update(iter_native_indices(bytes(item["body"])))
    if native_used != manifest_native_used:
        raise BuildError(
            "manifest bank61 native-token coverage is incomplete: "
            f"whole={len(native_used)} manifest={len(manifest_native_used)} "
            f"extra={sorted(native_used - manifest_native_used)[:20]}"
        )

    available_by_group: dict[int, list[int]] = defaultdict(list)
    for index in range(0x0F00):  # exclude FF-page entirely
        if (index & 0xFF) == 0 or index in native_used:
            continue
        group = index >> 10
        available_by_group[group].append(index)

    # Runtime-risk class: original/manifest prefix grammar <= 1 byte and current
    # body begins with a regular 4-byte ext3 portal.
    targets: list[dict[str, Any]] = []
    for item in bank61:
        body = bytes(item["body"])
        manifest_prefix = bytes(item["manifest_prefix"])
        if len(manifest_prefix) > 1 or len(body) < 4 or not is_ext3_magic(body[0], body[1]):
            continue
        if bytes(item["prefix"]) != manifest_prefix:
            raise BuildError(
                f"prefix drift at {int(item['logical']):06X}: current={bytes(item['prefix']).hex()} manifest={manifest_prefix.hex()}"
            )
        idx = dict_index_from_ext3_token(body[0], body[1], body[2], body[3])
        raw = bytes(dictionary.raw_entry(idx))
        if not raw or b"\x00" in raw:
            raise BuildError(f"invalid ext3 phrase at {int(item['logical']):06X} slot {idx:05X}")
        targets.append(
            {
                **item,
                "ext3_index": idx,
                "raw_phrase": raw,
                "render_before": dictionary.expand(raw, tbl).rstrip("　"),
            }
        )

    sig_set = {int(item["logical"]) for item in targets if int(item["logical"]) in (0x611DF8, 0x611E05)}
    if sig_set != {0x611DF8, 0x611E05}:
        raise BuildError(f"Sig proof records missing: {sorted(sig_set)}")
    if len(targets) != 1811:
        raise BuildError(f"risk population drifted: expected 1811 got {len(targets)}")

    unique_payloads = sorted({bytes(item["raw_phrase"]) for item in targets})
    if len(unique_payloads) != 1572:
        raise BuildError(f"unique shadow phrase population drifted: {len(unique_payloads)}")

    assigned, shadow_banks, group_stats = build_shadow_banks(unique_payloads, available_by_group)

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # Install shadow data banks.  We write all four formatted banks so future
    # groups have a deterministic FFFF table; only groups with phrases differ
    # semantically from all-FF.
    for seg, blob in sorted(shadow_banks.items()):
        patch_expansion_bank(candidate, seg, blob)
        allowed.append((seg << 16, (seg + 1) << 16))

    # Install fixed-bank shadow leaf hook.
    handler = build_shadow_leaf_handler()
    hook = far_jump(SHADOW_CAVE & 0xFFFF, SEG_7F)
    hook_patch = hook + b"\x90" * (len(HOOK_EXPECT) - len(hook))
    candidate[hook_file : hook_file + len(HOOK_EXPECT)] = hook_patch
    candidate[cave_file : cave_file + len(handler)] = handler
    allowed.extend(
        [
            (hook_file, hook_file + len(HOOK_EXPECT)),
            (cave_file, cave_file + len(handler)),
        ]
    )

    target_rows: list[dict[str, Any]] = []
    target_addrs: set[int] = set()
    for item in targets:
        logical = int(item["logical"])
        prefix = bytes(item["prefix"])
        body = bytes(item["body"])
        payload = bytes(item["payload"])
        term = int(item["terminator"])
        index = assigned[bytes(item["raw_phrase"])]
        if index in native_used or index >= 0x0F00 or (index & 0xFF) == 0:
            raise BuildError(f"unsafe shadow allocation {index:04X}")
        token = bytes(token_from_dict_index(index))
        if len(token) != 2:
            raise BuildError(f"shadow token is not 2 bytes: {index:04X}")
        new_body = token + b"\x01" * (len(body) - 2)
        new_payload = prefix + new_body
        if len(new_payload) != len(payload):
            raise BuildError(f"record size drift {logical:06X}")
        start = sb + logical
        candidate[start : start + len(payload)] = new_payload
        allowed.append((start, start + len(payload)))
        target_addrs.add(logical)
        target_rows.append(
            {
                "abs": f"{logical:06X}",
                "prefix_hex": prefix.hex().upper(),
                "payload_len": len(payload),
                "body_len": len(body),
                "ext3_index_before": f"{int(item['ext3_index']):05X}",
                "shadow_index": f"{index:04X}",
                "shadow_token_hex": token.hex().upper(),
                "render_before": str(item["render_before"]),
                "manifest_reason": str(item["manifest_reason"]),
                "terminator": f"{term - sb:06X}" if term >= sb else f"{term:06X}",
            }
        )

    # Carry approved weapon 카논 -> 캐논 correction without touching records.
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    cannon_proofs: list[dict[str, Any]] = []
    for logical, (idx, before, after) in sorted(CANNON.items()):
        payload, term = record(parent, logical)
        prefix, body, _ = split_prefix_body(payload)
        rendered = dictionary.expand(body, tbl).rstrip("　")
        slot_before = dictionary.expand_index(idx, tbl).rstrip("　")
        if rendered != before or slot_before != before:
            raise BuildError(f"cannon target drift {logical:06X}: {rendered!r}/{slot_before!r}")
        consumers = consumer_abs_set(union, idx)
        if consumers != {logical}:
            raise BuildError(f"cannon slot {idx:05X} not private: {sorted(consumers)}")
        storage = ext3_storage_proof(parent, dictionary, idx)
        encoded = encode(after, tbl)
        if not storage["ok"] or len(encoded) > int(storage["old_len"]):
            raise BuildError(f"cannon slot cannot be in-place {idx:05X}: {storage}")
        allowed.append(inplace_phrase(candidate, storage, encoded))
        cannon_proofs.append(
            {
                "abs": f"{logical:06X}",
                "slot": f"{idx:05X}",
                "before": before,
                "after": after,
                "record_payload_hex": payload.hex().upper(),
                "record_terminator": term,
                "storage": storage,
            }
        )

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)

    # Structural verification: all targets have native two-byte shadow bodies,
    # exact prefix/terminator, and raw shadow phrase bytes equal the old ext3 raw.
    shadow_verify: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for item in targets:
        logical = int(item["logical"])
        before_payload, before_term = record(parent, logical)
        after_payload, after_term = record(result, logical)
        before_prefix, _, _ = split_prefix_body(before_payload)
        after_prefix, after_body, _ = split_prefix_body(after_payload)
        index = assigned[bytes(item["raw_phrase"])]
        seg = SHADOW_SEG0 + (index >> 10)
        local = index & 0x3FF
        bank = shadow_banks[seg]
        ptr = struct.unpack_from("<H", bank, local * 2)[0]
        end = ptr
        while end < len(bank) and bank[end] != 0:
            end += 1
        raw_after = bytes(bank[ptr:end]) if ptr != SHADOW_SENTINEL else b""
        check = {
            "abs": f"{logical:06X}",
            "shadow_index": f"{index:04X}",
            "shadow_bank": f"{seg:02X}",
            "prefix_exact": bytes(before_prefix) == bytes(after_prefix),
            "terminator_exact": before_term == after_term,
            "body_is_native_two_byte_plus_padding": (
                len(after_body) == len(bytes(item["body"]))
                and after_body[:2] == token_from_dict_index(index)
                and all(value == 0x01 for value in after_body[2:])
            ),
            "raw_phrase_exact": raw_after == bytes(item["raw_phrase"]),
            "render_exact": dictionary.expand(raw_after, tbl).rstrip("　") == str(item["render_before"]),
        }
        check["ok"] = all(value for key, value in check.items() if key.endswith("_exact") or key.startswith("body_is_"))
        shadow_verify.append(check)
        if not check["ok"]:
            failures.append(check)

    # All non-target manifest-backed bank61 records must remain byte-exact.
    non_target_changes: list[str] = []
    for item in bank61:
        logical = int(item["logical"])
        if logical in target_addrs:
            continue
        before_payload, before_term = record(parent, logical)
        after_payload, after_term = record(result, logical)
        if before_payload != after_payload or before_term != after_term:
            non_target_changes.append(f"{logical:06X}")
            if len(non_target_changes) >= 40:
                break

    # Cannon records themselves are byte-exact; only private phrase storage moves.
    cannon_checks: list[dict[str, Any]] = []
    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)
    for logical, (idx, before, after) in sorted(CANNON.items()):
        bp, bt = record(parent, logical)
        ap, at = record(result, logical)
        _, body, _ = split_prefix_body(ap)
        rendered = result_dictionary.expand(body, tbl).rstrip("　")
        cannon_checks.append(
            {
                "abs": f"{logical:06X}",
                "rendered": rendered,
                "expected": after,
                "record_bytes_exact": bp == ap,
                "terminator_exact": bt == at,
                "ok": rendered == after and bp == ap and bt == at,
            }
        )

    # Runtime/data identity checks.
    runtime_checks = {
        "hook_exact": result[hook_file : hook_file + len(HOOK_EXPECT)] == hook_patch,
        "handler_exact": result[cave_file : cave_file + len(handler)] == handler,
        "source_bank_al": f"{SOURCE_BANK_AL:02X}",
        "shadow_bank_al0": f"{bank_al_expansion(SHADOW_SEG0):02X}",
        "future_banks_28_29_formatted_only": all(
            group_stats[g]["phrases"] == 0 for g in (2, 3)
        ),
        "sig_611df8_shadowed": 0x611DF8 in target_addrs,
        "sig_611e05_shadowed": 0x611E05 in target_addrs,
    }

    if failures or non_target_changes or not all(row["ok"] for row in cannon_checks) or not all(
        value if isinstance(value, bool) else True for value in runtime_checks.values()
    ):
        raise BuildError(
            "post-build verification failed: "
            + json.dumps(
                {
                    "target_failures": failures[:10],
                    "non_target_changes": non_target_changes,
                    "cannon_failures": [row for row in cannon_checks if not row["ok"]],
                    "runtime": runtime_checks,
                },
                ensure_ascii=False,
            )
        )

    # Whole-ROM diff allowlist.
    allowed = merged(allowed)
    runs = diff_runs(parent, result)
    unaccounted: list[int] = []
    for run in runs:
        start, end = int(run["start"], 16), int(run["end"], 16)
        for offset in range(start, end):
            if not in_intervals(offset, allowed):
                unaccounted.append(offset)
                if len(unaccounted) >= 40:
                    break
        if len(unaccounted) >= 40:
            break
    if unaccounted:
        raise BuildError("unaccounted bytes: " + ",".join(f"{x:06X}" for x in unaccounted))

    OUT_ROM.write_bytes(result)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)

    target_doc = {
        "schema_version": 1,
        "generated_by": "tools/build_bank61_shadow_dictionary_candidate.py",
        "parent_sha256": sha256(parent),
        "risk_rule": "manifest script bank61 + manifest prefix length <=1 + current body starts E5 18",
        "count": len(target_rows),
        "unique_raw_phrases": len(unique_payloads),
        "rows": target_rows,
    }
    atomic_json(OUT_TARGETS, target_doc)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_bank61_shadow_dictionary_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_test_required",
        "main_tip_modified": False,
        "inputs": {
            "main": {"path": str(MAIN), "sha256": sha256(parent), "size": len(parent)},
            "live_saveram": {"path": str(MAIN_SAVE), "sha256": sha256(save), "size": len(save)},
            "manifest": {"path": str(MANIFEST), "sha256": sha256(MANIFEST.read_bytes())},
        },
        "candidate": {
            "path": str(OUT_ROM),
            "sha256": sha256(result),
            "size": len(result),
            "checksum": f"{checksum:04X}",
        },
        "candidate_saveram": {
            "path": str(OUT_SAVE),
            "sha256": sha256(OUT_SAVE.read_bytes()),
            "size": OUT_SAVE.stat().st_size,
            "copied_from_live_at_build": True,
        },
        "shadow_dictionary": {
            "source_logical_bank": f"{SOURCE_LOGICAL_BANK:02X}",
            "source_bank_al": f"{SOURCE_BANK_AL:02X}",
            "expansion_banks": [f"{SHADOW_SEG0 + g:02X}" for g in range(SHADOW_GROUPS)],
            "groups": group_stats,
            "pointer_entries_per_bank": SHADOW_SLOTS_PER_GROUP,
            "pointer_table_bytes": SHADOW_PTR_BYTES,
            "pool_start": f"{SHADOW_POOL_START:04X}",
            "missing_pointer_sentinel": "FFFF",
            "allocation_rule": "current bank61 direct-native unused; non-FF index <0F00; trail !=00",
            "current_native_indices_used": len(native_used),
            "whole_bank61_native_scan_matches_manifest": native_used == manifest_native_used,
            "available_shadow_indices": sum(len(v) for v in available_by_group.values()),
            "target_records": len(targets),
            "unique_ext3_slots_before": len({int(item["ext3_index"]) for item in targets}),
            "unique_raw_phrases": len(unique_payloads),
            "handler_logical": f"{SHADOW_CAVE:06X}",
            "handler_len": len(handler),
            "hook_logical": f"{HOOK_SITE:06X}",
            "hook_before": HOOK_EXPECT.hex().upper(),
            "hook_after": hook_patch.hex().upper(),
        },
        "sig_runtime_proof": {
            "611DF8": next(row for row in target_rows if row["abs"] == "611DF8"),
            "611E05": next(row for row in target_rows if row["abs"] == "611E05"),
            "expected_runtime_sequence": [
                "장난치지 마라！",
                "세라를 죽여놓고선、",
                "뻔뻔하게 잘도 살아 숨 쉬는구나！！",
            ],
            "raw_E5_18_removed_from_two_reported_records": all(
                not bytes(record(result, logical)[0])[len(split_prefix_body(record(result, logical)[0])[0]) :].startswith(b"\xE5\x18")
                for logical in (0x611DF8, 0x611E05)
            ),
        },
        "cannon": {
            "records": cannon_checks,
            "proofs": cannon_proofs,
            "all_ok": all(row["ok"] for row in cannon_checks),
        },
        "gates": {
            "shadow_targets_verified": len(shadow_verify),
            "shadow_target_failures": len(failures),
            "bank61_non_target_record_changes": len(non_target_changes),
            "cannon_failures": sum(not row["ok"] for row in cannon_checks),
            "diff_runs": len(runs),
            "diff_bytes": sum(int(run["end"], 16) - int(run["start"], 16) for run in runs),
            "unaccounted_changed_bytes": len(unaccounted),
            "runtime_checks": runtime_checks,
        },
        "targets_file": str(OUT_TARGETS),
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({
        "candidate": str(OUT_ROM),
        "sha256": report["candidate"]["sha256"],
        "checksum": report["candidate"]["checksum"],
        "targets": len(targets),
        "unique_phrases": len(unique_payloads),
        "groups": group_stats,
        "handler_len": len(handler),
        "diff_runs": report["gates"]["diff_runs"],
        "diff_bytes": report["gates"]["diff_bytes"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
