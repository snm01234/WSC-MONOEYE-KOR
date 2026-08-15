#!/usr/bin/env python3
"""Build historical-63EB4A/09C9 interaction matrix and checksum-control probes.

User-confirmed facts:
- AC146 stage2 base is ending-seam clean.
- Each of the other four native-two-token rewrites is individually clean.
- A = historical 63EB4A + repurposed slot09C9 is clean.
- B = all other four combined is clean.
- C = all five record grammars with no retired slot is clean.
- Historical A + all four is bad.

Therefore the remaining cause is an interaction between historical A and a subset
of the four, unless the cart checksum bytes themselves are participating.  This
builder emits all A+subset probes of size 1..3, plus raw checksum swap controls.
Diagnostic only; never promote.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import load_rom, stock_base, read_encoded_z_safe, update_ws_checksum

BASE = ROOT / "out/patch/ending_seam_stage2_duplicate_ac146_probe.wsc"
BAD = ROOT / "out/patch/ending_seam_stage2_plus_native_two_token_probe.wsc"
LIVE_MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = ROOT / "out/patch/native_two_token_hist_interaction_matrix_report.json"
BASE_SHA = "ac146f120b3656caf150480428d3ee118b4e471433bc071dbfcd6d11029fd9c3"
BAD_SHA = "1e2b9b23c8f8d82e50c0f11c142e5ee655e090d18178107960661fa94d52e31b"

RECORDS = {
    0x63E6E4: (bytes.fromhex("173418E5184D40"), bytes.fromhex("173418F132F044")),
    0x63EB4A: (bytes.fromhex("173418E5184966"), bytes.fromhex("173418F9C9F191")),
    0x63F0BD: (bytes.fromhex("173418E518F2A6"), bytes.fromhex("173418F8EFF191")),
    0x63F483: (bytes.fromhex("173418E5181C41"), bytes.fromhex("173418F065F191")),
    0x63F67C: (bytes.fromhex("173418E5184831"), bytes.fromhex("173418F06EF60C")),
}
FOUR = [0x63E6E4, 0x63F0BD, 0x63F483, 0x63F67C]
SLOT09C9_LOGICAL = 0x5FE690
SLOT09C9_LEN_PLUS_NUL = 15


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def payload(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=64)
    if got is None:
        raise RuntimeError(f"cannot read {logical:06X}")
    return bytes(got[0]), int(got[1] - sb)


def build_hist_a_plus(base: bytes, bad: bytes, subset: tuple[int, ...]) -> bytearray:
    out = bytearray(base)
    sb = stock_base(base)
    # Historical A = 63EB4A record + exact historical slot09C9 bytes.
    out[sb + 0x63EB4A:sb + 0x63EB4A + 7] = RECORDS[0x63EB4A][1]
    slot = sb + SLOT09C9_LOGICAL
    out[slot:slot + SLOT09C9_LEN_PLUS_NUL] = bad[slot:slot + SLOT09C9_LEN_PLUS_NUL]
    for logical in subset:
        out[sb + logical:sb + logical + 7] = RECORDS[logical][1]
    update_ws_checksum(out)
    return out


def emit(name: str, rom: bytes | bytearray, tag: str, selected: list[str], valid_checksum: bool = True) -> dict:
    out_rom = ROOT / "out/patch" / f"{name}.wsc"
    out_save = ROOT / "sram" / f"{name}.sav"
    out_rom.write_bytes(bytes(rom))
    shutil.copyfile(LIVE_SAVE, out_save)
    return {
        "tag": tag,
        "selected": selected,
        "rom": str(out_rom.relative_to(ROOT)).replace("\\", "/"),
        "sha256": sha(rom),
        "checksum_bytes": bytes(rom[-2:]).hex().upper(),
        "checksum_word": f"{rom[-2] | (rom[-1] << 8):04X}",
        "checksum_valid": valid_checksum,
        "saveram": str(out_save.relative_to(ROOT)).replace("\\", "/"),
        "saveram_sha256": sha(out_save.read_bytes()),
    }


def main() -> int:
    base = bytes(load_rom(BASE))
    bad = bytes(load_rom(BAD))
    if sha(base) != BASE_SHA:
        raise RuntimeError(f"base SHA drift {sha(base)}")
    if sha(bad) != BAD_SHA:
        raise RuntimeError(f"bad SHA drift {sha(bad)}")
    if len(base) != len(bad) or len(base) != 16_777_216:
        raise RuntimeError("ROM size drift")
    if not LIVE_SAVE.is_file() or LIVE_SAVE.stat().st_size != 32768:
        raise RuntimeError("live SaveRAM missing/wrong size")

    main_before = sha(LIVE_MAIN.read_bytes())
    save_before = sha(LIVE_SAVE.read_bytes())

    # Pin base record extents/terminators before generating matrix.
    terms = {}
    for logical, (before, _) in RECORDS.items():
        p, term = payload(base, logical)
        if p != before:
            raise RuntimeError(f"base payload drift {logical:06X}: {p.hex().upper()}")
        terms[logical] = term

    rows = []
    for size in (1, 2, 3):
        for subset in itertools.combinations(FOUR, size):
            rom = build_hist_a_plus(base, bad, subset)
            # Full set is not emitted here; known bad reference already exists.
            for logical in RECORDS:
                p, term = payload(rom, logical)
                if term != terms[logical]:
                    raise RuntimeError(f"terminator drift {subset} {logical:06X}")
                if logical == 0x63EB4A:
                    expected = RECORDS[logical][1]
                elif logical in subset:
                    expected = RECORDS[logical][1]
                else:
                    expected = RECORDS[logical][0]
                if p != expected:
                    raise RuntimeError(f"payload drift {subset} {logical:06X}")
            suffix = "_".join(f"{x:06x}" for x in subset)
            name = f"ending_seam_histA_plus_{size}_{suffix}_probe"
            rows.append(emit(
                name,
                rom,
                f"historical 63EB4A/09C9 + subset size {size}",
                ["63EB4A historical", "slot09C9"] + [f"{x:06X}" for x in subset],
            ))

    # Verify A + all four reconstructs the user-confirmed bad historical bundle.
    full = build_hist_a_plus(base, bad, tuple(FOUR))
    if bytes(full) != bad:
        raise RuntimeError("A + all four does not reconstruct known-bad bundle")

    # Checksum controls. These intentionally have invalid cart checksums. They only
    # swap the final two bytes, so if the emulator boots them they isolate whether
    # the checksum field itself can affect the visual symptom.
    clean_with_bad_checksum = bytearray(base)
    clean_with_bad_checksum[-2:] = bad[-2:]
    bad_with_clean_checksum = bytearray(bad)
    bad_with_clean_checksum[-2:] = base[-2:]
    checksum_controls = [
        emit(
            "ending_seam_checksum_control_clean_payload_bad_checksum",
            clean_with_bad_checksum,
            "clean AC146 payload with known-bad bundle checksum bytes only",
            [f"checksum={bad[-2:].hex().upper()}"],
            valid_checksum=False,
        ),
        emit(
            "ending_seam_checksum_control_bad_payload_clean_checksum",
            bad_with_clean_checksum,
            "known-bad bundle payload with clean AC146 checksum bytes only",
            [f"checksum={base[-2:].hex().upper()}"],
            valid_checksum=False,
        ),
    ]

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_native_two_token_hist_interaction_matrix.py",
        "ok": True,
        "facts": {
            "base_clean_sha256": sha(base),
            "known_bad_bundle_sha256": sha(bad),
            "user_measured_A_B_C_all_clean": True,
            "A_plus_all_four_reconstructs_bad_byte_exact": True,
        },
        "matrix_probes": rows,
        "checksum_controls": checksum_controls,
        "recommended_next": [
            "First test existing G and H (historical A + pair groups).",
            "If one is bad, use size-1 matrix probes for its members to determine whether one additional rewrite is sufficient.",
            "If both G/H are clean, test cross-pair size-2 matrix probes; if all pairs are clean, move to size-3 probes.",
            "Checksum controls are optional but useful because the symptom is nonlinear across otherwise independent dialogue writes.",
        ],
        "main_tip_unchanged": sha(LIVE_MAIN.read_bytes()) == main_before,
        "live_saveram_unchanged": sha(LIVE_SAVE.read_bytes()) == save_before,
        "promotion": "blocked_pending_user_runtime_validation",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
