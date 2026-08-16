#!/usr/bin/env python3
"""Read-only global follow-up audit for dialogue structural regressions.

This audit is intentionally conservative.  It does not infer speaker/portrait
metadata from byte shape.  It combines only:

* the current authoritative runtime-contract manifest;
* the historical Garrod/page-boundary guard, used as a source-bound structural
  comparison (not as a current SHA pin);
* the two user-proven battle metadata families (0F and 5D) and their explicit
  visible-text exceptions;
* the promoted Gato 5D1E3E exact payload.

Broader exact-fit E5 18 populations are inventory/review only.  They are never
reported as confirmed bugs and are never auto-rewritten by this tool.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from audit_garrod_native_stock_guard import build_report as build_garrod_report
from dialogue_runtime_contracts import SCENARIO_FIRST_NATIVE_ONLY
from monoeye_rom import read_encoded_z_safe, stock_base

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
DEFAULT_BATTLE_INVENTORY = (
    ROOT / "legacy/release_core_20260815/out/script/battle_dialogue_structure_inventory.csv"
)
DEFAULT_OUT = ROOT / "out/patch/global_dialogue_structural_followup_audit.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"

EXPECTED_PROMOTED_SHA = "d5fb5d338875f9a5ff1071f04c3b042fcff1a3f38142aae09b6bf9e44ad0fac5"
EXPECTED_GATO = bytes.fromhex("0FF65A0101010101010101010101010101")
VISIBLE_TEXT_5D_EXCEPTIONS = {
    0x5D3122: bytes.fromhex("E7BAF50D01010101010101"),
    0x5D313B: bytes.fromhex("E7BAF50D01010101010101"),
}
RUNTIME_CONFIRMED_SAFE_SCENARIO_EXT3 = {
    0x62BF78: "user runtime-tested: '시로……' path shows no control leak, repetition, portrait error, or progression fault",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"not an object: {path}")
    return value


def record_payload(rom: bytes, logical: int) -> bytes:
    base = stock_base(rom)
    got = read_encoded_z_safe(rom, base + logical, max_len=512)
    if got is None:
        raise RuntimeError(f"unreadable record {logical:06X}")
    return bytes(got[0])


def source_unit_kinds(hex_body: str) -> tuple[str, ...]:
    body = bytes.fromhex(hex_body)
    kinds: list[str] = []
    i = 0
    while i < len(body):
        value = body[i]
        if 0xF0 <= value <= 0xFF and i + 1 < len(body):
            kinds.append("dict")
            i += 2
        elif 0xE0 <= value <= 0xE7 and i + 1 < len(body):
            kinds.append("glyph2")
            i += 2
        else:
            kinds.append("char1")
            i += 1
    return tuple(kinds)


def broad_scenario_review_population(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in contracts:
        if row.get("family") != "scenario_bundle" or row.get("line_role") != "first":
            continue
        body_hex = str(row.get("baseline_body_hex") or "")
        if not body_hex:
            continue
        body = bytes.fromhex(body_hex)
        boundary = row.get("baseline_boundary") or {}
        if (
            len(body) != 4
            or body[:2] != b"\xE5\x18"
            or int(boundary.get("nul_run") or 0) != 2
            or boundary.get("next_lead") not in {"17", "18", "08"}
        ):
            continue
        kinds = source_unit_kinds(str(row.get("source_body_hex") or ""))
        rows.append(
            {
                "address": str(row["address"]),
                "source_unit_kinds": list(kinds),
                "source_exact_native_dict2": kinds == ("dict", "dict"),
                "next_lead": boundary.get("next_lead"),
                "next_control": boundary.get("next_control") or "",
                "status": row.get("status"),
                "confidence": row.get("confidence"),
                "disposition": "review_only_no_bulk_rewrite",
            }
        )
    return rows


def battle_family_audit(target: bytes, inventory_path: Path) -> dict[str, Any]:
    rows = list(csv.DictReader(inventory_path.open(encoding="utf-8-sig")))
    result: dict[str, Any] = {}
    for metadata in ("0F", "5D"):
        family = [
            row
            for row in rows
            if row.get("classification") == "battle_voice_structured"
            and row.get("safe_structure_exact") == "yes"
            and str(row.get("metadata_hex") or "").upper() == metadata
        ]
        intentional = VISIBLE_TEXT_5D_EXCEPTIONS if metadata == "5D" else {}
        checked = [row for row in family if int(row["record_start"], 16) not in intentional]
        missing_metadata: list[str] = []
        ext3_after_metadata: list[str] = []
        for row in checked:
            logical = int(row["record_start"], 16)
            payload = record_payload(target, logical)
            lead = bytes.fromhex(metadata)
            if not payload.startswith(lead):
                missing_metadata.append(f"{logical:06X}")
                continue
            if payload[len(lead) : len(lead) + 2] == b"\xE5\x18":
                ext3_after_metadata.append(f"{logical:06X}")
        result[metadata] = {
            "historically_proven_safe_rows": len(family),
            "checked_after_explicit_visible_text_exceptions": len(checked),
            "explicit_visible_text_exceptions": [f"{value:06X}" for value in sorted(intentional)],
            "missing_metadata": missing_metadata,
            "still_e518_after_metadata": ext3_after_metadata,
            "clean": not missing_metadata and not ext3_after_metadata,
        }

    exception_rows: list[dict[str, Any]] = []
    for logical, expected in sorted(VISIBLE_TEXT_5D_EXCEPTIONS.items()):
        actual = record_payload(target, logical)
        exception_rows.append(
            {
                "address": f"{logical:06X}",
                "expected_payload_hex": expected.hex().upper(),
                "actual_payload_hex": actual.hex().upper(),
                "exact": actual == expected,
                "reason": "user-promoted visible-text lead exception; do not restore byte 5D as metadata",
            }
        )
    result["visible_text_exceptions"] = exception_rows

    gato = record_payload(target, 0x5D1E3E)
    result["gato_5D1E3E"] = {
        "expected_payload_hex": EXPECTED_GATO.hex().upper(),
        "actual_payload_hex": gato.hex().upper(),
        "exact": gato == EXPECTED_GATO,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS)
    parser.add_argument("--battle-inventory", type=Path, default=DEFAULT_BATTLE_INVENTORY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    target = args.target.read_bytes()
    contracts_doc = load_json(args.contracts)
    contracts = list(contracts_doc.get("contracts") or [])

    runtime_status = Counter(str(row.get("status") or "") for row in contracts)
    runtime_routes = Counter(str(row.get("route") or "") for row in contracts)
    battle_contracts = [row for row in contracts if row.get("family") == "battle_voice"]
    battle_quarantine_ext3 = [
        row
        for row in battle_contracts
        if row.get("status") == "quarantine"
        and bytes.fromhex(str(row.get("baseline_body_hex") or "")).startswith(b"\xE5\x18")
    ]

    native_guards: list[dict[str, Any]] = []
    for logical in sorted(SCENARIO_FIRST_NATIVE_ONLY):
        payload = record_payload(target, logical)
        native_guards.append(
            {
                "address": f"{logical:06X}",
                "payload_hex": payload.hex().upper(),
                "has_top_level_e518_after_173418": payload.startswith(b"\x17\x34\x18\xE5\x18"),
            }
        )

    # The Garrod guard is useful here only as a source-bound structural family
    # comparator.  Disable its stale current-TIP SHA binding.
    garrod = build_garrod_report(args.target, ORIGINAL, expected_target_sha=None)
    garrod_native_drift = [str(row["logical"]) for row in garrod.get("source_native_drift") or []]

    broad = broad_scenario_review_population(contracts)
    broad_exact = [row for row in broad if row["source_exact_native_dict2"]]
    broad_mixed = [row for row in broad if not row["source_exact_native_dict2"]]
    exact_by_next = Counter(str(row["next_lead"]) for row in broad_exact)
    mixed_by_next = Counter(str(row["next_lead"]) for row in broad_mixed)

    battle = battle_family_audit(target, args.battle_inventory)

    confirmed_regressions: list[str] = []
    if any(row["has_top_level_e518_after_173418"] for row in native_guards):
        confirmed_regressions.append("runtime-proven scenario native-only guard reverted to E5 18")
    if not battle["0F"]["clean"]:
        confirmed_regressions.append("metadata=0F proven battle family regression")
    if not battle["5D"]["clean"]:
        confirmed_regressions.append("metadata=5D proven battle family regression")
    if not all(row["exact"] for row in battle["visible_text_exceptions"]):
        confirmed_regressions.append("battle visible-text exception regression")
    if not battle["gato_5D1E3E"]["exact"]:
        confirmed_regressions.append("Gato 5D1E3E regression")

    runtime_safe_scenario = sorted(
        address for address in set(garrod_native_drift) if int(address, 16) in RUNTIME_CONFIRMED_SAFE_SCENARIO_EXT3
    )
    high_confidence_review = sorted(
        address for address in set(garrod_native_drift) if int(address, 16) not in RUNTIME_CONFIRMED_SAFE_SCENARIO_EXT3
    )
    status = "review" if high_confidence_review else ("pass" if not confirmed_regressions else "fail")

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_global_dialogue_structural_followup.py",
        "status": status,
        "target": {
            "path": str(args.target.resolve()),
            "size": len(target),
            "sha256": sha(target),
            "expected_promoted_sha256": EXPECTED_PROMOTED_SHA,
            "matches_promoted_main": sha(target) == EXPECTED_PROMOTED_SHA,
        },
        "runtime_contracts": {
            "total": len(contracts),
            "status_counts": dict(sorted(runtime_status.items())),
            "route_counts": dict(sorted(runtime_routes.items())),
            "battle_contracts": len(battle_contracts),
            "battle_quarantine_e518": len(battle_quarantine_ext3),
            "note": "quarantine rows are unresolved evidence, not confirmed defects and not auto-writable",
        },
        "scenario": {
            "runtime_proven_native_only_guards": native_guards,
            "garrod_source_bound_family": {
                "counts": garrod.get("counts") or {},
                "native_source_drift_addresses": sorted(set(garrod_native_drift)),
                "runtime_confirmed_safe_ext3_addresses": runtime_safe_scenario,
                "runtime_confirmed_safe_reasons": {
                    f"{address:06X}": reason for address, reason in sorted(RUNTIME_CONFIRMED_SAFE_SCENARIO_EXT3.items())
                },
                "mixed_ext3_review_count": int((garrod.get("counts") or {}).get("current_ext3_source_mixed_grammar", 0)),
                "note": "source-native drift is structural review evidence only; user-runtime-confirmed safe exceptions are excluded from focused follow-up",
            },
            "broader_exact_fit_boundary_inventory": {
                "total": len(broad),
                "source_exact_native_dict2": len(broad_exact),
                "source_mixed": len(broad_mixed),
                "exact_by_next_lead": dict(sorted(exact_by_next.items())),
                "mixed_by_next_lead": dict(sorted(mixed_by_next.items())),
                "rows": broad,
                "note": "broader inventory only; static shape alone is insufficient proof for bulk native rewrite",
            },
        },
        "battle": battle,
        "confirmed_regressions": confirmed_regressions,
        "runtime_confirmed_safe_review_exceptions": runtime_safe_scenario,
        "high_confidence_review_addresses": high_confidence_review,
        "conclusion": {
            "known_promoted_regressions_clean": not confirmed_regressions,
            "bulk_auto_rewrite_allowed": False,
            "focused_followup_recommended": bool(high_confidence_review),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": status,
                "target_sha256": report["target"]["sha256"],
                "runtime_contracts": report["runtime_contracts"],
                "garrod_counts": report["scenario"]["garrod_source_bound_family"]["counts"],
                "broad_counts": {
                    key: value
                    for key, value in report["scenario"]["broader_exact_fit_boundary_inventory"].items()
                    if key not in {"rows", "note"}
                },
                "battle_0F": battle["0F"],
                "battle_5D": battle["5D"],
                "gato": battle["gato_5D1E3E"],
                "confirmed_regressions": confirmed_regressions,
                "runtime_confirmed_safe_review_exceptions": runtime_safe_scenario,
                "high_confidence_review_addresses": high_confidence_review,
                "report": str(args.out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if confirmed_regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
