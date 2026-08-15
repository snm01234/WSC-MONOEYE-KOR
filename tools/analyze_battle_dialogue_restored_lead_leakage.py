#!/usr/bin/env python3
"""Audit Japanese-visible leads restored by the battle-dialogue structure repair.

The structure repair correctly restored many 5D/5E speaker/portrait identifiers,
but the old safe snapshot does not prove that *every* first code unit is metadata.
Some records start directly with visible text.  Restoring such a unit before the
already-Korean E5 18 body recreates a mixed line such as ``撃으랴앗！`` or
``この정도의 ...``.

Read-only with respect to ROM/SaveRAM.  Writes JSON/CSV analysis only.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_aux_false_prefix_cleanup import MANUAL_CONTROL_ABS
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Dictionary, Tbl, load_rom

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
INVENTORY = ROOT / "out/script/battle_dialogue_structure_inventory.csv"
AUX_REPORT = ROOT / "out/patch/aux_ko_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_JSON = ROOT / "out/patch/battle_dialogue_restored_lead_leakage_analysis.json"
OUT_CSV = ROOT / "out/script/battle_dialogue_restored_lead_leakage_candidates.csv"
EXPECTED_TIP = "bac5e179ae496dd2b70912da0b1987b2dc6f7551e9f4d9de2d48c8c2152f7c88"
ANCHORS = {
    0x5D0C39: "screen_死죽어서버리는",
    0x5D11C6: "screen_ダ안돼",
    0x5D1449: "screen_艦을가까이붙여라",
    0x5D5D58: "screen_この정도",
    0x5EBB7A: "screen_撃으랴앗",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def has_japanese(text: str) -> bool:
    return any("\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff" for ch in text)


def has_korean(text: str) -> bool:
    return any("\uac00" <= ch <= "\ud7a3" for ch in text)


def load_inventory() -> tuple[list[dict[str, str]], dict[int, dict[str, str]]]:
    with INVENTORY.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows, {int(row["record_start"], 16): row for row in rows}


def main() -> int:
    tip = TIP.read_bytes()
    original = ORIGINAL.read_bytes()
    if sha(tip) != EXPECTED_TIP:
        raise SystemExit(f"TIP identity drifted: {sha(tip)}")
    tbl = Tbl.load(TBL_PATH)
    od = Dictionary(original)
    cd = make_dictionary_ext3(tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    rows, by_abs = load_inventory()
    repaired = {int(row["record_start"], 16) for row in rows if row.get("action") == "repair"}

    aux = json.loads(AUX_REPORT.read_text(encoding="utf-8"))
    proven_controls = {
        int(row["abs"], 16)
        for row in aux.get("applied") or []
        if row.get("bank") in ("5D", "5E") and int(row.get("prefix_bytes") or 0) > 0
    }

    candidates: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for logical in sorted(repaired):
        inv = by_abs[logical]
        lead = bytes.fromhex(inv.get("metadata_hex") or "")
        prefix = bytes.fromhex(inv.get("prefix_hex") or "")
        body_original = bytes.fromhex(inv["body_hex_original"])
        if not lead:
            counts["repair_without_metadata"] += 1
            continue
        try:
            lead_text = od.expand(lead, tbl)
            original_full = od.expand(lead + prefix + body_original, tbl)
            original_body = od.expand(body_original, tbl)
        except Exception:
            counts["decode_failure"] += 1
            continue
        current_body = inv["current_render"]
        if not (has_japanese(lead_text) and has_korean(current_body) and not has_japanese(current_body)):
            counts["not_visible_jp_plus_clean_ko_shape"] += 1
            continue

        prev_addr = int(inv["previous_record"], 16) if inv.get("previous_record") else None
        next_addr = int(inv["next_record"], 16) if inv.get("next_record") else None
        prev_meta = by_abs.get(prev_addr, {}).get("metadata_hex", "") if prev_addr is not None else ""
        next_meta = by_abs.get(next_addr, {}).get("metadata_hex", "") if next_addr is not None else ""
        sandwich = bool(prev_meta and next_meta and prev_meta == next_meta and prev_meta != inv["metadata_hex"])

        neighbor_metadata: list[str] = []
        cursor = prev_addr
        for _ in range(3):
            if cursor is None or cursor not in by_abs:
                break
            neighbor = by_abs[cursor]
            if neighbor.get("metadata_hex"):
                neighbor_metadata.append(neighbor["metadata_hex"])
            cursor = int(neighbor["previous_record"], 16) if neighbor.get("previous_record") else None
        cursor = next_addr
        for _ in range(3):
            if cursor is None or cursor not in by_abs:
                break
            neighbor = by_abs[cursor]
            if neighbor.get("metadata_hex"):
                neighbor_metadata.append(neighbor["metadata_hex"])
            cursor = int(neighbor["next_record"], 16) if neighbor.get("next_record") else None
        neighbor_counts = Counter(neighbor_metadata)
        dominant_neighbor_meta, dominant_neighbor_count = (neighbor_counts.most_common(1)[0] if neighbor_counts else ("", 0))
        local_dominant_other = bool(dominant_neighbor_count >= 2 and dominant_neighbor_meta != inv["metadata_hex"])

        proven = logical in proven_controls
        manual = logical in MANUAL_CONTROL_ABS
        if logical in ANCHORS:
            classification = "runtime_screen_proven_visible_text"
        elif proven:
            classification = "proven_control_metadata"
        elif manual:
            classification = "manual_reviewed_control_metadata"
        elif len(lead) > 1:
            classification = "high_confidence_multibyte_visible_text_lead"
        elif sandwich:
            classification = "high_confidence_visible_text_sandwiched"
        elif local_dominant_other:
            classification = "likely_visible_text_local_metadata_outlier"
        else:
            classification = "restored_lead_review"
        counts[classification] += 1

        candidates.append(
            {
                "abs": f"{logical:06X}",
                "bank": f"{logical >> 16:02X}",
                "classification": classification,
                "screen_anchor": ANCHORS.get(logical, ""),
                "lead_hex": lead.hex().upper(),
                "lead_text": lead_text,
                "lead_frequency_in_block": "",
                "previous_record": inv.get("previous_record", ""),
                "previous_metadata_hex": prev_meta,
                "next_record": inv.get("next_record", ""),
                "next_metadata_hex": next_meta,
                "sandwiched_by_same_other_metadata": sandwich,
                "dominant_neighbor_metadata_hex": dominant_neighbor_meta,
                "dominant_neighbor_metadata_count": dominant_neighbor_count,
                "local_dominant_other_metadata": local_dominant_other,
                "original_full_text": original_full,
                "original_body_text_after_removed_lead": original_body,
                "current_korean_body": current_body,
                "candidate_payload_hex": inv.get("candidate_payload_hex", ""),
                "reason": (
                    "restored first code unit is likely visible source text, not metadata"
                    if classification.startswith("high_confidence") or classification.startswith("runtime_screen") or classification.startswith("likely_visible")
                    else "requires structural/manual review before changing the promoted TIP"
                ),
            }
        )

    anchor_rows = {int(row["abs"], 16): row for row in candidates if row.get("screen_anchor")}
    missing_anchors = sorted(set(ANCHORS) - set(anchor_rows))
    if missing_anchors:
        raise SystemExit("screen anchors missing: " + ", ".join(f"{x:06X}" for x in missing_anchors))

    fields = [
        "abs", "bank", "classification", "screen_anchor", "lead_hex", "lead_text",
        "lead_frequency_in_block", "previous_record", "previous_metadata_hex", "next_record",
        "next_metadata_hex", "sandwiched_by_same_other_metadata", "dominant_neighbor_metadata_hex",
        "dominant_neighbor_metadata_count", "local_dominant_other_metadata", "original_full_text",
        "original_body_text_after_removed_lead", "current_korean_body", "candidate_payload_hex", "reason",
    ]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(candidates)

    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_battle_dialogue_restored_lead_leakage.py",
        "read_only_rom": True,
        "ok": True,
        "tip_sha256": sha(tip),
        "cause": (
            "the structure repair used the safe snapshot first code unit as metadata; some records actually begin "
            "with visible text, so the restored Japanese unit is rendered before the already-Korean body token"
        ),
        "counts": {
            "repaired_records": len(repaired),
            "jp_lead_plus_clean_ko_body": len(candidates),
            **dict(sorted(counts.items())),
        },
        "screen_anchors": [anchor_rows[x] for x in sorted(ANCHORS)],
        "high_confidence_samples": [row for row in candidates if row["classification"] in {"runtime_screen_proven_visible_text", "high_confidence_multibyte_visible_text_lead", "high_confidence_visible_text_sandwiched", "likely_visible_text_local_metadata_outlier"}][:120],
        "outputs": {"csv": str(OUT_CSV.relative_to(ROOT)).replace("\\", "/")},
        "next_gate": (
            "do not bulk-delete restored leads; build a correction candidate only from runtime-proven or structurally "
            "high-confidence visible-text rows, while preserving dominant/proven speaker metadata"
        ),
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "counts": report["counts"], "anchors": report["screen_anchors"]}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
