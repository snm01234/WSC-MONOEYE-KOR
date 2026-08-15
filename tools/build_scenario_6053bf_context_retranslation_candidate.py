#!/usr/bin/env python3
"""Build a focused source-grounded retranslation candidate for scenario 6053BF.

Scope: 6053C8..605824, i.e. the continuous battle/tutorial scene after the
runtime-proven native predecessor 6053BF (하하하하!!) through the final
"leave before reinforcements arrive" line.

Safety model:
- parent is the audited 14K main-carry candidate;
- 6053BF itself is byte-exact protected;
- every target already contains exactly one E5 18 portal at body offset 0 or 1;
- only that 4-byte portal is retargeted to a fresh true-free private ext3 slot;
- leading 18, record extent, NUL boundary and 01 padding remain byte-exact;
- no existing ext3 phrase storage is overwritten.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_main_translation_rebase_candidate import bank_cursor  # noqa: E402
from dialogue_runtime_contracts import audit_manifest, build_manifest  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from mixed_residual_reference_union import build_reference_union  # noqa: E402
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402
from patch_3byte_dict_token import INDEX_BASE, index_end, token_from_ext3_index  # noqa: E402

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PARENT = ROOT / "out/patch/main_translation_rebase_maincarry_candidate.wsc"
PARENT_SAVE = ROOT / "sram/main_translation_rebase_maincarry_candidate.sav"
PARENT_CONTRACTS = ROOT / "out/script/main_translation_rebase_candidate_contracts.json"
TRANSLATIONS = ROOT / "data/scenario_6053bf_context_retranslation_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/scenario_6053bf_context_retranslation_candidate.wsc"
OUT_SAVE = ROOT / "sram/scenario_6053bf_context_retranslation_candidate.sav"
OUT_REPORT = ROOT / "out/patch/scenario_6053bf_context_retranslation_candidate_report.json"
OUT_CONTRACTS = ROOT / "out/script/scenario_6053bf_context_retranslation_candidate_contracts.json"
EXPECTED_PARENT_SHA = "a1386fcf205d6281a3bc63d47ac15098faf824ccc932eb7c7d1794e2f23bd10d"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
LINE_LIMIT = 20
TARGET_LO = 0x6053C8
TARGET_HI = 0x605824
PROTECTED = (0x6053BF, 0x61E234, 0x62663E, 0x627FB5)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def portal_offsets(body: bytes) -> list[int]:
    return [i for i in range(max(0, len(body) - 3)) if body[i:i + 2] == b"\xE5\x18"]


def ext3_index_from_token(token: bytes) -> int:
    if len(token) != 4 or token[:2] != b"\xE5\x18":
        raise BuildError(f"not an ext3 token: {token.hex().upper()}")
    return INDEX_BASE + (token[2] << 8) + token[3]


def main() -> int:
    parent = bytes(load_rom(PARENT))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent identity drifted: {sha(parent)}")
    if not PARENT_SAVE.is_file() or PARENT_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("paired parent SaveRAM missing or wrong size")

    source_doc = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
    targets = {str(k).upper(): normalize_ko_text(str(v)) for k, v in source_doc["targets"].items()}
    if len(targets) != 57:
        raise BuildError(f"expected 57 context targets, got {len(targets)}")
    if min(int(a, 16) for a in targets) != TARGET_LO or max(int(a, 16) for a in targets) != TARGET_HI:
        raise BuildError("target address range drifted")

    contracts_doc = json.loads(PARENT_CONTRACTS.read_text(encoding="utf-8"))
    contracts = {str(row["address"]).upper(): row for row in contracts_doc["contracts"]}
    if set(targets) - set(contracts):
        raise BuildError(f"targets missing contracts: {sorted(set(targets)-set(contracts))[:10]}")

    tbl = Tbl.load(TBL_PATH)
    encoded: dict[str, bytes] = {}
    width_rows = []
    for address, text in sorted(targets.items()):
        cells = len(text.replace("<E62F>", ""))
        width_rows.append({"abs": address, "cells": cells, "text": text})
        if cells > LINE_LIMIT:
            raise BuildError(f"20-cell violation {address}: {cells} {text!r}")
        payload = try_encode_ko_text(text, tbl, hangul_marker_code=marker_code(), hangul_marker_mode="run")
        if payload is None or b"\x00" in payload:
            raise BuildError(f"unencodable target {address}: {text!r}")
        encoded[address] = bytes(payload)

    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks != 16:
        raise BuildError(f"unexpected ext3 bank count {num_banks}")
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    alias_pages = int(dictionary.ext3_alias_page_count)
    if alias_pages != 5:
        raise BuildError(f"expected five-page ext3 alias runtime, got {alias_pages}")

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)

    # Reserve every ext3 index explicitly present in the runtime-contract corpus,
    # including leading-18 quarantined continuations that generic reference scans
    # may intentionally treat conservatively.
    contract_indices: set[int] = set()
    for row in contracts.values():
        body = bytes.fromhex(str(row.get("baseline_body_hex") or ""))
        for off in portal_offsets(body):
            contract_indices.add(ext3_index_from_token(body[off:off + 4]))

    physical_segs = list(range(0x11, 0x21)) + list(range(0x21, 0x26))
    cursors = {seg: bank_cursor(parent, seg, union=union, alias_pages=alias_pages) for seg in physical_segs}
    cursor_start = dict(cursors)

    free_by_seg: dict[int, deque[int]] = defaultdict(deque)
    for index in range(INDEX_BASE, index_end(num_banks) + 1):
        raw = index - INDEX_BASE
        if ((raw >> 8) & 0xFF) == 0 or (raw & 0xFF) == 0:
            continue
        if index in contract_indices or dictionary._ext3_is_alias(index):
            continue
        if not union.is_true_free(index) or union.parents_of(index):
            continue
        seg, _local = dictionary._ext3_bank_local(index)
        free_by_seg[int(seg)].append(index)

    candidate = bytearray(parent)
    storage_changes: list[dict[str, Any]] = []
    allocated: set[int] = set()

    def append_private(index: int, payload: bytes) -> None:
        seg, local = dictionary._ext3_bank_local(index)
        seg = int(seg)
        local = int(local)
        cursor = cursors[seg]
        need = len(payload) + 1
        if cursor + need > BANK_SIZE:
            raise BuildError(f"no room in physical ext3 bank {seg:02X}")
        base = seg * BANK_SIZE
        candidate[base + cursor:base + cursor + len(payload)] = payload
        candidate[base + cursor + len(payload)] = 0
        candidate[base + local * 2:base + local * 2 + 2] = cursor.to_bytes(2, "little")
        cursors[seg] = cursor + need
        storage_changes.append({
            "index": f"{index:05X}",
            "physical_segment": f"{seg:02X}",
            "physical_local": f"{local:03X}",
            "pointer": f"{cursor:04X}",
            "encoded_len": len(payload),
        })

    def allocate_private(payload: bytes) -> int:
        need = len(payload) + 1
        candidates = [seg for seg, slots in free_by_seg.items() if slots and cursors.get(seg, BANK_SIZE) + need <= BANK_SIZE]
        if not candidates:
            raise BuildError("private ext3 capacity exhausted")
        seg = max(candidates, key=lambda s: BANK_SIZE - cursors[s])
        index = free_by_seg[seg].popleft()
        if index in allocated:
            raise BuildError(f"duplicate private allocation {index:05X}")
        allocated.add(index)
        append_private(index, payload)
        return index

    sb = stock_base(parent)
    applied: dict[str, dict[str, Any]] = {}
    allowed_script_positions: set[int] = set()
    for address in sorted(targets):
        contract = contracts[address]
        start = sb + int(contract["body_start"], 16)
        cap = int(contract["body_capacity"])
        before = bytes.fromhex(str(contract.get("baseline_body_hex") or ""))
        if parent[start:start + cap] != before:
            raise BuildError(f"parent contract drift at {address}")
        offs = portal_offsets(before)
        if len(offs) != 1 or offs[0] not in {0, 1}:
            raise BuildError(f"unsupported portal shape {address}: {before.hex().upper()} offs={offs}")
        off = offs[0]
        if off == 1 and before[0] != 0x18:
            raise BuildError(f"offset-1 portal without preserved leading 18 at {address}")
        new_index = allocate_private(encoded[address])
        token = token_from_ext3_index(new_index, num_banks=num_banks)
        after = bytearray(before)
        after[off:off + 4] = token
        if bytes(after[:off]) != before[:off] or bytes(after[off + 4:]) != before[off + 4:]:
            raise BuildError(f"non-portal body drift at {address}")
        candidate[start:start + cap] = after
        for pos in range(start + off, start + off + 4):
            allowed_script_positions.add(pos)
        applied[address] = {
            "text": targets[address],
            "cells": len(targets[address]),
            "route": contract.get("route"),
            "status": contract.get("status"),
            "body_capacity": cap,
            "portal_offset": off,
            "old_body_hex": before.hex().upper(),
            "new_body_hex": bytes(after).hex().upper(),
            "old_slot": f"{ext3_index_from_token(before[off:off+4]):05X}",
            "new_slot": f"{new_index:05X}",
        }

    # Protect the runtime-proven predecessor and other recent native-only anchors.
    protected_bytes: dict[str, str] = {}
    for logical in PROTECTED:
        address = f"{logical:06X}"
        contract = contracts[address]
        start = sb + int(contract["body_start"], 16)
        cap = int(contract["body_capacity"])
        if candidate[start:start + cap] != parent[start:start + cap]:
            raise BuildError(f"protected runtime body changed: {address}")
        protected_bytes[address] = parent[start:start + cap].hex().upper()

    update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    OUT_ROM.write_bytes(candidate_bytes)
    shutil.copy2(PARENT_SAVE, OUT_SAVE)

    # Candidate-local runtime manifest plus decoder sanity.
    manifest = build_manifest(original, candidate_bytes, target_path=OUT_ROM)
    safety = audit_manifest(candidate_bytes, manifest, target_path=OUT_ROM)
    OUT_CONTRACTS.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if safety["counts"]["hard_failures"]:
        raise BuildError(f"candidate runtime-contract hard failures: {safety['hard_failures_rows'][:10]}")

    candidate_contracts = {str(row["address"]).upper(): row for row in manifest["contracts"]}
    d_candidate = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    verification_failures: list[dict[str, Any]] = []
    boundary_failures: list[str] = []
    for address, info in applied.items():
        slot = int(str(info["new_slot"]), 16)
        actual = normalize_ko_text(d_candidate.expand_index(slot, tbl))
        if actual != targets[address]:
            verification_failures.append({"abs": address, "expected": targets[address], "actual": actual})
        if candidate_contracts[address].get("baseline_boundary") != contracts[address].get("baseline_boundary"):
            boundary_failures.append(address)
    if verification_failures or boundary_failures:
        raise BuildError(f"verification failed text={len(verification_failures)} boundary={len(boundary_failures)}")

    # Diff allowlist: new ext3 storage, the 57 same-length portal spans in E0,
    # and final checksum bytes only.
    changed_banks = []
    outside_script_positions = []
    for bank in range(0x100):
        lo = bank * BANK_SIZE
        hi = lo + BANK_SIZE
        if parent[lo:hi] != candidate_bytes[lo:hi]:
            changed_banks.append(bank)
    allowed_banks = set(physical_segs) | {0xE0, 0xFF}
    if any(bank not in allowed_banks for bank in changed_banks):
        raise BuildError(f"changed bank outside allowlist: {[f'{b:02X}' for b in changed_banks if b not in allowed_banks]}")
    e0_lo = 0xE0 * BANK_SIZE
    for i, (a, b) in enumerate(zip(parent[e0_lo:e0_lo + BANK_SIZE], candidate_bytes[e0_lo:e0_lo + BANK_SIZE])):
        pos = e0_lo + i
        if a != b and pos not in allowed_script_positions:
            outside_script_positions.append(pos)
    if outside_script_positions:
        raise BuildError(f"unexpected E0 script diffs: {[hex(x) for x in outside_script_positions[:20]]}")
    ff_lo = 0xFF * BANK_SIZE
    ff_bad = [ff_lo + i for i, (a, b) in enumerate(zip(parent[ff_lo:], candidate_bytes[ff_lo:])) if a != b and ff_lo + i < len(parent) - 2]
    if ff_bad:
        raise BuildError(f"unexpected non-checksum bank FF diffs: {[hex(x) for x in ff_bad[:20]]}")

    stored = int.from_bytes(candidate_bytes[-2:], "little")
    computed = sum(candidate_bytes[:-2]) & 0xFFFF
    if stored != computed:
        raise BuildError("WonderSwan checksum mismatch")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scenario_6053bf_context_retranslation_candidate.py",
        "status": "candidate_pending_user_runtime_validation",
        "promotion_allowed": False,
        "parent": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(parent)},
        "candidate": {"path": str(OUT_ROM.relative_to(ROOT)), "sha256": sha(candidate_bytes), "checksum": f"{stored:04X}"},
        "saveram": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(OUT_SAVE.read_bytes()), "byte_exact_parent_copy": OUT_SAVE.read_bytes() == PARENT_SAVE.read_bytes()},
        "scope": {"start": "6053C8", "end": "605824", "targets": len(targets), "protected_predecessor": "6053BF"},
        "terminology": source_doc["policy"]["terminology"],
        "counts": {
            "targets": len(targets),
            "private_ext3_slots_allocated": len(allocated),
            "max_cells": max(row["cells"] for row in width_rows),
            "runtime_contract_hard_failures": safety["counts"]["hard_failures"],
            "text_verify_failures": len(verification_failures),
            "boundary_failures": len(boundary_failures),
            "unexpected_script_diffs": len(outside_script_positions),
        },
        "changed_banks": [f"{b:02X}" for b in changed_banks],
        "protected_runtime_bodies": protected_bytes,
        "physical_bank_room": {f"{seg:02X}": {"before": cursor_start[seg], "after": cursors[seg], "room": BANK_SIZE-cursors[seg]} for seg in physical_segs},
        "storage_changes": storage_changes,
        "applied": applied,
        "runtime_test_focus": [
            "6053BF remains a standalone 하하하하!! page",
            "6053C8 begins next page with 플라나간 기관의 뉴타입이라길래 and no visible こ",
            "6053E9 and other leading-18 continuations do not expose visible こ",
            "60554D-60561F indirect/sankai tutorial page grouping remains intact",
            "605659-605824 capture tutorial continues through final withdrawal line",
            "62663E still does not expose bogus がけはう",
        ],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate": str(OUT_ROM.relative_to(ROOT)),
        "sha256": sha(candidate_bytes),
        "checksum": f"{stored:04X}",
        "targets": len(targets),
        "private_slots": len(allocated),
        "max_cells": max(row["cells"] for row in width_rows),
        "hard_failures": safety["counts"]["hard_failures"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
