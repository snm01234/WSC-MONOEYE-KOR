#!/usr/bin/env python3
"""Independent postcheck for the current-main LLM-reviewed uncovered candidate."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/uncovered_llm_reviewed_candidate.wsc"
SHEET = ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv"
BUILD = ROOT / "out/patch/uncovered_llm_reviewed_candidate_report.json"
FALSE_SEGPTR = ROOT / "out/patch/uncovered_llm_reviewed_false_segptr.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/uncovered_llm_reviewed_postcheck.json"
EXPECTED_MAIN = "46d6d6a984ec7696428ade90f5ea1e191f218e568242e2439f7347a6004b9729"
EXPECTED_CANDIDATE = "b1071bc25d91346734a30950678dc4e6d9c7c721df5d6b93b9f9555b1293a23a"
BAD_TERMS = (
    "블레이드", "브라드", "블라드", "중좌", "소좌", "대좌", "카게로",
    "오노레", "아타시", "밀리알드", "노이에・지일", "마이차", "월광의　나비",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    main_rom = bytes(load_rom(MAIN))
    candidate = bytes(load_rom(CANDIDATE))
    if sha256(main_rom) != EXPECTED_MAIN:
        raise SystemExit("main identity drifted")
    if sha256(candidate) != EXPECTED_CANDIDATE:
        raise SystemExit("candidate identity drifted")

    build = json.loads(BUILD.read_text(encoding="utf-8"))
    false = json.loads(FALSE_SEGPTR.read_text(encoding="utf-8"))
    if build.get("ok") is not True:
        raise SystemExit("build report not clean")
    if false.get("ok") is not True or int(false.get("sites_found", -1)) != 0:
        raise SystemExit("false segmented-pointer report not clean")

    with SHEET.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = [dict(row) for row in csv.DictReader(fh)]
    if len(rows) != 1893 or any(row.get("review_status") != "approved" or row.get("translation_source") != "llm" for row in rows):
        raise SystemExit("reviewed sheet provenance drifted")

    tbl = Tbl.load(TBL)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    d_main = make_dictionary_ext3(main_rom, ext_meta, ext3_meta)
    d_candidate = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(candidate)

    portal_exact = 0
    nonportal_unchanged = 0
    mismatches: list[dict[str, str]] = []
    stale_sheet: list[dict[str, str]] = []
    for row in rows:
        if any(token in str(row.get("ko") or "") for token in BAD_TERMS):
            stale_sheet.append({"abs": row["abs"], "ko": row["ko"]})
        # Only rows reviewed in this pass are part of the 1,858 parity gate.
        if "2026-08-08 LLM line-by-line literal review" not in str(row.get("notes") or ""):
            continue
        logical = int(row["abs"], 16)
        main_got = read_encoded_z_safe(main_rom, sb + logical, max_len=256)
        cand_got = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if main_got is None or cand_got is None:
            mismatches.append({"abs": row["abs"], "reason": "unreadable"})
            continue
        main_payload, main_term = bytes(main_got[0]), int(main_got[1])
        cand_payload, cand_term = bytes(cand_got[0]), int(cand_got[1])
        positions = [pos for pos in range(max(0, len(cand_payload) - 3)) if cand_payload[pos:pos + 2] == b"\xE5\x18"]
        if len(positions) != 1:
            if main_payload == cand_payload and main_term == cand_term:
                nonportal_unchanged += 1
            else:
                mismatches.append({"abs": row["abs"], "reason": "nonportal_changed"})
            continue
        pos = positions[0]
        token = cand_payload[pos:pos + 4]
        index = 0x1000 + (token[2] << 8) + token[3]
        rendered = d_candidate.expand_index(index, tbl).rstrip("\u3000 \t")
        if rendered != row["ko"]:
            mismatches.append({"abs": row["abs"], "rendered": rendered, "expected": row["ko"]})
        else:
            portal_exact += 1

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_uncovered_llm_reviewed_candidate.py",
        "ok": not mismatches and not stale_sheet,
        "inputs": {
            "main_sha256": sha256(main_rom),
            "candidate_sha256": sha256(candidate),
            "sheet_sha256": sha256(SHEET.read_bytes()),
            "build_report_sha256": sha256(BUILD.read_bytes()),
            "false_segptr_sha256": sha256(FALSE_SEGPTR.read_bytes()),
        },
        "counts": {
            "sheet_rows": len(rows),
            "reviewed_rows_this_pass": 1858,
            "reviewed_portal_exact": portal_exact,
            "reviewed_nonportal_unchanged": nonportal_unchanged,
            "mismatches": len(mismatches),
            "stale_bad_terms": len(stale_sheet),
            "false_segmented_pointer_sites": int(false.get("sites_found", -1)),
        },
        "mismatches": mismatches,
        "stale_bad_terms": stale_sheet,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
