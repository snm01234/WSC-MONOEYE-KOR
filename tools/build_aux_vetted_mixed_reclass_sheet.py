#!/usr/bin/env python3
"""Reclassify vetted-aux jp/mixed rows after structural prefix stripping.

``measure_aux_sentence_rate`` is fail-closed on prefix evidence.  When the apply
report does not trust a prefix, bank-59 control bytes (``08 xx`` / ``01`` /
``18``) and bank-5D/5E voice-id units remain in the rendered string and decode
as kana, so fully Korean bodies are counted as ``mixed``.

This read-only tool:

* loads the current-tip aux sentence-rate population;
* keeps banks ``59`` / ``5D`` / ``5E`` jp_only+mixed rows;
* strips structural prefixes using the same rules as
  ``measure_aux_prefix_rule`` (plus ``TEXT_INITIAL_EXCEPTIONS``);
* reclassifies the body;
* writes a master sheet, batch CSVs, and a classification report.

No ROM or SaveRAM is modified.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import looks_like_jp
from find_aux_text_tables import coherent
from measure_aux_prefix_rule import (
    BANK_RULES,
    TEXT_INITIAL_EXCEPTIONS,
    prefix_len,
)
from mixed_residual_classification import (
    core_character_count,
    defect_annotations,
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import Dictionary, Tbl, find_rom, load_rom, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
AUX_RATE = ROOT / "out/patch/current_tip_remaining_aux_sentence_rate.json"
AUTO_DRAFT = ROOT / "out/script/uncovered_translation_sheet_auto_draft.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

MASTER = ROOT / "out/script/aux_vetted_mixed_reclass_sheet.csv"
BATCH_DIR = ROOT / "out/script/aux_vetted_mixed_reclass_batches"
ACTIONABLE = ROOT / "out/script/aux_vetted_mixed_reclass_actionable.csv"
REPORT = ROOT / "out/patch/aux_vetted_mixed_reclass_report.json"

TARGET_BANKS = {0x59, 0x5D, 0x5E}
MAX_BATCH = 48

FIELDS = [
    "batch_id",
    "batch_order",
    "scope",
    "bank",
    "block",
    "abs",
    "rate_classification",
    "reclass",
    "shape",
    "prefix_hex",
    "prefix_rule",
    "payload_capacity",
    "body_capacity",
    "original_jp",
    "current_text",
    "current_full_with_untrusted_prefix",
    "japanese_count",
    "hangul_count",
    "core_count",
    "strict_coherent_original",
    "defect_annotations",
    "in_auto_draft_sheet",
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


def auto_draft_addresses() -> set[str]:
    if not AUTO_DRAFT.is_file():
        return set()
    out: set[str] = set()
    with AUTO_DRAFT.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            abs_hex = str(row.get("abs") or "").strip().upper()
            if abs_hex:
                out.add(abs_hex)
    return out


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


def structural_prefix(original_payload: bytes, tip_payload: bytes, logical: int) -> tuple[bytes, str]:
    bank = logical >> 16
    if logical in TEXT_INITIAL_EXCEPTIONS:
        return b"", "text_initial_exception"
    rule = BANK_RULES.get(bank)
    if rule is None:
        return b"", "no_bank_rule"
    length = prefix_len(original_payload, rule)
    prefix = original_payload[:length]
    if prefix and tip_payload.startswith(prefix):
        return prefix, rule
    # Tip may have rewritten the record; recompute from tip bytes.
    tip_length = prefix_len(tip_payload, rule)
    tip_prefix = tip_payload[:tip_length]
    if tip_prefix:
        return tip_prefix, f"{rule}_from_tip"
    return b"", f"{rule}_unmatched"


def reclass_body(current_text: str, original_text: str) -> tuple[str, str]:
    jp = japanese_character_count(current_text)
    hangul = hangul_character_count(current_text)
    core = core_character_count(current_text)
    annotations = defect_annotations(current_text)
    shape = "mixed" if jp and hangul else ("jp_only" if jp else ("ko_only" if hangul else "no_text"))
    if jp == 0 and hangul > 0:
        cls = "ko_only_after_prefix"
    elif jp and hangul:
        if core >= 6 and (coherent(original_text) or looks_like_jp(original_text)):
            cls = "true_mixed_sentence"
        else:
            cls = "true_mixed_short_or_ambiguous"
    elif jp and core >= 6 and (coherent(original_text) or looks_like_jp(original_text)):
        cls = "jp_only_sentence"
    elif jp:
        cls = "jp_short_or_fragment"
    elif hangul:
        cls = "ko_only_after_prefix"
    else:
        cls = "no_text_after_prefix"
    if annotations and cls.startswith(("true_mixed", "jp_only", "jp_short")):
        cls = f"{cls}+defects:{','.join(annotations)}"
    return cls, shape


def block_label(abs_hex: str, blocks: list[dict[str, Any]]) -> str:
    logical = int(abs_hex, 16)
    for block in blocks:
        if str(block.get("bank") or "").upper() != abs_hex[:2]:
            continue
        lo = int(str(block["start"]), 16)
        hi = int(str(block["end_exclusive"]), 16)
        if lo <= logical < hi:
            return f"{block['start']}-{block['end_exclusive']}"
    return "unknown_block"


def pack_batches(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: collections.OrderedDict[str, list[dict[str, Any]]] = collections.OrderedDict()
    for row in rows:
        grouped.setdefault(str(row["block"]), []).append(row)
    packed: list[tuple[str, list[dict[str, Any]]]] = []
    current: list[dict[str, Any]] = []
    index = 1
    for _block, group in grouped.items():
        if current and len(current) + len(group) > MAX_BATCH:
            packed.append((f"M{index:03d}", current))
            index += 1
            current = []
        current.extend(group)
        if len(current) >= MAX_BATCH:
            packed.append((f"M{index:03d}", current))
            index += 1
            current = []
    if current:
        packed.append((f"M{index:03d}", current))
    return packed


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--aux-rate", type=Path, default=AUX_RATE)
    parser.add_argument("--out", type=Path, default=MASTER)
    parser.add_argument("--actionable-out", type=Path, default=ACTIONABLE)
    parser.add_argument("--batch-dir", type=Path, default=BATCH_DIR)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)

    tip = bytes(load_rom(args.tip))
    original_path = find_rom(ROOT)
    original = bytes(load_rom(original_path))
    tip_sha = sha(tip)
    tbl = Tbl.load(TBL_PATH)
    current_dictionary = make_dictionary_ext3(tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    original_dictionary = Dictionary(original)
    sb = stock_base(tip)
    osb = stock_base(original)

    aux_rate = load_json(args.aux_rate)
    blocks_doc = load_json(ROOT / "out/script/aux_text_blocks.json")
    blocks = [dict(row) for row in blocks_doc.get("blocks") or []]
    sheet_addresses = auto_draft_addresses()
    prior = existing_edits(args.out)

    source_rows = [
        row
        for row in ((aux_rate.get("population") or {}).get("records") or [])
        if str(row.get("source_classification") or "") in {"jp_only", "mixed"}
        and (int(str(row.get("abs") or "0"), 16) >> 16) in TARGET_BANKS
    ]
    if not source_rows:
        raise SheetError("no target aux jp/mixed rows found")

    built: list[dict[str, Any]] = []
    class_counts: collections.Counter[str] = collections.Counter()
    bank_counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)

    for source in sorted(source_rows, key=lambda row: int(str(row["abs"]), 16)):
        abs_hex = str(source["abs"]).upper()
        logical = int(abs_hex, 16)
        bank = f"{logical >> 16:02X}"
        tip_got = read_encoded_z_safe(tip, sb + logical, max_len=256)
        orig_got = read_encoded_z_safe(original, osb + logical, max_len=256)
        if not tip_got or not orig_got:
            raise SheetError(f"unreadable record at {abs_hex}")
        tip_payload = bytes(tip_got[0])
        original_payload = bytes(orig_got[0])
        prefix, prefix_rule = structural_prefix(original_payload, tip_payload, logical)
        if prefix and not tip_payload.startswith(prefix):
            raise SheetError(f"prefix mismatch at {abs_hex}")
        body = tip_payload[len(prefix) :]
        original_body = original_payload[len(prefix) :]
        current_text = current_dictionary.expand(body, tbl).rstrip("\u3000 \t")
        original_text = original_dictionary.expand(original_body, tbl).rstrip("\u3000 \t")
        full_text = current_dictionary.expand(tip_payload, tbl).rstrip("\u3000 \t")
        reclass, shape = reclass_body(current_text, original_text)
        jp = japanese_character_count(current_text)
        hangul = hangul_character_count(current_text)
        core = core_character_count(current_text)
        annotations = defect_annotations(current_text)
        actionable = reclass.startswith(("true_mixed", "jp_only_sentence"))
        prior_row = prior.get(abs_hex) or {}
        if actionable:
            workflow = str(prior_row.get("workflow_status") or "pending_translation")
            review = str(prior_row.get("review_status") or "unreviewed")
            notes = str(prior_row.get("notes") or "body still contains Japanese after structural prefix strip")
        else:
            workflow = "cleared_false_mixed"
            review = "not_needed_false_mixed"
            notes = str(
                prior_row.get("notes")
                or "rate-report mixed/jp_only was structural prefix kana; body is Korean after strip"
            )
        row = {
            "batch_id": "",
            "batch_order": "",
            "scope": "aux_vetted_mixed_reclass",
            "bank": bank,
            "block": block_label(abs_hex, blocks),
            "abs": abs_hex,
            "rate_classification": str(source.get("source_classification") or ""),
            "reclass": reclass,
            "shape": shape,
            "prefix_hex": prefix.hex().upper(),
            "prefix_rule": prefix_rule,
            "payload_capacity": len(tip_payload),
            "body_capacity": len(body),
            "original_jp": original_text,
            "current_text": current_text,
            "current_full_with_untrusted_prefix": full_text,
            "japanese_count": jp,
            "hangul_count": hangul,
            "core_count": core,
            "strict_coherent_original": coherent(original_text),
            "defect_annotations": ",".join(annotations),
            "in_auto_draft_sheet": "yes" if abs_hex in sheet_addresses else "no",
            "ko": str(prior_row.get("ko") or "") if actionable else "",
            "translation_source": str(prior_row.get("translation_source") or "") if actionable else "",
            "review_status": review,
            "workflow_status": workflow,
            "notes": notes,
            "current_payload_hex": tip_payload.hex().upper(),
            "source_body_sha256": sha(body),
            "parent_tip_sha256": tip_sha,
            "_actionable": actionable,
        }
        built.append(row)
        class_counts[reclass] += 1
        bank_counts[bank][reclass] += 1

    actionable_rows = [row for row in built if row["_actionable"]]
    cleared_rows = [row for row in built if not row["_actionable"]]

    # Cleared population stays in one audit batch; actionable gets Mxxx packs.
    for order, row in enumerate(cleared_rows, start=1):
        row["batch_id"] = "CLR"
        row["batch_order"] = str(order)
    actionable_batches = pack_batches(actionable_rows)
    for batch_id, rows in actionable_batches:
        for order, row in enumerate(rows, start=1):
            row["batch_id"] = batch_id
            row["batch_order"] = str(order)

    ordered = sorted(built, key=lambda row: int(row["abs"], 16))
    for row in ordered:
        row.pop("_actionable", None)

    write_csv(args.out, ordered)
    write_csv(args.actionable_out, [row for row in ordered if str(row["workflow_status"]) == "pending_translation"])

    args.batch_dir.mkdir(parents=True, exist_ok=True)
    for old in args.batch_dir.glob("*.csv"):
        old.unlink()
    by_batch: dict[str, list[dict[str, Any]]] = collections.OrderedDict()
    for row in ordered:
        by_batch.setdefault(str(row["batch_id"]), []).append(row)
    for batch_id, rows in by_batch.items():
        write_csv(args.batch_dir / f"{batch_id}.csv", rows)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_aux_vetted_mixed_reclass_sheet.py",
        "read_only": True,
        "ok": True,
        "current_tip": identity(args.tip, tip),
        "inputs": {
            "aux_rate": identity(args.aux_rate),
            "auto_draft_sheet_rows": len(sheet_addresses),
        },
        "scope": {
            "banks": sorted(f"{bank:02X}" for bank in TARGET_BANKS),
            "source": "aux sentence-rate population jp_only+mixed",
            "method": "structural prefix strip via measure_aux_prefix_rule.BANK_RULES",
        },
        "counts": {
            "source_rows": len(source_rows),
            "sheet_rows": len(ordered),
            "cleared_false_mixed": len(cleared_rows),
            "actionable_pending_translation": len(actionable_rows),
            "by_reclass": dict(sorted(class_counts.items())),
            "by_bank": {bank: dict(sorted(counter.items())) for bank, counter in sorted(bank_counts.items())},
            "overlap_auto_draft_sheet": sum(row["in_auto_draft_sheet"] == "yes" for row in ordered),
        },
        "outputs": {
            "master": str(args.out.relative_to(ROOT)).replace("\\", "/"),
            "actionable": str(args.actionable_out.relative_to(ROOT)).replace("\\", "/"),
            "batches": str(args.batch_dir.relative_to(ROOT)).replace("\\", "/"),
            "batch_ids": list(by_batch),
        },
        "interpretation": {
            "headline": (
                "Almost all aux sentence-rate mixed/jp_only rows in banks 59/5D/5E are "
                "false mixed caused by untrusted structural prefixes decoding as kana."
            ),
            "next_real_targets": [
                "encyclopedia_bank5c residuals",
                "broad_tier_b short UI/name fragments",
                "shared_dictionary live slots with invasion guard",
                "bank59/battle ambiguous quarantine only with runtime proof",
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
