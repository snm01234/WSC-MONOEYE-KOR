#!/usr/bin/env python3
"""Build the late 2026-08-09 runtime-measurement follow-up candidate.

Fixes:
1) 6248AC cross-event 20-cell reflow: restore the source-native record boundary.
2) 624A07 `리나!!` special-caller failure: restore exact source-native two-token grammar.
   The following 08 7A actor/portrait switch is byte-preserved rather than guessed.
3) 624AF9/624B30 Haman truncation: source-address-selective `하만!!!` repair.
4) Runtime battle screenshot `こ戦` + black portrait/sprite box: all current battle
   records with the exact E5 18 39... body-only signature are independently proven
   visible-text leads, not metadata.  Therefore *do not* restore their old Japanese
   first code unit.  Instead rehome their existing Korean ext3 phrase to ordinary
   native stock tokens, preserving text, controls, record extent, and terminator.

All newly used stock dictionary storage is strong-retired: zero current working
consumers, zero current native/ext3 nested parents, unique storage proof.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3
from build_scenario_page_boundary_guard_candidate import stock_text_map
from build_sig_scenario_stock_native_chain_candidate import current_ext3_nested_parents, current_nested_parents
from build_terminology_retranslation_candidate import stock_storage_proof
from extract_script import split_prefix_body
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import (
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
FALSE_SAFE = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
VOICE_SHEET = ROOT / "out/script/runtime_text_residual_voice_sheet.csv"
WORKLIST = ROOT / "out/script/dialogue_20cell_worklist.json"
OUT_ROM = ROOT / "out/patch/runtime_measured_round2_20260809_candidate.wsc"
OUT_SAVE = ROOT / "sram/runtime_measured_round2_20260809_candidate.sav"
REPORT = ROOT / "out/patch/runtime_measured_round2_20260809_candidate_report.json"

EXPECTED_MAIN_SHA = "f11b11c05e94e6b3007061e00586bbf74424be1e36c07571f43cb6a4584bafaf"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
HANGUL_MARKER = 0xEC8D
FRAGMENT_MAX_ENCODED = 10
EXT_META = {
    "stock_count": 3831,
    "slot_count": 265,
    "ext_ptr_off": "0000",
    "ext_seg": "10",
    "ext_in_expansion": True,
}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}
CONTROL_TAG = re.compile(r"<E([0-9A-Fa-f]{4})>")

SCENARIO_EXPECTED = {
    # Exact short `name!!` records followed immediately by an 08 xx actor switch.
    0x60C3B4: "173418E5186265",
    0x60C6D5: "173418E518C052",
    0x61A3F3: "173418E518C081",
    0x61D6BA: "173418E518C081",
    0x62BD5F: "173418E5180D76",
    # Applied 20-cell reflows that crossed a non-padding control/speaker gap.
    0x60C540: "18E518C233010101010101010101010101",
    0x60C55F: "E5184A8B010101010101",
    0x60CCFA: "18E5182AAB010101",
    0x60CD21: "E51872F70101010101",
    0x612D1E: "173418FAAEF24B",
    0x612D2E: "E51874BA0101010101010101010101",
    0x61CD5A: "173418E51812FE01010101010101",
    0x61CD76: "E51812FF01010101",
    0x621C99: "173418E5184AAB01",
    0x621CB4: "E518204901010101010101",
    0x6248AC: "173418E5182A9401",
    0x6248C1: "E51821D90101010101010101",
    # Runtime-measured short special-caller and Haman records.
    0x624A07: "173418E51821E8",
    0x624AF9: "173418E51829980101",
    0x624B30: "173418E51829980101",
}
SCENARIO_NATIVE_EXACT = {
    0x60C3B4: bytes.fromhex("173418F5DCF044"),  # 아인！！
    0x60C6D5: bytes.fromhex("173418F50BF044"),  # 시그！！
    0x61A3F3: bytes.fromhex("173418F97EF044"),  # 브라이트！！
    0x61D6BA: bytes.fromhex("173418F97EF044"),  # 브라이트！！
    0x62BD5F: bytes.fromhex("173418F64AF044"),  # 시로！！
    0x6248AC: bytes.fromhex("173418FA29F60C03"),  # 하만……！！
    0x624A07: bytes.fromhex("173418F59EF044"),    # 리나！！
}
HAMAN_NATIVE = bytes.fromhex("173418FA29F0440301")  # 하만！！！ + fixed tail pad
SCENARIO_TARGETS = {
    0x60C3B4: "아인！！",
    0x60C6D5: "시그！！",
    0x61A3F3: "브라이트！！",
    0x61D6BA: "브라이트！！",
    0x62BD5F: "시로！！",
    # Record-local replacements for every *applied* reflow that crossed a real
    # control/speaker boundary.  Each target is <=20 cells on its own.
    0x60C540: "사이드　６에　우군이　남아　있다고！？",
    0x60C55F: "아군　사이클롭스　부대입니다。",
    0x60CCFA: "가자、　바니！",
    0x60CD21: "꼴사납군……！！",
    0x612D1E: "그건……",
    0x612D2E: "제가　할　수　있다면　기꺼이。",
    0x61CD5A: "그럼、　예정대로……？",
    0x61CD76: "킬리만자로를　공격한다。",
    0x621C99: "데빌　건담！！",
    0x621CB4: "통로가　막혔어……！？",
    0x6248AC: "하만……！！",
    0x6248C1: "너무　말이　많았군。",
    0x624A07: "리나！！",
    0x624AF9: "하만！！！",
    0x624B30: "하만！！！",
}

SHORT_EXCLAMATION_NATIVE_SIBLINGS = {
    0x60C3B4,
    0x60C6D5,
    0x61A3F3,
    0x61D6BA,
    0x624A07,
    0x62BD5F,
}

EXPECTED_CROSS_CONTROL_REFLOWS = {
    "scenario_60C540": (0x60C540, 0x60C55F),
    "scenario_60CCFA": (0x60CCFA, 0x60CD21),
    "scenario_612D1E": (0x612D1E, 0x612D2E),
    "scenario_61CD5A": (0x61CD5A, 0x61CD76),
    "scenario_621C99": (0x621C99, 0x621CB4),
    "scenario_6248AC": (0x6248AC, 0x6248C1),
}
CROSS_CONTROL_SMALL_TARGETS = {
    SCENARIO_TARGETS[address]
    for pair in EXPECTED_CROSS_CONTROL_REFLOWS.values()
    for address in pair
}

EXPECTED_KOSEN_FAMILY = {
    0x5D0A97,
    0x5D1449,
    0x5D48C6,
    0x5D4B1D,
    0x5D7A26,
    0x5D7C01,
    0x5D9D85,
    0x5DA0B0,
    0x5DA3DB,
    0x5E18AF,
    0x5E291D,
    0x5E576E,
    0x5E5F53,
    0x5E9273,
    0x5EAF37,
    0x5EB8B5,
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def stripped(text: str) -> str:
    return text.rstrip("　 \t")


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable zstring at {logical:06X}")
    return bytes(got[0]), int(got[1])


def encode_phrase(text: str, tbl: Tbl) -> bytes:
    encoded = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=HANGUL_MARKER,
        hangul_marker_mode="run",
    )
    if encoded is None or not encoded or b"\x00" in encoded:
        raise BuildError(f"cannot encode phrase {text!r}")
    return bytes(encoded)


def split_text_fragment(text: str, tbl: Tbl) -> list[str]:
    """Greedy longest independently encodable fragments <= 10 bytes each."""
    if not text:
        return []
    # 6248C1 has ample record capacity, so split its 9-byte tail once more.
    # This keeps the scarce >=9-byte strong-retired stock population below its
    # exact current capacity without forcing any cross-record reflow.
    if text == SCENARIO_TARGETS[0x6248C1]:
        return ["너무　", "말이　", "많았", "군。"]
    # 612D1E is source-exact two-native-token grammar.  Preserve two native
    # code units even though the Korean text itself would fit in one token.
    if text == SCENARIO_TARGETS[0x612D1E]:
        return ["그건", "……"]
    limit = 8 if text in CROSS_CONTROL_SMALL_TARGETS else FRAGMENT_MAX_ENCODED
    out: list[str] = []
    pos = 0
    while pos < len(text):
        best: int | None = None
        for end in range(pos + 1, len(text) + 1):
            try:
                encoded = encode_phrase(text[pos:end], tbl)
            except BuildError:
                continue
            if len(encoded) <= limit:
                best = end
        if best is None:
            raise BuildError(f"cannot split native fragment at {text[pos:]!r}")
        out.append(text[pos:best])
        pos = best
    return out


def plan_display_text(text: str, tbl: Tbl) -> list[tuple[str, str | bytes]]:
    """Split display text into native stock fragments and raw control code units."""
    out: list[tuple[str, str | bytes]] = []
    pos = 0
    for match in CONTROL_TAG.finditer(text):
        for frag in split_text_fragment(text[pos:match.start()], tbl):
            out.append(("text", frag))
        out.append(("control", bytes.fromhex(match.group(1))))
        pos = match.end()
    for frag in split_text_fragment(text[pos:], tbl):
        out.append(("text", frag))
    return out


def render_plan_size(plan: list[tuple[str, str | bytes]]) -> int:
    return sum(2 for kind, _ in plan)  # stock token and supported controls are both 2-byte code units


def load_false_safe_addresses() -> set[int]:
    with FALSE_SAFE.open(encoding="utf-8-sig", newline="") as handle:
        return {int(row["abs"], 16) for row in csv.DictReader(handle)}


def current_kosen_family(parent: bytes, dictionary: Dictionary, tbl: Tbl) -> dict[int, dict[str, Any]]:
    sb = stock_base(parent)
    with VOICE_SHEET.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        logical = int(row["record_start"], 16)
        got = read_encoded_z_safe(parent, sb + logical, max_len=128)
        if got is None:
            continue
        payload, term = bytes(got[0]), int(got[1])
        if not payload.startswith(bytes.fromhex("E51839")):
            continue
        broken = stripped(dictionary.expand(payload[1:], tbl))
        if not broken.startswith("こ戦"):
            continue
        intended = stripped(dictionary.expand(payload, tbl))
        out[logical] = {
            "payload": payload,
            "term": term,
            "broken": broken,
            "intended": intended,
            "original_body": row.get("original_body") or "",
        }
    return out


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    d_parent = make_dictionary_ext3(parent, EXT_META, EXT3_META)
    d_original = Dictionary(original)
    sb = stock_base(parent)

    # Scenario preconditions.
    scenario_before: dict[int, bytes] = {}
    scenario_terms: dict[int, int] = {}
    scenario_prefixes: dict[int, bytes] = {}
    scenario_body_caps: dict[int, int] = {}
    for logical, expected_hex in SCENARIO_EXPECTED.items():
        payload, term = payload_at(parent, logical)
        if payload.hex().upper() != expected_hex:
            raise BuildError(f"scenario payload drift {logical:06X}: {payload.hex().upper()}")
        prefix, body, _kind = split_prefix_body(payload)
        scenario_before[logical] = payload
        scenario_terms[logical] = term
        scenario_prefixes[logical] = prefix
        scenario_body_caps[logical] = len(body)

    for logical, native in SCENARIO_NATIVE_EXACT.items():
        orig_payload, _ = payload_at(original, logical)
        if orig_payload != native:
            raise BuildError(f"source native grammar drift {logical:06X}: {orig_payload.hex().upper()}")
        prefix, body, _ = split_prefix_body(orig_payload)
        if stripped(d_parent.expand(body, tbl)) != SCENARIO_TARGETS[logical]:
            raise BuildError(f"current native mapping drift {logical:06X}")
        if logical == 0x624A07 and body != bytes.fromhex("F59EF044"):
            raise BuildError("624A07 exact source two-token grammar drifted")
        if logical == 0x6248AC and not stripped(d_original.expand(body, tbl)).startswith("ハマ－ン"):
            raise BuildError("6248AC source provenance drifted")

    # Exact adjacent control/portrait structures to preserve.
    preserve_ranges = {
        "6248_control_bridge": (0x6248B5, 0x6248C1),
        "624A_actor_switch": (0x624A0F, 0x624A13),
    }
    preserved_before = {
        name: parent[sb + lo:sb + hi]
        for name, (lo, hi) in preserve_ranges.items()
    }
    followup_6248b9, followup_6248b9_term = payload_at(parent, 0x6248B9)
    followup_624a13, followup_624a13_term = payload_at(parent, 0x624A13)

    # Runtime battle signature: every exact E5 18 39 -> `こ戦...` record is a
    # proven-visible-text-lead address, so metadata restoration is forbidden.
    false_safe = load_false_safe_addresses()
    kosen = current_kosen_family(parent, d_parent, tbl)
    if set(kosen) != EXPECTED_KOSEN_FAMILY:
        raise BuildError(
            "current E51839/こ戦 family drifted: "
            + ",".join(f"{x:06X}" for x in sorted(kosen))
        )
    if not EXPECTED_KOSEN_FAMILY <= false_safe:
        missing = EXPECTED_KOSEN_FAMILY - false_safe
        raise BuildError(f"battle false-visible proof missing: {[f'{x:06X}' for x in sorted(missing)]}")

    # Build record-local native plans for every scenario target that is not an
    # exact source-native/Haman payload restoration.  This also covers all five
    # additional applied 20-cell reflows found crossing real control gaps.
    exact_scenario = set(SCENARIO_NATIVE_EXACT) | {0x624AF9, 0x624B30}
    scenario_native_plans: dict[int, list[tuple[str, str | bytes]]] = {}
    for logical in sorted(set(SCENARIO_TARGETS) - exact_scenario):
        plan = plan_display_text(SCENARIO_TARGETS[logical], tbl)
        if render_plan_size(plan) > scenario_body_caps[logical]:
            raise BuildError(
                f"native scenario plan does not fit {logical:06X}: "
                f"{render_plan_size(plan)} > {scenario_body_caps[logical]}"
            )
        scenario_native_plans[logical] = plan

    # Battle strings are derived from their current ext3 render so the visible
    # Korean and embedded control content are preserved exactly.
    battle_plans: dict[int, list[tuple[str, str | bytes]]] = {}
    for logical, row in sorted(kosen.items()):
        plan = plan_display_text(str(row["intended"]), tbl)
        if render_plan_size(plan) > len(row["payload"]):
            raise BuildError(
                f"native battle plan does not fit {logical:06X}: "
                f"{render_plan_size(plan)} > {len(row['payload'])}"
            )
        battle_plans[logical] = plan

    # Collect all text fragments and prefer existing exact stock entries.
    all_fragment_texts = list(dict.fromkeys(
        [
            value
            for logical in sorted(scenario_native_plans)
            for kind, value in scenario_native_plans[logical]
            if kind == "text"
        ]
        + [
            value
            for logical in sorted(battle_plans)
            for kind, value in battle_plans[logical]
            if kind == "text"
        ]
    ))
    all_fragment_texts = [str(value) for value in all_fragment_texts]
    encoded_by_text = {text: encode_phrase(text, tbl) for text in all_fragment_texts}
    if any(len(encoded) > FRAGMENT_MAX_ENCODED for encoded in encoded_by_text.values()):
        raise BuildError("fragment splitter emitted oversized text")

    stock_map = stock_text_map(d_parent, tbl)
    existing: dict[str, int] = {}
    for text in all_fragment_texts:
        for index in stock_map.get(text) or []:
            if index < int(d_parent.stock_count) and dict_token_safe_in_zstring(index):
                existing[text] = int(index)
                break

    # Strong-retired pool for only fragments that have no existing exact token.
    union = build_reference_union(original, parent, ext_meta=EXT_META, ext3_meta=EXT3_META)
    watched = {
        index for index in range(min(int(d_parent.stock_count), 0x0F00))
        if dict_token_safe_in_zstring(index)
    }
    nested = current_nested_parents(d_parent, watched)
    ext3_nested = current_ext3_nested_parents(d_parent, watched)
    safe_pool: list[dict[str, Any]] = []
    for index in sorted(watched):
        if any("working" in c.seen_in for c in union.consumers_for(index)):
            continue
        if nested[index] or ext3_nested[index]:
            continue
        proof = stock_storage_proof(d_parent, index)
        if not proof["ok"]:
            continue
        safe_pool.append({
            "index": index,
            "proof": proof,
            "original_only_consumers": len([
                c for c in union.consumers_for(index) if "working" not in c.seen_in
            ]),
        })

    needed = [text for text in all_fragment_texts if text not in existing]
    allocated: dict[str, int] = {}
    used_slots: set[int] = set()
    # Largest encoded fragments first prevents wasting the few larger retired slots.
    for text in sorted(needed, key=lambda value: (-len(encoded_by_text[value]), value)):
        required = len(encoded_by_text[text])
        choices = [
            row for row in safe_pool
            if int(row["index"]) not in used_slots
            and int(row["index"]) not in set(existing.values())
            and int(row["proof"]["old_len"]) >= required
        ]
        if not choices:
            raise BuildError(f"no strong-retired stock slot for {text!r} ({required} bytes)")
        selected = min(choices, key=lambda row: (int(row["proof"]["old_len"]), int(row["index"])))
        index = int(selected["index"])
        used_slots.add(index)
        allocated[text] = index

    candidate = bytearray(parent)
    allowed: set[int] = set()
    stock_rows: list[dict[str, Any]] = []
    for text in sorted(allocated, key=lambda value: allocated[value]):
        index = allocated[text]
        encoded = encoded_by_text[text]
        selected = next(row for row in safe_pool if int(row["index"]) == index)
        proof = stock_storage_proof(make_dictionary_ext3(candidate, EXT_META, EXT3_META), index)
        if not proof["ok"] or int(proof["old_len"]) < len(encoded):
            raise BuildError(f"stock slot became unsafe {index:04X}")
        start = int(proof["entry_abs"])
        old_len = int(proof["old_len"])
        before_text = stripped(make_dictionary_ext3(candidate, EXT_META, EXT3_META).expand_index(index, tbl))
        candidate[start:start + len(encoded)] = encoded
        candidate[start + len(encoded)] = 0
        allowed.update(range(start, start + old_len + 1))
        check = make_dictionary_ext3(candidate, EXT_META, EXT3_META)
        if check.expand_index(index, tbl) != text:
            raise BuildError(f"stock fragment verify failed {index:04X}: {text!r}")
        stock_rows.append({
            "index": f"{index:04X}",
            "token": token_from_dict_index(index).hex().upper(),
            "before": before_text,
            "after": text,
            "encoded_len": len(encoded),
            "old_len": old_len,
            "working_consumers_before": 0,
            "native_nested_parents_before": 0,
            "ext3_nested_parents_before": 0,
            "original_only_consumers": int(selected["original_only_consumers"]),
        })

    token_for_text = {**existing, **allocated}

    def encode_plan(plan: list[tuple[str, str | bytes]]) -> bytes:
        out = bytearray()
        for kind, value in plan:
            if kind == "text":
                out += token_from_dict_index(token_for_text[str(value)])
            elif kind == "control":
                control = bytes(value)
                if len(control) != 2:
                    raise BuildError(f"unsupported control width: {control.hex().upper()}")
                out += control
            else:
                raise BuildError(f"unknown plan kind {kind!r}")
        return bytes(out)

    # Scenario rewrites.
    scenario_new: dict[int, bytes] = dict(SCENARIO_NATIVE_EXACT)
    scenario_new.update({
        0x624AF9: HAMAN_NATIVE,
        0x624B30: HAMAN_NATIVE,
    })
    for logical in sorted(scenario_native_plans):
        native_body = encode_plan(scenario_native_plans[logical])
        capacity = scenario_body_caps[logical]
        if len(native_body) > capacity:
            raise BuildError(f"scenario native body overflow {logical:06X}")
        scenario_new[logical] = (
            scenario_prefixes[logical]
            + native_body
            + b"\x01" * (capacity - len(native_body))
        )

    scenario_rows: list[dict[str, Any]] = []
    for logical in sorted(scenario_new):
        old = scenario_before[logical]
        new = scenario_new[logical]
        if len(new) != len(old):
            raise BuildError(f"scenario extent drift plan {logical:06X}")
        start = sb + logical
        candidate[start:start + len(old)] = new
        allowed.update(range(start, start + len(old)))
        got, term = payload_at(candidate, logical)
        if got != new or term != scenario_terms[logical]:
            raise BuildError(f"scenario boundary drift {logical:06X}")
        _, body, _ = split_prefix_body(got)
        render = stripped(make_dictionary_ext3(candidate, EXT_META, EXT3_META).expand(body, tbl))
        if render != SCENARIO_TARGETS[logical]:
            raise BuildError(f"scenario render drift {logical:06X}: {render!r}")
        scenario_rows.append({
            "abs": f"{logical:06X}",
            "before_hex": old.hex().upper(),
            "after_hex": new.hex().upper(),
            "render": render,
            "terminator": f"{term - sb:06X}",
            "ext3_removed": old.startswith(b"\xE5\x18") or b"\xE5\x18" in old,
        })

    # Battle E5 18 39 -> ordinary stock-native fragments.  No original lead is restored.
    battle_rows: list[dict[str, Any]] = []
    for logical in sorted(battle_plans):
        row = kosen[logical]
        old = bytes(row["payload"])
        new_body = encode_plan(battle_plans[logical])
        new = new_body + b"\x01" * (len(old) - len(new_body))
        if len(new) != len(old) or new.startswith(b"\xE5\x18") or b"\xE5\x18" in new:
            raise BuildError(f"battle native rehome plan unsafe {logical:06X}")
        start = sb + logical
        candidate[start:start + len(old)] = new
        allowed.update(range(start, start + len(old)))
        got, term = payload_at(candidate, logical)
        if got != new or term != int(row["term"]):
            raise BuildError(f"battle boundary drift {logical:06X}")
        render = stripped(make_dictionary_ext3(candidate, EXT_META, EXT3_META).expand(got, tbl))
        if render != str(row["intended"]):
            raise BuildError(
                f"battle render drift {logical:06X}: {render!r} != {row['intended']!r}"
            )
        battle_rows.append({
            "abs": f"{logical:06X}",
            "before_hex": old.hex().upper(),
            "after_hex": new.hex().upper(),
            "intended_render": render,
            "broken_visible_before_if_ext3_falls_through": row["broken"],
            "original_body": row["original_body"],
            "false_visible_lead_proven": True,
            "original_lead_restored": False,
            "ext3_removed": True,
            "terminator": f"{term - sb:06X}",
        })

    # Adjacent event controls and follow-up records remain exact.
    for name, (lo, hi) in preserve_ranges.items():
        if bytes(candidate[sb + lo:sb + hi]) != preserved_before[name]:
            raise BuildError(f"adjacent control range changed: {name}")
    got_6248b9, term_6248b9 = payload_at(candidate, 0x6248B9)
    got_624a13, term_624a13 = payload_at(candidate, 0x624A13)
    if got_6248b9 != followup_6248b9 or term_6248b9 != followup_6248b9_term:
        raise BuildError("6248B9 intermediate dialogue changed")
    if got_624a13 != followup_624a13 or term_624a13 != followup_624a13_term:
        raise BuildError("624A13 follow-up dialogue changed")

    # Selective Haman proof: never perform a global `하마` rewrite.
    for logical in (0x624AF9, 0x624B30):
        payload, _ = payload_at(candidate, logical)
        _, body, _ = split_prefix_body(payload)
        text = stripped(make_dictionary_ext3(candidate, EXT_META, EXT3_META).expand(body, tbl))
        if text != "하만！！！":
            raise BuildError(f"Haman selective repair failed {logical:06X}: {text!r}")

    checksum = update_ws_checksum(candidate)
    allowed.update(range(len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    unexpected = [
        i for i, (before, after) in enumerate(zip(parent, result))
        if before != after and i not in allowed
    ]
    if unexpected:
        raise BuildError(f"unexpected diff offset {unexpected[0]:07X}")

    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(result)
    OUT_SAVE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)

    report = {
        "schema_version": 2,
        "generated_by": "tools/build_runtime_measured_round2_20260809_candidate.py",
        "ok": True,
        "purpose": "runtime 6248/624A fixes + all applied cross-control 20-cell reflow repairs + exact short name!! actor-switch native restorations + selective Haman correction + native rehome of exact battle E51839/こ戦 family",
        "parent": {
            "path": str(MAIN.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(parent),
            "size": len(parent),
        },
        "candidate": {
            "path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(result),
            "size": len(result),
            "ws_checksum": f"{checksum:04X}",
        },
        "candidate_save": {
            "path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha(OUT_SAVE.read_bytes()),
            "size": OUT_SAVE.stat().st_size,
            "copied_from_live_at_build_time": True,
        },
        "scenario": {
            "rows": scenario_rows,
            "cross_record_reflow_removed": True,
            "applied_cross_control_reflows_found_in_parent": len(EXPECTED_CROSS_CONTROL_REFLOWS),
            "applied_cross_control_reflows_fixed": [
                {
                    "group_id": group_id,
                    "records": [f"{address:06X}" for address in pair],
                    "inter_record_gap_hex": parent[
                        scenario_terms[pair[0]] + 1:sb + pair[1]
                    ].hex().upper(),
                }
                for group_id, pair in EXPECTED_CROSS_CONTROL_REFLOWS.items()
            ],
            "short_name_bangbang_actor_switch_native_restored": [
                f"{address:06X}" for address in sorted(SHORT_EXCLAMATION_NATIVE_SIBLINGS)
            ],
            "624A07_source_native_two_token_restored": True,
            "624A_actor_control_changed": False,
            "Haman_global_dictionary_rewrite": False,
            "Haman_selective_records_fixed": ["624AF9", "624B30"],
            "preserved_control_ranges": {
                name: {
                    "start": f"{lo:06X}",
                    "end_exclusive": f"{hi:06X}",
                    "hex": preserved_before[name].hex().upper(),
                }
                for name, (lo, hi) in preserve_ranges.items()
            },
        },
        "battle": {
            "exact_e51839_kosen_family": len(kosen),
            "native_rehomed": len(battle_rows),
            "all_are_proven_visible_text_leads": True,
            "metadata_restored": 0,
            "rows": battle_rows,
            "interpretation": "runtime failure is ext3-at-record-head incompatibility/fallthrough in this battle route, not missing Japanese lead metadata; ordinary stock-native tokens remove E5 18 while preserving visible Korean and controls",
        },
        "stock_fragments": {
            "fragment_max_encoded_bytes": FRAGMENT_MAX_ENCODED,
            "unique_fragments": len(all_fragment_texts),
            "existing_exact_reused": len(existing),
            "strong_retired_allocated": len(allocated),
            "rows": stock_rows,
        },
        "checks": {
            "scenario_extents_preserved": True,
            "scenario_terminators_preserved": True,
            "adjacent_control_ranges_preserved": True,
            "6248B9_intermediate_dialogue_preserved": True,
            "624A13_followup_dialogue_preserved": True,
            "battle_extents_preserved": True,
            "battle_terminators_preserved": True,
            "battle_visible_text_preserved": True,
            "battle_false_lead_guard_respected": True,
            "battle_ext3_head_removed": True,
            "unexpected_diff_offsets_zero": True,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "ok=true "
        f"sha256={report['candidate']['sha256']} "
        f"checksum={report['candidate']['ws_checksum']} "
        f"scenario={len(scenario_rows)} battle={len(battle_rows)} "
        f"stock_allocated={len(allocated)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
