#!/usr/bin/env python3
"""Build a current-main candidate that removes the unsafe 09C9 retired-slot reuse.

The 2026-08-09 structural repair converted five exact source-native-two-token
records away from a single E5 18 portal. Runtime bisect now shows that the
native-two-token repair bundle introduces the ending seam. Four records can use
pre-existing stock tokens without dictionary writes. 63EB4A (すみません……)
was the only one that required repurposing stock slot 09C9.

This candidate:
  * keeps the other four exact-native-two-token repairs byte-exact,
  * restores stock slot 09C9 byte-for-byte from the known-good 2026-08-09 22:47 TIP,
  * rewrites 63EB4A as two already-existing stock tokens:
        06CF = 죄송합니다。
        0191 = ……
    so the required two-token iteration grammar remains intact without any new
    stock dictionary storage,
  * preserves all later current-main changes and live SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3
from build_sig_scenario_stock_native_chain_candidate import current_ext3_nested_parents, current_nested_parents
from build_terminology_retranslation_candidate import stock_storage_proof
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base, token_from_dict_index, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
GOOD = Path(r"D:\legacy_260814\out\patch\backup\20260809_224743_pre_runtime_measured_followup_structural\monoeye_ko_expanded.wsc")
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT_ROM = ROOT / "out/patch/native_two_token_no_retired_slot_candidate.wsc"
OUT_SAVE = ROOT / "sram/native_two_token_no_retired_slot_candidate.sav"
REPORT = ROOT / "out/patch/native_two_token_no_retired_slot_candidate_report.json"

EXPECTED_MAIN_SHA = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
EXPECTED_GOOD_SHA = "fb6629d89cfd0dd8f48a621af5c0175d4602f113abec5d781b54b32b14efa86d"
EXT_META = {"stock_count":3831,"slot_count":265,"ext_ptr_off":"0000","ext_seg":"10","ext_in_expansion":True}
EXT3_META = {"num_banks":16,"exp_seg0":"11"}
SLOT = 0x09C9
TARGET = 0x63EB4A
KEEP_NATIVE = (0x63E6E4, 0x63F0BD, 0x63F483, 0x63F67C)
EXPECTED_TARGET_CURRENT = bytes.fromhex("173418F9C9F191")
EXPECTED_KEEP = {
    0x63E6E4: bytes.fromhex("173418F132F044"),
    0x63F0BD: bytes.fromhex("173418F8EFF191"),
    0x63F483: bytes.fromhex("173418F065F191"),
    0x63F67C: bytes.fromhex("173418F06EF60C"),
}
NEW_TARGET = bytes.fromhex("173418F6CFF191")


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def payload(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable payload {logical:06X}")
    return bytes(got[0]), int(got[1])


def main() -> int:
    parent = bytes(load_rom(MAIN))
    good = bytes(load_rom(GOOD))
    original = bytes(load_rom(ORIGINAL))
    if sha(parent) != EXPECTED_MAIN_SHA:
        raise RuntimeError(f"main identity drift: {sha(parent)}")
    if sha(good) != EXPECTED_GOOD_SHA:
        raise RuntimeError(f"known-good identity drift: {sha(good)}")
    if not LIVE_SAVE.is_file() or LIVE_SAVE.stat().st_size != 32768:
        raise RuntimeError("live SaveRAM missing/wrong size")

    tbl = Tbl.load(TBL_PATH)
    d_parent = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    d_good = make_dictionary_ext3(good, EXT_META, EXT3_META)

    # Bind the current target and the four repairs that must remain unchanged.
    cur, cur_term = payload(parent, TARGET)
    if cur != EXPECTED_TARGET_CURRENT:
        raise RuntimeError(f"63EB4A drift: {cur.hex().upper()}")
    keep_before = {}
    for logical, expected in EXPECTED_KEEP.items():
        got, term = payload(parent, logical)
        if got != expected:
            raise RuntimeError(f"keep target drift {logical:06X}: {got.hex().upper()}")
        keep_before[logical] = (got, term)

    # Confirm token semantics in the current main before relying on them.
    if d_parent.expand_index(0x06CF, tbl).rstrip("　 \t") != "죄송합니다。":
        raise RuntimeError("06CF semantic drift")
    if d_parent.expand_index(0x0191, tbl).rstrip("　 \t") != "……":
        raise RuntimeError("0191 semantic drift")
    if d_parent.expand_index(SLOT, tbl).rstrip("　 \t") != "죄송합니다":
        raise RuntimeError("09C9 current semantic drift")

    # 09C9 must still be consumed only by 63EB4A and have no dictionary nesting.
    union = build_reference_union(original, parent, ext_meta=EXT_META, ext3_meta=EXT3_META)
    working = [c for c in union.consumers_for(SLOT) if "working" in c.seen_in]
    working_addrs = sorted({int(c.abs) for c in working})
    if working_addrs != [TARGET]:
        raise RuntimeError(f"09C9 current consumers drift: {[f'{x:06X}' for x in working_addrs]}")
    nested = current_nested_parents(d_parent, {SLOT})[SLOT]
    ext_nested = current_ext3_nested_parents(d_parent, {SLOT})[SLOT]
    if nested or ext_nested:
        raise RuntimeError(f"09C9 nested dependency exists: native={nested}, ext3={ext_nested}")

    pcur = stock_storage_proof(d_parent, SLOT)
    pgood = stock_storage_proof(d_good, SLOT)
    if not pcur["ok"] or not pgood["ok"]:
        raise RuntimeError(f"09C9 storage proof failed current={pcur} good={pgood}")
    if int(pcur["entry_abs"]) != int(pgood["entry_abs"]):
        raise RuntimeError("09C9 storage address changed")
    start = int(pgood["entry_abs"])
    old_len = int(pgood["old_len"])
    good_blob = good[start:start + old_len + 1]

    candidate = bytearray(parent)
    # Restore the complete known-good stock-entry storage extent including NUL.
    candidate[start:start + old_len + 1] = good_blob
    # Preserve prefix and exact 4-byte/two-token body grammar.
    sb = stock_base(candidate)
    candidate[sb + TARGET:sb + TARGET + len(NEW_TARGET)] = NEW_TARGET

    # Verify target extent/terminator did not move and render is expected.
    new_payload, new_term = payload(candidate, TARGET)
    if new_payload != NEW_TARGET or new_term != cur_term:
        raise RuntimeError("63EB4A boundary drift")
    d_candidate = make_dictionary_ext3(candidate, EXT_META, EXT3_META)
    if d_candidate.expand_index(SLOT, tbl).rstrip("　 \t") != d_good.expand_index(SLOT, tbl).rstrip("　 \t"):
        raise RuntimeError("09C9 did not restore known-good semantic")
    body_text = d_candidate.expand(new_payload[3:], tbl).rstrip("　 \t")
    if body_text != "죄송합니다。……":
        raise RuntimeError(f"alternate render drift: {body_text!r}")

    # Four other native-two-token repairs must remain byte-exact.
    for logical, (before_payload, before_term) in keep_before.items():
        after_payload, after_term = payload(candidate, logical)
        if after_payload != before_payload or after_term != before_term:
            raise RuntimeError(f"unrelated native-two-token repair changed {logical:06X}")

    # 09C9 must have no working consumer after replacement.
    post_union = build_reference_union(original, bytes(candidate), ext_meta=EXT_META, ext3_meta=EXT3_META)
    post_working = [c for c in post_union.consumers_for(SLOT) if "working" in c.seen_in]
    if post_working:
        raise RuntimeError(f"09C9 still has working consumers: {[f'{int(c.abs):06X}' for c in post_working]}")

    update_ws_checksum(candidate)
    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_SAVE.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(candidate)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)

    changed = [i for i,(a,b) in enumerate(zip(parent,candidate)) if a != b]
    non_checksum = [i for i in changed if i not in (len(candidate)-2,len(candidate)-1)]
    allowed = set(range(start,start+old_len+1)) | set(range(sb+TARGET,sb+TARGET+len(NEW_TARGET)))
    unexpected = [i for i in non_checksum if i not in allowed]
    if unexpected:
        raise RuntimeError(f"out-of-scope writes: {[hex(x) for x in unexpected[:20]]}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_native_two_token_no_retired_slot_candidate.py",
        "ok": True,
        "parent": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(parent)},
        "candidate": {"path": str(OUT_ROM.relative_to(ROOT)), "sha256": sha(candidate), "checksum": candidate[-2:].hex().upper()},
        "paired_saveram": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(OUT_SAVE.read_bytes()), "byte_exact_live": OUT_SAVE.read_bytes() == LIVE_SAVE.read_bytes()},
        "causal_change": {
            "restored_stock_slot": "09C9",
            "slot_storage_logical": f"{start-stock_base(parent):06X}",
            "slot_current_text": "죄송합니다",
            "slot_restored_text": d_good.expand_index(SLOT,tbl).rstrip("　 \t"),
            "current_working_consumers_before": [f"{x:06X}" for x in working_addrs],
            "working_consumers_after": 0,
            "native_nested_before": 0,
            "ext3_nested_before": 0,
        },
        "63EB4A": {
            "source_jp": "すみません……",
            "before_hex": cur.hex().upper(),
            "after_hex": new_payload.hex().upper(),
            "after_tokens": ["06CF", "0191"],
            "after_text": body_text,
            "exact_two_native_tokens": True,
            "terminator_preserved": new_term == cur_term,
            "note": "Keeps the source two-token iteration grammar without allocating or repurposing stock dictionary storage. Korean full stop is inherited from existing 06CF; ellipsis remains as the second native token.",
        },
        "other_four_native_two_token_repairs_byte_exact": [f"{x:06X}" for x in KEEP_NATIVE],
        "changed_nonchecksum_bytes": len(non_checksum),
        "unexpected_changes": 0,
        "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
        "live_saveram_unchanged": sha(LIVE_SAVE.read_bytes()),
        "runtime_validation": "Cold reset/replay ending seam and visit 63EB4A scene; do not use a savestate that serializes old VRAM/runtime state.",
        "promotion": "blocked_pending_user_runtime_validation",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
