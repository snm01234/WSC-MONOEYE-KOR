"""Read-only residual target discovery and deterministic manifest generation.

Record boundaries always come from the identity-validated Original ROM.  The
Working ROM contributes only the bytes and active dictionary used to render
those fixed extents.  Discovery never scans decoded character classes to add
records outside the proven population.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mixed_residual_classification import PrefixEvidenceResolver, classify_record
from mixed_residual_models import (
    CandidateDecision,
    DiscoveryInputIdentities,
    EvidenceIdentity,
    ProvenRecord,
    deterministic_json_dumps,
    deterministic_json_sha256,
    validate_discovery_inputs,
)
from mixed_residual_records import (
    AUX_EVIDENCE_KIND,
    OriginalRomProvenRecordEnumerator,
    ProvenRecordPopulation,
)
from monoeye_rom import Dictionary, Tbl, stock_base
from measure_aux_prefix_rule import BANK_RULES, prefix_len as rule_prefix_len

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_GENERATOR = "tools/mixed_residual_discovery.py"
DictionaryFactory = Callable[[bytes], Dictionary]

# Target scope.  Enumeration and the reference union stay broad on purpose; only
# the localization target population is narrowed here, with a recorded reason per
# record, so no dictionary consumer can disappear from the safety scans.
#
# Script: the dialogue write band already enforced by
# tools/verify_stock_noninvasion.py.  Banks 6A-6F are unit/data segments in
# tools/verify_all_stages_smoke.py (unit_segs 50-5D,6A-6F) and decode as data
# noise rather than sentences, so they are not translation targets.
DIALOGUE_BAND_START = 0x6040A5
DIALOGUE_BAND_END_EXCLUSIVE = 0x640000
# Aux: the banks with established text evidence in the project's own catalogs,
# data/aux_text_ko.json (59 mission dialogue, 5C skill/ability descriptions,
# 5D/5E pilot battle voice) and data/aux_body_ko.json (59, 5D/5E bodies).
AUX_TEXT_EVIDENCE_BANKS = frozenset({0x59, 0x5C, 0x5D, 0x5E})
SCRIPT_OUT_OF_BAND_REASON = "excluded_outside_dialogue_band"
AUX_BANK_WITHOUT_TEXT_EVIDENCE_REASON = "excluded_aux_bank_without_text_evidence"

# Inside the dialogue band, this range holds screen graphics rather than lines:
# 1-2 byte zstrings in dense clusters, tile separators repeated per row, single
# characters repeated for hundreds of bytes, and ``ゲ－ムオ－バ－`` fragments
# interleaved with them.  Dialogue resumes at 0x630000.
SCRIPT_GRAPHICS_BLOCKS = ((0x62D650, 0x630000),)
SCRIPT_GRAPHICS_BLOCK_REASON = "excluded_script_graphics_block"
# Safety nets for the same class of data outside the mapped block.  Both
# thresholds sit well above real dialogue: laughter such as ``あははははははっ！！``
# repeats a character six times and must stay a target.
NON_LINGUISTIC_REASON = "excluded_non_linguistic_fragment"
# A record whose Original-ROM expansion cannot resolve a dictionary index is not
# proven text: the bytes reference slots that do not exist in the Original.
# tools/verify_nondialogue_text.py already treats these as out of scope
# ("records_skipped_unresolvable_in_original"), so targets follow the same rule.
# In the Working ROM such indices can render localized text, which is dictionary
# leakage into a data record rather than a line waiting for translation.
UNRESOLVABLE_MARKER = "<BADDICT:"
UNRESOLVABLE_REASON = "excluded_unresolvable_in_original"
# Curated single-record exclusions, each bound to the Original payload digest so
# the entry cannot silently apply to a different record after any drift.
CURATED_NON_TEXT_REASON = "excluded_curated_non_text_record"
CURATED_NON_TEXT_RECORDS: Mapping[str, Mapping[str, str]] = {
    "name75:75E7CF": {
        "original_payload_sha256": (
            "44111f1f5e71d479b57f2243bda98b432747b69c68f1a9ef35b61469dbca9abf"
        ),
        "note": (
            "payload 05 02 02 02 01 02 01 eb 32 2e 5b 0e is control bytes at the "
            "tail of the Name75 table, not a displayed name"
        ),
    },
}
#: A record whose body cannot hold the 4-byte ext3 portal can only be localized
#: through a 2-byte dictionary token, and the 2-byte index space is saturated
#: (stock 3831 + ext 265 = 4096, every index consumed).  When the slot the
#: Original body points at is also rendered by aux/name75 records, Koreanizing it
#: would mean changing shared UI meaning, which the invasion guard refuses.  Such
#: a record is therefore out of this feature's target scope rather than an
#: unresolved target.  The decision needs the Reference_Union, so discovery takes
#: it as an injected evaluator instead of guessing.
SHARED_TOKEN_SHORT_RECORD_REASON = "excluded_shared_token_body_capacity"
PREFIX_UNPROVABLE_REASON = "excluded_prefix_unprovable"
TILE_SEPARATOR_CHARS = "背惹肖"
TILE_SEPARATOR_MIN_REPEATS = 3
REPEATED_CHARACTER_MIN_RUN = 10
_FRAGMENT_PUNCTUATION = "…。、！？「」『』（）・～"
_REPEATED_RUN = re.compile(r"(.)\1{%d,}" % (REPEATED_CHARACTER_MIN_RUN - 1))


class DiscoveryError(ValueError):
    """Raised when fixed-boundary rendering or manifest locking cannot be proven."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text_sha256(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _locked_bytes(path: str, *, size: int, sha256: str, label: str) -> bytes:
    try:
        value = Path(path).read_bytes()
    except OSError as exc:
        raise DiscoveryError(f"cannot read locked {label}: {path}: {exc}") from exc
    if len(value) != size:
        raise DiscoveryError(
            f"{label} changed after identity validation: expected size {size}, got {len(value)}"
        )
    actual = _sha256_bytes(value)
    if actual != sha256:
        raise DiscoveryError(
            f"{label} changed after identity validation: expected SHA-256 {sha256}, "
            f"got {actual}"
        )
    return value


def _locked_json(evidence: EvidenceIdentity) -> dict[str, Any]:
    raw = _locked_bytes(
        evidence.path,
        size=evidence.size,
        sha256=evidence.sha256,
        label=f"{evidence.kind} evidence",
    )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"locked {evidence.kind} evidence is not valid JSON") from exc
    if not isinstance(value, dict):
        raise DiscoveryError(f"locked {evidence.kind} evidence root must be an object")
    return value


def _payload_at_boundary(
    rom: bytes, record: ProvenRecord, *, role: str, require_terminator: bool = True
) -> bytes:
    """Read exactly the Original-derived payload extent from one ROM."""
    base = stock_base(rom)
    start = base + record.boundary.start
    end = start + record.boundary.payload_capacity
    terminator = base + record.boundary.terminator_offset
    if start < 0 or end > len(rom) or terminator >= len(rom):
        raise DiscoveryError(
            f"{record.record_id} Original-derived boundary is outside the {role} ROM"
        )
    if require_terminator and rom[terminator] != 0:
        raise DiscoveryError(
            f"{record.record_id} {role} terminator mismatch at "
            f"{record.boundary.terminator_offset:06X}"
        )
    return bytes(rom[start:end])


def _table_identity(tbl: Tbl) -> dict[str, Any]:
    rows = [
        {"code": code, "text": text}
        for code, text in sorted(tbl.code_to_char.items())
    ]
    return {
        "kind": "character_table_mapping",
        "entries": len(rows),
        "sha256": deterministic_json_sha256(rows),
    }


def _canonical_inputs(
    inputs: DiscoveryInputIdentities,
) -> DiscoveryInputIdentities:
    validated = validate_discovery_inputs(inputs)
    return DiscoveryInputIdentities(
        original_rom=validated.original_rom,
        working_rom=validated.working_rom,
        evidence=tuple(
            sorted(validated.evidence, key=lambda item: (item.kind, item.path))
        ),
    )


def _prefix_documents(
    inputs: DiscoveryInputIdentities, prefix_evidence_kinds: Sequence[str]
) -> tuple[tuple[EvidenceIdentity, Mapping[str, Any]], ...]:
    kinds = tuple(prefix_evidence_kinds)
    if len(kinds) != len(set(kinds)):
        raise DiscoveryError("prefix evidence kinds must be unique")
    by_kind = {item.kind: item for item in inputs.evidence}
    missing = sorted(set(kinds) - set(by_kind))
    if missing:
        raise DiscoveryError(
            "configured prefix evidence is missing from the identity lock: "
            + ", ".join(missing)
        )
    return tuple((by_kind[kind], _locked_json(by_kind[kind])) for kind in kinds)


def _resolve_working_record(
    record: ProvenRecord,
    *,
    original_rom: bytes,
    working_rom: bytes,
    working_rom_sha256: str,
    dictionary: Dictionary,
    tbl: Tbl,
    prefix_documents: Sequence[tuple[EvidenceIdentity, Mapping[str, Any]]],
    resolver: PrefixEvidenceResolver | None = None,
) -> ProvenRecord:
    original_payload = _payload_at_boundary(original_rom, record, role="Original")
    if _sha256_bytes(original_payload) != record.original_payload_sha256:
        raise DiscoveryError(
            f"{record.record_id} Original payload digest no longer matches its boundary"
        )
    working_payload = _payload_at_boundary(working_rom, record, role="Working")

    if resolver is None:
        resolver = PrefixEvidenceResolver()
    trusted: list[tuple[EvidenceIdentity, Mapping[str, Any]]] = []
    for identity, document in prefix_documents:
        if resolver.resolve(
            record,
            original_payload,
            document,
            evidence_name=identity.kind,
        ).trusted:
            trusted.append((identity, document))
    if len(trusted) > 1:
        labels = ", ".join(item.kind for item, _document in trusted)
        raise DiscoveryError(
            f"{record.record_id} has ambiguous trusted prefix evidence: {labels}"
        )

    selected_identity: EvidenceIdentity | None = None
    selected_report: Mapping[str, Any] | None = None
    if trusted:
        selected_identity, selected_report = trusted[0]

    try:
        rendered = resolver.resolve_record(
            record,
            original_payload,
            working_payload,
            lambda payload: dictionary.expand(payload, tbl),
            selected_report,
            evidence_name=(
                selected_identity.kind if selected_identity is not None else "prefix_report"
            ),
        )
    except Exception as exc:  # dictionary corruption must not silently drop a target
        raise DiscoveryError(f"cannot render {record.record_id}: {exc}") from exc

    provenance = list(rendered.provenance)
    provenance.append(f"working_rom:{working_rom_sha256}")
    if rendered.prefix_evidence is not None and selected_identity is not None:
        provenance.append(
            f"prefix_evidence:{selected_identity.kind}:{selected_identity.sha256}"
        )
    return replace(rendered, provenance=tuple(dict.fromkeys(provenance)))


def _non_linguistic_reason(text: str) -> str | None:
    """Return why a rendered body carries screen data instead of a line."""
    body = text.strip("\u3000 ")
    core = [
        character
        for character in body
        if character not in "\u3000 " and character not in _FRAGMENT_PUNCTUATION
    ]
    if any(body.count(mark) >= TILE_SEPARATOR_MIN_REPEATS for mark in TILE_SEPARATOR_CHARS):
        return f"{NON_LINGUISTIC_REASON}:tile_separator_run"
    if _REPEATED_RUN.search("".join(core)):
        return f"{NON_LINGUISTIC_REASON}:repeated_character_run"
    if (
        len(core) == 1
        and (
            "\u3040" <= core[0] <= "\u309f" or "\u30a0" <= core[0] <= "\u30ff"
        )
        and not any(character in _FRAGMENT_PUNCTUATION for character in body)
    ):
        return f"{NON_LINGUISTIC_REASON}:single_kana"
    return None


#: ``(record) -> reason | None``.  Supplied by the caller that owns the
#: Reference_Union; discovery never builds one itself.
StorageCapacityEvaluator = Callable[[ProvenRecord], str | None]


def _scope_exclusion_reason(
    record: ProvenRecord,
    *,
    original_text: str | None = None,
    original_payload: bytes | None = None,
    storage_capacity: StorageCapacityEvaluator | None = None,
) -> str | None:
    """Return why a proven record is out of the localization target scope.

    Enumeration-level exclusions win, so this only narrows records that would
    otherwise become targets.  The record still keeps its boundary, rendered
    source text, and decision reason in the manifest.
    """
    curated = CURATED_NON_TEXT_RECORDS.get(record.record_id)
    if (
        curated is not None
        and curated["original_payload_sha256"] == record.original_payload_sha256
    ):
        return CURATED_NON_TEXT_REASON
    if original_text is not None and UNRESOLVABLE_MARKER in original_text:
        return UNRESOLVABLE_REASON
    if record.region == "script":
        if not (
            DIALOGUE_BAND_START
            <= record.boundary.start
            < DIALOGUE_BAND_END_EXCLUSIVE
        ):
            return SCRIPT_OUT_OF_BAND_REASON
        if any(
            low <= record.boundary.start < high for low, high in SCRIPT_GRAPHICS_BLOCKS
        ):
            return SCRIPT_GRAPHICS_BLOCK_REASON
        fragment = _non_linguistic_reason(record.rendered_body)
        if fragment is not None:
            return fragment
    elif record.region == "aux":
        if record.bank not in AUX_TEXT_EVIDENCE_BANKS:
            return AUX_BANK_WITHOUT_TEXT_EVIDENCE_REASON
        if not classify_record(record).included:
            return None
        if original_payload is None:
            raise DiscoveryError(f"{record.record_id} lacks Original payload for prefix scope")
        rule = BANK_RULES.get(record.bank)
        rule_k = 0 if rule is None else rule_prefix_len(original_payload, rule)
        evidence_k = len(record.prefix_bytes)
        if evidence_k > 0 and rule_k > 0 and evidence_k != rule_k:
            return f"{PREFIX_UNPROVABLE_REASON}:rule={rule_k}:evidence={evidence_k}"
        if rule_k == 0 and (
            evidence_k > 0 or (original_payload and original_payload[0] < 0xE0)
        ):
            return f"{PREFIX_UNPROVABLE_REASON}:ambiguous_leading_byte"
        if len(original_payload) - rule_k < 4:
            if storage_capacity is not None:
                rule_record = replace(record, prefix_bytes=original_payload[:rule_k])
                shared_reason = storage_capacity(rule_record)
                if shared_reason is not None:
                    return shared_reason
            return f"{PREFIX_UNPROVABLE_REASON}:body_too_short"
    if storage_capacity is not None:
        return storage_capacity(record)
    return None


def _target_scope_identity(*, storage_capacity_evaluated: bool = False) -> dict[str, Any]:
    return {
        "script_dialogue_band": [
            f"{DIALOGUE_BAND_START:06X}",
            f"{DIALOGUE_BAND_END_EXCLUSIVE - 1:06X}",
        ],
        "script_out_of_band_reason": SCRIPT_OUT_OF_BAND_REASON,
        "script_band_evidence": [
            "tools/verify_stock_noninvasion.py:out_of_band_dialogue_writes",
            "tools/verify_all_stages_smoke.py:unit_segs=50-5D,6A-6F",
        ],
        "script_graphics_blocks": [
            [f"{low:06X}", f"{high - 1:06X}"] for low, high in SCRIPT_GRAPHICS_BLOCKS
        ],
        "script_graphics_block_reason": SCRIPT_GRAPHICS_BLOCK_REASON,
        "non_linguistic_fragment_reason": NON_LINGUISTIC_REASON,
        "non_linguistic_thresholds": {
            "tile_separator_chars": TILE_SEPARATOR_CHARS,
            "tile_separator_min_repeats": TILE_SEPARATOR_MIN_REPEATS,
            "repeated_character_min_run": REPEATED_CHARACTER_MIN_RUN,
            "single_kana_core": True,
        },
        "aux_text_evidence_banks": [
            f"{bank:02X}" for bank in sorted(AUX_TEXT_EVIDENCE_BANKS)
        ],
        "aux_bank_without_text_evidence_reason": AUX_BANK_WITHOUT_TEXT_EVIDENCE_REASON,
        "aux_bank_evidence": ["data/aux_text_ko.json", "data/aux_body_ko.json"],
        "unresolvable_in_original_reason": UNRESOLVABLE_REASON,
        "unresolvable_in_original_evidence": [
            "tools/verify_nondialogue_text.py:records_skipped_unresolvable_in_original"
        ],
        "curated_non_text_reason": CURATED_NON_TEXT_REASON,
        "curated_non_text_records": {
            record_id: dict(entry)
            for record_id, entry in sorted(CURATED_NON_TEXT_RECORDS.items())
        },
        "shared_token_short_record_reason": SHARED_TOKEN_SHORT_RECORD_REASON,
        "prefix_unprovable_reason": PREFIX_UNPROVABLE_REASON,
        "shared_token_short_record_evidence": [
            "tools/mixed_residual_reference_union.py:build_free_slot_inventory",
            "tools/expand_dictionary.py:slot_rewrite_refuse_reason",
        ],
        "shared_token_short_record_evaluated": storage_capacity_evaluated,
        "reference_union_narrowed": False,
    }


def _all_decision_records(
    population: ProvenRecordPopulation,
) -> tuple[tuple[ProvenRecord, str | None], ...]:
    rows: list[tuple[ProvenRecord, str | None]] = [
        (record, None) for record in population.localization_records
    ]
    rows.extend((item.record, item.reason) for item in population.excluded_records)
    rows.sort(key=lambda item: (item[0].region, item[0].boundary.start))
    ids = [record.record_id for record, _reason in rows]
    if len(ids) != len(set(ids)):
        raise DiscoveryError("proven decision population contains duplicate record IDs")
    return tuple(rows)


def _manifest_decision_row(
    record: ProvenRecord, decision: CandidateDecision
) -> dict[str, Any]:
    source_binding = {
        "record_id": record.record_id,
        "region": record.region,
        "logical_address": record.boundary.start,
        "boundary": record.boundary.to_json_data(),
        "original_payload_sha256": record.original_payload_sha256,
        "prefix_hex": record.prefix_bytes.hex(),
        "prefix_evidence": record.prefix_evidence,
        "source_text": record.source_text,
        "rendered_source_text": decision.rendered_source_text,
    }
    source_sha256 = deterministic_json_sha256(source_binding)
    target_binding = {
        "record_id": record.record_id,
        "source_sha256": source_sha256,
        "source_classification": decision.source_classification,
        "included": decision.included,
        "reason": decision.reason,
        "annotations": decision.annotations,
    }
    row = decision.to_json_data()
    row.update(
        {
            "abs": f"{record.boundary.start:06X}",
            "boundary": record.boundary.to_json_data(),
            "original_payload_sha256": record.original_payload_sha256,
            "prefix_hex": record.prefix_bytes.hex(),
            "prefix_evidence": record.prefix_evidence,
            "source_text": record.source_text,
            "source_body_sha256": _text_sha256(decision.rendered_source_text),
            "source_sha256": source_sha256,
            "target_sha256": deterministic_json_sha256(target_binding),
            "status_marker_codes": list(record.status_marker_codes),
        }
    )
    return row


def _counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    classifications = {
        name: sum(row["source_classification"] == name for row in rows)
        for name in ("mixed", "jp_only", "ko_only", "no_text", "excluded")
    }
    regions = {
        name: sum(row["region"] == name for row in rows)
        for name in ("script", "name75", "aux")
    }
    exclusion_reasons: dict[str, int] = {}
    for row in rows:
        if row["included"]:
            continue
        reason = str(row["reason"])
        exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1
    return {
        "decisions": len(rows),
        "included": sum(bool(row["included"]) for row in rows),
        "excluded": sum(not bool(row["included"]) for row in rows),
        "classifications": classifications,
        "regions": regions,
        "exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        # Reasons may carry per-record evidence after a colon (matched fragment
        # rule, measured body capacity, blocking slot). The base counts keep the
        # summary readable without dropping that evidence from the rows.
        "exclusion_reason_bases": dict(
            sorted(
                {
                    base: sum(
                        1
                        for row in rows
                        if not row["included"]
                        and str(row["reason"]).split(":", 1)[0] == base
                    )
                    for base in {
                        str(row["reason"]).split(":", 1)[0]
                        for row in rows
                        if not row["included"]
                    }
                }.items()
            )
        ),
    }


def discover_target_manifest(
    inputs: DiscoveryInputIdentities,
    tbl: Tbl,
    *,
    dictionary_factory: DictionaryFactory = Dictionary,
    working_dictionary_name: str = "monoeye_rom.Dictionary",
    prefix_evidence_kinds: Sequence[str] = (),
    enumerator: OriginalRomProvenRecordEnumerator | None = None,
    storage_capacity: StorageCapacityEvaluator | None = None,
) -> dict[str, Any]:
    """Discover and classify the complete proven population without ROM writes.

    ``dictionary_factory`` must construct the active Working-ROM dictionary.  A
    caller rendering an expanded patch should pass its ext/ext3-aware factory and
    a stable ``working_dictionary_name``; the resulting source text and tool
    identity are locked into the manifest.
    """
    if not working_dictionary_name:
        raise DiscoveryError("working dictionary name must not be empty")
    validated = _canonical_inputs(inputs)
    original_rom = _locked_bytes(
        validated.original_rom.path,
        size=validated.original_rom.size,
        sha256=validated.original_rom.sha256,
        label="Original ROM",
    )
    working_rom = _locked_bytes(
        validated.working_rom.path,
        size=validated.working_rom.size,
        sha256=validated.working_rom.sha256,
        label="Working ROM",
    )
    prefix_documents = _prefix_documents(validated, prefix_evidence_kinds)

    proven_enumerator = enumerator or OriginalRomProvenRecordEnumerator()
    population = proven_enumerator.enumerate(validated, tbl)
    try:
        dictionary = dictionary_factory(working_rom)
    except Exception as exc:
        raise DiscoveryError(f"cannot construct Working-ROM dictionary: {exc}") from exc

    try:
        original_dictionary = Dictionary(original_rom)
    except Exception as exc:
        raise DiscoveryError(f"cannot construct Original-ROM dictionary: {exc}") from exc

    def _original_text(record: ProvenRecord) -> str | None:
        """Expand the Original payload to detect unresolvable dictionary indices."""
        try:
            payload = _payload_at_boundary(
                original_rom, record, role="Original", require_terminator=False
            )
            return original_dictionary.expand(payload, tbl)
        except Exception:  # a record we cannot expand is handled by the caller
            return None

    decision_rows: list[dict[str, Any]] = []
    # One resolver for the whole population: its per-report address index is a
    # lookup accelerator, so reusing it changes speed, never a prefix decision.
    resolver = PrefixEvidenceResolver()
    for original_record, exclusion_reason in _all_decision_records(population):
        rendered_record = _resolve_working_record(
            original_record,
            original_rom=original_rom,
            working_rom=working_rom,
            working_rom_sha256=validated.working_rom.sha256,
            dictionary=dictionary,
            tbl=tbl,
            prefix_documents=prefix_documents,
            resolver=resolver,
        )
        decision = classify_record(
            rendered_record,
            # Scope rules read the rendered body, the same text the classifier
            # sees, so a prefix-only fragment cannot masquerade as a line.
            exclusion_reason=exclusion_reason
            or _scope_exclusion_reason(
                rendered_record,
                original_text=_original_text(original_record),
                original_payload=_payload_at_boundary(
                    original_rom, original_record, role="Original"
                ),
            ),
        )
        # The storage-capacity rule narrows the *target* population only. Running
        # it after classification keeps every other decision reason intact: a
        # ko_only or below-threshold record must keep reporting why it is not a
        # target, not "its body is too short".
        if decision.included and storage_capacity is not None:
            capacity_reason = storage_capacity(rendered_record)
            if capacity_reason is not None:
                decision = classify_record(
                    rendered_record, exclusion_reason=capacity_reason
                )
        decision_rows.append(_manifest_decision_row(rendered_record, decision))

    included = [row for row in decision_rows if row["included"]]
    excluded = [row for row in decision_rows if not row["included"]]
    target_ids = [row["record_id"] for row in included]
    detailed_reports = [
        {
            "kind": item.kind,
            "path": item.path,
            "size": item.size,
            "sha256": item.sha256,
            "generated_by": item.generated_by,
        }
        for item in validated.evidence
    ]
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_by": MANIFEST_GENERATOR,
        "read_only": True,
        "inputs": validated.to_json_data(),
        "tool_inputs": {
            "character_table": _table_identity(tbl),
            "working_dictionary": {"name": working_dictionary_name},
            "prefix_evidence_kinds": list(prefix_evidence_kinds),
        },
        "population": {
            "included": included,
            "excluded": excluded,
            "target_scope": _target_scope_identity(
                storage_capacity_evaluated=storage_capacity is not None
            ),
            "counts": _counts(decision_rows),
            "target_ids": target_ids,
            "source_population_sha256": deterministic_json_sha256(
                [row["source_sha256"] for row in decision_rows]
            ),
            "target_set_sha256": deterministic_json_sha256(
                [row["target_sha256"] for row in included]
            ),
            "reference_record_count": len(population.reference_records),
            "candidate_policy": "proven_records_only_no_character_class_expansion",
        },
        "detailed_reports": detailed_reports,
    }
    manifest["manifest_sha256"] = deterministic_json_sha256(manifest)
    return manifest


def validate_manifest_digest(manifest: Mapping[str, Any]) -> bool:
    expected = manifest.get("manifest_sha256")
    if not isinstance(expected, str):
        return False
    content = dict(manifest)
    content.pop("manifest_sha256", None)
    return deterministic_json_sha256(content) == expected


def write_target_manifest(manifest: Mapping[str, Any], output_path: str | Path) -> None:
    """Atomically write one JSON manifest while refusing ROM-like output paths."""
    output = Path(output_path)
    if output.suffix.lower() == ".wsc":
        raise DiscoveryError("refusing to write a ROM from read-only discovery")
    if not validate_manifest_digest(manifest):
        raise DiscoveryError("refusing to write a stale or malformed manifest digest")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            deterministic_json_dumps(manifest), encoding="utf-8", newline="\n"
        )
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def generate_target_manifest(
    inputs: DiscoveryInputIdentities,
    tbl: Tbl,
    output_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    """Discover completely, then publish the manifest; failures leave no partial file."""
    manifest = discover_target_manifest(inputs, tbl, **kwargs)
    write_target_manifest(manifest, output_path)
    return manifest


__all__ = [
    "DictionaryFactory",
    "DiscoveryError",
    "SHARED_TOKEN_SHORT_RECORD_REASON",
    "StorageCapacityEvaluator",
    "MANIFEST_GENERATOR",
    "MANIFEST_SCHEMA_VERSION",
    "discover_target_manifest",
    "generate_target_manifest",
    "validate_manifest_digest",
    "write_target_manifest",
]
