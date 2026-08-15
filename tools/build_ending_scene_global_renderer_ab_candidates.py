#!/usr/bin/env python3
"""Build narrow A/B diagnostics for the ending cinematic alignment issue.

A restores only the two stock FG tilemap stores at 78:A06E/A0EB that were
replaced by the intermission transition remapper.
B also restores the stock final renderer bank-restore call at 78:9C4D, bypassing
the intermission wrapper entirely.  All payloads remain in ROM but are
unreachable from these restored call sites.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import stock_base, update_ws_checksum, ws_header  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_A = ROOT / "out/patch/ending_scene_stock_renderer_stores_candidate.wsc"
OUT_B = ROOT / "out/patch/ending_scene_stock_renderer_full_candidate.wsc"
SAVE_A = ROOT / "sram/ending_scene_stock_renderer_stores_candidate.sav"
SAVE_B = ROOT / "sram/ending_scene_stock_renderer_full_candidate.sav"
REPORT = ROOT / "out/patch/ending_scene_global_renderer_ab_report.json"

EXPECTED_MAIN_SHA256 = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
SITE_DX = 0x78A06E
SITE_SI = 0x78A0EB
SITE_FINAL = 0x789C4D
MAIN_DX = bytes.fromhex("9A CF FD 00 90")
MAIN_SI = bytes.fromhex("9A D5 FD 00 90")
MAIN_FINAL = bytes.fromhex("9A D3 FC 00 80")
STOCK_DX = bytes.fromhex("26 89 97 00 38")
STOCK_SI = bytes.fromhex("26 89 B7 00 38")
STOCK_FINAL = bytes.fromhex("9A B5 DE 00 80")


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def checksum(data: bytes | bytearray) -> str:
    h = ws_header(data)
    return f"{int(h['checksum']):04X}"


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(a)))
    return runs


def make(parent: bytes, full: bool) -> bytes:
    out = bytearray(parent)
    sb = stock_base(out)
    if out[sb + SITE_DX : sb + SITE_DX + 5] != MAIN_DX:
        raise RuntimeError("78:A06E main bytes drifted")
    if out[sb + SITE_SI : sb + SITE_SI + 5] != MAIN_SI:
        raise RuntimeError("78:A0EB main bytes drifted")
    if out[sb + SITE_FINAL : sb + SITE_FINAL + 5] != MAIN_FINAL:
        raise RuntimeError("78:9C4D main bytes drifted")
    out[sb + SITE_DX : sb + SITE_DX + 5] = STOCK_DX
    out[sb + SITE_SI : sb + SITE_SI + 5] = STOCK_SI
    if full:
        out[sb + SITE_FINAL : sb + SITE_FINAL + 5] = STOCK_FINAL
    update_ws_checksum(out)
    return bytes(out)


def main() -> None:
    parent = MAIN.read_bytes()
    if sha(parent) != EXPECTED_MAIN_SHA256:
        raise RuntimeError(f"main SHA drifted: {sha(parent)}")
    a = make(parent, False)
    b = make(parent, True)
    atomic_write(OUT_A, a)
    atomic_write(OUT_B, b)
    save = LIVE_SAVE.read_bytes()
    atomic_write(SAVE_A, save)
    atomic_write(SAVE_B, save)

    sb = stock_base(parent)
    expected_a = [(sb + SITE_DX, sb + SITE_DX + 5), (sb + SITE_SI, sb + SITE_SI + 5)]
    expected_b = [(sb + SITE_FINAL, sb + SITE_FINAL + 5), (sb + SITE_DX, sb + SITE_DX + 5), (sb + SITE_SI, sb + SITE_SI + 5)]
    def logic_runs(candidate: bytes):
        return [(x, y) for x, y in diff_runs(parent, candidate) if x < len(candidate) - 2]
    def within_expected(runs: list[tuple[int, int]], allowed: list[tuple[int, int]]) -> bool:
        return all(any(lo <= x and y <= hi for lo, hi in allowed) for x, y in runs)
    if not within_expected(logic_runs(a), expected_a):
        raise RuntimeError(f"A unexpected logic diff: {logic_runs(a)}")
    if not within_expected(logic_runs(b), expected_b):
        raise RuntimeError(f"B unexpected logic diff: {logic_runs(b)}")
    if a[sb + SITE_DX : sb + SITE_DX + 5] != STOCK_DX or a[sb + SITE_SI : sb + SITE_SI + 5] != STOCK_SI:
        raise RuntimeError("A stock site restore failed")
    if b[sb + SITE_DX : sb + SITE_DX + 5] != STOCK_DX or b[sb + SITE_SI : sb + SITE_SI + 5] != STOCK_SI or b[sb + SITE_FINAL : sb + SITE_FINAL + 5] != STOCK_FINAL:
        raise RuntimeError("B stock site restore failed")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_scene_global_renderer_ab_candidates.py",
        "ok": True,
        "parent": {"path": "out/patch/monoeye_ko_expanded.wsc", "sha256": sha(parent), "checksum": checksum(parent)},
        "diagnosis": {
            "confirmed": [
                "FGXScroll is 00 in both Original and Main states",
                "page20 D4F1 overlay bypass did not change the visible misalignment",
                "78:8000-789200 animation/update code is byte-exact Original vs Main except the three global intermission renderer hook sites",
                "ending-state intermission remapper guard anchors are all false, so no intentional intermission entry substitution occurs"
            ],
            "remaining_test": "whether the extra global renderer far-calls/wrapper timing is responsible for the coroutine phase divergence"
        },
        "candidates": {
            "A": {
                "path": "out/patch/ending_scene_stock_renderer_stores_candidate.wsc",
                "sha256": sha(a),
                "checksum": checksum(a),
                "changes": ["78:A06E remapper call -> stock ES:[BX+3800]=DX", "78:A0EB remapper call -> stock ES:[BX+3800]=SI"],
                "tradeoff": "intermission transition protection is intentionally disabled for this diagnostic"
            },
            "B": {
                "path": "out/patch/ending_scene_stock_renderer_full_candidate.wsc",
                "sha256": sha(b),
                "checksum": checksum(b),
                "changes": ["A changes", "78:9C4D intermission wrapper call -> stock 8000:DEB5 bank restore"],
                "tradeoff": "intermission static wrapper and transition protection are intentionally bypassed for this diagnostic"
            }
        },
        "paired_saveram": {"sha256": sha(save), "byte_exact_live": True},
        "runtime_order": [
            "Test A first from cold reset with its paired SaveRAM.",
            "If A is still misaligned, test B the same way.",
            "Judge only the ending cinematic alignment; temporary intermission regressions are expected and must not be promoted."
        ],
        "promotion": "blocked_diagnostic_only"
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
