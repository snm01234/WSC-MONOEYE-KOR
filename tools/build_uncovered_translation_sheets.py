#!/usr/bin/env python3
"""Build the master/per-batch translation sheets for uncovered output text.

The sheets are bound to the current promoted TIP by address, payload bytes and
body digest. Existing translator edits are preserved when the script is rerun.
No ROM or SaveRAM is modified.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import read_encoded_z_safe, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
EVENT_AUDIT = ROOT / "out/patch/current_tip_bank59_uncovered_event_residual_audit.json"
VOICE_AUDIT = ROOT / "out/patch/current_tip_battle_voice_uncovered_residual_audit.json"
UI_AUDIT = ROOT / "out/patch/current_tip_id_indirect_command_residual_audit.json"
EVENT_CATALOG = ROOT / "data/next_stage_bank59_gap_event_ko.json"
UI_CATALOG = ROOT / "data/id_indirect_ui_activation_ko.json"
MASTER = ROOT / "out/script/uncovered_translation_sheet.csv"
BATCH_DIR = ROOT / "out/script/uncovered_batches"
MANIFEST = ROOT / "out/patch/uncovered_translation_batch_manifest.json"
SUMMARY = ROOT / "out/patch/uncovered_translation_sheet_report.json"

MAIN_SHA256 = "898f3b820c6ce901d2efcb08cea32151264d3773780a6e39ec94c06354accf62"
CANDIDATE = ROOT / "out/patch/next_stage_event_id_indirect_candidate.wsc"
CANDIDATE_SHA256 = "99ddfa32a81317e448b168fd4ae0a22b1dfbfd47542b26dfcda544e7e1b8b4ed"
MAX_BATCH_RECORDS = 48

FIELDS = [
    "batch_id", "batch_order", "scope", "bank", "gap", "abs",
    "classification", "shape", "prefix_hex", "payload_capacity",
    "body_capacity", "original_jp", "current_text", "ko",
    "translation_source", "review_status", "workflow_status", "notes",
    "current_payload_hex", "source_body_sha256", "parent_tip_sha256",
]

E001_TRANSLATIONS = {
    "59265F": "……뭐야！？",
    "59266D": "바로　아래에　모빌　슈트！！",
    "592679": "전투가　벌어지고　있는　모양입니다！",
    "59269B": "전원　제１종　전투　배치！！",
    "5926B1": "드디어　몰아붙였다！",
    "5926CC": "오늘이야말로　네놈과　결판을　내고、",
    "5926DA": "데빌　건담을　쓰러뜨리겠다！！",
    "592700": "네놈　따위가　그분을　쓰러뜨릴　수　있겠느냐！",
    "592713": "보아라！！",
    "592730": "우리의　신、데빌　건담　님의　등장이시다！",
    "5927BF": "뭐지、저　모빌　슈트는？",
    "5927D7": "저건、모빌　파이터　아니야？",
    "5927EF": "알고　있는　건가！？",
    "5927FC": "경기용　모빌　슈트예요。",
    "59280C": "건담・파이트라는",
    "592819": "모빌　슈트　격투에　쓰는　기체인데……",
    "592832": "……혹시　에우고　분들인가요！？",
}


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_z(rom: bytes, logical: int, limit: int = 256) -> bytes:
    # Encoded strings may legitimately contain a 00 trail byte inside a
    # dictionary token (for example F1 00). A raw NUL scan truncates those
    # records, so bind sheets with the same token-aware reader used by audits.
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=limit)
    if got is None:
        raise ValueError(f"unterminated record at {logical:06X}")
    return bytes(got[0])


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


def pack_groups(groups: list[tuple[str, list[dict[str, Any]]]], prefix: str, start: int = 1) -> list[tuple[str, list[dict[str, Any]]]]:
    packed: list[tuple[str, list[dict[str, Any]]]] = []
    current: list[dict[str, Any]] = []
    index = start
    for _gap, rows in groups:
        if current and len(current) + len(rows) > MAX_BATCH_RECORDS:
            packed.append((f"{prefix}{index:03d}", current))
            index += 1
            current = []
        current.extend(rows)
        if len(current) >= MAX_BATCH_RECORDS:
            packed.append((f"{prefix}{index:03d}", current))
            index += 1
            current = []
    if current:
        packed.append((f"{prefix}{index:03d}", current))
    return packed


def rows_by_gap(rows: Iterable[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for row in sorted(rows, key=lambda item: int(str(item["abs"]), 16)):
        grouped.setdefault(str(row.get("gap") or "ungrouped"), []).append(row)
    return list(grouped.items())


def catalog_translations(path: Path) -> dict[str, str]:
    document = load_object(path)
    values: dict[str, str] = {}
    for key in ("records", "lines"):
        for row in document.get(key) or []:
            address = str(row.get("abs") or "").upper()
            ko = str(row.get("ko") or "").strip()
            if address and ko:
                values[address] = ko
    return values


def bind_current_payloads(rom: bytes, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    bound: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        address = str(row["abs"]).upper()
        prefix_hex = str(row.get("prefix_hex") or "").replace(" ", "").upper()
        prefix = bytes.fromhex(prefix_hex)
        payload = read_z(rom, int(address, 16))
        if not payload.startswith(prefix) or len(payload) <= len(prefix):
            raise SystemExit(f"prefix/body boundary drifted at {address}")
        row["_prefix_hex"] = prefix_hex
        row["_payload_hex"] = payload.hex().upper()
        row["_payload_capacity"] = len(payload)
        row["_body_capacity"] = len(payload) - len(prefix)
        row["_body_sha256"] = sha256(payload[len(prefix):])
        bound.append(row)
    return bound


def main() -> int:
    rom = MAIN.read_bytes()
    if len(rom) != 16_777_216 or sha256(rom) != MAIN_SHA256:
        raise SystemExit("current main TIP identity drifted")
    if not CANDIDATE.exists() or sha256(CANDIDATE.read_bytes()) != CANDIDATE_SHA256:
        raise SystemExit("pending cumulative candidate identity drifted")

    event = load_object(EVENT_AUDIT)
    voice = load_object(VOICE_AUDIT)
    ui = load_object(UI_AUDIT)
    if not all(doc.get("ok") is True for doc in (event, voice, ui)):
        raise SystemExit("one or more source audits did not pass")

    pending_event_values = catalog_translations(EVENT_CATALOG)
    pending_ui_values = catalog_translations(UI_CATALOG)
    pending_values = {**pending_event_values, **pending_ui_values}
    pending_addresses = set(pending_values)

    event_rows = bind_current_payloads(rom, event.get("actionable") or [])
    voice_rows = bind_current_payloads(rom, voice.get("actionable") or [])
    ui_rows = bind_current_payloads(rom, ui.get("actionable") or [])

    event_pending = [row for row in event_rows if str(row["abs"]).upper() in pending_addresses]
    event_remaining = [row for row in event_rows if str(row["abs"]).upper() not in pending_addresses]
    if len(event_pending) != 9 or len(ui_rows) != 9:
        raise SystemExit("pending candidate target population drifted")

    event_direct = [row for row in event_remaining if int(row["_body_capacity"]) >= 4]
    event_short = [row for row in event_remaining if int(row["_body_capacity"]) < 4]
    event_groups = rows_by_gap(event_direct)
    first_gaps = {"59264E-59275F", "5927B6-59284F"}
    first_rows = [row for gap, rows in event_groups if gap in first_gaps for row in rows]
    later_groups = [(gap, rows) for gap, rows in event_groups if gap not in first_gaps]
    if set(E001_TRANSLATIONS) != {str(row["abs"]).upper() for row in first_rows}:
        raise SystemExit("E001 translation population drifted")

    batches: list[tuple[str, str, list[dict[str, Any]]]] = [
        ("C000", "candidate_pending", event_pending + ui_rows),
        ("E001", "approved_ready", first_rows),
    ]
    batches.extend((batch_id, "pending_translation", rows) for batch_id, rows in pack_groups(later_groups, "E", start=2))

    voice_direct = [row for row in voice_rows if int(row["_body_capacity"]) >= 4]
    voice_short = [row for row in voice_rows if int(row["_body_capacity"]) < 4]
    by_bank: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in voice_direct:
        by_bank[str(row.get("bank") or str(row["abs"])[:2])].append(row)
    voice_index = 1
    for bank in sorted(by_bank):
        packed = pack_groups(rows_by_gap(by_bank[bank]), "V", start=voice_index)
        batches.extend((batch_id, "pending_translation", rows) for batch_id, rows in packed)
        voice_index += len(packed)

    short_by_bank: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in event_short + voice_short:
        short_by_bank[str(row.get("bank") or str(row["abs"])[:2])].append(row)
    short_index = 1
    for bank in sorted(short_by_bank):
        packed = pack_groups(rows_by_gap(short_by_bank[bank]), "S", start=short_index)
        batches.extend((batch_id, "pending_translation", rows) for batch_id, rows in packed)
        short_index += len(packed)

    previous = existing_edits()
    all_rows: list[dict[str, str]] = []
    manifest_batches: list[dict[str, Any]] = []
    order = 0
    for batch_id, batch_status, sources in batches:
        order += 1
        batch_rows: list[dict[str, str]] = []
        for source in sorted(sources, key=lambda row: int(str(row["abs"]), 16)):
            address = str(source["abs"]).upper()
            scope = (
                "bank59_event" if address.startswith("59")
                else "battle_voice" if address.startswith(("5D", "5E"))
                else "id_indirect_ui"
            )
            prefix_hex = str(source["_prefix_hex"])
            prefix = bytes.fromhex(prefix_hex)
            payload = bytes.fromhex(str(source["_payload_hex"]))
            payload_capacity = int(source["_payload_capacity"])
            body_capacity = int(source["_body_capacity"])
            original = str(source.get("original") or source.get("original_body") or "")
            current = str(source.get("current") or source.get("current_body") or "")
            classification = str(source.get("classification") or source.get("category") or "actionable")
            shape = str(source.get("shape") or ("mixed" if current and original != current else "jp_only"))

            seed_ko = pending_values.get(address) or E001_TRANSLATIONS.get(address) or ""
            seed_source = "llm" if seed_ko else ""
            seed_review = "approved" if seed_ko else ""
            seed_workflow = "candidate_pending" if batch_id == "C000" else "approved" if seed_ko else "pending_translation"
            old = previous.get(address) or {}
            ko = str(old.get("ko") or seed_ko)
            translation_source = str(old.get("translation_source") or seed_source)
            review_status = str(old.get("review_status") or seed_review)
            workflow_status = str(old.get("workflow_status") or seed_workflow)
            notes = str(old.get("notes") or "")
            body = payload[len(prefix):]
            row = {
                "batch_id": batch_id,
                "batch_order": str(order),
                "scope": scope,
                "bank": address[:2],
                "gap": str(source.get("gap") or source.get("category") or "candidate_group"),
                "abs": address,
                "classification": classification,
                "shape": shape,
                "prefix_hex": prefix_hex,
                "payload_capacity": str(payload_capacity),
                "body_capacity": str(body_capacity),
                "original_jp": original,
                "current_text": current,
                "ko": ko,
                "translation_source": translation_source,
                "review_status": review_status,
                "workflow_status": workflow_status,
                "notes": notes,
                "current_payload_hex": payload.hex().upper(),
                "source_body_sha256": str(source["_body_sha256"]),
                "parent_tip_sha256": MAIN_SHA256,
            }
            batch_rows.append(row)
            all_rows.append(row)

        approved = sum(bool(row["ko"].strip()) and row["review_status"] == "approved" for row in batch_rows)
        manifest_batches.append({
            "batch_id": batch_id,
            "order": order,
            "scope": sorted({row["scope"] for row in batch_rows}),
            "banks": sorted({row["bank"] for row in batch_rows}),
            "gaps": list(OrderedDict.fromkeys(row["gap"] for row in batch_rows)),
            "records": len(batch_rows),
            "approved_records": approved,
            "status": batch_status if approved == len(batch_rows) else "pending_translation",
            "sheet": f"out/script/uncovered_batches/{batch_id}.csv",
            "requires_direct_ext3_only": all(int(row["body_capacity"]) >= 4 for row in batch_rows),
        })

    if len(all_rows) != 1893 or len({row["abs"] for row in all_rows}) != 1893:
        raise SystemExit("master sheet population drifted")

    MASTER.parent.mkdir(parents=True, exist_ok=True)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    with MASTER.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    rows_by_batch: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in all_rows:
        rows_by_batch[row["batch_id"]].append(row)
    for batch_id, rows in rows_by_batch.items():
        path = BATCH_DIR / f"{batch_id}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    current_batch_files = {f"{batch_id}.csv" for batch_id in rows_by_batch}
    for stale in BATCH_DIR.glob("*.csv"):
        if stale.name not in current_batch_files:
            stale.unlink()

    manifest = {
        "schema_version": 1,
        "generated_by": "tools/build_uncovered_translation_sheets.py",
        "read_only": True,
        "ok": True,
        "main_tip": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": len(rom), "sha256": MAIN_SHA256},
        "pending_base_candidate": {"path": "out/patch/next_stage_event_id_indirect_candidate.wsc", "sha256": CANDIDATE_SHA256, "targets": 18},
        "policy": {
            "max_records_per_packed_batch": MAX_BATCH_RECORDS,
            "natural_gap_boundaries_preserved": True,
            "translation_source_required": "llm",
            "review_status_required": "approved",
            "legacy_machine_translation_used": False,
            "candidate_chain": "C000 -> E001 -> next fully approved batch",
        },
        "counts": {
            "records": len(all_rows),
            "batches": len(manifest_batches),
            "candidate_pending_records": sum(row["workflow_status"] == "candidate_pending" for row in all_rows),
            "approved_new_records": sum(row["workflow_status"] == "approved" for row in all_rows),
            "pending_translation_records": sum(row["workflow_status"] == "pending_translation" for row in all_rows),
            "event_records": sum(row["scope"] == "bank59_event" for row in all_rows),
            "voice_records": sum(row["scope"] == "battle_voice" for row in all_rows),
            "ui_records": sum(row["scope"] == "id_indirect_ui" for row in all_rows),
        },
        "batches": manifest_batches,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_uncovered_translation_sheets.py",
        "ok": True,
        "master_sheet": str(MASTER.relative_to(ROOT)).replace("\\", "/"),
        "batch_directory": str(BATCH_DIR.relative_to(ROOT)).replace("\\", "/"),
        "manifest": str(MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "counts": manifest["counts"],
        "first_new_batch": next(batch for batch in manifest_batches if batch["batch_id"] == "E001"),
    }
    SUMMARY.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
