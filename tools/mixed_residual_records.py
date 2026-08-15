"""Original-ROM-derived proven text record enumeration.

Localization records and dictionary-reference records intentionally have
separate populations.  Localization is restricted to verified Script dialogue,
Name75 tables, and identity-bound aux blocks.  Reference enumeration remains
broad (script 60-6F, Name75, and aux 50-5F/76) so excluding a record from
localization can never make a shared dictionary consumer disappear.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from expand_dictionary import (
    AUX_TOKEN_BANKS,
    NAME75_RANGES,
    SCRIPT_TOKEN_BANKS,
    _walk_zstring_range,
)
from extract_script import extract_records
from mixed_residual_models import (
    DiscoveryInputIdentities,
    EvidenceIdentity,
    InputIdentityError,
    ProvenRecord,
    RecordBoundary,
    validate_discovery_inputs,
)
from monoeye_rom import BANK_SIZE, Dictionary, Tbl
from tbl_code_prefs import find_codes, flatten_codes, marker_codes

AUX_EVIDENCE_KIND = "aux_text_blocks"
AUX_EVIDENCE_PRODUCER = "tools/find_aux_text_blocks.py"
AUX_EVIDENCE_SCHEMA_VERSION = 2
NON_DIALOGUE_SCRIPT_BANKS = frozenset(range(0x64, 0x6A))


class RecordEnumerationError(ValueError):
    """Raised when proven-record evidence or Original-ROM boundaries are invalid."""


@dataclass(frozen=True)
class ExcludedProvenRecord:
    """A structurally proven record that is forbidden as a localization target."""

    record: ProvenRecord
    reason: str

    def __post_init__(self) -> None:
        if not self.reason:
            raise RecordEnumerationError("excluded records require a reason")


@dataclass(frozen=True)
class ReferenceRecord:
    """A parsed zstring retained solely for dictionary-consumer scans."""

    record_id: str
    region: str
    bank: int
    boundary: RecordBoundary
    original_payload: bytes
    kind: str
    localization_exclusion_reason: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "original_payload", bytes(self.original_payload))
        if self.region not in ("script", "name75", "aux"):
            raise RecordEnumerationError(f"unsupported reference region: {self.region}")
        if not self.original_payload:
            raise RecordEnumerationError("reference records require a payload")
        if not self.kind:
            raise RecordEnumerationError("reference records require a kind")


@dataclass(frozen=True)
class ProvenRecordPopulation:
    """Separate localization, excluded-proven, and broad reference populations."""

    localization_records: tuple[ProvenRecord, ...]
    excluded_records: tuple[ExcludedProvenRecord, ...]
    reference_records: tuple[ReferenceRecord, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "localization_records", tuple(self.localization_records))
        object.__setattr__(self, "excluded_records", tuple(self.excluded_records))
        object.__setattr__(self, "reference_records", tuple(self.reference_records))
        local_ids = [record.record_id for record in self.localization_records]
        excluded_ids = [item.record.record_id for item in self.excluded_records]
        if len(local_ids) != len(set(local_ids)):
            raise RecordEnumerationError("duplicate localization record IDs")
        if len(excluded_ids) != len(set(excluded_ids)):
            raise RecordEnumerationError("duplicate excluded record IDs")
        if set(local_ids) & set(excluded_ids):
            raise RecordEnumerationError("a record cannot be both localizable and excluded")


@dataclass(frozen=True)
class AuxTextBlock:
    bank: int
    start: int
    end_exclusive: int
    records: int


def _payload_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _parse_hex_address(value: Any, field: str) -> int:
    if not isinstance(value, str) or not value:
        raise RecordEnumerationError(f"aux block {field} must be non-empty hex text")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise RecordEnumerationError(f"aux block {field} is not hexadecimal: {value!r}") from exc


def parse_aux_text_blocks(document: Mapping[str, Any]) -> tuple[AuxTextBlock, ...]:
    """Parse identity-validated v2 aux evidence into non-overlapping intervals."""
    if document.get("generated_by") != AUX_EVIDENCE_PRODUCER:
        raise RecordEnumerationError("aux block evidence has an unexpected producer")
    if document.get("schema_version") != AUX_EVIDENCE_SCHEMA_VERSION:
        raise RecordEnumerationError(
            f"aux block evidence schema must be {AUX_EVIDENCE_SCHEMA_VERSION}"
        )
    if document.get("block_end_semantics") != "end_exclusive":
        raise RecordEnumerationError("aux block evidence lacks exclusive boundary semantics")
    rows = document.get("blocks")
    if not isinstance(rows, list):
        raise RecordEnumerationError("aux block evidence blocks must be a list")

    blocks: list[AuxTextBlock] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise RecordEnumerationError("every aux block must be an object")
        bank = _parse_hex_address(row.get("bank"), "bank")
        start = _parse_hex_address(row.get("start"), "start")
        end_exclusive = _parse_hex_address(row.get("end_exclusive"), "end_exclusive")
        records = row.get("records")
        if bank not in AUX_TOKEN_BANKS:
            raise RecordEnumerationError(f"aux block bank {bank:02X} is outside aux scope")
        if not isinstance(records, int) or isinstance(records, bool) or records <= 0:
            raise RecordEnumerationError("aux block records must be a positive integer")
        bank_start = bank * BANK_SIZE
        bank_end = bank_start + BANK_SIZE
        if not (bank_start <= start < end_exclusive <= bank_end):
            raise RecordEnumerationError(
                f"aux block {start:06X}-{end_exclusive:06X} escapes bank {bank:02X}"
            )
        blocks.append(AuxTextBlock(bank, start, end_exclusive, records))

    blocks.sort(key=lambda block: (block.start, block.end_exclusive))
    for previous, current in zip(blocks, blocks[1:]):
        if current.start < previous.end_exclusive:
            raise RecordEnumerationError(
                f"overlapping aux blocks at {previous.start:06X} and {current.start:06X}"
            )
    return tuple(blocks)


def _read_evidence_document(evidence: EvidenceIdentity) -> dict[str, Any]:
    try:
        document = json.loads(Path(evidence.path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecordEnumerationError(f"cannot read aux evidence: {exc}") from exc
    if not isinstance(document, dict):
        raise RecordEnumerationError("aux evidence root must be an object")
    return document


def _boundary_rows(
    rows: Sequence[tuple[int, bytes, str]],
) -> Iterable[tuple[int, bytes, str, RecordBoundary]]:
    for index, (logical, payload, kind) in enumerate(rows):
        terminator = logical + len(payload)
        next_start: int | None = None
        if index + 1 < len(rows):
            candidate = rows[index + 1][0]
            if candidate >> 16 == logical >> 16:
                next_start = candidate
        yield logical, payload, kind, RecordBoundary(
            start=logical,
            payload_capacity=len(payload),
            terminator_offset=terminator,
            next_record_start=next_start,
        )


def _walk_rows(
    original_rom: bytes,
    start: int,
    end: int,
    *,
    region: str,
    max_len: int,
) -> list[tuple[int, bytes, str]]:
    return list(
        _walk_zstring_range(
            original_rom, start, end, region=region, max_len=max_len
        )
    )


def _make_record(
    *,
    region: str,
    logical: int,
    payload: bytes,
    boundary: RecordBoundary,
    prefix: bytes,
    prefix_evidence: str | None,
    source_text: str,
    provenance: Sequence[str],
    status_marker_codes: Sequence[int] = (),
) -> ProvenRecord:
    return ProvenRecord(
        record_id=f"{region}:{logical:06X}",
        region=region,  # type: ignore[arg-type]
        bank=logical >> 16,
        boundary=boundary,
        original_payload_sha256=_payload_sha256(payload),
        prefix_bytes=prefix,
        prefix_evidence=prefix_evidence,
        source_text=source_text,
        rendered_body=source_text,
        provenance=tuple(provenance),
        status_marker_codes=tuple(status_marker_codes),
    )


def _enumerate_script(
    original_rom: bytes, tbl: Tbl, dictionary: Dictionary
) -> tuple[list[ProvenRecord], list[ExcludedProvenRecord]]:
    extracted = extract_records(bytearray(original_rom), tbl, dictionary)
    by_bank: dict[int, list[Any]] = {}
    for row in extracted:
        by_bank.setdefault(row.seg, []).append(row)

    local: list[ProvenRecord] = []
    excluded: list[ExcludedProvenRecord] = []
    for bank in sorted(by_bank):
        rows = by_bank[bank]
        for index, row in enumerate(rows):
            prefix = bytes.fromhex(row.prefix_hex)
            body = bytes.fromhex(row.body_hex)
            payload = prefix + body
            # ``extract_records`` returns the bank end as a synthetic terminator
            # when a non-zero tail has no NUL. Such a row has no proven zstring
            # boundary and therefore cannot enter either localization population.
            if row.abs + len(payload) >= (bank + 1) * BANK_SIZE:
                continue
            next_start = rows[index + 1].abs if index + 1 < len(rows) else None
            boundary = RecordBoundary(
                start=row.abs,
                payload_capacity=len(payload),
                terminator_offset=row.abs + len(payload),
                next_record_start=next_start,
            )
            # ``split_prefix_body`` remains useful for deriving the complete
            # Original-ROM boundary, but its inferred split is not sufficient
            # evidence to hide bytes from character classification.  Task 1.3's
            # PrefixEvidenceResolver may populate the prefix only from a
            # successful address/digest-bound report row.
            record = _make_record(
                region="script",
                logical=row.abs,
                payload=payload,
                boundary=boundary,
                prefix=b"",
                prefix_evidence=None,
                source_text=dictionary.expand(payload, tbl),
                provenance=("original_rom", "extract_script.extract_records"),
            )
            if bank in NON_DIALOGUE_SCRIPT_BANKS:
                excluded.append(ExcludedProvenRecord(record, "excluded_non_dialogue_bank"))
            elif row.kind != "dialogue":
                excluded.append(ExcludedProvenRecord(record, "excluded_non_dialogue_kind"))
            else:
                local.append(record)
    return local, excluded


def _enumerate_name75(
    original_rom: bytes, tbl: Tbl, dictionary: Dictionary
) -> list[ProvenRecord]:
    out: list[ProvenRecord] = []
    wanted_markers = marker_codes(tbl)
    for lo, hi in NAME75_RANGES:
        rows = _walk_rows(original_rom, lo, hi, region="name75", max_len=64)
        for logical, payload, _kind, boundary in _boundary_rows(rows):
            source = dictionary.expand(payload, tbl)
            markers = find_codes(flatten_codes(payload, dictionary), wanted_markers)
            out.append(
                _make_record(
                    region="name75",
                    logical=logical,
                    payload=payload,
                    boundary=boundary,
                    prefix=b"",
                    prefix_evidence=None,
                    source_text=source,
                    provenance=(
                        "original_rom",
                        "expand_dictionary.NAME75_RANGES",
                        "apply_name75_ko.status_marker_guard",
                    ),
                    status_marker_codes=markers,
                )
            )
    return out


def _enumerate_aux(
    original_rom: bytes,
    tbl: Tbl,
    dictionary: Dictionary,
    blocks: Sequence[AuxTextBlock],
) -> tuple[list[ProvenRecord], list[ExcludedProvenRecord]]:
    local: list[ProvenRecord] = []
    excluded: list[ExcludedProvenRecord] = []
    for block in blocks:
        rows = _walk_rows(
            original_rom,
            block.start,
            block.end_exclusive,
            region="aux",
            max_len=128,
        )
        if len(rows) != block.records:
            raise RecordEnumerationError(
                f"aux block {block.start:06X} declared {block.records} records but "
                f"Original ROM yields {len(rows)}"
            )
        for logical, payload, _kind, boundary in _boundary_rows(rows):
            if boundary.terminator_offset >= block.end_exclusive:
                raise RecordEnumerationError(
                    f"aux record {logical:06X} crosses its verified block boundary"
                )
            record = _make_record(
                region="aux",
                logical=logical,
                payload=payload,
                boundary=boundary,
                prefix=b"",
                prefix_evidence=None,
                source_text=dictionary.expand(payload, tbl),
                provenance=(
                    "original_rom",
                    "aux_text_blocks.json",
                    f"vetted_block:{block.start:06X}-{block.end_exclusive:06X}",
                ),
            )
            if block.bank == 0x5F:
                excluded.append(ExcludedProvenRecord(record, "excluded_dictionary_storage"))
            else:
                local.append(record)
    return local, excluded


def _reference_records(
    original_rom: bytes,
    localizable_ids: set[str],
    excluded_reasons: Mapping[str, str],
) -> list[ReferenceRecord]:
    scopes: list[tuple[str, int, int, int]] = []
    scopes.extend(
        ("script", bank * BANK_SIZE, (bank + 1) * BANK_SIZE, 256)
        for bank in SCRIPT_TOKEN_BANKS
    )
    scopes.extend(("name75", lo, hi, 64) for lo, hi in NAME75_RANGES)
    scopes.extend(
        ("aux", bank * BANK_SIZE, (bank + 1) * BANK_SIZE, 128)
        for bank in AUX_TOKEN_BANKS
    )

    out: list[ReferenceRecord] = []
    seen: set[tuple[str, int]] = set()
    for region, lo, hi, max_len in scopes:
        rows = _walk_rows(original_rom, lo, hi, region=region, max_len=max_len)
        for logical, payload, kind, boundary in _boundary_rows(rows):
            key = (region, logical)
            if key in seen:
                continue
            seen.add(key)
            record_id = f"{region}:{logical:06X}"
            reason = excluded_reasons.get(record_id)
            if reason is None and record_id not in localizable_ids:
                if region == "aux":
                    reason = (
                        "excluded_dictionary_storage"
                        if logical >> 16 == 0x5F
                        else "excluded_aux_unvetted"
                    )
                elif region == "script":
                    reason = (
                        "excluded_non_dialogue_bank"
                        if logical >> 16 in NON_DIALOGUE_SCRIPT_BANKS
                        else "excluded_non_dialogue_kind"
                    )
            out.append(
                ReferenceRecord(
                    record_id=record_id,
                    region=region,
                    bank=logical >> 16,
                    boundary=boundary,
                    original_payload=payload,
                    kind=kind,
                    localization_exclusion_reason=reason,
                )
            )
    return out


def enumerate_original_rom_records(
    original_rom: bytes,
    tbl: Tbl,
    aux_document: Mapping[str, Any],
) -> ProvenRecordPopulation:
    """Enumerate from already identity-validated Original ROM bytes and evidence.

    Production callers should use :class:`OriginalRomProvenRecordEnumerator`,
    which performs the required identity validation before reaching this pure
    boundary-enumeration core.
    """
    blocks = parse_aux_text_blocks(aux_document)
    dictionary = Dictionary(original_rom)
    script, script_excluded = _enumerate_script(original_rom, tbl, dictionary)
    name75 = _enumerate_name75(original_rom, tbl, dictionary)
    aux, aux_excluded = _enumerate_aux(original_rom, tbl, dictionary, blocks)

    local = sorted(script + name75 + aux, key=lambda record: (record.region, record.boundary.start))
    excluded = sorted(
        script_excluded + aux_excluded,
        key=lambda item: (item.record.region, item.record.boundary.start),
    )
    local_ids = {record.record_id for record in local}
    excluded_reasons = {item.record.record_id: item.reason for item in excluded}
    references = _reference_records(original_rom, local_ids, excluded_reasons)
    return ProvenRecordPopulation(tuple(local), tuple(excluded), tuple(references))


class OriginalRomProvenRecordEnumerator:
    """Fail-closed public enumerator bound to locked ROM/evidence identities."""

    def enumerate(
        self, inputs: DiscoveryInputIdentities, tbl: Tbl
    ) -> ProvenRecordPopulation:
        validated = validate_discovery_inputs(inputs)
        matching = [
            evidence
            for evidence in validated.evidence
            if evidence.kind == AUX_EVIDENCE_KIND
        ]
        if len(matching) != 1:
            raise InputIdentityError(
                "discovery requires exactly one aux_text_blocks evidence artifact"
            )
        evidence = matching[0]
        if evidence.generated_by != AUX_EVIDENCE_PRODUCER:
            raise InputIdentityError(
                f"aux_text_blocks must be generated by {AUX_EVIDENCE_PRODUCER}"
            )
        original_rom = Path(validated.original_rom.path).read_bytes()
        document = _read_evidence_document(evidence)
        return enumerate_original_rom_records(original_rom, tbl, document)


__all__ = [
    "AUX_EVIDENCE_KIND",
    "AUX_EVIDENCE_PRODUCER",
    "AUX_EVIDENCE_SCHEMA_VERSION",
    "AuxTextBlock",
    "ExcludedProvenRecord",
    "NON_DIALOGUE_SCRIPT_BANKS",
    "OriginalRomProvenRecordEnumerator",
    "ProvenRecordPopulation",
    "RecordEnumerationError",
    "ReferenceRecord",
    "enumerate_original_rom_records",
    "parse_aux_text_blocks",
]
