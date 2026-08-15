#!/usr/bin/env python3
"""Build weapon/name/encyclopedia-width follow-up on the UI padding candidate.

Parent is ``ui_onebyte_and_map_padding_candidate.wsc``.  Live main TIP and
SaveRAM are never modified.  Record bodies stay byte-identical; only private
ext3 phrase payloads are rewritten in place.

1. Weapon ``배부　빔　캐논`` → ``복부　빔　캐논`` (user request; JP is 背部).
2. Encyclopedia/unit name ``갈바르디β`` → ``가르발디β``.
3. MS encyclopedia description lines over 13 visual cells are shortened.
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
from build_terminology_retranslation_candidate import ext3_storage_proof, inplace_phrase
from expand_dictionary import _walk_zstring_range
from hangul_marker import marker_code
from mixed_residual_reference_union import build_reference_union, guard_slot_writes
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

PARENT = ROOT / "out/patch/ui_onebyte_and_map_padding_candidate.wsc"
PARENT_SAVE = ROOT / "sram/ui_onebyte_and_map_padding_candidate.sav"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CATALOG = ROOT / "data/encyclopedia_width13_weapon_name_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/weapon_enc_width13_candidate.wsc"
OUT_SAVE = ROOT / "sram/weapon_enc_width13_candidate.sav"
OUT_REPORT = ROOT / "out/patch/weapon_enc_width13_candidate_report.json"

EXPECTED_PARENT = "5929e41a58d9127b304e20ece1c96afdea127e029a946f8bc2995f0185a7b860"
EXPECTED_MAIN = "5d3cb79f7d4b5f2674f6a28c2c8033f31d52e5b88b6ca579b033fe6701905229"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ENC_START, ENC_END = 0x5C34CA, 0x5C7900
ENC_LIMIT = 13


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


def cells(text: str) -> int:
    return len(strip_pad(text))


def encode(text: str, tbl: Tbl) -> bytes:
    payload = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or b"\x00" in payload:
        raise BuildError(f"cannot encode {text!r}")
    return payload


def catalog_targets(doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    weapon = dict(doc["weapon"])
    weapon["kind"] = "weapon"
    rows.append(weapon)
    for row in doc["names"]:
        rows.append({**row, "kind": "name"})
    for row in doc["encyclopedia"]:
        rows.append({**row, "kind": "encyclopedia"})
    return rows


def main() -> int:
    parent = bytes(load_rom(PARENT))
    original = bytes(load_rom(ORIGINAL))
    save = PARENT_SAVE.read_bytes()
    main_rom = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        raise BuildError("parent candidate identity drifted")
    if sha(main_rom) != EXPECTED_MAIN:
        raise BuildError("live main TIP drifted; refusing to continue")
    if len(save) != SAVE_SIZE:
        raise BuildError("parent SaveRAM size drifted")
    if MAIN_SAVE.read_bytes() != save:
        # Parent save was copied byte-exact from main; keep that invariant.
        raise BuildError("parent SaveRAM is no longer byte-exact with live main")

    doc = json.loads(CATALOG.read_text(encoding="utf-8"))
    targets = catalog_targets(doc)
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)

    slot_payload: dict[int, bytes] = {}
    keeper_abs: dict[int, set[int]] = {}
    planned: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in targets:
        index = int(row["index"], 16)
        before = strip_pad(str(row["before"]))
        after = normalize_ko_text(str(row["after"]))
        actual = strip_pad(dictionary.expand_index(index, tbl))
        if actual != before:
            raise BuildError(f"slot {index:05X} drifted: {actual!r} != {before!r}")
        if row["kind"] == "encyclopedia" and cells(after) > ENC_LIMIT:
            raise BuildError(f"{row.get('abs')} after exceeds {ENC_LIMIT}: {after!r}")
        encoded = encode(after, tbl)
        if index in seen:
            if slot_payload[index] != encoded:
                raise BuildError(f"duplicate slot {index:05X} with different payload")
            continue
        seen.add(index)
        script = union.script_consumers(index)
        if script:
            raise BuildError(
                f"slot {index:05X} has script consumers: "
                + ", ".join(f"{c.abs:06X}" for c in script[:6])
            )
        consumers = union.consumers_for(index)
        keepers = {int(c.abs) for c in consumers}
        listed = {int(row[k], 16) for k in ("abs",) if row.get(k)}
        listed.update(int(value, 16) for value in row.get("also_abs") or [])
        if listed and not listed <= keepers:
            raise BuildError(
                f"slot {index:05X} listed abs {sorted(f'{v:06X}' for v in listed)} "
                f"not subset of consumers {sorted(f'{v:06X}' for v in keepers)}"
            )
        proof = ext3_storage_proof(parent, dictionary, index)
        if not proof["ok"]:
            raise BuildError(f"aliased ext3 storage at {index:05X}: {proof}")
        if len(encoded) > int(proof["old_len"]):
            raise BuildError(
                f"in-place growth refused at {index:05X}: {len(encoded)} > {proof['old_len']}"
            )
        slot_payload[index] = encoded
        keeper_abs[index] = keepers
        planned.append(
            {
                **row,
                "index": f"{index:05X}",
                "after": after,
                "new_len": len(encoded),
                "old_len": int(proof["old_len"]),
                "consumers": [f"{c.abs:06X}/{c.region}" for c in consumers],
                "entry_abs": int(proof["entry_abs"]),
            }
        )

    outcome = guard_slot_writes(
        parent,
        slot_payload,
        union=union,
        keeper_abs=keeper_abs,
        allow_aux_consumers=True,
        justification=(
            "UI/encyclopedia repair: rewrite already-aux/name75 private ext3 "
            "phrases in place. Keepers are the current consumers; no script "
            "refs. Weapon 배부→복부, unit 갈바르디→가르발디, encyclopedia "
            "description lines clipped at 13 cells."
        ),
    )
    if not outcome.ok:
        raise BuildError(outcome.outcome)

    candidate = bytearray(parent)
    allow: list[tuple[int, int]] = []
    for row in planned:
        index = int(row["index"], 16)
        proof = ext3_storage_proof(parent, dictionary, index)
        interval = inplace_phrase(candidate, proof, slot_payload[index])
        allow.append(interval)
        row["write"] = {"start": f"{interval[0]:06X}", "end": f"{interval[1]:06X}"}

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
    sb = stock_base(result)
    for row in planned:
        index = int(row["index"], 16)
        got = strip_pad(out_dict.expand_index(index, tbl))
        if got != row["after"]:
            raise BuildError(f"verify failed {index:05X}: {got!r}")

    over13 = []
    for logical, _orig, _kind in _walk_zstring_range(
        original, ENC_START, ENC_END, region="enc", max_len=256
    ):
        got = read_encoded_z_safe(result, sb + logical, max_len=256)
        if not got:
            continue
        try:
            text = out_dict.expand(got[0], tbl)
        except Exception:
            continue
        for line in text.split("<E62F>"):
            n = cells(line)
            if n > ENC_LIMIT:
                over13.append({"abs": f"{logical:06X}", "cells": n, "text": strip_pad(line)})
    if over13:
        raise BuildError(f"encyclopedia still over {ENC_LIMIT}: {over13[:8]}")

    weapon_text = strip_pad(out_dict.expand_index(int(doc["weapon"]["index"], 16), tbl))
    if weapon_text != "복부　빔　캐논":
        raise BuildError(f"weapon verify failed: {weapon_text!r}")
    if "갈바르디" in out_dict.expand_index(0xC3F8, tbl):
        raise BuildError("encyclopedia name still 갈바르디")
    if "가르발디β" not in strip_pad(out_dict.expand_index(0xC3F8, tbl)):
        raise BuildError("encyclopedia name missing 가르발디β")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(PARENT_SAVE, OUT_SAVE)
    report = {
        "ok": True,
        "parent": identity(PARENT, parent),
        "candidate": identity(OUT_ROM, result),
        "saveram": identity(OUT_SAVE),
        "checksum": f"{checksum:04X}",
        "changed_bytes": sum(hi - lo for lo, hi in diff_runs(parent, result)),
        "runs": len(diff_runs(parent, result)),
        "slots_written": len(planned),
        "guard": outcome.as_dict(),
        "weapon": doc["weapon"],
        "names": doc["names"],
        "encyclopedia_written": sum(1 for row in planned if row["kind"] == "encyclopedia"),
        "encyclopedia_over13_after": 0,
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
