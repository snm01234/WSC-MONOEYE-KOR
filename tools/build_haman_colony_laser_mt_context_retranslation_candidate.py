#!/usr/bin/env python3
"""Build Haman/Colony Laser MT-residue context retranslation candidate.

Legacy quality/sheet Korean is forensic only. Every replacement is validated
against Japanese text decoded from the pristine ROM, then installed by
retargeting existing E5 18 five-bank portals without moving record boundaries.
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
from extract_script import split_prefix_body  # noqa: E402
from mixed_residual_classification import is_japanese_character  # noqa: E402
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SPEC = ROOT / "data/haman_colony_laser_mt_context_retranslation_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT_ROM = ROOT / "out/patch/haman_colony_laser_mt_context_retranslation_candidate.wsc"
OUT_SAVE = ROOT / "sram/haman_colony_laser_mt_context_retranslation_candidate.sav"
REPORT = ROOT / "out/patch/haman_colony_laser_mt_context_retranslation_candidate_report.json"

EXPECTED_MAIN_SHA256 = "cef2d40d7a0568e3add4025d8ebc6f5e6340f0a2b545a5f88decc6d28e3375f5"
EXPECTED_ORIGINAL_SHA256 = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768
EXT_META = {
    "stock_count": 3831,
    "slot_count": 265,
    "ext_ptr_off": "0000",
    "ext_seg": "10",
    "ext_in_expansion": True,
}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}
HANGUL_MARKER = 0xEC8D
MAX_CELLS = 20


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temp, path)


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record at {logical:06X}")
    return bytes(got[0]), int(got[1])


def strip_display(text: str) -> str:
    return text.rstrip("　 \t")


def has_japanese(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in text)


def encode_phrase(text: str, tbl: Tbl) -> bytes:
    encoded = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=HANGUL_MARKER,
        hangul_marker_mode="run",
    )
    if encoded is None or not encoded or b"\x00" in encoded:
        raise BuildError(f"cannot encode target phrase: {text!r}")
    return bytes(encoded)


def portal_body(token: bytes, body_len: int) -> bytes:
    if len(token) != 4 or token[:2] != b"\xE5\x18":
        raise BuildError(f"invalid portal token: {token.hex().upper()}")
    if body_len < 4:
        raise BuildError(f"body too short for portal: {body_len}")
    return token + (b"\x01" * (body_len - 4))


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError(f"main TIP identity drifted: {sha256(parent)}")
    if len(original) != ORIGINAL_SIZE or sha256(original) != EXPECTED_ORIGINAL_SHA256:
        raise BuildError("pristine ROM identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")
    save_sha = sha256(save)

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    provenance = spec.get("provenance") or {}
    if not (
        provenance.get("translation_source") == "llm_from_japanese_original"
        and provenance.get("review_status") == "approved_for_main_candidate"
        and provenance.get("legacy_machine_translation_used_as_translation_source") is False
    ):
        raise BuildError("translation provenance policy failed")

    entries = list(spec.get("entries") or [])
    if not entries:
        raise BuildError("empty translation spec")
    addresses = [str(row.get("abs") or "").upper() for row in entries]
    if len(addresses) != len(set(addresses)):
        raise BuildError("duplicate target address")

    tbl = Tbl.load(TBL_PATH)
    d_parent = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    d_original = Dictionary(original, stock_count=3831)
    sb = stock_base(parent)
    prepared: list[dict[str, Any]] = []

    for row in sorted(entries, key=lambda item: int(str(item["abs"]), 16)):
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        mode = str(row.get("mode") or "").strip()
        ko = str(row["ko"]).replace(" ", "　")
        if not ko or len(ko) > MAX_CELLS or has_japanese(ko):
            raise BuildError(f"invalid target text at {address}: {ko!r}")
        if mode not in {"native_restore", "portal_retarget", "portal_install"}:
            raise BuildError(f"unsupported mode at {address}: {mode}")

        original_payload, _ = payload_at(original, logical)
        original_prefix, original_body, original_kind = split_prefix_body(original_payload)
        jp = strip_display(d_original.expand(original_body, tbl))
        if jp != str(row["jp"]):
            raise BuildError(f"Japanese source mismatch at {address}: {jp!r} != {row['jp']!r}")
        if original_kind != "dialogue":
            raise BuildError(f"source record is not dialogue at {address}: {original_kind}")

        payload, term = payload_at(parent, logical)
        prefix, body, kind = split_prefix_body(payload)
        current = strip_display(d_parent.expand(body, tbl))
        if kind != "dialogue":
            raise BuildError(f"current record is not dialogue at {address}: {kind}")
        if prefix != original_prefix:
            raise BuildError(f"dialogue prefix drift at {address}")
        if len(payload) != len(original_payload):
            raise BuildError(f"record extent drift at {address}")
        if term != sb + logical + len(payload) or parent[term] != 0:
            raise BuildError(f"terminator drift at {address}")
        if current == ko:
            raise BuildError(f"target is already a no-op at {address}")

        item: dict[str, Any] = {
            "abs": address,
            "logical": logical,
            "jp": jp,
            "before": current,
            "ko": ko,
            "mode": mode,
            "payload": payload,
            "prefix": prefix,
            "body": body,
            "terminator": term,
            "reason": str(row.get("reason") or "context retranslation"),
        }

        if mode == "native_restore":
            if len(original_body) != len(body):
                raise BuildError(f"native restore body length mismatch at {address}")
            restored = strip_display(d_parent.expand(original_body, tbl))
            if restored != ko:
                raise BuildError(
                    f"native restore render mismatch at {address}: {restored!r} != {ko!r}"
                )
            item["new_body"] = original_body
            item["encoded"] = b""
        else:
            encoded = encode_phrase(ko, tbl)
            item["encoded"] = encoded
            if mode == "portal_retarget":
                positions = [i for i in range(max(0, len(body) - 3)) if body[i : i + 2] == b"\xE5\x18"]
                if positions != [0]:
                    raise BuildError(f"portal_retarget needs one leading E5 18 at {address}")
                if any(byte != 0x01 for byte in body[4:]):
                    raise BuildError(f"non-padding after portal at {address}")
            elif mode == "portal_install":
                if len(body) < 4:
                    raise BuildError(f"portal_install body too short at {address}")

        prepared.append(item)

    portal_rows = [row for row in prepared if row["mode"] != "native_restore"]
    assignments: dict[str, dict[str, Any]] = {}
    states: dict[int, dict[str, Any]] = {}
    if portal_rows:
        assignments, states = allocate_ext3(parent, portal_rows)

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    allocation_rows: list[dict[str, Any]] = []

    for page, state in states.items():
        start = int(state["start"])
        candidate[start : start + len(state["bank"])] = state["bank"]
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
        start = sb + int(row["logical"]) + len(row["prefix"])
        old_body = bytes(row["body"])
        if row["mode"] == "native_restore":
            new_body = bytes(row["new_body"])
            if bytes(candidate[start : start + len(old_body)]) != old_body:
                raise BuildError(f"body changed before native restore at {row['abs']}")
            candidate[start : start + len(new_body)] = new_body
            allowed.append((start, start + len(new_body)))
            allocation_rows.append({
                "abs": row["abs"],
                "jp": row["jp"],
                "before": row["before"],
                "after": row["ko"],
                "mode": row["mode"],
                "reason": row["reason"],
                "old_body": old_body.hex().upper(),
                "new_body": new_body.hex().upper(),
            })
            continue

        info = assignments[row["ko"]]
        token = bytes(info["token"])
        new_body = portal_body(token, len(old_body))
        if bytes(candidate[start : start + len(old_body)]) != old_body:
            raise BuildError(f"body changed before portal write at {row['abs']}")
        candidate[start : start + len(new_body)] = new_body
        allowed.append((start, start + len(new_body)))
        allocation_rows.append({
            "abs": row["abs"],
            "jp": row["jp"],
            "before": row["before"],
            "after": row["ko"],
            "mode": row["mode"],
            "reason": row["reason"],
            "old_body": old_body.hex().upper(),
            "new_body": new_body.hex().upper(),
            "new_token": token.hex().upper(),
            "reused_existing_phrase": bool(info["reused"]),
            "page": int(info["page"]),
            "physical_bank": f"{int(info['segment']):02X}",
            "local": f"{int(info['local']):04X}",
        })

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)

    def allowed_offset(offset: int) -> bool:
        return any(left <= offset < right for left, right in allowed)

    changed = [i for i, (before, after) in enumerate(zip(parent, result)) if before != after]
    outside = [i for i in changed if not allowed_offset(i)]
    if outside:
        raise BuildError(f"unexpected diff outside allowlist: {[f'{x:08X}' for x in outside[:20]]}")

    d_result = make_dictionary_ext3(result, EXT_META, EXT3_META)
    render_checks: list[dict[str, Any]] = []
    for row in prepared:
        logical = int(row["logical"])
        payload, term = payload_at(result, logical)
        prefix, body, kind = split_prefix_body(payload)
        rendered = strip_display(d_result.expand(body, tbl))
        reasons: list[str] = []
        if rendered != row["ko"]:
            reasons.append("render_mismatch")
        if has_japanese(rendered):
            reasons.append("japanese_residual")
        if len(rendered) > MAX_CELLS:
            reasons.append("over_max_cells")
        if prefix != row["prefix"] or kind != "dialogue":
            reasons.append("prefix_or_kind_changed")
        if len(payload) != len(row["payload"]):
            reasons.append("record_extent_changed")
        if term != row["terminator"] or result[term] != 0:
            reasons.append("terminator_changed")
        render_checks.append({
            "abs": row["abs"],
            "mode": row["mode"],
            "rendered": rendered,
            "cells": len(rendered),
            "ok": not reasons,
            "reasons": reasons,
        })
    if not all(row["ok"] for row in render_checks):
        failures = [row for row in render_checks if not row["ok"]]
        raise BuildError(f"one or more target render checks failed: {failures[:10]}")

    collateral: list[dict[str, Any]] = []
    for row in prepared:
        term = int(row["terminator"])
        for offset in range(term, min(term + 8, len(result))):
            if result[offset] != parent[offset] and not allowed_offset(offset):
                collateral.append({"abs": row["abs"], "offset": f"{offset:08X}"})
    if collateral:
        raise BuildError(f"terminator neighborhood collateral: {collateral[:5]}")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if sha256(OUT_SAVE.read_bytes()) != save_sha:
        raise BuildError("candidate SaveRAM differs from the live main SaveRAM snapshot")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_haman_colony_laser_mt_context_retranslation_candidate.py",
        "status": "ready_for_runtime_validation",
        "source_policy": {
            "legacy_korean_translation_used_for_translation": False,
            "japanese_source_verified_from_pristine_rom": True,
            "quarantined_sources_left_unmodified": True,
        },
        "parent": {"path": str(MAIN.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(parent)},
        "original": {
            "path": str(ORIGINAL.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(original),
        },
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(result),
            "size": len(result),
            "checksum": f"{checksum:04X}",
        },
        "saveram": {
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "sha256": save_sha,
            "size": OUT_SAVE.stat().st_size,
            "byte_exact_to_live_main": True,
        },
        "summary": {
            "targets": len(prepared),
            "native_restore": sum(row["mode"] == "native_restore" for row in prepared),
            "portal_retarget": sum(row["mode"] == "portal_retarget" for row in prepared),
            "portal_install": sum(row["mode"] == "portal_install" for row in prepared),
            "unique_phrases": len({row["ko"] for row in prepared if row["mode"] != "native_restore"}),
            "new_alias_phrases": sum(
                not bool(assignments[row["ko"]]["reused"])
                for row in prepared
                if row["mode"] != "native_restore"
            ),
            "max_target_cells": max(row["cells"] for row in render_checks),
            "changed_bytes": len(changed),
            "unexpected_diff_bytes": len(outside),
        },
        "allocations": allocation_rows,
        "target_checks": render_checks,
        "checks": {
            "parent_identity_ok": True,
            "pristine_japanese_source_ok": True,
            "provenance_policy_ok": True,
            "all_targets_exact": True,
            "all_targets_max_cells_or_less": max(row["cells"] for row in render_checks) <= MAX_CELLS,
            "target_prefix_extent_terminator_preserved": True,
            "unexpected_diff_zero": len(outside) == 0,
            "saveram_byte_exact": True,
        },
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "candidate_sha256": report["candidate"]["sha256"],
                "checksum": report["candidate"]["checksum"],
                "targets": len(prepared),
                "modes": {
                    "native_restore": report["summary"]["native_restore"],
                    "portal_retarget": report["summary"]["portal_retarget"],
                    "portal_install": report["summary"]["portal_install"],
                },
                "max_cells": report["summary"]["max_target_cells"],
                "changed_bytes": len(changed),
                "report": str(REPORT.relative_to(ROOT)).replace("\\", "/"),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
