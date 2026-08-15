#!/usr/bin/env python3
"""Build v3 for the stubborn ``こ + 이 멍청한 놈이！！`` battle-voice leak.

v2 removed E5 18 from the six 4A/こ-family records, but compressed long native
source bodies to one/few native dictionary iterations.  Runtime still showed a
leading ``こ``.  v3 therefore restores the *source code-unit iteration count*
for every record in that exact duplicated 4A family while keeping the Korean
visible text and byte extent unchanged.  The already runtime-approved 62663E
``오우！！`` native two-token repair is retained.

Main TIP and live SaveRAM are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_scenario_page_boundary_guard_candidate import original_unit_kinds, safe_unreachable_slots  # noqa: E402
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base, token_from_dict_index, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/domon_runtime_structure_followup_v3_candidate.wsc"
OUT_SAVE = ROOT / "sram/domon_runtime_structure_followup_v3_candidate.sav"
REPORT = ROOT / "out/patch/domon_runtime_structure_followup_v3_candidate_report.json"
ITER_REPORT = ROOT / "out/patch/domon_runtime_structure_followup_v3_iteration_guard.json"

MAIN_SHA = "2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483"
ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXT = {"stock_count": 3831, "slot_count": 265, "ext_ptr_off": "0000", "ext_seg": "10", "ext_in_expansion": True}
EXT3 = {"num_banks": 16, "exp_seg0": "11"}

# Safe currently-unreachable stock slots selected from the current main.
SLOTS = {
    "멍청한": 0x024B,  # needs the one long-capacity dead entry
    "놈이": 0x00CF,
    "오우": 0x00FD,
    "약삭": 0x013E,
    "빠르": 0x0143,
    "구나": 0x0146,
}
EXISTING = {"이": 0x0053, "！！": 0x0044, "……": 0x0191}

# Current-main exact payloads, including 4A metadata where present.
CURRENT = {
    0x5D956C: bytes.fromhex("4AE518378701010101010101010101"),
    0x5D9590: bytes.fromhex("4AE51828D7010101010101"),
    0x5D95AD: bytes.fromhex("4AE518382C"),
    0x5D9747: bytes.fromhex("4AE518378701010101010101010101"),
    0x5D976B: bytes.fromhex("4AE51828D7010101010101"),
    0x5D9788: bytes.fromhex("4AE518382C"),
    0x62663E: bytes.fromhex("173418E5181CF8"),
}
TERMS = {
    0x5D956C: 0x5D957B,
    0x5D9590: 0x5D959B,
    0x5D95AD: 0x5D95B2,
    0x5D9747: 0x5D9756,
    0x5D976B: 0x5D9776,
    0x5D9788: 0x5D978D,
    0x62663E: 0x626645,
}
ORIGINAL_BODY = {
    0x5D956C: bytes.fromhex("F36214F081200517F1FB1009F044"),
    0x5D9590: bytes.fromhex("18E006F67131F76CF044"),
    0x5D95AD: bytes.fromhex("F362F191"),
    0x5D9747: bytes.fromhex("F36214F081200517F1FB1009F044"),
    0x5D976B: bytes.fromhex("18E006F67131F76CF044"),
    0x5D9788: bytes.fromhex("F362F191"),
    0x62663E: bytes.fromhex("F8A6F044"),
}
EXPECTED_TEXT = {
    0x5D956C: "이　멍청한　놈이！！",
    0x5D9590: "약삭빠르구나！！",
    0x5D95AD: "이……",
    0x5D9747: "이　멍청한　놈이！！",
    0x5D976B: "약삭빠르구나！！",
    0x5D9788: "이……",
    0x62663E: "오우！！",
}


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rr(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def enc(tbl: Tbl, text: str) -> bytes:
    raw = try_encode_ko_text(normalize_ko_text(text), tbl, hangul_marker_code=0xEC8D, hangul_marker_mode="run")
    if not raw or b"\x00" in raw:
        raise RuntimeError(f"cannot encode {text!r}")
    return bytes(raw)


def trim(text: str) -> str:
    return text.rstrip("　 \t")


def tok(index: int) -> bytes:
    value = token_from_dict_index(index)
    if len(value) != 2 or 0 in value:
        raise RuntimeError(f"unsafe native token {index:04X}")
    return value


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    save = SAVE.read_bytes()
    if sha(parent) != MAIN_SHA or sha(original) != ORIGINAL_SHA:
        raise RuntimeError("ROM identity drift")
    if len(save) != 32768:
        raise RuntimeError("live SaveRAM size drift")

    tbl = Tbl.load(TBL_PATH)
    d = make_dictionary_ext3(parent, EXT, EXT3)
    od = Dictionary(original)
    sb = stock_base(parent)

    for logical, expected in CURRENT.items():
        got, term = rr(parent, logical)
        if got != expected or term != TERMS[logical]:
            raise RuntimeError(f"current record drift {logical:06X}")

    # Prove source body and source iteration counts before building anything.
    source_counts: dict[int, int] = {}
    source_kinds: dict[int, list[str]] = {}
    for logical, body in ORIGINAL_BODY.items():
        kinds = original_unit_kinds(body)
        source_counts[logical] = len(kinds)
        source_kinds[logical] = kinds

    safe = {int(row["index"]): row for row in safe_unreachable_slots(parent, d)}
    if not set(SLOTS.values()) <= set(safe):
        missing = sorted(set(SLOTS.values()) - set(safe))
        raise RuntimeError("selected stock slots no longer safe: " + ",".join(f"{x:04X}" for x in missing))

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    slot_rows: list[dict[str, Any]] = []
    for text, index in SLOTS.items():
        row = safe[index]
        raw = enc(tbl, text)
        old_len = int(row["old_len"])
        start = int(row["entry_abs"])
        if len(raw) > old_len:
            raise RuntimeError(f"slot {index:04X} too small for {text!r}")
        before = d.expand_index(index, tbl)
        candidate[start:start+len(raw)] = raw
        candidate[start+len(raw)] = 0
        allowed.append((start, start + old_len + 1))
        slot_rows.append({"index": f"{index:04X}", "before": before, "after": text, "raw": raw.hex().upper(), "old_len": old_len, "entry_abs": start})

    # v3 central change: preserve source *iteration count* and total body extent.
    full_body = (
        tok(EXISTING["이"]) + b"\x01" + tok(SLOTS["멍청한"]) + b"\x01" +
        tok(SLOTS["놈이"]) + tok(EXISTING["！！"]) + b"\x01\x01\x01\x01"
    )
    fast_body = tok(SLOTS["약삭"]) + tok(SLOTS["빠르"]) + tok(SLOTS["구나"]) + tok(EXISTING["！！"]) + b"\x01\x01"
    this_body = tok(EXISTING["이"]) + tok(EXISTING["……"])
    ou_body = tok(SLOTS["오우"]) + tok(EXISTING["！！"])

    bodies = {
        0x5D956C: full_body,
        0x5D9590: fast_body,
        0x5D95AD: this_body,
        0x5D9747: full_body,
        0x5D976B: fast_body,
        0x5D9788: this_body,
        0x62663E: ou_body,
    }
    prefixes = {logical: (b"\x4A" if logical >> 16 == 0x5D else bytes.fromhex("173418")) for logical in bodies}

    expected_occ: dict[int, list[int]] = defaultdict(list)
    patch_rows: list[dict[str, Any]] = []
    for logical, body in bodies.items():
        new = prefixes[logical] + body
        old = CURRENT[logical]
        if len(new) != len(old):
            raise RuntimeError(f"extent mismatch {logical:06X}: {len(new)} != {len(old)}")
        cand_count = len(original_unit_kinds(body))
        if cand_count != source_counts[logical]:
            raise RuntimeError(f"iteration count mismatch {logical:06X}: source {source_counts[logical]} candidate {cand_count}")
        candidate[sb+logical:sb+logical+len(new)] = new
        allowed.append((sb+logical, sb+logical+len(new)))
        # Record exact expected references to every newly repurposed slot.
        pos = 0
        while pos < len(body):
            if body[pos] >= 0xF0:
                index = ((body[pos] - 0xF0) << 8) | body[pos+1]
                if index in SLOTS.values():
                    expected_occ[index].append(logical + len(prefixes[logical]) + pos)
                pos += 2
            elif body[pos] >= 0xE0:
                pos += 2
            else:
                pos += 1
        patch_rows.append({
            "abs": f"{logical:06X}", "before": old.hex().upper(), "after": new.hex().upper(),
            "source_body": ORIGINAL_BODY[logical].hex().upper(), "source_kinds": source_kinds[logical],
            "source_iterations": source_counts[logical], "candidate_kinds": original_unit_kinds(body),
            "candidate_iterations": cand_count, "terminator": f"{TERMS[logical]:06X}",
        })

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate)-2, len(candidate)))
    cb = bytes(candidate)
    fd = make_dictionary_ext3(cb, EXT, EXT3)

    # Re-read/semantic/terminator proof.
    renders: dict[str, str] = {}
    for logical, body in bodies.items():
        got, term = rr(cb, logical)
        if got != prefixes[logical] + body or term != TERMS[logical] or cb[sb+term] != 0:
            raise RuntimeError(f"record/terminator verification failed {logical:06X}")
        rendered = trim(fd.expand(body, tbl))
        if rendered != EXPECTED_TEXT[logical]:
            raise RuntimeError(f"render mismatch {logical:06X}: {rendered!r}")
        if len(original_unit_kinds(body)) != len(original_unit_kinds(ORIGINAL_BODY[logical])):
            raise RuntimeError(f"post-build iteration mismatch {logical:06X}")
        if logical in (0x5D956C, 0x5D9747) and (b"\x18" in body or b"\xE5\x18" in body or bytes.fromhex("F362") in body):
            raise RuntimeError(f"reported body still contains a direct こ-producing/source-leading sequence {logical:06X}")
        renders[f"{logical:06X}"] = rendered

    # Selected stock slots may be consumed only by the planned records.
    selected = set(SLOTS.values())
    ext = external_occurrence_map(cb, ext3_aware=True, wanted=selected)
    nested = nested_occurrence_map(fd, wanted=selected, ext3_aware=True)
    raw_hits = _raw_pair_hits(cb, sorted(selected))
    ref_rows = []
    for index in sorted(selected):
        expected = sorted(expected_occ.get(index, []))
        external = sorted(int(str(x["token_abs"]), 16) for x in ext.get(index, []))
        raw = sorted(int(str(x["token_abs"]), 16) for x in raw_hits.get(index, []))
        ne = nested.get(index, [])
        if external != expected or raw != expected or ne:
            raise RuntimeError(f"reference proof failed {index:04X}: exp={expected} ext={external} raw={raw} nested={ne}")
        ref_rows.append({"index": f"{index:04X}", "expected": [f"{x:06X}" for x in expected], "external": [f"{x:06X}" for x in external], "raw": [f"{x:06X}" for x in raw], "nested": []})

    runs = diff_runs(parent, cb)
    outside = [run for run in runs if not covered(run, allowed)]
    if outside:
        raise RuntimeError(f"diff escaped allowlist: {outside[:8]}")

    # Critical contradiction detector: if this candidate still displays literal こ,
    # it cannot originate directly from either reported record body.
    direct_ko_source_impossible = all(
        b"\x18" not in bodies[a] and bytes.fromhex("F362") not in bodies[a] and b"\xE5\x18" not in bodies[a]
        for a in (0x5D956C, 0x5D9747)
    )

    OUT.write_bytes(cb)
    shutil.copy2(SAVE, OUT_SAVE)
    iteration_guard = {
        "schema_version": 1,
        "ok": True,
        "rule": "for the runtime-reported duplicated 4A/こ family, candidate body code-unit iteration count must equal pristine source while visible Korean and record extent remain unchanged",
        "records": [
            {"abs": row["abs"], "source_iterations": row["source_iterations"], "candidate_iterations": row["candidate_iterations"], "source_kinds": row["source_kinds"], "candidate_kinds": row["candidate_kinds"]}
            for row in patch_rows
        ],
        "reported_body_has_no_direct_ko_source": direct_ko_source_impossible,
    }
    ITER_REPORT.write_text(json.dumps(iteration_guard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_domon_runtime_structure_followup_v3_candidate.py",
        "status": "pending_user_runtime_validation",
        "parent_sha256": sha(parent), "candidate_sha256": sha(cb), "checksum": f"{checksum:04X}",
        "saveram_sha256": sha(OUT_SAVE.read_bytes()),
        "diagnosis": {
            "v2_contradiction": "v2 target bodies had neither raw 18 nor F362 nor E5 18, yet runtime still showed literal こ; therefore the prior E5-portal-only explanation was incomplete",
            "v3_hypothesis": "battle-voice special consumer also depends on source code-unit iteration count; preserve that count instead of collapsing a 10-unit source sentence to one native token",
            "if_v3_still_shows_ko": "the visible こ is not sourced directly from 5D956C/5D9747 body bytes; next bind the live caller/source pointer or eliminate savestate/runtime predecode with a fresh-state address barcode probe",
        },
        "slots": slot_rows, "patches": patch_rows, "renders": renders, "reference_proof": ref_rows,
        "unexpected_diff_runs": 0,
        "main_unchanged": sha(MAIN.read_bytes()) == MAIN_SHA,
        "live_saveram_unchanged": SAVE.read_bytes() == save,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate": str(OUT.relative_to(ROOT)), "sha256": sha(cb), "checksum": f"{checksum:04X}", "save": str(OUT_SAVE.relative_to(ROOT)), "iteration_guard": str(ITER_REPORT.relative_to(ROOT))}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
