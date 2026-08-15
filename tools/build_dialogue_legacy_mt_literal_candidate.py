#!/usr/bin/env python3
"""Build batch001 literal-retranslation test ROM from the current main TIP.

Only existing E5 18 portal indices are retargeted. Record prefixes, body extents,
padding, NUL terminators, runtime code, existing dictionary phrases, and all
non-target records stay unchanged. Fresh/reused five-bank alias ext3 storage is
allocated with the same allocator used by the accepted dialogue pipelines.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3  # noqa: E402
from build_remaining_dialogue_candidate import diff_runs  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import BANK_SIZE, Dictionary, SEG_DICT, Tbl, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
WORKLIST = ROOT / "out/script/dialogue_legacy_source_retranslation_worklist.json"
CONTEXT_WORKLIST = ROOT / "out/script/dialogue_context_neighborhood_worklist.json"
BATCH_GLOB = "data/dialogue_legacy_mt_literal_batch*.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = {
    "stock_count": 3831,
    "slot_count": 265,
    "ext_ptr_off": "0000",
    "ext_seg": "10",
    "ext_in_expansion": True,
}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}
OUT_ROM = ROOT / "out/patch/dialogue_legacy_mt_literal_candidate.wsc"
OUT_SAVE = ROOT / "sram/dialogue_legacy_mt_literal_candidate.sav"
OUT_REPORT = ROOT / "out/patch/dialogue_legacy_mt_literal_candidate_report.json"
EXPECTED_MAIN = "93de328215eec7d4162279e5956e6cf110741b0ad3a311e9f499019ce6c5f81e"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
HANGUL_MARKER = 0xEC8D
# Optional: LEGACY_MT_ONLY_BATCH=018 loads only matching batch file(s) for incremental builds
# after earlier batches are already promoted into the main TIP.


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def encode_phrase(text: str, tbl: Tbl) -> bytes:
    """Encode with the tip-required invisible Hangul run marker EC8D (not E3DB/映)."""
    encoded = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=HANGUL_MARKER,
        hangul_marker_mode="run",
    )
    if encoded is None or not encoded or b"\x00" in encoded:
        raise BuildError(f"cannot encode phrase: {text!r}")
    return bytes(encoded)


def merged(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(a, b) for a, b in out]


def in_intervals(offset: int, intervals: list[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in intervals)


def main() -> int:
    parent = MAIN.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN:
        raise BuildError("current main TIP identity drifted")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live main SaveRAM missing or wrong size")

    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    if (work.get("summary") or {}).get("main_tip_sha256") != EXPECTED_MAIN:
        raise BuildError("legacy-source worklist is not bound to current main")
    by_abs = {str(row["abs"]).upper(): row for row in work.get("records") or []}
    context_work = json.loads(CONTEXT_WORKLIST.read_text(encoding="utf-8"))
    context_by_abs = {str(row["abs"]).upper(): row for row in context_work.get("records") or []}

    only = (os.environ.get("LEGACY_MT_ONLY_BATCH") or "").strip()
    batch_paths = sorted(ROOT.glob(BATCH_GLOB))
    if only:
        batch_paths = [path for path in batch_paths if f"batch{only}" in path.name]
    if not batch_paths:
        raise BuildError("no literal-retranslation batches found")
    raw_targets: dict[str, str] = {}
    direct_source_jp: dict[str, str] = {}
    batch_rows: list[dict[str, Any]] = []
    for path in batch_paths:
        batch = json.loads(path.read_text(encoding="utf-8"))
        if batch.get("translation_source") != "llm" or batch.get("review_status") != "approved_for_test_candidate":
            raise BuildError(f"batch provenance/review status invalid: {path.name}")
        local_targets = batch.get("targets") or {}
        local_sources = batch.get("source_jp") or {}
        for raw_address, raw_text in local_targets.items():
            address = str(raw_address).upper()
            text = str(raw_text)
            if address in raw_targets and raw_targets[address] != text:
                raise BuildError(f"conflicting target across batches at {address}")
            raw_targets[address] = text
            if address in local_sources:
                source = str(local_sources[address])
                if address in direct_source_jp and direct_source_jp[address] != source:
                    raise BuildError(f"conflicting Japanese source across batches at {address}")
                direct_source_jp[address] = source
        batch_rows.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "targets": len(local_targets)})
    expected_targets = len(raw_targets)

    tbl = Tbl.load(TBL_PATH)
    ext_meta = EXT_META
    ext3_meta = EXT3_META
    if ext3_meta.get("compact3") not in (None, False):
        raise BuildError("accepted runtime unexpectedly enables compact3")
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)

    prepared: list[dict[str, Any]] = []
    for address in sorted(raw_targets, key=lambda x: int(x, 16)):
        # Runtime dialogue uses U+3000 as the visible inter-word cell. ASCII
        # spaces are not a canonical display-space representation in this TBL.
        desired = str(raw_targets[address]).replace(" ", "　")
        spec = by_abs.get(address.upper())
        if spec is None:
            context_spec = context_by_abs.get(address.upper())
            explicit_source = direct_source_jp.get(address.upper())
            source_jp = explicit_source
            if source_jp is None and context_spec is not None:
                source_jp = str(context_spec.get("jp") or "")
            if not source_jp:
                raise BuildError(f"target missing Japanese source: {address}")
            if context_spec is not None and str(context_spec.get("jp") or "") != source_jp:
                raise BuildError(f"context Japanese source mismatch: {address}")
            if context_spec is None and explicit_source is None:
                raise BuildError(f"target outside context ledger without explicit Japanese source: {address}")
            logical = int(address, 16)
            if address.upper() in {"630695", "63CFEA"}:
                raise BuildError(f"known pathological record requires dedicated structural repair: {address}")
            if not (0x600000 <= logical <= 0x63FFFF):
                raise BuildError(f"context target outside safe scenario banks: {address}")
            direct_got = read_encoded_z_safe(parent, sb + logical, max_len=256)
            if direct_got is None:
                raise BuildError(f"context record unreadable: {address}")
            direct_payload, direct_term = bytes(direct_got[0]), int(direct_got[1])
            direct_prefix, direct_body, direct_kind = split_prefix_body(direct_payload)
            if direct_kind != "dialogue":
                raise BuildError(f"context target is not dialogue: {address} {direct_kind}")
            direct_positions = [pos for pos in range(max(0, len(direct_body) - 3)) if direct_body[pos:pos + 2] == b"\xE5\x18"]
            if len(direct_positions) == 1:
                direct_route = "existing_ext3_portal"
                direct_portal_offset = direct_positions[0]
            elif not direct_positions and len(direct_body) >= 4:
                direct_route = "retarget_body_to_ext3"
                direct_portal_offset = 0
            else:
                raise BuildError(f"direct-scene target has no safe ext3 route: {address}")
            spec = {
                "abs": address.upper(),
                "jp": source_jp,
                "current_render": dictionary.expand(direct_body, tbl).rstrip(" \u3000\t"),
                "payload_hex": direct_payload.hex().upper(),
                "prefix_hex": direct_prefix.hex().upper(),
                "terminator": f"{direct_term - sb:06X}",
                "route": direct_route,
                "portal_offset": direct_portal_offset,
                "direct_scene_source": True,
            }
        route = str(spec.get("route") or "")
        if route not in {"existing_ext3_portal", "retarget_body_to_ext3"}:
            raise BuildError(f"unsupported target route: {address} {route}")
        if len(desired.replace("<E62F>", "")) > 20:
            raise BuildError(f"target exceeds 20 cells: {address} {desired!r}")
        encoded = encode_phrase(desired, tbl)
        if not encoded or b"\x00" in encoded:
            raise BuildError(f"target cannot encode: {address}")

        logical = int(address, 16)
        got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        if got is None:
            raise BuildError(f"current record unreadable: {address}")
        payload, term = bytes(got[0]), int(got[1])
        prefix, body, kind = split_prefix_body(payload)
        if payload.hex().upper() != str(spec["payload_hex"]):
            raise BuildError(f"payload drift at {address}")
        if prefix.hex().upper() != str(spec["prefix_hex"]):
            raise BuildError(f"prefix drift at {address}")
        if f"{term - sb:06X}" != str(spec["terminator"]):
            raise BuildError(f"terminator drift at {address}")
        current = dictionary.expand(body, tbl).rstrip(" \u3000\t")
        if current != str(spec["current_render"]):
            raise BuildError(f"render drift at {address}")
        positions = [pos for pos in range(max(0, len(body) - 3)) if body[pos:pos + 2] == b"\xE5\x18"]
        if route == "existing_ext3_portal":
            if positions != [int(spec["portal_offset"])]:
                raise BuildError(f"portal position drift at {address}: {positions}")
            pos = positions[0]
            old_token = body[pos:pos + 4]
            old_index: int | None = 0x1000 + (old_token[2] << 8) + old_token[3]
        else:
            if positions:
                raise BuildError(f"retarget-body route unexpectedly already has E5 18 at {address}: {positions}")
            if len(body) < 4:
                raise BuildError(f"retarget-body route is shorter than 4 bytes at {address}")
            pos = 0
            old_token = body[:4]
            old_index = None
        prepared.append({
            "abs": address.upper(),
            "logical": logical,
            "jp": str(spec["jp"]),
            "before": current,
            "ko": desired,
            "encoded": encoded,
            "payload": payload,
            "prefix": prefix,
            "body": body,
            "terminator": term,
            "portal_offset_body": pos,
            "portal_offset_payload": len(prefix) + pos,
            "old_token": old_token,
            "old_index": old_index,
            "route": route,
        })

    assignments, states = allocate_ext3(parent, prepared)
    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    allocation_rows: list[dict[str, Any]] = []

    # Copy allocator-produced alias banks and whitelist only newly allocated
    # pointer entries / phrase tails inside them.
    for page, state in states.items():
        start = int(state["start"])
        candidate[start:start + 0x10000] = state["bank"]
        new_locals = {
            int(info["local"])
            for info in assignments.values()
            if int(info["page"]) == int(page) and not bool(info["reused"])
        }
        for local in sorted(new_locals):
            allowed.append((start + local * 2, start + local * 2 + 2))
        if int(state["cursor"]) > int(state["cursor_before"]):
            allowed.append((start + int(state["cursor_before"]), start + int(state["cursor"])))

    for row in prepared:
        info = assignments[row["ko"]]
        token = bytes(info["token"])
        if len(token) != 4 or token[:2] != b"\xE5\x18":
            raise BuildError(f"allocator returned invalid token for {row['abs']}")
        start = sb + row["logical"] + row["portal_offset_payload"]
        if row["route"] == "existing_ext3_portal":
            if bytes(candidate[start:start + 4]) != row["old_token"]:
                raise BuildError(f"portal changed before retarget: {row['abs']}")
            candidate[start:start + 4] = token
            # E5 18 stays exact; only ext3 leaf bytes are allowed to change.
            allowed.append((start + 2, start + 4))
        else:
            body_len = len(row["body"])
            if bytes(candidate[start:start + body_len]) != row["body"]:
                raise BuildError(f"body changed before fixed-extent retarget: {row['abs']}")
            candidate[start:start + body_len] = token + (b"\x01" * (body_len - 4))
            allowed.append((start, start + body_len))
        allocation_rows.append({
            "abs": row["abs"],
            "jp": row["jp"],
            "before": row["before"],
            "after": row["ko"],
            "old_index": None if row["old_index"] is None else f"{row['old_index']:05X}",
            "route": row["route"],
            "new_token": token.hex().upper(),
            "reused_existing_phrase": bool(info["reused"]),
            "page": int(info["page"]),
            "local": f"{int(info['local']):03X}",
        })

    # Three short-body stock phrases cannot be retargeted to an E5 18 portal
    # because their record bodies are only 2-3 bytes.  Each slot is a proven
    # script-only (or intentionally shared text-root) consumer and the corrected
    # Hangul payload is exactly the same encoded byte length, so replace the
    # phrase payload in place while keeping pointer, NUL, callers and extents
    # byte-exact.  This avoids compact3 and new stock-slot allocation entirely.
    # Incremental only-batch builds skip these historical stock fixes — they are
    # already promoted into the current main TIP.
    stock_scene_fixes: list[dict[str, Any]] = []
    if not only:
        stock_dict = Dictionary(parent)
        dict_bank_file = sb + SEG_DICT * BANK_SIZE
        stock_replacements = (
            (0xEBD, "브라이트　함장", ((0x613317, "브라이트　함장！"), (0x513C7A, "브라이트　함장－"))),
            (0x67F, "브라이트　함장은", ((0x6116F3, "브라이트　함장은"),)),
            (0x7EF, "밀리샤？", ((0x6192C6, "밀리샤？"),)),
        )
        for stock_idx, stock_phrase, consumers in stock_replacements:
            stock_encoded = encode_phrase(stock_phrase, tbl)
            stock_phrase_ptr = int(stock_dict.ptrs[stock_idx])
            stock_old = bytes(stock_dict.raw_entry(stock_idx))
            if len(stock_old) != len(stock_encoded):
                raise BuildError(
                    f"stock in-place replacement length drift idx={stock_idx:03X}: "
                    f"old={len(stock_old)} new={len(stock_encoded)}"
                )
            stock_payload_file = dict_bank_file + stock_phrase_ptr
            if bytes(candidate[stock_payload_file:stock_payload_file + len(stock_old)]) != stock_old:
                raise BuildError(f"stock phrase changed before in-place replacement idx={stock_idx:03X}")
            candidate[stock_payload_file:stock_payload_file + len(stock_encoded)] = stock_encoded
            allowed.append((stock_payload_file, stock_payload_file + len(stock_encoded)))
            stock_fix_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
            for logical, expected in consumers:
                fix_got = read_encoded_z_safe(candidate, sb + logical, max_len=96)
                if fix_got is None:
                    raise BuildError(f"stock fix unreadable: {logical:06X}")
                _fix_prefix, fix_body, _fix_kind = split_prefix_body(bytes(fix_got[0]))
                fix_text = stock_fix_dictionary.expand(fix_body, tbl).rstrip(" \u3000\t")
                if fix_text != expected:
                    raise BuildError(f"stock fix render mismatch: {logical:06X} {fix_text!r}")
                stock_scene_fixes.append({"abs": f"{logical:06X}", "expected": expected, "rendered": fix_text, "ok": True, "stock_index": f"{stock_idx:03X}"})

        # FEBD also appears as the leaf bytes of two E5 18 ext3 portals.  Those are
        # not stock consumers and must remain semantically unchanged.
        stock_fix_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        for logical, expected in ((0x5D794C, "미안하지만……끝장을　낸다！！"), (0x5D7B2E, "미안하지만……끝장을　낸다！！")):
            fix_got = read_encoded_z_safe(candidate, sb + logical, max_len=96)
            if fix_got is None:
                raise BuildError(f"stock portal guard unreadable: {logical:06X}")
            _fix_prefix, fix_body, _fix_kind = split_prefix_body(bytes(fix_got[0]))
            fix_text = stock_fix_dictionary.expand(fix_body, tbl).rstrip(" \u3000\t")
            if fix_text != expected:
                raise BuildError(f"stock portal guard changed: {logical:06X} {fix_text!r}")
            stock_scene_fixes.append({"abs": f"{logical:06X}", "expected": expected, "rendered": fix_text, "ok": True, "portal_guard": True})

    checksum = update_ws_checksum(candidate)
    allowed.append((len(parent) - 2, len(parent)))
    result = bytes(candidate)
    allowed_merged = merged(allowed)

    unexpected = [
        off for off, (a, b) in enumerate(zip(parent, result))
        if a != b and not in_intervals(off, allowed_merged)
    ]
    if unexpected:
        raise BuildError(f"unexpected diff outside allowlist: {unexpected[:20]}")

    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)
    checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in prepared:
        got = read_encoded_z_safe(result, sb + row["logical"], max_len=256)
        if got is None:
            failures.append({"abs": row["abs"], "reason": "unreadable_after"})
            continue
        payload, term = bytes(got[0]), int(got[1])
        prefix, body, kind = split_prefix_body(payload)
        text = result_dictionary.expand(body, tbl).rstrip(" \u3000\t")
        if row["route"] == "existing_ext3_portal":
            structure_ok = (
                len(payload) == len(row["payload"])
                and prefix == row["prefix"]
                and payload[:row["portal_offset_payload"]] == row["payload"][:row["portal_offset_payload"]]
                and payload[row["portal_offset_payload"]:row["portal_offset_payload"] + 2] == b"\xE5\x18"
                and payload[row["portal_offset_payload"] + 4:] == row["payload"][row["portal_offset_payload"] + 4:]
                and term == row["terminator"]
            )
        else:
            structure_ok = (
                len(payload) == len(row["payload"])
                and prefix == row["prefix"]
                and len(body) == len(row["body"])
                and body[:2] == b"\xE5\x18"
                and body[4:] == b"\x01" * (len(body) - 4)
                and term == row["terminator"]
            )
        ok = text == row["ko"] and structure_ok and len(text.replace("<E62F>", "")) <= 20
        check = {
            "abs": row["abs"],
            "rendered": text,
            "expected": row["ko"],
            "cells": len(text.replace("<E62F>", "")),
            "route": row["route"],
            "structure_ok": structure_ok,
            "terminator": f"{term - sb:06X}",
            "ok": ok,
        }
        checks.append(check)
        if not ok:
            failures.append(check)
    if failures:
        raise BuildError("post-build target verification failed: " + json.dumps(failures[:10], ensure_ascii=False))

    atomic_bytes(OUT_ROM, result)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_dialogue_legacy_mt_literal_candidate.py",
        "status": "candidate_built",
        "parent": {"path": str(MAIN.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(parent)},
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
            "size": len(result),
            "sha256": sha256(result),
            "checksum": f"{checksum:04X}",
        },
        "save_pair": {
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "size": OUT_SAVE.stat().st_size,
            "sha256": sha256(OUT_SAVE.read_bytes()),
        },
        "batches": batch_rows,
        "targets": len(prepared),
        "max_target_cells": max(check["cells"] for check in checks),
        "compact3_used": False,
        "record_prefix_changes": 0,
        "record_extent_changes": 0,
        "terminator_changes": 0,
        "unexpected_diff_bytes": 0,
        "allocator_reused_phrases": sum(row["reused_existing_phrase"] for row in allocation_rows),
        "allocator_new_phrases": sum(not row["reused_existing_phrase"] for row in allocation_rows),
        "stock_scene_fixes": stock_scene_fixes,
        "allocation_rows": allocation_rows,
        "checks": checks,
        "diff_runs": diff_runs(parent, result),
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({k: report[k] for k in ("status", "candidate", "save_pair", "targets", "max_target_cells", "compact3_used", "record_prefix_changes", "record_extent_changes", "terminator_changes", "unexpected_diff_bytes", "allocator_reused_phrases", "allocator_new_phrases")}, ensure_ascii=False, indent=2))
    print(OUT_ROM)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
