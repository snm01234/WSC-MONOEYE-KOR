#!/usr/bin/env python3
"""Rebind legacy reviewed staging rows to the current TIP when exact-safe.

No translation is generated and no ROM is written. A row is rebound only when
the original Japanese body and the current Korean rendering both match the
staging row after harmless whitespace/padding normalization. All other rows
are emitted as a retranslation hold.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from hangul_marker import marker_code
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_DIR = ROOT / "out/script/rebased_llm_staging"
OUT_CSV = OUT_DIR / "rebound_exact.csv"
HOLD_CSV = OUT_DIR / "rebase_hold.csv"
REPORT = OUT_DIR / "rebase_report.json"

SOURCES = [
    ROOT / "out/script/battle_voice_ambiguous_nonstub_translation_sheet.csv",
    ROOT / "out/script/battle_voice_inline_control_translation_sheet.csv",
    ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv",
    ROOT / "out/script/battle_dialogue_llm_review/results/battle_voice_ambiguous_nonstub_ready_reviewed.csv",
]

SPACE_RE = re.compile(r"[ \t\r\n\u3000]+")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cmp_text(text: str) -> str:
    return SPACE_RE.sub(" ", str(text or "").replace("<E62F>", " <E62F> ").strip(" \u3000\t\r\n"))


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    main_rom = bytes(load_rom(MAIN))
    original_rom = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL)
    current_dict = make_dictionary_ext3(main_rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    original_dict = Dictionary(original_rom)
    current_sb = stock_base(main_rom)
    original_sb = stock_base(original_rom)
    current_sha = digest(main_rom)

    union: dict[str, dict[str, str]] = {}
    source_by_abs: dict[str, str] = {}
    for path in SOURCES:
        if not path.is_file():
            continue
        for row in rows(path):
            address = str(row.get("abs") or "").upper()
            if address:
                union.setdefault(address, dict(row))
                source_by_abs.setdefault(address, str(path.relative_to(ROOT)).replace("\\", "/"))

    rebound: list[dict[str, str]] = []
    hold: list[dict[str, str]] = []
    for address, row in sorted(union.items(), key=lambda item: int(item[0], 16)):
        prefix_hex = str(row.get("prefix_hex") or "").replace(" ", "").strip()
        try:
            prefix = bytes.fromhex(prefix_hex) if prefix_hex else b""
        except ValueError:
            prefix = b""
        reasons: list[str] = []
        current_text = ""
        original_text = ""
        current_body = b""
        original_body = b""
        cur = read_encoded_z_safe(main_rom, current_sb + int(address, 16), max_len=512)
        org = read_encoded_z_safe(original_rom, original_sb + int(address, 16), max_len=512)
        if cur is None:
            reasons.append("current_record_unreadable")
        else:
            payload = bytes(cur[0])
            if prefix and not payload.startswith(prefix):
                reasons.append("current_prefix_drift")
            else:
                current_body = payload[len(prefix):]
                current_text = current_dict.expand(current_body, tbl).rstrip("\u3000 \t")
        if org is None:
            reasons.append("original_record_unreadable")
        else:
            payload = bytes(org[0])
            if prefix and not payload.startswith(prefix):
                reasons.append("original_prefix_drift")
            else:
                original_body = payload[len(prefix):]
                original_text = original_dict.expand(original_body, tbl).rstrip("\u3000 \t")
        expected_original = str(row.get("original_jp") or "").rstrip("\u3000 \t")
        expected_ko = str(row.get("ko") or row.get("proposed_ko") or "").rstrip("\u3000 \t")
        if cmp_text(original_text) != cmp_text(expected_original):
            reasons.append("original_body_text_mismatch")
        if cmp_text(current_text) != cmp_text(expected_ko):
            reasons.append("current_korean_render_mismatch")
        if not expected_ko:
            reasons.append("empty_reviewed_translation")
        if not reasons:
            item = dict(row)
            item.update({
                "source_model": "inherited approved LLM staging; static exact rebind",
                "review_count": "1",
                "reviewed_at": "2026-08-12",
                "main_tip_sha256": current_sha,
                "parent_tip_sha256": current_sha,
                "source_body_sha256": digest(current_body),
                "current_body_sha256": digest(current_body),
                "original_body_sha256": digest(original_body),
                "rebase_status": "static_exact_rebound",
                "rebase_source": source_by_abs[address],
            })
            rebound.append(item)
        else:
            hold.append({
                "abs": address,
                "source": source_by_abs[address],
                "parent_tip_sha256": str(row.get("parent_tip_sha256") or ""),
                "current_main_tip_sha256": current_sha,
                "reason": ";".join(reasons),
                "original_jp": expected_original,
                "expected_ko": expected_ko,
                "current_render": current_text,
                "original_render": original_text,
            })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rebound_fields = list(dict.fromkeys([key for row in rebound for key in row] + [
        "source_model", "review_count", "reviewed_at", "main_tip_sha256",
        "parent_tip_sha256", "current_body_sha256", "original_body_sha256",
        "rebase_status", "rebase_source",
    ]))
    if rebound:
        with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=rebound_fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rebound)
    hold_fields = ["abs", "source", "parent_tip_sha256", "current_main_tip_sha256", "reason", "original_jp", "expected_ko", "current_render", "original_render"]
    with HOLD_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=hold_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(hold)
    report = {
        "schema_version": 1,
        "artifact": "reviewed-staging-static-rebase/v1",
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "main_tip_sha256": current_sha,
        "source_rows_union": len(union),
        "static_exact_rebound_rows": len(rebound),
        "rebase_hold_rows": len(hold),
        "source_parent_tip_sha256_counts": {},
        "outputs": {
            "rebound": str(OUT_CSV.relative_to(ROOT)).replace("\\", "/"),
            "hold": str(HOLD_CSV.relative_to(ROOT)).replace("\\", "/"),
        },
        "promotion": "blocked_until_contract_encoding_and_runtime_gates",
    }
    import collections
    counts = collections.Counter(str(row.get("parent_tip_sha256") or "").lower() for row in union.values())
    report["source_parent_tip_sha256_counts"] = dict(sorted(counts.items()))
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
