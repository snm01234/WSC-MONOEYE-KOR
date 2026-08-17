#!/usr/bin/env python3
"""Build the second user-reported battle-runtime follow-up candidate.

The parent is always the live main TIP; the previous experimental candidate is
not chained.

Fixes:
* Uso duplicated continuation records 5D2514 and 5E595C: remove runtime-visible
  9B=来 in front of the already-correct ``않으면！！`` ext3 body.
* Preserve the accepted Haman(Hyper) fix by restoring all 66 bank-5D/5E
  不要/不用 battle sentinel bodies from Original (undo visible ``미사용``).
* Rebind all 75 canonical bank-5F live battle voices from
  data/bank5f_runtime_battle_voice_ko.json.  Current main has 73 catalog
  mismatches, while the remaining two short rows use E5 19 compact3 and are
  runtime-broken in the user's Colony Laser captures.  All 73 long rows are
  moved to newly allocated true-free slots in one non-alias standard ext3 page
  (page 9 / physical bank 1A).  The two 3-byte bodies use private native stock
  entries instead of compact3.

No live main TIP or SaveRAM write is allowed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import detect_ext3_alias_page_count, load_ext_meta, make_dictionary_ext3
from build_broad_stage2_dialogue_voice_candidate import payload_at
from hangul_marker import marker_code
from mixed_residual_reference_union import (
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (
    BANK_SIZE,
    COMPACT3_INDEX_BASE,
    COMPACT3_INDEX_END,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BANK5F_SPEC = ROOT / "data/bank5f_runtime_battle_voice_ko.json"
PLACEHOLDER_CATALOG = ROOT / "data/broad_stage2_placeholder_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
JP_TBL_PATH = ROOT / "data/monoeye_verified.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "battle_runtime_user_reported_followup_v2_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_runtime_user_reported_followup_v2_candidate.sav"
OUT_REPORT = PATCH / "battle_runtime_user_reported_followup_v2_candidate_report.json"

EXPECTED_MAIN_SHA = "dbc0e567fb3d7d3cd207a7e1dc6a737fbca5d131c299e8b0efc977daee546458"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
BANK5F_PREFIXES = {0xA1, 0x9B, 0x8A}
EXPECTED_BANK5F = 75
EXPECTED_PARENT_CATALOG_MISMATCH = 73
EXPECTED_PLACEHOLDERS = 66

USO_ADDRESSES = (0x5D2514, 0x5E595C)
USO_BEFORE = bytes.fromhex("9BE518474B0101")
USO_AFTER = bytes.fromhex("E518474B010101")
USO_TEXT = "않으면！！"

# Keep all new long bank5F phrases on one standard, non-alias ext3 page.
# Five-bank aliasing affects only raw pages 0..4/local>=0600.  Page 9 is
# therefore invariant under that rule and currently has ample free room.
DEDICATED_EXT3_PAGE = 9
DEDICATED_EXT3_SEG = EXP3_SEG0 + DEDICATED_EXT3_PAGE  # 0x1A

# Two short bank5F bodies cannot hold a four-byte E5 18 token.  Their current
# E5 19 compact3 form is runtime-broken, so use two current-zero-reference stock
# slots whose old phrase extents are exclusive and large enough.
SHORT_NATIVE = {
    # 0C94 owns an exclusive six-byte dead region: exactly enough for
    # EC8D + 큭 + ！ + NUL.
    "5F044F": {"text": "큭！", "slot": 0x0C94, "pointer": 0x6C99, "extent_end": 0x6C9F},
    # 0C5E/0C5F/0C60 are all current-zero-reference and jointly own the
    # contiguous 6B76-6B86 region.  0C5E may therefore span eight bytes across
    # the nominal dead 0C5F boundary without touching any live consumer.
    "5F047D": {"text": "젠장！", "slot": 0x0C5E, "pointer": 0x6B76, "extent_end": 0x6B86},
}
SHORT_GROUP = (0x0C5E, 0x0C5F, 0x0C60, 0x0C94)
SHORT_REGIONS = ((0x6B76, 0x6B86), (0x6C99, 0x6C9F))


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
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def encode_direct(tbl: Tbl, text: str) -> bytes:
    return b"".join(tbl.encode_char(ch) for ch in text)


def encode_ext3_phrase(tbl: Tbl, text: str) -> tuple[str, bytes]:
    norm = normalize_ko_text(text)
    raw = try_encode_ko_text(
        norm,
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if raw is None or not raw or b"\x00" in raw:
        raise BuildError(f"cannot encode ext3 phrase: {text!r}")
    return norm, bytes(raw)


def prove_short_stock_region(parent: bytes, stock: Dictionary) -> dict[str, Any]:
    wanted = set(SHORT_GROUP)
    external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(stock, wanted=wanted, ext3_aware=True)
    raw_hits = _raw_pair_hits(parent, SHORT_GROUP)
    for index in SHORT_GROUP:
        if external.get(index) or nested.get(index) or raw_hits.get(index):
            raise BuildError(f"short native stock slot still reachable: {index:04X}")

    extents: dict[int, tuple[int, int]] = {}
    for index in range(stock.stock_count):
        raw = bytes(stock.raw_entry(index))
        extents[index] = (stock.ptrs[index], stock.ptrs[index] + len(raw) + 1)
    expected = {
        0x0C5E: (0x6B76, 0x6B7C),
        0x0C5F: (0x6B7C, 0x6B82),
        0x0C60: (0x6B82, 0x6B86),
        0x0C94: (0x6C99, 0x6C9F),
    }
    actual = {index: extents[index] for index in SHORT_GROUP}
    if actual != expected:
        raise BuildError(f"short stock extent drifted: {actual}")
    for index, (left, right) in extents.items():
        if index in SHORT_GROUP:
            continue
        for region_left, region_right in SHORT_REGIONS:
            if left < region_right and region_left < right:
                raise BuildError(f"short stock region overlaps slot {index:04X}: {left:04X}-{right:04X}")
    return {
        "slots": [f"{i:04X}" for i in SHORT_GROUP],
        "regions": [[f"{a:04X}", f"{b:04X}"] for a, b in SHORT_REGIONS],
        "external_refs_before": 0,
        "nested_refs_before": 0,
        "raw_pair_hits_before": 0,
    }


def original_bank5f_source(
    original: bytes,
    address: int,
    jp_dictionary: Dictionary,
    jp_tbl: Tbl,
) -> tuple[str, bytes]:
    payload, _term = payload_at(original, address)
    prefix = payload[:1] if payload and payload[0] in BANK5F_PREFIXES else b""
    body = payload[len(prefix):]
    return jp_dictionary.expand(body, jp_tbl), prefix


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
    jp_tbl = Tbl.load(JP_TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta.get("num_banks") or 16)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    stock = Dictionary(parent)
    jp_dictionary = Dictionary(original)

    # --------------------------------------------------------------- Uso x2
    uso_rows: list[dict[str, Any]] = []
    for logical in USO_ADDRESSES:
        payload, term = payload_at(parent, logical)
        if payload != USO_BEFORE:
            raise BuildError(f"Uso duplicate payload drifted at {logical:06X}: {payload.hex().upper()}")
        index = dict_index_from_ext3_token(*payload[1:5])
        text = clean(d_parent.expand(bytes(d_parent.raw_entry(index)), tbl))
        if text != USO_TEXT:
            raise BuildError(f"Uso ext3 phrase drifted at {logical:06X}: {text!r}")
        uso_rows.append({"abs": f"{logical:06X}", "logical": logical, "term": term})

    # ----------------------------------------------------- Haman sentinel x66
    placeholder_doc = json.loads(PLACEHOLDER_CATALOG.read_text(encoding="utf-8"))
    placeholder_items = [
        dict(row)
        for row in (placeholder_doc.get("lines") or [])
        if str(row.get("abs") or "").upper().startswith(("5D", "5E"))
    ]
    if len(placeholder_items) != EXPECTED_PLACEHOLDERS:
        raise BuildError(f"battle placeholder population drifted: {len(placeholder_items)}")
    placeholder_plan: list[dict[str, Any]] = []
    for item in placeholder_items:
        logical = int(str(item["abs"]), 16)
        prefix = bytes.fromhex(str(item.get("prefix_hex") or ""))
        original_body = bytes.fromhex(str(item.get("body_hex") or ""))
        if len(original_body) != 2 or str(item.get("jp")) not in {"不要", "不用"}:
            raise BuildError(f"invalid placeholder catalog row at {logical:06X}")
        current_payload, current_term = payload_at(parent, logical)
        source_payload, source_term = payload_at(original, logical)
        if len(current_payload) != len(prefix) + 2 or not current_payload.startswith(prefix):
            raise BuildError(f"placeholder current shape drifted at {logical:06X}")
        if clean(stock.expand(current_payload[len(prefix):], tbl)) != "미사용":
            raise BuildError(f"placeholder current text is not 미사용 at {logical:06X}")
        if source_payload != prefix + original_body or source_term != current_term - sb:
            raise BuildError(f"placeholder Original binding drifted at {logical:06X}")
        placeholder_plan.append({
            "abs": f"{logical:06X}",
            "logical": logical,
            "prefix": prefix,
            "original_body": original_body,
            "term": current_term,
        })
    if not any(row["abs"] == "5DB482" for row in placeholder_plan):
        raise BuildError("screen-proven Haman 5DB482 sentinel is absent")

    # ------------------------------------------------------- bank5F canonical
    spec_doc = json.loads(BANK5F_SPEC.read_text(encoding="utf-8"))
    spec = {str(k).upper(): dict(v) for k, v in (spec_doc.get("targets") or {}).items()}
    if len(spec) != EXPECTED_BANK5F:
        raise BuildError(f"bank5F canonical population drifted: {len(spec)}")

    prepared: list[dict[str, Any]] = []
    mismatch_before: list[str] = []
    compact_before: list[str] = []
    ext3_phrases: dict[bytes, dict[str, Any]] = {}
    for address, item in sorted(spec.items()):
        logical = int(address, 16)
        source_text, source_prefix = original_bank5f_source(original, logical, jp_dictionary, jp_tbl)
        if source_text != str(item["source_jp"]):
            raise BuildError(f"bank5F Original source mismatch at {address}: {source_text!r}")

        payload, term = payload_at(parent, logical)
        if source_prefix:
            if not payload.startswith(source_prefix):
                raise BuildError(f"bank5F prefix disappeared at {address}")
            prefix = source_prefix
        else:
            prefix = b""
        body = payload[len(prefix):]
        desired, encoded = encode_ext3_phrase(tbl, str(item["after"]))
        rendered_before = clean(d_parent.expand(body, tbl))
        if rendered_before != desired:
            mismatch_before.append(address)
        if body.startswith(b"\xE5\x19"):
            compact_before.append(address)

        strategy = "native_stock" if address in SHORT_NATIVE else "ext3_page9"
        if strategy == "native_stock":
            if len(body) != 3 or desired != SHORT_NATIVE[address]["text"]:
                raise BuildError(f"short bank5F binding drifted at {address}")
        else:
            if len(body) < 4:
                raise BuildError(f"bank5F ext3 body too short at {address}: {len(body)}")
            ext3_phrases.setdefault(encoded, {"first_abs": address, "text": desired})
        prepared.append({
            "abs": address,
            "logical": logical,
            "prefix": prefix,
            "prefix_hex": prefix.hex().upper(),
            "body_len": len(body),
            "payload_len": len(payload),
            "term": term,
            "before_payload_hex": payload.hex().upper(),
            "before_render": rendered_before,
            "desired": desired,
            "encoded": encoded,
            "strategy": strategy,
        })

    if len(mismatch_before) != EXPECTED_PARENT_CATALOG_MISMATCH:
        raise BuildError(f"bank5F parent mismatch population drifted: {len(mismatch_before)}")
    if sorted(compact_before) != sorted(SHORT_NATIVE):
        raise BuildError(f"bank5F compact3 population drifted: {compact_before}")
    if len(ext3_phrases) != 71:
        raise BuildError(f"bank5F unique long phrase count drifted: {len(ext3_phrases)}")

    # ---------------------------------------------------- safe slot allocation
    short_proof = prove_short_stock_region(parent, stock)
    for address, info in SHORT_NATIVE.items():
        norm, raw = encode_ext3_phrase(tbl, str(info["text"]))
        if norm != str(info["text"]):
            raise BuildError(f"short native normalization drifted at {address}: {norm!r}")
        capacity = int(info["extent_end"]) - int(info["pointer"])
        if len(raw) + 1 > capacity or b"\x00" in raw or not raw.startswith(bytes.fromhex("EC8D")):
            raise BuildError(f"short native phrase does not fit marker contract {address}: {len(raw)+1}>{capacity}")
        info["raw"] = raw

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    if int(inventory.ext3_bank_room.get(DEDICATED_EXT3_PAGE, 0)) < sum(len(p) + 1 for p in ext3_phrases):
        raise BuildError("dedicated bank1A phrase room insufficient")

    compact_reserved = set(range(COMPACT3_INDEX_BASE, COMPACT3_INDEX_END + 1))
    page9_free: list[int] = []
    for index in inventory.ext3_free:
        if index in compact_reserved:
            continue
        seg, _local = bank_local_for_index(index)
        if seg == DEDICATED_EXT3_SEG:
            page9_free.append(int(index))
    page9_free.sort()
    if len(page9_free) < len(ext3_phrases):
        raise BuildError(f"dedicated bank1A free slot shortage: {len(page9_free)}")

    encoded_to_slot: dict[bytes, int] = {}
    slot_payload: dict[int, bytes] = {}
    for index, (encoded, sample) in zip(
        page9_free,
        sorted(ext3_phrases.items(), key=lambda kv: kv[1]["first_abs"]),
    ):
        if not union.is_true_free(index):
            raise BuildError(f"selected ext3 slot not true-free: {index:05X}")
        encoded_to_slot[encoded] = index
        slot_payload[index] = encoded
        sample["slot"] = index

    for row in prepared:
        if row["strategy"] == "ext3_page9":
            row["slot"] = encoded_to_slot[row["encoded"]]

    # --------------------------------------------------------------- apply
    candidate = bytearray(parent)

    # Two native stock phrases; stock pointers are not moved.
    stock_file = sb + SEG_DICT * BANK_SIZE
    for address, info in SHORT_NATIVE.items():
        at = stock_file + int(info["pointer"])
        raw = bytes(info["raw"])
        candidate[at:at + len(raw)] = raw
        candidate[at + len(raw)] = 0

    # Restore all battle placeholder sentinel bodies.
    for row in placeholder_plan:
        body_at = sb + int(row["logical"]) + len(row["prefix"])
        candidate[body_at:body_at + 2] = row["original_body"]
        if candidate[int(row["term"])] != 0:
            raise BuildError(f"placeholder terminator moved at {row['abs']}")

    # Fix both duplicated Uso continuation records.
    for row in uso_rows:
        at = sb + int(row["logical"])
        candidate[at:at + len(USO_BEFORE)] = USO_AFTER
        if candidate[int(row["term"])] != 0:
            raise BuildError(f"Uso terminator moved at {row['abs']}")

    # Allocate new bank5F long phrases only in standard ext3 bank 1A.
    ext3_write, ext3_guard = write_ext3_slots_guarded(
        candidate,
        slot_payload,
        union=union,
        num_banks=num_banks,
        justification="2026-08-16 runtime captures: bank5F private-slot rebinding; dedicated non-alias page9",
    )
    if not ext3_guard.ok:
        raise BuildError(f"ext3 guard failed: {ext3_guard.as_dict()}")
    if int(ext3_write.get("written") or 0) != len(slot_payload):
        raise BuildError(f"ext3 write count mismatch: {ext3_write}")
    if set((ext3_write.get("by_bank") or {}).keys()) != {f"{DEDICATED_EXT3_SEG:02X}"}:
        raise BuildError(f"ext3 writer escaped dedicated bank1A: {ext3_write}")

    # Rebind all 75 records.
    for row in prepared:
        body_len = int(row["body_len"])
        if row["strategy"] == "native_stock":
            slot = int(SHORT_NATIVE[row["abs"]]["slot"])
            token = token_from_dict_index(slot)
            new_body = token + b"\x01" * (body_len - len(token))
            row["slot"] = slot
        else:
            slot = int(row["slot"])
            seg, _local = bank_local_for_index(slot)
            if seg != DEDICATED_EXT3_SEG:
                raise BuildError(f"bank5F slot not in dedicated bank at {row['abs']}: {slot:05X}")
            token = token_from_ext3_index(slot, num_banks=num_banks)
            new_body = token + b"\x01" * (body_len - 4)
        if len(new_body) != body_len:
            raise BuildError(f"bank5F body extent changed at {row['abs']}")
        start = sb + int(row["logical"]) + len(row["prefix"])
        candidate[start:start + body_len] = new_body
        if candidate[int(row["term"])] != 0:
            raise BuildError(f"bank5F terminator moved at {row['abs']}")
        row["after_body_hex"] = new_body.hex().upper()
        row["slot_hex"] = f"{slot:05X}" if row["strategy"] == "ext3_page9" else f"{slot:04X}"

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)

    # ---------------------------------------------------------- verification
    if detect_ext3_alias_page_count(result) != 5:
        raise BuildError("candidate lost five-bank alias runtime")
    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)
    result_stock = Dictionary(result)

    # Uso duplicates both render without the visible 9B lead.
    for row in uso_rows:
        live, term = payload_at(result, int(row["logical"]))
        rendered = clean(d_result.expand(live, tbl))
        if live != USO_AFTER or rendered != USO_TEXT or term != int(row["term"]):
            raise BuildError(f"Uso verification failed at {row['abs']}: {live.hex().upper()} {rendered!r}")

    # Haman/placeholder family restored byte-exact to Original bodies.
    bad_placeholders: list[str] = []
    for row in placeholder_plan:
        live, term = payload_at(result, int(row["logical"]))
        if live != row["prefix"] + row["original_body"] or term != int(row["term"]):
            bad_placeholders.append(row["abs"])
    if bad_placeholders:
        raise BuildError(f"placeholder restore failures: {bad_placeholders[:20]}")

    # Every canonical bank5F record must now render exactly, preserve its prefix
    # and terminator, and contain no compact3 portal.
    bank5f_failures: list[dict[str, str]] = []
    for row in prepared:
        live, term = payload_at(result, int(row["logical"]))
        prefix = bytes(row["prefix"])
        if prefix and not live.startswith(prefix):
            bank5f_failures.append({"abs": row["abs"], "reason": "prefix_changed"})
            continue
        body = live[len(prefix):]
        rendered = clean(d_result.expand(body, tbl))
        reasons: list[str] = []
        if rendered != row["desired"]:
            reasons.append(f"render:{rendered!r}")
        if term != int(row["term"]):
            reasons.append("terminator_changed")
        if b"\xE5\x19" in body:
            reasons.append("compact3_remaining")
        if row["strategy"] == "ext3_page9":
            if not body.startswith(b"\xE5\x18"):
                reasons.append("ext3_missing")
            else:
                raw = (body[2] << 8) | body[3]
                if (raw >> 12) != DEDICATED_EXT3_PAGE:
                    reasons.append(f"wrong_ext3_page:{raw:04X}")
        if reasons:
            bank5f_failures.append({"abs": row["abs"], "reason": ",".join(reasons)})
        row["rendered_after"] = rendered
    if bank5f_failures:
        raise BuildError(f"bank5F post verification failed: {bank5f_failures[:20]}")

    # Short native slots are reachable only by their intended bank5F z-string
    # records.  Raw pair scans over the whole ROM are not meaningful here because
    # identical two-byte values occur coincidentally in code/graphics.  Instead
    # inspect the parser-visible body start of all 75 canonical bank5F records,
    # plus the complete stock dictionary for nested parents.
    short_ref_report: dict[str, list[str]] = {}
    for address, info in SHORT_NATIVE.items():
        slot = int(info["slot"])
        token = token_from_dict_index(slot)
        uses: list[str] = []
        for check in prepared:
            live, _term = payload_at(result, int(check["logical"]))
            prefix = bytes(check["prefix"])
            body = live[len(prefix):]
            if body.startswith(token):
                uses.append(check["abs"])
        if uses != [address]:
            raise BuildError(f"short native token escaped {slot:04X}: {uses}")
        if nested_occurrence_map(result_stock, wanted={slot}, ext3_aware=True).get(slot):
            raise BuildError(f"short native slot nested unexpectedly: {slot:04X}")
        short_ref_report[f"{slot:04X}"] = uses

    # The generic reference-union script walker intentionally does not include
    # the bank5F tagged-voice block.  Verify the new ext3 consumer graph directly
    # from all 75 parser-visible bank5F bodies instead, and independently reject
    # any nested dictionary parent.
    expected_by_slot: dict[int, set[int]] = defaultdict(set)
    for row in prepared:
        if row["strategy"] == "ext3_page9":
            expected_by_slot[int(row["slot"])].add(int(row["logical"]))
    actual_by_slot: dict[int, set[int]] = defaultdict(set)
    for row in prepared:
        live, _term = payload_at(result, int(row["logical"]))
        body = live[len(bytes(row["prefix"])):]
        if body.startswith(b"\xE5\x18"):
            slot = dict_index_from_ext3_token(*body[:4])
            if slot in expected_by_slot:
                actual_by_slot[slot].add(int(row["logical"]))
    nested_selected = nested_occurrence_map(
        result_stock,
        wanted=set(expected_by_slot),
        ext3_aware=True,
    )
    slot_consumer_failures: list[dict[str, Any]] = []
    for slot, expected_addrs in sorted(expected_by_slot.items()):
        consumers = actual_by_slot.get(slot, set())
        parents = nested_selected.get(slot) or []
        if consumers != expected_addrs or parents:
            slot_consumer_failures.append({
                "slot": f"{slot:05X}",
                "expected": [f"{a:06X}" for a in sorted(expected_addrs)],
                "actual": [f"{a:06X}" for a in sorted(consumers)],
                "parents": [str(p) for p in parents[:8]],
            })
    if slot_consumer_failures:
        raise BuildError(f"new ext3 consumer verification failed: {slot_consumer_failures[:10]}")

    # ------------------------------------------------------------ diff guard
    allowed: list[tuple[int, int]] = [
        (DEDICATED_EXT3_SEG * BANK_SIZE, (DEDICATED_EXT3_SEG + 1) * BANK_SIZE),
        *[(stock_file + left, stock_file + right) for left, right in SHORT_REGIONS],
        (len(result) - 2, len(result)),
    ]
    for row in uso_rows:
        at = sb + int(row["logical"])
        allowed.append((at, at + len(USO_BEFORE)))
    for row in placeholder_plan:
        at = sb + int(row["logical"]) + len(row["prefix"])
        allowed.append((at, at + 2))
    for row in prepared:
        at = sb + int(row["logical"]) + len(row["prefix"])
        allowed.append((at, at + int(row["body_len"])))

    changed = [i for i, (a, b) in enumerate(zip(parent, result)) if a != b]
    unaccounted = [i for i in changed if not any(left <= i < right for left, right in allowed)]
    if unaccounted:
        raise BuildError(f"unaccounted changed bytes: {len(unaccounted)} {[f'{i:08X}' for i in unaccounted[:32]]}")
    if MAIN.read_bytes() != parent or MAIN_SAVE.read_bytes() != save:
        raise BuildError("main TIP or main SaveRAM changed during build")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != save:
        raise BuildError("candidate SaveRAM differs from current main SaveRAM")

    report = {
        "schema_version": 2,
        "generated_by": "tools/build_battle_runtime_user_reported_followup_v2_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_pending_runtime_test",
        "parent": {"path": "out/patch/monoeye_ko_expanded.wsc", "sha256": sha(parent), "size": len(parent)},
        "candidate": {"path": str(OUT_ROM.relative_to(ROOT)).replace('\\\\','/'), "sha256": sha(result), "size": len(result), "checksum": f"{checksum:04X}"},
        "candidate_save": {"path": str(OUT_SAVE.relative_to(ROOT)).replace('\\\\','/'), "sha256": sha(save), "size": len(save)},
        "diagnosis": {
            "uso": {
                "duplicate_records": [f"{a:06X}" for a in USO_ADDRESSES],
                "cause": "previous candidate fixed only bank5D copy; runtime also uses duplicate bank5E copy",
                "before_hex": USO_BEFORE.hex().upper(),
                "after_hex": USO_AFTER.hex().upper(),
            },
            "haman_hyper": {
                "screen_proven": "5DB482",
                "cause": "battle sentinel 不要 was incorrectly localized as visible 미사용",
                "restored_records": len(placeholder_plan),
            },
            "bank5f": {
                "canonical_records": len(prepared),
                "catalog_mismatches_before": len(mismatch_before),
                "compact3_runtime_broken_before": compact_before,
                "effective_records_rebound": len(prepared),
                "cause": "bank5F records remained bound to later-reused private ext3/compact3 slots; canonical address->translation catalog itself is correct",
                "dedicated_ext3_page": DEDICATED_EXT3_PAGE,
                "dedicated_physical_bank": f"{DEDICATED_EXT3_SEG:02X}",
                "alias_overlap": False,
            },
        },
        "counts": {
            "uso_records": len(uso_rows),
            "battle_sentinels_restored": len(placeholder_plan),
            "bank5f_records": len(prepared),
            "bank5f_ext3_records": sum(r["strategy"] == "ext3_page9" for r in prepared),
            "bank5f_ext3_unique_phrases": len(slot_payload),
            "bank5f_native_short_records": sum(r["strategy"] == "native_stock" for r in prepared),
            "bank5f_catalog_mismatches_before": len(mismatch_before),
            "changed_bytes": len(changed),
            "unaccounted_changed_bytes": 0,
        },
        "short_native": {
            "proof": short_proof,
            "records": {
                address: {
                    "text": info["text"],
                    "slot": f"{int(info['slot']):04X}",
                    "pointer": f"{int(info['pointer']):04X}",
                    "raw_hex": bytes(info["raw"]).hex().upper(),
                }
                for address, info in SHORT_NATIVE.items()
            },
            "post_refs": short_ref_report,
        },
        "ext3": {
            "write": ext3_write,
            "guard": ext3_guard.as_dict(),
            "selected_slots": [f"{slot:05X}" for slot in sorted(slot_payload)],
            "all_on_non_alias_page9": True,
        },
        "verification": {
            "alias_pages": detect_ext3_alias_page_count(result),
            "bank5f_exact_catalog": True,
            "bank5f_compact3_remaining": 0,
            "uso_visible_9b_remaining": 0,
            "placeholder_restore_failures": 0,
            "new_ext3_consumer_failures": 0,
            "main_unchanged": True,
            "save_unchanged": True,
        },
        "bank5f_targets": [
            {k: v for k, v in row.items() if k not in {"encoded", "prefix"}}
            for row in prepared
        ],
        "runtime_test_points": [
            "웃소: 두 중복 경로 모두 '않으면！！' 앞 일본어/한자 글리프가 사라지는지",
            "하만(하이퍼): 피격 시 미사용이 더 이상 출력되지 않는지",
            "콜로니 레이저 패턴1: '큭！ / 방어선을 돌파당했나！！'",
            "콜로니 레이저 패턴2: '사다란에 지원 요청을！ / 이대로면 발사 불능이다！！'",
            "콜로니 레이저 패턴3: '젠장！ / 우군은 뭘 하고 있는 거냐！'",
            "콜로니 레이저의 다른 피격/상태 대사에서도 시그/가토/모빌슈트 등 타 캐릭터 문구가 나오지 않는지",
        ],
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({
        "candidate": report["candidate"],
        "counts": report["counts"],
        "diagnosis": report["diagnosis"],
        "verification": report["verification"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
