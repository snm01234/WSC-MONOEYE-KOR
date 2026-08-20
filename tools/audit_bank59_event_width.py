#!/usr/bin/env python3
"""Current-ROM 20-cell audit for bank 59 runtime text.

This replaces the old snapshot-bound audit.  It does not depend on archived
``aux_text_blocks.json``, ``aux_prefix_rule.json`` or a generated prefixed
sheet.  Instead it:

* derives the bank-59 text extent from the maintained curated prefixed-dialogue
  source in ``data/runtime_text_residual_new_ko_prefixed_dialogue.json``;
* walks the current target ROM directly inside that text extent;
* strips only prefixes recognized by the current script grammar;
* decodes through the current composite ext/ext3 dictionary mapping; and
* audits every record that actually renders Hangul in the target.

The result is therefore a statement about the ROM being tested now, not about a
historical translation/apply snapshot.
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

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from expand_dictionary import _walk_zstring_range  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from mixed_residual_classification import (  # noqa: E402
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base  # noqa: E402

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
ORIGINAL_TBL = ROOT / "data/monoeye.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
CURRENT_PREFIXED_SOURCE = ROOT / "data/runtime_text_residual_new_ko_prefixed_dialogue.json"
PROVEN_PREFIXES = ROOT / "data/bank59_proven_control_prefixes.json"
DEFAULT_OUT = ROOT / "out/patch/bank59_event_width_audit.json"

BANK59_START = 0x590000
CELL_LIMIT = 20
RUNTIME_ALIAS_PAGE_COUNT = 5
RUNTIME_ALIAS_LOCAL_START = 0x0600
RUNTIME_ALIAS_SEG0 = 0x21


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def payload_at(rom: bytes, logical: int) -> bytes | None:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=512)
    return None if got is None else bytes(got[0])


def active_dictionary(rom: bytes) -> Dictionary:
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    base = make_dictionary(rom, ext_meta)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks != 16 or str(ext3_meta.get("exp_seg0") or "11").upper() != "11":
        raise AuditError("current ext3 metadata no longer matches the 16-bank composite runtime")
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


def proven_prefix_map(target: bytes) -> dict[int, bytes]:
    """Load and verify address-specific structural prefixes against Original/current ROMs."""
    doc = json.loads(PROVEN_PREFIXES.read_text(encoding="utf-8"))
    original = ORIGINAL.read_bytes()
    original_tbl = Tbl.load(ORIGINAL_TBL)
    original_dictionary = Dictionary(original)
    result: dict[int, bytes] = {}
    for row in doc.get("records") or []:
        logical = int(str(row["address"]), 16)
        prefix = bytes.fromhex(str(row["prefix_hex"]))
        source = read_encoded_z_safe(original, stock_base(original) + logical, max_len=256)
        current = read_encoded_z_safe(target, stock_base(target) + logical, max_len=256)
        if source is None or current is None:
            raise AuditError(f"proven prefix record unreadable: {logical:06X}")
        source_payload = bytes(source[0])
        current_payload = bytes(current[0])
        if not source_payload.startswith(prefix) or not current_payload.startswith(prefix):
            raise AuditError(f"proven prefix bytes drifted: {logical:06X}")
        source_text = original_dictionary.expand(source_payload, original_tbl)
        if source_text != str(row.get("source_with_literal") or ""):
            raise AuditError(f"proven prefix source text drifted: {logical:06X}")
        result[logical] = prefix
    return result


def current_bank59_text_end(target: bytes) -> int:
    """Return the exact exclusive end of the last maintained bank-59 text record.

    The maintained prefixed-dialogue source covers the tail of the bank-59
    runtime text corpus.  The highest curated address is resolved in the exact
    target ROM and its NUL terminator becomes the scan boundary, so later
    bank-59 binary/name tables are never interpreted as dialogue.
    """
    doc = json.loads(CURRENT_PREFIXED_SOURCE.read_text(encoding="utf-8"))
    addresses: list[int] = []
    for row in doc.get("entries") or []:
        m = re.search(r"(?:^|:)(59[0-9A-Fa-f]{4})$", str(row.get("queue_id") or ""))
        if m:
            addresses.append(int(m.group(1), 16))
    if not addresses:
        raise AuditError("maintained bank59 prefixed-dialogue source has no addresses")
    highest = max(addresses)
    got = read_encoded_z_safe(target, stock_base(target) + highest, max_len=128)
    if got is None:
        raise AuditError(f"cannot resolve final maintained bank59 record: {highest:06X}")
    payload, _term = got
    end = highest + len(payload) + 1
    if not (BANK59_START < end <= 0x5A0000):
        raise AuditError(f"derived bank59 text end is invalid: {end:06X}")
    return end


def scan_bank59_current(
    target: bytes,
    tbl: Tbl | None = None,
    *,
    include_japanese_only: bool = False,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Scan current runtime text directly; return (rows, unreadable, end).

    By default only records rendering Hangul are width-audited.  Set
    ``include_japanese_only`` for the current untranslated-text audit.
    """
    tbl = tbl or Tbl.load(TBL)
    dictionary = active_dictionary(target)
    proven = proven_prefix_map(target)
    end = current_bank59_text_end(target)
    rows: list[dict[str, Any]] = []
    unreadable: list[str] = []

    for logical, payload, _kind in _walk_zstring_range(
        target,
        BANK59_START,
        end,
        region="aux",
        max_len=128,
    ):
        if not (BANK59_START <= logical < end):
            continue
        if logical in proven:
            prefix = proven[logical]
            if not payload.startswith(prefix):
                unreadable.append(f"{logical:06X}:proven_prefix_drift")
                continue
            body = payload[len(prefix):]
            prefix_kind = "address_proven_control_prefix"
        else:
            prefix, body, prefix_kind = split_prefix_body(payload)
        try:
            text = strip_pad(dictionary.expand(body, tbl))
        except Exception:  # noqa: BLE001
            unreadable.append(f"{logical:06X}:decode")
            continue
        hangul = hangul_character_count(text)
        japanese = japanese_character_count(text)
        if hangul <= 0 and not (include_japanese_only and japanese > 0):
            continue
        rows.append(
            {
                "address": f"{logical:06X}",
                "scope": "bank59_current_runtime_text",
                "route": "bank59_direct_scan",
                "prefix_kind": prefix_kind,
                "prefix_hex": prefix.hex().upper(),
                "text": text,
                "cells": len(text),
                "over_20": len(text) > CELL_LIMIT,
                "hangul_chars": hangul,
                "japanese_chars": japanese,
                "mixed_language": bool(hangul and japanese),
                "payload_hex": payload.hex().upper(),
            }
        )
    return rows, unreadable, end


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    target = args.target.read_bytes()
    tbl = Tbl.load(TBL)
    rows, unreadable, end = scan_bank59_current(target, tbl)
    offenders = [row for row in rows if row["over_20"]]
    mixed = [row for row in rows if row["mixed_language"]]

    report = {
        "schema_version": 2,
        "generated_by": "tools/audit_bank59_event_width.py",
        "status": "pass" if not offenders and not unreadable else "fail",
        "target": {"path": str(args.target), "size": len(target), "sha256": sha(target)},
        "scope": {
            "bank": "59",
            "start": f"{BANK59_START:06X}",
            "end_exclusive": f"{end:06X}",
            "cell_limit": CELL_LIMIT,
            "inventory": "current target ROM direct scan",
            "extent_source": str(CURRENT_PREFIXED_SOURCE.relative_to(ROOT)).replace("\\", "/"),
            "historical_generated_inputs": [],
            "address_proven_prefix_ledger": str(PROVEN_PREFIXES.relative_to(ROOT)).replace("\\", "/"),
        },
        "counts": {
            "records_checked": len(rows),
            "over_20": len(offenders),
            "mixed_language": len(mixed),
            "unreadable": len(unreadable),
        },
        "offenders": offenders,
        "mixed_language_records": mixed,
        "unreadable": unreadable,
        "rows": rows,
        "policy": "read-only current-ROM audit; no archived aux snapshot is an input",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "target_sha256": report["target"]["sha256"],
        "scope_end_exclusive": report["scope"]["end_exclusive"],
        "counts": report["counts"],
        "offenders": [
            {"address": row["address"], "cells": row["cells"], "text": row["text"]}
            for row in offenders
        ],
        "report": str(args.out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
