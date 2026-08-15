#!/usr/bin/env python3
"""
Localize the opening narration window with guard-approved stock 5F slots.

Why a dedicated path: the opening window (``0x6040A5``–``0x604570``) is not
rendered through the ext3 hook. Measured on this lineage, that window carried 0
ext3 tokens while every later window already carried hundreds of working ones
(early_tut 436, bank60 1616, bank61 2872, bank62 2576). Writing a 4-byte
``E5 18 xx yy`` token there leaves it undecoded and the trailing ``0x01`` padding
is walked as event opcodes — the in-game "event error 257 / 2049" on new game.

So the window needs a **2-byte** token. Those can only address indices 0..4095,
and the bank10 extension is therefore hard-capped at ``4096 - stock_count`` = 265
slots, all of which are in use. That leaves the stock ``5F`` table — the shared
one — so every slot taken here must clear both guard conditions
(dict-invasion-guard, fail closed):

1. **no aux (50-5F, 76) or name75 consumer in the ORIGINAL ROM.** Those are the
   intermission / battle-HUD / help / unit-label records that a Hangul story
   phrase would poison. Enumerated on the pristine original, because a corrupted
   zstring terminator in a work ROM hides the records behind it.
2. **no remaining consumer of any region in the target.** Every former consumer
   must already be retargeted — in practice re-homed onto a private ext3 slot by
   ``apply_3byte_seq_ko.py --rehome-stock-dict`` — before the slot is overwritten.

A record is also refused when ``looks_like_event_body`` flags its original body,
so an event record is never turned into token + padding.

``--dry-run`` is the default. ``--commit`` backs the target up first and updates
the checksum.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from apply_safe_unit import padded_token_payload  # noqa: E402
from event_record_heuristics import looks_like_event_body  # noqa: E402
from expand_dictionary import (  # noqa: E402
    DEFAULT_REF_REGIONS,
    build_dict_token_locs,
    iter_dict_indices,
    write_dictionary_slots_spill,
    _walk_zstring_range,
)
from extract_script import split_prefix_body  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    ws_header,
)
from normalize_ko_text import (  # noqa: E402
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_SHEET = ROOT / "out/script/translations_apply_all.json"
DEFAULT_SEED = ROOT / "data/translations_seed_hook96.json"
DEFAULT_REPORT = ROOT / "out/patch/opening_safe_slots_report.json"
BACKUP_ROOT = ROOT / "out/patch/backup"

# The window whose renderer does not go through the ext3 hook.
OPENING_LO = 0x6040A5
OPENING_HI = 0x604570
# The final opening-tail records are renderer/width-sensitive when shortened
# to a stock token plus 0x01 padding. Keep them Japanese until a dedicated
# width-safe path has been manually verified.
OPENING_TAIL_FALLBACK_LO = 0x604556

# Bank-75 UI label table (system/battle/title). Below NAME75_RANGES; omitting it
# let opening spill overwrite 07B6「명중」via 75B411 (2026-08-04).
UI_TABLE_RANGES = ((0x75B000, 0x75C000),)

HANGUL_MARKER = marker_code()
TOKEN_LEN = 2
MAX_PAD = 32


def _hangul(text: str | None) -> bool:
    return bool(text and any("가" <= c <= "힣" for c in text))


def safe_slots(jp: bytes, tgt: bytes, stock_count: int) -> List[int]:
    """Stock indices clearing both guard conditions (see module docstring)."""
    def reachable(rom: bytes, seeds: set[int]) -> set[int]:
        """Close external references over nested stock dictionary entries.

        A non-dialogue record often references a stock phrase which in turn
        references more stock phrases. Looking only at the record's direct
        token misses those children and lets an opening rewrite poison the
        non-dialogue consumer indirectly.
        """
        dictionary = Dictionary(rom)
        seen = set(seeds)
        pending = list(seen)
        while pending:
            index = pending.pop()
            if not 0 <= index < dictionary.count:
                continue
            for child in iter_dict_indices(dictionary.raw_entry(index)):
                if 0 <= child < dictionary.count and child not in seen:
                    seen.add(child)
                    pending.append(child)
        return seen

    def ui_table_seeds(rom: bytes) -> set[int]:
        seeds: set[int] = set()
        for lo, hi in UI_TABLE_RANGES:
            for _logical, payload, _kind in _walk_zstring_range(
                rom, lo, hi, region="name75_ui", max_len=64
            ):
                for index in iter_dict_indices(payload):
                    if 0 <= index < stock_count:
                        seeds.add(index)
        return seeds

    # Aux/name75 seeds must be closed through the original dictionary: a
    # non-dialogue record may reach a stock slot through a nested phrase.
    jp_locs = build_dict_token_locs(jp, regions=("aux", "name75"))
    tgt_locs = build_dict_token_locs(tgt, regions=DEFAULT_REF_REGIONS)
    aux_touched = reachable(jp, set(jp_locs) | ui_table_seeds(jp))
    live = reachable(tgt, set(tgt_locs) | ui_table_seeds(tgt))
    return [i for i in range(stock_count) if i not in aux_touched and i not in live]


def load_sheet(path: Path, seed_path: Path | None = None) -> Dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = data["lines"] if isinstance(data, dict) else data
    out = {r["abs"].upper(): (r.get("ko") or "") for r in lines}
    # The opening seed contains interstitial records that are intentionally
    # absent from the large apply sheet (for example 604251/604317). Merge it
    # as a fallback so an existing E518 record is not left in the opening
    # window merely because its translation lives in the seed catalog.
    if seed_path is not None and seed_path.exists():
        seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
        seed_lines = seed_data["lines"] if isinstance(seed_data, dict) else seed_data
        for row in seed_lines:
            abs_hex = str(row.get("abs") or "").upper()
            if abs_hex and abs_hex not in out:
                out[abs_hex] = row.get("ko") or ""
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument(
        "--seed",
        type=Path,
        default=DEFAULT_SEED,
        help="opening seed fallback; rows absent from --sheet are merged",
    )
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=OPENING_LO)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=OPENING_HI)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="default")
    args = ap.parse_args(argv)

    if args.commit and args.dry_run:
        raise SystemExit("--commit and --dry-run are mutually exclusive")
    if args.report.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a report over a .wsc")
    for p in (args.jp, args.target, args.sheet, args.tbl):
        if not p.exists():
            raise SystemExit(f"missing input: {p}")

    jp = bytes(load_rom(args.jp))
    rom = bytearray(load_rom(args.target))
    sj, st = stock_base(jp), stock_base(rom)
    tbl = Tbl.load(args.tbl)
    stock_count = Dictionary(jp).stock_count
    ko_by_abs = load_sheet(args.sheet, args.seed)

    d_now = make_dictionary_ext3(
        rom,
        load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
        load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
    )
    pool = safe_slots(jp, bytes(rom), stock_count)

    plan: List[dict] = []
    refused: List[dict] = []
    cursor = args.lo
    while cursor <= args.hi:
        got_jp = read_encoded_z_safe(jp, sj + cursor, max_len=256)
        if not got_jp:
            cursor += 1
            continue
        n = (got_jp[1] - sj) - cursor + 1
        nxt = (got_jp[1] - sj) + 1
        original = bytes(rom[st + cursor : st + cursor + n - 1])
        prefix, body, kind = split_prefix_body(original)
        site = f"{cursor:06X}"
        cursor = nxt

        if OPENING_TAIL_FALLBACK_LO <= int(site, 16) <= OPENING_HI:
            refused.append(
                {"abs": site, "reason": "opening_tail_width_sensitive_fallback"}
            )
            continue

        ko = normalize_ko_text(ko_by_abs.get(site, ""))
        if not ko:
            continue
        if is_low_quality_ko(ko):
            refused.append({"abs": site, "reason": "low_quality_ko"})
            continue
        # Existing ext3 records are unsafe in this window even when the
        # current target already expands to Hangul: this renderer bypasses the
        # ext3 hook. Force those records through the safe 2-byte path.
        has_ext3 = b"\xE5\x18" in body
        if _hangul(d_now.expand(body, tbl)) and not has_ext3:
            continue
        if looks_like_event_body(split_prefix_body(jp[sj + cursor - n : sj + cursor - 1])[1]):
            refused.append({"abs": site, "reason": "original body looks like an event body"})
            continue
        if len(prefix) + TOKEN_LEN > len(original):
            refused.append({"abs": site, "reason": "no room for a 2-byte token"})
            continue
        if len(original) - len(prefix) - TOKEN_LEN > MAX_PAD:
            refused.append({"abs": site, "reason": "pad would exceed MAX_PAD"})
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=HANGUL_MARKER, hangul_marker_mode="run"
        )
        if enc is None or b"\x00" in enc:
            refused.append({"abs": site, "reason": "encode_fail"})
            continue
        if not pool:
            refused.append({"abs": site, "reason": "no guard-approved slot left"})
            continue
        plan.append(
            {
                "abs": site,
                "ko": ko,
                "slot": pool.pop(0),
                "prefix_len": len(prefix),
                "record_len": len(original) + 1,
            }
        )

    slot_payload = {row["slot"]: b"" for row in plan}
    for row in plan:
        slot_payload[row["slot"]] = try_encode_ko_text(
            row["ko"], tbl, hangul_marker_code=HANGUL_MARKER, hangul_marker_mode="run"
        )

    applied: List[dict] = []
    if plan:
        # guard_hangul_slot_writes inside this call refuses aux-live slots again.
        write_dictionary_slots_spill(rom, slot_payload)
        d2 = make_dictionary_ext3(
            rom,
            load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
            load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
        )
        for row in plan:
            a = int(row["abs"], 16)
            got = read_encoded_z_safe(rom, st + a, max_len=256)
            if not got:
                refused.append({"abs": row["abs"], "reason": "record vanished"})
                continue
            original = got[0]
            prefix, _body, _ = split_prefix_body(original)
            token = token_from_dict_index(row["slot"])
            try:
                payload = padded_token_payload(prefix, token, original)
            except RuntimeError as exc:
                refused.append({"abs": row["abs"], "reason": f"pad: {exc}"})
                continue
            rom[st + a : st + a + len(payload)] = payload
            decoded = d2.expand(split_prefix_body(payload)[1], tbl).rstrip("\u3000")
            ok = decoded == row["ko"]
            applied.append(
                {
                    "abs": row["abs"],
                    "slot": f"{row['slot']:04X}",
                    "ko": row["ko"],
                    "decoded": decoded,
                    "ok": ok,
                    "token": token.hex(),
                }
            )
            if not ok:
                refused.append(
                    {
                        "abs": row["abs"],
                        "reason": f"decode mismatch: {decoded!r} != {row['ko']!r}",
                    }
                )

    bad = [a for a in applied if not a["ok"]]
    checksum_before = f"{ws_header(load_rom(args.target))['checksum']:04X}"
    backup = None
    checksum_after = None
    if args.commit and not bad:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        (BACKUP_ROOT / stamp).mkdir(parents=True, exist_ok=True)
        backup = BACKUP_ROOT / stamp / args.target.name
        shutil.copy2(args.target, backup)
        checksum_after = f"{update_ws_checksum(rom):04X}"
        args.target.write_bytes(rom)

    report = {
        "ok": not bad,
        "generated_by": "tools/apply_opening_safe_slots.py",
        "mode": "commit" if args.commit else "dry-run",
        "wrote": str(args.target) if (args.commit and not bad) else None,
        "band": [f"{args.lo:06X}", f"{args.hi:06X}"],
        "seed_fallback": str(args.seed),
        "existing_ext3_repair": True,
        "opening_tail_fallback": [
            f"{OPENING_TAIL_FALLBACK_LO:06X}", f"{OPENING_HI:06X}"
        ],
        "token_len": TOKEN_LEN,
        "why_2_byte": "the opening renderer does not go through the ext3 hook; a "
        "4-byte E5 18 token there is undecoded and its 0x01 padding is walked as "
        "event opcodes (event error 257 / 2049)",
        "guard": [
            "slot has no aux/name75 consumer in the ORIGINAL ROM",
            "slot has no remaining consumer of any region in the target",
            "record refused when looks_like_event_body flags its original body",
        ],
        "guard_approved_slots_available": len(pool) + len(plan),
        "planned": len(plan),
        "applied": len(applied),
        "decode_fail": len(bad),
        "refused": refused,
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "backup": str(backup) if backup else None,
        "revert": f"copy {backup} back" if backup else "no write performed",
        "sites": applied,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"band              : {report['band'][0]}-{report['band'][1]}")
    print(f"guard-approved pool: {report['guard_approved_slots_available']} slots")
    print(f"planned / applied : {len(plan)} / {len(applied)}  decode_fail={len(bad)}")
    for r in refused[:10]:
        print(f"  refused {r['abs']}: {r['reason']}")
    for a in applied[:10]:
        print(f"  {a['abs']} slot {a['slot']} token {a['token']} {a['decoded']!r}")
    if args.commit and not bad:
        print(f"backup            : {backup}")
        print(f"checksum          : {checksum_before} → {checksum_after}")
    elif bad:
        print("decode failures — nothing written")
    else:
        print("dry-run: nothing written. Add --commit to apply.")
    print(f"→ {args.report}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
