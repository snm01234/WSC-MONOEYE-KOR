#!/usr/bin/env python3
"""Build a name-mapping consistency candidate from the current main TIP.

The live main TIP and SaveRAM are immutable inputs.  Only dictionary-owned
payloads/pointers and the WonderSwan checksum may change in the candidate.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_gundam_terminology_standard import (
    dictionary_hits,
    entries as standard_entries,
    five_bank_dictionary_hits,
    forbidden_index,
    norm,
    rendered_record_hits,
    source_hits,
)
from audit_main_tip_name_mapping_consistency import (
    bank5c_hits,
    untranslated_standard_dictionary,
)
from build_terminology_consistency_followup_candidate import (
    canonicalize,
    covered,
    diff_runs,
    encode,
    patch_ext3,
    patch_stock,
    semantic_norm,
)
from build_gundam_terminology_candidate import preserve_ambiguous_direct_codes
from hangul_marker import marker_code
from monoeye_rom import Tbl, load_rom, token_from_dict_index, update_ws_checksum

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "main_tip_name_mapping_consistency_candidate.wsc"
OUT_SAVE = ROOT / "sram/main_tip_name_mapping_consistency_candidate.sav"
REPORT = PATCH / "main_tip_name_mapping_consistency_candidate_report.json"

EXPECTED_MAIN_SHA = "d7543ad4a62d9e7a9687583e85005dc4ca137e6fa62238eb70e58492248985c9"
EXPECTED_TBL_SHA = "cbeafbe074015bc79ad0dce1ade3be57a4d56bb1e5bf46102097cc6f0e17261c"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MARKER = 0xEC8D

REPLACEMENTS = [
    ("프라나간", "플라나간", "flanagan"),
    ("플래나간", "플라나간", "flanagan"),
    ("플래너간", "플라나간", "flanagan"),
    ("플래너건", "플라나간", "flanagan"),
    ("프래나간", "플라나간", "flanagan"),
    ("플라너간", "플라나간", "flanagan"),
    ("플라너건", "플라나간", "flanagan"),
    ("주인공이며브래드전대의ＭＳ", "주인공이며브라드전대의ＭＳ", "blard_fahren"),
    ("브래드", "브라드", "blard_fahren"),
    ("베드너", "웨드너", "sig_wedner"),
    ("웨드나", "웨드너", "sig_wedner"),
    ("라라아・순", "라라아・슨", "lalah_sune"),
    ("라라아　순", "라라아　슨", "lalah_sune"),
    ("라라아 순", "라라아 슨", "lalah_sune"),
    ("라라아순", "라라아슨", "lalah_sune"),
    ("라라　슨", "라라아　슨", "lalah_sune"),
    ("라라 슨", "라라아 슨", "lalah_sune"),
    ("채프", "챕", "chap_adel"),
]
DIRECT_STANDARD = {
    0x091E: ("ミリシャ", "밀리샤", "militia"),
    0x0CFA: ("フラナガン", "플라나간", "flanagan"),
}


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
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temp, path)


def patch_direct_standard(
    rom: bytearray, tbl: Tbl, ext_meta: dict[str, Any], ext3_meta: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    rows: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []
    for index, (expected_before, after, term) in DIRECT_STANDARD.items():
        dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        before = dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        if before != expected_before:
            raise BuildError(
                f"direct standard slot {index:04X} drifted: {before!r} != {expected_before!r}"
            )
        aliases = [
            other
            for other in range(dictionary.stock_count)
            if dictionary.entry_abs(other) == dictionary.entry_abs(index)
        ]
        if aliases != [index]:
            raise BuildError(f"direct standard slot {index:04X} has aliases: {aliases}")
        raw = bytes(dictionary.raw_entry(index))
        encoded = encode(after, tbl)
        if len(encoded) > len(raw):
            raise BuildError(
                f"direct standard slot {index:04X} would grow: "
                f"old_len={len(raw)} new_len={len(encoded)} old={raw.hex()} new={encoded.hex()}"
            )
        entry_abs = dictionary.entry_abs(index)
        rom[entry_abs : entry_abs + len(encoded)] = encoded
        rom[entry_abs + len(encoded)] = 0
        allowed.append((entry_abs, entry_abs + len(encoded) + 1))
        verify = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        rendered = verify.expand_index(index, tbl).rstrip("\u3000 \t")
        if semantic_norm(rendered) != semantic_norm(after):
            raise BuildError(f"direct standard verify failed {index:04X}: {rendered!r}")
        rows.append(
            {
                "index": f"{index:04X}",
                "term": term,
                "before": before,
                "after": after,
                "entry_abs": f"{entry_abs:07X}",
                "old_len": len(raw),
                "new_len": len(encoded),
                "mode": "inplace_shrink",
            }
        )
    return rows, allowed


def patch_five_bank_phrases(
    rom: bytearray,
    tbl: Tbl,
    dictionary,
    bad_index,
) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    hits = five_bank_dictionary_hits(bytes(rom), tbl, dictionary, bad_index)
    rows: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []
    for hit in hits:
        phrase_abs = int(str(hit["phrase_abs"]), 16)
        before = str(hit["text"])
        after, applied = canonicalize(before, REPLACEMENTS)
        if after == before or not applied:
            raise BuildError(f"no replacement for five-bank phrase {hit}")
        end = rom.find(0, phrase_abs)
        if end < 0:
            raise BuildError(f"unterminated five-bank phrase at {phrase_abs:07X}")
        raw = bytes(rom[phrase_abs:end])
        encoded = encode(after, tbl)
        compact_stock_terms: list[dict[str, Any]] = []
        if len(encoded) > len(raw) and "라라아" in after:
            lalah_index = 0x07F0
            lalah_text = dictionary.expand_index(lalah_index, tbl).rstrip("\u3000 \t")
            if lalah_text != "라라아":
                raise BuildError(f"Lalah stock slot drifted: {lalah_index:04X}={lalah_text!r}")
            parts = after.split("라라아")
            token = token_from_dict_index(lalah_index)
            chunks: list[bytes] = []
            for part_index, part in enumerate(parts):
                if part_index:
                    chunks.append(token)
                    compact_stock_terms.append({"text": "라라아", "stock_index": f"{lalah_index:04X}"})
                if part:
                    chunks.append(encode(part, tbl))
            encoded = b"".join(chunks)
        encoded, ambiguous = preserve_ambiguous_direct_codes(raw, encoded, tbl)
        if len(encoded) > len(raw):
            raise BuildError(
                f"five-bank phrase would grow at {phrase_abs:07X}: "
                f"{len(raw)} -> {len(encoded)}"
            )
        rom[phrase_abs : phrase_abs + len(encoded)] = encoded
        rom[phrase_abs + len(encoded)] = 0
        allowed.append((phrase_abs, phrase_abs + len(encoded) + 1))
        rows.append(
            {
                **hit,
                "before": before,
                "after": after,
                "old_len": len(raw),
                "new_len": len(encoded),
                "mode": "inplace" if len(encoded) == len(raw) else "inplace_shrink",
                "replacements": applied,
                "stock_term_substitutions": compact_stock_terms,
                "ambiguous_code_preservations": ambiguous,
            }
        )
    return rows, allowed


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    tbl_bytes = TBL_PATH.read_bytes()
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"current main identity drifted: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError("active TBL identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")
    if marker_code() != MARKER:
        raise BuildError(f"installed marker drifted: {marker_code():04X}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    bad_index = forbidden_index(standard_entries())
    rom_bad_index = list(bad_index)
    rom_bad_index.extend(
        (term, before, norm(before))
        for before, _after, term in REPLACEMENTS
        if any("ァ" <= char <= "ヿ" for char in before)
    )
    original_dictionary = __import__("monoeye_rom").Dictionary(original)
    before_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    before_source_hits = source_hits(bad_index)
    before_dict_hits = dictionary_hits(parent, tbl, before_dictionary, bad_index)
    before_five_bank_hits = five_bank_dictionary_hits(
        parent, tbl, before_dictionary, bad_index
    )
    before_inventory_hits = rendered_record_hits(parent, tbl, before_dictionary, bad_index)
    before_bank5c_hits = bank5c_hits(
        original, parent, tbl, original_dictionary, before_dictionary, bad_index
    )
    before_untranslated = untranslated_standard_dictionary(
        original_dictionary, before_dictionary, tbl
    )
    if before_source_hits:
        raise BuildError(f"active source terminology is not clean: {len(before_source_hits)}")

    candidate = bytearray(parent)
    stock_rows, stock_allowed = patch_stock(
        candidate, tbl, ext_meta, ext3_meta, rom_bad_index, REPLACEMENTS
    )
    direct_rows = [
        row
        for row in stock_rows
        if any("ァ" <= char <= "ヿ" for char in str(row.get("before") or ""))
    ]
    ext3_rows, ext3_allowed = patch_ext3(
        candidate, tbl, ext_meta, ext3_meta, rom_bad_index, REPLACEMENTS
    )
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    five_bank_rows, five_bank_allowed = patch_five_bank_phrases(
        candidate, tbl, candidate_dictionary, bad_index
    )
    allowed = stock_allowed + ext3_allowed + five_bank_allowed

    final_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    after_dict_hits = dictionary_hits(bytes(candidate), tbl, final_dictionary, rom_bad_index)
    after_five_bank_hits = five_bank_dictionary_hits(
        bytes(candidate), tbl, final_dictionary, bad_index
    )
    after_inventory_hits = rendered_record_hits(
        bytes(candidate), tbl, final_dictionary, bad_index
    )
    after_bank5c_hits = bank5c_hits(
        original, bytes(candidate), tbl, original_dictionary, final_dictionary, bad_index
    )
    after_untranslated = untranslated_standard_dictionary(
        original_dictionary, final_dictionary, tbl
    )
    if after_dict_hits or after_five_bank_hits or after_inventory_hits or after_bank5c_hits:
        raise BuildError(
            "candidate residuals remain: "
            f"dict={len(after_dict_hits)} five_bank={len(after_five_bank_hits)} "
            f"inventory={len(after_inventory_hits)} "
            f"bank5c={len(after_bank5c_hits)} untranslated={len(after_untranslated)}"
        )

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:10]}")
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum verification failed")

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_SAVE, save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_main_tip_name_mapping_consistency_candidate.py",
        "ok": True,
        "status": "candidate_static_verified",
        "promotion_performed": False,
        "inputs": {
            "main_tip": identity(MAIN, parent),
            "active_tbl": identity(TBL_PATH, tbl_bytes),
            "live_saveram": identity(MAIN_SAVE, save),
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, result),
            "candidate_saveram": identity(OUT_SAVE, save),
        },
        "before": {
            "active_source_forbidden_hits": len(before_source_hits),
            "dictionary_forbidden_hits": len(before_dict_hits),
            "five_bank_dictionary_forbidden_hits": len(before_five_bank_hits),
            "inventory_forbidden_hits": len(before_inventory_hits),
            "complete_bank5c_forbidden_hits": len(before_bank5c_hits),
            "untranslated_standard_dictionary_entries": len(before_untranslated),
        },
        "patches": {
            "stock_forbidden_groups": stock_rows,
            "direct_standard_slots": direct_rows,
            "ext3_forbidden_groups": ext3_rows,
            "five_bank_forbidden_groups": five_bank_rows,
        },
        "after": {
            "dictionary_forbidden_hits": 0,
            "five_bank_dictionary_forbidden_hits": 0,
            "inventory_forbidden_hits": 0,
            "complete_bank5c_forbidden_hits": 0,
            "untranslated_standard_dictionary_entries": len(after_untranslated),
            "untranslated_entries_are_dormant_in_scanned_records": all(
                not row["player_visible_in_scanned_records"] for row in after_untranslated
            ),
        },
        "checks": {
            "main_tip_unchanged": MAIN.read_bytes() == parent,
            "active_tbl_unchanged": TBL_PATH.read_bytes() == tbl_bytes,
            "candidate_saveram_exact_live": OUT_SAVE.read_bytes() == save,
            "marker_unchanged": marker_code() == MARKER,
            "source_terminology_clean": not source_hits(bad_index),
            "diff_allowlist_clean": not unexpected,
            "ws_checksum_valid": True,
        },
        "diff": {
            "changed_bytes": sum(end - start for start, end in runs),
            "changed_runs": len(runs),
            "unexpected_runs": len(unexpected),
            "runs": [
                {"start": f"{start:07X}", "end": f"{end:07X}", "length": end - start}
                for start, end in runs
            ],
        },
        "ws_checksum": f"{checksum:04X}",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": report["outputs"]["candidate_rom"],
                "before": report["before"],
                "patch_groups": {
                    "stock": len(stock_rows),
                    "direct": len(direct_rows),
                    "ext3": len(ext3_rows),
                    "five_bank": len(five_bank_rows),
                },
                "after": report["after"],
                "diff": report["diff"],
                "checksum": report["ws_checksum"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
