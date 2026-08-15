#!/usr/bin/env python3
"""
Localize the bank-75 unit/weapon display table in place, using ext3 tokens.

This is the size-preserving record rewrite that ``apply_weapon_table.py``
documents as its steps 2/3 but never wired up (``encode_token_body`` and
``longest_fragment_encode`` are declared there and never called).

Why ext3 and not a 2-byte dictionary token
------------------------------------------
Measured this session on the union of the original and the work ROM: of the 4,096
addressable 2-byte token indices, **7** have no consumer at all and **0** are
name75-only. Stock 3,831 + bank10 ext 265 fills the space exactly. "One 2-byte
token per record" is impossible for 1,206 records. ext3 (``E5 18 xx yy``,
16 banks x 4,096 = 65,536 indices, 3,382 used) is the only pool with room.

Why this is safe on name75
--------------------------
docs/EXT3_RENDER_PATH.md: exactly one piece of code in the ROM reads the
dictionary pointer table (``7A:0703``, inside leaf ``7A:06CE``), and it has
exactly two callers (``7A:0740``/``7A:0818``) — both hooked by the ext3 patch.
943 of 1,206 name75 records already carry stock dictionary tokens and render in
vanilla, so name75 provably reaches that leaf.

Fail-closed rules
-----------------
* **size preserving** — the 4-byte token plus zero padding must fit the original
  payload exactly. Records with a payload under 4 bytes are refused as
  ``too_short``; growing a record would desynchronise the sequential walk.
* the record must still hold the original bytes on the target (else
  ``already_changed`` — never overwrite someone else's edit)
* the original payload must not already contain the ext3 magic
* every patched record is re-expanded with an ext3-aware dictionary and must
  decode back to the intended Korean, or the ROM is not written
* identical Korean strings share one ext3 index

After running, re-run ``tools/verify_nondialogue_text.py`` — name75 is inside its
scan scope and its check (iii) is what catches a length/terminator mistake.

Report: ``out/patch/name75_ko_report.json``.
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
from apply_safe_unit import padded_token_payload  # noqa: E402
from expand_dictionary import NAME75_RANGES, _walk_zstring_range  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    find_rom,
    is_expanded_rom,
    is_ext3_magic,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402
from tbl_code_prefs import (  # noqa: E402
    find_codes,
    flatten_codes,
    marker_codes,
    retag_with_original_codes,
)
from monoeye_rom import BANK_SIZE, le16, slice_expansion_bank  # noqa: E402
from patch_3byte_dict_token import (  # noqa: E402
    EXP3_SEG0,
    EXP3_SLOTS,
    INDEX_BASE,
    list_free_ext3_indices,
    token_from_ext3_index,
    write_ext3_dictionary_slots,
)

DEFAULT_ROM = ROOT / "out/patch/monoeye_ko_ui_work.wsc"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_META = ROOT / "out/patch/ext_dictionary_meta.json"
DEFAULT_REPORT = ROOT / "out/patch/name75_ko_report.json"

TOKEN_LEN = 4
JAPANESE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
HANGUL = re.compile(r"[\uac00-\ud7a3]")


def load_catalog(paths: Sequence[Path], extra: Path | None) -> Dict[str, str]:
    """jp → ko from every catalog, first definition wins."""
    out: Dict[str, str] = {}
    for path in list(paths) + ([extra] if extra else []):
        if not path or not path.exists():
            continue
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = list(spec.get("entries") or []) + list(spec.get("fragments") or [])
        for row in rows:
            jp, ko = row.get("jp"), row.get("ko")
            if jp and ko and jp not in out:
                out[jp] = ko
    return out


def ext3_bank_room(rom: bytes | bytearray, num_banks: int) -> Dict[int, int]:
    """Bank index → free phrase bytes left.

    A free *pointer slot* is useless without *phrase room* in the same bank:
    ``write_ext3_dictionary_slots`` appends each phrase after the last live one
    in that bank. Measured on the current work ROM, banks 0x11-0x1D are packed to
    within a few bytes of 64 KiB by the dialogue pass while 0x1E/0x1F/0x20 hold
    ~124 KiB between them. Allocating by index order alone therefore picked full
    banks and every write overflowed.
    """
    empty_at = EXP3_SLOTS * 2
    room: Dict[int, int] = {}
    for bi in range(num_banks):
        bank = slice_expansion_bank(rom, EXP3_SEG0 + bi)
        if all(b == 0xFF for b in bank[:64]):
            room[bi] = BANK_SIZE - (empty_at + 1)
            continue
        cursor = empty_at + 1
        for i in range(EXP3_SLOTS):
            poff = le16(bank, i * 2)
            if empty_at <= poff < BANK_SIZE:
                end = poff
                while end < BANK_SIZE and bank[end] != 0:
                    end += 1
                cursor = max(cursor, end + 1)
        room[bi] = BANK_SIZE - cursor
    return room


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    ap.add_argument("--out-rom", type=Path, default=None, help="default: --rom")
    ap.add_argument("--base-rom", type=Path, default=None, help="default: original")
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument(
        "--names",
        type=Path,
        action="append",
        default=None,
        help="extra jp/ko catalog (repeatable). Default: every data/*_ko.json",
    )
    ap.add_argument("--out-report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--limit", type=int, default=0, help="apply at most N records")
    ap.add_argument(
        "--only-abs",
        action="append",
        default=None,
        help="restrict to these logical addresses, hex (repeatable). Probe mode.",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--dump-worklist",
        type=Path,
        default=None,
        help="write every record skipped as no_translation to this JSON, split "
        "into applicable (payload >= 4B, a token fits) and too_short. Implies "
        "--dry-run so nothing is written to the ROM.",
    )
    args = ap.parse_args(argv)
    if args.dump_worklist:
        args.dry_run = True

    out_rom = args.out_rom or args.rom
    rom = bytearray(load_rom(args.rom))
    if not is_expanded_rom(rom):
        raise SystemExit("refusing: ext3 needs the 16 MiB expanded ROM")

    base = bytes(load_rom(args.base_rom or find_rom(ROOT)))
    tbl = Tbl.load(args.tbl)
    meta = load_ext_meta(args.meta)
    meta3 = load_ext_meta(args.ext3_meta)
    num_banks = int(meta3.get("num_banks") or 0)
    if num_banks <= 0:
        raise SystemExit("refusing: ext3 banks are not installed (num_banks=0)")

    d_base = Dictionary(base)
    marker = marker_code()
    # Unit status icon codes ('█' placeholder family). Read from the tbl, not
    # hardcoded, so a tbl change cannot silently narrow the guard.
    MARKERS = marker_codes(tbl)

    catalogs = args.names or sorted((ROOT / "data").glob("*_ko.json"))
    catalog = load_catalog(catalogs, None)

    only = (
        {int(a, 16) for a in args.only_abs} if args.only_abs else None
    )

    sb_base, sb_rom = stock_base(base), stock_base(rom)

    # --- select records -----------------------------------------------------
    planned: List[dict] = []
    skipped: Dict[str, int] = {}
    skip_rows: List[dict] = []

    worklist: List[dict] = []

    def skip(reason: str, row: dict) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1
        if len(skip_rows) < 80:
            skip_rows.append({"reason": reason, **row})
        if reason == "no_translation" and args.dump_worklist:
            worklist.append(row)

    for lo, hi in NAME75_RANGES:
        for logical, payload, _kind in _walk_zstring_range(
            base, lo, hi, region="name75", max_len=64
        ):
            if only is not None and logical not in only:
                continue
            jp = d_base.expand(payload, tbl)
            info = {
                "abs": f"{logical:06X}",
                "jp": jp[:40],
                "bytes": len(payload),
                "jp_full": jp,
            }
            if not JAPANESE.search(jp):
                continue
            ko = catalog.get(jp)
            if not ko:
                skip("no_translation", info)
                continue
            if len(payload) < TOKEN_LEN:
                skip("too_short", info)
                continue
            if any(
                is_ext3_magic(payload[i], payload[i + 1])
                for i in range(len(payload) - 1)
            ):
                skip("original_has_ext3_magic", info)
                continue
            # The target must still hold the original bytes.
            got = read_encoded_z_safe(rom, sb_rom + logical, max_len=64)
            if not got or got[0] != payload:
                skip("already_changed", info)
                continue
            ko_n = normalize_ko_text(ko)
            # Pin ambiguous characters to the codes THIS record used. Several ROM
            # codes decode to the same placeholder ('█' <- E6C5/E6C9/E736), and
            # Tbl.char_to_code collapses them to the lowest, so a plain re-encode
            # silently swaps the unit status icon next to the name. See
            # tools/tbl_code_prefs.py.
            flat = flatten_codes(payload, d_base)
            ko_enc_text, code_notes = retag_with_original_codes(ko_n, flat, tbl)
            enc = try_encode_ko_text(
                ko_enc_text, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
            )
            if enc is None:
                skip("encode_fail", {**info, "ko": ko})
                continue
            if b"\x00" in enc:
                skip("ko_contains_nul", {**info, "ko": ko})
                continue
            # Fail-closed on the icon family: whatever status icons the original
            # record carried must come back byte-identical, in the same order.
            want_markers = find_codes(flat, MARKERS)
            got_markers = find_codes(enc, MARKERS)
            if want_markers != got_markers:
                skip(
                    "marker_code_lost",
                    {
                        **info,
                        "ko": ko,
                        "want": [f"{c:04X}" for c in want_markers],
                        "got": [f"{c:04X}" for c in got_markers],
                    },
                )
                continue
            planned.append(
                {
                    "logical": logical,
                    "abs": f"{logical:06X}",
                    "jp": jp,
                    "ko": ko_n,
                    "payload_len": len(payload),
                    "phrase": enc,
                    "code_prefs": code_notes,
                }
            )
            if args.limit and len(planned) >= args.limit:
                break
        if args.limit and len(planned) >= args.limit:
            break

    # --- allocate one ext3 index per unique Korean phrase -------------------
    # Allocation is per bank: a free pointer slot is only usable if that same
    # bank still has phrase bytes left (see ext3_bank_room).
    room = ext3_bank_room(rom, num_banks)
    free_by_bank: Dict[int, List[int]] = {}
    for idx in list_free_ext3_indices(rom, num_banks=num_banks):
        free_by_bank.setdefault((idx - INDEX_BASE) >> 12, []).append(idx)

    unique: Dict[bytes, int] = {}
    slot_payload: Dict[int, bytes] = {}
    unique_phrases = {p["phrase"] for p in planned}
    total_need = sum(len(p) + 1 for p in unique_phrases)
    total_room = sum(room.values())
    if total_need > total_room:
        raise SystemExit(
            f"refusing: need {total_need} phrase bytes, only {total_room} free "
            f"across {num_banks} ext3 bank(s)"
        )

    # Fill the roomiest banks first so a nearly-full bank cannot strand a phrase.
    bank_order = sorted(room, key=lambda b: -room[b])
    for row in planned:
        phrase = row["phrase"]
        idx = unique.get(phrase)
        if idx is None:
            need = len(phrase) + 1
            for bi in bank_order:
                if room.get(bi, 0) >= need and free_by_bank.get(bi):
                    idx = free_by_bank[bi].pop()
                    room[bi] -= need
                    break
            if idx is None:
                raise SystemExit(
                    f"refusing: no ext3 bank has {need} bytes plus a free slot "
                    f"for {row['ko']!r}"
                )
            unique[phrase] = idx
            slot_payload[idx] = phrase
        row["index"] = idx

    report = {
        "generated_by": "tools/apply_name75_ko.py",
        "rom_in": str(args.rom),
        "rom_out": str(out_rom),
        "marker": f"{marker:04X}",
        "ext3_banks": num_banks,
        "catalog_terms": len(catalog),
        "records_planned": len(planned),
        "unique_phrases": len(unique),
        "ext3_phrase_bytes_needed": total_need,
        "ext3_phrase_bytes_free": total_room,
        "ext3_bank_room_after": {f"{EXP3_SEG0 + b:02X}": r for b, r in sorted(room.items())},
        "skipped": skipped,
        "skipped_sample": skip_rows,
    }

    if args.dump_worklist:
        applicable = [r for r in worklist if r["bytes"] >= TOKEN_LEN]
        too_short = [r for r in worklist if r["bytes"] < TOKEN_LEN]
        seen: Dict[str, dict] = {}
        for row in applicable:
            hit = seen.get(row["jp_full"])
            if hit is None:
                seen[row["jp_full"]] = {
                    "jp": row["jp_full"],
                    "ko": "",
                    "min_bytes": row["bytes"],
                    "sites": [row["abs"]],
                }
            else:
                hit["sites"].append(row["abs"])
                hit["min_bytes"] = min(hit["min_bytes"], row["bytes"])
        payload_out = {
            "_note": (
                "name75 records with no Korean yet. Fill 'ko' in "
                "'applicable_unique' and save as a catalog with an 'entries' "
                "list, then pass it to apply_name75_ko.py --names. "
                "'min_bytes' is the smallest original payload across the sites "
                "sharing this text: the encoded token needs 4 bytes, so nothing "
                "below 4 can be used."
            ),
            "generated_by": "tools/apply_name75_ko.py --dump-worklist",
            "records_no_translation": len(worklist),
            "applicable_records": len(applicable),
            "too_short_records": len(too_short),
            "applicable_unique_texts": len(seen),
            "applicable_unique": sorted(
                seen.values(), key=lambda r: (-len(r["sites"]), r["jp"])
            ),
            "too_short": too_short,
        }
        args.dump_worklist.parent.mkdir(parents=True, exist_ok=True)
        args.dump_worklist.write_text(
            json.dumps(payload_out, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"worklist | no_translation={len(worklist)} "
            f"applicable={len(applicable)} unique={len(seen)} "
            f"too_short={len(too_short)} → {args.dump_worklist}"
        )

    if args.dry_run or not planned:
        report["dry_run"] = True
        report["applied"] = []
        args.out_report.parent.mkdir(parents=True, exist_ok=True)
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"{'DRY RUN' if args.dry_run else 'NOTHING TO DO'} | planned="
            f"{len(planned)} unique={len(unique)} skipped={skipped}"
        )
        return 0

    # --- write ext3 phrases, then the in-place tokens -----------------------
    write_info = write_ext3_dictionary_slots(
        rom, slot_payload, num_banks=num_banks
    )
    if write_info.get("skipped_overflow"):
        raise SystemExit(
            f"refusing: ext3 phrase overflow ({write_info['skipped_overflow']})"
        )

    applied: List[dict] = []
    for row in planned:
        token = token_from_ext3_index(row["index"], num_banks=num_banks)
        span = row["payload_len"]
        # Pad with 0x01 (ideographic space), NEVER 0x00. A NUL here would end the
        # zstring early, so this table's sequential walk would find the next
        # record inside our padding and every following entry would shift.
        # padded_token_payload carries that rule plus the oversized-record and
        # trail-00 refusals, so reuse it rather than re-deriving the padding.
        body = padded_token_payload(b"", token, bytes(span))
        assert len(body) == span, (len(body), span)
        at = sb_rom + row["logical"]
        rom[at : at + span] = body
        # terminator byte belongs to the record and stays 0
        rom[at + span] = 0
        applied.append(
            {
                "abs": row["abs"],
                "index": f"{row['index']:04X}",
                "jp": row["jp"][:40],
                "ko": row["ko"],
                "payload_len": span,
                "pad": span - len(token),
            }
        )

    # --- verify every patched record round-trips ----------------------------
    d_new = make_dictionary_ext3(bytes(rom), meta, meta3)
    decode_fail: List[dict] = []
    for row in applied:
        logical = int(row["abs"], 16)
        got = read_encoded_z_safe(rom, sb_rom + logical, max_len=64)
        text = d_new.expand(got[0], tbl) if got else "<no record>"
        stripped = text.rstrip("　 \t")
        row["ok"] = stripped == row["ko"].rstrip("　 \t")
        if not row["ok"]:
            row["decode"] = text[:60]
            decode_fail.append(row)

    report["applied"] = applied
    report["applied_count"] = len(applied)
    report["decode_fail"] = len(decode_fail)
    report["decode_fail_sample"] = decode_fail[:20]
    report["ext3_write"] = write_info

    if decode_fail:
        report["ok"] = False
        args.out_report.parent.mkdir(parents=True, exist_ok=True)
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"REFUSING TO WRITE ROM | {len(decode_fail)} record(s) did not decode "
            f"back to Korean. See {args.out_report}"
        )
        for row in decode_fail[:10]:
            print(f"  {row['abs']} want {row['ko']!r} got {row.get('decode')!r}")
        return 1

    report["ok"] = True
    report["checksum"] = f"{update_ws_checksum(rom):04X}"
    out_rom.parent.mkdir(parents=True, exist_ok=True)
    out_rom.write_bytes(rom)
    args.out_report.parent.mkdir(parents=True, exist_ok=True)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        f"name75 OK | records={len(applied)} unique_phrases={len(unique)} "
        f"decode_fail=0 checksum={report['checksum']}"
    )
    print(f"  skipped: {skipped}")
    for row in applied[:20]:
        print(f"  {row['abs']} [{row['index']}] {row['jp']} -> {row['ko']}")
    if len(applied) > 20:
        print(f"  ... +{len(applied) - 20} more")
    print(f"wrote {out_rom}")
    print(f"report {args.out_report}")
    print("NOW RUN: python tools/verify_nondialogue_text.py --target " f"{out_rom}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
