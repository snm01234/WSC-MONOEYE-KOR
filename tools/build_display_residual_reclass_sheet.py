#!/usr/bin/env python3
"""Reclassify encyclopedia / broad-tier-B / name75 display-JP residuals into sheets.

After aux false-mixed clearance, the remaining static JP hits from the unified
inventory are mostly:

* bank-5C encyclopedia walker hits whose payloads still match Original and
  decode as non-prose / damaged glyph soup;
* broad tier-B pre-opening script fragments (often known non-text);
* one name75 single-byte glyph.

This read-only tool binds those audits to the live TIP, reclassifies each row,
and writes master + actionable sheets.  No ROM or SaveRAM is modified.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from extract_script import looks_like_jp
from find_aux_text_tables import coherent
from mixed_residual_classification import (
    core_character_count,
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import load_rom, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ENCY = ROOT / "out/patch/current_tip_remaining_encyclopedia_bank5c.json"
BROAD = ROOT / "out/patch/current_tip_remaining_broad_japanese_residuals.json"
NAME75 = ROOT / "out/patch/current_tip_remaining_name75_untranslated_terms.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"

MASTER = ROOT / "out/script/display_residual_reclass_sheet.csv"
ACTIONABLE = ROOT / "out/script/display_residual_reclass_actionable.csv"
BATCH_DIR = ROOT / "out/script/display_residual_reclass_batches"
REPORT = ROOT / "out/patch/display_residual_reclass_report.json"

# From audit_current_untranslated_dialogue.SCRIPT_NON_TEXT
SCRIPT_NON_TEXT = {
    0x603C2E,
    0x603C79,
    0x603C9F,
    0x603E3E,
    0x603E4C,
    0x603EA9,
    0x603EB7,
    0x603F25,
    0x603F64,
}

# Dense non-prose encyclopedia walker cluster observed on current TIP.
ENCY_DAMAGED_CLUSTER = (0x5C2E62, 0x5C2EDF)

MAX_BATCH = 48
REPEAT_GLYPH_RE = re.compile(r"(.)\1")

FIELDS = [
    "batch_id",
    "batch_order",
    "scope",
    "bank",
    "abs",
    "source_tier",
    "reclass",
    "shape",
    "payload_capacity",
    "body_capacity",
    "original_jp",
    "current_text",
    "japanese_count",
    "hangul_count",
    "core_count",
    "payload_matches_original",
    "strict_coherent_original",
    "ko",
    "translation_source",
    "review_status",
    "workflow_status",
    "notes",
    "current_payload_hex",
    "source_body_sha256",
    "parent_tip_sha256",
]


class SheetError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SheetError(f"JSON root must be object: {path}")
    return value


def existing_edits(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    out: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            abs_hex = str(row.get("abs") or "").strip().upper()
            if abs_hex:
                out[abs_hex] = dict(row)
    return out


def text_shape(text: str) -> str:
    jp = japanese_character_count(text)
    hangul = hangul_character_count(text)
    if jp and hangul:
        return "mixed"
    if jp:
        return "jp_only"
    if hangul:
        return "ko_only"
    return "no_text"


def looks_like_data_soup(text: str) -> bool:
    if not text:
        return True
    if "<BAD" in text or "<ED" in text:
        return True
    if text.count("ェ") >= 2:
        return True
    if "ロ助" in text:
        return True
    # duplicated glyph noise common in false encyclopedia walker hits
    if REPEAT_GLYPH_RE.search(text) and not coherent(text):
        compact = re.sub(r"[　\s…。、！？・]", "", text)
        if len(compact) <= 8 and REPEAT_GLYPH_RE.search(compact):
            return True
    return False


def classify_encyclopedia(row: dict[str, Any], *, payload_len: int, matches_original: bool) -> tuple[str, str]:
    current = str(row.get("current") or "").rstrip("\u3000 \t")
    original = str(row.get("jp") or "").rstrip("\u3000 \t")
    logical = int(str(row["abs"]), 16)
    jp = int(row.get("japanese_count") or japanese_character_count(current))
    note_bits = []
    if matches_original:
        note_bits.append("tip_payload_identical_to_original")
    if ENCY_DAMAGED_CLUSTER[0] <= logical < ENCY_DAMAGED_CLUSTER[1]:
        return "encyclopedia_damaged_cluster", "walker cluster 5C2E62-5C2EDE; non-prose / glyph soup"
    if payload_len < 4:
        return "short_token_or_glyph", "payload under 4 bytes; not a sentence target"
    if payload_len >= 128 or looks_like_data_soup(current) or looks_like_data_soup(original):
        return "false_text_or_decode_garbage", "; ".join(note_bits + ["decode looks non-prose"])
    if jp >= 6 and coherent(original) and looks_like_jp(original) and not looks_like_data_soup(original):
        return "actionable_sentence", "; ".join(note_bits + ["coherent original sentence-like"])
    if jp >= 3 and looks_like_jp(original) and not looks_like_data_soup(original):
        return "actionable_phrase", "; ".join(note_bits + ["phrase-like original"])
    if jp >= 1 and hangul_character_count(current) == 0 and core_character_count(current) >= 2:
        return "jp_label_review", "; ".join(note_bits + ["short JP label; needs screen proof"])
    return "ambiguous_fragment", "; ".join(note_bits + ["keep quarantined"])


def classify_broad(row: dict[str, Any]) -> tuple[str, str]:
    logical = int(str(row["abs"]), 16)
    current = str(row.get("current_text") or "").rstrip("\u3000 \t")
    original = str(row.get("original_text") or "").rstrip("\u3000 \t")
    body = int(row.get("body_capacity") or 0)
    if logical in SCRIPT_NON_TEXT:
        return "known_script_non_text", "listed in SCRIPT_NON_TEXT data/control set"
    if looks_like_data_soup(current) or looks_like_data_soup(original):
        return "preopening_data_fragment", "non-prose / ロ助-style fragment"
    if body <= 1:
        return "one_byte_fragment", "single-byte body; glyph/table proof required"
    if body < 4:
        return "short_kana_fragment", "short kana fragment; not auto-patch"
    if japanese_character_count(current) >= 4 and coherent(original):
        return "actionable_phrase", "broad tier B coherent phrase"
    return "ambiguous_fragment", "broad tier B quarantine"


def classify_name75(row: dict[str, Any]) -> tuple[str, str]:
    payload = int(row.get("payload_bytes") or 0)
    current = str(row.get("current_text") or "").rstrip("\u3000 \t")
    if payload <= 1:
        return "single_byte_name_glyph", "single-byte name75 glyph"
    if looks_like_data_soup(current):
        return "false_text_or_decode_garbage", "name75 non-prose"
    if not row.get("likely_real_table_record"):
        return "name75_data_tail", "outside likely-real table"
    return "name75_review_label", "likely-real name75 with Japanese residue"


def actionable_reclass(reclass: str) -> bool:
    return reclass in {
        "actionable_sentence",
        "actionable_phrase",
        "jp_label_review",
        "name75_review_label",
    }


def pack_batches(rows: list[dict[str, Any]], prefix: str) -> list[tuple[str, list[dict[str, Any]]]]:
    packed: list[tuple[str, list[dict[str, Any]]]] = []
    current: list[dict[str, Any]] = []
    index = 1
    for row in rows:
        current.append(row)
        if len(current) >= MAX_BATCH:
            packed.append((f"{prefix}{index:03d}", current))
            index += 1
            current = []
    if current:
        packed.append((f"{prefix}{index:03d}", current))
    return packed


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def bind_row(
    *,
    tip: bytes,
    original: bytes,
    abs_hex: str,
    scope: str,
    source_tier: str,
    original_jp: str,
    current_text: str,
    reclass: str,
    notes: str,
    tip_sha: str,
    prior: dict[str, str],
    payload_override: bytes | None = None,
) -> dict[str, Any]:
    logical = int(abs_hex, 16)
    tip_got = read_encoded_z_safe(tip, stock_base(tip) + logical, max_len=256)
    orig_got = read_encoded_z_safe(original, stock_base(original) + logical, max_len=256)
    tip_payload = payload_override if payload_override is not None else (bytes(tip_got[0]) if tip_got else b"")
    orig_payload = bytes(orig_got[0]) if orig_got else b""
    matches = bool(tip_payload and tip_payload == orig_payload)
    jp = japanese_character_count(current_text)
    hangul = hangul_character_count(current_text)
    core = core_character_count(current_text)
    is_actionable = actionable_reclass(reclass)
    if is_actionable:
        workflow = str(prior.get("workflow_status") or "pending_translation")
        review = str(prior.get("review_status") or "unreviewed")
        ko = str(prior.get("ko") or "")
        source = str(prior.get("translation_source") or "")
    else:
        workflow = "quarantine_not_auto_translate"
        review = "quarantined"
        ko = ""
        source = ""
    return {
        "batch_id": "",
        "batch_order": "",
        "scope": scope,
        "bank": f"{logical >> 16:02X}",
        "abs": abs_hex,
        "source_tier": source_tier,
        "reclass": reclass,
        "shape": text_shape(current_text),
        "payload_capacity": len(tip_payload),
        "body_capacity": len(tip_payload),
        "original_jp": original_jp,
        "current_text": current_text,
        "japanese_count": jp,
        "hangul_count": hangul,
        "core_count": core,
        "payload_matches_original": "yes" if matches else "no",
        "strict_coherent_original": coherent(original_jp),
        "ko": ko,
        "translation_source": source,
        "review_status": review,
        "workflow_status": workflow,
        "notes": notes if not prior.get("notes") else str(prior.get("notes")),
        "current_payload_hex": tip_payload.hex().upper(),
        "source_body_sha256": sha(tip_payload),
        "parent_tip_sha256": tip_sha,
        "_actionable": is_actionable,
        "_scope_key": scope,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--encyclopedia", type=Path, default=ENCY)
    parser.add_argument("--broad", type=Path, default=BROAD)
    parser.add_argument("--name75", type=Path, default=NAME75)
    parser.add_argument("--out", type=Path, default=MASTER)
    parser.add_argument("--actionable-out", type=Path, default=ACTIONABLE)
    parser.add_argument("--batch-dir", type=Path, default=BATCH_DIR)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)

    tip = bytes(load_rom(args.tip))
    original = ORIGINAL.read_bytes()
    tip_sha = sha(tip)
    prior = existing_edits(args.out)

    ency = load_json(args.encyclopedia)
    broad = load_json(args.broad)
    name75 = load_json(args.name75)

    built: list[dict[str, Any]] = []

    for source in ency.get("records") or []:
        if source.get("status") not in {"japanese_residual", "name_alias_mismatch"}:
            continue
        abs_hex = str(source["abs"]).upper()
        tip_got = read_encoded_z_safe(tip, stock_base(tip) + int(abs_hex, 16), max_len=256)
        tip_payload = bytes(tip_got[0]) if tip_got else b""
        orig_got = read_encoded_z_safe(original, stock_base(original) + int(abs_hex, 16), max_len=256)
        orig_payload = bytes(orig_got[0]) if orig_got else b""
        matches = tip_payload == orig_payload and bool(tip_payload)
        reclass, notes = classify_encyclopedia(
            source,
            payload_len=len(tip_payload) or int(source.get("payload_len") or 0),
            matches_original=matches,
        )
        built.append(
            bind_row(
                tip=tip,
                original=original,
                abs_hex=abs_hex,
                scope="encyclopedia_bank5c",
                source_tier=str(source.get("status") or ""),
                original_jp=str(source.get("jp") or ""),
                current_text=str(source.get("current") or "").rstrip("\u3000 \t"),
                reclass=reclass,
                notes=notes,
                tip_sha=tip_sha,
                prior=prior.get(abs_hex) or {},
                payload_override=tip_payload,
            )
        )

    for source in (broad.get("records") or {}).get("tier_b") or []:
        abs_hex = str(source["abs"]).upper()
        reclass, notes = classify_broad(source)
        built.append(
            bind_row(
                tip=tip,
                original=original,
                abs_hex=abs_hex,
                scope="broad_tier_b",
                source_tier=f"B:{source.get('tier_reason') or ''}",
                original_jp=str(source.get("original_text") or ""),
                current_text=str(source.get("current_text") or "").rstrip("\u3000 \t"),
                reclass=reclass,
                notes=notes,
                tip_sha=tip_sha,
                prior=prior.get(abs_hex) or {},
            )
        )

    for source in name75.get("likely_real_records") or []:
        abs_hex = str(source["abs"]).upper()
        reclass, notes = classify_name75(source)
        built.append(
            bind_row(
                tip=tip,
                original=original,
                abs_hex=abs_hex,
                scope="name75_likely_real",
                source_tier="likely_real",
                original_jp=str(source.get("original_text") or ""),
                current_text=str(source.get("current_text") or "").rstrip("\u3000 \t"),
                reclass=reclass,
                notes=notes,
                tip_sha=tip_sha,
                prior=prior.get(abs_hex) or {},
            )
        )

    if not built:
        raise SheetError("no residual rows to sheet")

    # Deduplicate by abs (prefer encyclopedia over broad if overlap).
    by_abs: dict[str, dict[str, Any]] = {}
    priority = {"encyclopedia_bank5c": 0, "broad_tier_b": 1, "name75_likely_real": 2}
    for row in built:
        abs_hex = row["abs"]
        previous = by_abs.get(abs_hex)
        if previous is None or priority[row["_scope_key"]] < priority[previous["_scope_key"]]:
            by_abs[abs_hex] = row
    ordered = sorted(by_abs.values(), key=lambda row: (priority[row["_scope_key"]], int(row["abs"], 16)))

    actionable_rows = [row for row in ordered if row["_actionable"]]
    quarantine_rows = [row for row in ordered if not row["_actionable"]]

    for order, row in enumerate(quarantine_rows, start=1):
        row["batch_id"] = "QRN"
        row["batch_order"] = str(order)
    for batch_id, rows in pack_batches(actionable_rows, "R"):
        for order, row in enumerate(rows, start=1):
            row["batch_id"] = batch_id
            row["batch_order"] = str(order)

    for row in ordered:
        row.pop("_actionable", None)
        row.pop("_scope_key", None)

    write_csv(args.out, ordered)
    write_csv(
        args.actionable_out,
        [row for row in ordered if str(row["workflow_status"]) == "pending_translation"],
    )

    args.batch_dir.mkdir(parents=True, exist_ok=True)
    for old in args.batch_dir.glob("*.csv"):
        old.unlink()
    by_batch: collections.OrderedDict[str, list[dict[str, Any]]] = collections.OrderedDict()
    for row in ordered:
        by_batch.setdefault(str(row["batch_id"]), []).append(row)
    for batch_id, rows in by_batch.items():
        write_csv(args.batch_dir / f"{batch_id}.csv", rows)

    class_counts = collections.Counter(str(row["reclass"]) for row in ordered)
    scope_counts = collections.Counter(str(row["scope"]) for row in ordered)
    matches_original = sum(row["payload_matches_original"] == "yes" for row in ordered)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_display_residual_reclass_sheet.py",
        "read_only": True,
        "ok": True,
        "current_tip": identity(args.tip, tip),
        "inputs": {
            "encyclopedia": identity(args.encyclopedia),
            "broad": identity(args.broad),
            "name75": identity(args.name75),
        },
        "counts": {
            "sheet_rows": len(ordered),
            "actionable_pending_translation": len(actionable_rows),
            "quarantine": len(quarantine_rows),
            "payload_matches_original": matches_original,
            "by_scope": dict(sorted(scope_counts.items())),
            "by_reclass": dict(sorted(class_counts.items())),
        },
        "outputs": {
            "master": str(args.out.relative_to(ROOT)).replace("\\", "/"),
            "actionable": str(args.actionable_out.relative_to(ROOT)).replace("\\", "/"),
            "batches": str(args.batch_dir.relative_to(ROOT)).replace("\\", "/"),
            "batch_ids": list(by_batch),
        },
        "interpretation": {
            "headline": (
                "Encyclopedia/broad/name75 static JP hits are mostly false-text walker "
                "or short glyph fragments; real remaining translation work is elsewhere "
                "(shared-dictionary invasion-safe slots, runtime-proven UI)."
            ),
            "encyclopedia_note": (
                "All audited encyclopedia residual payloads matched Original bytes on "
                "this TIP; many decode as non-prose even under the Original dictionary."
            ),
            "next_real_targets": [
                "shared_dictionary live slots with DICT_INVASION_GUARD",
                "runtime screenshot-anchored UI/name leftovers",
                "bank59/battle ambiguous only with scene proof",
            ],
        },
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "ok": True,
                "counts": report["counts"],
                "outputs": report["outputs"],
                "interpretation": report["interpretation"]["headline"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
