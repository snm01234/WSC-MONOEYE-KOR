#!/usr/bin/env python3
"""Original+Working dictionary reference union and the mandatory Hangul guard.

Task 4.1 of the mixed Korean/Japanese residual localization spec.

Why a union and not "the tip's references"
------------------------------------------
A dictionary slot is safe to overwrite only if *every* record that can render it
is accounted for.  Two ROMs must be scanned for that statement to hold:

* the Original ROM shows the stock consumers, including records that the Korean
  pipeline has already rewritten (their old token is gone from the tip, but the
  slot is still shared stock data);
* the Working ROM shows the consumers introduced by localization so far.

Scanning only one of them is exactly the mistake that produced the historic
``ext_full_line_overshare`` / ``dict[21]`` residue: a slot looked sole-owned in
the tip while the Original still proved another consumer, or the reverse.

Scope: ``regions=("script", "name75", "aux")``
(:data:`expand_dictionary.DEFAULT_REF_REGIONS`) — script banks 60–6F including
the 64–69 data tables, the bank-75 name tables, and aux banks 50–5F plus 76,
including aux zstrings outside the vetted localization blocks.  Records excluded
from the *target* population stay consumers here on purpose.

Three reference classes are collected:

1. **external 2-byte tokens** — the Original uses
   :func:`expand_dictionary.build_dict_token_locs`; the Working ROM uses an
   ext3-aware walker when the ext3 hook is present.  This distinction is
   mandatory: treating ``E5 18 FE FB`` as a normal glyph pair and then scanning
   its tail would invent a false 2-byte ``FE FB``/``0EFB`` consumer;
2. **external ext3 tokens** — ``E5 18 xx yy`` and compact ``E5 19 xx`` portals.
   Only the Working ROM can hold these (the vanilla 8 MiB image has no ext3
   hook), so a "free" ext3 index is only free when no Working record points at
   it;
3. **nested references** — dictionary phrases that embed another slot's token.
   ``build_dict_token_locs`` deliberately does not follow these, so a slot with
   zero external consumers can still be live through a parent phrase.  A
   true-free claim must survive this check too.

Read-only: nothing here writes a ROM.  The write helpers take an in-memory
``bytearray`` scratch buffer supplied by the caller and route every Hangul
payload through :func:`expand_dictionary.guard_hangul_slot_writes` *before* the
underlying writer is reached.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import (  # noqa: E402
    load_ext_meta,
    make_dictionary,
    make_dictionary_ext3,
)
from apply_name75_ko import ext3_bank_room  # noqa: E402
from expand_dictionary import (  # noqa: E402
    AUX_TOKEN_BANKS,
    DEFAULT_REF_REGIONS,
    NAME75_RANGES,
    SCRIPT_TOKEN_BANKS,
    DictTokenRef,
    _walk_zstring_range,
    build_dict_token_locs,
    guard_hangul_slot_writes,
    payload_has_hangul_marker,
    slot_rewrite_refuse_reason,
)
from mixed_residual_models import DictionaryConsumer
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    EXT3_INDEX_BASE,
    dict_index_from_compact3_token,
    dict_index_from_token,
    is_compact3_magic,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    load_rom,
    stock_base,
)
from patch_3byte_dict_token import (  # noqa: E402
    DEFAULT_NUM_BANKS,
    INDEX_BASE,
    list_free_ext3_indices,
    write_ext3_dictionary_slots,
)

#: The only reference scope this feature is allowed to reason about.
UNION_REGIONS: tuple[str, ...] = tuple(DEFAULT_REF_REGIONS)

#: ``FF xx`` tokens land on this index page and collide with raw ``FF`` padding
#: bytes inside battle/UI banks.  Never a silent story-Hangul destination.
FF_PAGE_LO = 0xF00
FF_PAGE_HI = 0xFFF

STOCK_TOKEN_INDEX_MAX = 0xFFF

DEFAULT_ORIGINAL_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_WORKING_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/mixed_residual_reference_union.json"


class ReferenceUnionError(RuntimeError):
    """Raised when a union scan cannot be trusted."""


class SlotGuardRefusal(RuntimeError):
    """Raised when a dictionary write is refused by the invasion guard."""


def is_ff_page_index(index: int) -> bool:
    """True for indices reachable as a raw ``FF xx`` byte pair."""
    return FF_PAGE_LO <= index <= FF_PAGE_HI


def is_ext3_index(index: int) -> bool:
    return index >= EXT3_INDEX_BASE


def iter_token_refs_with_offsets(
    payload: bytes,
    *,
    ext3_aware: bool = True,
) -> Iterator[tuple[int, int, int]]:
    """Yield ``(dict_index, token_length, byte_offset)`` in runtime order.

    ``ext3_aware=False`` models the vanilla Original ROM, where ``E5 18`` and
    ``E5 19`` are ordinary two-byte glyph leads.  ``True`` models the patched
    Working ROM and consumes compact/ext3 portals before looking for a normal
    two-byte dictionary token.  The explicit offset is required by detachment
    proofs: a raw ``FC 91`` byte pair inside ``E5 18 FC 91`` must never be
    rewritten as a stock token occurrence.
    """
    cursor = 0
    size = len(payload)
    while cursor < size:
        lead = payload[cursor]
        if (
            ext3_aware
            and cursor + 2 < size
            and is_compact3_magic(lead, payload[cursor + 1])
        ):
            yield (
                dict_index_from_compact3_token(
                    lead, payload[cursor + 1], payload[cursor + 2]
                ),
                3,
                cursor,
            )
            cursor += 3
            continue
        if (
            ext3_aware
            and cursor + 3 < size
            and is_ext3_magic(lead, payload[cursor + 1])
        ):
            yield (
                EXT3_INDEX_BASE + ((payload[cursor + 2] << 8) | payload[cursor + 3]),
                4,
                cursor,
            )
            cursor += 4
            continue
        if is_dict_token(lead) and cursor + 1 < size:
            yield dict_index_from_token(lead, payload[cursor + 1]), 2, cursor
            cursor += 2
            continue
        if is_kanji_lead(lead) and cursor + 1 < size:
            cursor += 2
            continue
        cursor += 1


def iter_token_refs(payload: bytes) -> Iterator[tuple[int, int]]:
    """Yield ``(dict_index, token_length)`` using patched runtime precedence."""
    for index, length, _offset in iter_token_refs_with_offsets(payload):
        yield index, length


# Bank-75 contains a sequential UI/stage-name zstring table below the legacy
# NAME75_RANGES.  It includes shared dictionary consumers such as the three
# intermission placement tokens at 75:B4A5/B4AB/B860 and the four stage names
# at 75:BD62/BD6C/BD9A/BDEB.  Excluding it caused slot 0021 (배치) and later
# slot 0208 (공역) to be falsely classified as detached after only script/aux
# consumers were retargeted.
NAME75_UI_TABLE_RANGES: tuple[tuple[int, int], ...] = ((0x75B000, 0x75C000),)


def _reference_scopes() -> list[tuple[str, int, int, int]]:
    """``(region, logical_start, logical_end, max_len)`` for every known
    runtime zstring table that may consume dictionary tokens."""
    scopes: list[tuple[str, int, int, int]] = []
    scopes.extend(
        ("script", bank * BANK_SIZE, (bank + 1) * BANK_SIZE, 256)
        for bank in SCRIPT_TOKEN_BANKS
    )
    scopes.extend(("name75", lo, hi, 64) for lo, hi in NAME75_RANGES)
    scopes.extend(("name75", lo, hi, 64) for lo, hi in NAME75_UI_TABLE_RANGES)
    scopes.extend(
        ("aux", bank * BANK_SIZE, (bank + 1) * BANK_SIZE, 128)
        for bank in AUX_TOKEN_BANKS
    )
    return scopes


def _external_refs_by_length(
    rom: bytes,
    *,
    regions: Sequence[str] = UNION_REGIONS,
    token_lengths: frozenset[int],
) -> dict[int, list[DictTokenRef]]:
    """External references parsed with the ext3-aware runtime precedence.

    This walker is for a Working ROM whose ``E5 18``/``E5 19`` hook is active.
    The vanilla Original must keep using :func:`build_dict_token_locs`, because
    there those byte pairs are ordinary two-byte glyphs rather than portals.
    """
    want = set(regions)
    out: dict[int, list[DictTokenRef]] = {}
    for region, lo, hi, max_len in _reference_scopes():
        if region not in want:
            continue
        for logical, payload, kind in _walk_zstring_range(
            rom, lo, hi, region=region, max_len=max_len
        ):
            for index, token_len in iter_token_refs(payload):
                if token_len not in token_lengths:
                    continue
                out.setdefault(index, []).append(
                    DictTokenRef(abs=logical, region=region, kind=kind)
                )
    return out


def _working_two_byte_external_refs(
    rom: bytes, *, regions: Sequence[str] = UNION_REGIONS
) -> dict[int, list[DictTokenRef]]:
    """Working-ROM 2-byte refs without ext3-tail false positives."""
    return _external_refs_by_length(
        rom, regions=regions, token_lengths=frozenset((2,))
    )


def _ext3_external_refs(
    rom: bytes, *, regions: Sequence[str] = UNION_REGIONS
) -> dict[int, list[DictTokenRef]]:
    """External ext3 portals (E5 18 and compact E5 19), region by region."""
    return _external_refs_by_length(
        rom, regions=regions, token_lengths=frozenset((3, 4))
    )


def _nested_parents(dictionary: Dictionary) -> dict[int, set[int]]:
    """``child_index -> {parent_index}`` for tokens embedded in phrases."""
    nested: dict[int, set[int]] = {}
    for parent in range(dictionary.count):
        try:
            raw = dictionary.raw_entry(parent)
        except Exception:  # unreadable slot: cannot prove anything about it
            continue
        if not raw:
            continue
        for child, _length in iter_token_refs(raw):
            if child == parent:
                continue
            nested.setdefault(child, set()).add(parent)
    return nested


@dataclass(frozen=True)
class ReferenceUnion:
    """Deduplicated Original+Working consumer map for every dictionary index."""

    consumers: Mapping[int, tuple[DictionaryConsumer, ...]]
    nested_parents: Mapping[int, frozenset[int]]
    original_sha256: str
    working_sha256: str
    regions: tuple[str, ...] = UNION_REGIONS
    ext3_scanned: bool = True
    working_two_byte_ext3_aware: bool = False
    _locs: Mapping[int, list[DictTokenRef]] = field(default_factory=dict, repr=False)

    # ---------------------------------------------------------------- queries
    def consumers_for(self, index: int) -> tuple[DictionaryConsumer, ...]:
        return self.consumers.get(int(index), ())

    def script_consumers(self, index: int) -> tuple[DictionaryConsumer, ...]:
        return tuple(c for c in self.consumers_for(index) if c.region == "script")

    def aux_or_name75_consumers(self, index: int) -> tuple[DictionaryConsumer, ...]:
        return tuple(c for c in self.consumers_for(index) if c.region != "script")

    def parents_of(self, index: int) -> frozenset[int]:
        return self.nested_parents.get(int(index), frozenset())

    def region_counts(self, index: int) -> dict[str, int]:
        counts = {"script": 0, "name75": 0, "aux": 0}
        for consumer in self.consumers_for(index):
            counts[consumer.region] += 1
        return counts

    def as_locs(self) -> dict[int, list[DictTokenRef]]:
        """The merged map in :mod:`expand_dictionary` form.

        Passed straight to ``guard_hangul_slot_writes(locs=...)`` and
        ``slot_rewrite_refuse_reason`` so the guard judges the union, not just
        the ROM being written.
        """
        return dict(self._locs)

    # ------------------------------------------------------------- decisions
    def is_true_free(self, index: int) -> bool:
        """No external consumer in either ROM and no nested parent phrase."""
        return not self.consumers_for(index) and not self.parents_of(index)

    def refuse_reason(
        self,
        index: int,
        *,
        keeper_abs: set[int] | None = None,
        require_free: bool = False,
    ) -> str | None:
        """Fail-closed refusal string, or ``None`` when the write is allowed.

        Wraps :func:`expand_dictionary.slot_rewrite_refuse_reason` on the union
        and adds the nested-parent check that the canonical helper cannot make.
        """
        index = int(index)
        reason = slot_rewrite_refuse_reason(
            self.as_locs(),
            index,
            keeper_abs=keeper_abs,
            require_free=require_free,
        )
        if reason is not None:
            return reason
        parents = self.parents_of(index)
        if parents and require_free:
            sample = sorted(parents)[:6]
            return (
                f"slot {index} is nested inside dictionary phrase(s) "
                f"{[f'{p:04X}' for p in sample]}"
            )
        return None

    def audit(self, index: int) -> dict[str, Any]:
        """Report row for ``dictionary_changes`` (requirement 4.15)."""
        index = int(index)
        consumers = self.consumers_for(index)
        return {
            "index": index,
            "index_hex": f"{index:04X}",
            "ff_page": is_ff_page_index(index),
            "ext3": is_ext3_index(index),
            "consumer_count": len(consumers),
            "region_counts": self.region_counts(index),
            "seen_in": sorted({item for c in consumers for item in c.seen_in}),
            "consumers": [
                {
                    "abs": f"{c.abs:06X}",
                    "region": c.region,
                    "kind": c.kind,
                    "seen_in": sorted(c.seen_in),
                }
                for c in sorted(consumers, key=lambda c: (c.region, c.abs))[:40]
            ],
            "nested_parents": [f"{p:04X}" for p in sorted(self.parents_of(index))[:12]],
            "true_free": self.is_true_free(index),
        }

    def summary(self) -> dict[str, Any]:
        by_region = {"script": 0, "name75": 0, "aux": 0}
        by_seen = {"original": 0, "working": 0, "both": 0}
        for consumers in self.consumers.values():
            for consumer in consumers:
                by_region[consumer.region] += 1
                if consumer.seen_in == frozenset({"original", "working"}):
                    by_seen["both"] += 1
                elif "original" in consumer.seen_in:
                    by_seen["original"] += 1
                else:
                    by_seen["working"] += 1
        return {
            "regions": list(self.regions),
            "ext3_scanned": self.ext3_scanned,
            "working_two_byte_ext3_aware": self.working_two_byte_ext3_aware,
            "indices_with_consumers": len(self.consumers),
            "consumer_refs": sum(len(v) for v in self.consumers.values()),
            "refs_by_region": by_region,
            "refs_by_seen_in": by_seen,
            "indices_with_nested_parents": len(self.nested_parents),
            "original_rom_sha256": self.original_sha256,
            "working_rom_sha256": self.working_sha256,
        }


def _merge(
    target: dict[tuple[int, int, str, str], set[str]],
    locs: Mapping[int, list[DictTokenRef]],
    source: str,
) -> None:
    for index, refs in locs.items():
        for ref in refs:
            target.setdefault((int(index), ref.abs, ref.region, ref.kind), set()).add(
                source
            )


def build_reference_union(
    original_rom: bytes,
    working_rom: bytes,
    *,
    regions: Sequence[str] = UNION_REGIONS,
    ext_meta: Mapping[str, Any] | None = None,
    ext3_meta: Mapping[str, Any] | None = None,
    scan_ext3: bool = True,
    scan_nested: bool = True,
) -> ReferenceUnion:
    """Scan both ROMs and return the deduplicated consumer union.

    ``regions`` must keep the full :data:`UNION_REGIONS` scope for any decision
    that overwrites shared data; a narrower scope is accepted only so tests can
    build small fixtures.
    """
    if not original_rom or not working_rom:
        raise ReferenceUnionError("both ROM images are required")

    merged: dict[tuple[int, int, str, str], set[str]] = {}
    _merge(merged, build_dict_token_locs(original_rom, regions=tuple(regions)), "original")

    # ``build_dict_token_locs`` is correct for the vanilla Original, but not for
    # an ext3-enabled Working ROM: it consumes E5 18 as a two-byte glyph and can
    # then reinterpret the portal tail as a second 2-byte token.  Infer the hook
    # from explicit ext3 metadata; callers making a safety decision already pass
    # this metadata, while tiny unit fixtures without it retain the legacy path.
    working_ext3_aware = bool(
        scan_ext3
        and ext3_meta is not None
        and int((ext3_meta or {}).get("num_banks") or 0) > 0
    )
    if working_ext3_aware:
        _merge(
            merged,
            _working_two_byte_external_refs(working_rom, regions=regions),
            "working",
        )
    else:
        _merge(
            merged,
            build_dict_token_locs(working_rom, regions=tuple(regions)),
            "working",
        )
    if scan_ext3:
        # The vanilla image has no ext3 hook, so only the Working ROM can hold
        # ``E5 18``/``E5 19`` portals.
        _merge(merged, _ext3_external_refs(working_rom, regions=regions), "working")

    consumers: dict[int, list[DictionaryConsumer]] = {}
    locs: dict[int, list[DictTokenRef]] = {}
    for (index, abs_off, region, kind), seen in merged.items():
        consumers.setdefault(index, []).append(
            DictionaryConsumer(
                index=index,
                abs=abs_off,
                region=region,  # type: ignore[arg-type]
                kind=kind,
                seen_in=frozenset(seen),
            )
        )
        locs.setdefault(index, []).append(
            DictTokenRef(abs=abs_off, region=region, kind=kind)
        )

    nested: dict[int, frozenset[int]] = {}
    if scan_nested:
        raw_nested: dict[int, set[int]] = {}
        dictionaries: list[Dictionary] = [Dictionary(original_rom)]
        if ext_meta is not None:
            dictionaries.append(
                make_dictionary_ext3(working_rom, dict(ext_meta), dict(ext3_meta or {}))
            )
        else:
            dictionaries.append(Dictionary(working_rom))
        for dictionary in dictionaries:
            for child, parents in _nested_parents(dictionary).items():
                raw_nested.setdefault(child, set()).update(parents)
        nested = {child: frozenset(parents) for child, parents in raw_nested.items()}

    return ReferenceUnion(
        consumers={
            index: tuple(sorted(items, key=lambda c: (c.region, c.abs)))
            for index, items in consumers.items()
        },
        nested_parents=nested,
        original_sha256=hashlib.sha256(original_rom).hexdigest(),
        working_sha256=hashlib.sha256(working_rom).hexdigest(),
        regions=tuple(regions),
        ext3_scanned=bool(scan_ext3),
        working_two_byte_ext3_aware=working_ext3_aware,
        _locs=locs,
    )


# --------------------------------------------------------------------------- #
# mandatory guard path
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GuardOutcome:
    """Result of the mandatory Hangul-slot guard for one write batch."""

    ok: bool
    outcome: str
    slots: tuple[int, ...]
    hangul_slots: tuple[int, ...]
    blocked: Mapping[int, tuple[str, ...]]
    allow_aux_consumers: bool
    justification: str | None
    refuse_reasons: Mapping[int, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "outcome": self.outcome,
            "slots": [f"{s:04X}" for s in self.slots],
            "hangul_slots": [f"{s:04X}" for s in self.hangul_slots],
            "blocked": {
                f"{index:04X}": list(sites) for index, sites in self.blocked.items()
            },
            "allow_aux_consumers": self.allow_aux_consumers,
            "justification": self.justification,
            "refuse_reasons": {
                f"{index:04X}": reason for index, reason in self.refuse_reasons.items()
            },
        }


def guard_slot_writes(
    rom: bytes | bytearray,
    slot_payload: Mapping[int, bytes],
    *,
    union: ReferenceUnion,
    keeper_abs: Mapping[int, set[int]] | None = None,
    require_free: bool = False,
    allow_aux_consumers: bool = False,
    justification: str | None = None,
) -> GuardOutcome:
    """Run the mandatory guard for a batch of dictionary payload writes.

    Order matters and is the point of this function: the canonical
    :func:`guard_hangul_slot_writes` runs first, on the *union* locs, and only
    then are the per-slot rewrite refusals evaluated.  Callers must not reach a
    writer without a returned ``ok`` outcome.

    ``allow_aux_consumers=True`` requires a non-empty ``justification`` and is
    reserved for UI/repair or a fully accounted curated pair-steal migration.
    """
    slots = tuple(sorted(int(i) for i in slot_payload))
    hangul = tuple(
        sorted(int(i) for i, blob in slot_payload.items() if payload_has_hangul_marker(blob))
    )
    if allow_aux_consumers and not (justification or "").strip():
        raise SlotGuardRefusal(
            "allow_aux_consumers=True requires an explicit justification"
        )

    locs = union.as_locs()
    blocked: dict[int, tuple[str, ...]] = {}
    for index in hangul:
        sites = tuple(
            f"{c.abs:06X}/{c.region}" for c in union.aux_or_name75_consumers(index)
        )
        if sites:
            blocked[index] = sites[:6]

    try:
        guard_hangul_slot_writes(
            rom,
            dict(slot_payload),
            allow_aux_consumers=allow_aux_consumers,
            locs=locs,
        )
    except RuntimeError as exc:
        return GuardOutcome(
            ok=False,
            outcome=f"refused_by_guard_hangul_slot_writes: {exc}",
            slots=slots,
            hangul_slots=hangul,
            blocked=blocked,
            allow_aux_consumers=allow_aux_consumers,
            justification=justification,
        )

    refusals: dict[int, str] = {}
    for index in slots:
        reason = union.refuse_reason(
            index,
            keeper_abs=(keeper_abs or {}).get(index),
            require_free=require_free,
        )
        if reason is not None:
            refusals[index] = reason
    if refusals and not allow_aux_consumers:
        return GuardOutcome(
            ok=False,
            outcome="refused_by_slot_rewrite_refuse_reason",
            slots=slots,
            hangul_slots=hangul,
            blocked=blocked,
            allow_aux_consumers=allow_aux_consumers,
            justification=justification,
            refuse_reasons=refusals,
        )

    outcome = "guard_passed"
    if allow_aux_consumers:
        outcome = "guard_passed_with_aux_allowance"
    return GuardOutcome(
        ok=True,
        outcome=outcome,
        slots=slots,
        hangul_slots=hangul,
        blocked=blocked,
        allow_aux_consumers=allow_aux_consumers,
        justification=justification,
        refuse_reasons=refusals,
    )


def write_ext3_slots_guarded(
    scratch: bytearray,
    slot_payload: Mapping[int, bytes],
    *,
    union: ReferenceUnion,
    num_banks: int = DEFAULT_NUM_BANKS,
    allow_aux_consumers: bool = False,
    justification: str | None = None,
) -> tuple[dict[str, Any], GuardOutcome]:
    """Guarded :func:`patch_3byte_dict_token.write_ext3_dictionary_slots`.

    ext3 indices are private to this feature's allocation, but the guard still
    runs: a *phantom* consumer (an ``E5 18 xx yy`` byte pattern that already
    exists somewhere) must be able to refuse the write.
    """
    outcome = guard_slot_writes(
        scratch,
        slot_payload,
        union=union,
        require_free=True,
        allow_aux_consumers=allow_aux_consumers,
        justification=justification,
    )
    if not outcome.ok:
        raise SlotGuardRefusal(outcome.outcome)
    info = write_ext3_dictionary_slots(scratch, dict(slot_payload), num_banks=num_banks)
    if info.get("skipped_overflow"):
        raise SlotGuardRefusal(
            f"ext3 phrase overflow: {info['skipped_overflow']} slot(s) did not fit"
        )
    return info, outcome


# --------------------------------------------------------------------------- #
# free-slot inventory
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FreeSlotInventory:
    """True-free 2-byte and ext3 slots, split by hazard class."""

    stock_free: tuple[int, ...]
    ext_free: tuple[int, ...]
    ext_free_ff_page: tuple[int, ...]
    ext3_free: tuple[int, ...]
    ext3_bank_room: Mapping[int, int]
    stock_count: int
    ext_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "stock_count": self.stock_count,
            "ext_count": self.ext_count,
            "stock_free": len(self.stock_free),
            "ext_free": len(self.ext_free),
            "ext_free_ff_page": len(self.ext_free_ff_page),
            "ext_free_non_ff_page": len(self.ext_free) - len(self.ext_free_ff_page),
            "ext3_free": len(self.ext3_free),
            "ext3_bank_room": {f"{0x11 + b:02X}": r for b, r in sorted(self.ext3_bank_room.items())},
            "ext3_bank_room_total": sum(self.ext3_bank_room.values()),
            "stock_free_sample": [f"{i:04X}" for i in self.stock_free[:20]],
            "ext_free_sample": [f"{i:04X}" for i in self.ext_free[:20]],
        }


def build_free_slot_inventory(
    working_rom: bytes,
    *,
    union: ReferenceUnion,
    ext_meta: Mapping[str, Any],
    ext3_meta: Mapping[str, Any],
) -> FreeSlotInventory:
    """Two-byte and ext3 indices that the union proves nobody can render.

    A 2-byte slot qualifies only when the union has no consumer *and* no nested
    parent phrase.  Payload emptiness is not required (an unreferenced phrase is
    dead data), but the record must be provably unreachable.
    """
    dictionary = make_dictionary(working_rom, dict(ext_meta))
    stock_count = int(ext_meta.get("stock_count", dictionary.stock_count))
    total = dictionary.count

    stock_free: list[int] = []
    ext_free: list[int] = []
    for index in range(min(total, STOCK_TOKEN_INDEX_MAX + 1)):
        if not union.is_true_free(index):
            continue
        if index < stock_count:
            stock_free.append(index)
        else:
            ext_free.append(index)

    num_banks = int(ext3_meta.get("num_banks") or DEFAULT_NUM_BANKS)
    ext3_free = [
        index
        for index in list_free_ext3_indices(working_rom, num_banks=num_banks)
        if union.is_true_free(index)
    ]
    return FreeSlotInventory(
        stock_free=tuple(stock_free),
        ext_free=tuple(ext_free),
        ext_free_ff_page=tuple(i for i in ext_free if is_ff_page_index(i)),
        ext3_free=tuple(ext3_free),
        ext3_bank_room=ext3_bank_room(working_rom, num_banks),
        stock_count=stock_count,
        ext_count=max(0, min(total, STOCK_TOKEN_INDEX_MAX + 1) - stock_count),
    )


# --------------------------------------------------------------------------- #
# storage-capacity scope evaluator
# --------------------------------------------------------------------------- #

#: 4-byte ``E5 18 xx yy`` portal; a shorter body cannot hold it.
EXT3_TOKEN_LEN = 4
#: Two-byte token indices live in 0x000–0xFFF and that space is fully allocated
#: (stock 3831 + ext 265 = 4096).
TWO_BYTE_INDEX_MAX = 0xFFF


def make_shared_token_short_record_reason(
    original_rom: bytes,
    *,
    union: ReferenceUnion,
    reason: str = "excluded_shared_token_body_capacity",
    two_byte_free: Iterable[int] = (),
):
    """Build the discovery scope evaluator for Shared_Token_Short_Records.

    A record is out of target scope when all three hold:

    1. the body left after the Original-derived prefix is shorter than the ext3
       portal, so the proven in-place rewrite cannot be used;
    2. no 2-byte index is a True_Free_Slot in the Original+Working union, so the
       record cannot be retargeted to a private slot; and
    3. the slot the Original body already points at is shared with aux/name75
       consumers, or cannot be identified as a single token at all.

    Every returned reason carries the measured capacity and the blocking slot so
    the manifest row is self-explaining.
    """
    free_two_byte = sorted(
        index for index in two_byte_free if index <= TWO_BYTE_INDEX_MAX
    )
    stock_offset = stock_base(original_rom)

    def evaluate(record) -> str | None:
        capacity = record.boundary.payload_capacity
        prefix = len(record.prefix_bytes)
        body_span = capacity - prefix
        if body_span >= EXT3_TOKEN_LEN:
            return None
        if free_two_byte:
            # A private 2-byte slot exists, so this record is plannable and the
            # planner, not discovery, owns the decision.
            return None
        start = stock_offset + record.boundary.start
        payload = bytes(original_rom[start : start + capacity])
        body = payload[prefix:]
        indices = [index for index, length in iter_token_refs(body) if length == 2]
        if len(indices) != 1:
            return f"{reason}:body={body_span},slot=none"
        index = indices[0]
        shared = union.aux_or_name75_consumers(index)
        detail = f"{reason}:body={body_span},slot={index:04X}"
        if shared:
            return f"{detail},aux_or_name75_consumers={len(shared)}"
        return f"{detail},two_byte_index_space_saturated"

    return evaluate


# --------------------------------------------------------------------------- #
# CLI (read-only)
# --------------------------------------------------------------------------- #


def _identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {
        "path": str(path.resolve()),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    ap.add_argument("--working-rom", type=Path, default=DEFAULT_WORKING_ROM)
    ap.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="optional target manifest; adds a per-target storage feasibility count",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return ap


def _manifest_feasibility(manifest_path: Path) -> dict[str, Any]:
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    included = document.get("population", {}).get("included") or []
    rows: dict[str, dict[str, int]] = {}
    for row in included:
        region = row.get("region", "?")
        boundary = row.get("boundary") or {}
        capacity = int(boundary.get("payload_capacity") or 0)
        prefix = len(str(row.get("prefix_hex") or "")) // 2
        body = capacity - prefix
        bucket = rows.setdefault(
            region, {"targets": 0, "ext3_token_fits": 0, "two_byte_token_only": 0, "too_short": 0}
        )
        bucket["targets"] += 1
        if body >= 4:
            bucket["ext3_token_fits"] += 1
        elif body >= 2:
            bucket["two_byte_token_only"] += 1
        else:
            bucket["too_short"] += 1
    return {
        "manifest": str(manifest_path),
        "manifest_sha256": document.get("manifest_sha256"),
        "included": len(included),
        "by_region": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this tool is read-only")

    original = bytes(load_rom(args.original_rom))
    working = bytes(load_rom(args.working_rom))
    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)

    started = time.time()
    union = build_reference_union(
        original,
        working,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    inventory = build_free_slot_inventory(
        working, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta
    )
    report: dict[str, Any] = {
        "generated_by": "tools/mixed_residual_reference_union.py",
        "read_only": True,
        "inputs": {
            "original_rom": _identity(args.original_rom, original),
            "working_rom": _identity(args.working_rom, working),
            "ext_meta": _identity(args.ext_meta),
            "ext3_meta": _identity(args.ext3_meta),
        },
        "union": union.summary(),
        "free_slots": inventory.as_dict(),
        "elapsed_seconds": round(time.time() - started, 2),
    }
    if args.manifest is not None:
        report["target_feasibility"] = _manifest_feasibility(args.manifest)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "FF_PAGE_HI",
    "FF_PAGE_LO",
    "FreeSlotInventory",
    "GuardOutcome",
    "ReferenceUnion",
    "ReferenceUnionError",
    "SlotGuardRefusal",
    "UNION_REGIONS",
    "build_free_slot_inventory",
    "build_reference_union",
    "guard_slot_writes",
    "is_ext3_index",
    "is_ff_page_index",
    "iter_token_refs",
    "_working_two_byte_external_refs",
    "make_shared_token_short_record_reason",
    "write_ext3_slots_guarded",
]


if __name__ == "__main__":
    raise SystemExit(main())
