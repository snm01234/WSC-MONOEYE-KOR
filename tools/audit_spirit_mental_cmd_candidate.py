#!/usr/bin/env python3
"""Independent static audit for spirit_mental_cmd_mixed_quote_candidate.wsc."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from mixed_residual_classification import hangul_character_count, is_japanese_character
from monoeye_rom import Tbl, load_rom, stock_base
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/spirit_mental_cmd_mixed_quote_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/spirit_mental_cmd_mixed_quote_candidate.sav"
CATALOG = ROOT / "data/spirit_mental_cmd_mixed_and_quote_ko.json"
BUILD = ROOT / "out/patch/spirit_mental_cmd_mixed_quote_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/spirit_mental_cmd_mixed_quote_candidate_audit.json"

EXPECTED_MAIN = "b0438b51f0a6f5fab94418433f3fac6c551ab1ee8f434627ee4e42816928216a"
EXPECTED_APPLIED = 54
MAX_CELLS = 20
SAVE_SIZE = 32_768
DIANA = "5CC8AA"
RECOA = "5CAC75"


class AuditError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parent", type=Path, default=MAIN)
    ap.add_argument("--parent-save", type=Path, default=MAIN_SAVE)
    ap.add_argument("--candidate", type=Path, default=CANDIDATE)
    ap.add_argument("--candidate-save", type=Path, default=CANDIDATE_SAVE)
    ap.add_argument("--build", type=Path, default=BUILD)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--expected-parent-sha", default=EXPECTED_MAIN)
    ap.add_argument(
        "--expect-build-promotion-allowed",
        choices=("true", "false"),
        default="false",
    )
    args = ap.parse_args(argv)
    expected_parent = args.expected_parent_sha.lower()
    expected_build_promotion = args.expect_build_promotion_allowed == "true"

    parent = bytes(load_rom(args.parent))
    candidate = bytes(load_rom(args.candidate))
    if sha(parent) != expected_parent:
        raise AuditError("live main TIP identity drifted")
    catalog = load(CATALOG)
    build = load(args.build)
    if build.get("ok") is not True:
        raise AuditError("build report failed")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != sha(candidate):
        raise AuditError("candidate SHA drifted from build report")
    rows = [dict(row) for row in catalog.get("records") or []]
    if len(rows) != EXPECTED_APPLIED:
        raise AuditError(f"catalog count drifted: {len(rows)}")

    tbl = Tbl.load(TBL_PATH)
    parent_dictionary = make_dictionary_ext3(
        parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    candidate_dictionary = make_dictionary_ext3(
        candidate, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    sb = stock_base(candidate)
    render_failures: list[dict[str, Any]] = []
    target_extents: list[tuple[int, int]] = []
    diana_after = recoa_after = ""
    for row in rows:
        logical = int(str(row["abs"]), 16)
        payload_len = int(row["payload_len"])
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        payload = candidate[sb + logical : sb + logical + payload_len]
        actual = candidate_dictionary.expand(payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(row["ko"])).rstrip("\u3000 \t")
        if (
            actual != expected
            or len(actual) > MAX_CELLS
            or any(is_japanese_character(ch) for ch in actual)
            or hangul_character_count(actual) == 0
            or candidate[sb + logical + payload_len] != 0
            or not payload.startswith(prefix)
            or payload[len(prefix) : len(prefix) + 2] == b"\xE5\x19"
        ):
            render_failures.append(
                {"abs": row["abs"], "expected": expected, "actual": actual}
            )
        target_extents.append((sb + logical, sb + logical + payload_len))
        if str(row["abs"]).upper() == DIANA:
            diana_after = actual
        if str(row["abs"]).upper() == RECOA:
            recoa_after = actual

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(str(row["abs"]), 16) for row in rows},
    )
    unaccounted = [
        {"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}"}
        for lo, hi in diff_runs(parent, candidate)
        if not covered((lo, hi), target_extents + [(len(candidate) - 2, len(candidate))])
        and not (0x110000 <= lo < 0x210000)
    ]
    save_ok = (
        args.candidate_save.is_file()
        and args.candidate_save.stat().st_size == SAVE_SIZE
        and sha(args.candidate_save.read_bytes()) == sha(args.parent_save.read_bytes())
    )
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    stored = int.from_bytes(candidate[-2:], "little")
    computed = sum(candidate[:-2]) & 0xFFFF
    main_unchanged = sha(parent) == expected_parent
    ok = (
        not render_failures
        and not invariance.get("failures")
        and not unaccounted
        and save_ok
        and runtime_exact
        and stored == computed
        and int(build.get("applied_count") or -1) == EXPECTED_APPLIED
        and diana_after == "다음전투　자신　공격력과　명중이　상승"
        and recoa_after == "있지　않으면　살아있는　기분이　안　들어"
        and main_unchanged
        and build.get("promotion_allowed") is expected_build_promotion
    )
    report = {
        "ok": ok,
        "parent": identity(args.parent, parent),
        "candidate": identity(args.candidate, candidate),
        "applied_count": len(rows),
        "render_failures": render_failures,
        "invariance_failures": (invariance.get("failures") or [])[:20],
        "unaccounted_diff_runs": unaccounted,
        "runtime_banks_7A_7F_exact": runtime_exact,
        "checksum_exact": stored == computed,
        "checksum": f"{stored:04X}",
        "save_matches_main": save_ok,
        "max_after_cells": max(int(row["after_cells"]) for row in rows),
        "main_unchanged": main_unchanged,
        "diana_5CC8AA": diana_after,
        "recoa_5CAC75": recoa_after,
        "promotion_allowed": ok and expected_build_promotion,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "out": str(args.out), "applied": len(rows)}, ensure_ascii=True))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
