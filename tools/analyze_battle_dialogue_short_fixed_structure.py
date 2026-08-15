#!/usr/bin/env python3
"""Classify short/fixed 5D/5E battle-dialogue records left after structure repair.

The previous structure repair quarantined records whose authoritative body capacity
was <4 bytes because the live translation used a 4-byte E5 18 alias and there was
no room to restore speaker/portrait metadata safely.  This analyzer partitions the
remaining E5 18 short/fixed rows into:

* one-byte leading units that are independently proven speaker/control values by
  their repeated use as live structured prefixes elsewhere in the current main;
* multi-byte leading units, which are dictionary text tokens and must remain
  text-initial (never restored as speaker metadata).

Read-only with respect to ROM/SaveRAM.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import Dictionary, Tbl, load_rom, stock_base

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
INVENTORY = ROOT / "out/script/battle_dialogue_structure_inventory.csv"
AMBIGUOUS = ROOT / "out/script/battle_dialogue_structure_ambiguous_short_fixed.csv"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
OUT_JSON = PATCH / "battle_dialogue_short_fixed_structure_analysis.json"
OUT_META = ROOT / "out/script/battle_dialogue_short_fixed_metadata_targets.csv"
OUT_TEXT = ROOT / "out/script/battle_dialogue_short_fixed_text_initial.csv"
EXPECTED_TIP = "56b1ed5b81d9878bed01383f68abfffc876ad04eea5dd1d4d29525c833c83898"
SCREEN_ANCHOR = 0x5EB098
PARTNER_ANCHOR = 0x5EB09D
MIN_CONTROL_PEERS = 5


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def clean(value: str) -> str:
    return value.rstrip("\u3000 \t")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def main() -> int:
    tip = bytes(load_rom(TIP))
    original = bytes(load_rom(ORIGINAL))
    if sha(tip) != EXPECTED_TIP:
        raise SystemExit(f"main TIP identity drifted: {sha(tip)}")
    tbl = Tbl.load(TBL_PATH)
    od = Dictionary(original)
    sb = stock_base(tip)

    inventory = read_rows(INVENTORY)
    by_abs = {int(row["record_start"], 16): row for row in inventory}

    live_control_counts: Counter[str] = Counter()
    live_control_examples: dict[str, list[str]] = defaultdict(list)
    for row in inventory:
        metadata = bytes.fromhex(row.get("metadata_hex") or "")
        if len(metadata) != 1:
            continue
        logical = int(row["record_start"], 16)
        plen = len(bytes.fromhex(row["current_payload_hex"]))
        live = tip[sb + logical : sb + logical + plen]
        if live.startswith(metadata):
            live_control_counts[row["metadata_hex"]] += 1
            if len(live_control_examples[row["metadata_hex"]]) < 8:
                live_control_examples[row["metadata_hex"]].append(f"{logical:06X}")

    source_rows = []
    for row in read_rows(AMBIGUOUS):
        if row.get("reason") != "short/fixed body capacity < 4":
            continue
        if row.get("safe_structure_exact") != "yes" or row.get("current_structure_exact") != "no":
            continue
        live_before = bytes.fromhex(row.get("current_payload_hex") or "")
        if not live_before.startswith(b"\xE5\x18"):
            continue
        logical = int(row["record_start"], 16)
        live_now = tip[sb + logical : sb + logical + len(live_before)]
        if live_now != live_before:
            raise SystemExit(f"short/fixed source drift at {logical:06X}")
        if tip[sb + logical + len(live_before)] != 0:
            raise SystemExit(f"short/fixed terminator drift at {logical:06X}")
        source_rows.append(row)

    metadata_rows: list[dict[str, Any]] = []
    text_rows: list[dict[str, Any]] = []
    for row in source_rows:
        logical = int(row["record_start"], 16)
        metadata = bytes.fromhex(row["metadata_hex"])
        body = bytes.fromhex(row["body_hex_original"])
        try:
            metadata_text = od.expand(metadata, tbl)
            body_text = od.expand(body, tbl)
            full_text = od.expand(metadata + body, tbl)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"decode failed at {logical:06X}: {type(exc).__name__}") from exc

        common = {
            "abs": f"{logical:06X}",
            "bank": row["bank"],
            "metadata_hex": row["metadata_hex"],
            "metadata_width": len(metadata),
            "metadata_text": metadata_text,
            "body_capacity": int(row["body_capacity"]),
            "original_body_hex": row["body_hex_original"],
            "original_body_text": body_text,
            "original_full_text": full_text,
            "current_payload_hex": row["current_payload_hex"],
            "current_korean": clean(row["current_render"]),
            "previous_record": row.get("previous_record") or "",
            "next_record": row.get("next_record") or "",
            "control_peer_count": live_control_counts.get(row["metadata_hex"], 0),
            "control_peer_examples": ";".join(live_control_examples.get(row["metadata_hex"], [])),
            "screen_anchor": "user_black_portrait_short_record" if logical == SCREEN_ANCHOR else "",
        }
        if len(metadata) == 1:
            peers = int(common["control_peer_count"])
            if peers < MIN_CONTROL_PEERS:
                raise SystemExit(f"one-byte short metadata lacks independent control peers at {logical:06X}: {peers}")
            common["classification"] = "speaker_metadata_restore"
            common["reason"] = (
                "one-byte lead is independently used as live speaker/control metadata elsewhere; "
                "restore it and re-encode the Korean body in <=3 bytes"
            )
            metadata_rows.append(common)
        else:
            common["classification"] = "text_initial_multibyte"
            common["reason"] = (
                "multi-byte lead is a dictionary text token; proven speaker/control IDs in this family are one byte; "
                "do not restore as metadata"
            )
            text_rows.append(common)

    if len(source_rows) != 115 or len(metadata_rows) != 104 or len(text_rows) != 11:
        raise SystemExit(
            f"population drift: source={len(source_rows)} metadata={len(metadata_rows)} text={len(text_rows)}"
        )
    anchor = next((row for row in metadata_rows if int(row["abs"], 16) == SCREEN_ANCHOR), None)
    partner = by_abs.get(PARTNER_ANCHOR)
    if anchor is None or anchor["metadata_hex"] != "90" or anchor["current_korean"] != "아직이다！":
        raise SystemExit("screen anchor binding drifted")
    if partner is None or clean(partner["current_render"]) != "이\u3000정도로\u3000당할\u3000수\u3000있겠나！！":
        raise SystemExit("screen partner binding drifted")

    fields = [
        "abs", "bank", "classification", "metadata_hex", "metadata_width", "metadata_text",
        "body_capacity", "original_body_hex", "original_body_text", "original_full_text",
        "current_payload_hex", "current_korean", "previous_record", "next_record",
        "control_peer_count", "control_peer_examples", "screen_anchor", "reason",
    ]
    write_csv(OUT_META, metadata_rows, fields)
    write_csv(OUT_TEXT, text_rows, fields)

    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_battle_dialogue_short_fixed_structure.py",
        "read_only_rom": True,
        "ok": True,
        "tip": {"path": "out/patch/monoeye_ko_expanded.wsc", "sha256": sha(tip)},
        "cause": (
            "historical whole-record E5 18 rewrites consumed one-byte speaker/portrait metadata in short/fixed "
            "battle records; the previous structure repair quarantined them because their authoritative body "
            "capacity was below four bytes"
        ),
        "counts": {
            "short_fixed_e518_population": len(source_rows),
            "speaker_metadata_restore": len(metadata_rows),
            "text_initial_multibyte": len(text_rows),
            "unique_one_byte_control_values": len({row["metadata_hex"] for row in metadata_rows}),
            "minimum_independent_control_peers": min(int(row["control_peer_count"]) for row in metadata_rows),
        },
        "screen_anchor": anchor,
        "screen_partner": {
            "abs": f"{PARTNER_ANCHOR:06X}",
            "classification": partner["classification"],
            "current_render": clean(partner["current_render"]),
            "current_payload_hex": partner["current_payload_hex"],
        },
        "outputs": {
            "metadata_targets_csv": str(OUT_META.relative_to(ROOT)).replace("\\", "/"),
            "text_initial_csv": str(OUT_TEXT.relative_to(ROOT)).replace("\\", "/"),
        },
        "write_policy": (
            "104 one-byte rows may restore metadata only with a <=3-byte Korean body token; 11 multi-byte rows "
            "must never receive speaker metadata and are reviewed as full visible text"
        ),
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "counts": report["counts"], "screen_anchor": anchor}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
