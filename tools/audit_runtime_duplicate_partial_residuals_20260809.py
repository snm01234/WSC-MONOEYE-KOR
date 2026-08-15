#!/usr/bin/env python3
"""Audit current focused candidate for the two structures found by runtime measurement.

Structures:
1) duplicated original dialogue where only one runtime copy was translated/fixed;
2) attempted Korean localization that still leaves Japanese glyphs in the visible body.

The address population is the union of the project's vetted/script CSV inventories.
This is read-only and prints JSON; it does not modify ROM or reports.
"""
from __future__ import annotations

import csv
import glob
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3
from extract_script import split_prefix_body
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/runtime_measured_followup_20260809_candidate.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"

EXPECTED_MAIN_SHA = "fb6629d89cfd0dd8f48a621af5c0175d4602f113abec5d781b54b32b14efa86d"
EXPECTED_CANDIDATE_SHA = "f11b11c05e94e6b3007061e00586bbf74424be1e36c07571f43cb6a4584bafaf"
EXT_META = {"stock_count": 3831, "slot_count": 265, "ext_ptr_off": "0000", "ext_seg": "10", "ext_in_expansion": True}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}

ADDR_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
# Linguistic Japanese only. Exclude shared punctuation such as ・ and ー.
JP_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
KO_RE = re.compile(r"[\uac00-\ud7a3]")

# These CSVs are generated inventories with explicit record addresses.  Use all
# of them rather than one translation sheet so bank59 event/aux and battle rows
# are both covered. Backups are intentionally excluded by the glob pattern.
CSV_GLOB = str(ROOT / "out/script/*.csv")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("　 \t")


def shape(text: str) -> str:
    jp = bool(JP_RE.search(text))
    ko = bool(KO_RE.search(text))
    if jp and ko:
        return "mixed"
    if jp:
        return "jp_only"
    if ko:
        return "ko_only"
    return "other"


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int] | None:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        return None
    return bytes(got[0]), int(got[1] - sb)


def address_inventory() -> tuple[set[int], dict[int, set[str]]]:
    addresses: set[int] = set()
    provenance: dict[int, set[str]] = defaultdict(set)
    for name in glob.glob(CSV_GLOB):
        path = Path(name)
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    continue
                keys = [
                    key for key in reader.fieldnames
                    if key and key.lower() in {"abs", "address", "address_or_slot", "logical", "record_abs"}
                ]
                if not keys:
                    continue
                for row in reader:
                    for key in keys:
                        raw = str(row.get(key) or "").strip()
                        if not ADDR_RE.fullmatch(raw):
                            continue
                        logical = int(raw, 16)
                        # Only ROM logical text banks used by this project.
                        if not (0x570000 <= logical <= 0x75FFFF):
                            continue
                        addresses.add(logical)
                        provenance[logical].add(path.name)
        except (UnicodeDecodeError, csv.Error, OSError):
            continue
    return addresses, provenance


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    main = bytes(load_rom(MAIN))
    candidate = bytes(load_rom(CANDIDATE))
    if sha(main) != EXPECTED_MAIN_SHA:
        raise SystemExit(f"main drifted: {sha(main)}")
    if sha(candidate) != EXPECTED_CANDIDATE_SHA:
        raise SystemExit(f"candidate drifted: {sha(candidate)}")

    tbl = Tbl.load(TBL_PATH)
    d_original = Dictionary(original)
    d_candidate = make_dictionary_ext3(candidate, EXT_META, EXT3_META)
    addresses, provenance = address_inventory()

    rows: list[dict] = []
    source_groups: dict[str, list[dict]] = defaultdict(list)
    unreadable = 0
    prefix_drift = 0
    for logical in sorted(addresses):
        o = payload_at(original, logical)
        c = payload_at(candidate, logical)
        if o is None or c is None:
            unreadable += 1
            continue
        op, ot = o
        cp, ct = c
        prefix, source_body, kind = split_prefix_body(op)
        if kind != "dialogue":
            continue
        if not cp.startswith(prefix):
            prefix_drift += 1
            continue
        source_text = strip_pad(d_original.expand(source_body, tbl))
        if not JP_RE.search(source_text):
            continue
        current_body = cp[len(prefix):]
        current_text = strip_pad(d_candidate.expand(current_body, tbl))
        current_shape = shape(current_text)
        attempted = cp != op or current_text != source_text
        row = {
            "abs": f"{logical:06X}",
            "source": source_text,
            "candidate": current_text,
            "shape": current_shape,
            "attempted_translation": attempted,
            "prefix_hex": prefix.hex().upper(),
            "source_term": f"{ot:06X}",
            "candidate_term": f"{ct:06X}",
            "inventories": sorted(provenance[logical]),
        }
        rows.append(row)
        source_groups[source_text].append(row)

    attempted_mixed = [row for row in rows if row["attempted_translation"] and row["shape"] == "mixed"]

    duplicate_mismatch: list[dict] = []
    for source, group in source_groups.items():
        # Ignore one-glyph/data-tail families. The measured bug was a real
        # duplicated dialogue sentence/cry, not a table filler byte.
        if len(group) < 2 or len(JP_RE.findall(source)) < 2:
            continue
        shapes = {row["shape"] for row in group}
        clean = [row for row in group if row["shape"] in {"ko_only", "other"} and not JP_RE.search(row["candidate"])]
        residual = [row for row in group if row["shape"] in {"mixed", "jp_only"}]
        # A translated clean sibling plus a Japanese-bearing sibling is the
        # exact class of duplicate-family omission we are looking for.
        if clean and residual:
            duplicate_mismatch.append({
                "source": source,
                "records": group,
                "clean_count": len(clean),
                "residual_count": len(residual),
                "shapes": sorted(shapes),
            })

    # Strongest analogue of the measured bug: same original source duplicated,
    # at least one translated clean and another record still mixed/Japanese.
    duplicate_mismatch.sort(key=lambda x: (x["source"], x["records"][0]["abs"]))
    attempted_mixed.sort(key=lambda x: x["abs"])

    result = {
        "ok": not attempted_mixed and not duplicate_mismatch,
        "identity": {
            "main_sha256": sha(main),
            "candidate_sha256": sha(candidate),
        },
        "inventory": {
            "unique_addresses": len(addresses),
            "decoded_japanese_source_dialogue_records": len(rows),
            "unreadable_addresses": unreadable,
            "source_prefix_drift_skipped": prefix_drift,
        },
        "attempted_translation_with_japanese_residual_count": len(attempted_mixed),
        "attempted_translation_with_japanese_residual": attempted_mixed,
        "duplicate_source_partial_fix_family_count": len(duplicate_mismatch),
        "duplicate_source_partial_fix_families": duplicate_mismatch,
    }
    console = {
        "ok": result["ok"],
        "identity": result["identity"],
        "inventory": result["inventory"],
        "attempted_translation_with_japanese_residual_count": len(attempted_mixed),
        "attempted_translation_with_japanese_residual_first": attempted_mixed[:20],
        "duplicate_source_partial_fix_family_count": len(duplicate_mismatch),
        "duplicate_source_partial_fix_families_first": duplicate_mismatch[:20],
    }
    print(json.dumps(console, ensure_ascii=True, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
