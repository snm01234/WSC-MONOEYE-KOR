#!/usr/bin/env python3
"""Exact audit for scenario continuation native follow-up candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_scenario_continuation_native_followup_candidate import (  # noqa: E402
    DOCTOR_J,
    FOUR_ZERO,
    PROACTIVE_TWO_TOKEN,
)
from build_scenario_continuation_native_followup_v2_candidate import (  # noqa: E402
    RECORD_AFTER as DOCTOR_J_FOLLOWUP_AFTER,
    TARGETS as DOCTOR_J_FOLLOWUPS,
    WRAPPER_AFTER as DOCTOR_J_WRAPPER_AFTER,
    WRAPPER_SLOT as DOCTOR_J_WRAPPER_SLOT,
)
from build_scenario_continuation_native_followup_v3_candidate import (  # noqa: E402
    HELPER_RAW as KATEJINA_HELPER_RAW,
    HELPER_SLOT as KATEJINA_HELPER_SLOT,
    TARGETS as KATEJINA_NATIVE_TARGETS,
)
from build_scenario_page_boundary_guard_candidate import original_unit_kinds  # noqa: E402
from dialogue_runtime_contracts import build_manifest  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_TARGET = ROOT / "out/patch/scenario_continuation_native_followup_v3_candidate.wsc"
DEFAULT_OUT = ROOT / "out/patch/scenario_continuation_native_followup_v3_exact_audit.json"
EXPECTED_MAIN_SHA = "2bccff99a36cd453bf01225365a40ed4d744049e69d46e726ab427851afa1799"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_record(rom: bytes, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise RuntimeError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def risk_rows(manifest: dict) -> tuple[list[dict], list[dict]]:
    broad: list[dict] = []
    exact: list[dict] = []
    for row in manifest.get("contracts") or []:
        if row.get("route") != "scenario_continuation":
            continue
        source = bytes.fromhex(row["source_body_hex"])
        target = bytes.fromhex(row["baseline_body_hex"])
        if not source.startswith(b"\x18") or not target.startswith(b"\x18\xE5\x18"):
            continue
        broad.append(row)
        if len(source) == 5 and original_unit_kinds(source) == ["char1", "dict", "dict"]:
            exact.append(row)
    return broad, exact


def control_following_exact_risks(manifest: dict, rom: bytes) -> list[dict]:
    """Exact Original 18+dict+dict rows whose current portal is followed by a control row."""
    out: list[dict] = []
    sb = stock_base(rom)
    for row in manifest.get("contracts") or []:
        if row.get("route") != "scenario_continuation":
            continue
        source = bytes.fromhex(row["source_body_hex"])
        target = bytes.fromhex(row["baseline_body_hex"])
        if (
            len(source) != 5
            or original_unit_kinds(source) != ["char1", "dict", "dict"]
            or not source.startswith(b"\x18")
            or not target.startswith(b"\x18\xE5\x18")
        ):
            continue
        logical = int(row["address_int"])
        _payload, term = read_record(rom, logical)
        p = term + 1
        while p < term + 8 and rom[sb + p] == 0:
            p += 1
        if p < len(rom) - sb and rom[sb + p] == 0x17:
            out.append({
                "abs": row["address"],
                "current": row.get("baseline_text", ""),
                "next_control_abs": f"{p:06X}",
                "next_control_hex": rom[sb + p:sb + p + 8].hex().upper(),
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    target = args.target.read_bytes()
    failures: list[dict] = []
    if sha(parent) != EXPECTED_MAIN_SHA:
        failures.append({"reason": "main_sha_drift", "sha256": sha(parent)})

    parent_manifest = build_manifest(original, parent, target_path=MAIN)
    target_manifest = build_manifest(original, target, target_path=args.target)
    parent_broad, parent_exact = risk_rows(parent_manifest)
    target_broad, target_exact = risk_rows(target_manifest)

    # The three user-runtime-proven anchors are now active control-18 contracts,
    # so they are intentionally excluded from this *unresolved* risk inventory.
    if len(parent_broad) != 2774 or len(target_broad) != 2753:
        failures.append({"reason": "broad_risk_count_drift", "parent": len(parent_broad), "target": len(target_broad)})
    if len(parent_exact) != 50 or len(target_exact) != 29:
        failures.append({"reason": "exact_pair_risk_count_drift", "parent": len(parent_exact), "target": len(target_exact)})
    parent_control_following = control_following_exact_risks(parent_manifest, parent)
    target_control_following = control_following_exact_risks(target_manifest, target)
    if len(parent_control_following) != 21 or len(target_control_following) != 9:
        failures.append({
            "reason": "control_following_exact_risk_count_drift",
            "parent": len(parent_control_following),
            "target": len(target_control_following),
        })

    tbl = Tbl.load(TBL)
    dictionary = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    by_abs = {row["address"]: row for row in target_manifest["contracts"]}
    anchor_rows: list[dict] = []
    for logical, expected in {
        FOUR_ZERO: "어째서……",
        DOCTOR_J[0]: "……뭐、　승산　좋은　도박？",
        DOCTOR_J[1]: "……뭐、　승산　좋은　도박？",
    }.items():
        row = by_abs[f"{logical:06X}"]
        payload, _term = read_record(target, logical)
        rendered = dictionary.expand(payload[1:], tbl).rstrip("　 \t")
        ok = (
            row["status"] == "active"
            and row["control_prefix_hex"] == "18"
            and row["decoder"] == {"native_stock": True, "ext3": False, "compact3": False}
            and payload.startswith(b"\x18")
            and b"\xE5\x18" not in payload[1:]
            and rendered == expected
        )
        if not ok:
            failures.append({"reason": "runtime_anchor_failed", "abs": f"{logical:06X}", "rendered": rendered, "contract": row})
        anchor_rows.append({"abs": f"{logical:06X}", "payload_hex": payload.hex().upper(), "rendered": rendered, "ok": ok})

    followup_rows: list[dict] = []
    if bytes(dictionary.raw_entry(DOCTOR_J_WRAPPER_SLOT)) != DOCTOR_J_WRAPPER_AFTER:
        failures.append({
            "reason": "doctor_j_wrapper_shape_failed",
            "slot": f"{DOCTOR_J_WRAPPER_SLOT:04X}",
            "raw": bytes(dictionary.raw_entry(DOCTOR_J_WRAPPER_SLOT)).hex().upper(),
        })
    for logical in DOCTOR_J_FOLLOWUPS:
        row = by_abs[f"{logical:06X}"]
        payload, _term = read_record(target, logical)
        rendered = dictionary.expand(payload, tbl).rstrip("　 \t")
        ok = (
            row["status"] == "active"
            and not row["control_prefix_hex"]
            and row["decoder"] == {"native_stock": True, "ext3": False, "compact3": False}
            and payload == DOCTOR_J_FOLLOWUP_AFTER
            and rendered == "그건　아니지만。"
            and b"\xEC\x8D" not in bytes(dictionary.raw_entry(DOCTOR_J_WRAPPER_SLOT))
        )
        if not ok:
            failures.append({
                "reason": "doctor_j_followup_failed",
                "abs": f"{logical:06X}",
                "payload_hex": payload.hex().upper(),
                "rendered": rendered,
                "contract": row,
            })
        followup_rows.append({
            "abs": f"{logical:06X}",
            "payload_hex": payload.hex().upper(),
            "rendered": rendered,
            "ok": ok,
        })

    katejina_rows: list[dict] = []
    if bytes(dictionary.raw_entry(KATEJINA_HELPER_SLOT)) != KATEJINA_HELPER_RAW:
        failures.append({
            "reason": "katejina_helper_shape_failed",
            "slot": f"{KATEJINA_HELPER_SLOT:04X}",
            "raw": bytes(dictionary.raw_entry(KATEJINA_HELPER_SLOT)).hex().upper(),
        })
    for logical, spec in KATEJINA_NATIVE_TARGETS.items():
        row = by_abs[f"{logical:06X}"]
        payload, term = read_record(target, logical)
        rendered = dictionary.expand(payload[1:], tbl).rstrip("　 \t")
        sb = stock_base(target)
        p = term + 1
        while p < term + 8 and target[sb + p] == 0:
            p += 1
        expected_confidence = "runtime-proven" if logical == 0x624305 else "structural-duplicate"
        ok = (
            row["status"] == "active"
            and row["confidence"] == expected_confidence
            and row["control_prefix_hex"] == "18"
            and row["decoder"] == {"native_stock": True, "ext3": False, "compact3": False}
            and payload == spec["after"]
            and b"\xE5\x18" not in payload[1:]
            and rendered == spec["expected"]
            and target[sb + p:sb + p + 4] == spec["next_control"]
        )
        if not ok:
            failures.append({
                "reason": "katejina_native_control_boundary_failed",
                "abs": f"{logical:06X}",
                "rendered": rendered,
                "payload_hex": payload.hex().upper(),
                "contract": row,
            })
        katejina_rows.append({
            "abs": f"{logical:06X}",
            "payload_hex": payload.hex().upper(),
            "rendered": rendered,
            "next_control_abs": f"{p:06X}",
            "next_control_hex": target[sb + p:sb + p + 4].hex().upper(),
            "ok": ok,
        })

    proactive_rows: list[dict] = []
    for logical in PROACTIVE_TWO_TOKEN:
        before, before_term = read_record(parent, logical)
        after, after_term = read_record(target, logical)
        before_text = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)).expand(before[1:], tbl).rstrip("　 \t")
        after_text = dictionary.expand(after[1:], tbl).rstrip("　 \t")
        ok = (
            before_term == after_term
            and before.startswith(b"\x18\xE5\x18")
            and after.startswith(b"\x18")
            and b"\xE5\x18" not in after[1:]
            and original_unit_kinds(after) == ["char1", "dict", "dict"]
            and after_text == before_text
        )
        if not ok:
            failures.append({"reason": "proactive_pair_failed", "abs": f"{logical:06X}", "before_text": before_text, "after_text": after_text})
        proactive_rows.append({"abs": f"{logical:06X}", "before_text": before_text, "after_text": after_text, "ok": ok})

    unresolved = [
        {
            "abs": row["address"],
            "jp": row.get("original_japanese", ""),
            "current": row.get("baseline_text", ""),
            "source_body_hex": row["source_body_hex"],
            "target_body_hex": row["baseline_body_hex"],
        }
        for row in target_exact
    ]

    payload = {
        "schema_version": 1,
        "generated_by": "tools/audit_scenario_continuation_native_followup_candidate.py",
        "ok": not failures,
        "target": {"path": str(args.target), "sha256": sha(target), "size": len(target)},
        "counts": {
            "parent_control18_ext3_risk": len(parent_broad),
            "target_control18_ext3_risk": len(target_broad),
            "removed_control18_ext3_risk": len(parent_broad) - len(target_broad),
            "parent_exact_fivebyte_native_pair_risk": len(parent_exact),
            "target_exact_fivebyte_native_pair_risk": len(target_exact),
            "removed_exact_fivebyte_native_pair_risk": len(parent_exact) - len(target_exact),
            "runtime_anchors": len(anchor_rows),
            "doctor_j_followup_native_records": len(followup_rows),
            "katejina_native_control_boundary_records": len(katejina_rows),
            "parent_control_following_exact_risks": len(parent_control_following),
            "target_control_following_exact_risks": len(target_control_following),
            "removed_control_following_exact_risks": len(parent_control_following) - len(target_control_following),
            "proactive_existing_token_repairs": len(proactive_rows),
            "failures": len(failures),
        },
        "runtime_anchors": anchor_rows,
        "doctor_j_followups": followup_rows,
        "katejina_native_control_boundary": katejina_rows,
        "remaining_control_following_exact_risks": target_control_following,
        "proactive": proactive_rows,
        "unresolved_exact_fivebyte_native_pair_risks": unresolved,
        "failures": failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], ensure_ascii=True, indent=2))
    print(f"ok={payload['ok']} report={args.out}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
