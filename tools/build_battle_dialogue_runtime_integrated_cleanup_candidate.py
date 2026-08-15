#!/usr/bin/env python3
"""Build the integrated runtime cleanup candidate after short/fixed repair.

This stage addresses three runtime-only residual families without guessing new
speaker metadata:

1. 5D/5E body-only continuation records whose whole payload is an E5 18 alias.
   The static decoder renders them correctly, but the battle consumer can expose
   portal bytes as glyph/speaker data.  Rehome the exact already-approved phrase
   bytes into native two-byte stock dictionary tokens.
2. Two-byte visible text leads misclassified as metadata when they sit between
   the same one-byte speaker/control id and duplicate the beginning of the
   translated body.  Drop only that visible lead and rehome the existing body
   phrase byte-exactly into a stock token.
3. Screen-proven one-byte false lead 5E:4F43 and the scenario/system mixed line
   61:06D5.  The latter preserves runtime prefix 17 28 01 18 byte-exactly.

Input is the already gated short/fixed candidate; main TIP/SaveRAM are never
modified by this builder.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import (
    BANK_SIZE,
    DICT_PTR_START,
    EXT3_INDEX_BASE,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    dict_index_from_token,
    dict_token_safe_in_zstring,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
PARENT_ROM = PATCH / "battle_dialogue_short_fixed_structure_repair_candidate.wsc"
PARENT_SAVE = ROOT / "sram/battle_dialogue_short_fixed_structure_repair_candidate.sav"
PARENT_REPORT = PATCH / "battle_dialogue_short_fixed_structure_repair_report.json"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
INVENTORY = SCRIPT / "battle_dialogue_structure_inventory.csv"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "battle_dialogue_runtime_integrated_cleanup_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_dialogue_runtime_integrated_cleanup_candidate.sav"
SRAM_SAVE = ROOT / "sram/battle_dialogue_runtime_integrated_cleanup_candidate.sav"
REPORT = PATCH / "battle_dialogue_runtime_integrated_cleanup_report.json"
BODYONLY_CSV = SCRIPT / "battle_dialogue_bodyonly_e518_stock_rehome_targets.csv"
DUP2_CSV = SCRIPT / "battle_dialogue_duplicate_lead_stock_rehome_targets.csv"

EXPECTED_MAIN_SHA = "56b1ed5b81d9878bed01383f68abfffc876ad04eea5dd1d4d29525c833c83898"
EXPECTED_PARENT_SHA = "75e840e0782e2bb22c35ea6d52eec7705bad6e91d87fa56bf536ad6d531fe890"
EXPECTED_BODYONLY = 284
EXPECTED_DUP2 = 70
EXPECTED_SHORT_METADATA = 104
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
ONEBYTE_SCREEN = 0x5E4F43
ONEBYTE_PARTNER_PREV = 0x5E4F39
ONEBYTE_PARTNER_NEXT = 0x5E4F51
SYSTEM_RECORD = 0x6106D5
SYSTEM_PREFIX = bytes.fromhex("17280118")
SYSTEM_KO = "『ＵＣ．００８０……카라마・포인트』"
ONEBYTE_KO = "전　포문……쏴라！！"
SCREEN_BODYONLY = (0x5D1E57, 0x5D1E6C)
SCREEN_DUP2 = 0x5EB12A


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def ident(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    try:
        display = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        display = str(path.resolve())
    return {"path": display, "size": len(payload), "sha256": sha(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def visible_japanese(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in text)


def inventory_rows() -> list[dict[str, str]]:
    with INVENTORY.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def raw_ext3_phrase(dictionary, token: bytes) -> bytes:
    if len(token) < 4 or token[:2] != b"\xE5\x18":
        raise BuildError(f"not an E5 18 token: {token.hex().upper()}")
    index = dict_index_from_ext3_token(token[0], token[1], token[2], token[3])
    return dictionary.raw_entry(index)


def exact_stock_raw_map(dictionary: Dictionary) -> dict[bytes, list[int]]:
    out: dict[bytes, list[int]] = defaultdict(list)
    for index in range(dictionary.stock_count):
        try:
            raw = dictionary.raw_entry(index)
        except Exception:  # noqa: BLE001
            continue
        if raw:
            out[raw].append(index)
    return out


_CONTROL_RE = re.compile(r"<[0-9A-Fa-f]{4}>")


def stock_text_options(
    dictionary: Dictionary,
    tbl: Tbl,
    *,
    excluded: set[int],
) -> tuple[dict[str, list[tuple[str, bytes, int]]], dict[str, list[int]]]:
    """Safe native stock tokens, excluding slots that will be repurposed."""
    by_first: dict[str, list[tuple[str, bytes, int]]] = defaultdict(list)
    exact: dict[str, list[int]] = defaultdict(list)
    for index in range(dictionary.stock_count):
        if index in excluded or not dict_token_safe_in_zstring(index):
            continue
        try:
            raw = bytes(dictionary.raw_entry(index))
            if not raw:
                continue
            text = clean(dictionary.expand(raw, tbl))
            token = token_from_dict_index(index)
        except Exception:  # noqa: BLE001
            continue
        if (
            not text
            or "<BADDICT:" in text
            or visible_japanese(text)
            or 0 in token
        ):
            continue
        by_first[text[0]].append((text, token, index))
        exact[text].append(index)
    for key in by_first:
        by_first[key].sort(key=lambda item: (-len(item[0]), item[1], item[2]))
    return by_first, exact


def shortest_stock_encoding(
    text: str,
    *,
    dictionary: Dictionary,
    tbl: Tbl,
    options: dict[str, list[tuple[str, bytes, int]]],
) -> bytes:
    """Shortest native encoding while preserving Hangul-run compression.

    Direct text candidates are whole spans, not single characters.  Encoding a
    Hangul syllable one at a time repeats the sticky Hangul marker and produces a
    false capacity failure.  Inline <XXXX> controls remain atomic.
    """
    n = len(text)
    best: list[tuple[int, bytes] | None] = [None] * (n + 1)
    best[n] = (0, b"")
    for pos in range(n - 1, -1, -1):
        candidates: list[tuple[int, bytes]] = []
        if text[pos] == "<":
            match = _CONTROL_RE.match(text, pos)
            if match is not None:
                direct = bytes.fromhex(match.group(0)[1:-1])
                tail = best[match.end()]
                if tail is not None and direct and 0 not in direct:
                    candidates.append((len(direct) + tail[0], direct + tail[1]))
        else:
            next_control = text.find("<", pos)
            stop = n if next_control < 0 else next_control
            for end in range(pos + 1, stop + 1):
                tail = best[end]
                if tail is None:
                    continue
                try:
                    direct = encode_phrase(text[pos:end], tbl)
                except Exception:  # noqa: BLE001
                    continue
                if direct and 0 not in direct:
                    candidates.append((len(direct) + tail[0], direct + tail[1]))
            for phrase, token, _index in options.get(text[pos], []):
                if not text.startswith(phrase, pos):
                    continue
                tail = best[pos + len(phrase)]
                if tail is not None:
                    candidates.append((len(token) + tail[0], token + tail[1]))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            best[pos] = candidates[0]
    if best[0] is None:
        raise BuildError(f"cannot stock-encode target phrase: {text!r}")
    encoded = best[0][1]
    rendered = clean(dictionary.expand(encoded, tbl))
    if rendered != text:
        raise BuildError(f"short stock encoding render mismatch: {text!r} != {rendered!r}")
    if b"\x00" in encoded:
        raise BuildError(f"short stock encoding contains NUL: {text!r}")
    return encoded


def native_units(payload: bytes) -> list[tuple[str, bytes | int]]:
    """Split native text bytes into atomic one/two-byte code units."""
    units: list[tuple[str, bytes | int]] = []
    pos = 0
    while pos < len(payload):
        lead = payload[pos]
        width = 2 if lead >= 0xE0 else 1
        if pos + width > len(payload):
            raise BuildError(f"truncated native unit in {payload.hex().upper()}")
        raw = bytes(payload[pos:pos + width])
        if 0 in raw:
            raise BuildError(f"NUL in native unit {raw.hex().upper()}")
        units.append(("raw", raw))
        pos += width
    return units


def symbol_size(symbol: tuple[str, bytes | int]) -> int:
    return len(symbol[1]) if symbol[0] == "raw" else 2


def repair_pair_compress(
    payloads: dict[str, bytes],
    *,
    tail_capacity: int,
    max_rules: int,
) -> tuple[dict[str, list[tuple[str, bytes | int]]], list[tuple[Any, Any]], dict[str, int]]:
    """Re-Pair style native-token grammar; helper tokens always cost two bytes."""
    sequences = {text: native_units(raw) for text, raw in payloads.items()}
    rules: list[tuple[tuple[str, bytes | int], tuple[str, bytes | int]]] = []
    rule_depths: list[int] = []
    depth_ceiling = 5  # current stock dictionary already reaches depth 5

    def sym_depth(symbol: tuple[str, bytes | int]) -> int:
        return 0 if symbol[0] == "raw" else rule_depths[int(symbol[1])]

    def total_bytes() -> int:
        return sum(sum(symbol_size(sym) for sym in seq) + 1 for seq in sequences.values())

    initial = total_bytes()
    while total_bytes() > tail_capacity and len(rules) < max_rules:
        frequencies: Counter[tuple[tuple[str, bytes | int], tuple[str, bytes | int]]] = Counter()
        for seq in sequences.values():
            frequencies.update(zip(seq, seq[1:]))
        scored: list[tuple[int, int, str, tuple[tuple[str, bytes | int], tuple[str, bytes | int]]]] = []
        for pair, count in frequencies.items():
            per_hit = symbol_size(pair[0]) + symbol_size(pair[1]) - 2
            if per_hit <= 0:
                continue
            new_depth = 1 + max(sym_depth(pair[0]), sym_depth(pair[1]))
            if new_depth > depth_ceiling:
                continue
            scored.append((count * per_hit, count, repr(pair), pair))
        if not scored:
            break
        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        pair = scored[0][3]
        helper = ("helper", len(rules))
        rules.append(pair)
        rule_depths.append(1 + max(sym_depth(pair[0]), sym_depth(pair[1])))
        for text, seq in list(sequences.items()):
            out: list[tuple[str, bytes | int]] = []
            i = 0
            while i < len(seq):
                if i + 1 < len(seq) and seq[i] == pair[0] and seq[i + 1] == pair[1]:
                    out.append(helper)
                    i += 2
                else:
                    out.append(seq[i])
                    i += 1
            sequences[text] = out
    final = total_bytes()
    # The remaining bytes do not all have to live in the tail: top-level
    # phrases that fit their reclaimed slot's old phrase region are stored
    # in-place later.  Tail capacity is therefore enforced after slot packing.
    return sequences, rules, {
        "initial_bytes": initial,
        "final_bytes": final,
        "rules": len(rules),
        "max_depth": max(rule_depths, default=0),
    }


def symbol_bytes(
    symbol: tuple[str, bytes | int],
    helper_slots: dict[int, int],
) -> bytes:
    if symbol[0] == "raw":
        return bytes(symbol[1])
    return token_from_dict_index(helper_slots[int(symbol[1])])


def record_live(parent: bytes, sb: int, row: dict[str, str]) -> bytes:
    length = len(bytes.fromhex(row["current_payload_hex"]))
    start = sb + int(row["record_start"], 16)
    return parent[start:start + length]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    import io
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    atomic_text(path, buf.getvalue())


def main() -> int:
    parent = bytes(load_rom(PARENT_ROM))
    save = PARENT_SAVE.read_bytes()
    main_tip = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"short/fixed parent identity drifted: {sha(parent)}")
    if len(main_tip) != ROM_SIZE or sha(main_tip) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(main_tip)}")
    if len(save) != SAVE_SIZE:
        raise BuildError("candidate SaveRAM size drifted")
    parent_report = json.loads(PARENT_REPORT.read_text(encoding="utf-8"))
    if parent_report.get("ok") is not True:
        raise BuildError("short/fixed parent report is not green")
    if str(((parent_report.get("outputs") or {}).get("candidate_rom") or {}).get("sha256") or "").lower() != EXPECTED_PARENT_SHA:
        raise BuildError("short/fixed parent report identity mismatch")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)
    rows = inventory_rows()
    by_abs = {int(row["record_start"], 16): row for row in rows}

    # Family A: body-only E5 18 records sandwiched by the same native one-byte
    # speaker/control id.  Rehome phrase bytes only; do not insert metadata.
    bodyonly: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        if row["classification"] != "text_initial_exception" or not row["current_payload_hex"].startswith("E518"):
            continue
        if i == 0 or i + 1 >= len(rows):
            continue
        prev_meta = rows[i - 1]["metadata_hex"]
        next_meta = rows[i + 1]["metadata_hex"]
        if not prev_meta or prev_meta != next_meta or len(bytes.fromhex(prev_meta)) != 1:
            continue
        live = record_live(parent, sb, row)
        if live[:2] != b"\xE5\x18":
            raise BuildError(f"body-only live token drift at {row['record_start']}")
        raw = raw_ext3_phrase(dictionary, live[:4])
        render = clean(dictionary.expand(raw, tbl))
        if not render or visible_japanese(render):
            raise BuildError(f"body-only source phrase not clean Korean at {row['record_start']}: {render}")
        bodyonly.append({
            "abs": row["record_start"],
            "payload_len": len(live),
            "before_hex": live.hex().upper(),
            "phrase_raw": raw,
            "render": render,
            "sandwich_metadata": prev_meta,
            "previous_record": rows[i - 1]["record_start"],
            "next_record": rows[i + 1]["record_start"],
        })
    if len(bodyonly) != EXPECTED_BODYONLY:
        raise BuildError(f"body-only target count drifted: {len(bodyonly)}")

    # Family B: a two-byte visible text lead was mistaken for metadata.  Require
    # same one-byte control on both sides and exact Korean prefix duplication.
    dup2: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        meta_hex = row["metadata_hex"]
        if not meta_hex or len(bytes.fromhex(meta_hex)) != 2 or i == 0 or i + 1 >= len(rows):
            continue
        prev_meta = rows[i - 1]["metadata_hex"]
        next_meta = rows[i + 1]["metadata_hex"]
        if not prev_meta or prev_meta != next_meta or len(bytes.fromhex(prev_meta)) != 1:
            continue
        live = record_live(parent, sb, row)
        meta = bytes.fromhex(meta_hex)
        if live[:2] != meta:
            continue
        lead = clean(dictionary.expand(meta, tbl))
        if live[2:4] == b"\xE5\x18":
            raw = raw_ext3_phrase(dictionary, live[2:6])
        elif len(live) >= 4 and 0xF0 <= live[2] <= 0xFF:
            raw = dictionary.raw_entry(dict_index_from_token(live[2], live[3]))
        else:
            continue
        body = clean(dictionary.expand(raw, tbl))
        if not lead or lead.startswith("<") or not body.startswith(lead):
            continue
        if visible_japanese(body):
            raise BuildError(f"dup2 body not clean Korean at {row['record_start']}: {body}")
        dup2.append({
            "abs": row["record_start"],
            "payload_len": len(live),
            "before_hex": live.hex().upper(),
            "removed_lead_hex": meta_hex,
            "removed_lead_render": lead,
            "phrase_raw": raw,
            "render": body,
            "sandwich_metadata": prev_meta,
            "previous_record": rows[i - 1]["record_start"],
            "next_record": rows[i + 1]["record_start"],
        })
    if len(dup2) != EXPECTED_DUP2:
        raise BuildError(f"duplicate two-byte target count drifted: {len(dup2)}")

    bodyonly_abs = {int(row["abs"], 16) for row in bodyonly}
    dup2_abs = {int(row["abs"], 16) for row in dup2}
    if not set(SCREEN_BODYONLY) <= bodyonly_abs or SCREEN_DUP2 not in dup2_abs:
        raise BuildError("screen anchor family binding failed")

    # Screen-proven one-byte false lead.
    one_row = by_abs[ONEBYTE_SCREEN]
    one_live = record_live(parent, sb, one_row)
    if one_live[0] != 0x86 or clean(dictionary.expand(one_live[1:], tbl)) != "포문……쏴라！！":
        raise BuildError("5E4F43 screen anchor drifted")
    one_raw = encode_phrase(ONEBYTE_KO, tbl)

    # Scenario/system line.  Runtime screenshot proves first visible glyph is the
    # quote, so the 17 28 01 18 prefix is protected byte-exactly.
    sys_len = 25  # original/current payload length at 61:06D5
    sys_live = parent[sb + SYSTEM_RECORD: sb + SYSTEM_RECORD + sys_len]
    if not sys_live.startswith(SYSTEM_PREFIX):
        raise BuildError(f"system prefix drifted: {sys_live[:4].hex().upper()}")
    sys_raw = encode_phrase(SYSTEM_KO, tbl)

    # Every target is re-expressed through the native two-byte stock dictionary.
    # Strong-retired slots are the only writable dictionary ids.  The accepted
    # runtime is unchanged: no compact3/E5-18 portal is introduced here.
    target_texts = sorted({row["render"] for row in bodyonly + dup2} | {ONEBYTE_KO, SYSTEM_KO})
    retired = [
        index
        for index in current_strong_retired_slots(original, parent, dictionary)
        if dict_token_safe_in_zstring(index)
    ]
    wanted = set(retired)
    parent_external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    parent_nested = nested_occurrence_map(dictionary, wanted=wanted, ext3_aware=True)
    parent_raw = _raw_pair_hits(parent, retired)
    retired = [
        i for i in retired
        if not parent_external.get(i) and not parent_nested.get(i) and not parent_raw.get(i)
    ]
    if len(retired) < 400:
        raise BuildError(f"strong-retired stock population unexpectedly small: {len(retired)}")

    # Retired entries are allowed as read-only compression ingredients.  Any
    # such entry that the shortest encoding actually uses is removed from the
    # reclaim pool (including its nested stock dependencies).
    options, exact_text = stock_text_options(dictionary, tbl, excluded=set())
    text_assignment: dict[str, int] = {}
    for text in target_texts:
        if exact_text.get(text):
            text_assignment[text] = min(exact_text[text])
    missing_texts = [text for text in target_texts if text not in text_assignment]

    base_payloads = {
        text: shortest_stock_encoding(text, dictionary=dictionary, tbl=tbl, options=options)
        for text in missing_texts
    }

    retired_set = set(retired)
    preserved_retired: set[int] = {
        index for index in text_assignment.values() if index in retired_set
    }

    def stock_refs(payload: bytes) -> set[int]:
        refs: set[int] = set()
        for unit in native_units(payload):
            raw = bytes(unit[1])
            if len(raw) == 2 and 0xF0 <= raw[0] <= 0xFE:
                refs.add(dict_index_from_token(raw[0], raw[1]))
        return refs

    for payload in base_payloads.values():
        preserved_retired.update(stock_refs(payload) & retired_set)
    queue = list(preserved_retired)
    while queue:
        owner = queue.pop()
        try:
            children = stock_refs(bytes(dictionary.raw_entry(owner))) & retired_set
        except Exception:  # noqa: BLE001
            children = set()
        for child in children - preserved_retired:
            preserved_retired.add(child)
            queue.append(child)
    reclaimable_retired = [index for index in retired if index not in preserved_retired]

    candidate = bytearray(parent)
    stock_cursor_before = _stock_phrase_cursor(candidate)
    tail_capacity = BANK_SIZE - stock_cursor_before
    max_rules = len(reclaimable_retired) - len(missing_texts)
    if max_rules <= 0:
        raise BuildError(
            f"no retired slots remain for helper grammar: retired={len(retired)} top={len(missing_texts)}"
        )
    compressed_sequences, helper_rules, compression = repair_pair_compress(
        base_payloads,
        tail_capacity=tail_capacity,
        max_rules=max_rules,
    )
    if compression["max_depth"] > 5:
        raise BuildError(f"helper nesting depth exceeds reviewed ceiling: {compression['max_depth']}")

    # Dictionary ids and phrase storage are independent.  Use reclaimed ids for
    # helper/top-level entries, then pack their payloads across the *union* of all
    # unreachable old phrase regions before falling back to the contiguous tail.
    # This avoids per-slot fragmentation while changing no runtime code.
    ids = sorted(reclaimable_retired)
    if len(ids) < len(helper_rules) + len(missing_texts):
        raise BuildError(
            f"reclaimed id pool too small: ids={len(ids)} helpers={len(helper_rules)} top={len(missing_texts)}"
        )
    helper_ids = ids[:len(helper_rules)]
    top_ids = ids[len(helper_rules):len(helper_rules) + len(missing_texts)]
    helper_slots = {rule_id: helper_ids[rule_id] for rule_id in range(len(helper_rules))}
    top_slot_by_text = dict(zip(sorted(missing_texts), top_ids))
    top_slots = sorted(top_slot_by_text.values())
    text_assignment.update(top_slot_by_text)

    rule_required = {
        rule_id: symbol_size(pair[0]) + symbol_size(pair[1])
        for rule_id, pair in enumerate(helper_rules)
    }
    helper_payloads: dict[int, bytes] = {}
    for rule_id, pair in enumerate(helper_rules):
        payload = symbol_bytes(pair[0], helper_slots) + symbol_bytes(pair[1], helper_slots)
        if len(payload) != rule_required[rule_id] or b"\x00" in payload:
            raise BuildError(f"helper payload encoding drift at rule {rule_id}")
        helper_payloads[rule_id] = payload
    top_payloads = {
        text: b"".join(symbol_bytes(symbol, helper_slots) for symbol in compressed_sequences[text])
        for text in missing_texts
    }
    if any(not payload or b"\x00" in payload for payload in top_payloads.values()):
        raise BuildError("empty/NUL top-level stock payload")
    all_top_bytes = sum(len(payload) + 1 for payload in top_payloads.values())
    if all_top_bytes != compression["final_bytes"]:
        raise BuildError(
            f"top payload accounting drift: all={all_top_bytes} reported={compression['final_bytes']}"
        )

    stock_bank_file = sb + SEG_DICT * BANK_SIZE
    pointers_before = list(Dictionary(candidate).ptrs)

    # Merge all unreachable phrase extents into storage bins.  Every source id
    # in this pool was independently proven to have no external/nested/raw-pair
    # references before the candidate was built.
    raw_regions: list[tuple[int, int]] = []
    for index in reclaimable_retired:
        pointer = pointers_before[index]
        raw = bytes(dictionary.raw_entry(index))
        if not raw:
            continue
        raw_regions.append((pointer, pointer + len(raw) + 1))
    raw_regions.sort()
    merged_regions: list[list[int]] = []
    for left, right in raw_regions:
        if not merged_regions or left > merged_regions[-1][1]:
            merged_regions.append([left, right])
        else:
            merged_regions[-1][1] = max(merged_regions[-1][1], right)
    if any(right > stock_cursor_before for left, right in merged_regions):
        raise BuildError("reclaimed phrase region overlaps live stock tail")
    pool_capacity = sum(right - left for left, right in merged_regions)
    bins = [{"cursor": left, "end": right} for left, right in merged_regions]

    entry_payloads: dict[int, bytes] = {
        helper_slots[rule_id]: payload for rule_id, payload in helper_payloads.items()
    }
    entry_payloads.update({top_slot_by_text[text]: payload for text, payload in top_payloads.items()})
    helper_id_set = set(helper_ids)
    top_id_set = set(top_ids)
    storage_pointer: dict[int, int] = {}
    pool_ids: set[int] = set()
    tail_ids: set[int] = set()
    phrase_write_extents: list[tuple[int, int]] = []

    # Largest-first / best-fit minimizes fragmentation in the reclaimed bins.
    unplaced: list[tuple[int, bytes]] = []
    for index, payload in sorted(entry_payloads.items(), key=lambda item: (-(len(item[1]) + 1), item[0])):
        need = len(payload) + 1
        eligible = [
            (bin_index, info["end"] - info["cursor"])
            for bin_index, info in enumerate(bins)
            if info["end"] - info["cursor"] >= need
        ]
        if not eligible:
            unplaced.append((index, payload))
            continue
        bin_index = min(eligible, key=lambda item: (item[1], item[0]))[0]
        info = bins[bin_index]
        local = info["cursor"]
        info["cursor"] += need
        storage_pointer[index] = local
        pool_ids.add(index)
        phrase_file = stock_bank_file + local
        candidate[phrase_file:phrase_file + len(payload)] = payload
        candidate[phrase_file + len(payload)] = 0
        phrase_write_extents.append((phrase_file, phrase_file + need))

    stock_cursor_after = stock_cursor_before
    for index, payload in sorted(unplaced, key=lambda item: item[0]):
        need = len(payload) + 1
        if stock_cursor_after + need > BANK_SIZE:
            raise BuildError(
                f"combined reclaimed-pool/tail storage overflow at slot {index:04X}: "
                f"need={need} cursor={stock_cursor_after:04X}"
            )
        storage_pointer[index] = stock_cursor_after
        tail_ids.add(index)
        phrase_file = stock_bank_file + stock_cursor_after
        candidate[phrase_file:phrase_file + len(payload)] = payload
        candidate[phrase_file + len(payload)] = 0
        phrase_write_extents.append((phrase_file, phrase_file + need))
        stock_cursor_after += need

    packed_tail_bytes = stock_cursor_after - stock_cursor_before
    if packed_tail_bytes > tail_capacity:
        raise BuildError(
            f"packed tail over capacity after global reclaimed-region packing: "
            f"tail={packed_tail_bytes} capacity={tail_capacity} pool={pool_capacity}"
        )

    stock_pointer_extents: list[tuple[int, int]] = []
    for index, pointer in sorted(storage_pointer.items()):
        pointer_file = stock_bank_file + DICT_PTR_START + index * 2
        struct.pack_into("<H", candidate, pointer_file, pointer)
        stock_pointer_extents.append((pointer_file, pointer_file + 2))
    pointers_after = list(Dictionary(candidate).ptrs)
    expected_changed_pointer_indices = {
        index for index, pointer in storage_pointer.items() if pointers_before[index] != pointer
    }
    changed_pointer_indices = {
        i for i, (before, after) in enumerate(zip(pointers_before, pointers_after)) if before != after
    }
    if changed_pointer_indices != expected_changed_pointer_indices:
        raise BuildError(
            f"stock pointer change set drift: changed={len(changed_pointer_indices)} "
            f"expected={len(expected_changed_pointer_indices)}"
        )

    helper_pool_ids = helper_id_set & pool_ids
    helper_tail_ids = helper_id_set & tail_ids
    top_pool_ids = top_id_set & pool_ids
    top_tail_ids = top_id_set & tail_ids

    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []

    def replace_whole(logical: int, before: bytes, expected: str, family: str) -> None:
        index = text_assignment[expected]
        token = token_from_dict_index(index)
        replacement = token + b"\x01" * (len(before) - len(token))
        if len(replacement) != len(before):
            raise BuildError(f"replacement length drift at {logical:06X}")
        start = sb + logical
        if bytes(candidate[start:start + len(before)]) != before:
            raise BuildError(f"target parent drift at {logical:06X}")
        candidate[start:start + len(before)] = replacement
        target_extents.append((start, start + len(before)))
        applied.append({
            "abs": f"{logical:06X}",
            "family": family,
            "expected_render": expected,
            "before_hex": before.hex().upper(),
            "after_hex": replacement.hex().upper(),
            "stock_index": f"{index:04X}",
            "stock_token_hex": token.hex().upper(),
            "render_sha256": sha(expected.encode("utf-8")),
        })

    for row in bodyonly:
        logical = int(row["abs"], 16)
        before = bytes.fromhex(row["before_hex"])
        replace_whole(logical, before, row["render"], "bodyonly_e518_stock_rehome")
    for row in dup2:
        logical = int(row["abs"], 16)
        before = bytes.fromhex(row["before_hex"])
        replace_whole(logical, before, row["render"], "duplicate_two_byte_visible_lead")
    replace_whole(ONEBYTE_SCREEN, one_live, ONEBYTE_KO, "screen_proven_one_byte_visible_lead")

    # System record keeps the four protected prefix bytes.
    sys_index = text_assignment[SYSTEM_KO]
    sys_token = token_from_dict_index(sys_index)
    sys_replacement = SYSTEM_PREFIX + sys_token + b"\x01" * (len(sys_live) - len(SYSTEM_PREFIX) - len(sys_token))
    sys_start = sb + SYSTEM_RECORD
    if len(sys_replacement) != len(sys_live):
        raise BuildError("system replacement length drift")
    candidate[sys_start:sys_start + len(sys_live)] = sys_replacement
    target_extents.append((sys_start, sys_start + len(sys_live)))
    applied.append({
        "abs": f"{SYSTEM_RECORD:06X}",
        "family": "system_mixed_fullbody",
        "expected_render": SYSTEM_KO,
        "before_hex": sys_live.hex().upper(),
        "after_hex": sys_replacement.hex().upper(),
        "stock_index": f"{sys_index:04X}",
        "stock_token_hex": sys_token.hex().upper(),
        "protected_prefix_hex": SYSTEM_PREFIX.hex().upper(),
        "render_sha256": sha(SYSTEM_KO.encode("utf-8")),
    })

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)

    # Target exact render audit.
    failures: list[dict[str, Any]] = []
    for row in applied:
        logical = int(row["abs"], 16)
        after = bytes.fromhex(row["after_hex"])
        live = candidate_bytes[sb + logical: sb + logical + len(after)]
        offset = len(SYSTEM_PREFIX) if row["family"] == "system_mixed_fullbody" else 0
        render = clean(candidate_dictionary.expand(live[offset:], tbl))
        expected = row["expected_render"]
        if render != expected or visible_japanese(render):
            failures.append({"abs": row["abs"], "family": row["family"], "render": render, "expected": expected})
        if candidate_bytes[sb + logical + len(after)] != 0:
            failures.append({"abs": row["abs"], "family": row["family"], "reason": "terminator_changed"})

    # Every body-only/dup2 target must now start with a native two-byte token,
    # not E5 18.  Protected neighbor records remain byte-exact.
    for row in applied:
        if row["family"] == "system_mixed_fullbody":
            continue
        logical = int(row["abs"], 16)
        if candidate_bytes[sb + logical:sb + logical + 2] == b"\xE5\x18":
            failures.append({"abs": row["abs"], "reason": "E518_not_rehomed"})

    protected_neighbors = [ONEBYTE_PARTNER_PREV, ONEBYTE_PARTNER_NEXT]
    neighbor_failures = []
    for logical in protected_neighbors:
        row = by_abs[logical]
        before = record_live(parent, sb, row)
        after = candidate_bytes[sb + logical:sb + logical + len(before)]
        if after != before:
            neighbor_failures.append(f"{logical:06X}")

    # Stage-1 short/fixed metadata repairs must remain byte-exact.  The three
    # stage-1 full-body corrections may be intentionally rehomed, so audit them
    # by rendered text instead of token bytes.
    short_metadata_abs = {int(row["abs"], 16) for row in parent_report.get("metadata_targets") or []}
    if not short_metadata_abs:
        short_metadata_abs = {
            int(row["abs"], 16)
            for row in parent_report.get("applied_metadata") or []
            if row.get("abs")
        }
    # Fallback to the source target sheet count when report uses compact output.
    if not short_metadata_abs:
        with (SCRIPT / "battle_dialogue_short_fixed_metadata_targets.csv").open(encoding="utf-8-sig", newline="") as handle:
            short_metadata_abs = {int(row["abs"], 16) for row in csv.DictReader(handle)}
    if len(short_metadata_abs) != EXPECTED_SHORT_METADATA:
        raise BuildError(f"short metadata target set drifted: {len(short_metadata_abs)}")
    short_metadata_failures = []
    for logical in sorted(short_metadata_abs):
        row = by_abs[logical]
        length = len(bytes.fromhex(row["current_payload_hex"]))
        if candidate_bytes[sb + logical:sb + logical + length] != parent[sb + logical:sb + logical + length]:
            short_metadata_failures.append(f"{logical:06X}")

    # New stock entries must independently render to the intended Korean text.
    created_slot_failures: list[dict[str, Any]] = []
    for text, index in sorted(top_slot_by_text.items()):
        try:
            raw = bytes(candidate_dictionary.raw_entry(index))
            rendered = clean(candidate_dictionary.expand(raw, tbl))
        except Exception as exc:  # noqa: BLE001
            created_slot_failures.append({"index": f"{index:04X}", "text": text, "error": str(exc)})
            continue
        if raw != top_payloads[text] or rendered != text or visible_japanese(rendered):
            created_slot_failures.append({
                "index": f"{index:04X}",
                "text": text,
                "raw": raw.hex().upper(),
                "expected_raw": top_payloads[text].hex().upper(),
                "render": rendered,
            })
    helper_slot_failures: list[dict[str, Any]] = []
    for rule_id, index in sorted(helper_slots.items()):
        try:
            raw = bytes(candidate_dictionary.raw_entry(index))
            rendered = candidate_dictionary.expand(raw, tbl)
        except Exception as exc:  # noqa: BLE001
            helper_slot_failures.append({"rule": rule_id, "index": f"{index:04X}", "error": str(exc)})
            continue
        if (
            raw != helper_payloads[rule_id]
            or "<BADDICT:" in rendered
            or visible_japanese(rendered)
        ):
            helper_slot_failures.append({
                "rule": rule_id,
                "index": f"{index:04X}",
                "raw": raw.hex().upper(),
                "expected_raw": helper_payloads[rule_id].hex().upper(),
                "render": rendered,
            })

    # Reclaimed ids may now be referenced only by the intended records or by
    # the newly-created acyclic helper grammar.
    top_set = set(top_slots)
    helper_set = set(helper_slots.values())
    selected_set = top_set | helper_set
    candidate_external = external_occurrence_map(candidate_bytes, ext3_aware=True, wanted=selected_set)
    candidate_nested = nested_occurrence_map(candidate_dictionary, wanted=selected_set, ext3_aware=True)
    expected_sites: dict[int, set[str]] = defaultdict(set)
    for row in applied:
        index = int(row["stock_index"], 16)
        if index in top_set:
            expected_sites[index].add(row["abs"].upper())

    created_payload_by_owner: dict[int, bytes] = {
        top_slot_by_text[text]: payload for text, payload in top_payloads.items()
    }
    created_payload_by_owner.update({
        helper_slots[rule_id]: payload for rule_id, payload in helper_payloads.items()
    })
    expected_helper_parents: dict[int, set[str]] = defaultdict(set)
    for owner, payload in created_payload_by_owner.items():
        for unit in native_units(payload):
            raw = bytes(unit[1])
            if len(raw) != 2 or not (0xF0 <= raw[0] <= 0xFE):
                continue
            child = dict_index_from_token(raw[0], raw[1])
            if child in helper_set:
                expected_helper_parents[child].add(f"{owner:04X}")

    retired_reference_failures = []
    for index in sorted(top_set):
        actual_external = {
            str(ref.get("record_abs") or "").upper() for ref in candidate_external.get(index, [])
        }
        actual_nested = {str(ref.get("parent") or "").upper() for ref in candidate_nested.get(index, [])}
        if actual_external != expected_sites.get(index, set()) or actual_nested:
            retired_reference_failures.append({
                "index": f"{index:04X}",
                "kind": "top_level",
                "expected_external": sorted(expected_sites.get(index, set())),
                "actual_external": sorted(actual_external),
                "actual_nested": sorted(actual_nested),
            })
    for index in sorted(helper_set):
        actual_external = {
            str(ref.get("record_abs") or "").upper() for ref in candidate_external.get(index, [])
        }
        actual_nested = {str(ref.get("parent") or "").upper() for ref in candidate_nested.get(index, [])}
        if actual_external or actual_nested != expected_helper_parents.get(index, set()):
            retired_reference_failures.append({
                "index": f"{index:04X}",
                "kind": "helper",
                "expected_nested": sorted(expected_helper_parents.get(index, set())),
                "actual_external": sorted(actual_external),
                "actual_nested": sorted(actual_nested),
            })

    # Diff confinement to target payloads, selected stock pointers, packed
    # reclaimed/tail phrase writes, and checksum only.
    allowed = (
        target_extents
        + stock_pointer_extents
        + phrase_write_extents
        + [(len(parent) - 2, len(parent))]
    )
    runs = diff_runs(parent, candidate_bytes)
    unaccounted = [
        {"start": f"{left:07X}", "end_exclusive": f"{right:07X}"}
        for left, right in runs if not covered((left, right), allowed)
    ]

    old_ext3_exact = parent[0x11 * BANK_SIZE:0x21 * BANK_SIZE] == candidate_bytes[0x11 * BANK_SIZE:0x21 * BANK_SIZE]
    five_bank_exact = parent[0x21 * BANK_SIZE:0x26 * BANK_SIZE] == candidate_bytes[0x21 * BANK_SIZE:0x26 * BANK_SIZE]
    runtime_7a_exact = parent[0x7A * BANK_SIZE:0x7B * BANK_SIZE] == candidate_bytes[0x7A * BANK_SIZE:0x7B * BANK_SIZE]
    p7f = parent[0x7F * BANK_SIZE:0x80 * BANK_SIZE]
    c7f = candidate_bytes[0x7F * BANK_SIZE:0x80 * BANK_SIZE]
    runtime_7f_exact_except_checksum = p7f[:-2] == c7f[:-2]

    if (
        failures
        or created_slot_failures
        or helper_slot_failures
        or neighbor_failures
        or short_metadata_failures
        or retired_reference_failures
        or unaccounted
    ):
        raise BuildError(
            f"static gate failed targets={len(failures)} created={len(created_slot_failures)} "
            f"helpers={len(helper_slot_failures)} neighbors={len(neighbor_failures)} "
            f"short={len(short_metadata_failures)} retired={len(retired_reference_failures)} "
            f"unaccounted={len(unaccounted)}"
        )
    if not old_ext3_exact or not five_bank_exact or not runtime_7a_exact or not runtime_7f_exact_except_checksum:
        raise BuildError("runtime/dictionary bank preservation gate failed")
    if sys_replacement[:len(SYSTEM_PREFIX)] != SYSTEM_PREFIX:
        raise BuildError("system protected prefix changed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save)
    atomic_bytes(SRAM_SAVE, save)

    body_csv_rows = [
        {
            "abs": row["abs"],
            "sandwich_metadata": row["sandwich_metadata"],
            "previous_record": row["previous_record"],
            "next_record": row["next_record"],
            "before_hex": row["before_hex"],
            "render": row["render"],
            "stock_index": next(x["stock_index"] for x in applied if x["abs"] == row["abs"]),
        }
        for row in bodyonly
    ]
    dup_csv_rows = [
        {
            "abs": row["abs"],
            "removed_lead_hex": row["removed_lead_hex"],
            "removed_lead_render": row["removed_lead_render"],
            "sandwich_metadata": row["sandwich_metadata"],
            "previous_record": row["previous_record"],
            "next_record": row["next_record"],
            "before_hex": row["before_hex"],
            "render": row["render"],
            "stock_index": next(x["stock_index"] for x in applied if x["abs"] == row["abs"]),
        }
        for row in dup2
    ]
    write_csv(BODYONLY_CSV, body_csv_rows, ["abs", "sandwich_metadata", "previous_record", "next_record", "before_hex", "render", "stock_index"])
    write_csv(DUP2_CSV, dup_csv_rows, ["abs", "removed_lead_hex", "removed_lead_render", "sandwich_metadata", "previous_record", "next_record", "before_hex", "render", "stock_index"])

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_dialogue_runtime_integrated_cleanup_candidate.py",
        "ok": True,
        "promotion_allowed": False,
        "purpose": "runtime-safe stock-token rehome for battle continuation/duplicate-lead families plus screen-proven system mixed line",
        "inputs": {
            "main_tip": ident(MAIN, main_tip),
            "short_fixed_parent": ident(PARENT_ROM, parent),
            "short_fixed_report": ident(PARENT_REPORT),
            "saveram": ident(PARENT_SAVE, save),
        },
        "outputs": {
            "candidate_rom": ident(OUT_ROM, candidate_bytes),
            "candidate_saveram": ident(OUT_SAVE, save),
            "sram_mirror": ident(SRAM_SAVE, save),
            "bodyonly_targets": ident(BODYONLY_CSV),
            "duplicate_lead_targets": ident(DUP2_CSV),
        },
        "counts": {
            "bodyonly_e518_stock_rehome": len(bodyonly),
            "duplicate_two_byte_visible_lead": len(dup2),
            "screen_proven_one_byte_visible_lead": 1,
            "system_mixed_fullbody": 1,
            "total_stage2_records": len(applied),
            "unique_target_phrases": len(target_texts),
            "existing_exact_stock_phrases": len(target_texts) - len(missing_texts),
            "new_top_level_stock_slots": len(top_slots),
            "top_level_reclaimed_pool_slots": len(top_pool_ids),
            "top_level_tail_slots": len(top_tail_ids),
            "new_helper_stock_slots": len(helper_slots),
            "helper_reclaimed_pool_slots": len(helper_pool_ids),
            "helper_tail_slots": len(helper_tail_ids),
            "reclaimed_phrase_pool_bytes": pool_capacity,
            "total_reclaimed_stock_slots": len(top_slots) + len(helper_slots),
            "strong_retired_slots_available": len(retired),
            "preserved_retired_compression_ingredients": len(preserved_retired),
            "reclaimable_retired_slots": len(reclaimable_retired),
            "packed_tail_bytes": packed_tail_bytes,
            "packed_tail_capacity": tail_capacity,
            "helper_rules": compression["rules"],
            "helper_max_depth": compression["max_depth"],
            "precompression_tail_bytes": compression["initial_bytes"],
            "stage1_short_fixed_metadata_preserved": len(short_metadata_abs),
            "diff_runs": len(runs),
            "unaccounted_diff_runs": len(unaccounted),
        },
        "screen_anchors": {
            "black_portrait_1": {"abs": "5D1E57", "render": next(x["expected_render"] for x in applied if x["abs"] == "5D1E57")},
            "black_portrait_2": {"abs": "5D1E6C", "render": next(x["expected_render"] for x in applied if x["abs"] == "5D1E6C")},
            "duplicate_sankai": {"abs": "5EB12A", "render": next(x["expected_render"] for x in applied if x["abs"] == "5EB12A")},
            "one_byte_full": {"abs": "5E4F43", "render": ONEBYTE_KO, "partners_exact": not neighbor_failures},
            "system": {"abs": "6106D5", "prefix_hex": SYSTEM_PREFIX.hex().upper(), "render": SYSTEM_KO},
        },
        "gates": {
            "target_render_exact": not failures,
            "visible_japanese_in_targets_zero": not failures,
            "created_top_level_stock_render_exact": not created_slot_failures,
            "helper_stock_entries_valid": not helper_slot_failures,
            "helper_depth_le_existing_stock_max_5": compression["max_depth"] <= 5,
            "screen_partner_records_exact": not neighbor_failures,
            "stage1_short_fixed_metadata_exact": not short_metadata_failures,
            "retired_slot_reference_exact": not retired_reference_failures,
            "unaccounted_diff_runs_zero": not unaccounted,
            "old_ext3_banks_11_20_exact": old_ext3_exact,
            "five_bank_alias_banks_21_25_exact": five_bank_exact,
            "runtime_7a_exact": runtime_7a_exact,
            "runtime_7f_exact_except_checksum": runtime_7f_exact_except_checksum,
            "system_prefix_exact": sys_replacement[:len(SYSTEM_PREFIX)] == SYSTEM_PREFIX,
        },
        "checksum": f"{checksum:04X}",
        "applied": applied,
    }
    atomic_text(REPORT, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "ok": True,
        "candidate": ident(OUT_ROM, candidate_bytes),
        "counts": report["counts"],
        "screen_anchors": report["screen_anchors"],
        "checksum": report["checksum"],
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
