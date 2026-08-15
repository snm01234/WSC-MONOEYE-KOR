#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import stock_base, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CAND = ROOT / "out/patch/intermission_layout_fullstatic_candidate/intermission_layout_fullstatic_candidate.wsc"
OUT = ROOT / "out/patch/intermission_layout_fullstatic_candidate/boot_bisect"

RANGES = {
    "focus": [(0x542000, 0x544400)],
    "static": [(0x544400, 0x54B780)],
    "transition": [(0x54B780, 0x550000)],
    "runtime7d": [(0x7DFA40, 0x7E0000)],
    "render_hook": [(0x789C4D, 0x789C52)],
    "static_wrapper": [(0x78FCD3, 0x790000)],
    "private79": [(0x79FA8F, 0x7A0000)],
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(name: str, groups: list[str]) -> Path:
    main = MAIN.read_bytes()
    cand = CAND.read_bytes()
    base = stock_base(main)
    out = bytearray(main)
    for group in groups:
        for lo, hi in RANGES[group]:
            out[base + lo : base + hi] = cand[base + lo : base + hi]
    update_ws_checksum(out)
    path = OUT / f"{name}.wsc"
    path.write_bytes(out)
    print(name, groups, sha(bytes(out)), path)
    return path


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for group in RANGES:
        build(group, [group])
    build("focus_static_transition", ["focus", "static", "transition"])
    build("runtime_hook_wrapper", ["runtime7d", "render_hook", "static_wrapper", "private79"])
    build("runtime_hook_oldwrapper", ["runtime7d", "render_hook"])
    build("runtime_hook_newwrapper", ["runtime7d", "render_hook", "static_wrapper"])
    build("all_except_staticwrapper", ["focus", "static", "transition", "runtime7d", "render_hook"])
    build("all_except_focus", ["static", "transition", "runtime7d", "render_hook", "static_wrapper", "private79"])
    build("all_except_runtime", ["focus", "static", "transition"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
