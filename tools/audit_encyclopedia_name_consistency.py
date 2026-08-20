#!/usr/bin/env python3
"""Audit MS/character encyclopedia list-name consistency against current main.

Read-only with respect to ROMs.  It extracts name-like rows from the character
and MS encyclopedia catalogs, resolves the exact Original Japanese at each
address, renders the current main TIP at the same address, and cross-references
active JP->KO name sources.  High-confidence canonical mismatches are separated
from ambiguous aliases/variants that require user review.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import _walk_zstring_range  # noqa: E402
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
CHAR_CATALOG = ROOT / "data/encyclopedia_character_batch01_ko.json"
MS_CATALOGS = (
    ROOT / "data/encyclopedia_ms_batch01_ko.json",
    ROOT / "data/encyclopedia_ms_batch02_ko.json",
)
OUT_JSON = ROOT / "out/patch/encyclopedia_name_consistency_audit.json"
OUT_ALL = ROOT / "docs/ENCYCLOPEDIA_NAME_CONSISTENCY_ALL.csv"
OUT_AUTO = ROOT / "docs/ENCYCLOPEDIA_NAME_AUTO_UNIFY_CANDIDATES.csv"
OUT_REVIEW = ROOT / "docs/ENCYCLOPEDIA_NAME_REVIEW_EXCEPTIONS.csv"
OUT_UNREFERENCED = ROOT / "docs/ENCYCLOPEDIA_NAME_UNREFERENCED.csv"

ACTIVE_PAIR_FILES = (
    "data/unit_names_ko.json",
    "data/proper_nouns_ko.json",
    "data/ui_proper_nouns_ko.json",
    "data/name75_terms_ko.json",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hira_to_kata(text: str) -> str:
    return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in text)


def jp_key(text: str) -> str:
    value = hira_to_kata(unicodedata.normalize("NFKC", text))
    return re.sub(r"[\s・·･]+", "", value)


def ko_key(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    value = value.replace("（", "(").replace("）", ")")
    return re.sub(r"[\s・·･]+", "", value)


def display_ko(text: str) -> str:
    return text.replace("\u3000", " ").strip()


def name_like(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).strip()
    if not normalized or len(normalized) > 32:
        return False
    if any(mark in normalized for mark in ("。", "！", "？", "、", "「", "」", "『", "』")):
        return False
    # Entry titles in these two encyclopedias are proper/model names.  A
    # hiragana particle is a strong description-line signal (e.g. ～を～に).
    if any("ぁ" <= ch <= "ゖ" for ch in normalized):
        return False
    value = hira_to_kata(normalized)
    # Description-like Japanese grammar markers.  Parenthesized equipment/forms
    # remain valid names, e.g. ガンダム（ＭＡ）.
    if re.search(r"(です|ます|した|する|され|だった|である|という|ため|ので|だが|では|には|から|まで|より)", value):
        return False
    katakana = sum("ァ" <= ch <= "ヿ" for ch in value)
    latin_or_digit = sum(ch.isascii() and (ch.isalpha() or ch.isdigit()) for ch in value)
    kanji = sum("一" <= ch <= "龯" for ch in value)
    # Encyclopedia names are overwhelmingly compact proper nouns / model names.
    return katakana >= 2 or latin_or_digit >= 2 or (kanji >= 2 and len(value) <= 12)


def payload_text(rom: bytes, logical: int, dictionary: Any, tbl: Tbl) -> str:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        return ""
    try:
        return dictionary.expand(bytes(got[0]), tbl).rstrip("\u3000 \t")
    except Exception:
        return ""


def add_pair(store: dict[str, list[dict[str, str]]], jp: Any, ko: Any, source: str, kind: str = "exact") -> None:
    if not isinstance(jp, str) or not jp or not isinstance(ko, str) or not ko:
        return
    store[jp_key(jp)].append({"jp": jp, "ko": display_ko(ko), "source": source, "kind": kind})


def active_pairs() -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    pairs: dict[str, list[dict[str, str]]] = defaultdict(list)
    glossary_exact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    glossary_alias: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for relative in ACTIVE_PAIR_FILES:
        path = ROOT / relative
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        for row in list(doc.get("entries") or []) + list(doc.get("lines") or []):
            if isinstance(row, dict):
                add_pair(pairs, row.get("jp"), row.get("ko"), relative)

    path = ROOT / "data/name75_base_ko.json"
    if path.is_file():
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        for bucket in ("bases", "overrides", "qualifiers"):
            for jp, ko in (doc.get(bucket) or {}).items():
                add_pair(pairs, jp, ko, f"data/name75_base_ko.json#{bucket}")

    path = ROOT / "data/main_translation_glossary_ko.json"
    if path.is_file():
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        for row in doc.get("entries") or []:
            jp = row.get("jp")
            ko = row.get("canonical_ko")
            if isinstance(jp, str) and isinstance(ko, str):
                glossary_exact[jp_key(jp)].append(row)
                add_pair(pairs, jp, ko, "data/main_translation_glossary_ko.json", "official_exact")
            for alias in row.get("aliases") or []:
                if isinstance(alias, str) and alias:
                    glossary_alias[jp_key(alias)].append(row)
                    add_pair(pairs, alias, ko, "data/main_translation_glossary_ko.json#alias", "official_alias")

    path = ROOT / "data/gundam_terminology_standard_ko.json"
    if path.is_file():
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        for row in doc.get("entries") or []:
            canonical = row.get("canonical_ko")
            for jp in row.get("jp") or []:
                add_pair(pairs, jp, canonical, "data/gundam_terminology_standard_ko.json", "standard_exact")
            for jp, ko in (row.get("component_ko") or {}).items():
                add_pair(pairs, jp, ko, "data/gundam_terminology_standard_ko.json#component", "standard_component")
    return pairs, glossary_exact, glossary_alias


def source_variants(rows: list[dict[str, str]]) -> list[str]:
    seen: set[tuple[str, str]] = set()
    result: list[str] = []
    for row in rows:
        key = (row["ko"], row["source"])
        if key in seen:
            continue
        seen.add(key)
        result.append(f"{row['ko']} [{row['source']}]")
    return result


def exact_canonical(key: str, pairs: dict[str, list[dict[str, str]]], glossary_exact: dict[str, list[dict[str, Any]]]) -> tuple[str, str]:
    # Exact official glossary entry is the strongest automatic basis.  Aliases
    # are deliberately not used here because short-name vs full-name display is
    # context-sensitive and belongs in user review.
    exact = glossary_exact.get(key) or []
    official = {display_ko(str(row.get("canonical_ko") or "")) for row in exact if row.get("canonical_ko")}
    official.discard("")
    if len(official) == 1:
        return next(iter(official)), "official_exact_glossary"

    # Only a full exact terminology entry is strong enough to auto-rewrite a
    # displayed encyclopedia name.  Component mappings such as セイラ→세이라
    # must never shorten セイラ・マス automatically.
    strong = [row for row in pairs.get(key, []) if row.get("kind") == "standard_exact"]
    strong_values = {row["ko"] for row in strong if row.get("ko")}
    if len(strong_values) == 1:
        return next(iter(strong_values)), "gundam_terminology_standard_exact"
    return "", ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=MAIN)
    parser.add_argument("--out", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    tip = bytes(load_rom(args.tip))
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    original_dict = Dictionary(original)
    current_dict = make_dictionary_ext3(tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    pairs, glossary_exact, glossary_alias = active_pairs()

    # Catalog rows provide reviewed KO text, but many title records were already
    # localized before those residual catalogs were created.  Build a complete
    # title-key union, then scan the Original bank5C text directly so already-
    # translated list names are included too.
    catalog_by_abs: dict[str, dict[str, str]] = {}
    strict_catalog_keys: set[str] = set()
    char_doc = json.loads(CHAR_CATALOG.read_text(encoding="utf-8-sig"))
    for row in char_doc.get("lines") or []:
        address = str(row.get("abs") or "").upper()
        jp = str(row.get("jp") or "")
        catalog_by_abs[address] = {"jp": jp, "ko": display_ko(str(row.get("ko") or "")), "encyclopedia": "character"}
        if name_like(jp):
            strict_catalog_keys.add(jp_key(jp))

    for catalog_path in MS_CATALOGS:
        doc = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
        for row in doc.get("lines") or []:
            address = str(row.get("abs") or "").upper()
            if not address:
                continue
            logical = int(address, 16)
            jp = payload_text(original, logical, original_dict, tbl)
            catalog_by_abs[address] = {"jp": jp, "ko": display_ko(str(row.get("ko") or "")), "encyclopedia": "ms"}
            if name_like(jp):
                strict_catalog_keys.add(jp_key(jp))

    title_keys = set(pairs) | strict_catalog_keys
    raw_rows: list[dict[str, Any]] = []
    for logical, raw, _kind in _walk_zstring_range(
        original, 0x5C0000, 0x5C7900, region="bank5c_encyclopedia", max_len=256
    ):
        if not (0x5C0000 <= logical < 0x5C7900):
            continue
        try:
            jp = original_dict.expand(raw, tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        key = jp_key(jp)
        if key not in title_keys:
            continue
        # The middle 5C2E62-5C34C9 region is not part of the two encyclopedia
        # catalogs under review.
        if 0x5C0000 <= logical < 0x5C2E62:
            encyclopedia = "character"
        elif 0x5C34CA <= logical < 0x5C7900:
            encyclopedia = "ms"
        else:
            continue
        cat = catalog_by_abs.get(f"{logical:06X}") or {}
        raw_rows.append({
            "encyclopedia": encyclopedia,
            "address": f"{logical:06X}",
            "catalog_jp": str(cat.get("jp") or jp),
            "catalog_ko": str(cat.get("ko") or ""),
        })

    results: list[dict[str, Any]] = []
    auto: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    unreferenced: list[dict[str, Any]] = []
    seen_addr: set[str] = set()
    for row in sorted(raw_rows, key=lambda r: int(r["address"], 16)):
        address = row["address"]
        if address in seen_addr:
            continue
        seen_addr.add(address)
        logical = int(address, 16)
        original_jp = payload_text(original, logical, original_dict, tbl) or row["catalog_jp"]
        current = display_ko(payload_text(tip, logical, current_dict, tbl))
        catalog_ko = row["catalog_ko"]
        key = jp_key(original_jp)
        variants = pairs.get(key, [])
        variant_values = sorted({item["ko"] for item in variants if item.get("ko")})
        canonical, canonical_basis = exact_canonical(key, pairs, glossary_exact)
        alias_rows = glossary_alias.get(key) or []

        status = "consistent"
        reason = ""
        proposed = ""
        if canonical:
            # A full Japanese personal name must not be shortened to a surname
            # or component merely because the broad terminology standard uses
            # a short combat/UI form.
            full_name_shape = "・" in original_jp or "･" in original_jp
            canonical_short_shape = "・" not in canonical and " " not in canonical
            wing_zero_contextual = jp_key(original_jp) in {
                jp_key("ウイングゼロカスタム"), jp_key("ウイングゼロカスタムＳ")
            }
            if wing_zero_contextual and ko_key(current) != ko_key(canonical):
                status = "review_required"
                reason = "approved_contextual_wing_zero_surface_name"
                canonical = ""
                canonical_basis = ""
            elif full_name_shape and canonical_short_shape and ko_key(current) != ko_key(canonical):
                status = "review_required"
                reason = "full_encyclopedia_name_vs_short_standard_form"
                canonical = ""
                canonical_basis = ""
            elif ko_key(current) != ko_key(canonical):
                status = "auto_unify_candidate"
                proposed = canonical
                reason = canonical_basis
            elif len({ko_key(value) for value in variant_values}) > 1:
                status = "source_variants_but_current_canonical"
                reason = canonical_basis
        else:
            # Ignore official alias/full-name suggestions when deciding whether
            # two actual game surfaces disagree.  They remain visible in the
            # source columns for user review.
            non_alias = [
                item for item in variants
                if item.get("kind") not in {"official_alias", "standard_component"}
                and item.get("ko")
            ]
            non_alias_values = {ko_key(item["ko"]): item["ko"] for item in non_alias}
            if len(non_alias_values) > 1:
                status = "review_required"
                reason = "multiple_active_game_translations_no_unique_canonical"
            elif len(non_alias_values) == 1:
                only = next(iter(non_alias_values.values()))
                if ko_key(current) == ko_key(only):
                    status = "consistent_cross_reference"
                    reason = "single_active_mapping_matches_current"
                else:
                    status = "review_required"
                    reason = "current_encyclopedia_differs_from_active_game_mapping"
            elif alias_rows:
                alias_full = {display_ko(str(item.get("canonical_ko") or "")) for item in alias_rows if item.get("canonical_ko")}
                if alias_full and any(ko_key(value) != ko_key(current) for value in alias_full):
                    status = "review_required"
                    reason = "short_or_alias_display_vs_full_official_name"
                else:
                    status = "consistent_cross_reference"
                    reason = "official_alias_matches_current"
            elif catalog_ko and current and ko_key(catalog_ko) != ko_key(current):
                status = "review_required"
                reason = "catalog_vs_current_main_mismatch_without_canonical"
            else:
                status = "unreferenced"
                reason = "no_other_active_name_mapping"

        item = {
            "encyclopedia": row["encyclopedia"],
            "address": address,
            "original_jp": original_jp,
            "catalog_ko": catalog_ko,
            "current_main_ko": current,
            "canonical_ko": canonical,
            "canonical_basis": canonical_basis,
            "proposed_ko": proposed,
            "status": status,
            "reason": reason,
            "other_translation_values": " | ".join(variant_values),
            "other_translation_sources": " || ".join(source_variants(variants)),
        }
        results.append(item)
        if status == "auto_unify_candidate":
            auto.append(item)
        elif status == "review_required":
            review.append(item)
        elif status == "unreferenced":
            unreferenced.append(item)

    fields = [
        "encyclopedia", "address", "original_jp", "catalog_ko", "current_main_ko",
        "canonical_ko", "canonical_basis", "proposed_ko", "status", "reason",
        "other_translation_values", "other_translation_sources",
    ]
    for path, rows in ((OUT_ALL, results), (OUT_AUTO, auto), (OUT_REVIEW, review), (OUT_UNREFERENCED, unreferenced)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_encyclopedia_name_consistency.py",
        "read_only_rom_audit": True,
        "tip": {"path": str(args.tip), "size": len(tip), "sha256": sha(tip)},
        "scope": {
            "character_catalog_records": len(char_doc.get("lines") or []),
            "ms_catalog_records": sum(len(json.loads(path.read_text(encoding="utf-8-sig")).get("lines") or []) for path in MS_CATALOGS),
            "name_detection": "compact name-like row; cross-reference is used only for canonical decision, not inclusion",
        },
        "counts": {
            "name_rows": len(results),
            "character_name_rows": sum(row["encyclopedia"] == "character" for row in results),
            "ms_name_rows": sum(row["encyclopedia"] == "ms" for row in results),
            "consistent": sum(row["status"] == "consistent" for row in results),
            "consistent_cross_reference": sum(row["status"] == "consistent_cross_reference" for row in results),
            "source_variants_but_current_canonical": sum(row["status"] == "source_variants_but_current_canonical" for row in results),
            "auto_unify_candidates": len(auto),
            "review_required": len(review),
            "unreferenced": len(unreferenced),
        },
        "auto_unify_candidates": auto,
        "review_required": review,
        "outputs": {
            "all": str(OUT_ALL.relative_to(ROOT)).replace("\\", "/"),
            "auto": str(OUT_AUTO.relative_to(ROOT)).replace("\\", "/"),
            "review": str(OUT_REVIEW.relative_to(ROOT)).replace("\\", "/"),
            "unreferenced": str(OUT_UNREFERENCED.relative_to(ROOT)).replace("\\", "/"),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "tip_sha256": report["tip"]["sha256"], "counts": report["counts"], "outputs": report["outputs"]}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
