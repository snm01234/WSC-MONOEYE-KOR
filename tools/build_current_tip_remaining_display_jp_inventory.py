#!/usr/bin/env python3
"""Unified remaining display-JP inventory against the live main TIP.

Re-runs the actionable gap audits plus broader surface scanners that the
auto-draft sheet does not cover, then classifies each address into:

* actionable_gap_residual — still Japanese in bank59 / 5D-5E / ID-indirect
  scopes after the auto-draft promotion
* already_in_auto_draft_sheet — sheet address that no longer shows JP (clean)
* still_jp_despite_auto_draft — sheet address that still decodes with JP
* review_only_ambiguous / placeholder_or_template
* broad_tier_{a,b,c}
* name75_likely_real / name75_data_tail
* encyclopedia_bank5c
* shared_dictionary_live_slot
* forbidden_data_bank_64_69 (never translate)

Does not write ROM or SaveRAM.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
AUTO_DRAFT = ROOT / "out/script/uncovered_translation_sheet_auto_draft.csv"
OUT = ROOT / "out/patch/current_tip_remaining_display_jp_inventory.json"
SAMPLES_OUT = ROOT / "out/patch/current_tip_remaining_display_jp_samples.json"

BANK59_OUT = ROOT / "out/patch/current_tip_bank59_uncovered_event_residual_audit.json"
BATTLE_OUT = ROOT / "out/patch/current_tip_battle_voice_uncovered_residual_audit.json"
ID_OUT = ROOT / "out/patch/current_tip_id_indirect_command_residual_audit.json"
BROAD_OUT = ROOT / "out/patch/current_tip_remaining_broad_japanese_residuals.json"
NAME75_OUT = ROOT / "out/patch/current_tip_remaining_name75_untranslated_terms.json"
ENCY_OUT = ROOT / "out/patch/current_tip_remaining_encyclopedia_bank5c.json"
SHARED_OUT = ROOT / "out/patch/current_tip_remaining_shared_dictionary_japanese.json"
AUX_RATE_OUT = ROOT / "out/patch/current_tip_remaining_aux_sentence_rate.json"

FORBIDDEN_BANKS = set(range(0x64, 0x70))
NOISE_BANKS = {0x52, 0x56, 0x5A, 0x5B, 0x76}
NAME75_DATA_TAIL = 0x75E630


class InventoryError(RuntimeError):
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
        raise InventoryError(f"JSON root must be object: {path}")
    return value


def run_tool(
    script: str,
    args: list[str],
    *,
    label: str,
    allow_nonzero: bool = False,
    expect_out: Path | None = None,
) -> None:
    command = [sys.executable, str(ROOT / "tools" / script), *args]
    print(f"[run] {label}: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=str(ROOT), check=False)
    if completed.returncode != 0 and not allow_nonzero:
        raise InventoryError(f"{label} failed with exit {completed.returncode}")
    if expect_out is not None and not expect_out.is_file():
        raise InventoryError(f"{label} did not write expected output: {expect_out}")


def bank_of(abs_hex: str) -> int:
    return int(abs_hex, 16) >> 16


def classify_forbidden(abs_hex: str) -> str | None:
    bank = bank_of(abs_hex)
    if bank in FORBIDDEN_BANKS:
        return "forbidden_data_bank_64_69"
    if bank in NOISE_BANKS:
        return "noise_bank_table_or_graphics"
    return None


def load_auto_draft_addresses() -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with AUTO_DRAFT.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            abs_hex = str(row.get("abs") or "").strip().upper()
            if not abs_hex:
                continue
            rows[abs_hex] = {
                "scope": str(row.get("scope") or ""),
                "classification": str(row.get("classification") or ""),
                "workflow_status": str(row.get("workflow_status") or ""),
                "ko_present": "yes" if str(row.get("ko") or "").strip() else "no",
            }
    return rows


def row_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def add_record(
    by_abs: dict[str, dict[str, Any]],
    *,
    abs_hex: str,
    bucket: str,
    source: str,
    text: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    abs_hex = abs_hex.upper()
    forbidden = classify_forbidden(abs_hex)
    entry = by_abs.setdefault(
        abs_hex,
        {
            "abs": abs_hex,
            "bank": f"{bank_of(abs_hex):02X}",
            "buckets": [],
            "sources": [],
            "sample_text": "",
            "auto_draft": None,
            "final_class": "",
        },
    )
    if bucket not in entry["buckets"]:
        entry["buckets"].append(bucket)
    if source not in entry["sources"]:
        entry["sources"].append(source)
    if text and not entry["sample_text"]:
        entry["sample_text"] = text[:120]
    if extra:
        entry.setdefault("extra", {}).update(extra)
    if forbidden and "forbidden_or_noise" not in entry["buckets"]:
        entry["buckets"].append("forbidden_or_noise")
        entry.setdefault("extra", {})["forbidden_or_noise"] = forbidden


def finalize_class(entry: dict[str, Any], auto_draft: dict[str, dict[str, str]]) -> str:
    abs_hex = entry["abs"]
    buckets = set(entry["buckets"])
    sheet = auto_draft.get(abs_hex)
    entry["auto_draft"] = sheet
    if "forbidden_or_noise" in buckets:
        return str((entry.get("extra") or {}).get("forbidden_or_noise") or "forbidden_or_noise")
    live_jp_buckets = {
        "actionable_gap_residual",
        "encyclopedia_bank5c",
        "name75_likely_real",
        "broad_tier_a",
        "broad_tier_b",
        "aux_sentence_jp_or_mixed",
        "shared_dictionary_live_slot",
    }
    if sheet and buckets & live_jp_buckets:
        return "still_jp_despite_auto_draft"
    if sheet:
        return "already_in_auto_draft_sheet_seen_only_as_quarantine_or_tail"
    if "actionable_gap_residual" in buckets:
        return "actionable_gap_residual"
    if "encyclopedia_bank5c" in buckets:
        return "encyclopedia_bank5c"
    if "name75_likely_real" in buckets:
        return "name75_likely_real"
    if "broad_tier_a" in buckets:
        return "broad_tier_a"
    if "broad_tier_b" in buckets:
        return "broad_tier_b"
    if "shared_dictionary_live_slot" in buckets:
        return "shared_dictionary_live_slot"
    if "placeholder_or_template" in buckets:
        return "placeholder_or_template"
    if "review_only_ambiguous" in buckets:
        return "review_only_ambiguous"
    if "name75_data_tail" in buckets:
        return "name75_data_tail"
    if "broad_tier_c" in buckets:
        return "broad_tier_c"
    if "aux_sentence_jp_or_mixed" in buckets:
        return "aux_sentence_jp_or_mixed"
    return "other_surface"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-aux-rate", action="store_true", help="skip slow aux sentence-rate scan")
    parser.add_argument("--skip-run", action="store_true", help="only consolidate existing JSON outputs")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--samples-out", type=Path, default=SAMPLES_OUT)
    parser.add_argument("--sample-limit", type=int, default=40)
    args = parser.parse_args(argv)

    tip = TIP.read_bytes()
    tip_id = identity(TIP, tip)
    auto_draft = load_auto_draft_addresses()

    if not args.skip_run:
        run_tool("audit_bank59_uncovered_event_residuals.py", [], label="bank59_gap")
        run_tool("audit_battle_voice_uncovered_residuals.py", [], label="battle_voice_gap")
        run_tool("audit_id_indirect_command_residuals.py", [], label="id_indirect")
        run_tool(
            "audit_broad_japanese_residuals.py",
            ["--tip", str(TIP), "--out", str(BROAD_OUT)],
            label="broad",
            allow_nonzero=True,
            expect_out=BROAD_OUT,
        )
        run_tool(
            "audit_name75_untranslated_terms.py",
            ["--tip", str(TIP), "--out", str(NAME75_OUT)],
            label="name75",
            expect_out=NAME75_OUT,
        )
        run_tool(
            "audit_encyclopedia_bank5c.py",
            ["--rom", str(TIP), "--output", str(ENCY_OUT)],
            label="encyclopedia",
            expect_out=ENCY_OUT,
        )
        run_tool(
            "audit_shared_dictionary_japanese_residuals.py",
            ["--tip", str(TIP), "--out", str(SHARED_OUT)],
            label="shared_dictionary",
            allow_nonzero=True,
            expect_out=SHARED_OUT,
        )
        if not args.skip_aux_rate:
            run_tool(
                "measure_aux_sentence_rate.py",
                ["--rom", str(TIP), "--out", str(AUX_RATE_OUT), "--quiet"],
                label="aux_sentence_rate",
                expect_out=AUX_RATE_OUT,
            )

    bank59 = load_json(BANK59_OUT)
    battle = load_json(BATTLE_OUT)
    id_indirect = load_json(ID_OUT)
    broad = load_json(BROAD_OUT)
    name75 = load_json(NAME75_OUT)
    ency = load_json(ENCY_OUT)
    shared = load_json(SHARED_OUT)
    aux_rate = load_json(AUX_RATE_OUT) if AUX_RATE_OUT.is_file() else None

    for document, label in (
        (bank59, "bank59"),
        (battle, "battle"),
        (id_indirect, "id_indirect"),
    ):
        if document.get("ok") is not True:
            raise InventoryError(f"source audit not ok: {label}")
    # broad/shared may set ok=false on encode-plan failures while still usable as residue inventory
    for document, label in (
        (broad, "broad"),
        (name75, "name75"),
        (ency, "encyclopedia"),
        (shared, "shared"),
    ):
        if not isinstance(document, dict):
            raise InventoryError(f"source audit missing: {label}")

    by_abs: dict[str, dict[str, Any]] = {}

    for row in bank59.get("actionable") or []:
        abs_hex = str(row.get("abs") or "").upper()
        if not abs_hex:
            continue
        add_record(
            by_abs,
            abs_hex=abs_hex,
            bucket="actionable_gap_residual",
            source="bank59_uncovered",
            text=row_text(row, "current", "original"),
            extra={"gap_scope": "bank59_event", "shape": row.get("shape")},
        )
    for row in bank59.get("ambiguous_review_only") or []:
        abs_hex = str(row.get("abs") or "").upper()
        if abs_hex:
            add_record(
                by_abs,
                abs_hex=abs_hex,
                bucket="review_only_ambiguous",
                source="bank59_uncovered",
                text=row_text(row, "current", "original"),
            )

    for row in battle.get("actionable") or []:
        abs_hex = str(row.get("abs") or "").upper()
        if abs_hex:
            add_record(
                by_abs,
                abs_hex=abs_hex,
                bucket="actionable_gap_residual",
                source="battle_voice_uncovered",
                text=row_text(row, "current_body", "original_body"),
                extra={"gap_scope": "battle_voice", "shape": row.get("shape")},
            )
    for row in battle.get("placeholder_or_template") or []:
        abs_hex = str(row.get("abs") or "").upper()
        if abs_hex:
            add_record(
                by_abs,
                abs_hex=abs_hex,
                bucket="placeholder_or_template",
                source="battle_voice_uncovered",
                text=row_text(row, "current_body", "original_body"),
            )
    for row in battle.get("ambiguous_review_only") or []:
        abs_hex = str(row.get("abs") or "").upper()
        if abs_hex:
            add_record(
                by_abs,
                abs_hex=abs_hex,
                bucket="review_only_ambiguous",
                source="battle_voice_uncovered",
                text=row_text(row, "current_body", "original_body"),
            )

    for row in id_indirect.get("actionable") or []:
        abs_hex = str(row.get("abs") or "").upper()
        if abs_hex:
            add_record(
                by_abs,
                abs_hex=abs_hex,
                bucket="actionable_gap_residual",
                source="id_indirect",
                text=row_text(row, "current_body", "original_body"),
                extra={"gap_scope": "id_indirect", "category": row.get("category")},
            )

    for tier, bucket in (("tier_a", "broad_tier_a"), ("tier_b", "broad_tier_b"), ("tier_c", "broad_tier_c")):
        for row in (broad.get("records") or {}).get(tier) or []:
            abs_hex = str(row.get("abs") or "").upper()
            if abs_hex:
                add_record(
                    by_abs,
                    abs_hex=abs_hex,
                    bucket=bucket,
                    source="broad_japanese",
                    text=row_text(row, "current_text", "original_text"),
                    extra={"region": row.get("region"), "tier": tier},
                )

    for row in name75.get("all_records") or []:
        abs_hex = str(row.get("abs") or "").upper()
        if not abs_hex:
            continue
        logical = int(abs_hex, 16)
        bucket = "name75_likely_real" if logical < NAME75_DATA_TAIL or int(row.get("ptr_good") or 0) > 0 else "name75_data_tail"
        add_record(
            by_abs,
            abs_hex=abs_hex,
            bucket=bucket,
            source="name75",
            text=row_text(row, "current_text", "original_text"),
        )

    for row in ency.get("records") or []:
        abs_hex = str(row.get("abs") or "").upper()
        if not abs_hex:
            continue
        if row.get("status") not in {"japanese_residual", "name_alias_mismatch"}:
            continue
        add_record(
            by_abs,
            abs_hex=abs_hex,
            bucket="encyclopedia_bank5c",
            source="encyclopedia_bank5c",
            text=row_text(row, "current", "jp"),
            extra={"status": row.get("status")},
        )

    for tier in ("tier_a", "tier_b"):
        for row in (shared.get("records") or {}).get(tier) or []:
            index = str(row.get("index") or "").upper()
            # Shared slots are not abs records; store under synthetic key.
            key = f"DICT:{index}"
            entry = by_abs.setdefault(
                key,
                {
                    "abs": key,
                    "bank": "DICT",
                    "buckets": [],
                    "sources": [],
                    "sample_text": "",
                    "auto_draft": None,
                    "final_class": "",
                },
            )
            bucket = "shared_dictionary_live_slot"
            if bucket not in entry["buckets"]:
                entry["buckets"].append(bucket)
            if "shared_dictionary" not in entry["sources"]:
                entry["sources"].append("shared_dictionary")
            if not entry["sample_text"]:
                entry["sample_text"] = row_text(row, "current_text", "original_text")[:120]
            entry.setdefault("extra", {})["tier"] = tier
            entry.setdefault("extra", {})["index"] = index

    aux_rate_summary: dict[str, Any] | None = None
    aux_jp_mixed_by_bank: dict[str, int] = {}
    meaningful_aux_jp_mixed = 0
    if aux_rate is not None:
        population = aux_rate.get("population") or {}
        all_records = aux_rate.get("all_records") or {}
        sentences = aux_rate.get("sentences") or {}
        bank_counter: collections.Counter[str] = collections.Counter()
        for row in list(population.get("records") or []):
            classification = str(row.get("source_classification") or "").lower()
            if classification not in {"jp_only", "mixed"}:
                continue
            abs_hex = str(row.get("abs") or "").upper()
            if not abs_hex:
                continue
            bank = abs_hex[:2]
            bank_counter[bank] += 1
            if int(abs_hex[:2], 16) not in NOISE_BANKS:
                meaningful_aux_jp_mixed += 1
            add_record(
                by_abs,
                abs_hex=abs_hex,
                bucket="aux_sentence_jp_or_mixed",
                source="aux_sentence_rate",
                text="",
                extra={"aux_classification": classification, "core_count": row.get("core_count")},
            )
        aux_jp_mixed_by_bank = dict(sorted(bank_counter.items()))
        aux_rate_summary = {
            "path": str(AUX_RATE_OUT.relative_to(ROOT)).replace("\\", "/"),
            "all_records": all_records,
            "sentences": sentences,
            "by_bank": aux_rate.get("by_bank") or {},
            "jp_or_mixed_by_bank": aux_jp_mixed_by_bank,
            "jp_or_mixed_excluding_noise_banks": meaningful_aux_jp_mixed,
            "noise_banks": sorted(f"{bank:02X}" for bank in NOISE_BANKS),
        }

    class_counts: collections.Counter[str] = collections.Counter()
    actionable_gap_not_in_sheet: list[dict[str, Any]] = []
    still_jp_despite_sheet: list[dict[str, Any]] = []
    outside_sheet_priority: list[dict[str, Any]] = []

    for entry in by_abs.values():
        final = finalize_class(entry, auto_draft)
        entry["final_class"] = final
        class_counts[final] += 1
        slim = {
            "abs": entry["abs"],
            "bank": entry["bank"],
            "final_class": final,
            "buckets": entry["buckets"],
            "sources": entry["sources"],
            "sample_text": entry["sample_text"],
            "auto_draft": entry["auto_draft"],
        }
        if final == "actionable_gap_residual":
            actionable_gap_not_in_sheet.append(slim)
        elif final == "still_jp_despite_auto_draft":
            still_jp_despite_sheet.append(slim)
        elif final in {
            "encyclopedia_bank5c",
            "name75_likely_real",
            "broad_tier_a",
            "broad_tier_b",
            "shared_dictionary_live_slot",
            "aux_sentence_jp_or_mixed",
        }:
            outside_sheet_priority.append(slim)

    def sort_key(row: dict[str, Any]) -> tuple[int, str]:
        abs_value = str(row["abs"])
        if abs_value.startswith("DICT:"):
            return (1, abs_value)
        return (0, abs_value)

    actionable_gap_not_in_sheet.sort(key=sort_key)
    still_jp_despite_sheet.sort(key=sort_key)
    outside_sheet_priority.sort(key=sort_key)

    gap_counts = {
        "bank59_actionable": int((bank59.get("counts") or {}).get("actionable_sentence_total", 0)),
        "bank59_ambiguous": int((bank59.get("counts") or {}).get("ambiguous_review_only", 0)),
        "battle_actionable": int((battle.get("counts") or {}).get("actionable_sentence_total", 0)),
        "battle_ambiguous": int((battle.get("counts") or {}).get("ambiguous_review_only", 0)),
        "battle_placeholders": int((battle.get("counts") or {}).get("placeholder_or_template", 0)),
        "id_indirect_actionable": int((id_indirect.get("counts") or {}).get("actionable_residuals", 0)),
    }

    sheet_still_hit = len(still_jp_despite_sheet)
    sheet_total = len(auto_draft)
    sheet_clean_estimate = max(0, sheet_total - sheet_still_hit)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_current_tip_remaining_display_jp_inventory.py",
        "read_only": True,
        "ok": True,
        "current_tip": tip_id,
        "auto_draft_sheet": {
            "path": str(AUTO_DRAFT.relative_to(ROOT)).replace("\\", "/"),
            "rows": sheet_total,
            "still_showing_jp_in_gap_audits": sheet_still_hit,
            "no_longer_in_gap_actionable_estimate": sheet_clean_estimate,
            "note": "clean estimate = sheet rows minus addresses still present in gap actionable sets",
        },
        "source_audits": {
            "bank59": str(BANK59_OUT.relative_to(ROOT)).replace("\\", "/"),
            "battle_voice": str(BATTLE_OUT.relative_to(ROOT)).replace("\\", "/"),
            "id_indirect": str(ID_OUT.relative_to(ROOT)).replace("\\", "/"),
            "broad": str(BROAD_OUT.relative_to(ROOT)).replace("\\", "/"),
            "name75": str(NAME75_OUT.relative_to(ROOT)).replace("\\", "/"),
            "encyclopedia": str(ENCY_OUT.relative_to(ROOT)).replace("\\", "/"),
            "shared_dictionary": str(SHARED_OUT.relative_to(ROOT)).replace("\\", "/"),
            "aux_sentence_rate": str(AUX_RATE_OUT.relative_to(ROOT)).replace("\\", "/")
            if AUX_RATE_OUT.is_file()
            else None,
        },
        "gap_audit_counts": gap_counts,
        "surface_audit_counts": {
            "broad": broad.get("counts") or {},
            "name75": name75.get("counts") or {},
            "encyclopedia": ency.get("counts") or {},
            "shared_dictionary": shared.get("counts") or {},
            "aux_sentence_rate": aux_rate_summary,
        },
        "unified_classification_counts": dict(sorted(class_counts.items())),
        "priority_remaining": {
            "still_jp_despite_auto_draft": len(still_jp_despite_sheet),
            "actionable_gap_not_in_sheet": len(actionable_gap_not_in_sheet),
            "encyclopedia_bank5c": class_counts.get("encyclopedia_bank5c", 0),
            "encyclopedia_bank5c_jp_chars_ge_3": sum(
                1
                for row in ency.get("records") or []
                if row.get("status") in {"japanese_residual", "name_alias_mismatch"}
                and int(row.get("japanese_count") or 0) >= 3
            ),
            "name75_likely_real": class_counts.get("name75_likely_real", 0),
            "broad_tier_a": class_counts.get("broad_tier_a", 0),
            "broad_tier_b": class_counts.get("broad_tier_b", 0),
            "shared_dictionary_live_slot": class_counts.get("shared_dictionary_live_slot", 0),
            "shared_dictionary_tier_a_ready": int((shared.get("counts") or {}).get("tier_a_translation_ready", 0)),
            "aux_sentence_jp_or_mixed": class_counts.get("aux_sentence_jp_or_mixed", 0),
            "aux_sentence_jp_or_mixed_excluding_noise_banks": meaningful_aux_jp_mixed
            if aux_rate is not None
            else None,
            "aux_jp_or_mixed_by_bank": aux_jp_mixed_by_bank if aux_rate is not None else {},
        },
        "quarantine_counts": {
            "review_only_ambiguous": class_counts.get("review_only_ambiguous", 0),
            "placeholder_or_template": class_counts.get("placeholder_or_template", 0),
            "broad_tier_c": class_counts.get("broad_tier_c", 0),
            "name75_data_tail": class_counts.get("name75_data_tail", 0),
            "forbidden_data_bank_64_69": class_counts.get("forbidden_data_bank_64_69", 0),
            "noise_bank_table_or_graphics": class_counts.get("noise_bank_table_or_graphics", 0),
        },
        "unique_addresses_or_slots_scanned": len(by_abs),
        "notes": [
            "auto_draft sheet covers only the prior bank59+5D/5E+ID actionable population",
            "gap actionable totals are now 0 on current TIP: the 1,893 auto-draft rows no longer appear as gap JP",
            "banks 64-69 are event/unit data and must not be translated",
            "noise banks 52/56/5A/5B/76 decode as kana-like garbage",
            "shared_dictionary entries are stock slot indices (DICT:xxxx), not ROM abs; invasion guard required before rewrite",
            "aux_sentence jp/mixed inside vetted blocks was mostly false mixed from untrusted structural prefixes; see aux_vetted_mixed_reclass_report.json",
            "after structural prefix strip: 2819/2820 ko_only_after_prefix, 0 true mixed/jp sentence actionable",
            "audit_current_untranslated_dialogue.py skipped: older aux population report SHA binding",
        ],
    }

    samples = {
        "schema_version": 1,
        "generated_by": "tools/build_current_tip_remaining_display_jp_inventory.py",
        "current_tip_sha256": tip_id["sha256"],
        "still_jp_despite_auto_draft": still_jp_despite_sheet[: args.sample_limit],
        "actionable_gap_not_in_sheet": actionable_gap_not_in_sheet[: args.sample_limit],
        "outside_sheet_priority": outside_sheet_priority[: args.sample_limit],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    args.samples_out.write_text(json.dumps(samples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "ok": True,
                "current_tip": tip_id,
                "priority_remaining": report["priority_remaining"],
                "quarantine_counts": report["quarantine_counts"],
                "gap_audit_counts": gap_counts,
                "out": str(args.out.relative_to(ROOT)).replace("\\", "/"),
                "samples": str(args.samples_out.relative_to(ROOT)).replace("\\", "/"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
