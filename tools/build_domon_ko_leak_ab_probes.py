#!/usr/bin/env python3
"""Build causal A/B probes for the Master Asia ``こ + 이 멍청한 놈이！！`` leak.

Runtime already showed that v2's single native token body at ``5D956C`` /
``5D9747`` contains neither raw ``18`` nor ``F362`` nor ``E5 18``, yet a
leading ``こ`` can still appear.  These probes isolate the next two questions:

A) JP restore — replace only the duplicated metadata-``4A`` / natural-``こ``
   family with pristine Japanese payloads.  If the live scene prints the
   original Japanese sentence, the address bind is correct and Koreanization
   of this family is causal.  If Korean / mixed ``こ`` text remains, the live
   caller or savestate path is not these record bodies.

B) iteration-matched KO — keep Korean text but restore the pristine source
   code-unit iteration count (v3 hypothesis).  Only meaningful after A proves
   the addresses are live.

Both candidates start from the current main TIP.  ``62663E`` and every other
record stay byte-exact to main so the only variable is this 6-record family.
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

OUT_A = ROOT / "out/patch/domon_ko_leak_ab_a_jp_restore_candidate.wsc"
SAVE_A = ROOT / "sram/domon_ko_leak_ab_a_jp_restore_candidate.sav"
OUT_B = ROOT / "out/patch/domon_ko_leak_ab_b_iteration_ko_candidate.wsc"
SAVE_B = ROOT / "sram/domon_ko_leak_ab_b_iteration_ko_candidate.sav"
REPORT = ROOT / "out/patch/domon_ko_leak_ab_probe_report.json"

MAIN_SHA = "2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483"
ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXT = {"stock_count": 3831, "slot_count": 265, "ext_ptr_off": "0000", "ext_seg": "10", "ext_in_expansion": True}
EXT3 = {"num_banks": 16, "exp_seg0": "11"}

# Current-main exact payloads for the duplicated metadata-4A / natural-こ family.
CURRENT = {
    0x5D956C: bytes.fromhex("4AE518378701010101010101010101"),
    0x5D9590: bytes.fromhex("4AE51828D7010101010101"),
    0x5D95AD: bytes.fromhex("4AE518382C"),
    0x5D9747: bytes.fromhex("4AE518378701010101010101010101"),
    0x5D976B: bytes.fromhex("4AE51828D7010101010101"),
    0x5D9788: bytes.fromhex("4AE518382C"),
}
TERMS = {
    0x5D956C: 0x5D957B,
    0x5D9590: 0x5D959B,
    0x5D95AD: 0x5D95B2,
    0x5D9747: 0x5D9756,
    0x5D976B: 0x5D9776,
    0x5D9788: 0x5D978D,
}
ORIGINAL_FULL = {
    0x5D956C: bytes.fromhex("4AF36214F081200517F1FB1009F044"),
    0x5D9590: bytes.fromhex("4A18E006F67131F76CF044"),
    0x5D95AD: bytes.fromhex("4AF362F191"),
    0x5D9747: bytes.fromhex("4AF36214F081200517F1FB1009F044"),
    0x5D976B: bytes.fromhex("4A18E006F67131F76CF044"),
    0x5D9788: bytes.fromhex("4AF362F191"),
}
ORIGINAL_JP = {
    0x5D956C: "このうつけものがぁぁ－っ！！",
    0x5D9590: "こざかしいわぁぁっ！！",
    0x5D95AD: "この……",
    0x5D9747: "このうつけものがぁぁ－っ！！",
    0x5D976B: "こざかしいわぁぁっ！！",
    0x5D9788: "この……",
}
EXPECTED_KO = {
    0x5D956C: "이　멍청한　놈이！！",
    0x5D9590: "약삭빠르구나！！",
    0x5D95AD: "이……",
    0x5D9747: "이　멍청한　놈이！！",
    0x5D976B: "약삭빠르구나！！",
    0x5D9788: "이……",
}

SLOTS = {
    "멍청한": 0x024B,
    "놈이": 0x00CF,
    "약삭": 0x013E,
    "빠르": 0x0143,
    "구나": 0x0146,
}
EXISTING = {"이": 0x0053, "！！": 0x0044, "……": 0x0191}


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rr(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def enc(tbl: Tbl, text: str) -> bytes:
    raw = try_encode_ko_text(normalize_ko_text(text), tbl, hangul_marker_code=0xEC8D, hangul_marker_mode="run")
    if not raw or b"\x00" in raw:
        raise RuntimeError(f"cannot encode {text!r}")
    return bytes(raw)


def tok(index: int) -> bytes:
    value = token_from_dict_index(index)
    if len(value) != 2 or 0 in value:
        raise RuntimeError(f"unsafe native token {index:04X}")
    return value


def trim(text: str) -> str:
    return text.rstrip("　 \t")


def prove_parent(parent: bytes, original: bytes) -> None:
    od = Dictionary(original, stock_count=3831)
    tbl = Tbl.load(TBL_PATH)
    for logical, expected in CURRENT.items():
        got, term = rr(parent, logical)
        if got != expected or term != TERMS[logical]:
            raise RuntimeError(f"parent drift {logical:06X}")
        og, oterm = rr(original, logical)
        if og != ORIGINAL_FULL[logical] or oterm != TERMS[logical]:
            raise RuntimeError(f"original drift {logical:06X}")
        if len(got) != len(og):
            raise RuntimeError(f"extent drift {logical:06X}")
        body = og[1:]
        if trim(od.expand(body, tbl)) != ORIGINAL_JP[logical]:
            raise RuntimeError(f"original render drift {logical:06X}")


def build_a(parent: bytes, original: bytes) -> tuple[bytes, dict[str, Any]]:
    sb = stock_base(parent)
    cand = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    patches: list[dict[str, Any]] = []
    for logical, new in ORIGINAL_FULL.items():
        old = CURRENT[logical]
        if len(new) != len(old):
            raise RuntimeError(f"A extent {logical:06X}")
        cand[sb + logical : sb + logical + len(new)] = new
        allowed.append((sb + logical, sb + logical + len(new)))
        patches.append(
            {
                "abs": f"{logical:06X}",
                "before": old.hex().upper(),
                "after": new.hex().upper(),
                "terminator": f"{TERMS[logical]:06X}",
                "expected_render": ORIGINAL_JP[logical],
            }
        )
    checksum = update_ws_checksum(cand)
    allowed.append((len(cand) - 2, len(cand)))
    cb = bytes(cand)
    od = Dictionary(original, stock_count=3831)
    tbl = Tbl.load(TBL_PATH)
    renders: dict[str, str] = {}
    for logical, new in ORIGINAL_FULL.items():
        got, term = rr(cb, logical)
        if got != new or term != TERMS[logical] or cb[sb + term] != 0:
            raise RuntimeError(f"A verify failed {logical:06X}")
        rendered = trim(od.expand(got[1:], tbl))
        if rendered != ORIGINAL_JP[logical]:
            raise RuntimeError(f"A render {logical:06X}: {rendered!r}")
        # Intentional Japanese この/こ must remain in A; that is the control.
        renders[f"{logical:06X}"] = rendered
    outside = [run for run in diff_runs(parent, cb) if not covered(run, allowed)]
    if outside:
        raise RuntimeError(f"A diff escape {outside[:4]}")
    return cb, {
        "label": "A_jp_restore",
        "purpose": "Restore pristine Japanese payloads for the 6-record 4A/こ family only",
        "candidate_sha256": sha(cb),
        "checksum": f"{checksum:04X}",
        "patches": patches,
        "renders": renders,
        "unexpected_diff_runs": 0,
        "runtime_gate": [
            "cold boot with paired SaveRAM; do not reuse an old .State that may preload decoded text",
            "scene must show Japanese このうつけものがぁぁ－っ！！ (full intentional この), not Korean",
            "if Korean or mixed こい…… still appears, these record bodies are not the live source",
        ],
    }


def build_b(parent: bytes) -> tuple[bytes, dict[str, Any]]:
    tbl = Tbl.load(TBL_PATH)
    d = make_dictionary_ext3(parent, EXT, EXT3)
    sb = stock_base(parent)
    source_counts = {logical: len(original_unit_kinds(payload[1:])) for logical, payload in ORIGINAL_FULL.items()}

    safe = {int(row["index"]): row for row in safe_unreachable_slots(parent, d)}
    if not set(SLOTS.values()) <= set(safe):
        missing = sorted(set(SLOTS.values()) - set(safe))
        raise RuntimeError("B slots no longer safe: " + ",".join(f"{x:04X}" for x in missing))

    cand = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    slot_rows: list[dict[str, Any]] = []
    for text, index in SLOTS.items():
        row = safe[index]
        raw = enc(tbl, text)
        old_len = int(row["old_len"])
        start = int(row["entry_abs"])
        if len(raw) > old_len:
            raise RuntimeError(f"B slot capacity {index:04X}")
        before = d.expand_index(index, tbl)
        cand[start : start + len(raw)] = raw
        cand[start + len(raw)] = 0
        allowed.append((start, start + old_len + 1))
        slot_rows.append(
            {
                "index": f"{index:04X}",
                "before": before,
                "after": text,
                "raw": raw.hex().upper(),
                "old_len": old_len,
                "entry_abs": start,
            }
        )

    full_body = (
        tok(EXISTING["이"])
        + b"\x01"
        + tok(SLOTS["멍청한"])
        + b"\x01"
        + tok(SLOTS["놈이"])
        + tok(EXISTING["！！"])
        + b"\x01\x01\x01\x01"
    )
    fast_body = tok(SLOTS["약삭"]) + tok(SLOTS["빠르"]) + tok(SLOTS["구나"]) + tok(EXISTING["！！"]) + b"\x01\x01"
    this_body = tok(EXISTING["이"]) + tok(EXISTING["……"])
    bodies = {
        0x5D956C: full_body,
        0x5D9590: fast_body,
        0x5D95AD: this_body,
        0x5D9747: full_body,
        0x5D976B: fast_body,
        0x5D9788: this_body,
    }

    expected_occ: dict[int, list[int]] = defaultdict(list)
    patches: list[dict[str, Any]] = []
    for logical, body in bodies.items():
        new = b"\x4A" + body
        old = CURRENT[logical]
        if len(new) != len(old):
            raise RuntimeError(f"B extent {logical:06X}")
        cand_count = len(original_unit_kinds(body))
        if cand_count != source_counts[logical]:
            raise RuntimeError(f"B iteration mismatch {logical:06X}: {cand_count} != {source_counts[logical]}")
        if b"\x18" in body or b"\xE5\x18" in body or bytes.fromhex("F362") in body:
            raise RuntimeError(f"B still contains direct こ source {logical:06X}")
        cand[sb + logical : sb + logical + len(new)] = new
        allowed.append((sb + logical, sb + logical + len(new)))
        pos = 0
        while pos < len(body):
            if body[pos] >= 0xF0:
                index = ((body[pos] - 0xF0) << 8) | body[pos + 1]
                if index in SLOTS.values():
                    expected_occ[index].append(logical + 1 + pos)
                pos += 2
            elif body[pos] >= 0xE0:
                pos += 2
            else:
                pos += 1
        patches.append(
            {
                "abs": f"{logical:06X}",
                "before": old.hex().upper(),
                "after": new.hex().upper(),
                "source_iterations": source_counts[logical],
                "candidate_iterations": cand_count,
                "source_kinds": original_unit_kinds(ORIGINAL_FULL[logical][1:]),
                "candidate_kinds": original_unit_kinds(body),
                "terminator": f"{TERMS[logical]:06X}",
                "expected_render": EXPECTED_KO[logical],
            }
        )

    checksum = update_ws_checksum(cand)
    allowed.append((len(cand) - 2, len(cand)))
    cb = bytes(cand)
    fd = make_dictionary_ext3(cb, EXT, EXT3)
    renders: dict[str, str] = {}
    for logical, body in bodies.items():
        got, term = rr(cb, logical)
        if got != b"\x4A" + body or term != TERMS[logical] or cb[sb + term] != 0:
            raise RuntimeError(f"B verify failed {logical:06X}")
        rendered = trim(fd.expand(body, tbl))
        if rendered != EXPECTED_KO[logical]:
            raise RuntimeError(f"B render {logical:06X}: {rendered!r}")
        renders[f"{logical:06X}"] = rendered

    selected = set(SLOTS.values())
    ext = external_occurrence_map(cb, ext3_aware=True, wanted=selected)
    nested = nested_occurrence_map(fd, wanted=selected, ext3_aware=True)
    raw_hits = _raw_pair_hits(cb, sorted(selected))
    refs = []
    for index in sorted(selected):
        expected = sorted(expected_occ.get(index, []))
        external = sorted(int(str(x["token_abs"]), 16) for x in ext.get(index, []))
        raw = sorted(int(str(x["token_abs"]), 16) for x in raw_hits.get(index, []))
        ne = nested.get(index, [])
        if external != expected or raw != expected or ne:
            raise RuntimeError(f"B reference proof {index:04X}: exp={expected} ext={external} raw={raw} nested={ne}")
        refs.append(
            {
                "index": f"{index:04X}",
                "expected": [f"{x:06X}" for x in expected],
                "external": [f"{x:06X}" for x in external],
                "raw": [f"{x:06X}" for x in raw],
                "nested": [],
            }
        )

    outside = [run for run in diff_runs(parent, cb) if not covered(run, allowed)]
    if outside:
        raise RuntimeError(f"B diff escape {outside[:4]}")
    return cb, {
        "label": "B_iteration_matched_ko",
        "purpose": "Keep Korean text but match pristine source code-unit iteration counts for the same 6-record family",
        "candidate_sha256": sha(cb),
        "checksum": f"{checksum:04X}",
        "slots": slot_rows,
        "patches": patches,
        "renders": renders,
        "reference_proof": refs,
        "unexpected_diff_runs": 0,
        "runtime_gate": [
            "only after A proves the Japanese restore is the live text path",
            "scene must show 이　멍청한　놈이！！ with no leading こ",
            "if こ remains, body bytes alone are insufficient; next step is caller/barcode/fresh-state probe",
        ],
    }


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    save = SAVE.read_bytes()
    if sha(parent) != MAIN_SHA or sha(original) != ORIGINAL_SHA:
        raise RuntimeError("input ROM identity drift")
    if len(save) != 32768:
        raise RuntimeError("SaveRAM size drift")
    prove_parent(parent, original)

    a_bytes, a_meta = build_a(parent, original)
    b_bytes, b_meta = build_b(parent)

    OUT_A.write_bytes(a_bytes)
    shutil.copy2(SAVE, SAVE_A)
    OUT_B.write_bytes(b_bytes)
    shutil.copy2(SAVE, SAVE_B)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_domon_ko_leak_ab_probes.py",
        "status": "pending_user_runtime_validation",
        "parent_sha256": sha(parent),
        "original_sha256": sha(original),
        "saveram_sha256": sha(save),
        "main_unchanged": sha(MAIN.read_bytes()) == MAIN_SHA,
        "live_saveram_unchanged": SAVE.read_bytes() == save,
        "scope": {
            "family": [f"{a:06X}" for a in CURRENT],
            "unchanged_on_purpose": "62663E and all non-family records stay byte-exact to main",
            "why_not_metadata": "4A is speaker/portrait metadata; shared dict この is body token F362 after 4A",
        },
        "test_plan": {
            "order": ["A", "B"],
            "A_pass": "Japanese このうつけものがぁぁ－っ！！ appears -> addresses are live; Korean path is causal",
            "A_fail": "Korean/mixed こい still appears -> wrong live source or stale savestate; do not interpret B",
            "B_pass": "Korean without leading こ -> promote iteration-matched repair path",
            "B_fail": "こ remains despite no direct こ bytes -> caller/predecode barcode next",
        },
        "A": {
            **a_meta,
            "rom": str(OUT_A.relative_to(ROOT)).replace("\\", "/"),
            "save": str(SAVE_A.relative_to(ROOT)).replace("\\", "/"),
            "saveram_sha256": sha(SAVE_A.read_bytes()),
        },
        "B": {
            **b_meta,
            "rom": str(OUT_B.relative_to(ROOT)).replace("\\", "/"),
            "save": str(SAVE_B.relative_to(ROOT)).replace("\\", "/"),
            "saveram_sha256": sha(SAVE_B.read_bytes()),
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(REPORT.relative_to(ROOT)).replace("\\", "/"),
                "A": {"rom": report["A"]["rom"], "sha256": report["A"]["candidate_sha256"], "checksum": report["A"]["checksum"]},
                "B": {"rom": report["B"]["rom"], "sha256": report["B"]["candidate_sha256"], "checksum": report["B"]["checksum"]},
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
