#!/usr/bin/env python3
"""Shared translation-scope rules for Mono-Eye script tooling.

The original script extractor walks NUL-delimited byte sequences.  Some ranges
inside the nominal script banks are actually event/graphics structures and must
never be sent to machine translation or a text applier.
"""
from __future__ import annotations

from typing import Iterable

# Bank 62 tail: event/graphics structure block.  Interior NULs make the generic
# extractor misread pointer fields as short Japanese-looking strings.
SCRIPT_GRAPHICS_BLOCKS: tuple[tuple[int, int], ...] = (
    (0x62D650, 0x630000),
)

# Fixed-stride data tables, already excluded by the unified build pipeline.
FIXED_DATA_BLOCKS: tuple[tuple[int, int], ...] = (
    (0x640000, 0x6A0000),
)


def in_ranges(abs_off: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(lo <= abs_off < hi for lo, hi in ranges)


def script_graphics_reason(abs_off: int) -> str | None:
    if in_ranges(abs_off, SCRIPT_GRAPHICS_BLOCKS):
        return "excluded_script_graphics_block"
    return None


def translation_exclusion_reason(abs_off: int) -> str | None:
    reason = script_graphics_reason(abs_off)
    if reason:
        return reason
    if in_ranges(abs_off, FIXED_DATA_BLOCKS):
        return "excluded_fixed_stride_data_block"
    return None


def is_translation_target(abs_off: int) -> bool:
    return translation_exclusion_reason(abs_off) is None


def formatted_ranges(ranges: Iterable[tuple[int, int]]) -> list[str]:
    return [f"{lo:06X}-{hi - 1:06X}" for lo, hi in ranges]
