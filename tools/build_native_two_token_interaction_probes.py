#!/usr/bin/env python3
"""Build interaction probes after all four single native-two-token rewrites tested clean.

Clean base is exact historical AC146 stage2.  The historical five-record bundle is
known bad.  This builder isolates whether the seam requires 63EB4A, the other four
as a group, retired slot 09C9, or an interaction among them.  Diagnostic only.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import load_rom, stock_base, read_encoded_z_safe, update_ws_checksum

BASE = ROOT / "out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc"
HIST_BAD_BUNDLE = ROOT / "out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc"
LIVE_MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = ROOT / "out/patch/native_two_token_interaction_probes_report.json"

BASE_SHA = "ac146f120b3656caf150480428d3ee118b4e471433bc071dbfcd6d11029fd9c3"
SAVE_SIZE = 32768

# stage2/base payload -> native historical payload
RECORDS = {
    0x63E6E4: (bytes.fromhex("173418E5184D40"), bytes.fromhex("173418F132F044"), "잘 들어！！"),
    0x63EB4A: (bytes.fromhex("173418E5184966"), bytes.fromhex("173418F9C9F191"), "죄송합니다…… (09C9)"),
    0x63F0BD: (bytes.fromhex("173418E518F2A6"), bytes.fromhex("173418F8EFF191"), "흠……"),
    0x63F483: (bytes.fromhex("173418E5181C41"), bytes.fromhex("173418F065F191"), "제로……"),
    0x63F67C: (bytes.fromhex("173418E5184831"), bytes.fromhex("173418F06EF60C"), "윽……！"),
}
ALT_63EB4A = bytes.fromhex("173418F6CFF191")  # existing 06CF='죄송합니다。' + 0191='……'
SLOT09C9_LOGICAL = 0x5FE690
SLOT09C9_LEN_PLUS_NUL = 15

FOUR = [0x63E6E4, 0x63F0BD, 0x63F483, 0x63F67C]
PAIR_A = [0x63E6E4, 0x63F0BD]
PAIR_B = [0x63F483, 0x63F67C]


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def payload(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=64)
    if got is None:
        raise RuntimeError(f"cannot read {logical:06X}")
    return bytes(got[0]), int(got[1] - sb)


def build(base: bytes, hist_bad: bytes, records: list[int], mode_63eb4a: str = "none") -> bytearray:
    out = bytearray(base)
    sb = stock_base(base)
    for logical in records:
        if logical == 0x63EB4A:
            raise RuntimeError("63EB4A must be selected by mode_63eb4a")
        out[sb + logical: sb + logical + 7] = RECORDS[logical][1]
    if mode_63eb4a == "historical":
        out[sb + 0x63EB4A: sb + 0x63EB4A + 7] = RECORDS[0x63EB4A][1]
        # Apply exactly the historical repurposed 09C9 storage bytes.
        off = sb + SLOT09C9_LOGICAL
        out[off:off + SLOT09C9_LEN_PLUS_NUL] = hist_bad[off:off + SLOT09C9_LEN_PLUS_NUL]
    elif mode_63eb4a == "no_retired":
        out[sb + 0x63EB4A: sb + 0x63EB4A + 7] = ALT_63EB4A
    elif mode_63eb4a != "none":
        raise RuntimeError(mode_63eb4a)
    update_ws_checksum(out)
    return out


def emit(name: str, rom: bytearray, tag: str, selected: list[str]) -> dict:
    out_rom = ROOT / "out/patch" / f"{name}.wsc"
    out_save = ROOT / "sram" / f"{name}.sav"
    out_rom.write_bytes(rom)
    shutil.copyfile(LIVE_SAVE, out_save)
    return {
        "tag": tag,
        "selected": selected,
        "rom": str(out_rom.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha(rom),
        "checksum": f"{rom[-2] | (rom[-1] << 8):04X}",
        "saveram": str(out_save.relative_to(ROOT)).replace("\\", "/"),
        "saveram_sha256": sha(out_save.read_bytes()),
    }


def main() -> int:
    base = bytes(load_rom(BASE))
    hist_bad = bytes(load_rom(HIST_BAD_BUNDLE))
    if sha(base) != BASE_SHA:
        raise RuntimeError(f"base SHA drift {sha(base)}")
    if not LIVE_SAVE.is_file() or LIVE_SAVE.stat().st_size != SAVE_SIZE:
        raise RuntimeError("live SaveRAM missing/wrong size")
    main_before = sha(LIVE_MAIN.read_bytes())
    save_before = sha(LIVE_SAVE.read_bytes())
    sb = stock_base(base)

    terms = {}
    for logical, (before, _, _) in RECORDS.items():
        p, term = payload(base, logical)
        if p != before:
            raise RuntimeError(f"base payload drift {logical:06X}: {p.hex().upper()}")
        terms[logical] = term

    probes = []
    specs = [
        ("ending_seam_native_interact_a_63eb4a_historical_only", [], "historical",
         "63EB4A only, including historical repurposed slot09C9", ["63EB4A historical", "slot09C9"]),
        ("ending_seam_native_interact_b_four_combined", FOUR, "none",
         "other four rewrites combined, no 63EB4A and no dictionary change", [f"{x:06X}" for x in FOUR]),
        ("ending_seam_native_interact_c_five_no_retired", FOUR, "no_retired",
         "all five record grammars, but 63EB4A uses existing 06CF+0191 and slot09C9 is untouched", [f"{x:06X}" for x in FOUR] + ["63EB4A alt-no-retired"]),
        ("ending_seam_native_interact_d_63eb4a_no_retired_only", [], "no_retired",
         "63EB4A only with existing 06CF+0191, no dictionary change", ["63EB4A alt-no-retired"]),
        ("ending_seam_native_interact_e_pair_e6e4_f0bd", PAIR_A, "none",
         "pair split A among the four", [f"{x:06X}" for x in PAIR_A]),
        ("ending_seam_native_interact_f_pair_f483_f67c", PAIR_B, "none",
         "pair split B among the four", [f"{x:06X}" for x in PAIR_B]),
        ("ending_seam_native_interact_g_hist63eb4a_plus_pair_a", PAIR_A, "historical",
         "historical 63EB4A/09C9 plus pair A", ["63EB4A historical", "slot09C9"] + [f"{x:06X}" for x in PAIR_A]),
        ("ending_seam_native_interact_h_hist63eb4a_plus_pair_b", PAIR_B, "historical",
         "historical 63EB4A/09C9 plus pair B", ["63EB4A historical", "slot09C9"] + [f"{x:06X}" for x in PAIR_B]),
        ("ending_seam_native_interact_i_alt63eb4a_plus_pair_a", PAIR_A, "no_retired",
         "no-retired 63EB4A plus pair A", ["63EB4A alt-no-retired"] + [f"{x:06X}" for x in PAIR_A]),
        ("ending_seam_native_interact_j_alt63eb4a_plus_pair_b", PAIR_B, "no_retired",
         "no-retired 63EB4A plus pair B", ["63EB4A alt-no-retired"] + [f"{x:06X}" for x in PAIR_B]),
    ]
    for name, recs, mode, tag, selected in specs:
        rom = build(base, hist_bad, list(recs), mode)
        # Every record keeps its original terminator and 7-byte extent.
        for logical in RECORDS:
            p, term = payload(rom, logical)
            if term != terms[logical]:
                raise RuntimeError(f"terminator drift {name} {logical:06X}")
            expected = RECORDS[logical][0]
            if logical in recs:
                expected = RECORDS[logical][1]
            if logical == 0x63EB4A:
                expected = RECORDS[logical][1] if mode == "historical" else ALT_63EB4A if mode == "no_retired" else RECORDS[logical][0]
            if p != expected:
                raise RuntimeError(f"payload drift {name} {logical:06X}: {p.hex().upper()} != {expected.hex().upper()}")
        probes.append(emit(name, rom, tag, selected))

    # Historical full bundle must remain known-bad reference, and A+B union with historical 63EB4A
    # should reconstruct it exactly (apart from checksum already deterministic).
    recon = build(base, hist_bad, FOUR, "historical")
    if bytes(recon) != hist_bad:
        raise RuntimeError("historical five-record bundle reconstruction mismatch")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_native_two_token_interaction_probes.py",
        "ok": True,
        "facts": {
            "clean_base_sha256": sha(base),
            "known_bad_historical_bundle_sha256": sha(hist_bad),
            "four_single_probes_user_result": "all clean",
            "historical_full_bundle_reconstructed_byte_exact": True,
        },
        "recommended_first_tests": [
            "ending_seam_native_interact_a_63eb4a_historical_only.wsc",
            "ending_seam_native_interact_b_four_combined.wsc",
            "ending_seam_native_interact_c_five_no_retired.wsc",
        ],
        "decision": {
            "A_bad": "63EB4A historical change alone is sufficient; compare D to distinguish record-vs-slot form.",
            "A_clean_B_bad": "interaction exists among the other four; test E and F next.",
            "A_clean_B_clean_C_bad": "63EB4A record grammar interacts with one or more of the other four without requiring slot09C9; test I/J.",
            "A_clean_B_clean_C_clean": "historical slot09C9 participation is required; test G/H to split which pair interacts with it.",
        },
        "probes": probes,
        "main_tip_unchanged": sha(LIVE_MAIN.read_bytes()) == main_before,
        "live_saveram_unchanged": sha(LIVE_SAVE.read_bytes()) == save_before,
        "promotion": "blocked_pending_user_runtime_validation",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
