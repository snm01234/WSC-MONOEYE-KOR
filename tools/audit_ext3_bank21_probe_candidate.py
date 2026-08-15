#!/usr/bin/env python3
"""Independent static audit for ext3_bank21_probe_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_ext3_bank21_probe_candidate as build
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import BANK_SIZE, Tbl, le16, load_rom, stock_base, update_ws_checksum

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/ext3_bank21_probe_candidate.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAVE = ROOT / "sram/ext3_bank21_probe_candidate.sav"
BUILD_REPORT = ROOT / "out/patch/ext3_bank21_probe_report.json"
OUT = ROOT / "out/patch/ext3_bank21_probe_candidate_audit.json"


class AuditError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def diff_offsets(before: bytes, after: bytes) -> list[int]:
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


def in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def raw_phrase(bank: bytes, local: int) -> bytes:
    pointer = le16(bank, local * 2)
    if not 0 <= pointer < BANK_SIZE:
        raise AuditError(f"bank21 pointer out of range: local={local:04X} ptr={pointer:04X}")
    end = pointer
    while end < BANK_SIZE and bank[end] != 0:
        end += 1
    if end >= BANK_SIZE:
        raise AuditError(f"bank21 phrase has no terminator: local={local:04X}")
    return bank[pointer:end]


def main() -> int:
    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    parent_save = PARENT_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    if len(parent) != len(candidate) or len(parent) != build.ROM_SIZE:
        raise AuditError("ROM size mismatch")

    sb = stock_base(parent)
    expected_leaf = build.build_bank21_leaf()
    candidate_leaf = candidate[
        sb + build.FREE_CAVE_START:sb + build.FREE_CAVE_START + len(expected_leaf)
    ]

    bank21_start = build.BANK21_SEG * BANK_SIZE
    parent_bank21 = parent[bank21_start:bank21_start + BANK_SIZE]
    candidate_bank21 = candidate[bank21_start:bank21_start + BANK_SIZE]

    ext_meta = load_ext_meta(build.EXT_META_PATH)
    ext3_meta = load_ext_meta(build.EXT3_META_PATH)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    tbl = Tbl.load(build.TBL_PATH)
    expected_raw = build.encode_probe_phrase(tbl)
    expected_render = dictionary.expand(expected_raw, tbl)
    probe_raw = raw_phrase(candidate_bank21, build.PROBE_LOCAL)
    probe_render = dictionary.expand(probe_raw, tbl)

    pointer_checks: list[dict[str, Any]] = []
    pointer_ok = True
    for local in range(build.BANK21_POINTER_COUNT):
        got = le16(candidate_bank21, local * 2)
        expected = build.BANK21_EMPTY_AT + 1 if local == build.PROBE_LOCAL else build.BANK21_EMPTY_AT
        ok = got == expected
        if not ok or local in (0, 1, build.BANK21_LOCAL_COUNT - 1, build.BANK21_LOCAL_COUNT, 0xFFF):
            pointer_checks.append(
                {
                    "local": f"{local:04X}",
                    "pointer": f"{got:04X}",
                    "expected": f"{expected:04X}",
                    "ok": ok,
                }
            )
        pointer_ok = pointer_ok and ok

    target_file = sb + build.TARGET_ABS
    expected_candidate_payload = (
        build.TARGET_PREFIX
        + build.PROBE_TOKEN
        + bytes([0x01]) * (build.TARGET_BODY_CAPACITY - len(build.PROBE_TOKEN))
    )
    target_parent = parent[target_file:target_file + build.TARGET_CAPACITY]
    target_candidate = candidate[target_file:target_file + build.TARGET_CAPACITY]

    # Recompute checksum on a copy.  A byte-exact result proves the stored
    # checksum already matches the candidate contents.
    checksum_copy = bytearray(candidate)
    recomputed_checksum = update_ws_checksum(checksum_copy)
    checksum_exact = bytes(checksum_copy) == candidate

    allowed = [
        (bank21_start, bank21_start + BANK_SIZE),
        (
            sb + build.FREE_CAVE_START,
            sb + build.FREE_CAVE_START + len(expected_leaf),
        ),
        (sb + build.LEAF, sb + build.LEAF + 6),
        (target_file, target_file + build.TARGET_CAPACITY),
        (len(parent) - 2, len(parent)),
    ]
    changed = diff_offsets(parent, candidate)
    unaccounted = [offset for offset in changed if not in_ranges(offset, allowed)]

    parent_hits = build.reserved_token_hits(parent)
    candidate_hits = build.reserved_token_hits(candidate)

    checks = {
        "parent_identity": sha256(parent) == build.EXPECTED_MAIN_SHA256,
        "candidate_identity_matches_report": sha256(candidate)
        == str(report.get("candidate", {}).get("sha256")),
        "parent_save_available": bool(parent_save),
        "candidate_save_initially_byte_exact": candidate_save == parent_save,
        "parent_bank21_all_ff": all(byte == 0xFF for byte in parent_bank21),
        "bank21_pointer_table_exact": pointer_ok,
        "bank21_empty_string_exact": candidate_bank21[build.BANK21_EMPTY_AT] == 0,
        "probe_raw_payload_byte_exact": probe_raw == expected_raw,
        "probe_render_exact": probe_render == expected_render == build.normalize_ko_text(build.PROBE_TEXT),
        "probe_phrase_terminated": candidate_bank21[
            build.BANK21_EMPTY_AT + 1 + len(probe_raw)
        ]
        == 0,
        "new_leaf_exact": candidate_leaf == expected_leaf,
        "new_leaf_fits_verified_ff_run": build.FREE_CAVE_START + len(expected_leaf)
        <= build.FREE_CAVE_END,
        "leaf_hook_exact": candidate[sb + build.LEAF:sb + build.LEAF + 6]
        == build.far_jmp(build.FREE_CAVE_START & 0xFFFF, build.EXT_CAVE_SEG) + b"\x90",
        "site1_hook_unchanged": candidate[sb + build.SITE1:sb + build.SITE1 + 5]
        == parent[sb + build.SITE1:sb + build.SITE1 + 5]
        == build.EXPECTED_SITE1_HOOK,
        "site2_hook_unchanged": candidate[
            sb + build.SITE2_FIXED:sb + build.SITE2_FIXED + 5
        ]
        == parent[sb + build.SITE2_FIXED:sb + build.SITE2_FIXED + 5]
        == build.EXPECTED_SITE2_HOOK,
        "accepted_old_leaf_body_unchanged": candidate[
            sb + build.OLD_LEAF_START:sb + build.OLD_LEAF_END
        ]
        == parent[sb + build.OLD_LEAF_START:sb + build.OLD_LEAF_END],
        "accepted_walkers_unchanged": candidate[
            sb + build.WALKER1_START:sb + build.FREE_CAVE_START
        ]
        == parent[sb + build.WALKER1_START:sb + build.FREE_CAVE_START],
        "target_parent_exact": target_parent == build.EXPECTED_TARGET_PAYLOAD,
        "target_candidate_exact": target_candidate == expected_candidate_payload,
        "target_terminator_unchanged": candidate[target_file + build.TARGET_CAPACITY]
        == parent[target_file + build.TARGET_CAPACITY]
        == build.EXPECTED_TARGET_TERMINATOR,
        "parent_alias_reference_count_zero": parent_hits == [],
        "candidate_alias_reference_is_probe_only": candidate_hits
        == [target_file + len(build.TARGET_PREFIX)],
        "ext3_banks_11_20_byte_exact": all(
            candidate[seg * BANK_SIZE:(seg + 1) * BANK_SIZE]
            == parent[seg * BANK_SIZE:(seg + 1) * BANK_SIZE]
            for seg in range(0x11, 0x21)
        ),
        "stock_dictionary_bank_byte_exact": candidate[
            sb + 0x5F0000:sb + 0x600000
        ]
        == parent[sb + 0x5F0000:sb + 0x600000],
        "checksum_exact": checksum_exact,
        "unaccounted_changed_bytes_zero": not unaccounted,
        "main_rom_still_untouched": sha256(PARENT.read_bytes())
        == build.EXPECTED_MAIN_SHA256,
        "main_save_still_untouched": sha256(PARENT_SAVE.read_bytes())
        == sha256(parent_save),
    }
    ok = all(checks.values())

    audit = {
        "schema_version": 1,
        "generated_by": "tools/audit_ext3_bank21_probe_candidate.py",
        "read_only": True,
        "ok": ok,
        "parent": {
            "path": str(PARENT.relative_to(ROOT)),
            "sha256": sha256(parent),
        },
        "candidate": {
            "path": str(CANDIDATE.relative_to(ROOT)),
            "sha256": sha256(candidate),
            "stored_checksum_hex": candidate[-2:].hex().upper(),
            "recomputed_checksum": f"{recomputed_checksum:04X}",
        },
        "render_equivalence": {
            "translation_source": "fresh_llm_reviewed_for_user_captured_line",
            "expected_raw_sha256": sha256(expected_raw),
            "probe_raw_sha256": sha256(probe_raw),
            "expected_text": expected_render,
            "probe_text": probe_render,
        },
        "runtime": {
            "new_leaf_address": f"{build.FREE_CAVE_START:06X}",
            "new_leaf_length": len(expected_leaf),
            "new_leaf_sha256": sha256(expected_leaf),
            "alias_tokens": "E5 18 06 01 .. E5 18 0F FF",
            "alias_bank": "21",
            "new_token_added": False,
            "new_wram_state_added": False,
        },
        "bank21": {
            "pointer_checks": pointer_checks,
            "probe_local": f"{build.PROBE_LOCAL:04X}",
            "probe_pointer": f"{le16(candidate_bank21, build.PROBE_LOCAL * 2):04X}",
            "phrase_bytes": len(probe_raw),
            "phrase_room_after": BANK_SIZE
            - (build.BANK21_EMPTY_AT + 1 + len(probe_raw) + 1),
        },
        "changes": {
            "changed_bytes": len(changed),
            "unaccounted_changed_bytes": len(unaccounted),
            "unaccounted_sample": [f"{offset:07X}" for offset in unaccounted[:32]],
        },
        "checks": checks,
    }
    OUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=True, indent=2))
    if not ok:
        raise AuditError("bank21 probe candidate audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
