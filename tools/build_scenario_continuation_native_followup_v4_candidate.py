#!/usr/bin/env python3
"""Build corrected STAGE21t native-grammar follow-up v4 candidate.

Parent is the user-validated v1 scenario continuation candidate.  v2/v3 are not
parents because runtime retest proved their hypotheses incomplete and v3
patched the wrong duplicate Katejina record.

v4 fixes only:
1. Doctor J follow-up at 635866/635C0C.  Stock slot 0EF3 rendered `그건`
   correctly, then corrupted after the ideographic space.  The wrapper now
   inserts an explicit EC8D Hangul-run marker after the space before the
   existing `아니`/`지만` stock tokens.  Records use Original-like
   `stock-token + native 。` grammar.
2. Actual STAGE21t Katejina row at 63463A.  Original is
   `17 34 18 + FAA5 + F191`; current is `17 34 18 + E5 18 90FA`.
   Re-express the same text as two already-existing 2-byte dictionary tokens:
   F10E=`우`, FF08=`후후후……`.  No new dictionary slot/storage is created.

Main TIP and live SaveRAM are never modified.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = ROOT / "out/patch/scenario_continuation_native_followup_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/scenario_continuation_native_followup_v4_candidate.wsc"
OUT_SAVE = ROOT / "sram/scenario_continuation_native_followup_v4_candidate.sav"
REPORT = ROOT / "out/patch/scenario_continuation_native_followup_v4_candidate_report.json"

EXPECTED_MAIN_SHA = "2bccff99a36cd453bf01225365a40ed4d744049e69d46e726ab427851afa1799"
EXPECTED_PARENT_SHA = "c5283b0804fe47eb77b4acbe0c23cc7fe8a1e649a1052394ed03a3beee398688"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Doctor J wrapper and two duplicated follow-up records.
WRAPPER_SLOT = 0x0EF3
WRAPPER_BEFORE = bytes.fromhex("F171EC8DE7E901F5C5F4180A")
# F05E = 그건.  The ideographic space resets/ends the active Hangul run on the
# observed runtime path, so restart it explicitly before F5C5/F418.
WRAPPER_AFTER = bytes.fromhex("F05E01EC8DF5C5F418")
DOCTOR_TARGETS = (0x635866, 0x635C0C)
DOCTOR_BEFORE = bytes.fromhex("FEF301")
DOCTOR_AFTER = bytes.fromhex("FEF30A")
DOCTOR_ORIGINAL = bytes.fromhex("FEE10A")

# Actual STAGE21t Katejina row from the user screenshot/event sequence.
KATEJINA = 0x63463A
KATEJINA_BEFORE = bytes.fromhex("173418E51890FA")
KATEJINA_AFTER = bytes.fromhex("173418F10EFF08")
KATEJINA_ORIGINAL = bytes.fromhex("173418FAA5F191")
KATEJINA_EXPECTED = "우후후후……"


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def read_record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def main() -> int:
    main_rom = bytes(load_rom(MAIN))
    parent = bytes(load_rom(PARENT))
    original = bytes(load_rom(ORIGINAL))
    live_save = LIVE_SAVE.read_bytes()
    if len(main_rom) != ROM_SIZE or sha(main_rom) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(main_rom)}")
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"v1 parent identity drifted: {sha(parent)}")
    if sha(original) != EXPECTED_ORIGINAL_SHA:
        raise BuildError("Original ROM identity drifted")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)

    # Doctor J wrapper is private to the two runtime records as a stock token.
    wrapper_raw = bytes(dictionary.raw_entry(WRAPPER_SLOT))
    if wrapper_raw != WRAPPER_BEFORE:
        raise BuildError(f"0EF3 wrapper drifted: {wrapper_raw.hex().upper()}")
    if clean(dictionary.expand_index(WRAPPER_SLOT, tbl)) != "그건　아니지만。":
        raise BuildError("0EF3 semantic text drifted")
    wrapper_ptr = dictionary.ptrs[WRAPPER_SLOT]
    same_ptr = [i for i, ptr in enumerate(dictionary.ptrs[:dictionary.stock_count]) if ptr == wrapper_ptr]
    inside_ptr = [
        i for i, ptr in enumerate(dictionary.ptrs[:dictionary.stock_count])
        if i != WRAPPER_SLOT and wrapper_ptr < ptr < wrapper_ptr + len(wrapper_raw) + 1
    ]
    if same_ptr != [WRAPPER_SLOT] or inside_ptr:
        raise BuildError(f"0EF3 physical alias hazard: same={same_ptr} inside={inside_ptr}")
    refs = external_occurrence_map(parent, ext3_aware=True, wanted={WRAPPER_SLOT}).get(WRAPPER_SLOT, [])
    ref_rows = sorted((int(str(row["record_abs"]), 16), int(str(row["token_abs"]), 16)) for row in refs)
    if ref_rows != sorted((a, a) for a in DOCTOR_TARGETS):
        raise BuildError(f"0EF3 consumer drift: {ref_rows}")
    if nested_occurrence_map(dictionary, wanted={WRAPPER_SLOT}, ext3_aware=True).get(WRAPPER_SLOT):
        raise BuildError("0EF3 unexpectedly nested in another stock entry")
    if len(WRAPPER_AFTER) + 1 > len(WRAPPER_BEFORE) + 1:
        raise BuildError("Doctor J wrapper no longer fits its existing physical extent")

    for logical in DOCTOR_TARGETS:
        cur, term = read_record(parent, logical)
        src, src_term = read_record(original, logical)
        if cur != DOCTOR_BEFORE or src != DOCTOR_ORIGINAL or term != src_term:
            raise BuildError(f"Doctor J record drift at {logical:06X}")

    # Actual Katejina record: exact Original/current physical grammar proof.
    kate_cur, kate_term = read_record(parent, KATEJINA)
    kate_src, kate_src_term = read_record(original, KATEJINA)
    if kate_cur != KATEJINA_BEFORE:
        raise BuildError(f"Katejina current record drifted: {kate_cur.hex().upper()}")
    if kate_src != KATEJINA_ORIGINAL or kate_term != kate_src_term:
        raise BuildError("Katejina Original grammar/terminator drifted")
    # Verify the replacement itself already resolves to the exact intended text.
    if clean(dictionary.expand(KATEJINA_AFTER[3:], tbl)) != KATEJINA_EXPECTED:
        raise BuildError("Katejina two-token replacement does not decode to expected text")

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # Rewrite 0EF3 in place; explicit EC8D restarts Hangul after the space.
    entry_abs = dictionary.entry_abs(WRAPPER_SLOT)
    candidate[entry_abs:entry_abs + len(WRAPPER_AFTER)] = WRAPPER_AFTER
    candidate[entry_abs + len(WRAPPER_AFTER)] = 0
    allowed.append((entry_abs, entry_abs + len(WRAPPER_AFTER) + 1))

    for logical in DOCTOR_TARGETS:
        start = sb + logical
        candidate[start:start + len(DOCTOR_AFTER)] = DOCTOR_AFTER
        allowed.append((start, start + len(DOCTOR_AFTER)))

    kate_start = sb + KATEJINA
    candidate[kate_start:kate_start + len(KATEJINA_AFTER)] = KATEJINA_AFTER
    allowed.append((kate_start, kate_start + len(KATEJINA_AFTER)))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)

    if bytes(result_dictionary.raw_entry(WRAPPER_SLOT)) != WRAPPER_AFTER:
        raise BuildError("0EF3 wrapper rewrite failed")
    if clean(result_dictionary.expand_index(WRAPPER_SLOT, tbl)) != "그건　아니지만":
        raise BuildError("0EF3 explicit-marker wrapper render mismatch")

    doctor_rows = []
    for logical in DOCTOR_TARGETS:
        payload, term = read_record(result, logical)
        text = clean(result_dictionary.expand(payload, tbl))
        if payload != DOCTOR_AFTER or text != "그건　아니지만。":
            raise BuildError(f"Doctor J final mismatch at {logical:06X}: {payload.hex().upper()} {text!r}")
        doctor_rows.append({"abs": f"{logical:06X}", "payload_hex": payload.hex().upper(), "rendered": text, "terminator": f"{term:06X}"})

    kate_payload, kate_final_term = read_record(result, KATEJINA)
    kate_text = clean(result_dictionary.expand(kate_payload[3:], tbl))
    if kate_payload != KATEJINA_AFTER or kate_text != KATEJINA_EXPECTED or kate_final_term != kate_term:
        raise BuildError(f"Katejina final mismatch: {kate_payload.hex().upper()} {kate_text!r}")
    if b"\xE5\x18" in kate_payload[3:]:
        raise BuildError("Katejina still contains direct E5 18 portal")

    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"diff escaped v4 scope: {unexpected[:8]}")
    if MAIN.read_bytes() != main_rom or LIVE_SAVE.read_bytes() != live_save:
        raise BuildError("main TIP or live SaveRAM changed during v4 build")

    OUT.write_bytes(result)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != live_save:
        raise BuildError("v4 SaveRAM is not byte-exact live copy")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scenario_continuation_native_followup_v4_candidate.py",
        "status": "candidate_pending_user_runtime_validation",
        "parent": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(parent), "reason": "user-validated v1; excludes failed v2/v3 hypotheses"},
        "main_unchanged": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(main_rom)},
        "candidate": {"path": str(OUT.relative_to(ROOT)), "sha256": sha(result), "checksum": f"{checksum:04X}", "size": len(result)},
        "saveram": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(live_save), "byte_exact_to_live_main": True},
        "doctor_j": {
            "wrapper_slot": f"{WRAPPER_SLOT:04X}",
            "wrapper_before": WRAPPER_BEFORE.hex().upper(),
            "wrapper_after": WRAPPER_AFTER.hex().upper(),
            "root_cause": "Hangul run ended/reset across ideographic space; previous nested tokens after the space lacked a fresh EC8D marker",
            "records": doctor_rows,
            "physical_pointer_aliases": same_ptr,
            "inside_pointer_aliases": inside_ptr,
        },
        "katejina": {
            "abs": f"{KATEJINA:06X}",
            "before_hex": KATEJINA_BEFORE.hex().upper(),
            "after_hex": KATEJINA_AFTER.hex().upper(),
            "original_hex": KATEJINA_ORIGINAL.hex().upper(),
            "rendered": kate_text,
            "terminator": f"{kate_final_term:06X}",
            "strategy": "actual-stage21-scenario-first; replace E5-18 portal with two existing 2-byte dictionary tokens",
        },
        "guards": {
            "actual_katejina_address_changed": result[sb + KATEJINA:sb + KATEJINA + len(KATEJINA_AFTER)] == KATEJINA_AFTER,
            "katejina_direct_e518_removed": b"\xE5\x18" not in KATEJINA_AFTER[3:],
            "doctor_wrapper_explicit_ec8d_after_space": WRAPPER_AFTER[3:5] == b"\xEC\x8D",
            "unexpected_diff_runs": 0,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
        },
        "runtime_test": [
            "STAGE21t Katejina actual 63463A: 우후후후…… must no longer leak the following control row as がけはう/hiragana.",
            "STAGE21t Doctor J: 그건 아니지만。 must render through 아니지만 without glyph corruption.",
            "Four/Zero fix from user-validated v1 must remain normal because v4 is built directly on v1.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "saveram": report["saveram"],
        "doctor_j": report["doctor_j"],
        "katejina": report["katejina"],
        "unexpected_diff_runs": 0,
        "report": str(REPORT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
