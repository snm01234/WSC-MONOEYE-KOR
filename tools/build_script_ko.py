#!/usr/bin/env python3
"""
Hybrid Korean script compiler for Mono-Eye Gundams.

This is NOT a classic pointer-table inserter. Most dialogue is sequential NUL
scan (~92%); only ~8% has relocatable far pointers. The build picks a strategy
per line and refuses unsafe moves.

Phases (tip ROM in-place by default)
  Default placement=free-space:
    classify → free_space → opening_dedicated → verify
  Legacy placement:
    1) classify   — dry report of pointer / sequential / event / already-KO
    2) exp_spill  — far-pointer lines → expand bank30+ + pointer patch
    3) seq_dict   — no-pointer quality lines → size-preserving ext dict tokens
    4) separators — restore 00 00 before control/speaker (keeps title→face gap=0)
    5) opening_tail (opt) — dedicated free slots + shared phrases
    6) verify     — seed decode + opening gap guards

Hard bans (from regression history)
  - blanking sequential records / full-bank shift
  - force-format of bank10 ext dict while scripts still pin migrate slots
  - rewriting title/face window prefixes (17 1C / 08 xx 18)
  - weapon bank75 spill
  - sole_reclaim / stealing shared JP dict slots used by bank75 names
  - free-space: 60–69 body inplace / seq_dict (default off)

Usage
  python tools/build_script_ko.py --dry-run
  python tools/build_script_ko.py --placement free-space
  python tools/build_script_ko.py --placement legacy --phases classify,exp_spill,seq_dict,separators,verify
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import spillable_abs_set  # noqa: E402
from apply_translations_expanded import (  # noqa: E402
    apply_translations_expanded,
    load_translation_lines,
)
from event_record_heuristics import looks_like_event_body  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from fix_zstring_pad_separators import collect_starts, fix_at  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    decode_payload,
    load_rom,
    read_encoded_z_safe,
    update_ws_checksum,
)
from normalize_ko_text import is_low_quality_ko, normalize_ko_text  # noqa: E402

DEFAULT_PHASES = ("classify", "exp_spill", "seq_dict", "separators", "verify")
FREE_SPACE_PHASES = ("classify", "free_space", "opening_dedicated", "verify")
ALL_PHASES = DEFAULT_PHASES + ("opening_tail", "free_space", "opening_dedicated")

# Opening title→face pairs that must keep gap=0 (face 17 1C as L2).
OPENING_TITLE_FACE = (
    0x6040A5,
    0x6040CB,
    0x604116,
    0x6041AC,
    0x6041C2,
    0x6041F0,
    0x604247,
    0x604268,
    0x60430A,
)


def _parse_phases(raw: str) -> List[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    unknown = [p for p in parts if p not in ALL_PHASES]
    if unknown:
        raise SystemExit(f"unknown phases: {unknown}; allowed={list(ALL_PHASES)}")
    return parts


def _load_sheet_lines(path: Path) -> List[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "lines" in data:
        return list(data["lines"])
    if isinstance(data, list):
        return data
    raise SystemExit(f"unrecognized sheet format: {path}")


def _file_abs(rom: bytes | bytearray, logical_abs: int) -> int:
    """Map logical 8MiB abs into 16MiB file abs when needed."""
    from monoeye_rom import is_expanded_rom, stock_base

    if is_expanded_rom(rom) and logical_abs < 0x800000:
        return stock_base(rom) + logical_abs
    return logical_abs


def classify_sheet(
    rom: bytes,
    ptr_rom: bytes,
    sheet: Sequence[dict],
    seed_abs: Set[int],
    base_rom: bytes,
) -> Dict[str, Any]:
    """Bucket sheet rows without mutating ROM."""
    abs_list: List[int] = []
    rows: List[dict] = []
    for line in sheet:
        abs_s = line.get("abs")
        if not abs_s:
            continue
        abs_off = int(abs_s, 16)
        ko = normalize_ko_text(line.get("ko") or "")
        if abs_off in seed_abs:
            bucket = "seed"
        elif not ko or is_low_quality_ko(ko):
            bucket = "low_quality"
        else:
            bucket = "candidate"
        rows.append({"abs": abs_off, "ko": ko, "bucket": bucket})
        if bucket == "candidate":
            abs_list.append(abs_off)

    spillable = spillable_abs_set(ptr_rom, abs_list) if abs_list else set()
    counts: Counter[str] = Counter()
    detail = {"pointer": [], "sequential": [], "event": [], "missing": []}
    for row in rows:
        abs_off = row["abs"]
        if row["bucket"] != "candidate":
            counts[row["bucket"]] += 1
            continue
        base_got = read_encoded_z_safe(base_rom, abs_off)
        if base_got is None:
            counts["missing"] += 1
            if len(detail["missing"]) < 20:
                detail["missing"].append(f"{abs_off:06X}")
            continue
        body = split_prefix_body(base_got[0])[1]
        if looks_like_event_body(body):
            counts["event"] += 1
            if len(detail["event"]) < 20:
                detail["event"].append(f"{abs_off:06X}")
            continue
        if abs_off in spillable:
            counts["pointer"] += 1
            if len(detail["pointer"]) < 20:
                detail["pointer"].append(f"{abs_off:06X}")
        else:
            counts["sequential"] += 1
            if len(detail["sequential"]) < 20:
                detail["sequential"].append(f"{abs_off:06X}")

    return {
        "sheet_rows": len(rows),
        "counts": dict(counts),
        "spillable_candidates": len(spillable),
        "samples": detail,
        "note": (
            "pointer → exp_spill; sequential → seq_dict (slot-capped); "
            "event never rewritten; seed pinned"
        ),
    }


# Canary: false exp_spill used to rewrite this MS-master field (stage-2 Jagd Doga).
JAGD_GUARD_ABS = 0x6D937C
JAGD_GUARD_GOOD = bytes.fromhex("3fa660")


def assert_jagd_guard(rom: bytes | bytearray, *, where: str) -> None:
    from monoeye_rom import stock_base

    fo = stock_base(rom) + JAGD_GUARD_ABS
    got = bytes(rom[fo : fo + 3])
    if got != JAGD_GUARD_GOOD:
        raise RuntimeError(
            f"Jagd guard broken after {where}: 6D937C={got.hex()} "
            f"(want {JAGD_GUARD_GOOD.hex()}). Another writer may have raced, "
            f"or exp_spill invaded unit tables — run "
            f"tools/restore_false_expspill_sites.py --full-bank"
        )


def phase_free_space(
    rom_path: Path,
    out_rom: Path,
    *,
    tbl_path: Path,
    sheet_path: Path,
    jp_path: Path,
    hangul_marker: str,
    min_abs: int | None,
    max_abs: int | None,
    max_ptr_hits: int,
    report_path: Path,
    backup: bool,
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "apply_free_space_script_ko.py"),
        "--rom",
        str(rom_path),
        "--out-rom",
        str(out_rom),
        "--jp",
        str(jp_path),
        "--tbl",
        str(tbl_path),
        "--sheet",
        str(sheet_path),
        "--max-ptr-hits",
        str(max_ptr_hits),
        "--out-report",
        str(report_path),
    ]
    if backup:
        cmd.append("--backup")
    if min_abs is not None:
        cmd.extend(["--min-abs", f"{min_abs:X}"])
    if max_abs is not None:
        cmd.extend(["--max-abs", f"{max_abs:X}"])
    # marker via env not needed — apply_free_space reads pad3 meta
    rc = subprocess.call(cmd, cwd=ROOT)
    slim: Dict[str, Any] = {"rc": rc, "report": str(report_path)}
    if report_path.exists():
        full = json.loads(report_path.read_text(encoding="utf-8"))
        for key in (
            "relocated",
            "skipped",
            "pointer_allowlist_n",
            "verify",
            "wrote_rom",
            "expansion_bytes_used",
            "abort_reason",
        ):
            if key in full:
                slim[key] = full[key]
    return slim


def phase_exp_spill(
    rom: bytearray,
    tbl: Tbl,
    sheet_path: Path,
    *,
    hangul_marker: int,
    min_abs: int | None = None,
    max_abs: int | None = None,
) -> Dict[str, Any]:
    lines = load_translation_lines(sheet_path)
    report = apply_translations_expanded(
        rom,
        tbl,
        lines,
        max_shared_phrases=1024,
        allow_bank_rebuild=True,
        allow_inplace=False,
        hangul_marker_code=hangul_marker,
        overflow_mode="exp_spill",
        min_abs=min_abs,
        max_abs=max_abs,
    )
    br = report.get("bank_rebuild") or {}
    return {
        "lines_patched": report.get("lines_patched"),
        "decode_failures": report.get("decode_failures"),
        "mode_counts": report.get("mode_counts"),
        "relocated_records": br.get("relocated_records"),
        "pointer_fixes": br.get("pointer_fixes"),
        "skipped_no_pointer_count": br.get("skipped_no_pointer_count"),
        "skipped_no_seg_form_count": br.get("skipped_no_seg_form_count"),
        "expansion_bytes_used": br.get("expansion_bytes_used"),
    }


def phase_seq_dict(
    *,
    rom_path: Path,
    out_rom: Path,
    tbl_path: Path,
    sheet_path: Path,
    seed_path: Path,
    meta_path: Path,
    pointer_ref: Path,
    base_rom: Path,
    slots: int,
    report_path: Path,
    rank: str = "early-abs",
    stock_reclaim: bool = False,
    sole_reclaim: bool = False,
    band_early: int | None = None,
    band_bank60_rest: int | None = None,
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "apply_ext_dict_unit.py"),
        "--rom",
        str(rom_path),
        "--out-rom",
        str(out_rom),
        "--tbl",
        str(tbl_path),
        "--sheet",
        str(sheet_path),
        "--seed",
        str(seed_path),
        "--meta",
        str(meta_path),
        "--pointer-ref-rom",
        str(pointer_ref),
        "--base-rom",
        str(base_rom),
        "--only-no-pointer",
        "--rank",
        rank,
        "--slots",
        str(slots),
        "--out-report",
        str(report_path),
    ]
    if stock_reclaim:
        cmd.append("--stock-reclaim")
    if sole_reclaim:
        cmd.append("--sole-reclaim")
    if rank == "hybrid-bands":
        if band_early is not None:
            cmd.extend(["--band-early", str(band_early)])
        if band_bank60_rest is not None:
            cmd.extend(["--band-bank60-rest", str(band_bank60_rest)])
    rc = subprocess.call(cmd, cwd=ROOT)
    slim: Dict[str, Any] = {"rc": rc, "report": str(report_path)}
    if report_path.exists():
        full = json.loads(report_path.read_text(encoding="utf-8"))
        for key in (
            "unique_assigned",
            "lines_patched",
            "decode_fail",
            "seed_fail",
            "pool",
            "sole_reclaim",
            "checksum",
        ):
            if key in full:
                slim[key] = full[key]
        slim["assigned"] = full.get("unique_assigned", full.get("assigned"))
        slim["assigned_lines"] = full.get("lines_patched", full.get("assigned_lines"))
    if rc != 0:
        raise SystemExit(f"seq_dict phase failed rc={rc}")
    if int(slim.get("decode_fail") or 0) > 0:
        print(
            f"  warning: decode_fail={slim.get('decode_fail')} "
            f"(continuing; seed_fail={slim.get('seed_fail')})"
        )
    return slim


def phase_separators(rom: bytearray, banks: str = "60-6B") -> Dict[str, Any]:
    from monoeye_rom import stock_base

    lo_s, hi_s = banks.split("-")
    lo, hi = int(lo_s, 16), int(hi_s, 16)
    sb = stock_base(rom)
    starts = collect_starts(rom, lo, hi)
    fixed = []
    for i, abs_off in enumerate(starts):
        nxt = (
            starts[i + 1]
            if i + 1 < len(starts)
            else ((abs_off & ~0xFFFF) + 0x10000)
        )
        info = fix_at(rom, abs_off, nxt)
        if info:
            fixed.append(info)
    opening = []
    for x in fixed:
        logical = int(x["abs"], 16) - sb
        if 0x6040A0 <= logical <= 0x604500:
            opening.append({**x, "abs_logical": f"{logical:06X}"})
    return {"fixed": len(fixed), "opening_fixed": opening, "stock_base": f"{sb:06X}"}


def phase_opening_dedicated(
    *,
    rom_path: Path,
    out_rom: Path,
    tbl_path: Path,
    sheet_path: Path,
    seed_path: Path,
    meta_path: Path,
    lo: int,
    hi: int,
    report_path: Path,
    include_seed_abs: bool = True,
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "apply_opening_dedicated.py"),
        "--rom",
        str(rom_path),
        "--out-rom",
        str(out_rom),
        "--tbl",
        str(tbl_path),
        "--sheet",
        str(sheet_path),
        "--seed",
        str(seed_path),
        "--meta",
        str(meta_path),
        "--lo",
        f"{lo:X}",
        "--hi",
        f"{hi:X}",
        "--out-report",
        str(report_path),
    ]
    if include_seed_abs:
        cmd.append("--include-seed-abs")
    rc = subprocess.call(cmd, cwd=ROOT)
    slim: Dict[str, Any] = {"rc": rc, "report": str(report_path)}
    if report_path.exists():
        full = json.loads(report_path.read_text(encoding="utf-8"))
        for key in (
            "lines_patched",
            "plain_inplace",
            "reuse_existing",
            "window_unanimous",
            "full_line_tokens",
            "skipped_no_slot",
            "decode_failures",
            "uniques_assigned",
        ):
            if key in full:
                slim[key] = full[key]
        slim["body_abs"] = [
            row["abs"]
            for row in (full.get("applied") or [])
            if row.get("ok") is not False and row.get("abs")
        ]
    if rc:
        raise SystemExit(f"opening_dedicated phase failed rc={rc}")
    return slim


def phase_opening_tail(
    *,
    rom_path: Path,
    out_rom: Path,
    tbl_path: Path,
    sheet_path: Path,
    seed_path: Path,
    lo: int,
    hi: int,
    report_path: Path,
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "patch_opening_tail_ko.py"),
        "--rom",
        str(rom_path),
        "--out-rom",
        str(out_rom),
        "--tbl",
        str(tbl_path),
        "--sheet",
        str(sheet_path),
        "--seed",
        str(seed_path),
        "--lo",
        f"{lo:06X}",
        "--hi",
        f"{hi:06X}",
        "--out-report",
        str(report_path),
    ]
    rc = subprocess.call(cmd, cwd=ROOT)
    slim: Dict[str, Any] = {"rc": rc, "report": str(report_path)}
    if report_path.exists():
        full = json.loads(report_path.read_text(encoding="utf-8"))
        for key in ("applied_count", "skipped_count", "decode_failures", "checksum"):
            if key in full:
                slim[key] = full[key]
    if rc != 0:
        raise SystemExit(f"opening_tail phase failed rc={rc}")
    return slim


def phase_verify(
    rom: bytes,
    tbl: Tbl,
    seed_path: Path,
    meta_path: Path,
) -> Dict[str, Any]:
    from apply_ext_dict_unit import load_ext_meta, make_dictionary

    meta = load_ext_meta(meta_path)
    d = make_dictionary(rom, meta)
    seed = json.loads(seed_path.read_text(encoding="utf-8"))["lines"]
    seed_fail = []
    for row in seed:
        abs_off = int(row["abs"], 16)
        got = read_encoded_z_safe(rom, _file_abs(rom, abs_off))
        if got is None:
            seed_fail.append({"abs": row["abs"], "reason": "missing"})
            continue
        _pref, body, _kind = split_prefix_body(got[0])
        core = bytes(b for b in body if b != 0x01)
        text = decode_payload(core, tbl, d)
        want = normalize_ko_text(row["ko"])
        have = normalize_ko_text(text).rstrip("\u3000")
        if want not in have and have != want:
            seed_fail.append({"abs": row["abs"], "got": text, "want": row["ko"]})

    # Opening gap guards (logical abs space)
    from monoeye_rom import stock_base

    gap_issues = []
    sb = stock_base(rom)

    def walk(lo: int, hi: int):
        off = sb + lo
        end = sb + hi
        rows = []
        while off < end:
            if rom[off] == 0:
                off += 1
                continue
            got = read_encoded_z_safe(rom, off)
            if got is None:
                off += 1
                continue
            payload, term = got
            pref, _body, kind = split_prefix_body(payload)
            logical = off - sb
            term_log = term - sb
            rows.append((logical, term_log, kind, pref))
            off = term + 1
        return rows

    rows = walk(0x604080, 0x6044A0)
    by_abs = {a: (t, k, p) for a, t, k, p in rows}
    for title in OPENING_TITLE_FACE:
        if title not in by_abs:
            continue
        # find next dialogue after title
        idx = next(i for i, r in enumerate(rows) if r[0] == title)
        if idx + 1 >= len(rows):
            continue
        na, _nt, nk, np = rows[idx + 1]
        t = by_abs[title][0]
        gap = na - (t + 1)
        if nk == "dialogue" and gap != 0:
            gap_issues.append(
                {
                    "abs": f"{title:06X}",
                    "next": f"{na:06X}",
                    "kind": "title_face",
                    "gap": gap,
                    "expect": 0,
                }
            )
        # control/speaker after a dialogue must be gap>=1 when stock-style
    for i, (a, t, kind, _pref) in enumerate(rows):
        if kind != "dialogue" or i + 1 >= len(rows):
            continue
        na, _nt, nk, _np = rows[i + 1]
        if nk not in ("control", "speaker"):
            continue
        gap = na - (t + 1)
        if gap == 0 and a >= 0x6040A0:
            gap_issues.append(
                {
                    "abs": f"{a:06X}",
                    "next": f"{na:06X}",
                    "kind": nk,
                    "gap": gap,
                    "expect": 1,
                }
            )

    return {
        "seed_lines": len(seed),
        "seed_fail": seed_fail,
        "seed_fail_count": len(seed_fail),
        "opening_gap_issues": gap_issues,
        "opening_gap_issue_count": len(gap_issues),
        "ok": len(seed_fail) == 0 and len(gap_issues) == 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
        help="Tip ROM (16MiB preferred)",
    )
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=None,
        help="Output ROM (default: --rom in-place)",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out/patch/hangul_patch_pad3.tbl",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_quality.json",
        help="Translation sheet (quality preferred)",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data/translations_seed_hook96.json",
    )
    ap.add_argument(
        "--meta",
        type=Path,
        default=ROOT / "out/patch/exp_dictionary_meta.json",
    )
    ap.add_argument(
        "--pointer-ref-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc",
        help="Pre-spill image for far-pointer classification",
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_marked.wsc",
        help="Baseline for event-body heuristics",
    )
    ap.add_argument(
        "--phases",
        default=",".join(DEFAULT_PHASES),
        help=f"Comma list. Default={','.join(DEFAULT_PHASES)}. All={','.join(ALL_PHASES)}",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Only classify + verify-read; do not write ROM",
    )
    ap.add_argument("--slots", type=int, default=265, help="ext/exp dict slot count")
    ap.add_argument(
        "--rank",
        choices=("freq", "early-abs", "hybrid-bands"),
        default="early-abs",
        help=(
            "seq_dict unique ranking: freq | early-abs | hybrid-bands "
            "(band slot budgets; preserves early_tut then fills later bands)"
        ),
    )
    ap.add_argument(
        "--band-early",
        type=int,
        default=None,
        help="hybrid-bands: slots for [6040A5,607000] (default 180)",
    )
    ap.add_argument(
        "--band-bank60-rest",
        type=int,
        default=None,
        help="hybrid-bands: slots for [607001,60FFFF] (default 50; rest→610000+)",
    )
    ap.add_argument(
        "--stock-reclaim",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Assign leftover uniques into *unreferenced* stock 5F slots "
            "(default off — prefer ext/dedicated opening slots)"
        ),
    )
    ap.add_argument(
        "--sole-reclaim",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "After seq_dict, sole-fit reclaim (default off; name75-aware but "
            "still prefer tools/apply_opening_dedicated.py for opening)"
        ),
    )
    ap.add_argument("--hangul-marker", default="E3DB")
    ap.add_argument("--tail-lo", type=lambda s: int(s, 16), default=0x6044A1)
    ap.add_argument("--tail-hi", type=lambda s: int(s, 16), default=0x60456A)
    ap.add_argument(
        "--opening-lo",
        type=lambda s: int(s, 16),
        default=0x6040A5,
        help="opening_dedicated window low (hex)",
    )
    ap.add_argument(
        "--opening-hi",
        type=lambda s: int(s, 16),
        default=0x607000,
        help="opening_dedicated window high (hex)",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/build_script_ko_report.json",
    )
    ap.add_argument(
        "--backup",
        action="store_true",
        help="Copy tip ROM to out/patch/monoeye_ko_expanded.pre_ep3.wsc first",
    )
    ap.add_argument(
        "--min-abs",
        type=lambda s: int(s, 16),
        default=None,
        help="Inclusive low logical abs (hex); filters sheet before phases",
    )
    ap.add_argument(
        "--max-abs",
        type=lambda s: int(s, 16),
        default=None,
        help="Inclusive high logical abs (hex); filters sheet before phases",
    )
    ap.add_argument(
        "--placement",
        choices=("free-space", "legacy"),
        default="free-space",
        help=(
            "free-space (default): KO payloads → bank30+ only; sole ptrs; "
            "no seq_dict inplace. legacy: exp_spill+seq_dict path."
        ),
    )
    ap.add_argument(
        "--max-ptr-hits",
        type=int,
        default=1,
        help="free-space: max segmented pointer hits per line (default 1=sole)",
    )
    ap.add_argument(
        "--allow-seq-dict",
        action="store_true",
        help="With free-space placement, also run seq_dict (unsafe; off by default)",
    )
    ap.add_argument(
        "--jp",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
        help="JP reference for free-space verify / dirty checks",
    )
    args = ap.parse_args()

    # Placement selects default phase set when user left default --phases.
    default_phase_csv = ",".join(DEFAULT_PHASES)
    if args.placement == "free-space" and args.phases == default_phase_csv:
        phases = list(FREE_SPACE_PHASES)
        if args.allow_seq_dict:
            # Insert before verify
            phases = [p for p in phases if p != "verify"] + ["seq_dict", "verify"]
    else:
        phases = _parse_phases(args.phases)
    if args.dry_run:
        phases = [p for p in phases if p in ("classify", "verify")]
        if "classify" not in phases:
            phases.insert(0, "classify")

    out_rom = args.out_rom or args.rom
    marker = int(args.hangul_marker, 16)

    if not args.rom.exists():
        print(f"missing ROM: {args.rom}", file=sys.stderr)
        return 1
    if not args.sheet.exists():
        print(f"missing sheet: {args.sheet}", file=sys.stderr)
        return 1

    if args.backup and not args.dry_run:
        bak = ROOT / "out/patch/monoeye_ko_expanded.pre_ep3.wsc"
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_bytes(args.rom.read_bytes())
        print(f"backup → {bak}")

    seed_rows = json.loads(args.seed.read_text(encoding="utf-8")).get("lines", [])
    seed_abs = {int(r["abs"], 16) for r in seed_rows}
    sheet = _load_sheet_lines(args.sheet)
    if args.min_abs is not None or args.max_abs is not None:
        lo = args.min_abs if args.min_abs is not None else 0
        hi = args.max_abs if args.max_abs is not None else 0xFFFFFF
        before = len(sheet)
        sheet = [
            row
            for row in sheet
            if row.get("abs") and lo <= int(row["abs"], 16) <= hi
        ]
        print(f"abs window {lo:06X}-{hi:06X}: sheet {before} → {len(sheet)}")
        # Persist filtered sheet so subprocess phases (seq_dict) see the same window.
        filtered_sheet = ROOT / "out/script/_build_script_ko_filtered.json"
        filtered_sheet.write_text(
            json.dumps(
                {
                    "description": f"build_script_ko filter {lo:06X}-{hi:06X}",
                    "min_abs": f"{lo:06X}",
                    "max_abs": f"{hi:06X}",
                    "lines": sheet,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        args.sheet = filtered_sheet

    ptr_rom = (
        args.pointer_ref_rom.read_bytes()
        if args.pointer_ref_rom.exists()
        else args.rom.read_bytes()
    )
    base_rom = (
        args.base_rom.read_bytes() if args.base_rom.exists() else args.rom.read_bytes()
    )

    report: Dict[str, Any] = {
        "tool": "build_script_ko",
        "placement": args.placement,
        "phases": phases,
        "dry_run": bool(args.dry_run),
        "rom": str(args.rom),
        "out_rom": str(out_rom),
        "sheet": str(args.sheet),
        "seed": str(args.seed),
        "min_abs": f"{args.min_abs:06X}" if args.min_abs is not None else None,
        "max_abs": f"{args.max_abs:06X}" if args.max_abs is not None else None,
        "results": {},
    }

    # Work copy — reload after subprocess phases that write the file.
    rom = bytearray(load_rom(args.rom))
    tbl = Tbl.load(args.tbl)

    if "classify" in phases:
        print("== classify ==")
        report["results"]["classify"] = classify_sheet(
            bytes(rom), ptr_rom, sheet, seed_abs, base_rom
        )
        c = report["results"]["classify"]["counts"]
        print(
            f"  pointer={c.get('pointer', 0)} sequential={c.get('sequential', 0)} "
            f"event={c.get('event', 0)} seed={c.get('seed', 0)}"
        )

    if "exp_spill" in phases and not args.dry_run:
        if args.placement == "free-space":
            print("== exp_spill SKIPPED (placement=free-space) ==")
        else:
            print("== exp_spill ==")
            report["results"]["exp_spill"] = phase_exp_spill(
                rom,
                tbl,
                args.sheet,
                hangul_marker=marker,
                min_abs=args.min_abs,
                max_abs=args.max_abs,
            )
            assert_jagd_guard(rom, where="exp_spill")
            update_ws_checksum(rom)
            out_rom.write_bytes(rom)
            r = report["results"]["exp_spill"]
            print(
                f"  relocated={r.get('relocated_records')} "
                f"ptr_fixes={r.get('pointer_fixes')} "
                f"skip_noptr={r.get('skipped_no_pointer_count')}"
            )

    if "free_space" in phases and not args.dry_run:
        print("== free_space ==")
        out_rom.write_bytes(rom)
        fs_report = ROOT / "out/patch/build_script_ko_free_space_report.json"
        report["results"]["free_space"] = phase_free_space(
            rom_path=out_rom,
            out_rom=out_rom,
            tbl_path=args.tbl,
            sheet_path=args.sheet,
            jp_path=args.jp,
            hangul_marker=args.hangul_marker,
            min_abs=args.min_abs,
            max_abs=args.max_abs,
            max_ptr_hits=args.max_ptr_hits,
            report_path=fs_report,
            backup=False,
        )
        rom = bytearray(load_rom(out_rom))
        r = report["results"]["free_space"]
        print(
            f"  relocated={r.get('relocated')} allowlist={r.get('pointer_allowlist_n')} "
            f"verify_ok={(r.get('verify') or {}).get('ok')} wrote={r.get('wrote_rom')} "
            f"rc={r.get('rc')}"
        )
        if r.get("rc"):
            print("  free_space phase failed — aborting further phases")
            args.out_report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            return 1

    if "seq_dict" in phases and not args.dry_run:
        if args.placement == "free-space" and not args.allow_seq_dict:
            print("== seq_dict SKIPPED (placement=free-space; pass --allow-seq-dict) ==")
        else:
            print("== seq_dict ==")
            # Ensure tip file matches current buffer before subprocess.
            out_rom.write_bytes(rom)
            seq_report = ROOT / "out/patch/build_script_ko_seq_dict_report.json"
            report["results"]["seq_dict"] = phase_seq_dict(
                rom_path=out_rom,
                out_rom=out_rom,
                tbl_path=args.tbl,
                sheet_path=args.sheet,
                seed_path=args.seed,
                meta_path=args.meta,
                pointer_ref=args.pointer_ref_rom
                if args.pointer_ref_rom.exists()
                else out_rom,
                base_rom=args.base_rom if args.base_rom.exists() else out_rom,
                slots=args.slots,
                report_path=seq_report,
                rank=args.rank,
                stock_reclaim=bool(args.stock_reclaim),
                sole_reclaim=bool(args.sole_reclaim),
                band_early=args.band_early,
                band_bank60_rest=args.band_bank60_rest,
            )
            rom = bytearray(load_rom(out_rom))
            r = report["results"]["seq_dict"]
            sole = r.get("sole_reclaim") or {}
            sole_n = sole.get("assigned", 0) if isinstance(sole, dict) else 0
            print(
                f"  assigned={r.get('assigned')} lines={r.get('assigned_lines')} "
                f"sole_reclaim={sole_n} "
                f"decode_fail={r.get('decode_fail')} seed_fail={r.get('seed_fail')}"
            )

    if "separators" in phases and not args.dry_run:
        print("== separators ==")
        report["results"]["separators"] = phase_separators(rom)
        update_ws_checksum(rom)
        out_rom.write_bytes(rom)
        print(f"  fixed={report['results']['separators']['fixed']}")

    if "opening_dedicated" in phases and not args.dry_run:
        print("== opening_dedicated ==")
        out_rom.write_bytes(rom)
        od_report = ROOT / "out/patch/build_script_ko_opening_dedicated_report.json"
        report["results"]["opening_dedicated"] = phase_opening_dedicated(
            rom_path=out_rom,
            out_rom=out_rom,
            tbl_path=args.tbl,
            sheet_path=args.sheet,
            seed_path=args.seed,
            meta_path=args.meta if args.meta.exists() else ROOT / "out/patch/exp_dictionary_meta.json",
            lo=args.opening_lo,
            hi=args.opening_hi,
            report_path=od_report,
            include_seed_abs=True,
        )
        rom = bytearray(load_rom(out_rom))
        r = report["results"]["opening_dedicated"]
        print(
            f"  lines={r.get('lines_patched')} plain={r.get('plain_inplace')} "
            f"reuse={r.get('reuse_existing')} full={r.get('full_line_tokens')} "
            f"skip={r.get('skipped_no_slot')}"
        )

    if "opening_tail" in phases and not args.dry_run:
        print("== opening_tail ==")
        out_rom.write_bytes(rom)
        tail_report = ROOT / "out/patch/build_script_ko_opening_tail_report.json"
        report["results"]["opening_tail"] = phase_opening_tail(
            rom_path=out_rom,
            out_rom=out_rom,
            tbl_path=args.tbl,
            sheet_path=args.sheet,
            seed_path=args.seed,
            lo=args.tail_lo,
            hi=args.tail_hi,
            report_path=tail_report,
        )
        rom = bytearray(load_rom(out_rom))
        r = report["results"]["opening_tail"]
        print(
            f"  applied={r.get('applied_count')} skipped={r.get('skipped_count')} "
            f"fail={r.get('decode_failures')}"
        )

    if "verify" in phases:
        print("== verify ==")
        # Prefer on-disk out_rom after writes
        vrom = bytes(load_rom(out_rom if out_rom.exists() else args.rom))
        report["results"]["verify"] = phase_verify(vrom, tbl, args.seed, args.meta)
        v = report["results"]["verify"]
        print(
            f"  seed_fail={v['seed_fail_count']} "
            f"gap_issues={v['opening_gap_issue_count']} ok={v['ok']}"
        )
        if args.placement == "free-space" and not v.get("ok"):
            # Clean 60–69←JP wipes seed Hangul; free-space skips seed_protect abs.
            # Contract verify is allowlist (free_space phase), not seed decode.
            v["ok_for_placement"] = True
            v["soft_fail_note"] = (
                "seed/gap checks are informational under --placement free-space"
            )
            print("  note: free-space — seed/gap verify is soft-fail only")

    if not args.dry_run and out_rom.exists():
        final = bytearray(load_rom(out_rom))
        assert_jagd_guard(final, where="final write")
        report["checksum"] = f"{update_ws_checksum(final):04X}"
        if phases and phases[-1] not in ("classify",):
            out_rom.write_bytes(final)

    args.out_report.parent.mkdir(parents=True, exist_ok=True)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"report → {args.out_report}")

    # free-space success: allowlist verify (+ opening body allow after that phase)
    fs = report["results"].get("free_space") or {}
    od = report["results"].get("opening_dedicated") or {}
    if args.placement == "free-space":
        if fs.get("rc"):
            return 1
        # Re-verify with pointer allowlist ∪ opening body abs
        from verify_script_banks_allowlist import verify_script_banks_allowlist

        tip_bytes = bytes(load_rom(out_rom))
        jp_bytes = load_rom(args.jp) if args.jp.exists() else tip_bytes
        allow = []
        fs_rep = Path(fs.get("report") or ROOT / "out/patch/build_script_ko_free_space_report.json")
        if fs_rep.exists():
            full = json.loads(fs_rep.read_text(encoding="utf-8"))
            allow = [int(x, 16) for x in full.get("pointer_allowlist") or []]
        body = [int(x, 16) for x in (od.get("body_abs") or [])]
        final_v = verify_script_banks_allowlist(
            tip_bytes, jp_bytes, allowlist_logical=allow, body_abs=body
        )
        report["results"]["allowlist_final"] = final_v
        print(
            f"== allowlist_final == ok={final_v.get('ok')} "
            f"diffs={final_v.get('diff_bytes_60_69')} illegal={final_v.get('illegal_diff_count')}"
        )
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 0 if final_v.get("ok") else 1

    verify = report["results"].get("verify")
    if verify and not verify.get("ok"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
