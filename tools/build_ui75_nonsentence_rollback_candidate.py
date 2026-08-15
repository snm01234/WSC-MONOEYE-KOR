#!/usr/bin/env python3
"""Restore the UI75 walker-noise fragment at 75B2DD.

The bank59/name75 stack rewrote ``　ウ　移動`` to an ext3 portal for ``이동``.
That payload is not a sentence; UI75 does not consume the portal as Hangul, so
the map-select grab/move label showed Japanese.  This candidate:

* restores the original 5-byte payload ``01 77 01 F1 C2`` and keeps the NUL;
* returns exclusive ext3 slot ``043F8`` to the empty pointer ``2000``;
* does not touch bank59 titles, name75 tails, 攻/분 glyphs, or map padding.

Candidate only.  The live main TIP and SaveRAM are never written.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import (
    Tbl,
    le16,
    load_rom,
    patch_expansion_bank,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text
from patch_3byte_dict_token import EXP3_SLOTS, bank_local_for_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BACKUP = ROOT / "out/patch/backup/20260813_115727_pre_bank59_enc5c_name75/monoeye_ko_expanded.wsc"
CATALOG = ROOT / "data/ui75_nonsentence_rollback.json"
BANK59_CATALOG = ROOT / "data/bank59_enc5c_name75_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/ui75_nonsentence_rollback_candidate.wsc"
OUT_SAVE = ROOT / "sram/ui75_nonsentence_rollback_candidate.sav"
REPORT = ROOT / "out/patch/ui75_nonsentence_rollback_candidate_report.json"

EXPECTED_MAIN = "3eb5b66d0ba5b0d22ff39275039b95ab720e39743ebc61aedc544c066908de21"
EXPECTED_BACKUP = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EMPTY_PTR = 0x2000


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def load_catalog() -> dict[str, Any]:
    value = json.loads(CATALOG.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("catalog root must be object")
    if str(value.get("parent_tip_sha256") or "").lower() != EXPECTED_MAIN:
        raise BuildError("catalog parent identity drifted")
    if str(value.get("backup_tip_sha256") or "").lower() != EXPECTED_BACKUP:
        raise BuildError("catalog backup identity drifted")
    record = value.get("record")
    if not isinstance(record, dict):
        raise BuildError("catalog record missing")
    return value


def main() -> int:
    catalog = load_catalog()
    record = dict(catalog["record"])
    parent = bytes(load_rom(MAIN))
    backup = bytes(load_rom(BACKUP))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN:
        raise BuildError("live main TIP identity drifted")
    if len(backup) != ROM_SIZE or sha256(backup) != EXPECTED_BACKUP:
        raise BuildError("rollback backup identity drifted")
    save = MAIN_SAVE.read_bytes()
    if len(save) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    logical = int(str(record["abs"]), 16)
    payload_len = int(record["payload_len"])
    current = bytes.fromhex(str(record["current_payload_hex"]))
    restore = bytes.fromhex(str(record["restore_payload_hex"]))
    slot = int(str(record["ext3_slot"]), 16)
    restore_ptr = int(str(record["restore_ext3_pointer"]), 16)
    if payload_len != 5 or current != bytes.fromhex("E51833F801") or restore != bytes.fromhex("017701F1C2"):
        raise BuildError("rollback payload contract drifted")
    if slot != 0x043F8 or restore_ptr != EMPTY_PTR:
        raise BuildError("ext3 slot contract drifted")

    sb = stock_base(parent)
    at = sb + logical
    if parent[at : at + payload_len] != current:
        raise BuildError("live 75B2DD payload drifted")
    if parent[at + payload_len] != 0:
        raise BuildError("live 75B2DD terminator drifted")
    if backup[at : at + payload_len] != restore:
        raise BuildError("backup 75B2DD payload drifted")
    if backup[at + payload_len] != 0:
        raise BuildError("backup 75B2DD terminator drifted")
    if parent.find(bytes.fromhex("E51833F8")) != at:
        raise BuildError("ext3 portal is not exclusive to 75B2DD")
    if parent.find(bytes.fromhex("E51833F8"), at + 1) >= 0:
        raise BuildError("ext3 portal has additional consumers")

    seg, local = bank_local_for_index(slot)
    live_bank = bytearray(slice_expansion_bank(parent, seg))
    backup_bank = slice_expansion_bank(backup, seg)
    live_ptr = le16(live_bank, local * 2)
    backup_ptr = le16(backup_bank, local * 2)
    if backup_ptr != EMPTY_PTR:
        raise BuildError("backup slot 043F8 was not empty")
    if live_ptr == EMPTY_PTR:
        raise BuildError("live slot 043F8 is already empty")
    if live_bank[EMPTY_PTR] != 0:
        raise BuildError("ext3 empty_at is not a lone NUL")

    live_bank[local * 2 : local * 2 + 2] = EMPTY_PTR.to_bytes(2, "little")
    scratch = bytearray(parent)
    scratch[at : at + payload_len] = restore
    scratch[at + payload_len] = 0
    patch_expansion_bank(scratch, seg, live_bank)
    checksum = update_ws_checksum(scratch)
    candidate = bytes(scratch)

    pointer_file = seg * 0x10000 + local * 2
    allowed = [
        (at, at + payload_len),
        (pointer_file, pointer_file + 2),
        (len(candidate) - 2, len(candidate)),
    ]
    unaccounted = [run for run in diff_runs(parent, candidate) if not covered(run, allowed)]
    if unaccounted:
        raise BuildError(f"unaccounted diff runs: {unaccounted[:8]}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    restored_text = candidate_dictionary.expand(restore, tbl)
    live_text = parent_dictionary.expand(current, tbl)
    if restored_text != parent_dictionary.expand(restore, tbl):
        raise BuildError("restored payload no longer expands to the pre-stack text")
    if candidate[at : at + payload_len] != restore:
        raise BuildError("candidate payload write failed")

    bank59 = json.loads(BANK59_CATALOG.read_text(encoding="utf-8"))
    kept_rows = [
        row
        for row in (bank59.get("records") or [])
        if str(row.get("abs") or "").upper() != "75B2DD"
    ]
    render_failures: list[dict[str, Any]] = []
    for row in kept_rows:
        address = str(row["abs"]).upper()
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        payload_n = int(row["payload_len"])
        payload = candidate[sb + int(address, 16) : sb + int(address, 16) + payload_n]
        actual = candidate_dictionary.expand(payload[len(prefix) :], tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(row["ko"])).rstrip("\u3000 \t")
        if actual != expected or any(is_japanese_character(ch) for ch in actual):
            render_failures.append({"abs": address, "expected": expected, "actual": actual})
    if render_failures:
        raise BuildError(f"kept bank59/name75 renders drifted: {render_failures[:5]}")

    invariance = verify_non_target_invariance(
        parent,
        candidate,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={logical},
    )
    if invariance.get("failures"):
        raise BuildError(f"non-target invariance failed: {invariance['failures'][:5]}")

    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    if not runtime_exact:
        raise BuildError("runtime banks 7A/7F drifted")

    atomic_bytes(OUT_ROM, candidate)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "ok": True,
        "parent": identity(MAIN, parent),
        "backup": identity(BACKUP, backup),
        "candidate": identity(OUT_ROM, candidate),
        "checksum": f"{checksum:04X}",
        "record": {
            "abs": "75B2DD",
            "live_payload_hex": current.hex().upper(),
            "restore_payload_hex": restore.hex().upper(),
            "live_render": live_text,
            "restore_render": restored_text,
            "ext3_slot": f"{slot:05X}",
            "ext3_bank": f"{seg:02X}",
            "ext3_local": f"{local:03X}",
            "live_pointer": f"{live_ptr:04X}",
            "restore_pointer": f"{EMPTY_PTR:04X}",
        },
        "kept_bank59_name75_records": len(kept_rows),
        "unaccounted_diff_runs": [],
        "invariance": {"checked": invariance.get("checked"), "failure_count": 0},
        "runtime_banks_7A_7F_exact": True,
        "save": identity(OUT_SAVE),
        "save_matches_main": sha256(OUT_SAVE.read_bytes()) == sha256(save),
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate_sha256": report["candidate"]["sha256"],
                "checksum": report["checksum"],
                "restore_render": restored_text,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
