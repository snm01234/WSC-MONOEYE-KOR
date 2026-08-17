#!/usr/bin/env python3
"""Read-only Phase A audit for exact-continuation native 2-token recovery.

This audit proves whether five bank10 extended-dictionary IDs can be reused
without changing the main ROM.  Two IDs must be completely unreferenced; three
are duplicate phrases whose syntactic script consumers can be retargeted to a
byte-identical canonical ID with a 2-byte -> 2-byte substitution.

Raw byte-pair hits are reported only as diagnostics and never used as ownership
proof.  The ownership gates are:
- authoritative dialogue runtime contracts,
- ext3-aware script/name75/aux zstring walks,
- nested dictionary-token consumers,
- dictionary pointer aliases / interior pointers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from dialogue_runtime_contracts import build_manifest  # noqa: E402
from mixed_residual_reference_union import iter_token_refs_with_offsets  # noqa: E402
from monoeye_rom import Tbl, load_rom, token_from_dict_index  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/exact_continuation_native_recovery_id_audit.json"
EXPECTED_MAIN_SHA = "fc7c3a426c866f8b60f5056571349c79d6ba11a2632beee4209dfebbf8a0c5e9"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"

# Reclaim -> canonical keeper.  None means the slot must be truly unreferenced.
POOL: dict[int, int | None] = {
    0x0F59: None,
    0x0F6D: None,
    0x0F70: 0x0F00,
    0x0F72: 0x0F01,
    0x0FC0: 0x0F07,
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ptr_aliases(dictionary: Any, index: int) -> dict[str, list[str]]:
    ptr = int(dictionary.ptrs[index])
    raw = bytes(dictionary.raw_entry(index))
    same = [i for i, p in enumerate(dictionary.ptrs) if int(p) == ptr and i != index]
    inside = [
        i
        for i, p in enumerate(dictionary.ptrs)
        if i != index and ptr < int(p) < ptr + len(raw) + 1
    ]
    return {
        "same_pointer": [f"{i:04X}" for i in same],
        "interior_pointer": [f"{i:04X}" for i in inside],
    }


def runtime_contract_refs(manifest: dict[str, Any], wanted: set[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in wanted}
    for row in manifest.get("contracts") or []:
        try:
            body = bytes.fromhex(str(row.get("baseline_body_hex") or ""))
        except ValueError:
            continue
        for index, length, offset in iter_token_refs_with_offsets(body, ext3_aware=True):
            if length != 2 or index not in wanted:
                continue
            out[index].append(
                {
                    "address": str(row.get("address") or ""),
                    "status": str(row.get("status") or ""),
                    "route": str(row.get("route") or ""),
                    "confidence": str(row.get("confidence") or ""),
                    "body_offset": offset,
                    "token_hex": body[offset:offset + 2].hex().upper(),
                    "baseline_text": str(row.get("baseline_text") or ""),
                }
            )
    return out


def raw_pair_hits(data: bytes, token: bytes, *, limit: int = 32) -> dict[str, Any]:
    # Diagnostic only.  This deliberately scans file bytes, not parsed records.
    hits: list[str] = []
    pos = 0
    total = 0
    while True:
        pos = data.find(token, pos)
        if pos < 0:
            break
        total += 1
        if len(hits) < limit:
            hits.append(f"{pos:08X}")
        pos += 1
    return {"count": total, "sample_file_offsets": hits, "ownership_evidence": False}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    main_rom = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    failures: list[dict[str, Any]] = []
    if sha(main_rom) != EXPECTED_MAIN_SHA:
        failures.append({"reason": "main_sha_drift", "actual": sha(main_rom), "expected": EXPECTED_MAIN_SHA})
    if sha(original) != EXPECTED_ORIGINAL_SHA:
        failures.append({"reason": "original_sha_drift", "actual": sha(original), "expected": EXPECTED_ORIGINAL_SHA})

    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(main_rom, ext_meta, ext3_meta)
    tbl = Tbl.load(TBL)
    wanted = set(POOL) | {k for k in POOL.values() if k is not None}

    manifest = build_manifest(original, main_rom, target_path=MAIN)
    contract_refs = runtime_contract_refs(manifest, wanted)
    external = external_occurrence_map(main_rom, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(dictionary, wanted=wanted, ext3_aware=True)
    manifest_addresses = {str(row.get("address") or "") for row in manifest.get("contracts") or []}

    rows: list[dict[str, Any]] = []
    reclaimable: list[str] = []
    for index, keeper in POOL.items():
        raw = bytes(dictionary.raw_entry(index))
        text = dictionary.expand_index(index, tbl)
        refs = list(external.get(index, []))
        runtime_refs = list(contract_refs.get(index, []))
        nested_refs = list(nested.get(index, []))
        aux_refs = [r for r in refs if r.get("region") in {"aux", "name75"}]
        script_refs = [r for r in refs if r.get("region") == "script"]
        script_contract_refs = [r for r in script_refs if str(r.get("record_abs") or "") in manifest_addresses]
        script_syntactic_only = [r for r in script_refs if str(r.get("record_abs") or "") not in manifest_addresses]
        alias = ptr_aliases(dictionary, index)

        keeper_info: dict[str, Any] | None = None
        if keeper is not None:
            keeper_raw = bytes(dictionary.raw_entry(keeper))
            keeper_text = dictionary.expand_index(keeper, tbl)
            keeper_info = {
                "index": f"{keeper:04X}",
                "token_hex": bytes(token_from_dict_index(keeper)).hex().upper(),
                "pointer": f"{int(dictionary.ptrs[keeper]):04X}",
                "raw_hex": keeper_raw.hex().upper(),
                "rendered": keeper_text,
                "byte_identical": raw == keeper_raw,
                "render_identical": text == keeper_text,
            }

        if keeper is None:
            eligible = not refs and not runtime_refs and not nested_refs and not alias["same_pointer"] and not alias["interior_pointer"]
            strategy = "true_free_repoint_only"
        else:
            # FF-page broad zstring hits can be false positives, so do not infer
            # ownership from raw bytes.  For safety, every syntactically parsed
            # script hit is retargeted to the byte-identical canonical token.
            # Any aux/name75 or nested consumer blocks reclaim outright.
            eligible = (
                keeper_info is not None
                and bool(keeper_info["byte_identical"])
                and bool(keeper_info["render_identical"])
                and not aux_refs
                and not nested_refs
                and not alias["same_pointer"]
                and not alias["interior_pointer"]
                and all(str(r.get("region")) == "script" for r in refs)
            )
            strategy = "duplicate_retarget_all_syntactic_script_refs_then_repoint"

        if not eligible:
            failures.append({
                "reason": "slot_not_reclaimable",
                "index": f"{index:04X}",
                "keeper": f"{keeper:04X}" if keeper is not None else None,
                "external": len(refs),
                "runtime_contract": len(runtime_refs),
                "nested": len(nested_refs),
                "aux_name75": len(aux_refs),
                "aliases": alias,
            })
        else:
            reclaimable.append(f"{index:04X}")

        rows.append(
            {
                "index": f"{index:04X}",
                "token_hex": bytes(token_from_dict_index(index)).hex().upper(),
                "pointer": f"{int(dictionary.ptrs[index]):04X}",
                "raw_hex": raw.hex().upper(),
                "rendered": text,
                "strategy": strategy,
                "keeper": keeper_info,
                "runtime_contract_consumers": runtime_refs,
                "external_consumers": refs,
                "external_counts": {
                    "total": len(refs),
                    "script": len(script_refs),
                    "script_present_in_runtime_contract_manifest": len(script_contract_refs),
                    "script_syntactic_only": len(script_syntactic_only),
                    "aux_name75": len(aux_refs),
                },
                "nested_consumers": nested_refs,
                "pointer_aliases": alias,
                "raw_pair_hits": raw_pair_hits(main_rom, bytes(token_from_dict_index(index))),
                "eligible": eligible,
            }
        )

    if len(reclaimable) != 5:
        failures.append({"reason": "reclaimable_count", "actual": len(reclaimable), "expected": 5})

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_exact_continuation_native_recovery_ids.py",
        "read_only": True,
        "rom_written": False,
        "ok": not failures,
        "main": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(main_rom), "size": len(main_rom)},
        "original": {"path": str(ORIGINAL.relative_to(ROOT)), "sha256": sha(original), "size": len(original)},
        "policy": {
            "raw_hits_are_ownership_evidence": False,
            "duplicate_consumers": "retarget every ext3-aware syntactic script zstring occurrence to a byte-identical canonical 2-byte token before repointing",
            "aux_name75_consumer": "hard block",
            "nested_consumer": "hard block",
            "pointer_alias_or_interior_entry": "hard block",
        },
        "manifest": {
            "contracts": len(manifest.get("contracts") or []),
            "counts": manifest.get("counts") or {},
        },
        "reclaimable_ids": reclaimable,
        "slots": rows,
        "failures": failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "ok": report["ok"],
        "reclaimable_ids": reclaimable,
        "failures": failures,
        "report": str(args.out),
    }, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
