#!/usr/bin/env python3
"""Fix the STAGE21t Doctor J follow-up wrapper on top of the v1 candidate.

Runtime validation proved that converting the preceding 0x18+ext3 line to
native grammar was not sufficient: the following `그건 아니지만。` line still
corrupts.  The line itself currently uses stock slot 0EF3 whose payload mixes a
nested stock token with a direct EC8D Hangul marker.  Restore the pristine
record grammar (one stock token + native punctuation byte) and rewrite only the
0EF3 wrapper so its top level contains stock tokens/space only.

Parent/main TIP and live SaveRAM are never overwritten.
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

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = ROOT / "out/patch/scenario_continuation_native_followup_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/scenario_continuation_native_followup_v2_candidate.wsc"
OUT_SAVE = ROOT / "sram/scenario_continuation_native_followup_v2_candidate.sav"
REPORT = ROOT / "out/patch/scenario_continuation_native_followup_v2_candidate_report.json"

EXPECTED_MAIN_SHA = "2bccff99a36cd453bf01225365a40ed4d744049e69d46e726ab427851afa1799"
EXPECTED_PARENT_SHA = "c5283b0804fe47eb77b4acbe0c23cc7fe8a1e649a1052394ed03a3beee398688"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

WRAPPER_SLOT = 0x0EF3
WRAPPER_BEFORE = bytes.fromhex("F171EC8DE7E901F5C5F4180A")
# F05E = 그건, F5C5 = 아니, F418 = 지만.  No direct EC8D marker remains at
# the wrapper top level; punctuation moves back to the record like Original.
WRAPPER_AFTER = bytes.fromhex("F05E01F5C5F418")
TARGETS = (0x635866, 0x635C0C)
RECORD_BEFORE = bytes.fromhex("FEF301")
RECORD_AFTER = bytes.fromhex("FEF30A")
ORIGINAL_RECORD = bytes.fromhex("FEE10A")
UNRELATED_EXT3_RECORD = 0x5D473F  # contains E5 18 FE F3 as an ext3 index, not stock slot 0EF3


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def read_record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


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
    original_dictionary = make_dictionary_ext3(original, {}, None)
    sb = stock_base(parent)

    before_raw = bytes(dictionary.raw_entry(WRAPPER_SLOT))
    if before_raw != WRAPPER_BEFORE:
        raise BuildError(f"0EF3 wrapper drifted: {before_raw.hex().upper()}")
    if clean(dictionary.expand_index(WRAPPER_SLOT, tbl)) != "그건　아니지만。":
        raise BuildError("0EF3 semantic text drifted")

    wrapper_ptr = dictionary.ptrs[WRAPPER_SLOT]
    same_ptr = [i for i, ptr in enumerate(dictionary.ptrs[:dictionary.stock_count]) if ptr == wrapper_ptr]
    inside_ptr = [
        i for i, ptr in enumerate(dictionary.ptrs[:dictionary.stock_count])
        if i != WRAPPER_SLOT and wrapper_ptr < ptr < wrapper_ptr + len(before_raw) + 1
    ]
    if same_ptr != [WRAPPER_SLOT] or inside_ptr:
        raise BuildError(f"0EF3 pointer alias hazard: same={same_ptr} inside={inside_ptr}")

    refs = external_occurrence_map(parent, ext3_aware=True, wanted={WRAPPER_SLOT}).get(WRAPPER_SLOT, [])
    ref_rows = sorted((int(str(row["record_abs"]), 16), int(str(row["token_abs"]), 16)) for row in refs)
    expected_refs = sorted((logical, logical) for logical in TARGETS)
    if ref_rows != expected_refs:
        raise BuildError(f"0EF3 external consumers drifted: {ref_rows}")
    if nested_occurrence_map(dictionary, wanted={WRAPPER_SLOT}, ext3_aware=True).get(WRAPPER_SLOT):
        raise BuildError("0EF3 unexpectedly nested inside another stock entry")

    for logical in TARGETS:
        payload, term = read_record(parent, logical)
        pristine, pristine_term = read_record(original, logical)
        if payload != RECORD_BEFORE:
            raise BuildError(f"target current payload drifted {logical:06X}: {payload.hex().upper()}")
        if pristine != ORIGINAL_RECORD or pristine_term != term:
            raise BuildError(f"target Original grammar drifted {logical:06X}")

    unrelated_before = read_record(parent, UNRELATED_EXT3_RECORD)

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    entry_abs = dictionary.entry_abs(WRAPPER_SLOT)
    candidate[entry_abs:entry_abs + len(WRAPPER_AFTER)] = WRAPPER_AFTER
    candidate[entry_abs + len(WRAPPER_AFTER)] = 0
    # Bytes after the new NUL are intentionally left byte-exact; no pointer can
    # enter the old 0EF3 physical extent.
    allowed.append((entry_abs, entry_abs + len(WRAPPER_AFTER) + 1))

    for logical in TARGETS:
        start = sb + logical
        candidate[start:start + len(RECORD_AFTER)] = RECORD_AFTER
        allowed.append((start, start + len(RECORD_AFTER)))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)

    if bytes(result_dictionary.raw_entry(WRAPPER_SLOT)) != WRAPPER_AFTER:
        raise BuildError("0EF3 wrapper rewrite failed")
    if clean(result_dictionary.expand_index(WRAPPER_SLOT, tbl)) != "그건　아니지만":
        raise BuildError("0EF3 rewritten wrapper does not render expected text")

    rendered: list[dict[str, Any]] = []
    for logical in TARGETS:
        payload, term = read_record(result, logical)
        if payload != RECORD_AFTER:
            raise BuildError(f"target native record rewrite failed {logical:06X}")
        text = clean(result_dictionary.expand(payload, tbl))
        if text != "그건　아니지만。":
            raise BuildError(f"target render mismatch {logical:06X}: {text!r}")
        rendered.append({
            "abs": f"{logical:06X}",
            "payload_hex": payload.hex().upper(),
            "rendered": text,
            "terminator": f"{term:06X}",
            "original_payload_hex": ORIGINAL_RECORD.hex().upper(),
            "strategy": "original_stock_token_plus_native_punctuation_grammar",
        })

    if read_record(result, UNRELATED_EXT3_RECORD) != unrelated_before:
        raise BuildError("unrelated E5 18 FE F3 ext3 record changed")

    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"diff escaped focused v2 scope: {unexpected[:8]}")
    if MAIN.read_bytes() != main_rom or LIVE_SAVE.read_bytes() != live_save:
        raise BuildError("main TIP or live SaveRAM changed during v2 build")

    OUT.write_bytes(result)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != live_save:
        raise BuildError("v2 SaveRAM is not byte-exact live copy")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scenario_continuation_native_followup_v2_candidate.py",
        "status": "candidate_pending_user_runtime_validation",
        "parent": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(parent)},
        "main_unchanged": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(main_rom)},
        "candidate": {
            "path": str(OUT.relative_to(ROOT)),
            "sha256": sha(result),
            "checksum": f"{checksum:04X}",
            "size": len(result),
        },
        "saveram": {
            "path": str(OUT_SAVE.relative_to(ROOT)),
            "sha256": sha(live_save),
            "byte_exact_to_live_main": True,
        },
        "root_cause": {
            "slot": f"{WRAPPER_SLOT:04X}",
            "before_raw": WRAPPER_BEFORE.hex().upper(),
            "after_raw": WRAPPER_AFTER.hex().upper(),
            "before_shape": "nested stock + direct EC8D Hangul marker + nested stock + punctuation",
            "after_shape": "nested stock only; punctuation restored to record",
            "external_stock_consumers": [f"{logical:06X}" for logical in TARGETS],
            "pointer_aliases": same_ptr,
            "inside_pointer_aliases": inside_ptr,
        },
        "records": rendered,
        "guards": {
            "wrapper_top_level_direct_ec8d_removed": b"\xEC\x8D" not in WRAPPER_AFTER,
            "record_grammar_matches_original_shape": all(row["payload_hex"].endswith("0A") for row in rendered),
            "unrelated_ext3_fe_f3_record_unchanged": True,
            "unexpected_diff_runs": 0,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
        },
        "diff": {
            "runs": [{"start": start, "end": end, "length": end - start} for start, end in runs],
            "unexpected_runs": 0,
        },
        "runtime_test": [
            "STAGE21t Doctor J first copy: confirm ……뭐、 승산 좋은 도박？ then 그건 아니지만。 renders without corruption.",
            "Repeat the duplicate branch/copy if reachable and confirm the same result.",
            "Confirm the conversation continues normally after the follow-up line.",
            "Recheck Four/Zero line and prior battle fixes for regression only if convenient; v2 changes no other records.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "saveram": report["saveram"],
        "root_cause": report["root_cause"],
        "records": rendered,
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
