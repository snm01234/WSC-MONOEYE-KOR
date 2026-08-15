#!/usr/bin/env python3
"""Audit live shared dictionary phrases that still render Japanese characters.

The current Korean TIP reuses the stock dictionary heavily.  Earlier UI passes
intentionally skipped slots that had already been partially localized (for
example ``전투不能``), leaving kanji in every record that consumes the shared
slot.  This read-only audit joins:

* Original and current dictionary text;
* the Original+Working external/nested consumer union;
* reviewed UI/name/term catalogs and prior candidate reports.

Only live slots with a unique reviewed Korean target become tier A.  No ROM or
SaveRAM is written.
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from mixed_residual_classification import is_hangul_character, is_japanese_character
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import Dictionary, Tbl, load_rom
from normalize_ko_text import normalize_ko_text, try_encode_ko_text
from hangul_marker import marker_code

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/shared_dictionary_japanese_residual_audit.json"


class AuditError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = data if data is not None else path.read_bytes()
    return {"path": str(path.resolve()), "size": len(payload), "sha256": sha256_bytes(payload)}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_objects(value: Any, parent_key: str | None = None) -> Iterable[tuple[str | None, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        yield parent_key, value
        for key, child in value.items():
            if isinstance(child, Mapping):
                yield from iter_objects(child, str(key))
            elif isinstance(child, list):
                for item in child:
                    yield from iter_objects(item, str(key))
    elif isinstance(value, list):
        for item in value:
            yield from iter_objects(item, parent_key)


def reviewed_ko(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = normalize_ko_text(value)
    if "�" in text or any(0xE000 <= ord(ch) <= 0xF8FF for ch in text):
        return None
    if any(is_japanese_character(ch) for ch in text):
        return None
    if not any(is_hangul_character(ch) for ch in text):
        return None
    return text


def add(mapping: dict[Any, set[tuple[str, str]]], key: Any, ko: Any, source: str) -> None:
    text = reviewed_ko(ko)
    if key is not None and text:
        mapping.setdefault(key, set()).add((text, source))


def load_catalogs() -> tuple[dict[str, set[tuple[str, str]]], dict[int, set[tuple[str, str]]]]:
    by_jp: dict[str, set[tuple[str, str]]] = {}
    by_index: dict[int, set[tuple[str, str]]] = {}

    for name in sorted(glob.glob(str(ROOT / "data/**/*.json"), recursive=True)):
        path = Path(name)
        source = str(path.relative_to(ROOT)).replace("\\", "/")
        if source in {"data/ko_ui_overrides.json", "data/_quarantine_fragments.json"}:
            continue
        try:
            document = load_json(path)
        except Exception:
            continue
        for _parent, row in iter_objects(document):
            jp = row.get("jp")
            ko = row.get("ko")
            if isinstance(jp, str) and jp:
                add(by_jp, jp.rstrip("\u3000 \t"), ko, source)
            index_value = row.get("index")
            if isinstance(index_value, str):
                try:
                    index = int(index_value, 16)
                except ValueError:
                    index = None
                add(by_index, index, ko, source)
            elif isinstance(index_value, int) and not isinstance(index_value, bool):
                add(by_index, index_value, ko, source)

    # Prior reports are authoritative for slots that were deliberately skipped
    # because the TIP already held a partial translation.
    for name in sorted(glob.glob(str(ROOT / "out/patch/**/*report.json"), recursive=True)):
        path = Path(name)
        try:
            document = load_json(path)
        except Exception:
            continue
        source = str(path.relative_to(ROOT)).replace("\\", "/")
        for key in ("skipped_tip_reused", "applied"):
            rows = document.get(key) if isinstance(document, Mapping) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                value = row.get("index")
                try:
                    index = int(str(value), 16)
                except (TypeError, ValueError):
                    continue
                add(by_index, index, row.get("ko"), source)
                jp = row.get("jp")
                if isinstance(jp, str) and jp:
                    add(by_jp, jp.rstrip("\u3000 \t"), row.get("ko"), source)

    return by_jp, by_index


def choose(candidates: set[tuple[str, str]]) -> dict[str, Any]:
    values = sorted({ko for ko, _source in candidates})
    return {
        "ready": len(values) == 1,
        "ambiguous": len(values) > 1,
        "ko": values[0] if len(values) == 1 else "",
        "values": values,
        "evidence": [{"ko": ko, "source": source} for ko, source in sorted(candidates)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=TIP)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise AuditError("refusing to write a ROM")

    current = bytes(load_rom(args.tip))
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    current_dictionary = make_dictionary_ext3(current, ext_meta, ext3_meta)
    original_dictionary = Dictionary(original)
    union = build_reference_union(
        original,
        current,
        ext_meta=ext_meta,
        ext3_meta=ext3_meta,
    )
    by_jp, by_index = load_catalogs()

    rows: list[dict[str, Any]] = []
    stock_count = min(original_dictionary.stock_count, current_dictionary.count)
    for index in range(stock_count):
        try:
            original_text = original_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
            current_text = current_dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
            current_payload = bytes(current_dictionary.raw_entry(index))
        except Exception:
            continue
        if not current_text or not any(is_japanese_character(ch) for ch in current_text):
            continue
        consumers = union.consumers_for(index)
        current_consumers = [item for item in consumers if "working" in item.seen_in]
        original_consumers = [item for item in consumers if "original" in item.seen_in]
        parents = sorted(union.parents_of(index))
        if not current_consumers and not parents:
            continue

        candidates: set[tuple[str, str]] = set(by_index.get(index, set()))
        candidates.update(by_jp.get(original_text, set()))
        candidates.update(by_jp.get(current_text, set()))
        translation = choose(candidates)
        regions = collections.Counter(item.region for item in current_consumers)
        row = {
            "index": f"{index:04X}",
            "index_int": index,
            "original_text": original_text,
            "current_text": current_text,
            "current_payload_hex": current_payload.hex().upper(),
            "current_payload_bytes": len(current_payload),
            "translation": translation,
            "current_external_consumers": len(current_consumers),
            "original_external_consumers": len(original_consumers),
            "current_regions": dict(sorted(regions.items())),
            "nested_parent_count": len(parents),
            "nested_parent_sample": [f"{value:04X}" for value in parents[:20]],
            "consumer_sample": [
                {
                    "abs": f"{item.abs:06X}",
                    "region": item.region,
                    "kind": item.kind,
                    "seen_in": sorted(item.seen_in),
                }
                for item in current_consumers[:20]
            ],
        }
        if translation["ambiguous"]:
            row["tier"] = "B"
            row["tier_reason"] = "translation_catalog_conflict"
        elif translation["ready"]:
            row["tier"] = "A"
            row["tier_reason"] = "live_shared_phrase_reviewed_translation_ready"
        else:
            row["tier"] = "B"
            row["tier_reason"] = "reviewed_translation_missing"
        rows.append(row)

    rows.sort(key=lambda row: int(row["index_int"]))
    tier_a = [row for row in rows if row["tier"] == "A"]
    tier_b = [row for row in rows if row["tier"] == "B"]

    encoded: dict[str, bytes] = {}
    failures: list[str] = []
    for row in tier_a:
        text = str(row["translation"]["ko"])
        if text in encoded:
            continue
        payload = try_encode_ko_text(
            text,
            tbl,
            hangul_marker_code=marker_code(),
            hangul_marker_mode="run",
        )
        if payload is None or b"\x00" in payload:
            failures.append(text)
        else:
            encoded[text] = bytes(payload)

    unique_phrases = {str(row["translation"]["ko"]) for row in tier_a}
    required_bytes = sum(len(encoded[text]) + 1 for text in unique_phrases if text in encoded)
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_shared_dictionary_japanese_residuals.py",
        "read_only": True,
        "ok": not failures,
        "inputs": {
            "tip": identity(args.tip, current),
            "original": identity(ORIGINAL, original),
            "tbl": identity(TBL_PATH),
        },
        "counts": {
            "live_stock_slots_with_japanese": len(rows),
            "tier_a_translation_ready": len(tier_a),
            "tier_b_translation_missing_or_conflicted": len(tier_b),
            "tier_a_unique_phrases": len(unique_phrases),
            "tier_a_phrase_bytes_including_nul": required_bytes,
            "tier_a_external_consumers": sum(int(row["current_external_consumers"]) for row in tier_a),
            "tier_a_nested_parents": sum(int(row["nested_parent_count"]) for row in tier_a),
        },
        "patch_plan": {
            "strategy": "retarget selected stock dictionary pointers to new Korean payloads in verified bank-5F tail",
            "selected_slots": len(tier_a),
            "unique_phrases": len(unique_phrases),
            "phrase_bytes_including_nul": required_bytes,
            "encoding_failures": sorted(set(failures)),
            "guard": "Original+Working external and nested consumer union; all selected consumers change together",
        },
        "records": {"tier_a": tier_a, "tier_b": tier_b},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "counts": report["counts"], "patch_plan": report["patch_plan"], "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
