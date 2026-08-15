#!/usr/bin/env python3
"""
Text-bank overflow helpers.

WonderSwan Mono-Eye dialogue is mostly a sequential null-terminated stream.
Only a minority of records have far pointers. Therefore overflow prefers
in-bank shift expansion (keeps sequential order) and then patches every
discovered pointer form that targeted moved offsets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from monoeye_rom import (
    BANK_SIZE,
    logical_bank_offset,
    patch_bank,
    patch_expansion_bank,
    read_encoded_z,
    read_encoded_z_safe,
    slice_bank,
    slice_expansion_bank,
    stock_base,
    is_expanded_rom,
)

# Cap far-pointer rewrites per spilled offset. Higher counts are almost
# always noise for common offsets like bank+0000.
MAX_SPILL_POINTER_HITS = 16

# Never treat these banks as far-pointer *sites* when rewriting dialogue spills.
# Unit/scenario/roster tables live here; coincidental off16+seg bytes were
# rewritten to expansion (bank30+) and broke stage-2 guest MS (Jagd Doga at
# 6D937C). Dialogue sequential banks 60–6B remain searchable; event/aux
# 50–5B is searchable but hits require a real zstring target (see below).
POINTER_SEARCH_DENY_BANKS: frozenset[int] = frozenset(
    {
        0x5C,
        0x5D,
        0x5E,  # scenario / aux tables (not dialogue stream)
        0x5F,  # dictionary (already skipped historically)
        # 64–69 are fixed-stride data tables, not a dialogue stream: they hold the
        # per-stage (event-name, event-body) pointer pair tables — e.g. 64:4F79
        # indexes 64:4FF1 'ＳＴＧ３<E62F>オ－プニング' and the stage-3 event bodies
        # at 64:3300–64:43B6. Writes here broke the stage-3 event (freeze) and the
        # late-stage tables. Matches diff_stock_3way.DIALOGUE_HI = 0x63FFFF.
        0x64,
        0x65,
        0x66,
        0x67,
        0x68,
        0x69,
        0x6C,
        0x6D,  # MS master / unit records
        0x6E,
        0x6F,
    }
)

# Reject pointer hits whose target is a 0–1 byte "string" (mid-token / noise).
# Those offsets appear in coincidental off16+seg patterns inside unit/aux data
# and were rewritten into bank30, corrupting stage deployment / guest MS picks.
MIN_SPILL_POINTER_TARGET_LEN = 2


@dataclass
class PointerHit:
    abs_at: int
    kind: str
    segment: int
    old_off: int
    stride: int = 2


@dataclass
class PatchReport:
    kind_counts: Dict[str, int] = field(default_factory=dict)
    fixes: int = 0
    hits_considered: int = 0


def list_bank_records(rom: bytes | bytearray, segment: int) -> List[Tuple[int, bytes]]:
    """Return (logical_abs, payload) for each record in a stock text bank."""
    logical_base = logical_bank_offset(segment)
    bank = slice_bank(rom, segment)
    records: List[Tuple[int, bytes]] = []
    cursor = 0
    while cursor < len(bank):
        if bank[cursor] == 0:
            cursor += 1
            continue
        payload, terminator = read_encoded_z(bank, cursor, len(bank) - cursor)
        records.append((logical_base + cursor, payload))
        cursor = terminator + 1
    return records


def record_offset_set(rom: bytes | bytearray, segment: int) -> Set[int]:
    return {abs_off & 0xFFFF for abs_off, _ in list_bank_records(rom, segment)}


def trailing_free_start(bank: bytes | bytearray) -> int:
    index = len(bank)
    while index > 0 and bank[index - 1] in (0x00, 0xFF):
        index -= 1
    return index


def _hit_logical_bank(rom: bytes | bytearray, file_abs: int) -> int:
    return (file_abs - stock_base(rom)) >> 16


def discover_pointer_hits(
    rom: bytes | bytearray,
    segment: int,
    offsets: Set[int],
    *,
    search_segments: Iterable[int] | None = None,
) -> List[PointerHit]:
    """
    Find references to the given in-bank offsets.

    Formats:
      - off16_seg8:      oo oo bb   where bb is the *bank register* byte
      - off16_table:     oo oo inside a dense run of valid record offsets

    ``bb`` is not the bare logical bank number. Measured on the original ROM, the
    game stores the bank register value, which for stock content in the 16 MiB
    layout is ``(stock_base >> 16) + segment`` — e.g. the stage event tables in
    bank 66 hold ``b2 13 e6 00`` for ``66:13B2`` and the bank-65 event name/body
    table holds ``bc 60 e5 00`` for ``65:60BC``.

    Two shapes were removed because they can only ever fire by coincidence:

    * ``off16_00_seg8`` (``oo oo 00 ss``) — the game's 4-byte form is
      ``oo oo bb 00``, i.e. the bank byte comes *before* the pad, so this shape
      never matched a real pointer; and ``off16_seg8`` already covers it.
    * ``seg8_off16`` (``ss oo oo`` with a bare ``0x6x`` lead) — no measured
      instance in the ROM. A bare ``0x6x`` cannot address stock ROM here, so every
      hit was live data that merely happened to hold a valid record offset next to
      a ``0x6x`` byte. Confirmed victims: ``61:84E3`` (middle of a dialogue
      payload), ``68:2747`` (4bpp tile data), ``64:4458`` (event stream, where the
      framing overlapped the game's genuine ``61 44 e4`` = ``64:4461`` pointer and
      left bank byte ``0x06``).

    PointerHit.abs_at is a *file* offset (includes 16MB stock_base).
    """
    if not offsets:
        return []

    # Default: only dialogue banks 60–69. Searching 0x40–0x4F (graphics) or
    # 0x70–0x7F (code) yields false far-pointer hits that brick boot.
    # Event/aux 50–5B and 6A–6B also hold unit/scenario tables; rewriting
    # coincidental off16+seg there swaps stage-2 guest MS (Z Gundam etc.).
    # Deny list still blocks 5C–5F / 6C–6F if callers widen search_segments.
    segments = (
        list(search_segments)
        if search_segments is not None
        else [
            seg
            for seg in range(0x60, 0x6A)
            if seg != segment and seg not in POINTER_SEARCH_DENY_BANKS
        ]
    )
    hits: List[PointerHit] = []
    seen: Set[Tuple[int, str]] = set()
    sb = stock_base(rom)
    # Bank register value the game stores for a stock logical bank.
    target_bank_byte = (sb >> 16) + segment

    def add(hit: PointerHit) -> None:
        key = (hit.abs_at, hit.kind)
        if key in seen:
            return
        seen.add(key)
        hits.append(hit)

    # Pre-validate which offsets are real dialogue record starts in `segment`.
    # Mid-string / 1-byte false records must not authorize pointer rewrites.
    seg_file_base = sb + logical_bank_offset(segment)
    valid_target_offs: Set[int] = set()
    for off in offsets:
        got = read_encoded_z_safe(
            rom, seg_file_base + off, max_len=0x400
        )
        if got is not None and len(got[0]) >= MIN_SPILL_POINTER_TARGET_LEN:
            valid_target_offs.add(off)

    for sseg in segments:
        bank = slice_bank(rom, sseg)
        file_base = sb + logical_bank_offset(sseg)

        # Explicit far forms.
        for i in range(0, BANK_SIZE - 2):
            off = bank[i] | (bank[i + 1] << 8)
            if off not in valid_target_offs:
                continue
            abs_at = file_base + i
            if bank[i + 2] == target_bank_byte:
                add(PointerHit(abs_at, "off16_seg8", segment, off, 3))

        # Dense plain off16 tables (sequential script pointer lists).
        i = 0
        while i < BANK_SIZE - 3:
            run_at = []
            j = i
            prev = -1
            while j + 1 < BANK_SIZE:
                off = bank[j] | (bank[j + 1] << 8)
                if off not in valid_target_offs:
                    break
                if prev >= 0 and not (0 < off - prev <= 0x200):
                    break
                run_at.append((j, off))
                prev = off
                j += 2
            if len(run_at) >= 6:
                for rel, off in run_at:
                    add(PointerHit(file_base + rel, "off16_table", segment, off, 2))
                i = j
            else:
                i += 2

    return hits


def patch_pointer_hits(
    rom: bytearray,
    hits: Sequence[PointerHit],
    old_to_new_off: Dict[int, int],
    *,
    new_segment: int | None = None,
) -> PatchReport:
    """
    Rewrite discovered pointer hits.

    If new_segment is set, also update the segment byte on segmented forms
    (required when relocating to an expansion bank).
    """
    report = PatchReport()
    for hit in hits:
        new_off = old_to_new_off.get(hit.old_off)
        report.hits_considered += 1
        if new_off is None:
            continue
        same_off = new_off == hit.old_off
        same_seg = new_segment is None or new_segment == hit.segment
        if same_off and same_seg:
            continue
        hit_bank = _hit_logical_bank(rom, hit.abs_at)
        # Hard refuse: graphics/code, dictionary, and unit/scenario tables.
        if not (0x50 <= hit_bank <= 0x6F) or hit_bank in POINTER_SEARCH_DENY_BANKS:
            continue
        if hit.kind == "off16_seg8":
            rom[hit.abs_at] = new_off & 0xFF
            rom[hit.abs_at + 1] = (new_off >> 8) & 0xFF
            if new_segment is not None:
                rom[hit.abs_at + 2] = new_segment & 0xFF
        elif hit.kind == "off16_00_seg8":
            rom[hit.abs_at] = new_off & 0xFF
            rom[hit.abs_at + 1] = (new_off >> 8) & 0xFF
            if new_segment is not None:
                rom[hit.abs_at + 3] = new_segment & 0xFF
        elif hit.kind == "seg8_off16":
            if new_segment is not None:
                rom[hit.abs_at] = new_segment & 0xFF
            rom[hit.abs_at + 1] = new_off & 0xFF
            rom[hit.abs_at + 2] = (new_off >> 8) & 0xFF
        elif hit.kind == "off16_table":
            # No segment byte — only safe for same-bank moves.
            if new_segment is not None and new_segment != hit.segment:
                continue
            rom[hit.abs_at] = new_off & 0xFF
            rom[hit.abs_at + 1] = (new_off >> 8) & 0xFF
        else:
            continue
        report.fixes += 1
        report.kind_counts[hit.kind] = report.kind_counts.get(hit.kind, 0) + 1
    return report


SEGMENTED_POINTER_KINDS = frozenset({"off16_seg8", "off16_00_seg8", "seg8_off16"})

# Expansion script spill window (16MB prepend banks).
EXP_SCRIPT_SEG_START = 0x30
EXP_SCRIPT_SEG_END = 0x4F


def spill_replacements_to_expansion(
    rom: bytearray,
    replacements: Dict[int, bytes],
    *,
    seg_start: int = EXP_SCRIPT_SEG_START,
    seg_end: int = EXP_SCRIPT_SEG_END,
    blank_old: bool = False,
) -> dict:
    """
    Relocate far-pointer-backed records into expansion banks 0x30+.

    Only records with segmented pointer forms (off16_seg8 / seg8_off16 /
    off16_00_seg8) and hit count ≤ MAX_SPILL_POINTER_HITS are moved.
    Stock original bytes are left in place unless blank_old=True.
    """
    if not is_expanded_rom(rom):
        raise RuntimeError("expansion script spill requires a 16MB ROM")
    if not replacements:
        return {
            "mode": "exp_spill",
            "relocated_records": 0,
            "pointer_fixes": 0,
            "mapping": {},
            "banks": [],
            "skipped_no_pointer_count": 0,
            "skipped_ambiguous_pointer_count": 0,
            "skipped_no_seg_form_count": 0,
        }

    by_segment: Dict[int, Dict[int, bytes]] = {}
    for abs_off, payload in replacements.items():
        by_segment.setdefault(abs_off // BANK_SIZE, {})[abs_off] = payload

    # Working cursors per expansion bank image.
    # IMPORTANT: load existing bank contents — wiping to FF would orphan
    # already-retargeted pointers from prior spill passes (dangling → empty).
    exp_banks: Dict[int, bytearray] = {}
    exp_cursors: Dict[int, int] = {}

    def ensure_exp(seg: int) -> tuple[bytearray, int]:
        if seg not in exp_banks:
            exp_banks[seg] = bytearray(slice_expansion_bank(rom, seg))
            exp_cursors[seg] = trailing_free_start(exp_banks[seg])
        return exp_banks[seg], exp_cursors[seg]

    all_maps: Dict[int, int] = {}
    bank_rows = []
    total_fixes = 0
    kind_counts: Dict[str, int] = {}
    skipped_no_ptr = 0
    skipped_ambiguous = 0
    skipped_no_seg = 0
    next_exp = seg_start

    for segment, local in sorted(by_segment.items()):
        # Dialogue stream only — never treat 6C–6F MS/unit banks as spill sources.
        if not (0x60 <= segment <= 0x6B):
            skipped_no_ptr += len(local)
            continue

        candidate_offs = {abs_off & 0xFFFF for abs_off in local}
        hits = discover_pointer_hits(rom, segment, candidate_offs)
        hits = [
            h
            for h in hits
            if 0x50 <= _hit_logical_bank(rom, h.abs_at) <= 0x6F
            and _hit_logical_bank(rom, h.abs_at) not in POINTER_SEARCH_DENY_BANKS
        ]
        hits_by_off: Dict[int, List[PointerHit]] = {}
        for hit in hits:
            hits_by_off.setdefault(hit.old_off, []).append(hit)

        mapping: Dict[int, int] = {}  # old_logical -> new_logical
        relocated_hits: List[PointerHit] = []
        old_to_new_off: Dict[int, int] = {}
        new_seg_for_off: Dict[int, int] = {}

        for old_abs, new_payload in sorted(local.items()):
            old_off = old_abs & 0xFFFF
            off_hits = hits_by_off.get(old_off, [])
            if not off_hits:
                skipped_no_ptr += 1
                continue
            if len(off_hits) > MAX_SPILL_POINTER_HITS:
                skipped_ambiguous += 1
                continue
            seg_hits = [h for h in off_hits if h.kind in SEGMENTED_POINTER_KINDS]
            if not seg_hits:
                skipped_no_seg += 1
                continue

            # Find an expansion bank with room.
            placed = False
            for attempt in range(seg_start, seg_end + 1):
                exp_seg = next_exp if next_exp <= seg_end else attempt
                if exp_seg > seg_end:
                    exp_seg = attempt
                bank, cursor = ensure_exp(exp_seg)
                need = len(new_payload) + 1
                if cursor + need > BANK_SIZE:
                    next_exp = exp_seg + 1
                    continue
                bank[cursor : cursor + len(new_payload)] = new_payload
                bank[cursor + len(new_payload)] = 0
                new_logical = logical_bank_offset(exp_seg, cursor)
                mapping[old_abs] = new_logical
                old_to_new_off[old_off] = cursor
                new_seg_for_off[old_off] = exp_seg
                exp_cursors[exp_seg] = cursor + need
                next_exp = exp_seg
                relocated_hits.extend(seg_hits)
                placed = True
                break
            if not placed:
                skipped_ambiguous += 1  # treat as capacity/ambiguous drop

        if not mapping:
            bank_rows.append(
                {
                    "source_segment": f"{segment:02X}",
                    "relocated": 0,
                    "skipped_no_pointer": skipped_no_ptr,
                }
            )
            continue

        # Patch pointers — may target different new segments per offset.
        # Group by destination segment.
        by_dest: Dict[int, List[PointerHit]] = {}
        dest_off_maps: Dict[int, Dict[int, int]] = {}
        for hit in relocated_hits:
            dest = new_seg_for_off[hit.old_off]
            by_dest.setdefault(dest, []).append(hit)
            dest_off_maps.setdefault(dest, {})[hit.old_off] = old_to_new_off[hit.old_off]

        patch_fixes = 0
        patch_kinds: Dict[str, int] = {}
        for dest, dest_hits in by_dest.items():
            patch = patch_pointer_hits(
                rom, dest_hits, dest_off_maps[dest], new_segment=dest
            )
            patch_fixes += patch.fixes
            for k, c in patch.kind_counts.items():
                patch_kinds[k] = patch_kinds.get(k, 0) + c

        if blank_old:
            bank = bytearray(slice_bank(rom, segment))
            for old_abs in mapping:
                old_off = old_abs & 0xFFFF
                _payload, term = read_encoded_z(bank, old_off, BANK_SIZE - old_off)
                bank[old_off : term + 1] = b"\x00" * (term + 1 - old_off)
            patch_bank(rom, segment, bank)

        total_fixes += patch_fixes
        for k, c in patch_kinds.items():
            kind_counts[k] = kind_counts.get(k, 0) + c
        all_maps.update(mapping)
        bank_rows.append(
            {
                "source_segment": f"{segment:02X}",
                "relocated": len(mapping),
                "pointer_fixes": patch_fixes,
                "pointer_form_counts": patch_kinds,
                "dest_segments": sorted(
                    {f"{new_seg_for_off[o]:02X}" for o in old_to_new_off}
                ),
            }
        )

    for seg, bank in exp_banks.items():
        patch_expansion_bank(rom, seg, bank)

    return {
        "mode": "exp_spill",
        "banks": bank_rows,
        "pointer_fixes": total_fixes,
        "pointer_form_counts": kind_counts,
        "relocated_records": len(all_maps),
        "skipped_no_pointer_count": skipped_no_ptr,
        "skipped_ambiguous_pointer_count": skipped_ambiguous,
        "skipped_no_seg_form_count": skipped_no_seg,
        "mapping": {f"{o:06X}": f"{n:06X}" for o, n in all_maps.items()},
        "expansion_bytes_used": {
            f"{seg:02X}": exp_cursors.get(seg, 0) for seg in sorted(exp_banks)
        },
    }

def rebuild_text_segment_shift(
    rom: bytearray,
    segment: int,
    replacements: Dict[int, bytes],
) -> Tuple[Dict[int, int], dict]:
    """
    Rebuild one text bank with optional payload replacements, packing tightly
    from offset 0 while preserving record order.

    Returns mapping old_abs -> new_abs for every record in the bank.
    """
    if not (0x60 <= segment <= 0x6F):
        raise RuntimeError(f"Not a text bank: {segment:02X}")

    base = segment * BANK_SIZE
    original = list_bank_records(rom, segment)
    new_bank = bytearray(b"\xFF" * BANK_SIZE)
    cursor = 0
    mapping: Dict[int, int] = {}
    replaced = 0

    for old_abs, payload in original:
        new_payload = replacements.get(old_abs, payload)
        if old_abs in replacements:
            replaced += 1
        need = len(new_payload) + 1
        if cursor + need > BANK_SIZE:
            raise RuntimeError(
                f"Text bank {segment:02X} overflow while shifting "
                f"@{old_abs:06X} (need {cursor + need:#x})"
            )
        new_abs = base + cursor
        new_bank[cursor : cursor + len(new_payload)] = new_payload
        new_bank[cursor + len(new_payload)] = 0
        mapping[old_abs] = new_abs
        cursor += need

    # Keep a clear free tail.
    if cursor < BANK_SIZE:
        new_bank[cursor:] = b"\xFF" * (BANK_SIZE - cursor)

    # Discover pointers against the ORIGINAL offsets before overwriting the bank.
    old_offs = {abs_off & 0xFFFF for abs_off in mapping}
    hits = discover_pointer_hits(rom, segment, old_offs)
    patch_bank(rom, segment, new_bank)

    old_to_new_off = {
        old_abs & 0xFFFF: new_abs & 0xFFFF for old_abs, new_abs in mapping.items()
    }
    patch = patch_pointer_hits(rom, hits, old_to_new_off)

    info = {
        "segment": f"{segment:02X}",
        "records": len(original),
        "replaced": replaced,
        "moved": sum(1 for old, new in mapping.items() if old != new),
        "bytes_used": cursor,
        "bytes_free": BANK_SIZE - cursor,
        "pointer_hits": patch.hits_considered,
        "pointer_fixes": patch.fixes,
        "pointer_form_counts": patch.kind_counts,
    }
    return mapping, info


def filter_spill_to_tail_capacity(
    rom: bytes | bytearray,
    replacements: Dict[int, bytes],
) -> Tuple[Dict[int, bytes], List[int]]:
    """
    Keep only replacements that fit in each bank's trailing free region.

    Prefer lower abs (earlier dialogue). Dropped abs are returned for reporting.
    """
    by_segment: Dict[int, List[Tuple[int, bytes]]] = {}
    for abs_off, payload in replacements.items():
        by_segment.setdefault(abs_off // BANK_SIZE, []).append((abs_off, payload))

    kept: Dict[int, bytes] = {}
    dropped: List[int] = []
    for segment, items in sorted(by_segment.items()):
        if not (0x60 <= segment <= 0x6F):
            dropped.extend(abs_off for abs_off, _ in items)
            continue
        bank = slice_bank(rom, segment)
        free_at = trailing_free_start(bank)
        room = BANK_SIZE - free_at
        # Sort earliest-first so opening/bank head wins limited tail space.
        items.sort(key=lambda it: it[0])
        used = 0
        for abs_off, payload in items:
            need = len(payload) + 1
            if used + need > room:
                dropped.append(abs_off)
                continue
            kept[abs_off] = payload
            used += need
    return kept, dropped


def spill_replacements_to_bank_tails(
    rom: bytearray,
    replacements: Dict[int, bytes],
) -> dict:
    """
    Legacy spill mode: move only replaced payloads to the trailing pad and blank
    the old sites. Prefer shift rebuild for sequential scripts; keep this for
    records that already have exclusive far pointers and when explicitly requested.

    Safety: relocate only when pointer hits exist AND hit count is low enough to
    avoid rewriting noise matches for common offsets (e.g. bank+0000).
    """
    by_segment: Dict[int, Dict[int, bytes]] = {}
    for abs_off, payload in replacements.items():
        by_segment.setdefault(abs_off // BANK_SIZE, {})[abs_off] = payload

    all_maps: Dict[int, int] = {}
    bank_rows = []
    total_fixes = 0
    kind_counts: Dict[str, int] = {}

    for segment, local in sorted(by_segment.items()):
        if not (0x60 <= segment <= 0x6F):
            raise RuntimeError(f"Replacement outside text banks: {segment:02X}")
        base = segment * BANK_SIZE
        bank = bytearray(slice_bank(rom, segment))
        free_at = trailing_free_start(bank)
        mapping: Dict[int, int] = {}
        skipped_no_ptr: List[int] = []
        skipped_ambiguous: List[int] = []

        # Discover pointers first; only relocate records with a small number of
        # hits. Tiny offsets (especially 0000) match noise across the ROM and
        # produce hundreds of false pointer rewrites.
        candidate_offs = {abs_off & 0xFFFF for abs_off in local}
        hits = discover_pointer_hits(rom, segment, candidate_offs)
        # Drop hits outside the safe rewrite window before deciding to relocate.
        hits = [
            h
            for h in hits
            if 0x50 <= _hit_logical_bank(rom, h.abs_at) <= 0x6F
            and _hit_logical_bank(rom, h.abs_at) not in POINTER_SEARCH_DENY_BANKS
        ]
        hits_by_off: Dict[int, List[PointerHit]] = {}
        for hit in hits:
            hits_by_off.setdefault(hit.old_off, []).append(hit)

        for old_abs, new_payload in sorted(local.items()):
            old_off = old_abs & 0xFFFF
            off_hits = hits_by_off.get(old_off, [])
            if not off_hits:
                # Sequential-scan banks may still work, but blanking without a
                # pointer update is unsafe for far-pointer driven lines.
                skipped_no_ptr.append(old_abs)
                continue
            if len(off_hits) > MAX_SPILL_POINTER_HITS:
                skipped_ambiguous.append(old_abs)
                continue
            _old_payload, term = read_encoded_z(bank, old_off, BANK_SIZE - old_off)
            bank[old_off : term + 1] = b"\x00" * (term + 1 - old_off)
            need = len(new_payload) + 1
            if free_at + need > BANK_SIZE:
                raise RuntimeError(
                    f"Bank {segment:02X} tail overflow relocating @{old_abs:06X}"
                )
            new_off = free_at
            bank[new_off : new_off + len(new_payload)] = new_payload
            bank[new_off + len(new_payload)] = 0
            mapping[old_abs] = base + new_off
            free_at += need

        if not mapping:
            bank_rows.append(
                {
                    "segment": f"{segment:02X}",
                    "relocated": 0,
                    "skipped_no_pointer": len(skipped_no_ptr),
                    "skipped_ambiguous_pointer": len(skipped_ambiguous),
                    "bytes_free_after": BANK_SIZE - free_at,
                    "pointer_fixes": 0,
                    "pointer_form_counts": {},
                }
            )
            continue

        patch_bank(rom, segment, bank)
        old_to_new = {
            old_abs & 0xFFFF: new_abs & 0xFFFF for old_abs, new_abs in mapping.items()
        }
        hits = [h for h in hits if h.old_off in old_to_new]
        patch = patch_pointer_hits(rom, hits, old_to_new)
        total_fixes += patch.fixes
        for kind, count in patch.kind_counts.items():
            kind_counts[kind] = kind_counts.get(kind, 0) + count

        all_maps.update(mapping)
        bank_rows.append(
            {
                "segment": f"{segment:02X}",
                "relocated": len(mapping),
                "skipped_no_pointer": len(skipped_no_ptr),
                "skipped_ambiguous_pointer": len(skipped_ambiguous),
                "bytes_free_after": BANK_SIZE - free_at,
                "pointer_fixes": patch.fixes,
                "pointer_form_counts": patch.kind_counts,
            }
        )

    return {
        "mode": "spill_to_tail",
        "banks": bank_rows,
        "pointer_fixes": total_fixes,
        "pointer_form_counts": kind_counts,
        "relocated_records": len(all_maps),
        "skipped_no_pointer_count": sum(
            int(row.get("skipped_no_pointer", 0) or 0) for row in bank_rows
        ),
        "skipped_ambiguous_pointer_count": sum(
            int(row.get("skipped_ambiguous_pointer", 0) or 0) for row in bank_rows
        ),
        "mapping": {f"{old:06X}": f"{new:06X}" for old, new in all_maps.items()},
    }


def filter_replacements_to_bank_capacity(
    rom: bytes | bytearray,
    replacements: Dict[int, bytes],
    flexible_abs: Set[int],
) -> Tuple[Dict[int, bytes], List[int]]:
    """
    Drop flexible (usually shift-rebuild) replacements until each text bank fits.

    Size-preserving inplace replacements are never dropped when the original
    bank already packs within BANK_SIZE. Banks whose natural packed size already
    exceeds BANK_SIZE (binary/non-dialogue blobs misread as records) cannot be
    shift-rebuilt; all flexible expansions there are dropped.
    """
    kept = dict(replacements)
    dropped: List[int] = []
    segments = sorted(
        {
            abs_off // BANK_SIZE
            for abs_off in kept
            if 0x60 <= abs_off // BANK_SIZE <= 0x6F
        }
    )
    for segment in segments:
        records = list_bank_records(rom, segment)
        natural = sum(len(payload) + 1 for _abs, payload in records)
        if natural > BANK_SIZE:
            for old_abs, payload in records:
                if old_abs not in kept:
                    continue
                if old_abs in flexible_abs or len(kept[old_abs]) != len(payload):
                    del kept[old_abs]
                    dropped.append(old_abs)
            continue
        while True:
            cursor = 0
            overflow_at = None
            for old_abs, payload in records:
                new_payload = kept.get(old_abs, payload)
                need = len(new_payload) + 1
                if cursor + need > BANK_SIZE:
                    overflow_at = old_abs
                    break
                cursor += need
            if overflow_at is None:
                break
            victim = None
            if overflow_at in flexible_abs and overflow_at in kept:
                victim = overflow_at
            else:
                best_delta = -1
                for old_abs, payload in records:
                    if old_abs not in flexible_abs or old_abs not in kept:
                        continue
                    delta = len(kept[old_abs]) - len(payload)
                    if delta > best_delta:
                        best_delta = delta
                        victim = old_abs
            if victim is None:
                # Fall back: drop any longer-than-original replacement.
                for old_abs, payload in records:
                    if old_abs in kept and len(kept[old_abs]) > len(payload):
                        victim = old_abs
                        break
            if victim is None:
                raise RuntimeError(
                    f"Text bank {segment:02X} overflow with no flexible "
                    f"replacements left to drop (@{overflow_at:06X})"
                )
            del kept[victim]
            dropped.append(victim)
    return kept, dropped


def shift_replacements_in_text_banks(
    rom: bytearray,
    replacements: Dict[int, bytes],
) -> dict:
    """Primary overflow path: rebuild affected text banks with shift + pointer patch."""
    by_segment: Dict[int, Dict[int, bytes]] = {}
    for abs_off, payload in replacements.items():
        by_segment.setdefault(abs_off // BANK_SIZE, {})[abs_off] = payload

    all_maps: Dict[int, int] = {}
    bank_rows = []
    total_fixes = 0
    kind_counts: Dict[str, int] = {}

    for segment, local in sorted(by_segment.items()):
        mapping, info = rebuild_text_segment_shift(rom, segment, local)
        all_maps.update(mapping)
        total_fixes += info["pointer_fixes"]
        for kind, count in info["pointer_form_counts"].items():
            kind_counts[kind] = kind_counts.get(kind, 0) + count
        bank_rows.append(info)

    return {
        "mode": "shift_rebuild",
        "banks": bank_rows,
        "pointer_fixes": total_fixes,
        "pointer_form_counts": kind_counts,
        "relocated_records": sum(1 for old, new in all_maps.items() if old != new),
        "mapping": {f"{old:06X}": f"{new:06X}" for old, new in all_maps.items()},
        "mapping_changed": {
            f"{old:06X}": f"{new:06X}"
            for old, new in all_maps.items()
            if old != new
        },
    }
