#!/usr/bin/env python3
"""Build a current-TIP-bound review sheet for battle-voice placeholder templates.

These records contain Japanese ``セリフ`` / damage / capture template labels that
the battle-voice gap audit quarantined as ``placeholder_or_template``. The sheet
mirrors the ambiguous residual sheet format for triage and translation drafting.
ROM application still requires approved provenance and prefix-boundary review.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import read_encoded_z_safe, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
AUDIT = ROOT / "out/patch/current_tip_battle_voice_uncovered_residual_audit.json"
MASTER = ROOT / "out/script/battle_voice_placeholder_translation_sheet.csv"
BATCH_DIR = ROOT / "out/script/battle_voice_placeholder_batches"
MANIFEST = ROOT / "out/patch/battle_voice_placeholder_translation_manifest.json"
REPORT = ROOT / "out/patch/battle_voice_placeholder_translation_sheet_report.json"
MAX_BATCH_RECORDS = 64
EXPECTED_ROWS = 604
SPACE_RE = re.compile(r"[\s\u3000]+")
FRAGMENT_START_RE = re.compile(
    r"^[、。！？…っッぁぃぅぇぉゃゅょァィゥェォャュョ]"
    r"|^(?:が|を|は|に|の|と|も|へ|で|だ|ぞ|く|せ|け|っ！)"
)
FIELDS = [
    "batch_id", "batch_order", "scope", "bank", "gap", "abs", "priority",
    "runtime_evidence", "classification", "shape", "stub_class",
    "prefix_hex", "payload_capacity", "body_capacity",
    "original_jp", "original_display", "current_text", "current_display", "ko",
    "translation_source", "review_status", "workflow_status", "duplicate_group",
    "duplicate_count", "canonical_abs", "boundary_review_required", "notes",
    "current_payload_hex", "source_body_sha256", "parent_tip_sha256",
]


class SheetError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SheetError(f"JSON root must be object: {path}")
    return value


def read_z(rom: bytes, logical: int, limit: int = 256) -> bytes:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=limit)
    if got is None:
        raise SheetError(f"unterminated record at {logical:06X}")
    return bytes(got[0])


def normalize_source(text: str) -> str:
    return SPACE_RE.sub("", text.strip())


def display_text(text: str) -> str:
    return text.replace("<E62F>", " ⏎ ")


def stub_class(text: str) -> str:
    if "大ダメ" in text or "ダメ－ジ" in text or "ダメージ" in text:
        return "damage_template"
    if "射撃" in text:
        return "shoot_template"
    if "防御" in text:
        return "defend_template"
    if "捕獲" in text:
        return "capture_template"
    if "失敗" in text or "反撃" in text:
        return "counter_template"
    if "実行" in text:
        return "execute_template"
    if "兵器" in text:
        return "weapon_template"
    if "やられ" in text or "られ" in text:
        return "hit_reaction_template"
    if "セリフ" in text:
        return "serifu_template"
    return "other_template"


def priority_for(text: str, shape: str, count: int) -> str:
    kind = stub_class(text)
    if shape == "mixed":
        return "P1"
    if kind in {"damage_template", "shoot_template", "capture_template", "weapon_template"}:
        return "P2"
    if count >= 20:
        return "P3"
    return "P4"


def existing_edits() -> dict[str, dict[str, str]]:
    if not MASTER.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    with MASTER.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            address = str(row.get("abs") or "").upper()
            if address:
                out[address] = dict(row)
    return out


def rows_by_gap(rows: Iterable[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: collections.OrderedDict[str, list[dict[str, Any]]] = collections.OrderedDict()
    for row in sorted(rows, key=lambda item: int(str(item["abs"]), 16)):
        grouped.setdefault(str(row.get("gap") or "ungrouped"), []).append(row)
    return list(grouped.items())


def pack_groups(groups: list[tuple[str, list[dict[str, Any]]]]) -> list[list[dict[str, Any]]]:
    packed: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for _gap, rows in groups:
        if current and len(current) + len(rows) > MAX_BATCH_RECORDS:
            packed.append(current)
            current = []
        current.extend(rows)
        if len(current) >= MAX_BATCH_RECORDS:
            packed.append(current)
            current = []
    if current:
        packed.append(current)
    return packed


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    rom = MAIN.read_bytes()
    if len(rom) != 16_777_216:
        raise SheetError("main TIP size drifted")
    tip_sha = sha(rom)
    audit = load_object(AUDIT)
    if audit.get("ok") is not True:
        raise SheetError("battle voice audit did not pass")
    audit_tip = str((audit.get("tip") or {}).get("sha256") or "").lower()
    if audit_tip != tip_sha:
        raise SheetError(f"audit/main TIP mismatch: {audit_tip} != {tip_sha}")

    sources = [dict(row) for row in audit.get("placeholder_or_template") or []]
    sources.sort(key=lambda row: int(str(row["abs"]), 16))
    if len(sources) != EXPECTED_ROWS:
        raise SheetError(f"placeholder population drifted: {len(sources)} != {EXPECTED_ROWS}")
    if len({str(row["abs"]).upper() for row in sources}) != len(sources):
        raise SheetError("duplicate abs in placeholder residuals")

    source_counts = collections.Counter(normalize_source(str(row["original_body"])) for row in sources)
    canonical: dict[str, str] = {}
    group_ids: dict[str, str] = {}
    for index, key in enumerate(sorted(source_counts), start=1):
        group_ids[key] = f"PG{index:03d}"
    for row in sources:
        key = normalize_source(str(row["original_body"]))
        canonical.setdefault(key, str(row["abs"]).upper())

    previous = existing_edits()
    batches = pack_groups(rows_by_gap(sources))
    batch_by_abs: dict[str, tuple[str, int]] = {}
    for order, rows in enumerate(batches, start=1):
        batch_id = f"PH{order:03d}"
        for row in rows:
            batch_by_abs[str(row["abs"]).upper()] = (batch_id, order)

    out_rows: list[dict[str, str]] = []
    for source in sources:
        address = str(source["abs"]).upper()
        logical = int(address, 16)
        prefix_hex = str(source.get("prefix_hex") or "").replace(" ", "").upper()
        prefix = bytes.fromhex(prefix_hex)
        payload = read_z(rom, logical)
        if not payload.startswith(prefix) or len(payload) <= len(prefix):
            raise SheetError(f"prefix/body boundary drifted at {address}")
        body = payload[len(prefix) :]
        expected_capacity = int(source.get("body_capacity") or -1)
        if len(body) != expected_capacity:
            raise SheetError(f"body capacity drifted at {address}: {len(body)} != {expected_capacity}")

        original = str(source.get("original_body") or "")
        current = str(source.get("current_body") or "")
        shape = str(source.get("shape") or "")
        key = normalize_source(original)
        kind = stub_class(original)
        old = previous.get(address) or {}
        ko = str(old.get("ko") or "")
        translation_source = str(old.get("translation_source") or "")
        review_status = str(old.get("review_status") or "")
        workflow_status = str(old.get("workflow_status") or "pending_review")
        notes = str(old.get("notes") or "")
        note_bits = [notes] if notes else []
        note_bits.append("placeholder/template label; confirm whether visible at runtime before translating")
        if FRAGMENT_START_RE.search(original):
            note_bits.append("leading fragment: verify speaker-prefix boundary before ROM application")
        boundary_review = "yes" if FRAGMENT_START_RE.search(original) else "no"
        batch_id, batch_order = batch_by_abs[address]
        out_rows.append(
            {
                "batch_id": batch_id,
                "batch_order": str(batch_order),
                "scope": "battle_voice_placeholder",
                "bank": address[:2],
                "gap": str(source.get("gap") or ""),
                "abs": address,
                "priority": priority_for(original, shape, source_counts[key]),
                "runtime_evidence": str(old.get("runtime_evidence") or ""),
                "classification": "placeholder_or_template",
                "shape": shape,
                "stub_class": kind,
                "prefix_hex": prefix_hex,
                "payload_capacity": str(len(payload)),
                "body_capacity": str(len(body)),
                "original_jp": original,
                "original_display": display_text(original),
                "current_text": current,
                "current_display": display_text(current),
                "ko": ko,
                "translation_source": translation_source,
                "review_status": review_status,
                "workflow_status": workflow_status,
                "duplicate_group": group_ids[key],
                "duplicate_count": str(source_counts[key]),
                "canonical_abs": canonical[key],
                "boundary_review_required": boundary_review,
                "notes": "; ".join(dict.fromkeys(part for part in note_bits if part)),
                "current_payload_hex": payload.hex().upper(),
                "source_body_sha256": sha(body),
                "parent_tip_sha256": tip_sha,
            }
        )

    write_csv(MASTER, out_rows)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    by_batch: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in out_rows:
        by_batch[row["batch_id"]].append(row)
    for batch_id, rows in by_batch.items():
        write_csv(BATCH_DIR / f"{batch_id}.csv", rows)
    current_files = {f"{batch_id}.csv" for batch_id in by_batch}
    for stale in BATCH_DIR.glob("*.csv"):
        if stale.name not in current_files:
            stale.unlink()

    counts = {
        "records": len(out_rows),
        "unique_source_texts": len(source_counts),
        "duplicate_rows_beyond_canonical": sum(count - 1 for count in source_counts.values()),
        "duplicate_groups": sum(count > 1 for count in source_counts.values()),
        "banks": dict(collections.Counter(row["bank"] for row in out_rows)),
        "shapes": dict(collections.Counter(row["shape"] for row in out_rows)),
        "stub_classes": dict(collections.Counter(row["stub_class"] for row in out_rows)),
        "priorities": dict(collections.Counter(row["priority"] for row in out_rows)),
        "pending_review": sum(row["workflow_status"] == "pending_review" for row in out_rows),
        "with_ko": sum(bool(row["ko"]) for row in out_rows),
        "boundary_review_required": sum(row["boundary_review_required"] == "yes" for row in out_rows),
        "batches": len(by_batch),
    }
    top_texts = [{"text": text, "count": count} for text, count in source_counts.most_common(30)]
    manifest = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_voice_placeholder_translation_sheet.py",
        "read_only": True,
        "ok": True,
        "main_tip": {
            "path": str(MAIN.relative_to(ROOT)).replace("\\", "/"),
            "size": len(rom),
            "sha256": tip_sha,
        },
        "source_audit": str(AUDIT.relative_to(ROOT)).replace("\\", "/"),
        "policy": {
            "translation_target": "battle voice placeholder_or_template residuals (triage sheet)",
            "promotion_allowed_by_default": False,
            "preserve_prefix_until_boundary_review": True,
            "legacy_machine_translation_used": False,
            "required_for_promotion": [
                "runtime visibility proof or explicit template localization policy",
                "ko",
                "translation_source",
                "review_status=approved",
                "candidate-bound static audit",
            ],
        },
        "counts": counts,
        "top_source_texts": top_texts,
        "batches": [
            {
                "batch_id": batch_id,
                "order": int(batch_id[2:]),
                "records": len(rows),
                "banks": sorted({row["bank"] for row in rows}),
                "gaps": list(dict.fromkeys(row["gap"] for row in rows)),
                "sheet": f"out/script/battle_voice_placeholder_batches/{batch_id}.csv",
            }
            for batch_id, rows in sorted(by_batch.items())
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_voice_placeholder_translation_sheet.py",
        "ok": True,
        "master_sheet": str(MASTER.relative_to(ROOT)).replace("\\", "/"),
        "batch_directory": str(BATCH_DIR.relative_to(ROOT)).replace("\\", "/"),
        "manifest": str(MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "counts": counts,
        "top_source_texts": top_texts,
        "sample_rows": [
            {
                "abs": row["abs"],
                "priority": row["priority"],
                "stub_class": row["stub_class"],
                "original_jp": row["original_jp"],
            }
            for row in out_rows[:15]
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: report[k] for k in report if k != "sample_rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
