#!/usr/bin/env python3
"""Build a current-TIP-bound translation sheet for battle dialogue with E62F.

The battle voice gap audit used to reject every string containing a rendered
``<...>`` token.  Runtime evidence proved that ``<E62F>`` is an inline layout
separator inside visible dialogue, not evidence that the record is non-text.
This builder promotes every such record into a dedicated translation queue,
preserves the exact control-token count/order, groups exact duplicate sources,
and binds every row to the current TIP payload and body digest.

No ROM or SaveRAM is modified.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from monoeye_rom import read_encoded_z_safe, stock_base

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
AUDIT = ROOT / "out/patch/current_tip_battle_voice_uncovered_residual_audit.json"
MASTER = ROOT / "out/script/battle_voice_inline_control_translation_sheet.csv"
BATCH_DIR = ROOT / "out/script/battle_voice_inline_control_batches"
MANIFEST = ROOT / "out/patch/battle_voice_inline_control_translation_manifest.json"
REPORT = ROOT / "out/patch/battle_voice_inline_control_translation_sheet_report.json"
MAX_BATCH_RECORDS = 48
SCREEN_CONFIRMED = {
    "5DA6E5": {
        "evidence": "user_capture_20260804_235526",
        "draft_ko": "좋아、지금이다！<E62F>쏴라！！",
        "notes": "runtime-confirmed visible Japanese dialogue; exact screen text matched",
    }
}
FIELDS = [
    "batch_id", "batch_order", "scope", "bank", "gap", "abs", "priority",
    "runtime_evidence", "classification", "shape", "inline_control_tag",
    "inline_control_count", "prefix_hex", "payload_capacity", "body_capacity",
    "original_jp", "original_display", "current_text", "current_display", "ko",
    "translation_source", "review_status", "workflow_status", "duplicate_group",
    "duplicate_count", "canonical_abs", "boundary_review_required", "notes",
    "current_payload_hex", "source_body_sha256", "parent_tip_sha256",
]
SPACE_RE = re.compile(r"[\s\u3000]+")
FRAGMENT_START_RE = re.compile(r"^[、。！？…っッぁぃぅぇぉゃゅょァィゥェォャュョ]|^(?:が|を|は|に|の|と|も|へ|で|だ|ぞ|く|せ|け|っ！)")


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

    sources = [dict(row) for row in audit.get("inline_control_actionable") or []]
    if not sources:
        sources = [
            dict(row)
            for row in audit.get("actionable") or []
            if row.get("classification") == "inline_control_sentence"
        ]
    sources.sort(key=lambda row: int(str(row["abs"]), 16))
    if len(sources) != 268 or len({str(row["abs"]).upper() for row in sources}) != 268:
        raise SheetError(f"inline-control population drifted: {len(sources)} != 268")
    if any("<E62F>" not in str(row.get("original_body") or "") for row in sources):
        raise SheetError("inline-control source missing E62F")

    source_counts = collections.Counter(normalize_source(str(row["original_body"])) for row in sources)
    canonical: dict[str, str] = {}
    group_ids: dict[str, str] = {}
    for index, key in enumerate(sorted(source_counts), start=1):
        group_ids[key] = f"DG{index:03d}"
    for row in sources:
        key = normalize_source(str(row["original_body"]))
        canonical.setdefault(key, str(row["abs"]).upper())

    previous = existing_edits()
    batches = pack_groups(rows_by_gap(sources))
    batch_by_abs: dict[str, tuple[str, int]] = {}
    for order, rows in enumerate(batches, start=1):
        batch_id = f"IC{order:03d}"
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
        body = payload[len(prefix):]
        expected_capacity = int(source.get("body_capacity") or -1)
        if len(body) != expected_capacity:
            raise SheetError(f"body capacity drifted at {address}: {len(body)} != {expected_capacity}")

        original = str(source.get("original_body") or "")
        current = str(source.get("current_body") or "")
        control_count = original.count("<E62F>")
        if control_count not in {1, 2} or current.count("<E62F>") != control_count:
            raise SheetError(f"inline control count drifted at {address}")
        key = normalize_source(original)
        old = previous.get(address) or {}
        evidence = SCREEN_CONFIRMED.get(address) or {}
        seed_ko = str(evidence.get("draft_ko") or "")
        ko = str(old.get("ko") or seed_ko)
        translation_source = str(old.get("translation_source") or ("llm_from_user_capture" if seed_ko else ""))
        review_status = str(old.get("review_status") or ("unreviewed_draft" if seed_ko else ""))
        workflow_status = str(old.get("workflow_status") or ("draft_ready" if seed_ko else "pending_translation"))
        notes = str(old.get("notes") or evidence.get("notes") or "")
        if ko and ko.count("<E62F>") != control_count:
            raise SheetError(f"edited Korean control count differs at {address}")

        boundary_review = "yes" if FRAGMENT_START_RE.search(original) else "no"
        if boundary_review == "yes":
            notes = "; ".join(part for part in [notes, "leading fragment: verify speaker-prefix boundary before ROM application"] if part)
        batch_id, batch_order = batch_by_abs[address]
        out_rows.append({
            "batch_id": batch_id,
            "batch_order": str(batch_order),
            "scope": "battle_voice_inline_control",
            "bank": address[:2],
            "gap": str(source.get("gap") or ""),
            "abs": address,
            "priority": "P0" if address in SCREEN_CONFIRMED else "P1" if str(source.get("shape")) == "mixed" else "P2",
            "runtime_evidence": str(evidence.get("evidence") or ""),
            "classification": "inline_control_sentence",
            "shape": str(source.get("shape") or ""),
            "inline_control_tag": "E62F",
            "inline_control_count": str(control_count),
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
            "notes": notes,
            "current_payload_hex": payload.hex().upper(),
            "source_body_sha256": sha(body),
            "parent_tip_sha256": tip_sha,
        })

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
        "inline_control_occurrences": sum(int(row["inline_control_count"]) for row in out_rows),
        "multi_control_records": sum(int(row["inline_control_count"]) > 1 for row in out_rows),
        "screen_confirmed": sum(bool(row["runtime_evidence"]) for row in out_rows),
        "translation_prefilled_draft": sum(bool(row["ko"]) for row in out_rows),
        "pending_translation": sum(row["workflow_status"] == "pending_translation" for row in out_rows),
        "boundary_review_required": sum(row["boundary_review_required"] == "yes" for row in out_rows),
        "batches": len(by_batch),
    }
    manifest = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_voice_inline_control_translation_sheet.py",
        "read_only": True,
        "ok": True,
        "main_tip": {"path": str(MAIN.relative_to(ROOT)).replace("\\", "/"), "size": len(rom), "sha256": tip_sha},
        "source_audit": str(AUDIT.relative_to(ROOT)).replace("\\", "/"),
        "policy": {
            "translation_target": "all battle voice records containing visible inline E62F layout control",
            "preserve_control_tag_count_and_order": True,
            "preserve_prefix_until_boundary_review": True,
            "legacy_machine_translation_used": False,
            "required_for_promotion": ["ko", "translation_source", "review_status=approved", "candidate-bound static audit", "runtime sampling"],
        },
        "counts": counts,
        "batches": [
            {
                "batch_id": batch_id,
                "order": int(batch_id[2:]),
                "records": len(rows),
                "banks": sorted({row["bank"] for row in rows}),
                "gaps": list(dict.fromkeys(row["gap"] for row in rows)),
                "sheet": f"out/script/battle_voice_inline_control_batches/{batch_id}.csv",
            }
            for batch_id, rows in sorted(by_batch.items())
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_voice_inline_control_translation_sheet.py",
        "ok": True,
        "master_sheet": str(MASTER.relative_to(ROOT)).replace("\\", "/"),
        "batch_directory": str(BATCH_DIR.relative_to(ROOT)).replace("\\", "/"),
        "manifest": str(MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "counts": counts,
        "screen_confirmed_record": next(row for row in out_rows if row["abs"] == "5DA6E5"),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
