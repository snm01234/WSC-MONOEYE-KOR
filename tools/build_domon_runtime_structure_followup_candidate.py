#!/usr/bin/env python3
"""Build a focused runtime-structure candidate for two Domon/Master Asia leaks.

Scope is intentionally limited to the two current-TIP runtime defects reported
by the user:

* 626102: bare continuation exposes Japanese ``こ`` before the Korean line.
* 62663E: ``오우！！`` is followed by a bogus Japanese line (``がけはう``).

Both records are currently represented by E5 18 ext3 portals.  The pristine
ROM shows native code-unit grammars on the same record extents.  This candidate
keeps the current Korean wording but restores those pristine grammar shapes
using ordinary stock-dictionary tokens only.  Record extents, NUL terminators,
all surrounding control bytes, the main TIP, and live SaveRAM remain unchanged.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_scenario_page_boundary_guard_candidate import (  # noqa: E402
    original_unit_kinds,
    safe_unreachable_slots,
    stock_text_map,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/domon_runtime_structure_followup_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT_ROM = ROOT / "out/patch/domon_runtime_structure_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/domon_runtime_structure_followup_candidate.sav"
REPORT = ROOT / "out/patch/domon_runtime_structure_followup_candidate_report.json"

EXPECTED_MAIN_SHA256 = "2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483"
EXPECTED_SAVE_SHA256 = "68692a06497483f1f3a92f21cc51ce2f4c91f58f344d721072a8a2cd6eaecfe1"
EXPECTED_ORIGINAL_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768

EXT_META = {
    "stock_count": 3831,
    "slot_count": 265,
    "ext_ptr_off": "0000",
    "ext_seg": "10",
    "ext_in_expansion": True,
}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}

EXPECTED_CURRENT = {
    0x626102: bytes.fromhex("E518D55601010101010101"),
    0x62663E: bytes.fromhex("173418E5181CF8"),
}
EXPECTED_ORIGINAL = {
    0x626102: bytes.fromhex("F36207F3B7F2E7F879F044"),
    0x62663E: bytes.fromhex("173418F8A6F044"),
}
EXPECTED_TERMINATORS = {
    0x626102: 0x62610D,
    0x62663E: 0x626645,
}
EXPECTED_SELECTED_SLOTS = {0x024B, 0x00CF, 0x00FD, 0x013E}
HANGUL_MARKER = 0xEC8D

# Preserve the pristine code-unit grammar, not merely the byte length.
# 0x01 is the game's ordinary full-width visible space.
PLANS: dict[int, list[tuple[str, str | int]]] = {
    0x626102: [
        ("dict", "이"),
        ("char1", 0x01),
        ("dict", "바보"),
        ("dict", "　제자"),
        ("dict", "가"),
        ("dict", "！！"),
    ],
    0x62663E: [
        ("dict", "오우"),
        ("dict", "！！"),
    ],
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def read_record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record at {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def stripped(text: str) -> str:
    return text.rstrip("　 \t")


def encode_stock_text(tbl: Tbl, text: str) -> bytes:
    encoded = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=HANGUL_MARKER,
        hangul_marker_mode="run",
    )
    if encoded is None or not encoded or b"\x00" in encoded:
        raise BuildError(f"cannot EC8D-marker encode stock phrase: {text!r}")
    return bytes(encoded)


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if len(original) != ORIGINAL_SIZE or sha(original) != EXPECTED_ORIGINAL_SHA256:
        raise BuildError("pristine Japanese ROM identity drifted")
    if len(save) != SAVE_SIZE or sha(save) != EXPECTED_SAVE_SHA256:
        raise BuildError("live SaveRAM identity drifted")

    spec_doc = json.loads(SPEC.read_text(encoding="utf-8"))
    if str((spec_doc.get("provenance") or {}).get("parent_tip_sha256", "")).lower() != EXPECTED_MAIN_SHA256:
        raise BuildError("spec is not bound to the current TIP")
    spec_rows = {int(str(row["abs"]), 16): dict(row) for row in spec_doc.get("entries") or []}
    if set(spec_rows) != set(PLANS):
        raise BuildError("focused spec population drifted")

    tbl = Tbl.load(TBL_PATH)
    if (
        tbl.code_to_char.get(0x01) != "　"
        or tbl.code_to_char.get(0x18) != "こ"
        or tbl.code_to_char.get(HANGUL_MARKER) != ""
    ):
        raise BuildError("TBL structural/EC8D marker mapping drifted")
    dictionary = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    stock = stock_text_map(dictionary, tbl)
    sb = stock_base(parent)

    # Bind every target to the exact current payload, pristine Japanese payload,
    # prefix/body split, terminator, current Korean wording, and source grammar.
    source_rows: list[dict[str, Any]] = []
    for logical in sorted(PLANS):
        current, current_term = read_record(parent, logical)
        pristine, pristine_term = read_record(original, logical)
        if current != EXPECTED_CURRENT[logical]:
            raise BuildError(f"current payload drift at {logical:06X}: {current.hex().upper()}")
        if pristine != EXPECTED_ORIGINAL[logical]:
            raise BuildError(f"pristine payload drift at {logical:06X}: {pristine.hex().upper()}")
        if current_term != EXPECTED_TERMINATORS[logical] or pristine_term != EXPECTED_TERMINATORS[logical]:
            raise BuildError(f"terminator drift at {logical:06X}")

        current_prefix, current_body, current_kind = split_prefix_body(current)
        pristine_prefix, pristine_body, pristine_kind = split_prefix_body(pristine)
        if current_prefix != pristine_prefix or current_kind != pristine_kind:
            raise BuildError(f"prefix/kind drift at {logical:06X}")
        current_text = stripped(dictionary.expand(current_body, tbl))
        expected_text = str(spec_rows[logical]["ko"])
        if current_text != expected_text:
            raise BuildError(
                f"current semantic text drift at {logical:06X}: {current_text!r} != {expected_text!r}"
            )
        expected_jp = str(spec_rows[logical]["jp"])
        pristine_dictionary = make_dictionary_ext3(original, {}, None)
        pristine_text = stripped(pristine_dictionary.expand(pristine_body, tbl))
        if pristine_text != expected_jp:
            raise BuildError(
                f"pristine Japanese mismatch at {logical:06X}: {pristine_text!r} != {expected_jp!r}"
            )

        plan_kinds = [kind for kind, _value in PLANS[logical]]
        pristine_kinds = original_unit_kinds(pristine_body)
        if plan_kinds != pristine_kinds:
            raise BuildError(
                f"planned grammar does not match pristine at {logical:06X}: {plan_kinds} != {pristine_kinds}"
            )
        if b"\xE5\x18" not in current_body:
            raise BuildError(f"target is no longer an ext3 runtime-risk body at {logical:06X}")
        source_rows.append(
            {
                "abs": f"{logical:06X}",
                "jp": expected_jp,
                "ko": expected_text,
                "prefix_hex": current_prefix.hex().upper(),
                "current_hex": current.hex().upper(),
                "pristine_hex": pristine.hex().upper(),
                "pristine_kinds": pristine_kinds,
                "terminator": f"{current_term:06X}",
                "current_has_e518": True,
            }
        )

    needed_fragments = {
        str(value)
        for plan in PLANS.values()
        for kind, value in plan
        if kind == "dict" and str(value) not in stock
    }
    if needed_fragments != {"오우", "바보", "　제자", "가"}:
        raise BuildError(f"novel fragment set drifted: {sorted(needed_fragments)}")

    safe_pool = safe_unreachable_slots(parent, dictionary)
    if not safe_pool:
        raise BuildError("no safe unreachable stock slots remain")
    existing_indices = {
        int(stock[str(value)][0])
        for plan in PLANS.values()
        for kind, value in plan
        if kind == "dict" and str(value) in stock
    }
    available = [row for row in safe_pool if int(row["index"]) not in existing_indices]
    encoded_fragments = {text: encode_stock_text(tbl, text) for text in needed_fragments}

    assigned: dict[str, dict[str, Any]] = {}
    used_slots: set[int] = set()
    for text in sorted(needed_fragments, key=lambda x: (-len(encoded_fragments[x]), x)):
        encoded = encoded_fragments[text]
        selected = next(
            (
                row
                for row in available
                if int(row["index"]) not in used_slots and int(row["old_len"]) >= len(encoded)
            ),
            None,
        )
        if selected is None:
            raise BuildError(f"no safe slot fits {text!r} ({len(encoded)} bytes)")
        used_slots.add(int(selected["index"]))
        assigned[text] = selected
    if used_slots != EXPECTED_SELECTED_SLOTS:
        raise BuildError(
            "deterministic selected slot set drifted: "
            + ",".join(f"{index:04X}" for index in sorted(used_slots))
        )

    candidate = bytearray(parent)
    allowed_extents: list[tuple[int, int]] = []
    slot_rows: list[dict[str, Any]] = []
    for text, row in sorted(assigned.items(), key=lambda item: int(item[1]["index"])):
        encoded = encoded_fragments[text]
        index = int(row["index"])
        start = int(row["entry_abs"])
        old_len = int(row["old_len"])
        before_text = stripped(dictionary.expand_index(index, tbl))
        candidate[start : start + len(encoded)] = encoded
        candidate[start + len(encoded)] = 0
        allowed_extents.append((start, start + old_len + 1))
        slot_rows.append(
            {
                "index": f"{index:04X}",
                "fragment": text,
                "before": before_text,
                "encoded_hex": encoded.hex().upper(),
                "encoded_len": len(encoded),
                "old_len": old_len,
                "entry_abs": start,
                "old_ptr": row["ptr"],
            }
        )

    def index_for(text: str) -> int:
        if text in assigned:
            return int(assigned[text]["index"])
        values = stock.get(text) or []
        if not values:
            raise BuildError(f"stock fragment disappeared: {text!r}")
        return int(values[0])

    expected_selected_occurrences: dict[int, list[int]] = defaultdict(list)
    patch_rows: list[dict[str, Any]] = []
    for logical in sorted(PLANS):
        current, current_term = read_record(parent, logical)
        prefix, _body_before, _kind = split_prefix_body(current)
        body = bytearray()
        body_offset = 0
        parts: list[dict[str, Any]] = []
        for kind, raw_value in PLANS[logical]:
            if kind == "dict":
                text = str(raw_value)
                index = index_for(text)
                token = token_from_dict_index(index)
                if len(token) != 2 or 0 in token:
                    raise BuildError(f"unsafe native token {index:04X} at {logical:06X}")
                body.extend(token)
                parts.append({"kind": "dict", "text": text, "index": f"{index:04X}", "offset": body_offset})
                if index in used_slots:
                    expected_selected_occurrences[index].append(logical + len(prefix) + body_offset)
                body_offset += 2
            elif kind == "char1":
                value = int(raw_value)
                if value == 0 or value >= 0xF0:
                    raise BuildError(f"unsafe char1 {value:02X} at {logical:06X}")
                body.append(value)
                parts.append(
                    {
                        "kind": "char1",
                        "byte": f"{value:02X}",
                        "text": tbl.code_to_char.get(value, ""),
                        "offset": body_offset,
                    }
                )
                body_offset += 1
            else:
                raise BuildError(f"unknown plan kind {kind!r}")

        after = prefix + bytes(body)
        if len(after) != len(current):
            raise BuildError(
                f"record extent changed at {logical:06X}: {len(after)} != {len(current)}"
            )
        if b"\xE5\x18" in body:
            raise BuildError(f"target body still contains E5 18 at {logical:06X}")
        candidate[sb + logical : sb + logical + len(after)] = after
        allowed_extents.append((sb + logical, sb + logical + len(after)))
        patch_rows.append(
            {
                "abs": f"{logical:06X}",
                "before_hex": current.hex().upper(),
                "after_hex": after.hex().upper(),
                "payload_len": len(after),
                "terminator": f"{current_term:06X}",
                "parts": parts,
                "strategy": "pristine_native_code_unit_grammar_current_korean_text",
            }
        )

    checksum = update_ws_checksum(candidate)
    allowed_extents.append((len(candidate) - 2, len(candidate)))
    candidate_bytes = bytes(candidate)
    final_dictionary = make_dictionary_ext3(candidate_bytes, EXT_META, EXT3_META)

    # Raw marker-aware stock phrase verification.
    for text, row in assigned.items():
        index = int(row["index"])
        expected_raw = encoded_fragments[text]
        actual_raw = bytes(final_dictionary.raw_entry(index))
        if actual_raw != expected_raw:
            raise BuildError(
                f"marker-aware stock raw mismatch {index:04X}: {actual_raw.hex()} != {expected_raw.hex()}"
            )
        if final_dictionary.expand_index(index, tbl) != text:
            raise BuildError(f"stock fragment render mismatch {index:04X}: {text!r}")

    render_rows: list[dict[str, Any]] = []
    for logical in sorted(PLANS):
        payload, term = read_record(candidate_bytes, logical)
        prefix, body, _kind = split_prefix_body(payload)
        rendered = stripped(final_dictionary.expand(body, tbl))
        expected = str(spec_rows[logical]["ko"])
        if rendered != expected:
            raise BuildError(f"candidate render mismatch {logical:06X}: {rendered!r} != {expected!r}")
        if term != EXPECTED_TERMINATORS[logical] or candidate_bytes[sb + term] != 0:
            raise BuildError(f"candidate terminator drift at {logical:06X}")
        pristine_payload, _ = read_record(original, logical)
        pristine_prefix, pristine_body, _ = split_prefix_body(pristine_payload)
        if prefix != pristine_prefix or original_unit_kinds(body) != original_unit_kinds(pristine_body):
            raise BuildError(f"candidate native grammar drift at {logical:06X}")
        render_rows.append(
            {
                "abs": f"{logical:06X}",
                "rendered": rendered,
                "prefix_hex": prefix.hex().upper(),
                "body_hex": body.hex().upper(),
                "native_kinds": original_unit_kinds(body),
                "e518_present": b"\xE5\x18" in body,
                "terminator": f"{term:06X}",
            }
        )

    # Re-prove that every repurposed stock slot is reachable only from the exact
    # focused target positions and is never nested inside another dictionary entry.
    selected = set(used_slots)
    candidate_external = external_occurrence_map(candidate_bytes, ext3_aware=True, wanted=selected)
    candidate_nested = nested_occurrence_map(final_dictionary, wanted=selected, ext3_aware=True)
    candidate_raw = _raw_pair_hits(candidate_bytes, sorted(selected))
    reference_rows: list[dict[str, Any]] = []
    for index in sorted(selected):
        expected = sorted(expected_selected_occurrences.get(index, []))
        actual_external = sorted(int(str(item["token_abs"]), 16) for item in candidate_external.get(index, []))
        actual_raw = sorted(int(str(item["token_abs"]), 16) for item in candidate_raw.get(index, []))
        nested = candidate_nested.get(index, [])
        if actual_external != expected or actual_raw != expected or nested:
            raise BuildError(
                f"selected stock reference proof failed {index:04X}: "
                f"expected={expected} external={actual_external} raw={actual_raw} nested={nested}"
            )
        reference_rows.append(
            {
                "index": f"{index:04X}",
                "expected_external": [f"{value:06X}" for value in expected],
                "actual_external": [f"{value:06X}" for value in actual_external],
                "actual_raw": [f"{value:06X}" for value in actual_raw],
                "nested": [],
            }
        )

    # Global diff allowlist: only two target records, four private stock payloads,
    # and the WonderSwan checksum may differ from the parent TIP.
    runs = diff_runs(parent, candidate_bytes)
    outside = [run for run in runs if not covered(run, allowed_extents)]
    if outside:
        raise BuildError(f"diff escaped focused scope: {outside[:8]}")

    OUT_ROM.write_bytes(candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != save:
        raise BuildError("candidate SaveRAM is not byte-exact to live main")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_domon_runtime_structure_followup_candidate.py",
        "status": "candidate_pending_user_runtime_validation",
        "scope": "only 626102 stray こ and 62663E 오우！！/がけはう runtime leaks; older event-error screenshot excluded",
        "parent": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(parent)},
        "original": {"path": str(ORIGINAL.relative_to(ROOT)), "sha256": sha(original)},
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "sha256": sha(candidate_bytes),
            "size": len(candidate_bytes),
            "checksum": f"{checksum:04X}",
        },
        "saveram": {
            "path": str(OUT_SAVE.relative_to(ROOT)),
            "sha256": sha(OUT_SAVE.read_bytes()),
            "size": OUT_SAVE.stat().st_size,
            "byte_exact_to_live_main": True,
        },
        "source_rows": source_rows,
        "stock_fragments": slot_rows,
        "patches": patch_rows,
        "renders": render_rows,
        "selected_stock_reference_proof": reference_rows,
        "guards": {
            "only_two_runtime_records_changed": True,
            "target_payload_extents_preserved": True,
            "target_terminators_preserved": True,
            "pristine_native_code_unit_kinds_restored": True,
            "target_e518_removed": True,
            "stock_hangul_ec8d_marker_verified": True,
            "repurposed_stock_slots_external_nested_raw_proven": True,
            "unexpected_diff_runs": 0,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
            "older_event_error_excluded": True,
        },
        "diff": {
            "runs": [{"start": start, "end": end, "length": end - start} for start, end in runs],
            "unexpected_runs": 0,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "candidate_sha256": report["candidate"]["sha256"],
                "checksum": report["candidate"]["checksum"],
                "targets": [row["abs"] for row in render_rows],
                "selected_slots": sorted(f"{index:04X}" for index in used_slots),
                "report": str(REPORT.relative_to(ROOT)),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
