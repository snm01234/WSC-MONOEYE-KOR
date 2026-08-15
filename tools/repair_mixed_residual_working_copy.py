#!/usr/bin/env python3
"""Prepare and gate-publish the task-3.2 COPY-only structural repair.

This tool never modifies the source Working ROM or Original ROM. ``prepare``
creates only a temporary copy. ``publish`` atomically promotes that copy only
when every required read-only gate report passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402
from verify_all_stages_smoke import make_smoke_dictionary  # noqa: E402

EXPECTED_ORIGINAL_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXPECTED_SOURCE_SHA256 = "eb2126999933ed55d22fd09207556e718fc64b38cfc65f9d5859584fedb7080c"

PROVEN_REPAIRS = {
    0x61E5AA: {
        "terminator": 0x61E5AF,
        "next_record_start": 0x61E5B0,
        "before_byte": 0x08,
        "payload_hex": "173418f593",
        "rendered": "캬라",
    },
    0x61EDEB: {
        "terminator": 0x61EDF0,
        "next_record_start": 0x61EDF1,
        "before_byte": 0x08,
        "payload_hex": "173418fb0a",
        "rendered": "だったら",
    },
    0x63CFAE: {
        "terminator": 0x63CFBA,
        "next_record_start": 0x63CFBB,
        "before_byte": 0x01,
        "payload_hex": "18e518d18701010101010101",
        "rendered": "네오지온의　다카르　침공은　　　　　　　",
    },
}

INHERITED_START = 0x62B86E
INHERITED_EARLY_TERMINATOR = 0x62B874
INHERITED_ORIGINAL_TERMINATOR = 0x62B87C
INHERITED_NEXT_RECORD = 0x62B87D
INHERITED_STALE_BYTES = bytes.fromhex("000c3afd7f6ef044")
PADDING_BYTE = 0x01


def _sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"report root is not an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _trim_padding(text: str) -> str:
    return text.rstrip(" \u3000")


def _assert_distinct(*paths: Path) -> None:
    resolved = [path.resolve() for path in paths]
    if len(resolved) != len(set(resolved)):
        raise ValueError("source, Original, temporary, and published paths must be distinct")


def prepare(source: Path, original: Path, temporary: Path, report_path: Path) -> dict[str, Any]:
    _assert_distinct(source, original, temporary)
    if temporary.suffix.lower() != ".wsc" or ".tmp" not in temporary.name:
        raise ValueError("temporary output must be a .tmp.wsc path")

    source_bytes = source.read_bytes()
    original_bytes = original.read_bytes()
    if _sha256(source_bytes) != EXPECTED_SOURCE_SHA256:
        raise ValueError("source Working ROM identity does not match the authorized copy source")
    if _sha256(original_bytes) != EXPECTED_ORIGINAL_SHA256:
        raise ValueError("Original ROM identity does not match the authorized baseline")

    scratch = bytearray(source_bytes)
    before = bytes(source_bytes)
    sb = stock_base(scratch)
    original_sb = stock_base(original_bytes)
    tbl = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")
    dictionary = make_smoke_dictionary(scratch)

    changes: dict[int, tuple[int, int]] = {}
    for row in PROVEN_REPAIRS.values():
        terminator = int(row["terminator"])
        changes[terminator] = (int(row["before_byte"]), 0x00)

    stale = bytes(scratch[sb + INHERITED_EARLY_TERMINATOR : sb + INHERITED_ORIGINAL_TERMINATOR])
    if stale != INHERITED_STALE_BYTES:
        raise ValueError(
            f"62B86E stale extent drifted: expected {INHERITED_STALE_BYTES.hex()}, got {stale.hex()}"
        )
    for offset, old in enumerate(INHERITED_STALE_BYTES):
        changes[INHERITED_EARLY_TERMINATOR + offset] = (old, PADDING_BYTE)

    for logical, (old, new) in sorted(changes.items()):
        actual = scratch[sb + logical]
        if actual != old:
            raise ValueError(
                f"authorized byte {logical:06X} drifted: expected {old:02X}, got {actual:02X}"
            )
        scratch[sb + logical] = new

    physical_diff = [
        index for index, (old, new) in enumerate(zip(before, scratch)) if old != new
    ]
    expected_diff = sorted(sb + logical for logical in changes)
    if physical_diff != expected_diff:
        raise ValueError("scratch copy contains changes outside the authorized record extents")

    records: list[dict[str, Any]] = []
    for start, row in sorted(PROVEN_REPAIRS.items()):
        term = int(row["terminator"])
        next_start = int(row["next_record_start"])
        got = read_encoded_z_safe(scratch, sb + start, max_len=128)
        if got is None or got[1] - sb != term or got[0].hex() != row["payload_hex"]:
            raise ValueError(f"proven repair verification failed at {start:06X}")
        prefix, body, kind = split_prefix_body(got[0])
        rendered = dictionary.expand(body, tbl) if body else ""
        if rendered != row["rendered"]:
            raise ValueError(f"proven repair rendering drifted at {start:06X}")
        if bytes(before[sb + next_start : sb + next_start + 16]) != bytes(
            scratch[sb + next_start : sb + next_start + 16]
        ):
            raise ValueError(f"next record boundary changed at {next_start:06X}")
        records.append(
            {
                "record_start": f"{start:06X}",
                "prefix_hex": prefix.hex(),
                "kind": kind,
                "original_terminator": f"{term:06X}",
                "after_terminator": f"{got[1] - sb:06X}",
                "next_record_start": f"{next_start:06X}",
                "rendered_after": rendered,
                "rendered_preserved": True,
                "next_boundary_preserved": True,
            }
        )

    original_record = read_encoded_z_safe(
        original_bytes, original_sb + INHERITED_START, max_len=128
    )
    before_record = read_encoded_z_safe(before, sb + INHERITED_START, max_len=128)
    after_record = read_encoded_z_safe(scratch, sb + INHERITED_START, max_len=128)
    if original_record is None or original_record[1] - original_sb != INHERITED_ORIGINAL_TERMINATOR:
        raise ValueError("Original-derived 62B86E terminator is not 62B87C")
    if before_record is None or before_record[1] - sb != INHERITED_EARLY_TERMINATOR:
        raise ValueError("source Working ROM no longer has the inherited early terminator")
    if after_record is None or after_record[1] - sb != INHERITED_ORIGINAL_TERMINATOR:
        raise ValueError("62B86E repair did not restore the Original terminator")

    before_prefix, before_body, before_kind = split_prefix_body(before_record[0])
    after_prefix, after_body, after_kind = split_prefix_body(after_record[0])
    before_text = dictionary.expand(before_body, tbl)
    after_text = dictionary.expand(after_body, tbl)
    if before_prefix != after_prefix or before_prefix != b"\x18":
        raise ValueError("62B86E prefix was not preserved")
    if before_kind != after_kind or before_kind != "dialogue":
        raise ValueError("62B86E record kind changed")
    if _trim_padding(before_text) != _trim_padding(after_text):
        raise ValueError("62B86E rendered text changed beyond allowed trailing padding")
    if bytes(before[sb + INHERITED_NEXT_RECORD : sb + INHERITED_NEXT_RECORD + 16]) != bytes(
        scratch[sb + INHERITED_NEXT_RECORD : sb + INHERITED_NEXT_RECORD + 16]
    ):
        raise ValueError("62B86E next boundary bytes changed")
    if 0x00 in scratch[
        sb + INHERITED_START : sb + INHERITED_ORIGINAL_TERMINATOR
    ]:
        raise ValueError("62B86E repaired occupied extent contains an interior NUL")

    records.append(
        {
            "record_start": f"{INHERITED_START:06X}",
            "prefix_hex": after_prefix.hex(),
            "kind": after_kind,
            "before_terminator": f"{INHERITED_EARLY_TERMINATOR:06X}",
            "original_terminator": f"{INHERITED_ORIGINAL_TERMINATOR:06X}",
            "after_terminator": f"{after_record[1] - sb:06X}",
            "next_record_start": f"{INHERITED_NEXT_RECORD:06X}",
            "rendered_before": before_text,
            "rendered_after": after_text,
            "rendered_trimmed_preserved": True,
            "padding_hex": bytes(
                scratch[
                    sb + INHERITED_EARLY_TERMINATOR : sb + INHERITED_ORIGINAL_TERMINATOR
                ]
            ).hex(),
            "next_boundary_preserved": True,
        }
    )

    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(scratch)
    report: dict[str, Any] = {
        "ok": True,
        "published": False,
        "status": "prepared_temporary_copy",
        "source_working": {
            "path": str(source.resolve()),
            "size": len(source_bytes),
            "sha256": _sha256(source_bytes),
        },
        "original": {
            "path": str(original.resolve()),
            "size": len(original_bytes),
            "sha256": _sha256(original_bytes),
        },
        "temporary_candidate": {
            "path": str(temporary.resolve()),
            "size": len(scratch),
            "sha256": _sha256(scratch),
        },
        "changed_bytes": len(physical_diff),
        "logical_diff_addresses": [f"{index - sb:06X}" for index in physical_diff],
        "all_unrelated_bytes_preserved": True,
        "records": records,
    }
    _write_json(report_path, report)
    return report


def _gate_summary(
    structure: Mapping[str, Any],
    nondialogue: Mapping[str, Any],
    stock: Mapping[str, Any],
    false_segptr: Mapping[str, Any],
    smoke: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    stock_targets = stock.get("targets")
    stock_target = stock_targets[0] if isinstance(stock_targets, list) and len(stock_targets) == 1 else {}
    stock_counts = stock_target.get("counts", {}) if isinstance(stock_target, Mapping) else {}
    out_of_band = (
        stock_target.get("out_of_band_dialogue_writes", {})
        if isinstance(stock_target, Mapping)
        else {}
    )
    length_check = nondialogue.get("check_iii_length_terminator", {})
    summary = {
        "full_structure": {
            "ok": structure.get("ok") is True and structure.get("issues") == 0,
            "issues": structure.get("issues"),
        },
        "nondialogue": {
            "ok": nondialogue.get("ok") is True
            and isinstance(length_check, Mapping)
            and length_check.get("violations") == 0,
            "violations": length_check.get("violations") if isinstance(length_check, Mapping) else None,
        },
        "stock_noninvasion": {
            "ok": stock.get("ok") is True
            and isinstance(stock_target, Mapping)
            and stock_target.get("ok") is True
            and isinstance(stock_counts, Mapping)
            and stock_counts.get("unintended_runs") == 0
            and stock_counts.get("unintended_bytes") == 0
            and isinstance(out_of_band, Mapping)
            and out_of_band.get("runs") == 0
            and out_of_band.get("bytes") == 0,
            "unintended_runs": stock_counts.get("unintended_runs") if isinstance(stock_counts, Mapping) else None,
            "unintended_bytes": stock_counts.get("unintended_bytes") if isinstance(stock_counts, Mapping) else None,
            "out_of_band_runs": out_of_band.get("runs") if isinstance(out_of_band, Mapping) else None,
            "out_of_band_bytes": out_of_band.get("bytes") if isinstance(out_of_band, Mapping) else None,
        },
        "false_segment_pointer": {
            "ok": false_segptr.get("ok") is True and false_segptr.get("sites_found") == 0,
            "sites_found": false_segptr.get("sites_found"),
        },
        "smoke": {"ok": smoke.get("overall_ok") is True},
    }
    return all(item["ok"] is True for item in summary.values()), summary


def publish(
    temporary: Path,
    output: Path,
    preflight_path: Path,
    report_path: Path,
    gate_paths: Mapping[str, Path],
) -> dict[str, Any]:
    _assert_distinct(temporary, output)
    preflight = dict(_load_json(preflight_path))
    expected = preflight.get("temporary_candidate", {})
    if not temporary.is_file():
        raise ValueError("temporary candidate is missing")
    temporary_bytes = temporary.read_bytes()
    if not isinstance(expected, Mapping) or _sha256(temporary_bytes) != expected.get("sha256"):
        raise ValueError("temporary candidate identity drifted after preflight")

    reports = {name: _load_json(path) for name, path in gate_paths.items()}
    gates_ok, summary = _gate_summary(
        reports["structure"],
        reports["nondialogue"],
        reports["stock"],
        reports["false_segptr"],
        reports["smoke"],
    )
    final = dict(preflight)
    final["validation"] = {
        name: {**summary[name], "report": str(gate_paths[key])}
        for name, key in (
            ("full_structure", "structure"),
            ("nondialogue", "nondialogue"),
            ("stock_noninvasion", "stock"),
            ("false_segment_pointer", "false_segptr"),
            ("smoke", "smoke"),
        )
    }
    if not gates_ok:
        temporary.unlink(missing_ok=True)
        final.update(
            {
                "ok": False,
                "published": False,
                "status": "rejected_gate_failure",
            }
        )
        _write_json(report_path, final)
        return final

    output.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, output)
    published_bytes = output.read_bytes()
    final.update(
        {
            "ok": True,
            "published": True,
            "status": "published_all_gates_passed",
            "candidate_working_copy": {
                "path": str(output.resolve()),
                "size": len(published_bytes),
                "sha256": _sha256(published_bytes),
            },
        }
    )
    _write_json(report_path, final)
    return final


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--source", type=Path, required=True)
    prepare_parser.add_argument("--original", type=Path, required=True)
    prepare_parser.add_argument("--temporary", type=Path, required=True)
    prepare_parser.add_argument("--report", type=Path, required=True)

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--temporary", type=Path, required=True)
    publish_parser.add_argument("--output", type=Path, required=True)
    publish_parser.add_argument("--preflight", type=Path, required=True)
    publish_parser.add_argument("--structure", type=Path, required=True)
    publish_parser.add_argument("--nondialogue", type=Path, required=True)
    publish_parser.add_argument("--stock", type=Path, required=True)
    publish_parser.add_argument("--false-segptr", type=Path, required=True)
    publish_parser.add_argument("--smoke", type=Path, required=True)
    publish_parser.add_argument("--report", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.source, args.original, args.temporary, args.report)
        else:
            result = publish(
                args.temporary,
                args.output,
                args.preflight,
                args.report,
                {
                    "structure": args.structure,
                    "nondialogue": args.nondialogue,
                    "stock": args.stock,
                    "false_segptr": args.false_segptr,
                    "smoke": args.smoke,
                },
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"REJECT {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
