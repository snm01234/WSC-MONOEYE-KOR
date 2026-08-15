#!/usr/bin/env python3
"""Fail-closed guard for the 2026-08-09 runtime dialogue regression fixes.

This audit intentionally keeps the newly discovered bank-5F battle path and the
runtime-proven AD=死 records outside the older 5D/5E prefix classifier.  It also
pins the corrected scenario record boundaries so a later 20-cell reflow cannot
move words such as ``게냐`` or ``미안하지만`` into the following record again.
The runtime-proven God Gundam second-line record 5997BF is a permanent direct
lock so a later candidate branched from an older main cannot silently drop it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_dialogue_20cell_candidate import strip_pad, visible_lines
from extract_script import split_prefix_body
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base

SPEC = ROOT / "data/runtime_dialogue_regression_followup_ko.json"
BANK5F_SPEC = ROOT / "data/bank5f_runtime_battle_voice_ko.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
JP_TBL_PATH = ROOT / "data/monoeye_verified.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_TARGET = ROOT / "out/patch/runtime_dialogue_regression_followup_candidate.wsc"
ROM_SIZE = 16_777_216
LINE_LIMIT = 20
BANK5F_PREFIXES = {0xA1, 0x9B, 0x8A}
BANK5F_START = 0x5F00A6
BANK5F_END = 0x5F060E
EXPECTED_BANK5F_ACTIVE = 75
GOD_GUNDAM_5997BF = "내 이 손이 새빨갛게 타오른다！！"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise RuntimeError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def has_japanese(text: str) -> bool:
    return any(("ぁ" <= ch <= "ヿ") or ("一" <= ch <= "龯") for ch in text)


def discover_bank5f_active(original: bytes, dictionary: Dictionary, tbl: Tbl) -> set[str]:
    """Sequential parser: avoids false starts on 00 trail bytes of 2-byte glyphs."""
    sb = stock_base(original)
    address = BANK5F_START
    active: set[str] = set()
    while address <= BANK5F_END:
        got = read_encoded_z_safe(original, sb + address, max_len=256)
        if got is None:
            raise RuntimeError(f"bank5f source walk failed at {address:06X}")
        payload, term = bytes(got[0]), int(got[1])
        if payload:
            body = payload[1:] if payload[0] in BANK5F_PREFIXES else payload
            text = dictionary.expand(body, tbl)
            if text and "不要" not in text:
                active.add(f"{address:06X}")
        address = term - sb + 1
    return active


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    target = args.target.read_bytes()
    if len(target) != ROM_SIZE:
        raise SystemExit(f"target size drifted: {len(target)}")
    original = ORIGINAL.read_bytes()
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    bank5f_spec = json.loads(BANK5F_SPEC.read_text(encoding="utf-8"))
    canonical5f = {str(k).upper(): v for k, v in (bank5f_spec.get("targets") or {}).items()}

    jp_tbl = Tbl.load(JP_TBL_PATH)
    jp_dictionary = Dictionary(original)
    discovered = discover_bank5f_active(original, jp_dictionary, jp_tbl)
    coverage_ok = (
        len(discovered) == EXPECTED_BANK5F_ACTIVE
        and set(canonical5f) == discovered
    )

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(target, ext_meta, ext3_meta)

    targets: list[tuple[str, str, str, str]] = []
    for address, row in (spec.get("scenario_targets") or {}).items():
        targets.append((address.upper(), "scenario", "scenario", str(row["after"])))
    if not any(address == "5997BF" for address, *_rest in targets):
        targets.append(("5997BF", "scenario", "scenario", GOD_GUNDAM_5997BF))
    for address, row in (spec.get("battle_targets") or {}).items():
        targets.append((address.upper(), "battle", str(row["prefix_mode"]), str(row["after"])))
    for address, row in canonical5f.items():
        targets.append((address, "bank5f", "bank5f", str(row["after"])))

    failures: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for address, category, mode, expected in sorted(targets):
        payload, _term = payload_at(target, int(address, 16))
        prefix = b""
        if category == "scenario":
            prefix, body, _kind = split_prefix_body(payload)
            body = bytes(body)
        elif category == "bank5f":
            if payload and payload[0] in BANK5F_PREFIXES:
                prefix, body = payload[:1], payload[1:]
            else:
                body = payload
        elif category == "battle":
            if mode == "visible_full":
                body = payload
            elif mode == "preserve_battle_prefix":
                # Only 5D71AD currently uses this mode; 36 is independently
                # source-bound as its non-visible battle control byte.
                if address == "5D71AD" and payload.startswith(bytes.fromhex("36")):
                    prefix, body = payload[:1], payload[1:]
                else:
                    failures.append({"abs": address, "reasons": ["expected_36_prefix_missing"]})
                    body = payload
            else:
                failures.append({"abs": address, "reasons": [f"unknown_mode:{mode}"]})
                body = payload
        else:
            failures.append({"abs": address, "reasons": [f"unknown_category:{category}"]})
            body = payload

        rendered = strip_pad(dictionary.expand(body, tbl))
        expected_norm = expected.replace(" ", "\u3000")
        line_cells = [len(line) for line in visible_lines(rendered)]
        reasons: list[str] = []
        if rendered != expected_norm:
            reasons.append(f"render_mismatch:{rendered!r} != {expected_norm!r}")
        if line_cells and max(line_cells) > LINE_LIMIT:
            reasons.append(f"over_{LINE_LIMIT}:{line_cells}")
        if category in {"battle", "bank5f"} and has_japanese(rendered):
            reasons.append("japanese_residual")
        if address in {"5EAB36", "5EB6B2", "5EC27C"} and payload.startswith(bytes.fromhex("AD")):
            reasons.append("runtime_visible_AD_reintroduced")
        if reasons:
            failures.append({"abs": address, "reasons": reasons})
        rows.append({
            "abs": address,
            "category": category,
            "prefix_hex": prefix.hex().upper(),
            "rendered": rendered,
            "line_cells": line_cells,
        })

    # Pin the two screenshot-proven cross-record boundaries explicitly.
    by_abs = {row["abs"]: row["rendered"] for row in rows}
    boundary_checks = {
        "60610A": "아아……",
        "618812": "뭘\u3000떠들고\u3000있는\u3000거냐！！",
        "618834": "앗、당신은……！！",
    }
    for address, expected in boundary_checks.items():
        if by_abs.get(address) != expected:
            failures.append({"abs": address, "reasons": ["cross_record_boundary_regressed"]})

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_runtime_dialogue_regression_followup.py",
        "ok": coverage_ok and not failures,
        "target": {"path": str(args.target), "sha256": sha(target), "size": len(target)},
        "counts": {
            "targets": len(rows),
            "scenario": sum(r["category"] == "scenario" for r in rows),
            "battle_5d5e": sum(r["category"] == "battle" for r in rows),
            "bank5f": sum(r["category"] == "bank5f" for r in rows),
            "bank5f_discovered_active": len(discovered),
            "bank5f_canonical": len(canonical5f),
            "failures": len(failures),
        },
        "bank5f_coverage_ok": coverage_ok,
        "bank5f_missing_from_canonical": sorted(discovered - set(canonical5f)),
        "bank5f_stale_canonical": sorted(set(canonical5f) - discovered),
        "failures": failures,
        "rows": rows,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
