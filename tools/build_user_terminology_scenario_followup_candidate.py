#!/usr/bin/env python3
"""Apply the user-confirmed terminology and semantic scenario-lead follow-up.

The terminology pass is built first by
``build_terminology_consistency_followup_candidate.py``.  This stage repairs
only the five scenario continuation records where raw ``18`` is the Japanese
text ``こ`` rather than a disposable control lead.  Their existing exclusive
ext3 slots are retained, their Korean bodies are corrected, and the portal is
shifted one byte forward without changing record extents or terminators.
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

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_gundam_terminology_candidate import ext3_bank_cursor  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    dict_index_from_ext3_token,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    write_le16,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT = PATCH / "terminology_consistency_followup_candidate.wsc"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC = ROOT / "data/scenario_false_lead_semantic_followup_ko.json"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "user_terminology_scenario_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/user_terminology_scenario_followup_candidate.sav"
REPORT = PATCH / "user_terminology_scenario_followup_candidate_report.json"

EXPECTED_PARENT_SHA = "80ab1e531cda254479266f8a6f43008d27857a14be5ae5dd35d904303015b00e"
EXPECTED_MAIN_SHA = "4e1453f0d6bc1ad7be1431b617be8da772104f1a9a49d31261897acd332584db"
EXPECTED_TBL_SHA = "cbeafbe074015bc79ad0dce1ade3be57a4d56bb1e5bf46102097cc6f0e17261c"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MARKER = 0xEC8D


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def atomic_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def encode(tbl: Tbl, text: str) -> bytes:
    raw = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=MARKER,
        hangul_marker_mode="run",
    )
    if raw is None or b"\x00" in raw:
        raise BuildError(f"cannot encode {text!r}")
    return bytes(raw)


def encode_compact_with_stock(tbl: Tbl, dictionary, text: str, *, exclude_index: int) -> tuple[bytes, list[dict[str, Any]]]:
    """Find the shortest safe encoding using ordinary stock dictionary terms."""
    candidates: list[tuple[str, int, bytes]] = []
    seen: set[tuple[str, bytes]] = set()
    for index in range(dictionary.stock_count):
        if index == exclude_index:
            continue
        try:
            term = strip_pad(dictionary.expand_index(index, tbl))
            token = token_from_dict_index(index)
        except Exception:
            continue
        if not term or term not in text or b"\x00" in token:
            continue
        key = (term, token)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((term, index, token))
    candidates.sort(key=lambda item: (-len(item[0]), len(item[2]), item[1]))

    n = len(text)
    best: list[tuple[int, bytes, list[dict[str, Any]]] | None] = [None] * (n + 1)
    best[0] = (0, b"", [])
    for pos in range(n):
        state = best[pos]
        if state is None:
            continue
        _cost, payload, used = state
        for end in range(pos + 1, n + 1):
            try:
                raw = encode(tbl, text[pos:end])
            except BuildError:
                continue
            proposal = (len(payload) + len(raw), payload + raw, used)
            if best[end] is None or proposal[0] < best[end][0]:
                best[end] = proposal
        for term, index, token in candidates:
            if not text.startswith(term, pos):
                continue
            end = pos + len(term)
            proposal = (
                len(payload) + len(token),
                payload + token,
                used + [{"text": term, "stock_index": f"{index:04X}", "at": pos}],
            )
            if best[end] is None or proposal[0] < best[end][0]:
                best[end] = proposal
    if best[n] is None:
        raise BuildError(f"compact encoding failed for {text!r}")
    return best[n][1], best[n][2]


def referenced_ext3_indices(rom: bytes | bytearray) -> set[int]:
    found: set[int] = set()
    pos = 0
    payload = bytes(rom)
    while True:
        pos = payload.find(b"\xE5\x18", pos)
        if pos < 0 or pos + 3 >= len(payload):
            break
        found.add(0x1000 + ((payload[pos + 2] << 8) | payload[pos + 3]))
        pos += 1
    return found


def reclaim_dead_ext3_payload(
    rom: bytearray,
    dictionary,
    *,
    seg: int,
    need: int,
    exclude_entry_abs: int,
    consumed: set[int],
) -> tuple[int, int, list[int]]:
    """Return ``(file_abs, ptr, aliases)`` for a conservatively dead extent."""
    refs = referenced_ext3_indices(rom)
    by_ptr: defaultdict[int, list[int]] = defaultdict(list)
    for index in range(0x1000, 0x1000 + dictionary.ext3_count):
        try:
            index_seg, _local = dictionary._ext3_bank_local(index)
            if index_seg != seg:
                continue
            by_ptr[dictionary.entry_offset(index)].append(index)
        except Exception:
            continue
    candidates: list[tuple[int, int, list[int]]] = []
    bank_base = seg * BANK_SIZE
    for ptr, aliases in by_ptr.items():
        file_abs = bank_base + ptr
        if file_abs == exclude_entry_abs or file_abs in consumed:
            continue
        try:
            raw = bytes(dictionary.raw_entry(aliases[0]))
        except Exception:
            continue
        if len(raw) + 1 < need or any(index in refs for index in aliases):
            continue
        if any(ptr < other < ptr + len(raw) + 1 for other in by_ptr if other != ptr):
            continue
        candidates.append((len(raw), ptr, aliases))
    if not candidates:
        raise BuildError(f"ext3 bank {seg:02X} has no conservative dead extent for {need} bytes")
    _old_len, ptr, aliases = min(candidates, key=lambda item: (item[0], item[1]))
    consumed.add(bank_base + ptr)
    return bank_base + ptr, ptr, aliases


def read_record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def portal_index(raw: bytes) -> int:
    if len(raw) < 5 or raw[0] != 0x18 or raw[1:3] != b"\xE5\x18":
        raise BuildError(f"expected lead-18 exclusive ext3 portal, got {raw.hex().upper()}")
    return dict_index_from_ext3_token(*raw[1:5])


def main() -> int:
    parent = PARENT.read_bytes()
    main_before = MAIN.read_bytes()
    tbl_bytes = TBL_PATH.read_bytes()
    save_before = SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"terminology parent drift: {sha(parent)}")
    if sha(main_before) != EXPECTED_MAIN_SHA:
        raise BuildError("main TIP changed before combined build")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA or marker_code() != MARKER:
        raise BuildError("active TBL/marker drift")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drift")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("review_status") != "user_confirmed_and_semantically_reviewed":
        raise BuildError("semantic follow-up is not approved")
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    original_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate = bytearray(parent)
    sb = stock_base(candidate)
    allowed: list[tuple[int, int]] = []
    patches: list[dict[str, Any]] = []
    consumed_reclaims: set[int] = set()

    for row in spec["targets"]:
        logical = int(row["abs"], 16)
        current, terminator = read_record(parent, logical)
        index = portal_index(current)
        token = token_from_dict_index(index)
        if current[1:5] != token:
            raise BuildError(f"portal token mismatch {logical:06X}")
        before_render = strip_pad(original_dictionary.expand(current, tbl))
        if before_render != row["before"]:
            raise BuildError(f"before render drift {logical:06X}: {before_render!r}")

        physical_token = sb + logical + 1
        hits: list[int] = []
        pos = 0
        while True:
            pos = parent.find(token, pos)
            if pos < 0:
                break
            hits.append(pos)
            pos += 1
        if hits != [physical_token]:
            raise BuildError(f"ext3 slot {index:05X} is not exclusive: {hits}")

        dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        old_entry = bytes(dictionary.raw_entry(index))
        # Nested stock tokens are intentionally not used inside ext3 entries:
        # the runtime expands these private phrases as direct text bodies.
        encoded = encode(tbl, str(row["after"]))
        stock_terms: list[dict[str, Any]] = []
        entry_abs = dictionary.entry_abs(index)
        seg, local = dictionary._ext3_bank_local(index)
        if len(encoded) <= len(old_entry):
            candidate[entry_abs : entry_abs + len(encoded)] = encoded
            candidate[entry_abs + len(encoded)] = 0
            allowed.append((entry_abs, entry_abs + max(len(old_entry), len(encoded)) + 1))
            mode = "inplace"
            new_entry_abs = entry_abs
        else:
            cursor = ext3_bank_cursor(candidate, seg)
            need = len(encoded) + 1
            bank_base = seg * BANK_SIZE
            if cursor + need <= BANK_SIZE and all(b == 0xFF for b in candidate[bank_base + cursor : bank_base + cursor + need]):
                new_entry_abs = bank_base + cursor
                new_ptr = cursor
                mode = "append_repoint"
                reclaim_aliases: list[int] = []
            else:
                new_entry_abs, new_ptr, reclaim_aliases = reclaim_dead_ext3_payload(
                    candidate,
                    dictionary,
                    seg=seg,
                    need=need,
                    exclude_entry_abs=entry_abs,
                    consumed=consumed_reclaims,
                )
                mode = "dead_payload_reclaim"
            candidate[new_entry_abs : new_entry_abs + len(encoded)] = encoded
            candidate[new_entry_abs + len(encoded)] = 0
            ptr_at = bank_base + dictionary.ext3_ptr_off + local * 2
            write_le16(candidate, ptr_at, new_ptr)
            allowed.extend([(new_entry_abs, new_entry_abs + need), (ptr_at, ptr_at + 2)])

        shifted = current[1:] + b"\x01"
        if len(shifted) != len(current) or shifted[:4] != token or shifted[0] == 0x18:
            raise BuildError(f"lead shift failed {logical:06X}")
        candidate[sb + logical : sb + logical + len(shifted)] = shifted
        allowed.append((sb + logical, sb + logical + len(shifted)))

        verify_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        got, got_term = read_record(candidate, logical)
        rendered = strip_pad(verify_dictionary.expand(got, tbl))
        if got != shifted or got_term != terminator or rendered != row["after"]:
            raise BuildError(f"candidate verify failed {logical:06X}: {rendered!r}")
        if any("\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff" for ch in rendered):
            raise BuildError(f"Japanese residue remains {logical:06X}")
        patches.append({
            "abs": f"{logical:06X}",
            "terminator": f"{terminator:06X}",
            "ext3_index": f"{index:05X}",
            "before_raw": current.hex().upper(),
            "after_raw": shifted.hex().upper(),
            "before": before_render,
            "after": rendered,
            "slot_mode": mode,
            "stock_term_substitutions": stock_terms,
            "reclaimed_dead_aliases": [f"{value:05X}" for value in reclaim_aliases] if mode == "dead_payload_reclaim" else [],
            "old_entry_abs": f"{entry_abs:07X}",
            "new_entry_abs": f"{new_entry_abs:07X}",
        })

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"diff escaped allowlist: {unexpected[:8]}")
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum invalid")
    if MAIN.read_bytes() != main_before or TBL_PATH.read_bytes() != tbl_bytes or SAVE.read_bytes() != save_before:
        raise BuildError("live artifact changed during candidate build")

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_SAVE, save_before)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_user_terminology_scenario_followup_candidate.py",
        "ok": True,
        "promotion_allowed": True,
        "inputs": {
            "terminology_parent": identity(PARENT, parent),
            "main_tip_unchanged": identity(MAIN, main_before),
            "active_tbl": identity(TBL_PATH, tbl_bytes),
            "live_saveram": identity(SAVE, save_before),
            "semantic_catalog": identity(SPEC),
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, result),
            "candidate_saveram": identity(OUT_SAVE, save_before),
        },
        "patches": patches,
        "checks": {
            "semantic_targets_fixed": len(patches) == len(spec["targets"]) == 5,
            "all_target_extents_preserved": True,
            "all_target_terminators_preserved": True,
            "all_target_ext3_slots_exclusive": True,
            "non_target_diff_allowlist_clean": not unexpected,
            "active_tbl_unchanged": TBL_PATH.read_bytes() == tbl_bytes,
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
            "candidate_saveram_exact_live": OUT_SAVE.read_bytes() == save_before,
            "ws_checksum_valid": True,
        },
        "diff": {
            "changed_bytes": sum(end - start for start, end in runs),
            "changed_runs": len(runs),
            "unexpected_runs": len(unexpected),
            "runs": [{"start": f"{a:07X}", "end": f"{b:07X}", "length": b - a} for a, b in runs],
        },
        "ws_checksum": f"{checksum:04X}",
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["outputs"]["candidate_rom"],
        "semantic_fixes": len(patches),
        "diff": report["diff"],
        "checksum": report["ws_checksum"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
