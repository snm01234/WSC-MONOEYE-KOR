#!/usr/bin/env python3
"""Audit Gundam terminology consistency in active sources and the current TIP.

The canonical decisions live in ``data/gundam_terminology_standard_ko.json``.
This tool intentionally separates active translation inputs from historical
snapshots. Historical files may retain old wording as evidence; active sources
and the rendered current TIP must not contain forbidden Korean variants.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base  # noqa: E402

STANDARD = ROOT / "data/gundam_terminology_standard_ko.json"
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/gundam_terminology_audit.json"

FIVE_BANK_FIRST_SEG = 0x21
FIVE_BANK_PAGES = 5
FIVE_BANK_EMPTY_PTR = 0x2000
FIVE_BANK_LOCAL_LIMIT = 0x0A00

ACTIVE_SOURCE_GLOBS = (
    "aux_body_ko.json",
    "aux_body_ko_values.json",
    "aux_text_ko.json",
    "aux_text_ko_values.json",
    "bank59_opening_batch01_ko.json",
    "bank59_enc5c_name75_ko.json",
    "battle_voice_ambiguous_nonstub_llm_ko.json",
    "broad_stage2_title_ui_ko.json",
    "dialogue_singleton_rewrite_batch*.json",
    "dialogue_20cell_llm_batches/batch*.json",
    "dialogue_readability_batches/output_*.json",
    "encyclopedia_character_batch01_ko.json",
    "encyclopedia_character_batch01_ko_part*.json",
    "encyclopedia_character_safe_batch01_ko.json",
    "encyclopedia_ms_batch*_ko.json",
    "ko_ui_overrides.json",
    "mixed_residual_translations.json",
    "mixed_residual_values/*.json",
    "name75_base_ko.json",
    "name75_base_ko_values.json",
    "name75_terms_ko.json",
    "proper_nouns_ko.json",
    "runtime_measurement_followup_ko.json",
    "ui_proper_nouns_ko.json",
    "unit_names_ko.json",
    "weapon_names_ko.json",
)
ACTIVE_OUT_FILES = (
    ROOT / "out/script/dialogue_readability_changes.json",
)

# These fields preserve Japanese/original/current-before evidence and are not
# translation outputs. They must not be rewritten just to satisfy terminology.
SOURCE_EVIDENCE_KEYS = {
    "jp", "source", "source_text", "current", "current_main", "current_main_rows",
    "current_source_ko", "before", "before_rows", "pre20cell_ko_rows",
    "legacy_after", "legacy_dense_rows", "original", "original_ko", "auto_after",
    "review_notes", "change_summary", "description", "_note", "_marker_note",
    "generated_by", "lexicon", "unmatched", "reason", "note",
}

# Proven record inventories used only as address sources. These files are not
# rewritten by this audit.
RECORD_INVENTORIES = (
    ROOT / "out/script/dialogue_db.json",
    ROOT / "data/mixed_residual_translations.json",
    ROOT / "data/encyclopedia_character_batch01_ko.json",
    ROOT / "data/encyclopedia_character_safe_batch01_ko.json",
    ROOT / "data/encyclopedia_ms_batch01_ko.json",
    ROOT / "data/encyclopedia_ms_batch02_ko.json",
    ROOT / "out/patch/main_p1_base_manifest.json",
)

SPACE_RE = re.compile(r"\s+")
ABS_RE = re.compile(r"^[0-9A-Fa-f]{6,7}$")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).replace("\u3000", " ")
    return SPACE_RE.sub(" ", value).strip()


def term_occurs(text_norm: str, bad_norm: str) -> bool:
    """Match a Korean term without starting inside another Hangul word."""
    if not bad_norm:
        return False
    if "가" <= bad_norm[0] <= "힣":
        return re.search(rf"(?<![가-힣]){re.escape(bad_norm)}", text_norm) is not None
    return bad_norm in text_norm


def entries() -> list[dict[str, Any]]:
    return list(json.loads(STANDARD.read_text(encoding="utf-8"))["entries"])


def forbidden_index(rows: Iterable[dict[str, Any]]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        for bad in row.get("forbidden_ko") or []:
            key = (row["id"], norm(str(bad)))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            out.append((row["id"], str(bad), key[1]))
    # Longer strings first so a full-name hit is easier to inspect than a
    # component hit on the same line.
    out.sort(key=lambda item: (-len(item[2]), item[0], item[2]))
    return out


def source_files() -> list[Path]:
    data = ROOT / "data"
    files: set[Path] = set()
    for pattern in ACTIVE_SOURCE_GLOBS:
        files.update(path for path in data.glob(pattern) if path.is_file())
    files.update(path for path in ACTIVE_OUT_FILES if path.is_file())
    return sorted(files)


def iter_output_strings(obj: Any, path: str = "$", *, parent_key: str | None = None):
    """Yield only strings that can become Korean output, not source evidence."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_s = str(key)
            if key_s in SOURCE_EVIDENCE_KEYS:
                continue
            yield from iter_output_strings(value, f"{path}.{key_s}", parent_key=key_s)
    elif isinstance(obj, list):
        if parent_key in SOURCE_EVIDENCE_KEYS:
            return
        for index, value in enumerate(obj):
            yield from iter_output_strings(value, f"{path}[{index}]", parent_key=parent_key)
    elif isinstance(obj, str):
        # Metadata strings are generally ASCII/path/category labels. Restrict to
        # strings that actually contain Hangul so ordinary ids do not matter.
        if re.search(r"[가-힣]", obj):
            yield path, obj


def source_hits(bad_index: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in source_files():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            blob = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            hits.append({
                "file": rel,
                "json_path": "$",
                "term_id": "audit_parse_error",
                "forbidden": "",
                "text": str(exc),
            })
            continue
        for json_path, text in iter_output_strings(blob):
            matched: set[tuple[str, str]] = set()
            text_norm = norm(text)
            for term_id, raw_bad, bad_norm in bad_index:
                if not term_occurs(text_norm, bad_norm):
                    continue
                key = (term_id, bad_norm)
                if key in matched:
                    continue
                matched.add(key)
                hits.append({
                    "file": rel,
                    "json_path": json_path,
                    "term_id": term_id,
                    "forbidden": raw_bad,
                    "text": text,
                })
    return hits


def collect_abs_values(obj: Any, out: set[int]) -> None:
    if isinstance(obj, dict):
        value = obj.get("abs")
        if isinstance(value, str) and ABS_RE.fullmatch(value):
            out.add(int(value, 16))
        elif isinstance(value, int) and 0 <= value <= 0xFFFFFFF:
            out.add(value)
        for child in obj.values():
            collect_abs_values(child, out)
    elif isinstance(obj, list):
        for child in obj:
            collect_abs_values(child, out)


def record_addresses() -> set[int]:
    out: set[int] = set()
    for path in RECORD_INVENTORIES:
        if not path.exists():
            continue
        try:
            blob = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        collect_abs_values(blob, out)
    return {value for value in out if 0x500000 <= value <= 0x75FFFF}


def find_bad(text: str, bad_index: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    text_norm = norm(text)
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for term_id, raw_bad, bad_norm in bad_index:
        # A Korean term may take a particle on its right, but it must not begin
        # in the middle of another Hangul word.  Without this guard, ``지 오``
        # was falsely found across the word boundary in ``까지 오차``.
        if not term_occurs(text_norm, bad_norm):
            continue
        key = (term_id, bad_norm)
        if key in seen:
            continue
        seen.add(key)
        found.append({"term_id": term_id, "forbidden": raw_bad})
    return found


def dictionary_hits(rom: bytes, tbl: Tbl, dictionary, bad_index) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    ranges = [range(dictionary.count)]
    if dictionary.ext3_count > 0:
        ranges.append(range(0x1000, 0x1000 + dictionary.ext3_count))
    for indexes in ranges:
        for idx in indexes:
            try:
                text = dictionary.expand_index(idx, tbl).rstrip("\u3000 \t")
            except Exception:
                continue
            matched = find_bad(text, bad_index)
            if not matched:
                continue
            try:
                entry_abs = dictionary.entry_abs(idx)
            except Exception:
                entry_abs = None
            hits.append(
                {
                    "index": f"{idx:05X}",
                    "entry_abs": None if entry_abs is None else f"{entry_abs:07X}",
                    "text": text,
                    "matches": matched,
                }
            )
    return hits


def five_bank_dictionary_hits(
    rom: bytes, tbl: Tbl, dictionary, bad_index
) -> list[dict[str, Any]]:
    """Scan the runtime E5 18 alias pages in physical banks 21..25.

    ``make_dictionary_ext3`` covers the ordinary 16 ext3 banks, but the later
    character-encyclopedia/runtime expansion subtracts 0x0600 from selected
    E5 18 locals and dispatches them to these five independent pointer tables.
    Auditing only logical ext3 indices therefore renders these portals as blank
    padding and misses their physical Korean phrases.
    """
    hits: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for page in range(FIVE_BANK_PAGES):
        segment = FIVE_BANK_FIRST_SEG + page
        start = segment << 16
        bank = rom[start : start + 0x10000]
        if len(bank) != 0x10000:
            continue
        for local in range(1, FIVE_BANK_LOCAL_LIMIT):
            if (local & 0xFF) == 0:
                continue
            pointer = int.from_bytes(bank[local * 2 : local * 2 + 2], "little")
            if pointer == FIVE_BANK_EMPTY_PTR or not (FIVE_BANK_EMPTY_PTR < pointer < 0x10000):
                continue
            physical = (segment, pointer)
            if physical in seen:
                continue
            seen.add(physical)
            end = bank.find(b"\x00", pointer)
            if end < 0:
                continue
            raw = bytes(bank[pointer:end])
            try:
                text = dictionary.expand(raw, tbl).rstrip("\u3000 \t")
            except Exception:
                continue
            matched = find_bad(text, bad_index)
            if matched:
                hits.append(
                    {
                        "page": page,
                        "physical_bank": f"{segment:02X}",
                        "local": f"{local:04X}",
                        "pointer": f"{pointer:04X}",
                        "phrase_abs": f"{start + pointer:07X}",
                        "text": text,
                        "matches": matched,
                    }
                )
    return hits


def rendered_record_hits(rom: bytes, tbl: Tbl, dictionary, bad_index) -> list[dict[str, Any]]:
    sb = stock_base(rom)
    hits: list[dict[str, Any]] = []
    for logical in sorted(record_addresses()):
        got = read_encoded_z_safe(rom, sb + logical, max_len=512)
        if got is None:
            continue
        payload = bytes(got[0])
        try:
            text = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        matched = find_bad(text, bad_index)
        if matched:
            hits.append(
                {
                    "abs": f"{logical:06X}",
                    "text": text,
                    "matches": matched,
                }
            )
    return hits


def summarize_by_term(*hit_groups: list[dict[str, Any]]) -> dict[str, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for group in hit_groups:
        for row in group:
            if "term_id" in row:
                counts[str(row["term_id"])] += 1
            else:
                for match in row.get("matches") or []:
                    counts[str(match["term_id"])] += 1
    return dict(sorted(counts.items()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tip", type=Path, default=TIP)
    ap.add_argument("--tbl", type=Path, default=TBL)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--no-fail", action="store_true", help="write the report but return success even when hits remain")
    args = ap.parse_args()

    rows = entries()
    bad_index = forbidden_index(rows)
    sources = source_hits(bad_index)

    rom = bytes(load_rom(args.tip))
    tbl = Tbl.load(args.tbl)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    dict_hits = dictionary_hits(rom, tbl, dictionary, bad_index)
    five_bank_hits = five_bank_dictionary_hits(rom, tbl, dictionary, bad_index)
    record_hits = rendered_record_hits(rom, tbl, dictionary, bad_index)

    payload = {
        "standard": str(STANDARD.relative_to(ROOT)).replace("\\", "/"),
        "tip": {
            "path": str(args.tip.relative_to(ROOT)).replace("\\", "/") if args.tip.is_relative_to(ROOT) else str(args.tip),
            "size": len(rom),
            "sha256": sha256(rom),
        },
        "active_source_file_count": len(source_files()),
        "active_source_hits": sources,
        "dictionary_hits": dict_hits,
        "five_bank_dictionary_hits": five_bank_hits,
        "rendered_record_hits": record_hits,
        "counts": {
            "active_source_hits": len(sources),
            "dictionary_hits": len(dict_hits),
            "five_bank_dictionary_hits": len(five_bank_hits),
            "rendered_record_hits": len(record_hits),
            "by_term": summarize_by_term(sources, dict_hits, five_bank_hits, record_hits),
        },
        "status": (
            "clean"
            if not (sources or dict_hits or five_bank_hits or record_hits)
            else "violations_found"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], ensure_ascii=False))
    print(f"status={payload['status']} report={args.out}")
    if payload["status"] != "clean" and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
