#!/usr/bin/env python3
"""Audit dialogue hidden behind speaker/control bytes such as ``08 F0 00``.

Generic encoded-z parsing treats F0-FF as dictionary leads, so a structural
speaker record ``08 actor_id 00`` can accidentally consume its NUL terminator
when actor_id is in that range. The immediately following dialogue then falls
out of the translation inventory. This read-only audit discovers those control
collisions in the original 60-63 script banks, identifies immediate following
dialogue records, and decodes the same logical addresses from a target TIP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_ORIGINAL = ROOT / "data/monoeye.tbl"
TBL_TARGET = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/speaker_dictlead_nul_collision_audit.json"
JP_RE = re.compile(r"[ぁ-んァ-ン一-龥]")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode(rom: bytes, dictionary, tbl: Tbl, logical: int, *, expanded: bool) -> dict[str, Any] | None:
    base = stock_base(rom) if expanded else 0
    got = read_encoded_z_safe(rom, base + logical, max_len=256)
    if got is None:
        return None
    payload = bytes(got[0])
    prefix, body, kind = split_prefix_body(payload)
    return {
        "payload_hex": payload.hex().upper(),
        "prefix_hex": prefix.hex().upper(),
        "body_hex": body.hex().upper(),
        "kind": kind,
        "text": dictionary.expand(body, tbl).rstrip("　 "),
        "terminator": f"{int(got[1]) - base:06X}",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original", type=Path, default=ORIGINAL)
    ap.add_argument("--target", type=Path, default=TARGET)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    original = args.original.read_bytes()
    target = args.target.read_bytes()
    t0 = Tbl.load(TBL_ORIGINAL)
    t1 = Tbl.load(TBL_TARGET)
    d0 = Dictionary(original)
    d1 = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))

    collisions: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for logical in range(0x600000, 0x640000 - 4):
        if not (
            original[logical] == 0x08
            and 0xF0 <= original[logical + 1] <= 0xFF
            and original[logical + 2] == 0
        ):
            continue
        next_start = logical + 3
        row = {
            "speaker_record": f"{logical:06X}",
            "actor_id": f"{original[logical + 1]:02X}",
            "next_record_start": f"{next_start:06X}",
            "next_lead": f"{original[next_start]:02X}",
        }
        collisions.append(row)
        orec = decode(original, d0, t0, next_start, expanded=False)
        if not orec or orec["kind"] != "dialogue" or not orec["body_hex"]:
            continue
        trec = decode(target, d1, t1, next_start, expanded=True)
        current = "" if trec is None else str(trec["text"])
        hidden.append({
            **row,
            "source_jp": orec["text"],
            "original_prefix_hex": orec["prefix_hex"],
            "original_terminator": orec["terminator"],
            "target_text": current,
            "target_prefix_hex": None if trec is None else trec["prefix_hex"],
            "target_terminator": None if trec is None else trec["terminator"],
            "japanese_or_mixed_remaining": bool(JP_RE.search(current)),
            "over_20": len(current) > 20,
        })

    residuals = [r for r in hidden if r["japanese_or_mixed_remaining"]]
    over = [r for r in hidden if r["over_20"]]
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_speaker_dictlead_nul_collisions.py",
        "read_only": True,
        "ok": not residuals and not over,
        "inputs": {
            "original": {"path": str(args.original), "size": len(original), "sha256": sha(original)},
            "target": {"path": str(args.target), "size": len(target), "sha256": sha(target)},
        },
        "root_cause": "08 actor_id 00 is a structural speaker record; actor_id F0-FF must not be interpreted as a dictionary lead whose trail consumes the NUL",
        "counts": {
            "speaker_dictlead_nul_collisions": len(collisions),
            "immediate_hidden_dialogues": len(hidden),
            "japanese_or_mixed_remaining": len(residuals),
            "over_20": len(over),
        },
        "collisions": collisions,
        "hidden_dialogues": hidden,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    for row in residuals:
        print(row["next_record_start"], row["source_jp"], "=>", row["target_text"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
