#!/usr/bin/env python3
"""Independent audit for ending_scene_fg_x_zero_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CAND = ROOT / "out/patch/ending_scene_fg_x_zero_candidate.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND_SAVE = ROOT / "sram/ending_scene_fg_x_zero_candidate.sav"
BUILD = ROOT / "out/patch/ending_scene_fg_x_zero_candidate_report.json"
OUT = ROOT / "out/patch/ending_scene_fg_x_zero_candidate_audit.json"

MAIN_SHA = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
CAND_SHA = "2e93c4cad79b04bfb50517836655a1aac9a957b1622106dbe78a050a5304bef7"
SCROLL_BLOCK = 0x7ED4A5
EXPECTED_MAIN = bytes.fromhex("32C0E61032C0E611")
EXPECTED_CAND = bytes.fromhex("32C0E610E611E612")
PAGE20_HOOK = 0x7ED4F1
PAGE20 = bytes.fromhex("9A24FF00F090")
FULL_RESET = 0x7ED7D0
FULL = bytes.fromhex("32C0E61032C0E61132C0E61232C0E613")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(a):
        if a[i] == b[i]:
            i += 1
            continue
        s = i
        while i < len(a) and a[i] != b[i]:
            i += 1
        out.append((s, i))
    return out


def main() -> int:
    parent = MAIN.read_bytes()
    cand = CAND.read_bytes()
    save = SAVE.read_bytes()
    csave = CAND_SAVE.read_bytes()
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    sb = stock_base(parent)
    csb = stock_base(cand)

    runs = diff_runs(parent, cand)
    expected_file = csb + SCROLL_BLOCK
    logical_runs = [(a - csb, b - csb) for a, b in runs if a < len(cand) - 2]
    changed_non_checksum = sum((b - a) for a, b in runs if a < len(cand) - 2)
    checks: dict[str, bool] = {
        "main_identity_exact": sha(parent) == MAIN_SHA,
        "candidate_identity_exact": sha(cand) == CAND_SHA,
        "builder_report_ok": build.get("ok") is True,
        "paired_save_exact_live": csave == save,
        "main_block_exact": parent[sb + SCROLL_BLOCK : sb + SCROLL_BLOCK + 8] == EXPECTED_MAIN,
        "candidate_block_exact": cand[csb + SCROLL_BLOCK : csb + SCROLL_BLOCK + 8] == EXPECTED_CAND,
        "page20_hook_unchanged": cand[csb + PAGE20_HOOK : csb + PAGE20_HOOK + len(PAGE20)] == PAGE20 == parent[sb + PAGE20_HOOK : sb + PAGE20_HOOK + len(PAGE20)],
        "full_reset_reference_unchanged": cand[csb + FULL_RESET : csb + FULL_RESET + len(FULL)] == FULL == parent[sb + FULL_RESET : sb + FULL_RESET + len(FULL)],
        "only_three_logic_bytes_changed": changed_non_checksum == 3,
        "logic_diff_localized_to_D4A9_D4AC": all(0x7ED4A9 <= a and b <= 0x7ED4AD for a, b in logical_runs),
        "checksum_valid": int.from_bytes(cand[-2:], "little") == (sum(cand[:-2]) & 0xFFFF),
        "main_unchanged": sha(MAIN.read_bytes()) == MAIN_SHA,
        "live_save_unchanged": SAVE.read_bytes() == save,
    }
    ok = all(checks.values())
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "tools/audit_ending_scene_fg_x_zero_candidate.py",
        "ok": ok,
        "checks": checks,
        "candidate": {"sha256": sha(cand), "checksum": f"{int.from_bytes(cand[-2:], 'little'):04X}"},
        "scroll_block": {
            "logical": f"{SCROLL_BLOCK:06X}",
            "main_hex": parent[sb + SCROLL_BLOCK : sb + SCROLL_BLOCK + 8].hex().upper(),
            "candidate_hex": cand[csb + SCROLL_BLOCK : csb + SCROLL_BLOCK + 8].hex().upper(),
            "meaning": "BG X=0, BG Y=0, plus candidate FG X=0",
        },
        "diff": {
            "runs": [{"start": f"{a:08X}", "end_exclusive": f"{b:08X}", "length": b-a} for a,b in runs],
            "non_checksum_changed_bytes": changed_non_checksum,
        },
        "runtime_validation_required": True,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
