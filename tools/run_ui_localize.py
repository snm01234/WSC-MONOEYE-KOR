#!/usr/bin/env python3
"""
Apply unit / weapon / UI localization onto a ROM:

  unit → weapon → ui_system → battle → menu×3 → inplace → spill

Default target is tip; prefer --out-rom work then smoke → promote.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
ORIG = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_WORK = ROOT / "out/patch/monoeye_ko_ui_work.wsc"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=TIP, help="input ROM (default: tip)")
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=None,
        help="output ROM (default: same as --rom). Use monoeye_ko_ui_work.wsc for safe apply.",
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ORIG,
        help="JP match ROM for dict index lookup",
    )
    ap.add_argument(
        "--copy-from-tip",
        action="store_true",
        help=f"copy tip → --out-rom (or {DEFAULT_WORK.name}) before applying",
    )
    args = ap.parse_args()

    out = args.out_rom or args.rom
    if args.copy_from_tip:
        out = args.out_rom or DEFAULT_WORK
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TIP if args.rom == TIP else args.rom, out)
        print(f"copied → {out}")
        src = out
    else:
        src = args.rom
        if out != src:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            src = out

    py = sys.executable
    rom = str(src)
    base = str(args.base_rom)
    report_dir = ROOT / "out/patch"

    steps: list[tuple[str, list[str]]] = [
        (
            "unit_names",
            [
                "apply_proper_nouns.py",
                "--names",
                str(ROOT / "data/unit_names_ko.json"),
                "--out-report",
                str(report_dir / "unit_names_report.json"),
            ],
        ),
        (
            "weapon",
            [
                "apply_weapon_table.py",
                "--names",
                str(ROOT / "data/weapon_names_ko.json"),
                "--out-report",
                str(report_dir / "weapon_table_report.json"),
            ],
        ),
        (
            "ui_system",
            [
                "apply_proper_nouns.py",
                "--names",
                str(ROOT / "data/ui_system_ko.json"),
                "--out-report",
                str(report_dir / "ui_system_report.json"),
            ],
        ),
        (
            "ui_battle",
            [
                "apply_proper_nouns.py",
                "--names",
                str(ROOT / "data/ui_battle_terms_ko.json"),
                "--out-report",
                str(report_dir / "ui_battle_terms_report.json"),
            ],
        ),
        (
            "ui_menu",
            [
                "apply_proper_nouns.py",
                "--names",
                str(ROOT / "data/ui_menu_terms_ko.json"),
                "--out-report",
                str(report_dir / "ui_menu_terms_report.json"),
            ],
        ),
        (
            "ui_menu2",
            [
                "apply_proper_nouns.py",
                "--names",
                str(ROOT / "data/ui_menu_terms2_ko.json"),
                "--out-report",
                str(report_dir / "ui_menu_terms2_report.json"),
            ],
        ),
        (
            "ui_menu3",
            [
                "apply_proper_nouns.py",
                "--names",
                str(ROOT / "data/ui_menu_terms3_ko.json"),
                "--out-report",
                str(report_dir / "ui_menu_terms3_report.json"),
            ],
        ),
        (
            "ui_mined",
            [
                "apply_proper_nouns.py",
                "--names",
                str(ROOT / "data/ui_mined_terms_ko.json"),
                "--out-report",
                str(report_dir / "ui_mined_terms_report.json"),
            ],
        ),
        (
            "ui_names",
            [
                "apply_proper_nouns.py",
                "--names",
                str(ROOT / "data/ui_proper_nouns_ko.json"),
                "--out-report",
                str(report_dir / "ui_proper_nouns_report.json"),
            ],
        ),
        (
            "ui_inplace",
            [
                "apply_ui_inplace.py",
                "--strings",
                str(ROOT / "data/ui_inplace_ko.json"),
                "--out-report",
                str(report_dir / "ui_inplace_report.json"),
            ],
        ),
        (
            "ui_spill",
            [
                "apply_ui_spill.py",
                "--strings",
                str(ROOT / "data/ui_spill_ko.json"),
                "--out-report",
                str(report_dir / "ui_spill_report.json"),
            ],
        ),
    ]

    for name, extra in steps:
        script = extra[0]
        rest = extra[1:]
        # Skip optional catalogs when absent (forward-compatible)
        optional = {
            "ui_menu3": ROOT / "data/ui_menu_terms3_ko.json",
            "ui_mined": ROOT / "data/ui_mined_terms_ko.json",
            "ui_names": ROOT / "data/ui_proper_nouns_ko.json",
        }
        if name in optional and not optional[name].exists():
            print(f"skip {name}: catalog missing")
            continue
        cmd = [
            py,
            str(TOOLS / script),
            "--rom",
            rom,
            "--out-rom",
            rom,
            "--base-rom",
            base,
            *rest,
        ]
        # Proper-noun UI shares dict slots with opening seed lines → seed expand drift.
        if script == "apply_proper_nouns.py":
            cmd.append("--allow-seed-fail")
        run(cmd)

    print(f"Done → {rom}")


if __name__ == "__main__":
    main()
