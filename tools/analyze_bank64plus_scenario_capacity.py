#!/usr/bin/env python3
"""Read-only capacity audit for script splits 112..223 (logical banks 64..6F).

These rows sit outside the historical ext3 application band ending at 63FFFF.
The CSV Korean column is treated only as a sizing proxy because it contains
legacy translation material; it is never approved here for insertion.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
csv.field_size_limit(sys.maxsize)

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from hangul_marker import marker_code
from monoeye_rom import Tbl, load_rom, stock_base
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SPLITS = ROOT / "out/script/splits"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/bank64plus_scenario_capacity.json"

EXPECTED_MAIN_SHA256 = "b24b11934f4508c91aae8a3b38ab256d6fdc51228a6dd81c99b7b5733864c825"
FIRST_SPLIT = 112
LAST_SPLIT = 223
FIRST_LOGICAL = 0x640000
LAST_LOGICAL_EXCLUSIVE = 0x700000
ALIAS_TOKENS_PER_BANK = 2550
PHRASE_ROOM_PER_BANK = 0x10000 - 0x2001


class AnalysisError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
        for char in text
    )


def strip_padding(text: str) -> str:
    return text.rstrip("\u3000")


def load_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split_no in range(FIRST_SPLIT, LAST_SPLIT + 1):
        path = SPLITS / f"split_{split_no:03d}.csv"
        if not path.is_file():
            raise AnalysisError(f"missing split file: {path}")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                item = dict(row)
                item["split"] = split_no
                rows.append(item)
    return rows


def main() -> int:
    rom = bytes(load_rom(MAIN))
    if sha256(rom) != EXPECTED_MAIN_SHA256:
        raise AnalysisError("main TIP identity drifted")
    sb = stock_base(rom)
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    source_rows = load_rows()

    counts: Counter[str] = Counter()
    by_bank: dict[str, Counter[str]] = defaultdict(Counter)
    unique_proxy_payloads: dict[str, bytes] = {}
    needed_proxy_payloads: dict[str, bytes] = {}
    result_rows: list[dict[str, Any]] = []
    duplicate_abs: set[int] = set()
    seen_abs: set[int] = set()

    for source in source_rows:
        address = int(str(source["abs"]), 16)
        if address in seen_abs:
            duplicate_abs.add(address)
        seen_abs.add(address)
        if not FIRST_LOGICAL <= address < LAST_LOGICAL_EXCLUSIVE:
            raise AnalysisError(f"split row outside bank64..6F: {address:06X}")

        original_prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        original_body = bytes.fromhex(str(source.get("body_hex") or ""))
        capacity = len(original_prefix) + len(original_body)
        current_payload = rom[sb + address:sb + address + capacity]
        if len(current_payload) != capacity:
            raise AnalysisError(f"record outside ROM: {address:06X}")

        prefix_match = current_payload.startswith(original_prefix)
        if prefix_match:
            current_body = current_payload[len(original_prefix):]
            prefix_source = "original_csv"
        else:
            current_prefix, current_body, _ = split_prefix_body(current_payload)
            prefix_source = "current_fallback"
            counts["prefix_mismatch"] += 1

        current_text = strip_padding(dictionary.expand(current_body, tbl))
        proxy_target = normalize_ko_text(str(source.get("ko") or "").strip())
        proxy_encoded = (
            try_encode_ko_text(
                proxy_target,
                tbl,
                hangul_marker_code=marker_code(),
                hangul_marker_mode="run",
            )
            if proxy_target
            else None
        )

        if has_japanese(current_text):
            current_class = "japanese_residual"
        elif proxy_target and current_text == proxy_target:
            current_class = "proxy_exact"
        elif current_text:
            current_class = "non_japanese_different"
        else:
            current_class = "empty"
        counts[current_class] += 1

        token_fit = len(current_body) >= 4
        counts["token_fit" if token_fit else "short_under_4"] += 1
        bank = f"{address >> 16:02X}"
        by_bank[bank][current_class] += 1
        by_bank[bank]["records"] += 1
        by_bank[bank]["token_fit" if token_fit else "short_under_4"] += 1

        if not proxy_target:
            encoding_status = "proxy_missing"
        elif proxy_encoded is None or not proxy_encoded or b"\x00" in proxy_encoded:
            encoding_status = "proxy_encode_failure"
        else:
            encoding_status = "proxy_encodable"
            unique_proxy_payloads.setdefault(proxy_target, proxy_encoded)
            if current_class != "proxy_exact" and token_fit:
                needed_proxy_payloads.setdefault(proxy_target, proxy_encoded)
        counts[encoding_status] += 1
        by_bank[bank][encoding_status] += 1

        if current_class != "proxy_exact":
            counts["needs_work"] += 1
            by_bank[bank]["needs_work"] += 1
            if token_fit and encoding_status == "proxy_encodable":
                counts["needs_work_token_eligible"] += 1
                by_bank[bank]["needs_work_token_eligible"] += 1
            elif not token_fit:
                counts["needs_work_short"] += 1
                by_bank[bank]["needs_work_short"] += 1
            else:
                counts["needs_work_blocked"] += 1
                by_bank[bank]["needs_work_blocked"] += 1

        result_rows.append(
            {
                "split": int(source["split"]),
                "abs": f"{address:06X}",
                "bank": bank,
                "id": source.get("id"),
                "capacity": capacity,
                "body_capacity": len(current_body),
                "prefix_source": prefix_source,
                "current_class": current_class,
                "current_text": current_text,
                "source_text": source.get("jp"),
                "legacy_ko_sizing_proxy": proxy_target,
                "proxy_encoding_status": encoding_status,
                "proxy_encoded_bytes": len(proxy_encoded) if proxy_encoded else None,
                "token_fit": token_fit,
            }
        )

    all_proxy_bytes = sum(len(payload) + 1 for payload in unique_proxy_payloads.values())
    needed_proxy_bytes = sum(len(payload) + 1 for payload in needed_proxy_payloads.values())
    needed_unique = len(needed_proxy_payloads)
    banks_by_tokens = math.ceil(needed_unique / ALIAS_TOKENS_PER_BANK) if needed_unique else 0
    banks_by_bytes = math.ceil(needed_proxy_bytes / PHRASE_ROOM_PER_BANK) if needed_proxy_bytes else 0
    minimum_alias_banks = max(banks_by_tokens, banks_by_bytes)

    japanese_rows = [r for r in result_rows if r["current_class"] == "japanese_residual"]
    token_rows = [
        r for r in result_rows
        if r["current_class"] != "proxy_exact"
        and r["token_fit"]
        and r["proxy_encoding_status"] == "proxy_encodable"
    ]
    short_rows = [
        r for r in result_rows
        if r["current_class"] != "proxy_exact" and not r["token_fit"]
    ]

    report = {
        "schema_version": 1,
        "generated_by": "tools/analyze_bank64plus_scenario_capacity.py",
        "read_only": True,
        "ok": not duplicate_abs,
        "inputs": {
            "main": {
                "path": str(MAIN.relative_to(ROOT)),
                "sha256": sha256(rom),
            },
            "splits": [FIRST_SPLIT, LAST_SPLIT],
            "logical_range": [f"{FIRST_LOGICAL:06X}", f"{LAST_LOGICAL_EXCLUSIVE:06X}"],
            "historical_ext3_band_end": "63FFFF",
            "policy": (
                "CSV ko values are legacy sizing proxies only. They must not be inserted or "
                "treated as final translations; create fresh latest LLM/human-reviewed Korean."
            ),
        },
        "counts": {
            "records": len(source_rows),
            "unique_abs": len(seen_abs),
            "duplicate_abs": len(duplicate_abs),
            **dict(sorted(counts.items())),
            "all_proxy_unique_phrases": len(unique_proxy_payloads),
            "needed_proxy_unique_token_phrases": needed_unique,
        },
        "capacity": {
            "one_alias_bank_usable_tokens": ALIAS_TOKENS_PER_BANK,
            "one_alias_bank_phrase_room": PHRASE_ROOM_PER_BANK,
            "all_proxy_phrase_bytes_including_nul": all_proxy_bytes,
            "needed_proxy_phrase_bytes_including_nul": needed_proxy_bytes,
            "minimum_alias_banks_by_token_count": banks_by_tokens,
            "minimum_alias_banks_by_phrase_bytes": banks_by_bytes,
            "minimum_alias_banks_for_token_eligible_population": minimum_alias_banks,
            "one_bank_sufficient": minimum_alias_banks <= 1,
            "short_records_need_separate_route": counts["needs_work_short"],
        },
        "by_bank": {bank: dict(sorted(values.items())) for bank, values in sorted(by_bank.items())},
        "samples": {
            "japanese_residual": japanese_rows[:80],
            "token_eligible": token_rows[:80],
            "short": short_rows[:120],
        },
        "records": result_rows,
        "recommendation": {
            "runtime": (
                "First emulator-approve the one-bank E5 18 alias probe. Then generalize the same "
                "leaf-only mapping to enough zero-reference tail ranges and empty physical banks."
            ),
            "translation": (
                "Discard/archive legacy machine-style ko columns as application sources. Rebuild "
                "bank64..6F translations from JP with current LLM/human review and terminology."
            ),
            "short_records": (
                "Classify short rows into controls/speaker fragments versus genuine text before "
                "using exact-token composition or union-proven retired stock slots."
            ),
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "counts": report["counts"],
        "capacity": report["capacity"],
        "out": str(OUT.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
