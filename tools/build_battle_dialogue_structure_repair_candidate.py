#!/usr/bin/env python3
"""Build a conservative battle-dialogue structure repair candidate.

The historical residual-voice bulk pass treated many 5D/5E records as whole
text.  In this format the first code unit is normally speaker/portrait metadata;
reviewed false-prefix exceptions are real text and must *not* be restored as
metadata.  This builder therefore freezes battle structure from the last safe
pre-bulk-voice snapshot, reuses the already translated live E5 18 body token,
and changes no terminator/gap/partner/non-target byte.

Only records satisfying all of the following are auto-repaired:
  * bank 5D/5E voice inventory row;
  * not a runtime-proven text-initial false-prefix exception;
  * authoritative speaker/portrait prefix is byte-exact in the safe snapshot;
  * current structure is missing that prefix;
  * the current translated body starts with a complete 4-byte E5 18 token;
  * the authoritative body capacity is at least four bytes;
  * candidate body re-renders exactly to the current Korean render.

Short/fixed, stock-token, uncertain-boundary, and non-E5 cases are quarantined
into a separate CSV and are never auto-applied.
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

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from measure_aux_prefix_rule import code_units  # noqa: E402
from monoeye_rom import Tbl, load_rom, stock_base, update_ws_checksum  # noqa: E402

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAFE = PATCH / "backup/20260807_123035_pre_residual_voice_ko/runtime_text_id_scenario_voice_proven_candidate.wsc"
VOICE_SHEET = ROOT / "out/script/runtime_text_residual_voice_sheet.csv"
FALSE_PREFIX = ROOT / "data/aux_false_prefix_cleanup_ko.json"
RUNTIME_FALSE_PREFIX = ROOT / "data/battle_dialogue_prefix_cleanup_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "battle_dialogue_structure_repair_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_dialogue_structure_repair_candidate.sav"
SRAM_SAVE = ROOT / "sram/battle_dialogue_structure_repair_candidate.sav"
INVENTORY_CSV = ROOT / "out/script/battle_dialogue_structure_inventory.csv"
SPEAKER_CSV = ROOT / "out/script/battle_dialogue_speaker_portrait_metadata_inventory.csv"
AMBIGUOUS_CSV = ROOT / "out/script/battle_dialogue_structure_ambiguous_short_fixed.csv"
REPORT = PATCH / "battle_dialogue_structure_repair_report.json"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
EXPECTED_TIP = "0656db10b4146b03fd1d3d38dfaaf9fade33ab71bf9cd1f37a5b76fd27f1f606"
EXPECTED_SAFE = "5919fbf7bb25d692ca0593a63961fe34148a69ba9c0ef7b5a04b153b1c7414c4"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
SCREEN_PREFIXES = {
    0x5D014E: bytes.fromhex("02F191"),
    0x5D0211: bytes.fromhex("02F191"),
    0x5D03ED: bytes.fromhex("02F191"),
}
ANCHORS = {0x5E9BDE: "user_garbled_kobami_1", 0x5E9CC4: "user_garbled_kobami_2"}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def ident(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def load_false_prefix_addresses() -> set[int]:
    out: set[int] = set()
    doc = json.loads(FALSE_PREFIX.read_text(encoding="utf-8"))
    for row in doc.get("targets") or []:
        out.add(int(str(row["abs"]), 16))
    runtime = json.loads(RUNTIME_FALSE_PREFIX.read_text(encoding="utf-8"))
    row = runtime.get("record") or {}
    if row.get("abs"):
        out.add(int(str(row["abs"]), 16))
    return out


def first_unit(payload: bytes) -> bytes:
    units = code_units(payload)
    if not units:
        return b""
    off, size = units[0]
    if off != 0 or size <= 0:
        return b""
    return payload[:size]


def clean_text(value: str) -> str:
    return value.rstrip("\u3000 \t")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    current = bytes(load_rom(TIP))
    safe = bytes(load_rom(SAFE))
    save = MAIN_SAVE.read_bytes()
    if len(current) != ROM_SIZE or sha(current) != EXPECTED_TIP:
        raise BuildError("main TIP identity drifted")
    if len(safe) != ROM_SIZE or sha(safe) != EXPECTED_SAFE:
        raise BuildError("safe pre-bulk battle baseline identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")

    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(current, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    false_prefix = load_false_prefix_addresses()
    sb = stock_base(current)

    with VOICE_SHEET.open(encoding="utf-8-sig", newline="") as handle:
        sheet = [row for row in csv.DictReader(handle) if row.get("bank") in {"5D", "5E"}]
    sheet.sort(key=lambda row: int(row["record_start"], 16))

    candidate = bytearray(current)
    inventory: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    target_ranges: list[tuple[int, int]] = []

    # Map immediate sequential neighbours for partner/bundle auditing.
    logicals = [int(row["record_start"], 16) for row in sheet]
    prev_map = {logicals[i]: (logicals[i - 1] if i else None) for i in range(len(logicals))}
    next_map = {logicals[i]: (logicals[i + 1] if i + 1 < len(logicals) else None) for i in range(len(logicals))}

    for source in sheet:
        logical = int(source["record_start"], 16)
        original_payload = bytes.fromhex(source["original_payload_hex"])
        plen = len(original_payload)
        at = sb + logical
        live = current[at : at + plen]
        safe_payload = safe[at : at + plen]
        if len(live) != plen or len(safe_payload) != plen:
            raise BuildError(f"record OOB: {logical:06X}")
        live_term = current[at + plen]
        safe_term = safe[at + plen]

        text_initial = logical in false_prefix
        metadata = b"" if text_initial else first_unit(original_payload)
        full_prefix = SCREEN_PREFIXES.get(logical, metadata)
        if full_prefix and not full_prefix.startswith(metadata):
            raise BuildError(f"screen prefix does not include metadata: {logical:06X}")
        prefix = full_prefix[len(metadata) :]
        body_capacity = plen - len(full_prefix)
        structural_safe = safe_payload.startswith(full_prefix) and safe_term == 0
        structural_live = live.startswith(full_prefix) and live_term == 0

        inv = {
            "record_start": f"{logical:06X}",
            "bank": source["bank"],
            "metadata_hex": metadata.hex().upper(),
            "prefix_hex": prefix.hex().upper(),
            "authoritative_structure_hex": full_prefix.hex().upper(),
            "body_capacity": body_capacity,
            "body_hex_original": original_payload[len(full_prefix):].hex().upper(),
            "terminator_hex": f"{live_term:02X}",
            "current_payload_hex": live.hex().upper(),
            "current_render": source.get("current_body") or "",
            "classification": "text_initial_exception" if text_initial else "battle_voice_structured",
            "safe_structure_exact": "yes" if structural_safe else "no",
            "current_structure_exact": "yes" if structural_live else "no",
            "previous_record": "" if prev_map[logical] is None else f"{prev_map[logical]:06X}",
            "next_record": "" if next_map[logical] is None else f"{next_map[logical]:06X}",
            "screen_evidence": source.get("screen_evidence") or ANCHORS.get(logical, ""),
            "action": "none",
            "reason": "",
        }

        if text_initial:
            inv["reason"] = "runtime-proven first code unit is visible text; never restore as metadata"
            inventory.append(inv)
            continue
        if not structural_safe:
            inv["action"] = "quarantine"
            inv["reason"] = "authoritative safe snapshot does not match expected metadata/prefix"
            ambiguous.append(dict(inv))
            inventory.append(inv)
            continue
        if structural_live:
            inv["reason"] = "metadata/prefix already exact"
            inventory.append(inv)
            continue
        if live_term != 0:
            inv["action"] = "quarantine"
            inv["reason"] = "live terminator drift"
            ambiguous.append(dict(inv))
            inventory.append(inv)
            continue
        if body_capacity < 4:
            inv["action"] = "quarantine"
            inv["reason"] = "short/fixed body capacity < 4"
            ambiguous.append(dict(inv))
            inventory.append(inv)
            continue

        # Locate the existing translated E5 18 token without interpreting any
        # arbitrary structure byte as text.  Standard damaged rows lost all
        # metadata; screen-prefix rows can retain the first metadata unit.
        token_offset: int | None = None
        if live[:2] == b"\xE5\x18":
            token_offset = 0
        elif metadata and live.startswith(metadata) and live[len(metadata):len(metadata)+2] == b"\xE5\x18":
            token_offset = len(metadata)
        if token_offset is None or token_offset + 4 > plen:
            inv["action"] = "quarantine"
            inv["reason"] = "boundary unclear or current body is short/stock/non-E5"
            ambiguous.append(dict(inv))
            inventory.append(inv)
            continue

        token = live[token_offset : token_offset + 4]
        rebuilt_body = token + bytes([0x01]) * (body_capacity - 4)
        rebuilt = full_prefix + rebuilt_body
        if len(rebuilt) != plen:
            raise BuildError(f"rebuilt capacity mismatch: {logical:06X}")
        try:
            render = clean_text(dictionary.expand(rebuilt_body, tbl))
            live_body_render = clean_text(dictionary.expand(live[token_offset:], tbl))
        except Exception as exc:  # noqa: BLE001
            inv["action"] = "quarantine"
            inv["reason"] = f"decode failure: {type(exc).__name__}"
            ambiguous.append(dict(inv))
            inventory.append(inv)
            continue
        expected_render = clean_text(source.get("current_body") or "")
        if not render or render != live_body_render or (expected_render and render != expected_render):
            inv["action"] = "quarantine"
            inv["reason"] = "body render does not bind exactly to live Korean render"
            ambiguous.append(dict(inv))
            inventory.append(inv)
            continue

        candidate[at : at + plen] = rebuilt
        # Terminator intentionally not written. It must already be 00.
        inv["action"] = "repair"
        inv["reason"] = "restore authoritative metadata/prefix; reuse existing translated body token"
        inv["candidate_payload_hex"] = rebuilt.hex().upper()
        inv["candidate_render"] = render
        targets.append({
            **inv,
            "token_hex": token.hex().upper(),
            "before_payload_hex": live.hex().upper(),
            "after_payload_hex": rebuilt.hex().upper(),
            "anchor": ANCHORS.get(logical, ""),
        })
        target_ranges.append((at, at + plen))
        inventory.append(inv)

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    # Static guards.  Non-target bytes may differ only in the two-byte checksum.
    changed = [i for i, (a, b) in enumerate(zip(current, candidate_bytes)) if a != b]
    checksum_range = (len(current) - 2, len(current))
    unexpected = [
        i for i in changed
        if not (checksum_range[0] <= i < checksum_range[1])
        and not any(lo <= i < hi for lo, hi in target_ranges)
    ]
    if unexpected:
        raise BuildError(f"non-target byte changed: {unexpected[0]:08X}")

    # Every target structure comes from SAFE exactly; only the body token/pad is
    # selected from current. Terminator, neighbour records and gaps stay current.
    target_failures: list[dict[str, str]] = []
    for row in targets:
        logical = int(row["record_start"], 16)
        at = sb + logical
        plen = len(bytes.fromhex(row["after_payload_hex"]))
        metadata_prefix = bytes.fromhex(row["metadata_hex"] + row["prefix_hex"])
        got = candidate_bytes[at : at + plen]
        safe_got = safe[at : at + plen]
        body = got[len(metadata_prefix):]
        try:
            render = clean_text(dictionary.expand(body, tbl))
        except Exception:
            render = "<decode-error>"
        ok = (
            got.startswith(metadata_prefix)
            and safe_got.startswith(metadata_prefix)
            and candidate_bytes[at + plen] == current[at + plen] == safe[at + plen] == 0
            and render == clean_text(row["candidate_render"])
        )
        if not ok:
            target_failures.append({"abs": row["record_start"], "render": render})

    # Target partners: immediate inventory neighbours and every byte between the
    # target record terminator and neighbour start are not separately patched.
    partner_failures: list[dict[str, str]] = []
    target_set = {int(row["record_start"], 16) for row in targets}
    for row in targets:
        logical = int(row["record_start"], 16)
        for key in ("previous_record", "next_record"):
            value = row.get(key) or ""
            if not value:
                continue
            partner = int(value, 16)
            if partner in target_set:
                continue
            src = next((s for s in sheet if int(s["record_start"], 16) == partner), None)
            if src is None:
                continue
            p_len = len(bytes.fromhex(src["original_payload_hex"]))
            p_at = sb + partner
            if candidate_bytes[p_at:p_at+p_len+1] != current[p_at:p_at+p_len+1]:
                partner_failures.append({"target": f"{logical:06X}", "partner": f"{partner:06X}"})

    anchor_checks = []
    for logical, name in ANCHORS.items():
        target = next((row for row in targets if int(row["record_start"], 16) == logical), None)
        anchor_checks.append({
            "abs": f"{logical:06X}",
            "name": name,
            "repaired": target is not None,
            "metadata_hex": "" if target is None else target["metadata_hex"],
            "render": "" if target is None else target["candidate_render"],
        })

    if target_failures or partner_failures or unexpected or not all(row["repaired"] for row in anchor_checks):
        raise BuildError(
            f"static guard failed: target={len(target_failures)} partner={len(partner_failures)} "
            f"unexpected={len(unexpected)} anchors={anchor_checks}"
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save)
    atomic_bytes(SRAM_SAVE, save)

    inventory_fields = [
        "record_start", "bank", "metadata_hex", "prefix_hex", "authoritative_structure_hex", "body_capacity",
        "body_hex_original", "terminator_hex", "current_payload_hex", "current_render",
        "classification", "safe_structure_exact", "current_structure_exact",
        "previous_record", "next_record", "screen_evidence", "action", "reason",
        "candidate_payload_hex", "candidate_render",
    ]
    write_csv(INVENTORY_CSV, inventory, inventory_fields)
    speaker_fields = [
        "record_start", "bank", "metadata_hex", "prefix_hex", "authoritative_structure_hex",
        "safe_structure_exact", "current_structure_exact", "previous_record", "next_record",
        "screen_evidence", "classification", "action", "reason",
    ]
    write_csv(SPEAKER_CSV, inventory, speaker_fields)
    write_csv(AMBIGUOUS_CSV, ambiguous, inventory_fields)

    counts: dict[str, int] = {}
    for row in inventory:
        counts[row["action"]] = counts.get(row["action"], 0) + 1
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_dialogue_structure_repair_candidate.py",
        "ok": True,
        "promotion_allowed": False,
        "purpose": "repair battle-dialogue speaker/portrait structure lost by historical whole-record voice rewrites while preserving current Korean body tokens",
        "format": {
            "scope": "banks 5D/5E battle voice only; scenario/event dialogue is excluded",
            "record": "metadata(first code unit) + optional proven prefix + body + 00 terminator",
            "text_initial_exceptions": len(false_prefix),
            "screen_proven_long_prefixes": {f"{k:06X}": v.hex().upper() for k, v in SCREEN_PREFIXES.items()},
            "write_policy": "authoritative structure is frozen from safe snapshot; translated body token is reused; terminator/gaps/partners are never written; short/fixed/non-E5 cases are quarantined",
        },
        "inputs": {
            "main_tip": ident(TIP, current),
            "safe_battle_structure": ident(SAFE, safe),
            "voice_sheet": ident(VOICE_SHEET),
            "false_prefix_catalog": ident(FALSE_PREFIX),
            "runtime_false_prefix_catalog": ident(RUNTIME_FALSE_PREFIX),
            "main_saveram": ident(MAIN_SAVE, save),
        },
        "outputs": {
            "candidate_rom": ident(OUT_ROM, candidate_bytes),
            "candidate_saveram": ident(OUT_SAVE, save),
            "sram_mirror": ident(SRAM_SAVE, save),
            "inventory_csv": ident(INVENTORY_CSV),
            "speaker_portrait_metadata_csv": ident(SPEAKER_CSV),
            "ambiguous_csv": ident(AMBIGUOUS_CSV),
        },
        "counts": {
            "battle_records": len(inventory),
            "targets": len(targets),
            "ambiguous_short_fixed_or_boundary": len(ambiguous),
            "text_initial_exceptions": sum(row["classification"] == "text_initial_exception" for row in inventory),
            "actions": counts,
            "changed_bytes": len(changed),
            "changed_bytes_outside_target_or_checksum": len(unexpected),
        },
        "anchors": anchor_checks,
        "static_checks": {
            "battle_dialogue_target_render_exact": len(target_failures) == 0,
            "portrait_speaker_metadata_exact": all(
                bytes.fromhex(row["after_payload_hex"]).startswith(bytes.fromhex(row["metadata_hex"] + row["prefix_hex"]))
                for row in targets
            ),
            "bundle_partner_structure_exact": len(partner_failures) == 0,
            "non_target_battle_system_structure_exact": len(unexpected) == 0,
            "terminator_exact": all(
                candidate_bytes[sb + int(row["record_start"], 16) + len(bytes.fromhex(row["after_payload_hex"]))] == 0
                for row in targets
            ),
            "candidate_saveram_exact_main_snapshot": OUT_SAVE.read_bytes() == save == SRAM_SAVE.read_bytes(),
        },
        "checksum": f"{checksum:04X}",
        "target_failures": target_failures,
        "partner_failures": partner_failures,
        "target_sample": targets[:60],
        "remaining_gate": "user emulator validation; do not promote candidate to main TIP",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "ok": True,
        "candidate": report["outputs"]["candidate_rom"],
        "counts": report["counts"],
        "anchors": anchor_checks,
        "checksum": report["checksum"],
        "ambiguous_csv": report["outputs"]["ambiguous_csv"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
