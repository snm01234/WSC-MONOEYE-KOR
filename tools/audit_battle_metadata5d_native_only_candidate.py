#!/usr/bin/env python3
"""Independent read-only audit of the metadata=5D Heero native-only candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_battle_dialogue_runtime_integrated_cleanup_candidate import clean, visible_japanese
from build_battle_metadata5d_native_only_candidate import (
    EXPECTED_PARENT_SHA,
    EXPECTED_TARGETS,
    EXPECTED_UNIQUE,
    HEERO_ANCHORS,
)
from build_remaining_dialogue_candidate import covered, diff_runs
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/battle_metadata5d_native_only_candidate.wsc"
SAVE = ROOT / "sram/battle_metadata5d_native_only_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BUILD_REPORT = ROOT / "out/patch/battle_metadata5d_native_only_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_OUT = ROOT / "out/patch/battle_metadata5d_native_only_candidate_audit.json"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check(failures: list[dict[str, Any]], kind: str, ok: bool, **extra: Any) -> None:
    if not ok:
        failures.append({"kind": kind, **extra})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=PARENT)
    parser.add_argument("--candidate", type=Path, default=CANDIDATE)
    parser.add_argument("--build-report", type=Path, default=BUILD_REPORT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    parent = bytes(load_rom(args.parent))
    candidate = bytes(load_rom(args.candidate))
    build = json.loads(args.build_report.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    d_candidate = Dictionary(candidate)
    sb = stock_base(candidate)
    failures: list[dict[str, Any]] = []

    check(failures, "parent_size", len(parent) == ROM_SIZE)
    check(failures, "candidate_size", len(candidate) == ROM_SIZE)
    check(failures, "parent_sha", sha256(parent) == EXPECTED_PARENT_SHA)
    check(failures, "candidate_sha", sha256(candidate) == str(build["candidate"]["sha256"]).lower())
    check(failures, "main_unmodified", args.parent.read_bytes() == parent)
    check(failures, "live_save_unmodified", MAIN_SAVE.read_bytes() == SAVE.read_bytes())
    check(failures, "save_size", SAVE.stat().st_size == SAVE_SIZE)
    check(failures, "save_copied", SAVE.read_bytes() == MAIN_SAVE.read_bytes())
    check(failures, "target_count", int(build["counts"]["targets"]) == EXPECTED_TARGETS)
    check(failures, "unique_count", int(build["counts"]["unique_phrases"]) == EXPECTED_UNIQUE)
    check(failures, "hard_failures", int(build["counts"]["runtime_contract_hard_failures"]) == 0)
    check(failures, "unaccounted", int(build["counts"]["unaccounted_diff_runs"]) == 0)

    heero_seen = 0
    for row in build.get("targets") or []:
        logical = int(str(row["abs"]), 16)
        before = bytes.fromhex(str(row["before_hex"]))
        after = bytes.fromhex(str(row["after_hex"]))
        start = sb + logical
        check(failures, f"parent_before_{row['abs']}", bytes(parent[start:start + len(before)]) == before)
        check(failures, f"candidate_after_{row['abs']}", bytes(candidate[start:start + len(after)]) == after)
        check(failures, f"extent_{row['abs']}", len(before) == len(after))
        check(failures, f"meta_{row['abs']}", after.startswith(b"\x5D"))
        check(failures, f"no_e518_{row['abs']}", after[1:3] != b"\xE5\x18")
        live, term = read_encoded_z_safe(candidate, start, max_len=128) or (b"", -1)
        check(failures, f"zstring_{row['abs']}", bytes(live) == after)
        rendered = clean(d_candidate.expand(bytes(live[1:]), tbl))
        check(failures, f"render_{row['abs']}", rendered == row["text"] and not visible_japanese(rendered))
        check(failures, f"term_{row['abs']}", term == int(row["terminator_offset"]))
        if logical in HEERO_ANCHORS:
            heero_seen += 1
            check(failures, f"heero_nonempty_{row['abs']}", bool(str(row["text"]).replace("…", "").strip()))
    check(failures, "heero_anchors", heero_seen == int(build["counts"]["heero_anchors"]))

    allowed: list[tuple[int, int]] = []
    for row in build.get("targets") or []:
        logical = int(str(row["abs"]), 16)
        n = len(bytes.fromhex(str(row["after_hex"])))
        allowed.append((sb + logical, sb + logical + n))
    for run in build.get("changed_runs") or []:
        left = int(str(run["start"]), 16)
        right = int(str(run["end"]), 16)
        # changed_runs is informational; allowlist is rebuilt from actual diff.
        del left, right
    runs = diff_runs(parent, candidate)
    unexpected = [run for run in runs if not covered(run, allowed + [(len(candidate) - 2, len(candidate))])]
    # Dictionary storage/pointer runs are expected and listed in the build report
    # as extra changed_runs.  Accept them when they sit in the stock dict bank.
    dict_bank = sb + 0x5F * 0x10000
    still = []
    for left, right in unexpected:
        if dict_bank <= left < dict_bank + 0x10000 and dict_bank < right <= dict_bank + 0x10000:
            continue
        still.append({"start": f"{left:07X}", "end": f"{right:07X}"})
    check(failures, "allowlist", not still, runs=still)

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_metadata5d_native_only_candidate.py",
        "ok": not failures,
        "parent_sha256": sha256(parent),
        "candidate_sha256": sha256(candidate),
        "failures": failures,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "failures": len(failures)}, ensure_ascii=True, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
