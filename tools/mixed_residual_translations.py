"""Reviewed translation catalog, terminology index, and fail-closed validation.

The catalog is *data*, not a generator.  Every row must be bound to one target
row of a digest-valid target manifest by record id, region, logical address,
rendered source body, and source digest.  Nothing here invents a translation,
resolves a terminology conflict, or scores semantic intent: an unproven row
becomes an unresolved target, and any unresolved target blocks static
acceptance.

Validation order per target is fixed so reports stay deterministic:

1. duplicate catalog rows
2. missing catalog row
3. manifest binding mismatch (region/address)
4. source drift (rendered body, body digest, target digest)
5. review status other than ``approved``
6. empty Korean text
7. Japanese residue in the Korean text
8. no Hangul while the source contained Japanese
9. missing whole-expression review evidence for broken/split/particle defects
10. unresolvable terminology reference
11. terminology conflict without a reviewed pin
12. established terminology absent from the Korean text
13. unregistered proper noun without a reviewed pin
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Mapping, Sequence

from mixed_residual_classification import (
    hangul_character_count,
    japanese_character_count,
)
from mixed_residual_discovery import validate_manifest_digest
from mixed_residual_models import (
    DeterministicJsonModel,
    Region,
    deterministic_json_data,
    deterministic_json_sha256,
)

CATALOG_SCHEMA_VERSION = 1
CATALOG_GENERATOR = "reviewed_by_human"
REVIEW_REF_PREFIX = "review:"
TERM_LIKE_MAX_LENGTH = 8
DEFECT_ANNOTATIONS = frozenset(("broken_word", "split_compound", "japanese_particle"))

_KATAKANA_RUN = re.compile(r"[\u30a0-\u30ff]+")
_KATAKANA_EDGE = "・ー"
_INDEX_REF = re.compile(r"^(?P<source>[^#]+)#(?P<jp>[^=]+)=(?P<ko>.+)$")
_REVIEW_REF = re.compile(r"^review:(?P<jp>[^=]+)=(?P<ko>.+)$")
_ALLOWED_REVIEW_STATUS = frozenset(("approved", "draft", "rejected", "needs_review"))

TranslationStatus = Literal["approved", "unresolved"]


class TranslationCatalogError(ValueError):
    """Raised when a catalog, terminology source, or manifest cannot be trusted."""


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TranslationCatalogError(f"cannot read {label}: {path}: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TranslationCatalogError(
            f"{label} is not valid UTF-8 JSON: {path}: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise TranslationCatalogError(f"{label} root must be a JSON object: {path}")
    return document


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TranslationCatalogError(message)


# ---------------------------------------------------------------------------
# Terminology index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TerminologyTerm(DeterministicJsonModel):
    """One established Japanese to Korean terminology pair."""

    jp: str
    ko: str
    source: str

    def __post_init__(self) -> None:
        _require(bool(self.jp), "terminology jp must not be empty")
        _require(bool(self.ko), "terminology ko must not be empty")
        _require(bool(self.source), "terminology source must not be empty")
        _require(
            japanese_character_count(self.ko) == 0,
            f"established terminology {self.jp!r} maps to Japanese text {self.ko!r}",
        )

    @property
    def ref(self) -> str:
        return f"{self.source}#{self.jp}={self.ko}"


@dataclass(frozen=True)
class TerminologySource(DeterministicJsonModel):
    path: str
    size: int
    sha256: str
    terms: int


def _is_term_like(jp: str) -> bool:
    """Return whether a full-sheet row is a short proper-noun style term."""
    if not jp or len(jp) > TERM_LIKE_MAX_LENGTH:
        return False
    return all("\u30a0" <= character <= "\u30ff" for character in jp)


def _iter_terminology_rows(document: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    entries = document.get("entries")
    if isinstance(entries, list):
        for row in entries:
            if isinstance(row, Mapping):
                yield row
    lines = document.get("lines")
    if isinstance(lines, list):
        for row in lines:
            if isinstance(row, Mapping) and _is_term_like(str(row.get("jp", ""))):
                yield row


class TerminologyIndex:
    """Read-only merge of established terminology sources.

    Conflicting Korean spellings are preserved, never merged or auto-resolved.
    """

    def __init__(
        self,
        terms: Iterable[TerminologyTerm],
        sources: Sequence[TerminologySource] = (),
    ) -> None:
        grouped: dict[str, list[TerminologyTerm]] = {}
        for term in terms:
            bucket = grouped.setdefault(term.jp, [])
            if term not in bucket:
                bucket.append(term)
        self._terms: dict[str, tuple[TerminologyTerm, ...]] = {
            jp: tuple(sorted(bucket, key=lambda item: (item.ko, item.source)))
            for jp, bucket in grouped.items()
        }
        self._sources = tuple(sources)
        self._max_length = max((len(jp) for jp in self._terms), default=0)

    @property
    def sources(self) -> tuple[TerminologySource, ...]:
        return self._sources

    def __contains__(self, jp: object) -> bool:
        return jp in self._terms

    def __len__(self) -> int:
        return len(self._terms)

    def lookup(self, jp: str) -> tuple[TerminologyTerm, ...]:
        return self._terms.get(jp, ())

    def variants(self, jp: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(term.ko for term in self.lookup(jp)))

    def conflicts(self) -> tuple[str, ...]:
        return tuple(sorted(jp for jp in self._terms if len(self.variants(jp)) > 1))

    def resolve_ref(self, ref: str) -> TerminologyTerm | None:
        """Resolve ``source#jp=ko`` against the index; ``None`` when unknown."""
        match = _INDEX_REF.match(ref)
        if match is None:
            return None
        source = match.group("source").strip()
        jp = match.group("jp").strip()
        ko = match.group("ko").strip()
        for term in self.lookup(jp):
            if term.ko != ko:
                continue
            if term.source == source or Path(term.source).name == Path(source).name:
                return term
        return None

    def tokenize(self, text: str) -> tuple[tuple[str, bool], ...]:
        """Split katakana runs into ``(token, registered)`` pairs by longest match.

        Registered tokens come from the index; unregistered tokens are the
        remaining katakana segments of two or more characters, which represent
        proper nouns that no reviewed source has established yet.
        """
        tokens: list[tuple[str, bool]] = []
        for run in _KATAKANA_RUN.findall(text):
            index = 0
            pending = ""
            while index < len(run):
                matched: str | None = None
                limit = min(len(run), index + max(self._max_length, 1))
                for end in range(limit, index, -1):
                    candidate = run[index:end]
                    if candidate in self._terms:
                        matched = candidate
                        break
                if matched is None:
                    pending += run[index]
                    index += 1
                    continue
                self._flush_pending(pending, tokens)
                pending = ""
                tokens.append((matched, True))
                index += len(matched)
            self._flush_pending(pending, tokens)
        deduplicated: list[tuple[str, bool]] = []
        for token in tokens:
            if token not in deduplicated:
                deduplicated.append(token)
        return tuple(deduplicated)

    @staticmethod
    def _flush_pending(pending: str, tokens: list[tuple[str, bool]]) -> None:
        for segment in pending.split("・"):
            cleaned = segment.strip(_KATAKANA_EDGE)
            if len(cleaned) >= 2:
                tokens.append((cleaned, False))

    def to_json_data(self) -> dict[str, Any]:
        return {
            "terms": len(self._terms),
            "conflicting_terms": list(self.conflicts()),
            "sources": [deterministic_json_data(source) for source in self._sources],
            "index_sha256": deterministic_json_sha256(
                [
                    [jp, [term.ko for term in self.lookup(jp)]]
                    for jp in sorted(self._terms)
                ]
            ),
        }


def load_terminology_index(paths: Sequence[str | Path]) -> TerminologyIndex:
    """Load established terminology read-only from the declared sources."""
    resolved = [Path(path) for path in paths]
    _require(bool(resolved), "at least one terminology source is required")
    seen: set[str] = set()
    terms: list[TerminologyTerm] = []
    sources: list[TerminologySource] = []
    for path in resolved:
        key = str(path)
        _require(key not in seen, f"duplicate terminology source: {key}")
        seen.add(key)
        document = _read_json_object(path, "terminology source")
        source_terms: list[TerminologyTerm] = []
        for row in _iter_terminology_rows(document):
            jp = row.get("jp")
            ko = row.get("ko")
            if not isinstance(jp, str) or not isinstance(ko, str) or not jp or not ko:
                continue
            if japanese_character_count(ko):
                continue
            source_terms.append(TerminologyTerm(jp=jp, ko=ko, source=key))
        raw = path.read_bytes()
        sources.append(
            TerminologySource(
                path=key,
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                terms=len(source_terms),
            )
        )
        terms.extend(source_terms)
    return TerminologyIndex(terms, sources)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranslationEntry(DeterministicJsonModel):
    """One reviewed catalog row bound to a manifest target."""

    record_id: str
    region: Region
    abs: str
    source_text: str
    source_body_sha256: str
    ko: str
    review_status: str
    terminology_refs: tuple[str, ...] = ()
    target_sha256: str | None = None
    reviewer: str = ""
    complete_expression_review: bool = False
    review_notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "terminology_refs", tuple(self.terminology_refs))
        _require(bool(self.record_id), "catalog rows require a record_id")
        _require(
            self.region in ("script", "name75", "aux"),
            f"unsupported catalog region: {self.region}",
        )
        _require(
            bool(re.fullmatch(r"[0-9A-Fa-f]{4,8}", self.abs)),
            f"catalog abs must be a hex logical address: {self.abs!r}",
        )
        _require(
            bool(re.fullmatch(r"[0-9a-f]{64}", self.source_body_sha256)),
            f"{self.record_id} source_body_sha256 must be a SHA-256 hex digest",
        )
        _require(
            self.target_sha256 is None
            or bool(re.fullmatch(r"[0-9a-f]{64}", self.target_sha256)),
            f"{self.record_id} target_sha256 must be a SHA-256 hex digest",
        )
        _require(
            self.review_status in _ALLOWED_REVIEW_STATUS,
            f"{self.record_id} has an unsupported review_status: {self.review_status!r}",
        )
        _require(
            all(isinstance(ref, str) and ref for ref in self.terminology_refs),
            f"{self.record_id} terminology_refs must be non-empty strings",
        )

    @property
    def logical_address(self) -> int:
        return int(self.abs, 16)

    @property
    def approved(self) -> bool:
        return self.review_status == "approved"


@dataclass(frozen=True)
class TranslationCatalog(DeterministicJsonModel):
    schema_version: int
    original_rom_sha256: str
    terminology_sources: tuple[str, ...]
    entries: tuple[TranslationEntry, ...]
    manifest_sha256: str | None = None
    path: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "terminology_sources", tuple(self.terminology_sources))
        object.__setattr__(self, "entries", tuple(self.entries))
        _require(
            self.schema_version == CATALOG_SCHEMA_VERSION,
            f"unsupported catalog schema_version: {self.schema_version!r}",
        )
        _require(
            bool(re.fullmatch(r"[0-9a-f]{64}", self.original_rom_sha256)),
            "catalog original_rom_sha256 must be a SHA-256 hex digest",
        )
        _require(
            bool(self.terminology_sources),
            "catalog must declare at least one terminology source",
        )
        _require(
            len(self.terminology_sources) == len(set(self.terminology_sources)),
            "catalog terminology_sources must be unique",
        )
        _require(
            self.manifest_sha256 is None
            or bool(re.fullmatch(r"[0-9a-f]{64}", self.manifest_sha256)),
            "catalog manifest_sha256 must be a SHA-256 hex digest",
        )

    def rows_for(self, record_id: str) -> tuple[TranslationEntry, ...]:
        return tuple(entry for entry in self.entries if entry.record_id == record_id)

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(entry.record_id for entry in self.entries))


def _entry_from_json(row: Mapping[str, Any], index: int) -> TranslationEntry:
    if not isinstance(row, Mapping):
        raise TranslationCatalogError(f"catalog entry {index} must be a JSON object")
    missing = [
        field
        for field in (
            "record_id",
            "region",
            "abs",
            "source_text",
            "source_body_sha256",
            "ko",
            "review_status",
        )
        if field not in row
    ]
    if missing:
        raise TranslationCatalogError(
            f"catalog entry {index} is missing required fields: {', '.join(missing)}"
        )
    for field in ("record_id", "region", "abs", "source_text", "source_body_sha256", "ko", "review_status"):
        if not isinstance(row[field], str):
            raise TranslationCatalogError(
                f"catalog entry {index} field {field!r} must be a string"
            )
    refs = row.get("terminology_refs", [])
    if not isinstance(refs, list):
        raise TranslationCatalogError(
            f"catalog entry {index} terminology_refs must be a list"
        )
    complete = row.get("complete_expression_review", False)
    if not isinstance(complete, bool):
        raise TranslationCatalogError(
            f"catalog entry {index} complete_expression_review must be a boolean"
        )
    target_sha256 = row.get("target_sha256")
    if target_sha256 is not None and not isinstance(target_sha256, str):
        raise TranslationCatalogError(
            f"catalog entry {index} target_sha256 must be a string"
        )
    return TranslationEntry(
        record_id=row["record_id"],
        region=row["region"],  # type: ignore[arg-type]
        abs=row["abs"],
        source_text=row["source_text"],
        source_body_sha256=row["source_body_sha256"],
        ko=row["ko"],
        review_status=row["review_status"],
        terminology_refs=tuple(str(ref) for ref in refs),
        target_sha256=target_sha256,
        reviewer=str(row.get("reviewer", "")),
        complete_expression_review=complete,
        review_notes=str(row.get("review_notes", "")),
    )


def load_translation_catalog(path: str | Path) -> TranslationCatalog:
    """Load a reviewed catalog without repairing or completing any row."""
    resolved = Path(path)
    document = _read_json_object(resolved, "translation catalog")
    entries_value = document.get("entries")
    _require(
        isinstance(entries_value, list),
        "translation catalog requires an 'entries' list",
    )
    sources = document.get("terminology_sources", [])
    _require(
        isinstance(sources, list) and all(isinstance(item, str) for item in sources),
        "translation catalog terminology_sources must be a list of paths",
    )
    entries = tuple(
        _entry_from_json(row, index) for index, row in enumerate(entries_value)
    )
    return TranslationCatalog(
        schema_version=document.get("schema_version"),  # type: ignore[arg-type]
        original_rom_sha256=str(document.get("original_rom_sha256", "")),
        terminology_sources=tuple(str(item) for item in sources),
        entries=entries,
        manifest_sha256=document.get("manifest_sha256"),
        path=str(resolved),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranslationDecision(DeterministicJsonModel):
    record_id: str
    region: Region
    logical_address: int
    source_classification: str
    source_text: str
    korean_text: str | None
    status: TranslationStatus
    reason: str
    annotations: tuple[str, ...] = ()
    terminology_refs: tuple[str, ...] = ()
    terminology_checked: tuple[str, ...] = ()
    reviewer: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "annotations", tuple(self.annotations))
        object.__setattr__(self, "terminology_refs", tuple(self.terminology_refs))
        object.__setattr__(self, "terminology_checked", tuple(self.terminology_checked))
        _require(bool(self.reason), "translation decisions require a reason")
        _require(
            self.status in ("approved", "unresolved"),
            f"unsupported translation status: {self.status}",
        )
        _require(
            self.status != "approved" or bool(self.korean_text),
            "approved translation decisions require Korean text",
        )


@dataclass(frozen=True)
class TranslationValidation(DeterministicJsonModel):
    catalog_path: str
    manifest_sha256: str
    decisions: tuple[TranslationDecision, ...]
    terminology: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "decisions", tuple(self.decisions))

    @property
    def approved(self) -> tuple[TranslationDecision, ...]:
        return tuple(item for item in self.decisions if item.status == "approved")

    @property
    def unresolved(self) -> tuple[TranslationDecision, ...]:
        return tuple(item for item in self.decisions if item.status == "unresolved")

    @property
    def unresolved_count(self) -> int:
        return len(self.unresolved)

    @property
    def accepted(self) -> bool:
        return self.unresolved_count == 0

    def korean_text(self) -> dict[str, str]:
        return {
            item.record_id: item.korean_text
            for item in self.approved
            if item.korean_text is not None
        }

    def to_json_data(self) -> dict[str, Any]:
        return {
            "catalog_path": self.catalog_path,
            "manifest_sha256": self.manifest_sha256,
            "accepted": self.accepted,
            "unresolved_count": self.unresolved_count,
            "counts": {
                "targets": len(self.decisions),
                "approved": len(self.approved),
                "unresolved": self.unresolved_count,
            },
            "terminology": deterministic_json_data(self.terminology),
            "decisions": [item.to_json_data() for item in self.decisions],
        }


def _manifest_targets(manifest: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    population = manifest.get("population")
    _require(isinstance(population, Mapping), "manifest is missing its population")
    included = population.get("included")  # type: ignore[union-attr]
    _require(isinstance(included, list), "manifest population.included must be a list")
    rows: list[Mapping[str, Any]] = []
    for row in included:  # type: ignore[union-attr]
        _require(isinstance(row, Mapping), "manifest target rows must be objects")
        rows.append(row)
    ids = [str(row.get("record_id")) for row in rows]
    _require(len(ids) == len(set(ids)), "manifest target rows contain duplicate ids")
    return tuple(rows)


def _terminology_decision(
    entry: TranslationEntry, index: TerminologyIndex, source_text: str
) -> tuple[str | None, tuple[str, ...]]:
    """Return ``(failure_reason, checked_tokens)`` for terminology obligations."""
    pinned: dict[str, str] = {}
    for ref in entry.terminology_refs:
        if ref.startswith(REVIEW_REF_PREFIX):
            match = _REVIEW_REF.match(ref)
            if match is None:
                return "translation_terminology_ref_unknown", ()
            pinned[match.group("jp").strip()] = match.group("ko").strip()
            continue
        term = index.resolve_ref(ref)
        if term is None:
            return "translation_terminology_ref_unknown", ()
        pinned[term.jp] = term.ko

    checked: list[str] = []
    for token, registered in index.tokenize(source_text):
        checked.append(token)
        if registered:
            variants = index.variants(token)
            chosen = pinned.get(token)
            if chosen is None:
                if len(variants) != 1:
                    return "translation_terminology_conflict", tuple(checked)
                chosen = variants[0]
            elif chosen not in variants:
                return "translation_terminology_conflict", tuple(checked)
            if chosen not in entry.ko:
                return "translation_terminology_missing", tuple(checked)
            continue
        chosen = pinned.get(token)
        if chosen is None:
            return "translation_review_required", tuple(checked)
        if chosen not in entry.ko:
            return "translation_terminology_missing", tuple(checked)
    return None, tuple(checked)


def _evaluate(
    row: Mapping[str, Any],
    catalog: TranslationCatalog,
    index: TerminologyIndex,
) -> TranslationDecision:
    record_id = str(row.get("record_id"))
    region = str(row.get("region"))
    logical_address = int(row.get("logical_address", 0))
    source_text = str(row.get("rendered_source_text", ""))
    annotations = tuple(str(item) for item in row.get("annotations", ()) or ())
    source_japanese = japanese_character_count(source_text)
    rows = catalog.rows_for(record_id)

    def unresolved(reason: str, entry: TranslationEntry | None = None) -> TranslationDecision:
        return TranslationDecision(
            record_id=record_id,
            region=region,  # type: ignore[arg-type]
            logical_address=logical_address,
            source_classification=str(row.get("source_classification", "")),
            source_text=source_text,
            korean_text=entry.ko if entry is not None else None,
            status="unresolved",
            reason=reason,
            annotations=annotations,
            terminology_refs=entry.terminology_refs if entry is not None else (),
            reviewer=entry.reviewer if entry is not None else "",
        )

    if len(rows) > 1:
        return unresolved("translation_duplicate_rows")
    if not rows:
        return unresolved("translation_missing")
    entry = rows[0]

    if entry.region != region or entry.logical_address != logical_address:
        return unresolved("translation_binding_mismatch", entry)
    if (
        entry.source_text != source_text
        or entry.source_body_sha256 != str(row.get("source_body_sha256", ""))
        or entry.source_body_sha256 != _text_sha256(source_text)
    ):
        return unresolved("translation_source_drift", entry)
    if entry.target_sha256 is not None and entry.target_sha256 != str(
        row.get("target_sha256", "")
    ):
        return unresolved("translation_source_drift", entry)
    if not entry.approved:
        return unresolved("translation_not_approved", entry)
    if not entry.ko.strip():
        return unresolved("translation_empty", entry)
    if japanese_character_count(entry.ko):
        return unresolved("translation_japanese_residue", entry)
    if source_japanese and hangul_character_count(entry.ko) == 0:
        return unresolved("translation_missing_hangul", entry)
    if set(annotations) & DEFECT_ANNOTATIONS and not (
        entry.complete_expression_review and entry.review_notes.strip()
    ):
        return unresolved("translation_incomplete_expression_review", entry)

    failure, checked = _terminology_decision(entry, index, source_text)
    if failure is not None:
        decision = unresolved(failure, entry)
        return TranslationDecision(
            record_id=decision.record_id,
            region=decision.region,
            logical_address=decision.logical_address,
            source_classification=decision.source_classification,
            source_text=decision.source_text,
            korean_text=decision.korean_text,
            status="unresolved",
            reason=failure,
            annotations=annotations,
            terminology_refs=entry.terminology_refs,
            terminology_checked=checked,
            reviewer=entry.reviewer,
        )

    return TranslationDecision(
        record_id=record_id,
        region=region,  # type: ignore[arg-type]
        logical_address=logical_address,
        source_classification=str(row.get("source_classification", "")),
        source_text=source_text,
        korean_text=entry.ko,
        status="approved",
        reason="reviewed_translation_approved",
        annotations=annotations,
        terminology_refs=entry.terminology_refs,
        terminology_checked=checked,
        reviewer=entry.reviewer,
    )


def validate_translations(
    manifest: Mapping[str, Any],
    catalog: TranslationCatalog,
    terminology: TerminologyIndex,
) -> TranslationValidation:
    """Bind a reviewed catalog to a digest-valid manifest, failing closed.

    Manifest-level or catalog-level distrust raises; per-target problems become
    unresolved decisions so the caller can report every target and still refuse
    static acceptance while ``unresolved_count`` is positive.
    """
    _require(
        validate_manifest_digest(manifest),
        "refusing to validate translations against a stale manifest digest",
    )
    manifest_sha256 = str(manifest.get("manifest_sha256"))
    inputs = manifest.get("inputs")
    _require(isinstance(inputs, Mapping), "manifest is missing its input identities")
    original = inputs.get("original_rom")  # type: ignore[union-attr]
    _require(
        isinstance(original, Mapping),
        "manifest is missing its Original ROM identity",
    )
    _require(
        catalog.original_rom_sha256 == str(original.get("sha256")),  # type: ignore[union-attr]
        "catalog is bound to a different Original ROM than the manifest",
    )
    if catalog.manifest_sha256 is not None:
        _require(
            catalog.manifest_sha256 == manifest_sha256,
            "catalog is bound to a stale target manifest digest",
        )
    declared = set(catalog.terminology_sources)
    loaded = {source.path for source in terminology.sources}
    loaded_names = {Path(path).name for path in loaded}
    missing = sorted(
        item
        for item in declared
        if item not in loaded and Path(item).name not in loaded_names
    )
    _require(
        not missing,
        "terminology index is missing catalog sources: " + ", ".join(missing),
    )

    rows = _manifest_targets(manifest)
    decisions = [_evaluate(row, catalog, terminology) for row in rows]
    target_ids = {str(row.get("record_id")) for row in rows}
    for record_id in catalog.record_ids:
        if record_id in target_ids:
            continue
        entry = catalog.rows_for(record_id)[0]
        decisions.append(
            TranslationDecision(
                record_id=record_id,
                region=entry.region,
                logical_address=entry.logical_address,
                source_classification="",
                source_text=entry.source_text,
                korean_text=entry.ko,
                status="unresolved",
                reason="translation_target_not_in_manifest",
                terminology_refs=entry.terminology_refs,
                reviewer=entry.reviewer,
            )
        )
    return TranslationValidation(
        catalog_path=catalog.path,
        manifest_sha256=manifest_sha256,
        decisions=tuple(decisions),
        terminology=terminology.to_json_data(),
    )


def validate_catalog_files(
    manifest_path: str | Path,
    catalog_path: str | Path,
    *,
    terminology_sources: Sequence[str | Path] | None = None,
) -> TranslationValidation:
    """Load manifest, catalog, and declared terminology, then validate."""
    manifest = _read_json_object(Path(manifest_path), "target manifest")
    catalog = load_translation_catalog(catalog_path)
    sources = terminology_sources or catalog.terminology_sources
    index = load_terminology_index(list(sources))
    return validate_translations(manifest, catalog, index)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the reviewed residual translation catalog (read-only)."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--translations", required=True)
    parser.add_argument("--terminology", action="append", default=[])
    parser.add_argument("--out-report", default=None)
    args = parser.parse_args(argv)

    try:
        validation = validate_catalog_files(
            args.manifest,
            args.translations,
            terminology_sources=args.terminology or None,
        )
    except TranslationCatalogError as exc:
        print(f"REJECT {exc}", file=sys.stderr)
        return 2

    payload = validation.to_json_data()
    if args.out_report:
        output = Path(args.out_report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(
        json.dumps(
            {
                "accepted": payload["accepted"],
                "counts": payload["counts"],
                "unresolved": [
                    {"record_id": item["record_id"], "reason": item["reason"]}
                    for item in payload["decisions"]
                    if item["status"] == "unresolved"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if payload["accepted"] else 1


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "DEFECT_ANNOTATIONS",
    "REVIEW_REF_PREFIX",
    "TerminologyIndex",
    "TerminologySource",
    "TerminologyTerm",
    "TranslationCatalog",
    "TranslationCatalogError",
    "TranslationDecision",
    "TranslationEntry",
    "TranslationValidation",
    "load_terminology_index",
    "load_translation_catalog",
    "main",
    "validate_catalog_files",
    "validate_translations",
]


if __name__ == "__main__":
    raise SystemExit(main())
