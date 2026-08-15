#!/usr/bin/env python3
"""
Patch opening narration interstitial lines skipped by the early seed.

Root cause:
  Records like 6040B5 use prefix ``08 xx 01 17 xx 18`` + JP body.
  Old split_prefix_body left ``01 17 xx 18`` in the body, so the sheet
  showed garbage ``がらこ…`` and the seed never targeted these abs.
  On screen the title (6040A5) was KO while the next line (6040B5) stayed JP.

DEPRECATED — do not use for new work.
  This tool localizes by writing Korean phrases into stock ``5F`` dictionary
  slots. That table is shared: the intermission menus, the battle HUD, the help
  screens and the name75 unit/weapon tables expand the same indices, so a slot
  taken here leaks Korean into them and corrupts the non-dialogue UI.

  The opening now goes through the same path as every other dialogue band —
  private ext3 slots in the expansion banks::

      python tools/apply_3byte_seq_ko.py --lo 0x6040A5 --hi 0x60456A \
          --num-banks 16 --rehome-stock-dict

  ``--rehome-stock-dict`` also moves records that are *already* Korean through a
  stock slot onto ext3, and ``tools/repair_dict5f_pointers.py --mode
  unreferenced`` then hands the freed stock slots back to the original table.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_translations import find_unused_dictionary_slots  # noqa: E402
from expand_dictionary import write_dictionary_slots_spill  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import encode_ko_text, normalize_ko_text  # noqa: E402

# (abs, jp, ko) — jp verified against original ROM after prefix fix.
OPENING_INTERSTITIALS: List[Tuple[str, str, str]] = [
    ("6040B5", "ジオン・ズム・ダイクンの主導により", "지온　즘　다이쿤의　주도로"),
    ("6040DD", "ここに、ジオン共和国が成立した", "여기에、지온　공화국이　성립했다"),
    ("604121", "デギン・ザビ首相が初代公王に就任。", "데긴　자비　수상이　초대　공왕에　취임。"),
    ("6041B5", "ジオン公国は、", "지온　공국은、"),
    ("6041D8", "わずか１週間でサイド１、２、４を", "불과　１주일　만에　사이드　１、２、４를"),
    ("6041FB", "続く『ルウム戦役』において", "이은　『루움　전역』에서"),
    ("604251", "続く３ヶ月にて、公国軍は", "이은　３개월에、공국군은"),
    ("60427F", "しかし、急速な戦線の拡大により、", "그러나　전선이　커져、"),
    ("604317", "３日間に渡る激戦の末、連邦軍が勝利。", "３일　격전　끝、연방군이　승리。"),
    ("6043F5", "ジオン公国は", "지온　공국은"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out/patch/hangul_patch.tbl",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data/translations_seed_hook96.json",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/opening_interstitial_report.json",
    )
    ap.add_argument("--hangul-marker", default="E3DB")
    ap.add_argument(
        "--ref-rom",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
        help="pristine ORIGINAL 8 MiB ROM; slots must be unused in BOTH images",
    )
    ap.add_argument(
        "--i-know-this-touches-the-shared-dictionary",
        action="store_true",
        help="required: this tool localizes by taking stock 5F slots. Prefer "
        "apply_3byte_seq_ko.py --lo 0x6040A5 --hi 0x60456A --rehome-stock-dict, "
        "which puts the opening on private ext3 slots like every other band.",
    )
    args = ap.parse_args()
    if not args.i_know_this_touches_the_shared_dictionary:
        raise SystemExit(
            "refusing to run: this tool localizes the opening by taking stock 5F "
            "dictionary slots, which the intermission menus, the battle HUD, the "
            "help screens and the name75 tables also read. Use\n"
            "  python tools/apply_3byte_seq_ko.py --lo 0x6040A5 --hi 0x60456A "
            "--rehome-stock-dict\n"
            "to put the opening on private ext3 slots instead. Pass "
            "--i-know-this-touches-the-shared-dictionary to override."
        )

    marker = int(args.hangul_marker, 16)
    rom = bytearray(load_rom(args.rom))
    tbl = Tbl.load(args.tbl)
    d = Dictionary(rom)

    # DEPRECATED PATH — see the module docstring. Kept for history/repro only.
    #
    # Consumers are enumerated on the pristine ORIGINAL ROM as well as the work
    # ROM: a corrupted aux zstring terminator in the work ROM hides the records
    # behind it, so a slot the intermission / HUD / help text reads can look
    # unused and get taken. The union is fail-closed.
    unused = set(find_unused_dictionary_slots(rom, d))
    if args.ref_rom.exists():
        ref = bytearray(load_rom(args.ref_rom))
        unused &= set(find_unused_dictionary_slots(ref, Dictionary(ref)))
    unused = sorted(unused)
    if len(unused) < len(OPENING_INTERSTITIALS):
        print(
            f"warning: only {len(unused)} unused slots for "
            f"{len(OPENING_INTERSTITIALS)} lines; remaining stay JP "
            f"(will not reclaim sole slots - that breaks other dialogue)"
        )
    pool = list(unused)

    slot_payload: Dict[int, bytes] = {}
    plan = []
    skipped = []
    for abs_s, jp, ko in OPENING_INTERSTITIALS:
        abs_off = int(abs_s, 16)
        ko_n = normalize_ko_text(ko)
        enc = encode_ko_text(
            ko_n, tbl, hangul_marker_code=marker, hangul_marker_mode="each"
        )
        original, _ = read_encoded_z(rom, abs_off)
        prefix, body, kind = split_prefix_body(original)
        if kind != "dialogue" or len(body) < 2:
            raise RuntimeError(f"@{abs_s} not a dialogue body (kind={kind})")
        if not pool:
            skipped.append({"abs": abs_s, "jp": jp, "ko": ko_n, "reason": "no_unused_slot"})
            continue
        idx = pool.pop(0)
        slot_payload[idx] = enc
        plan.append(
            {
                "abs": abs_s,
                "jp": jp,
                "ko": ko_n,
                "dict_index": idx,
                "prefix_hex": prefix.hex(),
                "old_body_len": len(body),
            }
        )

    write_dictionary_slots_spill(rom, slot_payload)
    d2 = Dictionary(rom)

    applied = []
    for row in plan:
        abs_off = int(row["abs"], 16)
        original, _ = read_encoded_z(rom, abs_off)
        prefix, body, _ = split_prefix_body(original)
        token = token_from_dict_index(row["dict_index"])
        new_payload = bytearray(prefix) + bytearray(token)
        pad = len(original) - len(new_payload)
        if pad < 0:
            raise RuntimeError(f"@{row['abs']} prefix+token too long")
        new_payload.extend(b"\x01" * pad)  # not NUL — preserves next sequential line
        rom[abs_off : abs_off + len(original)] = new_payload
        got = d2.expand_index(row["dict_index"], tbl)
        ok = got == row["ko"]
        row["ok"] = ok
        row["decode"] = got
        row["pad"] = pad
        applied.append(row)
        if not ok:
            raise RuntimeError(f"@{row['abs']} decode mismatch: {got!r}")

    # Keep seed JSON in sync (append missing abs).
    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    have = {row["abs"].upper() for row in seed["lines"]}
    added = 0
    for row in applied:
        if row["abs"].upper() in have:
            continue
        seed["lines"].append(
            {"abs": row["abs"], "jp": row["jp"], "ko": row["ko"]}
        )
        added += 1
    seed["lines"].sort(key=lambda r: int(r["abs"], 16))
    seed["description"] = (
        "Opening~pre-stage1 dialogue including interstitial narration "
        "(08 xx 01 17 xx 18 bodies). KO uses each-marker encoding."
    )
    args.seed.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report = {
        "applied": applied,
        "skipped": skipped,
        "unused_available": len(unused),
        "seed_lines_added": added,
        "seed_total": len(seed["lines"]),
        "checksum": f"{update_ws_checksum(rom):04X}",
    }
    args.out_rom.write_bytes(rom)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Opening interstitials OK | n={len(applied)} "
        f"seed_added={added} checksum={report['checksum']}"
    )
    for row in applied:
        print(f"  {row['abs']} [{row['dict_index']:04X}] {row['ko']}")
    print(f"Wrote {args.out_rom}")
    print(f"Wrote {args.out_report}")
    print(f"Updated {args.seed}")


if __name__ == "__main__":
    main()
