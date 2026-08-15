#!/usr/bin/env python3
"""Build four single-record probes for the remaining native-two-token seam suspects.

Base is the exact historical AC146 stage2 ROM, which the user measured as clean.
Each probe applies exactly one of the four native-two-token record rewrites that
remain after excluding 63EB4A/retired slot 09C9.  No dictionary storage is changed.
Diagnostic only; never promote without runtime validation.
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
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = ROOT / "out/patch/native_two_token_four_single_probes_report.json"
EXPECTED_BASE_SHA = "ac146f120b3656caf150480428d3ee118b4e471433bc071dbfcd6d11029fd9c3"
EXPECTED_SAVE_SIZE = 32768

# logical: (stage2 payload, native-two-token payload, label)
TARGETS = {
    0x63E6E4: ("173418E5184D40", "173418F132F044", "잘 들어！！"),
    0x63F0BD: ("173418E518F2A6", "173418F8EFF191", "흠……"),
    0x63F483: ("173418E5181C41", "173418F065F191", "제로……"),
    0x63F67C: ("173418E5184831", "173418F06EF60C", "윽……！"),
}


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def payload(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=64)
    if got is None:
        raise RuntimeError(f"cannot read {logical:06X}")
    return bytes(got[0]), int(got[1] - sb)


def main() -> int:
    base = bytes(load_rom(BASE))
    if sha(base) != EXPECTED_BASE_SHA:
        raise RuntimeError(f"base SHA drift: {sha(base)}")
    if not LIVE_SAVE.is_file() or LIVE_SAVE.stat().st_size != EXPECTED_SAVE_SIZE:
        raise RuntimeError("live SaveRAM missing/wrong size")
    live_save_sha = sha(LIVE_SAVE.read_bytes())
    sb = stock_base(base)

    # Pin all four base payloads and terminators before writing anything.
    base_terms: dict[int, int] = {}
    for logical, (before_hex, _, _) in TARGETS.items():
        p, term = payload(base, logical)
        if p != bytes.fromhex(before_hex):
            raise RuntimeError(f"base payload drift {logical:06X}: {p.hex().upper()}")
        base_terms[logical] = term

    rows = []
    for logical, (before_hex, after_hex, label) in TARGETS.items():
        candidate = bytearray(base)
        before = bytes.fromhex(before_hex)
        after = bytes.fromhex(after_hex)
        if len(before) != len(after):
            raise RuntimeError(f"extent mismatch {logical:06X}")
        start = sb + logical
        candidate[start:start + len(after)] = after
        update_ws_checksum(candidate)

        # The selected record must be exact; the other three must stay byte-exact to AC146.
        p, term = payload(candidate, logical)
        if p != after or term != base_terms[logical]:
            raise RuntimeError(f"selected record verification failed {logical:06X}")
        for other in TARGETS:
            if other == logical:
                continue
            op, ot = payload(candidate, other)
            if op != bytes.fromhex(TARGETS[other][0]) or ot != base_terms[other]:
                raise RuntimeError(f"non-selected record changed {other:06X}")

        stem = f"ending_seam_native_single_{logical:06x}_probe"
        out_rom = ROOT / "out/patch" / f"{stem}.wsc"
        out_save = ROOT / "sram" / f"{stem}.sav"
        out_rom.write_bytes(candidate)
        shutil.copyfile(LIVE_SAVE, out_save)
        if sha(out_save.read_bytes()) != live_save_sha:
            raise RuntimeError(f"SaveRAM copy mismatch {logical:06X}")

        diffs = [i for i, (a, b) in enumerate(zip(base, candidate)) if a != b]
        non_checksum = [i for i in diffs if i not in (len(candidate)-2, len(candidate)-1)]
        expected_changed = {start + i for i, (a, b) in enumerate(zip(before, after)) if a != b}
        if set(non_checksum) != expected_changed:
            raise RuntimeError(f"unexpected diff scope {logical:06X}")

        rows.append({
            "logical": f"{logical:06X}",
            "label": label,
            "rom": str(out_rom.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(candidate),
            "checksum": f"{candidate[-2] | (candidate[-1] << 8):04X}",
            "saveram": str(out_save.relative_to(ROOT)).replace("\\", "/"),
            "saveram_sha256": live_save_sha,
            "before_hex": before.hex().upper(),
            "after_hex": after.hex().upper(),
            "terminator": f"{term:06X}",
            "changed_nonchecksum_bytes": len(non_checksum),
            "dictionary_changes": 0,
        })

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_native_two_token_four_single_probes.py",
        "ok": True,
        "clean_base": {"path": str(BASE.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(base)},
        "probes": rows,
        "interpretation": {
            "single_bad": "That record rewrite alone is sufficient to introduce the ending seam.",
            "all_single_clean": "No single rewrite is sufficient; test pairwise interaction next.",
        },
        "main_tip_changed": False,
        "live_saveram_changed": False,
        "promotion": "blocked_pending_user_runtime_validation",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
