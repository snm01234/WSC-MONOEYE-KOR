#!/usr/bin/env python3
"""Independent static audit for id_command_effect_width_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, load_rom, stock_base
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/id_command_effect_width_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/id_command_effect_width_candidate.sav"
CATALOG = ROOT / "data/id_command_effect_width_ko.json"
BUILD = ROOT / "out/patch/id_command_effect_width_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/id_command_effect_width_candidate_audit.json"

EXPECTED_MAIN = "2cb645e4bb700db4c111041f8cfbb9c65b8a0b937b8877fe9f76cc92ed3a1dda"
EXPECTED_APPLIED = 270
MAX_CELLS = 20
SAVE_SIZE = 32_768


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


def main() -> int:
    parent = bytes(load_rom(MAIN))
    candidate = bytes(load_rom(CANDIDATE))
    if sha(parent) != EXPECTED_MAIN:
        raise AuditError("live main TIP identity drifted")
    catalog = load(CATALOG)
    build = load(BUILD)
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
    for row in rows:
        logical = int(str(row["abs"]), 16)
        payload_len = int(row["payload_len"])
        payload = candidate[sb + logical : sb + logical + payload_len]
        actual = candidate_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(row["ko"])).rstrip("\u3000 \t")
        before = parent_dictionary.expand(
            bytes.fromhex(str(row["current_payload_hex"])), tbl
        ).rstrip("\u3000 \t")
        if (
            actual != expected
            or len(actual) > MAX_CELLS
            or any(is_japanese_character(ch) for ch in actual)
            or before != str(row["before"]).rstrip("\u3000 \t")
            or candidate[sb + logical + payload_len] != 0
        ):
            render_failures.append(
                {"abs": row["abs"], "expected": expected, "actual": actual}
            )
        target_extents.append((sb + logical, sb + logical + payload_len))

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
        CANDIDATE_SAVE.is_file()
        and CANDIDATE_SAVE.stat().st_size == SAVE_SIZE
        and sha(CANDIDATE_SAVE.read_bytes()) == sha(MAIN_SAVE.read_bytes())
    )
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    stored = int.from_bytes(candidate[-2:], "little")
    computed = sum(candidate[:-2]) & 0xFFFF
    ok = (
        not render_failures
        and not invariance.get("failures")
        and not unaccounted
        and save_ok
        and runtime_exact
        and stored == computed
        and int(build.get("applied_count") or -1) == EXPECTED_APPLIED
    )
    report = {
        "ok": ok,
        "parent": identity(MAIN, parent),
        "candidate": identity(CANDIDATE, candidate),
        "applied_count": len(rows),
        "render_failures": render_failures,
        "invariance_failures": (invariance.get("failures") or [])[:20],
        "unaccounted_diff_runs": unaccounted,
        "runtime_banks_7A_7F_exact": runtime_exact,
        "checksum_exact": stored == computed,
        "checksum": f"{stored:04X}",
        "save_matches_main": save_ok,
        "max_after_cells": max(int(row["after_cells"]) for row in rows),
        "main_unchanged": sha(parent) == EXPECTED_MAIN,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "out": str(OUT), "applied": len(rows)}, ensure_ascii=True))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
