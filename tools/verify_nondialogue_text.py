#!/usr/bin/env python3
"""
Non-dialogue text path gate — intermission menus, battle HUD, help (req 1.6/2.6).

READ-ONLY. This tool never opens a .wsc for writing.

Scope: the aux zstring banks ``50–5F`` + ``76`` (``expand_dictionary.AUX_TOKEN_BANKS``)
and the name75 tables (``expand_dictionary.NAME75_RANGES``) — the record set the
intermission menu, the battle HUD and the help screens read from. Records are
enumerated on the ORIGINAL 8 MiB ROM, so the original defines the record set and
the target cannot hide a record by breaking its terminator.

Three checks:

(i)   dictionary expansion identity — every record is expanded with the ORIGINAL
      dictionary and with the TARGET dictionary. A difference means a shared
      dictionary index moved under a non-dialogue consumer (hypothesis A3).
      Both variants are reported: ``dict_only`` keeps the original record bytes
      and swaps only the dictionary (pure index contamination), ``rendered``
      compares original bytes + original dict against target bytes + target dict
      (what the game actually paints).
(ii)  marker / ext3 misconsumption — records that already contain the Hangul
      marker pair ``E3DB`` or the ext3 magic ``E5 18`` in the ORIGINAL. The site
      list is measured by scanning the original, never trusted from the design
      doc; a disagreement with the documented snapshot is reported explicitly.
      Failure = the target walks such a pair as a lead-aligned 2-byte unit, i.e.
      the shared render hook would consume it as a marker / ext3 portal.
(iii) record length and zstring terminator preservation vs the original.

Comparison uses code sequences (``as_codes=True``), not TBL text, so a different
TBL between ROMs cannot mask or fake a difference.

Report: ``out/patch/nondialogue_text_report.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from diff_stock_3way import (  # noqa: E402
    INTERMISSION_TILE_BYTES,
    INTERMISSION_TILES,
    UI_APPROVED,
)
from expand_dictionary import AUX_TOKEN_BANKS, NAME75_RANGES, _walk_zstring_range  # noqa: E402
from hangul_marker import marker_pair  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    SEG_DICT,
    Dictionary,
    dict_index_from_ext3_token,
    dict_index_from_token,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)
from verify_all_stages_smoke import make_smoke_dictionary  # noqa: E402
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/nondialogue_text_report.json"

AUX_MAX_LEN = 128
NAME75_MAX_LEN = 64

# Bank 5F is dictionary storage, not a UI record region: diff_stock_3way
# classifies all of it as dict_string / dict_pointer_table / dict_spill. Walking
# it as zstrings reads pointer-table bytes as text, so a restored pointer shows
# up as a bogus "UI text changed" hit. The dictionary's effect on non-dialogue
# text is already covered by check (i), which expands every consumer record in
# 50-5E / 76 / name75 with the original vs the target dictionary — that is the
# index-level check, and it is the one that matters. Excluded from record
# enumeration only; 5F stays in the reference regions used for consumer lookup.
RECORD_SCAN_EXCLUDED_BANKS = (SEG_DICT,)

MARKER_PAIR = tuple(marker_pair())  # installed marker, from hangul_char_map.json
EXT3_PAIR = (0xE5, 0x18)     # patch_3byte_dict_token MAGIC E518

# Documented snapshot (bugfix.md §Hypothesis A2 / task 3.3). Verified by scan;
# the scan wins if they disagree.
DOC_MARKER_SITES = (
    "51:27C5",
    "59:6E80",
    "59:7122",
    "5C:486F",
    "5C:5746",
    "5C:594B",
    "5C:73C5",
    "5C:751B",
    "5C:7856",
    "5D:12AB",
)
DOC_EXT3_SITES: Tuple[str, ...] = ()

# Approved differences (3.11): the Hangul UI sites are deliberately not original.
#
# The intermission label tiles also live inside the aux scan scope: bank 54 is in
# the 50-5F range this gate walks as zstrings, but the label characters there are
# 4bpp graphics. Walking graphics as text synthesises records that do not exist, so
# editing a tile trips checks (i) and (iii) with sites like ``54:4401`` whose
# "original" decode is meaningless (``[75][2C][11][1F][11]``).
#
# Only the tiles that were *measured* to be on screen are approved. The list is
# published by tools/patch_intermission_labels_ko.py after validating each 16x16
# cell against a native intermission capture, so this stays an evidence-backed
# allowlist rather than a blanket exemption for bank 54.
UI_STRING_APPROVED_RANGES: Tuple[Tuple[int, int], ...] = tuple(
    (site, site + ln) for site, ln in sorted(UI_APPROVED.items())
)
#: Graphics cells only. These are the ranges where "the record moved" is
#: meaningless because there is no record: walking 4bpp tiles as zstrings
#: synthesises boundaries that do not exist. Length/terminator (check iii) is
#: waived for these and for nothing else — see GRAPHICS_ONLY_LENGTH_WAIVER.
GRAPHICS_APPROVED_RANGES: Tuple[Tuple[int, int], ...] = tuple(
    (tile, tile + INTERMISSION_TILE_BYTES) for tile in sorted(INTERMISSION_TILES)
)
APPROVED_RANGES: Tuple[Tuple[int, int], ...] = (
    UI_STRING_APPROVED_RANGES + GRAPHICS_APPROVED_RANGES
)

# Why check (iii) must not honour UI_APPROVED:
#
# tools/apply_ui_inplace.py used to write ``KO + 00 + 00...`` when the Korean was
# shorter than the Japanese, which pulls the terminator forward and turns the
# surplus bytes into phantom empty records. Bank 75 holds a back-to-back
# NUL-terminated UI label table at 0x75B690+, so on the tip three phantoms at
# 75B6AD / 75B7CC / 75B7D4 turned 48 records into 51 and every entry after the
# first shifted — including the single-code icon records ``75B6C8`` and
# ``75B6CB`` and labels like ``75B716`` 'ＭＡＰ<E62F>ＳＥＬＥＣＴ'. The unit
# strengthen screen then drew its neighbour's glyph. UI_APPROVED waived exactly
# those sites here, and the region was not even enumerated, so the gate reported
# 0 violations. Length and terminator are structural: never waive them for a
# real record.
GRAPHICS_ONLY_LENGTH_WAIVER = True

#: Bank-75 UI label table (system/battle/title strings), below NAME75_RANGES.
#: Enumerated for check (iii) only: it is a sequentially walked zstring table, so
#: a shortened record shifts every following entry.
UI_TABLE_RANGES: Tuple[Tuple[int, int], ...] = ((0x75B000, 0x75C000),)

# Deliberately rewritten name75 records (tools/apply_name75_ko.py). Those records
# ARE the localization — the unit/weapon table entry is replaced by an ext3 token
# — so check (i) must see their bytes change. Read from the apply report, never
# hardcoded, so the approval is exactly what the writer wrote. Length/terminator
# preservation is NOT waived: check (iii) still covers these records, and it is
# the check that catches the real hazard here (a NUL pad would shorten a record
# and shift every following entry in the sequentially walked table).
RECORD_REWRITE_REPORTS: Tuple[str, ...] = (
    "name75_ko_report.json",  # bank-75 unit/weapon display table
    "aux_ko_report.json",     # bank 59/5C/5D/5E battle text, skills, mission lines
    # mixed Korean/Japanese residual localization: the same in-place ext3 record
    # rewrite, driven by the reviewed residual catalog. Same policy as above —
    # the approval is exactly the rows the writer reports as applied, only when
    # the run says it succeeded, and check (iii) still enforces length and
    # terminator preservation for every one of them.
    "mixed_residual_localization_report.json",
    # Reviewed bank-5C MS encyclopedia localization.  The candidate rewrites
    # only the exact Original-derived record bodies listed in its successful
    # build report; check (iii) continues to enforce length and terminators.
    "encyclopedia_ms_batch01_report.json",
    # Widened rear bank-5C MS encyclopedia batch. The same successful-report
    # and exact applied-record policy applies; structural checks remain active.
    "encyclopedia_ms_batch02_report.json",
    # Runtime-safe character encyclopedia batch using only the already
    # accepted E5 18 portal; the rejected E5 2F candidate is never approved.
    "encyclopedia_character_safe_batch01_report.json",
)


def load_name75_rewrites(report_dir: Path) -> Tuple[Tuple[int, int], ...]:
    """Ranges of records deliberately rewritten in place, from the apply reports.

    Only honoured when a report says the run succeeded, so a failed or aborted
    apply never widens the approval.
    """
    out = []
    for name in RECORD_REWRITE_REPORTS:
        path = report_dir / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not data.get("ok"):
            continue
        for row in data.get("applied") or []:
            try:
                site = int(row["abs"], 16)
                ln = int(row["payload_len"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append((site, site + ln))
    return tuple(sorted(out))

MAX_LISTED = 200

# Dictionary slots the UI localization pipeline deliberately rewrites. Deliberate
# is not the same as invisible: rewriting a shared slot IS the mechanism that
# Koreanizes the intermission menu, the HUD and the unit tables, so check (i)
# must see those records change. What it must still catch is any OTHER index
# drifting under a non-dialogue consumer.
#
# The allowlist is read from the apply reports rather than hardcoded, so it is
# always the set the writers actually wrote — the same evidence-backed policy as
# INTERMISSION_TILES. A record is approved only when substituting the original
# payload back into the allowlisted indices reproduces the original expansion
# exactly, which proves nothing outside the curated set moved.
UI_LOCALIZE_REPORTS: Tuple[str, ...] = (
    "unit_names_report.json",
    "weapon_table_report.json",
    "ui_system_report.json",
    "ui_battle_terms_report.json",
    "ui_menu_terms_report.json",
    "ui_menu_terms2_report.json",
    "ui_menu_terms3_report.json",
    "ui_mined_terms_report.json",
    "ui_proper_nouns_report.json",
)


def load_localized_indices(report_dir: Path) -> Dict[int, str]:
    """Index → provenance, from the UI apply reports in ``report_dir``."""
    out: Dict[int, str] = {}
    for name in UI_LOCALIZE_REPORTS:
        path = report_dir / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = list(data.get("applied") or []) + list(data.get("slots_claimed") or [])
        for row in rows:
            raw = row.get("index")
            if raw is None:
                continue
            try:
                idx = int(raw, 16) if isinstance(raw, str) else int(raw)
            except (TypeError, ValueError):
                continue
            out[idx] = f"{name}:{row.get('jp', '')}→{row.get('ko', '')}"
    return out


class _HybridDictionary(Dictionary):
    """Target dictionary, except the listed indices keep their ORIGINAL payload.

    ``Dictionary.expand`` recurses through ``raw_entry``, so overriding it also
    substitutes allowlisted slots nested inside other phrases. Without that, a
    localized slot referenced from within a parent phrase would make the parent
    look like unexplained drift.
    """

    def __init__(self, target: Dictionary, original_payloads: Dict[int, bytes]):
        self.__dict__.update(target.__dict__)
        self._original_payloads = original_payloads

    def raw_entry(self, index: int, max_len: int = 256) -> bytes:
        hit = self._original_payloads.get(index)
        if hit is not None:
            return hit
        return Dictionary.raw_entry(self, index, max_len)


def build_hybrid(
    target: Dictionary, jp_dict: Dictionary, localized: Dict[int, str]
) -> Dictionary | None:
    if not localized:
        return None
    payloads: Dict[int, bytes] = {}
    for idx in localized:
        if 0 <= idx < jp_dict.count:
            try:
                payloads[idx] = jp_dict.raw_entry(idx)
            except Exception:
                continue
    return _HybridDictionary(target, payloads) if payloads else None


def _site(logical: int) -> str:
    return f"{logical >> 16:02X}:{logical & 0xFFFF:04X}"


def _overlaps(
    logical: int, length: int, ranges: Sequence[Tuple[int, int]]
) -> bool:
    # +1 covers the terminator byte, which belongs to the record: a record whose
    # payload ends immediately before an approved range still "moves" when the
    # first byte of that range stops being 0x00.
    lo, hi = logical, logical + max(length, 1) + 1
    for a_lo, a_hi in ranges:
        if lo < a_hi and a_lo < hi:
            return True
    return False


def approved(logical: int, length: int, extra: Sequence[Tuple[int, int]] = ()) -> bool:
    return _overlaps(logical, length, tuple(APPROVED_RANGES) + tuple(extra))


def approved_length_waiver(logical: int, length: int) -> bool:
    """Length/terminator waiver — graphics cells only, never real records."""
    return _overlaps(logical, length, GRAPHICS_APPROVED_RANGES)


# --- record enumeration on the original -------------------------------------


def scan_regions() -> List[Tuple[int, int, str, int]]:
    """(logical_lo, logical_hi, region, max_len) of the non-dialogue text set."""
    regions = [
        (seg * BANK_SIZE, (seg + 1) * BANK_SIZE, "aux", AUX_MAX_LEN)
        for seg in AUX_TOKEN_BANKS
        if seg not in RECORD_SCAN_EXCLUDED_BANKS
    ]
    regions += [(lo, hi, "name75", NAME75_MAX_LEN) for lo, hi in NAME75_RANGES]
    return regions


def walk_regions() -> List[Tuple[int, int, str, int]]:
    """scan_regions() plus the bank-75 UI label table (check (iii) scope)."""
    return scan_regions() + [
        (lo, hi, "name75_ui", NAME75_MAX_LEN) for lo, hi in UI_TABLE_RANGES
    ]


def enumerate_records(
    jp: bytes, regions: Sequence[Tuple[int, int, str, int]] | None = None
) -> List[dict]:
    out: List[dict] = []
    for lo, hi, region, max_len in regions if regions is not None else scan_regions():
        for logical, payload, _kind in _walk_zstring_range(
            jp, lo, hi, region=region, max_len=max_len
        ):
            out.append(
                {
                    "logical": logical,
                    "region": region,
                    "max_len": max_len,
                    "payload": payload,
                }
            )
    return out


# --- token walking / expansion ----------------------------------------------


def aligned_units(payload: bytes) -> Iterable[Tuple[int, int, int]]:
    """Yield (offset, code, size) exactly as the game's text walker consumes."""
    i = 0
    n = len(payload)
    while i < n:
        lead = payload[i]
        if lead == 0:
            return
        if lead >= 0xE0 and i + 1 < n:
            trail = payload[i + 1]
            if is_ext3_magic(lead, trail) and i + 3 < n:
                yield i, (lead << 8) | trail, 4
                i += 4
                continue
            yield i, (lead << 8) | trail, 2
            i += 2
            continue
        yield i, lead, 1
        i += 1


def memo_expand(dic: Dictionary, index: int, memo: Dict[int, str]) -> str:
    hit = memo.get(index)
    if hit is not None:
        return hit
    try:
        text = dic.expand(dic.raw_entry(index), None, as_codes=True)
    except Exception:
        text = f"<BADDICT:{index:04X}>"
    memo[index] = text
    return text


def code_string(dic: Dictionary, payload: bytes, memo: Dict[int, str]) -> str:
    """Expand a record into a code sequence (TBL-independent)."""
    parts: List[str] = []
    i = 0
    n = len(payload)
    while i < n:
        b = payload[i]
        if b == 0:
            break
        if is_dict_token(b) and i + 1 < n:
            parts.append(memo_expand(dic, dict_index_from_token(b, payload[i + 1]), memo))
            i += 2
            continue
        if is_kanji_lead(b) and i + 1 < n:
            if is_ext3_magic(b, payload[i + 1]) and i + 3 < n:
                idx = dict_index_from_ext3_token(
                    b, payload[i + 1], payload[i + 2], payload[i + 3]
                )
                parts.append(memo_expand(dic, idx, memo))
                i += 4
                continue
            parts.append(f"[{(b << 8) | payload[i + 1]:04X}]")
            i += 2
            continue
        parts.append(f"[{b:02X}]")
        i += 1
    return "".join(parts)


def unresolvable(text: str) -> bool:
    return "<BADDICT:" in text or "<TRUNC:" in text


def diff_window(a: str, b: str, width: int = 48) -> dict:
    """First differing character plus a window on both sides (counterexample)."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    lo = max(0, i - 8)
    return {
        "first_diff_at": i,
        "orig_window": a[lo : lo + width],
        "target_window": b[lo : lo + width],
    }


# --- check (i) --------------------------------------------------------------


def check_dict_expansion(
    jp: bytes,
    tgt: bytes,
    records: Sequence[dict],
    localized: Dict[int, str] | None = None,
    name75_rewrites: Sequence[Tuple[int, int]] = (),
    detachment_baseline: bytes | None = None,
    detachment_records: set[int] | None = None,
    detachment_target_records: set[int] | None = None,
) -> dict:
    jp_dict = Dictionary(jp)
    tgt_dict = make_smoke_dictionary(tgt)
    localized = localized or {}
    hybrid = build_hybrid(tgt_dict, jp_dict, localized)
    memo_jp: Dict[int, str] = {}
    memo_tgt: Dict[int, str] = {}
    memo_hybrid: Dict[int, str] = {}
    detachment_records = detachment_records or set()
    detachment_target_records = detachment_target_records or set()
    detachment_dict = (
        make_smoke_dictionary(detachment_baseline)
        if detachment_baseline is not None
        else None
    )
    memo_detachment: Dict[int, str] = {}
    sj, st = stock_base(jp), stock_base(tgt)
    sd = stock_base(detachment_baseline) if detachment_baseline is not None else 0

    compared = 0
    skipped = 0
    with_tokens = 0
    dict_only: List[dict] = []
    rendered: List[dict] = []
    approved_hits = 0
    ui_explained_dict_only = 0
    ui_explained_rendered = 0
    detachment_explained_dict_only = 0
    detachment_explained_rendered = 0
    detachment_target_approved_dict_only = 0
    detachment_target_approved_rendered = 0
    detachment_cache: Dict[tuple[int, int], bool] = {}

    def explained_by_ui(payload: bytes, base_text: str) -> bool:
        """True when restoring the curated slots reproduces the original exactly."""
        if hybrid is None:
            return False
        return code_string(hybrid, payload, memo_hybrid) == base_text

    def explained_by_detachment(logical: int, max_len: int) -> bool:
        """Independently compare the approved parent record with the candidate."""
        key = (logical, max_len)
        if key in detachment_cache:
            return detachment_cache[key]
        if (
            detachment_dict is None
            or detachment_baseline is None
            or logical not in detachment_records
        ):
            detachment_cache[key] = False
            return False
        baseline_got = read_encoded_z_safe(
            detachment_baseline, sd + logical, max_len=max_len
        )
        target_got = read_encoded_z_safe(tgt, st + logical, max_len=max_len)
        if baseline_got is None or target_got is None:
            detachment_cache[key] = False
            return False
        baseline_text = code_string(
            detachment_dict, bytes(baseline_got[0]), memo_detachment
        )
        target_text = code_string(tgt_dict, bytes(target_got[0]), memo_tgt)
        detachment_cache[key] = target_text == baseline_text
        return detachment_cache[key]

    for rec in records:
        payload = rec["payload"]
        if not any(is_dict_token(b) for b in payload) and not any(
            is_ext3_magic(payload[i], payload[i + 1]) for i in range(len(payload) - 1)
        ):
            continue
        with_tokens += 1
        logical = rec["logical"]
        base_text = code_string(jp_dict, payload, memo_jp)
        if unresolvable(base_text):
            # Not a genuine original consumer of a real dictionary slot (FF/E5
            # bytes used as data). Out of scope, counted so the scope is visible.
            skipped += 1
            continue
        compared += 1
        swapped = code_string(tgt_dict, payload, memo_tgt)
        if swapped != base_text:
            if logical in detachment_target_records:
                detachment_target_approved_dict_only += 1
            elif approved(logical, len(payload), name75_rewrites):
                approved_hits += 1
            elif explained_by_ui(payload, base_text):
                ui_explained_dict_only += 1
            elif explained_by_detachment(logical, rec["max_len"]):
                detachment_explained_dict_only += 1
            else:
                dict_only.append(
                    {
                        "site": _site(logical),
                        "region": rec["region"],
                        "orig_codes": base_text[:160],
                        "target_codes": swapped[:160],
                        **diff_window(base_text, swapped),
                    }
                )
        got = read_encoded_z_safe(tgt, st + logical, max_len=rec["max_len"])
        tgt_payload = got[0] if got else None
        if tgt_payload is None:
            rendered.append(
                {
                    "site": _site(logical),
                    "region": rec["region"],
                    "orig_codes": base_text[:160],
                    "target_codes": None,
                    "reason": "no zstring terminator within max_len in target",
                }
            )
            continue
        tgt_text = code_string(tgt_dict, tgt_payload, memo_tgt)
        if tgt_text != base_text and not approved(
            logical, len(payload), name75_rewrites
        ):
            if logical in detachment_target_records:
                detachment_target_approved_rendered += 1
            elif explained_by_ui(tgt_payload, base_text):
                ui_explained_rendered += 1
            elif explained_by_detachment(logical, rec["max_len"]):
                detachment_explained_rendered += 1
            else:
                rendered.append(
                    {
                        "site": _site(logical),
                        "region": rec["region"],
                        "orig_codes": base_text[:160],
                        "target_codes": tgt_text[:160],
                        **diff_window(base_text, tgt_text),
                    }
                )

    return {
        "ok": not dict_only and not rendered,
        "records_with_tokens": with_tokens,
        "records_compared": compared,
        "records_skipped_unresolvable_in_original": skipped,
        "approved_differences": approved_hits,
        "ui_localized_indices": len(localized),
        "name75_rewrite_ranges": len(name75_rewrites),
        "ui_explained_dict_only": ui_explained_dict_only,
        "ui_explained_rendered": ui_explained_rendered,
        "detachment_approved_records": len(detachment_records),
        "detachment_explained_dict_only": detachment_explained_dict_only,
        "detachment_explained_rendered": detachment_explained_rendered,
        "detachment_target_records": len(detachment_target_records),
        "detachment_target_approved_dict_only": detachment_target_approved_dict_only,
        "detachment_target_approved_rendered": detachment_target_approved_rendered,
        "dict_only_mismatches": len(dict_only),
        "rendered_mismatches": len(rendered),
        "dict_only_sample": dict_only[:MAX_LISTED],
        "rendered_sample": rendered[:MAX_LISTED],
        "note": "dict_only keeps the original record bytes and swaps only the "
        "dictionary; rendered compares original bytes+dict against target "
        "bytes+dict. Records whose original expansion is already unresolvable "
        "(FF/E5 data bytes, not real slots) are out of scope and counted. "
        "ui_explained_* are records whose entire difference disappears when the "
        "curated UI-localized slots are substituted back to their original "
        "payload — intentional localization, not drift.",
    }


# --- check (ii) -------------------------------------------------------------


def scan_pair(rom: bytes, pair: Tuple[int, int]) -> List[int]:
    """Every occurrence of a byte pair in the scanned regions (measured, not assumed)."""
    sb = stock_base(rom)
    needle = bytes(pair)
    hits: List[int] = []
    for lo, hi, _region, _ml in scan_regions():
        start, end = sb + lo, sb + hi
        i = rom.find(needle, start, end)
        while i >= 0:
            hits.append(i - sb)
            i = rom.find(needle, i + 1, end)
    return sorted(set(hits))


def containing_record(records: Sequence[dict], logical: int) -> dict | None:
    for rec in records:
        start = rec["logical"]
        if start <= logical < start + len(rec["payload"]) + 1:
            return rec
    return None


def check_marker_records(
    jp: bytes, tgt: bytes, records: Sequence[dict]
) -> dict:
    st = stock_base(tgt)
    by_start = {rec["logical"]: rec for rec in records}
    starts = sorted(by_start)

    def record_for(logical: int) -> dict | None:
        # records are non-overlapping and sorted
        lo, hi = 0, len(starts) - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if starts[mid] <= logical:
                best = starts[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None:
            return None
        rec = by_start[best]
        return rec if logical < best + len(rec["payload"]) + 1 else None

    results: List[dict] = []
    failures: List[dict] = []
    found: Dict[str, List[str]] = {"E3DB": [], "E518": []}

    for name, pair in (("E3DB", MARKER_PAIR), ("E518", EXT3_PAIR)):
        for logical in scan_pair(jp, pair):
            found[name].append(_site(logical))
            rec = record_for(logical)
            entry = {
                "pair": name,
                "site": _site(logical),
                "record": _site(rec["logical"]) if rec else None,
                "region": rec["region"] if rec else None,
            }
            if rec is None:
                entry["status"] = "not inside a walked record (padding/data)"
                entry["interpreted"] = False
                results.append(entry)
                continue
            got = read_encoded_z_safe(tgt, st + rec["logical"], max_len=rec["max_len"])
            if not got:
                entry["status"] = "target record has no terminator within max_len"
                entry["interpreted"] = True
                entry["reason"] = "cannot prove the pair is not consumed"
                results.append(entry)
                failures.append(entry)
                continue
            tgt_payload = got[0]
            off = logical - rec["logical"]
            aligned = any(
                o == off and code == ((pair[0] << 8) | pair[1])
                for o, code, _size in aligned_units(tgt_payload)
            )
            entry["offset_in_record"] = off
            entry["lead_aligned_in_target"] = aligned
            entry["interpreted"] = aligned
            entry["status"] = (
                "target walks this pair as a 2-byte unit → the shared hook "
                f"consumes it as {'marker' if name == 'E3DB' else 'ext3 portal'}"
                if aligned
                else "not lead-aligned in the target walk"
            )
            results.append(entry)
            if aligned:
                failures.append(entry)

    doc = {
        "E3DB": list(DOC_MARKER_SITES),
        "E518": list(DOC_EXT3_SITES),
    }
    discrepancy = {
        name: {
            "documented": doc[name],
            "scanned": found[name],
            "only_in_doc": sorted(set(doc[name]) - set(found[name])),
            "only_in_scan": sorted(set(found[name]) - set(doc[name])),
        }
        for name in ("E3DB", "E518")
        if set(doc[name]) != set(found[name])
    }

    return {
        "ok": not failures,
        "scan_scope": "aux banks 50-5F + 76 and name75 ranges, original ROM",
        "counts": {
            "E3DB": len(found["E3DB"]),
            "E518": len(found["E518"]),
            "documented_E3DB": len(DOC_MARKER_SITES),
            "documented_E518": len(DOC_EXT3_SITES),
        },
        "scanned_sites": found,
        "snapshot_discrepancy": discrepancy or None,
        "misconsumed": len(failures),
        "sites": results,
        "note": "counts come from scanning the original ROM, not from the design "
        "document; snapshot_discrepancy is non-null only if they disagree.",
    }


# --- check (iii) ------------------------------------------------------------


def check_lengths(jp: bytes, tgt: bytes, records: Sequence[dict]) -> dict:
    sj, st = stock_base(jp), stock_base(tgt)
    bad: List[dict] = []
    approved_hits = 0
    for rec in records:
        logical = rec["logical"]
        payload = rec["payload"]
        jp_len = len(payload)
        jp_term = logical + jp_len
        got = read_encoded_z_safe(tgt, st + logical, max_len=rec["max_len"])
        if not got:
            entry = {
                "site": _site(logical),
                "region": rec["region"],
                "orig_len": jp_len,
                "orig_terminator": _site(jp_term),
                "target_len": None,
                "target_terminator": None,
                "reason": "no 00 terminator within max_len in target",
            }
        else:
            t_payload, t_term_file = got
            t_term = t_term_file - st
            if len(t_payload) == jp_len and t_term == jp_term:
                continue
            entry = {
                "site": _site(logical),
                "region": rec["region"],
                "orig_len": jp_len,
                "orig_terminator": _site(jp_term),
                "target_len": len(t_payload),
                "target_terminator": _site(t_term),
                "reason": "record length / terminator moved",
            }
        if approved_length_waiver(logical, jp_len):
            approved_hits += 1
            continue
        bad.append(entry)
    return {
        "ok": not bad,
        "records_checked": len(records),
        "approved_differences": approved_hits,
        "violations": len(bad),
        "sample": bad[:MAX_LISTED],
    }


# --- candidate-bound detachment context ------------------------------------


def load_detachment_context(
    report_path: Path | None,
    *,
    target_path: Path,
    baseline_path: Path | None,
) -> tuple[set[int], dict | None]:
    if report_path is None:
        return set(), None
    if baseline_path is None:
        raise SystemExit("--baseline is required with --approved-detachment-report")
    indices, candidate_sha, ranges = load_approved_detachment(report_path)
    actual_candidate_sha = hashlib.sha256(target_path.read_bytes()).hexdigest()
    if candidate_sha != actual_candidate_sha:
        raise SystemExit(
            f"detachment approval is bound to {candidate_sha}, "
            f"but {target_path} is {actual_candidate_sha}"
        )
    document = json.loads(report_path.read_text(encoding="utf-8"))
    parent = document.get("parent_rom") or {}
    parent_sha = parent.get("sha256")
    actual_parent_sha = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    if parent_sha != actual_parent_sha:
        raise SystemExit(
            f"detachment approval parent is {parent_sha}, "
            f"but {baseline_path} is {actual_parent_sha}"
        )
    writes = ((document.get("duplicate") or {}).get("detachment_writes") or [])
    records: set[int] = set()
    nested_records: set[int] = set()
    range_pairs = {(lo, hi) for lo, hi, _owner in ranges}
    for row in writes:
        try:
            record_abs = int(str(row["record_abs"]), 16)
            token_abs = int(str(row["token_abs"]), 16)
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(
                f"invalid detachment write in {report_path}: {row!r}"
            ) from exc
        if (token_abs, token_abs + 2) not in range_pairs:
            raise SystemExit(
                f"detachment write {token_abs:06X} lacks an approved range"
            )
        if str(row.get("kind") or "") == "nested_dictionary":
            nested_records.add(record_abs)
        else:
            records.add(record_abs)
    impact_rows = ((document.get("duplicate") or {}).get("nested_impact_records") or [])
    impact_records: set[int] = set()
    for row in impact_rows:
        try:
            record_abs = int(str(row["record_abs"]), 16)
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(
                f"invalid nested impact record in {report_path}: {row!r}"
            ) from exc
        impact_records.add(record_abs)
        records.add(record_abs)
    retired = document.get("retired_slot_reclaim") or {}
    retired_records: set[int] = set()
    for slot_row in retired.get("selected_slots") or []:
        for occurrence in slot_row.get("historical_external_occurrences") or []:
            try:
                record_abs = int(str(occurrence["record_abs"]), 16)
            except (KeyError, TypeError, ValueError) as exc:
                raise SystemExit(
                    f"invalid retired-slot historical occurrence in {report_path}: "
                    f"{occurrence!r}"
                ) from exc
            retired_records.add(record_abs)
            records.add(record_abs)
    target_records: set[int] = set()
    for row in retired.get("stage_target_records") or []:
        try:
            record_abs = int(str(row["abs"]), 16)
            logical_start = int(str(row["logical_start"]), 16)
            logical_end = int(str(row["logical_end_exclusive"]), 16)
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(
                f"invalid retired-slot stage target in {report_path}: {row!r}"
            ) from exc
        if (logical_start, logical_end) not in range_pairs:
            raise SystemExit(
                f"retired-slot stage target lacks approved range: {row!r}"
            )
        target_records.add(record_abs)
    if not records and not nested_records and not target_records:
        raise SystemExit(f"detachment approval has no former consumer records: {report_path}")
    return records, {
        "path": str(report_path),
        "candidate_sha256": candidate_sha,
        "parent_sha256": parent_sha,
        "approved_stock_indices": [f"{index:04X}" for index in sorted(indices)],
        "approved_ranges": [
            {
                "logical_start": f"{lo:06X}",
                "logical_end_exclusive": f"{hi:06X}",
                "owner_id": owner,
            }
            for lo, hi, owner in ranges
        ],
        "former_consumer_records": [f"{value:06X}" for value in sorted(records)],
        "nested_dictionary_records": [
            f"{value:06X}" for value in sorted(nested_records)
        ],
        "nested_impact_records": [
            f"{value:06X}" for value in sorted(impact_records)
        ],
        "retired_slot_historical_records": [
            f"{value:06X}" for value in sorted(retired_records)
        ],
        "retired_slot_stage_target_records": [
            f"{value:06X}" for value in sorted(target_records)
        ],
    }


def check_nested_detachment_render(
    baseline: bytes | None,
    target: bytes,
    context: dict | None,
) -> dict:
    rows = [] if context is None else list(context.get("nested_dictionary_records") or [])
    if not rows:
        return {
            "ok": True,
            "records_checked": 0,
            "render_mismatches": 0,
            "length_or_terminator_mismatches": 0,
            "sample": [],
        }
    if baseline is None:
        raise SystemExit("nested detachment verification requires the parent baseline")
    baseline_dict = make_smoke_dictionary(baseline)
    target_dict = make_smoke_dictionary(target)
    baseline_sb = stock_base(baseline)
    target_sb = stock_base(target)
    failures: list[dict] = []
    render_mismatches = 0
    shape_mismatches = 0
    for raw in rows:
        logical = int(str(raw), 16)
        before = read_encoded_z_safe(baseline, baseline_sb + logical, max_len=256)
        after = read_encoded_z_safe(target, target_sb + logical, max_len=256)
        if before is None or after is None:
            shape_mismatches += 1
            failures.append({"logical": f"{logical:06X}", "reason": "unreadable"})
            continue
        before_payload, before_term = before
        after_payload, after_term = after
        before_render = baseline_dict.expand(bytes(before_payload), as_codes=True)
        after_render = target_dict.expand(bytes(after_payload), as_codes=True)
        render_equal = before_render == after_render
        shape_equal = (
            len(before_payload) == len(after_payload)
            and before_term - baseline_sb == after_term - target_sb
        )
        if not render_equal:
            render_mismatches += 1
        if not shape_equal:
            shape_mismatches += 1
        if not render_equal or not shape_equal:
            failures.append(
                {
                    "logical": f"{logical:06X}",
                    "before_payload_hex": bytes(before_payload).hex().upper(),
                    "after_payload_hex": bytes(after_payload).hex().upper(),
                    "before_render": before_render,
                    "after_render": after_render,
                    "render_equal": render_equal,
                    "shape_equal": shape_equal,
                }
            )
    return {
        "ok": not failures,
        "records_checked": len(rows),
        "render_mismatches": render_mismatches,
        "length_or_terminator_mismatches": shape_mismatches,
        "sample": failures[:MAX_LISTED],
    }


# --- main -------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="accepted parent candidate used only to independently verify "
        "candidate-bound duplicate-detachment records",
    )
    ap.add_argument(
        "--approved-detachment-report",
        type=Path,
        default=None,
        help="candidate-bound duplicate-detachment proof; requires --baseline",
    )
    ap.add_argument(
        "--ui-report-dir",
        type=Path,
        default=ROOT / "out/patch",
        help="where the UI apply reports live; their applied indices are the "
        "intentional-localization allowlist for check (i)",
    )
    ap.add_argument(
        "--no-ui-allowlist",
        action="store_true",
        help="require byte-for-byte dictionary identity, i.e. treat intentional "
        "UI localization as a failure too (pre-UI-pass baseline behaviour)",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this gate is read-only")
    for p in (args.jp, args.target):
        if not p.exists():
            raise SystemExit(f"missing input ROM: {p}")
    if args.baseline is not None and not args.baseline.exists():
        raise SystemExit(f"missing baseline ROM: {args.baseline}")

    detachment_records, detachment_context = load_detachment_context(
        args.approved_detachment_report,
        target_path=args.target,
        baseline_path=args.baseline,
    )
    jp = bytes(load_rom(args.jp))
    tgt = bytes(load_rom(args.target))
    detachment_baseline = (
        bytes(load_rom(args.baseline)) if args.baseline is not None else None
    )
    detachment_target_records = {
        int(str(value), 16)
        for value in (
            []
            if detachment_context is None
            else detachment_context.get("retired_slot_stage_target_records") or []
        )
    }

    records = enumerate_records(jp)
    localized = (
        {} if args.no_ui_allowlist else load_localized_indices(args.ui_report_dir)
    )
    name75_rewrites = (
        () if args.no_ui_allowlist else load_name75_rewrites(args.ui_report_dir)
    )
    c1 = check_dict_expansion(
        jp,
        tgt,
        records,
        localized,
        name75_rewrites,
        detachment_baseline=detachment_baseline,
        detachment_records=detachment_records,
        detachment_target_records=detachment_target_records,
    )
    c2 = check_marker_records(jp, tgt, records)
    # check (iii) walks a wider set: the bank-75 UI label table is sequentially
    # terminated, so a shortened record there shifts every following entry.
    walk_records = enumerate_records(jp, walk_regions())
    c3 = check_lengths(jp, tgt, walk_records)
    c4 = check_nested_detachment_render(
        detachment_baseline,
        tgt,
        detachment_context,
    )

    failures: List[str] = []
    if not c1["ok"]:
        failures.append(
            f"check (i) dictionary expansion: {c1['dict_only_mismatches']} dict-only "
            f"+ {c1['rendered_mismatches']} rendered mismatches"
        )
    if not c2["ok"]:
        failures.append(
            f"check (ii) marker/ext3 misconsumption: {c2['misconsumed']} original "
            "record(s) interpreted as marker/ext3 by the target"
        )
    if not c3["ok"]:
        failures.append(
            f"check (iii) length/terminator: {c3['violations']} record(s) changed"
        )
    if not c4["ok"]:
        failures.append(
            "check (iv) nested dictionary detachment: "
            f"{c4['render_mismatches']} render + "
            f"{c4['length_or_terminator_mismatches']} shape mismatch(es)"
        )

    report = {
        "ok": not failures,
        "generated_by": "tools/verify_nondialogue_text.py",
        "read_only": True,
        "original": str(args.jp),
        "target": str(args.target),
        "baseline": str(args.baseline) if args.baseline else None,
        "approved_detachment": detachment_context,
        "scope": {
            "aux_banks": [f"{s:02X}" for s in AUX_TOKEN_BANKS],
            "name75_ranges": [f"{lo:06X}-{hi:06X}" for lo, hi in NAME75_RANGES],
            "ui_table_ranges": [f"{lo:06X}-{hi:06X}" for lo, hi in UI_TABLE_RANGES],
            "records_enumerated_on_original": len(records),
            "records_enumerated_for_length_check": len(walk_records),
            "approved_ranges": [f"{lo:06X}-{hi:06X}" for lo, hi in APPROVED_RANGES],
            "length_check_waiver": "graphics cells only (intermission label tiles)",
        },
        "failures": failures,
        "check_i_dict_expansion": c1,
        "check_ii_marker_records": c2,
        "check_iii_length_terminator": c3,
        "check_iv_nested_dictionary_detachment": c4,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        print(f"original : {args.jp}")
        print(f"target   : {args.target}")
        print(f"records  : {len(records)} enumerated on the original")
        print(
            f"check (i)   dict expansion : compared {c1['records_compared']} "
            f"(of {c1['records_with_tokens']} token-bearing, "
            f"{c1['records_skipped_unresolvable_in_original']} out of scope) → "
            f"dict_only {c1['dict_only_mismatches']}, rendered {c1['rendered_mismatches']} "
            f"→ {'ok' if c1['ok'] else 'FAIL'}"
        )
        print(
            f"    UI allowlist: {c1['ui_localized_indices']} curated indices → "
            f"explained dict_only {c1['ui_explained_dict_only']}, "
            f"rendered {c1['ui_explained_rendered']}"
        )
        for m in c1["dict_only_sample"][:8]:
            print(f"    {m['site']} [{m['region']}] first diff at {m['first_diff_at']}")
            print(f"      orig   {m['orig_window']}")
            print(f"      target {m['target_window']}")
        print(
            f"check (ii)  marker/ext3    : E3DB {c2['counts']['E3DB']} sites "
            f"(doc {c2['counts']['documented_E3DB']}), E518 {c2['counts']['E518']} "
            f"(doc {c2['counts']['documented_E518']}), misconsumed "
            f"{c2['misconsumed']} → {'ok' if c2['ok'] else 'FAIL'}"
        )
        if c2["snapshot_discrepancy"]:
            print(f"    snapshot discrepancy: {c2['snapshot_discrepancy']}")
        for s in c2["sites"]:
            if s.get("interpreted"):
                print(f"    {s['pair']} {s['site']} in record {s['record']} → {s['status']}")
        print(
            f"check (iii) length/term    : {c3['violations']} violation(s) of "
            f"{c3['records_checked']} → {'ok' if c3['ok'] else 'FAIL'}"
        )
        for m in c3["sample"][:8]:
            print(
                f"    {m['site']} orig len {m['orig_len']} term {m['orig_terminator']} "
                f"→ target len {m['target_len']} term {m['target_terminator']} "
                f"({m['reason']})"
            )
        print(
            f"check (iv)  nested dict    : {c4['records_checked']} record(s), "
            f"render {c4['render_mismatches']}, shape "
            f"{c4['length_or_terminator_mismatches']} → "
            f"{'ok' if c4['ok'] else 'FAIL'}"
        )
        print(f"\n→ {args.out}")
        print(f"ok={report['ok']}")
        for f in failures:
            print(f"  FAIL {f}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
