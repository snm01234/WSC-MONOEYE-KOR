#!/usr/bin/env python3
"""Build a source-independent native-2B dictionary repair for the Sig scenario.

The broken bank61 event block contains 20 continuation/body records in
611D7A..611F79 whose original grammar used native 2-byte dictionary units but
whose current Korean body begins with the 4-byte E5 18 ext3 portal. Runtime
screens prove this leaks raw portal/control bytes (こ / み) and can terminate the
event early.

This candidate does NOT add another token syntax and does NOT depend on source
bank detection.  It reserves three CURRENT-UNREACHABLE native dictionary index
ranges (20 slots total):

    0D4C..0D52  (7)
    0DB3..0DB8  (6)
    0E81..0E87  (7)

The existing 7A:0700 native dictionary load trampoline is redirected to a small
wrapper at 7F:FF18.  Only those three ranges are mapped to expansion bank26;
every other native/bank10 index is delegated byte-for-byte to the accepted
7F:FC8C helper.  Bank26 holds a 4096-entry LE16 pointer table so the helper can
index it with the existing SI=index*2 value.

The 20 target phrases are copied byte-exact from their current ext3 payloads.
Each target record keeps its original prefix, payload extent, padding extent,
and NUL terminator; only its 4-byte E5 18 token is replaced by one reserved
2-byte native token and two additional 01 padding bytes.

The four previously requested weapon names 카논 -> 캐논 are included by the same
private-ext3 in-place method used by the earlier candidate.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_terminology_retranslation_candidate import (
    consumer_abs_set,
    diff_runs,
    encode,
    ext3_storage_proof,
    in_intervals,
    inplace_phrase,
    merged,
)
from expand_dictionary import iter_dict_indices
from extract_script import split_prefix_body
from mixed_residual_reference_union import (
    _nested_parents,
    _working_two_byte_external_refs,
    build_reference_union,
)
from monoeye_rom import (
    Tbl,
    dict_index_from_ext3_token,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXP_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/sig_scenario_native20_candidate.wsc"
OUT_SAVE = ROOT / "sram/sig_scenario_native20_candidate.sav"
OUT_REPORT = ROOT / "out/patch/sig_scenario_native20_report.json"
OUT_TARGETS = ROOT / "out/script/sig_scenario_native20_targets.json"

EXPECTED_MAIN = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Accepted loader contract on the current main.
TRAMP_LOGICAL = 0x7AFFED
TRAMP_EXPECT = bytes.fromhex("9A8CFC00F0C3")  # far call F000:FC8C ; ret
OLD_HELPER_SEG = 0xF000
OLD_HELPER_OFF = 0xFC8C
NEW_HELPER_LOGICAL = 0x7FFF18
NEW_HELPER_SEG = 0xF000
NEW_HELPER_OFF = 0xFF18
NEW_HELPER_LIMIT = 0x7FFFF0
BANK_MAP_SEG = 0x8000
BANK_MAP_OFF = 0xDEB5
NATIVE_EXP_BANK = 0x26
PTR_TABLE_BYTES = 0x2000  # 4096 * LE16
PHRASE_START = PTR_TABLE_BYTES

RESERVED_RANGES = (
    (0x0D4C, 0x0D52),
    (0x0DB3, 0x0DB8),
    (0x0E81, 0x0E87),
)
RESERVED_INDICES = tuple(
    idx for lo, hi in RESERVED_RANGES for idx in range(lo, hi + 1)
)

# Exact local risk set: all prefix<=1 + ext3 records in the same contiguous
# Sig event block from 611D7A through 611F79.  Count == reserved slots == 20.
TARGETS = (
    0x611D7A,
    0x611D86,
    0x611D96,
    0x611DF8,
    0x611E05,
    0x611E20,
    0x611E4C,
    0x611E57,
    0x611E62,
    0x611E78,
    0x611E86,
    0x611E8F,
    0x611E9B,
    0x611EAE,
    0x611EB7,
    0x611EC2,
    0x611EE5,
    0x611EEE,
    0x611F6F,
    0x611F79,
)

REPORTED = (0x611DF8, 0x611E05)
NEIGHBORS = (0x611DF0, 0x611E10, 0x611E13, 0x611E2D, 0x611F86)

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


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    tmp.replace(path)


def record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def patch_rel8(buf: bytearray, at: int, target: int) -> None:
    disp = target - (at + 2)
    if not -128 <= disp <= 127:
        raise BuildError(f"rel8 out of range {disp}")
    buf[at + 1] = disp & 0xFF


def far_call(off: int, seg: int) -> bytes:
    return b"\x9A" + struct.pack("<HH", off & 0xFFFF, seg & 0xFFFF)


def build_native_wrapper() -> bytes:
    """Far-callable wrapper. Entry/exit contract matches 7F:FC8C.

    Entry: SI=index*2, ES=3000, ROM1 currently mapped to stock dictionary 5F.
    Exit: AX=phrase offset, ROM1 mapped to the phrase bank, RETF.
    """
    out = bytearray()
    hit_jumps: list[int] = []

    # range 1
    out += b"\x81\xFE" + struct.pack("<H", RESERVED_RANGES[0][0] * 2)
    jb_r2 = len(out); out += b"\x72\x00"
    out += b"\x81\xFE" + struct.pack("<H", RESERVED_RANGES[0][1] * 2)
    jbe_hit1 = len(out); out += b"\x76\x00"; hit_jumps.append(jbe_hit1)

    r2 = len(out)
    patch_rel8(out, jb_r2, r2)
    out += b"\x81\xFE" + struct.pack("<H", RESERVED_RANGES[1][0] * 2)
    jb_r3 = len(out); out += b"\x72\x00"
    out += b"\x81\xFE" + struct.pack("<H", RESERVED_RANGES[1][1] * 2)
    jbe_hit2 = len(out); out += b"\x76\x00"; hit_jumps.append(jbe_hit2)

    r3 = len(out)
    patch_rel8(out, jb_r3, r3)
    out += b"\x81\xFE" + struct.pack("<H", RESERVED_RANGES[2][0] * 2)
    jb_fallback = len(out); out += b"\x72\x00"
    out += b"\x81\xFE" + struct.pack("<H", RESERVED_RANGES[2][1] * 2)
    jbe_hit3 = len(out); out += b"\x76\x00"; hit_jumps.append(jbe_hit3)

    fallback = len(out)
    patch_rel8(out, jb_fallback, fallback)
    out += far_call(OLD_HELPER_OFF, OLD_HELPER_SEG)
    out += b"\xCB"  # retf

    hit = len(out)
    for at in hit_jumps:
        patch_rel8(out, at, hit)
    out += b"\xB0" + bytes([NATIVE_EXP_BANK])
    out += far_call(BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\x26\x8B\x04"  # mov ax, es:[si]
    out += b"\xCB"

    if NEW_HELPER_LOGICAL + len(out) > NEW_HELPER_LIMIT:
        raise BuildError("native wrapper cave overflow")
    return bytes(out)


def expansion_bank_slice(rom: bytes | bytearray, seg: int) -> bytes:
    start = (seg & 0x7F) * 0x10000
    return bytes(rom[start:start + 0x10000])


def find_ext3_nested_hits(dictionary: Any, reserved: set[int]) -> dict[int, list[int]]:
    hits: dict[int, list[int]] = {}
    for parent in range(0x1000, 0x11000):
        try:
            raw = bytes(dictionary.raw_entry(parent))
        except Exception:
            continue
        if not raw:
            continue
        for child in iter_dict_indices(raw):
            child = int(child)
            if child in reserved:
                hits.setdefault(child, []).append(parent)
    return hits


def build() -> tuple[bytes, dict[str, Any]]:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN:
        raise BuildError("main TIP identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")
    if len(RESERVED_INDICES) != len(TARGETS) or len(TARGETS) != 20:
        raise BuildError("reserved/target count mismatch")

    tbl = Tbl.load(TBL_PATH)
    exp_meta = load_ext_meta(EXP_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, exp_meta, ext3_meta)
    union = build_reference_union(
        original,
        parent,
        ext_meta=exp_meta,
        ext3_meta=ext3_meta,
    )
    working_external = _working_two_byte_external_refs(parent)
    nested_native = _nested_parents(dictionary)
    ext3_nested = find_ext3_nested_hits(dictionary, set(RESERVED_INDICES))

    reserved_proofs: list[dict[str, Any]] = []
    for index in RESERVED_INDICES:
        if not dict_token_safe_in_zstring(index):
            raise BuildError(f"reserved token unsafe in zstring: {index:04X}")
        current_external = working_external.get(index) or []
        current_nested = nested_native.get(index) or set()
        current_ext3_nested = ext3_nested.get(index) or []
        if current_external or current_nested or current_ext3_nested:
            raise BuildError(
                f"reserved index {index:04X} is live: "
                f"external={current_external} nested={current_nested} ext3={current_ext3_nested}"
            )
        original_only = [
            {
                "abs": f"{row.abs:06X}",
                "region": row.region,
                "kind": row.kind,
                "seen_in": sorted(row.seen_in),
            }
            for row in union.consumers_for(index)
        ]
        reserved_proofs.append(
            {
                "index": f"{index:04X}",
                "token_hex": token_from_dict_index(index).hex().upper(),
                "working_external_consumers": 0,
                "current_native_nested_parents": 0,
                "current_ext3_nested_parents": 0,
                "union_provenance_consumers": original_only,
            }
        )

    # Runtime identities.
    sb = stock_base(parent)
    tramp_abs = sb + TRAMP_LOGICAL
    if parent[tramp_abs:tramp_abs + len(TRAMP_EXPECT)] != TRAMP_EXPECT:
        raise BuildError(
            f"native loader trampoline drifted: "
            f"{parent[tramp_abs:tramp_abs+len(TRAMP_EXPECT)].hex().upper()}"
        )
    cave_abs = sb + NEW_HELPER_LOGICAL
    cave_len = NEW_HELPER_LIMIT - NEW_HELPER_LOGICAL
    if any(b != 0xFF for b in parent[cave_abs:cave_abs + cave_len]):
        raise BuildError("7F:FF18 native helper cave is not free on current main")
    if any(b != 0xFF for b in expansion_bank_slice(parent, NATIVE_EXP_BANK)):
        raise BuildError("expansion bank26 is not free on current main")

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # Copy the current ext3 phrase payloads byte-exact into bank26.
    bank = bytearray(b"\xFF" * 0x10000)
    cursor = PHRASE_START
    target_rows: list[dict[str, Any]] = []
    for logical, native_index in zip(TARGETS, RESERVED_INDICES):
        payload, terminator = record(parent, logical)
        prefix, body, kind = split_prefix_body(payload)
        if kind != "dialogue" or len(prefix) > 1 or not body.startswith(b"\xE5\x18"):
            raise BuildError(
                f"target structure drifted at {logical:06X}: "
                f"prefix={prefix.hex()} body={body[:8].hex()} kind={kind}"
            )
        if len(body) < 4:
            raise BuildError(f"target body too short at {logical:06X}")
        ext3_index = dict_index_from_ext3_token(body[0], body[1], body[2], body[3])
        phrase = bytes(dictionary.raw_entry(ext3_index))
        if not phrase or b"\x00" in phrase:
            raise BuildError(f"bad ext3 phrase at {logical:06X}/{ext3_index:05X}")
        need = len(phrase) + 1
        if cursor + need > 0x10000:
            raise BuildError("bank26 phrase pool overflow")
        struct.pack_into("<H", bank, native_index * 2, cursor)
        bank[cursor:cursor + len(phrase)] = phrase
        bank[cursor + len(phrase)] = 0
        phrase_at = cursor
        cursor += need

        native_token = token_from_dict_index(native_index)
        new_body = native_token + b"\x01" * (len(body) - len(native_token))
        new_payload = prefix + new_body
        if len(new_payload) != len(payload):
            raise BuildError(f"payload extent changed at {logical:06X}")
        start = sb + logical
        candidate[start:start + len(payload)] = new_payload
        allowed.append((start, start + len(payload)))

        target_rows.append(
            {
                "abs": f"{logical:06X}",
                "prefix_hex": prefix.hex().upper(),
                "payload_len": len(payload),
                "body_len": len(body),
                "terminator": f"{terminator - sb:06X}",
                "old_ext3_index": f"{ext3_index:05X}",
                "old_ext3_token_hex": body[:4].hex().upper(),
                "native_index": f"{native_index:04X}",
                "native_token_hex": native_token.hex().upper(),
                "phrase_len": len(phrase),
                "phrase_sha256": sha256(phrase),
                "bank26_phrase_off": f"{phrase_at:04X}",
                "reported_screen_anchor": logical in REPORTED,
            }
        )

    bank_start = NATIVE_EXP_BANK * 0x10000
    candidate[bank_start:bank_start + 0x10000] = bank
    allowed.append((bank_start, bank_start + 0x10000))

    # Install source-independent native loader wrapper.  Keep the outer near
    # trampoline shape: it still far-calls one helper and near-returns.
    wrapper = build_native_wrapper()
    new_tramp = far_call(NEW_HELPER_OFF, NEW_HELPER_SEG) + b"\xC3"
    if len(new_tramp) != len(TRAMP_EXPECT):
        raise BuildError("trampoline length drift")
    candidate[tramp_abs:tramp_abs + len(new_tramp)] = new_tramp
    candidate[cave_abs:cave_abs + len(wrapper)] = wrapper
    allowed.append((tramp_abs, tramp_abs + len(new_tramp)))
    allowed.append((cave_abs, cave_abs + len(wrapper)))

    # 카논 -> 캐논: same-length private ext3 phrases; records remain exact.
    cannon_proofs: list[dict[str, Any]] = []
    for logical, (idx, before, after) in sorted(CANNON.items()):
        payload, _terminator = record(parent, logical)
        prefix, body, _ = split_prefix_body(payload)
        rendered = dictionary.expand(body, tbl).rstrip("　")
        if prefix or rendered != before or dictionary.expand_index(idx, tbl).rstrip("　") != before:
            raise BuildError(f"cannon target drifted at {logical:06X}")
        consumers = consumer_abs_set(union, idx)
        if consumers != {logical}:
            raise BuildError(f"cannon slot {idx:05X} shared: {sorted(consumers)}")
        storage = ext3_storage_proof(parent, dictionary, idx)
        encoded = encode(after, tbl)
        if not storage["ok"] or len(encoded) > int(storage["old_len"]):
            raise BuildError(f"cannon slot cannot be replaced in-place: {storage}")
        storage.update(
            {
                "record_abs": f"{logical:06X}",
                "before": before,
                "ko": after,
                "new_len": len(encoded),
                "consumers": [f"{x:06X}" for x in sorted(consumers)],
                "strategy": "inplace_private_ext3_phrase",
            }
        )
        allowed.append(inplace_phrase(candidate, storage, encoded))
        cannon_proofs.append(storage)

    checksum = update_ws_checksum(candidate)
    allowed.append((len(parent) - 2, len(parent)))
    result = bytes(candidate)

    # Candidate-bound structural verification. Do not use a normal Dictionary
    # to render reserved tokens: their new semantics live in the native wrapper.
    failures: list[dict[str, Any]] = []
    target_checks: list[dict[str, Any]] = []
    for row in target_rows:
        logical = int(row["abs"], 16)
        before_payload, before_term = record(parent, logical)
        after_payload, after_term = record(result, logical)
        prefix = bytes.fromhex(row["prefix_hex"])
        token = bytes.fromhex(row["native_token_hex"])
        expected = prefix + token + b"\x01" * (
            len(before_payload) - len(prefix) - len(token)
        )
        idx = int(row["native_index"], 16)
        ptr = result[bank_start + idx * 2] | (result[bank_start + idx * 2 + 1] << 8)
        phrase = result[bank_start + ptr:]
        phrase = phrase[:phrase.index(0)] if 0 in phrase else b""
        source_phrase = bytes(dictionary.raw_entry(int(row["old_ext3_index"], 16)))
        check = {
            "abs": row["abs"],
            "payload_exact": after_payload == expected,
            "terminator_exact": after_term == before_term,
            "native_token_exact": after_payload[len(prefix):len(prefix)+2] == token,
            "ext3_magic_removed_from_body": b"\xE5\x18" not in after_payload[len(prefix):],
            "bank26_pointer": f"{ptr:04X}",
            "phrase_raw_exact": phrase == source_phrase,
            "phrase_sha256": sha256(phrase),
        }
        check["ok"] = all(
            check[k]
            for k in (
                "payload_exact",
                "terminator_exact",
                "native_token_exact",
                "ext3_magic_removed_from_body",
                "phrase_raw_exact",
            )
        )
        target_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    neighbor_checks: list[dict[str, Any]] = []
    for logical in NEIGHBORS:
        bp, bt = record(parent, logical)
        ap, at = record(result, logical)
        check = {
            "abs": f"{logical:06X}",
            "record_bytes_exact": ap == bp,
            "terminator_exact": at == bt,
        }
        check["ok"] = check["record_bytes_exact"] and check["terminator_exact"]
        neighbor_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    # Wrapper/table proof.
    runtime_checks = {
        "trampoline_before": TRAMP_EXPECT.hex().upper(),
        "trampoline_after": result[tramp_abs:tramp_abs + len(new_tramp)].hex().upper(),
        "trampoline_exact": result[tramp_abs:tramp_abs + len(new_tramp)] == new_tramp,
        "wrapper_len": len(wrapper),
        "wrapper_exact": result[cave_abs:cave_abs + len(wrapper)] == wrapper,
        "old_helper_byte_exact": (
            result[sb + 0x7FFC8C:sb + 0x7FFCAB]
            == parent[sb + 0x7FFC8C:sb + 0x7FFCAB]
        ),
        "ext3_leaf_byte_exact": (
            result[sb + 0x7FFD10:sb + 0x7FFF18]
            == parent[sb + 0x7FFD10:sb + 0x7FFF18]
        ),
        "bank26_pointer_table_bytes": PTR_TABLE_BYTES,
        "bank26_phrase_end": f"{cursor:04X}",
        "reserved_slots": len(RESERVED_INDICES),
    }
    runtime_checks["ok"] = (
        runtime_checks["trampoline_exact"]
        and runtime_checks["wrapper_exact"]
        and runtime_checks["old_helper_byte_exact"]
        and runtime_checks["ext3_leaf_byte_exact"]
    )
    if not runtime_checks["ok"]:
        failures.append(runtime_checks)

    # Cannon render verification with ordinary dictionary (native reserved slots
    # are unrelated to those private ext3 entries).
    result_dictionary = make_dictionary_ext3(result, exp_meta, ext3_meta)
    cannon_checks: list[dict[str, Any]] = []
    for logical, (idx, before, after) in sorted(CANNON.items()):
        before_payload, before_term = record(parent, logical)
        after_payload, after_term = record(result, logical)
        _prefix, body, _ = split_prefix_body(after_payload)
        rendered = result_dictionary.expand(body, tbl).rstrip("　")
        check = {
            "abs": f"{logical:06X}",
            "slot": f"{idx:05X}",
            "before": before,
            "ko": after,
            "rendered": rendered,
            "record_bytes_exact": after_payload == before_payload,
            "terminator_exact": after_term == before_term,
        }
        check["ok"] = (
            rendered == after
            and check["record_bytes_exact"]
            and check["terminator_exact"]
        )
        cannon_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    # Whole-ROM allowlist.
    allowed = merged(allowed)
    runs = diff_runs(parent, result)
    unaccounted: list[int] = []
    for run in runs:
        start, end = int(run["start"], 16), int(run["end"], 16)
        for offset in range(start, end):
            if not in_intervals(offset, allowed):
                unaccounted.append(offset)
                if len(unaccounted) >= 64:
                    break
        if len(unaccounted) >= 64:
            break
    if unaccounted:
        failures.append(
            {
                "kind": "unaccounted_diff",
                "offsets": [f"{x:08X}" for x in unaccounted],
            }
        )

    if failures:
        raise BuildError(
            "candidate verification failed: "
            + json.dumps(failures[:12], ensure_ascii=False)
        )

    OUT_ROM.write_bytes(result)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != save:
        raise BuildError("candidate SaveRAM copy mismatch")

    targets_report = {
        "schema_version": 1,
        "generated_by": "tools/build_sig_scenario_native20_candidate.py",
        "parent_sha256": sha256(parent),
        "targets": target_rows,
        "reserved_indices": [f"{x:04X}" for x in RESERVED_INDICES],
    }
    atomic_json(OUT_TARGETS, targets_report)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_sig_scenario_native20_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_test_required",
        "main_tip_modified": False,
        "root_cause_revision": {
            "prior_shadow_candidate": "rejected by runtime test: こ remained and event ended after み",
            "new_model": (
                "the affected event block requires ordinary native 2-byte dictionary semantics; "
                "source-bank shadowing is not reliable across its continuation/load paths"
            ),
            "repair": (
                "source-independent reserved native indices handled in the accepted native dictionary loader; "
                "20 current-unreachable indices -> expansion bank26"
            ),
        },
        "inputs": {
            "main": str(MAIN),
            "main_sha256": sha256(parent),
            "live_saveram_sha256": sha256(save),
            "original_sha256": sha256(original),
        },
        "candidate": {
            "path": str(OUT_ROM),
            "sha256": sha256(result),
            "size": len(result),
            "checksum": f"{checksum:04X}",
            "save_path": str(OUT_SAVE),
            "save_sha256": sha256(save),
        },
        "native_reserved": {
            "ranges": [
                {"start": f"{lo:04X}", "end": f"{hi:04X}", "count": hi-lo+1}
                for lo, hi in RESERVED_RANGES
            ],
            "count": len(RESERVED_INDICES),
            "proofs": reserved_proofs,
            "expansion_bank": f"{NATIVE_EXP_BANK:02X}",
            "pointer_table_bytes": PTR_TABLE_BYTES,
            "phrase_start": f"{PHRASE_START:04X}",
            "phrase_end": f"{cursor:04X}",
            "runtime": runtime_checks,
        },
        "sig_scenario": {
            "scope": "611D7A-611F79 contiguous local risk set",
            "records": len(TARGETS),
            "reported": [f"{x:06X}" for x in REPORTED],
            "targets": target_checks,
            "neighbors": neighbor_checks,
            "risk_residual_in_scope": 0,
        },
        "cannon": {
            "records": len(CANNON),
            "proofs": cannon_proofs,
            "checks": cannon_checks,
        },
        "diff": {
            "runs": len(runs),
            "bytes": sum(int(r["length"]) for r in runs),
            "unaccounted_changed_bytes": 0,
        },
        "gates": {
            "reserved_external_consumers": 0,
            "reserved_native_nested_parents": 0,
            "reserved_ext3_nested_parents": 0,
            "target_failures": 0,
            "neighbor_failures": 0,
            "cannon_failures": 0,
            "unaccounted_changed_bytes": 0,
        },
    }
    atomic_json(OUT_REPORT, report)
    return result, report


def main() -> int:
    result, report = build()
    print(
        json.dumps(
            {
                "candidate": report["candidate"]["path"],
                "sha256": report["candidate"]["sha256"],
                "checksum": report["candidate"]["checksum"],
                "targets": report["sig_scenario"]["records"],
                "reserved": report["native_reserved"]["count"],
                "wrapper_len": report["native_reserved"]["runtime"]["wrapper_len"],
                "bank26_phrase_end": report["native_reserved"]["phrase_end"],
                "diff_runs": report["diff"]["runs"],
                "diff_bytes": report["diff"]["bytes"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
