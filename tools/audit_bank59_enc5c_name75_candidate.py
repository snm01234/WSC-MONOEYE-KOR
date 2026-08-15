#!/usr/bin/env python3
"""Independent static audit for bank59_enc5c_name75_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, load_rom, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = ROOT / "out/patch/term_unify_round2_candidate.wsc"
CANDIDATE = ROOT / "out/patch/bank59_enc5c_name75_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/bank59_enc5c_name75_candidate.sav"
CATALOG = ROOT / "data/bank59_enc5c_name75_ko.json"
BUILD = ROOT / "out/patch/bank59_enc5c_name75_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/bank59_enc5c_name75_candidate_audit.json"

EXPECTED_PARENT = "3d8701a7d43cc551155d9eddaa692886ea763ead8b65a293520c78e0e2be41c3"
EXPECTED_MAIN = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"
SAVE_SIZE = 32_768


class AuditError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def main() -> int:
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    main_rom = bytes(load_rom(MAIN))
    if sha(parent) != EXPECTED_PARENT:
        raise AuditError("parent identity drifted")
    if sha(main_rom) != EXPECTED_MAIN:
        raise AuditError("live main TIP was modified")
    catalog = load(CATALOG)
    build = load(BUILD)
    if build.get("ok") is not True:
        raise AuditError("build report failed")
    rows = [dict(row) for row in catalog.get("records") or []]
    applied = [dict(row) for row in build.get("applied") or []]
    catalog_abs = {str(row.get("abs") or "").upper() for row in rows}
    applied_abs = {str(row.get("abs") or "").upper() for row in applied}
    if catalog_abs != applied_abs:
        raise AuditError("applied population drifted from catalog")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(candidate)
    render_failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(str(row["abs"]), 16)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        payload_len = int(row["payload_len"])
        payload = candidate[sb + logical : sb + logical + payload_len]
        actual = candidate_dictionary.expand(payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        expected = str(row["ko"]).rstrip("\u3000 \t")
        if payload[: len(prefix)] != prefix or actual != expected or any(
            is_japanese_character(ch) for ch in actual
        ):
            render_failures.append(
                {"abs": row["abs"], "expected": expected, "actual": actual}
            )
        if candidate[sb + logical + payload_len] != 0:
            render_failures.append({"abs": row["abs"], "reason": "terminator_changed"})

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={int(str(row["abs"]), 16) for row in rows},
    )
    save_ok = (
        CANDIDATE_SAVE.is_file()
        and CANDIDATE_SAVE.stat().st_size == SAVE_SIZE
        and sha(CANDIDATE_SAVE.read_bytes()) == sha(MAIN_SAVE.read_bytes())
    )
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000]
        == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    ok = (
        not render_failures
        and not invariance.get("failures")
        and save_ok
        and runtime_exact
        and sha(main_rom) == EXPECTED_MAIN
    )
    report = {
        "ok": ok,
        "parent": identity(PARENT, parent),
        "candidate": identity(CANDIDATE, candidate),
        "main": identity(MAIN, main_rom),
        "save_matches_main": save_ok,
        "applied_count": len(applied),
        "render_failures": render_failures,
        "invariance_failures": (invariance.get("failures") or [])[:20],
        "runtime_banks_7A_7F_exact": runtime_exact,
        "main_unchanged": sha(main_rom) == EXPECTED_MAIN,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "out": str(OUT), "applied": len(applied)}, ensure_ascii=True))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
