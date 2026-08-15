"""Shared immutable models and input identities for residual localization.

This module is deliberately independent of record enumeration and ROM writing.
Discovery callers must construct a :class:`DiscoveryInputIdentities` lock and
pass it to :func:`validate_discovery_inputs` before reading any record boundary.
Evidence JSON is fail-closed: it must identify the Original ROM by size and
SHA-256, not merely by a path.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from monoeye_rom import ROM_SIZE, ROM_SIZE_16MB

Region = Literal["script", "name75", "aux"]
Classification = Literal["mixed", "jp_only", "ko_only", "no_text", "excluded"]
DictionaryStrategy = Literal["ext3", "true_free", "pair_steal"]
#: ``header_checksum`` covers the two WonderSwan header bytes that every writer
#: must refresh. They are not a target, slot or retarget, but they do change, and
#: an unaccounted diff would be worse than naming the kind explicitly.
ExtentKind = Literal[
    "record_body",
    "dictionary_pointer",
    "dictionary_payload",
    "consumer_retarget",
    "header_checksum",
]
RomRole = Literal["original", "working", "accepted_baseline", "candidate"]
SeenIn = Literal["original", "working"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_REGIONS = frozenset(("script", "name75", "aux"))
_ALLOWED_CLASSIFICATIONS = frozenset(
    ("mixed", "jp_only", "ko_only", "no_text", "excluded")
)
_ALLOWED_STRATEGIES = frozenset(("ext3", "true_free", "pair_steal"))
_ALLOWED_EXTENT_KINDS = frozenset(
    (
        "record_body",
        "dictionary_pointer",
        "dictionary_payload",
        "consumer_retarget",
        "header_checksum",
    )
)
_ALLOWED_ROM_ROLES = frozenset(
    ("original", "working", "accepted_baseline", "candidate")
)
_ALLOWED_SEEN_IN = frozenset(("original", "working"))


class ModelValidationError(ValueError):
    """Raised when an immutable model would represent an invalid state."""


class InputIdentityError(ValueError):
    """Raised before discovery when a ROM or evidence identity is invalid."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ModelValidationError(message)


def _validate_sha256(value: str, field_name: str = "sha256") -> None:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{field_name} must be a lowercase 64-character SHA-256 hex digest",
    )


def _json_value(value: Any) -> Any:
    """Convert supported immutable values into deterministic JSON data."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (frozenset, set)):
        converted = [_json_value(item) for item in value]
        return sorted(
            converted,
            key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported deterministic JSON value: {type(value).__name__}")


def deterministic_json_data(value: Any) -> Any:
    """Return a JSON-compatible representation with deterministic set ordering."""
    return _json_value(value)


def deterministic_json_dumps(value: Any, *, indent: int | None = 2) -> str:
    """Serialize models and immutable containers deterministically.

    Pretty output ends in one newline. ``indent=None`` produces canonical compact
    bytes suitable for identity digests.
    """
    data = deterministic_json_data(value)
    if indent is None:
        return json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=indent) + "\n"


def deterministic_json_sha256(value: Any) -> str:
    encoded = deterministic_json_dumps(value, indent=None).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DeterministicJsonModel:
    """Mixin shared by frozen data models."""

    def to_json_data(self) -> dict[str, Any]:
        data = deterministic_json_data(self)
        if not isinstance(data, dict):  # pragma: no cover - dataclass contract
            raise TypeError("model did not serialize to a JSON object")
        return data

    def to_json(self, *, indent: int | None = 2) -> str:
        return deterministic_json_dumps(self, indent=indent)

    def json_sha256(self) -> str:
        return deterministic_json_sha256(self)


@dataclass(frozen=True)
class RecordBoundary(DeterministicJsonModel):
    start: int
    payload_capacity: int
    terminator_offset: int
    next_record_start: int | None
    derived_from: Literal["original_rom"] = "original_rom"

    def __post_init__(self) -> None:
        _require(self.start >= 0, "record start must be non-negative")
        _require(self.payload_capacity >= 0, "payload capacity must be non-negative")
        _require(
            self.terminator_offset == self.start + self.payload_capacity,
            "terminator offset must equal start plus Original-derived payload capacity",
        )
        _require(
            self.next_record_start is None
            or self.next_record_start > self.terminator_offset,
            "next record must begin after the terminator",
        )
        _require(self.derived_from == "original_rom", "boundary must derive from Original ROM")


@dataclass(frozen=True)
class ProvenRecord(DeterministicJsonModel):
    record_id: str
    region: Region
    bank: int
    boundary: RecordBoundary
    original_payload_sha256: str
    prefix_bytes: bytes
    prefix_evidence: str | None
    source_text: str
    rendered_body: str
    provenance: tuple[str, ...]
    status_marker_codes: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "prefix_bytes", bytes(self.prefix_bytes))
        object.__setattr__(self, "provenance", tuple(self.provenance))
        object.__setattr__(self, "status_marker_codes", tuple(self.status_marker_codes))
        _require(bool(self.record_id), "record_id must not be empty")
        _require(self.region in _ALLOWED_REGIONS, f"unsupported record region: {self.region}")
        _require(0 <= self.bank <= 0xFF, "bank must fit in one byte")
        _require(
            all(isinstance(code, int) and 0 <= code <= 0xFFFF for code in self.status_marker_codes),
            "status marker codes must be 16-bit integers",
        )
        _require(
            self.region == "name75" or not self.status_marker_codes,
            "status marker metadata is only valid for Name75 records",
        )
        _validate_sha256(self.original_payload_sha256, "original_payload_sha256")
        _require(
            len(self.prefix_bytes) <= self.boundary.payload_capacity,
            "prefix exceeds Original-derived payload capacity",
        )
        _require(bool(self.provenance), "proven records require provenance")
        _require(
            all(isinstance(item, str) and item for item in self.provenance),
            "provenance entries must be non-empty strings",
        )


@dataclass(frozen=True)
class CandidateDecision(DeterministicJsonModel):
    record_id: str
    logical_address: int
    region: Region
    source_classification: Classification
    rendered_source_text: str
    provenance: tuple[str, ...]
    japanese_count: int
    hangul_count: int
    core_count: int
    included: bool
    reason: str
    annotations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", tuple(self.provenance))
        object.__setattr__(self, "annotations", tuple(self.annotations))
        _require(bool(self.record_id), "decision record_id must not be empty")
        _require(self.logical_address >= 0, "logical address must be non-negative")
        _require(self.region in _ALLOWED_REGIONS, f"unsupported decision region: {self.region}")
        _require(
            self.source_classification in _ALLOWED_CLASSIFICATIONS,
            f"unsupported classification: {self.source_classification}",
        )
        _require(
            self.japanese_count >= 0 and self.hangul_count >= 0 and self.core_count >= 0,
            "character counts must be non-negative",
        )
        _require(bool(self.reason), "candidate decisions require a reason")
        _require(bool(self.provenance), "candidate decisions require provenance")
        _require(
            self.included == (self.source_classification in ("mixed", "jp_only")),
            "only mixed or jp_only decisions may be included",
        )


@dataclass(frozen=True)
class DictionaryConsumer(DeterministicJsonModel):
    index: int
    abs: int
    region: Region
    kind: str
    seen_in: frozenset[SeenIn]

    def __post_init__(self) -> None:
        object.__setattr__(self, "seen_in", frozenset(self.seen_in))
        _require(self.index >= 0, "dictionary index must be non-negative")
        _require(self.abs >= 0, "consumer address must be non-negative")
        _require(self.region in _ALLOWED_REGIONS, f"unsupported consumer region: {self.region}")
        _require(bool(self.kind), "consumer kind must not be empty")
        _require(bool(self.seen_in), "consumer must be seen in at least one input ROM")
        _require(
            self.seen_in <= _ALLOWED_SEEN_IN,
            f"unsupported source ROM labels: {sorted(self.seen_in - _ALLOWED_SEEN_IN)}",
        )


@dataclass(frozen=True)
class DictionaryPlan(DeterministicJsonModel):
    strategy: DictionaryStrategy
    new_slot: int
    preserve_slot: int | None
    former_consumers: frozenset[DictionaryConsumer]
    keepers: frozenset[int]
    restore_or_retarget: frozenset[int]
    guard_outcome: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "former_consumers", frozenset(self.former_consumers))
        object.__setattr__(self, "keepers", frozenset(self.keepers))
        object.__setattr__(
            self, "restore_or_retarget", frozenset(self.restore_or_retarget)
        )
        _require(self.strategy in _ALLOWED_STRATEGIES, f"unsupported strategy: {self.strategy}")
        _require(self.new_slot >= 0, "new dictionary slot must be non-negative")
        _require(
            self.preserve_slot is None or self.preserve_slot >= 0,
            "preserve dictionary slot must be non-negative",
        )
        _require(
            self.strategy == "pair_steal" or self.preserve_slot is None,
            "only pair-steal plans may specify a preserve slot",
        )
        _require(
            self.strategy != "pair_steal" or self.preserve_slot is not None,
            "pair-steal plans require a preserve slot",
        )
        _require(
            self.preserve_slot != self.new_slot,
            "new and preserve dictionary slots must differ",
        )
        _require(bool(self.guard_outcome), "dictionary plans require a guard outcome")
        addresses = frozenset(consumer.abs for consumer in self.former_consumers)
        _require(
            all(consumer.index == self.new_slot for consumer in self.former_consumers),
            "every former consumer must reference the overwritten slot",
        )
        _require(self.keepers.isdisjoint(self.restore_or_retarget), "consumer actions overlap")
        _require(
            self.keepers | self.restore_or_retarget == addresses,
            "every former consumer must be a keeper or restored/retargeted",
        )


@dataclass(frozen=True)
class ApprovedChangeExtent(DeterministicJsonModel):
    start: int
    end: int
    kind: ExtentKind
    owner_id: str

    def __post_init__(self) -> None:
        _require(self.start >= 0 and self.end > self.start, "extent must be non-empty")
        _require(self.kind in _ALLOWED_EXTENT_KINDS, f"unsupported extent kind: {self.kind}")
        _require(bool(self.owner_id), "approved extent requires an owner_id")


@dataclass(frozen=True)
class RewritePlan(DeterministicJsonModel):
    record: ProvenRecord
    korean_text: str
    encoded_payload: bytes
    dictionary: DictionaryPlan
    approved_extents: tuple[ApprovedChangeExtent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "encoded_payload", bytes(self.encoded_payload))
        object.__setattr__(self, "approved_extents", tuple(self.approved_extents))
        _require(bool(self.korean_text), "rewrite plan requires Korean text")
        _require(bool(self.encoded_payload), "rewrite plan requires an encoded payload")
        _require(bool(self.approved_extents), "rewrite plan requires approved extents")


@dataclass(frozen=True)
class RomIdentity(DeterministicJsonModel):
    role: RomRole
    path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(self.path))
        _require(self.role in _ALLOWED_ROM_ROLES, f"unsupported ROM role: {self.role}")
        _require(bool(self.path), "ROM identity path must not be empty")
        _require(self.size > 0, "ROM identity size must be positive")
        _validate_sha256(self.sha256)


@dataclass(frozen=True)
class EvidenceIdentity(DeterministicJsonModel):
    kind: str
    path: str
    size: int
    sha256: str
    generated_by: str
    original_rom: RomIdentity

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", str(self.path))
        _require(bool(self.kind), "evidence kind must not be empty")
        _require(bool(self.path), "evidence identity path must not be empty")
        _require(self.size > 0, "evidence identity size must be positive")
        _validate_sha256(self.sha256)
        _require(bool(self.generated_by), "evidence generated_by must not be empty")
        _require(
            self.original_rom.role == "original",
            "evidence must bind to an Original ROM identity",
        )


@dataclass(frozen=True)
class DiscoveryInputIdentities(DeterministicJsonModel):
    original_rom: RomIdentity
    working_rom: RomIdentity
    evidence: tuple[EvidenceIdentity, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        _require(self.original_rom.role == "original", "original_rom has the wrong role")
        _require(self.working_rom.role == "working", "working_rom has the wrong role")
        kinds = [item.kind for item in self.evidence]
        _require(len(kinds) == len(set(kinds)), "evidence kinds must be unique")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def identify_rom(path: str | Path, role: RomRole) -> RomIdentity:
    """Measure a ROM without accepting it for discovery."""
    resolved = _resolved(path)
    if not resolved.is_file():
        raise InputIdentityError(f"{role} ROM does not exist: {resolved}")
    size = resolved.stat().st_size
    if size not in (ROM_SIZE, ROM_SIZE_16MB):
        raise InputIdentityError(
            f"{role} ROM has invalid size {size:#x}; expected {ROM_SIZE:#x} or "
            f"{ROM_SIZE_16MB:#x}"
        )
    if role == "original" and size != ROM_SIZE:
        raise InputIdentityError(
            f"Original ROM must be exactly {ROM_SIZE:#x} bytes, got {size:#x}"
        )
    return RomIdentity(role=role, path=str(resolved), size=size, sha256=_sha256_path(resolved))


def _validate_path_identity(
    path: Path, *, expected_size: int, expected_sha256: str, label: str
) -> None:
    if not path.is_file():
        raise InputIdentityError(f"{label} does not exist: {path}")
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise InputIdentityError(
            f"{label} size mismatch: expected {expected_size}, got {actual_size}"
        )
    actual_sha256 = _sha256_path(path)
    if actual_sha256 != expected_sha256:
        raise InputIdentityError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )


def validate_rom_identity(expected: RomIdentity) -> RomIdentity:
    """Validate a locked ROM path, legal size, and SHA-256."""
    path = _resolved(expected.path)
    if expected.size not in (ROM_SIZE, ROM_SIZE_16MB):
        raise InputIdentityError(
            f"{expected.role} ROM lock has invalid size {expected.size:#x}"
        )
    if expected.role == "original" and expected.size != ROM_SIZE:
        raise InputIdentityError("Original ROM identity is not for an 8 MiB image")
    _validate_path_identity(
        path,
        expected_size=expected.size,
        expected_sha256=expected.sha256,
        label=f"{expected.role} ROM",
    )
    return RomIdentity(
        role=expected.role,
        path=str(path),
        size=expected.size,
        sha256=expected.sha256,
    )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputIdentityError(f"{label} is not valid UTF-8 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InputIdentityError(f"{label} root must be a JSON object: {path}")
    return value


def _declared_original_rom(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    direct = document.get("original_rom_identity")
    if isinstance(direct, Mapping):
        return direct
    inputs = document.get("inputs")
    if isinstance(inputs, Mapping):
        nested = inputs.get("original_rom")
        if isinstance(nested, Mapping):
            return nested
    identity = document.get("identity")
    if isinstance(identity, Mapping):
        nested = identity.get("original_rom")
        if isinstance(nested, Mapping):
            return nested
    return None


def _validate_evidence_document(
    document: Mapping[str, Any], expected: EvidenceIdentity, original_rom: RomIdentity
) -> None:
    if document.get("generated_by") != expected.generated_by:
        raise InputIdentityError(
            f"{expected.kind} generated_by mismatch: expected {expected.generated_by!r}, "
            f"got {document.get('generated_by')!r}"
        )
    declared = _declared_original_rom(document)
    if declared is None:
        raise InputIdentityError(
            f"{expected.kind} lacks an Original ROM identity with size and SHA-256"
        )
    if declared.get("size") != original_rom.size:
        raise InputIdentityError(
            f"{expected.kind} Original ROM size mismatch: expected {original_rom.size}, "
            f"got {declared.get('size')!r}"
        )
    if declared.get("sha256") != original_rom.sha256:
        raise InputIdentityError(
            f"{expected.kind} Original ROM SHA-256 mismatch: expected "
            f"{original_rom.sha256}, got {declared.get('sha256')!r}"
        )


def identify_evidence(
    path: str | Path,
    *,
    kind: str,
    generated_by: str,
    original_rom: RomIdentity,
) -> EvidenceIdentity:
    """Measure identity-bearing JSON evidence and bind it to Original ROM."""
    resolved = _resolved(path)
    if not resolved.is_file():
        raise InputIdentityError(f"{kind} evidence does not exist: {resolved}")
    document = _read_json_object(resolved, f"{kind} evidence")
    provisional = EvidenceIdentity(
        kind=kind,
        path=str(resolved),
        size=resolved.stat().st_size,
        sha256=_sha256_path(resolved),
        generated_by=generated_by,
        original_rom=original_rom,
    )
    _validate_evidence_document(document, provisional, original_rom)
    return provisional


def validate_evidence_identity(
    expected: EvidenceIdentity, original_rom: RomIdentity
) -> EvidenceIdentity:
    """Validate evidence bytes, producer identity, and Original-ROM binding."""
    if (
        expected.original_rom.size != original_rom.size
        or expected.original_rom.sha256 != original_rom.sha256
    ):
        raise InputIdentityError(
            f"{expected.kind} lock is bound to a different Original ROM"
        )
    path = _resolved(expected.path)
    _validate_path_identity(
        path,
        expected_size=expected.size,
        expected_sha256=expected.sha256,
        label=f"{expected.kind} evidence",
    )
    document = _read_json_object(path, f"{expected.kind} evidence")
    _validate_evidence_document(document, expected, original_rom)
    return EvidenceIdentity(
        kind=expected.kind,
        path=str(path),
        size=expected.size,
        sha256=expected.sha256,
        generated_by=expected.generated_by,
        original_rom=original_rom,
    )


def validate_discovery_inputs(
    expected: DiscoveryInputIdentities,
) -> DiscoveryInputIdentities:
    """Validate every locked input before record discovery starts.

    The function performs no enumeration and returns only after both ROMs and
    every evidence artifact have passed size, digest, producer, and source-ROM
    checks. Callers must not fall back to unvalidated evidence when this raises.
    """
    original = validate_rom_identity(expected.original_rom)
    working = validate_rom_identity(expected.working_rom)
    evidence = tuple(
        validate_evidence_identity(item, original) for item in expected.evidence
    )
    return DiscoveryInputIdentities(
        original_rom=original,
        working_rom=working,
        evidence=evidence,
    )


__all__ = [
    "ApprovedChangeExtent",
    "CandidateDecision",
    "Classification",
    "DeterministicJsonModel",
    "DictionaryConsumer",
    "DictionaryPlan",
    "DictionaryStrategy",
    "DiscoveryInputIdentities",
    "EvidenceIdentity",
    "ExtentKind",
    "InputIdentityError",
    "ModelValidationError",
    "ProvenRecord",
    "RecordBoundary",
    "Region",
    "RewritePlan",
    "RomIdentity",
    "deterministic_json_data",
    "deterministic_json_dumps",
    "deterministic_json_sha256",
    "identify_evidence",
    "identify_rom",
    "validate_discovery_inputs",
    "validate_evidence_identity",
    "validate_rom_identity",
]
