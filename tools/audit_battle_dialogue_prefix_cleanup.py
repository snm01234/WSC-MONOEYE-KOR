#!/usr/bin/env python3
"""Independently audit the runtime-proven 5E:BD90 prefix cleanup candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
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
    le16,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

PATCH = ROOT / "out/patch"
DEFAULT_PARENT = PATCH / "monoeye_ko_expanded.wsc"
DEFAULT_CANDIDATE = PATCH / "battle_dialogue_prefix_cleanup_candidate.wsc"
DEFAULT_SPEC = ROOT / "data/battle_dialogue_prefix_cleanup_ko.json"
DEFAULT_REPORT = PATCH / "battle_dialogue_prefix_cleanup_audit.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META_PATH = PATCH / "ext_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"
ROM_SIZE = 16_777_216


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def has_japanese(text: str) -> bool:
    return any("\u3040" <= ch <= "\u30ff" for ch in text)


def record_payload(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=64)
    if got is None:
        raise RuntimeError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parent", type=Path, default=DEFAULT_PARENT)
    ap.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    ap.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = ap.parse_args(argv)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    row = spec["record"]
    parent = bytes(load_rom(args.parent))
    candidate = bytes(load_rom(args.candidate))
    original = bytes(load_rom(ORIGINAL))
    failures: list[str] = []

    if len(parent) != ROM_SIZE or len(candidate) != ROM_SIZE:
        failures.append("rom_size")
    if sha256(parent) != str(spec["parent_sha256"]).lower():
        failures.append("parent_identity")

    logical = int(row["abs"], 16)
    capacity = int(row["payload_capacity"])
    file_start = stock_base(parent) + logical
    before_payload, before_term = record_payload(parent, logical)
    after_payload, after_term = record_payload(candidate, logical)
    original_payload, _original_term = record_payload(original, logical)
    token = bytes.fromhex(row["ext3_token_hex"])
    expected_after = token + b"\x01" * (capacity - len(token))

    if before_payload.hex().upper() != row["expected_before_hex"]:
        failures.append("before_payload")
    if original_payload.hex().upper() != row["expected_original_hex"]:
        failures.append("original_payload")
    if after_payload != expected_after:
        failures.append("after_payload")
    if before_term != after_term or len(before_payload) != len(after_payload):
        failures.append("boundary")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    d_original = Dictionary(original)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    d_candidate = make_dictionary_ext3(candidate, ext_meta, ext3_meta)

    original_text = d_original.expand(original_payload, tbl)
    before_text = strip_pad(d_parent.expand(before_payload, tbl))
    after_text = strip_pad(d_candidate.expand(after_payload, tbl))
    token_before = strip_pad(d_parent.expand(token, tbl))
    token_after = strip_pad(d_candidate.expand(token, tbl))
    original_prefix, original_body, original_kind = split_prefix_body(original_payload)

    if original_text != row["expected_original_text"]:
        failures.append("original_decode")
    if before_text != row["expected_before_text"]:
        failures.append("before_decode")
    if after_text != row["ko"] or has_japanese(after_text):
        failures.append("after_decode")
    if token_before != row["ko"] or token_after != row["ko"]:
        failures.append("dictionary_token")
    if original_prefix or original_body != original_payload or original_kind != "dialogue":
        failures.append("canonical_prefix_proof")

    changed = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    allowed = set(range(file_start, file_start + capacity)) | {len(parent) - 2, len(parent) - 1}
    unexpected = [offset for offset in changed if offset not in allowed]
    if unexpected:
        failures.append("diff_confinement")

    # The surrounding records must remain byte-identical; 5EBD80 carries a real
    # non-printing battle prefix and must not be "cleaned" by analogy.
    surrounding: list[dict[str, Any]] = []
    for adjacent in (0x5EBD80, 0x5EBD9C):
        parent_payload, parent_term = record_payload(parent, adjacent)
        candidate_payload, candidate_term = record_payload(candidate, adjacent)
        ok = parent_payload == candidate_payload and parent_term == candidate_term
        surrounding.append(
            {
                "abs": f"{adjacent:06X}",
                "ok": ok,
                "payload_hex": candidate_payload.hex().upper(),
            }
        )
        if not ok:
            failures.append(f"surrounding_{adjacent:06X}")

    stored_checksum = le16(candidate, len(candidate) - 2)
    computed_checksum = sum(candidate[:-2]) & 0xFFFF
    if stored_checksum != computed_checksum:
        failures.append("checksum")

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_dialogue_prefix_cleanup.py",
        "ok": not failures,
        "failures": failures,
        "parent": {
            "path": str(args.parent.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            "sha256": sha256(parent),
            "size": len(parent),
        },
        "candidate": {
            "path": str(args.candidate.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            "sha256": sha256(candidate),
            "size": len(candidate),
            "checksum": f"{stored_checksum:04X}",
        },
        "target": {
            "abs": row["abs"],
            "before_hex": before_payload.hex().upper(),
            "after_hex": after_payload.hex().upper(),
            "original_text": original_text,
            "before_text": before_text,
            "after_text": after_text,
            "japanese_residual_count": sum(
                1 for ch in after_text if "\u3040" <= ch <= "\u30ff"
            ),
            "prefix_parser": {
                "prefix_hex": original_prefix.hex().upper(),
                "kind": original_kind,
                "body_is_full_payload": original_body == original_payload,
            },
            "boundary_preserved": before_term == after_term,
            "token_unchanged": token_before == token_after == row["ko"],
        },
        "diff": {
            "changed_byte_count": len(changed),
            "changed_offsets": [f"{offset:06X}" for offset in changed],
            "unexpected_changed_bytes": len(unexpected),
        },
        "surrounding_records": surrounding,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
