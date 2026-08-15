#!/usr/bin/env python3
"""Independent fail-closed audit for bank-62 scouting event-structure tail repair."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_scouting_map_event_structure_tail_repair_candidate import (
    EXPECTED_BYTES,
    EXPECTED_RUNS,
    MAIN_SHA,
    ORIGINAL_SIZE,
    PRIOR_REPAIR,
    RESTORE_LOGICAL_END,
    RESTORE_LOGICAL_START,
    ROM_SIZE,
    SAVE_SIZE,
)
from monoeye_rom import load_rom, stock_base, ws_header

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CAND = ROOT / "out/patch/scouting_map_event_structure_tail_repair_candidate.wsc"
CAND_SAVE = ROOT / "sram/scouting_map_event_structure_tail_repair_candidate.sav"
REPORT = ROOT / "out/patch/scouting_map_event_structure_tail_repair_report.json"
OUT = ROOT / "out/patch/scouting_map_event_structure_tail_repair_audit.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    target = CAND.read_bytes()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, **detail: Any) -> None:
        row = {"name": name, "ok": bool(ok), **detail}
        checks.append(row)
        if not ok:
            failures.append(row)

    check("report_ok", report.get("ok") is True)
    check("main_sha_pinned", sha(parent) == MAIN_SHA, sha256=sha(parent))
    check("candidate_matches_report", sha(target) == report["output"]["rom"]["sha256"].lower())
    check("saveram_byte_exact", CAND_SAVE.read_bytes() == MAIN_SAVE.read_bytes())
    check("sizes", len(parent) == ROM_SIZE and len(target) == ROM_SIZE and len(original) == ORIGINAL_SIZE)
    check("save_size", len(CAND_SAVE.read_bytes()) == SAVE_SIZE)
    checksum = int(ws_header(target)["checksum"])
    check("checksum_valid", checksum == (sum(target[:-2]) & 0xFFFF), checksum=f"{checksum:04X}")

    s_p = stock_base(parent)
    s_t = stock_base(target)
    s_o = stock_base(original)
    p62 = parent[s_p + 0x620000 : s_p + 0x630000]
    t62 = target[s_t + 0x620000 : s_t + 0x630000]
    o62 = original[s_o + 0x620000 : s_o + 0x630000]

    lo = RESTORE_LOGICAL_START & 0xFFFF
    hi = RESTORE_LOGICAL_END & 0xFFFF or 0x10000
    check("tail_equals_original", t62[lo:hi] == o62[lo:hi])
    check("tail_differed_from_parent", t62[lo:hi] != p62[lo:hi])
    check(
        "prior_repair_unchanged",
        t62[PRIOR_REPAIR[0] & 0xFFFF : PRIOR_REPAIR[1] & 0xFFFF]
        == p62[PRIOR_REPAIR[0] & 0xFFFF : PRIOR_REPAIR[1] & 0xFFFF]
        == o62[PRIOR_REPAIR[0] & 0xFFFF : PRIOR_REPAIR[1] & 0xFFFF],
    )
    check("early_bank62_equals_parent", t62[:lo] == p62[:lo])

    n_runs = 0
    n_bytes = 0
    i = lo
    while i < hi:
        if p62[i] == o62[i]:
            i += 1
            continue
        j = i
        while j < hi and p62[j] != o62[j]:
            j += 1
        n_runs += 1
        n_bytes += j - i
        i = j
    check("restore_run_count", n_runs == EXPECTED_RUNS, got=n_runs)
    check("restore_byte_count", n_bytes == EXPECTED_BYTES, got=n_bytes)
    check("report_run_count", report["output"]["restore_runs"] == EXPECTED_RUNS)
    check("name75_zedan_untouched", target[s_t + 0x75BDFA : s_t + 0x75BDFA + 5] == parent[s_p + 0x75BDFA : s_p + 0x75BDFA + 5])
    check("name75_sahara_untouched", target[s_t + 0x75BDB2 : s_t + 0x75BDB2 + 5] == parent[s_p + 0x75BDB2 : s_p + 0x75BDB2 + 5])
    check("main_tip_file_unchanged", MAIN.read_bytes() == parent)

    payload = {
        "ok": not failures,
        "generated_by": "tools/audit_scouting_map_event_structure_tail_repair_candidate.py",
        "checks": checks,
        "failures": failures,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": payload["ok"], "failures": len(failures)}, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
