#!/usr/bin/env python3
"""Inventory scenario dialogue still bound to quarantined legacy translation sources.

This is a provenance/risk inventory, not a translation generator.  Quarantined
Bing and mixed quality files are used only to prove that a current runtime
render still equals an old machine/mixed-source Korean string.  Their Korean
text is never treated as an approved translation.

Safe bulk scenario scope is banks 60-63. Banks 64-69 stay excluded because the
project has already proven they mix text with event/data tables. Rows already
source-retranslated in the 20-cell/readability/singleton passes are excluded.
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
QUALITY = ROOT / "out/script/translations_quality_all.json"
BING = ROOT / "out/script/excel_translate_cache.json"
WORK20 = ROOT / "out/script/dialogue_20cell_worklist.json"
READABILITY = ROOT / "out/script/dialogue_readability_changes.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/script/dialogue_legacy_source_retranslation_worklist.json"
SCENARIO_BANKS = {"60", "61", "62", "63"}
SPACE_RE = re.compile(r"[ \u3000]+")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm(text: str) -> str:
    return SPACE_RE.sub("", str(text or "").rstrip(" \u3000\t"))


def reviewed_addresses() -> tuple[set[str], dict[str, int]]:
    out: set[str] = set()
    counts: Counter[str] = Counter()

    work = json.loads(WORK20.read_text(encoding="utf-8"))
    for group in work.get("groups") or []:
        if group.get("mode") != "source_retranslation_required":
            continue
        for row in group.get("records") or []:
            out.add(str(row["abs"]).upper())
            counts["20cell_source_retranslation"] += 1

    readability = json.loads(READABILITY.read_text(encoding="utf-8"))
    for group in readability.get("groups") or []:
        if group.get("classification") != "semantic_rewrite_required":
            continue
        for address in group.get("addresses") or []:
            out.add(str(address).upper())
            counts["readability_semantic_rewrite"] += 1

    for raw in sorted(glob.glob(str(ROOT / "data/dialogue_singleton_rewrite_batch*.json"))):
        doc = json.loads(Path(raw).read_text(encoding="utf-8"))
        for address in (doc.get("targets") or {}):
            out.add(str(address).upper())
            counts["singleton_source_rewrite"] += 1

    counts["unique"] = len(out)
    return out, dict(counts)


def route_for(body: bytes) -> tuple[str, int | None]:
    positions = [
        pos for pos in range(max(0, len(body) - 3))
        if body[pos:pos + 2] == b"\xE5\x18"
    ]
    if len(positions) == 1:
        return "existing_ext3_portal", positions[0]
    if len(positions) > 1:
        return "unsupported_multi_portal", None
    if len(body) >= 4:
        return "retarget_body_to_ext3", None
    return "short_body_requires_stock_route", None


def main() -> int:
    rom = MAIN.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)
    quality = json.loads(QUALITY.read_text(encoding="utf-8")).get("lines") or []
    bing_doc = json.loads(BING.read_text(encoding="utf-8"))
    if bing_doc.get("engine") != "bing":
        raise SystemExit("unexpected forensic cache engine")
    bing = bing_doc.get("entries") or {}
    reviewed, reviewed_counts = reviewed_addresses()

    records: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    evidence_counts: Counter[str] = Counter()
    bank_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()

    for src in quality:
        address = str(src.get("abs") or "").upper()
        if address[:2] not in SCENARIO_BANKS:
            continue
        if address in reviewed:
            rejected["already_source_retranslated"] += 1
            continue
        got = read_encoded_z_safe(rom, sb + int(address, 16), max_len=256)
        if got is None:
            rejected["unreadable_current_record"] += 1
            continue
        payload, term = bytes(got[0]), int(got[1])
        prefix, body, kind = split_prefix_body(payload)
        try:
            current = dictionary.expand(body, tbl).rstrip(" \u3000\t")
        except Exception:
            rejected["decode_error"] += 1
            continue

        jp = str(src.get("jp") or "")
        legacy_quality = str(src.get("ko") or "")
        legacy_bing = str(bing.get(jp) or "")
        matches_quality = bool(legacy_quality) and norm(current) == norm(legacy_quality)
        matches_bing = bool(legacy_bing) and norm(current) == norm(legacy_bing)
        if not (matches_quality or matches_bing):
            rejected["current_no_longer_matches_blocked_source"] += 1
            continue

        if matches_quality and matches_bing:
            evidence = "blocked_quality_and_bing"
        elif matches_quality:
            evidence = "blocked_quality_only"
        else:
            evidence = "blocked_bing_only"
        route, portal_offset = route_for(body)
        row = {
            "abs": address,
            "bank": address[:2],
            "jp": jp,
            "current_render": current,
            "current_cells": len(current.replace("<E62F>", "")),
            "forensic_evidence": evidence,
            "blocked_quality_ko": legacy_quality,
            "blocked_bing_ko": legacy_bing,
            "payload_hex": payload.hex().upper(),
            "prefix_hex": prefix.hex().upper(),
            "body_hex": body.hex().upper(),
            "body_len": len(body),
            "terminator": f"{term - sb:06X}",
            "route": route,
            "portal_offset": portal_offset,
        }
        records.append(row)
        evidence_counts[evidence] += 1
        bank_counts[address[:2]] += 1
        route_counts[route] += 1

    unique_jp = len({row["jp"] for row in records})
    summary = {
        "main_tip_sha256": sha256(rom),
        "safe_scenario_banks_scanned": sorted(SCENARIO_BANKS),
        "source_rows_in_safe_banks": sum(str(r.get("abs") or "")[:2] in SCENARIO_BANKS for r in quality),
        "already_source_retranslated_excluded": reviewed_counts,
        "proven_blocked_source_records": len(records),
        "proven_blocked_source_unique_jp": unique_jp,
        "evidence_counts": dict(evidence_counts),
        "bank_counts": dict(bank_counts),
        "route_counts": dict(route_counts),
        "rejected_counts": dict(rejected),
        "policy": {
            "blocked_korean_use": "forensic_equality_proof_only_never_retranslation_source",
            "new_translation_source": "Japanese original only",
            "banks_64_69": "excluded_known_mixed_event_data_tables",
            "reviewed_populations": "excluded_to_avoid_regressing_recent_user_verified_work",
            "candidate_parent": "current promoted main TIP only",
        },
    }
    doc = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_legacy_source_retranslation_worklist.py",
        "summary": summary,
        "records": sorted(records, key=lambda row: int(row["abs"], 16)),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
