#!/usr/bin/env python3
"""Build a narrow candidate for three runtime-proven battle-voice regressions.

Current-main-only scope; this script never writes the live main TIP.

1. Uso 5D2514
   Runtime shows an extra Japanese-looking lead before the already-correct
   Korean continuation.  The live payload is ``9B | E5 18 47 4B | 01 01``.
   Move the existing ext3 token to the record start and fill the vacated tail
   byte with 01.  Record extent and terminator stay fixed.

2. Haman (Hyper) / battle placeholder sentinels
   An older broad pass translated 5D/5E ``不要`` / ``不用`` battle placeholders
   into the visible word ``미사용``.  The newer voice workflow explicitly treats
   those values as junk/sentinels and skips them.  Restore the original two-byte
   body for all 66 bank-5D/5E rows, preserving their existing leading byte and
   terminator.  Bank-5C UI/data placeholders are deliberately out of scope.

3. Neo Zeon officer on the Colony Laser
   The screen-proven two-record line is 5EB39A + 5EB3AA.  Re-express it without
   E5 18 using ordinary native stock tokens/direct glyphs:
       지구의　어리석은　자들을
       전멸시켜라！！
   The first line needs one tiny native helper phrase ``어리석은``.  Reuse the
   current-zero-reference, mutually-overlapping retired group 0C5E/0C5F/0C60,
   whose combined old phrase region is 5F:6B76-6B86.  Only token 0C5E becomes
   reachable; no stock pointer is changed.

The candidate keeps all record boundaries, all unrelated ROM bytes, and SaveRAM
byte-exact.  It also checks that the promoted five-bank E5 18 runtime is still
recognized by the offline decoder after the detector fix.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import detect_ext3_alias_page_count, load_ext_meta, make_dictionary_ext3
from build_broad_stage2_dialogue_voice_candidate import exact_slots, payload_at
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import (
    BANK_SIZE,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    dict_token_safe_in_zstring,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
PLACEHOLDER_CATALOG = ROOT / "data/broad_stage2_placeholder_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "battle_runtime_user_reported_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_runtime_user_reported_followup_candidate.sav"
OUT_REPORT = PATCH / "battle_runtime_user_reported_followup_candidate_report.json"

EXPECTED_MAIN_SHA = "dbc0e567fb3d7d3cd207a7e1dc6a737fbca5d131c299e8b0efc977daee546458"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

USO_ABS = 0x5D2514
USO_BEFORE = bytes.fromhex("9BE518474B0101")
USO_AFTER = bytes.fromhex("E518474B010101")
USO_TEXT = "않으면！！"

OFFICER_LINE1_ABS = 0x5EB39A
OFFICER_LINE2_ABS = 0x5EB3AA
OFFICER_LINE1_BEFORE = bytes.fromhex("8AE518497D01010101010101010101")
OFFICER_LINE2_BEFORE = bytes.fromhex("F08203F0A9F0A901")
OFFICER_LINE1_TEXT = "지구의　어리석은　자들을"
OFFICER_LINE2_TEXT = "전멸시켜라！！"
OFFICER_METADATA = bytes.fromhex("8A")

HELPER_TEXT = "어리석은"
HELPER_SLOT = 0x0C5E
HELPER_GROUP = (0x0C5E, 0x0C5F, 0x0C60)
HELPER_REGION_START = 0x6B76
HELPER_REGION_END = 0x6B86
EXPECTED_PLACEHOLDERS = 66


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def encode_chars(tbl: Tbl, text: str) -> bytes:
    return b"".join(tbl.encode_char(ch) for ch in text)


def native_stock_closure(dictionary: Dictionary, index: int) -> bool:
    """True iff a stock entry recursively uses only native bytes/stock tokens."""
    visiting: set[int] = set()
    memo: dict[int, bool] = {}

    def visit(current: int) -> bool:
        if current in memo:
            return memo[current]
        if current in visiting:
            return False
        visiting.add(current)
        try:
            raw = bytes(dictionary.raw_entry(current))
        except Exception:
            visiting.remove(current)
            memo[current] = False
            return False
        if b"\xE5\x18" in raw or b"\xE5\x19" in raw:
            visiting.remove(current)
            memo[current] = False
            return False
        cursor = 0
        ok = True
        while cursor < len(raw):
            lead = raw[cursor]
            if 0xF0 <= lead <= 0xFE:
                if cursor + 1 >= len(raw):
                    ok = False
                    break
                nested = ((lead - 0xF0) << 8) | raw[cursor + 1]
                cursor += 2
                if nested >= dictionary.stock_count or not visit(nested):
                    ok = False
                    break
            elif lead == 0xFF:
                # Keep this candidate on the ordinary stock renderer only.
                ok = False
                break
            elif 0xE0 <= lead <= 0xEF:
                if cursor + 1 >= len(raw):
                    ok = False
                    break
                cursor += 2
            else:
                cursor += 1
        visiting.remove(current)
        memo[current] = ok
        return ok

    return visit(index)


def exact_native_slot(dictionary: Dictionary, tbl: Tbl, text: str) -> int:
    hits = exact_slots(dictionary, tbl, {text}).get(text) or []
    hits = [
        index for index in hits
        if index < dictionary.stock_count
        and dict_token_safe_in_zstring(index)
        and native_stock_closure(dictionary, index)
    ]
    if not hits:
        raise BuildError(f"no native stock slot for {text!r}")
    return min(hits)


def prove_helper_region(parent: bytes, stock: Dictionary) -> dict[str, Any]:
    wanted = set(HELPER_GROUP)
    external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(stock, wanted=wanted, ext3_aware=True)
    raw_hits = _raw_pair_hits(parent, HELPER_GROUP)
    for index in HELPER_GROUP:
        if external.get(index) or nested.get(index) or raw_hits.get(index):
            raise BuildError(f"helper group slot still reachable: {index:04X}")

    extents: dict[int, tuple[int, int]] = {}
    for index in range(stock.stock_count):
        raw = bytes(stock.raw_entry(index))
        extents[index] = (stock.ptrs[index], stock.ptrs[index] + len(raw) + 1)

    expected = {
        0x0C5E: (0x6B76, 0x6B7C),
        0x0C5F: (0x6B7C, 0x6B82),
        0x0C60: (0x6B82, 0x6B86),
    }
    actual = {index: extents[index] for index in HELPER_GROUP}
    if actual != expected:
        raise BuildError(f"helper group extents drifted: {actual}")

    for index, (left, right) in extents.items():
        if index in HELPER_GROUP:
            continue
        if left < HELPER_REGION_END and HELPER_REGION_START < right:
            raise BuildError(
                f"helper region overlaps live/other stock slot {index:04X}: {left:04X}-{right:04X}"
            )
    return {
        "slots": [f"{index:04X}" for index in HELPER_GROUP],
        "region": [f"{HELPER_REGION_START:04X}", f"{HELPER_REGION_END:04X}"],
        "capacity": HELPER_REGION_END - HELPER_REGION_START,
        "external_refs_before": 0,
        "nested_refs_before": 0,
        "raw_pair_hits_before": 0,
    }


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"main SaveRAM size drifted: {len(save)}")
    if detect_ext3_alias_page_count(parent) != 5:
        raise BuildError("five-bank E5 18 runtime is not detected")

    sb = stock_base(parent)
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    alias = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    stock = Dictionary(parent)
    original_stock = Dictionary(original)

    # ------------------------------------------------------------------ Uso
    uso_payload, uso_term = payload_at(parent, USO_ABS)
    if uso_payload != USO_BEFORE:
        raise BuildError(f"Uso payload drifted: {uso_payload.hex().upper()}")
    uso_index = dict_index_from_ext3_token(*uso_payload[1:5])
    uso_ext3_text = clean(alias.expand(bytes(alias.raw_entry(uso_index)), tbl))
    if uso_ext3_text != USO_TEXT:
        raise BuildError(f"Uso ext3 text drifted: {uso_ext3_text!r}")

    # --------------------------------------------------------- helper proof
    helper_proof = prove_helper_region(parent, stock)
    helper_raw = encode_chars(tbl, HELPER_TEXT)
    if len(helper_raw) + 1 > HELPER_REGION_END - HELPER_REGION_START:
        raise BuildError("helper phrase does not fit reclaimed region")
    if b"\x00" in helper_raw or b"\xE5\x18" in helper_raw or b"\xE5\x19" in helper_raw:
        raise BuildError("helper phrase contains unsafe bytes")
    if token_from_dict_index(HELPER_SLOT) != bytes.fromhex("FC5E"):
        raise BuildError("helper token identity drifted")

    # Existing native building blocks.
    slot_earth = exact_native_slot(stock, tbl, "지구")
    slot_annihilate = exact_native_slot(stock, tbl, "전멸")
    slot_ra_bang = exact_native_slot(stock, tbl, "라！！")

    line1_body = (
        token_from_dict_index(slot_earth)
        + encode_chars(tbl, "의")
        + encode_chars(tbl, "　")
        + token_from_dict_index(HELPER_SLOT)
        + encode_chars(tbl, "　자들을")
    )
    line2_body = (
        token_from_dict_index(slot_annihilate)
        + encode_chars(tbl, "시켜")
        + token_from_dict_index(slot_ra_bang)
    )
    if len(line1_body) != 14:
        raise BuildError(f"officer line1 does not fit 14 bytes: {len(line1_body)}")
    if len(line2_body) != 8:
        raise BuildError(f"officer line2 does not fit 8 bytes: {len(line2_body)}")

    officer1_before, officer1_term = payload_at(parent, OFFICER_LINE1_ABS)
    officer2_before, officer2_term = payload_at(parent, OFFICER_LINE2_ABS)
    if officer1_before != OFFICER_LINE1_BEFORE:
        raise BuildError(f"officer line1 drifted: {officer1_before.hex().upper()}")
    if officer2_before != OFFICER_LINE2_BEFORE:
        raise BuildError(f"officer line2 drifted: {officer2_before.hex().upper()}")

    # ------------------------------------------- battle placeholder sentinels
    placeholder_doc = json.loads(PLACEHOLDER_CATALOG.read_text(encoding="utf-8"))
    placeholder_rows = [
        dict(row)
        for row in (placeholder_doc.get("lines") or [])
        if str(row.get("abs") or "").upper().startswith(("5D", "5E"))
    ]
    if len(placeholder_rows) != EXPECTED_PLACEHOLDERS:
        raise BuildError(f"battle placeholder population drifted: {len(placeholder_rows)}")

    placeholder_plan: list[dict[str, Any]] = []
    for item in placeholder_rows:
        logical = int(str(item["abs"]), 16)
        prefix = bytes.fromhex(str(item.get("prefix_hex") or ""))
        original_body = bytes.fromhex(str(item.get("body_hex") or ""))
        if len(original_body) != 2 or str(item.get("jp")) not in {"不要", "不用"}:
            raise BuildError(f"invalid placeholder row at {logical:06X}")
        current_payload, current_term = payload_at(parent, logical)
        original_payload, original_term = payload_at(original, logical)
        if len(current_payload) != len(prefix) + 2 or not current_payload.startswith(prefix):
            raise BuildError(f"placeholder current shape drifted at {logical:06X}")
        current_body = current_payload[len(prefix):]
        current_text = clean(stock.expand(current_body, tbl))
        if current_text != "미사용":
            raise BuildError(f"placeholder no longer renders 미사용 at {logical:06X}: {current_text!r}")
        if original_payload != prefix + original_body:
            raise BuildError(
                f"placeholder original shape drifted at {logical:06X}: "
                f"{original_payload.hex().upper()} != {(prefix + original_body).hex().upper()}"
            )
        if current_term - sb != original_term:
            raise BuildError(f"placeholder logical terminator drifted at {logical:06X}")
        placeholder_plan.append({
            "abs": f"{logical:06X}",
            "logical": logical,
            "prefix": prefix,
            "before_body": current_body,
            "original_body": original_body,
            "jp": str(item["jp"]),
            "term": current_term,
        })

    haman_row = next((row for row in placeholder_plan if row["abs"] == "5DB482"), None)
    if haman_row is None:
        raise BuildError("screen-proven Haman placeholder 5DB482 is missing")
    if (haman_row["prefix"] + haman_row["original_body"]).hex().upper() != "577981":
        raise BuildError("Haman original sentinel bytes drifted")

    # ------------------------------------------------------------- apply ROM
    candidate = bytearray(parent)

    # Native helper in current-zero-reference stock phrase bytes.  No pointer
    # needs to move: 0C5E already points at 5F:6B76.
    stock_file = sb + SEG_DICT * BANK_SIZE
    helper_at = stock_file + HELPER_REGION_START
    candidate[helper_at:helper_at + len(helper_raw)] = helper_raw
    candidate[helper_at + len(helper_raw)] = 0

    # Uso false lead removal.
    uso_at = sb + USO_ABS
    candidate[uso_at:uso_at + len(USO_BEFORE)] = USO_AFTER
    if candidate[uso_term] != 0:
        raise BuildError("Uso terminator moved")

    # Neo Zeon officer line pair. Preserve 8A metadata only on first row.
    officer1_after = OFFICER_METADATA + line1_body
    officer2_after = line2_body
    if len(officer1_after) != len(officer1_before) or len(officer2_after) != len(officer2_before):
        raise BuildError("officer record extent changed")
    officer1_at = sb + OFFICER_LINE1_ABS
    officer2_at = sb + OFFICER_LINE2_ABS
    candidate[officer1_at:officer1_at + len(officer1_after)] = officer1_after
    candidate[officer2_at:officer2_at + len(officer2_after)] = officer2_after
    if candidate[officer1_term] != 0 or candidate[officer2_term] != 0:
        raise BuildError("officer terminator moved")

    # Restore original sentinel bodies only; prefix is untouched.
    for row in placeholder_plan:
        body_at = sb + int(row["logical"]) + len(row["prefix"])
        candidate[body_at:body_at + 2] = row["original_body"]
        if candidate[int(row["term"])] != 0:
            raise BuildError(f"placeholder terminator moved at {row['abs']}")

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)

    # --------------------------------------------------------- verification
    if detect_ext3_alias_page_count(result) != 5:
        raise BuildError("candidate lost five-bank E5 18 runtime")
    result_alias = make_dictionary_ext3(result, ext_meta, ext3_meta)
    result_stock = Dictionary(result)

    helper_render = clean(result_stock.expand(token_from_dict_index(HELPER_SLOT), tbl))
    if helper_render != HELPER_TEXT:
        raise BuildError(f"helper render mismatch: {helper_render!r}")

    uso_live, uso_term_after = payload_at(result, USO_ABS)
    uso_render = clean(result_alias.expand(uso_live, tbl))
    if uso_live != USO_AFTER or uso_render != USO_TEXT or uso_term_after != uso_term:
        raise BuildError(
            f"Uso verification failed: {uso_live.hex().upper()} {uso_render!r} term={uso_term_after}"
        )

    officer1_live, officer1_term_after = payload_at(result, OFFICER_LINE1_ABS)
    officer2_live, officer2_term_after = payload_at(result, OFFICER_LINE2_ABS)
    if officer1_live != officer1_after or officer2_live != officer2_after:
        raise BuildError("officer payload verification mismatch")
    officer1_render = clean(result_stock.expand(officer1_live[1:], tbl))
    officer2_render = clean(result_stock.expand(officer2_live, tbl))
    if officer1_render != OFFICER_LINE1_TEXT or officer2_render != OFFICER_LINE2_TEXT:
        raise BuildError(
            f"officer render mismatch: {officer1_render!r} / {officer2_render!r}"
        )
    if officer1_term_after != officer1_term or officer2_term_after != officer2_term:
        raise BuildError("officer boundary verification mismatch")
    if b"\xE5\x18" in officer1_live or b"\xE5\x18" in officer2_live:
        raise BuildError("officer screen-proven pair still contains E5 18")

    placeholder_failures: list[str] = []
    for row in placeholder_plan:
        live, term = payload_at(result, int(row["logical"]))
        prefix = row["prefix"]
        if live != prefix + row["original_body"] or term != int(row["term"]):
            placeholder_failures.append(row["abs"])
    if placeholder_failures:
        raise BuildError(f"placeholder restore verification failed: {placeholder_failures[:16]}")

    # Helper token must be newly reachable only from the intended officer line.
    helper_refs = external_occurrence_map(result, ext3_aware=True, wanted={HELPER_SLOT}).get(HELPER_SLOT) or []
    helper_ref_addrs = sorted({int(str(ref["record_abs"]), 16) for ref in helper_refs})
    if helper_ref_addrs != [OFFICER_LINE1_ABS]:
        raise BuildError(
            f"helper token escaped intended record: {[f'{value:06X}' for value in helper_ref_addrs]}"
        )
    helper_nested = nested_occurrence_map(result_stock, wanted={HELPER_SLOT}, ext3_aware=True).get(HELPER_SLOT) or []
    if helper_nested:
        raise BuildError("helper token became nested in another dictionary entry")

    # Diff allow-list.
    allowed: list[tuple[int, int]] = [
        (helper_at, helper_at + len(helper_raw) + 1),
        (uso_at, uso_at + len(USO_BEFORE)),
        (officer1_at, officer1_at + len(officer1_after)),
        (officer2_at, officer2_at + len(officer2_after)),
        (len(result) - 2, len(result)),
    ]
    for row in placeholder_plan:
        body_at = sb + int(row["logical"]) + len(row["prefix"])
        allowed.append((body_at, body_at + 2))

    changed = [index for index, (before, after) in enumerate(zip(parent, result)) if before != after]
    unaccounted = [
        index
        for index in changed
        if not any(left <= index < right for left, right in allowed)
    ]
    if unaccounted:
        raise BuildError(
            f"unaccounted changed bytes: {len(unaccounted)} "
            f"{[f'{value:08X}' for value in unaccounted[:32]]}"
        )
    if MAIN.read_bytes() != parent or MAIN_SAVE.read_bytes() != save:
        raise BuildError("main TIP or main SaveRAM changed during candidate build")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != save:
        raise BuildError("candidate SaveRAM is not byte-identical to main SaveRAM")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_runtime_user_reported_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_pending_runtime_test",
        "parent": {
            "path": "out/patch/monoeye_ko_expanded.wsc",
            "sha256": sha(parent),
            "size": len(parent),
        },
        "candidate": {
            "path": "out/patch/battle_runtime_user_reported_followup_candidate.wsc",
            "sha256": sha(result),
            "size": len(result),
            "checksum": f"{checksum:04X}",
        },
        "candidate_save": {
            "path": "sram/battle_runtime_user_reported_followup_candidate.sav",
            "sha256": sha(save),
            "size": len(save),
        },
        "diagnosis": {
            "uso": {
                "abs": f"{USO_ABS:06X}",
                "before_hex": uso_payload.hex().upper(),
                "after_hex": USO_AFTER.hex().upper(),
                "false_visible_lead_hex": "9B",
                "existing_ext3_index": f"{uso_index:05X}",
                "existing_korean": uso_ext3_text,
                "cause": "runtime-visible source lead was preserved before a correct E5 18 Korean body",
                "pointer_change": False,
            },
            "haman_hyper": {
                "screen_proven_abs": "5DB482",
                "original_hex": payload_at(original, 0x5DB482)[0].hex().upper(),
                "main_before_hex": payload_at(parent, 0x5DB482)[0].hex().upper(),
                "candidate_hex": payload_at(result, 0x5DB482)[0].hex().upper(),
                "cause": "older broad placeholder localization translated battle sentinel 不要 into visible 미사용",
                "pointer_change": False,
                "restored_family": "all bank5D/5E 不要/不用 battle placeholders",
            },
            "neo_zeon_officer": {
                "screen_pair": [f"{OFFICER_LINE1_ABS:06X}", f"{OFFICER_LINE2_ABS:06X}"],
                "original_japanese": [
                    "地球にしがみつく愚か者どもを",
                    "一掃するのだっ！！",
                ],
                "after_korean": [OFFICER_LINE1_TEXT, OFFICER_LINE2_TEXT],
                "line1_before_hex": officer1_before.hex().upper(),
                "line1_after_hex": officer1_after.hex().upper(),
                "line2_before_hex": officer2_before.hex().upper(),
                "line2_after_hex": officer2_after.hex().upper(),
                "e518_removed_from_screen_pair": True,
                "metadata_8a_preserved": officer1_after.startswith(OFFICER_METADATA),
                "pointer_change": False,
            },
            "offline_alias_detector": {
                "before_bug": "five-bank leaf was falsely rejected when the following three cave bytes were reused",
                "candidate_detected_pages": detect_ext3_alias_page_count(result),
                "runtime_leaf_rom_bytes_changed": False,
            },
        },
        "native_helper": {
            **helper_proof,
            "selected_slot": f"{HELPER_SLOT:04X}",
            "token_hex": token_from_dict_index(HELPER_SLOT).hex().upper(),
            "text": HELPER_TEXT,
            "raw_hex": helper_raw.hex().upper(),
            "bytes_written_in_region": len(helper_raw) + 1,
            "pointer_changed": False,
            "refs_after": [f"{value:06X}" for value in helper_ref_addrs],
        },
        "native_existing_slots": {
            "지구": f"{slot_earth:04X}",
            "전멸": f"{slot_annihilate:04X}",
            "라！！": f"{slot_ra_bang:04X}",
        },
        "counts": {
            "uso_records": 1,
            "battle_placeholder_sentinels_restored": len(placeholder_plan),
            "neo_zeon_officer_records": 2,
            "new_helper_slots_made_reachable": 1,
            "stock_pointer_changes": 0,
            "changed_bytes": len(changed),
            "unaccounted_changed_bytes": len(unaccounted),
        },
        "verification": {
            "five_bank_alias_runtime_detected": detect_ext3_alias_page_count(result) == 5,
            "uso_text": uso_render,
            "officer_line1_text": officer1_render,
            "officer_line2_text": officer2_render,
            "officer_pair_e518_remaining": 0,
            "placeholder_restore_failures": 0,
            "record_terminators_preserved": True,
            "main_unchanged": True,
            "save_unchanged": True,
            "unaccounted_changed_bytes": len(unaccounted),
        },
        "runtime_test_points": [
            "웃소: '않으면！！' 앞에 일본어/한자 글리프가 더 이상 붙지 않는지",
            "하만(하이퍼) 피격: '미사용'이 더 이상 출력되지 않고 전투가 정상 진행되는지",
            "콜로니 레이저 네오 지온 사관: '지구의 어리석은 자들을 / 전멸시켜라！！'가 한글로 출력되는지",
            "세 화면 직후 공격/피격/회피 및 전투 종료까지 초상/대사/진행이 정상인지",
        ],
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
