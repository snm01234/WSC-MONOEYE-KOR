#!/usr/bin/env python3
"""
Scan tip ROM for dictionary-slot invasion / cross-talk.

Flags slots where a Hangul payload is consumed by multiple external sites that
disagree on expected KO (sheet), or where early-band KO leaks into late/aux/UI.

Does not modify the ROM.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from expand_dictionary import build_dict_token_locs  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    stock_base,
)
from normalize_ko_text import normalize_ko_text  # noqa: E402
from patch_ext_dictionary import STOCK_DICT_COUNT  # noqa: E402

MARKER = 0xE3DB
EARLY_LO, EARLY_HI = 0x6040A5, 0x607000
BANK60_REST_HI = 0x60FFFF
EP3_HI = 0x62FFFF


def band_of(abs_off: int) -> str:
    if EARLY_LO <= abs_off <= EARLY_HI:
        return "early"
    if 0x607001 <= abs_off <= BANK60_REST_HI:
        return "bank60_rest"
    if 0x610000 <= abs_off <= 0x61FFFF:
        return "bank61"
    if 0x620000 <= abs_off <= EP3_HI:
        return "bank62"
    if abs_off > EP3_HI:
        return "late_out"
    if abs_off < EARLY_LO:
        return "pre_opening"
    return "other"


def payload_has_hangul_marker(payload: bytes, marker: int = MARKER) -> bool:
    hi, lo = (marker >> 8) & 0xFF, marker & 0xFF
    i = 0
    while i < len(payload) - 1:
        if payload[i] == hi and payload[i + 1] == lo:
            return True
        i += 1
    return False


def load_sheet_ko(path: Path) -> Dict[int, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = data["lines"] if isinstance(data, dict) else data
    out: Dict[int, str] = {}
    for row in lines:
        abs_s = row.get("abs")
        ko = normalize_ko_text(row.get("ko") or "")
        if not abs_s or not ko:
            continue
        out[int(abs_s, 16)] = ko
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc"
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc",
        help="Reference for stock-slot JP baseline (8MB tip)",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_quality_all.json",
    )
    ap.add_argument(
        "--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl"
    )
    ap.add_argument(
        "--meta", type=Path, default=ROOT / "out/patch/exp_dictionary_meta.json"
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out/patch/dict_invasion_scan.json",
    )
    ap.add_argument("--max-examples", type=int, default=40)
    args = ap.parse_args()

    rom = load_rom(args.rom)
    base = load_rom(args.base_rom) if args.base_rom.exists() else None
    tbl = Tbl.load(args.tbl)
    meta = load_ext_meta(args.meta) if args.meta.exists() else {}
    d = make_dictionary(rom, meta) if meta else Dictionary(rom)
    d_base = Dictionary(base) if base is not None else None
    stock = int(meta.get("stock_count", STOCK_DICT_COUNT)) if meta else STOCK_DICT_COUNT
    sheet_ko = load_sheet_ko(args.sheet)

    print("scanning dict token locs (script+name75+aux)...")
    locs = build_dict_token_locs(rom, regions=("script", "name75", "aux"))

    findings: List[dict] = []
    class_counts: Counter[str] = Counter()

    for idx, refs in locs.items():
        if not refs:
            continue
        try:
            raw = d.raw_entry(idx)
        except Exception:
            continue
        if not raw or not payload_has_hangul_marker(raw):
            continue
        try:
            tip_text = d.expand(raw, tbl).rstrip("\u3000")
        except Exception:
            tip_text = ""
        if not tip_text or not any("\uac00" <= c <= "\ud7a3" for c in tip_text):
            continue

        base_raw = b""
        base_text = ""
        base_hangul = False
        if d_base is not None and idx < d_base.count:
            try:
                base_raw = d_base.raw_entry(idx)
                base_hangul = payload_has_hangul_marker(base_raw)
                if base_raw:
                    base_text = d_base.expand(base_raw, tbl).rstrip("\u3000")
            except Exception:
                pass

        regions = Counter(r.region for r in refs)
        bands = Counter(
            band_of(r.abs) for r in refs if r.region == "script"
        )
        script_refs = [r for r in refs if r.region == "script"]
        aux_refs = [r for r in refs if r.region == "aux"]
        name_refs = [r for r in refs if r.region == "name75"]

        # Expected KO from sheet at each script consumer.
        expected: Dict[str, List[str]] = defaultdict(list)
        disagree = 0
        for r in script_refs:
            want = sheet_ko.get(r.abs)
            if want is None:
                continue
            if want != tip_text:
                disagree += 1
                expected[want].append(f"{r.abs:06X}")

        early_script = bands.get("early", 0)
        late_script = (
            bands.get("bank61", 0)
            + bands.get("bank62", 0)
            + bands.get("late_out", 0)
            + bands.get("bank60_rest", 0)
        )

        kinds: List[str] = []
        # A) Early KO also used outside early (cross-band leak)
        if early_script and late_script and len(refs) > 1:
            kinds.append("early_ko_cross_band")
        # B) KO slot also hit by aux/UI
        if aux_refs or name_refs:
            kinds.append("ko_shared_with_aux_or_name75")
        # C) Multi script consumers disagree with tip vs sheet
        if disagree >= 1 and len(script_refs) >= 2:
            kinds.append("sheet_consumer_mismatch")
        # D) Stock slot: tip Hangul but base was short JP (sole-style overwrite residue)
        if (
            idx < stock
            and d_base is not None
            and base_raw
            and not base_hangul
            and len(raw) > len(base_raw) + 4
            and len(refs) > 1
        ):
            kinds.append("stock_sole_style_overwrite")
        # E) Multi-ref KO unique (intentional shared phrase) — only flag if mismatch
        if len(refs) > 1 and not kinds:
            if disagree == 0:
                kinds.append("shared_ok_unanimous")
            else:
                kinds.append("shared_ambiguous")

        # Only report problematic classes (not shared_ok)
        bad = [k for k in kinds if k != "shared_ok_unanimous"]
        if not bad:
            class_counts["shared_ok_unanimous"] += 1
            continue

        for k in bad:
            class_counts[k] += 1

        findings.append(
            {
                "dict_index": idx,
                "ext": idx >= stock,
                "tip_ko": tip_text[:80],
                "base_ko_or_jp": (base_text[:80] if base_text else ""),
                "base_hangul": base_hangul,
                "tip_len": len(raw),
                "base_len": len(base_raw) if base_raw else 0,
                "ref_count": len(refs),
                "regions": dict(regions),
                "script_bands": dict(bands),
                "kinds": bad,
                "sheet_mismatch_consumers": disagree,
                "mismatch_examples": {
                    k: v[:5] for k, v in list(expected.items())[:6]
                },
                "sample_refs": [
                    {
                        "abs": f"{r.abs:06X}",
                        "region": r.region,
                        "kind": r.kind,
                        "band": band_of(r.abs) if r.region == "script" else r.region,
                        "sheet_ko": (sheet_ko.get(r.abs) or "")[:40],
                    }
                    for r in refs[:12]
                ],
            }
        )

    # Severity ranking
    severity = {
        "stock_sole_style_overwrite": 0,
        "early_ko_cross_band": 1,
        "sheet_consumer_mismatch": 2,
        "ko_shared_with_aux_or_name75": 3,
        "shared_ambiguous": 4,
    }

    def sort_key(f: dict) -> Tuple:
        kinds = f["kinds"]
        sev = min(severity.get(k, 9) for k in kinds)
        return (sev, -f["ref_count"], f["dict_index"])

    findings.sort(key=sort_key)

    # Summary: early text appearing at non-early script abs (decode check)
    early_leak_sites: List[dict] = []
    for f in findings:
        if "early_ko_cross_band" not in f["kinds"]:
            continue
        tip = f["tip_ko"]
        for ref in f["sample_refs"]:
            if ref["region"] != "script":
                continue
            if ref["band"] == "early":
                continue
            early_leak_sites.append(
                {
                    "dict_index": f["dict_index"],
                    "consumer_abs": ref["abs"],
                    "band": ref["band"],
                    "slot_ko": tip[:60],
                    "sheet_ko": ref["sheet_ko"],
                }
            )

    report = {
        "rom": str(args.rom),
        "base_rom": str(args.base_rom) if base is not None else None,
        "sheet": str(args.sheet),
        "stock_count": stock,
        "dict_count": d.count,
        "class_counts": dict(class_counts),
        "problem_slots": len(findings),
        "early_cross_band_leak_samples": early_leak_sites[: args.max_examples],
        "findings": findings[: args.max_examples * 3],
        "findings_truncated": max(0, len(findings) - args.max_examples * 3),
        "all_problem_indices": [f["dict_index"] for f in findings],
    }

    # Fix ko_slots count properly
    ko_n = 0
    for idx, refs in locs.items():
        if not refs:
            continue
        try:
            if payload_has_hangul_marker(d.raw_entry(idx)):
                ko_n += 1
        except Exception:
            continue
    report["ko_slots_scanned_with_refs"] = ko_n

    # High-signal subsets
    report["counts"] = {
        "stock_sole_style": sum(
            1 for f in findings if "stock_sole_style_overwrite" in f["kinds"]
        ),
        "early_cross_band": sum(
            1 for f in findings if "early_ko_cross_band" in f["kinds"]
        ),
        "sheet_mismatch": sum(
            1 for f in findings if "sheet_consumer_mismatch" in f["kinds"]
        ),
        "aux_or_name75_share": sum(
            1 for f in findings if "ko_shared_with_aux_or_name75" in f["kinds"]
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"ko_slots={ko_n} problems={len(findings)} "
        f"sole_style={report['counts']['stock_sole_style']} "
        f"early_xband={report['counts']['early_cross_band']} "
        f"sheet_mis={report['counts']['sheet_mismatch']} "
        f"aux_share={report['counts']['aux_or_name75_share']} "
        f"→ {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
