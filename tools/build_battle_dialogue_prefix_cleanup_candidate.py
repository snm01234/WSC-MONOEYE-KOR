#!/usr/bin/env python3
"""Build a one-record candidate that removes a runtime-proven false aux prefix.

At logical 5E:BD90 the current TIP contains:

    14 | E5 18 D5 29 | 01 ...
    う    우와아아아……！

The conservative bank-5E prefix rule preserved the first code unit.  Runtime
output proves 0x14 is printable text, not control data.  This builder shifts the
existing ext3 token to the record start, pads the vacated byte at the tail, and
keeps the record boundary, dictionary, and SaveRAM unchanged.
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

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/battle_dialogue_prefix_cleanup_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META_PATH = PATCH / "ext_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "battle_dialogue_prefix_cleanup_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_dialogue_prefix_cleanup_candidate.sav"
OUT_REPORT = PATCH / "battle_dialogue_prefix_cleanup_build_report.json"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "size": len(data),
        "sha256": sha256(data),
    }


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    row = spec["record"]
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE:
        raise BuildError(f"unexpected main TIP size: {len(parent)}")
    parent_sha = sha256(parent)
    if parent_sha != str(spec["parent_sha256"]).lower():
        raise BuildError(
            f"main TIP identity drifted: expected {spec['parent_sha256']}, got {parent_sha}"
        )
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("main SaveRAM is missing or not 32 KiB")

    logical = int(row["abs"], 16)
    capacity = int(row["payload_capacity"])
    sb_parent = stock_base(parent)
    sb_original = stock_base(original)
    file_start = sb_parent + logical

    current_got = read_encoded_z_safe(parent, file_start, max_len=64)
    original_got = read_encoded_z_safe(original, sb_original + logical, max_len=64)
    if current_got is None or original_got is None:
        raise BuildError("target record is unreadable")
    current_payload, current_term = bytes(current_got[0]), int(current_got[1])
    original_payload, original_term = bytes(original_got[0]), int(original_got[1])
    if len(current_payload) != capacity:
        raise BuildError(f"payload capacity drifted: {len(current_payload)} != {capacity}")
    if current_payload.hex().upper() != row["expected_before_hex"]:
        raise BuildError(
            f"target bytes drifted: {current_payload.hex().upper()} != {row['expected_before_hex']}"
        )
    if original_payload.hex().upper() != row["expected_original_hex"]:
        raise BuildError("original record bytes drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    d_original = Dictionary(original)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)

    original_text = d_original.expand(original_payload, tbl)
    before_text = d_parent.expand(current_payload, tbl)
    if original_text != row["expected_original_text"]:
        raise BuildError(f"original decode drifted: {original_text!r}")
    if strip_pad(before_text) != row["expected_before_text"]:
        raise BuildError(f"current decode drifted: {before_text!r}")

    original_prefix, original_body, original_kind = split_prefix_body(original_payload)
    if original_prefix or original_kind != "dialogue" or original_body != original_payload:
        raise BuildError(
            "runtime-proven byte is still classified as a structural prefix by the canonical parser"
        )

    token = bytes.fromhex(row["ext3_token_hex"])
    if len(token) != 4 or not current_payload.startswith(bytes([0x14]) + token):
        raise BuildError("expected ext3 token is not immediately after the false prefix")
    token_text = d_parent.expand(token, tbl)
    if strip_pad(token_text) != row["ko"]:
        raise BuildError(f"existing ext3 token does not render target Korean: {token_text!r}")

    new_payload = token + b"\x01" * (capacity - len(token))
    candidate = bytearray(parent)
    candidate[file_start : file_start + capacity] = new_payload
    if candidate[current_term] != 0:
        raise BuildError("record terminator moved")
    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    candidate_got = read_encoded_z_safe(candidate_bytes, file_start, max_len=64)
    if candidate_got is None:
        raise BuildError("candidate target record is unreadable")
    candidate_payload, candidate_term = bytes(candidate_got[0]), int(candidate_got[1])
    if candidate_payload != new_payload or candidate_term != current_term:
        raise BuildError("candidate boundary or payload mismatch")
    d_candidate = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    after_text = d_candidate.expand(candidate_payload, tbl)
    if strip_pad(after_text) != row["ko"]:
        raise BuildError(f"candidate decode mismatch: {after_text!r}")

    changed = [i for i, (before, after) in enumerate(zip(parent, candidate_bytes)) if before != after]
    allowed = set(range(file_start, file_start + capacity)) | {len(parent) - 2, len(parent) - 1}
    unaccounted = [offset for offset in changed if offset not in allowed]
    if unaccounted:
        raise BuildError(f"unaccounted changes: {unaccounted[:16]}")
    if not all(file_start + delta in changed for delta in range(5)):
        raise BuildError("the false prefix/token shift did not change the expected five bytes")

    atomic_write(OUT_ROM, candidate_bytes)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if OUT_SAVE.stat().st_size != SAVE_SIZE or OUT_SAVE.read_bytes() != MAIN_SAVE.read_bytes():
        raise BuildError("candidate SaveRAM pair is not byte-identical to main SaveRAM")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_dialogue_prefix_cleanup_candidate.py",
        "status": "candidate_static_verified",
        "ok": True,
        "parent_rom": {
            "path": str(MAIN.relative_to(ROOT)).replace("\\", "/"),
            "size": len(parent),
            "sha256": parent_sha,
        },
        "candidate_rom": identity(OUT_ROM),
        "candidate_save": identity(OUT_SAVE),
        "checksum": f"{checksum:04X}",
        "diagnosis": {
            "abs": row["abs"],
            "false_prefix_hex": "14",
            "false_prefix_text": "う",
            "canonical_original_prefix_hex": original_prefix.hex().upper(),
            "canonical_original_kind": original_kind,
            "original_text": original_text,
            "before_text": strip_pad(before_text),
            "after_text": strip_pad(after_text),
            "cause": row["reason"],
        },
        "record": {
            "logical_abs": row["abs"],
            "file_start": f"{file_start:06X}",
            "payload_capacity": capacity,
            "terminator_file_offset": f"{current_term:06X}",
            "original_terminator_offset": f"{original_term:06X}",
            "before_hex": current_payload.hex().upper(),
            "after_hex": candidate_payload.hex().upper(),
            "ext3_token_hex": token.hex().upper(),
        },
        "verification": {
            "boundary_preserved": candidate_term == current_term,
            "dictionary_token_reused": True,
            "dictionary_data_changed": False,
            "changed_byte_count": len(changed),
            "changed_offsets": [f"{offset:06X}" for offset in changed],
            "unaccounted_changed_bytes": len(unaccounted),
            "save_pair_unchanged": True,
        },
    }
    OUT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
