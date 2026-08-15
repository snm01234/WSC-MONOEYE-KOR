#!/usr/bin/env python3
"""Measure Korean sentence rates over the shared, vetted Aux population.

READ-ONLY. Record membership and boundaries come exclusively from the
identity-checked Original ROM and ``aux_text_blocks.json`` through the shared
proven-record enumerator. Working-ROM terminators never define the population.
Prefix bytes are removed only when :class:`PrefixEvidenceResolver` accepts the
apply-report evidence; missing or stale evidence classifies the complete record.

Report: ``out/patch/aux_sentence_rate.json``.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from mixed_residual_classification import (  # noqa: E402
    PrefixEvidenceResolver,
    classify_record,
)
from mixed_residual_models import (  # noqa: E402
    DiscoveryInputIdentities,
    ProvenRecord,
    deterministic_json_dumps,
    deterministic_json_sha256,
    identify_evidence,
    identify_rom,
    validate_discovery_inputs,
)
from mixed_residual_records import (  # noqa: E402
    AUX_EVIDENCE_KIND,
    AUX_EVIDENCE_PRODUCER,
    OriginalRomProvenRecordEnumerator,
    ProvenRecordPopulation,
)
from monoeye_rom import Tbl, find_rom, stock_base  # noqa: E402

DEFAULT_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_AUX_REPORT = ROOT / "out/patch/aux_ko_report.json"
DEFAULT_META = ROOT / "out/patch/ext_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/aux_sentence_rate.json"
CLASSIFICATIONS = ("mixed", "jp_only", "ko_only", "no_text", "excluded")


class AuxRateError(ValueError):
    """Raised when the fixed vetted population cannot be rendered safely."""


class TextDictionary(Protocol):
    def expand(self, payload: bytes, tbl: Tbl) -> str: ...


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise AuxRateError(f"cannot read tool input {resolved}: {exc}") from exc
    return {
        "path": str(resolved),
        "size": len(raw),
        "sha256": _sha256_bytes(raw),
    }


def _load_prefix_report(path: Path) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    """Load optional prefix evidence while retaining a stable input identity.

    Missing or malformed evidence is intentionally non-fatal: the shared prefix
    resolver then classifies complete records, as required by the fail-closed
    prefix policy.
    """
    resolved = path.expanduser().resolve()
    identity: dict[str, Any] = {"path": str(resolved)}
    if not resolved.is_file():
        identity.update({"status": "missing", "size": 0, "sha256": None})
        return None, identity
    try:
        raw = resolved.read_bytes()
    except OSError:
        identity.update({"status": "unreadable", "size": 0, "sha256": None})
        return None, identity
    identity.update(
        {"size": len(raw), "sha256": _sha256_bytes(raw), "status": "loaded"}
    )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        identity["status"] = "invalid_json"
        return None, identity
    if not isinstance(document, Mapping):
        identity["status"] = "invalid_root"
        return None, identity
    identity["generated_by"] = document.get("generated_by")
    return document, identity


def _fixed_payload(rom: bytes, record: ProvenRecord, *, role: str) -> bytes:
    """Read one payload and terminator at its Original-derived offsets."""
    base = stock_base(rom)
    start = base + record.boundary.start
    end = start + record.boundary.payload_capacity
    terminator = base + record.boundary.terminator_offset
    if start < 0 or end > len(rom) or terminator >= len(rom):
        raise AuxRateError(
            f"{record.record_id} Original-derived boundary is outside the {role} ROM"
        )
    if rom[terminator] != 0:
        raise AuxRateError(
            f"{record.record_id} {role} terminator mismatch at "
            f"{record.boundary.terminator_offset:06X}"
        )
    return bytes(rom[start:end])


def _counter_json(counter: collections.Counter[str]) -> dict[str, int]:
    return {name: counter[name] for name in CLASSIFICATIONS}


def _pct(counter: collections.Counter[str], key: str) -> float:
    total = sum(counter.values())
    return round(counter[key] / total * 100, 2) if total else 0.0


def measure_aux_population(
    inputs: DiscoveryInputIdentities,
    tbl: Tbl,
    dictionary: TextDictionary,
    prefix_report: Mapping[str, Any] | None,
    *,
    min_core: int = 6,
    enumerator: OriginalRomProvenRecordEnumerator | None = None,
) -> dict[str, Any]:
    """Render and classify exactly the shared localizable Aux population."""
    if isinstance(min_core, bool) or not isinstance(min_core, int) or min_core < 0:
        raise AuxRateError("min_core must be a non-negative integer")

    validated = validate_discovery_inputs(inputs)
    original = Path(validated.original_rom.path).read_bytes()
    working = Path(validated.working_rom.path).read_bytes()
    population: ProvenRecordPopulation = (
        enumerator or OriginalRomProvenRecordEnumerator()
    ).enumerate(validated, tbl)
    records = sorted(
        (record for record in population.localization_records if record.region == "aux"),
        key=lambda record: record.boundary.start,
    )

    resolver = PrefixEvidenceResolver()
    overall: collections.Counter[str] = collections.Counter()
    sentences: collections.Counter[str] = collections.Counter()
    by_bank: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    samples: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    population_rows: list[dict[str, Any]] = []
    trusted_prefixes = 0

    for record in records:
        original_payload = _fixed_payload(original, record, role="Original")
        if _sha256_bytes(original_payload) != record.original_payload_sha256:
            raise AuxRateError(
                f"{record.record_id} Original payload digest does not match enumeration"
            )
        working_payload = _fixed_payload(working, record, role="Working")
        try:
            rendered = resolver.resolve_record(
                record,
                original_payload,
                working_payload,
                lambda payload: dictionary.expand(payload, tbl),
                prefix_report,
                evidence_name="aux_apply_report",
            )
        except Exception as exc:
            raise AuxRateError(f"cannot render {record.record_id}: {exc}") from exc

        decision = classify_record(rendered)
        classification = decision.source_classification
        overall[classification] += 1
        by_bank[f"{record.bank:02X}"][classification] += 1
        if rendered.prefix_evidence is not None:
            trusted_prefixes += 1
        if decision.core_count >= min_core:
            sentences[classification] += 1
            if len(samples[classification]) < 5:
                samples[classification].append(
                    {
                        "site": f"{record.bank:02X}:{record.boundary.start & 0xFFFF:04X}",
                        "text": decision.rendered_source_text[:52],
                    }
                )

        population_rows.append(
            {
                "record_id": record.record_id,
                "logical_address": record.boundary.start,
                "abs": f"{record.boundary.start:06X}",
                "boundary": record.boundary.to_json_data(),
                "original_payload_sha256": record.original_payload_sha256,
                "prefix_hex": rendered.prefix_bytes.hex(),
                "prefix_evidence": rendered.prefix_evidence,
                "source_classification": classification,
                "core_count": decision.core_count,
            }
        )

    record_identities = [
        {
            "record_id": row["record_id"],
            "boundary": row["boundary"],
            "original_payload_sha256": row["original_payload_sha256"],
        }
        for row in population_rows
    ]
    rendered_identities = [
        {
            "record_id": row["record_id"],
            "prefix_hex": row["prefix_hex"],
            "prefix_evidence": row["prefix_evidence"],
            "source_classification": row["source_classification"],
            "core_count": row["core_count"],
        }
        for row in population_rows
    ]
    population_identity = {
        "source": "mixed_residual_records.OriginalRomProvenRecordEnumerator",
        "kind": "original_derived_contiguity_vetted_aux",
        "boundaries_derived_from": "original_rom",
        "aux_scope": "identity_checked_contiguity_vetted_blocks",
    }
    population_sha256 = deterministic_json_sha256(
        {"identity": population_identity, "records": record_identities}
    )
    classification_counts = _counter_json(overall)
    sentence_counts = _counter_json(sentences)
    return {
        "population": {
            **population_identity,
            "population_sha256": population_sha256,
            "record_count": len(population_rows),
            "record_ids": [row["record_id"] for row in population_rows],
            "record_ids_sha256": deterministic_json_sha256(
                [row["record_id"] for row in population_rows]
            ),
            "records_sha256": deterministic_json_sha256(record_identities),
            "rendered_population_sha256": deterministic_json_sha256(
                rendered_identities
            ),
            "classification_counts": classification_counts,
            "trusted_prefix_count": trusted_prefixes,
            "untrusted_prefix_count": len(population_rows) - trusted_prefixes,
            "records": population_rows,
        },
        "all_records": {
            "total": len(population_rows),
            **classification_counts,
            "ko_only_pct": _pct(overall, "ko_only"),
        },
        "sentences": {
            "total": sum(sentences.values()),
            **sentence_counts,
            "ko_only_pct": _pct(sentences, "ko_only"),
        },
        "by_bank": {
            bank: _counter_json(counter) for bank, counter in sorted(by_bank.items())
        },
        "samples": dict(sorted(samples.items())),
    }


def _write_report(report: Mapping[str, Any], output: Path) -> None:
    if output.suffix.lower() == ".wsc":
        raise AuxRateError("refusing to write a .wsc — this measurement is read-only")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(deterministic_json_dumps(report), encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM, help="Working/Candidate ROM")
    ap.add_argument("--original-rom", type=Path, default=None)
    ap.add_argument("--blocks", type=Path, default=DEFAULT_BLOCKS)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument("--aux-report", type=Path, default=DEFAULT_AUX_REPORT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-core", type=int, default=6)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    original_path = args.original_rom or find_rom(ROOT)
    original_identity = identify_rom(original_path, "original")
    working_identity = identify_rom(args.rom, "working")
    blocks_identity = identify_evidence(
        args.blocks,
        kind=AUX_EVIDENCE_KIND,
        generated_by=AUX_EVIDENCE_PRODUCER,
        original_rom=original_identity,
    )
    inputs = DiscoveryInputIdentities(
        original_identity, working_identity, (blocks_identity,)
    )
    tbl = Tbl.load(args.tbl)
    working = Path(working_identity.path).read_bytes()
    dictionary = make_dictionary_ext3(
        working, load_ext_meta(args.meta), load_ext_meta(args.ext3_meta)
    )
    prefix_report, prefix_identity = _load_prefix_report(args.aux_report)
    measured = measure_aux_population(
        inputs,
        tbl,
        dictionary,
        prefix_report,
        min_core=args.min_core,
    )

    report = {
        "schema_version": 2,
        "generated_by": "tools/measure_aux_sentence_rate.py",
        "read_only": True,
        "inputs": inputs.to_json_data(),
        "tool_inputs": {
            "character_table": _file_identity(args.tbl),
            "ext_dictionary_meta": _file_identity(args.meta),
            "ext3_dictionary_meta": _file_identity(args.ext3_meta),
            "prefix_report": prefix_identity,
            "min_core_chars_for_sentence": args.min_core,
        },
        **measured,
        "population_note": (
            "denominator is the shared Original-ROM-derived, contiguity-vetted Aux "
            "population; no character-class scan can add records"
        ),
    }
    report["report_sha256"] = deterministic_json_sha256(report)
    _write_report(report, args.out)

    if not args.quiet:
        population = report["population"]
        print(f"working ROM : {Path(working_identity.path).name}")
        print(
            f"vetted Aux population: {population['record_count']} "
            f"({population['records_sha256']})"
        )
        print(
            f"trusted prefixes: {population['trusted_prefix_count']} / "
            f"{population['record_count']}"
        )
        for label, counts in (
            ("전체 레코드", report["all_records"]),
            (f"구문(코어 {args.min_core}자+)", report["sentences"]),
        ):
            total = counts["total"]
            print(f"\n=== {label}  {total} ===")
            for name in CLASSIFICATIONS:
                percentage = counts[name] / total * 100 if total else 0.0
                print(f"  {name:8s} {counts[name]:6d}  {percentage:5.2f}%")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
