#!/usr/bin/env python3
"""Build a narrow v3 follow-up for the two runtime-broken short bank5F lines.

Parent: battle_runtime_user_reported_followup_v2_candidate.wsc

v2 fixed the full bank5F block, Uso duplicate leads, and battle placeholders.
Runtime testing showed only the two 3-byte bank5F first-line bodies still render
corrupted.  v2 had routed them through newly reclaimed stock slots FC94/FC5E.
The live main already contains exact, widely-used two-byte extension-dictionary
phrases for the same strings:

    FF7F -> 큭！
    FF19 -> 젠장！

v3 changes only those two bodies to the existing immutable phrases, restores
v2's now-unused reclaimed stock phrase bytes from main, and updates checksum.
No dictionary pointer or phrase data is created or changed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import detect_ext3_alias_page_count, load_ext_meta, make_dictionary_ext3
from monoeye_rom import BANK_SIZE, SEG_DICT, Tbl, read_encoded_z_safe, stock_base, update_ws_checksum

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT = PATCH / "battle_runtime_user_reported_followup_v2_candidate.wsc"
SPEC = ROOT / "data/bank5f_runtime_battle_voice_ko.json"
TBL = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT = PATCH / "battle_runtime_user_reported_followup_v3_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_runtime_user_reported_followup_v3_candidate.sav"
REPORT = PATCH / "battle_runtime_user_reported_followup_v3_candidate_report.json"

EXPECTED_PARENT_SHA = "4c47df16df75dd6202faabe1ee9e15ac4a3bd457ab8f4e761c3fef6998c81ad6"
EXPECTED_MAIN_SHA = "dbc0e567fb3d7d3cd207a7e1dc6a737fbca797c2693df1f9d4b8e29eb201e"  # deliberately checked below against actual constant fix
# The line above is retained only to make accidental edits obvious; real main SHA follows.
EXPECTED_MAIN_SHA_REAL = "dbc0e567fb3d7d3cd207a7e1dc6a737fbca5d131c299e8b0efc977daee546458"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

SHORTS = {
    0x5F044F: {
        "before": bytes.fromhex("8AFC9401"),
        "after": bytes.fromhex("8AFF7F01"),
        "text": "큭！",
        "slot": "0F7F",
    },
    0x5F047D: {
        "before": bytes.fromhex("8AFC5E01"),
        "after": bytes.fromhex("8AFF1901"),
        "text": "젠장！",
        "slot": "0F19",
    },
}

# v2 used these dead stock phrase regions only for the two short phrases.
# v3 no longer references them, so restore them byte-exact from current main.
RESTORE_STOCK_REGIONS = ((0x6B76, 0x6B86), (0x6C99, 0x6C9F))


class BuildError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def main() -> int:
    parent = PARENT.read_bytes()
    main_rom = MAIN.read_bytes()
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"v2 parent identity drifted: {sha(parent)}")
    if len(main_rom) != ROM_SIZE or sha(main_rom) != EXPECTED_MAIN_SHA_REAL:
        raise BuildError(f"main identity drifted: {sha(main_rom)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"main SaveRAM size drifted: {len(save)}")

    tbl = Tbl.load(TBL)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)

    # Existing shared phrases must already be exact before v3 writes anything.
    expected_slots = {0x0F7F: "큭！", 0x0F19: "젠장！"}
    shared_rows = []
    for index, expected in expected_slots.items():
        raw = bytes(d_parent.raw_entry(index))
        rendered = strip_pad(d_parent.expand(raw, tbl))
        if rendered != expected:
            raise BuildError(f"shared phrase drifted {index:04X}: {rendered!r}")
        shared_rows.append({"slot": f"{index:04X}", "raw_hex": raw.hex().upper(), "rendered": rendered})

    candidate = bytearray(parent)
    sb = stock_base(parent)
    changed_records = []
    for logical, info in SHORTS.items():
        live, term = payload_at(parent, logical)
        if live != info["before"]:
            raise BuildError(f"short parent drifted {logical:06X}: {live.hex().upper()}")
        at = sb + logical
        candidate[at:at + len(live)] = info["after"]
        if candidate[term] != 0:
            raise BuildError(f"terminator moved {logical:06X}")
        changed_records.append({
            "abs": f"{logical:06X}",
            "before_hex": live.hex().upper(),
            "after_hex": info["after"].hex().upper(),
            "expected": info["text"],
            "shared_slot": info["slot"],
        })

    # Remove v2's now-unused dead stock phrase writes.
    stock_file = sb + SEG_DICT * BANK_SIZE
    restored_regions = []
    for left, right in RESTORE_STOCK_REGIONS:
        a = stock_file + left
        b = stock_file + right
        candidate[a:b] = main_rom[a:b]
        restored_regions.append({
            "bank": "5F",
            "start": f"{left:04X}",
            "end": f"{right:04X}",
            "restored_hex": main_rom[a:b].hex().upper(),
        })

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)

    # Exact short runtime payload/render check.
    short_failures = []
    for logical, info in SHORTS.items():
        live, _ = payload_at(result, logical)
        body = live[1:]  # 8A bank5F control prefix remains byte-exact.
        rendered = strip_pad(d_result.expand(body, tbl))
        if live != info["after"] or rendered != info["text"]:
            short_failures.append({
                "abs": f"{logical:06X}",
                "payload": live.hex().upper(),
                "rendered": rendered,
                "expected": info["text"],
            })
    if short_failures:
        raise BuildError(f"short verification failed: {short_failures}")

    # Full bank5F canonical catalog must remain exact after the narrow change.
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    canonical = {str(k).upper(): str(v["after"]).replace(" ", "　") for k, v in (spec.get("targets") or {}).items()}
    bank5f_failures = []
    for address, expected in sorted(canonical.items()):
        live, _ = payload_at(result, int(address, 16))
        body = live[1:] if live and live[0] in {0xA1, 0x9B, 0x8A} else live
        rendered = strip_pad(d_result.expand(body, tbl))
        if rendered != expected:
            bank5f_failures.append({"abs": address, "rendered": rendered, "expected": expected})
    if bank5f_failures:
        raise BuildError(f"bank5F regression: {bank5f_failures[:10]}")

    if detect_ext3_alias_page_count(result) != 5:
        raise BuildError("five-bank alias runtime detector regressed")
    if MAIN.read_bytes() != main_rom or MAIN_SAVE.read_bytes() != save:
        raise BuildError("live main ROM/SaveRAM changed during v3 build")

    OUT.write_bytes(result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != save:
        raise BuildError("candidate SaveRAM differs from current main SaveRAM")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_runtime_user_reported_followup_v3_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_pending_runtime_test",
        "parent": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(parent), "size": len(parent)},
        "candidate": {"path": str(OUT.relative_to(ROOT)), "sha256": sha(result), "size": len(result), "checksum": f"{checksum:04X}"},
        "candidate_save": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(save), "size": len(save)},
        "diagnosis": {
            "runtime_result": "only the two 3-byte first-line bank5F rows were corrupted; ext3/ext3 pattern and Uso were normal",
            "v2_short_strategy": "new reclaimed stock phrases FC94/FC5E (offline-correct, runtime-corrupted)",
            "v3_short_strategy": "reuse existing live extension-dictionary phrases FF7F/FF19 without modifying their payloads",
        },
        "shared_phrases": shared_rows,
        "records": changed_records,
        "restored_unused_stock_regions": restored_regions,
        "verification": {
            "short_exact": True,
            "bank5f_catalog_75_exact": True,
            "alias_pages": 5,
            "main_unchanged": True,
            "save_exact": True,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
