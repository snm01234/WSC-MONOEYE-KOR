#!/usr/bin/env python3
"""Spill-apply KO overflow bank-by-bank with capacity + pointer guards."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL = ROOT / "out/patch/hangul_patch.tbl"
SHEET = ROOT / "out/script/translations_full.json"
OUT = ROOT / "out/patch"


def main() -> None:
    # Default to dialogue banks with cleaner sheet KO; later banks often hold
    # Bing garbage and are not worth pointer-risked spill.
    start = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x60
    end = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x63
    summary = []
    cumulative_map = {}
    for bank in range(start, end + 1):
        min_abs = bank << 16
        max_abs = (bank << 16) | 0xFFFF
        cmd = [
            sys.executable,
            str(TOOLS / "apply_translations_expanded.py"),
            "--rom",
            str(ROM),
            "--tbl",
            str(TBL),
            "--translations",
            str(SHEET),
            "--hangul-marker",
            "E3DB",
            "--overflow-mode",
            "spill",
            "--max-shared-phrases",
            "32",
            "--min-abs",
            f"{min_abs:X}",
            "--max-abs",
            f"{max_abs:X}",
            "--out",
            str(OUT),
        ]
        rc = subprocess.call(cmd, cwd=ROOT)
        rep = json.loads((OUT / "apply_expanded_report.json").read_text(encoding="utf-8"))
        modes = rep.get("mode_counts", {})
        spill_n = int(modes.get("spill_rebuild", 0))
        br = rep.get("bank_rebuild") or {}
        cumulative_map.update(br.get("mapping") or {})
        summary.append(
            {
                "bank": f"{bank:02X}",
                "spill": spill_n,
                "patched": rep["lines_patched"],
                "decode_fail": rep["decode_failures"],
                "pointer_fixes": br.get("pointer_fixes", 0),
                "relocated": br.get("relocated_records", 0),
                "skipped_no_pointer": br.get("skipped_no_pointer_count", 0),
                "skipped_ambiguous_pointer": br.get(
                    "skipped_ambiguous_pointer_count", 0
                ),
                "rc": rc,
            }
        )
        print(
            f"bank {bank:02X}: spill={spill_n} relocated={br.get('relocated_records', 0)} "
            f"ptr_fixes={br.get('pointer_fixes', 0)} "
            f"amb={br.get('skipped_ambiguous_pointer_count', 0)} "
            f"fail={rep['decode_failures']} rc={rc}"
        )
        if rc != 0:
            break

    out_path = OUT / "spill_banks_summary.json"
    out_path.write_text(
        json.dumps(
            {
                "banks": summary,
                "total_relocated": sum(int(r["relocated"] or 0) for r in summary),
                "total_pointer_fixes": sum(int(r["pointer_fixes"] or 0) for r in summary),
                "mapping": cumulative_map,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
