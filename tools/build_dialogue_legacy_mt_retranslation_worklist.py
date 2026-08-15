#!/usr/bin/env python3
"""Build a current-TIP worklist of proven legacy machine-translation residues.

The quarantined Bing/Excel cache is evidence only: it is never used as a
translation source.  A scenario record is selected only when its *current
runtime render*, after spacing-only normalization, still equals the quarantined
Bing result for the same Japanese source and the address has not already gone
through a source-grounded semantic rewrite in the 20-cell/readability passes.

This gives a conservative, reproducible population of legacy MT wording that
survived later structural/width work.  Banks 64-69 remain excluded because the
project has already proven that they mix event/data tables and are not safe for
bulk dialogue rewriting.
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
QUALITY = ROOT / "out/script/translations_quality_all.json"
LEGACY_BING = ROOT / "out/script/excel_translate_cache.json"
WORK20 = ROOT / "out/script/dialogue_20cell_worklist.json"
READABILITY = ROOT / "out/script/dialogue_readability_changes.json"
SINGLETON_GLOB = str(ROOT / "data/dialogue_singleton_rewrite_batch*.json")
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/script/dialogue_legacy_mt_retranslation_worklist.json"

SCENARIO_BANKS = {"60", "61", "62", "63"}
SPACE_RE = re.compile(r"[ \u3000]+")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm_visible(text: str) -> str:
    return SPACE_RE.sub("", text.rstrip(" \u3000\t"))


def semantic_reviewed_addresses() -> tuple[set[str], dict[str, int]]:
    reviewed: set[str] = set()
    counts: Counter[str] = Counter()

    work = json.loads(WORK20.read_text(encoding="utf-8"))
    for group in work.get("groups") or []:
        if group.get("mode") != "source_retranslation_required":
            continue
        for row in group.get("records") or []:
            reviewed.add(str(row["abs"]).upper())
            counts["20cell_source_retranslation"] += 1

    doc = json.loads(READABILITY.read_text(encoding="utf-8"))
    for group in doc.get("groups") or []:
        if group.get("classification") != "semantic_rewrite_required":
            continue
        for address in group.get("addresses") or []:
            reviewed.add(str(address).upper())
            counts["readability_semantic_rewrite"] += 1

    for raw in sorted(glob.glob(SINGLETON_GLOB)):
        batch = json.loads(Path(raw).read_text(encoding="utf-8"))
        for address in (batch.get("targets") or {}):
            reviewed.add(str(address).upper())
            counts["singleton_source_rewrite"] += 1

    counts["unique"] = len(reviewed)
    return reviewed, dict(counts)


def main() -> int:
    rom = MAIN.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    sb = stock_base(rom)

    quality = json.loads(QUALITY.read_text(encoding="utf-8")).get("lines") or []
    legacy_doc = json.loads(LEGACY_BING.read_text(encoding="utf-8"))
    if legacy_doc.get("engine") != "bing":
        raise SystemExit("legacy translation evidence is not the expected Bing cache")
    legacy = legacy_doc.get("entries") or {}
    reviewed, reviewed_counts = semantic_reviewed_addresses()

    candidates: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    scenario_rows = 0
    legacy_source_hits = 0

    for src in quality:
        address = str(src.get("abs") or "").upper()
        if address[:2] not in SCENARIO_BANKS:
            continue
        scenario_rows += 1
        jp = str(src.get("jp") or "")
        if not jp or jp not in legacy:
            rejected["no_legacy_bing_source"] += 1
            continue
        legacy_source_hits += 1
        if address in reviewed:
            rejected["already_semantically_reviewed"] += 1
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
            rejected["current_decode_error"] += 1
            continue
        legacy_ko = str(legacy[jp]).rstrip(" \u3000\t")
        if norm_visible(current) != norm_visible(legacy_ko):
            rejected["current_no_longer_matches_legacy_mt"] += 1
            continue

        portal_positions = [
            pos
            for pos in range(max(0, len(body) - 3))
            if body[pos:pos + 2] == b"\xE5\x18"
        ]
        if len(portal_positions) == 1:
            route = "existing_ext3_portal"
        elif len(body) >= 4 and not portal_positions:
            route = "retarget_body_to_ext3"
        elif len(body) < 4 and not portal_positions:
            route = "short_body_requires_stock_route"
        else:
            route = "unsupported_multi_portal"

        candidates.append({
            "abs": address,
            "bank": address[:2],
            "jp": jp,
            "legacy_bing_ko": legacy_ko,
            "quality_source_ko": str(src.get("ko") or ""),
            "current_render": current,
            "current_cells": len(current.replace("<E62F>", "")),
            "payload_hex": payload.hex().upper(),
            "prefix_hex": prefix.hex().upper(),
            "body_hex": body.hex().upper(),
            "body_len": len(body),
            "terminator": f"{term - sb:06X}",
            "route": route,
            "portal_offset": portal_positions[0] if len(portal_positions) == 1 else None,
        })

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[row["jp"]].append(row)

    unique_sources: list[dict[str, Any]] = []
    for ordinal, jp in enumerate(sorted(grouped, key=lambda text: min(int(r["abs"], 16) for r in grouped[text])), 1):
        rows = sorted(grouped[jp], key=lambda row: int(row["abs"], 16))
        unique_sources.append({
            "source_id": f"L{ordinal:04d}",
            "jp": jp,
            "legacy_bing_ko": rows[0]["legacy_bing_ko"],
            "current_examples": list(dict.fromkeys(r["current_render"] for r in rows))[:4],
            "addresses": [r["abs"] for r in rows],
            "routes": dict(Counter(r["route"] for r in rows)),
        })

    route_counts = Counter(row["route"] for row in candidates)
    bank_counts = Counter(row["bank"] for row in candidates)
    summary = {
        "main_tip_sha256": sha256(rom),
        "scenario_banks": sorted(SCENARIO_BANKS),
        "scenario_rows_scanned": scenario_rows,
        "legacy_bing_source_hits": legacy_source_hits,
        "semantically_reviewed_excluded": reviewed_counts,
        "proven_legacy_mt_records": len(candidates),
        "proven_legacy_mt_unique_jp": len(unique_sources),
        "route_counts": dict(route_counts),
        "bank_counts": dict(bank_counts),
        "rejected_counts": dict(rejected),
        "policy": {
            "legacy_cache_use": "evidence_only_never_translation_source",
            "proof": "current_runtime_render_spacing_normalized_equals_quarantined_bing_result",
            "already_semantically_reviewed": "excluded",
            "banks_64_69": "excluded_known_mixed_event_data_tables",
            "candidate_build": "current_main_only_fixed_extent_no_terminator_move",
        },
    }
    doc = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_legacy_mt_retranslation_worklist.py",
        "summary": summary,
        "unique_sources": unique_sources,
        "records": sorted(candidates, key=lambda row: int(row["abs"], 16)),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
