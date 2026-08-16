#!/usr/bin/env python3
"""Read-only 20-cell width audit for bank59 event/dialogue records.

The historical dialogue_20cell audit covered bank59 only through a reviewed
subset.  This audit instead enumerates every bank59 aux/event address from the
archived source-bound aux block inventory, plus the explicit prefixed-dialogue
inventory.  It never writes a ROM and never authorizes structural rewrites.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base  # noqa: E402

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
ORIGINAL_TBL = ROOT / "data/monoeye.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
AUX_BLOCKS = ROOT / "legacy/release_core_20260815/out/script/aux_text_blocks.json"
PREFIX_RULE = ROOT / "legacy/release_core_20260815/out/script/aux_prefix_rule.json"
PREFIXED = ROOT / "legacy/release_core_20260815/out/script/runtime_text_residual_prefixed_dialogue_sheet.csv"
DEFAULT_OUT = ROOT / "out/patch/bank59_event_width_audit.json"

# The current composite runtime still applies the promoted five-page E5 18
# alias mapping even though the old exact-leaf hash detector became stale.
# Bank59 width must be measured through the same mapping the game executes.
RUNTIME_ALIAS_PAGE_COUNT = 5
RUNTIME_ALIAS_LOCAL_START = 0x0600
RUNTIME_ALIAS_SEG0 = 0x21


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def payload_at(rom: bytes, logical: int) -> bytes | None:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=512)
    return None if got is None else bytes(got[0])


def prefix_map() -> dict[int, int]:
    doc = json.loads(PREFIX_RULE.read_text(encoding="utf-8"))
    result: dict[int, int] = {}
    for rows in (doc.get("records") or {}).values():
        for row in rows:
            result[int(row["abs"], 16)] = int(row["prefix_bytes"])
    return result


def source_addresses() -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    doc = json.loads(AUX_BLOCKS.read_text(encoding="utf-8"))
    blocks = doc if isinstance(doc, list) else doc.get("blocks", [])
    for block in blocks:
        if str(block.get("bank") or "").upper() != "59":
            continue
        for row in block.get("targets") or []:
            logical = int(row["abs"], 16)
            result[logical] = {
                "address": f"{logical:06X}",
                "source": "aux_text_blocks",
                "inventory_jp": str(row.get("jp") or ""),
            }
    if PREFIXED.is_file():
        with PREFIXED.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                address = str(row.get("record_start") or row.get("abs") or "").upper()
                if not address.startswith("59"):
                    continue
                logical = int(address, 16)
                result.setdefault(
                    logical,
                    {
                        "address": address,
                        "source": "prefixed_dialogue_sheet",
                        "inventory_jp": str(
                            row.get("original_body")
                            or row.get("original_jp")
                            or row.get("jp")
                            or ""
                        ),
                    },
                )
                # Keep the source-bound current snapshot only as a fallback for
                # old prefixed records whose live ext3 slot now looks empty to
                # the static dictionary reader.  It is used only when the target
                # record payload still matches the snapshot byte-exactly.
                result[logical]["snapshot_current_body"] = str(row.get("current_body") or "")
                result[logical]["snapshot_current_payload_hex"] = str(
                    row.get("current_payload_hex") or ""
                ).upper()
                if row.get("prefix_hex"):
                    result[logical]["sheet_prefix_len"] = len(bytes.fromhex(row["prefix_hex"]))
    return result


def active_dictionary(rom: bytes) -> Dictionary:
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    base = make_dictionary(rom, ext_meta)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks != 16 or str(ext3_meta.get("exp_seg0") or "11").upper() != "11":
        raise RuntimeError("current ext3 metadata no longer matches the 16-bank composite runtime")
    return Dictionary(
        rom,
        count=base.count,
        ext_ptr_off=base.ext_ptr_off,
        ext_seg=base.ext_seg,
        stock_count=base.stock_count,
        ext_in_expansion=base.ext_in_expansion,
        ext3_ptr_off=0,
        ext3_seg=0x11,
        ext3_banks=num_banks,
        ext3_alias_page_count=RUNTIME_ALIAS_PAGE_COUNT,
        ext3_alias_local_start=RUNTIME_ALIAS_LOCAL_START,
        ext3_alias_seg=RUNTIME_ALIAS_SEG0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--start", type=lambda value: int(value, 16), default=0x590000)
    parser.add_argument("--end", type=lambda value: int(value, 16), default=0x59FFFF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    target = args.target.read_bytes()
    original = ORIGINAL.read_bytes()
    tbl = Tbl.load(TBL)
    original_tbl = Tbl.load(ORIGINAL_TBL)
    target_dictionary = active_dictionary(target)
    original_dictionary = Dictionary(original)
    prefixes = prefix_map()
    inventory = source_addresses()

    rows: list[dict[str, Any]] = []
    unreadable: list[str] = []
    for logical, inv in sorted(inventory.items()):
        if logical < args.start or logical > args.end:
            continue
        source_payload = payload_at(original, logical)
        target_payload = payload_at(target, logical)
        if source_payload is None or target_payload is None:
            unreadable.append(f"{logical:06X}")
            continue
        prefix_len = int(inv.get("sheet_prefix_len", prefixes.get(logical, 0)))
        # Explicit scenario/dialogue first-line control prefix is authoritative
        # when the archived aux prefix inventory did not include the row.
        if logical not in prefixes and source_payload.startswith(bytes.fromhex("173418")):
            prefix_len = 3
        if prefix_len > len(source_payload) or prefix_len > len(target_payload):
            unreadable.append(f"{logical:06X}:prefix")
            continue
        source_text = original_dictionary.expand(source_payload[prefix_len:], original_tbl)
        current_text = strip_pad(target_dictionary.expand(target_payload[prefix_len:], tbl))
        decode_source = "live_ext3_dictionary"
        snapshot_hex = str(inv.get("snapshot_current_payload_hex") or "")
        snapshot_body = str(inv.get("snapshot_current_body") or "")
        if (
            not current_text
            and snapshot_body
            and snapshot_hex
            and target_payload.hex().upper() == snapshot_hex
        ):
            current_text = strip_pad(snapshot_body)
            decode_source = "byte_bound_prefixed_snapshot_fallback"
        cells = len(current_text)
        rows.append(
            {
                "address": f"{logical:06X}",
                "source": inv["source"],
                "prefix_len": prefix_len,
                "original_japanese": source_text,
                "current_text": current_text,
                "decode_source": decode_source,
                "cells": cells,
                "over_20": cells > 20,
                "record_payload_hex": target_payload.hex().upper(),
            }
        )

    offenders = [row for row in rows if row["over_20"]]
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_bank59_event_width.py",
        "status": "pass" if not offenders and not unreadable else "fail",
        "target": {
            "path": str(args.target.resolve()),
            "size": len(target),
            "sha256": sha(target),
        },
        "scope": {
            "bank": "59",
            "start": f"{args.start:06X}",
            "end": f"{args.end:06X}",
            "cell_limit": 20,
            "inventory": [str(AUX_BLOCKS), str(PREFIXED)],
        },
        "counts": {
            "records_checked": len(rows),
            "over_20": len(offenders),
            "unreadable": len(unreadable),
        },
        "offenders": offenders,
        "unreadable": unreadable,
        "rows": rows,
        "policy": "read-only; width evidence does not authorize structural rewriting",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "target_sha256": report["target"]["sha256"],
                "counts": report["counts"],
                "offenders": [
                    {"address": row["address"], "cells": row["cells"], "text": row["current_text"]}
                    for row in offenders
                ],
                "report": str(args.out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
