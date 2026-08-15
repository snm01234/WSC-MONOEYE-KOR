#!/usr/bin/env python3
"""Independent static audit for ending global-renderer A/B diagnostics."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
A = ROOT / "out/patch/ending_scene_stock_renderer_stores_candidate.wsc"
B = ROOT / "out/patch/ending_scene_stock_renderer_full_candidate.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SAVE_A = ROOT / "sram/ending_scene_stock_renderer_stores_candidate.sav"
SAVE_B = ROOT / "sram/ending_scene_stock_renderer_full_candidate.sav"
BUILD = ROOT / "out/patch/ending_scene_global_renderer_ab_report.json"
OUT = ROOT / "out/patch/ending_scene_global_renderer_ab_audit.json"

MAIN_SHA = "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052"
A_SHA = "d0d0fbe22f98575f225d4ac5e06c1cb138eb9e7c7e83531321967c02dbdb13ef"
B_SHA = "ee8c3225c44b5657d15fdd6092583ada1d67cf585cf7016f2988175013feed28"
SITE_DX = 0x78A06E
SITE_SI = 0x78A0EB
SITE_FINAL = 0x789C4D
MAIN_DX = bytes.fromhex("9A CF FD 00 90")
MAIN_SI = bytes.fromhex("9A D5 FD 00 90")
MAIN_FINAL = bytes.fromhex("9A D3 FC 00 80")
STOCK_DX = bytes.fromhex("26 89 97 00 38")
STOCK_SI = bytes.fromhex("26 89 B7 00 38")
STOCK_FINAL = bytes.fromhex("9A B5 DE 00 80")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(a):
        if a[i] == b[i]:
            i += 1
            continue
        start = i
        while i < len(a) and a[i] != b[i]:
            i += 1
        out.append((start, i))
    return out


def allowed_only(diff: list[tuple[int, int]], allowed: list[tuple[int, int]], size: int) -> bool:
    for lo, hi in diff:
        if lo >= size - 2:
            continue
        if not any(a <= lo and hi <= b for a, b in allowed):
            return False
    return True


def checksum_valid(data: bytes) -> bool:
    return int.from_bytes(data[-2:], "little") == (sum(data[:-2]) & 0xFFFF)


def main() -> int:
    main_rom = MAIN.read_bytes()
    a = A.read_bytes()
    b = B.read_bytes()
    save = SAVE.read_bytes()
    save_a = SAVE_A.read_bytes()
    save_b = SAVE_B.read_bytes()
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    sb = stock_base(main_rom)
    da = runs(main_rom, a)
    db = runs(main_rom, b)
    allowed_a = [(sb + SITE_DX, sb + SITE_DX + 5), (sb + SITE_SI, sb + SITE_SI + 5)]
    allowed_b = [(sb + SITE_FINAL, sb + SITE_FINAL + 5), *allowed_a]

    checks = {
        "main_identity": sha(main_rom) == MAIN_SHA,
        "builder_ok": build.get("ok") is True,
        "A_identity": sha(a) == A_SHA,
        "B_identity": sha(b) == B_SHA,
        "main_sites_exact": (
            main_rom[sb + SITE_DX:sb + SITE_DX + 5] == MAIN_DX
            and main_rom[sb + SITE_SI:sb + SITE_SI + 5] == MAIN_SI
            and main_rom[sb + SITE_FINAL:sb + SITE_FINAL + 5] == MAIN_FINAL
        ),
        "A_stock_store_sites_exact": (
            a[sb + SITE_DX:sb + SITE_DX + 5] == STOCK_DX
            and a[sb + SITE_SI:sb + SITE_SI + 5] == STOCK_SI
            and a[sb + SITE_FINAL:sb + SITE_FINAL + 5] == MAIN_FINAL
        ),
        "B_all_three_sites_stock_exact": (
            b[sb + SITE_DX:sb + SITE_DX + 5] == STOCK_DX
            and b[sb + SITE_SI:sb + SITE_SI + 5] == STOCK_SI
            and b[sb + SITE_FINAL:sb + SITE_FINAL + 5] == STOCK_FINAL
        ),
        "A_diff_localized": allowed_only(da, allowed_a, len(a)),
        "B_diff_localized": allowed_only(db, allowed_b, len(b)),
        "A_checksum_valid": checksum_valid(a),
        "B_checksum_valid": checksum_valid(b),
        "paired_saveram_A_exact": save_a == save,
        "paired_saveram_B_exact": save_b == save,
        "main_unchanged": sha(MAIN.read_bytes()) == MAIN_SHA,
        "live_saveram_unchanged": SAVE.read_bytes() == save,
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_ending_scene_global_renderer_ab_candidates.py",
        "ok": all(checks.values()),
        "checks": checks,
        "A_sha256": sha(a),
        "B_sha256": sha(b),
        "A_diff_runs": [[f"{x:08X}", f"{y:08X}"] for x, y in da],
        "B_diff_runs": [[f"{x:08X}", f"{y:08X}"] for x, y in db],
        "runtime_validation_required": True,
        "promotion": "blocked_diagnostic_only",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
