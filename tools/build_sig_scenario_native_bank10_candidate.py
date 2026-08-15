#!/usr/bin/env python3
"""Build a minimal Sig scenario repair using the already-promoted native 2-byte bank10 dictionary.

Runtime evidence:
- JP-original block probe proves the active scene is 611DF0 -> 611DF8 -> 611E05 -> 611E13.
- Keeping the first three lines as current E5 18 ext3 records reproduces こ / early-end.
- Restoring original native script bytes makes the event continue, but exposes stale/reused
  stock dictionary slots (e.g. 0843 -> a battle-voice sentence).

This candidate therefore changes only the first three spoken records to *existing* native
2-byte extended-dictionary tokens (indices EF7..FFF, expansion bank10).  No runtime hook,
new token format, event pointer, record boundary, sprite/control byte, or ext3 code is changed.
611E13 onward stays byte-exact to the current main TIP, so 08 07 continues to select Ain as
in the original scenario.

The independent カノン -> 캐논 weapon-name correction is included as four private ext3
phrase in-place edits, matching the earlier approved test scope.
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
)
from expand_dictionary import iter_dict_indices
from extract_script import split_prefix_body
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import (
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from patch_exp_dictionary import write_exp_dictionary_slots

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/sig_scenario_native_bank10_candidate.wsc"
OUT_SAVE = ROOT / "sram/sig_scenario_native_bank10_candidate.sav"
OUT_REPORT = ROOT / "out/patch/sig_scenario_native_bank10_report.json"

EXPECTED_MAIN = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"

# Three EF7..FFF slots that are completely unreachable in the current Working ROM:
# zero current external consumers, zero current native parents, zero current ext3 parents.
TARGETS = {
    0x611DF0: {"slot": 0x0F4D, "ko": "장난치지　마라！", "prefix": bytes.fromhex("173418")},
    0x611DF8: {"slot": 0x0FA3, "ko": "세라를　죽여놓고선、", "prefix": bytes.fromhex("18")},
    0x611E05: {"slot": 0x0FB9, "ko": "뻔뻔하게　잘도　살아　숨　쉬는구나！！", "prefix": b""},
}

# Keep the independent weapon terminology fix bundled for eventual promotion.
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
    r = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=128)
    if r is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(r[0]), int(r[1])


def current_ext3_nested(dictionary: Any, slots: set[int]) -> dict[int, list[int]]:
    out = {s: [] for s in slots}
    for index in range(0x1000, 0x1000 + int(dictionary.ext3_count)):
        try:
            raw = bytes(dictionary.raw_entry(index))
        except Exception:
            continue
        for child in iter_dict_indices(raw):
            if child in out:
                out[child].append(index)
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

    slots = {int(v["slot"]) for v in TARGETS.values()}
    nested3 = current_ext3_nested(dictionary, slots)
    slot_proofs = []
    for slot in sorted(slots):
        consumers = [c for c in union.consumers_for(slot) if "working" in c.seen_in]
        native_parents = sorted(union.nested_parents.get(slot) or ())
        if consumers or native_parents or nested3[slot]:
            raise BuildError(
                f"reserved bank10 slot {slot:04X} is live: consumers={consumers}, "
                f"native_parents={native_parents}, ext3_parents={nested3[slot][:10]}"
            )
        slot_proofs.append({
            "slot": f"{slot:04X}",
            "working_external_consumers": 0,
            "native_nested_parents": 0,
            "ext3_nested_parents": 0,
        })

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # Write the three complete Korean lines to the already-installed expansion dictionary.
    slot_payload = {int(spec["slot"]): encode(str(spec["ko"]), tbl) for spec in TARGETS.values()}
    before_bank10 = bytes(candidate[0x10 * 0x10000:(0x10 + 1) * 0x10000])
    wr = write_exp_dictionary_slots(
        candidate,
        slot_payload,
        locs=union.as_locs(),
        allow_aux_consumers=False,
    )
    after_bank10 = bytes(candidate[0x10 * 0x10000:(0x10 + 1) * 0x10000])
    for i, (a, b) in enumerate(zip(before_bank10, after_bank10)):
        if a != b:
            allowed.append((0x10 * 0x10000 + i, 0x10 * 0x10000 + i + 1))

    # Replace exactly the three E5 18 bodies with native 2-byte bank10 tokens.
    target_checks = []
    sb = stock_base(parent)
    for logical, spec in sorted(TARGETS.items()):
        old_payload, old_term = record(parent, logical)
        prefix = bytes(spec["prefix"])
        if not old_payload.startswith(prefix):
            raise BuildError(f"prefix drift at {logical:06X}")
        old_body = old_payload[len(prefix):]
        if not old_body.startswith(b"\xE5\x18"):
            raise BuildError(f"expected E5 18 body at {logical:06X}: {old_body.hex()}")
        token = token_from_dict_index(int(spec["slot"]))
        body_capacity = len(old_payload) - len(prefix)
        if body_capacity < 2:
            raise BuildError(f"body too short at {logical:06X}")
        new_payload = prefix + token + (b"\x01" * (body_capacity - 2))
        start = sb + logical
        candidate[start:start + len(old_payload)] = new_payload
        allowed.append((start, start + len(old_payload)))
        target_checks.append({
            "abs": f"{logical:06X}",
            "before_payload": old_payload.hex().upper(),
            "after_payload": new_payload.hex().upper(),
            "prefix": prefix.hex().upper(),
            "slot": f"{int(spec['slot']):04X}",
            "ko": spec["ko"],
            "terminator": f"{old_term - sb:06X}",
        })

    # Cannon terminology correction: private ext3 phrase bytes only.
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

    # Candidate-bound verification.
    failures = []
    for logical, spec in sorted(TARGETS.items()):
        before_payload, before_term = record(parent, logical)
        after_payload, after_term = record(result, logical)
        prefix = bytes(spec["prefix"])
        token = token_from_dict_index(int(spec["slot"]))
        expected = prefix + token + b"\x01" * (len(before_payload) - len(prefix) - 2)
        rendered = result_dictionary.expand(token, tbl).rstrip("　")
        ok = (
            after_payload == expected
            and after_term == before_term
            and b"\xE5\x18" not in after_payload[len(prefix):]
            and rendered == str(spec["ko"])
        )
        if not ok:
            failures.append({"abs": f"{logical:06X}", "rendered": rendered, "payload": after_payload.hex()})

    # 611E10 (Ain control), 611E13 and onward must remain byte-exact to main.
    neighbor_checks = []
    for logical in (0x611E10, 0x611E13, 0x611E20, 0x611E2D, 0x611E32, 0x611E3C, 0x611E3F):
        a, at = record(parent, logical)
        b, bt = record(result, logical)
        ok = a == b and at == bt
        neighbor_checks.append({"abs": f"{logical:06X}", "exact": ok, "payload": b.hex().upper()})
        if not ok:
            failures.append(neighbor_checks[-1])

    cannon_checks = []
    for logical, (idx, before, after) in sorted(CANNON.items()):
        p0, t0 = record(parent, logical)
        p1, t1 = record(result, logical)
        _pre, body, _ = split_prefix_body(p1)
        rendered = result_dictionary.expand(body, tbl).rstrip("　")
        ok = p0 == p1 and t0 == t1 and rendered == after
        cannon_checks.append({"abs": f"{logical:06X}", "rendered": rendered, "ok": ok})
        if not ok:
            failures.append(cannon_checks[-1])

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
                if len(unaccounted) >= 20:
                    break
        if len(unaccounted) >= 20:
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
        "generated_by": "tools/build_sig_scenario_native_bank10_candidate.py",
        "main_tip_modified": False,
        "root_cause_model": {
            "active_scene_binding": "611DF0 -> 611DF8 -> 611E05 -> 611E10(08 07 Ain) -> 611E13",
            "jp_original_probe": "event continues when the early block is restored to native JP structure",
            "dictionary_collision_proof": "611E13 original F843 uses stock index 0843, currently stale/reused as battle voice 5E2BF2; Ain sprite is original-correct",
            "new_strategy": "no new runtime hook; first three lines only use the already-promoted native EF7..FFF bank10 dictionary",
        },
        "inputs": {"main_sha256": sha(parent), "live_sav_sha256": sha(live_sav)},
        "targets": target_checks,
        "slot_proofs": slot_proofs,
        "bank10_write": wr,
        "neighbors": neighbor_checks,
        "cannon": cannon_checks,
        "counts": {
            "scenario_records": len(TARGETS),
            "bank10_slots": len(slots),
            "cannon_records": len(CANNON),
            "diff_runs": len(runs),
            "diff_bytes": sum(int(r["end"],16)-int(r["start"],16) for r in runs),
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
        "candidate": str(OUT_ROM), "sha256": sha(result), "checksum": f"{checksum:04X}",
        "scenario": len(TARGETS), "bank10_phrase_end": f"{wr['phrase_end']:04X}",
        "diff_runs": report["counts"]["diff_runs"], "diff_bytes": report["counts"]["diff_bytes"],
    }, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
