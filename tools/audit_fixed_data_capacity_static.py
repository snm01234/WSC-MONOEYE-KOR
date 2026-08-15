#!/usr/bin/env python3
"""Measure fixed-stride payload capacity without treating legacy Korean as text source.

This audit only answers whether the *existing* legacy value can be represented
inside the already-observed physical body.  It is not a translation approval:
all rows remain blocked until a dedicated fixed-stride decoder and a fresh
Japanese-to-Korean review are attached.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "out/script/fixed_data_decoder_review_manifest.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
OUT = ROOT / "out/script/fixed_data_capacity_static_audit.json"
sys.path.insert(0, str(ROOT / "tools"))
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = manifest.get("records") or []
    tbl = Tbl.load(TBL)
    by_bank: dict[str, Counter[str]] = defaultdict(Counter)
    length_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    details: list[dict[str, object]] = []
    for row in rows:
        legacy = str(row.get("legacy_ko") or "")
        body_len = int(row.get("body_len") or 0)
        encoded = try_encode_ko_text(
            normalize_ko_text(legacy), tbl,
            hangul_marker_code=marker_code(), hangul_marker_mode="run",
        ) if legacy else None
        has_jp = bool(row.get("residual_japanese_in_legacy_ko"))
        has_control = bool(row.get("control_or_nul_in_legacy_ko"))
        if has_jp or has_control or not legacy.strip():
            status = "legacy_quality_blocked"
        elif encoded is None or not encoded or b"\x00" in encoded:
            status = "legacy_not_encodable_with_current_tbl"
        elif len(encoded) <= body_len:
            status = "legacy_direct_fit_diagnostic_only"
        else:
            status = "legacy_direct_over_capacity_diagnostic_only"
        bank = str(row.get("bank") or "")
        status_counts[status] += 1
        length_counts[str(body_len)] += 1
        by_bank[bank][status] += 1
        by_bank[bank][f"body_len_{body_len}"] += 1
        details.append({
            "abs": str(row.get("abs") or "").upper(),
            "bank": bank,
            "body_len": body_len,
            "next_record_gap": row.get("next_record_gap"),
            "legacy_ko": legacy,
            "legacy_encoded_len": len(encoded) if encoded else None,
            "legacy_direct_fit": bool(encoded) and b"\x00" not in encoded and len(encoded) <= body_len,
            "diagnostic_status": status,
            "application_allowed": False,
            "translation_source_policy": "legacy_ko_is_not_an_application_source",
        })
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_fixed_data_capacity_static.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "diagnostic sizing only; fixed-stride decoder and fresh semantic review are absent",
        "inputs": {
            "manifest": str(MANIFEST.relative_to(ROOT)).replace("\\", "/"),
            "manifest_sha256": sha(MANIFEST),
            "tbl_sha256": sha(TBL),
            "main_rom_sha256": sha(ROM),
        },
        "counts": {
            "rows": len(details),
            "diagnostic_status": dict(sorted(status_counts.items())),
            "body_length_distribution": dict(sorted(length_counts.items(), key=lambda item: int(item[0]))),
            "legacy_direct_fit_diagnostic_only": sum(bool(row["legacy_direct_fit"]) for row in details),
            "application_allowed": 0,
        },
        "by_bank": {bank: dict(sorted(counter.items())) for bank, counter in sorted(by_bank.items())},
        "next_actions": [
            "Do not use diagnostic direct-fit rows as translations; attach fresh Japanese review provenance.",
            "Define a fixed-stride decoder contract for each bank family, including padding and next-record gap.",
            "Only after that contract exists may a candidate payload map be constructed.",
        ],
        "records": details,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), "counts": report["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
