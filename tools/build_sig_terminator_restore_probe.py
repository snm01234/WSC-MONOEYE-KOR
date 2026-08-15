#!/usr/bin/env python3
"""Minimal runtime probe for the Sig scenario double-NUL boundary hypothesis.

Only the first spoken record at 611DF0 is changed.  The promoted TIP moved its
terminator from the original 611DF6 to 611DF7 in the historical P2 local-ext3
expansion, consuming the second NUL immediately before the 611DF8 continuation
record (lead 18).  This probe restores the exact original boundary shape while
keeping the first line Korean through one retired stock 2-byte token.

611DF8 and 611E05 are deliberately left byte-exact to the current main.  If the
reported こ / early-end disappears, the causal variable is the lost separator
NUL rather than the continuation record's dictionary format.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_terminology_retranslation_candidate import encode, stock_storage_proof
from expand_dictionary import iter_dict_indices
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/sig_terminator_restore_probe_candidate.wsc"
OUT_SAVE = ROOT / "sram/sig_terminator_restore_probe_candidate.sav"
OUT_REPORT = ROOT / "out/patch/sig_terminator_restore_probe_report.json"
EXPECTED_MAIN = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"
SLOT = 0x04B7
TEXT = "장난치지　마라！"


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def current_ext3_nested(dictionary, slot: int) -> list[int]:
    out = []
    for index in range(0x1000, 0x1000 + int(dictionary.ext3_count)):
        try:
            raw = bytes(dictionary.raw_entry(index))
        except Exception:
            continue
        if slot in set(iter_dict_indices(raw)):
            out.append(index)
    return out


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save = MAIN_SAVE.read_bytes()
    if sha(parent) != EXPECTED_MAIN:
        raise RuntimeError(f"main identity drift: {sha(parent)}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)

    working_consumers = [c for c in union.consumers_for(SLOT) if "working" in c.seen_in]
    native_parents = sorted(union.nested_parents.get(SLOT) or ())
    ext3_parents = current_ext3_nested(dictionary, SLOT)
    proof = stock_storage_proof(dictionary, SLOT)
    encoded = encode(TEXT, tbl)
    if working_consumers or native_parents or ext3_parents or not proof["ok"]:
        raise RuntimeError("retired stock slot 04B7 is no longer safe")
    if len(encoded) != int(proof["old_len"]):
        raise RuntimeError(f"04B7 exact-capacity expectation drifted: {len(encoded)} vs {proof['old_len']}")

    sb = stock_base(parent)
    # Exact preconditions around the causal boundary.
    before = bytes(parent[sb + 0x611DF0: sb + 0x611DF8])
    orig = bytes(original[0x611DF0:0x611DF8])
    if before != bytes.fromhex("173418E518B09600"):
        raise RuntimeError(f"611DF0 current bytes drifted: {before.hex().upper()}")
    if orig != bytes.fromhex("173418FBFC030000"):
        raise RuntimeError(f"611DF0 original bytes drifted: {orig.hex().upper()}")

    candidate = bytearray(parent)
    # In-place phrase replacement; pointer stays exactly where it was.
    entry = int(proof["entry_abs"])
    candidate[entry:entry + len(encoded)] = encoded
    candidate[entry + len(encoded)] = 0

    # Restore original payload capacity/terminator/gap shape:
    # payload = 17 34 18 | F4 B7 | 01, term 00 at DF6, separator 00 at DF7.
    candidate[sb + 0x611DF0: sb + 0x611DF8] = bytes.fromhex("173418F4B7010000")

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)

    # Candidate-bound checks.
    if result[sb + 0x611DF6] != 0 or result[sb + 0x611DF7] != 0:
        raise RuntimeError("double-NUL boundary not restored")
    if result[sb + 0x611DF8:sb + 0x611E05] != parent[sb + 0x611DF8:sb + 0x611E05]:
        raise RuntimeError("611DF8 continuation changed; probe must isolate 611DF0")
    if result[sb + 0x611E05:sb + 0x611E10] != parent[sb + 0x611E05:sb + 0x611E10]:
        raise RuntimeError("611E05 continuation changed; probe must isolate 611DF0")

    # Verify actual zstring now terminates at DF6 and renders through the stock token.
    got = read_encoded_z_safe(result, sb + 0x611DF0, max_len=32)
    if got is None or int(got[1]) - sb != 0x611DF6:
        raise RuntimeError(f"terminator did not restore to 611DF6: {got}")
    d2 = make_dictionary_ext3(result, ext_meta, ext3_meta)
    rendered = d2.expand(bytes.fromhex("F4B7"), tbl).rstrip("　")
    if rendered != TEXT:
        raise RuntimeError(f"stock phrase roundtrip mismatch: {rendered!r}")

    OUT_ROM.write_bytes(result)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    report = {
        "ok": True,
        "status": "diagnostic_runtime_probe_only_not_for_promotion",
        "generated_by": "tools/build_sig_terminator_restore_probe.py",
        "main_tip_modified": False,
        "hypothesis": "historical local-ext3 terminator move consumed a structural second NUL before lead-18 continuation",
        "inputs": {"main_sha256": sha(parent), "live_sav_sha256": sha(save)},
        "boundary": {
            "original_611DF0_611DF7": orig.hex().upper(),
            "main_611DF0_611DF7": before.hex().upper(),
            "probe_611DF0_611DF7": result[sb + 0x611DF0:sb + 0x611DF8].hex().upper(),
            "original_terminator": "611DF6",
            "main_terminator": "611DF7",
            "probe_terminator": "611DF6",
            "separator_611DF7": "00",
        },
        "isolation": {
            "611DF8_through_611E04_byte_exact_main": True,
            "611E05_through_611E0F_byte_exact_main": True,
            "runtime_hook_changes": 0,
            "dictionary_pointer_changes": 0,
        },
        "slot": {**proof, "ko": TEXT, "working_consumers": 0, "native_parents": 0, "ext3_parents": 0},
        "outputs": {"rom": str(OUT_ROM), "rom_sha256": sha(result), "sav": str(OUT_SAVE), "sav_sha256": sha(save), "checksum": f"{checksum:04X}"},
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
