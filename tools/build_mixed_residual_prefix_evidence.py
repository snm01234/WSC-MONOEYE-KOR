#!/usr/bin/env python3
"""Normalize extraction/apply prefix evidence for residual discovery.

The output contains only positive prefixes that are unique by address, agree
byte-for-byte with the Original-ROM-derived record, and carry that record's
payload digest.

Three sources feed the report, in descending precedence:

1. ``aux_ko_report.json`` apply rows: bytes actually applied to the ROM.
2. ``translation_sheet.csv``: the translator worksheet column.
3. ``extract_script.split_prefix_body`` re-derived over every proven Original
   record.  The worksheet is filtered to ``kind == "dialogue" and
   looks_like_jp(jp)``, so records whose body is already Korean carry no
   worksheet row and previously lost their prefix.  Re-deriving with the same
   parser removes that selection filter without weakening the evidence bar:
   every row is still bound to the Original address, prefix bytes, and payload
   digest, and a row is dropped when it disagrees with a higher-precedence
   source.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from extract_script import split_prefix_body  # noqa: E402
from mixed_residual_models import (  # noqa: E402
    DiscoveryInputIdentities,
    identify_evidence,
    identify_rom,
)
from mixed_residual_records import (  # noqa: E402
    AUX_EVIDENCE_KIND,
    AUX_EVIDENCE_PRODUCER,
    OriginalRomProvenRecordEnumerator,
    parse_aux_text_blocks,
)
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

GENERATED_BY = "tools/build_mixed_residual_prefix_evidence.py"
EVIDENCE_KIND = "record_prefixes"
PARSER_SOURCE = "tools/extract_script.split_prefix_body"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def build(
    original_path: Path,
    working_path: Path,
    blocks_path: Path,
    script_csv: Path,
    aux_report_path: Path,
    table_path: Path,
) -> dict[str, Any]:
    original = identify_rom(original_path, "original")
    working = identify_rom(working_path, "working")
    blocks = identify_evidence(
        blocks_path,
        kind=AUX_EVIDENCE_KIND,
        generated_by=AUX_EVIDENCE_PRODUCER,
        original_rom=original,
    )
    blocks_document = _read_object(blocks_path)
    vetted_blocks = parse_aux_text_blocks(blocks_document)
    original_bytes = original_path.read_bytes()
    original_base = stock_base(original_bytes)
    rows: dict[int, dict[str, Any]] = {}

    def original_payload(address: int, *, max_len: int) -> bytes | None:
        got = read_encoded_z_safe(
            original_bytes, original_base + address, max_len=max_len
        )
        if got is None:
            return None
        return bytes(got[0])

    csv.field_size_limit(10_000_000)
    with script_csv.open(encoding="utf-8-sig", newline="") as stream:
        for source in csv.DictReader(stream):
            prefix_hex = (source.get("prefix_hex") or "").strip()
            if not prefix_hex:
                continue
            address = int(str(source["abs"]), 16)
            if not 0x600000 <= address < 0x700000:
                continue
            prefix = bytes.fromhex(prefix_hex)
            payload = original_payload(address, max_len=256)
            if payload is None:
                continue
            if not prefix or not payload.startswith(prefix):
                raise ValueError(f"script prefix evidence disagrees at {address:06X}")
            row = {
                "abs": f"{address:06X}",
                "region": "script",
                "ok": True,
                "prefix_bytes": len(prefix),
                "prefix_hex": prefix.hex(),
                "original_payload_sha256": hashlib.sha256(payload).hexdigest(),
                "source": str(script_csv),
            }
            if address in rows and rows[address] != row:
                raise ValueError(f"ambiguous script prefix evidence at {address:06X}")
            rows[address] = row

    aux_report = _read_object(aux_report_path)
    if aux_report.get("ok") is not True or not isinstance(aux_report.get("applied"), list):
        raise ValueError("aux apply report is not a successful applied-row report")
    for source in aux_report["applied"]:
        if not isinstance(source, Mapping) or source.get("ok") is not True:
            continue
        prefix_bytes = source.get("prefix_bytes")
        prefix_hex = source.get("prefix_hex")
        if not isinstance(prefix_bytes, int) or prefix_bytes <= 0 or not isinstance(prefix_hex, str):
            continue
        address = int(str(source.get("abs")), 16)
        if not any(block.start <= address < block.end_exclusive for block in vetted_blocks):
            continue
        prefix = bytes.fromhex(prefix_hex)
        payload = original_payload(address, max_len=128)
        if payload is None:
            continue
        if len(prefix) != prefix_bytes or not payload.startswith(prefix):
            raise ValueError(f"aux prefix evidence disagrees at {address:06X}")
        row = {
            "abs": f"{address:06X}",
            "region": "aux",
            "ok": True,
            "prefix_bytes": len(prefix),
            "prefix_hex": prefix.hex(),
            "original_payload_sha256": hashlib.sha256(payload).hexdigest(),
            "source": str(aux_report_path),
        }
        if address in rows and rows[address] != row:
            raise ValueError(f"ambiguous prefix evidence at {address:06X}")
        rows[address] = row

    # Third source: re-derive with the worksheet's own parser over every proven
    # Original record, so a record whose body is already Korean keeps its prefix.
    inputs = DiscoveryInputIdentities(
        original_rom=original, working_rom=working, evidence=(blocks,)
    )
    population = OriginalRomProvenRecordEnumerator().enumerate(
        inputs, Tbl.load(table_path)
    )
    parser_added = 0
    parser_agreed = 0
    parser_conflicts: list[dict[str, Any]] = []
    for record in population.localization_records:
        address = record.boundary.start
        payload = original_payload(
            address, max_len=max(record.boundary.payload_capacity, 1)
        )
        if payload is None or len(payload) != record.boundary.payload_capacity:
            continue
        digest = hashlib.sha256(payload).hexdigest()
        if digest != record.original_payload_sha256:
            continue
        prefix, _body, _kind = split_prefix_body(payload)
        if not prefix or not payload.startswith(prefix):
            continue
        existing = rows.get(address)
        if existing is not None:
            if existing["prefix_hex"] == prefix.hex():
                parser_agreed += 1
            else:
                parser_conflicts.append(
                    {
                        "abs": f"{address:06X}",
                        "kept_prefix_hex": existing["prefix_hex"],
                        "kept_source": existing["source"],
                        "parser_prefix_hex": prefix.hex(),
                    }
                )
            continue
        rows[address] = {
            "abs": f"{address:06X}",
            "region": record.region,
            "ok": True,
            "prefix_bytes": len(prefix),
            "prefix_hex": prefix.hex(),
            "original_payload_sha256": digest,
            "source": PARSER_SOURCE,
        }
        parser_added += 1

    applied = [rows[address] for address in sorted(rows)]
    return {
        "schema_version": 1,
        "generated_by": GENERATED_BY,
        "read_only": True,
        "ok": True,
        "original_rom_identity": original.to_json_data(),
        "working_rom_identity": working.to_json_data(),
        "sources": [
            {"path": str(script_csv.resolve()), "sha256": _sha256(script_csv)},
            {"path": str(aux_report_path.resolve()), "sha256": _sha256(aux_report_path)},
            {
                "path": PARSER_SOURCE,
                "derivation": "original_rom_proven_records",
                "note": (
                    "same parser as the worksheet prefix column, applied without "
                    "the worksheet's looks_like_jp selection filter"
                ),
            },
        ],
        "counts": {
            "applied": len(applied),
            "script": sum(row["region"] == "script" for row in applied),
            "aux": sum(row["region"] == "aux" for row in applied),
            "name75": sum(row["region"] == "name75" for row in applied),
            "parser_derived": sum(row["source"] == PARSER_SOURCE for row in applied),
            "parser_agreed_with_existing": parser_agreed,
            "parser_conflicts_dropped": len(parser_conflicts),
        },
        "parser_conflicts": parser_conflicts,
        "applied": applied,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--working", type=Path, required=True)
    parser.add_argument("--blocks", type=Path, required=True)
    parser.add_argument("--script-csv", type=Path, required=True)
    parser.add_argument("--aux-report", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build(
            args.original,
            args.working,
            args.blocks,
            args.script_csv,
            args.aux_report,
            args.table,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.out.with_name(f".{args.out.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(args.out)
    except (OSError, ValueError) as exc:
        print(f"REJECT {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
