#!/usr/bin/env python3
"""Independent fail-closed audit for the MAP SELECT FF09 pointer restore."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_scouting_map_select_ff09_pointer_restore_candidate import (
    KEEP_FF09_NAMES,
    MAIN_SHA,
    ORIGINAL_SIZE,
    RESTORE_SITES,
    ROM_SIZE,
    SAVE_SIZE,
)
from monoeye_rom import load_rom, stock_base, ws_header

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CAND = ROOT / "out/patch/scouting_map_select_ff09_pointer_restore_candidate.wsc"
CAND_SAVE = ROOT / "sram/scouting_map_select_ff09_pointer_restore_candidate.sav"
REPORT = ROOT / "out/patch/scouting_map_select_ff09_pointer_restore_report.json"
OUT = ROOT / "out/patch/scouting_map_select_ff09_pointer_restore_audit.json"


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
    restored = []
    for logical, reason in RESTORE_SITES:
        jp_b = original[s_o + logical]
        parent_b = parent[s_p + logical]
        cand_b = target[s_t + logical]
        ok = jp_b == 0x09 and parent_b == 0x10 and cand_b == 0x09
        restored.append({"logical": f"{logical:06X}", "ok": ok, "reason": reason})
        check(f"restore_{logical:06X}", ok, jp=f"{jp_b:02X}", parent=f"{parent_b:02X}", cand=f"{cand_b:02X}")

    for logical in KEEP_FF09_NAMES:
        tok = bytes(target[s_t + logical : s_t + logical + 2])
        parent_tok = bytes(parent[s_p + logical : s_p + logical + 2])
        check(
            f"keep_ff09_{logical:06X}",
            tok == bytes.fromhex("FF09") and tok == parent_tok,
            cand=tok.hex().upper(),
        )

    zedan_p = parent[s_p + 0x75BDFA : s_p + 0x75BE00]
    zedan_t = target[s_t + 0x75BDFA : s_t + 0x75BE00]
    sahara_p = parent[s_p + 0x75BDB2 : s_p + 0x75BDB8]
    sahara_t = target[s_t + 0x75BDB2 : s_t + 0x75BDB8]
    check("zedan_name_unchanged", zedan_p == zedan_t, hex=zedan_t.hex().upper())
    check("sahara_name_unchanged", sahara_p == sahara_t, hex=sahara_t.hex().upper())
    check(
        "far_pointer_66_09FF",
        bytes(target[s_t + 0x6609DE : s_t + 0x6609E2]) == bytes.fromhex("FF09E600"),
    )

    # Candidate vs parent: only the six sites plus checksum.
    diffs: list[int] = [i for i, (a, b) in enumerate(zip(parent, target)) if a != b]
    expected = {s_t + logical for logical, _ in RESTORE_SITES}
    expected.update({len(target) - 2, len(target) - 1})
    extra = [i for i in diffs if i not in expected]
    missing = [i for i in sorted(expected) if parent[i] == target[i] and i < len(target) - 2]
    check("no_extra_diffs", not extra, extra=[f"{i:08X}" for i in extra[:12]], diff_count=len(diffs))
    check("all_restore_sites_differ_from_parent", not missing, missing=[f"{i:08X}" for i in missing])
    check("main_tip_file_unchanged", bytes(load_rom(MAIN)) == parent)
    check("main_saveram_file_unchanged", MAIN_SAVE.read_bytes() == CAND_SAVE.read_bytes())

    result = {
        "schema_version": 1,
        "generated_by": "tools/audit_scouting_map_select_ff09_pointer_restore_candidate.py",
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "restored": restored,
        "diff_count_vs_main": len(diffs),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "failures": [row["name"] for row in failures], "audit": str(OUT)}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
