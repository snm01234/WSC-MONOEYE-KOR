#!/usr/bin/env python3
"""
Keep a record's original character *codes* when its text is re-encoded.

The problem
-----------
``monoeye.tbl`` maps several distinct ROM codes onto the same Unicode character,
because the extractor had no better label for them. ``Tbl.char_to_code`` is a
reverse map, so it silently collapses each group to the lowest code:

    '█' <- E6C5, E6C9, E736   ->  char_to_code['█'] == E6C5
    'ｅ' <- E5A1, E63B, E63E, E641, E72A, E730
    'Ｆ' <- E2B3, E721   ·   'Ｒ' <- E483, E63D   ·   '◎' <- E60B, E60D   (23 groups)

Anything that decodes a record to text and re-encodes it therefore *changes the
glyph* whenever the record used a non-lowest code. Measured on the tip: 181
name75 records carried a '█'-class icon code and 168 of them came back as E6C5 —
155 of those had been ``E736``. On screen the strengthened-unit marker next to the
unit name stopped being its own icon and became the E6C5 glyph.

The '█' group is not a rank/decoration character. It is the unit status icon
family the game draws right after the name in the unit list, which is why this
showed up as "the strengthen icon turned into a broken ＭＡ icon".

The fix
-------
Take the codes from the record being replaced, not from the reverse map. The
encoder in :mod:`normalize_ko_text` already understands ``<XXXX>`` escapes, so
:func:`retag_with_original_codes` rewrites the Korean text to pin each ambiguous
character to the code the original record used at the same occurrence index.

:func:`flatten_codes` expands dictionary tokens first, because a name75 payload
may reach the icon code through a stock dictionary phrase rather than inline.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

from monoeye_rom import (
    Dictionary,
    Tbl,
    dict_index_from_token,
    is_dict_token,
    is_kanji_lead,
)

#: How deep to follow nested dictionary phrases when flattening.
MAX_EXPAND_DEPTH = 4

#: The placeholder the extractor uses for the unit status icon family.
MARKER_CHAR = "\u2588"  # '█'

_TAG_RE = re.compile(r"<([0-9A-Fa-f]{4})>")


def ambiguous_chars(tbl: Tbl) -> Dict[str, Tuple[int, ...]]:
    """char -> every code that decodes to it, for chars with more than one."""
    rev: Dict[str, List[int]] = defaultdict(list)
    for code, ch in tbl.code_to_char.items():
        rev[ch].append(code)
    return {ch: tuple(sorted(v)) for ch, v in rev.items() if len(v) > 1}


def marker_codes(tbl: Tbl) -> frozenset[int]:
    """The '█'-class codes (unit status icons)."""
    return frozenset(
        code for code, ch in tbl.code_to_char.items() if ch == MARKER_CHAR
    )


def flatten_codes(
    payload: bytes, dic: Dictionary, *, depth: int = 0
) -> bytes:
    """Payload with dictionary tokens replaced by their phrase bytes."""
    out = bytearray()
    i = 0
    n = len(payload)
    while i < n:
        lead = payload[i]
        if is_dict_token(lead) and i + 1 < n and depth < MAX_EXPAND_DEPTH:
            index = dict_index_from_token(lead, payload[i + 1])
            try:
                sub = dic.raw_entry(index, max_len=128)
            except (IndexError, ValueError):
                sub = b""
            out += flatten_codes(sub, dic, depth=depth + 1)
            i += 2
        elif is_kanji_lead(lead) and i + 1 < n:
            out += payload[i : i + 2]
            i += 2
        else:
            out.append(lead)
            i += 1
    return bytes(out)


def find_codes(flat: bytes, wanted: Sequence[int] | frozenset[int]) -> List[int]:
    """Codes from ``wanted`` in the order they appear in ``flat``."""
    want = set(wanted)
    out: List[int] = []
    i = 0
    while i < len(flat) - 1:
        code = (flat[i] << 8) | flat[i + 1]
        if code in want:
            out.append(code)
            i += 2
            continue
        i += 1
    return out


def retag_with_original_codes(
    ko: str, flat: bytes, tbl: Tbl
) -> Tuple[str, List[dict]]:
    """
    Pin ambiguous characters in ``ko`` to the codes the original record used.

    The n-th occurrence of an ambiguous character in ``ko`` is bound to the n-th
    occurrence of that character's code group in the original. Occurrences beyond
    what the original had keep the tbl default. Returns the rewritten text plus
    one row per substitution for the report.
    """
    groups = ambiguous_chars(tbl)
    if not groups:
        return ko, []
    per_char: Dict[str, List[int]] = {}
    for ch, codes in groups.items():
        found = find_codes(flat, codes)
        if found:
            per_char[ch] = found
    if not per_char:
        return ko, []

    seen: Dict[str, int] = defaultdict(int)
    notes: List[dict] = []
    out: List[str] = []
    i = 0
    while i < len(ko):
        # Do not walk into an existing <XXXX> escape.
        m = _TAG_RE.match(ko, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        ch = ko[i]
        codes = per_char.get(ch)
        if codes is not None:
            k = seen[ch]
            seen[ch] += 1
            if k < len(codes):
                want = codes[k]
                default = tbl.char_to_code.get(ch)
                if want != default:
                    out.append(f"<{want:04X}>")
                    notes.append(
                        {
                            "char": ch,
                            "occurrence": k,
                            "code": f"{want:04X}",
                            "tbl_default": f"{default:04X}" if default else None,
                        }
                    )
                    i += 1
                    continue
        out.append(ch)
        i += 1
    return "".join(out), notes
