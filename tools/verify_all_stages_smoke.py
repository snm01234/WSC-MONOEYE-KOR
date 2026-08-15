#!/usr/bin/env python3
"""
Verification for monoeye_ko_all.wsc / full-sheet free-space build.

Gates:
  - jagd_ok at 6D937C
  - unit banks 50-5D / 6A-6F compared against the ORIGINAL 8 MiB ROM
    (``unit_baseline: "jp"``). Only the explicit intended allowlist may differ —
    approved Hangul UI 75 sites, glyph padding where the original was FF, code
    caves, hook sites, the dialogue band, the 5F dictionary and the header.
    One byte outside it sets ``unit_banks_clean: false``.
  - opening samples Hangul (6040A5 / 6040B5 required)
  - optional sheet-driven stage samples that already have Hangul KO

Baseline history: this gate used to compare against the tip, which grandfathered
every invasion already promoted into the tip (bugfix.md 1.7). It now shares the
original-ROM baseline and the classifier with ``tools/diff_stock_3way.py`` and
``tools/smoke_free_space_static.py``.

READ-ONLY with respect to ROMs: only the JSON report is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from baseline_metadata import load_stock_approved_ranges  # noqa: E402

from apply_ext_dict_unit import attach_ext3  # noqa: E402
from build_script_ko import JAGD_GUARD_ABS, JAGD_GUARD_GOOD  # noqa: E402
from diff_stock_3way import (  # noqa: E402
    UNINTENDED,
    Run,
    classify_byte,
    diff_positions,
    guess_tool,
)
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base  # noqa: E402
from patch_exp_dictionary import make_exp_dictionary  # noqa: E402
from verify_stock_noninvasion import load_approved_detachment  # noqa: E402

SRC_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_PAD3 = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXP_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"


def make_smoke_dictionary(rom: bytes | bytearray) -> Dictionary:
    """Stock + bank10 ext + optional bank11 ext3 for offline expand."""
    if EXP_META.exists():
        meta = json.loads(EXP_META.read_text(encoding="utf-8"))
        d = make_exp_dictionary(rom, meta)
    else:
        d = Dictionary(rom)
    if not EXT3_META.exists():
        return d
    e3 = json.loads(EXT3_META.read_text(encoding="utf-8"))
    return attach_ext3(d, rom, e3)

# Always required (opening regression class).
REQUIRED_OPENING = (0x6040A5, 0x6040B5, 0x6040CB)

# Prefer these if sheet has Hangul KO at/near them; otherwise pick from sheet.
STAGE_ANCHORS = (
    ("Ep1-3", 0x60456B, 0x62FFFF),
    ("Ep4", 0x630000, 0x63FFFF),
    ("Ep5-8", 0x640000, 0x69FFFF),
)


def bank_diff(a: bytes, b: bytes, seg: int) -> int:
    sa = stock_base(a) + (seg << 16)
    sb = stock_base(b) + (seg << 16)
    return sum(1 for x, y in zip(a[sa : sa + 0x10000], b[sb : sb + 0x10000]) if x != y)


UNIT_SEGS = tuple(range(0x50, 0x5E)) + tuple(range(0x6A, 0x70))


def unit_bank_runs(
    work: bytes,
    jp: bytes,
    segs,
    baseline_ranges: Sequence[tuple[int, int, str, bytes]] = (),
) -> List[Run]:
    """Merged diff runs of ``work`` vs the ORIGINAL ROM inside ``segs``.

    Classification is delegated to ``diff_stock_3way.classify_byte`` so this
    gate and the 3-way diff share one allowlist definition.
    """
    sw, sj = stock_base(work), stock_base(jp)
    wv, jv = memoryview(bytes(work)), memoryview(bytes(jp))
    runs: List[Run] = []
    for seg in segs:
        base = seg << 16
        tgt = wv[sw + base : sw + base + 0x10000]
        orig = jv[sj + base : sj + base + 0x10000]
        start = -1
        cls = cat = ""
        prev = -2

        def flush(end: int) -> None:
            if start < 0:
                return
            ln = end - start + 1
            runs.append(
                Run(
                    logical=base + start,
                    length=ln,
                    orig=bytes(orig[start : start + ln]),
                    tgt=bytes(tgt[start : start + ln]),
                    classification=cls,
                    category=cat,
                    abs_jp=sj + base + start,
                    abs_target=sw + base + start,
                )
            )

        for pos in diff_positions(orig, tgt):
            c, k = classify_byte(
                base + pos, orig[pos], tgt[pos], baseline_ranges
            )
            if start >= 0 and pos == prev + 1 and c == cls and k == cat:
                prev = pos
                continue
            flush(prev)
            start, cls, cat = pos, c, k
            prev = pos
        flush(prev)
    for r in runs:
        r.attributed_tool, r.tool_candidates = guess_tool(r)
    return runs


def unit_site_detail(r: Run) -> dict:
    """Per-site detail required by the gate report (task 3.1)."""
    return {
        "bank": f"{r.bank:02X}",
        "off": f"{r.off:04X}",
        "site": r.site,
        "len": r.length,
        "orig": r.orig.hex(),
        "tip": r.tgt.hex(),
        "attributed_tool": r.attributed_tool,
        "classification": r.classification,
        "category": r.category,
        "tool_candidates": r.tool_candidates,
    }


def _hangul(t: str | None) -> bool:
    return bool(t and any("\uac00" <= c <= "\ud7a3" for c in t))


def pick_stage_samples(sheet_path: Path | None) -> list[int]:
    samples = list(REQUIRED_OPENING)
    if sheet_path is None or not sheet_path.exists():
        return samples
    data = json.loads(sheet_path.read_text(encoding="utf-8"))
    lines = data["lines"] if isinstance(data, dict) else data
    by_band: dict[str, list[int]] = {name: [] for name, _, _ in STAGE_ANCHORS}
    for row in lines:
        ko = row.get("ko") or ""
        if not _hangul(ko):
            continue
        try:
            a = int(row["abs"], 16)
        except Exception:
            continue
        for name, lo, hi in STAGE_ANCHORS:
            if lo <= a <= hi:
                by_band[name].append(a)
                break
    for name, _lo, _hi in STAGE_ANCHORS:
        abs_list = sorted(by_band[name])
        if not abs_list:
            continue
        # first + mid sample per band
        samples.append(abs_list[0])
        samples.append(abs_list[len(abs_list) // 2])
    # unique preserve order
    seen: set[int] = set()
    out: list[int] = []
    for a in samples:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_all.wsc")
    ap.add_argument(
        "--report",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_all_build_report.json",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_apply_all.json",
        help="Apply sheet used to pick real Hangul sample abs",
    )
    ap.add_argument(
        "--approved-detachment-report",
        type=Path,
        default=None,
        help="candidate-bound duplicate-detachment proof whose exact unit-bank "
        "ranges may be treated as meaning-preserving",
    )
    ap.add_argument(
        "--baseline-meta",
        type=Path,
        default=None,
        help="normalized P0 metadata whose record_body ranges are accepted "
        "main-TIP baseline changes",
    )
    args = ap.parse_args()

    if not args.rom.exists():
        print(f"Error: Target ROM not found: {args.rom}", file=sys.stderr)
        return 1
    if args.report.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this gate is read-only")

    detachment_indices, detachment_sha, detachment_ranges = load_approved_detachment(
        args.approved_detachment_report
    )
    if detachment_sha is not None:
        actual_sha = hashlib.sha256(args.rom.read_bytes()).hexdigest()
        if actual_sha != detachment_sha:
            raise SystemExit(
                f"detachment approval is bound to {detachment_sha}, "
                f"but {args.rom} is {actual_sha}"
            )

    work = load_rom(args.rom)
    jp = load_rom(SRC_JP)
    tip_rom = load_rom(TIP) if TIP.exists() else None
    tbl = Tbl.load(TBL_PAD3)

    fo = stock_base(work) + JAGD_GUARD_ABS
    jagd_ok = bytes(work[fo : fo + 3]) == JAGD_GUARD_GOOD

    unit_segs = list(UNIT_SEGS)
    # Baseline = ORIGINAL 8 MiB ROM (bugfix.md 1.7 / 2.7). The old tip baseline
    # passed every invasion that had already been promoted into the tip.
    baseline_ranges = load_stock_approved_ranges(args.baseline_meta)
    unit_runs = unit_bank_runs(work, jp, unit_segs, baseline_ranges)

    def detachment_owner(run: Run) -> str | None:
        start = run.logical
        end = start + run.length
        for lo, hi, owner in detachment_ranges:
            if lo <= start and end <= hi:
                return owner
        return None

    authorized_detachment = [
        r
        for r in unit_runs
        if r.classification == UNINTENDED and detachment_owner(r) is not None
    ]
    authorized_ids = {id(r) for r in authorized_detachment}
    unit_violations = [
        r
        for r in unit_runs
        if r.classification == UNINTENDED and id(r) not in authorized_ids
    ]
    unit_ok = not unit_violations
    unit_diffs = {
        f"{seg:02X}": bank_diff(work, jp, seg)
        for seg in unit_segs
        if bank_diff(work, jp, seg)
    }
    unit_vs_jp = dict(unit_diffs)
    unit_vs_tip = (
        {
            f"{seg:02X}": bank_diff(work, tip_rom, seg)
            for seg in unit_segs
            if bank_diff(work, tip_rom, seg)
        }
        if tip_rom is not None
        else None
    )
    unit_allowed = {}
    for r in unit_runs:
        if r.classification == UNINTENDED and id(r) not in authorized_ids:
            continue
        key = f"{r.bank:02X}"
        unit_allowed[key] = unit_allowed.get(key, 0) + r.length

    st = stock_base(work)
    dictionary = make_smoke_dictionary(work)
    sample_abs = pick_stage_samples(args.sheet if args.sheet.exists() else None)
    samples: dict[str, str | None] = {}
    for a in sample_abs:
        r = read_encoded_z_safe(work, st + a, max_len=120)
        if not r:
            samples[f"{a:06X}"] = None
            continue
        _prefix, payload, _kind = split_prefix_body(r[0])
        samples[f"{a:06X}"] = (
            dictionary.expand(payload, tbl) if payload else None
        )

    opening_ok = all(
        _hangul(samples.get(f"{a:06X}")) for a in REQUIRED_OPENING
    )
    # At least one Hangul sample overall (opening already required).
    hangul_ok = opening_ok and any(_hangul(t) for t in samples.values())

    report = {
        "rom": str(args.rom),
        "jagd_ok": jagd_ok,
        "unit_banks_clean": unit_ok,
        "unit_baseline": "jp",
        "unit_baseline_path": str(SRC_JP),
        "baseline_meta": str(args.baseline_meta) if args.baseline_meta else None,
        "baseline_approved_ranges": len(baseline_ranges),
        "approved_detachment_report": (
            str(args.approved_detachment_report)
            if args.approved_detachment_report
            else None
        ),
        "approved_detachment_indices": [
            f"{index:04X}" for index in sorted(detachment_indices)
        ],
        "approved_detachment_ranges": [
            {
                "logical_start": f"{lo:06X}",
                "logical_end_exclusive": f"{hi:06X}",
                "owner_id": owner,
            }
            for lo, hi, owner in detachment_ranges
        ],
        "unit_segs": "50-5D,6A-6F",
        "unit_allowlist": (
            "approved UI 75 sites, glyph padding where original was FF, caves, "
            "hook sites, dialogue band, 5F dict, header "
            "(diff_stock_3way.classify_byte)"
        ),
        "unit_diffs": {k: v for k, v in unit_diffs.items() if v},
        "unit_vs_jp_nonzero": unit_vs_jp,
        "unit_vs_tip_nonzero": unit_vs_tip,
        "unit_allowed_bytes_by_bank": unit_allowed,
        "unit_run_counts": {
            "runs": len(unit_runs),
            "diff_bytes": sum(r.length for r in unit_runs),
            "violation_runs": len(unit_violations),
            "violation_bytes": sum(r.length for r in unit_violations),
        },
        "unit_violation_sites": [unit_site_detail(r) for r in unit_violations],
        "unit_authorized_detachment_sites": [
            {**unit_site_detail(r), "detachment_owner": detachment_owner(r)}
            for r in authorized_detachment
        ],
        "opening_required_ok": opening_ok,
        "hangul_samples": samples,
        "hangul_ok": hangul_ok,
        "overall_ok": bool(jagd_ok and unit_ok and hangul_ok),
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
