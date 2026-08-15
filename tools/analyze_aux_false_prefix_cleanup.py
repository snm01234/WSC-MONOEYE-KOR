#!/usr/bin/env python3
"""Build the reviewed 5D/5E false-prefix cleanup specification.

The old mixed-residual writer treated the first encoded code unit of every
bank-5D/5E record as a control field.  Runtime evidence proved that this is only
true for some records: a real sentence-initial glyph can be preserved in front
of the Korean body and become visible (for example ``う우와…``).

This read-only analysis separates three populations:

* already proven control-prefixed records from ``aux_ko_report.json``;
* locally dominant one-byte IDs (at least three uses in one coherent block),
  which are the speaker/control field measured by the original proof;
* rare/non-dominant leads.  Every rare case was reviewed as Japanese text.  A
  small explicit set whose body is the coherent sentence remains control data;
  every other rare lead is a false prefix and becomes a cleanup target.

Only records whose *current* lead still renders Japanese and whose remainder is
complete Korean are eligible.  This deliberately excludes ordinary untranslated
body residuals such as ``레일라のために``.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import _walk_zstring_range  # noqa: E402
from measure_aux_prefix_rule import code_units  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
BLOCKS = ROOT / "out/script/aux_text_blocks.json"
AUX_REPORT = ROOT / "out/patch/aux_ko_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_SPEC = ROOT / "data/aux_false_prefix_cleanup_ko.json"
OUT_REPORT = ROOT / "out/patch/aux_false_prefix_cleanup_analysis.json"

EXPECTED_PARENT_SHA256 = "a47569820eed19ab0028b432dabf840bb35f9689cf403e63ed2af71f8431cf9a"
EXPECTED_PREFIX_SHAPED = 2218
EXPECTED_RARE_REVIEWED = 349
EXPECTED_MANUAL_CONTROLS = 41
EXPECTED_TARGETS = 308

# Rare leads that were manually confirmed as control/speaker IDs.  In each
# record, deleting the listed lead yields the coherent Japanese sentence that
# the Korean body translates.  Several IDs repeat as a two-record alternate
# speaker inside a block (91/95/98/99); others are single alternate IDs.
MANUAL_CONTROL_ABS = {
    0x5D27BD,
    0x5D3EC5,
    0x5D440E,
    0x5D444F,
    0x5D445C,
    0x5D494C,
    0x5D4D0D,
    0x5D4D21,
    0x5D5007,
    0x5D501B,
    0x5D56C0,
    0x5D56EF,
    0x5D66CD,
    0x5D7259,
    0x5D81DF,
    0x5D9201,
    0x5D94DC,
    0x5DA7AB,
    0x5DA7BA,
    0x5DAE61,
    0x5E0AC8,
    0x5E1952,
    0x5E1972,
    0x5E1C0A,
    0x5E1C18,
    0x5E25E2,
    0x5E32CE,
    0x5E32F6,
    0x5E335B,
    0x5E4176,
    0x5E9CE1,
    0x5E9CED,
    0x5EA8F3,
    0x5EA902,
    0x5EAF99,
    0x5EAFA5,
    0x5EB466,
    0x5EB979,
    0x5EBC53,
    0x5EBC63,
    0x5EBFF6,
}


class AnalysisError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def has_korean(text: str) -> bool:
    return any("\uac00" <= ch <= "\ud7a3" for ch in text)


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff"
        for ch in text
    )


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    parent = bytes(load_rom(PARENT))
    parent_sha = sha256(parent)
    if parent_sha != EXPECTED_PARENT_SHA256:
        raise AnalysisError(
            f"parent TIP identity drifted: expected {EXPECTED_PARENT_SHA256}, got {parent_sha}"
        )

    tbl = Tbl.load(TBL_PATH)
    original_dictionary = Dictionary(original)
    current_dictionary = make_dictionary_ext3(
        parent,
        load_ext_meta(EXT_META_PATH),
        load_ext_meta(EXT3_META_PATH),
    )
    sb = stock_base(parent)
    blocks = json.loads(BLOCKS.read_text(encoding="utf-8"))["blocks"]
    aux_report = json.loads(AUX_REPORT.read_text(encoding="utf-8"))
    proven_controls = {
        int(row["abs"], 16)
        for row in aux_report.get("applied") or []
        if row.get("bank") in ("5D", "5E") and int(row.get("prefix_bytes") or 0) > 0
    }

    shaped: list[dict[str, Any]] = []
    seen: set[int] = set()
    for block in blocks:
        lo, hi = int(block["start"], 16), int(block["end"], 16)
        if lo >> 16 not in (0x5D, 0x5E):
            continue
        original_rows: list[tuple[int, bytes, bytes]] = []
        for logical, payload, _kind in _walk_zstring_range(
            original, lo, hi + 1, region="aux", max_len=256
        ):
            if logical in seen or not payload:
                continue
            seen.add(logical)
            units = code_units(payload)
            if not units:
                continue
            lead = payload[: units[0][1]]
            original_rows.append((logical, lead, bytes(payload)))
        lead_counts = Counter(lead for _logical, lead, _payload in original_rows)

        for logical, lead, original_payload in original_rows:
            got = read_encoded_z_safe(parent, sb + logical, max_len=256)
            if got is None:
                continue
            current_payload, terminator = bytes(got[0]), int(got[1])
            if len(current_payload) < len(lead) or not current_payload.startswith(lead):
                continue
            lead_text = current_dictionary.expand(current_payload[: len(lead)], tbl)
            body_payload = current_payload[len(lead) :]
            body_text = strip_pad(current_dictionary.expand(body_payload, tbl))
            if not (
                has_japanese(lead_text)
                and has_korean(body_text)
                and not has_japanese(body_text)
            ):
                continue
            shaped.append(
                {
                    "logical": logical,
                    "abs": f"{logical:06X}",
                    "bank": f"{logical >> 16:02X}",
                    "block_start": f"{lo:06X}",
                    "block_end": f"{hi:06X}",
                    "original_payload": original_payload,
                    "original_payload_hex": original_payload.hex().upper(),
                    "original_text": original_dictionary.expand(original_payload, tbl),
                    "lead": lead,
                    "lead_hex": lead.hex().upper(),
                    "lead_len": len(lead),
                    "lead_text": lead_text,
                    "lead_frequency_in_block": lead_counts[lead],
                    "current_payload": current_payload,
                    "current_payload_hex": current_payload.hex().upper(),
                    "current_text": strip_pad(current_dictionary.expand(current_payload, tbl)),
                    "body_payload": body_payload,
                    "body_payload_hex": body_payload.hex().upper(),
                    "body_text": body_text,
                    "terminator": terminator,
                    "terminator_file": f"{terminator:06X}",
                    "proven_control": logical in proven_controls,
                    "dominant_one_byte_control": len(lead) == 1 and lead_counts[lead] >= 3,
                }
            )

    if len(shaped) != EXPECTED_PREFIX_SHAPED:
        raise AnalysisError(
            f"prefix-shaped population drifted: {len(shaped)} != {EXPECTED_PREFIX_SHAPED}"
        )

    rare = [
        row
        for row in shaped
        if not row["proven_control"] and not row["dominant_one_byte_control"]
    ]
    if len(rare) != EXPECTED_RARE_REVIEWED:
        raise AnalysisError(
            f"rare reviewed population drifted: {len(rare)} != {EXPECTED_RARE_REVIEWED}"
        )
    rare_abs = {row["logical"] for row in rare}
    missing_manual = sorted(MANUAL_CONTROL_ABS - rare_abs)
    if missing_manual:
        raise AnalysisError(
            "manual-control address no longer belongs to rare population: "
            + ", ".join(f"{value:06X}" for value in missing_manual)
        )
    if len(MANUAL_CONTROL_ABS) != EXPECTED_MANUAL_CONTROLS:
        raise AnalysisError("manual-control set count drifted")

    controls: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for row in sorted(rare, key=lambda item: item["logical"]):
        if row["logical"] in MANUAL_CONTROL_ABS:
            controls.append(
                {
                    "abs": row["abs"],
                    "block_start": row["block_start"],
                    "lead_hex": row["lead_hex"],
                    "lead_text": row["lead_text"],
                    "lead_frequency_in_block": row["lead_frequency_in_block"],
                    "original_text": row["original_text"],
                    "body_text": row["body_text"],
                    "current_payload_hex": row["current_payload_hex"],
                    "reason": "manual_review_control_id_body_is_coherent_sentence",
                }
            )
            continue

        after_payload = row["body_payload"] + b"\x01" * row["lead_len"]
        if len(after_payload) != len(row["current_payload"]):
            raise AnalysisError(f"length calculation failed at {row['abs']}")
        after_text = strip_pad(current_dictionary.expand(after_payload, tbl))
        if after_text != row["body_text"] or has_japanese(after_text):
            raise AnalysisError(
                f"post-shift static render failed at {row['abs']}: {after_text!r}"
            )
        targets.append(
            {
                "abs": row["abs"],
                "bank": row["bank"],
                "block_start": row["block_start"],
                "block_end": row["block_end"],
                "lead_len": row["lead_len"],
                "lead_hex": row["lead_hex"],
                "lead_text": row["lead_text"],
                "lead_frequency_in_block": row["lead_frequency_in_block"],
                "original_payload_hex": row["original_payload_hex"],
                "original_text": row["original_text"],
                "expected_before_hex": row["current_payload_hex"],
                "expected_before_text": row["current_text"],
                "body_hex": row["body_payload_hex"],
                "ko": row["body_text"],
                "after_hex": after_payload.hex().upper(),
                "payload_len": len(row["current_payload"]),
                "terminator_file": row["terminator_file"],
                "classification": "manual_review_text_initial_false_prefix",
            }
        )

    if len(controls) != EXPECTED_MANUAL_CONTROLS:
        raise AnalysisError(
            f"manual-control output count drifted: {len(controls)} != {EXPECTED_MANUAL_CONTROLS}"
        )
    if len(targets) != EXPECTED_TARGETS:
        raise AnalysisError(
            f"cleanup target count drifted: {len(targets)} != {EXPECTED_TARGETS}"
        )

    already_fixed = []
    for logical in (0x5EBD90,):
        got = read_encoded_z_safe(parent, sb + logical, max_len=64)
        if got is None:
            raise AnalysisError(f"already-fixed record unreadable: {logical:06X}")
        payload = bytes(got[0])
        rendered = strip_pad(current_dictionary.expand(payload, tbl))
        already_fixed.append(
            {
                "abs": f"{logical:06X}",
                "payload_hex": payload.hex().upper(),
                "rendered": rendered,
                "japanese_residual": has_japanese(rendered),
            }
        )
        if has_japanese(rendered):
            raise AnalysisError(f"already-fixed record regressed: {logical:06X}")

    spec = {
        "schema_version": 1,
        "generated_by": "tools/analyze_aux_false_prefix_cleanup.py",
        "description": "Reviewed false one-code-unit prefix cleanup for bank 5D/5E battle dialogue",
        "parent_sha256": parent_sha,
        "classification_contract": {
            "eligible_shape": "current lead renders Japanese; remainder is Korean and contains no Japanese",
            "proven_controls_preserved": len(
                [row for row in shaped if row["proven_control"]]
            ),
            "unproven_dominant_controls_preserved": len(
                [
                    row
                    for row in shaped
                    if not row["proven_control"] and row["dominant_one_byte_control"]
                ]
            ),
            "rare_records_reviewed": len(rare),
            "manual_control_exclusions": len(controls),
            "false_prefix_targets": len(targets),
        },
        "manual_control_exclusions": controls,
        "already_fixed": already_fixed,
        "targets": targets,
    }
    OUT_SPEC.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_aux_false_prefix_cleanup.py",
        "ok": True,
        "original": {
            "path": str(ORIGINAL.relative_to(ROOT)).replace("\\", "/"),
            "size": len(original),
            "sha256": sha256(original),
        },
        "parent": {
            "path": str(PARENT.relative_to(ROOT)).replace("\\", "/"),
            "size": len(parent),
            "sha256": parent_sha,
        },
        "counts": {
            "prefix_shaped": len(shaped),
            "proven_controls": len([row for row in shaped if row["proven_control"]]),
            "unproven_dominant_controls": len(
                [
                    row
                    for row in shaped
                    if not row["proven_control"] and row["dominant_one_byte_control"]
                ]
            ),
            "rare_reviewed": len(rare),
            "manual_control_exclusions": len(controls),
            "targets": len(targets),
        },
        "target_by_bank": dict(Counter(row["bank"] for row in targets)),
        "target_by_lead_len": dict(Counter(str(row["lead_len"]) for row in targets)),
        "target_samples": targets[:30],
        "control_samples": controls[:30],
        "spec": str(OUT_SPEC.relative_to(ROOT)).replace("\\", "/"),
    }
    OUT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(f"target_by_bank: {report['target_by_bank']}")
    print(f"target_by_lead_len: {report['target_by_lead_len']}")
    print(f"spec: {report['spec']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
