#!/usr/bin/env python3
"""Independent Phase D audit for exact-continuation native recovery candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_scenario_page_boundary_guard_candidate import original_unit_kinds  # noqa: E402
from dialogue_runtime_contracts import audit_manifest, build_manifest, write_manifest  # noqa: E402
from mixed_residual_reference_union import iter_token_refs_with_offsets  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base, token_from_dict_index  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/exact_continuation_native_recovery_candidate.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/exact_continuation_native_recovery_exact_audit.json"
DEFAULT_MANIFEST = ROOT / "out/patch/exact_continuation_native_recovery_runtime_contracts.json"
DEFAULT_SAFETY = ROOT / "out/patch/exact_continuation_native_recovery_runtime_safety.json"

EXPECTED_MAIN_SHA = "fc7c3a426c866f8b60f5056571349c79d6ba11a2632beee4209dfebbf8a0c5e9"
EXPECTED_TARGET_SHA = "f68b3261beecc32047d17952e36bc2b891cd5d66410f9fc9293487571a0fc8e2"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"

DUPLICATES = {0x0F70: 0x0F00, 0x0F72: 0x0F01, 0x0FC0: 0x0F07}
HELPERS: dict[int, tuple[int, str, bytes]] = {
    0x0F59: (0x1240, "설마", bytes.fromhex("EC8DE830E7AF")),
    0x0F6D: (0x1280, "큭", bytes.fromhex("EC8DE85E")),
    0x0F70: (0x12C0, "후후", bytes.fromhex("EC8DE7C0E7C0")),
    0x0FC0: (0x1300, "이걸로", bytes.fromhex("EC8DE743E85CE748")),
    0x0F72: (0x1330, "명심해라", bytes.fromhex("EC8DE83AE87AE7AAE7A1")),
}
TARGETS: dict[int, tuple[bytes, str]] = {
    0x609A83: (bytes.fromhex("18FF59F191"), "설마……"),
    0x60D194: (bytes.fromhex("18FF6DF191"), "큭……"),
    0x60F27C: (bytes.fromhex("18FF70F191"), "후후……"),
    0x61010E: (bytes.fromhex("18FF72F191"), "명심해라……"),
    0x61802F: (bytes.fromhex("18FF70FF07"), "후후후후……"),
    0x62439F: (bytes.fromhex("18FF59F191"), "설마……"),
    0x628AB8: (bytes.fromhex("18FF70FF07"), "후후후후……"),
    0x62CC7D: (bytes.fromhex("18FF70FF07"), "후후후후……"),
    0x63A9F8: (bytes.fromhex("18FFC0F191"), "이걸로……"),
}
EXPECTED_CONSUMERS: dict[int, set[int]] = {
    0x0F59: {0x609A83, 0x62439F},
    0x0F6D: {0x60D194},
    0x0F70: {0x60F27C, 0x61802F, 0x628AB8, 0x62CC7D},
    0x0FC0: {0x63A9F8},
    0x0F72: {0x61010E},
}

# These two rows match the broad byte-shape predicate, but they are deliberately
# outside this recovery plan.  The earlier v3 experiment targeted them as a
# Katejina duplicate hypothesis; runtime retest showed that hypothesis did not
# address the reported STAGE21t branch, and v4 intentionally returned to the v1
# parent and fixed the actual 63463A row instead.  They are therefore protected
# as unchanged baseline rows, not silently counted as part of the plan's nine.
EXCLUDED_PRIOR_V3_HYPOTHESES = {0x624305, 0x6335A6}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_record(rom: bytes, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def next_non_nul(rom: bytes, term: int) -> tuple[int, bytes]:
    sb = stock_base(rom)
    p = term + 1
    while p < term + 32 and rom[sb + p] == 0:
        p += 1
    return p, bytes(rom[sb + p:sb + p + 12])


def control_following_exact_risks(manifest: dict[str, Any], rom: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in manifest.get("contracts") or []:
        if row.get("route") != "scenario_continuation":
            continue
        source = bytes.fromhex(str(row.get("source_body_hex") or ""))
        current = bytes.fromhex(str(row.get("baseline_body_hex") or ""))
        if len(source) != 5 or source[:1] != b"\x18":
            continue
        if original_unit_kinds(source) != ["char1", "dict", "dict"]:
            continue
        if not current.startswith(b"\x18\xE5\x18"):
            continue
        logical = int(row["address_int"])
        _payload, term = read_record(rom, logical)
        p, following = next_non_nul(rom, term)
        if following.startswith(b"\x17"):
            rows.append({
                "abs": str(row["address"]),
                "body_hex": current.hex().upper(),
                "next_control_abs": f"{p:06X}",
                "next_control_hex": following.hex().upper(),
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--manifest-out", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--safety-out", type=Path, default=DEFAULT_SAFETY)
    args = ap.parse_args()

    # Plain read_bytes avoids mutable global bank-base state during identity checks.
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    target = args.target.read_bytes()
    failures: list[dict[str, Any]] = []
    if sha(parent) != EXPECTED_MAIN_SHA:
        failures.append({"reason": "main_sha_drift", "actual": sha(parent)})
    if sha(original) != EXPECTED_ORIGINAL_SHA:
        failures.append({"reason": "original_sha_drift", "actual": sha(original)})
    if sha(target) != EXPECTED_TARGET_SHA:
        failures.append({"reason": "candidate_sha_drift", "actual": sha(target), "expected": EXPECTED_TARGET_SHA})

    tbl = Tbl.load(TBL)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    dictionary = make_dictionary_ext3(target, ext_meta, ext3_meta)

    parent_manifest = build_manifest(original, parent, target_path=MAIN)
    target_manifest = build_manifest(original, target, target_path=args.target)
    parent_risks_all = control_following_exact_risks(parent_manifest, parent)
    target_risks_all = control_following_exact_risks(target_manifest, target)
    parent_risks = [r for r in parent_risks_all if int(r["abs"], 16) not in EXCLUDED_PRIOR_V3_HYPOTHESES]
    target_risks = [r for r in target_risks_all if int(r["abs"], 16) not in EXCLUDED_PRIOR_V3_HYPOTHESES]
    parent_excluded = [r for r in parent_risks_all if int(r["abs"], 16) in EXCLUDED_PRIOR_V3_HYPOTHESES]
    target_excluded = [r for r in target_risks_all if int(r["abs"], 16) in EXCLUDED_PRIOR_V3_HYPOTHESES]
    if len(parent_risks) != 9:
        failures.append({"reason": "parent_selected_exact_control_risk_count", "actual": len(parent_risks), "expected": 9})
    if target_risks:
        failures.append({"reason": "candidate_selected_exact_control_risk_remaining", "rows": target_risks})
    if {int(r["abs"], 16) for r in parent_excluded} != EXCLUDED_PRIOR_V3_HYPOTHESES:
        failures.append({"reason": "excluded_prior_v3_baseline_drift", "rows": parent_excluded})
    if {int(r["abs"], 16) for r in target_excluded} != EXCLUDED_PRIOR_V3_HYPOTHESES:
        failures.append({"reason": "excluded_prior_v3_candidate_shape_drift", "rows": target_excluded})
    excluded_rows: list[dict[str, Any]] = []
    for logical in sorted(EXCLUDED_PRIOR_V3_HYPOTHESES):
        before, before_term = read_record(parent, logical)
        after, after_term = read_record(target, logical)
        unchanged = before == after and before_term == after_term
        if not unchanged:
            failures.append({"reason": "excluded_prior_v3_row_changed", "abs": f"{logical:06X}"})
        excluded_rows.append({
            "abs": f"{logical:06X}",
            "payload_hex": after.hex().upper(),
            "terminator": f"{after_term:06X}",
            "byte_exact_to_main": unchanged,
            "reason": "prior v3 wrong-branch/duplicate hypothesis; explicitly outside current 9-row recovery scope",
        })

    target_rows: list[dict[str, Any]] = []
    for logical, (expected_payload, expected_text) in TARGETS.items():
        before, before_term = read_record(parent, logical)
        payload, term = read_record(target, logical)
        source, source_term = read_record(original, logical)
        tokens = list(iter_token_refs_with_offsets(payload[1:], ext3_aware=True)) if payload.startswith(b"\x18") else []
        rendered = dictionary.expand(payload[1:], tbl).rstrip("\u3000 \t") if payload.startswith(b"\x18") else ""
        p0, control0 = next_non_nul(parent, before_term)
        p1, control1 = next_non_nul(target, term)
        ok = (
            payload == expected_payload
            and len(payload) == 5
            and payload[0] == 0x18
            and len(tokens) == 2
            and all(length == 2 for _index, length, _offset in tokens)
            and b"\xE5\x18" not in payload[1:]
            and b"\xE5\x19" not in payload[1:]
            and rendered == expected_text
            and term == before_term == source_term
            and p1 == p0
            and control1 == control0
            and control1.startswith(b"\x17")
        )
        if not ok:
            failures.append({
                "reason": "target_contract_failed",
                "abs": f"{logical:06X}",
                "before": before.hex().upper(),
                "source": source.hex().upper(),
                "payload": payload.hex().upper(),
                "rendered": rendered,
                "tokens": tokens,
                "control_before": control0.hex().upper(),
                "control_after": control1.hex().upper(),
            })
        target_rows.append({
            "abs": f"{logical:06X}",
            "payload_hex": payload.hex().upper(),
            "rendered": rendered,
            "terminator": f"{term:06X}",
            "next_control_abs": f"{p1:06X}",
            "next_control_hex": control1.hex().upper(),
            "native_two_token": len(tokens) == 2 and all(length == 2 for _i, length, _o in tokens),
            "direct_e518": b"\xE5\x18" in payload[1:],
            "compact3": b"\xE5\x19" in payload[1:],
            "ok": ok,
        })

    helper_external = external_occurrence_map(target, ext3_aware=True, wanted=set(HELPERS))
    helper_nested = nested_occurrence_map(dictionary, wanted=set(HELPERS), ext3_aware=True)
    helper_rows: list[dict[str, Any]] = []
    for index, (expected_ptr, expected_text, expected_raw) in HELPERS.items():
        raw = bytes(dictionary.raw_entry(index))
        rendered = dictionary.expand_index(index, tbl)
        refs = helper_external.get(index, [])
        actual_records = {int(str(r["record_abs"]), 16) for r in refs}
        nested = helper_nested.get(index, [])
        ok = (
            int(dictionary.ptrs[index]) == expected_ptr
            and raw == expected_raw
            and rendered == expected_text
            and actual_records == EXPECTED_CONSUMERS[index]
            and all(str(r.get("region")) == "script" for r in refs)
            and not nested
        )
        if not ok:
            failures.append({
                "reason": "helper_contract_failed",
                "index": f"{index:04X}",
                "pointer": f"{int(dictionary.ptrs[index]):04X}",
                "raw": raw.hex().upper(),
                "rendered": rendered,
                "actual_consumers": [f"{x:06X}" for x in sorted(actual_records)],
                "nested": nested,
            })
        helper_rows.append({
            "index": f"{index:04X}",
            "pointer": f"{int(dictionary.ptrs[index]):04X}",
            "raw_hex": raw.hex().upper(),
            "rendered": rendered,
            "consumer_records": [f"{x:06X}" for x in sorted(actual_records)],
            "nested_consumers": len(nested),
            "ok": ok,
        })

    # Independent diff scope: re-derive the 21 duplicate consumer sites from
    # the parent and combine them with the five pointers, five helper payloads,
    # nine exact records and checksum.  Any other byte difference fails closed.
    allowed: list[tuple[int, int]] = []
    sb = stock_base(parent)
    duplicate_rows: list[dict[str, Any]] = []
    ext_before = external_occurrence_map(parent, ext3_aware=True, wanted=set(DUPLICATES))
    for reclaim, keeper in DUPLICATES.items():
        old = bytes(token_from_dict_index(reclaim))
        new = bytes(token_from_dict_index(keeper))
        expected_counts = {0x0F70: 4, 0x0F72: 7, 0x0FC0: 10}
        refs = ext_before.get(reclaim, [])
        if len(refs) != expected_counts[reclaim] or any(str(r.get("region")) != "script" for r in refs):
            failures.append({"reason": "duplicate_parent_consumer_drift", "index": f"{reclaim:04X}", "count": len(refs)})
        for ref in refs:
            logical_token = int(str(ref["token_abs"]), 16)
            off = sb + logical_token
            if parent[off:off + 2] != old or target[off:off + 2] != new:
                failures.append({"reason": "duplicate_retarget_failed", "token_abs": f"{logical_token:06X}"})
            allowed.append((off, off + 2))
            duplicate_rows.append({
                "reclaim": f"{reclaim:04X}",
                "keeper": f"{keeper:04X}",
                "token_abs": f"{logical_token:06X}",
                "before_hex": old.hex().upper(),
                "after_hex": new.hex().upper(),
            })

    bank10_abs = 0x10 * 0x10000
    stock_count = int(ext_meta["stock_count"])
    for index, (ptr, _text, raw) in HELPERS.items():
        local = index - stock_count
        ptr_file = bank10_abs + local * 2
        allowed.append((ptr_file, ptr_file + 2))
        phrase_file = bank10_abs + ptr
        allowed.append((phrase_file, phrase_file + len(raw) + 1))
    for logical in TARGETS:
        allowed.append((sb + logical, sb + logical + 5))
    allowed.append((len(target) - 2, len(target)))

    runs = diff_runs(parent, target)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        failures.append({"reason": "unexpected_diff_runs", "runs": unexpected[:32]})

    # Runtime contract/safety reports are generated from this candidate itself.
    write_manifest(args.manifest_out, target_manifest)
    safety = audit_manifest(target, target_manifest, target_path=args.target)
    args.safety_out.parent.mkdir(parents=True, exist_ok=True)
    args.safety_out.write_text(json.dumps(safety, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if safety.get("ok") is not True or int((safety.get("counts") or {}).get("hard_failures", -1)) != 0 or int((safety.get("counts") or {}).get("review_items", -1)) != 0:
        failures.append({"reason": "runtime_safety_failed", "counts": safety.get("counts")})

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_exact_continuation_native_recovery_candidate.py",
        "ok": not failures,
        "target": {"path": str(args.target), "sha256": sha(target), "size": len(target)},
        "parent": {"path": str(MAIN), "sha256": sha(parent)},
        "counts": {
            "parent_broad_control_following_exact_risks": len(parent_risks_all),
            "target_broad_control_following_exact_risks": len(target_risks_all),
            "parent_selected_control_following_exact_risks": len(parent_risks),
            "target_selected_control_following_exact_risks": len(target_risks),
            "removed_selected_control_following_exact_risks": len(parent_risks) - len(target_risks),
            "excluded_prior_v3_hypotheses": len(excluded_rows),
            "target_records": len(target_rows),
            "native_two_token_records": sum(1 for r in target_rows if r["native_two_token"]),
            "direct_e518_records": sum(1 for r in target_rows if r["direct_e518"]),
            "compact3_records": sum(1 for r in target_rows if r["compact3"]),
            "helpers": len(helper_rows),
            "duplicate_retargets": len(duplicate_rows),
            "diff_runs": len(runs),
            "unexpected_diff_runs": len(unexpected),
            "runtime_hard_failures": int((safety.get("counts") or {}).get("hard_failures", -1)),
            "runtime_review_items": int((safety.get("counts") or {}).get("review_items", -1)),
            "failures": len(failures),
        },
        "scope_policy": {
            "selected_plan_rows": 9,
            "broad_shape_rows_outside_plan": [f"{x:06X}" for x in sorted(EXCLUDED_PRIOR_V3_HYPOTHESES)],
            "exclusion_reason": "v3 patched the wrong Katejina duplicate hypothesis; v4 intentionally discarded v2/v3 ancestry and fixed actual 63463A. Current plan preserves those two rows byte-exact.",
        },
        "parent_risks": parent_risks,
        "remaining_risks": target_risks,
        "excluded_prior_v3_rows": excluded_rows,
        "targets": target_rows,
        "helpers": helper_rows,
        "duplicate_retargets": duplicate_rows,
        "diff_scope": {"allowed_ranges": len(allowed), "unexpected_runs": unexpected},
        "runtime_contracts": {"path": str(args.manifest_out), "sha256": hashlib.sha256(args.manifest_out.read_bytes()).hexdigest()},
        "runtime_safety": {"path": str(args.safety_out), "ok": safety.get("ok"), "counts": safety.get("counts")},
        "failures": failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "counts": report["counts"], "report": str(args.out)}, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
