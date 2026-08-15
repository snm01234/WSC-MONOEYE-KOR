"""Trustworthy prefix resolution and rendered-body classification.

Prefix removal is deliberately fail-closed.  A structural-looking leading byte
is not enough: both the report and its row must be successful, and the row must
bind the positive prefix length and bytes to this record's Original-ROM address
and payload digest.  Any disagreement leaves the complete decoded record as the
rendered body.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from mixed_residual_models import CandidateDecision, ProvenRecord
from mixed_residual_records import NON_DIALOGUE_SCRIPT_BANKS

CORE_EXCLUDED_CHARACTERS = frozenset("…。、！？")
MIDDLE_DOT = "・"


@dataclass(frozen=True)
class PrefixResolution:
    """Result of validating one record's prefix evidence."""

    prefix_bytes: bytes
    trusted: bool
    reason: str
    evidence: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "prefix_bytes", bytes(self.prefix_bytes))
        if self.trusted != bool(self.prefix_bytes):
            raise ValueError("trusted prefix resolutions require non-empty bytes")
        if not self.reason:
            raise ValueError("prefix resolutions require a reason")
        if self.trusted != (self.evidence is not None):
            raise ValueError("trusted prefix resolutions require an evidence label")

    @property
    def prefix_length(self) -> int:
        return len(self.prefix_bytes)


class PrefixEvidenceResolver:
    """Validate report evidence before allowing any decoded prefix exclusion."""

    _ROW_FIELDS = ("applied", "rows")
    _INDEX_CACHE_LIMIT = 8

    def __init__(self) -> None:
        # Address lookup is memoized per report object so that resolving a whole
        # population does not rescan every evidence row for every record.  The
        # cache keeps the report alive, so an ``id`` key cannot be reused by a
        # different object, and it never changes which rows a record matches.
        self._row_index_cache: dict[
            int, tuple[Mapping[str, Any], dict[int, tuple[Mapping[str, Any], ...]]]
        ] = {}

    @staticmethod
    def _untrusted(reason: str) -> PrefixResolution:
        return PrefixResolution(b"", False, reason)

    def _rows_by_address(
        self, report: Mapping[str, Any], rows: Sequence[Any]
    ) -> dict[int, tuple[Mapping[str, Any], ...]]:
        """Group evidence rows by parsed ``abs`` address, preserving row order.

        This is a lookup accelerator only: a row that is not a mapping, or whose
        ``abs`` cannot be parsed, is absent here exactly as it fails the
        equality test in a linear scan.
        """
        cached = self._row_index_cache.get(id(report))
        if cached is not None and cached[0] is report:
            return cached[1]
        grouped: dict[int, list[Mapping[str, Any]]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            address = self._parse_address(row.get("abs"))
            if address is None:
                continue
            grouped.setdefault(address, []).append(row)
        index = {address: tuple(items) for address, items in grouped.items()}
        if len(self._row_index_cache) >= self._INDEX_CACHE_LIMIT:
            self._row_index_cache.clear()
        self._row_index_cache[id(report)] = (report, index)
        return index

    @staticmethod
    def _parse_address(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if not isinstance(value, str):
            return None
        text = value.strip()
        if re.fullmatch(r"[0-9A-Fa-f]{2}:[0-9A-Fa-f]{1,4}", text):
            text = text.replace(":", "")
        elif text.lower().startswith("0x"):
            text = text[2:]
        if not text or re.fullmatch(r"[0-9A-Fa-f]+", text) is None:
            return None
        return int(text, 16)

    def resolve(
        self,
        record: ProvenRecord,
        original_payload: bytes,
        report: Mapping[str, Any] | None,
        *,
        evidence_name: str = "prefix_report",
    ) -> PrefixResolution:
        """Return a trusted positive prefix or an empty fail-closed result."""
        payload = bytes(original_payload)
        expected_digest = hashlib.sha256(payload).hexdigest()
        if (
            len(payload) != record.boundary.payload_capacity
            or expected_digest != record.original_payload_sha256
        ):
            return self._untrusted("original_payload_mismatch")
        if not isinstance(report, Mapping):
            return self._untrusted("prefix_report_missing")
        if report.get("ok") is not True:
            return self._untrusted("prefix_report_not_successful")

        present_fields = [field for field in self._ROW_FIELDS if field in report]
        if len(present_fields) != 1:
            return self._untrusted("prefix_report_rows_invalid")
        rows = report.get(present_fields[0])
        if not isinstance(rows, list):
            return self._untrusted("prefix_report_rows_invalid")

        matching_rows = self._rows_by_address(report, rows).get(
            record.boundary.start, ()
        )
        if not matching_rows:
            return self._untrusted("prefix_row_not_found")
        if len(matching_rows) != 1:
            return self._untrusted("prefix_row_ambiguous")
        row = matching_rows[0]

        if row.get("ok") is not True:
            return self._untrusted("prefix_row_not_successful")
        prefix_length = row.get("prefix_bytes")
        if (
            isinstance(prefix_length, bool)
            or not isinstance(prefix_length, int)
            or prefix_length <= 0
            or prefix_length > len(payload)
        ):
            return self._untrusted("prefix_length_invalid")
        if row.get("original_payload_sha256") != expected_digest:
            return self._untrusted("prefix_digest_mismatch")

        prefix_hex = row.get("prefix_hex")
        if not isinstance(prefix_hex, str):
            return self._untrusted("prefix_hex_invalid")
        try:
            reported_prefix = bytes.fromhex(prefix_hex)
        except ValueError:
            return self._untrusted("prefix_hex_invalid")
        if len(reported_prefix) != prefix_length:
            return self._untrusted("prefix_length_hex_mismatch")
        if reported_prefix != payload[:prefix_length]:
            return self._untrusted("prefix_bytes_mismatch")

        evidence = f"{evidence_name}:{record.boundary.start:06X}"
        return PrefixResolution(reported_prefix, True, "trusted_prefix", evidence)

    def resolve_record(
        self,
        record: ProvenRecord,
        original_payload: bytes,
        rendered_payload: bytes,
        decoder: Callable[[bytes], str],
        report: Mapping[str, Any] | None,
        *,
        evidence_name: str = "prefix_report",
    ) -> ProvenRecord:
        """Decode a record after removing only a prefix proven by ``resolve``.

        A candidate/working payload whose leading bytes no longer equal the
        proven Original prefix is classified whole as an additional fail-closed
        guard against hiding changed display text.
        """
        payload = bytes(rendered_payload)
        resolution = self.resolve(
            record, original_payload, report, evidence_name=evidence_name
        )
        if resolution.trusted and not payload.startswith(resolution.prefix_bytes):
            resolution = self._untrusted("rendered_prefix_mismatch")
        full_text = decoder(payload)
        body_payload = payload[resolution.prefix_length :]
        body_text = decoder(body_payload) if resolution.trusted else full_text
        return replace(
            record,
            prefix_bytes=resolution.prefix_bytes,
            prefix_evidence=resolution.evidence,
            source_text=full_text,
            rendered_body=body_text,
        )


def is_japanese_character(character: str) -> bool:
    """Return whether one code point is Japanese under the feature contract.

    U+30FB KATAKANA MIDDLE DOT is excluded.  It lives inside the katakana block
    but is a name separator, not a letter: Korean renders foreign names as
    ``돔・바인니히츠``, so counting it as Japanese would flag finished Korean text
    and demand rewriting already-localized name tables.  The same reasoning is
    recorded in ``tools/scan_fragment_composition_hazard.py``.
    """
    return (
        len(character) == 1
        and character != MIDDLE_DOT
        and (
            "\u3040" <= character <= "\u309f"
            or "\u30a0" <= character <= "\u30ff"
            or "\u4e00" <= character <= "\u9fff"
        )
    )


def is_hangul_character(character: str) -> bool:
    """Return whether one code point is a precomposed Hangul syllable."""
    return len(character) == 1 and "\uac00" <= character <= "\ud7a3"


def japanese_character_count(text: str) -> int:
    return sum(is_japanese_character(character) for character in text)


def hangul_character_count(text: str) -> int:
    return sum(is_hangul_character(character) for character in text)


def core_character_count(text: str) -> int:
    """Count characters excluding whitespace and specified sentence punctuation."""
    return sum(
        not character.isspace() and character not in CORE_EXCLUDED_CHARACTERS
        for character in text
    )


def _is_katakana_glue(character: str) -> bool:
    return bool(character) and character != MIDDLE_DOT and "\u30a0" <= character <= "\u30ff"


def defect_annotations(text: str) -> tuple[str, ...]:
    """Return stable whole-expression defect annotations for a rendered body."""
    found: set[str] = set()
    for index, character in enumerate(text):
        if not is_hangul_character(character):
            continue
        previous = text[index - 1] if index else ""
        following = text[index + 1] if index + 1 < len(text) else ""
        if _is_katakana_glue(previous) or _is_katakana_glue(following):
            found.add("broken_word")
        if (
            previous == MIDDLE_DOT
            and index >= 2
            and _is_katakana_glue(text[index - 2])
        ) or (
            following == MIDDLE_DOT
            and index + 2 < len(text)
            and _is_katakana_glue(text[index + 2])
        ):
            found.add("split_compound")
        if following and "\u3040" <= following <= "\u309f":
            found.add("japanese_particle")
    order = ("broken_word", "split_compound", "japanese_particle")
    return tuple(annotation for annotation in order if annotation in found)


def classify_record(
    record: ProvenRecord,
    *,
    proven: bool = True,
    exclusion_reason: str | None = None,
) -> CandidateDecision:
    """Classify one rendered body using the region-specific target predicates."""
    body = record.rendered_body
    japanese = japanese_character_count(body)
    hangul = hangul_character_count(body)
    core = core_character_count(body)
    annotations = defect_annotations(body)

    automatic_exclusion: str | None = exclusion_reason
    if not proven:
        automatic_exclusion = "excluded_unproven"
    elif record.region == "aux" and record.bank == 0x5F:
        automatic_exclusion = "excluded_dictionary_storage"
    elif record.region == "aux" and not any(
        item.startswith("vetted_block:") for item in record.provenance
    ):
        automatic_exclusion = "excluded_aux_unvetted"
    elif record.region == "script" and record.bank in NON_DIALOGUE_SCRIPT_BANKS:
        automatic_exclusion = "excluded_non_dialogue_bank"

    if automatic_exclusion is not None:
        classification = "excluded"
        included = False
        reason = automatic_exclusion
    elif japanese and hangul:
        classification = "mixed"
        included = True
        reason = "mixed_hangul_and_japanese"
    elif japanese and record.region == "script":
        classification = "jp_only"
        included = True
        reason = "script_japanese_only"
    elif japanese and record.region == "aux" and core >= 6:
        classification = "jp_only"
        included = True
        reason = "aux_japanese_only_sentence"
    elif japanese and record.region == "aux":
        classification = "excluded"
        included = False
        reason = "excluded_aux_below_core_threshold"
    elif japanese:
        classification = "excluded"
        included = False
        reason = "excluded_name75_japanese_only"
    elif hangul:
        classification = "ko_only"
        included = False
        reason = "hangul_without_japanese"
    else:
        classification = "no_text"
        included = False
        reason = "no_japanese_or_hangul"

    return CandidateDecision(
        record_id=record.record_id,
        logical_address=record.boundary.start,
        region=record.region,
        source_classification=classification,  # type: ignore[arg-type]
        rendered_source_text=body,
        provenance=record.provenance,
        japanese_count=japanese,
        hangul_count=hangul,
        core_count=core,
        included=included,
        reason=reason,
        annotations=annotations,
    )


__all__ = [
    "CORE_EXCLUDED_CHARACTERS",
    "PrefixEvidenceResolver",
    "PrefixResolution",
    "classify_record",
    "core_character_count",
    "defect_annotations",
    "hangul_character_count",
    "is_hangul_character",
    "is_japanese_character",
    "japanese_character_count",
]
