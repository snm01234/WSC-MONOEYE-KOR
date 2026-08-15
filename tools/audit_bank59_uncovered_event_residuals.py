#!/usr/bin/env python3
"""Audit untranslated bank59 event dialogue omitted between vetted text blocks.

The original detector required long coherent runs and therefore omitted short
scene fragments and entire dialogue runs separated by control records. This
read-only audit walks every gap between the first and last proven bank59 text
block, strips Original-derived dialogue prefixes, and separates:

* confirmed_sentence: the original body passes the project's high-precision
  prose coherence test;
* contextual_sentence: a Japanese/mixed dialogue body that does not pass the
  strict test (often katakana-heavy or very short) but sits in a gap containing
  at least two confirmed prose records and passes conservative text checks;
* ambiguous: residual records not meeting either bar, retained for review only.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import _walk_zstring_range
from extract_script import looks_like_jp, split_prefix_body
from find_aux_text_tables import coherent
from mixed_residual_classification import (
    core_character_count,
    hangul_character_count,
    japanese_character_count,
)
from monoeye_rom import Dictionary, Tbl, find_rom, load_rom, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
BLOCKS = ROOT / "out/script/aux_text_blocks.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/current_tip_bank59_uncovered_event_residual_audit.json"
TEXT_INITIAL_CLEANUPS = {0x590A2B}
# The uploaded runtime screenshot identifies this short event gap. Two valid
# utterances inside it are too short/katakana-heavy for the generic prose test,
# so the exact bounded gap is accepted as dialogue context rather than discarded.
EXPLICIT_DIALOGUE_GAPS = {
    (0x593E8A, 0x593F28): "user runtime screenshot plus complete bounded event-scene walk",
}
REPEAT_RE = re.compile(r"(.)\1{5,}")


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be object: {path}")
    return value


def contextual_text(text: str) -> bool:
    body = text.rstrip("\u3000 ")
    if len(body) < 3 or "<" in body or REPEAT_RE.search(body):
        return False
    if "\u3000" in body:
        return False
    if japanese_character_count(body) < 2 or core_character_count(body) < 3:
        return False
    return looks_like_jp(body)


def main() -> int:
    tip = bytes(load_rom(TIP))
    original_path = find_rom(ROOT)
    original = bytes(load_rom(original_path))
    tbl = Tbl.load(TBL_PATH)
    original_dictionary = Dictionary(original)
    current_dictionary = make_dictionary_ext3(
        tip,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    sb = stock_base(tip)

    spec = load_object(BLOCKS)
    bank_blocks = sorted(
        [dict(row) for row in spec.get("blocks") or [] if row.get("bank") == "59"],
        key=lambda row: int(str(row["start"]), 16),
    )
    if len(bank_blocks) < 2:
        raise AuditError("bank59 block population missing")

    gaps: list[dict[str, Any]] = []
    confirmed_rows: list[dict[str, Any]] = []
    contextual_rows: list[dict[str, Any]] = []
    ambiguous_rows: list[dict[str, Any]] = []

    for left, right in zip(bank_blocks, bank_blocks[1:]):
        lo = int(str(left["end_exclusive"]), 16)
        hi = int(str(right["start"]), 16)
        if hi <= lo:
            continue
        decoded: list[dict[str, Any]] = []
        for logical, original_payload, _kind in _walk_zstring_range(
            original, lo, hi, region="bank59_gap", max_len=128
        ):
            prefix, original_body, kind = split_prefix_body(original_payload)
            if kind != "dialogue" or not original_body:
                continue
            got = read_encoded_z_safe(tip, sb + logical, max_len=128)
            if not got:
                continue
            current_payload = bytes(got[0])
            if logical in TEXT_INITIAL_CLEANUPS:
                current_body = current_payload
            elif prefix and current_payload.startswith(prefix):
                current_body = current_payload[len(prefix):]
            elif not prefix:
                current_body = current_payload
            else:
                current_body = current_payload
            try:
                original_text = original_dictionary.expand(original_body, tbl).rstrip("\u3000 \t")
                current_text = current_dictionary.expand(current_body, tbl).rstrip("\u3000 \t")
            except Exception:
                continue
            jp = japanese_character_count(current_text)
            if jp <= 0:
                continue
            row = {
                "abs": f"{logical:06X}",
                "gap": f"{lo:06X}-{hi:06X}",
                "prefix_hex": prefix.hex().upper(),
                "payload_capacity": len(original_payload),
                "body_capacity": len(original_body),
                "original": original_text,
                "current": current_text,
                "shape": "mixed" if hangul_character_count(current_text) else "jp_only",
                "japanese_count": jp,
                "hangul_count": hangul_character_count(current_text),
                "strict_coherent": coherent(original_text),
                "contextual_text": contextual_text(original_text),
            }
            decoded.append(row)

        strict_count = sum(row["strict_coherent"] for row in decoded)
        explicit_gap_reason = EXPLICIT_DIALOGUE_GAPS.get((lo, hi))
        gap_is_dialogue = strict_count >= 2 or explicit_gap_reason is not None
        gap_confirmed: list[dict[str, Any]] = []
        gap_contextual: list[dict[str, Any]] = []
        gap_ambiguous: list[dict[str, Any]] = []
        for row in decoded:
            if row["strict_coherent"]:
                row["classification"] = "confirmed_sentence"
                gap_confirmed.append(row)
                confirmed_rows.append(row)
            elif gap_is_dialogue and (row["contextual_text"] or explicit_gap_reason is not None):
                row["classification"] = "contextual_sentence"
                if explicit_gap_reason is not None and not row["contextual_text"]:
                    row["explicit_dialogue_gap_reason"] = explicit_gap_reason
                gap_contextual.append(row)
                contextual_rows.append(row)
            else:
                row["classification"] = "ambiguous"
                gap_ambiguous.append(row)
                ambiguous_rows.append(row)
        if decoded:
            gaps.append(
                {
                    "start": f"{lo:06X}",
                    "end_exclusive": f"{hi:06X}",
                    "bytes": hi - lo,
                    "dialogue_confirmed": gap_is_dialogue,
                    "explicit_dialogue_gap_reason": explicit_gap_reason,
                    "counts": {
                        "confirmed_sentence": len(gap_confirmed),
                        "contextual_sentence": len(gap_contextual),
                        "ambiguous": len(gap_ambiguous),
                    },
                    "confirmed": gap_confirmed,
                    "contextual": gap_contextual,
                    "ambiguous": gap_ambiguous,
                }
            )

    actionable = confirmed_rows + contextual_rows
    counts = {
        "confirmed_sentence": len(confirmed_rows),
        "contextual_sentence": len(contextual_rows),
        "actionable_sentence_total": len(actionable),
        "ambiguous_review_only": len(ambiguous_rows),
        "actionable_jp_only": sum(row["shape"] == "jp_only" for row in actionable),
        "actionable_mixed": sum(row["shape"] == "mixed" for row in actionable),
        "gaps_with_actionable_sentences": sum(
            (gap["counts"]["confirmed_sentence"] + gap["counts"]["contextual_sentence"]) > 0
            for gap in gaps
        ),
    }
    by_gap = collections.Counter(row["gap"] for row in actionable)
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_bank59_uncovered_event_residuals.py",
        "read_only": True,
        "ok": True,
        "tip": {"path": str(TIP.relative_to(ROOT)), "size": len(tip), "sha256": sha(tip)},
        "original": {"path": str(original_path), "size": len(original), "sha256": sha(original)},
        "scope": {
            "bank": "59",
            "from": bank_blocks[0]["start"],
            "to_exclusive": bank_blocks[-1]["end_exclusive"],
            "method": "all gaps between the 94 existing vetted bank59 blocks",
            "count_semantics": "static record count, not unique spoken phrase count",
        },
        "counts": counts,
        "actionable_by_gap": dict(sorted(by_gap.items())),
        "actionable": sorted(actionable, key=lambda row: int(row["abs"], 16)),
        "ambiguous_review_only": sorted(ambiguous_rows, key=lambda row: int(row["abs"], 16)),
        "gaps": gaps,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "counts": counts, "top_gaps": by_gap.most_common(20), "out": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
