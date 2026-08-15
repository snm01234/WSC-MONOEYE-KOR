#!/usr/bin/env python3
"""Failure-atomic scratch transaction for residual localization (task 5.1).

The transaction is the only component allowed to produce a ROM, and even then
only in two steps: an in-memory scratch buffer, then a *temporary* candidate
file that is renamed into place solely after every mandatory static gate has
passed.  Any failure at any point discards the scratch and writes no ROM.

State machine (design.md §4.8)::

    PLANNED -> GUARDED -> APPLIED_TO_SCRATCH -> PRECOMMIT_VERIFIED
            -> TEMPORARY_WRITTEN -> STATIC_GATES_PASSED -> PUBLISHED
            \\-> ABORTED

Ordering rules that are not negotiable:

* dictionary phrases are written **before** any record body points at them, so a
  half-applied scratch can never render a stale slot;
* a curated pair-steal runs ``T <- old S payload`` → retarget former consumers →
  verify their rendering is preserved → ``S <- new Korean`` → retarget the
  target, and aborts the whole transaction if any of those steps fails;
* ext3 phrase reclaim relocates every live phrase byte-for-byte and re-reads all
  of them afterwards before anything else is written;
* precommit decodes every resolved target through the *candidate's own*
  dictionary and compares it with the approved Korean text.

Approved-extent confinement (requirement 3.8) is verified here, not asserted:
the scratch is diffed against the Accepted_Baseline and every differing byte
must fall inside an extent owned by a resolved target, an ext3 phrase area, an
ext3 pointer entry, a consumer retarget, or the ROM header checksum.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from mixed_residual_models import ApprovedChangeExtent  # noqa: E402
from mixed_residual_planner import (  # noqa: E402
    LocalizationPlan,
    PAD_BYTE,
    TargetPlan,
    _rule_prefix,
    plan_slot_payloads,
)
from mixed_residual_reference_union import (  # noqa: E402
    ReferenceUnion,
    SlotGuardRefusal,
    write_ext3_slots_guarded,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    EXT3_INDEX_BASE,
    Tbl,
    le16,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    is_ext3_magic,
    update_ws_checksum,
    write_le16,
)
from patch_3byte_dict_token import EXP3_SEG0, EXP3_SLOTS, INDEX_BASE  # noqa: E402

GENERATED_BY = "tools/mixed_residual_transaction.py"
EXT3_EMPTY_AT = EXP3_SLOTS * 2


class TransactionState(str, Enum):
    PLANNED = "PLANNED"
    GUARDED = "GUARDED"
    APPLIED_TO_SCRATCH = "APPLIED_TO_SCRATCH"
    PRECOMMIT_VERIFIED = "PRECOMMIT_VERIFIED"
    TEMPORARY_WRITTEN = "TEMPORARY_WRITTEN"
    STATIC_GATES_PASSED = "STATIC_GATES_PASSED"
    PUBLISHED = "PUBLISHED"
    ABORTED = "ABORTED"


class TransactionAbort(RuntimeError):
    """Raised whenever the scratch must be discarded without writing a ROM."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _expansion_bank_base(seg: int) -> int:
    if seg > 0x7F:
        raise TransactionAbort(f"expansion bank out of range: {seg:#x}")
    return seg * BANK_SIZE


@dataclass
class PrecommitResult:
    ok: bool
    failures: tuple[str, ...]
    decoded: int
    extents: tuple[ApprovedChangeExtent, ...]
    diff_bytes: int
    diff_runs: int
    unaccounted: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failures": list(self.failures),
            "targets_decoded": self.decoded,
            "diff_bytes": self.diff_bytes,
            "diff_runs": self.diff_runs,
            "unaccounted_runs": [dict(item) for item in self.unaccounted],
            "approved_change_extents": [
                {
                    "start": f"{item.start:06X}",
                    "end": f"{item.end:06X}",
                    "kind": item.kind,
                    "owner_id": item.owner_id,
                }
                for item in self.extents
            ],
        }


class LocalizationTransaction:
    """Owns the scratch ROM, the precommit proof, and publication."""

    def __init__(
        self,
        *,
        working_rom: bytes,
        baseline_rom: bytes,
        plan: LocalizationPlan,
        union: ReferenceUnion,
        tbl: Tbl,
        ext_meta: Mapping[str, Any],
        ext3_meta: Mapping[str, Any],
    ) -> None:
        if len(working_rom) != len(baseline_rom):
            raise TransactionAbort("working and baseline ROMs differ in size")
        self._working = bytes(working_rom)
        self._baseline = bytes(baseline_rom)
        self._plan = plan
        self._union = union
        self._tbl = tbl
        self._ext_meta = dict(ext_meta)
        self._ext3_meta = dict(ext3_meta)
        self._num_banks = plan.ext3_num_banks
        self._scratch: bytearray | None = None
        self.state = TransactionState.PLANNED
        self.journal: list[dict[str, Any]] = []
        self._extents: list[ApprovedChangeExtent] = []
        self._ext3_write_info: dict[str, Any] = {}
        self._reclaim_info: dict[str, Any] = {}
        self._applied_rows: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ utils
    def _log(self, event: str, **fields: Any) -> None:
        self.journal.append({"state": self.state.value, "event": event, **fields})

    def _abort(self, reason: str) -> None:
        self._scratch = None
        self.state = TransactionState.ABORTED
        self._log("aborted", reason=reason)
        raise TransactionAbort(reason)

    @property
    def scratch(self) -> bytearray:
        if self._scratch is None:
            raise TransactionAbort("scratch buffer has been discarded")
        return self._scratch

    # ------------------------------------------------------------------ apply
    def apply_to_scratch(self) -> None:
        """Apply the whole plan to an in-memory copy, in dependency order."""
        if self.state is not TransactionState.PLANNED:
            self._abort(f"apply_to_scratch called in state {self.state.value}")
        if self._plan.unresolved_count:
            self._abort(
                f"refusing to apply a plan with {self._plan.unresolved_count} "
                "unresolved target(s)"
            )
        self._scratch = bytearray(self._working)
        self.state = TransactionState.GUARDED

        try:
            self._reclaim_ext3()
            self._write_dictionary_phrases()
            self._write_record_bodies()
        except SlotGuardRefusal as exc:
            self._abort(f"dictionary guard refused the write: {exc}")
        except TransactionAbort:
            raise
        except Exception as exc:  # any writer failure discards the scratch
            self._abort(f"{type(exc).__name__}: {exc}")

        checksum = update_ws_checksum(self.scratch)
        self._extents.append(
            ApprovedChangeExtent(
                start=len(self.scratch) - 2,
                end=len(self.scratch),
                kind="header_checksum",
                owner_id="ws_header_checksum",
            )
        )
        self.state = TransactionState.APPLIED_TO_SCRATCH
        self._log("applied_to_scratch", checksum=f"{checksum:04X}")

    def _reclaim_ext3(self) -> None:
        """Repack provably unreferenced ext3 phrase space, preserving live ones."""
        reclaim = self._plan.reclaim
        if reclaim is None:
            return
        banks: dict[str, Any] = {}
        for bank in reclaim.banks:
            seg = EXP3_SEG0 + bank
            base = _expansion_bank_base(seg)
            buffer = bytearray(slice_expansion_bank(self.scratch, seg))
            live = list(reclaim.live_indices.get(bank, ()))
            dead = list(reclaim.dead_indices.get(bank, ()))

            captured: dict[int, bytes] = {}
            for index in live:
                local = (index - INDEX_BASE) & 0xFFF
                pointer = le16(buffer, local * 2)
                if not (EXT3_EMPTY_AT <= pointer < BANK_SIZE):
                    self._abort(
                        f"live ext3 slot {index:04X} has an out-of-range pointer"
                    )
                end = pointer
                while end < BANK_SIZE and buffer[end] != 0:
                    end += 1
                captured[index] = bytes(buffer[pointer:end])

            cursor = EXT3_EMPTY_AT + 1
            for index in sorted(captured):
                payload = captured[index]
                need = len(payload) + 1
                if cursor + need > BANK_SIZE:
                    self._abort(f"ext3 reclaim overflow in bank {seg:02X}")
                buffer[cursor : cursor + len(payload)] = payload
                buffer[cursor + len(payload)] = 0
                write_le16(buffer, ((index - INDEX_BASE) & 0xFFF) * 2, cursor)
                cursor += need
            for index in dead:
                write_le16(buffer, ((index - INDEX_BASE) & 0xFFF) * 2, EXT3_EMPTY_AT)
            # Everything after the repacked live phrases is free space again.
            for offset in range(cursor, BANK_SIZE):
                buffer[offset] = 0xFF

            # Verify before publishing the bank back into the scratch.
            for index, payload in captured.items():
                local = (index - INDEX_BASE) & 0xFFF
                pointer = le16(buffer, local * 2)
                end = pointer
                while end < BANK_SIZE and buffer[end] != 0:
                    end += 1
                if bytes(buffer[pointer:end]) != payload:
                    self._abort(
                        f"ext3 reclaim did not preserve live slot {index:04X}"
                    )
            self.scratch[base : base + BANK_SIZE] = buffer
            self._extents.append(
                ApprovedChangeExtent(
                    start=base,
                    end=base + BANK_SIZE,
                    kind="dictionary_payload",
                    owner_id=f"ext3_reclaim_bank_{seg:02X}",
                )
            )
            banks[f"{seg:02X}"] = {
                "live_preserved": len(captured),
                "dead_released": len(dead),
                "phrase_cursor": cursor,
                "room_after": BANK_SIZE - cursor,
            }
        self._reclaim_info = {"banks": banks, "proof": reclaim.proof}
        self._log("ext3_reclaimed", banks=list(banks))

    def _write_dictionary_phrases(self) -> None:
        payloads = plan_slot_payloads(self._plan)
        if not payloads:
            return
        ext3 = {i: p for i, p in payloads.items() if i >= EXT3_INDEX_BASE}
        other = {i: p for i, p in payloads.items() if i < EXT3_INDEX_BASE}
        if other:
            # Stock/ext (2-byte) writes only ever arrive through a reviewed
            # pair-steal, which this build does not use. Refuse rather than
            # invent a path.
            self._abort(
                "two-byte dictionary writes require a reviewed pair-steal path: "
                f"{sorted(f'{i:04X}' for i in other)}"
            )

        cursor_before: dict[int, int] = {}
        for index in ext3:
            seg = EXP3_SEG0 + ((index - INDEX_BASE) >> 12)
            if seg in cursor_before:
                continue
            cursor_before[seg] = self._ext3_phrase_cursor(seg)

        info, outcome = write_ext3_slots_guarded(
            self.scratch,
            ext3,
            union=self._union,
            num_banks=self._num_banks,
        )
        self._ext3_write_info = {"write": info, "guard": outcome.as_dict()}
        for seg, start in cursor_before.items():
            base = _expansion_bank_base(seg)
            end = self._ext3_phrase_cursor(seg)
            if end > start:
                self._extents.append(
                    ApprovedChangeExtent(
                        start=base + start,
                        end=base + end,
                        kind="dictionary_payload",
                        owner_id=f"ext3_phrases_bank_{seg:02X}",
                    )
                )
        for index in sorted(ext3):
            seg = EXP3_SEG0 + ((index - INDEX_BASE) >> 12)
            base = _expansion_bank_base(seg)
            local = (index - INDEX_BASE) & 0xFFF
            self._extents.append(
                ApprovedChangeExtent(
                    start=base + local * 2,
                    end=base + local * 2 + 2,
                    kind="dictionary_pointer",
                    owner_id=f"ext3_slot_{index:04X}",
                )
            )
        self._log("dictionary_phrases_written", slots=len(ext3), info=info)

    def _ext3_phrase_cursor(self, seg: int) -> int:
        bank = slice_expansion_bank(self.scratch, seg)
        if all(b == 0xFF for b in bank[:64]):
            return EXT3_EMPTY_AT + 1
        cursor = EXT3_EMPTY_AT + 1
        for local in range(EXP3_SLOTS):
            pointer = le16(bank, local * 2)
            if pointer < EXT3_EMPTY_AT or pointer >= BANK_SIZE:
                continue
            end = pointer
            while end < BANK_SIZE and bank[end] != 0:
                end += 1
            cursor = max(cursor, end + 1)
        return cursor

    def _write_record_bodies(self) -> None:
        sb = stock_base(self.scratch)
        for target in self._plan.resolved:
            start = sb + target.logical_address
            capacity = target.payload_capacity
            k = target.prefix_bytes
            body = bytes.fromhex(target.new_body_hex)
            if len(body) != capacity - k:
                self._abort(f"{target.record_id}: planned body length drifted")
            if 0x00 in body:
                self._abort(f"{target.record_id}: planned body contains a NUL")
            prefix_now = bytes(self.scratch[start : start + k])
            expected_prefix = bytes(self._working[start : start + k])
            if prefix_now != expected_prefix:
                self._abort(
                    f"{target.record_id}: prefix bytes drifted before the write"
                )
            self.scratch[start + k : start + capacity] = body
            if self.scratch[start + capacity] != 0x00:
                self._abort(f"{target.record_id}: terminator is not NUL after write")
            self._applied_rows.append(
                {
                    "abs": f"{target.logical_address:06X}",
                    "region": target.region,
                    "bank": f"{target.bank:02X}",
                    "payload_len": capacity,
                    "prefix_bytes": k,
                    "prefix_hex": prefix_now.hex().upper(),
                    "index": (
                        None if target.dict_index is None else f"{target.dict_index:04X}"
                    ),
                    "ko": target.korean_text,
                    "pad": target.pad_bytes,
                    "ok": True,
                }
            )
            self._extents.append(
                ApprovedChangeExtent(
                    start=start + k,
                    end=start + capacity,
                    kind="record_body",
                    owner_id=target.record_id,
                )
            )
        self._log("record_bodies_written", records=len(self._plan.resolved))

    # -------------------------------------------------------------- precommit
    def precommit_verify(self) -> PrecommitResult:
        if self.state is not TransactionState.APPLIED_TO_SCRATCH:
            self._abort(f"precommit_verify called in state {self.state.value}")
        scratch = bytes(self.scratch)
        failures: list[str] = []
        dictionary = make_dictionary_ext3(scratch, self._ext_meta, self._ext3_meta)
        sb = stock_base(scratch)
        decoded = 0

        for target in self._plan.resolved:
            max_len = 256 if target.region == "script" else (
                64 if target.region == "name75" else 128
            )
            got = read_encoded_z_safe(scratch, sb + target.logical_address, max_len=max_len)
            if got is None:
                failures.append(f"{target.record_id}: no terminated record after write")
                continue
            payload = got[0]
            if len(payload) != target.payload_capacity:
                failures.append(
                    f"{target.record_id}: record length changed "
                    f"({len(payload)} != {target.payload_capacity})"
                )
                continue
            baseline_start = sb + target.logical_address
            baseline_payload = bytes(
                self._working[baseline_start : baseline_start + target.payload_capacity]
            )
            rule_k = _rule_prefix(baseline_payload, target.region, target.bank)
            if rule_k != target.prefix_bytes:
                failures.append(
                    f"{target.record_id}: prefix rule drift ({rule_k} != {target.prefix_bytes})"
                )
                continue
            if payload[: target.prefix_bytes].hex().upper() != _original_prefix_hex(
                self._working, sb, target
            ):
                failures.append(f"{target.record_id}: prefix bytes changed")
                continue
            if target.prefix_bytes and payload[:2] == b"\xE5\x18":
                failures.append(f"{target.record_id}: prefix was replaced by an ext3 portal")
                continue
            body_payload = payload[target.prefix_bytes :]
            if target.strategy == "ext3" and (
                len(body_payload) < 2 or not is_ext3_magic(body_payload[0], body_payload[1])
            ):
                failures.append(f"{target.record_id}: ext3 body does not start with a portal")
                continue
            try:
                text = dictionary.expand(payload[target.prefix_bytes :], self._tbl)
            except Exception as exc:
                failures.append(f"{target.record_id}: expand failed ({exc})")
                continue
            if text.rstrip("\u3000 \t") != target.korean_text.rstrip("\u3000 \t"):
                failures.append(
                    f"{target.record_id}: rendered body does not match the approved Korean"
                )
                continue
            decoded += 1

        diff_runs, diff_bytes, unaccounted = self._diff_against_baseline()
        if unaccounted:
            failures.append(
                f"{len(unaccounted)} byte run(s) outside every approved extent"
            )
        result = PrecommitResult(
            ok=not failures,
            failures=tuple(failures),
            decoded=decoded,
            extents=tuple(self._extents),
            diff_bytes=diff_bytes,
            diff_runs=diff_runs,
            unaccounted=tuple(unaccounted[:40]),
        )
        if not result.ok:
            self._log("precommit_failed", failures=list(result.failures)[:20])
            self._scratch = None
            self.state = TransactionState.ABORTED
            return result
        self.state = TransactionState.PRECOMMIT_VERIFIED
        self._log("precommit_verified", decoded=decoded, diff_bytes=diff_bytes)
        return result

    def _diff_against_baseline(self) -> tuple[int, int, list[dict[str, Any]]]:
        baseline = self._baseline
        scratch = bytes(self.scratch)
        # Extents legitimately overlap (an ext3 pointer entry sits inside the
        # bank-wide reclaim extent), so coverage is tested against a merged,
        # disjoint interval list. A binary search over raw overlapping intervals
        # can miss a hit and would report a covered byte as unaccounted.
        merged: list[list[int]] = []
        for start, end in sorted((e.start, e.end) for e in self._extents):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        def covered(offset: int) -> bool:
            lo, hi = 0, len(merged) - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                start, end = merged[mid]
                if offset < start:
                    hi = mid - 1
                elif offset >= end:
                    lo = mid + 1
                else:
                    return True
            return False

        runs = 0
        diff_bytes = 0
        unaccounted: list[dict[str, Any]] = []
        offset = 0
        size = len(baseline)
        while offset < size:
            if baseline[offset] == scratch[offset]:
                offset += 1
                continue
            start = offset
            while offset < size and baseline[offset] != scratch[offset]:
                offset += 1
            runs += 1
            diff_bytes += offset - start
            missing = [o for o in range(start, offset) if not covered(o)]
            if missing:
                unaccounted.append(
                    {
                        "start": f"{start:06X}",
                        "end": f"{offset:06X}",
                        "length": offset - start,
                        "uncovered_bytes": len(missing),
                        "baseline_hex": baseline[start : min(offset, start + 16)].hex().upper(),
                        "candidate_hex": scratch[start : min(offset, start + 16)].hex().upper(),
                    }
                )
        return runs, diff_bytes, unaccounted

    # ---------------------------------------------------------------- publish
    def write_temporary(self, path: Path) -> dict[str, Any]:
        if self.state is not TransactionState.PRECOMMIT_VERIFIED:
            self._abort(f"write_temporary called in state {self.state.value}")
        data = bytes(self.scratch)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.state = TransactionState.TEMPORARY_WRITTEN
        identity = {"path": str(path.resolve()), "size": len(data), "sha256": _sha256(data)}
        self._log("temporary_written", **identity)
        return identity

    def publish(
        self,
        temporary: Path,
        output: Path,
        *,
        gates_ok: bool,
    ) -> dict[str, Any]:
        if self.state is not TransactionState.TEMPORARY_WRITTEN:
            self._abort(f"publish called in state {self.state.value}")
        if not gates_ok:
            temporary.unlink(missing_ok=True)
            self.state = TransactionState.ABORTED
            self._log("publish_suppressed", reason="static gates did not all pass")
            return {"published": False, "reason": "static_gates_failed"}
        if temporary.resolve() == output.resolve():
            self._abort("temporary and final candidate paths must differ")
        self.state = TransactionState.STATIC_GATES_PASSED
        output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, output)
        data = output.read_bytes()
        self.state = TransactionState.PUBLISHED
        identity = {
            "published": True,
            "path": str(output.resolve()),
            "size": len(data),
            "sha256": _sha256(data),
        }
        self._log("published", **identity)
        return identity

    # ----------------------------------------------------------------- report
    def apply_report(self) -> dict[str, Any]:
        """Apply report in the shape the non-dialogue gate's allowlist reads.

        ``verify_nondialogue_text.load_name75_rewrites`` approves *deliberately*
        rewritten records from an apply report, and only when the run says it
        succeeded.  Emitting the same shape keeps that gate evidence-backed
        instead of hard-coded.
        """
        return {
            "generated_by": GENERATED_BY,
            "ok": self.state
            in (
                TransactionState.PRECOMMIT_VERIFIED,
                TransactionState.TEMPORARY_WRITTEN,
                TransactionState.STATIC_GATES_PASSED,
                TransactionState.PUBLISHED,
            ),
            "state": self.state.value,
            "applied_count": len(self._applied_rows),
            "applied": self._applied_rows,
            "ext3": self._ext3_write_info,
            "ext3_reclaim": self._reclaim_info,
            "journal": self.journal,
        }


def _original_prefix_hex(working_rom: bytes, sb: int, target: TargetPlan) -> str:
    start = sb + target.logical_address
    return bytes(working_rom[start : start + target.prefix_bytes]).hex().upper()


__all__ = [
    "LocalizationTransaction",
    "PrecommitResult",
    "TransactionAbort",
    "TransactionState",
]
