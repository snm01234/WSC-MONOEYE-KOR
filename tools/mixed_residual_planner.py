#!/usr/bin/env python3
"""ext3-first, fail-closed rewrite planning for residual localization targets.

Tasks 4.2, 4.3 and 4.4 of the mixed Korean/Japanese residual localization spec.
This module is **pure planning**: it reads ROMs and evidence, and returns a
:class:`LocalizationPlan`.  It never writes a ROM.  Only
``mixed_residual_transaction`` may apply a plan, and only to an in-memory copy.

Storage strategy precedence (design.md §4.5, requirement 4.7)
------------------------------------------------------------
1. ``ext3`` — *Verified_Ext3_Record_Rewrite*.  The Korean sentence goes into a
   free ext3 phrase slot and the record body becomes ``E5 18 xx yy`` plus
   ``0x01`` padding.  No stock or ext dictionary meaning changes, so no shared
   consumer can be disturbed.  Needs a 4-byte body.
2. ``true_free`` — a 2-byte index that the Original+Working reference union
   proves nobody can render (no external consumer, no nested parent phrase).
   Used for records whose body cannot hold a 4-byte token.
3. ``pair_steal`` — only from a reviewed S/T pair manifest.  T must be
   true-free and must first receive S's old payload; every former consumer of S
   must be an explicit keeper or be retargeted to T.  Nothing is auto-selected
   from a pool.

If none applies the target is ``unresolved`` with a machine-readable reason, and
a plan with any unresolved target cannot be statically accepted (requirement
2.7/2.8).

Protections that are not optional
---------------------------------
* Every dictionary write goes through
  :func:`mixed_residual_reference_union.guard_slot_writes` before a writer is
  reachable, and the guard sees the *union*, not one ROM.
* ``0x6040A5``–``0x607000`` script consumers are always keepers: a shared-slot
  plan that would detach an early-band consumer is refused instead of "fixed".
* ``FF xx`` indices (``0xF00``–``0xFFF``) are refused for story Hangul whenever
  the union shows any aux/name75 consumer, because those bytes also occur raw
  inside battle/UI zstrings.
* Record structure is Original-derived: prefix bytes, capacity, terminator
  offset and the next record start come from the Original ROM and are re-proved
  here, never recomputed from the Working ROM.
* Remainder padding is ``0x01`` (ideographic space).  A new ``0x00`` inside a
  record extent is refused, and so is padding a record with a shared fragment
  token (requirement 4.14): the only tokens a planned body may contain are the
  one that renders this target's own Korean text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import (  # noqa: E402
    load_ext_meta,
    make_dictionary,
    make_dictionary_ext3,
)
from apply_name75_ko import ext3_bank_room  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from measure_aux_prefix_rule import BANK_RULES, prefix_len as rule_prefix_len  # noqa: E402
from mixed_residual_records import NON_DIALOGUE_SCRIPT_BANKS  # noqa: E402
from mixed_residual_models import deterministic_json_sha256  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    ReferenceUnion,
    build_reference_union,
    guard_slot_writes,
    is_ff_page_index,
    iter_token_refs,
)
from mixed_residual_translations import (  # noqa: E402
    TranslationValidation,
    validate_catalog_files,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    EXT3_INDEX_BASE,
    MAX_SAFE_RECORD_LEN,
    Dictionary,
    Tbl,
    is_compact3_magic,
    is_ext3_magic,
    load_rom,
    stock_base,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402
from patch_3byte_dict_token import (  # noqa: E402
    DEFAULT_NUM_BANKS,
    EXP3_SEG0,
    EXP3_SLOTS,
    INDEX_BASE,
    list_free_ext3_indices,
    token_from_ext3_index,
)

SCHEMA_VERSION = 1
GENERATED_BY = "tools/mixed_residual_planner.py"

EXT3_TOKEN_LEN = 4
TWO_BYTE_TOKEN_LEN = 2
PAD_BYTE = 0x01
#: ``apply_safe_unit.padded_token_payload`` refuses a larger remainder; keeping
#: the same ceiling keeps this planner and the proven writer in agreement.
MAX_PAD_BYTES = 64
EARLY_BAND_LO = 0x6040A5
EARLY_BAND_HI = 0x607000

DEFAULT_ORIGINAL_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_WORKING_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_MANIFEST = ROOT / "out/patch/mixed_residual_target_manifest.json"
DEFAULT_TRANSLATIONS = ROOT / "data/mixed_residual_translations.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_PLAN = ROOT / "out/patch/mixed_residual_plan.json"


class PlanningError(RuntimeError):
    """Raised when planning inputs cannot be trusted at all."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def in_early_band(logical: int) -> bool:
    return EARLY_BAND_LO <= logical <= EARLY_BAND_HI


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TargetPlan:
    """One target's decision: resolved with a strategy, or unresolved."""

    record_id: str
    region: str
    logical_address: int
    bank: int
    payload_capacity: int
    prefix_bytes: int
    terminator_offset: int
    next_record_start: int | None
    source_text: str
    korean_text: str
    status: str
    reason: str
    strategy: str | None = None
    dict_index: int | None = None
    token_hex: str = ""
    new_body_hex: str = ""
    pad_bytes: int = 0
    phrase_len: int = 0
    phrase_sha256: str = ""
    annotations: tuple[str, ...] = ()
    prefix_hex: str = ""
    prefix_source: str = "rule"

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"

    def body_file_extent(self, stock_base_offset: int) -> tuple[int, int]:
        start = stock_base_offset + self.logical_address + self.prefix_bytes
        return start, stock_base_offset + self.logical_address + self.payload_capacity

    def to_json_data(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "region": self.region,
            "abs": f"{self.logical_address:06X}",
            "bank": f"{self.bank:02X}",
            "payload_capacity": self.payload_capacity,
            "prefix_bytes": self.prefix_bytes,
            "prefix_hex": self.prefix_hex,
            "prefix_source": self.prefix_source,
            "terminator_offset": f"{self.terminator_offset:06X}",
            "next_record_start": (
                None if self.next_record_start is None else f"{self.next_record_start:06X}"
            ),
            "source_text": self.source_text,
            "korean_text": self.korean_text,
            "status": self.status,
            "reason": self.reason,
            "strategy": self.strategy,
            "dict_index": None if self.dict_index is None else f"{self.dict_index:04X}",
            "token_hex": self.token_hex,
            "new_body_hex": self.new_body_hex,
            "pad_bytes": self.pad_bytes,
            "phrase_len": self.phrase_len,
            "phrase_sha256": self.phrase_sha256,
            "annotations": list(self.annotations),
        }


@dataclass(frozen=True)
class SlotPlan:
    """One dictionary slot the plan will write, with its full audit."""

    index: int
    strategy: str
    token_hex: str
    payload_len: int
    payload_sha256: str
    write_required: bool
    ff_page: bool
    keepers: tuple[int, ...]
    restore_or_retarget: tuple[int, ...]
    preserve_slot: int | None
    guard_outcome: str
    union_audit: Mapping[str, Any]
    targets: tuple[str, ...]
    bank: int | None = None

    def to_json_data(self) -> dict[str, Any]:
        return {
            "index": f"{self.index:04X}",
            "index_decimal": self.index,
            "strategy": self.strategy,
            "token_hex": self.token_hex,
            "payload_len": self.payload_len,
            "payload_sha256": self.payload_sha256,
            "write_required": self.write_required,
            "ff_page": self.ff_page,
            "bank": None if self.bank is None else f"{self.bank:02X}",
            "keepers": [f"{a:06X}" for a in self.keepers],
            "restore_or_retarget": [f"{a:06X}" for a in self.restore_or_retarget],
            "preserve_slot": (
                None if self.preserve_slot is None else f"{self.preserve_slot:04X}"
            ),
            "guard_outcome": self.guard_outcome,
            "union": dict(self.union_audit),
            "targets": list(self.targets),
        }


@dataclass(frozen=True)
class Ext3ReclaimPlan:
    """Provably unreferenced ext3 phrase space to repack, per bank.

    A phrase is reclaimable only when its ``E5 18 xx yy`` token appears nowhere
    in the whole ROM image *and* the reference union has no consumer for the
    index.  Live phrases in the same bank are relocated byte-for-byte; the
    transaction verifies every one of them after the repack.
    """

    banks: tuple[int, ...]
    dead_indices: Mapping[int, tuple[int, ...]]
    live_indices: Mapping[int, tuple[int, ...]]
    freed_bytes: Mapping[int, int]
    room_after: Mapping[int, int]
    proof: str

    def to_json_data(self) -> dict[str, Any]:
        return {
            "banks": [f"{EXP3_SEG0 + b:02X}" for b in self.banks],
            "dead_slot_counts": {
                f"{EXP3_SEG0 + b:02X}": len(v) for b, v in sorted(self.dead_indices.items())
            },
            "live_slot_counts": {
                f"{EXP3_SEG0 + b:02X}": len(v) for b, v in sorted(self.live_indices.items())
            },
            "freed_bytes": {
                f"{EXP3_SEG0 + b:02X}": v for b, v in sorted(self.freed_bytes.items())
            },
            "freed_bytes_total": sum(self.freed_bytes.values()),
            "room_after": {
                f"{EXP3_SEG0 + b:02X}": v for b, v in sorted(self.room_after.items())
            },
            "proof": self.proof,
        }


@dataclass(frozen=True)
class LocalizationPlan:
    """Everything the transaction needs, plus everything the report must show."""

    manifest_sha256: str
    inputs: Mapping[str, Any]
    targets: tuple[TargetPlan, ...]
    slots: tuple[SlotPlan, ...]
    reclaim: Ext3ReclaimPlan | None
    ext3_num_banks: int
    counts: Mapping[str, Any]
    guard_outcomes: tuple[Mapping[str, Any], ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def resolved(self) -> tuple[TargetPlan, ...]:
        return tuple(t for t in self.targets if t.resolved)

    @property
    def unresolved(self) -> tuple[TargetPlan, ...]:
        return tuple(t for t in self.targets if not t.resolved)

    @property
    def unresolved_count(self) -> int:
        return len(self.unresolved)

    @property
    def ok(self) -> bool:
        return self.unresolved_count == 0 and bool(self.resolved)

    def to_json_data(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_by": GENERATED_BY,
            "read_only": True,
            "ok": self.ok,
            "manifest_sha256": self.manifest_sha256,
            "inputs": dict(self.inputs),
            "counts": dict(self.counts),
            "unresolved_count": self.unresolved_count,
            "ext3": {
                "num_banks": self.ext3_num_banks,
                "reclaim": None if self.reclaim is None else self.reclaim.to_json_data(),
            },
            "guard_outcomes": [dict(item) for item in self.guard_outcomes],
            "dictionary_changes": [slot.to_json_data() for slot in self.slots],
            "targets": [target.to_json_data() for target in self.targets],
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# structure proofs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Structure:
    payload: bytes
    prefix: bytes
    body_span: int
    reason: str | None
    write_prefix_len: int = 0
    prefix_source: str = "rule"


def _rule_prefix(payload: bytes, region: str, bank: int) -> int:
    if region == "aux":
        rule = BANK_RULES.get(bank)
        return 0 if rule is None else rule_prefix_len(payload, rule)
    if region == "script":
        return len(split_prefix_body(payload)[0])
    return 0


def _p1_text_initial_proof(
    row: Mapping[str, Any],
    payload: bytes,
    *,
    capacity: int,
    terminator: int,
) -> tuple[bool, str | None]:
    """Validate an embedded P1 proof that an aux record starts with text.

    The proof is deliberately narrow: only bank 5C's established continuation-
    text zstring format is accepted.  A malformed or drifted proof fails closed
    instead of falling back to the old ambiguous-leading-byte decision.
    """
    raw = row.get("p1_text_initial_proof")
    if raw is None:
        return False, None
    if not isinstance(raw, Mapping):
        return False, "p1_text_initial_proof_invalid"
    proof = dict(raw)
    expected = proof.pop("proof_sha256", None)
    if not isinstance(expected, str) or deterministic_json_sha256(proof) != expected:
        return False, "p1_text_initial_proof_digest_mismatch"
    start = int(row["logical_address"])
    checks = (
        proof.get("schema_version") == 1,
        proof.get("proof_kind") == "aux_bank5c_continuation_zstring",
        proof.get("record_id") == row.get("record_id"),
        proof.get("logical_address") == start,
        proof.get("bank") == "5C",
        proof.get("prefix_bytes") == 0,
        proof.get("original_payload_sha256") == _sha256(payload),
        proof.get("payload_capacity") == capacity,
        proof.get("terminator_offset") == terminator,
        proof.get("original_rule")
        == "nul_terminated_continuation_text_no_record_prefix",
    )
    try:
        block_start = int(str(proof.get("block_start")), 16)
        block_end = int(str(proof.get("block_end_exclusive")), 16)
    except (TypeError, ValueError):
        return False, "p1_text_initial_proof_invalid"
    if not all(checks) or not (block_start <= start < block_end):
        return False, "p1_text_initial_proof_binding_mismatch"
    return True, None


def _prove_structure(
    row: Mapping[str, Any],
    original_rom: bytes,
    working_rom: bytes,
) -> _Structure:
    """Re-derive the Original-ROM boundary and refuse any drift.

    The manifest is evidence, not authority: capacity, prefix bytes, terminator
    and the next record start are all re-read from the Original ROM here, and
    the Working ROM record must still hold the same bytes (otherwise something
    else already rewrote it and this plan would clobber that work).
    """
    boundary = row.get("boundary") or {}
    start = int(row["logical_address"])
    capacity = int(boundary.get("payload_capacity") or 0)
    terminator = int(boundary.get("terminator_offset") or 0)
    next_start = boundary.get("next_record_start")
    prefix_hex = str(row.get("prefix_hex") or "").strip()
    region = str(row.get("region"))
    bank = start >> 16

    sb_o = stock_base(original_rom)
    sb_w = stock_base(working_rom)
    payload = bytes(original_rom[sb_o + start : sb_o + start + capacity])
    try:
        evidence = bytes.fromhex(prefix_hex)
    except ValueError:
        evidence = b""
        evidence_malformed = bool(prefix_hex)
    else:
        evidence_malformed = False
    evidence_len = len(evidence)
    prefix = payload[:evidence_len]

    if capacity <= 0:
        return _Structure(payload, prefix, 0, "structure_zero_capacity")
    if terminator != start + capacity:
        return _Structure(payload, prefix, 0, "structure_terminator_offset_mismatch")
    if len(payload) != capacity:
        return _Structure(payload, prefix, 0, "structure_capacity_out_of_range")
    if _sha256(payload) != str(row.get("original_payload_sha256")):
        return _Structure(payload, prefix, 0, "structure_original_payload_drift")
    if original_rom[sb_o + terminator] != 0x00:
        return _Structure(payload, prefix, 0, "structure_missing_original_terminator")
    if next_start is not None and int(next_start) <= terminator:
        return _Structure(payload, prefix, 0, "structure_next_record_overlap")
    if evidence_malformed or evidence != payload[:evidence_len]:
        return _Structure(payload, prefix, 0, "structure_prefix_evidence_mismatch")
    write_prefix_len = _rule_prefix(payload, region, bank)
    write_prefix = payload[:write_prefix_len]
    text_initial_proven, text_initial_error = _p1_text_initial_proof(
        row, payload, capacity=capacity, terminator=terminator
    )
    if text_initial_error is not None:
        return _Structure(
            payload,
            write_prefix,
            0,
            text_initial_error,
            write_prefix_len,
            "p1_text_initial_proof",
        )
    if evidence_len > 0 and evidence_len != write_prefix_len:
        return _Structure(
            payload,
            write_prefix,
            0,
            "prefix_rule_mismatch",
            write_prefix_len,
            "rule+evidence",
        )
    if (
        region == "aux"
        and write_prefix_len == 0
        and payload
        and payload[0] < 0xE0
        and not text_initial_proven
    ):
        return _Structure(
            payload,
            write_prefix,
            0,
            "ambiguous_leading_byte",
            write_prefix_len,
            "rule",
        )
    if capacity > MAX_SAFE_RECORD_LEN:
        return _Structure(
            payload, write_prefix, 0, "structure_record_longer_than_safe_limit", write_prefix_len
        )
    if region == "script" and bank in NON_DIALOGUE_SCRIPT_BANKS:
        return _Structure(
            payload, write_prefix, 0, "structure_non_dialogue_bank", write_prefix_len
        )
    # A 0x00 *inside* the payload is legal: it occurs as the trail byte of a
    # two-byte code (``E0 00``, ``F4 00``), which the zstring walker consumes as
    # a pair.  What must never happen is a *new* NUL introduced by the rewrite,
    # and that is checked on the planned body instead.
    if any(
        is_ext3_magic(payload[i], payload[i + 1]) for i in range(len(payload) - 1)
    ):
        return _Structure(
            payload, write_prefix, 0, "structure_original_has_ext3_magic", write_prefix_len
        )

    # A ``mixed`` target is by definition already partly rewritten in the Working
    # ROM, so byte equality with the Original is not required.  What is required
    # is that the Original-derived structure still holds there: the same prefix
    # bytes at the same offsets and the terminator still at the derived offset.
    working_payload = bytes(working_rom[sb_w + start : sb_w + start + capacity])
    if len(working_payload) != capacity:
        return _Structure(
            payload, write_prefix, 0, "structure_working_capacity_out_of_range", write_prefix_len
        )
    if working_payload[:write_prefix_len] != write_prefix:
        return _Structure(
            payload, write_prefix, 0, "structure_working_prefix_drift", write_prefix_len
        )
    if working_rom[sb_w + terminator] != 0x00:
        return _Structure(
            payload, write_prefix, 0, "structure_missing_working_terminator", write_prefix_len
        )

    return _Structure(
        payload,
        write_prefix,
        capacity - write_prefix_len,
        None,
        write_prefix_len,
        (
            "p1_text_initial_proof"
            if text_initial_proven
            else ("rule+evidence" if evidence_len else "rule")
        ),
    )


# --------------------------------------------------------------------------- #
# ext3 inventory and reclaim
# --------------------------------------------------------------------------- #


@dataclass
class _Ext3Space:
    num_banks: int
    room: dict[int, int]
    free_by_bank: dict[int, list[int]]
    payload_index: dict[bytes, int]
    dead_by_bank: dict[int, list[int]]
    live_by_bank: dict[int, list[int]]
    dead_bytes: dict[int, int]
    live_bytes: dict[int, int]


def _ext3_token_is_nul_free(index: int) -> bool:
    """Reject ext3 indices whose ``E5 18 xx yy`` token would embed a ``0x00``.

    ``token_from_ext3_index`` already refuses a NUL trail; the high byte is just
    as fatal, because a NUL anywhere inside the body terminates the record early
    and every following sequential entry shifts.
    """
    slot = index - INDEX_BASE
    return ((slot >> 8) & 0xFF) != 0 and (slot & 0xFF) != 0


def _referenced_ext3_indices(rom: bytes) -> set[int]:
    """Every ext3 index whose 4-byte portal appears anywhere in the image.

    Deliberately cruder than the reference union: any ``E5 18`` byte pair
    anywhere counts, including code and unscanned banks.  False positives only
    keep a phrase alive, which is the safe direction.
    """
    found: set[int] = set()
    cursor = 0
    limit = len(rom) - 3
    while True:
        hit = rom.find(b"\xE5\x18", cursor)
        if hit < 0 or hit > limit:
            break
        found.add(EXT3_INDEX_BASE + ((rom[hit + 2] << 8) | rom[hit + 3]))
        cursor = hit + 1
    compact_limit = len(rom) - 2
    cursor = 0
    while True:
        hit = rom.find(b"\xE5\x19", cursor)
        if hit < 0 or hit > compact_limit:
            break
        found.add(0xC000 + rom[hit + 2])
        cursor = hit + 1
    return found


def _ext3_space(
    working_rom: bytes,
    *,
    union: ReferenceUnion,
    dictionary: Dictionary,
    num_banks: int,
) -> _Ext3Space:
    referenced = _referenced_ext3_indices(working_rom)
    room = ext3_bank_room(working_rom, num_banks)
    free_by_bank: dict[int, list[int]] = {}
    for index in list_free_ext3_indices(working_rom, num_banks=num_banks):
        if not union.is_true_free(index) or not _ext3_token_is_nul_free(index):
            continue
        free_by_bank.setdefault((index - INDEX_BASE) >> 12, []).append(index)
    for bucket in free_by_bank.values():
        bucket.sort()

    payload_index: dict[bytes, int] = {}
    dead_by_bank: dict[int, list[int]] = {}
    live_by_bank: dict[int, list[int]] = {}
    dead_bytes: dict[int, int] = {}
    live_bytes: dict[int, int] = {}
    for bank in range(num_banks):
        for local in range(EXP3_SLOTS):
            index = INDEX_BASE + (bank << 12) + local
            try:
                raw = dictionary.raw_entry(index)
            except Exception:
                continue
            if not raw:
                continue
            size = len(raw) + 1
            if index in referenced or not union.is_true_free(index):
                live_by_bank.setdefault(bank, []).append(index)
                live_bytes[bank] = live_bytes.get(bank, 0) + size
                if _ext3_token_is_nul_free(index):
                    payload_index.setdefault(bytes(raw), index)
            else:
                dead_by_bank.setdefault(bank, []).append(index)
                dead_bytes[bank] = dead_bytes.get(bank, 0) + size
    return _Ext3Space(
        num_banks=num_banks,
        room=dict(room),
        free_by_bank=free_by_bank,
        payload_index=payload_index,
        dead_by_bank=dead_by_bank,
        live_by_bank=live_by_bank,
        dead_bytes=dead_bytes,
        live_bytes=live_bytes,
    )


def _plan_reclaim(space: _Ext3Space, needed: int) -> Ext3ReclaimPlan | None:
    """Choose the fewest banks whose dead phrases cover the shortfall."""
    empty_at = EXP3_SLOTS * 2
    available = sum(space.room.values())
    if available >= needed:
        return None
    order = sorted(space.dead_bytes, key=lambda b: -space.dead_bytes[b])
    banks: list[int] = []
    freed: dict[int, int] = {}
    room_after: dict[int, int] = {}
    for bank in order:
        if available >= needed:
            break
        live = space.live_bytes.get(bank, 0)
        new_room = BANK_SIZE - (empty_at + 1) - live
        gain = new_room - space.room.get(bank, 0)
        if gain <= 0:
            continue
        banks.append(bank)
        freed[bank] = gain
        room_after[bank] = new_room
        available += gain
    if not banks:
        return None
    return Ext3ReclaimPlan(
        banks=tuple(banks),
        dead_indices={b: tuple(space.dead_by_bank.get(b, ())) for b in banks},
        live_indices={b: tuple(space.live_by_bank.get(b, ())) for b in banks},
        freed_bytes=freed,
        room_after=room_after,
        proof=(
            "each reclaimed phrase has zero reference-union consumers and its "
            "E5 18 xx yy portal appears nowhere in the ROM image; live phrases "
            "in the same bank are repacked byte-for-byte and re-verified"
        ),
    )


# --------------------------------------------------------------------------- #
# curated pair-steal manifest
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PairCandidate:
    steal_slot: int
    preserve_slot: int
    keepers: tuple[int, ...]
    retargets: tuple[int, ...]
    reviewer: str
    note: str


def load_pair_manifest(path: Path | None) -> dict[int, PairCandidate]:
    """Reviewed S/T pairs only. No pool, no heuristics, no auto-selection."""
    if path is None:
        return {}
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not document.get("ok"):
        raise PlanningError(f"pair manifest is not marked ok: {path}")
    out: dict[int, PairCandidate] = {}
    for row in document.get("pairs") or []:
        steal = int(str(row["steal_slot"]), 16)
        preserve = int(str(row["preserve_slot"]), 16)
        reviewer = str(row.get("reviewer") or "")
        if not reviewer:
            raise PlanningError(f"pair {steal:04X} has no reviewer")
        out[steal] = PairCandidate(
            steal_slot=steal,
            preserve_slot=preserve,
            keepers=tuple(int(a, 16) for a in row.get("keepers") or ()),
            retargets=tuple(int(a, 16) for a in row.get("retargets") or ()),
            reviewer=reviewer,
            note=str(row.get("note") or ""),
        )
    return out


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #


def _two_byte_candidate(
    union: ReferenceUnion,
    free_two_byte: list[int],
) -> tuple[int | None, str | None]:
    """Pop the next true-free 2-byte index that is safe for story Hangul."""
    while free_two_byte:
        index = free_two_byte.pop(0)
        if not union.is_true_free(index):
            continue
        if is_ff_page_index(index) and union.aux_or_name75_consumers(index):
            # Cannot happen for a true-free slot, but the FF page is exactly
            # where a wrong assumption poisons tutorial/help text, so the check
            # stays explicit rather than implied.
            continue
        return index, None
    return None, "no_true_free_two_byte_slot"


def _early_band_keepers(
    union: ReferenceUnion, index: int
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """``(early_keepers, later_consumers)`` for a shared-slot operation."""
    script = union.script_consumers(index)
    early = tuple(sorted(c.abs for c in script if in_early_band(c.abs)))
    later = tuple(sorted(c.abs for c in script if not in_early_band(c.abs)))
    return early, later


def build_plan(
    *,
    original_rom: bytes,
    working_rom: bytes,
    manifest: Mapping[str, Any],
    validation: TranslationValidation,
    tbl: Tbl,
    ext_meta: Mapping[str, Any],
    ext3_meta: Mapping[str, Any],
    union: ReferenceUnion | None = None,
    pair_manifest: Mapping[int, PairCandidate] | None = None,
    allow_ext3_reclaim: bool = True,
    inputs: Mapping[str, Any] | None = None,
) -> LocalizationPlan:
    """Plan every included target, choosing the first feasible safe strategy."""
    population = manifest.get("population") or {}
    included = list(population.get("included") or ())
    if not included:
        raise PlanningError("manifest has no included targets")
    manifest_sha256 = str(manifest.get("manifest_sha256"))

    if union is None:
        union = build_reference_union(
            original_rom, working_rom, ext_meta=ext_meta, ext3_meta=ext3_meta
        )
    pairs = dict(pair_manifest or {})
    korean = validation.korean_text()
    unresolved_translations = {
        item.record_id: item.reason for item in validation.unresolved
    }

    num_banks = int(ext3_meta.get("num_banks") or DEFAULT_NUM_BANKS)
    dictionary = make_dictionary_ext3(working_rom, dict(ext_meta), dict(ext3_meta))
    space = _ext3_space(
        working_rom, union=union, dictionary=dictionary, num_banks=num_banks
    )
    two_byte_free = sorted(
        index
        for index in range(make_dictionary(working_rom, dict(ext_meta)).count)
        if index <= 0xFFF and union.is_true_free(index)
    )

    marker = marker_code()
    targets: list[TargetPlan] = []
    # unique encoded phrase -> (index, write_required, targets)
    phrase_alloc: dict[bytes, tuple[int, bool]] = {}
    slot_targets: dict[int, list[str]] = {}
    slot_payload: dict[int, bytes] = {}
    slot_strategy: dict[int, str] = {}
    slot_preserve: dict[int, int | None] = {}
    #: ext3 indices that already hold exactly this Korean payload. Nothing is
    #: written for them, so they are references, not slot rewrites, and the
    #: true-free requirement does not apply.
    reused_slots: set[int] = set()
    reasons: dict[str, int] = {}
    pending: list[tuple[Mapping[str, Any], _Structure, bytes, str]] = []

    def note_reason(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    def unresolved(
        row: Mapping[str, Any], structure: _Structure | None, reason: str, ko: str = ""
    ) -> None:
        boundary = row.get("boundary") or {}
        prefix_len = (
            structure.write_prefix_len
            if structure is not None
            else len(str(row.get("prefix_hex") or "")) // 2
        )
        prefix_hex = structure.prefix.hex().upper() if structure is not None else ""
        prefix_source = structure.prefix_source if structure is not None else "rule"
        note_reason(reason)
        targets.append(
            TargetPlan(
                record_id=str(row.get("record_id")),
                region=str(row.get("region")),
                logical_address=int(row["logical_address"]),
                bank=int(row["logical_address"]) >> 16,
                payload_capacity=int(boundary.get("payload_capacity") or 0),
                prefix_bytes=prefix_len,
                terminator_offset=int(boundary.get("terminator_offset") or 0),
                next_record_start=(
                    None
                    if boundary.get("next_record_start") is None
                    else int(boundary["next_record_start"])
                ),
                source_text=str(row.get("source_text") or ""),
                korean_text=ko,
                status="unresolved",
                reason=reason,
                annotations=tuple(row.get("annotations") or ()),
                prefix_hex=prefix_hex,
                prefix_source=prefix_source,
            )
        )

    # --- pass 1: structure + encoding + strategy eligibility ----------------
    for row in included:
        record_id = str(row.get("record_id"))
        if record_id in unresolved_translations:
            unresolved(row, None, unresolved_translations[record_id])
            continue
        ko_raw = korean.get(record_id)
        if not ko_raw:
            unresolved(row, None, "translation_missing_for_target")
            continue
        structure = _prove_structure(row, original_rom, working_rom)
        if structure.reason is not None:
            unresolved(row, structure, structure.reason, ko_raw)
            continue
        if str(row.get("region")) == "name75" and tuple(
            row.get("status_marker_codes") or ()
        ):
            unresolved(row, structure, "name75_status_marker_would_be_lost", ko_raw)
            continue
        ko = normalize_ko_text(ko_raw)
        phrase = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if phrase is None:
            unresolved(row, structure, "translation_encode_failed", ko)
            continue
        if b"\x00" in phrase:
            unresolved(row, structure, "translation_payload_contains_nul", ko)
            continue
        pending.append((row, structure, bytes(phrase), ko))

    # --- ext3 room: decide reclaim before allocating -------------------------
    need_bytes = 0
    for _row, structure, phrase, _ko in pending:
        if structure.body_span < EXT3_TOKEN_LEN:
            continue
        if phrase in space.payload_index or phrase in phrase_alloc:
            continue
        phrase_alloc[phrase] = (-1, True)  # placeholder to count uniques once
        need_bytes += len(phrase) + 1
    phrase_alloc.clear()

    reclaim = _plan_reclaim(space, need_bytes) if allow_ext3_reclaim else None
    if reclaim is not None:
        for bank in reclaim.banks:
            space.room[bank] = reclaim.room_after[bank]
            # Reclaimed slots become allocatable indices as well.
            freed_indices = [
                index
                for index in reclaim.dead_indices.get(bank, ())
                if union.is_true_free(index) and _ext3_token_is_nul_free(index)
            ]
            space.free_by_bank.setdefault(bank, []).extend(freed_indices)
            space.free_by_bank[bank] = sorted(set(space.free_by_bank[bank]))

    # --- pass 2: allocate storage -------------------------------------------
    for row, structure, phrase, ko in pending:
        record_id = str(row.get("record_id"))
        region = str(row.get("region"))
        start = int(row["logical_address"])
        boundary = row.get("boundary") or {}
        capacity = int(boundary["payload_capacity"])
        prefix_len = structure.write_prefix_len
        body_span = structure.body_span

        strategy: str | None = None
        index: int | None = None
        write_required = True
        failure: str | None = None
        token = b""

        if body_span >= EXT3_TOKEN_LEN and body_span - EXT3_TOKEN_LEN <= MAX_PAD_BYTES:
            strategy = "ext3"
            existing = phrase_alloc.get(phrase)
            if existing is not None:
                index, write_required = existing
            elif phrase in space.payload_index:
                # An identical Korean phrase already lives in ext3: point at it
                # instead of spending bank room. Same bytes, same rendering.
                index = space.payload_index[phrase]
                write_required = False
                reused_slots.add(index)
                phrase_alloc[phrase] = (index, False)
            else:
                need = len(phrase) + 1
                chosen: int | None = None
                for bank in sorted(space.room, key=lambda b: -space.room[b]):
                    if space.room.get(bank, 0) < need or not space.free_by_bank.get(bank):
                        continue
                    chosen = space.free_by_bank[bank].pop(0)
                    space.room[bank] -= need
                    break
                if chosen is None:
                    failure = "ext3_no_bank_with_free_slot_and_room"
                else:
                    index = chosen
                    phrase_alloc[phrase] = (index, True)
            if index is not None:
                try:
                    token = token_from_ext3_index(index, num_banks=num_banks)
                except ValueError as exc:
                    failure = f"ext3_token_unsafe:{exc}"
        elif body_span >= TWO_BYTE_TOKEN_LEN:
            index, failure = _two_byte_candidate(union, two_byte_free)
            if index is not None:
                strategy = "true_free"
                from monoeye_rom import token_from_dict_index

                token = token_from_dict_index(index)
                if token[-1] == 0x00:
                    failure = "two_byte_token_trail_nul"
                    index = None
                    strategy = None
            if index is None:
                pair = None
                for slot, candidate in pairs.items():
                    if slot in slot_targets:
                        continue
                    pair = candidate
                    break
                if pair is not None:
                    early, later = _early_band_keepers(union, pair.steal_slot)
                    accounted = set(pair.keepers) | set(pair.retargets)
                    former = {c.abs for c in union.consumers_for(pair.steal_slot)}
                    missing_early = [a for a in early if a not in set(pair.keepers)]
                    if union.aux_or_name75_consumers(pair.steal_slot):
                        failure = "pair_steal_slot_has_aux_or_name75_consumers"
                    elif not union.is_true_free(pair.preserve_slot):
                        failure = "pair_steal_preserve_slot_not_true_free"
                    elif accounted != former:
                        failure = "pair_steal_former_consumers_unaccounted"
                    elif missing_early:
                        failure = "pair_steal_early_band_consumer_not_keeper"
                    else:
                        from monoeye_rom import token_from_dict_index

                        strategy = "pair_steal"
                        index = pair.steal_slot
                        token = token_from_dict_index(index)
                        slot_preserve[index] = pair.preserve_slot
                        _ = later
                else:
                    failure = failure or "no_curated_pair_available"
        else:
            failure = f"record_body_too_short_for_any_token:{body_span}"

        if strategy is None or index is None or failure is not None:
            unresolved(
                row,
                structure,
                failure or f"no_safe_storage_strategy:body_span={body_span}",
                ko,
            )
            continue

        pad = body_span - len(token)
        if pad < 0:
            unresolved(row, structure, "token_longer_than_record_body", ko)
            continue
        if pad > MAX_PAD_BYTES:
            unresolved(row, structure, f"remainder_padding_too_large:{pad}", ko)
            continue
        new_body = token + bytes([PAD_BYTE]) * pad
        if 0x00 in new_body:
            unresolved(row, structure, "planned_body_contains_nul", ko)
            continue
        if len(new_body) != body_span:
            unresolved(row, structure, "planned_body_length_mismatch", ko)
            continue
        # Requirement 4.14: the body may contain this target's token and nothing
        # else. A second token would be a shared fragment used as padding.
        body_tokens = [i for i, _len in iter_token_refs(new_body)]
        if body_tokens != [index]:
            unresolved(row, structure, "planned_body_has_foreign_token", ko)
            continue

        slot_targets.setdefault(index, []).append(record_id)
        slot_strategy[index] = strategy
        if write_required:
            slot_payload[index] = phrase
        targets.append(
            TargetPlan(
                record_id=record_id,
                region=region,
                logical_address=start,
                bank=start >> 16,
                payload_capacity=capacity,
                prefix_bytes=prefix_len,
                terminator_offset=int(boundary["terminator_offset"]),
                next_record_start=(
                    None
                    if boundary.get("next_record_start") is None
                    else int(boundary["next_record_start"])
                ),
                source_text=str(row.get("source_text") or ""),
                korean_text=ko,
                status="resolved",
                reason=f"planned_{strategy}",
                strategy=strategy,
                dict_index=index,
                token_hex=token.hex().upper(),
                new_body_hex=new_body.hex().upper(),
                pad_bytes=pad,
                phrase_len=len(phrase),
                phrase_sha256=_sha256(phrase),
                annotations=tuple(row.get("annotations") or ()),
                prefix_hex=structure.prefix.hex().upper(),
                prefix_source=structure.prefix_source,
            )
        )

    # --- mandatory guard, before any writer can be reached -------------------
    keeper_map: dict[int, set[int]] = {}
    for index, strategy in slot_strategy.items():
        if strategy != "pair_steal":
            continue
        candidate = pairs.get(index)
        keeper_map[index] = set(candidate.keepers) if candidate else set()

    slot_guard: dict[int, str] = {}
    refused_slots: set[int] = set()
    for index, strategy in slot_strategy.items():
        if index in reused_slots:
            # No payload write: the record simply points at an existing phrase
            # whose bytes already equal the approved Korean, so nothing shared
            # changes meaning and there is no slot to keep free.
            slot_guard[index] = "reused_existing_identical_payload"
            continue
        reason = union.refuse_reason(
            index,
            keeper_abs=keeper_map.get(index),
            require_free=strategy != "pair_steal",
        )
        if reason is None:
            slot_guard[index] = "guard_passed"
        else:
            slot_guard[index] = f"refused:{reason}"
            refused_slots.add(index)

    guard_outcomes: list[Mapping[str, Any]] = []
    if slot_payload:
        batch = {
            index: payload
            for index, payload in slot_payload.items()
            if index not in refused_slots and slot_strategy[index] != "pair_steal"
        }
        if batch:
            outcome = guard_slot_writes(
                working_rom,
                batch,
                union=union,
                require_free=True,
            )
            guard_outcomes.append(outcome.as_dict())
            if not outcome.ok:
                refused_slots.update(batch)
                for index in batch:
                    slot_guard[index] = f"refused:{outcome.outcome}"

    if refused_slots:
        kept: list[TargetPlan] = []
        for target in targets:
            if target.resolved and target.dict_index in refused_slots:
                note_reason("dictionary_guard_refused_slot")
                kept.append(
                    TargetPlan(
                        record_id=target.record_id,
                        region=target.region,
                        logical_address=target.logical_address,
                        bank=target.bank,
                        payload_capacity=target.payload_capacity,
                        prefix_bytes=target.prefix_bytes,
                        terminator_offset=target.terminator_offset,
                        next_record_start=target.next_record_start,
                        source_text=target.source_text,
                        korean_text=target.korean_text,
                        status="unresolved",
                        reason=slot_guard.get(
                            target.dict_index or -1, "dictionary_guard_refused_slot"
                        ),
                        annotations=target.annotations,
                        prefix_hex=target.prefix_hex,
                        prefix_source=target.prefix_source,
                    )
                )
            else:
                kept.append(target)
        targets = kept
        for index in refused_slots:
            slot_targets.pop(index, None)
            slot_payload.pop(index, None)

    # --- slot audits --------------------------------------------------------
    slots: list[SlotPlan] = []
    for index in sorted(slot_targets):
        strategy = slot_strategy[index]
        payload = slot_payload.get(index)
        audit = union.audit(index)
        early, later = _early_band_keepers(union, index)
        keepers = early + later if strategy == "pair_steal" else ()
        slots.append(
            SlotPlan(
                index=index,
                strategy=strategy,
                token_hex=(
                    token_from_ext3_index(index, num_banks=num_banks).hex().upper()
                    if index >= EXT3_INDEX_BASE
                    else ""
                ),
                payload_len=0 if payload is None else len(payload),
                payload_sha256="" if payload is None else _sha256(payload),
                write_required=payload is not None,
                ff_page=is_ff_page_index(index),
                keepers=keepers,
                restore_or_retarget=(),
                preserve_slot=slot_preserve.get(index),
                guard_outcome=slot_guard.get(index, "guard_passed"),
                union_audit=audit,
                targets=tuple(slot_targets[index]),
                bank=(
                    EXP3_SEG0 + ((index - INDEX_BASE) >> 12)
                    if index >= EXT3_INDEX_BASE
                    else None
                ),
            )
        )

    counts = {
        "included": len(included),
        "resolved": sum(1 for t in targets if t.resolved),
        "unresolved": sum(1 for t in targets if not t.resolved),
        "by_strategy": {
            strategy: sum(1 for t in targets if t.strategy == strategy)
            for strategy in ("ext3", "true_free", "pair_steal")
        },
        "by_region_resolved": {
            region: sum(1 for t in targets if t.resolved and t.region == region)
            for region in ("script", "name75", "aux")
        },
        "unresolved_reasons": dict(sorted(reasons.items())),
        "slots_written": sum(1 for s in slots if s.write_required),
        "slots_reused": sum(1 for s in slots if not s.write_required),
        "ext3_phrase_bytes_required": sum(
            len(payload) + 1 for payload in slot_payload.values()
        ),
        "ext3_room_remaining": sum(space.room.values()),
        "two_byte_true_free_slots": len(two_byte_free),
    }
    plan = LocalizationPlan(
        manifest_sha256=manifest_sha256,
        inputs=dict(inputs or {}),
        targets=tuple(
            sorted(targets, key=lambda t: (t.region, t.logical_address))
        ),
        slots=tuple(slots),
        reclaim=reclaim,
        ext3_num_banks=num_banks,
        counts=counts,
        guard_outcomes=tuple(guard_outcomes),
        notes=(
            "storage precedence: ext3 -> true_free -> curated pair_steal",
            "record bodies keep Original-derived prefix, capacity and terminator",
            f"remainder padding is 0x{PAD_BYTE:02X}, never 0x00",
        ),
    )
    # Keep the payloads reachable for the transaction without widening the model.
    object.__setattr__(plan, "_slot_payloads", dict(slot_payload))
    return plan


def plan_slot_payloads(plan: LocalizationPlan) -> dict[int, bytes]:
    """Phrase bytes the transaction still has to write, by dictionary index."""
    return dict(getattr(plan, "_slot_payloads", {}))


# --------------------------------------------------------------------------- #
# CLI (read-only)
# --------------------------------------------------------------------------- #


def _identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else Path(path).read_bytes()
    return {
        "path": str(Path(path).resolve()),
        "size": len(payload),
        "sha256": _sha256(payload),
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original-rom", type=Path, default=DEFAULT_ORIGINAL_ROM)
    ap.add_argument("--working-rom", type=Path, default=DEFAULT_WORKING_ROM)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--translations", type=Path, default=DEFAULT_TRANSLATIONS)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument("--pair-manifest", type=Path, default=None)
    ap.add_argument(
        "--no-ext3-reclaim",
        action="store_true",
        help="never repack provably unreferenced ext3 phrases, even if room runs out",
    )
    ap.add_argument("--out-plan", type=Path, default=DEFAULT_PLAN)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.out_plan.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — planning is read-only")

    original = bytes(load_rom(args.original_rom))
    working = bytes(load_rom(args.working_rom))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    validation = validate_catalog_files(args.manifest, args.translations)
    plan = build_plan(
        original_rom=original,
        working_rom=working,
        manifest=manifest,
        validation=validation,
        tbl=Tbl.load(args.tbl),
        ext_meta=load_ext_meta(args.ext_meta),
        ext3_meta=load_ext_meta(args.ext3_meta),
        pair_manifest=load_pair_manifest(args.pair_manifest),
        allow_ext3_reclaim=not args.no_ext3_reclaim,
        inputs={
            "original_rom": _identity(args.original_rom, original),
            "working_rom": _identity(args.working_rom, working),
            "manifest": _identity(args.manifest),
            "translations": _identity(args.translations),
            "tbl": _identity(args.tbl),
            "ext_meta": _identity(args.ext_meta),
            "ext3_meta": _identity(args.ext3_meta),
        },
    )
    document = plan.to_json_data()
    document["translation_validation"] = {
        "accepted": validation.accepted,
        "unresolved_count": validation.unresolved_count,
    }
    args.out_plan.parent.mkdir(parents=True, exist_ok=True)
    args.out_plan.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "ok": plan.ok,
        "counts": document["counts"],
        "reclaim": document["ext3"]["reclaim"],
        "plan": str(args.out_plan),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if plan.ok else 1


__all__ = [
    "EARLY_BAND_HI",
    "EARLY_BAND_LO",
    "EXT3_TOKEN_LEN",
    "Ext3ReclaimPlan",
    "LocalizationPlan",
    "MAX_PAD_BYTES",
    "PAD_BYTE",
    "PairCandidate",
    "PlanningError",
    "SlotPlan",
    "TargetPlan",
    "build_plan",
    "in_early_band",
    "load_pair_manifest",
    "plan_slot_payloads",
]


if __name__ == "__main__":
    raise SystemExit(main())
