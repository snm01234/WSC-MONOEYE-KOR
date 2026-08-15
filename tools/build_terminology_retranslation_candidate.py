#!/usr/bin/env python3
"""Build the source-grounded terminology/retranslation candidate.

Safety strategy for the current main TIP:
* record bodies are never rewritten;
* four unaliased stock dictionary phrases are replaced in place, never grown;
* private E5 18 phrases are replaced in place when they fit;
* a private phrase that grows is repointed inside its actual physical ext3 bank,
  including the five-bank alias mapping used by the live TIP;
* two derived long dictionary phrases inherit 少佐 -> 소령 through slot 1011;
* Rick Dom's ヒ－ト剣 and カゲロウ -> 하루살이 growth cases use that same
  guarded physical-bank append path.

All target record bytes and terminators therefore remain byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from mixed_residual_reference_union import build_reference_union  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    le16,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TARGETS = ROOT / "out/script/machine_translation_terminology_targets.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/terminology_retranslation_candidate.wsc"
OUT_SAVE = ROOT / "sram/terminology_retranslation_candidate.sav"
OUT_REPORT = ROOT / "out/patch/terminology_retranslation_report.json"
EXPECTED_MAIN_SHA256 = "64ade267ea6f5153e0d19bbdc308ed3f07b1da0891fcb485cc70dcd3100b2464"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
WEAPON_ABS = 0x75C1E9
WEAPON_JP = "ヒ－ト剣"
WEAPON_KO = "히트　사벨"

# Direct physical phrase replacements. Each has been measured as unaliased and
# the replacement encodes to <= the existing phrase length.
INPLACE_STOCK = {
    93: ("대좌", "대령"),
    414: ("블레이드　중좌。", "브래드　중령。"),
    1011: ("소좌", "소령"),
    2378: ("블레이드", "브래드"),
}

# These phrases are not directly rewritten. They contain a nested reference to
# slot 1011, so changing 少佐's canonical slot fixes them automatically.
DERIVED_SHARED = {
    2077: ("라이덴　소좌！<E62F>녀석은　위험합니다！！", "라이덴　소령！<E62F>녀석은　위험합니다！！"),
    2673: ("죄송합니다、라이덴　소좌！", "죄송합니다、라이덴　소령！"),
}

BAD_TOKENS = (
    "블레이드", "브라드", "블라드", "중좌", "소좌", "대좌", "중사", "소사", "대사",
    "카미이치유", "카미이유", "히트　검", "카게로",
)


class BuildError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def encode(text: str, tbl: Tbl) -> bytes:
    payload = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if payload is None or b"\x00" in payload:
        raise BuildError(f"cannot encode target text: {text!r}")
    return payload


def ext3_index(body: bytes) -> int | None:
    if len(body) < 4 or body[:2] != b"\xE5\x18":
        return None
    return 0x1000 + (body[2] << 8) + body[3]


def diff_runs(before: bytes, after: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pos = 0
    while pos < len(before):
        if before[pos] == after[pos]:
            pos += 1
            continue
        start = pos
        while pos < len(before) and before[pos] != after[pos]:
            pos += 1
        rows.append({
            "start": f"{start:06X}",
            "end": f"{pos:06X}",
            "length": pos - start,
            "before_hex": before[start:min(pos, start + 16)].hex().upper(),
            "after_hex": after[start:min(pos, start + 16)].hex().upper(),
        })
    return rows


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
    return any(a <= offset < b for a, b in intervals)


def consumer_abs_set(union, index: int) -> set[int]:
    return {int(c.abs) for c in union.consumers_for(index)}


def target_accounted(consumer: int, targets: set[int]) -> bool:
    # The canonical reference union may report the enclosing record start while
    # the source inventory reports the dialogue body after 1-3 control bytes.
    return any(0 <= target - consumer <= 8 for target in targets)


def stock_storage_proof(dictionary, index: int) -> dict[str, Any]:
    ptr = int(dictionary.ptrs[index])
    raw = bytes(dictionary.raw_entry(index))
    aliases = [i for i, value in enumerate(dictionary.ptrs) if int(value) == ptr]
    interior = [
        i for i, value in enumerate(dictionary.ptrs)
        if ptr < int(value) <= ptr + len(raw)
    ]
    return {
        "index": f"{index:04X}",
        "ptr": f"{ptr:04X}",
        "entry_abs": int(dictionary.entry_abs(index)),
        "old_len": len(raw),
        "aliases": aliases,
        "interior_pointer_indices": interior,
        "ok": aliases == [index] and not interior,
    }


def ext3_storage_proof(rom: bytes, dictionary, index: int) -> dict[str, Any]:
    # The live TIP can remap high locals from the first ext3 pages into the
    # alias expansion banks (e.g. logical 01A2A -> physical 21:42A).  Storage
    # safety must therefore use the same physical mapping as Dictionary rather
    # than the simple logical bank_local_for_index() helper.
    seg, local = dictionary._ext3_bank_local(index)
    base = (seg & 0x7F) * BANK_SIZE
    ptr = le16(rom, base + local * 2)
    raw = bytes(dictionary.raw_entry(index))
    alias_locals: list[int] = []
    interior_locals: list[int] = []
    for other_local in range(0x1000):
        other_ptr = le16(rom, base + other_local * 2)
        if other_ptr == ptr:
            alias_locals.append(other_local)
        elif ptr < other_ptr <= ptr + len(raw):
            interior_locals.append(other_local)
    return {
        "index": f"{index:05X}",
        "physical_segment": f"{seg:02X}",
        "physical_local": f"{local:03X}",
        "ptr": f"{ptr:04X}",
        "entry_abs": int(dictionary.entry_abs(index)),
        "old_len": len(raw),
        "physical_pointer_aliases": [f"{seg:02X}:{value:03X}" for value in alias_locals],
        "physical_interior_pointer_entries": [f"{seg:02X}:{value:03X}" for value in interior_locals],
        "ok": alias_locals == [local] and not interior_locals,
    }


def inplace_phrase(candidate: bytearray, proof: dict[str, Any], encoded: bytes) -> tuple[int, int]:
    if not proof["ok"]:
        raise BuildError(f"phrase storage is aliased: {proof}")
    old_len = int(proof["old_len"])
    if len(encoded) > old_len:
        raise BuildError(
            f"in-place phrase growth refused at {proof['index']}: {len(encoded)} > {old_len}"
        )
    start = int(proof["entry_abs"])
    candidate[start:start + len(encoded)] = encoded
    candidate[start + len(encoded)] = 0
    return start, start + old_len + 1


def append_ext3_phrase_alias_aware(
    candidate: bytearray,
    dictionary,
    index: int,
    encoded: bytes,
) -> tuple[dict[str, Any], list[tuple[int, int]]]:
    """Repoint one ext3 slot to appended phrase room in its physical bank.

    ``Dictionary._ext3_bank_local`` mirrors the live runtime alias mapping, so
    logical early-page high locals are written into banks 21-25 rather than the
    obsolete logical 11-15 pointer tables.
    """
    seg, local = dictionary._ext3_bank_local(index)
    base = (seg & 0x7F) * BANK_SIZE
    cursor = 0x2001
    for other_local in range(0x1000):
        ptr = le16(candidate, base + other_local * 2)
        if ptr < 0x2000 or ptr >= BANK_SIZE:
            continue
        end = ptr
        while end < BANK_SIZE and candidate[base + end] != 0:
            end += 1
        if end >= BANK_SIZE:
            raise BuildError(f"unterminated ext3 phrase in physical bank {seg:02X}")
        cursor = max(cursor, end + 1)

    need = len(encoded) + 1
    if cursor + need > BANK_SIZE:
        raise BuildError(
            f"no physical ext3 room for {index:05X}: need {need}, "
            f"room {BANK_SIZE - cursor} in bank {seg:02X}"
        )
    pointer_abs = base + local * 2
    phrase_abs = base + cursor
    candidate[phrase_abs : phrase_abs + len(encoded)] = encoded
    candidate[phrase_abs + len(encoded)] = 0
    candidate[pointer_abs : pointer_abs + 2] = cursor.to_bytes(2, "little")
    return (
        {
            "index": f"{index:05X}",
            "physical_segment": f"{seg:02X}",
            "physical_local": f"{local:03X}",
            "new_ptr": f"{cursor:04X}",
            "new_len": len(encoded),
            "room_before": BANK_SIZE - cursor,
            "room_after": BANK_SIZE - (cursor + need),
        },
        [(pointer_abs, pointer_abs + 2), (phrase_abs, phrase_abs + need)],
    )


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.exists() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or unexpected size")

    target_doc = json.loads(TARGETS.read_text(encoding="utf-8"))
    if str((target_doc.get("tip") or {}).get("sha256") or "").lower() != EXPECTED_MAIN_SHA256:
        raise BuildError("target worklist is not bound to the current main TIP")
    rows = list(target_doc.get("targets") or [])
    if not rows:
        raise BuildError("empty target worklist")
    if any(
        row.get("translation_source") != "llm" or row.get("review_status") != "approved"
        for row in rows
    ):
        raise BuildError("all target translations must be approved LLM review")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks <= 0:
        raise BuildError("ext3 runtime metadata missing")
    sb = stock_base(parent)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)

    candidate = bytearray(parent)
    allowed_intervals: list[tuple[int, int]] = []

    # 1) Four canonical stock phrases, all unaliased and non-growing.
    stock_proofs: list[dict[str, Any]] = []
    for index, (before_text, after_text) in sorted(INPLACE_STOCK.items()):
        actual = strip_pad(d_parent.expand_index(index, tbl))
        if actual != before_text:
            raise BuildError(f"stock slot {index:04X} drifted: {actual!r}")
        proof = stock_storage_proof(d_parent, index)
        encoded = encode(after_text, tbl)
        proof.update({
            "before": before_text,
            "ko": after_text,
            "new_len": len(encoded),
            "fits_in_place": len(encoded) <= int(proof["old_len"]),
        })
        if not proof["ok"] or not proof["fits_in_place"]:
            raise BuildError(f"unsafe stock phrase replacement: {proof}")
        allowed_intervals.append(inplace_phrase(candidate, proof, encoded))
        stock_proofs.append(proof)

    # 2) Prepare source-grounded records. Dialogue targets validate the normal
    #    rendered body; broad AUX follow-ups validate the private E5 18 phrase
    #    beginning at their measured portal offset. Three short records inherit
    #    the canonical stock phrase changes above.
    ext3_payload: dict[int, bytes] = {}
    ext3_expected: dict[int, str] = {}
    ext3_targets: dict[int, set[int]] = defaultdict(set)
    prepared_rows: list[dict[str, Any]] = []
    non_ext3_rows: list[dict[str, Any]] = []
    for row in rows:
        logical = int(row["abs"], 16)
        got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        if got is None:
            raise BuildError(f"target record unreadable: {logical:06X}")
        payload, terminator = bytes(got[0]), int(got[1])
        mode = str(row.get("target_mode") or "record_render")
        expected = strip_pad(str(row["ko"]))

        if mode == "private_ext3_phrase":
            portal_offset = int(row.get("portal_offset") or 0)
            if portal_offset < 0 or portal_offset + 4 > len(payload):
                raise BuildError(f"bad portal offset at {logical:06X}: {portal_offset}")
            token = payload[portal_offset : portal_offset + 4]
            idx = ext3_index(token)
            if idx is None:
                raise BuildError(f"expected ext3 portal missing at {logical:06X}+{portal_offset}")
            declared = str(row.get("ext3_index") or "").upper()
            if declared and declared != f"{idx:05X}":
                raise BuildError(f"ext3 index drifted at {logical:06X}: {declared} != {idx:05X}")
            current = strip_pad(d_parent.expand_index(idx, tbl))
            if current != strip_pad(str(row["current"])):
                raise BuildError(f"private ext3 phrase drifted at {logical:06X}: {current!r}")
            prefix = payload[:portal_offset]
            body = payload[portal_offset:]
            kind = str(row.get("record_kind") or "manifest")
        elif mode == "record_render":
            prefix, body, kind = split_prefix_body(payload)
            current = strip_pad(d_parent.expand(body, tbl))
            if current != strip_pad(str(row["current"])):
                raise BuildError(f"current render drifted at {logical:06X}: {current!r}")
            idx = ext3_index(body)
            portal_offset = len(prefix) if idx is not None else -1
        else:
            raise BuildError(f"unknown target mode at {logical:06X}: {mode}")

        prepared = {
            **row,
            "logical": logical,
            "target_mode": mode,
            "portal_offset_actual": portal_offset,
            "prefix_hex_actual": prefix.hex().upper(),
            "body_hex": body.hex().upper(),
            "body_len_actual": len(body),
            "terminator_actual": f"{terminator:06X}",
            "ext3_index": f"{idx:05X}" if idx is not None else None,
        }
        prepared_rows.append(prepared)
        if idx is None:
            non_ext3_rows.append(prepared)
            continue
        encoded = encode(expected, tbl)
        previous = ext3_payload.setdefault(idx, encoded)
        if previous != encoded:
            raise BuildError(f"conflicting ext3 phrases for slot {idx:05X}")
        previous_text = ext3_expected.setdefault(idx, expected)
        if previous_text != expected:
            raise BuildError(f"conflicting ext3 target text for slot {idx:05X}")
        ext3_targets[idx].add(logical)

    if {row["abs"] for row in non_ext3_rows} != {"5C214D", "60447D", "60E616"}:
        raise BuildError(
            "unexpected non-ext3 target set: "
            + repr(sorted(row["abs"] for row in non_ext3_rows))
        )

    # 3) Prove each ext3 slot has no unrelated consumer and no storage alias,
    #    then replace its phrase in place. No pointer or record changes.
    ext3_checks: list[dict[str, Any]] = []
    for index in sorted(ext3_payload):
        expected_targets = ext3_targets[index]
        consumers = consumer_abs_set(union, index)
        unexpected = sorted(
            value for value in consumers if not target_accounted(value, expected_targets)
        )
        missing = sorted(
            target for target in expected_targets
            if not any(0 <= target - value <= 8 for value in consumers)
        )
        proof = ext3_storage_proof(parent, d_parent, index)
        encoded = ext3_payload[index]
        proof.update({
            "expected_targets": [f"{value:06X}" for value in sorted(expected_targets)],
            "actual_consumers": [f"{value:06X}" for value in sorted(consumers)],
            "unexpected_consumers": [f"{value:06X}" for value in unexpected],
            "missing_consumers": [f"{value:06X}" for value in missing],
            "before": strip_pad(d_parent.expand_index(index, tbl)),
            "ko": ext3_expected[index],
            "new_len": len(encoded),
            "fits_in_place": len(encoded) <= int(proof["old_len"]),
        })
        proof["consumer_ok"] = not unexpected and not missing
        if not proof["ok"] or not proof["consumer_ok"]:
            raise BuildError(f"unsafe private ext3 replacement: {proof}")
        if proof["fits_in_place"]:
            proof["strategy"] = "inplace"
            allowed_intervals.append(inplace_phrase(candidate, proof, encoded))
        else:
            proof["strategy"] = "physical_bank_repoint_append"
            write, intervals = append_ext3_phrase_alias_aware(
                candidate, d_parent, index, encoded
            )
            proof["write"] = write
            allowed_intervals.extend(intervals)
        proof["ok_all"] = True
        ext3_checks.append(proof)

    # 4) Rick Dom weapon: same private ext3 slot, but its corrected text grows by
    #    two bytes. Repoint only that existing slot to appended room in bank 1F.
    weapon_got = read_encoded_z_safe(parent, sb + WEAPON_ABS, max_len=64)
    if weapon_got is None:
        raise BuildError("Rick Dom weapon record unreadable")
    weapon_payload, weapon_term = bytes(weapon_got[0]), int(weapon_got[1])
    weapon_prefix, weapon_body, _ = split_prefix_body(weapon_payload)
    if weapon_prefix:
        raise BuildError("unexpected Rick Dom weapon prefix")
    weapon_current = strip_pad(d_parent.expand(weapon_body, tbl))
    if weapon_current != "히트　검":
        raise BuildError(f"Rick Dom weapon drifted: {weapon_current!r}")
    weapon_slot = ext3_index(weapon_body)
    if weapon_slot is None:
        raise BuildError("Rick Dom weapon no longer uses ext3")
    weapon_consumers = consumer_abs_set(union, weapon_slot)
    if any(not target_accounted(value, {WEAPON_ABS}) for value in weapon_consumers):
        raise BuildError(f"Rick Dom weapon slot is shared: {sorted(weapon_consumers)}")
    weapon_proof = ext3_storage_proof(parent, d_parent, weapon_slot)
    if not weapon_proof["ok"]:
        raise BuildError(f"Rick Dom weapon phrase storage aliased: {weapon_proof}")
    weapon_encoded = encode(WEAPON_KO, tbl)
    weapon_write, weapon_intervals = append_ext3_phrase_alias_aware(
        candidate, d_parent, weapon_slot, weapon_encoded
    )
    allowed_intervals.extend(weapon_intervals)

    checksum = update_ws_checksum(candidate)
    allowed_intervals.append((len(parent) - 2, len(parent)))
    result = bytes(candidate)
    d_result = make_dictionary_ext3(result, ext_meta, ext3_meta)

    # 5) Verify every target record/phrase decodes exactly while record bytes
    #    and terminators remain unchanged.
    target_checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in prepared_rows:
        logical = int(row["logical"])
        before_got = read_encoded_z_safe(parent, sb + logical, max_len=256)
        after_got = read_encoded_z_safe(result, sb + logical, max_len=256)
        if before_got is None or after_got is None:
            failures.append({"abs": row["abs"], "reason": "unreadable_after"})
            continue
        before_payload, before_term = bytes(before_got[0]), int(before_got[1])
        after_payload, after_term = bytes(after_got[0]), int(after_got[1])
        mode = str(row.get("target_mode") or "record_render")
        if mode == "private_ext3_phrase":
            portal_offset = int(row["portal_offset_actual"])
            rendered = strip_pad(d_result.expand(after_payload[portal_offset:], tbl))
        else:
            rendered = strip_pad(d_result.expand(split_prefix_body(after_payload)[1], tbl))
        expected = strip_pad(str(row["ko"]))
        check = {
            "target_mode": mode,
            "abs": row["abs"],
            "jp": row["jp"],
            "before": row["current"],
            "ko": expected,
            "rendered": rendered,
            "record_bytes_exact": before_payload == after_payload,
            "terminator_exact": before_term == after_term,
            "ok": rendered == expected and before_payload == after_payload and before_term == after_term,
        }
        target_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    # 6) Verify all six global dictionary outputs, including the two nested
    #    consumers that were intentionally not directly rewritten.
    shared_checks: list[dict[str, Any]] = []
    for index, (before_text, after_text) in sorted({**INPLACE_STOCK, **DERIVED_SHARED}.items()):
        rendered = strip_pad(d_result.expand_index(index, tbl))
        check = {
            "index": f"{index:04X}",
            "before": before_text,
            "ko": after_text,
            "rendered": rendered,
            "strategy": "inplace" if index in INPLACE_STOCK else "nested_slot_1011",
            "ok": rendered == after_text,
        }
        shared_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    after_weapon = read_encoded_z_safe(result, sb + WEAPON_ABS, max_len=64)
    if after_weapon is None:
        raise BuildError("Rick Dom weapon unreadable after build")
    weapon_rendered = strip_pad(d_result.expand(split_prefix_body(bytes(after_weapon[0]))[1], tbl))
    weapon_check = {
        "abs": f"{WEAPON_ABS:06X}",
        "jp": WEAPON_JP,
        "before": weapon_current,
        "ko": WEAPON_KO,
        "rendered": weapon_rendered,
        "slot": f"{weapon_slot:05X}",
        "slot_consumers": [f"{value:06X}" for value in sorted(weapon_consumers)],
        "record_bytes_exact": weapon_payload == bytes(after_weapon[0]),
        "terminator_exact": weapon_term == int(after_weapon[1]),
        "ok": weapon_rendered == WEAPON_KO and weapon_payload == bytes(after_weapon[0]) and weapon_term == int(after_weapon[1]),
    }
    if not weapon_check["ok"]:
        failures.append(weapon_check)

    if failures:
        raise BuildError(
            "candidate verification failures: "
            + json.dumps(failures[:12], ensure_ascii=False)
        )

    # 7) No target may retain the requested bad terminology.
    bad_target_residuals: list[dict[str, str]] = []
    for row in target_checks:
        if any(token in row["rendered"] for token in BAD_TOKENS):
            bad_target_residuals.append({"abs": row["abs"], "rendered": row["rendered"]})
    if any(token in weapon_rendered for token in BAD_TOKENS):
        bad_target_residuals.append({"abs": f"{WEAPON_ABS:06X}", "rendered": weapon_rendered})
    if bad_target_residuals:
        raise BuildError(f"bad terminology remains: {bad_target_residuals[:8]}")

    # 8) Diff allowlist. Record address ranges are intentionally absent.
    allowed_intervals = merged(allowed_intervals)
    runs = diff_runs(parent, result)
    unaccounted: list[int] = []
    for run in runs:
        start, end = int(run["start"], 16), int(run["end"], 16)
        for offset in range(start, end):
            if not in_intervals(offset, allowed_intervals):
                unaccounted.append(offset)
                if len(unaccounted) >= 50:
                    break
        if len(unaccounted) >= 50:
            break
    if unaccounted:
        raise BuildError(
            "unaccounted changed bytes: "
            + ", ".join(f"{value:06X}" for value in unaccounted[:20])
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_terminology_retranslation_candidate.py",
        "status": "candidate_static_verified",
        "ok": True,
        "main_tip_modified": False,
        "inputs": {
            "main": {
                "path": "out/patch/monoeye_ko_expanded.wsc",
                "size": len(parent),
                "sha256": sha256(parent),
            },
            "targets": {
                "path": "out/script/machine_translation_terminology_targets.json",
                "sha256": sha256(TARGETS.read_bytes()),
            },
        },
        "counts": {
            "translation_records": len(target_checks),
            "record_render_targets": sum(row.get("target_mode") == "record_render" for row in target_checks),
            "private_ext3_phrase_targets": sum(row.get("target_mode") == "private_ext3_phrase" for row in target_checks),
            "weapon_records": 1,
            "inplace_stock_slots": len(stock_proofs),
            "derived_nested_slots": len(DERIVED_SHARED),
            "private_ext3_slots": len(ext3_checks),
            "inplace_private_ext3_slots": sum(row.get("strategy") == "inplace" for row in ext3_checks),
            "repointed_private_ext3_slots": sum(row.get("strategy") == "physical_bank_repoint_append" for row in ext3_checks),
            "non_ext3_records_fixed_via_stock_dictionary": len(non_ext3_rows),
            "target_failures": len(failures),
            "bad_target_residuals": len(bad_target_residuals),
        },
        "stock_storage_proofs": stock_proofs,
        "private_ext3_storage_and_consumer_proofs": ext3_checks,
        "shared_dictionary_checks": shared_checks,
        "translation_records": target_checks,
        "weapon": {**weapon_check, "storage_proof": weapon_proof, "write": weapon_write},
        "verification": {
            "record_bodies_unchanged": all(row["record_bytes_exact"] for row in target_checks) and weapon_check["record_bytes_exact"],
            "terminators_unchanged": all(row["terminator_exact"] for row in target_checks) and weapon_check["terminator_exact"],
            "all_target_renders_exact": all(row["ok"] for row in target_checks) and weapon_check["ok"],
            "all_shared_dictionary_renders_exact": all(row["ok"] for row in shared_checks),
            "all_private_ext3_proofs_ok": all(row["ok_all"] for row in ext3_checks),
            "unaccounted_changed_bytes": len(unaccounted),
            "diff_runs": len(runs),
            "diff_bytes": sum(int(row["length"]) for row in runs),
            "checksum": f"{checksum:04X}",
            "candidate_sha256": sha256(result),
        },
        "diff_sample": runs[:120],
        "candidate_rom": {
            "path": "out/patch/terminology_retranslation_candidate.wsc",
            "size": len(result),
            "sha256": sha256(result),
        },
        "candidate_save": {
            "path": "sram/terminology_retranslation_candidate.sav",
            "size": MAIN_SAVE.stat().st_size,
            "sha256": sha256(MAIN_SAVE.read_bytes()),
        },
    }

    OUT_ROM.parent.mkdir(parents=True, exist_ok=True)
    OUT_ROM.write_bytes(result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    OUT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["verification"], ensure_ascii=False, indent=2))
    print("candidate:", OUT_ROM)
    print("report:", OUT_REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
