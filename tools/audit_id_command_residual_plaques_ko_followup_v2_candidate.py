#!/usr/bin/env python3
"""Independent static audit for residual ID-command plaque follow-up v2."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_id_command_plaques_ko_candidate as base  # noqa: E402

PARENT = ROOT / "out/patch/id_command_residual_plaques_ko_followup_candidate.wsc"
CANDIDATE = ROOT / "out/patch/id_command_residual_plaques_ko_followup_v2_candidate.wsc"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PAIRED_SAVE = ROOT / "sram/id_command_residual_plaques_ko_followup_v2_candidate.sav"
REPORT = ROOT / "out/patch/id_command_residual_plaques_ko_followup_v2_candidate_audit.json"

EXPECTED_PARENT = "2d03a635b1db344e12f39dc19cf0307c112749870065032aced6593358e507af"
EXPECTED_CANDIDATE = "93b1b0222a672c5ee8e059f567380985f55c29ef558f6af5b981d5d5edecbf30"
EXPECTED_STOCK = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"

TARGETS = {
    0x4C4A74: 320,
    0x4C4BB4: 256,
    0x4C50F4: 320,
    0x4C53B4: 320,
    0x4CBEAA: 384,
    0x4CC32A: 320,
    0x4CE86A: 384,
    0x4CE9EA: 256,
    0x4CC52A: 384,
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def count_hits(data: bytes, needle: bytes, start: int, end: int) -> list[int]:
    out: list[int] = []
    pos = start
    while True:
        hit = data.find(needle, pos, end)
        if hit < 0:
            return out
        out.append(hit)
        pos = hit + 1


def compose(candidate: bytes, stock: bytes, logical: int, storage: str) -> list[list[int]]:
    base_off = 0x800000
    success = base.decode_grid(candidate[base_off + 0x4C4654 : base_off + 0x4C4654 + 384], 6, 2)
    left = [row[:8] for row in success]
    right = [row[40:48] for row in success]
    if storage == "body40":
        body = base.decode_grid(candidate[base_off + logical : base_off + logical + 320], 5, 2)
        return [body[y] + right[y] for y in range(16)]
    if storage == "body32":
        body = base.decode_grid(candidate[base_off + logical : base_off + logical + 256], 4, 2)
        return [body[y] + right[y] for y in range(16)]
    if storage == "both32":
        body = base.decode_grid(candidate[base_off + logical : base_off + logical + 256], 4, 2)
        return [left[y] + body[y] + right[y] for y in range(16)]
    if storage == "full48":
        return base.decode_grid(candidate[base_off + logical : base_off + logical + 384], 6, 2)
    if storage == "sparse":
        body = base.decode_grid(candidate[base_off + logical : base_off + logical + 320], 5, 2)
        top = base.decode_grid(candidate[base_off + 0x4CB80A : base_off + 0x4CB80A + 32], 1, 1)
        bottom = base.decode_grid(candidate[base_off + 0x4CB8AA : base_off + 0x4CB8AA + 32], 1, 1)
        rows: list[list[int]] = []
        for y in range(16):
            shared = top[y] if y < 8 else bottom[y - 8]
            cols = [body[y][x : x + 8] for x in range(0, 40, 8)]
            rows.append(cols[0] + cols[1] + cols[2] + shared + cols[3] + cols[4])
        return rows
    raise RuntimeError(storage)


def main() -> int:
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    stock = STOCK.read_bytes()
    live = LIVE_SAVE.read_bytes()
    paired = PAIRED_SAVE.read_bytes()
    base_off = base.stock_base(candidate)

    dirty_top = stock[0x4C46F4 : 0x4C46F4 + 32]
    dirty_bottom = stock[0x4C47B4 : 0x4C47B4 + 32]
    bank_start = base_off + 0x4C0000
    bank_end = base_off + 0x4D0000

    success_parent = parent[base_off + 0x4C4654 : base_off + 0x4C4654 + 384]
    success_candidate = candidate[base_off + 0x4C4654 : base_off + 0x4C4654 + 384]
    success_grid = base.decode_grid(success_candidate, 6, 2)

    seal = compose(candidate, stock, 0x4C4A74, "body40")
    shield = compose(candidate, stock, 0x4C4BB4, "body32")
    hit = compose(candidate, stock, 0x4C50F4, "body40")
    evade = compose(candidate, stock, 0x4C53B4, "body40")
    move = compose(candidate, stock, 0x4CBEAA, "full48")
    pursuit = compose(candidate, stock, 0x4CC32A, "sparse")
    penetrate = compose(candidate, stock, 0x4CE86A, "full48")
    preempt = compose(candidate, stock, 0x4CE9EA, "both32")
    hp = compose(candidate, stock, 0x4CC52A, "full48")
    hp_stock = base.decode_grid(stock[0x4CC52A : 0x4CC52A + 384], 6, 2)
    move_parent = base.decode_grid(parent[base_off + 0x4CBEAA : base_off + 0x4CBEAA + 384], 6, 2)

    shared_top_parent = parent[base_off + 0x4CB80A : base_off + 0x4CB80A + 32]
    shared_bottom_parent = parent[base_off + 0x4CB8AA : base_off + 0x4CB8AA + 32]
    shared_top_candidate = candidate[base_off + 0x4CB80A : base_off + 0x4CB80A + 32]
    shared_bottom_candidate = candidate[base_off + 0x4CB8AA : base_off + 0x4CB8AA + 32]

    allowed = [(base_off + a, base_off + a + n) for a, n in TARGETS.items()] + [(len(candidate) - 2, len(candidate))]
    runs = base.diff_runs(parent, candidate)
    unexpected = [(s, e) for s, e in runs if not any(lo <= s and e <= hi for lo, hi in allowed)]

    bright = [seal, hit, evade, pursuit, penetrate, preempt]
    checks = {
        "parent_sha_bound": sha(parent) == EXPECTED_PARENT,
        "candidate_sha_bound": sha(candidate) == EXPECTED_CANDIDATE,
        "stock_sha_bound": sha(stock) == EXPECTED_STOCK,
        "rom_size_valid": len(candidate) == 16_777_216,
        "stock_base_800000": base_off == 0x800000,
        "checksum_valid": (sum(candidate[:-2]) & 0xFFFF) == int.from_bytes(candidate[-2:], "little"),
        "success_canonical_asset_unchanged": success_candidate == success_parent,
        "canonical_left_inner_strip_clean": all(success_grid[y][6:8] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "canonical_right_inner_strip_clean": all(success_grid[y][40:42] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "active_dirty_right_top_aliases_zero": not count_hits(candidate, dirty_top, bank_start, bank_end),
        "active_dirty_right_bottom_aliases_zero": not count_hits(candidate, dirty_bottom, bank_start, bank_end),
        "bright_result_right_strips_clean": all(all(p[y][40:42] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)) for p in bright),
        "seal_left_strip_clean": all(seal[y][6:8] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "sure_hit_left_strip_clean": all(hit[y][6:8] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "evade_left_strip_clean": all(evade[y][6:8] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "pursuit_left_strip_clean": all(pursuit[y][6:8] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "penetrate_left_strip_clean": all(penetrate[y][6:8] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "shield_right_external_strip_clean": all(shield[y][32:34] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "move_right_strip_clean_down_tone": all(move[y][40:42] == ([0xA, 0xA] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "move_arrow_x0_x12_exact": all(move[y][0:13] == move_parent[y][0:13] for y in range(16)),
        "pursuit_shared_alias_unchanged": shared_top_candidate == shared_top_parent and shared_bottom_candidate == shared_bottom_parent,
        "hp_stock_x0_x13_exact": all(hp[y][0:14] == hp_stock[y][0:14] for y in range(16)),
        "hp_right_strip_clean": all(hp[y][40:42] == ([0xF, 0xF] if y in (0, 15) else [0xC, 0xC]) for y in range(16)),
        "diff_allowlist_clean": not unexpected,
        "paired_saveram_latest_live_exact": paired == live,
    }
    ok = all(checks.values())
    result = {
        "schema_version": 1,
        "generated_by": "tools/audit_id_command_residual_plaques_ko_followup_v2_candidate.py",
        "read_only": True,
        "ok": ok,
        "parent_sha256": sha(parent),
        "candidate_sha256": sha(candidate),
        "stock_sha256": sha(stock),
        "live_saveram_sha256": sha(live),
        "paired_saveram_sha256": sha(paired),
        "dirty_cap_hits_after": {
            "top": [f"{x:08X}" for x in count_hits(candidate, dirty_top, bank_start, bank_end)],
            "bottom": [f"{x:08X}" for x in count_hits(candidate, dirty_bottom, bank_start, bank_end)],
        },
        "checks": checks,
        "diff": {
            "changed_bytes_including_checksum": sum(e - s for s, e in runs),
            "run_count_including_checksum": len(runs),
            "unexpected": [{"start": f"{s:08X}", "end_exclusive": f"{e:08X}"} for s, e in unexpected],
        },
        "runtime_verification": "required before promotion",
    }
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
