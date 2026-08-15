#!/usr/bin/env python3
"""Independent read-only audit for the residual plaque visual follow-up candidate."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_id_command_plaques_ko_candidate as base  # noqa: E402

PARENT = ROOT / "out/patch/id_command_residual_plaques_ko_candidate.wsc"
CANDIDATE = ROOT / "out/patch/id_command_residual_plaques_ko_followup_candidate.wsc"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PAIRED_SAVE = ROOT / "sram/id_command_residual_plaques_ko_followup_candidate.sav"
REPORT = ROOT / "out/patch/id_command_residual_plaques_ko_followup_candidate_audit.json"

EXPECTED_PARENT = "3ffbb11f18643ad029dcd869bd26f0b38b6ee2e1274ac489e12f3f4d4e553029"
EXPECTED_CANDIDATE = "2d03a635b1db344e12f39dc19cf0307c112749870065032aced6593358e507af"
EXPECTED_STOCK = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
TARGETS = {
    0x4C4A74: 320,
    0x4C4BB4: 256,
    0x4CC32A: 320,
    0x4CC52A: 384,
}


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def merged(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def main() -> int:
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    stock = STOCK.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    paired_save = PAIRED_SAVE.read_bytes()
    base_off = base.stock_base(parent)

    success = base.decode_grid(stock[0x4C4654 : 0x4C4654 + 384], 6, 2)
    common_right = [row[40:48] for row in success]

    seal_body = base.decode_grid(candidate[base_off + 0x4C4A74 : base_off + 0x4C4A74 + 320], 5, 2)
    seal = [seal_body[y] + common_right[y] for y in range(16)]

    shield_body = base.decode_grid(candidate[base_off + 0x4C4BB4 : base_off + 0x4C4BB4 + 256], 4, 2)
    shield = [shield_body[y] + common_right[y] for y in range(16)]

    pursuit_private = base.decode_grid(candidate[base_off + 0x4CC32A : base_off + 0x4CC32A + 320], 5, 2)
    shared_top = base.decode_grid(candidate[base_off + 0x4CB80A : base_off + 0x4CB80A + 32], 1, 1)
    shared_bottom = base.decode_grid(candidate[base_off + 0x4CB8AA : base_off + 0x4CB8AA + 32], 1, 1)
    pursuit = []
    for y in range(16):
        shared = shared_top[y] if y < 8 else shared_bottom[y - 8]
        cols = [pursuit_private[y][x : x + 8] for x in range(0, 40, 8)]
        pursuit.append(cols[0] + cols[1] + cols[2] + shared + cols[3] + cols[4])

    hp = base.decode_grid(candidate[base_off + 0x4CC52A : base_off + 0x4CC52A + 384], 6, 2)
    hp_stock = base.decode_grid(stock[0x4CC52A : 0x4CC52A + 384], 6, 2)

    allowed = merged([(base_off + a, base_off + a + n) for a, n in TARGETS.items()] + [(len(parent) - 2, len(parent))])
    runs = base.diff_runs(parent, candidate)
    unexpected = [(s, e) for s, e in runs if not any(lo <= s and e <= hi for lo, hi in allowed)]

    parent_shared_top = parent[base_off + 0x4CB80A : base_off + 0x4CB80A + 32]
    parent_shared_bottom = parent[base_off + 0x4CB8AA : base_off + 0x4CB8AA + 32]

    checks = {
        "parent_sha_bound": sha(parent) == EXPECTED_PARENT,
        "candidate_sha_bound": sha(candidate) == EXPECTED_CANDIDATE,
        "stock_sha_bound": sha(stock) == EXPECTED_STOCK,
        "rom_size_16mib": len(candidate) == 16_777_216,
        "stock_base_800000": base_off == 0x800000,
        "checksum_valid": (sum(candidate[:-2]) & 0xFFFF) == int.from_bytes(candidate[-2:], "little"),
        "diff_allowlist_clean": not unexpected,
        "all_four_targets_changed": all(parent[base_off+a:base_off+a+n] != candidate[base_off+a:base_off+a+n] for a, n in TARGETS.items()),
        "seal_x6_x7_clear": all(seal[y][6:8] == [0xC, 0xC] for y in range(1, 15)),
        "pursuit_x6_x7_clear": all(pursuit[y][6:8] == [0xC, 0xC] for y in range(1, 15)),
        "seal_left_cap_preserved": all(
            seal_body[y][:6] == base.decode_grid(parent[base_off+0x4C4A74:base_off+0x4C4A74+320], 5, 2)[y][:6]
            for y in range(16)
        ),
        "shield_left_cap_preserved": all(
            shield_body[y][:6] == base.decode_grid(parent[base_off+0x4C4BB4:base_off+0x4C4BB4+256], 4, 2)[y][:6]
            for y in range(16)
        ),
        "pursuit_shared_alias_unchanged": (
            candidate[base_off + 0x4CB80A : base_off + 0x4CB80A + 32] == parent_shared_top
            and candidate[base_off + 0x4CB8AA : base_off + 0x4CB8AA + 32] == parent_shared_bottom
        ),
        "pursuit_shared_column_composes_exactly": all(
            pursuit[y][24:32] == (shared_top[y] if y < 8 else shared_bottom[y - 8]) for y in range(16)
        ),
        "shield_has_no_old_parent_body": candidate[base_off+0x4C4BB4:base_off+0x4C4BB4+256] != parent[base_off+0x4C4BB4:base_off+0x4C4BB4+256],
        "hp_stock_x0_x13_exact": all(hp[y][:14] == hp_stock[y][:14] for y in range(16)),
        "hp_common_right_cap_x40_x47": all(hp[y][40:48] == common_right[y] for y in range(16)),
        "paired_saveram_latest_live_exact": paired_save == live_save,
    }
    ok = all(checks.values())
    result = {
        "schema_version": 1,
        "generated_by": "tools/audit_id_command_residual_plaques_ko_followup_candidate.py",
        "read_only": True,
        "ok": ok,
        "parent_sha256": sha(parent),
        "candidate_sha256": sha(candidate),
        "stock_sha256": sha(stock),
        "live_saveram_sha256": sha(live_save),
        "paired_saveram_sha256": sha(paired_save),
        "checks": checks,
        "diff": {
            "changed_bytes_including_checksum": sum(e - s for s, e in runs),
            "run_count_including_checksum": len(runs),
            "unexpected": [{"start": f"{s:08X}", "end_exclusive": f"{e:08X}"} for s, e in unexpected],
        },
        "runtime_verification": "still required before promotion",
    }
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
