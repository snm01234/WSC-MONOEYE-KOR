#!/usr/bin/env python3
"""Central guard for translation provenance and legacy MT quarantine."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data/translation_source_policy.json"


class TranslationSourcePolicyError(RuntimeError):
    pass


def load_policy() -> dict[str, Any]:
    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("status") != "active":
        raise TranslationSourcePolicyError("translation source policy is missing or inactive")
    return payload


LEGACY_ROOT = ROOT / "legacy"


def rel(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(resolved).replace("\\", "/")


def forensic_asset(relative: str) -> Path:
    """Locate a blocked sheet in the active tree or the legacy archive."""
    relative = relative.replace("\\", "/").lstrip("/")
    if relative.startswith("legacy/"):
        relative = relative[len("legacy/") :]
    for candidate in (ROOT / relative, LEGACY_ROOT / relative):
        if candidate.is_file():
            return candidate
    return ROOT / relative


def _canonical_blocked_name(relative: str) -> str:
    value = relative.replace("\\", "/")
    if value.startswith("legacy/"):
        return value[len("legacy/") :]
    return value


def _is_blocked_name(relative: str, policy: dict[str, Any]) -> bool:
    relative = _canonical_blocked_name(relative)
    exact = {str(value) for value in policy.get("blocked_exact_paths") or []}
    prefixes = tuple(str(value) for value in policy.get("blocked_path_prefixes") or [])
    return relative in exact or relative.startswith(prefixes)


def _validate_reviewed_csv(path: Path, policy: dict[str, Any]) -> dict[str, int]:
    spec = policy.get("future_canonical_sheet") or {}
    required = [str(value) for value in spec.get("required_columns") or []]
    allowed_sources = {str(value) for value in spec.get("allowed_translation_source_values") or []}
    allowed_reviews = {str(value) for value in spec.get("allowed_review_status_values") or []}
    minimum_review_count = int(spec.get("minimum_review_count") or 0)
    checked = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        missing = [column for column in required if column not in headers]
        if missing:
            raise TranslationSourcePolicyError(
                f"reviewed translation sheet lacks provenance columns {missing}: {rel(path)}"
            )
        for line_no, row in enumerate(reader, start=2):
            if not (row.get("ko") or "").strip():
                continue
            source = (row.get("translation_source") or "").strip()
            review = (row.get("review_status") or "").strip()
            try:
                review_count = int((row.get("review_count") or "0").strip())
            except ValueError:
                review_count = -1
            if (
                source not in allowed_sources
                or review not in allowed_reviews
                or review_count < minimum_review_count
            ):
                raise TranslationSourcePolicyError(
                    f"unapproved translation provenance at {rel(path)}:{line_no}: "
                    f"translation_source={source!r}, review_status={review!r}, "
                    f"review_count={review_count!r}"
                )
            checked += 1
    return {"approved_nonempty_rows": checked}


def _json_source_chain_is_blocked(path: Path, policy: dict[str, Any]) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        return None
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    source_rel = rel(source_path)
    return source_rel if _is_blocked_name(source_rel, policy) else None


def assert_translation_source_allowed(path: Path, *, role: str) -> dict[str, Any]:
    """Reject known legacy MT assets and validate the future reviewed sheet."""
    policy = load_policy()
    relative = rel(path)
    canonical = str((policy.get("future_canonical_sheet") or {}).get("path") or "")

    if _is_blocked_name(relative, policy):
        raise TranslationSourcePolicyError(
            f"blocked legacy/unprovenanced translation source for {role}: {relative}. "
            f"Use {canonical} with approved provenance, or a curated data/*_ko.json specification."
        )

    if relative == canonical:
        if not path.is_file():
            raise TranslationSourcePolicyError(f"reviewed translation sheet is missing: {relative}")
        return {
            "path": relative,
            "class": "reviewed_sheet",
            **_validate_reviewed_csv(path, policy),
        }

    if relative.startswith("data/") and (
        relative.endswith("_ko.json")
        or Path(relative).name.startswith("ko_") and relative.endswith("_overrides.json")
        or Path(relative).name.startswith("translations_seed") and relative.endswith(".json")
    ):
        return {"path": relative, "class": "curated_project_data"}

    if relative.startswith("out/script/translation_sheet"):
        raise TranslationSourcePolicyError(
            f"unapproved translation sheet for {role}: {relative}. "
            f"Only {canonical} may be used after provenance validation."
        )

    approved_json_prefix = str(
        (policy.get("future_canonical_sheet") or {}).get("approved_generated_json_prefix") or ""
    )
    if relative.startswith("out/script/translations"):
        if not approved_json_prefix or not relative.startswith(approved_json_prefix):
            raise TranslationSourcePolicyError(
                f"unapproved generated translation asset for {role}: {relative}. "
                f"Use the reviewed lineage {approved_json_prefix or '<not configured>'}."
            )
        if path.suffix.lower() != ".json" or not path.is_file():
            raise TranslationSourcePolicyError(
                f"reviewed generated translation JSON is missing or invalid: {relative}"
            )
        blocked_source = _json_source_chain_is_blocked(path, policy)
        if blocked_source:
            raise TranslationSourcePolicyError(
                f"generated translation asset {relative} derives from blocked source {blocked_source}"
            )
        return {"path": relative, "class": "reviewed_generated_json"}

    if path.suffix.lower() == ".json":
        blocked_source = _json_source_chain_is_blocked(path, policy)
        if blocked_source:
            raise TranslationSourcePolicyError(
                f"generated translation asset {relative} derives from blocked source {blocked_source}"
            )

    return {"path": relative, "class": "non_sheet_input"}


def reject_legacy_generator(generator_path: Path) -> None:
    policy = load_policy()
    relative = rel(generator_path)
    blocked = {
        str(item.get("path")): str(item.get("reason"))
        for item in policy.get("blocked_generators") or []
        if isinstance(item, dict)
    }
    reason = blocked.get(relative)
    if reason:
        raise TranslationSourcePolicyError(
            f"legacy machine-translation workflow is quarantined: {relative} ({reason})"
        )
