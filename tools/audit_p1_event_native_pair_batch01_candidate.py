#!/usr/bin/env python3
"""Independent audit for P1 event-safe native-pair batch01."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from dialogue_runtime_contracts import build_manifest  # noqa: E402
from monoeye_rom import Tbl, load_rom, stock_base, ws_header  # noqa: E402

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TARGET = ROOT / "out/patch/p1_event_native_pair_batch01_candidate.wsc"
OUT = ROOT / "out/patch/p1_event_native_pair_batch01_audit.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

PARENT_SHA = "fbd7ad5f36d1248aab27b9a3a1e90b4ef2ec0676567b6bb42b76979e3c9b3260"
TARGET_SHA = "53d2180e31d0c05d862482e1629cfc5581b717fd38425865dbf2af700a1cc0ae"
TARGETS = (0x6256CC, 0x625730)
PREFIX = bytes.fromhex("173418")
PARENT_BODY = bytes.fromhex("E5181999")
SOURCE_BODY = bytes.fromhex("F589F191")
BOUNDARY = bytes.fromhex("000017280106")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    st = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and st is None:
            st = i
        elif x == y and st is not None:
            out.append((st, i)); st = None
    if st is not None:
        out.append((st, len(a)))
    return out


def is_source_pair(body: bytes) -> bool:
    return len(body) == 4 and 0xF0 <= body[0] <= 0xFF and 0xF0 <= body[2] <= 0xFF


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    parent = bytes(load_rom(PARENT))
    target = bytes(load_rom(TARGET))
    if sha(parent) != PARENT_SHA:
        raise SystemExit("parent main identity drifted")
    if sha(target) != TARGET_SHA:
        raise SystemExit("candidate identity drifted")
    psb = stock_base(parent)
    tsb = stock_base(target)

    dictionary = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    rendered = dictionary.expand(SOURCE_BODY, Tbl.load(TBL))
    failures: list[dict[str, Any]] = []

    for logical in TARGETS:
        p = parent[psb + logical:psb + logical + 7]
        c = target[tsb + logical:tsb + logical + 7]
        o = original[logical:logical + 7]
        term = logical + 7
        if p != PREFIX + PARENT_BODY:
            failures.append({"abs": f"{logical:06X}", "reason": "parent_payload_drift", "hex": p.hex().upper()})
        if c != PREFIX + SOURCE_BODY or o != PREFIX + SOURCE_BODY:
            failures.append({"abs": f"{logical:06X}", "reason": "source_body_not_restored_exact"})
        if target[tsb + term:tsb + term + len(BOUNDARY)] != BOUNDARY:
            failures.append({"abs": f"{logical:06X}", "reason": "boundary_control_changed"})
        if target[tsb + term:tsb + term + 0x10] != parent[psb + term:psb + term + 0x10]:
            failures.append({"abs": f"{logical:06X}", "reason": "post_record_parent_drift"})

    manifest = build_manifest(original, target, target_path=TARGET)
    contracts = {row["address"]: row for row in manifest["contracts"]}
    target_contracts = []
    for logical in TARGETS:
        row = contracts[f"{logical:06X}"]
        view = {
            "address": row["address"],
            "status": row["status"],
            "confidence": row["confidence"],
            "route": row["route"],
            "body": row["baseline_body_hex"],
            "decoder": row["decoder"],
            "nul_run": (row.get("baseline_boundary") or {}).get("nul_run"),
            "next_control": (row.get("baseline_boundary") or {}).get("next_control"),
        }
        target_contracts.append(view)
        if not (
            view["status"] == "active"
            and view["route"] == "scenario_first"
            and view["body"] == SOURCE_BODY.hex().upper()
            and view["decoder"].get("native_stock") is True
            and view["decoder"].get("ext3") is False
            and view["nul_run"] == 2
            and view["next_control"] == "1728"
        ):
            failures.append({"abs": f"{logical:06X}", "reason": "runtime_contract_not_native_safe", "contract": view})

    # Recompute the strong exact4 structural suspect population on this candidate.
    exact4 = 0
    p1 = 0
    for row in manifest["contracts"]:
        source = bytes.fromhex(row.get("source_body_hex") or "")
        body = bytes.fromhex(row.get("baseline_body_hex") or "")
        b = row.get("baseline_boundary") or {}
        if (
            len(body) == 4 and body.startswith(b"\xE5\x18") and is_source_pair(source)
            and b.get("nul_run") == 2 and b.get("next_lead") in {"08", "17"}
        ):
            exact4 += 1
            if row.get("route") == "scenario_first" and b.get("next_control") == "1728":
                p1 += 1

    term_drift = sum(
        row.get("source_terminator") != row.get("baseline_terminator")
        for row in manifest["contracts"]
    )
    if exact4 != 218:
        failures.append({"reason": "global_exact4_count_unexpected", "expected": 218, "actual": exact4})
    if p1 != 135:
        failures.append({"reason": "global_P1_count_unexpected", "expected": 135, "actual": p1})
    if term_drift:
        failures.append({"reason": "global_terminator_drift", "count": term_drift})
    if rendered != "도몬……":
        failures.append({"reason": "native_pair_render_changed", "rendered": rendered})

    runs = diff_runs(parent, target)
    expected_file_ranges = [
        (tsb + logical + 3, tsb + logical + 7) for logical in TARGETS
    ] + [(len(target) - 2, len(target))]
    unexpected_runs = [
        [a, b] for a, b in runs
        if not any(lo <= a and b <= hi for lo, hi in expected_file_ranges)
    ]
    if unexpected_runs:
        failures.append({"reason": "unexpected_whole_rom_diff", "runs": unexpected_runs})

    checksum_ok = int(ws_header(target)["checksum"]) == (sum(target[:-2]) & 0xFFFF)
    if not checksum_ok:
        failures.append({"reason": "checksum_invalid"})

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_p1_event_native_pair_batch01_candidate.py",
        "ok": not failures,
        "target": {"path": str(TARGET.relative_to(ROOT)), "sha256": sha(target), "size": len(target)},
        "rendered_native_pair": rendered,
        "target_contracts": target_contracts,
        "global_risk_delta": {
            "exact4_before": 220,
            "exact4_after": exact4,
            "P1_before": 137,
            "P1_after": p1,
            "terminator_drift": term_drift,
        },
        "diff": {
            "runs": [[a, b] for a, b in runs],
            "bytes": sum(b - a for a, b in runs),
            "unexpected_runs": unexpected_runs,
        },
        "checksum_valid": checksum_ok,
        "failures": failures,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "ok": report["ok"],
        "rendered_native_pair": rendered,
        "global_risk_delta": report["global_risk_delta"],
        "diff": report["diff"],
        "failures": failures,
        "report": str(OUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
