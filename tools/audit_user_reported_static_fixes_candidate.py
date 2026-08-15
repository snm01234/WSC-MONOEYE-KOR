#!/usr/bin/env python3
"""Independent static audit of user_reported_static_fixes_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_scenario_page_boundary_guard_candidate import original_unit_kinds  # noqa: E402
from expand_dictionary import NAME75_RANGES, NAME75_STRUCTURED_RANGES  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    build_free_slot_inventory,
    build_reference_union,
)
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402
from patch_3byte_dict_token import bank_local_for_index  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/user_reported_static_fixes_candidate.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAVE = ROOT / "sram/user_reported_static_fixes_candidate.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BUILD_REPORT = ROOT / "out/patch/user_reported_static_fixes_report.json"
OUT = ROOT / "out/patch/user_reported_static_fixes_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_PARENT = "1886d04e697baba17b454089dbd07cc556d3e49c5808e23e321cf6863773bc3d"
TERRAIN_START = 0x75E720
TERRAIN_END = 0x75E901
TERRAIN_STRIDE = 13
TERRAIN_COUNT = 37
DIALOGUE_ABS = 0x62663E
DIALOGUE_AFTER = bytes.fromhex("173418F8A6F044")
HIT_ABS = 0x67E9F7
HIT_AFTER = bytes.fromhex("E51807E501010101")
HIT_SLOT = 0x017E5


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def far_target(row: bytes) -> int:
    offset = row[0] | (row[1] << 8)
    segment = row[2] | (row[3] << 8)
    return 0x700000 + (((segment << 4) + offset) & 0xFFFFF)


def read_record(rom: bytes, logical: int) -> tuple[bytes, int]:
    base = stock_base(rom)
    got = read_encoded_z_safe(rom, base + logical, max_len=128)
    if got is None:
        raise AuditError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - base


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    rows: list[tuple[int, int]] = []
    start: int | None = None
    for pos, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = pos
        elif left == right and start is not None:
            rows.append((start, pos))
            start = None
    if start is not None:
        rows.append((start, len(before)))
    return rows


def main() -> int:
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    original = ORIGINAL.read_bytes()
    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    checks: dict[str, bool] = {}
    evidence: dict[str, Any] = {}

    def check(name: str, condition: bool, **details: Any) -> None:
        checks[name] = bool(condition)
        if details:
            evidence[name] = details

    check("parent_identity", sha256(parent) == EXPECTED_PARENT)
    check(
        "candidate_identity",
        sha256(candidate) == str(report["candidate"]["sha256"]),
        sha256=sha256(candidate),
    )
    check("candidate_size", len(candidate) == len(parent) == 16_777_216)
    stored_checksum = int.from_bytes(candidate[-2:], "little")
    check(
        "wonderswan_checksum",
        stored_checksum == (sum(candidate[:-2]) & 0xFFFF),
        stored=f"{stored_checksum:04X}",
    )
    check("main_unchanged", MAIN.read_bytes() == parent)
    check(
        "paired_save_exact",
        CANDIDATE_SAVE.read_bytes() == SAVE.read_bytes(),
        sha256=sha256(CANDIDATE_SAVE.read_bytes()),
    )

    parent_base = stock_base(parent)
    candidate_base = stock_base(candidate)
    original_base = stock_base(original)
    restored = candidate[
        candidate_base + TERRAIN_START : candidate_base + TERRAIN_END
    ]
    pristine = original[original_base + TERRAIN_START : original_base + TERRAIN_END]
    check(
        "terrain_table_exact_original",
        restored == pristine and len(restored) == TERRAIN_COUNT * TERRAIN_STRIDE,
    )
    terrain_targets = {
        "abao": far_target(restored[0:TERRAIN_STRIDE]),
        "space": far_target(restored[3 * TERRAIN_STRIDE : 4 * TERRAIN_STRIDE]),
    }
    check(
        "terrain_far_pointers",
        terrain_targets == {"abao": 0x75E58C, "space": 0x75E59A},
        **{key: f"{value:06X}" for key, value in terrain_targets.items()},
    )
    check(
        "terrain_source_guard",
        NAME75_STRUCTURED_RANGES == ((TERRAIN_START, TERRAIN_END),)
        and all(
            not (lo < TERRAIN_END and TERRAIN_START < hi)
            for lo, hi in NAME75_RANGES
        ),
    )

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    rendered_terrain: dict[str, str] = {}
    for key, logical in terrain_targets.items():
        payload, _term = read_record(candidate, logical)
        rendered_terrain[key] = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
    check(
        "terrain_korean_targets",
        rendered_terrain == {"abao": "아・바오아・쿠", "space": "우주"},
        rendered=rendered_terrain,
    )

    dialogue, dialogue_term = read_record(candidate, DIALOGUE_ABS)
    check(
        "dialogue_outer_original_grammar",
        dialogue == DIALOGUE_AFTER
        and dialogue_term == 0x626645
        and original_unit_kinds(dialogue[3:]) == ["dict", "dict"],
        payload=dialogue.hex().upper(),
        terminator=f"{dialogue_term:06X}",
    )
    check(
        "dialogue_nested_wrapper",
        bytes(dictionary.raw_entry(0x08A6)) == bytes.fromhex("F0FD")
        and dictionary.expand_index(0x08A6, tbl) == "오우"
        and dictionary.expand(dialogue[3:], tbl) == "오우！！",
        wrapper_raw=bytes(dictionary.raw_entry(0x08A6)).hex().upper(),
    )
    native_rows: dict[str, str] = {}
    native_ok = True
    for logical in (0x6053BF, 0x61E234, 0x62663E, 0x627FB5):
        payload, _term = read_record(candidate, logical)
        native_rows[f"{logical:06X}"] = payload.hex().upper()
        native_ok &= payload[:1] == b"\x17" and payload[2:3] == b"\x18"
        native_ok &= original_unit_kinds(payload[3:]) == ["dict", "dict"]
    check("similar_native_only_contracts", native_ok, records=native_rows)

    hit_raw = candidate[candidate_base + HIT_ABS : candidate_base + HIT_ABS + 8]
    hit_text = dictionary.expand(hit_raw, tbl).rstrip("\u3000 \t")
    check(
        "heat_type_fixed_extent",
        hit_raw == HIT_AFTER
        and candidate[candidate_base + HIT_ABS + 8 : candidate_base + HIT_ABS + 10]
        == b"\x00\x00",
        raw=hit_raw.hex().upper(),
    )
    check(
        "heat_type_korean",
        hit_text == "히트　무기　데미지",
        rendered=hit_text,
        phrase_raw=bytes(dictionary.raw_entry(HIT_SLOT)).hex().upper(),
    )

    union = build_reference_union(
        original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    inventory = build_free_slot_inventory(
        parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    check(
        "heat_type_slot_was_true_free",
        HIT_SLOT in inventory.ext3_free
        and not union.consumers_for(HIT_SLOT)
        and not union.parents_of(HIT_SLOT),
        slot=f"{HIT_SLOT:05X}",
    )

    hit_segment, hit_local = bank_local_for_index(HIT_SLOT)
    hit_entry = dictionary.entry_abs(HIT_SLOT)
    ou_entry = parent_dictionary.entry_abs(0x08A6)
    allowed = [
        (candidate_base + TERRAIN_START, candidate_base + TERRAIN_END),
        (candidate_base + DIALOGUE_ABS, candidate_base + DIALOGUE_ABS + 7),
        (ou_entry, ou_entry + len(parent_dictionary.raw_entry(0x08A6)) + 1),
        (candidate_base + HIT_ABS, candidate_base + HIT_ABS + 8),
        (hit_segment * 0x10000 + hit_local * 2, hit_segment * 0x10000 + hit_local * 2 + 2),
        (hit_entry, hit_entry + len(dictionary.raw_entry(HIT_SLOT)) + 1),
        (len(candidate) - 2, len(candidate)),
    ]
    runs = diff_runs(parent, candidate)
    unexpected = [
        (lo, hi)
        for lo, hi in runs
        if not any(a <= lo and hi <= b for a, b in allowed)
    ]
    check(
        "diff_whitelist",
        not unexpected,
        diff_runs=len(runs),
        unexpected=[f"{lo:07X}-{hi:07X}" for lo, hi in unexpected],
    )

    ok = all(checks.values())
    result = {
        "schema_version": 1,
        "generated_by": "tools/audit_user_reported_static_fixes_candidate.py",
        "ok": ok,
        "verification_mode": "static_only_no_bizhawk",
        "candidate_sha256": sha256(candidate),
        "counts": {
            "checks": len(checks),
            "passed": sum(checks.values()),
            "failed": sum(not value for value in checks.values()),
        },
        "checks": checks,
        "evidence": evidence,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not ok:
        raise AuditError(
            "static audit failed: "
            + ", ".join(name for name, passed in checks.items() if not passed)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
