#!/usr/bin/env python3
"""Build a context-neighborhood review ledger around all literal-retranslation targets.

This is read-only with respect to ROMs.  It expands every approved literal target by
three adjacent translation-sheet records on each side (same bank), merges overlaps,
and records current runtime text plus evidence that can prioritize manual JP-source
context review.  Historical/blocked Korean is evidence only and is never a source for
new translation text.
"""
from __future__ import annotations

import csv
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

RADIUS = 5
SHEET = ROOT / "out/script/translation_sheet.csv"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
FORENSIC = ROOT / "out/script/dialogue_legacy_source_retranslation_worklist.json"
OUT = ROOT / "out/script/dialogue_context_neighborhood_worklist.json"

BAD_TERMS = (
    "우역", "커틀릿", "오마에", "키사마", "아이츠", "코이츠", "치쿠쇼",
    "독수리의", "악마 건담", "밝은 중령", "밝은 함장", "제리도", "르스",
    "양해입니다", "양해했다", "비단입니다", "비단이라고", "미리샤",
    "캡틴", "모빌스－츠", "직원에게는 서리", "인신 공공", "인신어공",
)
REPEAT_RE = re.compile(r"([가-힣]{2,6})\1")


def norm(s: str) -> str:
    return (s or "").replace(" ", "　").strip("　 \t")


def main() -> int:
    targets: dict[str, str] = {}
    for raw in sorted(glob.glob(str(ROOT / "data/dialogue_legacy_mt_literal_batch*.json"))):
        d = json.loads(Path(raw).read_text(encoding="utf-8"))
        for a, text in (d.get("targets") or {}).items():
            a = str(a).upper()
            if a in targets and norm(targets[a]) != norm(str(text)):
                raise RuntimeError(f"conflicting batch target at {a}")
            targets[a] = str(text)

    csv.field_size_limit(10_000_000)
    with SHEET.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    idx = {str(r.get("abs") or "").upper(): i for i, r in enumerate(rows) if r.get("abs")}
    seed_indices = sorted((idx[a], a) for a in targets if a in idx)

    neighborhood: set[str] = set()
    touched_indices: set[int] = set()
    for i, a in seed_indices:
        bank = a[:2]
        for j in range(max(0, i - RADIUS), min(len(rows), i + RADIUS + 1)):
            aa = str(rows[j].get("abs") or "").upper()
            if aa[:2] == bank:
                neighborhood.add(aa)
                touched_indices.add(j)

    # Merge contiguous touched sheet rows into context clusters.
    clusters: list[tuple[int, int]] = []
    for i in sorted(touched_indices):
        if not clusters or i > clusters[-1][1] + 1 or str(rows[i].get("abs") or "")[:2] != str(rows[clusters[-1][1]].get("abs") or "")[:2]:
            clusters.append((i, i))
        else:
            clusters[-1] = (clusters[-1][0], i)

    forensic = json.loads(FORENSIC.read_text(encoding="utf-8"))
    by_forensic = {str(r["abs"]).upper(): r for r in forensic.get("records") or []}

    # Same-JP corrected translations are useful review evidence, but not blindly applied.
    jp_target_texts: dict[str, Counter[str]] = defaultdict(Counter)
    for a, text in targets.items():
        i = idx.get(a)
        if i is None:
            continue
        jp = str(rows[i].get("jp") or "")
        if jp:
            jp_target_texts[jp][norm(text)] += 1

    rom = MAIN.read_bytes()
    tbl = Tbl.load(TBL)
    d = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)

    cluster_id_by_index: dict[int, int] = {}
    cluster_rows: list[dict] = []
    for cid, (lo, hi) in enumerate(clusters, start=1):
        for i in range(lo, hi + 1):
            cluster_id_by_index[i] = cid
        abses = [str(rows[i].get("abs") or "").upper() for i in range(lo, hi + 1)]
        cluster_rows.append({
            "cluster": cid,
            "bank": abses[0][:2] if abses else "",
            "start_abs": abses[0] if abses else "",
            "end_abs": abses[-1] if abses else "",
            "records": len(abses),
            "seed_records": sum(a in targets for a in abses),
            "new_neighbors": sum(a not in targets for a in abses),
        })

    out_rows: list[dict] = []
    flags = Counter()
    for a in sorted(neighborhood, key=lambda x: idx[x]):
        i = idx[a]
        sheet_row = rows[i]
        jp = str(sheet_row.get("jp") or "")
        got = read_encoded_z_safe(rom, sb + int(a, 16), max_len=256)
        if got is None:
            current = ""
            kind = "unreadable"
        else:
            prefix, body, kind = split_prefix_body(bytes(got[0]))
            try:
                current = d.expand(body, tbl).rstrip("　 \t")
            except Exception:
                current = ""
        reasons: list[str] = []
        if a not in targets:
            if a in by_forensic:
                reasons.append("forensic_blocked_source_neighbor")
            variants = jp_target_texts.get(jp)
            if variants:
                best, n = variants.most_common(1)[0]
                if norm(current) != best:
                    reasons.append("same_jp_as_corrected_target_differs")
            if any(term in current for term in BAD_TERMS):
                reasons.append("known_mt_lexical_residue")
            if REPEAT_RE.search(current):
                reasons.append("repeated_korean_chunk")
            jp_len = max(1, len(jp.replace("　", "").replace(" ", "")))
            ko_len = len(current.replace("　", "").replace(" ", ""))
            ratio = ko_len / jp_len
            if jp_len >= 5 and ratio >= 1.65:
                reasons.append("strong_expansion")
            elif jp_len >= 8 and ratio <= 0.38:
                reasons.append("strong_undertranslation")
            for r in reasons:
                flags[r] += 1
        out_rows.append({
            "cluster": cluster_id_by_index[i],
            "abs": a,
            "bank": a[:2],
            "is_existing_target": a in targets,
            "jp": jp,
            "sheet_ko_forensic_only": str(sheet_row.get("ko") or ""),
            "current_render": current,
            "kind": kind,
            "forensic_route": str((by_forensic.get(a) or {}).get("route") or ""),
            "same_jp_corrected_variants": dict(jp_target_texts.get(jp) or {}),
            "review_reasons": reasons,
        })

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_context_neighborhood_worklist.py",
        "radius_records_each_side": RADIUS,
        "policy": {
            "translation_source": "Japanese original only",
            "blocked_or_legacy_korean": "forensic evidence only",
            "same_jp_corrected_text": "review evidence; context must still be checked",
        },
        "summary": {
            "seed_targets": len(targets),
            "seed_targets_in_sheet": len(seed_indices),
            "context_clusters": len(clusters),
            "neighborhood_records": len(neighborhood),
            "new_neighbor_records": sum(a not in targets for a in neighborhood),
            "new_neighbor_in_forensic_ledger": sum(a not in targets and a in by_forensic for a in neighborhood),
            "new_neighbor_outside_forensic_ledger": sum(a not in targets and a not in by_forensic for a in neighborhood),
            "flagged_new_neighbors": sum(a not in targets and bool(next(r for r in out_rows if r["abs"] == a)["review_reasons"]) for a in neighborhood),
            "reason_counts": dict(flags),
        },
        "clusters": cluster_rows,
        "records": out_rows,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
