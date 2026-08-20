#!/usr/bin/env python3
"""Build a narrow name75 candidate unifying 팔라스・아테네 -> 파라스・아테네.

Only two current-main name75 display records are retargeted to new private ext3
phrases.  The already-correct MS encyclopedia record is left untouched.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_stage3_reflow_idhelp_followup_candidate as base  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base, update_ws_checksum, ws_header  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/pallas_athene_name_unify_candidate.wsc"
OUT_SAVE = ROOT / "sram/pallas_athene_name_unify_candidate.sav"
OUT_REPORT = ROOT / "out/patch/pallas_athene_name_unify_report.json"

EXPECTED_MAIN_SHA = "a44dcb232c70956c36726d55494f8d4a59608648efaa9ff27379127475e8a159"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
TARGETS = (
    {"address": "75C629", "before": "팔라스・아테네", "after": "파라스・아테네", "payload_hex": "E518EF69010101"},
    {"address": "75CF6E", "before": "팔라스・아테네█", "after": "파라스・아테네█", "payload_hex": "E518EE710101010101"},
)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def ident(path: Path, data: bytes | None = None) -> dict:
    payload = path.read_bytes() if data is None else data
    return {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "size": len(payload), "sha256": sha(payload)}


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def main() -> int:
    parent = MAIN.read_bytes()
    live_save = SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main identity drifted: {sha(parent)}")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("SaveRAM size drifted")

    tbl = Tbl.load(TBL_PATH)
    ext = load_ext_meta(EXT_META)
    ext3 = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext, ext3)

    checked = []
    sb = stock_base(parent)
    for spec in TARGETS:
        logical = int(spec["address"], 16)
        payload, term = payload_at(parent, logical)
        if payload.hex().upper() != spec["payload_hex"]:
            raise BuildError(f"{spec['address']}: payload drifted {payload.hex().upper()}")
        if term != sb + logical + len(payload) or parent[term] != 0:
            raise BuildError(f"{spec['address']}: terminator drifted")
        rendered = dictionary.expand(payload, tbl).rstrip("　 \t")
        if rendered != spec["before"]:
            raise BuildError(f"{spec['address']}: before render drifted {rendered!r}")
        checked.append({**spec, "payload_len": len(payload), "file_at": sb + logical})

    out = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    phrase_tokens, allocations = base.allocate_phrases(
        out, parent, dictionary, tbl, marker_code(), [row["after"] for row in checked], allowed
    )

    retargets = []
    for row in checked:
        token = phrase_tokens[row["after"]]
        at = int(row["file_at"])
        old_payload = bytes.fromhex(row["payload_hex"])
        new_payload = bytearray(old_payload)
        new_payload[:4] = token
        if out[at : at + len(old_payload)] != old_payload:
            raise BuildError(f"{row['address']}: parent payload changed during build")
        out[at : at + len(old_payload)] = new_payload
        allowed.append((at, at + 4))
        retargets.append({
            "address": row["address"],
            "before": row["before"],
            "after": row["after"],
            "old_token": old_payload[:4].hex().upper(),
            "new_token": token.hex().upper(),
            "payload_len": len(old_payload),
        })

    update_ws_checksum(out)
    candidate = bytes(out)
    allowed.append((len(candidate) - 2, len(candidate)))
    after_dict = make_dictionary_ext3(candidate, ext, ext3)

    for row in checked:
        logical = int(row["address"], 16)
        payload, term = payload_at(candidate, logical)
        rendered = after_dict.expand(payload, tbl).rstrip("　 \t")
        if rendered != row["after"]:
            raise BuildError(f"{row['address']}: candidate render mismatch {rendered!r}")
        if len(payload) != row["payload_len"] or term != sb + logical + len(payload):
            raise BuildError(f"{row['address']}: structure changed")

    runs = base.diff_runs(parent, candidate)
    unexpected = [run for run in runs if not base.covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected}")
    if MAIN.read_bytes() != parent or SAVE.read_bytes() != live_save:
        raise BuildError("main TIP or live SaveRAM changed during build")
    if int(ws_header(candidate)["checksum"]) != (sum(candidate[:-2]) & 0xFFFF):
        raise BuildError("WonderSwan checksum invalid")

    OUT_ROM.write_bytes(candidate)
    OUT_SAVE.write_bytes(live_save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_pallas_athene_name_unify_candidate.py",
        "ok": True,
        "parent": ident(MAIN, parent),
        "candidate": ident(OUT_ROM, candidate),
        "save": ident(OUT_SAVE, live_save),
        "checksum": f"{int(ws_header(candidate)['checksum']):04X}",
        "counts": {"target_records": len(checked), "new_ext3_phrases": len(allocations), "unexpected_diff_runs": 0},
        "retargets": retargets,
        "ext3_allocations": allocations,
        "checks": {
            "main_tip_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": SAVE.read_bytes() == live_save,
            "candidate_saveram_exact_copy": OUT_SAVE.read_bytes() == live_save,
            "record_lengths_preserved": True,
            "terminators_preserved": True,
            "checksum_valid": True,
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "candidate_sha256": report["candidate"]["sha256"], "checksum": report["checksum"], "retargets": retargets}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
