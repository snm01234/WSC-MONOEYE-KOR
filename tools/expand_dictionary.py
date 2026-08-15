#!/usr/bin/env python3
"""
Dictionary expansion helpers for full Korean translation capacity.

Reclaims dictionary slots that become unreferenced once selected script
records are rewritten, rebuilds bank 5F with a denser layout, and optionally
builds a small Korean phrase dictionary for compression.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from hangul_marker import marker_code
from monoeye_rom import (
    BANK_SIZE,
    DICT_DATA_START,
    DICT_PTR_START,
    Dictionary,
    SEG_DICT,
    Tbl,
    dict_index_from_token,
    encode_plaintext,
    is_dict_token,
    is_kanji_lead,
    patch_bank,
    read_encoded_z_safe,
    rebuild_dictionary,
    slice_bank,
    stock_base,
    token_from_dict_index,
    write_le16,
)

# Bank 75:E720-E900 is not text.  It is a 37-entry terrain descriptor table:
# each 13-byte row is ``far_name_pointer + 9 bytes of terrain stats``.  Walking
# it as NUL-delimited text corrupted both the far pointers and the modifiers.
NAME75_STRUCTURED_RANGES = (
    (0x75E720, 0x75E901),
)

# External dict-token consumers (not bank-5F/5E phrase payloads — those are nested).
NAME75_RANGES = (
    (0x75C000, 0x75E720),  # unit/weapon/name strings before terrain descriptors
    (0x75FE93, 0x760000),  # bank75 trail spill
)
# Battle/UI banks 50–5F also embed stock dict tokens in zstrings. Missing these
# made sole_reclaim treat shared slots as sole (e.g. dict[21] → mid-game KO leak).
# False positives only block reclaim (fail-closed); false negatives corrupt UI.
# Bank 5F is the stock dictionary payload/pointer bank itself, not an
# external zstring consumer. Scanning it as aux walks arbitrary phrase bytes
# as FF-page tokens (for example 2938@5F9960) and can reject a genuinely free
# story slot. Keep 50-5E and 76 as external aux consumers.
AUX_TOKEN_BANKS = tuple(range(0x50, 0x5F)) + (0x76,)
SCRIPT_TOKEN_BANKS = tuple(range(0x60, 0x70))
# name75 + aux required so sole_reclaim never steals unit/UI-shared tokens.
DEFAULT_REF_REGIONS = ("script", "name75", "aux")


@dataclass(frozen=True)
class DictTokenRef:
    """One use of a dictionary token outside nested phrase expansion."""

    abs: int  # logical file abs (stock-relative)
    region: str  # script | name75 | aux
    kind: str  # dialogue | zstring


def iter_dict_indices(payload: bytes) -> Iterable[int]:
    cursor = 0
    while cursor < len(payload):
        lead = payload[cursor]
        if is_dict_token(lead) and cursor + 1 < len(payload):
            yield dict_index_from_token(lead, payload[cursor + 1])
            cursor += 2
        elif is_kanji_lead(lead) and cursor + 1 < len(payload):
            cursor += 2
        else:
            cursor += 1


def _classify_script_kind(payload: bytes) -> str:
    """Best-effort: dialogue vs plain zstring (avoid import cycles)."""
    try:
        from extract_script import split_prefix_body  # noqa: WPS433

        return split_prefix_body(payload)[2]
    except Exception:
        return "zstring"


def _walk_zstring_range(
    rom: bytes | bytearray,
    logical_start: int,
    logical_end: int,
    *,
    region: str,
    max_len: int = 256,
) -> Iterable[Tuple[int, bytes, str]]:
    """Yield (logical_abs, payload, kind) for NUL-terminated records in range."""
    sb = stock_base(rom)
    off = sb + logical_start
    end = sb + logical_end
    if end > len(rom):
        end = len(rom)
    while off < end:
        # 00 / FF are padding; never start a record on them (FF xx looks like
        # ext-dict tokens and creates massive false consumers).
        if rom[off] in (0x00, 0xFF):
            off += 1
            continue
        got = read_encoded_z_safe(rom, off, max_len=max_len)
        if got is None:
            off += 1
            continue
        payload, term = got
        if not payload:
            off = term + 1 if term >= off else off + 1
            continue
        logical = off - sb
        kind = (
            _classify_script_kind(payload)
            if region == "script"
            else "zstring"
        )
        yield logical, payload, kind
        off = term + 1 if term >= off else off + 1


def build_dict_token_locs(
    rom: bytes | bytearray,
    *,
    regions: Sequence[str] = DEFAULT_REF_REGIONS,
    exclude_abs: Set[int] | None = None,
) -> Dict[int, List[DictTokenRef]]:
    """
    Map dict index → external reference sites.

    Default regions: script 60–6F + bank75 name tables + aux (50–5F, 76).
    Bank 5F/5E *phrase dictionary payloads* are not scanned as nested refs
    here (nested walk is separate); aux bank walks are external zstrings only.
    """
    exclude_abs = exclude_abs or set()
    want = set(regions)
    locs: Dict[int, List[DictTokenRef]] = defaultdict(list)

    def add_payload(logical: int, payload: bytes, region: str, kind: str) -> None:
        if logical in exclude_abs:
            return
        for index in iter_dict_indices(payload):
            locs[index].append(
                DictTokenRef(abs=logical, region=region, kind=kind)
            )

    if "script" in want:
        for seg in SCRIPT_TOKEN_BANKS:
            start = seg * BANK_SIZE
            for logical, payload, kind in _walk_zstring_range(
                rom, start, start + BANK_SIZE, region="script"
            ):
                add_payload(logical, payload, "script", kind)

    if "name75" in want:
        for lo, hi in NAME75_RANGES:
            for logical, payload, kind in _walk_zstring_range(
                rom, lo, hi, region="name75", max_len=64
            ):
                add_payload(logical, payload, "name75", kind)

    if "aux" in want:
        for seg in AUX_TOKEN_BANKS:
            start = seg * BANK_SIZE
            for logical, payload, kind in _walk_zstring_range(
                rom, start, start + BANK_SIZE, region="aux", max_len=128
            ):
                add_payload(logical, payload, "aux", kind)

    return locs


def slot_non_keeper_consumers(
    locs: Dict[int, List[DictTokenRef]],
    index: int,
    *,
    keeper_abs: Set[int] | None = None,
) -> Tuple[List[DictTokenRef], List[DictTokenRef]]:
    """
    Split refs for ``index`` into (script_non_keepers, aux_or_name75).

    ``keeper_abs is None`` means the slot must be free: every script ref is a
    non-keeper. Aux/name75 are always non-keepers for rewrite/steal.
    """
    keepers = keeper_abs or set()
    script_leak: List[DictTokenRef] = []
    aux_leak: List[DictTokenRef] = []
    for ref in locs.get(index, []):
        if ref.region != "script":
            aux_leak.append(ref)
            continue
        if keeper_abs is None or ref.abs not in keepers:
            script_leak.append(ref)
    return script_leak, aux_leak


def slot_rewrite_refuse_reason(
    locs: Dict[int, List[DictTokenRef]],
    index: int,
    *,
    keeper_abs: Set[int] | None = None,
    require_free: bool = False,
) -> Optional[str]:
    """
    Fail-closed gate before rewriting a dict slot payload.

    - ``require_free``: no external refs allowed (true free slot).
    - else: every script ref must be in ``keeper_abs``; any aux/name75 → refuse.
    """
    if require_free:
        refs = locs.get(index) or []
        if refs:
            regions = sorted({r.region for r in refs})
            return f"slot {index} not free (regions={regions}, n={len(refs)})"
        return None
    script_leak, aux_leak = slot_non_keeper_consumers(
        locs, index, keeper_abs=keeper_abs
    )
    if aux_leak:
        sample = [f"{r.abs:06X}/{r.region}" for r in aux_leak[:4]]
        return f"slot {index} has aux/name75 consumers: {sample}"
    if script_leak:
        sample = [f"{r.abs:06X}" for r in script_leak[:6]]
        return f"slot {index} has non-keeper script consumers: {sample}"
    return None


def payload_has_hangul_marker(
    payload: bytes, marker: int | None = None
) -> bool:
    """Detect the installed Hangul run marker, plus the legacy fallback.

    The active release marker moved from ``E3DB`` to ``EC8D``.  A hard-coded legacy
    default silently disabled the aux/name75 invasion guard for every current
    payload, so the no-argument path must resolve :func:`marker_code` at runtime.
    The legacy code stays in the set to keep old work ROMs fail-closed too.
    """
    if not payload or len(payload) < 2:
        return False
    markers = {int(marker)} if marker is not None else {marker_code(), 0xE3DB}
    pairs = {((code >> 8) & 0xFF, code & 0xFF) for code in markers}
    return any(
        (payload[i], payload[i + 1]) in pairs for i in range(len(payload) - 1)
    )


def indices_with_aux_or_name75(
    locs: Dict[int, List[DictTokenRef]],
    indices: Iterable[int],
) -> Dict[int, List[str]]:
    """Map index → sample aux/name75 abs hex for blocked slots."""
    out: Dict[int, List[str]] = {}
    for index in indices:
        aux = [
            f"{r.abs:06X}/{r.region}"
            for r in locs.get(index, [])
            if r.region != "script"
        ]
        if aux:
            out[int(index)] = aux[:6]
    return out


def guard_hangul_slot_writes(
    rom: bytes | bytearray,
    slot_payload: Dict[int, bytes],
    *,
    allow_aux_consumers: bool = False,
    locs: Dict[int, List[DictTokenRef]] | None = None,
) -> None:
    """
    Refuse Hangul (marker) payloads on slots that already appear in aux/name75.

    Default fail-closed for all spill/exp/ext writers. Pass
    ``allow_aux_consumers=True`` only for intentional UI/shared writes or
    repair tools that migrate/neutralize aux-facing slots.
    """
    if allow_aux_consumers or not slot_payload:
        return
    hangul_idxs = [
        idx
        for idx, blob in slot_payload.items()
        if payload_has_hangul_marker(blob)
    ]
    if not hangul_idxs:
        return
    if locs is None:
        locs = build_dict_token_locs(rom, regions=DEFAULT_REF_REGIONS)
    blocked = indices_with_aux_or_name75(locs, hangul_idxs)
    if not blocked:
        return
    sample = "; ".join(
        f"{idx}@{','.join(sites[:2])}" for idx, sites in list(blocked.items())[:8]
    )
    raise RuntimeError(
        f"refusing Hangul dict write on aux/name75-live slots ({len(blocked)}): "
        f"{sample}. Use a free/script-only slot, or allow_aux_consumers=True "
        f"for intentional UI/repair (see docs/DICT_INVASION_GUARD.md)."
    )


def filter_story_safe_indices(
    rom: bytes | bytearray,
    indices: Iterable[int],
    *,
    locs: Dict[int, List[DictTokenRef]] | None = None,
) -> List[int]:
    """Drop indices that have any aux/name75 consumer (unsafe for story KO)."""
    idxs = list(indices)
    if not idxs:
        return []
    if locs is None:
        locs = build_dict_token_locs(rom, regions=DEFAULT_REF_REGIONS)
    blocked = indices_with_aux_or_name75(locs, idxs)
    return [i for i in idxs if i not in blocked]


def referenced_dict_closure(
    rom: bytes | bytearray,
    dictionary: Dictionary,
    *,
    exclude_script_abs: Set[int] | None = None,
    regions: Sequence[str] = DEFAULT_REF_REGIONS,
) -> Set[int]:
    """
    Dictionary indices still required after excluding selected script records.

    Starts from external token references (script + name75 + aux by default),
    then closes over nested dictionary-token references inside kept phrases.
    """
    exclude_script_abs = exclude_script_abs or set()
    locs = build_dict_token_locs(
        rom, regions=regions, exclude_abs=exclude_script_abs
    )
    direct: Set[int] = {
        index
        for index, refs in locs.items()
        if 0 <= index < dictionary.count and refs
    }

    keep = set(direct)
    queue = list(direct)
    while queue:
        index = queue.pop()
        if index < 0 or index >= dictionary.count:
            continue
        for child in iter_dict_indices(dictionary.raw_entry(index)):
            if child >= dictionary.count:
                continue
            if child not in keep:
                keep.add(child)
                queue.append(child)
    return {index for index in keep if 0 <= index < dictionary.count}


def reclaimable_slots(
    rom: bytes | bytearray,
    dictionary: Dictionary,
    exclude_script_abs: Set[int],
) -> List[int]:
    keep = referenced_dict_closure(
        rom, dictionary, exclude_script_abs=exclude_script_abs
    )
    return [index for index in range(dictionary.count) if index not in keep]


@dataclass
class PhrasePlan:
    """Shared Korean substrings assigned to dictionary indices."""

    phrases: List[str]
    encoded: List[bytes]
    index_by_phrase: Dict[str, int]


def select_shared_phrases(
    texts: Sequence[str],
    *,
    min_len: int = 2,
    max_len: int = 8,
    max_phrases: int = 512,
    min_count: int = 3,
) -> List[str]:
    """
    Pick frequent Korean substrings for dictionary compression.

    Scoring approximates bytes saved when a Hangul substring becomes one
    2-byte dictionary token: save ≈ count * (2*len(phrase) - 2).
    Already-chosen longer phrases suppress shorter substrings that are only
    useful as nested pieces of those longer phrases.
    """
    if max_phrases <= 0:
        return []
    counts: Counter[str] = Counter()
    for text in texts:
        length = len(text)
        seen_here: Set[str] = set()
        for size in range(min_len, min(max_len, length) + 1):
            for start in range(0, length - size + 1):
                piece = text[start : start + size]
                if piece in seen_here:
                    continue
                seen_here.add(piece)
                counts[piece] += 1

    scored: List[Tuple[int, int, str]] = []
    for phrase, count in counts.items():
        if count < min_count:
            continue
        # Assume Hangul/extended glyphs average 2 encoded bytes.
        save = count * max(1, (2 * len(phrase) - 2))
        scored.append((save, len(phrase), phrase))
    scored.sort(reverse=True)

    chosen: List[str] = []
    for _save, _length, phrase in scored:
        # Skip phrases fully covered by an already chosen longer phrase unless
        # they also occur often on their own (count stays high relative to parents).
        covered = False
        for parent in chosen:
            if phrase != parent and phrase in parent:
                parent_count = counts[parent]
                # If nearly all occurrences are explained by the parent, skip.
                if counts[phrase] <= parent_count + 1:
                    covered = True
                    break
        if covered:
            continue
        chosen.append(phrase)
        if len(chosen) >= max_phrases:
            break
    return chosen


def compress_with_phrases(
    text: str,
    tbl: Tbl,
    phrase_to_index: Dict[str, int],
    *,
    hangul_marker_code: int | None = None,
) -> bytes:
    """Longest-match encode using shared phrase dict tokens + literal chars."""
    from normalize_ko_text import TAG_RE, encode_ko_text

    if not phrase_to_index:
        return encode_ko_text(text, tbl, hangul_marker_code=hangul_marker_code)

    phrases = sorted(phrase_to_index.keys(), key=len, reverse=True)
    out = bytearray()
    cursor = 0
    while cursor < len(text):
        matched = None
        for phrase in phrases:
            if text.startswith(phrase, cursor):
                matched = phrase
                break
        if matched:
            out.extend(token_from_dict_index(phrase_to_index[matched]))
            cursor += len(matched)
            continue
        if text[cursor] == "<":
            tag = TAG_RE.match(text, cursor)
            if tag:
                out.extend(
                    encode_ko_text(
                        tag.group(0), tbl, hangul_marker_code=hangul_marker_code
                    )
                )
                cursor = tag.end()
                continue
        out.extend(
            encode_ko_text(
                text[cursor], tbl, hangul_marker_code=hangul_marker_code
            )
        )
        cursor += 1
    return bytes(out)


def encode_line_variants(
    text: str,
    tbl: Tbl,
    phrase_to_index: Dict[str, int] | None = None,
) -> Dict[str, bytes]:
    plain = encode_plaintext(text, tbl)
    variants = {"plain": plain}
    if phrase_to_index:
        variants["compressed"] = compress_with_phrases(text, tbl, phrase_to_index)
    return variants


def rebuild_bank_with_phrases(
    base_phrases: Sequence[bytes],
    *,
    data_start: int = DICT_DATA_START,
) -> Tuple[bytearray, List[int]]:
    """
    Pack phrases tightly from data_start, placing the pointer table at the end
    of the bank so phrase capacity is maximized.
    """
    count = len(base_phrases)
    ptr_bytes = count * 2
    ptr_start = BANK_SIZE - ptr_bytes
    if ptr_start < data_start:
        raise ValueError("Pointer table does not leave room for phrase data")
    return rebuild_dictionary(
        base_phrases,
        data_start=data_start,
        ptr_start=ptr_start,
    )


def patch_pointer_table_location_note(ptr_start: int) -> dict:
    """Metadata for consumers that assume stock DICT_PTR_START."""
    return {
        "stock_ptr_start": DICT_PTR_START,
        "rebuilt_ptr_start": ptr_start,
        "relocated": ptr_start != DICT_PTR_START,
        "warning": (
            "Pointer table relocated to maximize phrase space. "
            "Runtime code still reads stock 5F:7BCC unless a code patch is applied; "
            "expanded apply keeps stock pointer-table location by default."
        ),
    }


def write_dictionary_slots_spill(
    rom: bytearray,
    slot_payload: Dict[int, bytes],
    *,
    spill_start: int = 0x99BA,
    allow_aux_consumers: bool = False,
    locs: Dict[int, List[DictTokenRef]] | None = None,
) -> tuple[List[int], int]:
    """
    Write KO phrases into the FF padding after the stock pointer table and
    retarget only those slot pointers — same strategy as the working seed patch.

    Full dictionary rebuild relocates nearly every pointer and has been observed
    to skip/break New Game → opening flow even when phrase bytes round-trip.

    Hangul payloads on aux/name75-live slots are refused unless
    ``allow_aux_consumers=True``.
    """
    if not slot_payload:
        dictionary = Dictionary(rom)
        return list(dictionary.ptrs), spill_start

    guard_hangul_slot_writes(
        rom,
        slot_payload,
        allow_aux_consumers=allow_aux_consumers,
        locs=locs,
    )
    dictionary = Dictionary(rom)
    bank5f = bytearray(slice_bank(rom, SEG_DICT))
    ptrs = list(dictionary.ptrs)

    # Occupied phrase ranges for slots we are NOT retargeting — spill must
    # never overwrite those bytes (invasion into live dictionary data).
    occupied: list[tuple[int, int]] = []
    for i, p in enumerate(ptrs):
        if i in slot_payload or p == 0:
            continue
        end = p
        while end < BANK_SIZE and bank5f[end] != 0:
            end += 1
        end += 1  # terminator
        occupied.append((p, end))

    phrase_cursor = spill_start
    # Preserve any existing spill already used (e.g. prior seed write).
    for p in ptrs:
        if p >= spill_start:
            end = p
            while end < BANK_SIZE and bank5f[end] != 0:
                end += 1
            end += 1  # terminator
            phrase_cursor = max(phrase_cursor, end)

    def _overlaps_occupied(start: int, need: int) -> int | None:
        write_end = start + need
        for a, b in occupied:
            if start < b and write_end > a:
                return a
        return None

    for index, encoded in sorted(slot_payload.items()):
        need = len(encoded) + 1
        # Skip forward over any live phrase still parked in the spill zone.
        while True:
            if phrase_cursor + need > BANK_SIZE:
                raise RuntimeError(
                    f"Dictionary spill overflow writing slot {index} "
                    f"(need {phrase_cursor + need:#x})"
                )
            hit = _overlaps_occupied(phrase_cursor, need)
            if hit is None:
                break
            # Jump past the occupied range.
            for a, b in occupied:
                if a == hit:
                    phrase_cursor = max(phrase_cursor, b)
                    break
            else:
                phrase_cursor = hit + 1
        bank5f[phrase_cursor : phrase_cursor + len(encoded)] = encoded
        bank5f[phrase_cursor + len(encoded)] = 0
        ptrs[index] = phrase_cursor
        phrase_cursor += need

    for i, p in enumerate(ptrs):
        off = DICT_PTR_START + i * 2
        bank5f[off] = p & 0xFF
        bank5f[off + 1] = (p >> 8) & 0xFF
    patch_bank(rom, SEG_DICT, bank5f)
    return ptrs, phrase_cursor


def rebuild_bank_stock_layout(
    phrases: Sequence[bytes],
    *,
    count: int | None = None,
) -> Tuple[bytearray, List[int]]:
    """
    Rebuild phrases using the stock pointer-table location (5F:7BCC).

    Prefer write_dictionary_slots_spill for gameplay ROMs — full rebuild
    relocates pointers and can break New Game / opening sequencing.
    """
    if count is None:
        count = len(phrases)
    if len(phrases) != count:
        raise ValueError("phrases length must equal dictionary count")
    return rebuild_dictionary(
        phrases,
        data_start=DICT_DATA_START,
        ptr_start=DICT_PTR_START,
    )

