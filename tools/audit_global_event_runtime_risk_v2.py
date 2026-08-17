#!/usr/bin/env python3
"""Whole-game structural audit for event/runtime text risks on the STAGE22t v2 candidate.

Read-only.  The audit does not claim every structural match is a runtime bug;
it separates proven failures from unverified records that share the same grammar.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from dialogue_runtime_contracts import (  # noqa: E402
    SCENARIO_CONTINUATION_CONTROL18_NATIVE_ONLY,
    SCENARIO_CONTINUATION_NATIVE_ONLY,
    SCENARIO_FIRST_NATIVE_ONLY,
    build_manifest,
)
from expand_dictionary import AUX_TOKEN_BANKS, NAME75_RANGES, SCRIPT_TOKEN_BANKS, _walk_zstring_range  # noqa: E402
from monoeye_rom import read_encoded_z_safe, load_rom, stock_base, token_from_dict_index  # noqa: E402

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TARGET = ROOT / "out/patch/stage22t_uso_katejina_event8ce3_native2_portal_v2_candidate.wsc"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/global_event_runtime_risk_v2.json"

SPECIAL = bytes.fromhex("E51B")
STAGE22_TARGET = 0x638CD5
CONTROL_LEADS = {"08", "17", "18"}

# Known intentional event-name/fixed-label localization ranges.  Everything
# else in logical banks64-69 is treated as an unknown event/data diff.
EVENT_ALLOWLIST = [
    (0x643200, 0x643202), (0x64500E, 0x645010), (0x645019, 0x64501B),
    (0x64501D, 0x64501F), (0x64B2B9, 0x64B2BB), (0x651F16, 0x651F18),
    (0x6649C4, 0x6649C6), (0x66A145, 0x66A147), (0x66BB3B, 0x66BB3D),
    (0x66E004, 0x66E006), (0x66F18A, 0x66F18C), (0x673E06, 0x673E08),
    (0x673EA0, 0x673EA2), (0x67AF01, 0x67AF09), (0x67C0EC, 0x67C0F4),
    (0x67EBFB, 0x67EBFD), (0x67EC02, 0x67EC04), (0x67EC83, 0x67EC85),
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_dict_pair2(body: bytes) -> bool:
    return len(body) == 4 and 0xF0 <= body[0] <= 0xFF and 0xF0 <= body[2] <= 0xFF


def diff_runs(a: bytes, b: bytes, lo: int, hi: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    st = None
    for i in range(lo, hi):
        if a[i] != b[i] and st is None:
            st = i
        elif a[i] == b[i] and st is not None:
            out.append((st, i)); st = None
    if st is not None:
        out.append((st, hi))
    return out


def allowed_event_run(run: tuple[int, int]) -> bool:
    lo, hi = run
    return any(a <= lo and hi <= b for a, b in EVENT_ALLOWLIST)


def scan_e5_units(payload: bytes, counter: Counter[int], source: Counter[tuple[int, str]], kind: str) -> None:
    i = 0
    while i < len(payload):
        b = payload[i]
        if b >= 0xE0 and i + 1 < len(payload):
            trail = payload[i + 1]
            if b == 0xE5:
                counter[trail] += 1
                source[(trail, kind)] += 1
                if trail == 0x18 and i + 3 < len(payload):
                    i += 4
                    continue
            i += 2
        else:
            i += 1


def semantic_e5_usage(rom: bytes, dictionary: Any) -> tuple[Counter[int], Counter[tuple[int, str]]]:
    counter: Counter[int] = Counter()
    source: Counter[tuple[int, str]] = Counter()
    for seg in SCRIPT_TOKEN_BANKS:
        for _a, payload, _k in _walk_zstring_range(rom, seg << 16, (seg + 1) << 16, region="script"):
            scan_e5_units(payload, counter, source, "script")
    for seg in AUX_TOKEN_BANKS:
        for _a, payload, _k in _walk_zstring_range(rom, seg << 16, (seg + 1) << 16, region="aux", max_len=128):
            scan_e5_units(payload, counter, source, "aux")
    for lo, hi in NAME75_RANGES:
        for _a, payload, _k in _walk_zstring_range(rom, lo, hi, region="name75", max_len=64):
            scan_e5_units(payload, counter, source, "name75")
    for index in range(dictionary.count):
        try:
            scan_e5_units(bytes(dictionary.raw_entry(index, max_len=2048)), counter, source, "native_dictionary")
        except Exception:
            pass

    meta = load_ext_meta(EXT3_META)
    num_banks = int(meta.get("num_banks") or 0)
    seg0 = int(str(meta.get("exp_seg0") or "11"), 16)
    for bi in range(num_banks):
        bank = rom[(seg0 + bi) << 16:(seg0 + bi + 1) << 16]
        seen: set[int] = set()
        for slot in range(4096):
            off = bank[slot * 2] | (bank[slot * 2 + 1] << 8)
            if off in seen or off >= 0x10000:
                continue
            seen.add(off)
            got = read_encoded_z_safe(bank, off, max_len=2048)
            if got is not None:
                scan_e5_units(bytes(got[0]), counter, source, "ext3_phrase")
    return counter, source


def row_view(r: dict[str, Any]) -> dict[str, Any]:
    boundary = r.get("baseline_boundary") or {}
    return {
        "address": r["address"], "route": r.get("route"), "status": r.get("status"),
        "confidence": r.get("confidence"), "source_body_hex": r.get("source_body_hex"),
        "candidate_body_hex": r.get("baseline_body_hex"), "nul_run": boundary.get("nul_run"),
        "next_lead": boundary.get("next_lead"), "next_control": boundary.get("next_control"),
        "original_japanese": r.get("original_japanese"), "candidate_text": r.get("baseline_text"),
    }


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    main_rom = bytes(load_rom(MAIN))
    target = bytes(load_rom(TARGET))
    sb = stock_base(target)
    manifest = build_manifest(original, target, target_path=TARGET)

    terminator_drift = [r for r in manifest["contracts"] if r.get("source_terminator") != r.get("baseline_terminator")]
    unsafe_ext3_zero: list[dict[str, str]] = []
    exact4_matches: list[dict[str, Any]] = []
    cont18_matches: list[dict[str, Any]] = []
    control_adjacent_ext3 = 0

    for r in manifest["contracts"]:
        body = bytes.fromhex(r.get("baseline_body_hex") or "")
        source_body = bytes.fromhex(r.get("source_body_hex") or "")
        boundary = r.get("baseline_boundary") or {}
        next_lead = boundary.get("next_lead")
        if body.startswith(b"\xE5\x18") and next_lead in CONTROL_LEADS:
            control_adjacent_ext3 += 1
        i = 0
        while i + 3 < len(body):
            if body[i:i + 2] == b"\xE5\x18":
                if body[i + 2] == 0 or body[i + 3] == 0:
                    unsafe_ext3_zero.append({"address": r["address"], "body_hex": body.hex().upper()})
                i += 4
            else:
                i += 1
        if (
            len(body) == 4 and body.startswith(b"\xE5\x18") and is_dict_pair2(source_body)
            and next_lead in {"08", "17"} and boundary.get("nul_run") == 2
        ):
            exact4_matches.append(row_view(r))
        if (
            len(source_body) == 5 and source_body[0] == 0x18
            and 0xF0 <= source_body[1] <= 0xFF and 0xF0 <= source_body[3] <= 0xFF
            and len(body) == 5 and body[:3] == b"\x18\xE5\x18"
            and next_lead in {"08", "17"}
        ):
            cont18_matches.append(row_view(r))

    # Event/data banks 64-69 stay source-byte-exact except the maintained label allowlist.
    event_runs: list[dict[str, Any]] = []
    event_unknown: list[dict[str, Any]] = []
    for bank in range(0x64, 0x6A):
        lo, hi = bank << 16, (bank + 1) << 16
        for run in diff_runs(original, target[sb:], lo, hi):
            item = {"start": f"{run[0]:06X}", "end_exclusive": f"{run[1]:06X}", "bytes": run[1] - run[0], "allowlisted": allowed_event_run(run)}
            event_runs.append(item)
            if not item["allowlisted"]:
                event_unknown.append(item)

    dictionary_target = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    special_nested = []
    special_indices: set[int] = set()
    for index in range(dictionary_target.count):
        try:
            raw = bytes(dictionary_target.raw_entry(index, max_len=2048))
        except Exception:
            continue
        if SPECIAL in raw:
            special_indices.add(index)
            special_nested.append({"index": f"{index:04X}", "token": token_from_dict_index(index).hex().upper(), "raw_hex": raw.hex().upper()})
    ext_refs = external_occurrence_map(target, ext3_aware=True, wanted=special_indices) if special_indices else {}
    nested_refs = nested_occurrence_map(dictionary_target, wanted=special_indices, ext3_aware=True) if special_indices else {}
    for row in special_nested:
        idx = int(row["index"], 16)
        row["external_consumers"] = ext_refs.get(idx, [])
        row["nested_consumers"] = nested_refs.get(idx, [])

    # Parent-main semantic ownership pool: candidates for a future safer 2-byte portal.
    dictionary_main = make_dictionary_ext3(main_rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    e5_count, e5_source = semantic_e5_usage(main_rom, dictionary_main)
    semantic_zero = [trail for trail in range(1, 256) if e5_count[trail] == 0 and trail not in {0x18, 0x19}]
    recommended = 0x1D if 0x1D in semantic_zero else semantic_zero[0]

    proven_native = sorted({f"{x:06X}" for x in SCENARIO_FIRST_NATIVE_ONLY | SCENARIO_CONTINUATION_CONTROL18_NATIVE_ONLY | SCENARIO_CONTINUATION_NATIVE_ONLY})
    exact4_addresses = {r["address"] for r in exact4_matches}
    cont18_addresses = {r["address"] for r in cont18_matches}

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_global_event_runtime_risk_v2.py",
        "read_only": True,
        "status": "review_required",
        "inputs": {
            "original": {"path": str(ORIGINAL.relative_to(ROOT)), "sha256": sha(original)},
            "main": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(main_rom)},
            "target": {"path": str(TARGET.relative_to(ROOT)), "sha256": sha(target)},
        },
        "runtime_evidence": {
            "638CD5_v2": "user confirmed Event Error 12288/36067 is gone and following dialogue/event progression is normal",
            "v1_to_v2": "direct Hangul bytes in the expansion helper corrupted the following Uso text; nested-native-only helper fixed it",
        },
        "counts": {
            "contracts": len(manifest["contracts"]),
            "terminator_drift": len(terminator_drift),
            "unsafe_ext3_zero_middle": len(unsafe_ext3_zero),
            "control_adjacent_direct_ext3": control_adjacent_ext3,
            "exact4_source_two_native_to_direct_ext3_structural_suspects": len(exact4_matches),
            "control18_source_two_native_to_direct_ext3_structural_suspects": len(cont18_matches),
            "event_bank_unknown_diff_runs": len(event_unknown),
            "special_E51B_native_dictionary_entries": len(special_nested),
            "semantic_zero_E5_trails_on_parent_main": len(semantic_zero),
        },
        "hard_good_news": {
            "all_contract_terminators_source_exact": len(terminator_drift) == 0,
            "unsafe_E518_zero_middle_absent": len(unsafe_ext3_zero) == 0,
            "event_banks_64_69_unknown_diff_absent": len(event_unknown) == 0,
        },
        "promotion_blockers_for_current_v2": {
            "E51B_nested_dictionary_collision": bool(special_nested),
            "details": special_nested,
            "reason": "E51B is globally intercepted by the new runtime portal, but two reachable native dictionary phrases contain E51B as an ordinary glyph pair.",
        },
        "structural_suspects": {
            "interpretation": "These are not 223 proven bugs. They are records that share the strongest byte-level grammar with previously runtime-proven failures and require staged validation or rehome policy.",
            "exact4_source_two_native_to_direct_ext3": exact4_matches,
            "control18_source_two_native_to_direct_ext3": cont18_matches,
            "overlap_with_current_runtime_native_only_ledger": sorted((exact4_addresses | cont18_addresses) & set(proven_native)),
        },
        "event_banks": {"diff_runs": event_runs, "unknown_runs": event_unknown},
        "safer_2byte_portal_pool": {
            "policy": "reserve only E5xx code units with zero semantic consumers across script/aux/name75/native-dictionary/ext3-phrase ownership; lock them with a build-time union audit",
            "semantic_zero_trails_hex": [f"{x:02X}" for x in semantic_zero],
            "recommended_next_probe": f"E5{recommended:02X}",
            "recommended_reason": "zero semantic consumers on the promoted parent main; unlike E51B, no native dictionary phrase owns it",
            "current_E51B_semantic_usage_on_parent": {
                kind: e5_source.get((0x1B, kind), 0)
                for kind in ("script", "aux", "name75", "native_dictionary", "ext3_phrase")
            },
        },
        "fix_direction": [
            "Do not promote the current E51B v2 unchanged because of its two reachable native-dictionary collisions.",
            "Build a v3 probe by changing only the portal magic from E51B to one union-proven semantic-zero code unit (recommended E51D), preserving the already runtime-proven nested-native-only bank26 helper.",
            "After v3 runtime confirmation, generalize the portal as a sparse reserved E5xx helper-ID table rather than reclaiming F0-FF dictionary slots.",
            "Every expansion helper must be nested-native-only; direct Hangul marker/glyph bytes are forbidden in this special event-sensitive route.",
            "Prioritize the 3 control18 and 220 exact4 structural suspects by stage/bundle. Do not bulk rewrite all 223 without caller/runtime evidence.",
            "For each staged batch preserve prefix, record extent, original terminator address, NUL run, and following 08/17 control bytes byte-exact.",
            "Keep banks64-69 executable/event bodies under the existing allowlist gate; any new non-allowlisted diff is a hard build failure.",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "counts": report["counts"], "recommended_next_probe": report["safer_2byte_portal_pool"]["recommended_next_probe"], "report": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
