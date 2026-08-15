#!/usr/bin/env python3
"""Find untranslated bank-5C dialogue duplicates of translated Name75 strings.

The canonical Name75 table is translated, but battle and ID-command logic can
store independent copies in bank 5C.  This audit searches only exact complete
records shaped as::

    17 34 18 + original Name75 payload + 00

That explicit prefix/body/terminator contract avoids the raw-pair false
positive that previously corrupted the structured table at 5C:B5C2.
The tool does not modify ROM or SaveRAM.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import NAME75_RANGES, _walk_zstring_range  # noqa: E402
from mixed_residual_classification import (  # noqa: E402
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    find_rom,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)
from normalize_ko_text import normalize_ko_text  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
CATALOG = ROOT / "data/name75_terms_ko.json"
EXISTING_SPEC = ROOT / "data/battle_id_command_followup_ko.json"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_JSON = ROOT / "out/patch/name75_battle_id_duplicate_residual_audit.json"
OUT_CSV = ROOT / "out/script/name75_battle_id_duplicate_residual_sheet.csv"

PREFIX = bytes.fromhex("173418")
BANK5C_LO = 0x5C0000
BANK5C_HI = 0x5D0000
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class AuditError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": digest(data),
    }


def load_catalog() -> dict[str, str]:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for row in document.get("entries") or []:
        jp = str(row.get("jp") or "")
        ko = str(row.get("ko") or "")
        if jp and ko and jp not in result:
            result[jp] = ko
    return result


def load_existing_sites() -> set[int]:
    if not EXISTING_SPEC.is_file():
        return set()
    document = json.loads(EXISTING_SPEC.read_text(encoding="utf-8"))
    return {
        int(str(row["record_start"]), 16)
        for row in document.get("records") or []
        if row.get("record_start")
    }


def source_payloads(
    original: bytes,
    tip: bytes,
    tbl: Tbl,
    catalog: dict[str, str],
) -> dict[bytes, dict[str, Any]]:
    original_dictionary = Dictionary(original)
    current_dictionary = make_dictionary_ext3(
        tip,
        load_ext_meta(EXT_META_PATH),
        load_ext_meta(EXT3_META_PATH),
    )
    tip_base = stock_base(tip)
    grouped: dict[bytes, dict[str, Any]] = {}
    for lo, hi in NAME75_RANGES:
        for logical, payload, _kind in _walk_zstring_range(
            original, lo, hi, region="name75", max_len=64
        ):
            try:
                jp = original_dictionary.expand(payload, tbl)
            except Exception:
                continue
            catalog_ko = normalize_ko_text(str(catalog.get(jp) or ""))
            if japanese_character_count(jp) <= 0 or len(payload) < 4:
                continue

            canonical_ko = ""
            current_payload_hex = ""
            got = read_encoded_z_safe(tip, tip_base + logical, max_len=64)
            if got is not None:
                current_payload = bytes(got[0])
                current_payload_hex = current_payload.hex().upper()
                try:
                    rendered = normalize_ko_text(
                        current_dictionary.expand(current_payload, tbl).rstrip("\u3000 \t")
                    )
                except Exception:
                    rendered = ""
                if (
                    rendered
                    and hangul_character_count(rendered) > 0
                    and japanese_character_count(rendered) == 0
                ):
                    canonical_ko = rendered

            ko = canonical_ko or catalog_ko
            if not ko:
                continue
            translation_source = (
                "current_main_name75_canonical"
                if canonical_ko
                else "data/name75_terms_ko.json"
            )
            review_status = (
                "approved_current_main_canonical"
                if canonical_ko
                else "approved_existing_name75_catalog"
            )
            row = grouped.setdefault(
                bytes(payload),
                {
                    "jp": jp,
                    "ko": ko,
                    "catalog_ko": catalog_ko,
                    "source_name75_sites": [],
                    "canonical_variants": [],
                    "translation_source": translation_source,
                    "review_status": review_status,
                },
            )
            if row["jp"] != jp:
                raise AuditError("one payload maps to conflicting Name75 source text")
            if canonical_ko:
                variant = {
                    "logical": f"{logical:06X}",
                    "ko": canonical_ko,
                    "payload_hex": current_payload_hex,
                }
                row["canonical_variants"].append(variant)
                if row["translation_source"] != "current_main_name75_canonical":
                    row["ko"] = canonical_ko
                    row["translation_source"] = "current_main_name75_canonical"
                    row["review_status"] = "approved_current_main_canonical"
                elif row["ko"] != canonical_ko:
                    raise AuditError(
                        f"conflicting current canonical translations for {jp!r}: "
                        f"{row['ko']!r} vs {canonical_ko!r}"
                    )
            row["source_name75_sites"].append(f"{logical:06X}")
    return grouped


def find_all(data: bytes, needle: bytes, lo: int, hi: int) -> list[int]:
    result: list[int] = []
    cursor = lo
    while True:
        found = data.find(needle, cursor, hi)
        if found < 0:
            return result
        result.append(found)
        cursor = found + 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON)
    parser.add_argument("--out-csv", type=Path, default=OUT_CSV)
    args = parser.parse_args(argv)

    tip = bytes(load_rom(args.tip))
    original_path = find_rom(ROOT)
    original = bytes(load_rom(original_path))
    save_path = ROOT / "sram/monoeye_ko_expanded.sav"
    if len(tip) != ROM_SIZE:
        raise AuditError("TIP is not 16 MiB")
    if not save_path.is_file() or save_path.stat().st_size != SAVE_SIZE:
        raise AuditError("live SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    catalog = load_catalog()
    sources = source_payloads(original, tip, tbl, catalog)
    existing_sites = load_existing_sites()
    sb = stock_base(tip)
    file_lo = sb + BANK5C_LO
    file_hi = sb + BANK5C_HI

    rows: list[dict[str, Any]] = []
    seen_intervals: list[tuple[int, int]] = []
    for body, source in sorted(sources.items(), key=lambda item: (item[1]["jp"], item[0])):
        pattern = PREFIX + body + b"\x00"
        for file_start in find_all(tip, pattern, file_lo, file_hi):
            logical = file_start - sb
            file_end = file_start + len(pattern)
            if file_start > file_lo and tip[file_start - 1] != 0:
                continue
            if any(not (file_end <= lo or hi <= file_start) for lo, hi in seen_intervals):
                raise AuditError(f"overlapping exact records at {logical:06X}")
            seen_intervals.append((file_start, file_end))
            rows.append(
                {
                    "record_start": f"{logical:06X}",
                    "body_start": f"{logical + len(PREFIX):06X}",
                    "prefix_hex": PREFIX.hex().upper(),
                    "body_hex": body.hex().upper(),
                    "body_capacity": len(body),
                    "jp": source["jp"],
                    "ko": source["ko"],
                    "source_name75_sites": ";".join(source["source_name75_sites"]),
                    "catalog_ko": source.get("catalog_ko") or "",
                    "translation_source": source["translation_source"],
                    "review_status": source["review_status"],
                    "already_in_followup_spec": logical in existing_sites,
                    "category": "name75_battle_id_exact_duplicate",
                    "safe_record_contract": True,
                }
            )

    rows.sort(key=lambda row: int(row["record_start"], 16))
    new_rows = [row for row in rows if not row["already_in_followup_spec"]]
    by_phrase: dict[str, int] = defaultdict(int)
    for row in new_rows:
        by_phrase[str(row["jp"])] += 1

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "record_start", "body_start", "prefix_hex", "body_hex", "body_capacity",
        "jp", "ko", "source_name75_sites", "catalog_ko", "translation_source",
        "review_status", "already_in_followup_spec", "category",
        "safe_record_contract",
    ]
    with args.out_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_name75_battle_id_duplicate_residuals.py",
        "read_only": True,
        "ok": True,
        "inputs": {
            "tip": identity(args.tip, tip),
            "original": identity(original_path, original),
            "catalog": identity(CATALOG),
            "ext_meta": identity(EXT_META_PATH),
            "ext3_meta": identity(EXT3_META_PATH),
            "existing_followup_spec": identity(EXISTING_SPEC) if EXISTING_SPEC.is_file() else None,
        },
        "scope": {
            "bank": "5C",
            "record_shape": "173418 + exact original Name75 payload + 00",
            "raw_pair_search_forbidden": True,
            "overlap_allowed": False,
        },
        "counts": {
            "translated_name75_payloads": len(sources),
            "exact_duplicate_records": len(rows),
            "already_in_followup_spec": len(rows) - len(new_rows),
            "new_residual_records": len(new_rows),
            "new_unique_phrases": len(by_phrase),
            "current_canonical_translation_records": sum(
                row["translation_source"] == "current_main_name75_canonical"
                for row in rows
            ),
            "catalog_fallback_translation_records": sum(
                row["translation_source"] == "data/name75_terms_ko.json"
                for row in rows
            ),
            "canonical_catalog_text_differences": sum(
                bool(row.get("catalog_ko")) and row["catalog_ko"] != row["ko"]
                for row in rows
            ),
        },
        "new_phrase_counts": dict(sorted(by_phrase.items())),
        "rows": rows,
        "outputs": {"csv": identity(args.out_csv)},
    }
    args.out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "counts": report["counts"],
                "new_phrase_counts": report["new_phrase_counts"],
                "csv": str(args.out_csv.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
                "json": str(args.out_json.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
