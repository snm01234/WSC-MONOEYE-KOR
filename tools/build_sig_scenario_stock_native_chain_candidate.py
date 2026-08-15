#!/usr/bin/env python3
"""Build the Sig scenario repair using only genuine stock native F0..FE tokens.

Why this candidate exists
-------------------------
Runtime tests ruled out three other paths for the bank61 continuation scene:
* E5 18 ext3 -> visible こ / early event end
* custom native shadow wrapper -> same symptom
* FF-page/bank10 native tokens -> visible こ / early event end

The JP-original probe proves the scene address binding and that the original
F0..FE stock-token chain runs through.  This candidate therefore uses no new
runtime code, no bank10/FF-page token, no ext3 token in the first three lines,
and no pointer relocation.  Five current-unreachable stock dictionary slots
(<0x0F00) with private in-place storage are repurposed into Korean fragments;
the three script records then use ordinary F0..FE token chains exactly like the
original game grammar.

The independent カノン -> 캐논 weapon terminology correction is included as
four private ext3 in-place phrase edits.
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
from expand_dictionary import iter_dict_indices
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
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/sig_scenario_stock_native_chain_candidate.wsc"
OUT_SAVE = ROOT / "sram/sig_scenario_stock_native_chain_candidate.sav"
OUT_REPORT = ROOT / "out/patch/sig_scenario_stock_native_chain_report.json"

EXPECTED_MAIN = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"

# Current-unreachable native stock slots, all <0x0F00 and all private storage.
# The phrases are deliberately split so each fits the slot's existing storage.
SLOT_TEXT = {
    0x04B7: "장난치지　마라！",   # 18 / 18 bytes
    0x0D35: "세라를　",           #  9 / 12 bytes
    0x0E43: "죽여놓고선、",       # 13 / 13 bytes
    0x0DB0: "잘도　",             #  7 / 12 bytes
    0x05B4: "태연하구나！！",     # 14 / 16 bytes
}

TARGETS = {
    0x611DF0: {
        "prefix": bytes.fromhex("173418"),
        "slots": (0x04B7,),
        "ko": "장난치지　마라！",
    },
    0x611DF8: {
        "prefix": bytes.fromhex("18"),
        "slots": (0x0D35, 0x0E43),
        "ko": "세라를　죽여놓고선、",
    },
    0x611E05: {
        "prefix": b"",
        "slots": (0x0DB0, 0x05B4),
        "ko": "잘도　태연하구나！！",
    },
}

CANNON = {
    0x75C3D3: (0x0FFAA, "메가　카논　포", "메가　캐논　포"),
    0x75C7B2: (0x0FF3E, "배부　빔　카논", "배부　빔　캐논"),
    0x75C7E5: (0x0FF38, "빔　카논", "빔　캐논"),
    0x75CBC7: (0x0FECF, "메가　카논", "메가　캐논"),
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def record(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def current_nested_parents(dictionary: Any, watched: set[int]) -> dict[int, set[int]]:
    out = {i: set() for i in watched}
    for parent in range(min(int(dictionary.count), 0x1000)):
        try:
            raw = bytes(dictionary.raw_entry(parent))
        except Exception:
            continue
        for child in iter_dict_indices(raw):
            if child in out:
                out[child].add(parent)
    return out


def current_ext3_nested_parents(dictionary: Any, watched: set[int]) -> dict[int, set[int]]:
    out = {i: set() for i in watched}
    start = 0x1000
    end = start + int(dictionary.ext3_count)
    for parent in range(start, end):
        try:
            raw = bytes(dictionary.raw_entry(parent))
        except Exception:
            continue
        for child in iter_dict_indices(raw):
            if child in out:
                out[child].add(parent)
    return out


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    live_sav = MAIN_SAVE.read_bytes()
    if sha(parent) != EXPECTED_MAIN:
        raise BuildError(f"main identity drifted: {sha(parent)}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)

    watched = set(SLOT_TEXT)
    nested_now = current_nested_parents(dictionary, watched)
    nested_ext3_now = current_ext3_nested_parents(dictionary, watched)

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    slot_proofs: list[dict[str, Any]] = []

    # 1) Reuse five true-retired stock entries in-place; no pointer moves.
    for slot, text in sorted(SLOT_TEXT.items()):
        if not (0 <= slot < 0x0F00) or not dict_token_safe_in_zstring(slot):
            raise BuildError(f"slot {slot:04X} is not a safe ordinary native token")
        working_consumers = [c for c in union.consumers_for(slot) if "working" in c.seen_in]
        if working_consumers or nested_now[slot] or nested_ext3_now[slot]:
            raise BuildError(
                f"stock slot {slot:04X} is live: consumers={working_consumers}, "
                f"native_nested={sorted(nested_now[slot])[:10]}, "
                f"ext3_nested={sorted(nested_ext3_now[slot])[:10]}"
            )
        proof = stock_storage_proof(dictionary, slot)
        encoded = encode(text, tbl)
        if not proof["ok"] or len(encoded) > int(proof["old_len"]):
            raise BuildError(f"slot {slot:04X} storage cannot fit target: {proof}, need={len(encoded)}")
        original_only = [
            {
                "abs": f"{c.abs:06X}",
                "region": c.region,
                "kind": c.kind,
                "seen_in": sorted(c.seen_in),
            }
            for c in union.consumers_for(slot)
            if "working" not in c.seen_in
        ]
        proof.update({
            "ko": text,
            "encoded_len": len(encoded),
            "token_hex": token_from_dict_index(slot).hex().upper(),
            "working_external_consumers": 0,
            "current_native_nested_parents": 0,
            "current_ext3_nested_parents": 0,
            "original_only_consumers": original_only,
            "strategy": "retired_stock_private_storage_inplace",
        })
        allowed.append(inplace_phrase(candidate, proof, encoded))
        slot_proofs.append(proof)

    # 2) Replace the first three ext3 bodies by only ordinary F0..FE token chains.
    sb = stock_base(parent)
    target_rows: list[dict[str, Any]] = []
    for logical, spec in sorted(TARGETS.items()):
        old_payload, old_term = record(parent, logical)
        prefix = bytes(spec["prefix"])
        if not old_payload.startswith(prefix):
            raise BuildError(f"prefix drift at {logical:06X}")
        old_body = old_payload[len(prefix):]
        if not old_body.startswith(b"\xE5\x18"):
            raise BuildError(f"expected E5 18 body at {logical:06X}: {old_body.hex().upper()}")
        chain = b"".join(token_from_dict_index(int(slot)) for slot in spec["slots"])
        if any(byte == 0xFF for byte in chain[::2]):
            raise BuildError(f"FF lead leaked into stock native chain at {logical:06X}: {chain.hex()}")
        body_capacity = len(old_payload) - len(prefix)
        if len(chain) > body_capacity:
            raise BuildError(f"native chain too long at {logical:06X}: {len(chain)}>{body_capacity}")
        new_payload = prefix + chain + (b"\x01" * (body_capacity - len(chain)))
        start = sb + logical
        candidate[start:start + len(old_payload)] = new_payload
        allowed.append((start, start + len(old_payload)))
        target_rows.append({
            "abs": f"{logical:06X}",
            "before_payload": old_payload.hex().upper(),
            "after_payload": new_payload.hex().upper(),
            "prefix": prefix.hex().upper(),
            "slots": [f"{int(s):04X}" for s in spec["slots"]],
            "token_chain_hex": chain.hex().upper(),
            "ko": spec["ko"],
            "terminator": f"{old_term - sb:06X}",
        })

    # 3) Keep the independent weapon-name terminology correction.
    cannon_proofs = []
    for logical, (idx, before, after) in sorted(CANNON.items()):
        payload, _term = record(parent, logical)
        prefix, body, _ = split_prefix_body(payload)
        rendered = dictionary.expand(body, tbl).rstrip("　")
        if prefix or rendered != before or dictionary.expand_index(idx, tbl).rstrip("　") != before:
            raise BuildError(f"cannon target drift at {logical:06X}")
        consumers = consumer_abs_set(union, idx)
        if consumers != {logical}:
            raise BuildError(f"cannon slot {idx:05X} shared: {sorted(consumers)}")
        storage = ext3_storage_proof(parent, dictionary, idx)
        encoded = encode(after, tbl)
        if not storage["ok"] or len(encoded) > int(storage["old_len"]):
            raise BuildError(f"cannon slot cannot replace in-place: {storage}")
        storage.update({"record_abs": f"{logical:06X}", "before": before, "ko": after})
        allowed.append(inplace_phrase(candidate, storage, encoded))
        cannon_proofs.append(storage)

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)

    # 4) Candidate-bound verification.
    failures: list[dict[str, Any]] = []
    for slot, text in sorted(SLOT_TEXT.items()):
        got = result_dictionary.expand_index(slot, tbl).rstrip("　")
        if got != text.rstrip("　"):
            failures.append({"slot": f"{slot:04X}", "want": text, "got": got})

    for logical, spec in sorted(TARGETS.items()):
        before_payload, before_term = record(parent, logical)
        after_payload, after_term = record(result, logical)
        prefix = bytes(spec["prefix"])
        chain = b"".join(token_from_dict_index(int(slot)) for slot in spec["slots"])
        expected = prefix + chain + b"\x01" * (len(before_payload) - len(prefix) - len(chain))
        rendered = result_dictionary.expand(after_payload[len(prefix):], tbl).rstrip("　")
        ok = (
            after_payload == expected
            and after_term == before_term
            and b"\xE5\x18" not in after_payload[len(prefix):]
            and 0xFF not in chain[::2]
            and rendered == str(spec["ko"])
        )
        if not ok:
            failures.append({
                "abs": f"{logical:06X}", "rendered": rendered,
                "payload": after_payload.hex().upper(), "expected": expected.hex().upper(),
            })

    # All control/sprite records and the first Ain/Sig lines remain byte-exact.
    neighbors = []
    for logical in (0x611E10, 0x611E13, 0x611E20, 0x611E2D, 0x611E32, 0x611E3C, 0x611E3F):
        p0, t0 = record(parent, logical)
        p1, t1 = record(result, logical)
        exact = p0 == p1 and t0 == t1
        row = {"abs": f"{logical:06X}", "exact": exact, "payload": p1.hex().upper()}
        neighbors.append(row)
        if not exact:
            failures.append(row)

    # Explicitly prove Ain then Sig speaker controls are untouched.
    if record(result, 0x611E10)[0] != bytes.fromhex("0807"):
        failures.append({"abs": "611E10", "reason": "Ain control changed"})
    if record(result, 0x611E2D)[0] != bytes.fromhex("17280802"):
        failures.append({"abs": "611E2D", "reason": "Sig control changed"})

    cannon_checks = []
    for logical, (_idx, _before, after) in sorted(CANNON.items()):
        p0, t0 = record(parent, logical)
        p1, t1 = record(result, logical)
        _pre, body, _ = split_prefix_body(p1)
        rendered = result_dictionary.expand(body, tbl).rstrip("　")
        ok = p0 == p1 and t0 == t1 and rendered == after
        row = {"abs": f"{logical:06X}", "rendered": rendered, "ok": ok}
        cannon_checks.append(row)
        if not ok:
            failures.append(row)

    if failures:
        raise BuildError("candidate verification failed: " + json.dumps(failures, ensure_ascii=False))

    allowed = merged(allowed)
    runs = diff_runs(parent, result)
    unaccounted = []
    for run in runs:
        start, end = int(run["start"], 16), int(run["end"], 16)
        for off in range(start, end):
            if not in_intervals(off, allowed):
                unaccounted.append(off)
                if len(unaccounted) >= 30:
                    break
        if len(unaccounted) >= 30:
            break
    if unaccounted:
        raise BuildError("unaccounted bytes: " + ",".join(f"{x:08X}" for x in unaccounted))

    OUT_ROM.write_bytes(result)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != live_sav:
        raise BuildError("candidate SaveRAM differs from current live SaveRAM")

    report = {
        "ok": True,
        "status": "candidate_static_verified_runtime_test_required",
        "generated_by": "tools/build_sig_scenario_stock_native_chain_candidate.py",
        "main_tip_modified": False,
        "runtime_evidence": {
            "jp_original_probe": "611DF0/611DF8/611E05 native JP chain continues past the prior early-end point",
            "ext3_candidate": "fails: visible こ / early event end",
            "custom_native_wrapper_candidate": "fails: visible こ / early event end",
            "bank10_ff_page_candidate": "fails: visible こ / early event end",
            "new_strategy": "only genuine stock native F0..FE tokens (<0x0F00), no runtime hook/pointer move",
        },
        "inputs": {"main_sha256": sha(parent), "live_sav_sha256": sha(live_sav)},
        "stock_slots": slot_proofs,
        "targets": target_rows,
        "neighbors": neighbors,
        "cannon": cannon_checks,
        "counts": {
            "scenario_records": len(TARGETS),
            "stock_slots": len(SLOT_TEXT),
            "cannon_records": len(CANNON),
            "diff_runs": len(runs),
            "diff_bytes": sum(int(r["end"], 16) - int(r["start"], 16) for r in runs),
            "unaccounted": len(unaccounted),
        },
        "outputs": {
            "rom": str(OUT_ROM), "rom_sha256": sha(result),
            "sav": str(OUT_SAVE), "sav_sha256": sha(live_sav),
            "checksum": f"{checksum:04X}",
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate": str(OUT_ROM),
        "sha256": sha(result),
        "checksum": f"{checksum:04X}",
        "stock_slots": len(SLOT_TEXT),
        "scenario_records": len(TARGETS),
        "diff_runs": report["counts"]["diff_runs"],
        "diff_bytes": report["counts"]["diff_bytes"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
