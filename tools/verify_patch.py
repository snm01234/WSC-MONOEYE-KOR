#!/usr/bin/env python3
"""Verify seed Korean patch: decode patched bodies and compare to translations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z  # noqa: E402
from normalize_ko_text import try_encode_ko_text  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out" / "patch" / "monoeye_ko_seed.wsc")
    ap.add_argument("--tbl", type=Path, default=ROOT / "out" / "patch" / "hangul_patch.tbl")
    ap.add_argument("--translations", type=Path, default=ROOT / "data" / "translations_seed.json")
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "patch" / "verify_report.json")
    args = ap.parse_args()

    rom = load_rom(args.rom)
    tbl = Tbl.load(args.tbl)
    # Dictionary grew past stock 3831
    # Detect count from apply_report if present
    apply_report = ROOT / "out" / "patch" / "apply_expanded_report.json"
    if not apply_report.exists():
        apply_report = ROOT / "out" / "patch" / "apply_report.json"
    abs_remap = {}
    patched_abs = None
    if apply_report.exists():
        applied = json.loads(apply_report.read_text(encoding="utf-8"))
        count = applied.get("dict_count")
        if applied.get("patched_abs"):
            patched_abs = {a.upper() for a in applied["patched_abs"]}
        for row in applied.get("results", []) + applied.get("results_sample", []):
            if row.get("new_abs") and row.get("abs") and row["new_abs"] != row["abs"]:
                abs_remap[row["abs"].upper()] = row["new_abs"]
            if patched_abs is None and not str(row.get("mode", "")).startswith("skipped"):
                # fallback if patched_abs missing
                pass
    else:
        count = None
    d = Dictionary(rom, count=count)
    from apply_translations_expanded import load_translation_lines

    translation_lines = load_translation_lines(args.translations)

    rows = []
    fails = 0
    skipped = 0
    for line in translation_lines:
        abs_key = line["abs"].upper()
        if patched_abs is not None and abs_key not in patched_abs:
            skipped += 1
            continue
        abs_off = int(abs_remap.get(abs_key, line["abs"]), 16)
        payload, _ = read_encoded_z(rom, abs_off)
        prefix, body, kind = split_prefix_body(payload)
        decoded = d.expand(body, tbl)
        encoded = try_encode_ko_text(line["ko"].replace(" ", "　"), tbl)
        if encoded is None:
            skipped += 1
            continue
        expect = d.expand(encoded, tbl)
        ok = decoded == expect
        if not ok:
            fails += 1
        rows.append(
            {
                "abs": line["abs"],
                "jp": line["jp"],
                "expected_ko": expect,
                "decoded": decoded,
                "prefix_hex": " ".join(f"{b:02X}" for b in prefix),
                "body_hex": " ".join(f"{b:02X}" for b in body),
                "ok": ok,
            }
        )

    report = {
        "rom": str(args.rom),
        "checked": len(rows),
        "passed": len(rows) - fails,
        "failed": fails,
        "skipped_unencodable": skipped,
        "checksum_stored": f"{rom[-2] | (rom[-1] << 8):04X}",
        "checksum_calculated": f"{sum(rom[:-2]) & 0xFFFF:04X}",
        "rows": rows,
    }
    report["checksum_ok"] = (
        report["checksum_stored"] == report["checksum_calculated"]
    )
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = args.out.with_suffix(".md")
    lines = [
        "# Korean seed patch verification",
        "",
        f"- ROM: `{args.rom.name}`",
        f"- Result: **{report['passed']}/{report['checked']} passed**",
        f"- ROM checksum: **{'PASS' if report['checksum_ok'] else 'FAIL'}** "
        f"(`{report['checksum_stored']}`)",
        "",
        "| Abs | JP | KO (decoded) | OK |",
        "|-----|----|--------------|----|",
    ]
    for r in rows:
        jp = r["jp"].replace("|", "\\|")
        ko = r["decoded"].replace("|", "\\|")
        lines.append(f"| `{r['abs']}` | {jp} | {ko} | {'Y' if r['ok'] else 'N'} |")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Verify: {report['passed']}/{report['checked']} passed")
    print(f"Wrote {args.out}")
    print(f"Wrote {md}")
    if fails or not report["checksum_ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
