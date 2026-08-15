#!/usr/bin/env python3
"""Build the reviewed residual translation catalog from a reviewed values file.

The tool never invents a translation.  Korean text, review notes, and term pins
come only from ``--values``; everything else is copied from the digest-valid
target manifest so a row can never drift from the source it was reviewed
against:

* ``region``, ``abs``, ``source_text``, ``source_body_sha256``, ``target_sha256``
* ``terminology_refs`` for katakana tokens, built from the loaded terminology
  index for registered terms and from reviewed ``review:jp=ko`` pins otherwise

``report`` lists the work still owed per target, including the katakana tokens
that need a reviewed pin, so a batch can be authored without guessing.

Both subcommands are read-only with respect to ROMs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from mixed_residual_discovery import validate_manifest_digest  # noqa: E402
from mixed_residual_translations import (  # noqa: E402
    CATALOG_SCHEMA_VERSION,
    DEFECT_ANNOTATIONS,
    TerminologyIndex,
    load_terminology_index,
)

GENERATED_BY = "tools/build_mixed_residual_catalog.py"
DEFAULT_TERMINOLOGY = (
    "data/proper_nouns_ko.json",
    "data/unit_names_ko.json",
    "data/weapon_names_ko.json",
    "data/ui_proper_nouns_ko.json",
    "data/name75_terms_ko.json",
)


class CatalogBuildError(ValueError):
    """Raised when the values file cannot be bound to the manifest."""


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogBuildError(f"cannot read {label}: {path}: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogBuildError(f"{label} is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(document, dict):
        raise CatalogBuildError(f"{label} root must be a JSON object: {path}")
    return document


def _manifest_rows(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if not validate_manifest_digest(manifest):
        raise CatalogBuildError("refusing to build against a stale manifest digest")
    population = manifest.get("population")
    if not isinstance(population, Mapping):
        raise CatalogBuildError("manifest is missing its population")
    included = population.get("included")
    if not isinstance(included, list):
        raise CatalogBuildError("manifest population.included must be a list")
    rows: dict[str, Mapping[str, Any]] = {}
    for row in included:
        if not isinstance(row, Mapping):
            raise CatalogBuildError("manifest target rows must be objects")
        rows[str(row.get("record_id"))] = row
    return rows


def _values_entries(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    entries = document.get("entries")
    if not isinstance(entries, Mapping):
        raise CatalogBuildError("values file requires an 'entries' object")
    out: dict[str, Mapping[str, Any]] = {}
    for record_id, value in entries.items():
        if not isinstance(value, Mapping):
            raise CatalogBuildError(f"values entry {record_id} must be an object")
        out[str(record_id)] = value
    return out


def _merge_values(paths: Sequence[Path]) -> tuple[dict[str, Mapping[str, Any]], str, list[dict[str, Any]]]:
    """Merge batch values files, refusing a record reviewed twice."""
    merged: dict[str, Mapping[str, Any]] = {}
    owner: dict[str, Path] = {}
    reviewer = ""
    identities: list[dict[str, Any]] = []
    for path in paths:
        document = _read_object(path, "values file")
        reviewer = reviewer or str(document.get("reviewer", ""))
        for record_id, value in _values_entries(document).items():
            if record_id in merged:
                raise CatalogBuildError(
                    f"{record_id} is reviewed in both {owner[record_id]} and {path}"
                )
            merged[record_id] = value
            owner[record_id] = path
        identities.append(
            {
                "path": path.as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "entries": len(_values_entries(document)),
            }
        )
    return merged, reviewer, identities


def _token_requirements(
    index: TerminologyIndex, source_text: str
) -> tuple[list[str], list[tuple[str, tuple[str, ...]]]]:
    """Return ``(unregistered_tokens, ambiguous_registered_tokens)``."""
    unregistered: list[str] = []
    ambiguous: list[tuple[str, tuple[str, ...]]] = []
    for token, registered in index.tokenize(source_text):
        if not registered:
            unregistered.append(token)
            continue
        variants = index.variants(token)
        if len(variants) != 1:
            ambiguous.append((token, variants))
    return unregistered, ambiguous


def _terminology_refs(
    index: TerminologyIndex,
    source_text: str,
    pins: Mapping[str, str],
    record_id: str,
) -> tuple[str, ...]:
    refs: list[str] = []
    for token, registered in index.tokenize(source_text):
        chosen = pins.get(token)
        if registered:
            variants = index.variants(token)
            if chosen is None:
                if len(variants) != 1:
                    raise CatalogBuildError(
                        f"{record_id} needs a reviewed pin for ambiguous term {token!r}"
                        f" among {list(variants)}"
                    )
                chosen = variants[0]
            if chosen not in variants:
                raise CatalogBuildError(
                    f"{record_id} pins {token!r} to {chosen!r}, which is not an"
                    f" established spelling {list(variants)}"
                )
            term = next(item for item in index.lookup(token) if item.ko == chosen)
            ref = f"{term.source}#{term.jp}={term.ko}"
        else:
            if chosen is None:
                raise CatalogBuildError(
                    f"{record_id} needs a reviewed pin for unregistered term {token!r}"
                )
            ref = f"review:{token}={chosen}"
        if ref not in refs:
            refs.append(ref)
    return tuple(refs)


def _pins(value: Mapping[str, Any], record_id: str) -> dict[str, str]:
    raw = value.get("terms", {})
    if not isinstance(raw, Mapping):
        raise CatalogBuildError(f"{record_id} 'terms' must be an object")
    pins: dict[str, str] = {}
    for token, korean in raw.items():
        if not isinstance(token, str) or not isinstance(korean, str) or not korean:
            raise CatalogBuildError(f"{record_id} has an invalid term pin: {token!r}")
        pins[token] = korean
    return pins


def build(
    manifest_path: Path,
    values_paths: Sequence[Path],
    terminology_paths: Sequence[str],
    reviewer: str,
    *,
    skip_unlisted: bool = False,
) -> dict[str, Any]:
    manifest = _read_object(manifest_path, "target manifest")
    rows = _manifest_rows(manifest)
    entries_in, values_reviewer, values_identities = _merge_values(values_paths)
    index = load_terminology_index(list(terminology_paths))
    inputs = manifest.get("inputs")
    if not isinstance(inputs, Mapping) or not isinstance(inputs.get("original_rom"), Mapping):
        raise CatalogBuildError("manifest is missing its Original ROM identity")
    original_sha256 = str(inputs["original_rom"]["sha256"])
    default_reviewer = values_reviewer or reviewer

    catalog_entries: list[dict[str, Any]] = []
    skipped_unlisted: list[str] = []
    for record_id in sorted(entries_in):
        value = entries_in[record_id]
        row = rows.get(record_id)
        if row is None:
            if skip_unlisted:
                # Reviewed Korean for a record the current manifest no longer
                # targets (a scope exclusion). The value is kept in the values
                # file for a future scope, and reported here rather than dropped
                # silently.
                skipped_unlisted.append(record_id)
                continue
            raise CatalogBuildError(
                f"{record_id} is not an included target of this manifest"
            )
        korean = value.get("ko")
        if not isinstance(korean, str) or not korean.strip():
            raise CatalogBuildError(f"{record_id} requires non-empty Korean text")
        source_text = str(row.get("rendered_source_text", ""))
        digest = _text_sha256(source_text)
        if digest != str(row.get("source_body_sha256", "")):
            raise CatalogBuildError(f"{record_id} manifest source digest is inconsistent")
        annotations = tuple(str(item) for item in row.get("annotations", ()) or ())
        notes = str(value.get("review_notes", ""))
        complete = bool(value.get("complete_expression_review", False))
        if set(annotations) & DEFECT_ANNOTATIONS and not (complete and notes.strip()):
            raise CatalogBuildError(
                f"{record_id} carries {sorted(set(annotations) & DEFECT_ANNOTATIONS)}"
                " and needs complete_expression_review with review_notes"
            )
        refs = _terminology_refs(index, source_text, _pins(value, record_id), record_id)
        catalog_entries.append(
            {
                "record_id": record_id,
                "region": str(row.get("region")),
                "abs": str(row.get("abs")),
                "source_text": source_text,
                "source_body_sha256": digest,
                "ko": korean,
                "review_status": str(value.get("review_status", "approved")),
                "terminology_refs": list(refs),
                "target_sha256": str(row.get("target_sha256")),
                "reviewer": str(value.get("reviewer", "")) or default_reviewer,
                "complete_expression_review": complete,
                "review_notes": notes,
            }
        )

    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "description": (
            "Reviewed Korean translations for the mixed/Japanese residual target"
            " set. Do not hand-edit: edit the values file and rebuild."
        ),
        "original_rom_sha256": original_sha256,
        "manifest_sha256": str(manifest.get("manifest_sha256")),
        "terminology_sources": list(terminology_paths),
        "values_sources": values_identities,
        "counts": {
            "targets": len(rows),
            "entries": len(catalog_entries),
            "remaining": len(rows) - len(catalog_entries),
            "skipped_not_in_manifest": len(skipped_unlisted),
        },
        "skipped_not_in_manifest": skipped_unlisted,
        "entries": catalog_entries,
    }


def report(
    manifest_path: Path,
    values_paths: Sequence[Path],
    terminology_paths: Sequence[str],
    region: str | None,
    limit: int,
) -> dict[str, Any]:
    manifest = _read_object(manifest_path, "target manifest")
    rows = _manifest_rows(manifest)
    index = load_terminology_index(list(terminology_paths))
    existing = [path for path in values_paths if path.is_file()]
    done = set(_merge_values(existing)[0]) if existing else set()

    todo: list[dict[str, Any]] = []
    for record_id in sorted(rows, key=lambda key: (rows[key]["region"], rows[key]["abs"])):
        if record_id in done:
            continue
        row = rows[record_id]
        if region is not None and str(row.get("region")) != region:
            continue
        source_text = str(row.get("rendered_source_text", ""))
        unregistered, ambiguous = _token_requirements(index, source_text)
        annotations = [str(item) for item in row.get("annotations", ()) or ()]
        todo.append(
            {
                "record_id": record_id,
                "abs": str(row.get("abs")),
                "region": str(row.get("region")),
                "source_classification": str(row.get("source_classification")),
                "source_text": source_text,
                "annotations": annotations,
                "needs_review_notes": bool(set(annotations) & DEFECT_ANNOTATIONS),
                "unregistered_terms": unregistered,
                "ambiguous_terms": [
                    {"token": token, "variants": list(variants)}
                    for token, variants in ambiguous
                ],
            }
        )

    return {
        "generated_by": GENERATED_BY,
        "manifest_sha256": str(manifest.get("manifest_sha256")),
        "counts": {
            "targets": len(rows),
            "translated": len(done),
            "remaining": len(todo),
        },
        "remaining_by_region": {
            name: sum(1 for item in todo if item["region"] == name)
            for name in ("script", "aux", "name75")
        },
        "todo": todo[:limit] if limit > 0 else todo,
    }


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--manifest", type=Path, required=True)
    build_parser.add_argument("--values", type=Path, action="append", default=[])
    build_parser.add_argument("--values-glob", default=None)
    build_parser.add_argument("--terminology", action="append", default=[])
    build_parser.add_argument("--reviewer", default="")
    build_parser.add_argument(
        "--skip-unlisted",
        action="store_true",
        help="keep reviewed rows whose record is no longer an included target out "
        "of the catalog instead of failing, and report them",
    )
    build_parser.add_argument("--out", type=Path, required=True)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--manifest", type=Path, required=True)
    report_parser.add_argument("--values", type=Path, action="append", default=[])
    report_parser.add_argument("--values-glob", default=None)
    report_parser.add_argument("--terminology", action="append", default=[])
    report_parser.add_argument("--region", default=None)
    report_parser.add_argument("--limit", type=int, default=0)
    report_parser.add_argument("--out", type=Path, default=None)

    args = parser.parse_args(argv)
    terminology = args.terminology or list(DEFAULT_TERMINOLOGY)
    candidates = list(args.values or [])
    if args.values_glob:
        candidates.extend(sorted(ROOT.glob(args.values_glob)))
    values = sorted(
        dict.fromkeys(Path(item).resolve() for item in candidates),
        key=lambda path: path.as_posix(),
    )
    if not values:
        print("REJECT no values files were given", file=sys.stderr)
        return 2
    try:
        if args.command == "build":
            document = build(
                args.manifest,
                values,
                terminology,
                args.reviewer,
                skip_unlisted=args.skip_unlisted,
            )
            _write_json(args.out, document)
            print(json.dumps(document["counts"], indent=2))
        else:
            document = report(
                args.manifest, values, terminology, args.region, args.limit
            )
            if args.out is not None:
                _write_json(args.out, document)
            print(
                json.dumps(
                    {
                        "counts": document["counts"],
                        "remaining_by_region": document["remaining_by_region"],
                    },
                    indent=2,
                )
            )
    except CatalogBuildError as exc:
        print(f"REJECT {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
