#!/usr/bin/env python3
"""Build the STAGE16t event/dialogue follow-up candidate.

Parent is the unpromoted terminology candidate from 2026-08-16.  The live main
TIP, live TBL and live SaveRAM are never modified.

The candidate fixes three runtime-structure anchors by returning them to native
stock-token grammar, and rewrites the surrounding STAGE16t Korean text through
single-consumer ext3 slots.  Event-control bytes, record terminators and portrait
control rows are protected byte-exactly.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_dialogue_20cell_candidate import alias_bank_cursor, encode, ext3_index  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_scenario_page_boundary_guard_candidate import safe_unreachable_slots  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    patch_expansion_bank,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    write_le16,
)

PATCH = ROOT / "out/patch"
PARENT = PATCH / "main_tip_name_mapping_consistency_candidate.wsc"
PARENT_TBL = PATCH / "main_tip_name_mapping_consistency_candidate.tbl"
PARENT_SAVE = ROOT / "sram/main_tip_name_mapping_consistency_candidate.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
SPEC = ROOT / "data/stage16t_event_followup_ko.json"
OUT_ROM = PATCH / "stage16t_event_followup_candidate.wsc"
OUT_TBL = PATCH / "stage16t_event_followup_candidate.tbl"
OUT_SAVE = ROOT / "sram/stage16t_event_followup_candidate.sav"
REPORT = PATCH / "stage16t_event_followup_candidate_report.json"

EXPECTED_PARENT_SHA = "bb78b5704d752bf86fa430f975e414cb226013f239f74f22a5cc4ea0f79354cc"
EXPECTED_TBL_SHA = "5a6189f77e198dc3fd0b891778eb80512ba60537a74cf9b4353db2bcc520f47a"
EXPECTED_SAVE_SHA = "a409de705c53a6a107f375da5dc8d393da9866cc600520ce0edff0441fd43079"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Four native fragments are needed.  Three use byte-identical duplicate stock
# payload extents that can be consolidated without changing their old render;
# the fourth uses the proven contiguous, unreachable 07BD+07BE span.
NATIVE_FRAGMENT_INDEX = {
    "해냈나": 0x078C,
    "남자가": 0x0799,
    "힘？": 0x079D,
    "맞": 0x07BD,
}
CONTIGUOUS_HELPER = 0x07BE

# Existing stock tokens used to compose the three runtime-sensitive records.
EXISTING_STOCK_TEXT = {
    0x060C: "……！",
    0x0191: "……",
    0x0171: "그",
    0x005C: "다！",
}

CONTROL_WINDOWS = (
    # Bright line terminator -> event row -> next citizen first line.
    (0x623DCD, 0x623DD7),
    # Citizen continuation terminator -> event controls -> next first line.
    (0x623DF4, 0x623E00),
    # Scirocco short line terminator -> portrait/event control -> Katejina line.
    (0x624278, 0x62427D),
)


class BuildError(RuntimeError):
    pass


def sha(payload: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(payload)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def payload_at(rom: bytes | bytearray, logical: int, *, max_len: int = 256) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=max_len)
    if got is None:
        raise BuildError(f"cannot read zstring {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def original_body_text(original: bytes, dictionary: Dictionary, tbl: Tbl, logical: int, prefix_len: int) -> str:
    raw, _ = payload_at(original, logical)
    if len(raw) < prefix_len:
        raise BuildError(f"original record too short at {logical:06X}")
    return dictionary.expand(raw[prefix_len:], tbl)


def rewrite_ext3_by_address(
    candidate: bytearray,
    parent: bytes,
    original: bytes,
    tbl: Tbl,
    original_dictionary: Dictionary,
    ext_meta: dict[str, Any],
    ext3_meta: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    jobs: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    seen_tokens: set[bytes] = set()

    for spec in rows:
        logical = int(spec["abs"], 16)
        raw, _term = payload_at(parent, logical)
        pos = raw.find(b"\xE5\x18")
        if pos not in (0, 1, 3) or raw.find(b"\xE5\x18", pos + 2) >= 0:
            raise BuildError(f"{logical:06X} is not a single E5 18 record: {raw.hex().upper()}")
        token = raw[pos : pos + 4]
        if len(token) != 4 or parent.count(token) != 1:
            raise BuildError(f"{logical:06X} ext3 token is not single-consumer: {token.hex().upper()}")
        if token in seen_tokens:
            raise BuildError(f"duplicate ext3 target token {token.hex().upper()}")
        seen_tokens.add(token)
        index = ext3_index(token)
        if index is None:
            raise BuildError(f"invalid ext3 token at {logical:06X}")
        seg, local = parent_dictionary._ext3_bank_local(index)
        original_text = original_body_text(original, original_dictionary, tbl, logical, pos)
        if original_text != spec["jp"]:
            raise BuildError(
                f"JP source drift at {logical:06X}: {original_text!r} != {spec['jp']!r}"
            )
        if len(spec["ko"]) > 20:
            raise BuildError(f"20-cell overflow in source spec {logical:06X}: {spec['ko']!r}")
        encoded = encode(str(spec["ko"]), tbl)
        jobs[int(seg)].append(
            {
                "logical": logical,
                "token": token,
                "index": int(index),
                "local": int(local),
                "jp": str(spec["jp"]),
                "ko": str(spec["ko"]),
                "encoded": encoded,
                "record_prefix_len": pos,
                "record_hex": raw.hex().upper(),
            }
        )

    report: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []
    for seg, seg_jobs in sorted(jobs.items()):
        bank = bytearray(slice_expansion_bank(candidate, seg))
        cursor = alias_bank_cursor(bytes(bank))
        for job in sorted(seg_jobs, key=lambda row: row["local"]):
            local = int(job["local"])
            old_ptr = struct.unpack_from("<H", bank, local * 2)[0]
            old_end = old_ptr
            if old_ptr >= BANK_SIZE:
                raise BuildError(f"invalid ext3 pointer {seg:02X}:{local:03X}={old_ptr:04X}")
            while old_end < BANK_SIZE and bank[old_end] != 0:
                old_end += 1
            if old_end >= BANK_SIZE:
                raise BuildError(f"unterminated ext3 slot {seg:02X}:{local:03X}")
            old_payload = bytes(bank[old_ptr:old_end])
            encoded = bytes(job["encoded"])
            need = len(encoded) + 1
            if cursor + need > BANK_SIZE:
                raise BuildError(f"ext3 bank {seg:02X} overflow")
            if any(byte != 0xFF for byte in bank[cursor : cursor + need]):
                raise BuildError(f"ext3 bank {seg:02X} tail is not pristine at {cursor:04X}")
            new_ptr = cursor
            bank[new_ptr : new_ptr + len(encoded)] = encoded
            bank[new_ptr + len(encoded)] = 0
            struct.pack_into("<H", bank, local * 2, new_ptr)
            # Expansion-bank file offsets are direct segment offsets in the
            # prepended 8MB half of the 16MB ROM.
            allowed.append((seg * BANK_SIZE + local * 2, seg * BANK_SIZE + local * 2 + 2))
            allowed.append((seg * BANK_SIZE + new_ptr, seg * BANK_SIZE + new_ptr + need))
            cursor += need
            report.append(
                {
                    "abs": f"{job['logical']:06X}",
                    "jp": job["jp"],
                    "ko": job["ko"],
                    "record_prefix_len": job["record_prefix_len"],
                    "record_hex_unchanged": job["record_hex"],
                    "token": job["token"].hex().upper(),
                    "index": f"{job['index']:05X}",
                    "physical_segment": f"{seg:02X}",
                    "physical_local": f"{local:03X}",
                    "old_pointer": f"{old_ptr:04X}",
                    "new_pointer": f"{new_ptr:04X}",
                    "old_payload_len": len(old_payload),
                    "new_payload_len": len(encoded),
                    "new_payload_hex": encoded.hex().upper(),
                }
            )
        patch_expansion_bank(candidate, seg, bank)

    verify = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    for row in report:
        index = int(row["index"], 16)
        rendered = strip_pad(verify.expand_index(index, tbl))
        if rendered != row["ko"]:
            raise BuildError(f"ext3 verify failed {row['abs']}: {rendered!r} != {row['ko']!r}")
        after_raw, _ = payload_at(candidate, int(row["abs"], 16))
        before_raw, _ = payload_at(parent, int(row["abs"], 16))
        if after_raw != before_raw:
            raise BuildError(f"record bytes changed during ext3-only rewrite at {row['abs']}")
    return report, allowed


def duplicate_storage_groups(dictionary: Any) -> list[dict[str, Any]]:
    by_raw: defaultdict[bytes, defaultdict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    for index in range(dictionary.stock_count):
        try:
            raw = bytes(dictionary.raw_entry(index))
            ptr = int(dictionary.ptrs[index])
        except Exception:
            continue
        by_raw[raw][ptr].append(index)
    out: list[dict[str, Any]] = []
    for raw, groups in by_raw.items():
        if len(raw) < 4 or len(groups) < 2:
            continue
        pointers = sorted(groups)
        for victim_ptr in pointers[1:]:
            survivor_ptr = pointers[0]
            interior = [
                idx for idx, value in enumerate(dictionary.ptrs)
                if victim_ptr < int(value) <= victim_ptr + len(raw)
            ]
            if interior:
                continue
            out.append(
                {
                    "raw": raw,
                    "old_len": len(raw),
                    "survivor_ptr": survivor_ptr,
                    "victim_ptr": victim_ptr,
                    "survivor_indices": list(groups[survivor_ptr]),
                    "victim_indices": list(groups[victim_ptr]),
                }
            )
    out.sort(key=lambda row: (-row["old_len"], row["victim_ptr"]))
    return out


def allocate_native_fragments(
    candidate: bytearray,
    parent: bytes,
    tbl: Tbl,
    ext_meta: dict[str, Any],
    ext3_meta: dict[str, Any],
) -> tuple[dict[str, int], list[dict[str, Any]], list[tuple[int, int]]]:
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    safe = {int(row["index"]): row for row in safe_unreachable_slots(parent, dictionary)}
    required_safe = set(NATIVE_FRAGMENT_INDEX.values()) | {CONTIGUOUS_HELPER}
    if not required_safe <= set(safe):
        missing = sorted(required_safe - set(safe))
        raise BuildError(f"native fragment safe slots drifted: {[f'{x:04X}' for x in missing]}")
    raw_hits = _raw_pair_hits(parent, sorted(required_safe))
    if any(raw_hits.get(index) for index in required_safe):
        raise BuildError("native fragment selected slot has raw-pair hits")

    # Existing helper tokens must still render exactly as expected.
    for index, expected in EXISTING_STOCK_TEXT.items():
        got = strip_pad(dictionary.expand_index(index, tbl))
        if got != expected:
            raise BuildError(f"existing stock helper drifted {index:04X}: {got!r}")

    fragments = {text: encode(text, tbl) for text in NATIVE_FRAGMENT_INDEX}
    groups = duplicate_storage_groups(dictionary)
    large = [row for row in groups if row["old_len"] >= 5]
    # The parent is expected to expose exactly one 9-byte and two 8-byte
    # duplicate physical payloads.  These are semantic no-op reclaim extents.
    if [row["old_len"] for row in large[:3]] != [9, 8, 8]:
        raise BuildError(f"duplicate stock reclaim population drifted: {[r['old_len'] for r in large[:6]]}")

    assignments = [
        ("해냈나", NATIVE_FRAGMENT_INDEX["해냈나"], large[0]),
        ("남자가", NATIVE_FRAGMENT_INDEX["남자가"], large[1]),
        ("힘？", NATIVE_FRAGMENT_INDEX["힘？"], large[2]),
    ]
    allowed: list[tuple[int, int]] = []
    report: list[dict[str, Any]] = []

    for text, selected_index, storage in assignments:
        encoded = fragments[text]
        if len(encoded) > int(storage["old_len"]):
            raise BuildError(f"duplicate reclaim too small for {text!r}")
        current = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        before_survivor = strip_pad(current.expand_index(storage["survivor_indices"][0], tbl))
        before_victim = strip_pad(current.expand_index(storage["victim_indices"][0], tbl))
        if before_survivor != before_victim:
            raise BuildError("duplicate payload render disagreement")
        for victim_index in storage["victim_indices"]:
            ptr_at = current.ptr_file + victim_index * 2
            write_le16(candidate, ptr_at, int(storage["survivor_ptr"]))
            allowed.append((ptr_at, ptr_at + 2))
        ptr_at = current.ptr_file + selected_index * 2
        write_le16(candidate, ptr_at, int(storage["victim_ptr"]))
        allowed.append((ptr_at, ptr_at + 2))
        dst = current.base + int(storage["victim_ptr"])
        span = int(storage["old_len"]) + 1
        candidate[dst : dst + span] = encoded + b"\x00" + b"\xFF" * (int(storage["old_len"]) - len(encoded))
        allowed.append((dst, dst + span))
        after = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        if strip_pad(after.expand_index(selected_index, tbl)) != text:
            raise BuildError(f"native fragment verify failed {text!r}")
        for victim_index in storage["victim_indices"]:
            if strip_pad(after.expand_index(victim_index, tbl)) != before_victim:
                raise BuildError(f"duplicate victim semantic drift {victim_index:04X}")
        report.append(
            {
                "text": text,
                "selected_index": f"{selected_index:04X}",
                "encoded_hex": encoded.hex().upper(),
                "storage_mode": "duplicate_payload_reclaim",
                "old_payload_render": before_victim,
                "old_len": storage["old_len"],
                "survivor_ptr": f"{storage['survivor_ptr']:04X}",
                "victim_ptr": f"{storage['victim_ptr']:04X}",
                "victim_indices": [f"{x:04X}" for x in storage["victim_indices"]],
            }
        )

    # Reclaim the contiguous, fully unreachable 07BD + 07BE physical span for
    # the one-syllable fragment "맞".  07BE becomes an unused alias of 07BD.
    current = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    first = safe[NATIVE_FRAGMENT_INDEX["맞"]]
    second = safe[CONTIGUOUS_HELPER]
    first_ptr = int(first["ptr"], 16)
    second_ptr = int(second["ptr"], 16)
    first_len = int(first["old_len"])
    second_len = int(second["old_len"])
    if first_ptr + first_len + 1 != second_ptr:
        raise BuildError("07BD/07BE contiguous reclaim contract drifted")
    span = first_len + 1 + second_len + 1
    encoded = fragments["맞"]
    if len(encoded) + 1 > span:
        raise BuildError("contiguous stock reclaim too small")
    ptr_at = current.ptr_file + CONTIGUOUS_HELPER * 2
    write_le16(candidate, ptr_at, first_ptr)
    allowed.append((ptr_at, ptr_at + 2))
    dst = current.base + first_ptr
    candidate[dst : dst + span] = encoded + b"\x00" + b"\xFF" * (span - len(encoded) - 1)
    allowed.append((dst, dst + span))
    after = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    if strip_pad(after.expand_index(NATIVE_FRAGMENT_INDEX["맞"], tbl)) != "맞":
        raise BuildError("contiguous native fragment verify failed")
    if int(after.ptrs[CONTIGUOUS_HELPER]) != first_ptr:
        raise BuildError("contiguous helper pointer did not alias selected slot")
    report.append(
        {
            "text": "맞",
            "selected_index": f"{NATIVE_FRAGMENT_INDEX['맞']:04X}",
            "encoded_hex": encoded.hex().upper(),
            "storage_mode": "contiguous_unreachable_pair_reclaim",
            "span_start_ptr": f"{first_ptr:04X}",
            "span_end_ptr": f"{first_ptr + span:04X}",
            "helper_index_repointed": f"{CONTIGUOUS_HELPER:04X}",
        }
    )
    return dict(NATIVE_FRAGMENT_INDEX), report, allowed


def replace_same_extent(candidate: bytearray, logical: int, new_payload: bytes) -> tuple[dict[str, Any], tuple[int, int]]:
    before, term = payload_at(candidate, logical)
    if len(new_payload) > len(before):
        raise BuildError(f"record overflow {logical:06X}: {len(new_payload)} > {len(before)}")
    sb = stock_base(candidate)
    start = sb + logical
    candidate[start : start + len(before)] = new_payload + b"\x01" * (len(before) - len(new_payload))
    after, after_term = payload_at(candidate, logical)
    if after_term != term:
        raise BuildError(f"terminator moved at {logical:06X}")
    return (
        {
            "abs": f"{logical:06X}",
            "before_hex": before.hex().upper(),
            "after_hex": after.hex().upper(),
            "terminator": f"{term:06X}",
            "capacity": len(before),
        },
        (start, start + len(before)),
    )


def patch_native_records(
    candidate: bytearray,
    parent: bytes,
    original: bytes,
    tbl: Tbl,
    ext_meta: dict[str, Any],
    ext3_meta: dict[str, Any],
    native_spec: list[dict[str, Any]],
    fragment_index: dict[str, int],
) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    expected = {int(row["abs"], 16): row for row in native_spec}
    if set(expected) != {0x623DC6, 0x623DD7, 0x624271}:
        raise BuildError("native-route spec population drifted")
    original_dictionary = Dictionary(original)
    original_tbl = tbl
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)

    for logical, row in expected.items():
        original_raw, _ = payload_at(original, logical)
        if not original_raw.startswith(bytes.fromhex("173418")):
            raise BuildError(f"native target original prefix drifted {logical:06X}")
        source = original_dictionary.expand(original_raw[3:], original_tbl)
        if source != row["jp"]:
            raise BuildError(f"native target JP drift {logical:06X}: {source!r}")
        parent_raw, _ = payload_at(parent, logical)
        if not parent_raw.startswith(bytes.fromhex("173418E518")):
            raise BuildError(f"native target parent route drifted {logical:06X}: {parent_raw.hex().upper()}")

    # Keep the original 17 34 18 scenario prefix.  The bodies below contain no
    # E5 18 portal and therefore stay on the ordinary native stock iteration path.
    payloads = {
        0x623DC6: bytes.fromhex("173418")
        + token_from_dict_index(fragment_index["해냈나"])
        + token_from_dict_index(0x060C),
        0x623DD7: bytes.fromhex("173418")
        + token_from_dict_index(0x0191)
        + token_from_dict_index(0x0171)
        + b"\x01"
        + token_from_dict_index(fragment_index["남자가"])
        + b"\x01"
        + token_from_dict_index(fragment_index["맞"])
        + token_from_dict_index(0x005C),
        0x624271: bytes.fromhex("173418")
        + token_from_dict_index(0x0191)
        + token_from_dict_index(fragment_index["힘？"]),
    }

    report: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []
    for logical in sorted(payloads):
        row, extent = replace_same_extent(candidate, logical, payloads[logical])
        allowed.append(extent)
        dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        after_raw, _ = payload_at(candidate, logical)
        rendered = strip_pad(dictionary.expand(after_raw[3:], tbl))
        target = str(expected[logical]["ko"])
        if rendered != target:
            raise BuildError(f"native render failed {logical:06X}: {rendered!r} != {target!r}")
        if b"\xE5\x18" in after_raw[3:]:
            raise BuildError(f"E5 18 remained in native target {logical:06X}")
        row.update({"jp": expected[logical]["jp"], "ko": target, "rendered": rendered})
        report.append(row)

    # The parent and candidate must keep the event/portrait-control windows
    # byte-exact.  Only the preceding text record bodies are permitted to change.
    sbp = stock_base(parent)
    sbc = stock_base(candidate)
    for lo, hi in CONTROL_WINDOWS:
        if parent[sbp + lo : sbp + hi] != candidate[sbc + lo : sbc + hi]:
            raise BuildError(f"control window changed {lo:06X}-{hi:06X}")

    # The citizen continuation and the post-control first line remain the same
    # record bytes; only their private ext3 dictionary payloads may change.
    for logical in (0x623DE8, 0x623E00, 0x62427D, 0x62428D):
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(candidate, logical)
        if before != after or before_term != after_term:
            raise BuildError(f"neighbor record bytes drifted at {logical:06X}")

    # Snapshot helper renders are still stable after stock-pointer changes.
    final_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    for index, expected_text in EXISTING_STOCK_TEXT.items():
        if strip_pad(final_dictionary.expand_index(index, tbl)) != expected_text:
            raise BuildError(f"stock helper changed after native patch {index:04X}")
    return report, allowed


def main() -> int:
    parent = PARENT.read_bytes()
    tbl_bytes = PARENT_TBL.read_bytes()
    save = PARENT_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent candidate identity drifted: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError("parent candidate TBL identity drifted")
    if len(save) != SAVE_SIZE or sha(save) != EXPECTED_SAVE_SHA:
        raise BuildError("parent candidate SaveRAM identity drifted")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("stage") != "STAGE16t" or spec.get("review_status") != "user_reported_candidate":
        raise BuildError("STAGE16t spec status drifted")

    tbl = Tbl.load(PARENT_TBL)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    original_dictionary = Dictionary(original)
    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # Rewrite wording first.  The actual scenario records remain byte-exact.
    ext3_rows, ext3_allowed = rewrite_ext3_by_address(
        candidate,
        parent,
        original,
        tbl,
        original_dictionary,
        ext_meta,
        ext3_meta,
        list(spec["ext3_rewrites"]),
    )
    allowed.extend(ext3_allowed)

    # Allocate four ordinary stock fragments without growing the exhausted
    # stock tail, then bind the three runtime-sensitive records to native grammar.
    fragments, fragment_rows, fragment_allowed = allocate_native_fragments(
        candidate, parent, tbl, ext_meta, ext3_meta
    )
    allowed.extend(fragment_allowed)
    native_rows, native_allowed = patch_native_records(
        candidate,
        parent,
        original,
        tbl,
        ext_meta,
        ext3_meta,
        list(spec["native_route"]),
        fragments,
    )
    allowed.extend(native_allowed)

    # Focused semantic verification of every requested text target.
    final_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    rendered: dict[str, str] = {}
    for row in spec["ext3_rewrites"]:
        logical = int(row["abs"], 16)
        raw, _ = payload_at(candidate, logical)
        pos = raw.find(b"\xE5\x18")
        text = strip_pad(final_dictionary.expand(raw[pos:], tbl))
        if text != row["ko"]:
            raise BuildError(f"final ext3 record render mismatch {logical:06X}: {text!r}")
        rendered[row["abs"]] = text
    for row in native_rows:
        rendered[row["abs"]] = row["rendered"]

    # Explicit user-observed regressions must be absent from final rendered text.
    forbidden_visible = (
        "생생한\u3000감정이나\u3000드러내선\u3000속물은",
        "ＳＵｂｅ는",
        "인신공양",
    )
    if any(bad in value for bad in forbidden_visible for value in rendered.values()):
        raise BuildError("user-reported stale wording remains in focused render set")
    if rendered.get("623A8C") == rendered.get("623A9C"):
        raise BuildError("Scirocco duplicate-line regression check failed")

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:10]}")
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum verification failed")

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_TBL, tbl_bytes)
    atomic_bytes(OUT_SAVE, save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage16t_event_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified",
        "promotion_performed": False,
        "stage": "STAGE16t",
        "inputs": {
            "parent_candidate": identity(PARENT, parent),
            "parent_tbl": identity(PARENT_TBL, tbl_bytes),
            "parent_saveram": identity(PARENT_SAVE, save),
            "spec": identity(SPEC),
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, result),
            "candidate_tbl": identity(OUT_TBL, tbl_bytes),
            "candidate_saveram": identity(OUT_SAVE, save),
        },
        "changes": {
            "ext3_rewrites": ext3_rows,
            "native_fragment_storage": fragment_rows,
            "native_route_records": native_rows,
        },
        "focused_render": rendered,
        "checks": {
            "live_main_untouched": True,
            "parent_candidate_untouched": PARENT.read_bytes() == parent,
            "parent_tbl_untouched": PARENT_TBL.read_bytes() == tbl_bytes,
            "candidate_tbl_exact_parent": OUT_TBL.read_bytes() == tbl_bytes,
            "candidate_saveram_exact_parent": OUT_SAVE.read_bytes() == save,
            "control_windows_byte_exact": True,
            "neighbor_record_bytes_byte_exact": True,
            "native_targets_have_no_e518": True,
            "all_text_targets_max_20_cells": max(len(str(row["ko"])) for row in spec["ext3_rewrites"] + spec["native_route"]) <= 20,
            "diff_allowlist_clean": not unexpected,
            "ws_checksum_valid": True,
        },
        "diff": {
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "changed_runs": len(runs),
            "unexpected_runs": len(unexpected),
            "runs": [
                {"start": f"{lo:07X}", "end": f"{hi:07X}", "length": hi - lo}
                for lo, hi in runs
            ],
        },
        "ws_checksum": f"{checksum:04X}",
        "runtime_validation_targets": [
            "623A8C/623A9C: 속인/속물 중복 없이 2행이 자연스럽게 이어지는지",
            "623DC6: 해냈나……！ 뒤에 がけ/제어문이 노출되지 않는지",
            "623DD7-623E00: 시민 3개 대사가 한 번만 진행되는지",
            "623EB9-624107: Sube/인신공양 MT 잔재 없이 희생양 문맥이 자연스러운지",
            "624271: ……힘？ 뒤에 は？가 노출되지 않는지",
            "62427D/62428D: 카테지나 루스 초상이 유지되고 시그 초상으로 바뀌지 않는지",
        ],
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["outputs"]["candidate_rom"],
        "ext3_rewrites": len(ext3_rows),
        "native_routes": len(native_rows),
        "native_fragments": len(fragment_rows),
        "diff": report["diff"],
        "checksum": report["ws_checksum"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
