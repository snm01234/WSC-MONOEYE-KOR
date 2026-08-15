#!/usr/bin/env python3
"""Build a current-main candidate that changes UI label 近全 to 근전 only.

The 75:B3FD record is only 3 bytes, so a normal Hangul marker sequence cannot
fit.  Reuse the proven-unreachable stock dictionary slot 0B68 and its existing
5-byte storage.  Its payload becomes two otherwise-unused compact glyph codes
(E511/E51B) whose bitmaps are copied from the installed Hangul glyphs 근/전.
75:B3FD becomes the 2-byte stock token plus one 01 pad.  No other UI label,
including adjacent 射全 at 75:B401, is changed.

Main TIP and live SaveRAM are never overwritten by this builder.
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

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_terrain_space_abaoaqu_compact_glyph_candidate import (  # noqa: E402
    compact_glyph_offset,
    hangul_glyph_offset,
    read_glyph,
    select_steal_codes,
)
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "near_all_geunjeon_candidate.wsc"
OUT_SAVE = ROOT / "sram/near_all_geunjeon_candidate.sav"
REPORT = PATCH / "near_all_geunjeon_candidate_report.json"

EXPECTED_MAIN = "92fea67dc128d28a6c95e91faaeb21c8632547d23b8baace57cf904f3df3a40c"
EXPECTED_ORIGINAL = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
TARGET = 0x75B3FD
NEIGHBOR = 0x75B401
TARGET_BEFORE = bytes.fromhex("E04A86")
NEIGHBOR_BEFORE = bytes.fromhex("E08F86")
RETIRED_SLOT = 0x0B68
EXPECTED_RETIRED_RAW = bytes.fromhex("2F2C10E007")  # スオ－ノ; 5-byte dead payload
STOLEN_CODES = (0xE511, 0xE51B)
HANGUL_SOURCE = {"근": 0xE8B0, "전": 0xE745}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=32)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(before):
        if before[i] == after[i]:
            i += 1
            continue
        start = i
        while i < len(before) and before[i] != after[i]:
            i += 1
        runs.append((start, i))
    return runs


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    lo, hi = run
    pos = lo
    for a, b in sorted(allowed):
        if b <= pos:
            continue
        if a > pos:
            return False
        pos = max(pos, b)
        if pos >= hi:
            return True
    return pos >= hi


def main() -> int:
    parent = MAIN.read_bytes()
    save = MAIN_SAVE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN:
        raise BuildError(f"main identity drifted: {sha(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")
    if sha(original) != EXPECTED_ORIGINAL:
        raise BuildError("Original identity drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)
    sb = stock_base(parent)

    target_payload, target_term = payload_at(parent, TARGET)
    neighbor_payload, neighbor_term = payload_at(parent, NEIGHBOR)
    if target_payload != TARGET_BEFORE or target_term != TARGET + len(TARGET_BEFORE):
        raise BuildError("75B3FD 近全 record drifted")
    if neighbor_payload != NEIGHBOR_BEFORE or neighbor_term != NEIGHBOR + len(NEIGHBOR_BEFORE):
        raise BuildError("75B401 射全 neighbor drifted")

    # Reuse only a current-runtime-unreachable stock slot.
    wanted = {RETIRED_SLOT}
    external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(dictionary, wanted=wanted, ext3_aware=True)
    raw_hits = _raw_pair_hits(parent, [RETIRED_SLOT])
    if external.get(RETIRED_SLOT) or nested.get(RETIRED_SLOT) or raw_hits.get(RETIRED_SLOT):
        raise BuildError("0B68 is no longer unreachable")
    old_raw = bytes(dictionary.raw_entry(RETIRED_SLOT))
    if old_raw != EXPECTED_RETIRED_RAW:
        raise BuildError(f"0B68 payload drifted: {old_raw.hex().upper()}")
    entry_abs = int(dictionary.entry_abs(RETIRED_SLOT))
    ptr = dictionary.ptrs[RETIRED_SLOT]
    aliases = [i for i, value in enumerate(dictionary.ptrs) if value == ptr]
    interiors = [i for i, value in enumerate(dictionary.ptrs) if ptr < value <= ptr + len(old_raw)]
    if aliases != [RETIRED_SLOT] or interiors:
        raise BuildError(f"0B68 storage alias hazard aliases={aliases} interiors={interiors}")

    # The same project scanner used by the terrain compact-glyph repair proves
    # E511/E51B are absent from both Original and current semantic text graphs.
    chosen = select_steal_codes(parent, original, dictionary, original_dictionary, tbl)
    got_codes = tuple(int(row["code"]) for row in chosen[:2])
    if got_codes != STOLEN_CODES:
        raise BuildError(f"unused compact-code set drifted: {[f'{x:04X}' for x in got_codes]}")

    source_glyphs = {
        ch: read_glyph(parent, hangul_glyph_offset(parent, code))
        for ch, code in HANGUL_SOURCE.items()
    }
    new_phrase = bytes.fromhex("E511E51B")
    if len(new_phrase) > len(old_raw):
        raise BuildError("0B68 cannot hold two compact glyph codes")

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    glyph_rows: list[dict[str, Any]] = []
    for ch, code in zip(("근", "전"), STOLEN_CODES):
        off = compact_glyph_offset(candidate, code)
        glyph = source_glyphs[ch]
        before = bytes(candidate[off : off + 16])
        if before == glyph:
            raise BuildError(f"{code:04X} already contains {ch}")
        candidate[off : off + 16] = glyph
        allowed.append((off, off + 16))
        glyph_rows.append(
            {
                "hangul": ch,
                "stolen_code": f"{code:04X}",
                "compact_offset": f"{off:07X}",
                "source_hangul_code": f"{HANGUL_SOURCE[ch]:04X}",
                "before_hex": before.hex().upper(),
                "after_hex": glyph.hex().upper(),
            }
        )

    # Rewrite the dead dictionary phrase in-place, preserving its pointer.
    candidate[entry_abs : entry_abs + len(new_phrase)] = new_phrase
    candidate[entry_abs + len(new_phrase)] = 0
    if len(old_raw) > len(new_phrase):
        candidate[entry_abs + len(new_phrase) + 1 : entry_abs + len(old_raw) + 1] = b"\xFF" * (len(old_raw) - len(new_phrase))
    allowed.append((entry_abs, entry_abs + len(old_raw) + 1))

    token = token_from_dict_index(RETIRED_SLOT)
    if len(token) != 2 or 0 in token:
        raise BuildError("0B68 token is unsafe")
    replacement = token + b"\x01"
    at = sb + TARGET
    candidate[at : at + 3] = replacement
    allowed.append((at, at + 3))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)

    # Static checks bind the intended runtime path.  TBL still names E511/E51B
    # by their old kanji because this is a font-glyph steal, so verify bytes and
    # glyph equality rather than TBL text for the target phrase.
    after_dict = make_dictionary_ext3(result, ext_meta, ext3_meta)
    out_payload, out_term = payload_at(result, TARGET)
    out_neighbor, out_neighbor_term = payload_at(result, NEIGHBOR)
    if out_payload != replacement or out_term != target_term:
        raise BuildError("75B3FD retarget failed")
    if out_neighbor != neighbor_payload or out_neighbor_term != neighbor_term:
        raise BuildError("75B401 collateral")
    if bytes(after_dict.raw_entry(RETIRED_SLOT)) != new_phrase:
        raise BuildError("0B68 compact phrase write failed")
    if after_dict.ptrs[RETIRED_SLOT] != ptr:
        raise BuildError("0B68 pointer changed")
    for row in glyph_rows:
        off = int(row["compact_offset"], 16)
        ch = str(row["hangul"])
        if bytes(result[off : off + 16]) != source_glyphs[ch]:
            raise BuildError(f"glyph verify failed for {ch}")

    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:10]}")
    stored = int.from_bytes(result[-2:], "little")
    if stored != (sum(result[:-2]) & 0xFFFF) or stored != checksum:
        raise BuildError("checksum failed")

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_SAVE, save)
    if MAIN.read_bytes() != parent or MAIN_SAVE.read_bytes() != save:
        raise BuildError("live main mutated")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_near_all_geunjeon_candidate.py",
        "ok": True,
        "status": "candidate_static_verified",
        "parent": identity(MAIN, parent),
        "candidate": {**identity(OUT_ROM, result), "ws_checksum": f"{checksum:04X}"},
        "candidate_saveram": identity(OUT_SAVE, save),
        "target": {
            "abs": f"{TARGET:06X}",
            "before": "近全",
            "after": "근전",
            "before_hex": TARGET_BEFORE.hex().upper(),
            "after_hex": replacement.hex().upper(),
            "stock_index": f"{RETIRED_SLOT:04X}",
            "stock_token": token.hex().upper(),
            "dictionary_payload_hex": new_phrase.hex().upper(),
            "dictionary_pointer_unchanged": True,
        },
        "neighbor_75B401": {
            "text": "射全",
            "payload_hex": neighbor_payload.hex().upper(),
            "byte_exact_unchanged": True,
        },
        "retired_slot": {
            "index": f"{RETIRED_SLOT:04X}",
            "old_raw_hex": old_raw.hex().upper(),
            "new_raw_hex": new_phrase.hex().upper(),
            "entry_abs": f"{entry_abs:07X}",
            "old_capacity": len(old_raw),
            "external_refs_before": 0,
            "nested_refs_before": 0,
            "raw_pair_hits_before": 0,
            "aliases": [f"{i:04X}" for i in aliases],
            "interior_pointers": interiors,
        },
        "glyphs": glyph_rows,
        "checks": {
            "target_extent_preserved": True,
            "target_terminator_preserved": True,
            "neighbor_75B401_unchanged": True,
            "retired_slot_unreachable_before": True,
            "retired_slot_pointer_unchanged": True,
            "stolen_compact_codes_unused_in_original_and_parent": True,
            "diff_allowlist_clean": True,
            "checksum_valid": True,
            "main_unchanged": True,
            "live_saveram_unchanged": True,
        },
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(b - a for a, b in runs),
            "unexpected_runs": 0,
            "runs_detail": [
                {"start": f"{a:07X}", "end_exclusive": f"{b:07X}", "length": b - a}
                for a, b in runs
            ],
        },
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "target": report["target"],
        "neighbor": report["neighbor_75B401"],
        "glyphs": report["glyphs"],
        "diff": report["diff"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
