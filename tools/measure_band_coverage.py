#!/usr/bin/env python3
"""Measure ep3-window band exact-match coverage (pointer-aware).

Sequential lines: decode at original logical abs.
Pointer/spillable lines: follow tip far-pointer target, then decode.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import (  # noqa: E402
    _file_abs,
    load_ext_meta,
    make_dictionary_ext3,
    spillable_abs_set,
)
from extract_script import split_prefix_body  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    is_expanded_rom,
    load_rom,
    logical_bank_offset,
    read_encoded_z_safe,
    stock_base,
)
from normalize_ko_text import (  # noqa: E402
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)
from rebuild_script_banks import (  # noqa: E402
    MAX_SPILL_POINTER_HITS,
    SEGMENTED_POINTER_KINDS,
    discover_pointer_hits,
    _hit_logical_bank,
)

BANDS = [
    ("opening", 0x6040A5, 0x60456A),
    ("early_tut", 0x60456B, 0x607000),
    ("bank60_rest", 0x607001, 0x60FFFF),
    ("bank61", 0x610000, 0x61FFFF),
    ("bank62", 0x620000, 0x62FFFF),
]
MARKER = marker_code()


def is_expansion_seg(seg: int) -> bool:
    return 0x30 <= seg <= 0x4F


def target_file_abs(rom: bytes | bytearray, logical_abs: int) -> int:
    seg = (logical_abs >> 16) & 0xFF
    off = logical_abs & 0xFFFF
    if is_expanded_rom(rom) and is_expansion_seg(seg):
        return logical_bank_offset(seg, off)
    return _file_abs(rom, logical_abs)


def decode_at(rom: bytes | bytearray, file_abs: int, tbl: Tbl, d) -> Optional[str]:
    got = read_encoded_z_safe(rom, file_abs)
    if got is None:
        return None
    _pref, body, _kind = split_prefix_body(got[0])
    # Keep SPACE pads — stripping them can truncate dict expansion (<TRUNC:FF>).
    return d.expand(body, tbl)


def want_expand(ko: str, tbl: Tbl, d) -> Optional[str]:
    enc = try_encode_ko_text(
        ko, tbl, hangul_marker_code=MARKER, hangul_marker_mode="run"
    )
    if enc is None:
        return None
    return d.expand(enc, tbl).rstrip("\u3000")


def read_pointer_target(rom: bytes | bytearray, hit) -> Optional[Tuple[int, int]]:
    if hit.kind == "off16_seg8":
        off = rom[hit.abs_at] | (rom[hit.abs_at + 1] << 8)
        seg = rom[hit.abs_at + 2]
        return seg, off
    if hit.kind == "off16_00_seg8":
        off = rom[hit.abs_at] | (rom[hit.abs_at + 1] << 8)
        seg = rom[hit.abs_at + 3]
        return seg, off
    if hit.kind == "seg8_off16":
        seg = rom[hit.abs_at]
        off = rom[hit.abs_at + 1] | (rom[hit.abs_at + 2] << 8)
        return seg, off
    return None


def build_hits_by_off(rom: bytes | bytearray, abs_list: set[int]) -> Dict[int, list]:
    by_seg: Dict[int, set[int]] = defaultdict(set)
    for logical in abs_list:
        seg = (logical >> 16) & 0xFF
        if 0x60 <= seg <= 0x6F:
            by_seg[seg].add(logical & 0xFFFF)
    out: Dict[int, list] = defaultdict(list)
    for segment, offs in by_seg.items():
        hits = discover_pointer_hits(rom, segment, offs)
        hits = [
            h
            for h in hits
            if 0x50 <= _hit_logical_bank(rom, h.abs_at) <= 0x6F
            and _hit_logical_bank(rom, h.abs_at) != 0x5F
        ]
        grouped: Dict[int, list] = defaultdict(list)
        for h in hits:
            grouped[h.old_off].append(h)
        for off, off_hits in grouped.items():
            if len(off_hits) > MAX_SPILL_POINTER_HITS:
                continue
            if not any(h.kind in SEGMENTED_POINTER_KINDS for h in off_hits):
                continue
            out[(segment << 16) | off] = off_hits
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--pointer-ref-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_ep3_window.json",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out/patch/hangul_patch_pad3.tbl",
    )
    ap.add_argument(
        "--meta",
        type=Path,
        default=ROOT / "out/patch/exp_dictionary_meta.json",
    )
    ap.add_argument(
        "--ext3-meta",
        type=Path,
        default=ROOT / "out/patch/ext3_dictionary_meta.json",
        help="ext3 bank meta; without it E5 18 tokens expand to <BADDICT> and "
        "every ext3-encoded line is miscounted as untranslated",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out/patch/coverage_hybrid_bands_summary.json",
    )
    ap.add_argument(
        "--seq-dict-report",
        type=Path,
        default=ROOT / "out/patch/build_script_ko_seq_dict_report.json",
    )
    args = ap.parse_args()

    tip = load_rom(args.rom)
    ptr = args.pointer_ref_rom.read_bytes()
    tbl = Tbl.load(args.tbl)
    meta = load_ext_meta(args.meta)
    meta3 = load_ext_meta(args.ext3_meta)
    d = make_dictionary_ext3(tip, meta, meta3)
    lines = json.loads(args.sheet.read_text(encoding="utf-8"))["lines"]

    quality: List[Tuple[int, str]] = []
    for row in lines:
        abs_s = row.get("abs")
        if not abs_s:
            continue
        abs_off = int(abs_s, 16)
        ko = normalize_ko_text(row.get("ko") or "")
        if not ko or is_low_quality_ko(ko):
            continue
        quality.append((abs_off, ko))

    abs_set = {a for a, _ in quality}
    spillable = spillable_abs_set(ptr, sorted(abs_set))
    hits_by = build_hits_by_off(ptr, set(spillable))
    tip_sb = stock_base(tip)
    ptr_sb = stock_base(ptr)

    def resolve_target(logical_abs: int) -> Tuple[int, bool]:
        """Return (decode_logical_abs, via_ptr)."""
        if logical_abs not in spillable:
            return logical_abs, False
        for h in hits_by.get(logical_abs, []):
            if h.kind not in SEGMENTED_POINTER_KINDS:
                continue
            tip_abs = tip_sb + (h.abs_at - ptr_sb)
            adapted = type(h)(tip_abs, h.kind, h.segment, h.old_off, h.stride)
            parsed = read_pointer_target(tip, adapted)
            if not parsed:
                continue
            seg, off = parsed
            return (seg << 16) | off, True
        return logical_abs, True

    band_stats: Dict[str, Dict[str, Any]] = {
        name: {
            "quality": 0,
            "ok": 0,
            "jp": 0,
            "ratio": 0.0,
            "via_ptr": 0,
            "via_seq": 0,
        }
        for name, _lo, _hi in BANDS
    }

    for abs_off, ko in quality:
        band_name = None
        for name, lo, hi in BANDS:
            if lo <= abs_off <= hi:
                band_name = name
                break
        if band_name is None:
            continue
        st = band_stats[band_name]
        st["quality"] += 1
        target, via_ptr = resolve_target(abs_off)
        if via_ptr:
            st["via_ptr"] += 1
        else:
            st["via_seq"] += 1
        file_off = target_file_abs(tip, target)
        got = decode_at(tip, file_off, tbl, d)
        want = want_expand(ko, tbl, d)
        got_n = (got or "").rstrip("\u3000")
        ok = got_n == ko or (want is not None and got_n == want)
        if ok:
            st["ok"] += 1
        else:
            st["jp"] += 1

    for st in band_stats.values():
        q = st["quality"]
        st["ratio"] = round(st["ok"] / q, 4) if q else 0.0

    bank30_nonzero = 0
    if is_expanded_rom(tip):
        bank30 = tip[0x30 * 0x10000 : 0x31 * 0x10000]
        bank30_nonzero = sum(1 for b in bank30 if b != 0xFF)

    seq_unique = None
    seq_lines = None
    pool = None
    sole = None
    if args.seq_dict_report.exists():
        rep = json.loads(args.seq_dict_report.read_text(encoding="utf-8"))
        write = rep.get("write") or {}
        pool = rep.get("pool")
        sole = write.get("sole_reclaim") or pool.get("sole_reclaim") if pool else None
        seq_unique = write.get("written")
        if pool and "unique_assigned" in pool:
            seq_unique = pool.get("unique_assigned")
        seq_lines = pool.get("lines_covered_by_assign") if pool else None

    sole_bands: Dict[str, Any] = {}
    total_sole = 0
    for label, path in (
        ("bank60_rest", ROOT / "out/patch/sole_reclaim_bank60_rest.json"),
        ("bank61", ROOT / "out/patch/sole_reclaim_bank61.json"),
        ("bank62", ROOT / "out/patch/sole_reclaim_bank62.json"),
    ):
        if not path.exists():
            continue
        r = json.loads(path.read_text(encoding="utf-8"))
        n = int(r.get("assigned") or 0)
        sole_bands[label] = n
        total_sole += n
    if sole_bands:
        sole_bands["total_lines"] = total_sole
        sole_bands["note"] = "post hybrid-bands mode1 sole-reclaim on tip"

    out = {
        "dictionary": {
            "ext_in_expansion": bool(meta.get("ext_in_expansion")),
            "ext3_banks": d.ext3_banks,
            "ext3_seg": f"{d.ext3_seg:02X}" if d.ext3_banks else None,
            "note": "ext3 banks must be wired in or every E5 18 token expands to "
            "<BADDICT> and its line is counted as untranslated",
        },
        "bands": band_stats,
        "seq_dict": {
            "unique": seq_unique,
            "lines": seq_lines,
            "pool": pool,
        },
        "sole": sole,
        "bank30_nonzero": bank30_nonzero,
        "sole_bands_applied": sole_bands or None,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("band coverage (pointer-aware exact):")
    for name, _lo, _hi in BANDS:
        st = band_stats[name]
        print(
            f"  {name:12} ok={st['ok']:4}/{st['quality']:<4} "
            f"ratio={st['ratio']:.1%}  ptr={st['via_ptr']} seq={st['via_seq']}"
        )
    print(f"bank30_nonzero={bank30_nonzero}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
