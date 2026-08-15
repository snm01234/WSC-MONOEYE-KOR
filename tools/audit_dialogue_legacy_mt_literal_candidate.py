#!/usr/bin/env python3
"""Independent acceptance audit for dialogue_legacy_mt_literal_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = ROOT / "out/patch/dialogue_legacy_mt_literal_candidate.wsc"
CAND_SAVE = ROOT / "sram/dialogue_legacy_mt_literal_candidate.sav"
BATCH_GLOB = "data/dialogue_legacy_mt_literal_batch*.json"
WORKLIST = ROOT / "out/script/dialogue_legacy_source_retranslation_worklist.json"
CONTEXT_WORKLIST = ROOT / "out/script/dialogue_context_neighborhood_worklist.json"
WIDTH = ROOT / "out/patch/dialogue_legacy_mt_literal_width_audit.json"
WIDTH_MAIN = ROOT / "out/patch/dialogue_legacy_mt_literal_main_width_baseline.json"
TERM = ROOT / "out/patch/dialogue_legacy_mt_literal_terminator_audit.json"
FALSE_SEG = ROOT / "out/patch/dialogue_legacy_mt_literal_false_segptr.json"
TERMS = ROOT / "out/patch/dialogue_legacy_mt_literal_terminology_audit.json"
SMOKE = ROOT / "out/patch/dialogue_legacy_mt_literal_smoke.json"
BUILD_REPORT = ROOT / "out/patch/dialogue_legacy_mt_literal_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/dialogue_legacy_mt_literal_acceptance_audit.json"
EXPECTED_MAIN = "6425767be35813bf09e1fd2b223b98a9cd05d804cba254456e5d93f00a0a4f3c"
JP_RE = re.compile(r"[ぁ-んァ-ン一-龥]")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    main = MAIN.read_bytes()
    cand = CAND.read_bytes()
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    by_abs = {str(row["abs"]).upper(): row for row in work.get("records") or []}
    context_work = json.loads(CONTEXT_WORKLIST.read_text(encoding="utf-8"))
    context_by_abs = {str(row["abs"]).upper(): row for row in context_work.get("records") or []}
    targets: dict[str, str] = {}
    direct_source_jp: dict[str, str] = {}
    batch_rows: list[dict[str, Any]] = []
    for path in sorted(ROOT.glob(BATCH_GLOB)):
        batch = json.loads(path.read_text(encoding="utf-8"))
        if batch.get("translation_source") != "llm" or batch.get("review_status") != "approved_for_test_candidate":
            raise RuntimeError(f"invalid batch provenance: {path.name}")
        local_sources = batch.get("source_jp") or {}
        for raw_address, raw_text in (batch.get("targets") or {}).items():
            address = str(raw_address).upper()
            text = str(raw_text).replace(" ", "　")
            if address in targets and targets[address] != text:
                raise RuntimeError(f"conflicting target across batches at {address}")
            targets[address] = text
            if address in local_sources:
                source = str(local_sources[address])
                if address in direct_source_jp and direct_source_jp[address] != source:
                    raise RuntimeError(f"conflicting Japanese source across batches at {address}")
                direct_source_jp[address] = source
        batch_rows.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "targets": len(batch.get("targets") or {})})

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    md = make_dictionary_ext3(main, ext_meta, ext3_meta)
    cd = make_dictionary_ext3(cand, ext_meta, ext3_meta)
    sb = stock_base(main)

    rows: list[dict[str, Any]] = []
    target_failures: list[dict[str, Any]] = []
    for address in sorted(targets, key=lambda x: int(x, 16)):
        logical = int(address, 16)
        before_got = read_encoded_z_safe(main, sb + logical, max_len=256)
        after_got = read_encoded_z_safe(cand, sb + logical, max_len=256)
        if before_got is None or after_got is None:
            target_failures.append({"abs": address, "reason": "unreadable"})
            continue
        before_payload, before_term = bytes(before_got[0]), int(before_got[1])
        after_payload, after_term = bytes(after_got[0]), int(after_got[1])
        bp, bb, before_kind = split_prefix_body(before_payload)
        ap, ab, after_kind = split_prefix_body(after_payload)
        before_text = md.expand(bb, tbl).rstrip("　 \t")
        after_text = cd.expand(ab, tbl).rstrip("　 \t")
        spec = by_abs.get(address)
        if spec is None:
            context_spec = context_by_abs.get(address)
            explicit_source = direct_source_jp.get(address)
            source_jp = explicit_source
            if source_jp is None and context_spec is not None:
                source_jp = str(context_spec.get("jp") or "")
            source_bound = bool(source_jp) and (
                (context_spec is not None and str(context_spec.get("jp") or "") == source_jp)
                or (context_spec is None and explicit_source is not None)
            )
            if (
                source_bound
                and address not in {"630695", "63CFEA"}
                and 0x600000 <= logical <= 0x63FFFF
                and before_kind == "dialogue"
            ):
                positions = [pos for pos in range(max(0, len(bb) - 3)) if bb[pos:pos + 2] == b"\xE5\x18"]
                if len(positions) == 1:
                    direct_route = "existing_ext3_portal"
                    direct_portal_offset = positions[0]
                elif not positions and len(bb) >= 4:
                    direct_route = "retarget_body_to_ext3"
                    direct_portal_offset = 0
                else:
                    direct_route = "unsupported"
                    direct_portal_offset = -1
                spec = {
                    "route": direct_route,
                    "portal_offset": direct_portal_offset,
                    "current_render": before_text,
                    "jp": source_jp,
                    "context_source": True,
                }
        spec = spec or {}
        route = str(spec.get("route") or "")
        if route == "existing_ext3_portal":
            pos = int(spec.get("portal_offset", -1))
            portal_ok = (
                pos >= 0
                and bb[pos:pos + 2] == b"\xE5\x18"
                and ab[pos:pos + 2] == b"\xE5\x18"
            )
            structure_ok = (
                len(before_payload) == len(after_payload)
                and bp == ap
                and before_term == after_term
                and len(bb) == len(ab)
                and portal_ok
                and bb[:pos] == ab[:pos]
                and bb[pos + 4:] == ab[pos + 4:]
            )
        elif route == "retarget_body_to_ext3":
            pos = 0
            portal_ok = len(ab) >= 4 and ab[:2] == b"\xE5\x18"
            structure_ok = (
                len(before_payload) == len(after_payload)
                and bp == ap
                and before_term == after_term
                and len(bb) == len(ab)
                and portal_ok
                and ab[4:] == b"\x01" * (len(ab) - 4)
            )
        else:
            pos = -1
            portal_ok = False
            structure_ok = False
        desired = targets[address]
        ok = (
            before_text == str(spec.get("current_render") or "")
            and after_text == desired
            and structure_ok
            and len(after_text.replace("<E62F>", "")) <= 20
            and not JP_RE.search(after_text)
            and route in {"existing_ext3_portal", "retarget_body_to_ext3"}
            and before_kind == "dialogue"
            and after_kind == "dialogue"
        )
        row = {
            "abs": address,
            "before": before_text,
            "after": after_text,
            "cells": len(after_text.replace("<E62F>", "")),
            "route": route,
            "prefix_exact": bp == ap,
            "record_extent_exact": len(before_payload) == len(after_payload),
            "terminator_exact": before_term == after_term,
            "portal_shape_exact": portal_ok,
            "route_structure_ok": structure_ok,
            "jp_residual": bool(JP_RE.search(after_text)),
            "ok": ok,
        }
        rows.append(row)
        if not ok:
            target_failures.append(row)

    width = json.loads(WIDTH.read_text(encoding="utf-8"))
    width_main = json.loads(WIDTH_MAIN.read_text(encoding="utf-8"))
    candidate_offenders = {str(r["abs"]) for r in width.get("offenders") or []}
    main_offenders = {str(r["abs"]) for r in width_main.get("offenders") or []}
    term = json.loads(TERM.read_text(encoding="utf-8"))
    false_seg = json.loads(FALSE_SEG.read_text(encoding="utf-8"))
    terms = json.loads(TERMS.read_text(encoding="utf-8"))
    smoke = json.loads(SMOKE.read_text(encoding="utf-8"))
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))

    false_seg_count = int(false_seg.get("sites_found", 0) or 0)
    terminology_clean = (
        not (terms.get("active_source_hits") or [])
        and not (terms.get("dictionary_hits") or [])
        and not (terms.get("rendered_record_hits") or [])
        and terms.get("status") == "clean"
    )
    smoke_tip_zero = all(int(v or 0) == 0 for v in (smoke.get("unit_vs_tip_nonzero") or {}).values())

    grips_stock_expected = {
        0x613317: "브라이트　함장！",
        0x513C7A: "브라이트　함장－",
        0x6116F3: "브라이트　함장은",
        0x6192C6: "밀리샤？",
        0x5D794C: "미안하지만……끝장을　낸다！！",
        0x5D7B2E: "미안하지만……끝장을　낸다！！",
    }
    grips_stock_rows: list[dict[str, Any]] = []
    grips_stock_ok = True
    for logical, expected in grips_stock_expected.items():
        got = read_encoded_z_safe(cand, sb + logical, max_len=96)
        if got is None:
            grips_stock_rows.append({"abs": f"{logical:06X}", "expected": expected, "rendered": None, "ok": False})
            grips_stock_ok = False
            continue
        _p, body, _k = split_prefix_body(bytes(got[0]))
        rendered = cd.expand(body, tbl).rstrip("　 \t")
        ok = rendered == expected
        grips_stock_rows.append({"abs": f"{logical:06X}", "expected": expected, "rendered": rendered, "ok": ok})
        grips_stock_ok = grips_stock_ok and ok
    build_stock_rows = build.get("stock_scene_fixes") or []
    grips_stock_report_ok = len(build_stock_rows) == 6 and all(bool(r.get("ok")) for r in build_stock_rows)

    gates = {
        "main_identity_ok": sha(main) == EXPECTED_MAIN,
        "target_population_ok": len(targets) > 0 and int(build.get("targets", -1)) == len(targets),
        "all_targets_render_exact": not target_failures and len(rows) == len(targets),
        "all_target_rows_le20": max((row["cells"] for row in rows), default=999) <= 20,
        "all_target_prefixes_exact": all(row["prefix_exact"] for row in rows),
        "all_target_extents_exact": all(row["record_extent_exact"] for row in rows),
        "all_target_terminators_exact": all(row["terminator_exact"] for row in rows),
        "all_target_portal_shapes_exact": all(row["portal_shape_exact"] for row in rows),
        "all_target_jp_residual_zero": not any(row["jp_residual"] for row in rows),
        "compact3_disabled": ext3_meta.get("compact3") in (None, False) and not bool(build.get("compact3_used")),
        "unexpected_diff_bytes_zero": int(build.get("unexpected_diff_bytes", -1)) == 0,
        "false_segmented_pointer_writes_zero": false_seg_count == 0,
        "p2_runtime_risk_zero": bool(term.get("ok")) and int((term.get("counts") or {}).get("runtime_risk", -1)) == 0 and int((term.get("counts") or {}).get("separator_nul_lost", -1)) == 0,
        "terminology_clean": terminology_clean,
        "candidate_width_offenders_same_as_main": candidate_offenders == main_offenders,
        "candidate_width_offenders_are_preexisting_only": candidate_offenders == {"630695", "63CFEA"},
        "unit_banks_candidate_equals_main": smoke_tip_zero,
        "opening_required_ok": bool(smoke.get("opening_required_ok")),
        "hangul_samples_ok": bool(smoke.get("hangul_ok")),
        "same_stem_save_matches_live": CAND_SAVE.read_bytes() == MAIN_SAVE.read_bytes(),
        "grips_stock_short_body_fix_ok": grips_stock_ok and grips_stock_report_ok,
    }
    overall_ok = all(gates.values())
    report = {
        "schema_version": 1,
        "candidate": str(CAND.relative_to(ROOT)).replace("\\", "/"),
        "candidate_sha256": sha(cand),
        "batches": batch_rows,
        "targets": len(targets),
        "max_target_cells": max(row["cells"] for row in rows),
        "preexisting_width_offenders": sorted(candidate_offenders),
        "smoke_original_baseline_overall_ok": bool(smoke.get("overall_ok")),
        "smoke_note": "legacy all-stages smoke still fails against original JP in pre-existing unit/table ranges; unit_vs_tip_nonzero is zero, so this candidate adds no unit-bank drift",
        "gates": gates,
        "target_failures": target_failures,
        "grips_stock_short_body_rows": grips_stock_rows,
        "overall_ok": overall_ok,
        "rows": rows,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_sha256": report["candidate_sha256"], "targets": len(targets), "max_target_cells": report["max_target_cells"], "preexisting_width_offenders": report["preexisting_width_offenders"], "gates": gates, "overall_ok": overall_ok}, ensure_ascii=False, indent=2))
    print(OUT)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
