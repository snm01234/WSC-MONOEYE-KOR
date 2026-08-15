#!/usr/bin/env python3
"""
Apply quality KO lines into extended dictionary slots + script tokens.

On 16MB ROMs (or meta.ext_in_expansion): payloads go to expand bank 0x10
via patch_exp_dictionary. Otherwise stock bank 5E (patch_ext_dictionary).

Size-preserving sequential rewrite: prefix + dict token + space (0x01) pad.
Prefer --force-format only when reassigning indices together with script tokens;
migrated bank10 phrases should not be wiped while scripts still reference them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_safe_unit import padded_token_payload, read_record_at  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    MAX_SAFE_RECORD_LEN,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    is_expanded_rom,
    load_rom,
    read_encoded_z,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import (  # noqa: E402
    encode_ko_text,
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)
from event_record_heuristics import looks_like_event_body  # noqa: E402
from patch_exp_dictionary import (  # noqa: E402
    EXP_PTR_OFF_DEFAULT,
    EXP_SEG,
    install_exp_dict_hook,
    make_exp_dictionary,
    write_exp_dictionary_slots as write_exp_slots,
)
from expand_dictionary import (  # noqa: E402
    DEFAULT_REF_REGIONS,
    build_dict_token_locs,
    filter_story_safe_indices,
    reclaimable_slots,
    slot_rewrite_refuse_reason,
    write_dictionary_slots_spill,
)
from patch_ext_dictionary import (  # noqa: E402
    EXT_PTR_OFF_DEFAULT,
    STOCK_DICT_COUNT,
    install_ext_dict_hook,
    write_ext_dictionary_slots as write_stock_ext_slots,
)
from rebuild_script_banks import (  # noqa: E402
    MAX_SPILL_POINTER_HITS,
    SEGMENTED_POINTER_KINDS,
    discover_pointer_hits,
)

# Coverage bands — match docs/SCRIPT_COVERAGE_STATUS.md §3.3 / §8:
#   opening     6040A5–60456A  (included in early below)
#   early_tut   60456B–607000
#   bank60_rest 607001–60FFFF
#   bank61/62   610000–62FFFF
BAND_EARLY_LO = 0x6040A5
BAND_EARLY_HI = 0x607000
BAND_BANK60_REST_LO = 0x607001
BAND_BANK60_REST_HI = 0x60FFFF
BAND_LATE_LO = 0x610000
BAND_LATE_HI = 0x62FFFF

DEFAULT_BAND_EARLY_BUDGET = 180
DEFAULT_BAND_BANK60_REST_BUDGET = 50


def hybrid_band_name(min_abs: int) -> str:
    """Return band label for a unique's earliest abs (SCRIPT_COVERAGE_STATUS)."""
    if BAND_EARLY_LO <= min_abs <= BAND_EARLY_HI:
        return "early"  # opening head + early_tut
    if BAND_BANK60_REST_LO <= min_abs <= BAND_BANK60_REST_HI:
        return "bank60_rest"
    if BAND_LATE_LO <= min_abs <= BAND_LATE_HI:
        return "late"  # bank61 / bank62
    return "other"


def _within_band_rank_key(kv: Tuple[str, List[int]]) -> Tuple[int, int]:
    abs_list = kv[1]
    # Frequency first inside a band (one slot covers many sites), then earliest.
    return (-len(abs_list), min(abs_list))


def select_hybrid_band_uniques(
    text_to_abs: Dict[str, List[int]],
    *,
    capacity: int,
    early_budget: int = DEFAULT_BAND_EARLY_BUDGET,
    bank60_rest_budget: int = DEFAULT_BAND_BANK60_REST_BUDGET,
) -> Tuple[List[Tuple[str, List[int]]], List[Tuple[str, List[int]]], Dict[str, object]]:
    """
    Band-budget ranking for --rank hybrid-bands.

    Reserves up to ``early_budget`` assignment slots for uniques whose min abs
    is in [6040A5, 607000], then up to ``bank60_rest_budget`` (+ unused early
    quota) for [607001, 60FFFF], then fills remaining capacity from
    [610000, 62FFFF] (then any out-of-window "other"). Empty reserved slots
    cascade to later bands so capacity is never wasted.
    """
    buckets: Dict[str, List[Tuple[str, List[int]]]] = {
        "early": [],
        "bank60_rest": [],
        "late": [],
        "other": [],
    }
    for ko, abs_list in text_to_abs.items():
        buckets[hybrid_band_name(min(abs_list))].append((ko, abs_list))
    for name in buckets:
        buckets[name].sort(key=_within_band_rank_key)

    chosen: List[Tuple[str, List[int]]] = []
    taken = {"early": 0, "bank60_rest": 0, "late": 0, "other": 0}

    def _take(pool: List[Tuple[str, List[int]]], band: str, limit: int) -> int:
        if capacity <= 0 or limit <= 0:
            return 0
        n = min(limit, len(pool), capacity - len(chosen))
        if n <= 0:
            return 0
        chosen.extend(pool[:n])
        taken[band] += n
        return n

    got_early = _take(buckets["early"], "early", early_budget)
    unused_early = max(0, early_budget - got_early)
    b60_limit = bank60_rest_budget + unused_early
    got_b60 = _take(buckets["bank60_rest"], "bank60_rest", b60_limit)
    # Unused early/b60 quota cascades: late/other simply fill remaining capacity.
    got_late = _take(buckets["late"], "late", capacity - len(chosen))
    got_other = _take(buckets["other"], "other", capacity - len(chosen))

    chosen_keys = {ko for ko, _ in chosen}
    leftover: List[Tuple[str, List[int]]] = []
    for band in ("early", "bank60_rest", "late", "other"):
        for item in buckets[band]:
            if item[0] not in chosen_keys:
                leftover.append(item)

    stats: Dict[str, object] = {
        "rank": "hybrid-bands",
        "capacity": capacity,
        "early_budget": early_budget,
        "bank60_rest_budget": bank60_rest_budget,
        "band_pool_sizes": {k: len(v) for k, v in buckets.items()},
        "band_taken": dict(taken),
        "band_taken_detail": {
            "early": got_early,
            "bank60_rest": got_b60,
            "late": got_late,
            "other": got_other,
        },
        "bands": {
            "early": f"{BAND_EARLY_LO:06X}-{BAND_EARLY_HI:06X}",
            "bank60_rest": f"{BAND_BANK60_REST_LO:06X}-{BAND_BANK60_REST_HI:06X}",
            "late": f"{BAND_LATE_LO:06X}-{BAND_LATE_HI:06X}",
        },
    }
    return chosen, leftover, stats


def load_ext_meta(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def make_dictionary(rom: bytes | bytearray, meta: dict) -> Dictionary:
    if meta.get("ext_in_expansion"):
        return make_exp_dictionary(rom, meta)
    stock = int(meta.get("stock_count", STOCK_DICT_COUNT))
    slots = int(meta.get("slot_count", 0))
    ext_off = int(meta.get("ext_ptr_off", f"{EXT_PTR_OFF_DEFAULT:04X}"), 16)
    if slots <= 0:
        return Dictionary(rom)
    return Dictionary(
        rom,
        count=stock + slots,
        ext_ptr_off=ext_off,
        stock_count=stock,
    )


EXT3_ALIAS_LEAF_OFF = 0x7FFE9D
EXT3_ONE_BANK_LEAF_LEN = 126
EXT3_ONE_BANK_LEAF_SHA256 = "5f873ff7164b4142fbf46a30914830f672a10008d47b317979110d496fab5292"
EXT3_FIVE_BANK_LEAF_LEN = 123
EXT3_FIVE_BANK_LEAF_SHA256 = "199936d8cc33388f57711012ab1eb5f4c0b024be0f5d3e0b095a7892c48c6bf0"


def detect_ext3_alias_page_count(rom: bytes | bytearray) -> int:
    """Return the exact runtime alias page count encoded in the active leaf.

    The alias rule must be enabled only when the corresponding runtime has
    actually been installed.  This preserves historical/pre-alias ROM decoding
    while making all shared offline audits agree with the one-bank and five-bank
    E5 18 render paths.
    """
    if not is_expanded_rom(rom):
        return 0
    start = stock_base(rom) + EXT3_ALIAS_LEAF_OFF
    one = bytes(rom[start:start + EXT3_ONE_BANK_LEAF_LEN])
    five = bytes(rom[start:start + EXT3_FIVE_BANK_LEAF_LEN])
    if (
        len(five) == EXT3_FIVE_BANK_LEAF_LEN
        and hashlib.sha256(five).hexdigest() == EXT3_FIVE_BANK_LEAF_SHA256
        and bytes(
            rom[
                start + EXT3_FIVE_BANK_LEAF_LEN:
                start + EXT3_ONE_BANK_LEAF_LEN
            ]
        ) == b"\xFF" * (EXT3_ONE_BANK_LEAF_LEN - EXT3_FIVE_BANK_LEAF_LEN)
    ):
        return 5
    if (
        len(one) == EXT3_ONE_BANK_LEAF_LEN
        and hashlib.sha256(one).hexdigest() == EXT3_ONE_BANK_LEAF_SHA256
    ):
        return 1
    return 0


def attach_ext3(
    d: Dictionary, rom: bytes | bytearray, meta3: dict | None
) -> Dictionary:
    """Return ``d`` re-created with the ext3 expansion banks wired in.

    ``make_dictionary`` only knows about the stock ``5F`` table and the bank10
    extension, so a ``E5 18 xx yy`` token expands to ``<BADDICT:…>`` and any
    caller that measures Hangul coverage counts the line as untranslated. Every
    tool that reads text out of an ext3-promoted ROM needs this wiring, so it
    lives here next to ``make_dictionary`` instead of being re-derived per tool.

    Returns ``d`` unchanged when the meta is missing or declares no ext3 banks.
    """
    if not meta3:
        return d
    num_banks = int(meta3.get("num_banks") or 0)
    if num_banks <= 0:
        slots = int(meta3.get("slots", 0))
        num_banks = max(1, (slots + 0xFFF) // 0x1000) if slots else 0
    if num_banks <= 0:
        return d
    seg0 = meta3.get("exp_seg0") or meta3.get("exp_seg") or "11"
    alias_page_count = detect_ext3_alias_page_count(rom)
    return Dictionary(
        rom,
        count=d.count,
        ext_ptr_off=d.ext_ptr_off,
        ext_seg=d.ext_seg,
        stock_count=d.stock_count,
        ext_in_expansion=d.ext_in_expansion,
        ext3_ptr_off=0,
        ext3_seg=int(seg0, 16),
        ext3_banks=num_banks,
        ext3_alias_page_count=alias_page_count,
        ext3_alias_local_start=0x0600,
        ext3_alias_seg=0x21,
    )


def make_dictionary_ext3(
    rom: bytes | bytearray, meta: dict, meta3: dict | None
) -> Dictionary:
    """``make_dictionary`` plus ext3 wiring — the full read path of a tip ROM."""
    return attach_ext3(make_dictionary(rom, meta), rom, meta3)


def _use_expansion(rom: bytes | bytearray, meta: dict) -> bool:
    if meta.get("ext_in_expansion"):
        return True
    return is_expanded_rom(rom)


def _file_abs(rom: bytes | bytearray, logical_abs: int) -> int:
    """Sheet/script abs is stock-relative; 16MB prepend adds stock_base."""
    return stock_base(rom) + logical_abs


def _hit_logical_bank(rom: bytes | bytearray, file_abs: int) -> int:
    return ((file_abs - stock_base(rom)) >> 16) & 0xFF


def spillable_abs_set(rom: bytes | bytearray, abs_list: List[int]) -> Set[int]:
    """
    Abs that exp_spill can relocate (segmented far pointer, low hit count).

    Use a pre-spill ROM (e.g. expdict) so already-relocated lines are not
    misclassified as sequential on the expspill image.
    """
    by_seg: Dict[int, Set[int]] = defaultdict(set)
    for abs_off in abs_list:
        seg = (abs_off >> 16) & 0xFF
        if 0x60 <= seg <= 0x6F:
            by_seg[seg].add(abs_off & 0xFFFF)

    spillable: Set[int] = set()
    for segment, offs in by_seg.items():
        from rebuild_script_banks import POINTER_SEARCH_DENY_BANKS  # noqa: WPS433

        hits = discover_pointer_hits(rom, segment, offs)
        hits = [
            h
            for h in hits
            if 0x50 <= _hit_logical_bank(rom, h.abs_at) <= 0x6F
            and _hit_logical_bank(rom, h.abs_at) not in POINTER_SEARCH_DENY_BANKS
        ]
        hits_by_off: Dict[int, List] = defaultdict(list)
        for hit in hits:
            hits_by_off[hit.old_off].append(hit)
        for off, off_hits in hits_by_off.items():
            if len(off_hits) > MAX_SPILL_POINTER_HITS:
                continue
            if not any(h.kind in SEGMENTED_POINTER_KINDS for h in off_hits):
                continue
            spillable.add((segment << 16) | off)
    return spillable


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_patch_pad3.tbl",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out" / "script" / "translations_full.json",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data" / "translations_seed_hook96.json",
    )
    ap.add_argument(
        "--meta",
        type=Path,
        default=ROOT / "out" / "patch" / "exp_dictionary_meta.json",
    )
    ap.add_argument("--slots", type=int, default=265)
    ap.add_argument(
        "--force-format",
        action="store_true",
        help="Rebuild empty ext ptr/payload table before assign (use when growing slots)",
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_marked.wsc",
        help="Baseline ROM for collision repair (default: marked)",
    )
    ap.add_argument(
        "--only-no-pointer",
        action="store_true",
        help=(
            "Only assign/patch sequential-scan lines (not exp_spill-relocatable). "
            "Ranks unique KO among that pool; keeps already-matching high-freq phrases."
        ),
    )
    ap.add_argument(
        "--pointer-ref-rom",
        type=Path,
        default=None,
        help=(
            "ROM used to classify far pointers (default: --rom, or expdict when "
            "input looks like expspill). Prefer pre-spill image."
        ),
    )
    ap.add_argument(
        "--rank",
        choices=("freq", "early-abs", "hybrid-bands"),
        default="freq",
        help=(
            "Unique KO ranking: freq=high occurrence first; "
            "early-abs=earliest min abs first (early-game feel); "
            "hybrid-bands=reserve slot budgets per coverage band "
            "(early_tut, then bank60_rest, then bank61/62)"
        ),
    )
    ap.add_argument(
        "--band-early",
        type=int,
        default=None,
        help=(
            "hybrid-bands: max slots for min_abs in "
            f"[{BAND_EARLY_LO:06X},{BAND_EARLY_HI:06X}] "
            f"(default {DEFAULT_BAND_EARLY_BUDGET})"
        ),
    )
    ap.add_argument(
        "--band-bank60-rest",
        type=int,
        default=None,
        help=(
            "hybrid-bands: max slots for min_abs in "
            f"[{BAND_BANK60_REST_LO:06X},{BAND_BANK60_REST_HI:06X}] "
            f"(default {DEFAULT_BAND_BANK60_REST_BUDGET}; unused early quota cascades here)"
        ),
    )
    ap.add_argument(
        "--stock-reclaim",
        action="store_true",
        help=(
            "Also assign leftover early uniques into unreclaimed stock 5F slots "
            "(~10 free on tip). Spill-writes only; no full dict rebuild. "
            "With hybrid-bands, stock slots count toward the same band budgets."
        ),
    )
    ap.add_argument(
        "--sole-reclaim",
        action="store_true",
        help=(
            "After ext/stock script patches, run sole-fit stock reclaim "
            "(mode1: sole owner→own KO; mode2: inline JP into pure-token owner "
            "and reuse slot). Spill-writes only; no shared slots / force-format."
        ),
    )
    ap.add_argument(
        "--sole-lo",
        type=lambda s: int(s, 16),
        default=0x60456B,
        help="Sole-reclaim early target window low abs (default early_tut)",
    )
    ap.add_argument(
        "--sole-hi",
        type=lambda s: int(s, 16),
        default=0x607000,
        help="Sole-reclaim early target window high abs",
    )
    ap.add_argument("--hangul-marker", default="E3DB")
    ap.add_argument("--max-assign", type=int, default=0, help="0=use all free ext slots")
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out" / "patch" / "ext_dict_apply_report.json",
    )
    args = ap.parse_args()
    if args.rank == "hybrid-bands":
        if args.band_early is None:
            args.band_early = DEFAULT_BAND_EARLY_BUDGET
        if args.band_bank60_rest is None:
            args.band_bank60_rest = DEFAULT_BAND_BANK60_REST_BUDGET
    elif args.band_early is not None or args.band_bank60_rest is not None:
        print(
            "warning: --band-early/--band-bank60-rest ignored unless "
            "--rank hybrid-bands",
            file=sys.stderr,
        )

    marker = int(args.hangul_marker, 16)
    rom = bytearray(load_rom(args.rom))
    tbl = Tbl.load(args.tbl)

    meta = load_ext_meta(args.meta)
    use_exp = _use_expansion(rom, meta)
    prev_slots = int(meta.get("slot_count", 0) or 0)
    want_slots = int(args.slots)
    grow = want_slots > prev_slots > 0
    force_format = bool(args.force_format or grow or not meta.get("helper"))
    default_ptr = EXP_PTR_OFF_DEFAULT if use_exp else EXT_PTR_OFF_DEFAULT
    ext_ptr_off = int(meta.get("ext_ptr_off", f"{default_ptr:04X}"), 16)

    from patch_ext_dictionary import STOCK_LOAD_SITE, STOCK_LOAD_EXPECT

    site = bytes(rom[stock_base(rom) + STOCK_LOAD_SITE : stock_base(rom) + STOCK_LOAD_SITE + 5])
    need_install = (
        not meta.get("helper")
        or site == STOCK_LOAD_EXPECT
        or force_format
        or int(meta.get("slot_count", 0) or 0) != want_slots
        or (use_exp and not meta.get("ext_in_expansion"))
    )
    if need_install:
        if use_exp:
            meta = install_exp_dict_hook(
                rom,
                ext_ptr_off=ext_ptr_off if meta.get("ext_in_expansion") else EXP_PTR_OFF_DEFAULT,
                slot_count=want_slots,
                force_format=force_format,
                migrate_from_5e=not meta.get("ext_in_expansion"),
            )
        else:
            meta = install_ext_dict_hook(
                rom,
                ext_ptr_off=ext_ptr_off,
                slot_count=want_slots,
                force_format=force_format,
            )

    # CLI --slots is authoritative for this run.
    meta["slot_count"] = want_slots
    stock = int(meta["stock_count"])
    slot_count = want_slots
    ext_ptr_off = int(meta["ext_ptr_off"], 16)
    meta["index_base"] = stock
    meta["index_end"] = stock + slot_count - 1
    if use_exp:
        meta["ext_in_expansion"] = True
        meta.setdefault("ext_seg", f"{EXP_SEG:02X}")

    seed_rows = json.loads(args.seed.read_text(encoding="utf-8")).get("lines", [])
    seed_abs = {int(row["abs"], 16) for row in seed_rows}
    lines = json.loads(args.sheet.read_text(encoding="utf-8"))["lines"]
    abs_to_line = {int(line["abs"], 16): line for line in lines}

    # Event heuristics must use marked/baseline bytes — current ROM may already
    # carry prior ext_dict tokens that hide the original control payload.
    # Read without load_rom() so 16MB stock_base is not reset to 0.
    if args.base_rom.exists():
        base_rom = args.base_rom.read_bytes()
    else:
        base_rom = bytes(rom)

    d = make_dictionary(rom, meta)

    # Snapshot seed decode before slot/script mutation (regression gate).
    seed_input_decode: Dict[int, str] = {}
    for row in seed_rows:
        abs_off = int(row["abs"], 16)
        try:
            body = split_prefix_body(
                read_encoded_z(rom, _file_abs(rom, abs_off))[0]
            )[1]
            seed_input_decode[abs_off] = d.expand(body, tbl).rstrip("\u3000")
        except Exception:
            continue

    # Collect quality candidates first; optional sequential-only filter next.
    candidates: List[Tuple[str, int, bool]] = []  # ko, abs, already_match
    skipped_event_abs = 0
    skipped_body_lt2 = 0
    for abs_off, line in abs_to_line.items():
        if abs_off in seed_abs:
            continue
        ko = normalize_ko_text(line.get("ko") or "")
        if not ko or is_low_quality_ko(ko):
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if enc is None:
            continue
        # Drop sheet rows with embedded hex control placeholders like <EB>.
        if "<E" in ko.upper() or "<BADDICT" in ko.upper():
            continue
        try:
            base_got = read_encoded_z_safe(base_rom, abs_off)
            if base_got is None:
                continue
            base_body = split_prefix_body(base_got[0])[1]
            if looks_like_event_body(base_body):
                # Sheet often mislabels event/control bytes as dialogue
                # (e.g. 65:CB0F → Event Error … 51983). Never rewrite those.
                skipped_event_abs += 1
                continue
            got = read_encoded_z_safe(rom, _file_abs(rom, abs_off))
            if got is None:
                continue
            original = got[0]
            prefix, body, _ = split_prefix_body(original)
            if len(body) < 2 or len(prefix) + 2 > len(original):
                skipped_body_lt2 += 1
                continue
            already = d.expand(body, tbl) == d.expand(enc, tbl)
        except Exception:
            continue
        candidates.append((ko, abs_off, already))

    pool_stats: Dict[str, object] = {
        "quality_candidates": len(candidates),
        "already_match": sum(1 for _k, _a, m in candidates if m),
        "skipped_event_abs": skipped_event_abs,
        "skipped_body_lt2": skipped_body_lt2,
    }

    if args.only_no_pointer:
        ptr_path = args.pointer_ref_rom
        if ptr_path is None:
            # Prefer pre-spill expdict so relocated far-pointer lines stay excluded.
            guess = ROOT / "out" / "patch" / "monoeye_ko_expanded_8mb.wsc"
            ptr_path = guess if guess.exists() else args.rom
        ptr_rom = ptr_path.read_bytes() if ptr_path != args.rom else bytes(rom)
        cand_abs = [abs_off for _ko, abs_off, _m in candidates]
        spillable = spillable_abs_set(ptr_rom, cand_abs)
        before = len(candidates)
        candidates = [
            (ko, abs_off, already)
            for ko, abs_off, already in candidates
            if abs_off not in spillable
        ]
        pool_stats["pointer_ref_rom"] = str(ptr_path)
        pool_stats["spillable_excluded"] = before - len(candidates)
        pool_stats["sequential_pool"] = len(candidates)
        # Keep already-matching uniques in the ranking so high-freq phrases
        # retain slots when we reassign the 264 safe indices.
        text_to_abs: Dict[str, List[int]] = defaultdict(list)
        for ko, abs_off, _already in candidates:
            text_to_abs[ko].append(abs_off)
    else:
        text_to_abs = defaultdict(list)
        for ko, abs_off, already in candidates:
            if already:
                continue
            text_to_abs[ko].append(abs_off)
        pool_stats["assign_pool"] = len(text_to_abs)

    def _rank_key(kv: Tuple[str, List[int]]) -> Tuple:
        abs_list = kv[1]
        m = min(abs_list)
        if args.rank == "early-abs":
            # Soft band order (no hard slot budgets): early ≤607000, then
            # bank60_rest, bank61, then later — see SCRIPT_COVERAGE_STATUS.md.
            if m <= BAND_EARLY_HI:
                band = 0
            elif m <= BAND_BANK60_REST_HI:
                band = 1
            elif m <= 0x61FFFF:
                band = 2
            else:
                band = 3
            return (band, m, -len(abs_list))
        return (-len(abs_list), m)

    # Skip indices whose token trail is 0x00 (zstring NUL collision), e.g. 0xF00.
    # Also pin ext indices still referenced by seed lines (do not reassign).
    reserved_seed_indices: Set[int] = set()
    for abs_off in seed_abs:
        try:
            got = read_encoded_z_safe(rom, _file_abs(rom, abs_off))
            if got is None:
                continue
            body = split_prefix_body(got[0])[1]
            if len(body) < 2 or not (0xF0 <= body[0] <= 0xFF):
                continue
            idx = ((body[0] - 0xF0) << 8) | body[1]
            if stock <= idx < stock + slot_count:
                reserved_seed_indices.add(idx)
        except Exception:
            continue
    # Exclude FF-page indices already embedded in battle/UI zstrings — assigning
    # story Hangul there is the tutorial/help invasion class (FF63 etc.).
    locs_story = build_dict_token_locs(rom, regions=DEFAULT_REF_REGIONS)
    raw_safe = [
        stock + i
        for i in range(slot_count)
        if dict_token_safe_in_zstring(stock + i)
        and (stock + i) not in reserved_seed_indices
    ]
    safe_indices = filter_story_safe_indices(rom, raw_safe, locs=locs_story)
    ext_budget = len(safe_indices) if args.max_assign <= 0 else min(
        len(safe_indices), args.max_assign
    )
    pool_stats["ext_indices_before_aux_filter"] = len(raw_safe)
    pool_stats["ext_indices_aux_blocked"] = len(raw_safe) - len(safe_indices)

    stock_reclaim_info: Dict[str, object] = {"enabled": bool(args.stock_reclaim)}
    free_stock: List[int] = []
    if args.stock_reclaim:
        stock_dict = Dictionary(rom)
        free_stock = []
        for idx in reclaimable_slots(rom, stock_dict, exclude_script_abs=set()):
            if idx >= stock or not dict_token_safe_in_zstring(idx):
                continue
            # Second-pass gate: reclaimable_slots can race with nested closure;
            # refuse any slot that still has external consumers.
            if slot_rewrite_refuse_reason(locs_story, idx, require_free=True):
                continue
            free_stock.append(idx)
        free_stock.sort()

    if args.rank == "hybrid-bands":
        # Band budgets apply across ext + stock-reclaim assignment slots.
        total_capacity = ext_budget + len(free_stock)
        chosen_all, leftover, band_stats = select_hybrid_band_uniques(
            text_to_abs,
            capacity=total_capacity,
            early_budget=int(args.band_early),
            bank60_rest_budget=int(args.band_bank60_rest),
        )
        chosen = chosen_all[:ext_budget]
        stock_chosen = chosen_all[ext_budget:]
        leftover = leftover  # truly unassigned after ext+stock
        pool_stats.update(band_stats)
        pool_stats["unique_in_pool"] = len(text_to_abs)
        pool_stats["ext_budget"] = ext_budget
        pool_stats["stock_free"] = len(free_stock)
        pool_stats["total_capacity"] = total_capacity
    else:
        ranked = sorted(text_to_abs.items(), key=_rank_key)
        chosen = ranked[:ext_budget]
        leftover = ranked[ext_budget:]
        stock_chosen = []
        pool_stats["rank"] = args.rank
        pool_stats["unique_in_pool"] = len(ranked)

    pool_stats["unique_assigned"] = len(chosen)
    pool_stats["lines_covered_by_assign"] = sum(len(v) for _k, v in chosen)
    pool_stats["reserved_seed_indices"] = sorted(reserved_seed_indices)
    if chosen:
        pool_stats["chosen_min_abs"] = f"{min(min(v) for _k, v in chosen):06X}"
        pool_stats["chosen_max_min_abs"] = f"{max(min(v) for _k, v in chosen):06X}"

    slot_payload: Dict[int, bytes] = {}
    assignments: List[Tuple[str, int, List[int]]] = []
    for ko, abs_list in chosen:
        index = safe_indices[len(assignments)]
        slot_payload[index] = encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        assignments.append((ko, index, abs_list))

    if args.stock_reclaim:
        # hybrid-bands: pre-selected stock_chosen under the same budgets.
        # freq / early-abs: fill from leftover in rank order.
        stock_queue = stock_chosen if args.rank == "hybrid-bands" else leftover
        stock_payload: Dict[int, bytes] = {}
        stock_n = 0
        for ko, abs_list in stock_queue:
            if stock_n >= len(free_stock):
                break
            index = free_stock[stock_n]
            stock_payload[index] = encode_ko_text(
                ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
            )
            assignments.append((ko, index, abs_list))
            stock_n += 1
        if args.rank != "hybrid-bands":
            leftover = leftover[stock_n:]
        if stock_payload:
            spill_ptrs, spill_end = write_dictionary_slots_spill(rom, stock_payload)
            stock_reclaim_info.update(
                {
                    "free_available": len(free_stock),
                    "assigned": len(stock_payload),
                    "indices": sorted(stock_payload),
                    "spill_end": spill_end,
                    "lines": sum(len(a[2]) for a in assignments[-len(stock_payload) :]),
                }
            )
        else:
            stock_reclaim_info["free_available"] = len(free_stock)
            stock_reclaim_info["assigned"] = 0
        pool_stats["stock_reclaim"] = stock_reclaim_info
        pool_stats["unique_assigned"] = len(assignments)
        pool_stats["lines_covered_by_assign"] = sum(
            len(abs_list) for _ko, _idx, abs_list in assignments
        )

    sole_reclaim_info: Dict[str, object] = {"enabled": bool(args.sole_reclaim)}

    if meta.get("ext_in_expansion"):
        write_info = write_exp_slots(
            rom,
            slot_payload,
            ext_ptr_off=ext_ptr_off,
            stock_count=stock,
            slot_count=slot_count,
        )
    else:
        write_info = write_stock_ext_slots(
            rom,
            slot_payload,
            ext_ptr_off=ext_ptr_off,
            stock_count=stock,
            slot_count=slot_count,
        )
    write_info["stock_reclaim"] = stock_reclaim_info
    write_info["sole_reclaim"] = sole_reclaim_info

    # Refresh dictionary view after writes
    meta["slot_count"] = slot_count
    meta["stock_count"] = stock
    meta["ext_ptr_off"] = f"{ext_ptr_off:04X}"
    d2 = make_dictionary(rom, meta)

    patches = []
    fails = 0
    patched_abs: Set[int] = set()
    for ko, index, abs_list in assignments:
        token = token_from_dict_index(index)
        for abs_off in abs_list:
            try:
                # read_record_at expects logical abs (adds stock_base itself).
                original = read_record_at(rom, abs_off)
                prefix, body, _ = split_prefix_body(original)
                # Always retarget to the newly assigned index — slot payloads
                # were just rewritten, so "already matched" via an old index
                # would decode wrong if left untouched.
                new_payload = padded_token_payload(prefix, token, original)
            except Exception:
                fails += 1
                continue
            file_off = _file_abs(rom, abs_off)
            rom[file_off : file_off + len(original)] = new_payload
            patched_abs.add(abs_off)
            got = d2.expand(split_prefix_body(new_payload)[1], tbl)
            # Size-preserving pad uses ideographic spaces (0x01) after the token.
            ok = got.rstrip("\u3000") == ko or got == ko
            if not ok:
                fails += 1
            patches.append(
                {
                    "abs": f"{abs_off:06X}",
                    "dict_index": index,
                    "ko": ko,
                    "ok": ok,
                    "decode": got,
                }
            )

    # Sole-fit reclaim after ext/stock script patches so already-covered early
    # lines are skipped (decode match) and only leftover uniques consume slots.
    if args.sole_reclaim:
        from apply_sole_reclaim_early import apply_sole_reclaim  # noqa: WPS433

        sole_report = apply_sole_reclaim(
            rom,
            tbl,
            abs_to_line=abs_to_line,
            seed_abs=seed_abs,
            skip_abs=patched_abs,
            lo=int(args.sole_lo),
            hi=int(args.sole_hi),
            marker=marker,
            base_rom=base_rom,
            dry_run=False,
        )
        sole_reclaim_info = {
            "enabled": True,
            "ref_regions": sole_report.get("ref_regions"),
            "sole_fit_available": sole_report.get("sole_fit_available"),
            "early_uniques_needing_ko": sole_report.get("early_uniques_needing_ko"),
            "reject_stats": sole_report.get("reject_stats"),
            "assigned": sole_report.get("assigned"),
            "lines_patched": sole_report.get("lines_patched"),
            "indices": sole_report.get("indices"),
            "displaced": sole_report.get("displaced"),
            "seed_fail": sole_report.get("seed_fail"),
            "decode_fail": sole_report.get("decode_fail"),
            "spill_end": sole_report.get("spill_end"),
        }
        pool_stats["sole_reclaim"] = sole_reclaim_info
        write_info["sole_reclaim"] = sole_reclaim_info
        for row in sole_report.get("applied") or []:
            patches.append(row)
            try:
                patched_abs.add(int(row["abs"], 16))
            except Exception:
                pass
        fails += int(sole_report.get("decode_fail") or 0)
        # Refresh dict view after sole spill (stock 5F pointers moved).
        d2 = make_dictionary(rom, meta)

    # Repair accidental hits: pre-existing FE/FF high tokens that now land in
    # our extended range. Restore those records from the marked baseline ROM.
    # Never zero-pad — oversized false records must be skipped entirely.
    repaired = 0
    if args.base_rom.exists():
        base = base_rom
        idx_lo = stock
        idx_hi = stock + slot_count
        for abs_off, line in abs_to_line.items():
            if abs_off in patched_abs or abs_off in seed_abs:
                continue
            try:
                file_off = _file_abs(rom, abs_off)
                cur = read_encoded_z_safe(rom, file_off)
                if cur is None:
                    continue
                payload, _ = cur
                _prefix, body, _ = split_prefix_body(payload)
                if len(body) < 2 or not (0xF0 <= body[0] <= 0xFF):
                    continue
                idx = ((body[0] - 0xF0) << 8) | body[1]
                if not (idx_lo <= idx < idx_hi):
                    continue
                base_got = read_encoded_z_safe(base, abs_off)
                if base_got is None:
                    continue
                base_payload, _ = base_got
                if len(base_payload) != len(payload):
                    continue
                if len(payload) > MAX_SAFE_RECORD_LEN:
                    continue
                rom[file_off : file_off + len(payload)] = base_payload
                repaired += 1
            except Exception:
                continue
    report_repairs = repaired

    # Seed: fail only on regressions vs pre-patch decode.
    seed_fail = 0
    seed_mismatch_pre = 0
    for row in seed_rows:
        abs_off = int(row["abs"], 16)
        ko = normalize_ko_text(row["ko"])
        body = split_prefix_body(read_encoded_z(rom, _file_abs(rom, abs_off))[0])[1]
        got = d2.expand(body, tbl).rstrip("\u3000")
        exp = encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        exp2 = encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="each"
        )
        want = d2.expand(exp, tbl).rstrip("\u3000")
        want2 = d2.expand(exp2, tbl).rstrip("\u3000")
        ok_now = got == want or got == want2
        prev = seed_input_decode.get(abs_off)
        if prev is not None and got != prev and not ok_now:
            seed_fail += 1
        elif not ok_now:
            seed_mismatch_pre += 1

    match = 0
    for abs_off, line in abs_to_line.items():
        ko = normalize_ko_text(line.get("ko") or "")
        if not ko:
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        enc2 = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="each"
        )
        if enc is None and enc2 is None:
            continue
        try:
            got = d2.expand(
                split_prefix_body(read_encoded_z(rom, _file_abs(rom, abs_off))[0])[1],
                tbl,
            )
            if (enc and got == d2.expand(enc, tbl)) or (
                enc2 and got == d2.expand(enc2, tbl)
            ):
                match += 1
        except Exception:
            pass

    report = {
        "meta": meta,
        "write": write_info,
        "only_no_pointer": bool(args.only_no_pointer),
        "pool": pool_stats,
        "unique_assigned": len(assignments)
        + int(sole_reclaim_info.get("assigned") or 0),
        "lines_patched": len(patches),
        "skipped_event_abs": skipped_event_abs,
        "collisions_repaired": report_repairs,
        "decode_fail": fails,
        "seed_fail": seed_fail,
        "seed_mismatch_preexisting": seed_mismatch_pre,
        "matching_old_abs": match,
        "sole_reclaim": sole_reclaim_info,
        "sample": patches[:20],
        "assigned_texts": [
            {"ko": ko, "dict_index": idx, "abs_count": len(abs_list)}
            for ko, idx, abs_list in assignments[:30]
        ],
        "checksum": f"{update_ws_checksum(rom):04X}",
    }

    args.out_rom.write_bytes(rom)
    args.meta.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    mode = "seq" if args.only_no_pointer else "all"
    print(
        f"Ext dict OK ({mode}) | slots={slot_count} unique={len(assignments)} "
        f"lines={len(patches)} skipped_event={skipped_event_abs} "
        f"repaired={report_repairs} fail={fails} "
        f"seed_fail={seed_fail} seed_pre={seed_mismatch_pre} "
        f"matching={match} checksum={report['checksum']}"
    )
    print(f"Wrote {args.out_rom}")
    print(f"Wrote {args.out_report}")
    # Seed regression is fatal; a few size-preserve decode mismatches are not
    # (shared KO text on non-dialogue bodies, pad refusal, etc.).
    if seed_fail:
        sys.exit(1)
    if fails and not patches:
        sys.exit(1)


if __name__ == "__main__":
    main()
