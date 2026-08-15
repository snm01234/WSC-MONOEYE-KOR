#!/usr/bin/env python3
"""Independent static audit for ui75_nonsentence_rollback_candidate.wsc."""
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
from monoeye_rom import Tbl, le16, load_rom, slice_expansion_bank, stock_base
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import bank_local_for_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BACKUP = ROOT / "out/patch/backup/20260813_115727_pre_bank59_enc5c_name75/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/ui75_nonsentence_rollback_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/ui75_nonsentence_rollback_candidate.sav"
CATALOG = ROOT / "data/ui75_nonsentence_rollback.json"
BANK59_CATALOG = ROOT / "data/bank59_enc5c_name75_ko.json"
BUILD = ROOT / "out/patch/ui75_nonsentence_rollback_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/ui75_nonsentence_rollback_candidate_audit.json"

EXPECTED_MAIN = "3eb5b66d0ba5b0d22ff39275039b95ab720e39743ebc61aedc544c066908de21"
EXPECTED_BACKUP = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"
SAVE_SIZE = 32_768
EMPTY_PTR = 0x2000


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
    backup = bytes(load_rom(BACKUP))
    candidate = bytes(load_rom(CANDIDATE))
    if sha(parent) != EXPECTED_MAIN:
        raise AuditError("live main TIP identity drifted")
    if sha(backup) != EXPECTED_BACKUP:
        raise AuditError("rollback backup identity drifted")
    catalog = load(CATALOG)
    build = load(BUILD)
    if build.get("ok") is not True:
        raise AuditError("build report failed")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != sha(candidate):
        raise AuditError("candidate SHA drifted from build report")
    record = dict(catalog.get("record") or {})
    logical = int(str(record["abs"]), 16)
    restore = bytes.fromhex(str(record["restore_payload_hex"]))
    current = bytes.fromhex(str(record["current_payload_hex"]))
    slot = int(str(record["ext3_slot"]), 16)
    sb = stock_base(candidate)
    at = sb + logical
    failures: list[str] = []
    if parent[at : at + 5] != current:
        failures.append("parent payload drifted")
    if candidate[at : at + 5] != restore:
        failures.append("candidate did not restore 75B2DD")
    if backup[at : at + 5] != restore:
        failures.append("backup payload drifted")
    if candidate[at + 5] != 0:
        failures.append("terminator moved")
    if candidate.find(bytes.fromhex("E51833F8")) >= 0:
        failures.append("ext3 portal still present")

    seg, local = bank_local_for_index(slot)
    pointer = le16(slice_expansion_bank(candidate, seg), local * 2)
    if pointer != EMPTY_PTR:
        failures.append(f"slot 043F8 pointer is {pointer:04X}, expected 2000")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    restored = candidate_dictionary.expand(restore, tbl)
    if restored != parent_dictionary.expand(restore, tbl):
        failures.append("restored expand mismatch")

    bank59 = load(BANK59_CATALOG)
    render_failures: list[dict[str, Any]] = []
    kept = 0
    for row in bank59.get("records") or []:
        address = str(row.get("abs") or "").upper()
        if address == "75B2DD":
            continue
        kept += 1
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        payload_n = int(row["payload_len"])
        payload = candidate[sb + int(address, 16) : sb + int(address, 16) + payload_n]
        actual = candidate_dictionary.expand(payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(row["ko"])).rstrip("\u3000 \t")
        if actual != expected or any(is_japanese_character(ch) for ch in actual):
            render_failures.append({"abs": address, "expected": expected, "actual": actual})

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={logical},
    )
    pointer_file = seg * 0x10000 + local * 2
    allowed = [
        (at, at + 5),
        (pointer_file, pointer_file + 2),
        (len(candidate) - 2, len(candidate)),
    ]
    unaccounted = [run for run in diff_runs(parent, candidate) if not covered(run, allowed)]
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
        not failures
        and not render_failures
        and not invariance.get("failures")
        and not unaccounted
        and save_ok
        and runtime_exact
        and stored == computed
        and kept == 39
    )
    report = {
        "ok": ok,
        "parent": identity(MAIN, parent),
        "backup": identity(BACKUP, backup),
        "candidate": identity(CANDIDATE, candidate),
        "failures": failures,
        "kept_bank59_name75_records": kept,
        "render_failures": render_failures,
        "invariance_failures": (invariance.get("failures") or [])[:20],
        "unaccounted_diff_runs": [
            {"start": f"{lo:06X}", "end": f"{hi:06X}"} for lo, hi in unaccounted
        ],
        "runtime_banks_7A_7F_exact": runtime_exact,
        "checksum_exact": stored == computed,
        "checksum": f"{stored:04X}",
        "save_matches_main": save_ok,
        "restore_render": restored,
        "main_unchanged": sha(parent) == EXPECTED_MAIN,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": ok, "out": str(OUT), "kept": kept}, ensure_ascii=True))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
