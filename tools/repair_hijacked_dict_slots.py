#!/usr/bin/env python3
"""
Restore stock dictionary slots that were re-pointed without a beneficiary.

Measured example: slot ``0B57`` holds ``００７９`` in the original and is read by
eight opening-narration date records (``60:419F``, ``60:4239``, ``60:42C9``,
``60:42FC``, ``60:4354``, ``60:4380``, ``60:43AA``, ``60:43E1``). It was re-pointed
to the Korean phrase ``후후후……``, so those records now render
``후후후……．１．３`` where the year belongs.

``repair_dict5f_pointers.py --mode unreferenced`` cannot catch this: the slot *does*
have script consumers, so it looks like a translated dialogue slot. The distinction
is whether the re-point has a **beneficiary** — a record that was rewritten to point
at the slot in order to show that Korean text. A hijack has none: every consumer in
the target is also a consumer in the original, i.e. the slot only ever served stock
strings and now feeds them the wrong phrase.

Selection (all three must hold):

1. the slot's pointer differs from the original,
2. the target phrase contains Hangul while the original phrase does not,
3. the target's consumer set is a subset of the original's — no new consumer was
   added, and
4. **no consumer's sheet KO matches the slot's Korean text.** Condition 3 alone is
   not enough: when a record's body is just one token, re-pointing that token's slot
   *is* the translation mechanism, so the beneficiary is the existing consumer. For
   example slot ``014D`` carries ``연방에 기운다。`` and its only consumer ``60:4333``
   has exactly that KO in the sheet — legitimate, leave it. Slot ``0B57`` carries
   ``후후후……`` while its consumers are date records whose KO is nothing like it —
   a hijack.

Restoring is then free: the stock strings get their phrase back and no Hangul is
lost. Only slots whose original phrase bytes are still intact at the original
offset are touched, so a restore can never point a slot at garbage.

``--dry-run`` is the default; ``--commit`` backs the target up first.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import DEFAULT_REF_REGIONS, build_dict_token_locs  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_END,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    le16,
    load_rom,
    stock_base,
    update_ws_checksum,
    ws_header,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/hijacked_dict_slots.json"
BACKUP_ROOT = ROOT / "out/patch/backup"


def has_hangul(text: str | None) -> bool:
    return bool(text and any("가" <= c <= "힣" for c in text))


def phrase_at(rom: bytes, off: int, limit: int = 256) -> bytes:
    base = stock_base(rom) + SEG_DICT * BANK_SIZE
    end = off
    while end < BANK_SIZE and end - off < limit and rom[base + end] != 0:
        end += 1
    return bytes(rom[base + off : base + end])


def load_sheet_ko(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = data["lines"] if isinstance(data, dict) else data
    return {r["abs"].upper(): (r.get("ko") or "") for r in lines}


def _norm(text: str | None) -> str:
    return (text or "").replace("\u3000", "").replace(" ", "").strip()


def _ko_matches(sheet: str | None, slot_text: str) -> bool:
    """Does a consumer's sheet KO account for the slot's Korean text?

    The slot usually holds the whole line, but a line can also be assembled from a
    prefix plus the slot, so containment either way counts as a match.
    """
    a, b = _norm(sheet), _norm(slot_text)
    if not a or not b:
        return False
    return a == b or b in a or a in b


def consumer_sets(rom: bytes) -> Dict[int, Set[int]]:
    locs = build_dict_token_locs(rom, regions=DEFAULT_REF_REGIONS)
    return {idx: {r.abs for r in refs} for idx, refs in locs.items() if refs}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--out-rom", type=Path, default=None, help="write a copy instead")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="default")
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_apply_all.json",
        help="apply sheet, used to tell a translation re-point from a hijack",
    )
    ap.add_argument("--tbl-jp", type=Path, default=ROOT / "data/monoeye.tbl")
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    args = ap.parse_args(argv)

    if args.commit and args.dry_run:
        raise SystemExit("--commit and --dry-run are mutually exclusive")
    for p in (args.jp, args.target):
        if not p.exists():
            raise SystemExit(f"missing ROM: {p}")

    jp = bytes(load_rom(args.jp))
    rom = bytearray(load_rom(args.target))
    sj, st = stock_base(jp), stock_base(rom)
    tbl_jp = Tbl.load(args.tbl_jp)
    tbl = Tbl.load(args.tbl)

    d_jp = Dictionary(jp)
    d_tgt = make_dictionary_ext3(
        bytes(rom),
        load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
        load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
    )
    jp_cons = consumer_sets(jp)
    tgt_cons = consumer_sets(bytes(rom))

    n = (DICT_PTR_END - DICT_PTR_START + 1) // 2
    ptr_base_jp = sj + SEG_DICT * BANK_SIZE + DICT_PTR_START
    ptr_base_tg = st + SEG_DICT * BANK_SIZE + DICT_PTR_START

    sheet_ko = load_sheet_ko(args.sheet)
    hijacked: List[dict] = []
    legitimate: List[dict] = []
    skipped_no_beneficiary_check: List[dict] = []
    for idx in range(n):
        p_jp = le16(jp, ptr_base_jp + idx * 2)
        p_tg = le16(rom, ptr_base_tg + idx * 2)
        if p_jp == p_tg:
            continue
        try:
            t_jp = d_jp.expand_index(idx, tbl_jp)
            t_tg = d_tgt.expand_index(idx, tbl)
        except Exception:  # pragma: no cover - informational
            continue
        if not has_hangul(t_tg) or has_hangul(t_jp):
            continue
        orig_c = jp_cons.get(idx, set())
        tgt_c = tgt_cons.get(idx, set())
        if not tgt_c or not tgt_c.issubset(orig_c):
            continue  # a new consumer depends on the Korean text
        beneficiary = next(
            (
                f"{a >> 16:02X}:{a & 0xFFFF:04X}"
                for a in sorted(tgt_c)
                if _ko_matches(sheet_ko.get(f"{a:06X}"), t_tg)
            ),
            None,
        )
        if beneficiary:
            legitimate.append(
                {
                    "index": f"{idx:04X}",
                    "text": t_tg,
                    "beneficiary": beneficiary,
                    "note": "a consumer's sheet KO matches the slot text — this "
                    "re-point is the translation, not a hijack",
                }
            )
            continue
        if phrase_at(jp, p_jp) != phrase_at(bytes(rom), p_jp):
            skipped_no_beneficiary_check.append(
                {"index": f"{idx:04X}", "reason": "original phrase no longer intact"}
            )
            continue
        hijacked.append(
            {
                "index": f"{idx:04X}",
                "ptr_original": f"{p_jp:04X}",
                "ptr_target": f"{p_tg:04X}",
                "text_original": t_jp,
                "text_target": t_tg,
                "consumers": [f"{a >> 16:02X}:{a & 0xFFFF:04X}" for a in sorted(tgt_c)],
            }
        )

    for row in hijacked:
        idx = int(row["index"], 16)
        want = int(row["ptr_original"], 16)
        at = ptr_base_tg + idx * 2
        rom[at] = want & 0xFF
        rom[at + 1] = (want >> 8) & 0xFF

    checksum_before = f"{ws_header(load_rom(args.target))['checksum']:04X}"
    dest = None
    backup = None
    checksum_after = None
    if hijacked and (args.commit or args.out_rom):
        if args.out_rom:
            dest = args.out_rom
            dest.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest = args.target
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            (BACKUP_ROOT / stamp).mkdir(parents=True, exist_ok=True)
            backup = BACKUP_ROOT / stamp / args.target.name
            shutil.copy2(args.target, backup)
        checksum_after = f"{update_ws_checksum(rom):04X}"
        dest.write_bytes(rom)

    report = {
        "ok": True,
        "generated_by": "tools/repair_hijacked_dict_slots.py",
        "mode": "commit" if args.commit else ("copy" if args.out_rom else "dry-run"),
        "criteria": [
            "pointer differs from the original",
            "target phrase has Hangul, original phrase does not",
            "target consumer set is a subset of the original's (no beneficiary)",
            "original phrase bytes still intact at the original offset",
        ],
        "target": str(args.target),
        "wrote": str(dest) if dest else None,
        "backup": str(backup) if backup else None,
        "revert": f"copy {backup} back" if backup else "no in-place write performed",
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "restored": len(hijacked),
        "left_alone_legitimate": legitimate,
        "skipped": skipped_no_beneficiary_check,
        "slots": hijacked,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"hijacked slots without a beneficiary: {len(hijacked)}")
    for row in hijacked:
        print(
            f"  {row['index']}  {row['text_target']!r} → {row['text_original']!r}  "
            f"consumers {', '.join(row['consumers'][:8])}"
        )
    for row in skipped_no_beneficiary_check:
        print(f"  skipped {row['index']}: {row['reason']}")
    if dest:
        print(f"wrote {dest}  checksum {checksum_before} → {checksum_after}")
        if backup:
            print(f"backup {backup}")
    elif hijacked:
        print("dry-run: nothing written. Use --out-rom or --commit.")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
