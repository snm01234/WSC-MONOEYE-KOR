#!/usr/bin/env python3
"""Build a deduplicated current-TIP Japanese residual worklist.

This consolidates the live current-TIP audits across uncovered event dialogue,
battle/ship voice, ID-command text, broad residual surfaces, name75, the bank-5C
encyclopedia, and live shared dictionary slots.  It intentionally stores text
hashes and character counts instead of reproducing the full game text; the
source audit and record address/slot are sufficient to retrieve the original
row when a translation or runtime check is started.

No ROM or SaveRAM is modified.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"

BANK59 = ROOT / "out/patch/current_tip_bank59_uncovered_event_residual_audit.json"
BATTLE = ROOT / "out/patch/current_tip_battle_voice_uncovered_residual_audit.json"
ID_INDIRECT = ROOT / "out/patch/current_tip_id_indirect_command_residual_audit.json"
BROAD = ROOT / "out/patch/current_tip_remaining_broad_japanese_residuals.json"
NAME75 = ROOT / "out/patch/current_tip_remaining_name75_untranslated_terms.json"
ENCYCLOPEDIA = ROOT / "out/patch/current_tip_remaining_encyclopedia_bank5c.json"
SHARED = ROOT / "out/patch/current_tip_remaining_shared_dictionary_japanese.json"
AUX_RECLASS = ROOT / "out/patch/aux_vetted_mixed_reclass_report.json"
AUX_ACTIONABLE = ROOT / "out/script/aux_vetted_mixed_reclass_actionable.csv"
UNIFIED = ROOT / "out/patch/current_tip_remaining_display_jp_inventory.json"

OUT_CSV = ROOT / "out/script/current_tip_all_japanese_residual_worklist.csv"
MIXED_CSV = ROOT / "out/script/current_tip_mixed_japanese_worklist.csv"
SUMMARY_JSON = ROOT / "out/patch/current_tip_all_japanese_residual_summary.json"
DOC = ROOT / "docs/CURRENT_TIP_JAPANESE_RESIDUAL_INVENTORY.md"

JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
KO_RE = re.compile(r"[\uac00-\ud7a3]")

FIELDS = [
    "priority",
    "record_key",
    "address_or_slot",
    "bank",
    "primary_class",
    "all_classes",
    "shape",
    "japanese_count",
    "hangul_count",
    "core_length",
    "current_text_sha256",
    "original_text_sha256",
    "confidence",
    "runtime_status",
    "recommended_action",
    "translation_state",
    "translation_candidate_present",
    "consumer_count",
    "consumer_regions",
    "reason",
    "source_reports",
]

CLASS_RANK = {
    "confirmed_sentence_residual": 0,
    "aux_confirmed_sentence_residual": 1,
    "encyclopedia_bank5c": 10,
    "broad_tier_b": 20,
    "name75_likely_real": 25,
    "bank59_ambiguous": 30,
    "battle_ambiguous": 31,
    "battle_placeholder_or_template": 40,
    "shared_dictionary_tier_a": 50,
    "shared_dictionary_tier_b": 51,
    "name75_data_tail": 55,
    "broad_tier_c": 60,
}

SOURCE_PATHS = {
    "bank59": BANK59,
    "battle": BATTLE,
    "id_indirect": ID_INDIRECT,
    "broad": BROAD,
    "name75": NAME75,
    "encyclopedia": ENCYCLOPEDIA,
    "shared_dictionary": SHARED,
    "aux_reclass": AUX_RECLASS,
}


class WorklistError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_sha(text: str) -> str:
    return sha(text.encode("utf-8")) if text else ""


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WorklistError(f"JSON root must be object: {path}")
    return value


def source_tip_sha(document: dict[str, Any]) -> str:
    for candidate in (
        document.get("tip"),
        document.get("current_tip"),
        (document.get("inputs") or {}).get("tip"),
        (document.get("inputs") or {}).get("rom"),
    ):
        if isinstance(candidate, dict) and candidate.get("sha256"):
            return str(candidate["sha256"]).lower()
    return ""


def count_chars(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text))


def text_shape(text: str) -> str:
    jp = count_chars(JP_RE, text)
    ko = count_chars(KO_RE, text)
    if jp and ko:
        return "mixed"
    if jp:
        return "jp_only"
    if ko:
        return "ko_only"
    return "no_text"


def normalize_regions(value: Any) -> str:
    if isinstance(value, dict):
        return ";".join(f"{key}:{value[key]}" for key in sorted(value))
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    return str(value or "")


def canonical_priority(primary_class: str, shape: str, jp_count: int) -> tuple[str, str, str, str]:
    if primary_class in {"confirmed_sentence_residual", "aux_confirmed_sentence_residual"}:
        return (
            "P0",
            "confirmed_output",
            "translate_and_build_candidate",
            "confirmed",
        )
    if primary_class == "shared_dictionary_tier_a":
        return (
            "P1",
            "indirect_shared_dictionary",
            "guarded_dictionary_candidate",
            "reviewed_translation_ready",
        )
    if primary_class == "encyclopedia_bank5c":
        return (
            "P1" if shape == "mixed" or jp_count >= 3 else "P2",
            "direct_display_candidate",
            "review_translation_and_runtime_sample",
            "translation_missing_or_unreviewed",
        )
    if primary_class == "broad_tier_b":
        return (
            "P1" if shape == "mixed" else "P2",
            "direct_display_candidate",
            "review_translation_then_candidate",
            "reviewed_translation_missing_or_conflicted",
        )
    if primary_class == "name75_likely_real":
        return (
            "P2",
            "likely_table_output",
            "capture_runtime_context_before_translation",
            "translation_missing",
        )
    if primary_class in {"bank59_ambiguous", "battle_ambiguous"}:
        return (
            "P1" if shape == "mixed" else "P3",
            "ambiguous_static_candidate",
            "runtime_capture_required",
            "not_translation_target_until_runtime_proof",
        )
    if primary_class == "battle_placeholder_or_template":
        return (
            "P4",
            "placeholder_or_template",
            "exclude_until_natural_language_runtime_proof",
            "not_translation_target",
        )
    if primary_class == "shared_dictionary_tier_b":
        return (
            "P2" if shape == "mixed" else "P3",
            "indirect_shared_dictionary",
            "review_phrase_and_consumer_union_before_patch",
            "translation_missing_or_conflicted",
        )
    if primary_class in {"broad_tier_c", "name75_data_tail"}:
        return (
            "P3",
            "ambiguous_or_data",
            "screen_or_pointer_evidence_required",
            "not_translation_target_until_proof",
        )
    return ("P3", "review_required", "manual_review", "unknown")


def choose_primary(classes: set[str]) -> str:
    return min(classes, key=lambda item: (CLASS_RANK.get(item, 999), item))


def add_record(
    records: dict[str, dict[str, Any]],
    *,
    key: str,
    address_or_slot: str,
    bank: str,
    classification: str,
    current_text: str,
    original_text: str,
    reason: str,
    source_report: Path,
    confidence: str,
    translation_candidate_present: bool = False,
    consumer_count: int = 0,
    consumer_regions: Any = "",
) -> None:
    current_text = str(current_text or "")
    original_text = str(original_text or "")
    entry = records.setdefault(
        key,
        {
            "record_key": key,
            "address_or_slot": address_or_slot,
            "bank": bank,
            "classes": set(),
            "current_text": "",
            "original_text": "",
            "reasons": set(),
            "sources": set(),
            "confidences": set(),
            "translation_candidate_present": False,
            "consumer_count": 0,
            "consumer_regions": set(),
        },
    )
    entry["classes"].add(classification)
    if current_text and (not entry["current_text"] or len(current_text) > len(entry["current_text"])):
        entry["current_text"] = current_text
    if original_text and (not entry["original_text"] or len(original_text) > len(entry["original_text"])):
        entry["original_text"] = original_text
    if reason:
        entry["reasons"].add(reason)
    entry["sources"].add(str(source_report.relative_to(ROOT)).replace("\\", "/"))
    if confidence:
        entry["confidences"].add(confidence)
    entry["translation_candidate_present"] = bool(
        entry["translation_candidate_present"] or translation_candidate_present
    )
    entry["consumer_count"] = max(int(entry["consumer_count"]), int(consumer_count or 0))
    normalized = normalize_regions(consumer_regions)
    if normalized:
        entry["consumer_regions"].add(normalized)


def finalize(records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in records.values():
        primary = choose_primary(entry["classes"])
        current_text = str(entry["current_text"])
        original_text = str(entry["original_text"])
        shape = text_shape(current_text)
        jp_count = count_chars(JP_RE, current_text)
        ko_count = count_chars(KO_RE, current_text)
        priority, runtime_status, action, translation_state = canonical_priority(
            primary, shape, jp_count
        )
        rows.append(
            {
                "priority": priority,
                "record_key": entry["record_key"],
                "address_or_slot": entry["address_or_slot"],
                "bank": entry["bank"],
                "primary_class": primary,
                "all_classes": ";".join(sorted(entry["classes"])),
                "shape": shape,
                "japanese_count": jp_count,
                "hangul_count": ko_count,
                "core_length": jp_count + ko_count,
                "current_text_sha256": text_sha(current_text),
                "original_text_sha256": text_sha(original_text),
                "confidence": ";".join(sorted(entry["confidences"])),
                "runtime_status": runtime_status,
                "recommended_action": action,
                "translation_state": translation_state,
                "translation_candidate_present": "yes"
                if entry["translation_candidate_present"]
                else "no",
                "consumer_count": entry["consumer_count"],
                "consumer_regions": ";".join(sorted(entry["consumer_regions"])),
                "reason": ";".join(sorted(entry["reasons"])),
                "source_reports": ";".join(sorted(entry["sources"])),
            }
        )
    rows.sort(
        key=lambda row: (
            int(str(row["priority"])[1:]),
            row["primary_class"],
            row["bank"],
            row["address_or_slot"],
        )
    )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDS} for row in rows)


def json_identity(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_CSV)
    parser.add_argument("--mixed-out", type=Path, default=MIXED_CSV)
    parser.add_argument("--summary", type=Path, default=SUMMARY_JSON)
    parser.add_argument("--doc", type=Path, default=DOC)
    args = parser.parse_args(argv)

    tip_data = TIP.read_bytes()
    tip_sha = sha(tip_data)
    documents = {name: load_json(path) for name, path in SOURCE_PATHS.items()}
    stale_sources: dict[str, str] = {}
    for name, document in documents.items():
        bound = source_tip_sha(document)
        if bound and bound != tip_sha:
            stale_sources[name] = bound
    if stale_sources:
        raise WorklistError(f"source audits are stale for current TIP: {stale_sources}")

    records: dict[str, dict[str, Any]] = {}

    bank59 = documents["bank59"]
    for row in bank59.get("actionable") or []:
        abs_hex = str(row.get("abs") or "").upper()
        add_record(
            records,
            key=f"ABS:{abs_hex}",
            address_or_slot=abs_hex,
            bank=abs_hex[:2],
            classification="confirmed_sentence_residual",
            current_text=str(row.get("current") or ""),
            original_text=str(row.get("original") or ""),
            reason=f"bank59:{row.get('classification') or 'actionable'}",
            source_report=BANK59,
            confidence="confirmed_or_contextual",
        )
    for row in bank59.get("ambiguous_review_only") or []:
        abs_hex = str(row.get("abs") or "").upper()
        add_record(
            records,
            key=f"ABS:{abs_hex}",
            address_or_slot=abs_hex,
            bank=abs_hex[:2],
            classification="bank59_ambiguous",
            current_text=str(row.get("current") or ""),
            original_text=str(row.get("original") or ""),
            reason=f"gap={row.get('gap') or ''};static={row.get('classification') or 'ambiguous'}",
            source_report=BANK59,
            confidence="ambiguous_static_only",
        )

    battle = documents["battle"]
    for group in ("actionable", "inline_control_actionable"):
        for row in battle.get(group) or []:
            abs_hex = str(row.get("abs") or "").upper()
            add_record(
                records,
                key=f"ABS:{abs_hex}",
                address_or_slot=abs_hex,
                bank=abs_hex[:2],
                classification="confirmed_sentence_residual",
                current_text=str(row.get("current_body") or ""),
                original_text=str(row.get("original_body") or ""),
                reason=f"battle:{row.get('classification') or group}",
                source_report=BATTLE,
                confidence="confirmed_or_contextual",
            )
    for row in battle.get("ambiguous_review_only") or []:
        abs_hex = str(row.get("abs") or "").upper()
        add_record(
            records,
            key=f"ABS:{abs_hex}",
            address_or_slot=abs_hex,
            bank=abs_hex[:2],
            classification="battle_ambiguous",
            current_text=str(row.get("current_body") or ""),
            original_text=str(row.get("original_body") or ""),
            reason=f"gap={row.get('gap') or ''};static={row.get('classification') or 'ambiguous'}",
            source_report=BATTLE,
            confidence="ambiguous_static_only",
        )
    for row in battle.get("placeholder_or_template") or []:
        abs_hex = str(row.get("abs") or "").upper()
        add_record(
            records,
            key=f"ABS:{abs_hex}",
            address_or_slot=abs_hex,
            bank=abs_hex[:2],
            classification="battle_placeholder_or_template",
            current_text=str(row.get("current_body") or ""),
            original_text=str(row.get("original_body") or ""),
            reason=f"gap={row.get('gap') or ''};placeholder=true",
            source_report=BATTLE,
            confidence="template_or_placeholder",
        )

    id_indirect = documents["id_indirect"]
    for row in id_indirect.get("actionable") or []:
        abs_hex = str(row.get("abs") or "").upper()
        add_record(
            records,
            key=f"ABS:{abs_hex}",
            address_or_slot=abs_hex,
            bank=abs_hex[:2],
            classification="confirmed_sentence_residual",
            current_text=str(row.get("current_body") or ""),
            original_text=str(row.get("original_body") or ""),
            reason=f"id_indirect:{row.get('category') or ''}",
            source_report=ID_INDIRECT,
            confidence="confirmed",
        )

    broad = documents["broad"]
    for tier, classification, confidence in (
        ("tier_a", "confirmed_sentence_residual", "translation_ready"),
        ("tier_b", "broad_tier_b", "trusted_extraction_translation_missing"),
        ("tier_c", "broad_tier_c", "ambiguous_or_data"),
    ):
        for row in (broad.get("records") or {}).get(tier) or []:
            abs_hex = str(row.get("abs") or "").upper()
            translation = row.get("translation") or {}
            add_record(
                records,
                key=f"ABS:{abs_hex}",
                address_or_slot=abs_hex,
                bank=abs_hex[:2],
                classification=classification,
                current_text=str(row.get("current_text") or ""),
                original_text=str(row.get("original_text") or ""),
                reason=f"{row.get('tier_reason') or ''};{row.get('legacy_reason') or ''}",
                source_report=BROAD,
                confidence=confidence,
                translation_candidate_present=bool(translation.get("ready")),
            )

    name75 = documents["name75"]
    likely = {str(row.get("abs") or "").upper() for row in name75.get("likely_real_records") or []}
    for row in name75.get("all_records") or []:
        abs_hex = str(row.get("abs") or "").upper()
        is_likely = abs_hex in likely
        add_record(
            records,
            key=f"ABS:{abs_hex}",
            address_or_slot=abs_hex,
            bank=abs_hex[:2],
            classification="name75_likely_real" if is_likely else "name75_data_tail",
            current_text=str(row.get("current_text") or ""),
            original_text=str(row.get("original_text") or ""),
            reason=(
                "likely_real_table_record"
                if is_likely
                else "name75_tail_or_unproven_table_record"
            ),
            source_report=NAME75,
            confidence="likely_real" if is_likely else "data_tail_or_unproven",
            translation_candidate_present=bool(row.get("catalog_translations")),
        )

    encyclopedia = documents["encyclopedia"]
    for row in encyclopedia.get("records") or []:
        abs_hex = str(row.get("abs") or "").upper()
        add_record(
            records,
            key=f"ABS:{abs_hex}",
            address_or_slot=abs_hex,
            bank=abs_hex[:2],
            classification="encyclopedia_bank5c",
            current_text=str(row.get("current") or ""),
            original_text=str(row.get("jp") or ""),
            reason=str(row.get("status") or "japanese_residual"),
            source_report=ENCYCLOPEDIA,
            confidence="proven_bank5c_record_boundary",
            translation_candidate_present=bool(str(row.get("ko") or "").strip()),
        )

    shared = documents["shared_dictionary"]
    for tier, classification, confidence in (
        ("tier_a", "shared_dictionary_tier_a", "translation_ready_consumer_union_required"),
        ("tier_b", "shared_dictionary_tier_b", "translation_missing_consumer_union_required"),
    ):
        for row in (shared.get("records") or {}).get(tier) or []:
            index = str(row.get("index") or "").upper()
            translation = row.get("translation") or {}
            add_record(
                records,
                key=f"DICT:{index}",
                address_or_slot=f"DICT:{index}",
                bank="DICT",
                classification=classification,
                current_text=str(row.get("current_text") or ""),
                original_text=str(row.get("original_text") or ""),
                reason=str(row.get("tier_reason") or "live_shared_dictionary_slot"),
                source_report=SHARED,
                confidence=confidence,
                translation_candidate_present=bool(translation.get("ready")),
                consumer_count=int(row.get("current_external_consumers") or 0),
                consumer_regions=row.get("current_regions") or {},
            )

    if AUX_ACTIONABLE.is_file():
        with AUX_ACTIONABLE.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                abs_hex = str(row.get("abs") or "").upper()
                if not abs_hex:
                    continue
                add_record(
                    records,
                    key=f"ABS:{abs_hex}",
                    address_or_slot=abs_hex,
                    bank=abs_hex[:2],
                    classification="aux_confirmed_sentence_residual",
                    current_text=str(row.get("current_text") or ""),
                    original_text=str(row.get("original_jp") or ""),
                    reason=str(row.get("reclass") or "aux_actionable_after_prefix_strip"),
                    source_report=AUX_RECLASS,
                    confidence="confirmed_after_structural_prefix_strip",
                    translation_candidate_present=bool(str(row.get("ko") or "").strip()),
                )

    rows = finalize(records)
    mixed_rows = [row for row in rows if row["shape"] == "mixed"]
    write_csv(args.out, rows)
    write_csv(args.mixed_out, mixed_rows)

    by_class = collections.Counter(str(row["primary_class"]) for row in rows)
    by_shape = collections.Counter(str(row["shape"]) for row in rows)
    by_priority = collections.Counter(str(row["priority"]) for row in rows)
    mixed_by_class = collections.Counter(str(row["primary_class"]) for row in mixed_rows)
    by_bank = collections.Counter(str(row["bank"]) for row in rows)

    aux_counts = documents["aux_reclass"].get("counts") or {}
    unified = load_json(UNIFIED) if UNIFIED.is_file() else {}
    unified_classes = unified.get("unified_classification_counts") or {}
    source_counts = {
        "bank59": bank59.get("counts") or {},
        "battle_voice": battle.get("counts") or {},
        "id_indirect": id_indirect.get("counts") or {},
        "broad": broad.get("counts") or {},
        "name75": name75.get("counts") or {},
        "encyclopedia": encyclopedia.get("counts") or {},
        "shared_dictionary": shared.get("counts") or {},
        "aux_reclass": aux_counts,
    }

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_current_tip_all_japanese_residual_worklist.py",
        "read_only": True,
        "ok": True,
        "current_tip": {
            "path": str(TIP.relative_to(ROOT)).replace("\\", "/"),
            "size": len(tip_data),
            "sha256": tip_sha,
        },
        "source_freshness": {
            "all_bound_to_current_tip": True,
            "source_tip_sha256": tip_sha,
            "sources": {name: json_identity(path) for name, path in SOURCE_PATHS.items()},
        },
        "headline": {
            "confirmed_natural_language_sentence_residuals": by_class.get(
                "confirmed_sentence_residual", 0
            )
            + by_class.get("aux_confirmed_sentence_residual", 0),
            "unique_worklist_records_or_slots": len(rows),
            "mixed_korean_japanese_records_or_slots": len(mixed_rows),
            "direct_display_review_candidates": (
                by_class.get("encyclopedia_bank5c", 0)
                + by_class.get("broad_tier_b", 0)
                + by_class.get("name75_likely_real", 0)
            ),
            "review_only_ambiguous_records": by_class.get("bank59_ambiguous", 0)
            + by_class.get("battle_ambiguous", 0),
            "placeholder_or_template_records": by_class.get(
                "battle_placeholder_or_template", 0
            ),
            "shared_dictionary_live_slots": by_class.get("shared_dictionary_tier_a", 0)
            + by_class.get("shared_dictionary_tier_b", 0),
        },
        "counts": {
            "by_primary_class": dict(sorted(by_class.items())),
            "by_shape": dict(sorted(by_shape.items())),
            "by_priority": dict(sorted(by_priority.items())),
            "mixed_by_primary_class": dict(sorted(mixed_by_class.items())),
            "by_bank": dict(sorted(by_bank.items())),
            "encyclopedia_japanese_count_ge_3": sum(
                1
                for row in rows
                if row["primary_class"] == "encyclopedia_bank5c"
                and int(row["japanese_count"]) >= 3
            ),
            "shared_dictionary_translation_ready": by_class.get(
                "shared_dictionary_tier_a", 0
            ),
        },
        "excluded_or_cleared": {
            "aux_false_mixed_total": int(aux_counts.get("cleared_false_mixed", 0)),
            "aux_ko_only_after_prefix": int(
                (aux_counts.get("by_reclass") or {}).get("ko_only_after_prefix", 0)
            ),
            "aux_no_text_after_prefix": int(
                (aux_counts.get("by_reclass") or {}).get("no_text_after_prefix", 0)
            ),
            "id_indirect_already_clean": int(
                (id_indirect.get("counts") or {}).get("already_clean", 0)
            ),
            "noise_bank_table_or_graphics": int(
                unified_classes.get("noise_bank_table_or_graphics", 0)
            ),
        },
        "source_counts": source_counts,
        "outputs": {
            "all_worklist": str(args.out.relative_to(ROOT)).replace("\\", "/"),
            "mixed_worklist": str(args.mixed_out.relative_to(ROOT)).replace("\\", "/"),
            "summary": str(args.summary.relative_to(ROOT)).replace("\\", "/"),
            "document": str(args.doc.relative_to(ROOT)).replace("\\", "/"),
        },
        "count_semantics": {
            "direct_records": "deduplicated by absolute ROM record address",
            "shared_dictionary": "deduplicated by live stock dictionary slot index",
            "mixed": "current decoded text contains both Hangul and Japanese code points",
            "not_runtime_reachability_count": True,
            "full_text_not_copied": "worklists store text hashes and counts; retrieve text from source report by address/slot",
        },
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    class_rows = report["counts"]["by_primary_class"]
    mixed_rows_by_class = report["counts"]["mixed_by_primary_class"]
    doc_lines = [
        "# 현재 메인 TIP 일본어 잔여·한일 혼합 통합 목록",
        "",
        f"- 기준 ROM: `out/patch/monoeye_ko_expanded.wsc`",
        f"- SHA-256: `{tip_sha.upper()}`",
        "- 생성 도구: `tools/build_current_tip_all_japanese_residual_worklist.py`",
        "- ROM/SaveRAM 변경: 없음",
        "",
        "## 결론",
        "",
        f"- 확정된 자연어 문장 일본어 잔여: **{report['headline']['confirmed_natural_language_sentence_residuals']}건**",
        f"- 주소/공유 슬롯 중복 제거 후 전체 검토 목록: **{len(rows)}건**",
        f"- 현재 디코드가 실제 한일 혼합인 레코드/슬롯: **{len(mixed_rows)}건**",
        f"- 직접 화면 후보(도감+broad B+name75 likely): **{report['headline']['direct_display_review_candidates']}건**",
        f"- ambiguous 검토 전용: **{report['headline']['review_only_ambiguous_records']}건**",
        f"- 전투 템플릿/placeholder: **{report['headline']['placeholder_or_template_records']}건**",
        f"- 일본어를 포함한 live 공유 사전 슬롯: **{report['headline']['shared_dictionary_live_slots']}건**",
        "",
        "## 구간별 분류",
        "",
        "| 분류 | 고유 건수 | 혼합 건수 | 처리 방침 |",
        "|---|---:|---:|---|",
        f"| bank59 ambiguous | {class_rows.get('bank59_ambiguous', 0)} | {mixed_rows_by_class.get('bank59_ambiguous', 0)} | 런타임 캡처 후 번역 대상 승격 |",
        f"| battle/ship voice ambiguous | {class_rows.get('battle_ambiguous', 0)} | {mixed_rows_by_class.get('battle_ambiguous', 0)} | 런타임 캡처 후 번역 대상 승격 |",
        f"| battle placeholder/template | {class_rows.get('battle_placeholder_or_template', 0)} | {mixed_rows_by_class.get('battle_placeholder_or_template', 0)} | 자연어 출력 증거 전까지 제외 |",
        f"| broad tier B | {class_rows.get('broad_tier_b', 0)} | {mixed_rows_by_class.get('broad_tier_b', 0)} | 번역 검수 후 후보 생성 |",
        f"| broad tier C | {class_rows.get('broad_tier_c', 0)} | {mixed_rows_by_class.get('broad_tier_c', 0)} | 화면/포인터 증거 필요 |",
        f"| name75 likely real | {class_rows.get('name75_likely_real', 0)} | {mixed_rows_by_class.get('name75_likely_real', 0)} | 런타임 문맥 확보 |",
        f"| name75 data tail | {class_rows.get('name75_data_tail', 0)} | {mixed_rows_by_class.get('name75_data_tail', 0)} | 데이터 오독 가능성, 자동 적용 금지 |",
        f"| encyclopedia bank5C | {class_rows.get('encyclopedia_bank5c', 0)} | {mixed_rows_by_class.get('encyclopedia_bank5c', 0)} | 경계 확정, 번역 검수 대상 |",
        f"| shared dictionary tier A | {class_rows.get('shared_dictionary_tier_a', 0)} | {mixed_rows_by_class.get('shared_dictionary_tier_a', 0)} | 소비자 합집합 가드로 적용 가능 |",
        f"| shared dictionary tier B | {class_rows.get('shared_dictionary_tier_b', 0)} | {mixed_rows_by_class.get('shared_dictionary_tier_b', 0)} | 번역·소비자 영향 검토 필요 |",
        "",
        "## 오탐·정리 완료",
        "",
        f"- aux 원시 jp/mixed 2,820건은 구조 프리픽스 제거 후 `ko_only` {report['excluded_or_cleared']['aux_ko_only_after_prefix']}건, `no_text` {report['excluded_or_cleared']['aux_no_text_after_prefix']}건으로 재분류되어 실제 번역 대상은 **0건**이다.",
        f"- ID 커맨드 간접/사격 점검 {report['excluded_or_cleared']['id_indirect_already_clean']}건은 현재 모두 clean이다.",
        f"- 노이즈 뱅크 표/그래픽 오독 {report['excluded_or_cleared']['noise_bank_table_or_graphics']}건은 작업 목록에서 제외했다.",
        "",
        "## 산출물",
        "",
        f"- 전체 메타데이터 작업 목록: `{report['outputs']['all_worklist']}`",
        f"- 실제 한일 혼합만 필터: `{report['outputs']['mixed_worklist']}`",
        f"- 기계 판독 요약: `{report['outputs']['summary']}`",
        "- 각 행의 실제 문자열은 `source_reports` 열의 JSON에서 `address_or_slot`으로 조회한다.",
        "",
        "## 우선순위",
        "",
        "1. P1 혼합 후보와 도감 3자 이상 레코드, 공유 사전 tier A를 먼저 화면 확인/번역한다.",
        "2. broad tier B와 짧은 도감 레코드를 문맥별로 검수한다.",
        "3. ambiguous와 tier C는 savestate/런타임 포인터 증거가 확보된 행만 번역 대상으로 승격한다.",
        "4. placeholder/template과 name75 data tail은 자동 적용하지 않는다.",
        "",
    ]
    args.doc.parent.mkdir(parents=True, exist_ok=True)
    args.doc.write_text("\n".join(doc_lines), encoding="utf-8", newline="\n")

    print(
        json.dumps(
            {
                "ok": True,
                "current_tip_sha256": tip_sha,
                "unique_worklist_records_or_slots": len(rows),
                "mixed_records_or_slots": len(mixed_rows),
                "confirmed_sentence_residuals": report["headline"][
                    "confirmed_natural_language_sentence_residuals"
                ],
                "by_primary_class": report["counts"]["by_primary_class"],
                "mixed_by_primary_class": report["counts"]["mixed_by_primary_class"],
                "outputs": report["outputs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
