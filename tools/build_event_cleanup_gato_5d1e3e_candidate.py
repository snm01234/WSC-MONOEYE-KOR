#!/usr/bin/env python3
"""Build a narrow Anavel Gato battle portrait/text regression fix.

Parent:
  out/patch/event_bank_false_replacement_cleanup_candidate.wsc

Runtime evidence:
  - Anavel Gato battle shows a black/broken portrait and visible ``こ暴``.

Static binding:
  - The exact visible ``こ暴`` decodes from current record 5D:1E3E when its
    whole-record E5 18 portal is interpreted by the battle consumer.
  - Japanese original record 5D:1E3E is ``0F | body | 00``; therefore 0F is
    authoritative speaker/portrait metadata, not text.
  - The current whole record incorrectly lost 0F and starts with E5 18.
  - Duplicate Gato phrase record 5D:1C02 already uses native stock token F65A
    and renders exactly ``결국、가치관이　다른　듯하군……``.

Fix:
  restore metadata 0F and reuse the proven native token F65A, padding the
  original record extent with 01. No dictionary, event-bank, or other battle
  record bytes are changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_battle_dialogue_runtime_integrated_cleanup_candidate import clean  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
    ws_header,
)

PATCH = ROOT / "out/patch"
PARENT = PATCH / "event_bank_false_replacement_cleanup_candidate.wsc"
PARENT_SAVE = ROOT / "sram/event_bank_false_replacement_cleanup_candidate.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

OUT_ROM = PATCH / "event_cleanup_gato_5d1e3e_candidate.wsc"
OUT_SAVE = ROOT / "sram/event_cleanup_gato_5d1e3e_candidate.sav"
OUT_REPORT = PATCH / "event_cleanup_gato_5d1e3e_report.json"

EXPECTED_PARENT_SHA = "3dfa367018944b9b32fc783038facc2f7da4fa5ba79a02dadd9da01791061920"
EXPECTED_PARENT_SIZE = 16_777_216
EXPECTED_SAVE_SIZE = 32_768
TARGET = 0x5D1E3E
DUPLICATE_NATIVE = 0x5D1C02
EXPECTED_PARENT_RECORD = bytes.fromhex("E518E1E501010101010101010101010101")
EXPECTED_ORIGINAL_RECORD = bytes.fromhex("0FF9B507F3E6E25F17DA14F49EF549F191")
EXPECTED_DUPLICATE_RECORD = bytes.fromhex("F65A0101010101010101010101010101")
EXPECTED_TEXT = "결국、가치관이　다른　듯하군……"
REPLACEMENT = bytes.fromhex("0FF65A") + b"\x01" * 14


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def checksum_valid(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def diff_positions(a: bytes, b: bytes) -> list[int]:
    if len(a) != len(b):
        raise BuildError("ROM size mismatch")
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


def main() -> int:
    parent = bytes(load_rom(PARENT))
    original = bytes(load_rom(ORIGINAL))
    save = PARENT_SAVE.read_bytes()
    if len(parent) != EXPECTED_PARENT_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent identity drifted: {len(parent)} {sha(parent)}")
    if len(save) != EXPECTED_SAVE_SIZE:
        raise BuildError(f"paired SaveRAM size drifted: {len(save)}")

    sb = stock_base(parent)
    so = stock_base(original)
    target = read_encoded_z_safe(parent, sb + TARGET, max_len=64)
    source = read_encoded_z_safe(original, so + TARGET, max_len=64)
    duplicate = read_encoded_z_safe(parent, sb + DUPLICATE_NATIVE, max_len=64)
    if target is None or source is None or duplicate is None:
        raise BuildError("failed to read bounded target/source/duplicate record")
    parent_record, parent_term = target
    original_record, original_term = source
    duplicate_record, duplicate_term = duplicate
    if parent_record != EXPECTED_PARENT_RECORD:
        raise BuildError(f"target parent drifted: {parent_record.hex().upper()}")
    if original_record != EXPECTED_ORIGINAL_RECORD:
        raise BuildError(f"target original drifted: {original_record.hex().upper()}")
    if duplicate_record != EXPECTED_DUPLICATE_RECORD:
        raise BuildError(f"duplicate native record drifted: {duplicate_record.hex().upper()}")
    if parent_term != sb + TARGET + len(parent_record):
        raise BuildError("target terminator is not immediately after payload")
    if original_term != so + TARGET + len(original_record):
        raise BuildError("original target terminator drifted")
    if duplicate_term != sb + DUPLICATE_NATIVE + len(duplicate_record):
        raise BuildError("duplicate terminator drifted")

    tbl = Tbl.load(TBL)
    ext = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    stock = Dictionary(parent)
    parent_render = clean(ext.expand(parent_record, tbl))
    duplicate_render = clean(stock.expand(duplicate_record, tbl))
    token_render = clean(stock.expand(bytes.fromhex("F65A"), tbl))
    if parent_render != EXPECTED_TEXT or duplicate_render != EXPECTED_TEXT or token_render != EXPECTED_TEXT:
        raise BuildError(
            f"render proof drifted: parent={parent_render!r} duplicate={duplicate_render!r} token={token_render!r}"
        )

    candidate = bytearray(parent)
    before_boundary = bytes(parent[sb + TARGET : sb + TARGET + len(parent_record) + 9])
    candidate[sb + TARGET : sb + TARGET + len(parent_record)] = REPLACEMENT
    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)

    result_target = read_encoded_z_safe(result, sb + TARGET, max_len=64)
    if result_target is None:
        raise BuildError("result target no longer terminates")
    result_record, result_term = result_target
    result_stock = Dictionary(result)
    result_render = clean(result_stock.expand(result_record[1:], tbl))

    after_boundary = bytes(result[sb + TARGET : sb + TARGET + len(parent_record) + 9])
    diffs = diff_positions(parent, result)
    non_checksum = [x for x in diffs if x < len(result) - 2]
    expected_non_checksum = [sb + TARGET + i for i in range(len(parent_record)) if parent_record[i] != REPLACEMENT[i]]

    checks = {
        "runtime_visible_signature_bound_to_5d1e3e": parent_render == EXPECTED_TEXT and parent_record[:4] == bytes.fromhex("E518E1E5"),
        "original_metadata_0f_restored": result_record[0] == 0x0F and original_record[0] == 0x0F,
        "native_duplicate_f65a_proven": duplicate_record[:2] == bytes.fromhex("F65A") and duplicate_render == EXPECTED_TEXT,
        "target_no_longer_starts_e518": result_record[:2] != bytes.fromhex("E518"),
        "target_body_is_native_f65a": result_record[1:3] == bytes.fromhex("F65A"),
        "target_render_exact": result_render == EXPECTED_TEXT,
        "record_extent_preserved": len(result_record) == len(parent_record) == len(original_record),
        "terminator_preserved": result_term == parent_term and result[result_term] == 0,
        "next_boundary_preserved": after_boundary[len(parent_record):] == before_boundary[len(parent_record):],
        "nonchecksum_delta_exactly_target": non_checksum == expected_non_checksum,
        "event_cleanup_parent_other_bytes_unchanged": all(TARGET <= (x - sb) < TARGET + len(parent_record) for x in non_checksum),
        "checksum_valid": checksum_valid(result),
    }
    if not all(checks.values()):
        raise BuildError(json.dumps(checks, ensure_ascii=False, indent=2))

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_SAVE, save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_event_cleanup_gato_5d1e3e_candidate.py",
        "ok": True,
        "published": False,
        "parent": {"path": rel(PARENT), "sha256": sha(parent).upper(), "size": len(parent)},
        "output": {
            "rom": {"path": rel(OUT_ROM), "sha256": sha(result).upper(), "size": len(result)},
            "save": {"path": rel(OUT_SAVE), "sha256": sha(save).upper(), "size": len(save)},
            "checksum": f"{checksum:04X}",
        },
        "runtime_evidence": {
            "character": "Anavel Gato",
            "visible_corruption": "black/broken portrait + こ暴",
            "bound_record": "5D:1E3E",
        },
        "history": {
            "2026-08-07": "5D1E3E was included in duplicate-proven voice text conversion as a whole-record E5 18 portal.",
            "later_structure_audit": "5D1E3E was quarantined because the safe snapshot disagreed with authoritative metadata/prefix; therefore broad structure repair did not rewrite it.",
            "2026-08-13_metadata0f_fix": "the 35-record metadata=0F native-only fix intentionally excluded one safe-snapshot mismatch; 5D1E3E is that surviving mismatch.",
            "event_cleanup_relation": "event_bank_false_replacement_cleanup_candidate changes event banks only; it did not introduce the 5D1E3E regression.",
        },
        "proof": {
            "parent_record_hex": parent_record.hex().upper(),
            "original_record_hex": original_record.hex().upper(),
            "duplicate_5d1c02_hex": duplicate_record.hex().upper(),
            "replacement_hex": REPLACEMENT.hex().upper(),
            "expected_text": EXPECTED_TEXT,
            "parent_whole_record_render": parent_render,
            "duplicate_native_render": duplicate_render,
            "result_body_render": result_render,
        },
        "checks": checks,
        "promotion": "blocked_pending_user_runtime_verification",
        "test_protocol": [
            "Use event_cleanup_gato_5d1e3e_candidate.wsc with the paired SaveRAM.",
            "Reproduce the same Anavel Gato battle that showed a black/broken portrait and こ暴.",
            "Confirm Gato portrait/sprite is normal and the line renders as 결국、가치관이 다른 듯하군…… without stray Japanese glyphs.",
            "Confirm battle progression continues normally.",
        ],
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["output"]["rom"],
        "save": report["output"]["save"],
        "checksum": report["output"]["checksum"],
        "record": report["proof"],
        "checks": checks,
        "report": rel(OUT_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
