#!/usr/bin/env python3
"""Reclassify live shared-dictionary JP slots with DICT_INVASION_GUARD labels.

Uses ``current_tip_remaining_shared_dictionary_japanese.json`` (Original+Working
consumer union already baked into ``current_regions``).  Does not rewrite ROM.

Hard rules mirrored from ``docs/DICT_INVASION_GUARD.md``:

* aux/name75 consumers → refuse sole/free rewrite (``allow_aux`` only for
  unanimous UI/repair/pair-steal style wholesale retarget);
* multi-region live slots are never treated as sole-owned;
* nested parents count as live consumers even without external zstring hits;
* tier-A rows with reviewed KO are ``guarded_*`` candidates, not free patches.
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

from mixed_residual_classification import (
    core_character_count,
    hangul_character_count,
    japanese_character_count,
)

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
AUDIT = ROOT / "out/patch/current_tip_remaining_shared_dictionary_japanese.json"
MASTER = ROOT / "out/script/shared_dictionary_reclass_sheet.csv"
ACTIONABLE = ROOT / "out/script/shared_dictionary_reclass_actionable.csv"
BATCH_DIR = ROOT / "out/script/shared_dictionary_reclass_batches"
REPORT = ROOT / "out/patch/shared_dictionary_reclass_report.json"

MAX_BATCH = 48

FIELDS = [
    "batch_id",
    "batch_order",
    "scope",
    "index",
    "audit_tier",
    "reclass",
    "guard_status",
    "shape",
    "original_jp",
    "current_text",
    "ko",
    "translation_ready",
    "translation_ambiguous",
    "translation_source",
    "review_status",
    "workflow_status",
    "japanese_count",
    "hangul_count",
    "core_count",
    "payload_bytes",
    "current_external_consumers",
    "original_external_consumers",
    "nested_parent_count",
    "regions",
    "aux_consumers",
    "script_consumers",
    "name75_consumers",
    "consumer_sample",
    "nested_parent_sample",
    "notes",
    "current_payload_hex",
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
            index = str(row.get("index") or "").strip().upper()
            if index:
                out[index] = dict(row)
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


def classify(row: dict[str, Any]) -> tuple[str, str, str]:
    """Return (reclass, guard_status, notes)."""
    regions = row.get("current_regions") or {}
    aux = int(regions.get("aux") or 0)
    script = int(regions.get("script") or 0)
    name75 = int(regions.get("name75") or 0)
    nested = int(row.get("nested_parent_count") or 0)
    external = int(row.get("current_external_consumers") or 0)
    tier = str(row.get("tier") or "")
    tier_reason = str(row.get("tier_reason") or "")
    current = str(row.get("current_text") or "")
    jp = japanese_character_count(current)
    hangul = hangul_character_count(current)
    core = core_character_count(current)
    translation = row.get("translation") or {}
    ready = bool(translation.get("ready"))
    ambiguous = bool(translation.get("ambiguous"))

    region_bits = []
    if aux:
        region_bits.append(f"aux={aux}")
    if script:
        region_bits.append(f"script={script}")
    if name75:
        region_bits.append(f"name75={name75}")
    if nested:
        region_bits.append(f"nested_parents={nested}")
    region_note = ", ".join(region_bits) or "no_external_region_hits"

    if name75:
        guard = "refuse_sole_rewrite_name75_live"
        base_note = f"name75 live; {region_note}"
        if tier == "A" and ready:
            return "guarded_tier_a_name75_blocked", guard, base_note + "; KO ready but name75 blocks sole rewrite"
        return "guard_blocked_name75_live", guard, base_note

    if aux:
        guard = "allow_aux_only_with_unanimous_retarget"
        base_note = f"aux live; {region_note}"
        if tier == "A" and ready and not ambiguous:
            if script:
                return (
                    "guarded_tier_a_allow_aux_multi_region",
                    guard,
                    base_note + "; reviewed KO; wholesale pointer retarget only",
                )
            return (
                "guarded_tier_a_allow_aux_ui_term",
                guard,
                base_note + "; reviewed KO; allow_aux_consumers UI/repair path",
            )
        if ambiguous:
            return "guard_blocked_aux_catalog_conflict", guard, base_note + "; catalog conflict"
        if hangul and jp:
            return "guard_blocked_aux_mixed_slot", guard, base_note + "; already mixed KO/JP"
        if jp <= 2 or core <= 2:
            return "guard_blocked_aux_short_particle", guard, base_note + "; short particle/function word"
        return "guard_blocked_aux_catalog_missing", guard, base_note + "; needs reviewed KO"

    if script:
        guard = "script_shared_not_free"
        base_note = f"script-only external; {region_note}"
        if ambiguous:
            return "script_shared_catalog_conflict", guard, base_note
        if ready:
            return "script_shared_ko_ready_review", guard, base_note + "; KO ready but verify all script consumers"
        if jp <= 2 or core <= 2:
            return "script_shared_short_particle", guard, base_note
        return "script_shared_catalog_missing", guard, base_note

    if nested or external == 0:
        guard = "nested_parent_live"
        base_note = f"no external region hit; {region_note}"
        if ready:
            return "nested_parent_ko_ready_review", guard, base_note
        if jp <= 2 or core <= 2:
            return "nested_parent_short_particle", guard, base_note
        return "nested_parent_catalog_missing", guard, base_note

    return "unclassified_shared_slot", "review", f"{tier_reason}; {region_note}"


def actionable_reclass(reclass: str) -> bool:
    return reclass.startswith("guarded_tier_a_")


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


def consumer_sample_text(row: dict[str, Any]) -> str:
    parts = []
    for item in (row.get("consumer_sample") or [])[:6]:
        parts.append(f"{item.get('abs')}/{item.get('region')}")
    return ";".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--audit", type=Path, default=AUDIT)
    parser.add_argument("--out", type=Path, default=MASTER)
    parser.add_argument("--actionable-out", type=Path, default=ACTIONABLE)
    parser.add_argument("--batch-dir", type=Path, default=BATCH_DIR)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)

    tip = args.tip.read_bytes()
    tip_sha = sha(tip)
    audit = load_json(args.audit)
    audit_tip = str(((audit.get("inputs") or {}).get("tip") or {}).get("sha256") or "").lower()
    if audit_tip and audit_tip != tip_sha.lower():
        raise SheetError(f"audit tip SHA {audit_tip} != current tip {tip_sha}")

    prior = existing_edits(args.out)
    source_rows = list((audit.get("records") or {}).get("tier_a") or []) + list(
        (audit.get("records") or {}).get("tier_b") or []
    )
    if not source_rows:
        raise SheetError("shared dictionary audit has no records")

    built: list[dict[str, Any]] = []
    for source in sorted(source_rows, key=lambda row: int(row.get("index_int") or int(str(row["index"]), 16))):
        index = str(source["index"]).upper()
        current = str(source.get("current_text") or "")
        original = str(source.get("original_text") or "")
        translation = source.get("translation") or {}
        regions = source.get("current_regions") or {}
        reclass, guard_status, notes = classify(source)
        prior_row = prior.get(index) or {}
        ready = bool(translation.get("ready"))
        ambiguous = bool(translation.get("ambiguous"))
        ko = str(translation.get("ko") or prior_row.get("ko") or "")
        evidence = translation.get("evidence") or []
        source_label = ""
        if evidence and isinstance(evidence, list) and isinstance(evidence[0], dict):
            source_label = str(evidence[0].get("source") or "")
        is_actionable = actionable_reclass(reclass)
        if is_actionable:
            workflow = str(prior_row.get("workflow_status") or "pending_guarded_allow_aux_retarget")
            review = str(prior_row.get("review_status") or "unreviewed_guarded")
        else:
            workflow = "quarantine_invasion_guard"
            review = "quarantined"
            if not ready:
                ko = ""
        built.append(
            {
                "batch_id": "",
                "batch_order": "",
                "scope": "shared_dictionary_stock",
                "index": index,
                "audit_tier": str(source.get("tier") or ""),
                "reclass": reclass,
                "guard_status": guard_status,
                "shape": text_shape(current),
                "original_jp": original,
                "current_text": current,
                "ko": ko if (is_actionable or ready) else "",
                "translation_ready": "yes" if ready else "no",
                "translation_ambiguous": "yes" if ambiguous else "no",
                "translation_source": source_label,
                "review_status": review,
                "workflow_status": workflow,
                "japanese_count": japanese_character_count(current),
                "hangul_count": hangul_character_count(current),
                "core_count": core_character_count(current),
                "payload_bytes": int(source.get("current_payload_bytes") or 0),
                "current_external_consumers": int(source.get("current_external_consumers") or 0),
                "original_external_consumers": int(source.get("original_external_consumers") or 0),
                "nested_parent_count": int(source.get("nested_parent_count") or 0),
                "regions": ",".join(f"{key}:{regions[key]}" for key in sorted(regions)),
                "aux_consumers": int(regions.get("aux") or 0),
                "script_consumers": int(regions.get("script") or 0),
                "name75_consumers": int(regions.get("name75") or 0),
                "consumer_sample": consumer_sample_text(source),
                "nested_parent_sample": ",".join(str(x) for x in (source.get("nested_parent_sample") or [])[:12]),
                "notes": str(prior_row.get("notes") or notes),
                "current_payload_hex": str(source.get("current_payload_hex") or ""),
                "parent_tip_sha256": tip_sha,
                "_actionable": is_actionable,
            }
        )

    actionable_rows = [row for row in built if row["_actionable"]]
    quarantine_rows = [row for row in built if not row["_actionable"]]

    for order, row in enumerate(quarantine_rows, start=1):
        row["batch_id"] = "QRN"
        row["batch_order"] = str(order)
    for batch_id, rows in pack_batches(actionable_rows, "A"):
        for order, row in enumerate(rows, start=1):
            row["batch_id"] = batch_id
            row["batch_order"] = str(order)

    ordered = sorted(built, key=lambda row: int(row["index"], 16))
    for row in ordered:
        row.pop("_actionable", None)

    write_csv(args.out, ordered)
    write_csv(args.actionable_out, [row for row in ordered if str(row["workflow_status"]).startswith("pending_guarded")])

    args.batch_dir.mkdir(parents=True, exist_ok=True)
    for old in args.batch_dir.glob("*.csv"):
        old.unlink()
    by_batch: collections.OrderedDict[str, list[dict[str, Any]]] = collections.OrderedDict()
    for row in ordered:
        by_batch.setdefault(str(row["batch_id"]), []).append(row)
    for batch_id, rows in by_batch.items():
        write_csv(args.batch_dir / f"{batch_id}.csv", rows)

    class_counts = collections.Counter(str(row["reclass"]) for row in ordered)
    guard_counts = collections.Counter(str(row["guard_status"]) for row in ordered)
    tier_counts = collections.Counter(str(row["audit_tier"]) for row in ordered)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_shared_dictionary_reclass_sheet.py",
        "read_only": True,
        "ok": True,
        "current_tip": identity(args.tip, tip),
        "inputs": {"audit": identity(args.audit)},
        "guard_policy": {
            "regions": ["script", "name75", "aux"],
            "aux_name75": "refuse sole/free rewrite",
            "tier_a_path": "allow_aux_consumers only with unanimous Original+Working retarget",
            "reference": "docs/DICT_INVASION_GUARD.md",
        },
        "counts": {
            "sheet_rows": len(ordered),
            "actionable_guarded_tier_a": len(actionable_rows),
            "quarantine": len(quarantine_rows),
            "by_audit_tier": dict(sorted(tier_counts.items())),
            "by_reclass": dict(sorted(class_counts.items())),
            "by_guard_status": dict(sorted(guard_counts.items())),
        },
        "outputs": {
            "master": str(args.out.relative_to(ROOT)).replace("\\", "/"),
            "actionable": str(args.actionable_out.relative_to(ROOT)).replace("\\", "/"),
            "batches": str(args.batch_dir.relative_to(ROOT)).replace("\\", "/"),
            "batch_ids": list(by_batch),
        },
        "interpretation": {
            "headline": (
                "Shared JP stock slots are almost all invasion-guard blocked for sole rewrite; "
                "only tier-A reviewed UI terms may proceed via allow_aux unanimous retarget."
            ),
            "next_step": (
                "Build/refresh shared_dictionary cleanup candidate from actionable sheet with "
                "guard_slot_writes(allow_aux_consumers=True), then user visual check before TIP promotion."
            ),
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
