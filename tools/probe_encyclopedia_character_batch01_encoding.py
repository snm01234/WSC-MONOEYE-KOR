#!/usr/bin/env python3
"""Probe shortest existing-token encodings for the character encyclopedia."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_ext3_compaction_probe import compact_bank
from build_remaining_dialogue_candidate import encode_phrase
from mixed_residual_classification import is_japanese_character
from mixed_residual_reference_union import build_free_slot_inventory, build_reference_union
from monoeye_rom import Tbl, dict_token_safe_in_zstring, token_from_dict_index
from patch_3byte_dict_token import EXP3_SEG0, bank_local_for_index, token_from_ext3_index

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
WORKLIST = ROOT / "out/patch/encyclopedia_character_batch01_worklist.json"
CATALOG = ROOT / "data/encyclopedia_character_batch01_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
REPORT = ROOT / "out/patch/encyclopedia_character_batch01_encoding_probe.json"
SELECTED_SEGMENTS = (0x19, 0x1C, 0x1E, 0x1F, 0x20)


def payload_options(dictionary: Any, tbl: Tbl, num_banks: int) -> dict[str, list[tuple[str, bytes, str]]]:
    by_first: dict[str, list[tuple[str, bytes, str]]] = defaultdict(list)
    seen: dict[tuple[str, bytes], str] = {}
    indices = list(range(int(dictionary.stock_count))) + list(range(0x1000, 0x1000 + num_banks * 0x1000))
    for index in indices:
        if not dict_token_safe_in_zstring(index):
            continue
        try:
            raw = bytes(dictionary.raw_entry(index))
            if not raw:
                continue
            text = dictionary.expand(raw, tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        if not text or len(text) > 13 or "<BADDICT:" in text or any(is_japanese_character(ch) for ch in text):
            continue
        try:
            token = token_from_dict_index(index) if index < 0x1000 else token_from_ext3_index(index, num_banks=num_banks)
        except Exception:
            continue
        if 0 in token:
            continue
        source = f"stock:{index:04X}" if index < 0x1000 else f"ext3:{index:05X}"
        key = (text, token)
        if key not in seen:
            seen[key] = source
            by_first[text[0]].append((text, token, source))
    for key in by_first:
        by_first[key].sort(key=lambda item: (-len(item[0]), len(item[1]), item[2]))
    return by_first


def shortest(text: str, *, dictionary: Any, tbl: Tbl, options: dict[str, list[tuple[str, bytes, str]]]) -> tuple[bytes, list[dict[str, Any]]]:
    n = len(text)
    best: list[tuple[int, bytes, list[dict[str, Any]]] | None] = [None] * (n + 1)
    best[n] = (0, b"", [])
    for pos in range(n - 1, -1, -1):
        candidates: list[tuple[int, bytes, list[dict[str, Any]]]] = []
        try:
            direct = encode_phrase(text[pos], tbl)
            tail = best[pos + 1]
            if tail is not None and direct and 0 not in direct:
                candidates.append((len(direct) + tail[0], direct + tail[1], [{"kind": "direct", "text": text[pos], "hex": direct.hex().upper()}] + tail[2]))
        except Exception:
            pass
        for phrase, token, source in options.get(text[pos], []):
            if not text.startswith(phrase, pos):
                continue
            tail = best[pos + len(phrase)]
            if tail is None:
                continue
            candidates.append((len(token) + tail[0], token + tail[1], [{"kind": "token", "text": phrase, "source": source, "hex": token.hex().upper()}] + tail[2]))
        if candidates:
            candidates.sort(key=lambda item: (item[0], len(item[2]), item[1]))
            best[pos] = candidates[0]
    if best[0] is None:
        raise RuntimeError(f"cannot encode {text!r}")
    encoded = best[0][1]
    rendered = dictionary.expand(encoded, tbl).rstrip("\u3000 \t")
    if rendered != text:
        raise RuntimeError(f"shortest encoding render mismatch: {text!r} != {rendered!r}")
    return encoded, best[0][2]


def main() -> int:
    parent = MAIN.read_bytes()
    original = ORIGINAL.read_bytes()
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    source_by_abs = {str(row["abs"]).upper(): row for row in work["records"]}
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    num_banks = int(ext3_meta["num_banks"])
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    options = payload_options(dictionary, tbl, num_banks)

    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    banks = []
    for segment in SELECTED_SEGMENTS:
        _, info, _ = compact_bank(segment=segment, dictionary=dictionary, free_indices=set(inventory.ext3_free))
        banks.append(info)
    room = sum(int(info["free_room"]) for info in banks)

    rows = []
    private: dict[str, bytes] = {}
    failures = []
    for line in catalog["lines"]:
        address = str(line["abs"]).upper()
        text = str(line["ko"])
        capacity = int(source_by_abs[address]["payload_len"])
        encoded, steps = shortest(text, dictionary=dictionary, tbl=tbl, options=options)
        if len(encoded) <= capacity:
            strategy = "inline_shortest"
        elif capacity >= 4:
            strategy = "private_ext3_shortest_payload"
            private.setdefault(text, encoded)
        else:
            strategy = "unfit_short"
            failures.append({"abs": address, "capacity": capacity, "ko": text, "encoded_len": len(encoded), "encoded_hex": encoded.hex().upper(), "steps": steps})
        rows.append({"abs": address, "payload_len": capacity, "ko": text, "shortest_len": len(encoded), "shortest_hex": encoded.hex().upper(), "strategy": strategy, "steps": steps})

    private_bytes = sum(len(payload) + 1 for payload in private.values())
    report = {
        "schema_version": 1,
        "generated_by": "tools/probe_encyclopedia_character_batch01_encoding.py",
        "ok": not failures and private_bytes <= room,
        "counts": {
            "targets": len(rows),
            "inline": sum(row["strategy"] == "inline_shortest" for row in rows),
            "private_ext3_records": sum(row["strategy"] == "private_ext3_shortest_payload" for row in rows),
            "private_ext3_unique_phrases": len(private),
            "short_failures": len(failures),
            "private_phrase_bytes": private_bytes,
            "compacted_room": room,
            "room_after": room - private_bytes,
            "lexicon_first_char_buckets": len(options),
            "lexicon_options": sum(len(value) for value in options.values()),
        },
        "selected_segments": [f"{segment:02X}" for segment in SELECTED_SEGMENTS],
        "banks": banks,
        "failures": failures,
        "rows": rows,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "counts": report["counts"], "banks": banks, "failures": failures}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
