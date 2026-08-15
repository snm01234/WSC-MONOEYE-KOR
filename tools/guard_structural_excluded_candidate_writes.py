#!/usr/bin/env python3
"""Reject candidate writes into records marked structural_excluded_non_dialogue.

This guard exists because generic dialogue/duplicate scans can decode arbitrary
fixed/binary data as plausible Japanese text.  The fixed-data review manifest is
higher-authority than a CSV `dialogue` label: records with
`review_status=structural_excluded_non_dialogue` and `application_allowed=false`
must not be changed by a generic text candidate.

Dedicated fixed-data/graphics builders may pass explicit --allow-address values
after they have their own format/runtime proof.  The allowlist is intentionally
per logical record address rather than a broad bank/range exemption.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "out/script/fixed_data_decoder_review_manifest.json"


def load_records(path: Path) -> list[dict]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    for key in ("records", "rows", "items"):
        value = obj.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    raise SystemExit(f"unsupported manifest schema: {path}")


def parse_addr(text: str) -> int:
    text = text.strip().replace(":", "")
    return int(text, 16)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent", required=True, type=Path)
    ap.add_argument("--candidate", required=True, type=Path)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--allow-address", action="append", default=[], help="logical record start, e.g. 67AF01")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    parent = args.parent.read_bytes()
    candidate = args.candidate.read_bytes()
    if len(parent) != len(candidate):
        raise SystemExit("parent/candidate size mismatch")
    # Current project ROMs are 16 MiB with the stock 8 MiB mapped at +0x800000.
    stock_base = max(0, len(candidate) - 0x800000)
    allow = {parse_addr(x) for x in args.allow_address}

    violations = []
    checked = 0
    for row in load_records(args.manifest):
        if row.get("review_status") != "structural_excluded_non_dialogue":
            continue
        if row.get("application_allowed") is not False:
            continue
        raw_abs = row.get("abs")
        body_len = row.get("body_len")
        if not raw_abs or body_len is None:
            continue
        logical = parse_addr(str(raw_abs))
        size = int(body_len)
        start = stock_base + logical
        end = start + size
        if start < 0 or end > len(candidate):
            continue
        checked += 1
        if parent[start:end] == candidate[start:end]:
            continue
        changed_rel = [i for i, (a, b) in enumerate(zip(parent[start:end], candidate[start:end])) if a != b]
        item = {
            "abs": f"{logical:06X}",
            "body_len": size,
            "route": row.get("route"),
            "jp": row.get("jp"),
            "changed_relative_offsets": changed_rel,
            "parent_hex": parent[start:end].hex().upper(),
            "candidate_hex": candidate[start:end].hex().upper(),
            "allowed": logical in allow,
        }
        if logical not in allow:
            violations.append(item)

    result = {
        "ok": not violations,
        "checked_structural_excluded_records": checked,
        "explicit_allow_addresses": [f"{x:06X}" for x in sorted(allow)],
        "violation_count": len(violations),
        "violations": violations,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
