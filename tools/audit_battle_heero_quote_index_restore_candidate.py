#!/usr/bin/env python3
"""Independent read-only audit of the Heero quote-index restore candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_battle_heero_quote_index_restore_candidate import (  # noqa: E402
    HEERO_NATIVE_PREFIX,
    HEERO_PTRS,
    HEERO_PTR_SITE,
    HEERO_PTR_VALUE,
    HEERO_RECORD,
    MAIN_SHA,
    NATIVE_SHA,
    TABLE_END,
    TABLE_SLICE_SHA,
    TABLE_START,
)
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base, ws_header

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
NATIVE = ROOT / "out/patch/battle_metadata5d_native_only_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CANDIDATE = ROOT / "out/patch/battle_heero_quote_index_restore_candidate.wsc"
SAVE = ROOT / "sram/battle_heero_quote_index_restore_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BUILD_REPORT = ROOT / "out/patch/battle_heero_quote_index_restore_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_OUT = ROOT / "out/patch/battle_heero_quote_index_restore_candidate_audit.json"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def le16_at(rom: bytes, logical: int) -> int:
    start = stock_base(rom) + logical
    return int.from_bytes(rom[start : start + 2], "little")


def check(failures: list[dict[str, Any]], kind: str, ok: bool, **extra: Any) -> None:
    if not ok:
        failures.append({"kind": kind, **extra})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    main_rom = bytes(load_rom(MAIN))
    native = bytes(load_rom(NATIVE))
    original = bytes(load_rom(ORIGINAL))
    candidate = bytes(load_rom(CANDIDATE))
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    d_candidate = Dictionary(candidate)
    failures: list[dict[str, Any]] = []

    check(failures, "main_size", len(main_rom) == ROM_SIZE)
    check(failures, "candidate_size", len(candidate) == ROM_SIZE)
    check(failures, "main_sha", sha256(MAIN.read_bytes()) == MAIN_SHA)
    check(failures, "native_sha", sha256(NATIVE.read_bytes()) == NATIVE_SHA)
    check(failures, "candidate_sha", sha256(candidate) == str(build["candidate"]["sha256"]).lower())
    check(failures, "main_unmodified", MAIN.read_bytes() == main_rom)
    check(failures, "live_save_unmodified", MAIN_SAVE.read_bytes() == SAVE.read_bytes())
    check(failures, "save_size", SAVE.stat().st_size == SAVE_SIZE)
    check(failures, "save_copied", SAVE.read_bytes() == MAIN_SAVE.read_bytes())
    check(failures, "build_ok", build.get("ok") is True)

    orig_sb = stock_base(original)
    cand_sb = stock_base(candidate)
    native_sb = stock_base(native)
    good = bytes(original[orig_sb + TABLE_START : orig_sb + TABLE_END])
    got = bytes(candidate[cand_sb + TABLE_START : cand_sb + TABLE_END])
    native_slice = bytes(native[native_sb + TABLE_START : native_sb + TABLE_END])
    check(failures, "original_slice_sha", sha256(good) == TABLE_SLICE_SHA)
    check(failures, "table_matches_original", got == good)
    check(failures, "table_differs_from_native", native_slice != good)
    check(failures, "heero_ptr", le16_at(candidate, HEERO_PTR_SITE) == HEERO_PTR_VALUE)
    for addr, value in HEERO_PTRS.items():
        check(failures, f"ptr_{addr:06X}", le16_at(candidate, addr) == value, want=f"{value:04X}")

    rec = read_encoded_z_safe(candidate, cand_sb + HEERO_RECORD, max_len=32)
    live = bytes(rec[0]) if rec else b""
    check(failures, "heero_body_prefix", live.startswith(HEERO_NATIVE_PREFIX))
    rendered = d_candidate.expand(live[1:], tbl).rstrip("\u3000 ") if live else ""
    check(failures, "heero_body_nonempty", bool(rendered.replace("…", "").strip()), render=rendered)
    check(failures, "heero_body_no_e518", live[1:3] != b"\xE5\x18")

    table_file = cand_sb + TABLE_START
    vs_native = diff_runs(native, candidate)
    unexpected = [
        run
        for run in vs_native
        if not covered(run, [(table_file, table_file + len(good)), (len(candidate) - 2, len(candidate))])
    ]
    check(failures, "vs_native_allowlist", not unexpected, runs=[
        {"start": f"{lo:08X}", "end": f"{hi:08X}"} for lo, hi in unexpected
    ])
    check(
        failures,
        "checksum",
        int(ws_header(candidate)["checksum"]) == (sum(candidate[:-2]) & 0xFFFF),
    )

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_heero_quote_index_restore_candidate.py",
        "ok": not failures,
        "parent_sha256": sha256(main_rom),
        "native_sha256": sha256(native),
        "candidate_sha256": sha256(candidate),
        "heero_render": rendered,
        "failures": failures,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "failures": len(failures), "heero_render": rendered}, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
