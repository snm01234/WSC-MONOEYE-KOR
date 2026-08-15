#!/usr/bin/env python3
"""
Apply Korean translations with dictionary reclaim, phrase compression, and
script-bank rebuild for overflow lines.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from expand_dictionary import (  # noqa: E402
    compress_with_phrases,
    reclaimable_slots,
    select_shared_phrases,
    write_dictionary_slots_spill,
)
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    DICT_PTR_START,
    Dictionary,
    SEG_DICT,
    Tbl,
    find_rom,
    load_rom,
    patch_bank,
    read_encoded_z,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import encode_ko_text, is_low_quality_ko, try_encode_ko_text  # noqa: E402
from script_translation_scope import translation_exclusion_reason  # noqa: E402
from translation_source_policy import assert_translation_source_allowed  # noqa: E402
from rebuild_script_banks import (  # noqa: E402
    filter_replacements_to_bank_capacity,
    filter_spill_to_tail_capacity,
    shift_replacements_in_text_banks,
    spill_replacements_to_bank_tails,
    spill_replacements_to_expansion,
)


def file_abs(rom: bytes | bytearray, logical_abs: int) -> int:
    return stock_base(rom) + logical_abs


def read_record_at(rom: bytearray, abs_off: int) -> bytes:
    payload, _ = read_encoded_z(rom, file_abs(rom, abs_off))
    return payload


def padded_body(encoded: bytes, original_len: int) -> bytes:
    if len(encoded) > original_len:
        raise ValueError("encoded body longer than original")
    return encoded + (b"\x00" * (original_len - len(encoded)))


def apply_translations_expanded(
    rom: bytearray,
    tbl: Tbl,
    lines: Sequence[dict],
    *,
    max_shared_phrases: int = 256,
    min_phrase_count: int = 2,
    allow_bank_rebuild: bool = True,
    allow_inplace: bool = False,
    max_abs: int | None = None,
    min_abs: int | None = None,
    hangul_marker_code: int | None = None,
    overflow_mode: str = "spill",
) -> dict:
    """
    Apply KO lines with size-preserving writes when possible.

    Safety defaults (match working seed ROM behavior):
      - allow_inplace=False: do not rewrite script bodies in place. Only use
        unused dict slots via spill writes (0x99BA+), like apply_translations.py.
      - Full dictionary rebuild is never used.
      - overflow_mode:
          * \"spill\" (default): move only replaced records to bank tails
          * \"shift\": full in-bank repack (can relocate seed abs — avoid with marker)
          * \"none\": skip overflow lines that need bank rebuild

    allow_inplace is experimental: bulk inplace patches have been observed to
    skip opening narration and freeze on stage 1.
    """
    if overflow_mode not in {"spill", "shift", "none", "exp_spill"}:
        raise ValueError(f"Unknown overflow_mode: {overflow_mode}")
    allow_bank_rebuild = allow_bank_rebuild and overflow_mode != "none"
    normalized = []
    skipped_unencodable = []
    skipped_excluded_scope = []
    unsafe_ctrl = re.compile(r"<(?:TRUNC:)?([E][0-9A-Fa-f])>")
    seed_protect: Set[int] = set()
    seed_path = ROOT / "data" / "translations_seed_hook96.json"
    if seed_path.exists():
        for row in json.loads(seed_path.read_text(encoding="utf-8")).get("lines", []):
            try:
                seed_protect.add(int(row["abs"], 16))
            except Exception:
                pass
    for line in lines:
        abs_off = int(line["abs"], 16)
        exclusion = translation_exclusion_reason(abs_off)
        if exclusion:
            skipped_excluded_scope.append({"abs": f"{abs_off:06X}", "reason": exclusion})
            continue
        if max_abs is not None and abs_off > max_abs:
            continue
        if min_abs is not None and abs_off < min_abs:
            continue
        if abs_off in seed_protect:
            continue
        ko = line["ko"].replace(" ", "　")
        jp = line.get("jp") or ""
        if "<TRUNC:" in ko or "<TRUNC:" in jp:
            skipped_unencodable.append(line.get("abs", "?"))
            continue
        if unsafe_ctrl.search(ko) or unsafe_ctrl.search(jp):
            skipped_unencodable.append(line.get("abs", "?"))
            continue
        encoded = try_encode_ko_text(ko, tbl, hangul_marker_code=hangul_marker_code)
        if encoded is None:
            skipped_unencodable.append(line.get("abs", "?"))
            continue
        if is_low_quality_ko(ko):
            skipped_unencodable.append(line.get("abs", "?"))
            continue
        # Skip lines that already decode to the target KO (idempotent multi-pass).
        try:
            cur_payload, _ = read_encoded_z(rom, file_abs(rom, abs_off))
            _p, cur_body, _ = split_prefix_body(cur_payload)
            dictionary_probe = Dictionary(rom)
            if dictionary_probe.expand(cur_body, tbl) == dictionary_probe.expand(
                encoded, tbl
            ):
                continue
        except Exception:
            pass
        normalized.append({**line, "ko": ko, "_encoded": encoded})


    line_meta = []
    for line in normalized:
        abs_off = int(line["abs"], 16)
        original = read_record_at(rom, abs_off)
        prefix, body, _kind = split_prefix_body(original)
        plain = line["_encoded"]
        fits_plain = len(plain) <= len(body)
        line_meta.append(
            {
                "line": line,
                "abs_off": abs_off,
                "original": original,
                "prefix": prefix,
                "body": body,
                "plain": plain,
                "compressed": plain,
                "fits_compressed": 1 <= len(plain) <= len(body),
                "fits_plain": fits_plain,
                # Without inplace, even fitting lines go through dict tokens.
                "needs_overflow": (not fits_plain) or (not allow_inplace),
            }
        )

    # Phase 1 (optional): inplace writes — disabled by default.
    if allow_inplace:
        for meta in line_meta:
            if not meta["fits_plain"]:
                continue
            body = meta["body"]
            prefix = meta["prefix"]
            new_body = padded_body(meta["plain"], len(body))
            new_payload = bytes(prefix) + new_body
            rom[file_abs(rom, meta["abs_off"]) : file_abs(rom, meta["abs_off"]) + len(new_payload)] = new_payload
            meta["overflow_mode"] = "inplace_plain"
            meta["planned_payload"] = new_payload
            meta["needs_overflow"] = False

    # Phase 2: safe dict reclaim for overflow lines we will actually rewrite.
    dictionary = Dictionary(rom)
    overflow_metas = [
        meta
        for meta in line_meta
        if meta["needs_overflow"] and len(meta["body"]) >= 2
    ]
    text_freq: Dict[str, int] = {}
    text_to_metas: Dict[str, List[dict]] = {}
    for meta in overflow_metas:
        text = meta["line"]["ko"]
        text_freq[text] = text_freq.get(text, 0) + 1
        text_to_metas.setdefault(text, []).append(meta)

    # Prefer early-game / high-frequency unique texts so New Game and opening
    # keep Korean coverage when dict slots are scarce.
    seed_path = ROOT / "data" / "translations_seed.json"
    seed_texts: Set[str] = set()
    if seed_path.exists():
        seed_payload = json.loads(seed_path.read_text(encoding="utf-8"))
        for row in seed_payload.get("lines", []):
            ko = (row.get("ko") or "").replace(" ", "　")
            if ko:
                seed_texts.add(ko)

    def overflow_rank(text: str) -> tuple:
        metas = text_to_metas[text]
        freq = text_freq[text]
        min_abs = min(meta["abs_off"] for meta in metas)
        seg = min_abs // 0x10000
        early = 0
        if text in seed_texts:
            early += 1_000_000
        if seg == 0x60:
            early += 100_000 - (min_abs & 0xFFFF)
        elif seg == 0x61:
            early += 50_000 - (min_abs & 0xFFFF)
        elif seg <= 0x63:
            early += 10_000
        return (early + freq * 10, -min_abs, text)

    unique_overflow = sorted(text_freq.keys(), key=overflow_rank, reverse=True)
    chosen_texts: List[str] = []
    free_slots: List[int] = []
    lo, hi = 0, len(unique_overflow)
    # Binary search largest K where reclaimable slots under exclude(K) >= K.
    while lo < hi:
        mid = (lo + hi + 1) // 2
        texts_k = unique_overflow[:mid]
        exclude = {
            meta["abs_off"]
            for text in texts_k
            for meta in text_to_metas[text]
        }
        free_k = reclaimable_slots(rom, dictionary, exclude)
        if len(free_k) >= mid:
            lo = mid
            chosen_texts = texts_k
            free_slots = free_k
        else:
            hi = mid - 1

    # Shared phrases disabled by default in the safe path unless budget remains.
    shared: List[str] = []
    phrase_to_index: Dict[str, int] = {}
    slot_payload: Dict[int, bytes] = {}
    slot_cursor = 0
    reserve = len(chosen_texts)
    shared_budget = min(
        max_shared_phrases,
        max(0, len(free_slots) - reserve),
    )
    if shared_budget > 0:
        shared = select_shared_phrases(
            [line["ko"] for line in normalized],
            max_phrases=shared_budget,
            min_count=min_phrase_count,
        )
        for phrase in shared:
            index = free_slots[slot_cursor]
            slot_cursor += 1
            phrase_to_index[phrase] = index
            slot_payload[index] = encode_ko_text(
                phrase, tbl, hangul_marker_code=hangul_marker_code
            )
        # Re-evaluate compression fits for remaining overflow after phrases.
        for meta in line_meta:
            if meta.get("overflow_mode") == "inplace_plain":
                continue
            compressed = compress_with_phrases(
                meta["line"]["ko"],
                tbl,
                phrase_to_index,
                hangul_marker_code=hangul_marker_code,
            )
            meta["compressed"] = compressed
            meta["fits_compressed"] = 1 <= len(compressed) <= len(meta["body"])
            if meta["fits_compressed"] and allow_inplace:
                new_body = padded_body(compressed, len(meta["body"]))
                new_payload = bytes(meta["prefix"]) + new_body
                rom[file_abs(rom, meta["abs_off"]) : file_abs(rom, meta["abs_off"]) + len(new_payload)] = new_payload
                meta["overflow_mode"] = "inplace_compressed"
                meta["planned_payload"] = new_payload
                meta["needs_overflow"] = False

        # Refresh overflow choice after compression wins.
        dictionary = Dictionary(rom)
        overflow_metas = [
            meta
            for meta in line_meta
            if meta.get("needs_overflow") and len(meta["body"]) >= 2
            and meta.get("overflow_mode") not in {"inplace_plain", "inplace_compressed"}
        ]
        text_freq = {}
        text_to_metas = {}
        for meta in overflow_metas:
            text = meta["line"]["ko"]
            text_freq[text] = text_freq.get(text, 0) + 1
            text_to_metas.setdefault(text, []).append(meta)
        unique_overflow = sorted(text_freq.keys(), key=lambda t: (-text_freq[t], t))
        chosen_texts = []
        remaining_slots = free_slots[slot_cursor:]
        lo, hi = 0, min(len(unique_overflow), len(remaining_slots))
        while lo < hi:
            mid = (lo + hi + 1) // 2
            texts_k = unique_overflow[:mid]
            exclude = {
                meta["abs_off"]
                for text in texts_k
                for meta in text_to_metas[text]
            }
            free_k = reclaimable_slots(rom, dictionary, exclude)
            # Must include already-consumed phrase slots as unavailable conceptually;
            # reclaimable_slots sees current ROM (phrases not yet written). Use count.
            usable = [i for i in free_k if i not in slot_payload]
            if len(usable) >= mid:
                lo = mid
                chosen_texts = texts_k
                remaining_slots = usable
            else:
                hi = mid - 1
        free_slots = list(slot_payload.keys()) + remaining_slots
        slot_cursor = len(slot_payload)

    chosen_set = set(chosen_texts)
    line_to_index: Dict[str, int] = {}
    for text in chosen_texts:
        index = free_slots[slot_cursor]
        slot_cursor += 1
        encoded = (
            compress_with_phrases(
                text,
                tbl,
                phrase_to_index,
                hangul_marker_code=hangul_marker_code,
            )
            if phrase_to_index
            else encode_ko_text(text, tbl, hangul_marker_code=hangul_marker_code)
        )
        line_to_index[text] = index
        slot_payload[index] = encoded

    shift_rebuild_abs: Dict[int, bytes] = {}
    skipped_no_capacity: List[str] = []
    for meta in line_meta:
        if meta.get("overflow_mode") in {"inplace_plain", "inplace_compressed"}:
            continue
        if not meta.get("needs_overflow"):
            meta["overflow_mode"] = None
            continue
        text = meta["line"]["ko"]
        if text in chosen_set and len(meta["body"]) >= 2:
            meta["overflow_mode"] = "dict_token"
        elif allow_bank_rebuild:
            encoded = meta["compressed"] if phrase_to_index else meta["plain"]
            shift_rebuild_abs[meta["abs_off"]] = bytes(meta["prefix"]) + encoded
            meta["overflow_mode"] = (
                "spill_rebuild"
                if overflow_mode in {"spill", "exp_spill"}
                else "shift_rebuild"
            )
        else:
            meta["overflow_mode"] = "skipped"
            skipped_no_capacity.append(meta["line"]["abs"])

    # Seed-style spill write: retarget only filled slots; never relocate the
    # whole dictionary (full rebuild skips/breaks opening → stage flow).
    if slot_payload:
        ptrs, spill_end = write_dictionary_slots_spill(rom, slot_payload)
    else:
        ptrs = list(dictionary.ptrs)
        spill_end = 0x99BA

    # Build final plan / replacements for shift path and reporting.
    planned: List[dict] = []
    bank_replacements: Dict[int, bytes] = {}
    for meta in line_meta:
        line = meta["line"]
        body = meta["body"]
        prefix = meta["prefix"]
        plain = meta["plain"]
        compressed = meta["compressed"]
        mode = meta.get("overflow_mode")

        if mode in {"inplace_plain", "inplace_compressed"}:
            new_payload = meta["planned_payload"]
            full_index = None
            new_body_len = len(body)
            bank_replacements[meta["abs_off"]] = new_payload
        elif mode == "skipped" or mode is None:
            if mode is None and not meta.get("needs_overflow"):
                # Should have been inplace; treat as skipped if somehow missed.
                mode = "skipped"
                skipped_no_capacity.append(line["abs"])
            planned.append(
                {
                    "abs": line["abs"],
                    "jp": line.get("jp"),
                    "ko": line["ko"],
                    "mode": mode or "skipped",
                    "dict_index": None,
                    "token": None,
                    "plain_len": len(plain),
                    "compressed_len": len(compressed),
                    "old_body_len": len(body),
                    "new_body_len": len(body),
                    "new_abs": line["abs"],
                    "abs_off": meta["abs_off"],
                }
            )
            continue
        elif mode in {"shift_rebuild", "spill_rebuild"}:
            new_payload = shift_rebuild_abs[meta["abs_off"]]
            full_index = None
            new_body_len = len(new_payload) - len(prefix)
            bank_replacements[meta["abs_off"]] = new_payload
        elif mode == "dict_token":
            full_index = line_to_index[line["ko"]]
            new_body = padded_body(token_from_dict_index(full_index), len(body))
            new_payload = bytes(prefix) + new_body
            new_body_len = len(body)
            bank_replacements[meta["abs_off"]] = new_payload
            # Dict-token lines were not written in phase 1.
            rom[file_abs(rom, meta["abs_off"]) : file_abs(rom, meta["abs_off"]) + len(new_payload)] = new_payload
        else:
            planned.append(
                {
                    "abs": line["abs"],
                    "jp": line.get("jp"),
                    "ko": line["ko"],
                    "mode": "skipped",
                    "dict_index": None,
                    "token": None,
                    "plain_len": len(plain),
                    "compressed_len": len(compressed),
                    "old_body_len": len(body),
                    "new_body_len": len(body),
                    "new_abs": line["abs"],
                    "abs_off": meta["abs_off"],
                }
            )
            continue

        planned.append(
            {
                "abs": line["abs"],
                "jp": line.get("jp"),
                "ko": line["ko"],
                "mode": mode,
                "dict_index": full_index,
                "token": (
                    " ".join(f"{b:02X}" for b in token_from_dict_index(full_index))
                    if full_index is not None
                    else None
                ),
                "plain_len": len(plain),
                "compressed_len": len(compressed),
                "old_body_len": len(body),
                "new_body_len": new_body_len,
                "new_abs": line["abs"],
                "abs_off": meta["abs_off"],
            }
        )

    flexible_abs = set(shift_rebuild_abs.keys())
    if overflow_mode == "spill":
        spill_candidates = {
            abs_off: bank_replacements[abs_off]
            for abs_off in flexible_abs
            if abs_off in bank_replacements
        }
        spill_kept, dropped_abs = filter_spill_to_tail_capacity(rom, spill_candidates)
        dropped_set = set(dropped_abs)
        skipped_no_capacity.extend(f"{abs_off:06X}" for abs_off in dropped_abs)
        for row in planned:
            if row["abs_off"] in dropped_set and row["mode"] == "spill_rebuild":
                row["mode"] = "skipped_bank_full"
                row["new_body_len"] = row["old_body_len"]
        bank_replacements = spill_kept
    elif overflow_mode == "exp_spill":
        # Keep all overflow candidates; expansion banks have multi-MiB room.
        # filter_replacements_to_bank_capacity is for in-bank shift only.
        pass
    else:
        bank_replacements, dropped_abs = filter_replacements_to_bank_capacity(
            rom, bank_replacements, flexible_abs
        )
        dropped_set = set(dropped_abs)
        skipped_no_capacity.extend(f"{abs_off:06X}" for abs_off in dropped_abs)
        for row in planned:
            if row["abs_off"] in dropped_set:
                row["mode"] = "skipped_bank_full"
                row["new_body_len"] = row["old_body_len"]

    bank_report = None
    still_overflow = any(
        row["mode"] in {"shift_rebuild", "spill_rebuild"} for row in planned
    )
    if still_overflow:
        if overflow_mode == "spill":
            bank_report = spill_replacements_to_bank_tails(rom, bank_replacements)
        elif overflow_mode == "exp_spill":
            # Only the overflow payloads (not already-written dict_token lines).
            exp_payloads = {
                abs_off: bank_replacements[abs_off]
                for abs_off in flexible_abs
                if abs_off in bank_replacements
            }
            bank_report = spill_replacements_to_expansion(rom, exp_payloads)
        else:
            bank_report = shift_replacements_in_text_banks(rom, bank_replacements)
        abs_map = {
            int(old, 16): int(new, 16)
            for old, new in bank_report.get("mapping", {}).items()
        }
        for row in planned:
            if str(row["mode"]).startswith("skipped"):
                continue
            if row["mode"] == "spill_rebuild" and row["abs_off"] not in abs_map:
                # Pointer-less or ambiguous (too many hits) — leave original bytes.
                row["mode"] = "skipped_no_pointer"
                row["new_body_len"] = row["old_body_len"]
                skipped_no_capacity.append(f"{row['abs_off']:06X}")
                continue
            new_abs = abs_map.get(row["abs_off"], row["abs_off"])
            row["new_abs"] = f"{new_abs:06X}"
            if (
                row["mode"] != "shift_rebuild"
                and row["mode"] != "spill_rebuild"
                and new_abs != row["abs_off"]
            ):
                row["mode"] = f"{row['mode']}+shifted"
            elif row["mode"] == "spill_rebuild" and new_abs != row["abs_off"]:
                row["mode"] = "spill_rebuild"
    else:
        for abs_off, payload in bank_replacements.items():
            fa = file_abs(rom, abs_off)
            rom[fa : fa + len(payload)] = payload

    plain_by_abs = {meta["abs_off"]: meta["plain"] for meta in line_meta}
    d2 = Dictionary(rom)
    results = []
    fails = 0
    from rebuild_script_banks import EXP_SCRIPT_SEG_END, EXP_SCRIPT_SEG_START
    from monoeye_rom import is_expanded_rom

    def resolve_check_abs(logical: int) -> int:
        seg = (logical >> 16) & 0xFF
        if is_expanded_rom(rom) and EXP_SCRIPT_SEG_START <= seg <= EXP_SCRIPT_SEG_END:
            return logical
        return file_abs(rom, logical)

    for row in planned:
        row = dict(row)
        row.pop("abs_off", None)
        if str(row["mode"]).startswith("skipped"):
            row["decode_check"] = None
            row["ko_source"] = row["ko"]
            results.append(row)
            continue
        check_abs = resolve_check_abs(int(row["new_abs"] or row["abs"], 16))
        payload, _ = read_encoded_z(rom, check_abs)
        _prefix, body, _kind = split_prefix_body(payload)
        decoded = d2.expand(body, tbl)
        expected = d2.expand(plain_by_abs[int(row["abs"], 16)], tbl)
        row["ko_source"] = row["ko"]
        row["ko"] = expected
        row["decode_check"] = decoded
        if decoded != expected:
            fails += 1
        results.append(row)

    patched = sum(1 for row in results if not str(row["mode"]).startswith("skipped"))
    return {
        "dict_count": len(ptrs),
        "ptr_start": DICT_PTR_START,
        "reclaimable_slots": len(free_slots),
        "shared_phrases": len(shared),
        "overflow_lines": len(chosen_texts),
        "slots_used": len(slot_payload),
        "dict_slots_written": sorted(slot_payload.keys()),
        "lines_patched": patched,
        "patched_abs": [
            row["abs"] for row in results if not str(row["mode"]).startswith("skipped")
        ],
        "lines_skipped_unencodable": len(skipped_unencodable),
        "lines_skipped_excluded_scope": len(skipped_excluded_scope),
        "lines_skipped_no_capacity": len(skipped_no_capacity),
        "skipped_unencodable_sample": skipped_unencodable[:40],
        "skipped_excluded_scope_sample": skipped_excluded_scope[:40],
        "skipped_no_capacity_sample": skipped_no_capacity[:40],
        "decode_failures": fails,
        "bank_rebuild": bank_report,
        "mode_counts": {
            mode: sum(1 for row in results if row["mode"] == mode)
            for mode in sorted({row["mode"] for row in results})
        },
        "shared_phrase_samples": shared[:20],
        "results": results,
    }


def load_translation_lines(path: Path) -> List[dict]:
    """Load {lines:[...]} JSON or translation_sheet.csv."""
    if path.suffix.lower() == ".csv":
        import csv

        csv.field_size_limit(10_000_000)
        lines: List[dict] = []
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                abs_raw = (row.get("abs") or "").strip()
                ko = (row.get("ko") or "").strip()
                if not abs_raw or not ko:
                    continue
                try:
                    abs_off = int(abs_raw, 16)
                except ValueError:
                    continue
                lines.append(
                    {
                        "abs": f"{abs_off:06X}",
                        "jp": row.get("jp") or "",
                        "ko": ko.replace(" ", "　"),
                    }
                )
        return lines
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["lines"] if isinstance(payload, dict) else payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_patch_pad3.tbl",
    )
    ap.add_argument(
        "--translations",
        type=Path,
        default=ROOT / "data" / "translations_seed.json",
    )
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "patch")
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=None,
        help="Output ROM (default: <out>/monoeye_ko_expanded.wsc)",
    )
    ap.add_argument("--max-shared-phrases", type=int, default=1024)
    ap.add_argument(
        "--no-bank-rebuild",
        action="store_true",
        help="Skip overflow lines that need bank rebuild/spill",
    )
    ap.add_argument(
        "--overflow-mode",
        choices=["spill", "shift", "none", "exp_spill"],
        default="exp_spill",
        help="Overflow: spill=tail, exp_spill=16MB bank30+, shift=repack, none=skip",
    )
    ap.add_argument(
        "--allow-inplace",
        action="store_true",
        help="Experimental: rewrite fitting lines in place (can skip opening / freeze stage 1)",
    )
    ap.add_argument(
        "--hangul-marker",
        type=lambda s: int(s, 16),
        default=0xE3DB,
        help="Prefix each Hangul syllable with this marker (0 to disable)",
    )
    ap.add_argument(
        "--max-abs",
        type=lambda s: int(s, 16),
        default=None,
        help="Only patch records at/under this absolute offset (hex), e.g. 6001C5",
    )
    ap.add_argument(
        "--min-abs",
        type=lambda s: int(s, 16),
        default=None,
        help="Only patch records at/above this absolute offset (hex)",
    )
    args = ap.parse_args()

    assert_translation_source_allowed(
        args.translations,
        role="expanded translation application",
    )

    if not args.rom.exists():
        args.rom = find_rom(ROOT)
        print(f"WARNING: font ROM missing, using {args.rom}")

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    rom = load_rom(args.rom)
    tbl = Tbl.load(args.tbl)
    lines = load_translation_lines(args.translations)
    print(f"Loaded {len(lines)} translation lines from {args.translations}")

    marker = None if args.hangul_marker == 0 else args.hangul_marker
    overflow_mode = "none" if args.no_bank_rebuild else args.overflow_mode
    print(f"Overflow mode: {overflow_mode}")
    report = apply_translations_expanded(
        rom,
        tbl,
        lines,
        max_shared_phrases=args.max_shared_phrases,
        allow_bank_rebuild=overflow_mode != "none",
        allow_inplace=args.allow_inplace,
        max_abs=args.max_abs,
        min_abs=args.min_abs,
        hangul_marker_code=marker,
        overflow_mode=overflow_mode,
    )
    report["hangul_marker"] = f"{marker:04X}" if marker is not None else None
    report["checksum"] = f"{update_ws_checksum(rom):04X}"

    out_rom = args.out_rom or (out / "monoeye_ko_expanded.wsc")
    # Guard: do not silently replace the promoted 16MB tip with an 8MB rebuild.
    tip = ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc"
    if (
        out_rom.resolve() == tip.resolve()
        and tip.exists()
        and tip.stat().st_size == 0x1000000
        and len(rom) == 0x800000
    ):
        raise SystemExit(
            "refusing to overwrite 16MB monoeye_ko_expanded.wsc with an 8MB image; "
            "pass --out-rom to a different path (8MB backup: monoeye_ko_expanded_8mb.wsc)"
        )
    out_rom.write_bytes(rom)

    print(
        f"Patched {report['lines_patched']} lines | "
        f"reclaimable={report['reclaimable_slots']} "
        f"shared={report['shared_phrases']} "
        f"overflow={report['overflow_lines']} "
        f"skipped_cap={report.get('lines_skipped_no_capacity', 0)} "
        f"skipped_bad={report.get('lines_skipped_unencodable', 0)} "
        f"decode_fail={report['decode_failures']} "
        f"modes={report['mode_counts']}"
    )
    if report.get("bank_rebuild"):
        print(
            f"  shift relocated={report['bank_rebuild'].get('relocated_records')} "
            f"pointer_fixes={report['bank_rebuild'].get('pointer_fixes')} "
            f"forms={report['bank_rebuild'].get('pointer_form_counts')}"
        )
    shown = 0
    for row in report["results"]:
        if str(row.get("mode", "")).startswith("skipped"):
            continue
        ok = row.get("decode_check") == row.get("ko")
        mark = "OK" if ok else "FAIL"
        loc = row["abs"]
        new_loc = row.get("new_abs")
        where = f"@{loc}" if not new_loc or new_loc == loc else f"@{loc}->{new_loc}"
        try:
            print(f"  [{mark}] {where} ({row['mode']}) -> {row['decode_check']}")
        except UnicodeEncodeError:
            print(
                f"  [{mark}] {where} ({row['mode']}) -> "
                f"{str(row.get('decode_check')).encode('unicode_escape').decode()}"
            )
        shown += 1
        if shown >= 20:
            break
    if report["lines_patched"] > shown:
        print(f"  ... {report['lines_patched'] - shown} more patched lines")

    report_path = out / "apply_expanded_report.json"
    fails = [
        r
        for r in report["results"]
        if r.get("decode_check") is not None and r.get("decode_check") != r.get("ko")
    ]
    disk_report = {k: v for k, v in report.items() if k != "results"}
    disk_report["results_sample"] = [
        r
        for r in report["results"]
        if not str(r.get("mode", "")).startswith("skipped")
    ][:50]
    disk_report["decode_failure_rows"] = fails[:200]
    report_path.write_text(
        json.dumps(disk_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {out_rom}")
    print(f"Wrote {report_path}")
    if report["decode_failures"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
