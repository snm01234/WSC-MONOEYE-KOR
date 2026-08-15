#!/usr/bin/env python3
"""
Restore unit/scenario table bytes corrupted by false exp_spill pointer rewrites.

exp_spill searched banks 5C–5E / 6C–6F for far-pointers to dialogue offsets.
Coincidental off16+seg patterns were rewritten to expansion (seg 30–4F),
breaking MS master fields (e.g. stage-2 Jagd Doga @ 6D937C).

Default mode (--full-bank) copies entire deny banks from a clean 8 MiB reference.
Site mode only rewrites 3-byte expansion-looking windows (can leave residue).

Does not touch dictionary (5F) or dialogue banks (60–6B).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum  # noqa: E402
from rebuild_script_banks import POINTER_SEARCH_DENY_BANKS  # noqa: E402

# Restore only deny-list banks (unit/scenario). Never 5F (dict) or 60–6B.
RESTORE_BANKS = sorted(seg for seg in POINTER_SEARCH_DENY_BANKS if seg != 0x5F)


def file_off(rom: bytes | bytearray, logical: int) -> int:
    return stock_base(rom) + logical


def find_false_sites(
    tip: bytes | bytearray,
    ref: bytes | bytearray,
) -> list[dict]:
    """
    3-byte sites in RESTORE_BANKS where tip looks like expansion retarget
    (… seg in 30–4F) and ref does not.
    """
    sites: list[dict] = []
    for seg in RESTORE_BANKS:
        tip_base = file_off(tip, seg << 16)
        ref_base = file_off(ref, seg << 16)
        tip_bank = tip[tip_base : tip_base + 0x10000]
        ref_bank = ref[ref_base : ref_base + 0x10000]
        i = 0
        while i < 0x10000 - 2:
            if tip_bank[i : i + 3] == ref_bank[i : i + 3]:
                i += 1
                continue
            tip_seg = tip_bank[i + 2]
            ref_seg = ref_bank[i + 2]
            if 0x30 <= tip_seg <= 0x4F and not (0x30 <= ref_seg <= 0x4F):
                logical = (seg << 16) | i
                sites.append(
                    {
                        "abs": f"{logical:06X}",
                        "tip": tip_bank[i : i + 3].hex(),
                        "ref": ref_bank[i : i + 3].hex(),
                    }
                )
                i += 3
                continue
            i += 1
    return sites


def count_bank_diffs(
    tip: bytes | bytearray,
    ref: bytes | bytearray,
) -> dict[str, int]:
    out: dict[str, int] = {}
    for seg in RESTORE_BANKS:
        tip_bank = tip[file_off(tip, seg << 16) : file_off(tip, seg << 16) + 0x10000]
        ref_bank = ref[file_off(ref, seg << 16) : file_off(ref, seg << 16) + 0x10000]
        out[f"{seg:02X}"] = sum(1 for a, b in zip(tip_bank, ref_bank) if a != b)
    return out


def restore_full_banks(
    tip: bytearray,
    ref: bytes | bytearray,
) -> dict[str, int]:
    changed: dict[str, int] = {}
    for seg in RESTORE_BANKS:
        tip_base = file_off(tip, seg << 16)
        ref_base = file_off(ref, seg << 16)
        n = 0
        for i in range(0x10000):
            if tip[tip_base + i] != ref[ref_base + i]:
                tip[tip_base + i] = ref[ref_base + i]
                n += 1
        changed[f"{seg:02X}"] = n
    return changed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--ref-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc",
        help="Clean pre-false-spill reference (8 MiB or matching tip layout)",
    )
    ap.add_argument(
        "--backup",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.pre_restore_expspill.wsc",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/restore_false_expspill_report.json",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--full-bank",
        action="store_true",
        help="Copy entire deny banks from ref (default; clears residue)",
    )
    mode.add_argument(
        "--sites-only",
        action="store_true",
        help="Only rewrite expansion-looking 3-byte windows (legacy)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    full_bank = not args.sites_only

    tip = bytearray(load_rom(args.rom))
    ref = load_rom(args.ref_rom)
    sites = find_false_sites(tip, ref)
    bank_diffs = count_bank_diffs(tip, ref)

    report = {
        "mode": "full_bank" if full_bank else "sites_only",
        "restore_banks": [f"{s:02X}" for s in RESTORE_BANKS],
        "sites_found": len(sites),
        "bank_byte_diffs": bank_diffs,
        "bank_byte_diffs_total": sum(bank_diffs.values()),
        "dry_run": bool(args.dry_run),
        "sample": sites[:30],
        "includes_6D937C": any(s["abs"] == "6D937C" for s in sites),
        "note": (
            "Stage-2 wrong Jagd Doga was false far-pointer rewrite at 6D937C "
            "(stock 3FA660). Prefer --full-bank so non-expansion residue is cleared too."
        ),
    }

    if args.dry_run:
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"DRY mode={report['mode']} sites={len(sites)} "
            f"bank_diffs={report['bank_byte_diffs_total']} "
            f"includes_6D937C={report['includes_6D937C']}"
        )
        print(f"Wrote {args.out_report}")
        return

    if not args.backup.exists():
        shutil.copy2(args.rom, args.backup)

    if full_bank:
        changed = restore_full_banks(tip, ref)
        restored = sum(changed.values())
        report["restored_bytes_per_bank"] = changed
    else:
        restored = 0
        for site in sites:
            logical = int(site["abs"], 16)
            ref_bytes = bytes.fromhex(site["ref"])
            fo = file_off(tip, logical)
            tip[fo : fo + 3] = ref_bytes
            restored += 1

    cs = f"{update_ws_checksum(tip):04X}"
    args.rom.write_bytes(tip)
    post_diffs = count_bank_diffs(tip, ref)
    report.update(
        {
            "restored": restored,
            "checksum": cs,
            "backup": str(args.backup),
            "post_bank_byte_diffs": post_diffs,
            "post_bank_byte_diffs_total": sum(post_diffs.values()),
            "post_6D937C": tip[file_off(tip, 0x6D937C) : file_off(tip, 0x6D937C) + 3].hex(),
        }
    )
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"OK mode={report['mode']} restored={restored} "
        f"post_diffs={report['post_bank_byte_diffs_total']} "
        f"6D937C={report['post_6D937C']} checksum={cs}"
    )


if __name__ == "__main__":
    main()
