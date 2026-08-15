#!/usr/bin/env python3
"""Build a focused native-stock candidate for the remaining 2026-08-09 runtime captures.

The separate Oita/machine-translation sample is intentionally excluded per the
user's request.  This builder changes only the runtime-measured dialogue records,
private unreachable stock-dictionary storage used by those records, and the
WonderSwan checksum.  All target record extents and NUL terminators are fixed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3
from build_scenario_page_boundary_guard_candidate import stock_text_map
from build_sig_scenario_stock_native_chain_candidate import current_ext3_nested_parents, current_nested_parents
from build_terminology_retranslation_candidate import stock_storage_proof
from extract_script import split_prefix_body
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import (
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
SPEC_PATH = ROOT / "data/runtime_measured_followup_20260809_ko.json"
OUT_ROM = ROOT / "out/patch/runtime_measured_followup_20260809_candidate.wsc"
OUT_SAVE = ROOT / "sram/runtime_measured_followup_20260809_candidate.sav"
REPORT = ROOT / "out/patch/runtime_measured_followup_20260809_candidate_report.json"

EXPECTED_MAIN_SHA = "fb6629d89cfd0dd8f48a621af5c0175d4602f113abec5d781b54b32b14efa86d"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
HANGUL_MARKER = 0xEC8D
SPACE_TEXT = "　"
EXT_META = {
    "stock_count": 3831,
    "slot_count": 265,
    "ext_ptr_off": "0000",
    "ext_seg": "10",
    "ext_in_expansion": True,
}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}

EXPECTED_CURRENT = {
    0x59976D: "171C18F732F35303",
    0x59984F: "171C18FA9D09F044",
    0x59987B: "173418E51808980101",
    0x599977: "FA8517FA9DE0B5E0B5F044",
    0x5999A6: "173418E51808980101",
    0x6226BE: "18E5184C13010101010101010101010101",
    0x622832: "173418E5181A710101010101",
    0x622848: "173418E518F2A1",
    0x622850: "E518208C010101010101010101",
    0x67AF01: "E0CA103F2C107010",
    0x67C0EC: "E0CA103F2C107010",
    0x693D54: "F24403",
    0x63E6E4: "173418E5184D40",
    0x63EB4A: "173418E5184966",
    0x63F0BD: "173418E518F2A6",
    0x63F483: "173418E5181C41",
    0x63F67C: "173418E5184831",
}

# Every target is expressed entirely with ordinary 2-byte stock tokens.  The
# standalone full-width-space token is an existing live stock entry and is not
# repurposed.  All other fragments are stored in newly proven unreachable slots.
NATIVE_PLANS = {
    0x59976D: ["가아앗", "！"],
    0x59984F: ["최종오의", "！！"],
    0x59987B: ["천경궈어어언", "！！", "！"],
    0x599977: ["동방불패", "！", SPACE_TEXT, "최종오의이이", "！！"],
    0x5999A6: ["천경궈어어언", "！！", "！"],
    0x6226BE: ["더　이상의　증식을", SPACE_TEXT, "허용하지　마！！"],
    0x622832: ["……", "하", "아앗！", "！"],
    0x622848: ["흥……　이제야", "　좀　전사의"],
    0x622850: ["낯빛으로　각오를", "　다진　것　같구만。"],
    0x67AF01: ["게임　오버"],
    0x67C0EC: ["게임　오버"],
    0x693D54: ["오오！"],
    0x63E6E4: ["잘　들어", "！！"],
    0x63EB4A: ["죄송합니다", "……"],
    0x63F0BD: ["흠", "……"],
    0x63F483: ["제로", "……"],
    0x63F67C: ["윽", "……！"],
}

EXACT_NATIVE_TWO_TOKEN = {0x63E6E4, 0x63EB4A, 0x63F0BD, 0x63F483, 0x63F67C}

UNCHANGED_CONTEXT = (0x599841, 0x599864, 0x59996D)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def encode_phrase(text: str, tbl: Tbl) -> bytes:
    encoded = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=HANGUL_MARKER,
        hangul_marker_mode="run",
    )
    if encoded is None or not encoded or b"\x00" in encoded:
        raise BuildError(f"cannot encode target phrase: {text!r}")
    return bytes(encoded)


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable zstring at {logical:06X}")
    return bytes(got[0]), int(got[1])


def stripped(text: str) -> str:
    return text.rstrip("　 \t")


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= ch <= "\u30ff"
        or "\u3400" <= ch <= "\u4dbf"
        or "\u4e00" <= ch <= "\u9fff"
        for ch in text
    )


def load_spec() -> dict[int, dict[str, Any]]:
    doc = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    rows: dict[int, dict[str, Any]] = {}
    for row in doc.get("entries") or []:
        logical = int(str(row["abs"]), 16)
        rows[logical] = dict(row)
    if set(rows) != set(EXPECTED_CURRENT):
        raise BuildError(
            "spec population drifted; Oita pair must stay excluded and only the runtime-measured follow-up captures are allowed: "
            + ",".join(f"{x:06X}" for x in sorted(rows))
        )
    return rows


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    if tbl.code_to_char.get(HANGUL_MARKER) != "":
        raise BuildError("installed EC8D Hangul marker is not present in pad3 table")
    ext_meta = EXT_META
    ext3_meta = EXT3_META
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    d_original = Dictionary(original)
    sb = stock_base(parent)
    spec = load_spec()

    # Verify source provenance, current exact bytes, prefixes, target width, and
    # exact original Japanese before allocating any storage.
    prefixes: dict[int, bytes] = {}
    source_rows: list[dict[str, Any]] = []
    for logical, row in sorted(spec.items()):
        current, current_term = payload_at(parent, logical)
        expected = bytes.fromhex(EXPECTED_CURRENT[logical])
        if current != expected:
            raise BuildError(f"current payload drift {logical:06X}: {current.hex().upper()}")
        original_payload, _ = payload_at(original, logical)
        prefix, original_body, kind = split_prefix_body(original_payload)
        source_text = stripped(d_original.expand(original_body, tbl))
        if source_text != str(row["jp"]):
            raise BuildError(
                f"original source mismatch {logical:06X}: {source_text!r} != {row['jp']!r}"
            )
        if logical in EXACT_NATIVE_TWO_TOKEN:
            source_exact_two = (
                len(original_body) == 4
                and 0xF0 <= original_body[0] <= 0xFE
                and 0xF0 <= original_body[2] <= 0xFE
            )
            if not source_exact_two:
                raise BuildError(
                    f"source exact native-two-token grammar drift {logical:06X}: "
                    f"{original_body.hex().upper()}"
                )
        if not current.startswith(prefix):
            raise BuildError(f"current prefix drift {logical:06X}: {prefix.hex().upper()}")
        target = str(row["ko"])
        if len(target) > 20:
            raise BuildError(f"target exceeds 20 cells {logical:06X}: {len(target)} {target!r}")
        if has_japanese(target):
            raise BuildError(f"Japanese residual in target {logical:06X}: {target!r}")
        prefixes[logical] = prefix
        source_rows.append({
            "abs": f"{logical:06X}",
            "kind": kind,
            "prefix_hex": prefix.hex().upper(),
            "body_capacity": len(current) - len(prefix),
            "terminator": f"{current_term - sb:06X}",
            "source_jp": source_text,
            "target_ko": target,
        })

    stock_map = stock_text_map(d_parent, tbl)
    space_indices = stock_map.get(SPACE_TEXT) or []
    if not space_indices:
        raise BuildError("existing full-width-space stock token disappeared")
    space_index = int(space_indices[0])

    all_parts = list(dict.fromkeys(part for parts in NATIVE_PLANS.values() for part in parts))
    existing_parts: dict[str, int] = {}
    for part in all_parts:
        values = stock_map.get(part) or []
        if values:
            existing_parts[part] = int(values[0])
    if existing_parts.get(SPACE_TEXT) != space_index:
        raise BuildError("space token resolution drifted")

    fragments = [part for part in all_parts if part not in existing_parts]
    fragment_encoded = {text: encode_phrase(text, tbl) for text in fragments}

    # The current TIP has no stock slots that are unused in both pristine and
    # working ROMs, because the newly promoted Domon batch exhausted that strict
    # union-free class.  Follow the already proven strong-retired policy used by
    # the scenario native-chain repairs: original-only historical consumers are
    # allowed, but *current working* consumers and all current nested parents
    # must be zero.  Storage must also have one unique dictionary pointer and no
    # interior pointer.
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    watched = {
        index for index in range(min(int(d_parent.stock_count), 0x0F00))
        if dict_token_safe_in_zstring(index)
    }
    nested = current_nested_parents(d_parent, watched)
    ext3_nested = current_ext3_nested_parents(d_parent, watched)
    safe_pool: list[dict[str, Any]] = []
    for index in sorted(watched):
        working_consumers = [c for c in union.consumers_for(index) if "working" in c.seen_in]
        if working_consumers or nested[index] or ext3_nested[index]:
            continue
        proof = stock_storage_proof(d_parent, index)
        if not proof["ok"]:
            continue
        original_only = [c for c in union.consumers_for(index) if "working" not in c.seen_in]
        safe_pool.append({
            "index": index,
            "old_len": int(proof["old_len"]),
            "proof": proof,
            "original_only_consumers": len(original_only),
        })
    if not safe_pool:
        raise BuildError("no strong-retired stock storage remains")

    existing_indices = set(existing_parts.values())
    used_slots: set[int] = set()
    fragment_slots: dict[str, int] = {}
    for text in sorted(fragments, key=lambda value: (-len(fragment_encoded[value]), value)):
        encoded = fragment_encoded[text]
        candidates = [
            row for row in safe_pool
            if int(row["index"]) not in used_slots
            and int(row["index"]) not in existing_indices
            and int(row["old_len"]) >= len(encoded)
        ]
        if not candidates:
            raise BuildError(f"no union-proven stock slot for {text!r} ({len(encoded)} bytes)")
        selected = min(candidates, key=lambda row: (int(row["old_len"]), int(row["index"])))
        index = int(selected["index"])
        working_consumers = [c for c in union.consumers_for(index) if "working" in c.seen_in]
        if working_consumers:
            raise BuildError(f"selected stock slot {index:04X} has working consumers")
        used_slots.add(index)
        fragment_slots[text] = index

    candidate = bytearray(parent)
    allowed_offsets: set[int] = set()
    stock_rows: list[dict[str, Any]] = []
    for text in sorted(fragment_slots, key=lambda value: fragment_slots[value]):
        index = fragment_slots[text]
        encoded = fragment_encoded[text]
        current_dict = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        proof = stock_storage_proof(current_dict, index)
        if not proof["ok"] or len(encoded) > int(proof["old_len"]):
            raise BuildError(f"stock slot {index:04X} became unsafe: {proof}")
        start = int(proof["entry_abs"])
        old_len = int(proof["old_len"])
        before_text = stripped(current_dict.expand_index(index, tbl))
        candidate[start:start + len(encoded)] = encoded
        candidate[start + len(encoded)] = 0
        allowed_offsets.update(range(start, start + old_len + 1))
        check_dict = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        after_text = stripped(check_dict.expand_index(index, tbl))
        if after_text != text:
            raise BuildError(f"stock slot verify failed {index:04X}: {after_text!r} != {text!r}")
        selected_meta = next(row for row in safe_pool if int(row["index"]) == index)
        stock_rows.append({
            "index": f"{index:04X}",
            "token": token_from_dict_index(index).hex().upper(),
            "before": before_text,
            "after": after_text,
            "encoded_len": len(encoded),
            "old_len": old_len,
            "working_external_consumers_before": 0,
            "current_native_nested_parents_before": 0,
            "current_ext3_nested_parents_before": 0,
            "original_only_consumers": int(selected_meta["original_only_consumers"]),
        })

    # Record rewrite: ordinary stock tokens only, same payload size, same NUL.
    patch_rows: list[dict[str, Any]] = []
    for logical in sorted(spec):
        old, old_term = payload_at(parent, logical)
        prefix = prefixes[logical]
        capacity = len(old) - len(prefix)
        indices: list[int] = []
        for part in NATIVE_PLANS[logical]:
            indices.append(existing_parts[part] if part in existing_parts else fragment_slots[part])
        body = b"".join(token_from_dict_index(index) for index in indices)
        if logical in EXACT_NATIVE_TWO_TOKEN and (len(indices) != 2 or len(body) != 4):
            raise BuildError(
                f"exact native-two-token restoration drift {logical:06X}: "
                f"indices={indices} body={body.hex().upper()}"
            )
        if len(body) > capacity:
            raise BuildError(
                f"native plan does not fit {logical:06X}: {len(body)} > {capacity} ({indices})"
            )
        new = prefix + body + b"\x01" * (capacity - len(body))
        start = sb + logical
        candidate[start:start + len(old)] = new
        allowed_offsets.update(range(start, start + len(old)))
        check, check_term = payload_at(candidate, logical)
        if check != new or check_term != old_term:
            raise BuildError(f"record boundary drift {logical:06X}")
        patch_rows.append({
            "abs": f"{logical:06X}",
            "route": "ordinary_native_stock_only",
            "prefix_hex": prefix.hex().upper(),
            "before_hex": old.hex().upper(),
            "after_hex": new.hex().upper(),
            "terminator": f"{old_term - sb:06X}",
            "stock_indices": [f"{index:04X}" for index in indices],
        })

    # Re-scan the candidate reference graph.  Each newly repurposed slot must
    # be consumed only by the explicitly planned target records and must not
    # become nested inside any dictionary phrase through byte-pattern aliasing.
    selected_indices = set(fragment_slots.values())
    d_candidate_pre = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    post_native_nested = current_nested_parents(d_candidate_pre, selected_indices)
    post_ext3_nested = current_ext3_nested_parents(d_candidate_pre, selected_indices)
    if any(post_native_nested[index] or post_ext3_nested[index] for index in selected_indices):
        raise BuildError("new stock slot became a nested dictionary dependency")
    expected_consumers: dict[int, set[int]] = {index: set() for index in selected_indices}
    for logical, parts in NATIVE_PLANS.items():
        for part in parts:
            if part in fragment_slots:
                expected_consumers[fragment_slots[part]].add(logical)
    candidate_union = build_reference_union(
        original, bytes(candidate), ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    candidate_consumer_rows: list[dict[str, Any]] = []
    for index in sorted(selected_indices):
        working = [c for c in candidate_union.consumers_for(index) if "working" in c.seen_in]
        actual = {int(c.abs) for c in working}
        if actual != expected_consumers[index]:
            raise BuildError(
                f"candidate stock consumer drift {index:04X}: actual="
                f"{sorted(f'{x:06X}' for x in actual)} expected="
                f"{sorted(f'{x:06X}' for x in expected_consumers[index])}"
            )
        candidate_consumer_rows.append({
            "index": f"{index:04X}",
            "working_consumers": [f"{x:06X}" for x in sorted(actual)],
            "native_nested_parents": 0,
            "ext3_nested_parents": 0,
        })

    checksum = update_ws_checksum(candidate)
    allowed_offsets.update({len(candidate) - 2, len(candidate) - 1})

    d_candidate = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    render_rows: list[dict[str, Any]] = []
    for logical in sorted(spec):
        payload, term = payload_at(candidate, logical)
        prefix = prefixes[logical]
        rendered = stripped(d_candidate.expand(payload[len(prefix):], tbl))
        target = str(spec[logical]["ko"])
        if rendered != target:
            raise BuildError(f"render mismatch {logical:06X}: {rendered!r} != {target!r}")
        if has_japanese(rendered):
            raise BuildError(f"Japanese residual after patch {logical:06X}: {rendered!r}")
        render_rows.append({
            "abs": f"{logical:06X}",
            "rendered": rendered,
            "visible_cells": len(rendered),
            "terminator": f"{term - sb:06X}",
        })

    # Explicitly prove nearby technique-chain/context records stayed byte-exact.
    context_rows: list[dict[str, Any]] = []
    for logical in UNCHANGED_CONTEXT:
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(candidate, logical)
        if before != after or before_term != after_term:
            raise BuildError(f"unchanged context moved {logical:06X}")
        original_payload, _ = payload_at(original, logical)
        prefix, _, _ = split_prefix_body(original_payload)
        body = after[len(prefix):] if after.startswith(prefix) else after
        text = stripped(d_candidate.expand(body, tbl))
        context_rows.append({"abs": f"{logical:06X}", "rendered": text, "byte_exact": True})

    changed = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    outside = [i for i in changed if i not in allowed_offsets]
    if outside:
        raise BuildError(f"diff escaped scope: {[f'{x:08X}' for x in outside[:12]]}")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_runtime_measured_followup_20260809_candidate.py",
        "status": "candidate_pending_user_runtime_validation",
        "scope_note": "63CF7C/63CF8A Oita/machine-translation sample excluded per user request",
        "parent": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(parent)},
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "sha256": sha(candidate),
            "size": len(candidate),
            "checksum": f"{checksum:04X}",
        },
        "saveram": {
            "path": str(OUT_SAVE.relative_to(ROOT)),
            "sha256": sha(OUT_SAVE.read_bytes()),
            "size": OUT_SAVE.stat().st_size,
        },
        "source_rows": source_rows,
        "patches": patch_rows,
        "renders": render_rows,
        "unchanged_context": context_rows,
        "existing_stock_parts": {text: f"{index:04X}" for text, index in sorted(existing_parts.items())},
        "new_stock_slots": stock_rows,
        "candidate_stock_consumer_proof": candidate_consumer_rows,
        "safe_pool_count": len(safe_pool),
        "strong_retired_pool_count": len(safe_pool),
        "diff": {"changed_bytes": len(changed), "outside_declared_scope": len(outside)},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "candidate": report["candidate"],
        "renders": render_rows,
        "unchanged_context": context_rows,
        "new_stock_slots": stock_rows,
        "diff": report["diff"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
