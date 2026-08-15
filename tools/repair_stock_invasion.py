#!/usr/bin/env python3
"""
Restore stock-address-space invasion against the ORIGINAL 8 MiB ROM
(requirements 2.3, 2.4; preservation 3.4, 3.5, 3.11).

Reference is always ``SD Gundam G Generation Mono-Eye Gundams.wsc`` (8 MiB),
never ``monoeye_ko_expanded_8mb.wsc`` — the invasion entered at the 8 MiB backup
and every rebuild inherits it, so using ``_8mb`` as the reference would make the
corruption permanent (bugfix.md §Fix Implementation 1).

The restore list is **derived, not hardcoded**: this tool runs the task-1
classifier (``tools/diff_stock_3way.py``) against each target and restores every
run it classifies UNINTENDED. The intended allowlist is therefore exactly
``diff_stock_3way.classify_byte`` — approved Hangul UI (75:B6A6/B7C5/B7CD/B7D5/
BA40), glyph padding (3F/40/41), code caves, hook sites, the dialogue band,
the 5F dictionary and the header are never reverted. The derived set is
cross-checked against the design's documented 297 B core list and any difference
is reported.

Out-of-band writes (dialogue banks 60–69 below the allowed band 0x6040A5) are
handled behind their own flag ``--restore-out-of-band`` / ``--no-restore-out-of
-band`` so they can be reverted independently of the 297 B core set. Default is
ON; see OUT_OF_BAND_EVIDENCE below for the measurements behind that default.

``--dry-run`` is the default. Writing requires an explicit ``--commit``, which
first copies the target to ``out/patch/backup/<timestamp>/<filename>`` (path
recorded in the report), then restores, then updates the WonderSwan header
checksum, then re-reads and confirms every site.

This is a one-time cleanup plus a diagnostic tool. It is deliberately NOT wired
into any permanent pipeline stage (bugfix.md §Fix Implementation 2).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from diff_stock_3way import (  # noqa: E402
    DIALOGUE_LO,
    UNINTENDED,
    run_diff,
)
from monoeye_rom import load_rom, stock_base, update_ws_checksum  # noqa: E402

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_PRE = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_OUT = ROOT / "out/patch/repair_stock_invasion_report.json"
BACKUP_ROOT = ROOT / "out/patch/backup"

# Fixed pipeline order (task 4.2): the base is restored first so a later cold
# rebuild cannot re-inherit the corruption.
PIPELINE: Tuple[Path, ...] = (
    ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc",
    ROOT / "out/patch/monoeye_free_space_base.wsc",
    ROOT / "out/patch/monoeye_ko_expanded.wsc",
    ROOT / "out/patch/monoeye_ko_all.wsc",
)

# bugfix.md §Fix Implementation 1 — documented core restore list, 297 B total.
# Used only as a cross-check against the derived set; never as the restore input.
DESIGN_CORE_SITES: Dict[int, int] = {
    0x51CAF5: 2,
    0x522D9B: 2,
    0x5406C7: 2,
    0x5716F5: 2,
    0x57818C: 2,
    0x57843B: 2,
    0x593EF6: 2,
    0x598D89: 10,
    0x598F8C: 4,
    0x5A5031: 2,
    0x5D1CA0: 2,
    0x5D1EB4: 2,
    0x5E22B9: 2,
    0x6B2477: 256,
    0x6E2D44: 2,
    0x6F275F: 1,
    0x6F5202: 2,
}
DESIGN_CORE_BYTES = sum(DESIGN_CORE_SITES.values())  # 297

OUT_OF_BAND_EVIDENCE = {
    "question": "restore dialogue-bank writes below the allowed band 0x6040A5?",
    "decision": "restore (default on)",
    "measured": [
        "walking zstrings from 60:0000 to the band start yields 2,627 NUL-terminated "
        "records (payload 0–27 B, mean 5.5); every out-of-band site lands inside one "
        "and the original bytes expand with the original dictionary into coherent "
        "Japanese dialogue, so the original content is dialogue text, not a pointer "
        "table and not dense binary",
        "monoeye_ko_expanded_8mb.wsc: 71 runs / 461 B across 60:0005–60:3AEA — 67 runs "
        "are ext/dict token + 0x01 padding in-place body substitutions, 3 runs blank a "
        "record with zero bytes (the lone-NUL / empty-phrase condition the design links "
        "to the early-termination path), 1 other",
        "at all 71 of those sites the tip, monoeye_free_space_base.wsc and "
        "monoeye_ko_all.wsc are already byte-identical to the original (0 of 71 differ), "
        "so restoring _8mb only drops stale corruption the rest of the pipeline has "
        "already discarded",
        "60:11BA (3 B, 62 84 2e → 30 ef 06 in the tip) is a false seg8_off16 pointer hit "
        "inside the live record at 60:11B8 ('ここのブラックド－ルで全てを破壊し、'); "
        "free_space_pointer_allowlist.json lists 6011BA/BB/BC and the reloc row "
        "abs=622E84 names 6011BA as its only ptr_site, i.e. the relocation treated three "
        "bytes of running prose as the sole far pointer to 62:2E84",
        "62:2E84 already carries its own in-place substitution in the tip, so the "
        "relocated spill copy behind 60:11BA is redundant; restoring the 3 bytes repairs "
        "a corrupted below-band record and removes no reachable Hangul",
        "coverage bands start at 0x6040A5 (apply_ext_dict_unit BAND_EARLY_LO), so none of "
        "these sites is inside a measured coverage band",
    ],
    "not_a_hangul_pad_reference": (
        "no out-of-band written value is a pad-glyph reference a live string depends on: "
        "the _8mb writes are ext-dict tokens for records the promoted pipeline already "
        "reverted, and 60:11BA is an expansion far pointer (seg 0x30) whose target text "
        "is delivered in place instead"
    ),
}

CATEGORY_OUT_OF_BAND = "dialogue_bank_outside_band"


# --- partition --------------------------------------------------------------


def is_out_of_band(run: dict) -> bool:
    """Dialogue-bank write below the allowed band 0x6040A5."""
    bank = int(run["bank"], 16)
    logical = int(run["logical"], 16)
    return (
        run["category"] == CATEGORY_OUT_OF_BAND
        or (0x60 <= bank <= 0x69 and logical < DIALOGUE_LO)
    )


def partition(unintended: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    core: List[dict] = []
    oob: List[dict] = []
    for r in unintended:
        (oob if is_out_of_band(r) else core).append(r)
    return core, oob


def cross_check_design(core: Sequence[dict]) -> dict:
    """Compare the derived core set against the design's documented 297 B list."""
    derived = {int(r["logical"], 16): r["len"] for r in core}
    matched = {}
    len_mismatch = []
    for logical, ln in sorted(DESIGN_CORE_SITES.items()):
        if logical in derived:
            matched[logical] = derived[logical]
            if derived[logical] != ln:
                len_mismatch.append(
                    {
                        "site": _site(logical),
                        "design_len": ln,
                        "derived_len": derived[logical],
                    }
                )
    missing = [
        {"site": _site(k), "len": v}
        for k, v in sorted(DESIGN_CORE_SITES.items())
        if k not in derived
    ]
    extra = [
        {"site": _site(k), "len": v}
        for k, v in sorted(derived.items())
        if k not in DESIGN_CORE_SITES
    ]
    return {
        "design_sites": len(DESIGN_CORE_SITES),
        "design_bytes": DESIGN_CORE_BYTES,
        "derived_core_sites": len(derived),
        "derived_core_bytes": sum(derived.values()),
        "matched_sites": len(matched),
        "missing_from_target": missing,
        "extra_vs_design": extra,
        "length_mismatch": len_mismatch,
        "identical_to_design": not missing and not extra and not len_mismatch,
    }


def _site(logical: int) -> str:
    return f"{logical >> 16:02X}:{logical & 0xFFFF:04X}"


# --- restore ----------------------------------------------------------------


def make_backup(target: Path, stamp: str) -> Path:
    dest_dir = BACKUP_ROOT / stamp
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / target.name
    shutil.copy2(target, dest)
    return dest


def apply_restore(
    target: Path,
    jp: bytes,
    runs: Sequence[dict],
    *,
    commit: bool,
    stamp: str,
) -> dict:
    rom = bytearray(load_rom(target))
    sb_t = stock_base(rom)
    sb_j = stock_base(jp)

    sites: List[dict] = []
    for r in sorted(runs, key=lambda x: int(x["logical"], 16)):
        logical = int(r["logical"], 16)
        ln = r["len"]
        orig = jp[sb_j + logical : sb_j + logical + ln]
        before = bytes(rom[sb_t + logical : sb_t + logical + ln])
        rom[sb_t + logical : sb_t + logical + ln] = orig
        sites.append(
            {
                "site": _site(logical),
                "logical": f"{logical:06X}",
                "len": ln,
                "before": before.hex(),
                "restored_to": orig.hex(),
                "category": r["category"],
                "attribution": r["attribution"],
                "attributed_tool": r["attributed_tool"],
                "class": "out_of_band" if is_out_of_band(r) else "core",
                "already_original": before == orig,
            }
        )

    result: dict = {
        "target": str(target),
        "target_size": len(rom),
        "stock_base": f"{sb_t:#x}",
        "sites_planned": len(sites),
        "bytes_planned": sum(s["len"] for s in sites),
        "sites": sites,
        "committed": False,
        "backup": None,
        "checksum_before": None,
        "checksum_after": None,
        "confirmed_sites": 0,
        "unconfirmed": [],
    }
    if not commit:
        return result

    from monoeye_rom import ws_header

    result["checksum_before"] = f"{ws_header(load_rom(target))['checksum']:04X}"
    result["backup"] = str(make_backup(target, stamp))
    result["checksum_after"] = f"{update_ws_checksum(rom):04X}"
    target.write_bytes(rom)
    result["committed"] = True

    # Confirm from disk, not from the in-memory buffer.
    check = bytes(load_rom(target))
    sb_c = stock_base(check)
    for s in sites:
        logical = int(s["logical"], 16)
        got = check[sb_c + logical : sb_c + logical + s["len"]]
        s["confirmed"] = got.hex() == s["restored_to"]
        if s["confirmed"]:
            result["confirmed_sites"] += 1
        else:
            result["unconfirmed"].append({"site": s["site"], "on_disk": got.hex()})
    return result


# --- per-target driver ------------------------------------------------------


def process_target(
    target: Path,
    *,
    jp_path: Path,
    jp_bytes: bytes,
    pre_path: Path,
    tbl: Path | None,
    restore_out_of_band: bool,
    commit: bool,
    stamp: str,
) -> dict:
    diff = run_diff(
        jp_path,
        pre_path,
        target,
        tbl_path=tbl,
        hex_cap=32,
        decode=False,
        max_per_cat=200,
    )
    # diff["unintended"] always carries every UNINTENDED run in full.
    unintended = [r for r in diff["unintended"] if r["classification"] == UNINTENDED]
    core, oob = partition(unintended)
    selected = list(core) + (list(oob) if restore_out_of_band else [])

    entry = apply_restore(target, jp_bytes, selected, commit=commit, stamp=stamp)
    entry["derived"] = {
        "unintended_runs": len(unintended),
        "unintended_bytes": sum(r["len"] for r in unintended),
        "core_runs": len(core),
        "core_bytes": sum(r["len"] for r in core),
        "out_of_band_runs": len(oob),
        "out_of_band_bytes": sum(r["len"] for r in oob),
        "out_of_band_restored": restore_out_of_band,
        "skipped_out_of_band": []
        if restore_out_of_band
        else [{"site": r["site"], "len": r["len"]} for r in oob],
    }
    entry["design_cross_check"] = cross_check_design(core)
    entry["out_of_band_sites"] = [
        {
            "site": r["site"],
            "len": r["len"],
            "orig": r["orig_hex"],
            "target": r["target_hex"],
            "attribution": r["attribution"],
            "attributed_tool": r["attributed_tool"],
        }
        for r in oob
    ]
    entry["intended_left_untouched"] = {
        "runs": diff["counts"]["intended_runs"],
        "bytes": diff["counts"]["intended_bytes"],
        "categories": sorted(
            c
            for c, b in diff["by_category"].items()
            if b["bytes"] - b["unintended_bytes"] > 0
        ),
    }
    return entry


# --- reporting --------------------------------------------------------------


def print_entry(e: dict, *, commit: bool) -> None:
    d = e["derived"]
    x = e["design_cross_check"]
    print(f"\n=== {'COMMIT' if commit else 'DRY-RUN'}: {e['target']}")
    print(
        f"  derived UNINTENDED : {d['unintended_bytes']} B / {d['unintended_runs']} runs "
        f"(core {d['core_bytes']} B / {d['core_runs']}, "
        f"out-of-band {d['out_of_band_bytes']} B / {d['out_of_band_runs']})"
    )
    print(
        f"  design cross-check : derived core {x['derived_core_bytes']} B / "
        f"{x['derived_core_sites']} sites vs design {x['design_bytes']} B / "
        f"{x['design_sites']} sites → "
        f"{'identical' if x['identical_to_design'] else 'DIFFERS'}"
    )
    for m in x["missing_from_target"]:
        print(f"    design site absent here : {m['site']} ({m['len']} B) — already clean")
    for m in x["extra_vs_design"]:
        print(f"    extra vs design         : {m['site']} ({m['len']} B)")
    for m in x["length_mismatch"]:
        print(
            f"    length mismatch         : {m['site']} design {m['design_len']} B "
            f"vs derived {m['derived_len']} B"
        )
    if not d["out_of_band_restored"] and d["out_of_band_runs"]:
        print(f"    out-of-band SKIPPED     : {d['out_of_band_bytes']} B "
              f"(--no-restore-out-of-band)")
    print(f"  restore plan       : {e['bytes_planned']} B at {e['sites_planned']} sites")
    if e["committed"]:
        print(f"  backup             : {e['backup']}")
        print(f"  checksum           : {e['checksum_before']} → {e['checksum_after']}")
        print(f"  confirmed          : {e['confirmed_sites']}/{e['sites_planned']} sites")
    for s in e["sites"]:
        mark = "ok " if s.get("confirmed") else ("   " if not e["committed"] else "!! ")
        print(
            f"    {mark}{s['site']:10s} {s['len']:>4} B [{s['class']:11s}] "
            f"{s['before'][:24]:24s} → {s['restored_to'][:24]:24s} "
            f"({s['attributed_tool']})"
        )
    if e["unconfirmed"]:
        print(f"  UNCONFIRMED: {e['unconfirmed']}")


# --- main -------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--jp",
        type=Path,
        default=DEFAULT_JP,
        help="ORIGINAL 8 MiB reference ROM (never monoeye_ko_expanded_8mb.wsc)",
    )
    ap.add_argument("--pre", type=Path, default=DEFAULT_PRE, help="pre-ext3 tip (attribution only)")
    ap.add_argument(
        "--target",
        type=Path,
        action="append",
        default=None,
        help="target ROM (repeatable). Default: --pipeline order",
    )
    ap.add_argument(
        "--pipeline",
        action="store_true",
        help="use the fixed pipeline order: _8mb → free_space_base → tip → ko_all",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--tbl", type=Path, default=ROOT / "data/monoeye.tbl")
    ap.add_argument(
        "--commit",
        action="store_true",
        help="actually write (default is --dry-run); backs up first",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="explicit no-write mode (this is the default)",
    )
    ap.add_argument(
        "--restore-out-of-band",
        dest="oob",
        action="store_true",
        default=True,
        help="restore dialogue-bank writes below 0x6040A5 (default: on, evidence-backed)",
    )
    ap.add_argument(
        "--no-restore-out-of-band",
        dest="oob",
        action="store_false",
        help="leave out-of-band writes alone (revert this decision independently)",
    )
    args = ap.parse_args(argv)

    if args.commit and args.dry_run:
        raise SystemExit("--commit and --dry-run are mutually exclusive")
    commit = bool(args.commit)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a report over a .wsc")
    if "_8mb" in args.jp.name:
        raise SystemExit(
            f"reference ROM must be the ORIGINAL 8 MiB image, not {args.jp.name} — "
            "the invasion entered at the 8 MiB backup"
        )
    targets = list(args.target or ([] if not args.pipeline else PIPELINE)) or list(PIPELINE)
    for p in [args.jp, args.pre, *targets]:
        if not p.exists():
            raise SystemExit(f"missing input ROM: {p}")
    for t in targets:
        if t.resolve() == args.jp.resolve():
            raise SystemExit(f"refusing to write the reference ROM: {t}")

    jp_bytes = bytes(load_rom(args.jp))
    if len(jp_bytes) != 0x800000:
        raise SystemExit(
            f"reference ROM must be 8 MiB, got {len(jp_bytes):#x} ({args.jp})"
        )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    entries: List[dict] = []
    for t in targets:
        entries.append(
            process_target(
                t,
                jp_path=args.jp,
                jp_bytes=jp_bytes,
                pre_path=args.pre,
                tbl=args.tbl,
                restore_out_of_band=args.oob,
                commit=commit,
                stamp=stamp,
            )
        )
        print_entry(entries[-1], commit=commit)

    ok = all(
        (not e["committed"]) or e["confirmed_sites"] == e["sites_planned"]
        for e in entries
    )
    report = {
        "ok": ok,
        "generated_by": "tools/repair_stock_invasion.py",
        "mode": "commit" if commit else "dry-run",
        "reference_rom": str(args.jp),
        "reference_note": "ORIGINAL 8 MiB image — never monoeye_ko_expanded_8mb.wsc",
        "pre_ext3": str(args.pre),
        "restore_list_source": "derived by running tools/diff_stock_3way.py per target "
        "and restoring every run classified UNINTENDED (allowlist = "
        "diff_stock_3way.classify_byte)",
        "not_a_pipeline_stage": "one-time cleanup + diagnostic tool; deliberately not "
        "wired into any permanent build stage (bugfix.md §Fix Implementation 2)",
        "out_of_band_policy": {
            "restored": args.oob,
            "flag": "--restore-out-of-band / --no-restore-out-of-band",
            "evidence": OUT_OF_BAND_EVIDENCE,
        },
        "backup_root": str(BACKUP_ROOT / stamp) if commit else None,
        "revert": (
            f"copy files from {BACKUP_ROOT / stamp} back over the targets"
            if commit
            else "no write performed"
        ),
        "targets": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n→ {args.out}")
    if commit:
        print(f"backup dir → {BACKUP_ROOT / stamp}")
        print(f"revert     → copy the backup files back over the targets")
    else:
        print("dry-run: no ROM was written. Add --commit to apply.")
    print(f"ok={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
