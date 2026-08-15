#!/usr/bin/env python3
"""Independent audit for terrain_gakehau_selective_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_scenario_page_boundary_guard_candidate import original_unit_kinds  # noqa: E402
from expand_dictionary import NAME75_STRUCTURED_RANGES  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/terrain_gakehau_selective_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/terrain_gakehau_selective_candidate.sav"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
BUILD_REPORT = ROOT / "out/patch/terrain_gakehau_selective_candidate_report.json"
OUT = ROOT / "out/patch/terrain_gakehau_selective_candidate_audit.json"

EXPECTED_MAIN_SHA = "2ec5a8e57ff58afa9076ba68ed10f703c6a9dbf6caa8d58587d99cd9654ffbce"
EXPECTED_CANDIDATE_SHA = "92fea67dc128d28a6c95e91faaeb21c8632547d23b8baace57cf904f3df3a40c"
TERRAIN_START = 0x75E720
TERRAIN_END = 0x75E901
DIALOGUE_ABS = 0x62663E
DIALOGUE_AFTER = bytes.fromhex("173418F8A6F044")
OU_WRAPPER_SLOT = 0x08A6
OU_SECOND_CONSUMER = 0x672555
NATIVE_NEIGHBORS = (0x6053BF, 0x61E234, 0x627FB5)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_record(rom: bytes, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def far_target(row: bytes) -> int:
    off = row[0] | (row[1] << 8)
    seg = row[2] | (row[3] << 8)
    return 0x700000 + ((((seg << 4) + off) & 0xFFFFF))


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    out = []
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


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    s, e = run
    cur = s
    for lo, hi in sorted(allowed):
        if hi <= cur:
            continue
        if lo > cur:
            return False
        cur = max(cur, hi)
        if cur >= e:
            return True
    return cur >= e


def main() -> int:
    main = MAIN.read_bytes()
    cand = CANDIDATE.read_bytes()
    original = ORIGINAL.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    cand_save = CANDIDATE_SAVE.read_bytes()
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(cand, ext_meta, ext3_meta)
    sb = stock_base(cand)
    osb = stock_base(original)

    checks: dict[str, bool] = {}
    checks["main_identity_exact"] = sha(main) == EXPECTED_MAIN_SHA
    checks["candidate_identity_exact"] = sha(cand) == EXPECTED_CANDIDATE_SHA
    checks["build_report_ok"] = build.get("ok") is True
    checks["candidate_save_exact_live"] = cand_save == live_save
    checks["source_guard_present"] = NAME75_STRUCTURED_RANGES == ((TERRAIN_START, TERRAIN_END),)

    terrain = cand[sb + TERRAIN_START : sb + TERRAIN_END]
    terrain_original = original[osb + TERRAIN_START : osb + TERRAIN_END]
    checks["terrain_table_exact_original"] = terrain == terrain_original
    targets = {
        "abao": far_target(terrain[:13]),
        "space": far_target(terrain[39:52]),
    }
    checks["terrain_pointers_exact"] = targets == {"abao": 0x75E58C, "space": 0x75E59A}
    renders = {}
    for key, logical in targets.items():
        payload, _ = read_record(cand, logical)
        renders[key] = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
    checks["terrain_render_exact"] = renders == {"abao": "아・바오아・쿠", "space": "우주"}

    payload, term = read_record(cand, DIALOGUE_ABS)
    checks["62663E_payload_exact"] = payload == DIALOGUE_AFTER
    checks["62663E_terminator_exact"] = term == 0x626645
    checks["62663E_native_two_token"] = original_unit_kinds(payload[3:]) == ["dict", "dict"]
    checks["62663E_wrapper_exact"] = bytes(dictionary.raw_entry(OU_WRAPPER_SLOT)) == bytes.fromhex("F0FD")
    checks["62663E_render_exact"] = dictionary.expand(payload[3:], tbl) == "오우！！"
    checks["secondary_wrapper_consumer_preserved"] = cand[sb + OU_SECOND_CONSUMER : sb + OU_SECOND_CONSUMER + 2] == bytes.fromhex("F8A6")

    neighbor_rows = {}
    for logical in NATIVE_NEIGHBORS:
        before, before_term = read_record(main, logical)
        after, after_term = read_record(cand, logical)
        ok = before == after and before_term == after_term and original_unit_kinds(after[3:]) == ["dict", "dict"]
        checks[f"neighbor_{logical:06X}_unchanged"] = ok
        neighbor_rows[f"{logical:06X}"] = {
            "payload_hex": after.hex().upper(),
            "terminator": f"{after_term:06X}",
            "body_units": original_unit_kinds(after[3:]),
        }

    # The unrelated fixed-stride heat-weapon/type patch in the old combined
    # builder must not be present here.
    checks["67E9F7_unrelated_change_absent"] = cand[sb + 0x67E9F7 : sb + 0x67E9FF] == main[sb + 0x67E9F7 : sb + 0x67E9FF]

    stored = int.from_bytes(cand[-2:], "little")
    computed = sum(cand[:-2]) & 0xFFFF
    checks["checksum_valid"] = stored == computed == 0x26D7

    wrapper_entry = dictionary.entry_abs(OU_WRAPPER_SLOT)
    allowed = [
        (sb + TERRAIN_START, sb + TERRAIN_END),
        (sb + DIALOGUE_ABS, sb + DIALOGUE_ABS + len(DIALOGUE_AFTER)),
        (wrapper_entry, wrapper_entry + 4),
        (len(cand) - 2, len(cand)),
    ]
    runs = diff_runs(main, cand)
    unexpected = [run for run in runs if not covered(run, allowed)]
    checks["diff_allowlist_clean"] = not unexpected
    checks["main_unchanged"] = sha(MAIN.read_bytes()) == EXPECTED_MAIN_SHA
    checks["live_save_unchanged"] = LIVE_SAVE.read_bytes() == live_save

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_terrain_gakehau_selective_candidate.py",
        "ok": all(checks.values()),
        "checks": checks,
        "terrain": {
            "range": "75E720-75E900",
            "targets": {k: f"{v:06X}" for k, v in targets.items()},
            "rendered": renders,
        },
        "gakehau": {
            "abs": "62663E",
            "payload_hex": payload.hex().upper(),
            "terminator": f"{term:06X}",
            "wrapper_raw": bytes(dictionary.raw_entry(OU_WRAPPER_SLOT)).hex().upper(),
            "render": dictionary.expand(payload[3:], tbl),
            "neighbors": neighbor_rows,
        },
        "diff": {
            "runs": len(runs),
            "bytes": sum(e - s for s, e in runs),
            "unexpected": [{"start": f"{s:07X}", "end": f"{e:07X}"} for s, e in unexpected],
        },
        "checksum": f"{stored:04X}",
        "runtime_validation_required": True,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
