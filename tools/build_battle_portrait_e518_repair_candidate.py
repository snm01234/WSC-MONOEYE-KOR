#!/usr/bin/env python3
"""Restore provably lost battle speaker/portrait prefixes on the current follow-up candidate.

This is a narrow structural repair for banks 5D/5E. Historical whole-record
voice rewrites replaced some records with a dictionary token at byte 0, consuming
the original speaker/portrait metadata. Runtime then treats the token lead as the
speaker id and renders the remaining bytes as text; the user-observed 5D7084 case
becomes `こやナ` because E5 is consumed and bytes 18 3A 43 are rendered literally.

Only rows with byte-exact authoritative structure in the pre-bulk safe snapshot,
a missing live prefix, a complete live dictionary token (E5 18 ext3 or ordinary
F0-FF two-byte token), sufficient body capacity, token-only padding, and an
unchanged 00 terminator are repaired. The token itself is reused unchanged, so
Korean wording is preserved. Ambiguous/short rows are not touched.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from measure_aux_prefix_rule import code_units
from monoeye_rom import Tbl, stock_base, update_ws_checksum

PATCH = ROOT / "out/patch"
PARENT = PATCH / "dialogue_runtime_followup_candidate.wsc"
PARENT_SAVE = ROOT / "sram/dialogue_runtime_followup_candidate.sav"
SAFE = PATCH / "backup/20260807_123035_pre_residual_voice_ko/runtime_text_id_scenario_voice_proven_candidate.wsc"
VOICE = ROOT / "out/script/runtime_text_residual_voice_sheet.csv"
FALSE_A = ROOT / "data/aux_false_prefix_cleanup_ko.json"
FALSE_B = ROOT / "data/battle_dialogue_prefix_cleanup_ko.json"
FALSE_LEAD_SAFE = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "dialogue_runtime_followup_portrait_candidate.wsc"
OUT_SAVE = ROOT / "sram/dialogue_runtime_followup_portrait_candidate.sav"
OUT_REPORT = PATCH / "dialogue_runtime_followup_portrait_report.json"
EXPECTED_PARENT = "a7b6e622a767b2e894ad6e8b683319a8a6d2089052b3551b8561c7510369d03e"
EXPECTED_SAFE = "5919fbf7bb25d692ca0593a63961fe34148a69ba9c0ef7b5a04b153b1c7414c4"
# 358 was the old population before the visible-text lead proof was folded
# into this repair stage. 264 of those rows are proven sentence leads, not
# speaker/portrait metadata, leaving 94 genuine metadata repairs.
EXPECTED_TARGETS = 94
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ANCHOR = 0x5D7084
SCREEN_PREFIXES = {
    0x5D014E: bytes.fromhex("02F191"),
    0x5D0211: bytes.fromhex("02F191"),
    0x5D03ED: bytes.fromhex("02F191"),
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def first_unit(payload: bytes) -> bytes:
    units = code_units(payload)
    if not units:
        return b""
    off, size = units[0]
    if off != 0 or size <= 0:
        return b""
    return payload[:size]


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def false_prefixes() -> set[int]:
    out: set[int] = set()
    for path in (FALSE_A, FALSE_B):
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc.get("targets") or ([doc.get("record")] if doc.get("record") else [])
        for row in rows:
            if row and row.get("abs"):
                out.add(int(str(row["abs"]), 16))
    return out


def proven_visible_text_leads() -> set[int]:
    """Rows whose first original code unit is proven visible sentence text.

    These 264 rows were independently fixed by the false-lead cleanup. They
    must have higher precedence than the older snapshot-prefix heuristic.
    Treating the snapshot first code unit as portrait metadata reintroduces
    Japanese such as ``こんな`` before an otherwise-correct Korean body.
    """
    with FALSE_LEAD_SAFE.open(encoding="utf-8-sig", newline="") as handle:
        return {int(str(row["abs"]), 16) for row in csv.DictReader(handle)}


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def main() -> int:
    parent = PARENT.read_bytes()
    safe = SAFE.read_bytes()
    save = PARENT_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        raise BuildError(f"parent identity drifted: {sha(parent)}")
    if len(safe) != ROM_SIZE or sha(safe) != EXPECTED_SAFE:
        raise BuildError("safe structure baseline drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("candidate SaveRAM missing/wrong size")

    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(parent)
    visible_text = proven_visible_text_leads()
    if len(visible_text) != 264:
        raise BuildError(f"proven visible-text lead population drifted: {len(visible_text)} != 264")
    false = false_prefixes() | visible_text
    with VOICE.open(encoding="utf-8-sig", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("bank") in {"5D", "5E"}]

    candidate = bytearray(parent)
    targets: list[dict[str, Any]] = []
    skipped = {"text_initial": 0, "safe_unproven": 0, "already_exact": 0, "short": 0, "non_token": 0}
    allowed: list[tuple[int, int]] = []

    for src in rows:
        logical = int(src["record_start"], 16)
        original = bytes.fromhex(src["original_payload_hex"])
        plen = len(original)
        at = sb + logical
        live = parent[at:at + plen]
        safe_payload = safe[at:at + plen]
        if parent[at + plen] != 0:
            raise BuildError(f"parent terminator drift at {logical:06X}")
        if logical in false:
            skipped["text_initial"] += 1
            continue

        metadata = first_unit(original)
        full_prefix = SCREEN_PREFIXES.get(logical, metadata)
        body_capacity = plen - len(full_prefix)
        structural_safe = safe_payload.startswith(full_prefix) and safe[at + plen] == 0
        structural_live = live.startswith(full_prefix)
        if not structural_safe:
            skipped["safe_unproven"] += 1
            continue
        if structural_live:
            skipped["already_exact"] += 1
            continue
        if body_capacity < 4:
            skipped["short"] += 1
            continue

        token_offset: int | None = None
        token_len = 0
        token_kind = ""
        probe_offsets = [0]
        if metadata and live.startswith(metadata):
            probe_offsets.append(len(metadata))
        for probe in probe_offsets:
            rest = live[probe:]
            if rest[:2] == b"\xE5\x18" and len(rest) >= 4 and all(b == 0x01 for b in rest[4:]):
                token_offset, token_len, token_kind = probe, 4, "ext3"
                break
            if len(rest) >= 2 and 0xF0 <= rest[0] <= 0xFF and all(b == 0x01 for b in rest[2:]):
                token_offset, token_len, token_kind = probe, 2, "stock_or_ext2"
                break
        if token_offset is None or token_len <= 0 or token_offset + token_len > plen or body_capacity < token_len:
            skipped["non_token"] += 1
            continue

        token = live[token_offset:token_offset + token_len]
        old_render = clean(dictionary.expand(live[token_offset:], tbl))
        rebuilt_body = token + b"\x01" * (body_capacity - token_len)
        new_render = clean(dictionary.expand(rebuilt_body, tbl))
        if not old_render or new_render != old_render:
            raise BuildError(f"render drift at {logical:06X}: {old_render!r} -> {new_render!r}")
        rebuilt = full_prefix + rebuilt_body
        if len(rebuilt) != plen:
            raise BuildError(f"extent drift at {logical:06X}")

        candidate[at:at + plen] = rebuilt
        allowed.append((at, at + plen))
        broken_visible = ""
        if live.startswith(b"\xE5\x18"):
            # Actual battle caller consumes the first byte as speaker metadata.
            broken_visible = clean(dictionary.expand(live[1:], tbl))
        targets.append({
            "abs": f"{logical:06X}",
            "metadata_hex": metadata.hex().upper(),
            "prefix_hex": full_prefix[len(metadata):].hex().upper(),
            "authoritative_structure_hex": full_prefix.hex().upper(),
            "body_capacity": body_capacity,
            "before_payload_hex": live.hex().upper(),
            "after_payload_hex": rebuilt.hex().upper(),
            "token_hex": token.hex().upper(),
            "token_kind": token_kind,
            "render": new_render,
            "broken_visible_if_first_byte_consumed": broken_visible,
        })

    if len(targets) != EXPECTED_TARGETS:
        raise BuildError(f"repairable target population drifted: {len(targets)} != {EXPECTED_TARGETS}")
    anchor = next((r for r in targets if int(r["abs"], 16) == ANCHOR), None)
    if anchor is None:
        raise BuildError("5D7084 screenshot anchor not repaired")
    if anchor["broken_visible_if_first_byte_consumed"] != "こやナ":
        raise BuildError(f"5D7084 screenshot signature drifted: {anchor['broken_visible_if_first_byte_consumed']!r}")
    if anchor["render"] != "아직　무대가　안　갖춰졌다는　건가……":
        raise BuildError(f"5D7084 intended Korean drifted: {anchor['render']!r}")

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    allowed.append((len(result) - 2, len(result)))
    unexpected = [
        i for i, (a, b) in enumerate(zip(parent, result))
        if a != b and not any(lo <= i < hi for lo, hi in allowed)
    ]
    if unexpected:
        raise BuildError(f"unexpected diff at {unexpected[0]:07X}")

    # Re-check every target prefix, terminator and rendered body on final bytes.
    final_dictionary = make_dictionary_ext3(result, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    failures: list[dict[str, Any]] = []
    for row in targets:
        logical = int(row["abs"], 16)
        at = sb + logical
        payload = bytes.fromhex(row["after_payload_hex"])
        prefix = bytes.fromhex(row["authoritative_structure_hex"])
        got = result[at:at + len(payload)]
        body = got[len(prefix):]
        render = clean(final_dictionary.expand(body, tbl))
        if got != payload or not got.startswith(prefix) or result[at + len(payload)] != 0 or render != row["render"]:
            failures.append({"abs": row["abs"], "render": render})
    if failures:
        raise BuildError(f"final target verification failed: {failures[:10]}")

    atomic_write(OUT_ROM, result)
    shutil.copyfile(PARENT_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_portrait_e518_repair_candidate.py",
        "ok": True,
        "purpose": "restore missing speaker/portrait metadata on provably safe E5 18 battle-voice records while preserving current Korean ext3 bodies",
        "parent": {"path": str(PARENT.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(parent), "size": len(parent)},
        "safe_structure": {"path": str(SAFE.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(safe)},
        "candidate": {"path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(result), "size": len(result), "ws_checksum": f"{checksum:04X}"},
        "candidate_save": {"path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(OUT_SAVE.read_bytes()), "size": OUT_SAVE.stat().st_size},
        "counts": {
            "voice_rows": len(rows),
            "targets": len(targets),
            "proven_visible_text_lead_exclusions": len(visible_text),
            **skipped,
            "unexpected_diff_offsets": len(unexpected),
        },
        "screenshot_anchor_5D7084": anchor,
        "checks": {
            "target_count_exact": True,
            "safe_snapshot_prefix_exact": True,
            "live_dictionary_token_reused": True,
            "korean_render_preserved": True,
            "terminators_preserved": True,
            "record_extents_preserved": True,
            "unexpected_diff_offsets_zero": True,
            "screenshot_signature_reproduced": True,
        },
        "targets": targets,
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate": report["candidate"], "counts": report["counts"], "anchor": anchor}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
