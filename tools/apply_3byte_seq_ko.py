#!/usr/bin/env python3
"""
Apply quality sequential KO via ext3 tokens (E5 18 xx yy → banks 0x11+).

Installs/upgrades the multi-bank runtime hook, fills free ext3 indices
(frequency-ranked), size-preserving rewrites (SPACE pad).

Default band: 0x610000–0x69FFFF (stage2 + Ep4 + Ep5-8). Tip is never written
unless the caller copies after smoke.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_safe_unit import padded_token_payload  # noqa: E402
from event_record_heuristics import looks_like_event_body  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    dict_index_from_token,
    is_dict_token,
    is_expanded_rom,
    is_ext3_magic,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import (  # noqa: E402
    encode_ko_text,
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)
from patch_3byte_dict_token import (  # noqa: E402
    DEFAULT_NUM_BANKS,
    EXP3_SEG0,
    INDEX_BASE,
    bank_local_for_index,
    install as install_ext3_hook,
    list_free_ext3_indices,
    write_ext3_dictionary_slots,
)
from patch_exp_dictionary import make_exp_dictionary  # noqa: E402

DEFAULT_LO = 0x610000
DEFAULT_HI = 0x69FFFF
HANGUL_MARKER = marker_code()

# Hard band from requirement 2.5 — an abs outside it is refused, never silently
# skipped, so a mistyped --lo/--hi cannot reach non-dialogue data.
#
# The upper bound is 0x63FFFF, not 0x69FFFF. Banks 64-69 are **data**, not
# dialogue: the extractor walks them as zstrings and produces records the
# translators then translated, but the originals are fixed-stride table entries
# repeating one skeleton — `をん…買の…買は…` in bank 64, `…校の…` in 65,
# `…尊の…` in 66, `…俵の…` in 67. That single starting motif accounts for
# 23-38% of the changed records in those banks, while banks 60-63 top out at 14%
# on `……` like ordinary prose. Writing a token + 0x01 padding over a table entry
# corrupts the battle UI (broken frame glyphs and unrelated text when a unit
# moves) and, for the entries an event stream reads, produces the new-game event
# error. Confirmed by bisection: reverting banks 64-69 fixes both symptoms while
# reverting bank 63 alone does not.
HARD_LO = 0x6040A5
HARD_HI = 0x63FFFF

# The opening renderer bypasses the ext3 leaf. It must be localized with the
# guard-approved 2-byte stock-slot path, never with E5 18 xx yy + padding.
OPENING_LO = 0x6040A5
OPENING_HI = 0x604570
DATA_BANK_LO = 0x640000
DATA_BANK_HI = 0x69FFFF

# The intermission pilot/face roster is fixed-layout data, not a dialogue
# stream. Its consumer does not understand ext3 tokens or token+padding.
# Keep this range fail-closed even when a future sheet misclassifies it.
FIXED_ROSTER_LO = 0x61E403
FIXED_ROSTER_HI = 0x61F480

# Known table-motif leftovers inside a dialogue bank: 36 changed records in bank
# 62 start with the same `をん` skeleton. Not yet proven harmful, so they are only
# reported, not refused. See tools/scan_table_motif_records.py.
MOTIF_WATCH_BANKS = (0x62,)

# `6B:2477` was a 256-byte table that passed a `max_len=256` read and got
# clobbered, so the comparison is `>=`, not `>`.
MAX_SAFE_RECORD_LEN = 256
MAX_SAFE_PAD = 32


def jp_record(rom: bytes | bytearray, jp_rom: bytes | None, abs_off: int) -> bytes:
    """The record payload as it is in the pristine original, when available.

    The event heuristic has to look at the *original* body: once a record has been
    rewritten as token + padding it no longer looks like an event record, so
    testing the working ROM would let the second pass through.
    """
    src = jp_rom if jp_rom is not None else rom
    got = read_encoded_z_safe(src, stock_base(src) + abs_off, max_len=256)
    return got[0] if got else b""


def _first_dict_index(payload: bytes) -> int | None:
    """Dictionary index of the first 2-byte dict token in a record body.

    A 2-byte token addresses the shared stock ``5F`` table (or the bank10
    extension). ``E5 18 xx yy`` ext3 tokens are skipped, so a record already
    re-homed into the expansion banks returns ``None``.
    """
    i = 0
    n = len(payload)
    while i < n:
        lead = payload[i]
        if i + 1 < n and is_ext3_magic(lead, payload[i + 1]):
            i += 4
            continue
        if is_dict_token(lead) and i + 1 < n:
            return dict_index_from_token(lead, payload[i + 1])
        i += 1
    return None


def stock_dict_index(payload: bytes, stock_count: int) -> int | None:
    """The stock 5F index a record depends on, or None.

    Localizing a line by re-pointing a stock dictionary slot is unsafe: the
    intermission menus, the battle HUD, the help screens and the name75 tables
    read the same table, so the slot's new Hangul text leaks into them (design
    hypothesis A3). Records reported here should be re-homed onto an ext3 slot,
    which is private to the expansion banks — the mechanism every other dialogue
    band already uses.
    """
    idx = _first_dict_index(payload)
    if idx is None or idx >= stock_count:
        return None
    return idx


def _file_abs(rom: bytes | bytearray, logical: int) -> int:
    return stock_base(rom) + logical


def _already_hangul(expanded: str) -> bool:
    return any("가" <= ch <= "힣" for ch in expanded)


def _load_sheet(path: Path) -> List[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return list(data.get("lines") or [])


def _make_dict(rom: bytes | bytearray, exp_meta: dict | None, num_banks: int) -> Dictionary:
    if exp_meta and exp_meta.get("ext_in_expansion"):
        d = make_exp_dictionary(rom, exp_meta)
    else:
        d = Dictionary(rom, count=0x1000, ext_in_expansion=True, ext_seg=0x10)
    return Dictionary(
        rom,
        count=d.count,
        ext_ptr_off=d.ext_ptr_off,
        ext_seg=d.ext_seg,
        stock_count=d.stock_count,
        ext_in_expansion=d.ext_in_expansion,
        ext3_ptr_off=0,
        ext3_seg=EXP3_SEG0,
        ext3_banks=num_banks,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--out-rom", type=Path, default=ROOT / "out/patch/monoeye_ko_3byte_work.wsc")
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_apply_all.json",
    )
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument(
        "--exp-meta",
        type=Path,
        default=ROOT / "out/patch/exp_dictionary_meta.json",
    )
    ap.add_argument(
        "--ext3-meta",
        type=Path,
        default=ROOT / "out/patch/ext3_dictionary_meta.json",
    )
    ap.add_argument(
        "--jp",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
        help="pristine ORIGINAL 8 MiB ROM; the event-record guard must judge the "
        "original body, since a body already rewritten as token + padding no "
        "longer looks like an event record",
    )
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=DEFAULT_LO)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=DEFAULT_HI)
    ap.add_argument("--num-banks", type=int, default=DEFAULT_NUM_BANKS)
    ap.add_argument("--limit", type=int, default=0, help="Max uniques (0=fill free)")
    ap.add_argument(
        "--rehome-stock-dict",
        action="store_true",
        help="also rewrite records that are already Hangul *through a shared stock "
        "5F dictionary slot*, moving them onto a private ext3 slot. Without this "
        "they are skipped as already_ko and keep hijacking the shared table that "
        "the intermission menus / battle HUD / help screens read.",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--reinstall-hook",
        action="store_true",
        help="Rewrite cave/leaf for multi-bank (keeps bank11 phrases)",
    )
    args = ap.parse_args()

    if args.lo <= OPENING_HI and args.hi >= OPENING_LO:
        raise SystemExit(
            f"refusing ext3 in opening window {OPENING_LO:06X}-{OPENING_HI:06X}; "
            "use tools/apply_opening_safe_slots.py for this range and start "
            "the ext3 pass at 0x604571 or later"
        )

    rom = bytearray(load_rom(args.rom))
    if not is_expanded_rom(rom):
        raise SystemExit("16MiB ROM required")
    jp_rom = bytes(load_rom(args.jp)) if args.jp.exists() else None
    if jp_rom is None:
        raise SystemExit(
            f"missing original ROM {args.jp}; the event-record guard needs it "
            "(requirement 2.5 is fail-closed)"
        )
    tbl = Tbl.load(args.tbl)
    exp_meta = (
        json.loads(args.exp_meta.read_text(encoding="utf-8"))
        if args.exp_meta.exists()
        else {}
    )

    hooked = rom[stock_base(rom) + 0x7A0736] == 0xEA
    if (not hooked) or args.reinstall_hook:
        meta = install_ext3_hook(
            rom, force_format=False, num_banks=args.num_banks
        )
    else:
        meta = (
            json.loads(args.ext3_meta.read_text(encoding="utf-8"))
            if args.ext3_meta.exists()
            else {}
        )
        # Still ensure extra banks formatted
        meta = install_ext3_hook(
            rom, force_format=False, num_banks=args.num_banks
        )

    num_banks = int(meta.get("num_banks", args.num_banks))
    d = _make_dict(rom, exp_meta, num_banks)
    lines = _load_sheet(args.sheet)
    # Fail closed if a future extractor re-exports fixed-stride bank64-69
    # entries as non-empty dialogue. The normal build produces an explicit
    # exclusion artifact and passes a sheet with zero such rows.
    data_rows = []
    for row in lines:
        try:
            row_abs = int(row.get("abs", ""), 16)
        except (TypeError, ValueError):
            continue
        if not (args.lo <= row_abs <= args.hi):
            continue
        if DATA_BANK_LO <= row_abs <= DATA_BANK_HI and (row.get("ko") or "").strip():
            data_rows.append(f"{row_abs:06X}")
    if data_rows:
        sample = ", ".join(data_rows[:8])
        raise SystemExit(
            "translation input contains non-empty bank64-69 data rows; "
            "rebuild the guarded apply sheet first "
            f"(count={len(data_rows)}, sample={sample})"
        )

    text_to_abs: Dict[str, List[int]] = defaultdict(list)
    # abs -> stock 5F index the record currently depends on (re-homing candidates)
    rehome_source: Dict[int, int] = {}
    skipped = {
        "out_of_band": 0,
        "empty_ko": 0,
        "low_quality": 0,
        "no_record": 0,
        "already_ko": 0,
        "encode_fail": 0,
        "too_short": 0,
        "rehomed_from_stock_dict": 0,
        "refused_out_of_band": 0,
        "refused_event_body": 0,
        "refused_no_dict_token": 0,
        "refused_record_len": 0,
        "refused_pad": 0,
        "refused_fixed_roster": 0,
    }
    refused: List[dict] = []

    for L in lines:
        abs_off = int(L["abs"], 16)
        if not (args.lo <= abs_off <= args.hi):
            skipped["out_of_band"] += 1
            continue
        if not (HARD_LO <= abs_off <= HARD_HI):
            refused.append({"abs": f"{abs_off:06X}", "reason": "outside the hard band"})
            skipped["refused_out_of_band"] += 1
            continue
        ko = normalize_ko_text(L.get("ko") or "")
        if not ko:
            skipped["empty_ko"] += 1
            continue
        if is_low_quality_ko(ko):
            skipped["low_quality"] += 1
            continue
        if FIXED_ROSTER_LO <= abs_off <= FIXED_ROSTER_HI:
            refused.append(
                {"abs": f"{abs_off:06X}", "reason": "fixed_pilot_roster"}
            )
            skipped["refused_fixed_roster"] += 1
            continue
        got = read_encoded_z_safe(rom, _file_abs(rom, abs_off))
        if not got:
            skipped["no_record"] += 1
            continue
        prefix, body, _ = split_prefix_body(got[0])
        if len(prefix) + 4 > len(got[0]):
            skipped["too_short"] += 1
            continue
        # Requirement 2.5, fail closed. A size-preserving rewrite is
        # `prefix + token + 0x01 * pad`, which only preserves *meaning* for a real
        # dialogue record. On an event record the interpreter walks the padding as
        # opcodes and the game reports an event error (0x0101 / 0x0801 observed on
        # new game). The extraction sheet does contain KO for records that are
        # actually control data, so the applier has to refuse them itself.
        jp_body = split_prefix_body(jp_record(rom, jp_rom, abs_off))[1]
        if looks_like_event_body(jp_body):
            refused.append({"abs": f"{abs_off:06X}", "reason": "event_body"})
            skipped["refused_event_body"] += 1
            continue
        # Confirmed cause of the new-game event error: banks 64-69 hold fixed-stride
        # data tables whose entries the extractor mistook for dialogue. A real
        # dialogue record in this engine carries at least one dictionary token; a
        # table entry carries none and decodes to nonsense (e.g. 64:0860 ->
        # 'をん行は買の手は買はい機は'). Writing a token + 0x01 padding over one makes
        # the interpreter walk the padding as opcodes. This discriminator catches
        # what looks_like_event_body misses.
        if not any(is_dict_token(b) for b in jp_body):
            refused.append({"abs": f"{abs_off:06X}", "reason": "no_dict_token_in_original"})
            skipped["refused_no_dict_token"] += 1
            continue
        if len(got[0]) >= MAX_SAFE_RECORD_LEN:
            refused.append({"abs": f"{abs_off:06X}", "reason": "record too long"})
            skipped["refused_record_len"] += 1
            continue
        if len(got[0]) - len(prefix) - 4 > MAX_SAFE_PAD:
            refused.append({"abs": f"{abs_off:06X}", "reason": "pad too large"})
            skipped["refused_pad"] += 1
            continue
        expanded = d.expand(body, tbl)
        if _already_hangul(expanded):
            # Already Hangul — but check *how*. Hangul delivered through a shared
            # stock 5F slot is the bug, not the goal: the same slot is read by the
            # intermission / HUD / help / name75 records. Re-home it onto ext3.
            shared_idx = (
                stock_dict_index(body, d.stock_count)
                if args.rehome_stock_dict
                else None
            )
            if shared_idx is None:
                skipped["already_ko"] += 1
                continue
            rehome_source[abs_off] = shared_idx
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=HANGUL_MARKER, hangul_marker_mode="run"
        )
        if enc is None or b"\x00" in enc:
            skipped["encode_fail"] += 1
            continue
        text_to_abs[ko].append(abs_off)

    ranked: List[Tuple[str, List[int]]] = sorted(
        text_to_abs.items(), key=lambda kv: (-len(kv[1]), min(kv[1]), kv[0])
    )
    free_indices = list_free_ext3_indices(rom, num_banks=num_banks)
    if args.limit > 0:
        free_indices = free_indices[: args.limit]
        ranked = ranked[: args.limit]

    PHRASE_BUDGET_PER_BANK = 0x10000 - (0x1000 * 2) - 1

    def _bank_phrase_used(seg: int) -> int:
        from monoeye_rom import slice_expansion_bank

        bank = slice_expansion_bank(rom, seg)
        empty_at = 0x1000 * 2
        if all(b == 0xFF for b in bank[:64]):
            return 0
        cursor = empty_at + 1
        for i in range(0x1000):
            poff = bank[i * 2] | (bank[i * 2 + 1] << 8)
            if poff <= empty_at or poff >= 0x10000:
                continue
            end = poff
            while end < 0x10000 and bank[end] != 0:
                end += 1
            end += 1
            cursor = max(cursor, end)
        return max(0, cursor - (empty_at + 1))

    phrase_used: Dict[int, int] = defaultdict(int)
    for bi in range(num_banks):
        phrase_used[EXP3_SEG0 + bi] = _bank_phrase_used(EXP3_SEG0 + bi)

    assignments: Dict[str, int] = {}
    slot_payload: Dict[int, bytes] = {}
    free_i = 0
    for ko, _abs_list in ranked:
        enc = encode_ko_text(
            ko, tbl, hangul_marker_code=HANGUL_MARKER, hangul_marker_mode="run"
        )
        if b"\x00" in enc:
            skipped["encode_fail"] += 1
            continue
        need = len(enc) + 1
        placed = False
        while free_i < len(free_indices):
            index = free_indices[free_i]
            seg, _local = bank_local_for_index(index)
            if phrase_used[seg] + need <= PHRASE_BUDGET_PER_BANK:
                free_i += 1
                phrase_used[seg] += need
                assignments[ko] = index
                slot_payload[index] = enc
                placed = True
                break
            # Bank phrase-full: skip remaining free indices in this bank.
            while free_i < len(free_indices) and bank_local_for_index(
                free_indices[free_i]
            )[0] == seg:
                free_i += 1
        if not placed:
            skipped["phrase_full"] = skipped.get("phrase_full", 0) + 1
            break
    ranked = [(ko, text_to_abs[ko]) for ko in assignments]

    if args.dry_run:
        print(
            json.dumps(
                {
                    "uniques": len(ranked),
                    "slots": len(slot_payload),
                    "sites": sum(len(v) for _, v in ranked),
                    "free_indices": len(free_indices),
                    "num_banks": num_banks,
                    "skipped": skipped,
                    "top": [
                        {
                            "ko": ko[:40],
                            "n": len(a),
                            "idx": f"{assignments[ko]:04X}",
                        }
                        for ko, a in ranked[:10]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    wr = write_ext3_dictionary_slots(
        rom, slot_payload, num_banks=num_banks
    )
    # Drop assignments that overflowed (not written)
    if wr.get("skipped_overflow"):
        # Re-read: only keep indices that have non-empty phrases — simpler to
        # re-verify on expand.
        pass
    d2 = _make_dict(rom, exp_meta, num_banks)

    rewritten = 0
    verify_fail = 0
    # stock 5F index -> record abs list that no longer depends on it
    rehomed_indices: Dict[str, List[str]] = {}
    for ko, abs_list in ranked:
        index = assignments[ko]
        try:
            token = token_from_dict_index(index)
        except ValueError:
            continue
        # Confirm phrase exists
        try:
            raw = d2.raw_entry(index)
        except Exception:
            continue
        if not raw:
            continue
        for abs_off in abs_list:
            got = read_encoded_z_safe(rom, _file_abs(rom, abs_off))
            if not got:
                continue
            original = got[0]
            prefix, _body, _ = split_prefix_body(original)
            try:
                new_payload = padded_token_payload(prefix, token, original)
            except RuntimeError:
                skipped["too_short"] += 1
                continue
            fa = _file_abs(rom, abs_off)
            rom[fa : fa + len(new_payload)] = new_payload
            got2 = d2.expand(split_prefix_body(new_payload)[1], tbl)
            if not _already_hangul(got2):
                verify_fail += 1
                continue
            rewritten += 1
            if abs_off in rehome_source:
                skipped["rehomed_from_stock_dict"] += 1
                rehomed_indices.setdefault(
                    f"{rehome_source[abs_off]:04X}", []
                ).append(f"{abs_off:06X}")

    cs = update_ws_checksum(rom)
    meta = dict(meta)
    meta.update(
        {
            "checksum": f"{cs:04X}",
            "applied_uniques": len(slot_payload),
            "applied_sites": rewritten,
            "verify_fail": verify_fail,
            "write": wr,
            "band": [f"{args.lo:06X}", f"{args.hi:06X}"],
            "skipped": skipped,
            "phrase_budget_hint_per_bank": PHRASE_BUDGET_PER_BANK,
            "refused": refused[:400],
            "refused_total": len(refused),
            "rehomed_stock_dict_indices": rehomed_indices,
            "rehome_note": "these stock 5F indices are no longer read by the "
            "rewritten records; run tools/repair_dict5f_pointers.py --mode "
            "unreferenced to hand them back to the original table",
        }
    )
    args.out_rom.parent.mkdir(parents=True, exist_ok=True)
    args.out_rom.write_bytes(rom)
    args.ext3_meta.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary = {
        "out_rom": str(args.out_rom),
        "num_banks": num_banks,
        "uniques": len(slot_payload),
        "sites": rewritten,
        "verify_fail": verify_fail,
        "write": wr,
        "skipped": skipped,
        "rehomed_stock_dict_indices": rehomed_indices,
    }
    sum_path = ROOT / "out/patch/ext3_apply_summary.json"
    sum_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("->", args.out_rom)
    return 0 if verify_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
