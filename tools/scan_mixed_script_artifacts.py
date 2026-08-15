#!/usr/bin/env python3
"""Scan proven non-dialogue text for Korean/Japanese composition artifacts.

READ-ONLY. Aux records come only from identity-checked contiguity-vetted blocks
and all record boundaries come from the Original ROM. Name75 enumeration and
the existing broken-word/split-compound predicates remain part of the scan.
Prefix bytes are excluded only when the shared evidence resolver validates a
successful, positive, address/digest-bound report row.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from mixed_residual_classification import PrefixEvidenceResolver  # noqa: E402
from mixed_residual_models import (  # noqa: E402
    DiscoveryInputIdentities,
    ProvenRecord,
    RomIdentity,
    identify_evidence,
    identify_rom,
)
from mixed_residual_records import (  # noqa: E402
    AUX_EVIDENCE_KIND,
    AUX_EVIDENCE_PRODUCER,
    OriginalRomProvenRecordEnumerator,
    ProvenRecordPopulation,
)
from monoeye_rom import Tbl, stock_base  # noqa: E402
from measure_aux_prefix_rule import BANK_RULES, prefix_len as rule_prefix_len

DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_META = ROOT / "out/patch/ext_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

HANGUL = re.compile(r"[\uac00-\ud7a3]")
KATAKANA = re.compile(r"[\u30a0-\u30ff]")
HIRAGANA = re.compile(r"[\u3040-\u309f]")
LONG_VOWEL = "ー－"
NOT_A_LETTER = "・"
MAX_LISTED = 120
SCHEMA_VERSION = 2


def load_preserved_prefixes(path: Path) -> Dict[int, int]:
    """Legacy report reader retained for callers pending shared-resolver migration.

    The scanner itself does not use this compatibility helper. It validates each
    prefix against Original-ROM bytes through :class:`PrefixEvidenceResolver`.
    """
    out: Dict[int, int] = {}
    if not path.exists():
        return out
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not blob.get("ok"):
        return out
    for row in blob.get("applied") or []:
        try:
            k = int(row.get("prefix_bytes") or 0)
            if k > 0:
                out[int(row["abs"], 16)] = k
        except (KeyError, TypeError, ValueError):
            continue
    return out


def is_hangul(ch: str) -> bool:
    return bool(HANGUL.match(ch))


def is_kata_glue(ch: str) -> bool:
    if not ch or ch in NOT_A_LETTER:
        return False
    return bool(KATAKANA.match(ch)) or ch in LONG_VOWEL


def classify_boundaries(text: str) -> List[dict]:
    """Return every Hangul/Japanese boundary in ``text``."""
    out: List[dict] = []
    for i, ch in enumerate(text):
        if not is_hangul(ch):
            continue
        prev_ch = text[i - 1] if i > 0 else ""
        next_ch = text[i + 1] if i + 1 < len(text) else ""
        at_start = not (prev_ch and is_hangul(prev_ch))
        at_end = not (next_ch and is_hangul(next_ch))
        for at_edge, dot_ch, across, side in (
            (at_start, prev_ch, text[i - 2] if i > 1 else "", "before"),
            (at_end, next_ch, text[i + 2] if i + 2 < len(text) else "", "after"),
        ):
            if (
                at_edge
                and dot_ch == NOT_A_LETTER
                and across
                and (KATAKANA.match(across) or across in LONG_VOWEL)
            ):
                lo = max(0, i - 10)
                out.append(
                    {
                        "severity": "split_compound",
                        "side": side,
                        "neighbour": across,
                        "context": text[lo : i + 10],
                    }
                )
        if at_start and is_kata_glue(prev_ch):
            lo = max(0, i - 8)
            out.append(
                {
                    "severity": "broken_word",
                    "side": "before",
                    "neighbour": prev_ch,
                    "context": text[lo : i + 8],
                }
            )
        if at_end and next_ch:
            if is_kata_glue(next_ch):
                lo = max(0, i - 8)
                out.append(
                    {
                        "severity": "broken_word",
                        "side": "after",
                        "neighbour": next_ch,
                        "context": text[lo : i + 9],
                    }
                )
            elif HIRAGANA.match(next_ch):
                lo = max(0, i - 8)
                out.append(
                    {
                        "severity": "particle",
                        "side": "after",
                        "neighbour": next_ch,
                        "context": text[lo : i + 9],
                    }
                )
    return out


def artifact_population_records(
    population: ProvenRecordPopulation,
) -> tuple[ProvenRecord, ...]:
    """Select Name75 plus vetted, non-dictionary Aux from the shared population."""
    records = tuple(
        record
        for record in population.localization_records
        if record.region in ("aux", "name75")
    )
    return tuple(sorted(records, key=lambda row: (row.region, row.boundary.start)))


def _payload_at_boundary(rom: bytes, record: ProvenRecord) -> tuple[bytes, bool]:
    """Read one candidate payload at its fixed Original-derived extent."""
    base = stock_base(rom)
    start = base + record.boundary.start
    end = start + record.boundary.payload_capacity
    terminator = base + record.boundary.terminator_offset
    if start < 0 or end > len(rom) or terminator >= len(rom):
        raise ValueError(f"{record.record_id} boundary is outside the ROM")
    return bytes(rom[start:end]), rom[terminator] == 0


def scan_artifact_population(
    records: Sequence[ProvenRecord],
    original_rom: bytes,
    candidate_rom: bytes,
    decoder: Callable[[bytes], str],
    prefix_report: Mapping[str, Any] | None,
    *,
    max_broken: int = 0,
) -> dict[str, Any]:
    """Scan a shared proven population using fixed Original-ROM boundaries."""
    resolver = PrefixEvidenceResolver()
    broken: list[dict[str, Any]] = []
    split_compounds: list[dict[str, Any]] = []
    scan_errors: list[dict[str, str]] = []
    particles = 0
    records_with_hangul = 0
    prefixes_skipped = 0
    by_neighbour: collections.Counter[str] = collections.Counter()
    by_context: collections.Counter[str] = collections.Counter()

    aux_records = sum(record.region == "aux" for record in records)
    name75_records = sum(record.region == "name75" for record in records)
    for record in records:
        logical = record.boundary.start
        try:
            original_payload, original_terminated = _payload_at_boundary(
                original_rom, record
            )
            candidate_payload, candidate_terminated = _payload_at_boundary(
                candidate_rom, record
            )
            if not original_terminated:
                raise ValueError("Original terminator is absent at the proven boundary")
            if not candidate_terminated:
                scan_errors.append(
                    {
                        "record_id": record.record_id,
                        "reason": "candidate_terminator_mismatch",
                    }
                )
            rule = BANK_RULES.get(record.bank) if record.region == "aux" else None
            rule_k = 0 if rule is None else rule_prefix_len(original_payload, rule)
            if rule_k > 0:
                text = decoder(candidate_payload[rule_k:])
                prefixes_skipped += 1
                rendered = None
            else:
                rendered = resolver.resolve_record(
                    record,
                    original_payload,
                    candidate_payload,
                    decoder,
                    prefix_report,
                    evidence_name="prefix_report",
                )
                text = rendered.rendered_body
        except Exception as exc:
            scan_errors.append(
                {"record_id": record.record_id, "reason": f"render_error:{exc}"}
            )
            continue

        if rendered is not None and rendered.prefix_evidence is not None:
            prefixes_skipped += 1
        if not HANGUL.search(text):
            continue
        records_with_hangul += 1
        site = f"{logical >> 16:02X}:{logical & 0xFFFF:04X}"
        for hit in classify_boundaries(text):
            if hit["severity"] == "particle":
                particles += 1
                continue
            row = {"site": site, "region": record.region, **hit}
            if hit["severity"] == "split_compound":
                split_compounds.append(row)
                continue
            by_neighbour[hit["neighbour"]] += 1
            by_context[hit["context"]] += 1
            broken.append(row)

    counts = {
        "population_records": len(records),
        "aux_records": aux_records,
        "name75_records": name75_records,
        "records_with_hangul": records_with_hangul,
        "preserved_prefixes_skipped": prefixes_skipped,
        "broken_word_hits": len(broken),
        "split_compound_hits": len(split_compounds),
        "particle_hits": particles,
        "scan_errors": len(scan_errors),
    }
    return {
        "ok": (
            counts["broken_word_hits"] <= max_broken
            and counts["split_compound_hits"] == 0
            and counts["scan_errors"] == 0
        ),
        "counts": counts,
        # Stable top-level gate fields are emitted even when their value is zero.
        "broken_word_hits": counts["broken_word_hits"],
        "split_compound_hits": counts["split_compound_hits"],
        "particle_hits": counts["particle_hits"],
        "records_with_hangul": counts["records_with_hangul"],
        "preserved_prefixes_skipped": counts["preserved_prefixes_skipped"],
        "max_broken_allowed": max_broken,
        "split_compound_sites": split_compounds[:MAX_LISTED],
        "broken_sample": broken[:MAX_LISTED],
        "scan_error_sample": scan_errors[:MAX_LISTED],
        "top_contexts": by_context.most_common(60),
        "neighbour_histogram": by_neighbour.most_common(30),
    }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_prefix_input(path: Path) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read prefix report {path}: {exc}") from exc
    identity: dict[str, Any] = {
        "path": str(path.resolve()),
        "size": len(raw),
        "sha256": _sha256_bytes(raw),
    }
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        identity["json_object"] = False
        return None, identity
    if not isinstance(document, Mapping):
        identity["json_object"] = False
        return None, identity
    identity["json_object"] = True
    identity["report_ok"] = document.get("ok") is True
    return document, identity


def _working_identity(candidate: RomIdentity) -> RomIdentity:
    return RomIdentity(
        role="working",
        path=candidate.path,
        size=candidate.size,
        sha256=candidate.sha256,
    )


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--original-rom",
        "--base-rom",
        dest="original_rom",
        type=Path,
        required=True,
        help="8 MiB Original ROM used for all record boundaries",
    )
    ap.add_argument(
        "--blocks",
        type=Path,
        required=True,
        help="identity-bound out/script/aux_text_blocks.json",
    )
    ap.add_argument(
        "--prefix-report",
        "--prefix-evidence",
        "--aux-report",
        dest="prefix_report",
        type=Path,
        required=True,
        help="prefix evidence report (untrusted rows cause full-record scanning)",
    )
    ap.add_argument(
        "--candidate-rom",
        "--candidate",
        "--rom",
        dest="candidate_rom",
        type=Path,
        required=True,
        help="Candidate ROM whose rendered text is scanned",
    )
    ap.add_argument(
        "--output",
        "--out",
        dest="output",
        type=Path,
        required=True,
        help="JSON report output path",
    )
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument(
        "--max-broken",
        type=int,
        default=0,
        help="allowed broken_word hits before exit 1",
    )
    ap.add_argument("--quiet", action="store_true")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.output.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this scan is read-only")
    if args.max_broken < 0:
        raise SystemExit("--max-broken must be non-negative")

    original_identity = identify_rom(args.original_rom, "original")
    candidate_identity = identify_rom(args.candidate_rom, "candidate")
    blocks_identity = identify_evidence(
        args.blocks,
        kind=AUX_EVIDENCE_KIND,
        generated_by=AUX_EVIDENCE_PRODUCER,
        original_rom=original_identity,
    )
    inputs = DiscoveryInputIdentities(
        original_rom=original_identity,
        working_rom=_working_identity(candidate_identity),
        evidence=(blocks_identity,),
    )
    tbl = Tbl.load(args.tbl)
    population = OriginalRomProvenRecordEnumerator().enumerate(inputs, tbl)
    records = artifact_population_records(population)

    original = Path(original_identity.path).read_bytes()
    candidate = Path(candidate_identity.path).read_bytes()
    dictionary = make_dictionary_ext3(
        candidate, load_ext_meta(args.meta), load_ext_meta(args.ext3_meta)
    )
    prefix_report, prefix_identity = _read_prefix_input(args.prefix_report)
    result = scan_artifact_population(
        records,
        original,
        candidate,
        lambda payload: dictionary.expand(payload, tbl),
        prefix_report,
        max_broken=args.max_broken,
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "tools/scan_mixed_script_artifacts.py",
        "read_only": True,
        "inputs": {
            "original_rom": original_identity.to_json_data(),
            "aux_text_blocks": blocks_identity.to_json_data(),
            "prefix_report": prefix_identity,
            "candidate_rom": candidate_identity.to_json_data(),
            "output": str(args.output.resolve()),
        },
        "population": {
            "source": "mixed_residual_records.OriginalRomProvenRecordEnumerator",
            "boundaries_derived_from": "original_rom",
            "aux_scope": "identity_checked_contiguity_vetted_blocks",
            "name75_scope": "expand_dictionary.NAME75_RANGES",
            "bank_5f_dictionary_storage_excluded": True,
            "records": result["counts"]["population_records"],
            "aux_records": result["counts"]["aux_records"],
            "name75_records": result["counts"]["name75_records"],
        },
        **result,
        "note": (
            "broken_word = a Hangul run touching katakana or a long-vowel mark; "
            "split_compound = Hangul one middle dot away from katakana; particle "
            "= Hangul followed by hiragana and is not a defect. Aux records are "
            "limited to identity-checked vetted blocks and all boundaries derive "
            "from the Original ROM."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        counts = report["counts"]
        print(f"candidate : {args.candidate_rom}")
        print(
            "population: "
            f"{counts['population_records']} "
            f"(aux {counts['aux_records']}, name75 {counts['name75_records']})"
        )
        print(f"records with Hangul : {counts['records_with_hangul']}")
        print(f"preserved prefixes skipped : {counts['preserved_prefixes_skipped']}")
        print(
            f"broken_word hits : {counts['broken_word_hits']} "
            f"(allowed {args.max_broken})"
        )
        print(f"split_compound hits : {counts['split_compound_hits']}")
        print(f"particle hits : {counts['particle_hits']} (not a defect)")
        print(f"scan errors : {counts['scan_errors']}")
        print(f"wrote {args.output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
