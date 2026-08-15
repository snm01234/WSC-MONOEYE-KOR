#!/usr/bin/env python3
"""Build the corrected Sig bank61 continuation + cannon terminology candidate.

Runtime evidence from the user's captured scene binds the broken sequence to:
* 611DF0: ふざけるな！ -> 장난치지 마라！ (first line, already correct)
* 611DF8: 18 | E5 18 91 99 ... -> visible こ before the Korean second line
* 611E05: E5 18 01 69 ... -> next window visibly leaks 69 == み

The original 611DF8/611E05 continuation grammar uses ordinary two-byte dictionary
units, not a four-byte E5 18 portal.  Restore that token shape with two retired
stock slots that have zero current external consumers and zero current nested
parents.  The original-only consumers are retained in the proof report.

Four weapon names containing カノン remain a separate private-ext3 in-place
terminology correction (카논 -> 캐논).
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

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_terminology_retranslation_candidate import (
    consumer_abs_set,
    diff_runs,
    encode,
    ext3_storage_proof,
    in_intervals,
    inplace_phrase,
    merged,
    stock_storage_proof,
)
from expand_dictionary import iter_dict_indices, write_dictionary_slots_spill
from extract_script import split_prefix_body
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import (
    Tbl,
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
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/sig_scenario_cannon_fix_candidate.wsc"
OUT_SAVE = ROOT / "sram/sig_scenario_cannon_fix_candidate.sav"
OUT_REPORT = ROOT / "out/patch/sig_scenario_cannon_fix_report.json"

EXPECTED_MAIN = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# These slots are deliberately below the FF page.  On the current promoted TIP
# they have no working external consumer and no current dictionary parent.  The
# Original+Working union still remembers their original-only consumers, so the
# report records those addresses instead of pretending the slots were always
# free.
SCENARIO = {
    0x611DF8: {
        "before_payload_hex": "18E518919901010101010101",
        "prefix_hex": "18",
        "before": "세라를　죽여놓고선、",
        "after": "세라를　죽여놓고선、",
        "slot": 0x002A,
        "visible_leak_before": "こ",
    },
    0x611E05: {
        "before_payload_hex": "E51801690101010101",
        "prefix_hex": "",
        "before": "뻔뻔하게　잘도　살아　숨　쉬는구나！！",
        "after": "뻔뻔하게　잘도　살아　숨　쉬는구나！！",
        "slot": 0x003B,
        "visible_leak_before": "み",
    },
}
SCENARIO_NEIGHBORS = {
    0x611DF0: "장난치지　마라！",
    0x611E13: "에？……　죽였다고요？",
}

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
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def record(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def current_nested_parents(dictionary: Any) -> dict[int, set[int]]:
    parents: dict[int, set[int]] = {i: set() for i in range(0x1000)}
    for parent_index in range(min(int(dictionary.count), 0x1000)):
        try:
            raw = bytes(dictionary.raw_entry(parent_index))
        except Exception:
            continue
        for child in iter_dict_indices(raw):
            if 0 <= int(child) < 0x1000:
                parents[int(child)].add(parent_index)
    return parents


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN:
        raise BuildError("main TIP identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    nested_now = current_nested_parents(dictionary)

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # 1) Prove two retired stock slots are dead in the *current* runtime graph.
    #    Keep Original-only references in the report as provenance evidence.
    scenario_slot_proofs: list[dict[str, Any]] = []
    slot_payloads: dict[int, bytes] = {}
    for logical, spec in sorted(SCENARIO.items()):
        slot = int(spec["slot"])
        if slot >= 0x0F00 or not dict_token_safe_in_zstring(slot):
            raise BuildError(f"unsafe two-byte scenario slot {slot:04X}")
        consumers = sorted(union.consumers_for(slot), key=lambda row: (row.abs, row.region, row.kind))
        working_consumers = [row for row in consumers if "working" in row.seen_in]
        current_parents = sorted(nested_now.get(slot) or ())
        if working_consumers or current_parents:
            raise BuildError(
                f"retired slot {slot:04X} is live: consumers={working_consumers}, parents={current_parents}"
            )
        storage = stock_storage_proof(dictionary, slot)
        if not storage["ok"]:
            raise BuildError(f"retired slot storage is aliased: {storage}")
        encoded = encode(str(spec["after"]), tbl)
        slot_payloads[slot] = encoded
        scenario_slot_proofs.append({
            **storage,
            "record_abs": f"{logical:06X}",
            "token_hex": token_from_dict_index(slot).hex().upper(),
            "target": spec["after"],
            "encoded_len": len(encoded),
            "working_external_consumers": [],
            "current_nested_parents": [],
            "original_only_consumers": [
                {
                    "abs": f"{row.abs:06X}",
                    "region": row.region,
                    "kind": row.kind,
                    "seen_in": sorted(row.seen_in),
                }
                for row in consumers
            ],
            "runtime_contract": "retired_current_free_non_ff_two_byte_slot",
        })

    # 2) Put the two Korean phrases behind ordinary F0-.. two-byte tokens.
    #    write_dictionary_slots_spill retargets only the selected pointers.
    before_ptrs = {slot: int(dictionary.ptrs[slot]) for slot in slot_payloads}
    pointers_after, _spill_end = write_dictionary_slots_spill(candidate, slot_payloads)
    dict_after_stock = make_dictionary_ext3(bytes(candidate), ext_meta, ext3_meta)
    for proof in scenario_slot_proofs:
        slot = int(proof["index"], 16)
        pointer_abs = int(dictionary.ptr_file) + slot * 2
        allowed.append((pointer_abs, pointer_abs + 2))
        new_entry_abs = int(dict_after_stock.entry_abs(slot))
        new_raw = bytes(dict_after_stock.raw_entry(slot))
        if new_raw != slot_payloads[slot]:
            raise BuildError(f"stock spill round-trip mismatch at {slot:04X}")
        allowed.append((new_entry_abs, new_entry_abs + len(new_raw) + 1))
        proof["old_ptr"] = f"{before_ptrs[slot]:04X}"
        proof["new_ptr"] = f"{int(pointers_after[slot]):04X}"
        proof["new_entry_abs"] = new_entry_abs

    # 3) Restore the original continuation token *shape* at 611DF8/611E05.
    #    Prefix/record length/terminator remain exact; only body token+padding is
    #    rewritten.  Neither target may retain E5 18 after this step.
    scenario_record_checks_before: list[dict[str, Any]] = []
    sb = stock_base(parent)
    for logical, spec in sorted(SCENARIO.items()):
        payload, terminator = record(parent, logical)
        if payload.hex().upper() != str(spec["before_payload_hex"]):
            raise BuildError(
                f"scenario payload drifted at {logical:06X}: {payload.hex().upper()}"
            )
        prefix = bytes.fromhex(str(spec["prefix_hex"]))
        if not payload.startswith(prefix):
            raise BuildError(f"scenario prefix drifted at {logical:06X}")
        body = payload[len(prefix):]
        before_render = dictionary.expand(body, tbl).rstrip("　")
        if before_render != str(spec["before"]):
            raise BuildError(
                f"scenario render drifted at {logical:06X}: {before_render!r}"
            )
        token = token_from_dict_index(int(spec["slot"]))
        body_capacity = len(payload) - len(prefix)
        if body_capacity < len(token):
            raise BuildError(f"scenario body too short at {logical:06X}")
        new_payload = prefix + token + (b"\x01" * (body_capacity - len(token)))
        start = sb + logical
        candidate[start:start + len(payload)] = new_payload
        allowed.append((start, start + len(payload)))
        scenario_record_checks_before.append({
            "abs": f"{logical:06X}",
            "before_payload_hex": payload.hex().upper(),
            "after_payload_hex": new_payload.hex().upper(),
            "prefix_hex": prefix.hex().upper(),
            "terminator": f"{terminator - sb:06X}",
            "body_capacity": body_capacity,
            "token_hex": token.hex().upper(),
            "visible_leak_before": spec["visible_leak_before"],
        })

    # 4) 카논 -> 캐논: four private ext3 phrase bodies, all same-length.
    cannon_proofs: list[dict[str, Any]] = []
    for logical, (idx, before, after) in sorted(CANNON.items()):
        payload, _terminator = record(parent, logical)
        prefix, body, _ = split_prefix_body(payload)
        rendered = dictionary.expand(body, tbl).rstrip("　")
        if prefix or rendered != before or dictionary.expand_index(idx, tbl).rstrip("　") != before:
            raise BuildError(f"cannon target drifted at {logical:06X}")
        consumers = consumer_abs_set(union, idx)
        if consumers != {logical}:
            raise BuildError(f"cannon slot {idx:05X} is shared: {sorted(consumers)}")
        storage = ext3_storage_proof(parent, dictionary, idx)
        encoded = encode(after, tbl)
        if not storage["ok"] or len(encoded) > int(storage["old_len"]):
            raise BuildError(f"cannon slot cannot be replaced in-place: {storage}")
        storage.update({
            "record_abs": f"{logical:06X}",
            "before": before,
            "ko": after,
            "new_len": len(encoded),
            "consumers": [f"{x:06X}" for x in sorted(consumers)],
            "strategy": "inplace_private_ext3_phrase",
        })
        allowed.append(inplace_phrase(candidate, storage, encoded))
        cannon_proofs.append(storage)

    checksum = update_ws_checksum(candidate)
    allowed.append((len(parent) - 2, len(parent)))
    result = bytes(candidate)
    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)

    # 5) Candidate-bound verification.
    failures: list[dict[str, Any]] = []
    scenario_checks: list[dict[str, Any]] = []
    for logical, spec in sorted(SCENARIO.items()):
        before_payload, before_term = record(parent, logical)
        after_payload, after_term = record(result, logical)
        prefix = bytes.fromhex(str(spec["prefix_hex"]))
        token = token_from_dict_index(int(spec["slot"]))
        expected_payload = prefix + token + b"\x01" * (len(before_payload) - len(prefix) - 2)
        rendered = result_dictionary.expand(token, tbl).rstrip("　")
        body = after_payload[len(prefix):]
        check = {
            "abs": f"{logical:06X}",
            "before": spec["before"],
            "ko": spec["after"],
            "rendered": rendered,
            "prefix_hex": prefix.hex().upper(),
            "token_hex": token.hex().upper(),
            "payload_exact": after_payload == expected_payload,
            "terminator_exact": after_term == before_term,
            "body_starts_with_two_byte_token": body.startswith(token),
            "body_contains_ext3_magic": b"\xE5\x18" in body,
            "leak_byte_removed": (
                logical != 0x611E05 or 0x69 not in body[:4]
            ),
        }
        check["ok"] = (
            rendered == str(spec["after"])
            and check["payload_exact"]
            and check["terminator_exact"]
            and check["body_starts_with_two_byte_token"]
            and not check["body_contains_ext3_magic"]
            and check["leak_byte_removed"]
        )
        scenario_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    neighbor_checks: list[dict[str, Any]] = []
    for logical, expected in sorted(SCENARIO_NEIGHBORS.items()):
        before_payload, before_term = record(parent, logical)
        after_payload, after_term = record(result, logical)
        _prefix, body, _ = split_prefix_body(after_payload)
        rendered = result_dictionary.expand(body, tbl).rstrip("　")
        check = {
            "abs": f"{logical:06X}",
            "expected": expected,
            "rendered": rendered,
            "record_bytes_exact": after_payload == before_payload,
            "terminator_exact": after_term == before_term,
        }
        check["ok"] = (
            rendered == expected
            and check["record_bytes_exact"]
            and check["terminator_exact"]
        )
        neighbor_checks.append(check)
        if not check["ok"]:
            failures.append(check)

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

    if failures:
        raise BuildError("verification failures: " + json.dumps(failures, ensure_ascii=False))

    # 6) Whole-ROM diff must be covered by: two stock pointer/phrase writes,
    #    the two scenario payloads, four private cannon phrases, and checksum.
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
    if OUT_SAVE.read_bytes() != save:
        raise BuildError("candidate SaveRAM is not byte-exact live SaveRAM")

    report = {
        "schema_version": 2,
        "generated_by": "tools/build_sig_scenario_cannon_fix_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_test_required",
        "main_tip_modified": False,
        "root_cause": {
            "actual_scene": "bank61 611DF0 -> 611DF8 -> 611E05",
            "wrong_previous_binding": "bank59 E006 5942F3/5942FC; reverted from reviewed source",
            "screen_byte_binding": {
                "611DF8": "leading 18 == TBL こ",
                "611E05": "old E5 18 01 69 portal tail 69 == TBL み",
            },
            "repair": "restore ordinary non-FF two-byte dictionary token shape for the two continuation records",
        },
        "inputs": {
            "main_sha256": sha256(parent),
            "live_saveram_sha256": sha256(save),
        },
        "counts": {
            "sig_scenario_continuation_records": len(SCENARIO),
            "retired_two_byte_slots": len(scenario_slot_proofs),
            "weapon_cannon_records": len(CANNON),
            "target_failures": 0,
        },
        "scenario_slot_proofs": scenario_slot_proofs,
        "scenario_record_rewrites": scenario_record_checks_before,
        "scenario_checks": scenario_checks,
        "neighbor_checks": neighbor_checks,
        "cannon_proofs": cannon_proofs,
        "cannon_checks": cannon_checks,
        "verification": {
            "all_scenario_renders_exact": all(row["ok"] for row in scenario_checks),
            "all_scenario_terminators_exact": all(row["terminator_exact"] for row in scenario_checks),
            "all_scenario_ext3_removed": all(not row["body_contains_ext3_magic"] for row in scenario_checks),
            "all_neighbors_byte_exact": all(row["ok"] for row in neighbor_checks),
            "all_cannon_renders_exact": all(row["ok"] for row in cannon_checks),
            "all_cannon_records_byte_exact": all(row["record_bytes_exact"] for row in cannon_checks),
            "unaccounted_changed_bytes": len(unaccounted),
            "diff_runs": len(runs),
            "diff_bytes": sum(int(row["length"]) for row in runs),
            "checksum": f"{checksum:04X}",
            "candidate_sha256": sha256(result),
            "candidate_saveram_sha256": sha256(save),
        },
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({"counts": report["counts"], "verification": report["verification"]}, ensure_ascii=True, indent=2))
    print(f"candidate: {OUT_ROM}")
    print(f"report: {OUT_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
