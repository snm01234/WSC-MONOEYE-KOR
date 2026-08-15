#!/usr/bin/env python3
"""
Measure how much NON-dialogue text is actually Korean on a target ROM.

Non-dialogue = the surfaces the dialogue sheet never covers: intermission and
battle menus, HUD labels, help text, unit (기체) names, weapon (무장) names.

READ-ONLY. This tool never opens a .wsc for writing.

Four sections:

(1) catalogs — every ``data/*_ko.json`` term is resolved to stock dictionary
    indices by exact ``expand_index`` match on the ORIGINAL ROM, then the same
    index is read on the target and classified ko / jp / other / missing_jp.
    ``missing_jp`` means the term has no exact stock slot at all, so the catalog
    row can never apply through the dictionary path (weapon full names are all
    like this — they live in the bank-75 table, not the dictionary).

(2) name75 — the unit/weapon display table (``expand_dictionary.NAME75_RANGES``)
    walked as zstrings and rendered with the target dictionary. This is what the
    player reads on the unit and weapon screens.

(3) ui_facing_slots — the headline number. Stock dictionary indices that have at
    least one consumer in aux (``AUX_TOKEN_BANKS`` = 50–5F + 76) or name75, i.e.
    slots the menus / HUD / unit tables actually read, classified ko / jp on the
    target. Consumers are scanned on the ORIGINAL, per docs/DICT_INVASION_GUARD.md
    (a work ROM can hide consumers behind a broken terminator).

(4) marker_health — payload marker audit. A dictionary payload carrying the
    RETIRED marker is a defect: that code is a real character (``E3DB`` = ``映``)
    so it renders as that character and never raises the Hangul run flag.

The dictionary is built ext3-aware (``make_dictionary_ext3``). Without the ext3
meta every ``E5 18`` token expands to ``<BADDICT>`` and Korean text is miscounted
as untranslated — the bug that made measure_band_coverage.py report 4.7%.

Report: ``out/patch/nondialogue_ko_audit.json``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import (  # noqa: E402
    AUX_TOKEN_BANKS,
    NAME75_RANGES,
    _walk_zstring_range,
    build_dict_token_locs,
)
from hangul_marker import marker_code, marker_pair  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    SEG_DICT,
    Dictionary,
    Tbl,
    find_rom,
    load_rom,
)

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_META = ROOT / "out/patch/ext_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/nondialogue_ko_audit.json"

# Retired marker. Kept as a literal ON PURPOSE: this is the value we are
# detecting as a defect, not a value we write. The installed marker always
# comes from hangul_marker.marker_code().
RETIRED_MARKER = 0xE3DB

# Catalogs consumed by tools/run_ui_localize.py, in apply order.
CATALOGS: Sequence[str] = (
    "unit_names_ko",
    "weapon_names_ko",
    "ui_system_ko",
    "ui_battle_terms_ko",
    "ui_menu_terms_ko",
    "ui_menu_terms2_ko",
    "ui_menu_terms3_ko",
    "ui_mined_terms_ko",
    # Keep in sync with run_ui_localize.py. A missing name here does not error —
    # the catalog is silently absent from the coverage report, which is how
    # ui_proper_nouns_ko went unmeasured. scan_fragment_composition_hazard.py
    # had the same defect and now discovers catalogs instead.
    "ui_proper_nouns_ko",
)

HANGUL = re.compile(r"[\uac00-\ud7a3]")
JAPANESE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

MAX_LISTED = 60


def classify(text: str) -> str:
    """ko if any Hangul, jp if any kana/kanji, else neutral (digits/latin/punct)."""
    if HANGUL.search(text):
        return "ko"
    if JAPANESE.search(text):
        return "jp"
    return "neutral"


def _pct(num: int, den: int) -> float:
    return round(num / den * 100, 2) if den else 0.0


# --- (1) catalogs ------------------------------------------------------------


def index_base_terms(d_base: Dictionary, tbl: Tbl) -> Dict[str, List[int]]:
    """Exact stock phrase → every index holding it, measured on the original."""
    out: Dict[str, List[int]] = {}
    for idx in range(d_base.count):
        out.setdefault(d_base.expand_index(idx, tbl), []).append(idx)
    return out


def audit_catalogs(
    d_base: Dictionary,
    d_tgt: Dictionary,
    tbl: Tbl,
    by_phrase: Dict[str, List[int]],
) -> dict:
    per_catalog: Dict[str, dict] = {}
    totals = {"ko": 0, "jp": 0, "other": 0, "missing_jp": 0}
    still_jp: List[dict] = []

    for name in CATALOGS:
        path = ROOT / f"data/{name}.json"
        if not path.exists():
            per_catalog[name] = {"error": "catalog missing"}
            continue
        spec = json.loads(path.read_text(encoding="utf-8"))
        rows = list(spec.get("entries") or []) + list(spec.get("fragments") or [])
        counts = {"ko": 0, "jp": 0, "other": 0, "missing_jp": 0}
        for row in rows:
            jp, ko = row.get("jp"), row.get("ko")
            if not jp or not ko:
                continue
            idxs = by_phrase.get(jp) or []
            if not idxs:
                counts["missing_jp"] += 1
                continue
            for idx in idxs:
                got = d_tgt.expand_index(idx, tbl)
                if HANGUL.search(got):
                    counts["ko"] += 1
                elif got == jp:
                    counts["jp"] += 1
                    if len(still_jp) < MAX_LISTED:
                        still_jp.append(
                            {
                                "catalog": name,
                                "index": f"{idx:04X}",
                                "jp": jp,
                                "ko": ko,
                            }
                        )
                else:
                    counts["other"] += 1
        applicable = counts["ko"] + counts["jp"] + counts["other"]
        per_catalog[name] = {
            "rows": len(rows),
            **counts,
            "applicable_slots": applicable,
            "ko_pct": _pct(counts["ko"], applicable),
        }
        for key in totals:
            totals[key] += counts[key]

    applicable = totals["ko"] + totals["jp"] + totals["other"]
    return {
        "per_catalog": per_catalog,
        "totals": totals,
        "applicable_slots": applicable,
        "ko_pct": _pct(totals["ko"], applicable),
        "still_jp_sample": still_jp,
    }


# --- (2) name75 -------------------------------------------------------------


def audit_name75(target: bytes, d_tgt: Dictionary, tbl: Tbl) -> dict:
    counts = {"ko": 0, "jp": 0, "neutral": 0}
    jp_sample: List[dict] = []
    total = 0
    for lo, hi in NAME75_RANGES:
        for logical, payload, _kind in _walk_zstring_range(
            target, lo, hi, region="name75", max_len=64
        ):
            total += 1
            try:
                text = d_tgt.expand(payload, tbl)
            except Exception:
                continue
            kind = classify(text)
            counts[kind] += 1
            if kind == "jp" and len(jp_sample) < MAX_LISTED:
                jp_sample.append({"site": f"{logical:06X}", "jp": text[:40]})
    textual = counts["ko"] + counts["jp"]
    return {
        "ranges": [f"{lo:06X}-{hi:06X}" for lo, hi in NAME75_RANGES],
        "records": total,
        **counts,
        "textual_records": textual,
        "ko_pct": _pct(counts["ko"], textual),
        "still_jp_sample": jp_sample,
    }


# --- (3) UI-facing dictionary slots -----------------------------------------


def audit_ui_facing_slots(
    original: bytes,
    d_base: Dictionary,
    d_tgt: Dictionary,
    tbl: Tbl,
) -> dict:
    """Slots the menus / HUD / unit tables read, per consumers on the ORIGINAL."""
    locs = build_dict_token_locs(original)
    counts = {"ko": 0, "jp": 0, "neutral": 0}
    ranked: List[dict] = []
    for idx in range(d_base.count):
        refs = locs.get(idx) or []
        aux_refs = [r for r in refs if r.region != "script"]
        if not aux_refs:
            continue
        jp = d_base.expand_index(idx, tbl)
        got = d_tgt.expand_index(idx, tbl)
        kind = classify(got)
        counts[kind] += 1
        if kind == "jp":
            ranked.append(
                {
                    "index": f"{idx:04X}",
                    "jp": jp,
                    "consumers": len(aux_refs),
                    "regions": sorted({r.region for r in aux_refs}),
                }
            )
    ranked.sort(key=lambda r: -r["consumers"])
    textual = counts["ko"] + counts["jp"]
    return {
        "consumer_scan_rom": "original",
        "aux_banks": [f"{s:02X}" for s in AUX_TOKEN_BANKS],
        "ui_facing_slots": counts["ko"] + counts["jp"] + counts["neutral"],
        **counts,
        "textual_slots": textual,
        "ko_pct": _pct(counts["ko"], textual),
        "top_still_jp": ranked[:MAX_LISTED],
        "still_jp_total": len(ranked),
    }


# --- (4) marker health ------------------------------------------------------


def audit_marker_health(target: bytes, d_tgt: Dictionary) -> dict:
    installed = marker_code()
    good = bytes(marker_pair())
    bad = bytes([RETIRED_MARKER >> 8, RETIRED_MARKER & 0xFF])
    n_good = n_bad = 0
    bad_sample: List[str] = []
    stock = Dictionary(target).count
    for idx in range(stock):
        raw = d_tgt.raw_entry(idx)
        if good in raw:
            n_good += 1
        if bad in raw:
            n_bad += 1
            if len(bad_sample) < MAX_LISTED:
                bad_sample.append(f"{idx:04X}")
    return {
        "installed_marker": f"{installed:04X}",
        "retired_marker": f"{RETIRED_MARKER:04X}",
        "stock_slots_scanned": stock,
        "slots_with_installed_marker": n_good,
        "slots_with_retired_marker": n_bad,
        "retired_marker_slots": bad_sample,
        "ok": n_bad == 0,
        "note": (
            "A payload carrying the retired marker renders that marker as a real "
            "character and never raises the Hangul run flag."
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_TARGET, help="target ROM")
    ap.add_argument("--base-rom", type=Path, default=None, help="default: original")
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--skip-consumer-scan",
        action="store_true",
        help="skip section (3); it walks 16 script banks + aux and costs ~1 min",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this audit is read-only")

    base_path = args.base_rom or find_rom(ROOT)
    original = bytes(load_rom(base_path))
    target = bytes(load_rom(args.rom))
    tbl = Tbl.load(args.tbl)
    meta = load_ext_meta(args.meta)
    meta3 = load_ext_meta(args.ext3_meta)

    d_base = Dictionary(original)
    d_tgt = make_dictionary_ext3(target, meta, meta3)

    by_phrase = index_base_terms(d_base, tbl)
    cat = audit_catalogs(d_base, d_tgt, tbl, by_phrase)
    n75 = audit_name75(target, d_tgt, tbl)
    mk = audit_marker_health(target, d_tgt)
    ui = (
        {"skipped": True}
        if args.skip_consumer_scan
        else audit_ui_facing_slots(original, d_base, d_tgt, tbl)
    )

    report = {
        "generated_by": "tools/audit_nondialogue_ko.py",
        "read_only": True,
        "original": str(base_path),
        "target": str(args.rom),
        "tbl": str(args.tbl),
        "ext3_banks": getattr(d_tgt, "ext3_banks", 0),
        "catalogs": cat,
        "name75": n75,
        "ui_facing_slots": ui,
        "marker_health": mk,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        print(f"target : {args.rom}")
        print(f"ext3   : {report['ext3_banks']} bank(s) wired")
        print(
            f"marker : installed {mk['installed_marker']} | "
            f"slots with it {mk['slots_with_installed_marker']} | "
            f"retired {mk['retired_marker']} present in "
            f"{mk['slots_with_retired_marker']} → "
            f"{'ok' if mk['ok'] else 'DEFECT'}"
        )
        print(
            f"catalogs   : {cat['totals']['ko']} KO / {cat['applicable_slots']} "
            f"applicable slots ({cat['ko_pct']}%) | jp {cat['totals']['jp']} "
            f"other {cat['totals']['other']} missing_jp {cat['totals']['missing_jp']}"
        )
        for name, row in cat["per_catalog"].items():
            if "error" in row:
                print(f"    {name:22s} {row['error']}")
                continue
            print(
                f"    {name:22s} rows={row['rows']:4d} KO={row['ko']:4d} "
                f"JP={row['jp']:4d} other={row['other']:4d} "
                f"missing_jp={row['missing_jp']:4d} → {row['ko_pct']}%"
            )
        print(
            f"name75     : {n75['ko']} KO / {n75['textual_records']} textual "
            f"records ({n75['ko_pct']}%) of {n75['records']} walked"
        )
        if not args.skip_consumer_scan:
            print(
                f"ui_facing  : {ui['ko']} KO / {ui['textual_slots']} textual "
                f"UI-facing slots ({ui['ko_pct']}%) | still JP "
                f"{ui['still_jp_total']}"
            )
            for row in ui["top_still_jp"][:12]:
                print(
                    f"    {row['index']} x{row['consumers']:<4d} "
                    f"{row['regions']} {row['jp'][:30]}"
                )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
