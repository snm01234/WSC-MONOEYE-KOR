#!/usr/bin/env python3
"""Build the 2026-08-09 runtime-structure/terminology follow-up candidate.

Candidate-only scope bound to user runtime captures:

* 61E23D / 61E24B: restore original-style native 2-byte dictionary bodies so
  the continuation path can no longer expose ``18=こ`` or ext3 index bytes as
  ``ソ회신``.
* 5D5982 / 5D5B1F: runtime proves leading 82 is visible Japanese text, not
  metadata; remove it while preserving the record extent/terminator.
* 5997A8: shorten the mistranslated/overlong God Gundam line to the actual
  source meaning (King of Heart name), and 59971D: use an ordinary native
  token because the captured runtime path emitted the JP source despite the
  static ext3 phrase being Korean.
* 十二王方牌大車輪 -> 십이왕방패대차병 and battle shout 大車輪 -> 대차병.
* プルツ－ -> 플투 globally across the live dictionary phrases.
* Replace trailing visible 0x01 padding in weapon-name fields with the two
  already user-approved zero-width filler tokens, leaving at most one visible
  trailing cell while preserving every field length and NUL boundary.

The live main TIP and live SaveRAM are never overwritten by this builder.
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
from build_sig_scenario_stock_native_chain_candidate import (
    current_ext3_nested_parents,
    current_nested_parents,
)
from build_terminology_retranslation_candidate import (
    diff_runs,
    encode,
    ext3_storage_proof,
    in_intervals,
    inplace_phrase,
    merged,
    stock_storage_proof,
)
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import (
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_3byte_dict_token import token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
JP_TBL_PATH = ROOT / "data/monoeye_verified.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
WEAPON_SPEC = ROOT / "data/weapon_names_ko.json"
OUT_ROM = ROOT / "out/patch/runtime_structure_terminology_candidate.wsc"
OUT_SAVE = ROOT / "sram/runtime_structure_terminology_candidate.sav"
OUT_REPORT = ROOT / "out/patch/runtime_structure_terminology_candidate_report.json"
SRAM_MIRROR = ROOT / "sram/runtime_structure_terminology_candidate.sav"

EXPECTED_MAIN_SHA = "f4f0ee2c0546e0794dae262b6246a190525763b6174d3423bec3ca20d8d2f212"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Existing runtime-approved zero-width fillers from the promoted menu/weapon
# follow-up.  These are reused only in bank75 weapon display fields.
EMPTY_EXT3_INDEX = 0x0A48E
EMPTY_STOCK_INDEX = 0x00A9

# Strong-retired ordinary stock slots.  The builder re-proves all three on the
# exact parent before repurposing their private storage.
NATIVE_SLOT_TEXT = {
    0x02B7: "내　이　손이　새빨갛게　타오른다！！",
    0x02B8: "……음、　우선　티파를",
    0x02C5: "안전한　곳에　데려가야겠지？",
}

# (logical, required prefix, expected current body token, native slot)
NATIVE_RECORDS = (
    (0x61E23D, bytes.fromhex("18"), bytes.fromhex("E51813E1"), 0x02B8),
    (0x61E24B, b"", bytes.fromhex("E518C3F2"), 0x02C5),
    (0x59971D, bytes.fromhex("173418"), bytes.fromhex("E5180892"), 0x02B7),
)

FALSE_VISIBLE_LEAD_RECORDS = {
    0x5D5982: bytes.fromhex("82E5184332"),
    0x5D5B1F: bytes.fromhex("82E5184332"),
}

EXT3_TEXT_PATCHES = {
    0x032D1: (
        "우주　최고의　무투가、　킹　오브　하트의　긍지를　걸고！！",
        "킹・오브・하트의　이름을　걸고！！",
    ),
    0x0189D: ("거대　바퀴이이이！！", "대차병！！"),
    0x0FEFB: ("십이왕방패대회전", "십이왕방패대차병"),
    0x102BD: ("십이왕방패！　대차린！！", "십이왕방패！　대차병！！"),
}

PLE_HIT_INDICES = {
    0x008F4,
    0x026C3,
    0x0279F,
    0x027A1,
    0x027A2,
    0x037A1,
    0x0479F,
    0x0579D,
    0x057A0,
    0x0F02D,
    0x0F0EB,
    0x0F3CE,
}

# Special technique name is in the same bank75 weapon-display family but is not
# listed in data/weapon_names_ko.json.
SPECIAL_WEAPON_LOGICALS = {0x75CA18}
WEAPON_TABLE_START = 0x75C000
WEAPON_TABLE_END = 0x75E800


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def payload_at(rom: bytes | bytearray, logical: int, max_len: int = 128) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=max_len)
    if got is None:
        raise BuildError(f"unreadable zstring {logical:06X}")
    return bytes(got[0]), int(got[1])


def strip_pad(text: str) -> str:
    return text.rstrip("　 \t")


def trailing_01(payload: bytes) -> int:
    count = 0
    for byte in reversed(payload):
        if byte != 0x01:
            break
        count += 1
    return count


def filler_for(length: int, empty_ext3: bytes, empty_stock: bytes) -> bytes:
    if length < 0:
        raise BuildError("negative filler length")
    out = bytearray()
    while length >= 4:
        out += empty_ext3
        length -= 4
    if length >= 2:
        out += empty_stock
        length -= 2
    if length == 1:
        out.append(0x01)
        length = 0
    if length != 0:
        raise BuildError("filler decomposition failed")
    return bytes(out)


def dictionary_indices(dictionary: Any):
    yield from range(int(dictionary.count))
    yield from range(0x1000, 0x1000 + int(dictionary.ext3_count))


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live SaveRAM missing/wrong size")
    live_save = MAIN_SAVE.read_bytes()

    tbl = Tbl.load(TBL_PATH)
    jp_tbl = Tbl.load(JP_TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 16)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    d_jp = Dictionary(original)
    sb = stock_base(parent)

    empty_ext3 = token_from_ext3_index(EMPTY_EXT3_INDEX, num_banks=num_banks)
    empty_stock = token_from_dict_index(EMPTY_STOCK_INDEX)
    if d_parent.expand(empty_ext3, tbl) != "" or d_parent.expand(empty_stock, tbl) != "":
        raise BuildError("approved zero-width filler no longer renders empty")

    # Re-prove the three ordinary native storage slots from scratch on this
    # exact parent.  Original-only historical consumers are provenance, not a
    # live dependency: the safety condition is zero *working* external
    # consumers plus zero current native/ext3 nested parents.  This matches the
    # source-independent stock-native recovery proven in the earlier bank61
    # incident while remaining fail-closed against current executable reuse.
    watched_native = set(NATIVE_SLOT_TEXT)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    native_nested = current_nested_parents(d_parent, watched_native)
    ext3_nested = current_ext3_nested_parents(d_parent, watched_native)
    native_provenance: dict[int, dict[str, Any]] = {}
    for index in sorted(watched_native):
        working_consumers = [
            c for c in union.consumers_for(index) if "working" in c.seen_in
        ]
        original_only = [
            {
                "abs": f"{int(c.abs):06X}",
                "region": c.region,
                "kind": c.kind,
                "seen_in": sorted(c.seen_in),
            }
            for c in union.consumers_for(index)
            if "working" not in c.seen_in
        ]
        if working_consumers or native_nested[index] or ext3_nested[index]:
            raise BuildError(
                f"native slot {index:04X} is live: "
                f"working={[(f'{int(c.abs):06X}', c.region, c.kind) for c in working_consumers]} "
                f"native_nested={sorted(native_nested[index])} "
                f"ext3_nested={sorted(ext3_nested[index])}"
            )
        native_provenance[index] = {
            "working_external_consumers": 0,
            "current_native_nested_parents": 0,
            "current_ext3_nested_parents": 0,
            "original_only_consumers": original_only,
        }

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    native_slot_rows: list[dict[str, Any]] = []

    # 1) Populate three source-independent ordinary native slots in-place.
    for index, text in sorted(NATIVE_SLOT_TEXT.items()):
        current_dict = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        proof = stock_storage_proof(current_dict, index)
        encoded = encode(text, tbl)
        if not proof["ok"] or len(encoded) > int(proof["old_len"]):
            raise BuildError(f"native slot {index:04X} unsafe/too small: {proof}")
        before = strip_pad(current_dict.expand_index(index, tbl))
        allowed.append(inplace_phrase(candidate, proof, encoded))
        check = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        after = strip_pad(check.expand_index(index, tbl))
        if after != text:
            raise BuildError(f"native slot verify failed {index:04X}: {after!r}")
        native_slot_rows.append({
            "index": f"{index:04X}",
            "token": token_from_dict_index(index).hex().upper(),
            "before": before,
            "after": after,
            "old_len": int(proof["old_len"]),
            "new_len": len(encoded),
            **native_provenance[index],
        })

    # 2) Global Ple-Two terminology correction.  All known live dictionary
    # phrases are direct literals on this parent, so same-length in-place text
    # replacement is sufficient and cannot change record boundaries.
    d_scan = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    ple_hits: dict[int, str] = {}
    for index in dictionary_indices(d_scan):
        try:
            text = strip_pad(d_scan.expand_index(index, tbl))
        except Exception:
            continue
        if "풀투" in text:
            ple_hits[index] = text
    if set(ple_hits) != PLE_HIT_INDICES:
        raise BuildError(
            "풀투 dictionary population drifted: got="
            + ",".join(f"{x:05X}" for x in sorted(ple_hits))
        )
    ple_rows: list[dict[str, Any]] = []
    for index in sorted(ple_hits):
        current_dict = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        before = strip_pad(current_dict.expand_index(index, tbl))
        after = before.replace("풀투", "플투")
        proof = (
            stock_storage_proof(current_dict, index)
            if index < 0x1000
            else ext3_storage_proof(bytes(candidate), current_dict, index)
        )
        encoded = encode(after, tbl)
        if not proof["ok"] or len(encoded) > int(proof["old_len"]):
            raise BuildError(f"플투 replacement unsafe at {index:05X}: {proof}")
        allowed.append(inplace_phrase(candidate, proof, encoded))
        ple_rows.append({"index": f"{index:05X}", "before": before, "after": after})

    # 3) Semantic ext3 phrase corrections whose physical storage is private.
    ext3_rows: list[dict[str, Any]] = []
    for index, (expected_before, desired) in sorted(EXT3_TEXT_PATCHES.items()):
        current_dict = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        before = strip_pad(current_dict.expand_index(index, tbl))
        if before != expected_before:
            raise BuildError(f"ext3 source drift {index:05X}: {before!r} != {expected_before!r}")
        proof = ext3_storage_proof(bytes(candidate), current_dict, index)
        encoded = encode(desired, tbl)
        if not proof["ok"] or len(encoded) > int(proof["old_len"]):
            raise BuildError(f"ext3 phrase {index:05X} unsafe/too small: {proof}")
        allowed.append(inplace_phrase(candidate, proof, encoded))
        after = strip_pad(make_dictionary_ext3(candidate, ext_meta, ext3_meta).expand_index(index, tbl))
        if after != desired:
            raise BuildError(f"ext3 phrase verify failed {index:05X}: {after!r}")
        ext3_rows.append({
            "index": f"{index:05X}",
            "before": before,
            "after": after,
            "old_len": int(proof["old_len"]),
            "new_len": len(encoded),
        })

    # 4) Rewrite the two broken scenario continuation records and the captured
    # God-Gundam runtime line to ordinary native-token grammar.  Prefix, total
    # payload length and terminator stay byte-for-byte at their old locations.
    native_record_rows: list[dict[str, Any]] = []
    for logical, prefix, expected_body_token, slot in NATIVE_RECORDS:
        old, old_term = payload_at(parent, logical)
        if not old.startswith(prefix + expected_body_token):
            raise BuildError(f"native-record source drift {logical:06X}: {old.hex().upper()}")
        body_capacity = len(old) - len(prefix)
        token = token_from_dict_index(slot)
        if len(token) > body_capacity:
            raise BuildError(f"native token does not fit {logical:06X}")
        new = prefix + token + b"\x01" * (body_capacity - len(token))
        start = sb + logical
        candidate[start:start + len(old)] = new
        allowed.append((start, start + len(old)))
        check, check_term = payload_at(candidate, logical)
        if check != new or check_term != old_term:
            raise BuildError(f"native-record boundary drift {logical:06X}")
        d_now = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        rendered_body = strip_pad(d_now.expand(check[len(prefix):], tbl))
        desired = NATIVE_SLOT_TEXT[slot]
        if rendered_body != desired:
            raise BuildError(f"native-record render failed {logical:06X}: {rendered_body!r}")
        native_record_rows.append({
            "abs": f"{logical:06X}",
            "prefix_hex": prefix.hex().upper(),
            "before": old.hex().upper(),
            "after": new.hex().upper(),
            "terminator": f"{old_term - sb:06X}",
            "rendered_body": rendered_body,
            "stock_slot": f"{slot:04X}",
        })

    # 5) Runtime-visible 82 was incorrectly restored as metadata.  Drop it and
    # shift the already-correct ext3 body one byte left; one trailing pad keeps
    # the exact record extent and terminator.
    false_lead_rows: list[dict[str, Any]] = []
    for logical, expected_prefix_body in sorted(FALSE_VISIBLE_LEAD_RECORDS.items()):
        old, old_term = payload_at(parent, logical)
        if not old.startswith(expected_prefix_body):
            raise BuildError(f"82 visible-lead source drift {logical:06X}: {old.hex().upper()}")
        new = old[1:] + b"\x01"
        start = sb + logical
        candidate[start:start + len(old)] = new
        allowed.append((start, start + len(old)))
        check, check_term = payload_at(candidate, logical)
        if check != new or check_term != old_term or check.startswith(b"\x82"):
            raise BuildError(f"82 visible-lead repair failed {logical:06X}")
        false_lead_rows.append({
            "abs": f"{logical:06X}",
            "before": old.hex().upper(),
            "after": new.hex().upper(),
            "terminator": f"{old_term - sb:06X}",
        })

    # 6) Generalize the already runtime-approved zero-width filler technique to
    # weapon display fields.  Select normal weapons from the canonical weapon
    # spec plus explicit special-technique records, and only replace the final
    # run of 0x01 padding.  No token/name bytes or NUL positions are moved.
    weapon_spec = json.loads(WEAPON_SPEC.read_text(encoding="utf-8"))
    weapon_jp_names = {str(row["jp"]) for row in weapon_spec.get("entries") or []}
    selected: list[int] = []
    logical = WEAPON_TABLE_START
    jp_sb = stock_base(original)
    while logical < WEAPON_TABLE_END:
        got = read_encoded_z_safe(original, jp_sb + logical, max_len=64)
        if got is None:
            logical += 1
            continue
        raw, term = got
        jp_text = d_jp.expand(bytes(raw), jp_tbl)
        if jp_text in weapon_jp_names or logical in SPECIAL_WEAPON_LOGICALS:
            selected.append(logical)
        next_logical = int(term) - jp_sb + 1
        logical = next_logical if next_logical > logical else logical + 1

    # Explicit screen examples must be in the selected family.
    for required in (0x75C9E6, 0x75CA18):
        if required not in selected:
            selected.append(required)
    selected = sorted(set(selected))

    weapon_rows: list[dict[str, Any]] = []
    weapon_padding_skipped: list[dict[str, Any]] = []
    for logical in selected:
        old, old_term = payload_at(candidate, logical)
        pad = trailing_01(old)
        if pad < 2:
            continue
        d_before_pad = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        before_render = strip_pad(d_before_pad.expand(old, tbl))
        filler = filler_for(pad, empty_ext3, empty_stock)
        if len(filler) != pad:
            raise BuildError(f"weapon filler length mismatch {logical:06X}")
        new = old[:-pad] + filler
        start = sb + logical
        candidate[start:start + len(old)] = new
        check, check_term = payload_at(candidate, logical)
        after_render = strip_pad(make_dictionary_ext3(candidate, ext_meta, ext3_meta).expand(check, tbl))
        visible_after = trailing_01(check)
        if (
            len(check) != len(old)
            or check_term != old_term
            or before_render != after_render
            or visible_after > 1
        ):
            # The zero-width fillers are runtime-approved for ordinary name75
            # weapon token fields, but a few table records use a parser context
            # where inserting a native token changes interpretation.  Fail
            # closed per-record: restore the exact parent bytes and record the
            # skip instead of generalizing across an unproven field grammar.
            candidate[start:start + len(old)] = old
            weapon_padding_skipped.append({
                "abs": f"{logical:06X}",
                "name_before": before_render,
                "name_tentative": after_render,
                "visible_pad_before": pad,
                "visible_pad_tentative": visible_after,
                "reason": "zero_width_filler_not_semantics_preserving_in_this_field",
            })
            continue
        allowed.append((start + len(old) - pad, start + len(old)))
        weapon_rows.append({
            "abs": f"{logical:06X}",
            "name": after_render,
            "payload_len": len(old),
            "visible_pad_before": pad,
            "visible_pad_after": visible_after,
            "before_tail": old[-pad:].hex().upper(),
            "after_tail": filler.hex().upper(),
            "terminator": f"{old_term - sb:06X}",
        })

    # Candidate-bound semantic/runtime invariants before checksum.
    d_final = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    if strip_pad(d_final.expand_index(0x0FEFB, tbl)) != "십이왕방패대차병":
        raise BuildError("십이왕방패대차병 final verification failed")
    if strip_pad(d_final.expand_index(0x0189D, tbl)) != "대차병！！":
        raise BuildError("대차병 battle phrase final verification failed")
    if strip_pad(d_final.expand_index(0x032D1, tbl)) != "킹・오브・하트의　이름을　걸고！！":
        raise BuildError("God Gundam shortened line final verification failed")

    residual_ple: list[dict[str, Any]] = []
    for index in dictionary_indices(d_final):
        try:
            text = strip_pad(d_final.expand_index(index, tbl))
        except Exception:
            continue
        if "풀투" in text:
            residual_ple.append({"index": f"{index:05X}", "text": text})
    if residual_ple:
        raise BuildError(f"풀투 residual dictionary entries remain: {residual_ple[:8]}")

    # Screen-proven continuation exception gate: these exact runtime paths may
    # not regress to an E5 18 body again.
    for logical, prefix, _old_token, _slot in NATIVE_RECORDS:
        payload, _term = payload_at(candidate, logical)
        body = payload[len(prefix):]
        if body.startswith(b"\xE5\x18"):
            raise BuildError(f"screen-proven unsafe ext3 body reintroduced at {logical:06X}")

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)

    intervals = merged(allowed)
    runs = diff_runs(parent, result)
    unaccounted: list[int] = []
    for run in runs:
        a = int(run["start"], 16)
        b = int(run["end"], 16)
        for off in range(a, b):
            if not in_intervals(off, intervals):
                unaccounted.append(off)
                if len(unaccounted) >= 20:
                    break
        if len(unaccounted) >= 20:
            break
    if unaccounted:
        raise BuildError("unaccounted ROM diffs: " + ",".join(f"{x:07X}" for x in unaccounted))

    if sha(MAIN.read_bytes()) != EXPECTED_MAIN_SHA or MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("live main TIP/SaveRAM changed during candidate build")

    OUT_ROM.write_bytes(result)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    SRAM_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MAIN_SAVE, SRAM_MIRROR)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_runtime_structure_terminology_candidate.py",
        "parent": {"path": str(MAIN.relative_to(ROOT)), "sha256": EXPECTED_MAIN_SHA},
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)),
            "sha256": sha(result),
            "size": len(result),
            "checksum": f"{checksum:04X}",
        },
        "native_slots": native_slot_rows,
        "native_runtime_records": native_record_rows,
        "false_visible_lead_records": false_lead_rows,
        "ext3_semantic_corrections": ext3_rows,
        "ple_two_dictionary_rewrites": ple_rows,
        "weapon_padding": {
            "selected_weapon_records": len(selected),
            "rewritten_records": len(weapon_rows),
            "visible_pad_before_total": sum(row["visible_pad_before"] for row in weapon_rows),
            "visible_pad_after_total": sum(row["visible_pad_after"] for row in weapon_rows),
            "records": weapon_rows,
            "skipped_records": weapon_padding_skipped,
        },
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(int(run["length"]) for run in runs),
            "unaccounted": 0,
            "runs_detail": runs,
        },
        "verification": {
            "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
            "main_saveram_unchanged": MAIN_SAVE.read_bytes() == live_save,
            "candidate_saveram_matches_main": OUT_SAVE.read_bytes() == live_save,
            "screen_proven_unsafe_ext3_bodies_zero": True,
            "visible_82_lead_zero": True,
            "ple_two_residual_zero": True,
            "weapon_padding_at_most_one_visible_cell": all(row["visible_pad_after"] <= 1 for row in weapon_rows),
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate": report["candidate"],
        "native_records": len(native_record_rows),
        "false_leads": len(false_lead_rows),
        "ple_rewrites": len(ple_rows),
        "weapon_padding_records": len(weapon_rows),
        "weapon_visible_pad": [
            report["weapon_padding"]["visible_pad_before_total"],
            report["weapon_padding"]["visible_pad_after_total"],
        ],
        "diff_runs": len(runs),
        "changed_bytes": report["diff"]["changed_bytes"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
