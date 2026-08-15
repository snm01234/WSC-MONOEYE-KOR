#!/usr/bin/env python3
"""Independent static audit for the encyclopedia gojuon-index candidate."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base
from normalize_ko_text import normalize_ko_text
from scan_false_segptr_writes import main as scan_false_segptr

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CATALOG = ROOT / "data/encyclopedia_kana_index_ko.json"
OUT_DIR = ROOT / "out/patch/encyclopedia_kana_index_candidate"
CANDIDATE = OUT_DIR / "monoeye_ko_expanded_encyclopedia_kana_index_test.wsc"
CANDIDATE_SAVE = OUT_DIR / "monoeye_ko_expanded_encyclopedia_kana_index_test.sav"
BUILD = OUT_DIR / "encyclopedia_kana_index_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
FALSE_SEGPTR = OUT_DIR / "encyclopedia_kana_index_false_segptr.json"
OUT = OUT_DIR / "encyclopedia_kana_index_audit.json"

EXPECTED_MAIN_SHA = "0ff2bc7398c5b677d02bc1d81df21d12dc7731d2d16d62c3cc7cd25b1c74ca11"
EXPECTED_ROWS = 9
KEEP_LATIN = 0x75B8C6
KEEP_LATIN_HEX = "E1C0E0F5E1C907E132"
SAVE_SIZE = 32_768
NEIGHBORS = (
    (0x75B882, 7),  # 전함도감 payload+NUL, ends before 75B889
    (0x75B8D0, 3),  # 건담 payload+NUL
    (0x75B8D3, 7),  # ００８０ payload+NUL
)


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha256(data)}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    result = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if result is None:
        raise AuditError(f"unreadable record {logical:06X}")
    return bytes(result[0]), int(result[1])


def main() -> int:
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    if sha256(parent) != EXPECTED_MAIN_SHA:
        raise AuditError("live main TIP identity drifted")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    if build.get("ok") is not True:
        raise AuditError("build report failed")
    rows = [dict(row) for row in catalog.get("records") or []]
    applied = [dict(row) for row in build.get("records") or []]
    if {str(row.get("abs") or "").upper() for row in rows} != {
        str(row.get("abs") or "").upper() for row in applied
    }:
        raise AuditError("applied population drifted from catalog")
    if len(rows) != EXPECTED_ROWS:
        raise AuditError(f"catalog population drifted: {len(rows)}")
    reported = str((build.get("candidate") or {}).get("sha256") or "")
    if reported != sha256(candidate):
        raise AuditError("candidate SHA does not match build report")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(candidate)
    render_failures: list[dict[str, Any]] = []
    for row in rows:
        logical = int(str(row["abs"]), 16)
        payload, terminator = payload_at(candidate, logical)
        actual = candidate_dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(row["ko"])).rstrip("\u3000 \t")
        if actual != expected or any(is_japanese_character(ch) for ch in actual):
            render_failures.append(
                {"abs": row["abs"], "expected": expected, "actual": actual}
            )
        if len(payload) != int(row["payload_len"]):
            render_failures.append({"abs": row["abs"], "reason": "payload_len_changed"})
        if terminator != sb + logical + int(row["payload_len"]) or candidate[terminator] != 0:
            render_failures.append({"abs": row["abs"], "reason": "terminator_changed"})
        if len(actual) > int(row["max_visual_cells"]):
            render_failures.append({"abs": row["abs"], "reason": "visual_width_exceeded"})

    latin = candidate[sb + KEEP_LATIN : sb + KEEP_LATIN + len(bytes.fromhex(KEEP_LATIN_HEX))]
    latin_ok = latin == bytes.fromhex(KEEP_LATIN_HEX)
    neighbor_ok = True
    for logical, length in NEIGHBORS:
        if parent[sb + logical : sb + logical + length] != candidate[sb + logical : sb + logical + length]:
            neighbor_ok = False

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
        and sha256(CANDIDATE_SAVE.read_bytes()) == sha256(MAIN_SAVE.read_bytes())
    )
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    scan_false_segptr(["--target", str(CANDIDATE), "--out", str(FALSE_SEGPTR)])
    false_segptr = json.loads(FALSE_SEGPTR.read_text(encoding="utf-8"))
    false_ok = (
        false_segptr.get("ok") is True and int(false_segptr.get("sites_found") or 0) == 0
    )
    ok = (
        not render_failures
        and latin_ok
        and neighbor_ok
        and invariance.get("ok") is True
        and save_ok
        and runtime_exact
        and false_ok
        and sha256(MAIN.read_bytes()) == EXPECTED_MAIN_SHA
    )
    report = {
        "ok": ok,
        "generated_by": "tools/audit_encyclopedia_kana_index_candidate.py",
        "parent": identity(MAIN, parent),
        "candidate": identity(CANDIDATE, candidate),
        "save_matches_live": save_ok,
        "applied_count": len(applied),
        "render_failures": render_failures,
        "latin_index_row_unchanged": latin_ok,
        "neighbors_unchanged": neighbor_ok,
        "invariance_ok": invariance.get("ok") is True,
        "invariance_failures": (invariance.get("failures") or [])[:20],
        "runtime_banks_7A_7F_exact": runtime_exact,
        "false_segptr_sites": int(false_segptr.get("sites_found") or 0),
        "main_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
    }
    atomic_json(OUT, report)
    print(
        json.dumps(
            {"ok": ok, "out": rel(OUT), "applied": len(applied), "false_segptr": report["false_segptr_sites"]},
            ensure_ascii=True,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
