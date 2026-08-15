#!/usr/bin/env python3
"""Build the data-first dialogue runtime-contract candidate from main TIP.

Only caller/screen-proven unsafe records are changed.  Every replacement uses
ordinary native stock tokens/direct native bytes; battle body-only and ID
continuation records contain no E5 18 or compact3 portal after this stage.
No portrait/OAM/runtime-hook bytes are touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import diff_runs, encode_phrase  # noqa: E402
from dialogue_runtime_contracts import (  # noqa: E402
    DEFAULT_MANIFEST,
    audit_manifest,
    boundary_signature,
    build_manifest,
    has_japanese,
    physical_widths,
    scan_portals,
    semantic_widths,
    write_manifest,
)
from expand_dictionary import payload_has_hangul_marker  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    find_rom,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_ROM = PATCH / "dialogue_runtime_contract_candidate.wsc"
OUT_SAVE = ROOT / "sram/dialogue_runtime_contract_candidate.sav"
REPORT = PATCH / "dialogue_runtime_contract_candidate_report.json"
SAFETY = PATCH / "dialogue_runtime_contract_candidate_safety.json"
EXPECTED_MAIN_SHA = "27321bdd4ed7fd6b35d56f80745d47946e2b517aadd83689d34c31b59694a483"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EMPTY_FILLER = bytes.fromhex("F0A9")


class BuildError(RuntimeError):
    pass


def sha(payload: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(payload)).hexdigest()


def ident(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def native_payloads(tbl: Tbl) -> dict[int, bytes]:
    token = token_from_dict_index
    direct = lambda value: encode_phrase(value, tbl)
    # Wording is source-grounded and deliberately concise.  The goal of this
    # stage is storage/caller safety; it does not manufacture extra records or
    # cross a bundle/control boundary.
    return {
        0x5C9794: token(0x0507) + token(0x00EE) + direct("긍지") + token(0x0071),
        0x5C97C0: token(0x0B5B) + b"\x2A" + token(0x0B9F) + token(0x0053) + direct("다！"),
        0x5D01F4: token(0x00F1) + token(0x004D) + token(0x0296) + token(0x03ED),
        0x5D0C39: token(0x0191) + direct("죽나？"),
        0x5D11C6: token(0x033E) + token(0x0361),
        0x5D5982: token(0x0499) + direct("군……"),
        0x5D5B1F: token(0x0499) + direct("군……"),
        0x5D5D58: token(0x0053) + b"\x01" + token(0x01C6) + token(0x004D) + direct("불만！"),
        0x5EAB36: token(0x0296) + direct("는다！！"),
        0x5EB3AA: token(0x0082) + b"\x03",
        0x5EB6B2: token(0x0296) + direct("는다！！"),
        0x5EBB7A: token(0x03D7),
        0x5EC27C: token(0x0296) + direct("는다！！"),
        # Scenario continuation: byte 18 is the visible Japanese こ and must be
        # removed.  The already measured native phrase is retained.
        0x61E23D: token(0x02B8),
        # This scenario continuation has an independently measured ext3 route;
        # keep its phrase token and replace visible 01 padding only.
        0x626509: bytes.fromhex("E5183C20"),
    }


def padded(payload: bytes, capacity: int) -> bytes:
    if not payload or 0 in payload:
        raise BuildError(f"empty/NUL native payload: {payload.hex().upper()}")
    if len(payload) > capacity:
        raise BuildError(f"native payload does not fit: {len(payload)} > {capacity}")
    room = capacity - len(payload)
    # F0A9 is the measured zero-width native filler.  One visible 01 is used
    # only when the fixed extent is odd; it counts as a physical cell.
    return payload + EMPTY_FILLER * (room // 2) + b"\x01" * (room % 2)


def main() -> int:
    parent = bytes(load_rom(MAIN))
    save = SAVE.read_bytes()
    original = bytes(load_rom(find_rom(ROOT)))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"latest main identity drifted: {sha(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drifted: {len(save)}")
    tbl = Tbl.load(PATCH / "hangul_patch_pad3.tbl")
    dictionary = make_dictionary_ext3(
        parent,
        load_ext_meta(PATCH / "exp_dictionary_meta.json"),
        load_ext_meta(PATCH / "ext3_dictionary_meta.json"),
    )
    baseline_manifest = build_manifest(original, parent, target_path=MAIN)
    by_address = {int(row["address_int"]): row for row in baseline_manifest["contracts"]}
    replacements = native_payloads(tbl)
    candidate = bytearray(parent)
    base = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []

    for logical, native in sorted(replacements.items()):
        contract = by_address.get(logical)
        if contract is None:
            raise BuildError(f"target is absent from runtime contract: {logical:06X}")
        if contract["status"] != "active":
            raise BuildError(f"target contract is not active: {logical:06X}")
        prefix_hex = contract.get("metadata_hex") or contract.get("control_prefix_hex") or ""
        prefix = bytes.fromhex(str(prefix_hex)) if prefix_hex else b""
        before = bytes.fromhex(str(contract["baseline_payload_hex"]))
        capacity = len(before) - len(prefix)
        replacement_body = padded(native, capacity)
        after = prefix + replacement_body
        if len(after) != len(before):
            raise BuildError(f"record extent changed at {logical:06X}")
        start = base + logical
        if bytes(candidate[start:start + len(before)]) != before or candidate[start + len(before)] != 0:
            raise BuildError(f"main target/terminator drift at {logical:06X}")
        before_boundary = boundary_signature(parent, logical + len(before))
        candidate[start:start + len(before)] = after
        after_boundary = boundary_signature(candidate, logical + len(before))
        if before_boundary != after_boundary:
            raise BuildError(f"separator/control boundary changed at {logical:06X}")
        target_extents.append((start, start + len(before)))
        portals = scan_portals(replacement_body)
        compact = [row for row in portals if row.get("kind") == "compact3"]
        ext3 = [row for row in portals if row.get("kind") in {"ext3", "truncated_ext3"}]
        if compact or (ext3 and not bool((contract.get("decoder") or {}).get("ext3"))):
            raise BuildError(f"unproven special portal remains at {logical:06X}: {portals}")
        rendered = dictionary.expand(replacement_body, tbl)
        physical = physical_widths(rendered)
        semantic = semantic_widths(rendered)
        if has_japanese(rendered) or any(value > 20 for value in physical + semantic):
            raise BuildError(f"visible/width failure at {logical:06X}: {rendered!r} {physical}")
        if any("가" <= ch <= "힣" for ch in rendered) and not payload_has_hangul_marker(replacement_body):
            # Native dictionary tokens can carry the marker in their leaf.  A
            # direct payload without a marker is allowed only when it contains
            # no direct Hangul bytes; the manifest audit checks the full closure.
            direct_hangul = any(byte == 0xEC for byte in replacement_body)
            if direct_hangul:
                raise BuildError(f"direct Hangul marker missing at {logical:06X}")
        applied.append({
            "abs": f"{logical:06X}",
            "bundle_id": contract["bundle_id"],
            "route": contract["route"],
            "line_role": contract["line_role"],
            "before_hex": before.hex().upper(),
            "after_hex": after.hex().upper(),
            "rendered": rendered.rstrip("\u3000 \t"),
            "physical_cells": physical,
            "semantic_cells": semantic,
            "terminator": contract["baseline_terminator"],
            "boundary": before_boundary,
        })

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    runs = diff_runs(parent, result)
    allowed = target_extents + [(len(result) - 2, len(result))]
    unexpected = [
        (left, right)
        for left, right in runs
        if not any(allow_left <= left and right <= allow_right for allow_left, allow_right in allowed)
    ]
    if unexpected:
        raise BuildError(f"unexpected diff run: {unexpected[0]}")

    candidate_manifest = build_manifest(original, result, target_path=OUT_ROM)
    safety = audit_manifest(result, candidate_manifest, target_path=OUT_ROM)
    if not safety["ok"]:
        raise BuildError(
            f"candidate contract gate failed: {safety['counts']['hard_by_reason']}"
        )
    write_manifest(DEFAULT_MANIFEST, candidate_manifest)

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_SAVE, save)
    atomic_json(SAFETY, safety)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_runtime_contract_candidate.py",
        "ok": True,
        "promotion_allowed": False,
        "purpose": "data-first native storage repair for measured battle body-only, ID continuation, and scenario continuation anchors",
        "inputs": {
            "main_tip": ident(MAIN, parent),
            "original": ident(find_rom(ROOT), original),
            "live_saveram": ident(SAVE, save),
        },
        "outputs": {
            "candidate_rom": ident(OUT_ROM, result),
            "candidate_saveram": ident(OUT_SAVE, save),
            "candidate_safety": ident(SAFETY),
            "candidate_contract": ident(DEFAULT_MANIFEST),
        },
        "counts": {
            "records_changed": len(applied),
            "battle_body_only_native": sum(row["route"] == "battle_body_only" for row in applied),
            "id_continuation_native": sum(row["route"] == "id_continuation" for row in applied),
            "scenario_continuation_native": sum(row["route"] == "scenario_continuation" for row in applied),
            "diff_runs": len(runs),
            "unexpected_diff_runs": len(unexpected),
            "candidate_contract_hard_failures": safety["counts"]["hard_failures"],
        },
        "gates": {
            "candidate_rebuilt_from_latest_main": True,
            "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN_SHA,
            "saveram_byte_exact_copy": sha(save) == sha(OUT_SAVE.read_bytes()),
            "record_extent_terminator_separator_control_exact": True,
            "unproven_special_route_e518_zero_in_targets": True,
            "compact3_zero_in_targets": True,
            "target_japanese_or_control_glyph_zero": True,
            "target_physical_and_semantic_width_le_20": True,
            "quarantine_changes_zero": True,
            "unexpected_diff_runs_zero": not unexpected,
            "contract_gate_zero": safety["ok"],
        },
        "checksum": f"{checksum:04X}",
        "applied": applied,
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["outputs"]["candidate_rom"],
        "candidate_save": report["outputs"]["candidate_saveram"],
        "counts": report["counts"],
        "checksum": report["checksum"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
