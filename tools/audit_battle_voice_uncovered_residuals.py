#!/usr/bin/env python3
"""Audit untranslated battle/ship voice records omitted between banks5D/5E text blocks."""
from __future__ import annotations

import collections
import argparse
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
from extract_script import looks_like_jp
from find_aux_text_tables import coherent
from measure_aux_prefix_rule import code_units
from mixed_residual_classification import core_character_count, hangul_character_count, japanese_character_count
from monoeye_rom import Dictionary, Tbl, find_rom, load_rom, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
BLOCKS = ROOT / "out/script/aux_text_blocks.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/current_tip_battle_voice_uncovered_residual_audit.json"
REPEAT_RE = re.compile(r"(.)\1{5,}")
PLACEHOLDER_TERMS = ("セリフ", "Ａセリフ", "Ｂセリフ", "Ｃセリフ")


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contextual_text(text: str) -> bool:
    body = text.rstrip("\u3000 ")
    if len(body) < 3 or "<" in body or "\u3000" in body or REPEAT_RE.search(body):
        return False
    return japanese_character_count(body) >= 2 and core_character_count(body) >= 3 and looks_like_jp(body)


def inline_control_text(text: str) -> bool:
    """Recognize visible dialogue containing the E62F inline layout token.

    E62F is rendered between clauses/lines in battle dialogue.  The previous
    audit rejected every string containing ``<...>`` before language scoring,
    which quarantined real screen text such as
    ``よぉし、今だ！<E62F>撃てぇぇっ！！``.  Keep the tag in the source
    record, but score the visible clauses after replacing it with a space.
    """
    if "<E62F>" not in text:
        return False
    if re.search(r"<(?!E62F>)[A-F0-9]+>", text):
        return False
    visible = text.replace("<E62F>", " ").rstrip("\u3000 ")
    if len(visible) < 3 or REPEAT_RE.search(visible):
        return False
    clauses = [part.strip("\u3000 ") for part in text.split("<E62F>")]
    if len(clauses) < 2 or any(not part for part in clauses):
        return False
    return japanese_character_count(visible) >= 2 and core_character_count(visible) >= 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=TIP)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    target_path = args.target.resolve()
    out_path = args.out.resolve()

    tip = bytes(load_rom(target_path))
    original_path = find_rom(ROOT)
    original = bytes(load_rom(original_path))
    tbl = Tbl.load(TBL_PATH)
    od = Dictionary(original)
    cd = make_dictionary_ext3(tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(tip)
    spec = json.loads(BLOCKS.read_text(encoding="utf-8"))

    confirmed: list[dict[str, Any]] = []
    contextual: list[dict[str, Any]] = []
    inline_control: list[dict[str, Any]] = []
    placeholders: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    gap_summaries: list[dict[str, Any]] = []

    for bank in (0x5D, 0x5E):
        blocks = sorted(
            [dict(row) for row in spec.get("blocks") or [] if row.get("bank") == f"{bank:02X}"],
            key=lambda row: int(str(row["start"]), 16),
        )
        for left, right in zip(blocks, blocks[1:]):
            lo = int(str(left["end_exclusive"]), 16)
            hi = int(str(right["start"]), 16)
            if hi <= lo:
                continue
            decoded: list[dict[str, Any]] = []
            for logical, op, _kind in _walk_zstring_range(original, lo, hi, region="battle_gap", max_len=128):
                units = code_units(op)
                if not units:
                    continue
                prefix_len = units[0][1]
                if len(op) - prefix_len < 2:
                    continue
                got = read_encoded_z_safe(tip, sb + logical, max_len=128)
                if not got:
                    continue
                cp = bytes(got[0])
                current_body = cp[prefix_len:] if cp[:prefix_len] == op[:prefix_len] else cp
                try:
                    ot = od.expand(op[prefix_len:], tbl).rstrip("\u3000 \t")
                    ct = cd.expand(current_body, tbl).rstrip("\u3000 \t")
                except Exception:
                    continue
                if japanese_character_count(ct) <= 0:
                    continue
                row = {
                    "abs": f"{logical:06X}",
                    "bank": f"{bank:02X}",
                    "gap": f"{lo:06X}-{hi:06X}",
                    "prefix_hex": op[:prefix_len].hex().upper(),
                    "body_capacity": len(op) - prefix_len,
                    "original_body": ot,
                    "current_body": ct,
                    "shape": "mixed" if hangul_character_count(ct) else "jp_only",
                    "strict_coherent": coherent(ot),
                    "contextual_text": contextual_text(ot),
                    "placeholder": any(term in ot for term in PLACEHOLDER_TERMS),
                }
                decoded.append(row)
            strict_count = sum(row["strict_coherent"] for row in decoded)
            gap_is_dialogue = strict_count >= 2
            counts = collections.Counter()
            for row in decoded:
                if row["placeholder"]:
                    row["classification"] = "placeholder_or_template"
                    placeholders.append(row)
                elif inline_control_text(str(row["original_body"])):
                    row["classification"] = "inline_control_sentence"
                    row["inline_control_tag"] = "E62F"
                    row["inline_control_count"] = str(row["original_body"]).count("<E62F>")
                    inline_control.append(row)
                elif row["strict_coherent"]:
                    row["classification"] = "confirmed_sentence"
                    confirmed.append(row)
                elif gap_is_dialogue and row["contextual_text"]:
                    row["classification"] = "contextual_sentence"
                    contextual.append(row)
                else:
                    row["classification"] = "ambiguous"
                    ambiguous.append(row)
                counts[row["classification"]] += 1
            if decoded:
                gap_summaries.append({"bank": f"{bank:02X}", "start": f"{lo:06X}", "end_exclusive": f"{hi:06X}", "counts": dict(counts)})

    actionable = confirmed + contextual + inline_control
    counts = {
        "confirmed_sentence": len(confirmed),
        "contextual_sentence": len(contextual),
        "inline_control_sentence": len(inline_control),
        "actionable_sentence_total": len(actionable),
        "actionable_jp_only": sum(row["shape"] == "jp_only" for row in actionable),
        "actionable_mixed": sum(row["shape"] == "mixed" for row in actionable),
        "placeholder_or_template": len(placeholders),
        "ambiguous_review_only": len(ambiguous),
        "by_bank": dict(collections.Counter(row["bank"] for row in actionable)),
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_voice_uncovered_residuals.py",
        "read_only": True,
        "ok": True,
        "tip": {"path": str(target_path.relative_to(ROOT)), "size": len(tip), "sha256": sha(tip)},
        "scope": {
            "banks": ["5D", "5E"],
            "method": "all gaps between existing vetted text blocks; first Original code unit treated as voice/speaker identifier for sentence classification only",
            "warning": "automatic ROM edits still require duplicate or runtime evidence for the leading code unit",
        },
        "counts": counts,
        "actionable": sorted(actionable, key=lambda row: int(row["abs"], 16)),
        "inline_control_actionable": sorted(inline_control, key=lambda row: int(row["abs"], 16)),
        "placeholder_or_template": sorted(placeholders, key=lambda row: int(row["abs"], 16)),
        "ambiguous_review_only": sorted(ambiguous, key=lambda row: int(row["abs"], 16)),
        "gaps": gap_summaries,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "counts": counts, "out": str(out_path.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
