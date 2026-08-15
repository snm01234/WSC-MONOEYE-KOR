#!/usr/bin/env python3
"""
Restore stock 5F dictionary pointers against the original (req 2.6, 3.2, 3.10).

The stock dictionary is shared by dialogue, by the intermission / battle-HUD /
help zstrings in the aux banks (``50–5F``, ``76``) and by the name75 tables.
Re-pointing an index changes the text for *every* consumer, so an index that a
non-dialogue record reads must keep its original pointer (design hypothesis A3).

This tool restores individual pointers only. A full pointer-table rebuild is
forbidden by requirement 3.10 and is not implemented here.

Modes (``--mode``):

* ``non-dialogue`` — restore only indices with an aux / name75 consumer in the
  original. Minimal blast radius; leaves dialogue-only re-points alone.
* ``unreferenced`` — restore every changed pointer that no dialogue record in the
  target still reads. This is the mode to use after the lines have been re-homed
  onto private ext3 slots (``apply_3byte_seq_ko.py --rehome-stock-dict``): the
  index is dead as far as dialogue is concerned, so handing it back costs no
  Hangul and moves the table toward parity with the original.
* ``all`` — restore every changed pointer whose original phrase is still intact
  at its original offset. Reaches full parity with the original table, but
  reverts any dialogue still reading a re-pointed slot — check ``unreferenced``
  first.
* ``gate-min`` — restore every non-dialogue-consumed index, then top up with the
  *fewest* extra pointers needed to reach the requirement 3.10 floor
  (``--gate-min``, default 3,802 / 3,831). Top-up order is least-damaging first:
  candidates are ranked by how much translated dialogue the restore reverts,
  measured on the TARGET (``build_dict_token_locs(target, regions=("script",))``),
  and indices an opening-band record reads (``6040A5`` / ``6040B5`` / ``6040CB``,
  requirement 3.1) are ranked last so they survive when the floor can be met
  without them. Both the restored set and the unrestored keep-set land in the
  report, so the selection rule is auditable.

Only pointers whose original phrase bytes are still present at the original
offset are ever restored, so a restore can never point an index at garbage. The
phrases the target added stay in the ROM; they simply become unreferenced.

``--dry-run`` is the default. ``--commit`` writes in place after backing the
target up to ``out/patch/backup/<timestamp>/``; ``--out`` writes an experiment
copy elsewhere and never touches the target. Both update the header checksum.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_dict5f_pointer_invasion import (  # noqa: E402
    PTR_GATE_MIN,
    analyze,
    pointer_count,
    ptr_base,
)
from monoeye_rom import load_rom, update_ws_checksum, ws_header  # noqa: E402

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_PRE = ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_REPORT = ROOT / "out/patch/repair_dict5f_pointers_report.json"
BACKUP_ROOT = ROOT / "out/patch/backup"

MODES = ("non-dialogue", "all", "unreferenced", "gate-min")

# Opening narration band (requirement 3.1). The three documented records plus a
# small margin, so a record start that shifted by a byte is still recognised.
OPENING_BAND = (0x604000, 0x6041FF)
OPENING_ABS = (0x6040A5, 0x6040B5, 0x6040CB)


def script_ref_locs(target: Path) -> dict:
    """dict index → dialogue reference sites (logical abs) in the target ROM."""
    from expand_dictionary import build_dict_token_locs  # noqa: E402

    rom = bytes(load_rom(target))
    locs = build_dict_token_locs(rom, regions=("script",))
    return {idx: [r.abs for r in refs] for idx, refs in locs.items() if refs}


def script_consumers(target: Path) -> set[int]:
    """Dictionary indices still read by a dialogue record in the target ROM.

    Used by ``--mode unreferenced``: once a line has been re-homed onto a private
    ext3 slot it no longer reads its old stock index, so that index can be handed
    back to the original table without reverting any Hangul.
    """
    return set(script_ref_locs(target))


def _restorable(analysis: dict) -> List[dict]:
    return [
        c
        for c in analysis["changed_all"]
        if c["original_phrase_intact_at_original_offset"]
    ]


def select_gate_min(
    analysis: dict, target: Path, gate_min: int
) -> tuple[List[dict], List[dict], dict]:
    """Mandatory non-dialogue restores + the fewest extra restores to hit the floor.

    Returns ``(picks, kept, meta)``. ``kept`` are the restorable pointers left at
    their target value, i.e. the mismatches the floor still allows.
    """
    restorable = _restorable(analysis)
    mandatory = [c for c in restorable if c["classification"].startswith("must_restore")]
    optional = [
        c for c in restorable if not c["classification"].startswith("must_restore")
    ]
    locs = script_ref_locs(target)
    lo, hi = OPENING_BAND

    for c in optional:
        refs = locs.get(c["index_dec"], [])
        opening = [a for a in refs if lo <= a <= hi]
        c["_target_script_refs"] = len(refs)
        c["_opening_refs"] = len(opening)
        c["_opening_sites"] = [f"{a:06X}" for a in sorted(set(opening))[:8]]

    # least damaging first: no opening use, then fewest dialogue references.
    optional.sort(
        key=lambda c: (
            1 if c["_opening_refs"] else 0,
            c["_target_script_refs"],
            c["index_dec"],
        )
    )

    need = gate_min - (analysis["match"]["now"] + len(mandatory))
    take = optional[: max(0, need)]
    kept = optional[len(take) :]
    picks = mandatory + take
    meta = {
        "selection_rule": (
            "mandatory = every restorable index with an aux/name75 consumer; "
            "top-up = fewest additional pointers to reach the floor, ordered by "
            "(opening_band_reference asc, target dialogue reference count asc, "
            "index asc) so the restores that revert the least Hangul — and none "
            "of the opening band if avoidable — are taken first"
        ),
        "gate_min": gate_min,
        "mandatory_non_dialogue": len(mandatory),
        "topup_needed": max(0, need),
        "topup_taken": len(take),
        "optional_candidates": len(optional),
        "kept_unrestored": len(kept),
        "opening_band": [f"{lo:06X}", f"{hi:06X}"],
        "opening_records": [f"{a:06X}" for a in OPENING_ABS],
        "topup_opening_hits": sum(1 for c in take if c["_opening_refs"]),
        "kept_detail": [
            {
                "index": c["index"],
                "ptr_original": c["ptr_original"],
                "ptr_target": c["ptr_target"],
                "target_script_refs": c["_target_script_refs"],
                "opening_refs": c["_opening_refs"],
                "opening_sites": c["_opening_sites"],
            }
            for c in kept
        ],
        "topup_detail": [
            {
                "index": c["index"],
                "target_script_refs": c["_target_script_refs"],
                "opening_refs": c["_opening_refs"],
            }
            for c in take
        ],
    }
    return picks, kept, meta


def select(analysis: dict, mode: str, *, target: Path | None = None) -> List[dict]:
    """Changed pointers to restore, filtered by mode and by restorability."""
    changed = analysis["changed_all"]
    if len(changed) != analysis["match"]["changed"]:
        raise SystemExit(
            f"analysis changed_all has {len(changed)} entries but reports "
            f"{analysis['match']['changed']} changed pointers"
        )
    live: set[int] = set()
    if mode == "unreferenced":
        if target is None:
            raise SystemExit("--mode unreferenced needs the target ROM")
        live = script_consumers(target)

    out: List[dict] = []
    for c in changed:
        if not c["original_phrase_intact_at_original_offset"]:
            continue
        if mode == "non-dialogue" and not c["classification"].startswith("must_restore"):
            continue
        if mode == "unreferenced" and c["index_dec"] in live:
            continue
        out.append(c)
    return out


def apply_pointers(rom: bytearray, picks: Sequence[dict]) -> List[dict]:
    base = ptr_base(rom)
    done: List[dict] = []
    for c in picks:
        idx = c["index_dec"]
        want = int(c["ptr_original"], 16)
        at = base + idx * 2
        before = rom[at] | (rom[at + 1] << 8)
        rom[at] = want & 0xFF
        rom[at + 1] = (want >> 8) & 0xFF
        done.append(
            {
                "index": c["index"],
                "ptr_before": f"{before:04X}",
                "ptr_after": f"{want:04X}",
                "classification": c["classification"],
                "consumer_regions": c["consumer_regions"],
                "changed_by": c["changed_by"],
            }
        )
    return done


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--pre", type=Path, default=DEFAULT_PRE)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--mode", choices=MODES, default="non-dialogue")
    ap.add_argument(
        "--gate-min",
        type=int,
        default=PTR_GATE_MIN,
        help=f"pointer match floor for --mode gate-min (default {PTR_GATE_MIN})",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write an experiment copy here instead of modifying the target",
    )
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--tbl", type=Path, default=ROOT / "data/monoeye.tbl")
    ap.add_argument("--commit", action="store_true", help="write the target in place")
    ap.add_argument("--dry-run", action="store_true", help="explicit no-write (default)")
    args = ap.parse_args(argv)

    if args.commit and args.dry_run:
        raise SystemExit("--commit and --dry-run are mutually exclusive")
    if args.commit and args.out:
        raise SystemExit("--commit writes the target; --out writes a copy — pick one")
    if args.report.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a report over a .wsc")
    if "_8mb" in args.jp.name:
        raise SystemExit(f"reference must be the ORIGINAL 8 MiB ROM, not {args.jp.name}")
    for p in (args.jp, args.pre, args.target):
        if not p.exists():
            raise SystemExit(f"missing input ROM: {p}")

    analysis = analyze(args.jp, args.pre, args.target, tbl_path=args.tbl)
    gate_meta: dict | None = None
    if args.mode == "gate-min":
        picks, _kept, gate_meta = select_gate_min(analysis, args.target, args.gate_min)
    else:
        picks = select(analysis, args.mode, target=args.target)

    n = pointer_count()
    match_before = analysis["match"]["now"]
    match_after = match_before + len(picks)

    rom = bytearray(load_rom(args.target))
    checksum_before = f"{ws_header(rom)['checksum']:04X}"
    done = apply_pointers(rom, picks)

    dest: Path | None = None
    backup: Path | None = None
    checksum_after = None
    if args.commit or args.out:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if args.commit:
            dest = args.target
            (BACKUP_ROOT / stamp).mkdir(parents=True, exist_ok=True)
            backup = BACKUP_ROOT / stamp / args.target.name
            shutil.copy2(args.target, backup)
        else:
            dest = args.out
            dest.parent.mkdir(parents=True, exist_ok=True)
        checksum_after = f"{update_ws_checksum(rom):04X}"
        dest.write_bytes(rom)

    report = {
        "ok": True,
        "generated_by": "tools/repair_dict5f_pointers.py",
        "mode": args.mode,
        "wrote": str(dest) if dest else None,
        "in_place": bool(args.commit),
        "backup": str(backup) if backup else None,
        "revert": (
            f"copy {backup} back over {args.target}"
            if backup
            else "no in-place write performed"
        ),
        "original": str(args.jp),
        "target": str(args.target),
        "rebuild_forbidden": "individual pointers only — full table rebuild is "
        "forbidden by requirement 3.10",
        "pointer_count": n,
        "gate_min_match": PTR_GATE_MIN,
        "match_before": match_before,
        "restored": len(picks),
        "match_after": match_after,
        "gate_ok_after": match_after >= PTR_GATE_MIN,
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "classification_counts": analysis["classification_counts"],
        "gate_min_selection": gate_meta,
        "restored_pointers": done,
    }
    if gate_meta and match_after < args.gate_min:
        report["ok"] = False
        report["gate_note"] = (
            "not enough restorable pointers to reach the floor — "
            f"best reachable is {match_after}/{n}"
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"target      : {args.target}")
    print(f"mode        : {args.mode}")
    print(f"match       : {match_before}/{n} → {match_after}/{n} "
          f"(restored {len(picks)}, gate min {PTR_GATE_MIN}) "
          f"→ {'ok' if match_after >= PTR_GATE_MIN else 'BELOW GATE'}")
    if gate_meta:
        print(
            f"gate-min    : mandatory {gate_meta['mandatory_non_dialogue']} + "
            f"top-up {gate_meta['topup_taken']}/{gate_meta['optional_candidates']} "
            f"(needed {gate_meta['topup_needed']}), kept "
            f"{gate_meta['kept_unrestored']} unrestored, "
            f"opening-band restores {gate_meta['topup_opening_hits']}"
        )
    if dest:
        print(f"wrote       : {dest}")
        if backup:
            print(f"backup      : {backup}")
        print(f"checksum    : {checksum_before} → {checksum_after}")
    else:
        print("dry-run: nothing written. Use --out for an experiment copy or "
              "--commit to write the target.")
    print(f"→ {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
