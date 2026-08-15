#!/usr/bin/env python3
"""Build a narrow terminology-resynchronization candidate from current main.

Only dictionary phrase storage, dictionary pointers, and the WonderSwan
checksum may change.  The active TBL and live SaveRAM are read-only inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_gundam_terminology_standard import (  # noqa: E402
    dictionary_hits,
    entries as standard_entries,
    forbidden_index,
    rendered_record_hits,
)
from build_gundam_terminology_candidate import (  # noqa: E402
    ext3_bank_cursor,
    preserve_ambiguous_direct_codes,
    stock_tail_cursor,
)
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Tbl,
    token_from_dict_index,
    update_ws_checksum,
    write_le16,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
SPEC = ROOT / "data/terminology_consistency_followup_ko.json"
OUT_ROM = PATCH / "terminology_consistency_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/terminology_consistency_followup_candidate.sav"
REPORT = PATCH / "terminology_consistency_followup_candidate_report.json"

EXPECTED_MAIN_SHA = "4e1453f0d6bc1ad7be1431b617be8da772104f1a9a49d31261897acd332584db"
EXPECTED_TBL_SHA = "cbeafbe074015bc79ad0dce1ade3be57a4d56bb1e5bf46102097cc6f0e17261c"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MARKER = 0xEC8D


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


def atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def semantic_norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def encode(text: str, tbl: Tbl) -> bytes:
    payload = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=MARKER,
        hangul_marker_mode="run",
    )
    if payload is None or b"\x00" in payload:
        missing = sorted({ch for ch in text if "가" <= ch <= "힣" and ch not in tbl.char_to_code})
        raise BuildError(f"cannot encode {text!r}; missing={missing}")
    return payload


def load_replacements() -> list[tuple[str, str, str]]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("review_status") != "approved_for_main_tip":
        raise BuildError("terminology follow-up catalog is not approved")
    rows = []
    for row in spec["replacements"]:
        before, after = str(row["before"]), str(row["after"])
        if not before or before == after:
            raise BuildError(f"invalid replacement row: {row}")
        rows.append((before, after, str(row["term"])))
    rows.sort(key=lambda item: -len(item[0]))
    return rows


def replace_term(text: str, before: str, after: str) -> tuple[str, int]:
    # A Korean name may take a particle on its right, but must not begin in the
    # middle of another word.  This prevents 지 오 from matching 자리까지 오차.
    pattern = re.compile(rf"(?<![가-힣]){re.escape(before)}")
    return pattern.subn(after, text)


def canonicalize(text: str, replacements: list[tuple[str, str, str]]) -> tuple[str, list[dict[str, Any]]]:
    out = text
    applied: list[dict[str, Any]] = []
    for before, after, term in replacements:
        out, count = replace_term(out, before, after)
        if count:
            applied.append({"term": term, "before": before, "after": after, "count": count})
    return out, applied


def encode_with_stock_terms(
    text: str,
    tbl: Tbl,
    dictionary,
    replacements: list[tuple[str, str, str]],
) -> tuple[bytes, list[dict[str, Any]]]:
    """Encode a phrase while reusing exact canonical stock-name entries."""
    wanted = {after for _before, after, _term in replacements if len(after) >= 2}
    candidates: list[tuple[str, int]] = []
    seen: set[str] = set()
    for index in range(dictionary.stock_count):
        try:
            rendered = strip_pad(dictionary.expand_index(index, tbl))
        except Exception:
            continue
        if rendered not in wanted or rendered in seen:
            continue
        seen.add(rendered)
        candidates.append((rendered, index))
    candidates.sort(key=lambda item: -len(item[0]))

    chunks: list[bytes] = []
    substitutions: list[dict[str, Any]] = []
    plain_start = 0
    pos = 0
    while pos < len(text):
        match = next(((term, index) for term, index in candidates if text.startswith(term, pos)), None)
        if match is None:
            pos += 1
            continue
        term, index = match
        if plain_start < pos:
            chunks.append(encode(text[plain_start:pos], tbl))
        chunks.append(token_from_dict_index(index))
        substitutions.append({"text": term, "stock_index": f"{index:04X}", "at": pos})
        pos += len(term)
        plain_start = pos
    if plain_start < len(text):
        chunks.append(encode(text[plain_start:], tbl))
    payload = b"".join(chunks)
    return (payload if substitutions else encode(text, tbl)), substitutions


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    pos = 0
    while pos < len(before):
        if before[pos] == after[pos]:
            pos += 1
            continue
        start = pos
        while pos < len(before) and before[pos] != after[pos]:
            pos += 1
        runs.append((start, pos))
    return runs


def covered(run: tuple[int, int], intervals: list[tuple[int, int]]) -> bool:
    start, end = run
    cursor = start
    for lo, hi in sorted(intervals):
        if hi <= cursor:
            continue
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return cursor >= end


def patch_stock(
    rom: bytearray,
    tbl: Tbl,
    ext_meta: dict[str, Any],
    ext3_meta: dict[str, Any],
    bad_index,
    replacements: list[tuple[str, str, str]],
) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
    hits = dictionary_hits(bytes(rom), tbl, dictionary, bad_index)
    indices = sorted({int(row["index"], 16) for row in hits if int(row["index"], 16) < 0x1000})
    groups: defaultdict[int, list[int]] = defaultdict(list)
    for index in indices:
        groups[dictionary.entry_abs(index)].append(index)

    cursor = stock_tail_cursor(dictionary)
    bank_end = dictionary.base + BANK_SIZE
    consumed_reclaims: set[int] = set()
    report: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []
    for entry_abs, aliases in sorted(groups.items()):
        dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        before = strip_pad(dictionary.expand_index(aliases[0], tbl))
        after, applied = canonicalize(before, replacements)
        if before == after or not applied:
            raise BuildError(f"no explicit replacement for stock hit {aliases[0]:04X}: {before!r}")
        for alias in aliases[1:]:
            if strip_pad(dictionary.expand_index(alias, tbl)) != before:
                raise BuildError(f"stock physical alias disagreement at {entry_abs:06X}")
        raw = bytes(dictionary.raw_entry(aliases[0]))
        encoded = encode(after, tbl)
        stock_terms: list[dict[str, Any]] = []
        encoded, ambiguous = preserve_ambiguous_direct_codes(raw, encoded, tbl)
        if len(encoded) <= len(raw):
            rom[entry_abs : entry_abs + len(encoded)] = encoded
            rom[entry_abs + len(encoded)] = 0
            allowed.append((entry_abs, entry_abs + len(encoded) + 1))
            mode = "inplace"
        else:
            need = len(encoded) + 1
            dst = dictionary.base + cursor
            if dst + need <= bank_end and all(byte == 0xFF for byte in rom[dst : dst + need]):
                rom[dst : dst + len(encoded)] = encoded
                rom[dst + len(encoded)] = 0
                allowed.append((dst, dst + need))
                for alias in aliases:
                    ptr_at = dictionary.ptr_file + alias * 2
                    write_le16(rom, ptr_at, cursor)
                    allowed.append((ptr_at, ptr_at + 2))
                cursor += need
                mode = "tail_repoint"
            else:
                # The stock tail is nearly full.  Reclaim one physical payload
                # only when another distinct stock pointer has byte-identical
                # raw data.  Repointing the duplicate preserves its exact
                # semantics (including ambiguous graphic codes) and yields a
                # proven dictionary-owned extent for the growing name.
                by_raw: defaultdict[bytes, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
                for candidate_index in range(dictionary.stock_count):
                    try:
                        candidate_raw = bytes(dictionary.raw_entry(candidate_index))
                        candidate_ptr = int(dictionary.ptrs[candidate_index])
                    except Exception:
                        continue
                    by_raw[candidate_raw][candidate_ptr].append(candidate_index)
                reclaimed = None
                for duplicate_raw, pointer_groups in sorted(by_raw.items(), key=lambda item: -len(item[0])):
                    if len(duplicate_raw) + 1 < need or len(pointer_groups) < 2:
                        continue
                    pointers = sorted(pointer_groups)
                    survivor_ptr = pointers[0]
                    for victim_ptr in pointers[1:]:
                        if victim_ptr in consumed_reclaims or victim_ptr == dictionary.entry_offset(aliases[0]):
                            continue
                        interior = [
                            (idx, int(value))
                            for idx, value in enumerate(dictionary.ptrs)
                            if victim_ptr < int(value) < victim_ptr + len(duplicate_raw) + 1
                        ]
                        if interior:
                            continue
                        reclaimed = (duplicate_raw, survivor_ptr, victim_ptr, pointer_groups[victim_ptr])
                        break
                    if reclaimed is not None:
                        break
                if reclaimed is None:
                    raise BuildError(f"stock tail unavailable and no exact duplicate reclaim for {aliases[0]:04X}")
                duplicate_raw, survivor_ptr, victim_ptr, victim_indices = reclaimed
                for victim_index in victim_indices:
                    ptr_at = dictionary.ptr_file + victim_index * 2
                    write_le16(rom, ptr_at, survivor_ptr)
                    allowed.append((ptr_at, ptr_at + 2))
                dst = dictionary.base + victim_ptr
                rom[dst : dst + len(encoded)] = encoded
                rom[dst + len(encoded)] = 0
                allowed.append((dst, dst + len(duplicate_raw) + 1))
                for alias in aliases:
                    ptr_at = dictionary.ptr_file + alias * 2
                    write_le16(rom, ptr_at, victim_ptr)
                    allowed.append((ptr_at, ptr_at + 2))
                consumed_reclaims.add(victim_ptr)
                mode = "duplicate_payload_reclaim"
        verify = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        for alias in aliases:
            rendered = strip_pad(verify.expand_index(alias, tbl))
            if semantic_norm(rendered) != semantic_norm(after):
                raise BuildError(f"stock verify failed {alias:04X}: {rendered!r} != {after!r}")
        report.append({
            "indices": [f"{index:04X}" for index in aliases],
            "entry_abs": f"{entry_abs:06X}",
            "before": before,
            "after": after,
            "mode": mode,
            "old_len": len(raw),
            "new_len": len(encoded),
            "replacements": applied,
            "stock_term_substitutions": stock_terms,
            "ambiguous_code_preservations": ambiguous,
        })
    return report, allowed


def patch_ext3(
    rom: bytearray,
    tbl: Tbl,
    ext_meta: dict[str, Any],
    ext3_meta: dict[str, Any],
    bad_index,
    replacements: list[tuple[str, str, str]],
) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
    hits = dictionary_hits(bytes(rom), tbl, dictionary, bad_index)
    indices = sorted({int(row["index"], 16) for row in hits if int(row["index"], 16) >= 0x1000})
    groups: defaultdict[int, list[int]] = defaultdict(list)
    for index in indices:
        groups[dictionary.entry_abs(index)].append(index)

    report: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []
    for entry_abs, aliases in sorted(groups.items()):
        dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        before = strip_pad(dictionary.expand_index(aliases[0], tbl))
        after, applied = canonicalize(before, replacements)
        if before == after or not applied:
            raise BuildError(f"no explicit replacement for ext3 hit {aliases[0]:05X}: {before!r}")
        for alias in aliases[1:]:
            if strip_pad(dictionary.expand_index(alias, tbl)) != before:
                raise BuildError(f"ext3 physical alias disagreement at {entry_abs:06X}")
        raw = bytes(dictionary.raw_entry(aliases[0]))
        encoded, stock_terms = encode_with_stock_terms(after, tbl, dictionary, replacements)
        encoded, ambiguous = preserve_ambiguous_direct_codes(raw, encoded, tbl)
        seg, _local = dictionary._ext3_bank_local(aliases[0])
        if len(encoded) <= len(raw):
            rom[entry_abs : entry_abs + len(encoded)] = encoded
            rom[entry_abs + len(encoded)] = 0
            allowed.append((entry_abs, entry_abs + len(encoded) + 1))
            mode = "inplace"
        else:
            cursor = ext3_bank_cursor(rom, seg)
            need = len(encoded) + 1
            base = seg * BANK_SIZE
            if cursor + need > BANK_SIZE or any(byte != 0xFF for byte in rom[base + cursor : base + cursor + need]):
                raise BuildError(f"ext3 bank {seg:02X} tail unavailable for {aliases[0]:05X}")
            rom[base + cursor : base + cursor + len(encoded)] = encoded
            rom[base + cursor + len(encoded)] = 0
            allowed.append((base + cursor, base + cursor + need))
            for alias in aliases:
                alias_seg, local = dictionary._ext3_bank_local(alias)
                if alias_seg != seg:
                    raise BuildError("cross-bank ext3 physical alias")
                ptr_at = alias_seg * BANK_SIZE + dictionary.ext3_ptr_off + local * 2
                write_le16(rom, ptr_at, cursor)
                allowed.append((ptr_at, ptr_at + 2))
            mode = "append_repoint"
        verify = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        for alias in aliases:
            rendered = strip_pad(verify.expand_index(alias, tbl))
            if semantic_norm(rendered) != semantic_norm(after):
                raise BuildError(f"ext3 verify failed {alias:05X}: {rendered!r} != {after!r}")
        report.append({
            "indices": [f"{index:05X}" for index in aliases],
            "entry_abs": f"{entry_abs:06X}",
            "physical_bank": f"{seg:02X}",
            "before": before,
            "after": after,
            "mode": mode,
            "old_len": len(raw),
            "new_len": len(encoded),
            "replacements": applied,
            "stock_term_substitutions": stock_terms,
            "ambiguous_code_preservations": ambiguous,
        })
    return report, allowed


def main() -> int:
    parent = MAIN.read_bytes()
    tbl_bytes = TBL_PATH.read_bytes()
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"current main identity drifted: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError("active TBL identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")
    if marker_code() != MARKER:
        raise BuildError(f"installed marker drifted: {marker_code():04X}")

    tbl = Tbl.load(TBL_PATH)
    replacements = load_replacements()
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    bad_index = forbidden_index(standard_entries())
    before_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    before_dict_hits = dictionary_hits(parent, tbl, before_dictionary, bad_index)
    before_record_hits = rendered_record_hits(parent, tbl, before_dictionary, bad_index)

    candidate = bytearray(parent)
    stock_rows, stock_allowed = patch_stock(candidate, tbl, ext_meta, ext3_meta, bad_index, replacements)
    ext3_rows, ext3_allowed = patch_ext3(candidate, tbl, ext_meta, ext3_meta, bad_index, replacements)
    allowed = stock_allowed + ext3_allowed

    final_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    after_dict_hits = dictionary_hits(bytes(candidate), tbl, final_dictionary, bad_index)
    after_record_hits = rendered_record_hits(bytes(candidate), tbl, final_dictionary, bad_index)
    if after_dict_hits or after_record_hits:
        raise BuildError(
            f"terminology remains: dictionary={len(after_dict_hits)} records={len(after_record_hits)}"
        )

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    candidate_bytes = bytes(candidate)
    runs = diff_runs(parent, candidate_bytes)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:10]}")
    if (sum(candidate_bytes[:-2]) & 0xFFFF) != int.from_bytes(candidate_bytes[-2:], "little"):
        raise BuildError("WonderSwan checksum verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_terminology_consistency_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified",
        "promotion_allowed": True,
        "inputs": {
            "main_tip": identity(MAIN, parent),
            "active_tbl": identity(TBL_PATH, tbl_bytes),
            "live_saveram": identity(MAIN_SAVE, save),
            "catalog": identity(SPEC),
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, candidate_bytes),
            "candidate_saveram": identity(OUT_SAVE, save),
        },
        "before": {
            "dictionary_hits": len(before_dict_hits),
            "rendered_record_hits": len(before_record_hits),
        },
        "patches": {"stock_groups": stock_rows, "ext3_groups": ext3_rows},
        "after": {"dictionary_hits": 0, "rendered_record_hits": 0},
        "checks": {
            "dictionary_terminology_clean": not after_dict_hits,
            "rendered_records_terminology_clean": not after_record_hits,
            "active_tbl_unchanged": TBL_PATH.read_bytes() == tbl_bytes,
            "candidate_saveram_exact_live": OUT_SAVE.read_bytes() == save,
            "marker_unchanged": marker_code() == MARKER,
            "diff_allowlist_clean": not unexpected,
            "ws_checksum_valid": True,
        },
        "diff": {
            "changed_bytes": sum(end - start for start, end in runs),
            "changed_runs": len(runs),
            "unexpected_runs": len(unexpected),
            "runs": [
                {"start": f"{start:06X}", "end": f"{end:06X}", "length": end - start}
                for start, end in runs
            ],
        },
        "ws_checksum": f"{checksum:04X}",
    }
    atomic_text(REPORT, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "ok": True,
        "candidate": report["outputs"]["candidate_rom"],
        "before": report["before"],
        "stock_groups": len(stock_rows),
        "ext3_groups": len(ext3_rows),
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
