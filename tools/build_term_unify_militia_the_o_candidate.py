#!/usr/bin/env python3
"""Unify confirmed Gundam terms on the current test ROM.

Parent is ``weapon_enc_width13_candidate.wsc``.  Live main TIP is not modified.
Record bodies stay byte-identical; only unaliased stock/ext3 phrase payloads
are rewritten in place.

Confirmed this round (user examples + existing standard + namuwiki):
* 미리샤 → 밀리샤
* 자빈느 → 자비네
* 지・오 → 디・오 (unit/encyclopedia names)
* 토르기스 → 톨기스
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs
from build_terminology_retranslation_candidate import (
    ext3_storage_proof,
    inplace_phrase,
    stock_storage_proof,
)
from expand_dictionary import _walk_zstring_range
from hangul_marker import marker_code
from mixed_residual_reference_union import build_reference_union, guard_slot_writes
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from tbl_code_prefs import flatten_codes, retag_with_original_codes

PARENT = ROOT / "out/patch/weapon_enc_width13_candidate.wsc"
PARENT_SAVE = ROOT / "sram/weapon_enc_width13_candidate.sav"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/term_unify_militia_the_o_candidate.wsc"
OUT_SAVE = ROOT / "sram/term_unify_militia_the_o_candidate.sav"
OUT_REPORT = ROOT / "out/patch/term_unify_militia_the_o_candidate_report.json"

EXPECTED_PARENT = "8439a9f35aac9f3844e8138b9c2537c84667f3bb9575ab179478e6ad26558874"
EXPECTED_MAIN = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

STOCK = {
    0x0B22: ("자빈느", "자비네"),
    0x04E6: ("지・오", "디・오"),
    0x0696: ("지・오", "디・오"),
    0x0C23: ("토르기스", "톨기스"),
}
EXT3 = {
    0x0F0CC: ("된　∀건담。미리샤", "된　∀건담。밀리샤"),
    0x0C4A9: ("잉그레사　미리샤의　기함", "잉그레사　밀리샤의　기함"),
    0x02455: ("미리샤　병사", "밀리샤　병사"),
    0x0FE77: ("지・오█", "디・오█"),
    0x0F107: ("토르기스　강화형으로、한때", "톨기스　강화형으로、한때"),
    0x01739: ("자빈느－－！！", "자비네－－！！"),
    0x02720: ("자빈느　님。", "자비네　님。"),
    0x0471E: ("이게　무슨　일이지、　자빈느？", "이게　무슨　일이지、　자비네？"),
    0x0C49D: ("우리　미리샤의　공적은　되지　않는다。", "우리　밀리샤의　공적은　되지　않는다。"),
    0x0C49E: ("우리　미리샤를　필요로　하지　않게　된다。", "우리　밀리샤를　필요로　하지　않게　된다。"),
}
FORBIDDEN_AFTER = ("미리샤", "자빈느", "지・오", "토르기스")
SCAN_RANGES = (
    (0x590000, 0x5A0000),
    (0x5C0000, 0x5C7900),
    (0x75C000, 0x75E800),
    (0x600000, 0x640000),
)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def encode(text: str, tbl: Tbl, *, old_raw: bytes | None = None, dictionary=None) -> bytes:
    pinned = normalize_ko_text(text)
    if old_raw is not None and dictionary is not None and "█" in pinned:
        flat = flatten_codes(old_raw, dictionary)
        pinned, _notes = retag_with_original_codes(pinned, flat, tbl)
    payload = try_encode_ko_text(
        pinned,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or b"\x00" in payload:
        raise BuildError(f"cannot encode {text!r}")
    return payload


def main() -> int:
    parent = bytes(load_rom(PARENT))
    original = bytes(load_rom(ORIGINAL))
    save = PARENT_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        raise BuildError("parent test ROM identity drifted")
    if sha(bytes(load_rom(MAIN))) != EXPECTED_MAIN:
        raise BuildError("live main TIP drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("parent SaveRAM size drifted")
    if MAIN_SAVE.read_bytes() != save:
        raise BuildError("parent SaveRAM is no longer byte-exact with live main")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)

    slot_payload: dict[int, bytes] = {}
    keeper_abs: dict[int, set[int]] = {}
    planned: list[dict[str, Any]] = []

    def queue(index: int, before: str, after: str, kind: str, proof: dict[str, Any]) -> None:
        actual = strip_pad(dictionary.expand_index(index, tbl))
        if actual != before:
            if actual == after:
                return
            raise BuildError(f"{kind} {index:05X} drifted: {actual!r} != {before!r}")
        raw = bytes(dictionary.raw_entry(index))
        encoded = encode(after, tbl, old_raw=raw, dictionary=dictionary)
        if not proof["ok"]:
            raise BuildError(f"aliased storage at {index:05X}: {proof}")
        if len(encoded) > int(proof["old_len"]):
            raise BuildError(f"in-place growth refused at {index:05X}")
        consumers = union.consumers_for(index)
        slot_payload[index] = encoded
        keeper_abs[index] = {int(c.abs) for c in consumers}
        planned.append(
            {
                "kind": kind,
                "index": f"{index:05X}",
                "before": before,
                "after": after,
                "old_len": int(proof["old_len"]),
                "new_len": len(encoded),
                "consumers": [f"{c.abs:06X}/{c.region}" for c in consumers[:12]],
                "consumer_n": len(consumers),
                "entry_abs": int(proof["entry_abs"]),
            }
        )

    for index, (before, after) in STOCK.items():
        queue(index, before, after, "stock", stock_storage_proof(dictionary, index))
    for index, (before, after) in EXT3.items():
        queue(index, before, after, "ext3", ext3_storage_proof(parent, dictionary, index))

    outcome = guard_slot_writes(
        parent,
        slot_payload,
        union=union,
        keeper_abs=keeper_abs,
        allow_aux_consumers=True,
        justification=(
            "Terminology unify on already-live name75/aux/script phrases. "
            "Keepers are current consumers. Confirmed: 미리샤→밀리샤, 자빈느→자비네, "
            "지・오→디・오, 토르기스→톨기스."
        ),
    )
    if not outcome.ok:
        raise BuildError(outcome.outcome)

    candidate = bytearray(parent)
    allow: list[tuple[int, int]] = []
    for row in planned:
        index = int(row["index"], 16)
        if row["kind"] == "stock":
            proof = stock_storage_proof(dictionary, index)
        else:
            proof = ext3_storage_proof(parent, dictionary, index)
        allow.append(inplace_phrase(candidate, proof, slot_payload[index]))

    checksum = update_ws_checksum(candidate)
    allow.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    unexpected = [run for run in diff_runs(parent, result) if not covered(run, allow)]
    if unexpected:
        raise BuildError(
            "diff outside allowlist: "
            + ", ".join(f"{lo:08X}-{hi:08X}" for lo, hi in unexpected)
        )

    out_dict = make_dictionary_ext3(result, ext_meta, ext3_meta)
    for row in planned:
        got = strip_pad(out_dict.expand_index(int(row["index"], 16), tbl))
        if got != row["after"]:
            raise BuildError(f"verify failed {row['index']}: {got!r}")

    sb = stock_base(result)
    leftover = []
    for start, end in SCAN_RANGES:
        for logical, _orig, _kind in _walk_zstring_range(
            original, start, end, region="scan", max_len=256
        ):
            got = read_encoded_z_safe(result, sb + logical, max_len=256)
            if not got:
                continue
            try:
                text = out_dict.expand(got[0], tbl)
            except Exception:
                continue
            for bad in FORBIDDEN_AFTER:
                if bad in text:
                    leftover.append({"abs": f"{logical:06X}", "bad": bad, "text": strip_pad(text)[:60]})
    if leftover:
        raise BuildError(f"forbidden leftover: {leftover[:8]}")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(PARENT_SAVE, OUT_SAVE)
    report = {
        "ok": True,
        "parent": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, result),
        "saveram": identity(OUT_SAVE),
        "checksum": f"{checksum:04X}",
        "changed_bytes": sum(hi - lo for lo, hi in diff_runs(parent, result)),
        "slots_written": len(planned),
        "guard": outcome.as_dict(),
        "applied": planned,
    }
    atomic_json(OUT_REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["candidate"]["sha256"],
                "checksum": report["checksum"],
                "slots": report["slots_written"],
                "changed_bytes": report["changed_bytes"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        raise SystemExit(1)
