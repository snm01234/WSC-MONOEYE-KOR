#!/usr/bin/env python3
"""Audit unresolved scenario continuation rows whose leading 0x18 may leak as visible `こ`.

The extraction sheet already separates a structural ``prefix_hex=18`` from the
Japanese/Korean text for these rows.  The runtime-contract layer deliberately
keeps unresolved scenario continuations in quarantine because byte 0x18 is
context-sensitive.  If the current physical payload still begins with 0x18,
the generic decoder renders that byte as `こ`.

This audit therefore does NOT call every row a proven runtime bug.  It creates a
review sheet with disjoint priority tiers:

P0: user-runtime-proven bad anchor (60BB48).
P1: direct 18+E518, historical sheet text differs only by injected こ, immediate 08/17 control.
P2: same exact text-only mismatch, no immediate 08/17 control.
P3: direct 18+E518 + immediate control, but wording drift exists beyond こ.
P4: remaining direct 18+E518 unresolved rows.
P5: non-direct/native current payloads; static decoder may be a false positive,
    including already runtime-proven native-only continuations. Never auto-fix.

Outputs are read-only audits/review sheets; the main ROM is not modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
SHEET = ROOT / "out/script/translation_sheet.csv"
OUT_JSON = ROOT / "out/patch/scenario_continuation_leading18_audit.json"
OUT_CSV = ROOT / "docs/SCENARIO_CONTINUATION_LEADING18_REVIEW_SHEET.csv"
OUT_MD = ROOT / "docs/SCENARIO_CONTINUATION_LEADING18_REVIEW_SHEET.md"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"

RUNTIME_BAD = {"60BB48"}
RUNTIME_NATIVE_SAFE = {"63449B", "635855", "635BFB", "635866", "635C0C"}
HISTORICAL_FIXED = {"6002F1"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(text: str) -> str:
    return (
        re.sub(r"[ \u3000]", "", text or "")
        .replace("!", "！")
        .replace("?", "？")
        .replace(",", "、")
        .replace(".", "。")
    )


def clean_for_csv(text: Any) -> str:
    return str(text or "").replace("\r", " ").replace("\n", " ")


def compact_hex(text: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", text or "").upper()


def cell_len(text: str) -> int:
    # Dialogue runtime uses one visible codepoint per cell for these sheet rows.
    return len((text or "").replace("<E62F>", ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--contracts", type=Path, default=CONTRACTS)
    ap.add_argument("--sheet", type=Path, default=SHEET)
    ap.add_argument("--rom", type=Path, default=MAIN)
    ap.add_argument("--out-json", type=Path, default=OUT_JSON)
    ap.add_argument("--out-csv", type=Path, default=OUT_CSV)
    ap.add_argument("--out-md", type=Path, default=OUT_MD)
    args = ap.parse_args(argv)

    doc = json.loads(args.contracts.read_text(encoding="utf-8"))
    contracts = list(doc.get("contracts") or [])
    by_addr = {str(r["address"]).upper(): r for r in contracts}
    ordered = sorted(
        [r for r in contracts if str(r.get("route") or "").startswith("scenario_")],
        key=lambda r: int(str(r["address"]), 16),
    )
    order_index = {str(r["address"]).upper(): i for i, r in enumerate(ordered)}

    csv.field_size_limit(sys.maxsize)
    sheet_rows: dict[str, dict[str, str]] = {}
    with args.sheet.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            sheet_rows[str(row.get("abs") or "").upper()] = row

    rows: list[dict[str, Any]] = []
    for r in ordered:
        address = str(r["address"]).upper()
        if r.get("route") != "scenario_continuation":
            continue
        s = sheet_rows.get(address)
        if not s:
            continue
        if str(s.get("prefix_hex") or "").replace(" ", "").upper() != "18":
            continue
        expected = str(s.get("ko") or "")
        current = str(r.get("baseline_text") or "")
        if not bool(r.get("source_structural_prefix_proven")):
            continue

        payload = str(r.get("baseline_payload_hex") or "").upper()
        direct = bool(r.get("control18_storage_risk"))
        boundary = r.get("baseline_boundary") or {}
        next_control = str(boundary.get("next_control") or "").upper()
        control_lead = next_control[:2]
        control_adjacent = control_lead in {"08", "17"}
        # Canonical contracts now consume the source-proven structural 18, so
        # baseline_text no longer contains the synthetic static `こ`.  Exact
        # text equality here is the post-prefix equivalent of the old
        # "only leading こ differs" test.
        only_leading = norm(current) == norm(expected)

        if address in RUNTIME_BAD:
            tier = "P0_runtime_proven_bad"
            action = "fix_now_drop_control18_preserve_extent"
        elif direct and only_leading and control_adjacent:
            tier = "P1_direct_ext3_exact_text_control_adjacent"
            action = "high_priority_batch_after_anchor_pass"
        elif direct and only_leading:
            tier = "P2_direct_ext3_exact_text"
            action = "second_batch_representative_runtime_test"
        elif direct and control_adjacent:
            tier = "P3_direct_ext3_control_adjacent_text_drift"
            action = "confirm_current_text_then_rehome"
        elif direct:
            tier = "P4_direct_ext3_text_drift"
            action = "review_translation_and_runtime_by_bundle"
        else:
            tier = "P5_non_direct_native_or_other"
            action = "do_not_autofix_static_false_positive_possible"

        idx = order_index[address]
        prev_row = ordered[idx - 1] if idx > 0 else None
        next_row = ordered[idx + 1] if idx + 1 < len(ordered) else None
        predecessor = next(
            (
                x
                for x in ordered[max(0, idx - 4):idx]
                if str((x.get("baseline_boundary") or {}).get("next_address") or "").upper() == address
            ),
            None,
        )
        predecessor_sheet = None if predecessor is None else sheet_rows.get(str(predecessor.get("address") or "").upper())
        source_payload_hex = compact_hex(str(r.get("source_payload_hex") or ""))
        sheet_body_hex = compact_hex(str(s.get("body_hex") or ""))
        source_leading18 = source_payload_hex.startswith("18")
        sheet_body_matches_source_after_prefix = (
            source_leading18 and bool(sheet_body_hex) and source_payload_hex[2:] == sheet_body_hex
        )
        original_japanese = str(r.get("original_japanese") or "")
        sheet_jp = str(s.get("jp") or "")
        original_jp_matches_sheet_after_leading18 = norm(original_japanese) == norm(sheet_jp)
        same_bundle_predecessor = bool(
            predecessor is not None and predecessor.get("bundle_id") == r.get("bundle_id")
        )
        source_structural_prefix_proven = bool(r.get("source_structural_prefix_proven"))
        pred_jp = "" if predecessor_sheet is None else str(predecessor_sheet.get("jp") or "")
        pred_ko = "" if predecessor_sheet is None else str(predecessor_sheet.get("ko") or "")
        jp_combined_cells = cell_len(pred_jp) + cell_len(sheet_jp)
        ko_combined_cells = cell_len(pred_ko) + cell_len(expected)
        translation_overflow_vs_source = bool(
            predecessor_sheet is not None
            and jp_combined_cells <= 20
            and ko_combined_cells > 20
        )
        source_and_target_both_need_split = bool(
            predecessor_sheet is not None
            and jp_combined_cells > 20
            and ko_combined_cells > 20
        )
        rows.append({
            "priority": tier,
            "address": address,
            "bundle_id": r.get("bundle_id"),
            "status": r.get("status"),
            "confidence": r.get("confidence"),
            "sheet_prefix_hex": s.get("prefix_hex"),
            "sheet_body_hex": s.get("body_hex"),
            "sheet_jp": s.get("jp"),
            "sheet_ko": expected,
            "source_leading18": source_leading18,
            "sheet_body_matches_source_after_prefix": sheet_body_matches_source_after_prefix,
            "original_jp_matches_sheet_after_leading18": original_jp_matches_sheet_after_leading18,
            "same_bundle_predecessor": same_bundle_predecessor,
            "source_structural_prefix_proven": source_structural_prefix_proven,
            "predecessor_sheet_jp_cells": cell_len(pred_jp),
            "continuation_sheet_jp_cells": cell_len(sheet_jp),
            "combined_sheet_jp_cells": jp_combined_cells,
            "predecessor_sheet_ko_cells": cell_len(pred_ko),
            "continuation_sheet_ko_cells": cell_len(expected),
            "combined_sheet_ko_cells": ko_combined_cells,
            "translation_overflow_vs_source": translation_overflow_vs_source,
            "source_and_target_both_need_split": source_and_target_both_need_split,
            "current_static_text": current,
            "expected_if_18_is_control": current,
            "only_leading_ko_diff_vs_sheet": only_leading,
            "source_payload_hex": r.get("source_payload_hex"),
            "current_payload_hex": payload,
            "direct_18_E518": direct,
            "nul_run": boundary.get("nul_run"),
            "next_address": boundary.get("next_address"),
            "next_control": next_control,
            "control_adjacent_08_17": control_adjacent,
            "predecessor_address": None if predecessor is None else predecessor.get("address"),
            "predecessor_text": None if predecessor is None else predecessor.get("baseline_text"),
            "previous_address": None if prev_row is None else prev_row.get("address"),
            "previous_text": None if prev_row is None else prev_row.get("baseline_text"),
            "next_dialogue_address": None if next_row is None else next_row.get("address"),
            "next_dialogue_text": None if next_row is None else next_row.get("baseline_text"),
            "runtime_status": (
                "user_reported_bad_current" if address in RUNTIME_BAD
                else "runtime_proven_native_safe" if address in RUNTIME_NATIVE_SAFE
                else "already_repaired_prefix_removed" if not bool(r.get("target_physically_keeps_source_prefix"))
                else "unverified"
            ),
            "recommended_action": action,
        })

    counts = Counter(str(r["priority"]) for r in rows)
    direct_count = sum(bool(r["direct_18_E518"]) for r in rows)
    control_count = sum(bool(r["control_adjacent_08_17"]) for r in rows)
    only_count = sum(bool(r["only_leading_ko_diff_vs_sheet"]) for r in rows)
    structural_proven_count = sum(bool(r["source_structural_prefix_proven"]) for r in rows)
    translation_overflow_count = sum(bool(r["translation_overflow_vs_source"]) for r in rows)
    both_split_count = sum(bool(r["source_and_target_both_need_split"]) for r in rows)

    # Historical fixed anchor proves the distinction: source extraction still has prefix 18,
    # but current payload no longer starts with 18 and the current static text no longer starts こ.
    fixed_rows = []
    for address in sorted(HISTORICAL_FIXED):
        r = by_addr.get(address)
        s = sheet_rows.get(address)
        if r and s:
            fixed_rows.append({
                "address": address,
                "sheet_prefix_hex": s.get("prefix_hex"),
                "sheet_ko": s.get("ko"),
                "current_payload_hex": r.get("baseline_payload_hex"),
                "current_text": r.get("baseline_text"),
            })

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_scenario_continuation_leading18_leakage.py",
        "read_only": True,
        "main": {"path": str(args.rom.resolve().relative_to(ROOT.resolve())), "sha256": sha(args.rom), "size": args.rom.stat().st_size},
        "inputs": {"contracts": str(args.contracts.resolve().relative_to(ROOT.resolve())), "sheet": str(args.sheet.resolve().relative_to(ROOT.resolve()))},
        "root_cause": (
            "translation/extraction already separates prefix_hex=18, but unresolved scenario_continuations are quarantined; "
            "when the current payload remains 18+E518 the static decoder exposes 18 as TBL glyph こ. "
            "Previous repairs were runtime-anchor-specific and did not create a global hard gate for all quarantined continuations."
        ),
        "counts": {
            "residual_candidates": len(rows),
            "current_direct_18_E518": direct_count,
            "current_non_direct": len(rows) - direct_count,
            "immediate_08_or_17_control": control_count,
            "sheet_diff_only_in_leading_ko": only_count,
            "source_structural_prefix_proven": structural_proven_count,
            "translation_overflow_vs_source_20cell": translation_overflow_count,
            "source_and_target_both_over_20cell_combined": both_split_count,
            **dict(sorted(counts.items())),
        },
        "runtime_bad_anchor": "60BB48",
        "historical_fixed_anchor": fixed_rows,
        "rows": rows,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    fields = [
        "priority", "address", "bundle_id", "runtime_status", "recommended_action",
        "sheet_jp", "sheet_ko", "current_static_text", "expected_if_18_is_control",
        "only_leading_ko_diff_vs_sheet", "current_payload_hex", "direct_18_E518",
        "source_payload_hex", "source_leading18", "sheet_body_matches_source_after_prefix",
        "original_jp_matches_sheet_after_leading18", "same_bundle_predecessor", "source_structural_prefix_proven",
        "predecessor_sheet_jp_cells", "continuation_sheet_jp_cells", "combined_sheet_jp_cells",
        "predecessor_sheet_ko_cells", "continuation_sheet_ko_cells", "combined_sheet_ko_cells",
        "translation_overflow_vs_source", "source_and_target_both_need_split",
        "nul_run", "next_control", "control_adjacent_08_17",
        "predecessor_address", "predecessor_text", "next_dialogue_address", "next_dialogue_text",
        "status", "confidence",
    ]
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: clean_for_csv(row.get(k)) for k in fields})

    top = [r for r in rows if r["priority"] in {"P0_runtime_proven_bad", "P1_direct_ext3_exact_text_control_adjacent"}]
    md = [
        "# Scenario continuation 선두 `18 → こ` 구조 검수 시트",
        "",
        f"메인 TIP: `{report['main']['sha256'].upper()}`  ",
        f"전체 residual candidate: **{len(rows):,}건**  ",
        f"현재 `18 + E5 18` direct 구조: **{direct_count:,}건**  ",
        f"직후 `08/17` control 인접: **{control_count:,}건**  ",
        f"과거 번역 시트와 비교해 선두 `こ` 하나만 다른 행: **{only_count:,}건**  ",
        f"원본 payload/시트 body/일본어/동일 bundle predecessor까지 일치해 `18=구조 prefix`가 증명되는 행: **{structural_proven_count:,}건**  ",
        f"원문 결합은 20셀 이하이나 번역 결합이 20셀 초과인 reflow 증거: **{translation_overflow_count:,}건**  ",
        f"원문/번역 결합 모두 20셀 초과라 원본부터 split이 필요한 행: **{both_split_count:,}건**",
        "",
        "## 왜 과거 수정이 전체 반영되지 않았는가",
        "",
        "- 추출 시트는 이미 `prefix_hex=18`과 본문을 분리했지만, runtime contract는 caller trace가 없는 continuation을 `quarantine`으로 둔다.",
        "- 따라서 `18`을 전역적으로 제거하지 않았고, 사용자가 실제 화면 오류를 확인한 주소만 좁게 복구했다.",
        "- `6002F1`은 과거 실측 후 선두 18을 제거해 현재 static text에서도 `こ`가 사라진 반면, `60BB48`은 여전히 `18E518...`라 이번 화면에서 실제 `こ`가 노출됐다.",
        "- 최근 220/59건 복구는 `scenario_first` exact4/제어 인접 문제를 대상으로 했으므로 이 `scenario_continuation` quarantine 집단은 범위 밖이었다.",
        "",
        "## 우선순위",
        "",
        "| Tier | 건수 | 의미 | 권장 처리 |",
        "|---|---:|---|---|",
    ]
    descriptions = {
        "P0_runtime_proven_bad": "현재 사용자 실측 오류", 
        "P1_direct_ext3_exact_text_control_adjacent": "선두 こ만 불일치 + direct ext3 + 즉시 08/17", 
        "P2_direct_ext3_exact_text": "선두 こ만 불일치 + direct ext3", 
        "P3_direct_ext3_control_adjacent_text_drift": "direct ext3 + 즉시 제어, 번역문은 이후 변경 이력 있음", 
        "P4_direct_ext3_text_drift": "direct ext3, 번역문 변경 이력 있음", 
        "P5_non_direct_native_or_other": "native/기타 경로; 정적 false positive 가능",
    }
    actions = {
        "P0_runtime_proven_bad": "즉시 좁은 후보 수정/실측",
        "P1_direct_ext3_exact_text_control_adjacent": "P0 PASS 후 1차 일괄 후보",
        "P2_direct_ext3_exact_text": "2차 bundle 단위 후보",
        "P3_direct_ext3_control_adjacent_text_drift": "현행 번역 확인 후 구조만 수정",
        "P4_direct_ext3_text_drift": "문맥 검토 후 단계 처리",
        "P5_non_direct_native_or_other": "자동 수정 금지",
    }
    for tier in [
        "P0_runtime_proven_bad", "P1_direct_ext3_exact_text_control_adjacent", "P2_direct_ext3_exact_text",
        "P3_direct_ext3_control_adjacent_text_drift", "P4_direct_ext3_text_drift", "P5_non_direct_native_or_other",
    ]:
        md.append(f"| `{tier}` | {counts.get(tier, 0):,} | {descriptions[tier]} | {actions[tier]} |")

    md += [
        "",
        "## P0 / P1 상위 검수 대상",
        "",
        "| Tier | 주소 | 현재 static | `18` 제어 시 기대 | 직후 control | 이전 문맥 | 다음 대사 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in top[:80]:
        md.append(
            f"| `{row['priority']}` | `{row['address']}` | {clean_for_csv(row['current_static_text'])} | "
            f"{clean_for_csv(row['expected_if_18_is_control'])} | `{row['next_control']}` | "
            f"{clean_for_csv(row['predecessor_text'])} | {clean_for_csv(row['next_dialogue_text'])} |"
        )
    md += [
        "",
        "전체 2,849건은 CSV에서 필터링한다. `P5`는 이미 runtime-proven native-only 행이 섞일 수 있으므로 자동 수정하면 안 된다.",
        "",
        "## 회귀 기준",
        "",
        "- bad anchor: `60BB48` — 화면에서 `こ뜻입니까！？` 재현.",
        "- historical fixed anchor: `6002F1` — 과거 선두 `18=こ` 실측 후 payload를 18 없이 rehome하여 정상화.",
        "- runtime-native-safe anchors: `63449B`, `635855`, `635BFB`, `635866`, `635C0C` — static `18` 해석만 보고 일괄 삭제하면 안 되는 반례군.",
    ]
    args.out_md.write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")

    print(json.dumps({"counts": report["counts"], "json": str(args.out_json.resolve().relative_to(ROOT.resolve())), "csv": str(args.out_csv.resolve().relative_to(ROOT.resolve())), "md": str(args.out_md.resolve().relative_to(ROOT.resolve()))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
