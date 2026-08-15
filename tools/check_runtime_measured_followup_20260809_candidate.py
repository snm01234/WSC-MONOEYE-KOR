#!/usr/bin/env python3
"""Read-only independent audit for runtime_measured_followup_20260809_candidate.

This intentionally does not write reports.  It validates the remaining
user-reported runtime defects after the Oita/machine-translation pair was
explicitly removed from scope, including both duplicate Tenkyouken records.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3
from audit_battle_false_lead_recurrence import (
    DUPLICATE,
    EXPECTED_DUPLICATE,
    EXPECTED_SAFE,
    RUNTIME_OVERRIDES,
    SAFE,
)
from audit_dialogue_20cell_candidate import (
    QUALITY,
    REVIEWED,
    VOICE,
    decode as decode_20,
    load_battle_prefixes,
    visible_lines,
)
from audit_dialogue_runtime_safety_gate import audit as runtime_safety_audit
from audit_garrod_native_stock_guard import build_report as build_garrod_guard
from build_sig_scenario_stock_native_chain_candidate import (
    current_ext3_nested_parents,
    current_nested_parents,
)
from build_terminology_retranslation_candidate import stock_storage_proof
from extract_script import split_prefix_body
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base
from scan_false_segptr_writes import classify, isolated_triples, is_ext3_token_prefix

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/runtime_measured_followup_20260809_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/runtime_measured_followup_20260809_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
SPEC_PATH = ROOT / "data/runtime_measured_followup_20260809_ko.json"
BUILD_REPORT = ROOT / "out/patch/runtime_measured_followup_20260809_candidate_report.json"

EXPECTED_PARENT_SHA = "fb6629d89cfd0dd8f48a621af5c0175d4602f113abec5d781b54b32b14efa86d"
EXPECTED_CANDIDATE_SHA = "f11b11c05e94e6b3007061e00586bbf74424be1e36c07571f43cb6a4584bafaf"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXT_META = {
    "stock_count": 3831,
    "slot_count": 265,
    "ext_ptr_off": "0000",
    "ext_seg": "10",
    "ext_in_expansion": True,
}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}
OITA_EXCLUDED = (0x63CF7C, 0x63CF8A)
UNCHANGED_CONTEXT = (0x599841, 0x599864, 0x59996D)
TARGETS = {
    0x59976D: "가아앗！",
    0x59984F: "최종오의！！",
    0x59987B: "천경궈어어언！！！",
    0x599977: "동방불패！　최종오의이이！！",
    0x5999A6: "천경궈어어언！！！",
    0x6226BE: "더　이상의　증식을　허용하지　마！！",
    0x622832: "……하아앗！！",
    0x622848: "흥……　이제야　좀　전사의",
    0x622850: "낯빛으로　각오를　다진　것　같구만。",
    0x67AF01: "게임　오버",
    0x67C0EC: "게임　오버",
    0x693D54: "오오！",
    0x63E6E4: "잘　들어！！",
    0x63EB4A: "죄송합니다……",
    0x63F0BD: "흠……",
    0x63F483: "제로……",
    0x63F67C: "윽……！",
}
TARGET_PREFIX = {
    0x59976D: bytes.fromhex("171C18"),
    0x59984F: bytes.fromhex("171C18"),
    0x59987B: bytes.fromhex("173418"),
    0x599977: b"",
    0x5999A6: bytes.fromhex("173418"),
    0x6226BE: bytes.fromhex("18"),
    0x622832: bytes.fromhex("173418"),
    0x622848: bytes.fromhex("173418"),
    0x622850: b"",
    0x67AF01: b"",
    0x67C0EC: b"",
    0x693D54: b"",
    0x63E6E4: bytes.fromhex("173418"),
    0x63EB4A: bytes.fromhex("173418"),
    0x63F0BD: bytes.fromhex("173418"),
    0x63F483: bytes.fromhex("173418"),
    0x63F67C: bytes.fromhex("173418"),
}
EXACT_NATIVE_TWO_TOKEN = {0x63E6E4, 0x63EB4A, 0x63F0BD, 0x63F483, 0x63F67C}
EXPECTED_CONTEXT_TEXT = {
    0x599841: "유파、동방불패！",
    0x599864: "……석파！！",
    0x59996D: "……유파！！",
}


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise RuntimeError(f"unreadable zstring {logical:06X}")
    return bytes(got[0]), int(got[1] - sb)


def strip_pad(text: str) -> str:
    return text.rstrip("　 \t")


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= ch <= "\u30ff"
        or "\u3400" <= ch <= "\u4dbf"
        or "\u4e00" <= ch <= "\u9fff"
        for ch in text
    )


def logical_pattern_hits(rom: bytes, pattern: bytes) -> list[int]:
    base = stock_base(rom)
    view = rom[base:]
    hits: list[int] = []
    start = 0
    while True:
        pos = view.find(pattern, start)
        if pos < 0:
            break
        hits.append(pos)
        start = pos + 1
    return hits


def check_false_leads(rom: bytes) -> dict:
    sb = stock_base(rom)
    with SAFE.open(encoding="utf-8-sig", newline="") as handle:
        safe = list(csv.DictReader(handle))
    with DUPLICATE.open(encoding="utf-8-sig", newline="") as handle:
        duplicate = list(csv.DictReader(handle))
    if len(safe) != EXPECTED_SAFE or len(duplicate) != EXPECTED_DUPLICATE:
        raise RuntimeError("false-lead source population drifted")
    bad: list[str] = []
    for row in safe:
        logical = int(str(row["abs"]), 16)
        lead = bytes.fromhex(str(row["lead_hex"]))
        extent = len(bytes.fromhex(str(row["candidate_payload_hex"])))
        if rom[sb + logical:sb + logical + extent].startswith(lead):
            bad.append(f"safe:{logical:06X}")
    for row in duplicate:
        logical = int(str(row["abs"]), 16)
        lead = bytes.fromhex(str(row["removed_lead_hex"]))
        extent = len(bytes.fromhex(str(row["before_hex"])))
        if rom[sb + logical:sb + logical + extent].startswith(lead):
            bad.append(f"duplicate:{logical:06X}")
    for address, lead in RUNTIME_OVERRIDES.items():
        logical = int(address, 16)
        if rom[sb + logical:sb + logical + 16].startswith(lead):
            bad.append(f"runtime:{address}")
    return {
        "total_guarded": len(safe) + len(duplicate) + len(RUNTIME_OVERRIDES),
        "reintroduced": len(bad),
        "failures": bad,
    }


def check_false_segptr(original: bytes, candidate: bytes) -> dict:
    sb = stock_base(candidate)
    sites: list[dict] = []
    for bank in range(0x5D, 0x76):
        lo, hi = bank << 16, (bank << 16) + 0x10000
        for at in isolated_triples(original, candidate, sb, lo, hi):
            triple = bytes(candidate[sb + at:sb + at + 3])
            if is_ext3_token_prefix(triple):
                continue
            info = classify(original, candidate, sb, at)
            if info is not None:
                sites.append({"logical": f"{at:06X}", **info})
    return {"sites_found": len(sites), "sites": sites}


def check_20cell(candidate: bytes, dictionary, tbl: Tbl) -> dict:
    battle_prefixes = load_battle_prefixes()
    rows: list[tuple[str, str, int]] = []
    quality = json.loads(QUALITY.read_text(encoding="utf-8"))
    for src in quality.get("lines") or []:
        address = str(src.get("abs") or "").upper()
        if src.get("kind") != "dialogue" or address[:2] not in {"60", "61", "62", "63"}:
            continue
        if not src.get("ko"):
            continue
        _payload, text = decode_20(candidate, dictionary, tbl, address)
        rows.extend((address, line, len(line)) for line in visible_lines(text))

    csv.field_size_limit(10_000_000)
    audited_battle: set[str] = set()
    with REVIEWED.open(encoding="utf-8-sig", newline="") as handle:
        for src in csv.DictReader(handle):
            scope = src.get("scope") or ""
            if scope not in {"bank59_event", "battle_voice", "id_indirect_ui"}:
                continue
            address = str(src.get("abs") or "").upper()
            _payload, text = decode_20(
                candidate,
                dictionary,
                tbl,
                address,
                battle_prefixes=battle_prefixes if scope == "battle_voice" else None,
            )
            rows.extend((address, line, len(line)) for line in visible_lines(text))
            if scope == "battle_voice":
                audited_battle.add(address)
    with VOICE.open(encoding="utf-8-sig", newline="") as handle:
        for src in csv.DictReader(handle):
            address = str(src.get("record_start") or "").upper()
            if not address or (src.get("bank") or "").upper() not in {"5D", "5E"} or address in audited_battle:
                continue
            _payload, text = decode_20(
                candidate, dictionary, tbl, address, battle_prefixes=battle_prefixes
            )
            rows.extend((address, line, len(line)) for line in visible_lines(text))
            audited_battle.add(address)
    offenders = [row for row in rows if row[2] > 20]
    return {
        "lines": len(rows),
        "max_cells": max((row[2] for row in rows), default=0),
        "offenders": len(offenders),
        "first_offenders": offenders[:10],
    }


def hard_signature(report: dict) -> Counter[tuple[str, str, str, str]]:
    rows = list(report.get("hard_failures_rows") or []) + list(report.get("bank5f_failures") or [])
    out: Counter[tuple[str, str, str, str]] = Counter()
    for row in rows:
        out[(
            str(row.get("reason") or ""),
            str(row.get("abs") or row.get("address") or ""),
            str(row.get("route") or ""),
            str(row.get("family") or ""),
        )] += 1
    return out


def main() -> int:
    parent = bytes(load_rom(MAIN))
    candidate = bytes(load_rom(CANDIDATE))
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    failures: list[str] = []
    checks: dict[str, object] = {}

    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        failures.append(f"parent identity drift: {sha(parent)}")
    if len(candidate) != ROM_SIZE or sha(candidate) != EXPECTED_CANDIDATE_SHA:
        failures.append(f"candidate identity drift: {sha(candidate)}")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        failures.append("main SaveRAM missing/wrong size")
    if not CANDIDATE_SAVE.is_file() or CANDIDATE_SAVE.stat().st_size != SAVE_SIZE:
        failures.append("candidate SaveRAM missing/wrong size")
    checks["identity"] = {
        "parent_sha256": sha(parent),
        "candidate_sha256": sha(candidate),
        "main_unchanged": sha(parent) == EXPECTED_PARENT_SHA,
        "candidate_saveram_matches_current": (
            CANDIDATE_SAVE.is_file()
            and MAIN_SAVE.is_file()
            and CANDIDATE_SAVE.read_bytes() == MAIN_SAVE.read_bytes()
        ),
    }

    dictionary = make_dictionary_ext3(candidate, EXT_META, EXT3_META)
    original_dictionary = Dictionary(original)

    # Explicitly prove the user-excluded Oita pair is byte-identical.
    excluded_rows = []
    for logical in OITA_EXCLUDED:
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(candidate, logical)
        same = before == after and before_term == after_term
        if not same:
            failures.append(f"excluded Oita record changed: {logical:06X}")
        excluded_rows.append({
            "abs": f"{logical:06X}",
            "byte_exact": same,
            "payload_hex": after.hex().upper(),
            "terminator": f"{after_term:06X}",
        })
    checks["oita_excluded"] = excluded_rows

    target_rows = []
    for logical, expected_text in TARGETS.items():
        payload, term = payload_at(candidate, logical)
        parent_payload, parent_term = payload_at(parent, logical)
        source_payload, source_term = payload_at(original, logical)
        source_prefix, source_body, kind = split_prefix_body(source_payload)
        prefix = TARGET_PREFIX[logical]
        if source_prefix != prefix:
            failures.append(f"source prefix mismatch {logical:06X}")
        if term != parent_term or term != source_term:
            failures.append(f"terminator moved {logical:06X}: {term:06X}/{parent_term:06X}/{source_term:06X}")
        if len(payload) != len(parent_payload):
            failures.append(f"payload extent changed {logical:06X}")
        if not payload.startswith(prefix):
            failures.append(f"prefix changed {logical:06X}")
        body = payload[len(prefix):]
        rendered = strip_pad(dictionary.expand(body, tbl))
        if rendered != expected_text:
            failures.append(f"render mismatch {logical:06X}: {rendered!r}")
        if has_japanese(rendered):
            failures.append(f"Japanese residual {logical:06X}: {rendered!r}")
        if b"\xE5\x18" in body:
            failures.append(f"ext3 token remained on native-only route {logical:06X}")
        if logical in EXACT_NATIVE_TWO_TOKEN:
            exact_two = (
                len(body) == 4
                and 0xF0 <= body[0] <= 0xFE
                and 0xF0 <= body[2] <= 0xFE
                and len(source_body) == 4
                and 0xF0 <= source_body[0] <= 0xFE
                and 0xF0 <= source_body[2] <= 0xFE
            )
            if not exact_two:
                failures.append(f"exact native-two-token grammar not restored {logical:06X}")
        source_text = strip_pad(original_dictionary.expand(source_body, tbl))
        target_rows.append({
            "abs": f"{logical:06X}",
            "kind": kind,
            "source_jp": source_text,
            "rendered": rendered,
            "prefix_hex": prefix.hex().upper(),
            "payload_len": len(payload),
            "terminator": f"{term:06X}",
            "native_only": b"\xE5\x18" not in body,
        })
    checks["targets"] = target_rows

    # Runtime testing proved the Tenkyouken shout has two byte-identical source
    # records.  The previous candidate changed only 59987B while the game
    # consumed 5999A6, so lock the whole duplicate family instead of trusting a
    # single address.  Also pin the short God-Finger shout as a unique source
    # record so a second hidden copy cannot silently survive.
    expected_tenkyou = {0x59987B, 0x5999A6}
    original_tenkyou = set(logical_pattern_hits(original, bytes.fromhex("173418F267FE6BF044")))
    parent_tenkyou_old = set(logical_pattern_hits(parent, bytes.fromhex("173418E51808980101")))
    candidate_tenkyou_old = set(logical_pattern_hits(candidate, bytes.fromhex("173418E51808980101")))
    if original_tenkyou != expected_tenkyou:
        failures.append(f"Tenkyouken source duplicate population drifted: {sorted(original_tenkyou)}")
    if parent_tenkyou_old != expected_tenkyou:
        failures.append(f"Tenkyouken parent duplicate population drifted: {sorted(parent_tenkyou_old)}")
    if candidate_tenkyou_old:
        failures.append(f"old Tenkyouken ext3 duplicate remains: {sorted(candidate_tenkyou_old)}")
    god_source_hits = set(logical_pattern_hits(original, bytes.fromhex("171C18F732F35303")))
    god_candidate_old = set(logical_pattern_hits(candidate, bytes.fromhex("171C18F732F35303")))
    if god_source_hits != {0x59976D}:
        failures.append(f"God short-shout source population drifted: {sorted(god_source_hits)}")
    if god_candidate_old:
        failures.append(f"old God short-shout payload remains: {sorted(god_candidate_old)}")
    checks["duplicate_family_runtime_lock"] = {
        "tenkyou_source_records": [f"{x:06X}" for x in sorted(original_tenkyou)],
        "tenkyou_parent_old_records": [f"{x:06X}" for x in sorted(parent_tenkyou_old)],
        "tenkyou_candidate_old_records": [f"{x:06X}" for x in sorted(candidate_tenkyou_old)],
        "god_short_source_records": [f"{x:06X}" for x in sorted(god_source_hits)],
        "god_short_candidate_old_records": [f"{x:06X}" for x in sorted(god_candidate_old)],
    }

    context_rows = []
    for logical in UNCHANGED_CONTEXT:
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(candidate, logical)
        same = before == after and before_term == after_term
        if not same:
            failures.append(f"context record changed {logical:06X}")
        source_payload, _ = payload_at(original, logical)
        prefix, _source_body, _kind = split_prefix_body(source_payload)
        body = after[len(prefix):] if after.startswith(prefix) else after
        rendered = strip_pad(dictionary.expand(body, tbl))
        if rendered != EXPECTED_CONTEXT_TEXT[logical]:
            failures.append(f"context render drift {logical:06X}: {rendered!r}")
        context_rows.append({
            "abs": f"{logical:06X}",
            "byte_exact": same,
            "rendered": rendered,
        })
    checks["unchanged_context"] = context_rows

    # The speaker/portrait sequence implicated by the screenshots must retain
    # the source control grammar: Domon record, Touhou-Fuhai record, then its
    # bare continuation.  The leaked phrase is redistributed only inside these
    # three visible bodies; their speaker/control prefixes remain source-exact.
    speaker_sequence = []
    for logical in (0x622832, 0x622848, 0x622850):
        source, _ = payload_at(original, logical)
        current, term = payload_at(candidate, logical)
        prefix, _body, kind = split_prefix_body(source)
        if not current.startswith(prefix):
            failures.append(f"speaker prefix drift {logical:06X}")
        speaker_sequence.append({
            "abs": f"{logical:06X}",
            "kind": kind,
            "prefix_hex": prefix.hex().upper(),
            "terminator": f"{term:06X}",
        })
    if speaker_sequence[0]["prefix_hex"] != "173418" or speaker_sequence[1]["prefix_hex"] != "173418" or speaker_sequence[2]["prefix_hex"] != "":
        failures.append("Domon/Touhou-Fuhai source speaker grammar changed")
    checks["speaker_sequence_622832_622850"] = speaker_sequence

    # 6226BE is the screenshot where the portrait box was corrupted.  The
    # original one-byte route prefix and the bytes immediately following its
    # terminator are pinned; only the body is converted away from ext3.
    p_before, p_term = payload_at(parent, 0x6226BE)
    p_after, c_term = payload_at(candidate, 0x6226BE)
    sb = stock_base(candidate)
    next_parent = parent[stock_base(parent) + p_term:stock_base(parent) + p_term + 12]
    next_candidate = candidate[sb + c_term:sb + c_term + 12]
    portrait_boundary_ok = (
        p_after.startswith(b"\x18")
        and c_term == p_term
        and next_parent == next_candidate
    )
    if not portrait_boundary_ok:
        failures.append("6226BE portrait/control boundary drift")
    checks["portrait_boundary_6226BE"] = {
        "prefix_hex": p_after[:1].hex().upper(),
        "terminator": f"{c_term:06X}",
        "next_12_bytes_byte_exact": next_parent == next_candidate,
        "ext3_removed_from_body": b"\xE5\x18" not in p_after[1:],
    }

    # Validate the builder's stock-slot proof again against both parent and
    # candidate graphs.
    build_report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    slot_indices = {int(row["index"], 16) for row in build_report.get("new_stock_slots") or []}
    parent_dict = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    parent_union = build_reference_union(original, parent, ext_meta=EXT_META, ext3_meta=EXT3_META)
    candidate_union = build_reference_union(original, candidate, ext_meta=EXT_META, ext3_meta=EXT3_META)
    parent_nested = current_nested_parents(parent_dict, slot_indices)
    parent_ext3_nested = current_ext3_nested_parents(parent_dict, slot_indices)
    candidate_nested = current_nested_parents(dictionary, slot_indices)
    candidate_ext3_nested = current_ext3_nested_parents(dictionary, slot_indices)
    slot_rows = []
    expected_consumers: dict[int, set[int]] = defaultdict(set)
    for row in build_report.get("candidate_stock_consumer_proof") or []:
        index = int(row["index"], 16)
        expected_consumers[index] = {int(x, 16) for x in row.get("working_consumers") or []}
    for index in sorted(slot_indices):
        proof = stock_storage_proof(parent_dict, index)
        parent_working = {int(c.abs) for c in parent_union.consumers_for(index) if "working" in c.seen_in}
        candidate_working = {int(c.abs) for c in candidate_union.consumers_for(index) if "working" in c.seen_in}
        ok = (
            proof["ok"]
            and not parent_working
            and not parent_nested[index]
            and not parent_ext3_nested[index]
            and candidate_working == expected_consumers[index]
            and not candidate_nested[index]
            and not candidate_ext3_nested[index]
        )
        if not ok:
            failures.append(f"stock-slot dependency proof failed {index:04X}")
        slot_rows.append({
            "index": f"{index:04X}",
            "parent_working_consumers": sorted(f"{x:06X}" for x in parent_working),
            "candidate_working_consumers": sorted(f"{x:06X}" for x in candidate_working),
            "expected_candidate_consumers": sorted(f"{x:06X}" for x in expected_consumers[index]),
            "parent_native_nested": len(parent_nested[index]),
            "parent_ext3_nested": len(parent_ext3_nested[index]),
            "candidate_native_nested": len(candidate_nested[index]),
            "candidate_ext3_nested": len(candidate_ext3_nested[index]),
            "unique_storage": bool(proof["ok"]),
        })
    checks["stock_slot_dependency_proof"] = slot_rows

    false_leads = check_false_leads(candidate)
    if false_leads["reintroduced"]:
        failures.append(f"false visible leads reintroduced: {false_leads['failures'][:6]}")
    checks["false_visible_leads"] = false_leads

    false_segptr = check_false_segptr(original, candidate)
    if false_segptr["sites_found"]:
        failures.append(f"false segmented pointer writes: {false_segptr['sites_found']}")
    checks["false_segmented_pointers_5D_75"] = false_segptr

    parent_width = check_20cell(parent, parent_dict, tbl)
    width = check_20cell(candidate, dictionary, tbl)
    parent_offenders = {(a, text, cells) for a, text, cells in parent_width["first_offenders"]}
    candidate_offenders = {(a, text, cells) for a, text, cells in width["first_offenders"]}
    # The just-promoted Domon retranslation parent already contains seven
    # >20-cell lines outside this focused task.  This candidate must not add any
    # new offender, and all seven user-targeted records themselves must be <=20.
    new_width_offenders = sorted(candidate_offenders - parent_offenders)
    if width["offenders"] != parent_width["offenders"] or new_width_offenders:
        failures.append(
            f"20-cell differential worsened: parent={parent_width['offenders']} "
            f"candidate={width['offenders']} new={new_width_offenders[:3]}"
        )
    if any(len(text) > 20 for text in TARGETS.values()):
        failures.append("one or more focused target strings exceed 20 cells")
    checks["dialogue_20cell_differential"] = {
        "parent": parent_width,
        "candidate": width,
        "new_offenders": new_width_offenders,
        "focused_target_max_cells": max(len(text) for text in TARGETS.values()),
    }

    parent_garrod = build_garrod_guard(MAIN, ORIGINAL, expected_target_sha=None)
    garrod = build_garrod_guard(CANDIDATE, ORIGINAL, expected_target_sha=None)
    parent_gc = parent_garrod.get("counts") or {}
    candidate_gc = garrod.get("counts") or {}
    garrod_repair_ok = (
        parent_garrod.get("status") == "fail"
        and int(parent_gc.get("source_exact_native_two_token_current_non_native") or 0) == 5
        and int(parent_gc.get("current_ext3_source_exact_native_two_token") or 0) == 5
        and garrod.get("status") == "pass"
        and int(candidate_gc.get("source_exact_native_two_token_current_non_native") or 0) == 0
        and int(candidate_gc.get("current_ext3_source_exact_native_two_token") or 0) == 0
        and int(candidate_gc.get("current_exact_ext3_risk_shape") or 0) == 18
        and int(candidate_gc.get("current_ext3_source_mixed_grammar") or 0) == 18
        and int(candidate_gc.get("family_binding_failures") or 0) == 0
        and int(candidate_gc.get("scan_errors") or 0) == 0
        and not garrod.get("issues")
    )
    if not garrod_repair_ok:
        failures.append("exact native-two-token structural repair did not clear the five inherited Garrod-family risks")
    checks["garrod_native_stock_guard_differential"] = {
        "parent_status": parent_garrod.get("status"),
        "candidate_status": garrod.get("status"),
        "five_exact_native_risks_cleared": garrod_repair_ok,
        "mixed_18_review_only_preserved": int(candidate_gc.get("current_ext3_source_mixed_grammar") or 0) == 18,
        "parent_counts": parent_gc,
        "candidate_counts": candidate_gc,
        "parent_issues": parent_garrod.get("issues"),
        "candidate_issues": garrod.get("issues"),
    }

    # Generic runtime safety is intentionally not clean on the current parent;
    # it contains a large pre-existing deferred population.  The meaningful
    # candidate gate is differential: this focused patch must introduce zero new
    # hard failures compared with its exact parent.
    parent_safety = runtime_safety_audit(
        parent, original, target_path=MAIN, placeholder_catalog=None
    )
    candidate_safety = runtime_safety_audit(
        candidate, original, target_path=CANDIDATE, placeholder_catalog=None
    )
    parent_hard = hard_signature(parent_safety)
    candidate_hard = hard_signature(candidate_safety)
    new_hard_counter = candidate_hard - parent_hard
    cleared_hard_counter = parent_hard - candidate_hard
    new_hard = sorted(new_hard_counter.elements())
    cleared_hard = sorted(cleared_hard_counter.elements())
    if new_hard:
        failures.append(f"runtime safety introduced {len(new_hard)} new hard failures")
    checks["runtime_safety_differential"] = {
        "parent_ok": bool(parent_safety.get("ok")),
        "candidate_ok": bool(candidate_safety.get("ok")),
        "parent_hard_failures": sum(parent_hard.values()),
        "candidate_hard_failures": sum(candidate_hard.values()),
        "new_hard_failures": len(new_hard),
        "cleared_hard_failures": len(cleared_hard),
        "new_hard_first": [list(row) for row in new_hard[:5]],
        "cleared_hard_first": [list(row) for row in cleared_hard[:5]],
    }

    summary = {
        "ok": not failures,
        "candidate": {
            "path": str(CANDIDATE.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(candidate),
            "size": len(candidate),
        },
        "scope": {
            "targets": [f"{x:06X}" for x in sorted(TARGETS)],
            "oita_excluded_byte_exact": [f"{x:06X}" for x in OITA_EXCLUDED],
        },
        "checks": checks,
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
