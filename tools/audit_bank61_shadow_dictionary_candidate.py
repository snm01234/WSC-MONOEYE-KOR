#!/usr/bin/env python3
"""Independent read-only audit for bank61 shadow-dictionary candidate."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from mixed_residual_reference_union import _working_two_byte_external_refs  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    dict_index_from_ext3_token,
    dict_index_from_token,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    le16,
    load_rom,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/bank61_shadow_dictionary_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/bank61_shadow_dictionary_candidate.sav"
MANIFEST = ROOT / "out/patch/main_p1_base_manifest.json"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
REPORT = ROOT / "out/patch/bank61_shadow_dictionary_audit.json"

EXPECTED_MAIN = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"
SHADOW_SEG0 = 0x26
SHADOW_POOL_START = 0x0800
SHADOW_SENTINEL = 0xFFFF
HOOK = 0x7FFF0D
CAVE = 0x7FFF18
CAVE_END = 0x7FFFF0
HOOK_EXPECT = bytes.fromhex("EA18FF00F0909090909090")
CAVE_REQUIRED = [
    bytes.fromhex("81FE00F0"),       # cmp si,F000
    bytes.fromhex("9AB2DE0080"),     # get current ROM1 bank
    bytes.fromhex("3CE1"),           # source bank61 only
    bytes.fromhex("81EB00F0"),       # token -> 12-bit index
    bytes.fromhex("B10AD3E8"),       # group=index>>10
    bytes.fromhex("81E3FF03"),       # local=index&03FF
    bytes.fromhex("0426"),           # expansion bank26 + group
    bytes.fromhex("9AB5DE0080"),     # map bank helper
    bytes.fromhex("3DFFFF"),         # sentinel fallback
    bytes.fromhex("9AD0FA0080"),     # far ptr conversion
    bytes.fromhex("EA430700A0"),     # stock phrase loop
    bytes.fromhex("EAE20600A0"),     # stock leaf fallback
]
CANNON = {
    0x75C3D3: "메가　캐논　포",
    0x75C7B2: "배부　빔　캐논",
    0x75C7E5: "빔　캐논",
    0x75CBC7: "메가　캐논",
}


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def record(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise RuntimeError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1])


def iter_native_indices(body: bytes) -> Iterable[int]:
    i = 0
    while i < len(body):
        if i + 3 < len(body) and is_ext3_magic(body[i], body[i + 1]):
            i += 4
            continue
        lead = body[i]
        if is_dict_token(lead) and i + 1 < len(body):
            yield dict_index_from_token(lead, body[i + 1])
            i += 2
            continue
        if is_kanji_lead(lead) and i + 1 < len(body):
            i += 2
            continue
        i += 1


def manifest_bank61() -> list[dict[str, Any]]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pop = data["population"]
    all_rows = list(pop.get("excluded") or []) + list(pop.get("included") or [])
    seen: set[int] = set()
    out = []
    for row in all_rows:
        a = int(row.get("logical_address") or 0)
        if row.get("region") != "script" or not 0x610000 <= a < 0x620000 or a in seen:
            continue
        seen.add(a)
        out.append(row)
    return sorted(out, key=lambda row: int(row["logical_address"]))


def shadow_raw(candidate: bytes, index: int) -> tuple[int, int, bytes]:
    group = index >> 10
    seg = SHADOW_SEG0 + group
    local = index & 0x3FF
    bank = bytes(slice_expansion_bank(candidate, seg))
    ptr = le16(bank, local * 2)
    if ptr == SHADOW_SENTINEL or ptr < SHADOW_POOL_START or ptr >= 0x10000:
        return seg, ptr, b""
    end = ptr
    while end < 0x10000 and bank[end] != 0:
        end += 1
    if end >= 0x10000:
        return seg, ptr, b""
    return seg, ptr, bank[ptr:end]


def main() -> int:
    main = bytes(load_rom(MAIN))
    cand = bytes(load_rom(CANDIDATE))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    main_dict = make_dictionary_ext3(main, ext_meta, ext3_meta)
    cand_dict = make_dictionary_ext3(cand, ext_meta, ext3_meta)
    failures: list[dict[str, Any]] = []

    if sha256(main) != EXPECTED_MAIN:
        failures.append({"kind": "main_identity", "got": sha256(main)})

    rows = manifest_bank61()
    main_records: dict[int, tuple[bytes, int, bytes, bytes]] = {}
    manifest_native_used: set[int] = set()
    for row in rows:
        a = int(row["logical_address"])
        p, t = record(main, a)
        prefix, body, _ = split_prefix_body(p)
        manifest_native_used.update(iter_native_indices(bytes(body)))
        main_records[a] = (p, t, bytes(prefix), bytes(body))
    whole_refs = _working_two_byte_external_refs(main, regions=("script",))
    native_used = {
        int(index)
        for index, refs in whole_refs.items()
        if any(0x610000 <= int(ref.abs) < 0x620000 for ref in refs)
    }
    if native_used != manifest_native_used:
        failures.append(
            {
                "kind": "whole_bank61_native_coverage",
                "whole_count": len(native_used),
                "manifest_count": len(manifest_native_used),
                "extra": [f"{x:04X}" for x in sorted(native_used - manifest_native_used)[:20]],
            }
        )

    risk: list[tuple[int, int, bytes, str]] = []
    for row in rows:
        a = int(row["logical_address"])
        p, t, prefix, body = main_records[a]
        manifest_prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        if len(manifest_prefix) > 1 or len(body) < 4 or not is_ext3_magic(body[0], body[1]):
            continue
        if prefix != manifest_prefix:
            failures.append({"kind": "manifest_prefix_drift", "abs": f"{a:06X}"})
            continue
        idx = dict_index_from_ext3_token(body[0], body[1], body[2], body[3])
        raw = bytes(main_dict.raw_entry(idx))
        risk.append((a, idx, raw, main_dict.expand(raw, tbl).rstrip("　")))

    if len(risk) != 1811:
        failures.append({"kind": "risk_count", "got": len(risk), "expected": 1811})
    if len({raw for _, _, raw, _ in risk}) != 1572:
        failures.append({"kind": "unique_phrase_count", "got": len({raw for _, _, raw, _ in risk}), "expected": 1572})

    target_addrs = {a for a, _, _, _ in risk}
    shadow_indices: set[int] = set()
    raw_to_shadow: dict[bytes, int] = {}
    target_checks = []
    for a, old_idx, raw_before, render_before in risk:
        bp, bt, bprefix, bbody = main_records[a]
        ap, at = record(cand, a)
        aprefix, abody, _ = split_prefix_body(ap)
        ok_shape = len(abody) == len(bbody) and len(abody) >= 2 and is_dict_token(abody[0])
        if ok_shape:
            idx = dict_index_from_token(abody[0], abody[1])
            ok_shape = idx < 0x0F00 and (idx & 0xFF) != 0 and all(v == 0x01 for v in abody[2:])
        else:
            idx = -1
        if idx >= 0:
            seg, ptr, raw_after = shadow_raw(cand, idx)
        else:
            seg, ptr, raw_after = -1, -1, b""
        render_after = main_dict.expand(raw_after, tbl).rstrip("　") if raw_after else ""
        if raw_before in raw_to_shadow and raw_to_shadow[raw_before] != idx:
            failures.append({"kind": "same_phrase_multiple_shadow_indices", "abs": f"{a:06X}"})
        elif idx >= 0:
            raw_to_shadow[raw_before] = idx
        check = {
            "abs": f"{a:06X}",
            "old_ext3_index": f"{old_idx:05X}",
            "shadow_index": f"{idx:04X}" if idx >= 0 else None,
            "shadow_bank": f"{seg:02X}" if seg >= 0 else None,
            "shadow_ptr": f"{ptr:04X}" if ptr >= 0 else None,
            "prefix_exact": bprefix == aprefix,
            "terminator_exact": bt == at,
            "size_exact": len(bp) == len(ap),
            "native_two_byte_shape": ok_shape,
            "parent_index_was_unused": idx not in native_used,
            "raw_phrase_exact": raw_after == raw_before,
            "render_exact": render_after == render_before,
            "ext3_removed": not (len(abody) >= 4 and is_ext3_magic(abody[0], abody[1])),
        }
        check["ok"] = all(v for k, v in check.items() if k not in {"abs", "old_ext3_index", "shadow_index", "shadow_bank", "shadow_ptr"})
        target_checks.append(check)
        if not check["ok"]:
            failures.append({"kind": "target", **check})
        if idx >= 0:
            shadow_indices.add(idx)

    # Non-target manifest records must be byte/terminator exact.
    non_target_changes = []
    for row in rows:
        a = int(row["logical_address"])
        if a in target_addrs:
            continue
        bp, bt = record(main, a)
        ap, at = record(cand, a)
        if bp != ap or bt != at:
            non_target_changes.append(f"{a:06X}")
            if len(non_target_changes) >= 40:
                break
    if non_target_changes:
        failures.append({"kind": "non_target_bank61_changes", "sample": non_target_changes})

    # Shadow banks: only 26/27 should have live entries for this build.
    pointer_counts: dict[str, int] = {}
    phrase_ranges_ok = True
    for seg in range(0x26, 0x2A):
        bank = bytes(slice_expansion_bank(cand, seg))
        count = 0
        for local in range(0x400):
            ptr = le16(bank, local * 2)
            if ptr == SHADOW_SENTINEL:
                continue
            count += 1
            if not (SHADOW_POOL_START <= ptr < 0x10000):
                phrase_ranges_ok = False
        pointer_counts[f"{seg:02X}"] = count
    if sum(pointer_counts.values()) != 1572:
        failures.append({"kind": "shadow_pointer_count", "counts": pointer_counts})
    if pointer_counts["28"] != 0 or pointer_counts["29"] != 0:
        failures.append({"kind": "unexpected_future_bank_usage", "counts": pointer_counts})
    if not phrase_ranges_ok:
        failures.append({"kind": "shadow_pointer_range"})

    # Runtime fixed-bank patch: parent tail was FF and candidate has the required
    # bank61-specific lookup/fallback signatures.
    sb = stock_base(cand)
    hook_bytes = cand[sb + HOOK : sb + HOOK + len(HOOK_EXPECT)]
    cave_bytes = cand[sb + CAVE : sb + CAVE_END]
    runtime = {
        "hook_exact": hook_bytes == HOOK_EXPECT,
        "parent_cave_was_ff": all(v == 0xFF for v in main[sb + CAVE : sb + CAVE_END]),
        "required_sequences": {seq.hex().upper(): seq in cave_bytes for seq in CAVE_REQUIRED},
        "cave_tail_still_ff": all(v == 0xFF for v in cave_bytes[103:]),
    }
    if not runtime["hook_exact"] or not runtime["parent_cave_was_ff"] or not runtime["cave_tail_still_ff"] or not all(runtime["required_sequences"].values()):
        failures.append({"kind": "runtime", **runtime})

    # Explicit live-evidence records.
    sig = {}
    for a in (0x611DF8, 0x611E05):
        ap, at = record(cand, a)
        prefix, body, _ = split_prefix_body(ap)
        idx = dict_index_from_token(body[0], body[1])
        seg, ptr, raw = shadow_raw(cand, idx)
        sig[f"{a:06X}"] = {
            "payload_hex": ap.hex().upper(),
            "prefix_hex": bytes(prefix).hex().upper(),
            "shadow_index": f"{idx:04X}",
            "shadow_bank": f"{seg:02X}",
            "shadow_render": main_dict.expand(raw, tbl).rstrip("　"),
            "terminator": f"{at - sb:06X}",
        }

    # Cannon correction is carried in the same candidate, with record bytes exact.
    cannon_checks = []
    for a, expected in sorted(CANNON.items()):
        bp, bt = record(main, a)
        ap, at = record(cand, a)
        _, body, _ = split_prefix_body(ap)
        rendered = cand_dict.expand(body, tbl).rstrip("　")
        row = {
            "abs": f"{a:06X}",
            "expected": expected,
            "rendered": rendered,
            "record_bytes_exact": bp == ap,
            "terminator_exact": bt == at,
            "ok": rendered == expected and bp == ap and bt == at,
        }
        cannon_checks.append(row)
        if not row["ok"]:
            failures.append({"kind": "cannon", **row})

    checksum_stored = cand[-2] | (cand[-1] << 8)
    checksum_calc = sum(cand[:-2]) & 0xFFFF
    save_same = CANDIDATE_SAVE.exists() and CANDIDATE_SAVE.read_bytes() == MAIN_SAVE.read_bytes()
    if checksum_stored != checksum_calc:
        failures.append({"kind": "checksum", "stored": f"{checksum_stored:04X}", "calc": f"{checksum_calc:04X}"})

    out = {
        "schema_version": 1,
        "generated_by": "tools/audit_bank61_shadow_dictionary_candidate.py",
        "read_only": True,
        "ok": not failures,
        "inputs": {
            "main_sha256": sha256(main),
            "candidate_sha256": sha256(cand),
            "main_save_sha256": sha256(MAIN_SAVE.read_bytes()),
            "candidate_save_sha256": sha256(CANDIDATE_SAVE.read_bytes()) if CANDIDATE_SAVE.exists() else None,
        },
        "counts": {
            "manifest_bank61_records": len(rows),
            "risk_targets": len(risk),
            "unique_parent_raw_phrases": len({raw for _, _, raw, _ in risk}),
            "shadow_indices_used": len(shadow_indices),
            "whole_bank61_native_indices_before": len(native_used),
            "whole_bank61_native_scan_matches_manifest": native_used == manifest_native_used,
            "shadow_pointer_counts": pointer_counts,
            "non_target_changes": len(non_target_changes),
            "target_failures": sum(not row["ok"] for row in target_checks),
            "cannon_failures": sum(not row["ok"] for row in cannon_checks),
        },
        "runtime": runtime,
        "sig": sig,
        "cannon": cannon_checks,
        "checksum": {"stored": f"{checksum_stored:04X}", "calculated": f"{checksum_calc:04X}", "ok": checksum_stored == checksum_calc},
        "candidate_save_matches_current_live_at_audit": save_same,
        "failures": failures,
    }
    REPORT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": out["ok"],
        "candidate_sha256": out["inputs"]["candidate_sha256"],
        "targets": len(risk),
        "shadow_indices": len(shadow_indices),
        "pointer_counts": pointer_counts,
        "target_failures": out["counts"]["target_failures"],
        "non_target_changes": len(non_target_changes),
        "cannon_failures": out["counts"]["cannon_failures"],
        "checksum": out["checksum"],
        "failures": failures[:10],
    }, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
