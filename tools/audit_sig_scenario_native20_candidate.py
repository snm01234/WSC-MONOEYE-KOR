#!/usr/bin/env python3
"""Independent read-only audit for sig_scenario_native20_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_terminology_retranslation_candidate import consumer_abs_set
from expand_dictionary import iter_dict_indices
from extract_script import split_prefix_body
from mixed_residual_reference_union import (
    _nested_parents,
    _working_two_byte_external_refs,
    build_reference_union,
)
from monoeye_rom import (
    Tbl,
    dict_index_from_ext3_token,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/sig_scenario_native20_candidate.wsc"
SAVE = ROOT / "sram/sig_scenario_native20_candidate.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
EXP_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/sig_scenario_native20_audit.json"

EXPECTED_MAIN = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"
EXPECTED_WRAPPER = bytes.fromhex(
    "81FE981A7206"
    "81FEA41A761E"
    "81FE661B7206"
    "81FE701B7612"
    "81FE021D7206"
    "81FE0E1D7606"
    "9A8CFC00F0CB"
    "B0269AB5DE0080268B04CB"
)
EXPECTED_TRAMP = bytes.fromhex("9A18FF00F0C3")
TARGETS = (
    0x611D7A, 0x611D86, 0x611D96, 0x611DF8, 0x611E05,
    0x611E20, 0x611E4C, 0x611E57, 0x611E62, 0x611E78,
    0x611E86, 0x611E8F, 0x611E9B, 0x611EAE, 0x611EB7,
    0x611EC2, 0x611EE5, 0x611EEE, 0x611F6F, 0x611F79,
)
RESERVED = tuple(
    list(range(0x0D4C, 0x0D53))
    + list(range(0x0DB3, 0x0DB9))
    + list(range(0x0E81, 0x0E88))
)
CANNON = {
    0x75C3D3: (0x0FFAA, "메가　캐논　포"),
    0x75C7B2: (0x0FF3E, "배부　빔　캐논"),
    0x75C7E5: (0x0FF38, "빔　캐논"),
    0x75CBC7: (0x0FECF, "메가　캐논"),
}


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise RuntimeError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1])


def ext3_nested_hits(dictionary: Any, reserved: set[int]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for parent in range(0x1000, 0x11000):
        try:
            raw = bytes(dictionary.raw_entry(parent))
        except Exception:
            continue
        for child in iter_dict_indices(raw):
            if int(child) in reserved:
                out.setdefault(int(child), []).append(parent)
    return out


def checksum_value(rom: bytes) -> int:
    return sum(rom[:-2]) & 0xFFFF


def main() -> int:
    parent = bytes(load_rom(MAIN))
    cand = bytes(load_rom(CANDIDATE))
    orig = bytes(load_rom(ORIGINAL))
    save = SAVE.read_bytes()
    exp_meta = load_ext_meta(EXP_META)
    ext3_meta = load_ext_meta(EXT3_META)
    tbl = Tbl.load(TBL_PATH)
    dp = make_dictionary_ext3(parent, exp_meta, ext3_meta)
    dc = make_dictionary_ext3(cand, exp_meta, ext3_meta)
    union_parent = build_reference_union(orig, parent, ext_meta=exp_meta, ext3_meta=ext3_meta)
    union_cand = build_reference_union(orig, cand, ext_meta=exp_meta, ext3_meta=ext3_meta)

    failures: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    checks["main_identity"] = sha(parent) == EXPECTED_MAIN
    checks["candidate_size"] = len(cand) == 16_777_216
    checks["save_size"] = len(save) == 32_768
    checks["save_equals_live"] = save == (ROOT / "sram/monoeye_ko_expanded.sav").read_bytes()

    sb = stock_base(parent)
    checks["trampoline_exact"] = cand[sb+0x7AFFED:sb+0x7AFFF3] == EXPECTED_TRAMP
    checks["wrapper_exact"] = cand[sb+0x7FFF18:sb+0x7FFF18+len(EXPECTED_WRAPPER)] == EXPECTED_WRAPPER
    checks["old_helper_exact"] = cand[sb+0x7FFC8C:sb+0x7FFCAB] == parent[sb+0x7FFC8C:sb+0x7FFCAB]
    checks["ext3_runtime_exact"] = cand[sb+0x7FFD10:sb+0x7FFF18] == parent[sb+0x7FFD10:sb+0x7FFF18]

    reserved_set = set(RESERVED)
    ext_parent = _working_two_byte_external_refs(parent)
    nested_parent = _nested_parents(dp)
    ext3_parent = ext3_nested_hits(dp, reserved_set)
    parent_reserved = {}
    for idx in RESERVED:
        parent_reserved[f"{idx:04X}"] = {
            "external": [f"{r.abs:06X}" for r in ext_parent.get(idx, [])],
            "native_nested": [f"{x:04X}" for x in sorted(nested_parent.get(idx) or ())],
            "ext3_nested": [f"{x:05X}" for x in ext3_parent.get(idx, [])],
        }
    checks["parent_reserved_all_unreachable"] = all(
        not row["external"] and not row["native_nested"] and not row["ext3_nested"]
        for row in parent_reserved.values()
    )

    # Candidate native references: each reserved index must have exactly its one
    # assigned target as an external reference and no nested parents.
    ext_cand = _working_two_byte_external_refs(cand)
    nested_cand = _nested_parents(dc)
    ext3_cand = ext3_nested_hits(dc, reserved_set)
    candidate_reserved = {}
    for idx, target in zip(RESERVED, TARGETS):
        refs = sorted({r.abs for r in ext_cand.get(idx, [])})
        candidate_reserved[f"{idx:04X}"] = {
            "expected": f"{target:06X}",
            "external": [f"{x:06X}" for x in refs],
            "native_nested": [f"{x:04X}" for x in sorted(nested_cand.get(idx) or ())],
            "ext3_nested": [f"{x:05X}" for x in ext3_cand.get(idx, [])],
            "ok": refs == [target] and not nested_cand.get(idx) and not ext3_cand.get(idx),
        }
    checks["candidate_reserved_exact_consumers"] = all(r["ok"] for r in candidate_reserved.values())

    bank26 = cand[0x26_0000:0x27_0000]
    target_checks = []
    for logical, idx in zip(TARGETS, RESERVED):
        bp, bt = record(parent, logical)
        cp, ct = record(cand, logical)
        bpre, bbody, bkind = split_prefix_body(bp)
        cpre, cbody, ckind = split_prefix_body(cp)
        old_idx = None
        if len(bbody) >= 4 and bbody[:2] == b"\xE5\x18":
            old_idx = dict_index_from_ext3_token(*bbody[:4])
        ptr = bank26[idx*2] | (bank26[idx*2+1] << 8)
        phrase = b""
        if 0x2000 <= ptr < 0x10000:
            end = bank26.find(b"\x00", ptr)
            if end >= 0:
                phrase = bank26[ptr:end]
        source_phrase = bytes(dp.raw_entry(old_idx)) if old_idx is not None else b""
        expected_token = bytes([0xF0 + (idx >> 8), idx & 0xFF])
        check = {
            "abs": f"{logical:06X}",
            "native_index": f"{idx:04X}",
            "old_ext3_index": f"{old_idx:05X}" if old_idx is not None else None,
            "prefix_exact": cpre == bpre,
            "record_len_exact": len(cp) == len(bp),
            "terminator_exact": ct == bt,
            "candidate_body_native_token": cbody[:2] == expected_token,
            "candidate_body_no_ext3": not cbody.startswith(b"\xE5\x18"),
            "trailing_padding_01": all(x == 1 for x in cbody[2:]),
            "bank26_ptr": f"{ptr:04X}",
            "phrase_raw_exact": phrase == source_phrase and bool(phrase),
        }
        check["ok"] = all(v for k, v in check.items() if k in {
            "prefix_exact", "record_len_exact", "terminator_exact",
            "candidate_body_native_token", "candidate_body_no_ext3",
            "trailing_padding_01", "phrase_raw_exact"
        })
        target_checks.append(check)
        if not check["ok"]:
            failures.append({"kind": "target", **check})

    # No non-target record in the local event neighborhood may change.
    manifest = json.loads((ROOT / "out/patch/main_p1_base_manifest.json").read_text(encoding="utf-8"))
    non_target_changes = []
    target_set = set(TARGETS)
    seen = set()
    for sec in ("included", "excluded"):
        for row in manifest["population"].get(sec, []):
            if row.get("region") != "script":
                continue
            logical = int(row.get("logical_address") or 0)
            if not (0x611D00 <= logical < 0x612100) or logical in seen:
                continue
            seen.add(logical)
            if logical in target_set:
                continue
            try:
                bp, bt = record(parent, logical)
                cp, ct = record(cand, logical)
            except Exception:
                continue
            if bp != cp or bt != ct:
                non_target_changes.append(f"{logical:06X}")
    checks["local_non_target_record_changes"] = len(non_target_changes) == 0

    # Residual risky structure in the repaired local scope.
    residual = []
    for logical in TARGETS:
        cp, _ = record(cand, logical)
        pre, body, _ = split_prefix_body(cp)
        if len(pre) <= 1 and body.startswith(b"\xE5\x18"):
            residual.append(f"{logical:06X}")
    checks["local_risk_residual_zero"] = not residual

    # Every non-reserved bank26 pointer must remain FFFF.
    unexpected_ptrs = []
    for idx in range(0x1000):
        ptr = bank26[idx*2] | (bank26[idx*2+1] << 8)
        if idx in reserved_set:
            continue
        if ptr != 0xFFFF:
            unexpected_ptrs.append((f"{idx:04X}", f"{ptr:04X}"))
            if len(unexpected_ptrs) >= 20:
                break
    checks["bank26_nonreserved_ptrs_ffff"] = not unexpected_ptrs

    # Cannon correction.
    cannon_checks = []
    for logical, (idx, expected) in CANNON.items():
        bp, bt = record(parent, logical)
        cp, ct = record(cand, logical)
        pre, body, _ = split_prefix_body(cp)
        rendered = dc.expand(body, tbl).rstrip("　")
        row = {
            "abs": f"{logical:06X}",
            "slot": f"{idx:05X}",
            "expected": expected,
            "rendered": rendered,
            "record_bytes_exact": bp == cp,
            "terminator_exact": bt == ct,
            "consumer_exact": consumer_abs_set(union_cand, idx) == {logical},
        }
        row["ok"] = rendered == expected and row["record_bytes_exact"] and row["terminator_exact"] and row["consumer_exact"]
        cannon_checks.append(row)
        if not row["ok"]:
            failures.append({"kind": "cannon", **row})

    stored = cand[-2] | (cand[-1] << 8)
    calculated = checksum_value(cand)
    checks["checksum_exact"] = stored == calculated

    for key, value in checks.items():
        if isinstance(value, bool) and not value:
            failures.append({"kind": "gate", "gate": key})

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_sig_scenario_native20_candidate.py",
        "read_only": True,
        "ok": not failures,
        "inputs": {
            "main_sha256": sha(parent),
            "candidate_sha256": sha(cand),
            "candidate_size": len(cand),
            "save_sha256": sha(save),
        },
        "checks": checks,
        "parent_reserved": parent_reserved,
        "candidate_reserved": candidate_reserved,
        "targets": target_checks,
        "local_non_target_changes": non_target_changes,
        "local_risk_residual": residual,
        "bank26_unexpected_nonreserved_ptrs": unexpected_ptrs,
        "cannon": cannon_checks,
        "checksum": {"stored": f"{stored:04X}", "calculated": f"{calculated:04X}"},
        "failures": failures,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "candidate_sha256": report["inputs"]["candidate_sha256"],
        "targets": len(target_checks),
        "target_failures": sum(1 for x in target_checks if not x["ok"]),
        "reserved_exact": checks["candidate_reserved_exact_consumers"],
        "local_non_target_changes": len(non_target_changes),
        "risk_residual": len(residual),
        "cannon_failures": sum(1 for x in cannon_checks if not x["ok"]),
        "checksum": report["checksum"],
        "failures": failures[:10],
    }, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
