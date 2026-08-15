#!/usr/bin/env python3
"""Consolidate the current TIP untranslated-output audits without double counting."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
REVIEWED = ROOT / "out/patch/current_tip_reviewed_population_untranslated_audit.json"
BANK59 = ROOT / "out/patch/current_tip_bank59_uncovered_event_residual_audit.json"
BATTLE = ROOT / "out/patch/current_tip_battle_voice_uncovered_residual_audit.json"
ID_INDIRECT = ROOT / "out/patch/current_tip_id_indirect_command_residual_audit.json"
BROAD = ROOT / "out/patch/current_tip_broad_japanese_residuals_post_aux_promotion.json"
CANDIDATE = ROOT / "out/patch/next_stage_event_id_indirect_candidate.wsc"
CANDIDATE_AUDIT = ROOT / "out/patch/next_stage_event_id_indirect_candidate_audit.json"
OUT = ROOT / "out/patch/current_tip_untranslated_output_inventory.json"

EXPECTED_TIP = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
EXPECTED_CANDIDATE = "99ddfa32a81317e448b168fd4ae0a22b1dfbfd47542b26dfcda544e7e1b8b4ed"


class InventoryError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InventoryError(f"JSON root must be object: {path}")
    return value


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def addresses(rows: list[dict[str, Any]]) -> set[str]:
    return {str(row.get("abs") or "").upper() for row in rows}


def main() -> int:
    tip = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    if sha(tip) != EXPECTED_TIP:
        raise InventoryError("main TIP identity drifted")
    if sha(candidate) != EXPECTED_CANDIDATE:
        raise InventoryError("pending candidate identity drifted")

    reviewed = load(REVIEWED)
    bank59 = load(BANK59)
    battle = load(BATTLE)
    id_indirect = load(ID_INDIRECT)
    broad = load(BROAD)
    candidate_audit = load(CANDIDATE_AUDIT)
    if not all(
        document.get("ok") is True
        for document in (reviewed, bank59, battle, id_indirect, broad, candidate_audit)
    ):
        raise InventoryError("one or more source audits failed")

    reviewed_counts = reviewed.get("counts") or {}
    if int(reviewed_counts.get("meaningful_untranslated_records", -1)) != 0:
        raise InventoryError("reviewed population is no longer clean")
    if int(reviewed_counts.get("already_applied_aux_japanese_residuals", -1)) != 0:
        raise InventoryError("Japanese residual returned in reviewed aux population")

    bank59_rows = [dict(row) for row in bank59.get("actionable") or []]
    battle_rows = [dict(row) for row in battle.get("actionable") or []]
    id_rows = [dict(row) for row in id_indirect.get("actionable") or []]
    bank59_addresses = addresses(bank59_rows)
    battle_addresses = addresses(battle_rows)
    id_addresses = addresses(id_rows)
    if bank59_addresses & battle_addresses or bank59_addresses & id_addresses or battle_addresses & id_addresses:
        raise InventoryError("actionable populations overlap")

    actionable_addresses = bank59_addresses | battle_addresses | id_addresses
    if len(actionable_addresses) != len(bank59_rows) + len(battle_rows) + len(id_rows):
        raise InventoryError("duplicate actionable address detected")

    bank59_counts = bank59.get("counts") or {}
    battle_counts = battle.get("counts") or {}
    id_counts = id_indirect.get("counts") or {}
    actionable_total = len(actionable_addresses)
    pure_japanese = (
        int(bank59_counts.get("actionable_jp_only", 0))
        + int(battle_counts.get("actionable_jp_only", 0))
        + int(id_counts.get("residual_jp_only", 0))
    )
    mixed = (
        int(bank59_counts.get("actionable_mixed", 0))
        + int(battle_counts.get("actionable_mixed", 0))
        + int(id_counts.get("residual_mixed", 0))
    )
    if actionable_total != pure_japanese + mixed:
        raise InventoryError("shape totals do not match actionable total")

    candidate_rows = [dict(row) for row in candidate_audit.get("target_checks") or []]
    candidate_addresses = addresses(candidate_rows)
    if len(candidate_addresses) != 18 or not candidate_addresses <= actionable_addresses:
        raise InventoryError("pending candidate targets are not an exact actionable subset")
    if not all(row.get("ok") is True for row in candidate_rows):
        raise InventoryError("pending candidate target audit failed")

    broad_counts = broad.get("counts") or {}
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_current_tip_untranslated_output_inventory.py",
        "read_only": True,
        "ok": True,
        "count_semantics": {
            "unit": "static Original-derived output record",
            "not_unique_phrase_count": True,
            "not_runtime_reachability_count": True,
            "actionable_definition": "high-confidence or context-confirmed natural-language output body with Japanese remaining after control/speaker prefix handling",
            "review_only_excluded": "ambiguous data-like records and explicit voice templates are not included in actionable totals",
        },
        "current_tip": identity(TIP, tip),
        "reviewed_sheet_population": {
            "status": "clean",
            "meaningful_untranslated_records": int(reviewed_counts.get("meaningful_untranslated_records", 0)),
            "already_applied_aux_records_checked": int(reviewed_counts.get("already_applied_aux_clean", 0)),
            "japanese_residuals": int(reviewed_counts.get("already_applied_aux_japanese_residuals", 0)),
            "audit": str(REVIEWED.relative_to(ROOT)),
        },
        "actionable_current_tip": {
            "total_records": actionable_total,
            "pure_japanese_records": pure_japanese,
            "mixed_korean_japanese_records": mixed,
            "by_scope": {
                "bank59_uncovered_event_dialogue": {
                    "records": len(bank59_rows),
                    "confirmed": int(bank59_counts.get("confirmed_sentence", 0)),
                    "contextual": int(bank59_counts.get("contextual_sentence", 0)),
                    "pure_japanese": int(bank59_counts.get("actionable_jp_only", 0)),
                    "mixed": int(bank59_counts.get("actionable_mixed", 0)),
                    "audit": str(BANK59.relative_to(ROOT)),
                },
                "banks5d5e_uncovered_battle_and_ship_voice": {
                    "records": len(battle_rows),
                    "confirmed": int(battle_counts.get("confirmed_sentence", 0)),
                    "contextual": int(battle_counts.get("contextual_sentence", 0)),
                    "pure_japanese": int(battle_counts.get("actionable_jp_only", 0)),
                    "mixed": int(battle_counts.get("actionable_mixed", 0)),
                    "by_bank": battle_counts.get("by_bank") or {},
                    "audit": str(BATTLE.relative_to(ROOT)),
                },
                "id_command_indirect_and_shooting": {
                    "records": len(id_rows),
                    "pure_japanese": int(id_counts.get("residual_jp_only", 0)),
                    "mixed": int(id_counts.get("residual_mixed", 0)),
                    "by_category": id_counts.get("residual_by_category") or {},
                    "audit": str(ID_INDIRECT.relative_to(ROOT)),
                },
            },
        },
        "review_only_not_in_actionable_total": {
            "bank59_ambiguous": int(bank59_counts.get("ambiguous_review_only", 0)),
            "battle_voice_ambiguous": int(battle_counts.get("ambiguous_review_only", 0)),
            "battle_voice_placeholders_or_templates": int(battle_counts.get("placeholder_or_template", 0)),
            "broad_proven_surface_candidates": {
                "records": int(broad_counts.get("japanese_residual_records", 0)),
                "tier_b_translation_needed_or_conflicted": int(broad_counts.get("tier_b_translation_needed_or_conflicted", 0)),
                "tier_c_ambiguous_or_data": int(broad_counts.get("tier_c_ambiguous_or_data", 0)),
                "warning": "kept separate because these are short/ambiguous UI, name, or script fragments rather than confirmed displayed sentences",
                "audit": str(BROAD.relative_to(ROOT)),
            },
        },
        "pending_cumulative_candidate": {
            "candidate": identity(CANDIDATE, candidate),
            "targets": len(candidate_addresses),
            "target_failures": int((candidate_audit.get("counts") or {}).get("target_failures", -1)),
            "event_dialogue_targets": sum(row.get("phase") == "next_stage_event" for row in candidate_rows),
            "id_indirect_targets": sum(row.get("phase") == "id_indirect" for row in candidate_rows),
            "remaining_actionable_after_rom_promotion": actionable_total - len(candidate_addresses),
            "audit": str(CANDIDATE_AUDIT.relative_to(ROOT)),
            "status": "static_verified_pending_user_emulator_validation",
        },
        "checks": {
            "source_audits_ok": True,
            "reviewed_population_clean": True,
            "actionable_populations_disjoint": True,
            "actionable_total_exact": actionable_total == 1893,
            "shape_total_exact": pure_japanese == 1290 and mixed == 603,
            "pending_candidate_is_actionable_subset": True,
            "pending_candidate_targets_exactly_18": len(candidate_addresses) == 18,
        },
    }
    if not all(report["checks"].values()):
        raise InventoryError("consolidated inventory checks failed")
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "current_tip": report["current_tip"],
                "reviewed_sheet_population": report["reviewed_sheet_population"],
                "actionable_current_tip": report["actionable_current_tip"],
                "pending_cumulative_candidate": report["pending_cumulative_candidate"],
                "out": str(OUT.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
