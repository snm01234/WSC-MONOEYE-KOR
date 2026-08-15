#!/usr/bin/env python3
"""
Rewrite aux battle-text records in place with ext3 tokens.

Same size-preserving mechanism as tools/apply_name75_ko.py, but the aux banks are
a far more dangerous neighbourhood than the bank-75 display table: they also hold
graphics and fixed data tables. Writing a token over one of those is the
bank 64-69 failure (padding walked as opcodes → event error 257/2049). So the
record set is not chosen by this tool at all — it is supplied, already vetted, by
``out/script/aux_block_eligible.json``, and every record is re-checked here.

Eligibility (all enforced again below, never assumed):
  * the address must appear in the eligible list, which came from
    ``find_aux_text_blocks.py`` (contiguous runs of coherent records — real
    strings tile, garbage does not sustain a run) filtered to records whose first
    byte is a dictionary token or 2-byte character lead. That last condition
    matters: many aux records begin with a speaker/portrait id that renders as a
    stray glyph (``5a``→``カ``), and it cannot be distinguished from real leading
    text (``80``→``機`` in ``機銃座は……``), so ambiguous-leading records are excluded
    rather than guessed at.
  * the target must still hold the original bytes
  * payload >= 4 bytes for the token, and padding is ``0x01`` — never ``0x00``,
    which would pull the zstring terminator forward and shift every following
    record
  * the original must not already contain the ext3 magic
  * every rewritten record is re-expanded and must decode back to the intended
    Korean, or the ROM is not written at all

Prefix-preserving mode (``--prefix-rule``)
-----------------------------------------
The "ambiguous leading byte" refusal above is correct but expensive: it blocks
~2,700 records that sampling shows are real battle dialogue carrying one stray
glyph in front (``'アほう……やるな！'``). Rather than *classify* that byte, this mode
**keeps it verbatim** and rewrites only the body after it:

    original :  17 34 18 | いや、大したことじゃないんだが、
    written  :  17 34 18 | E5 18 xx yy 01 01 01 … 00
                ^^^^^^^^   untouched                ^^ terminator stays put

The split is not guessed here either. It comes from
``out/script/aux_prefix_rule.json``, which is produced by
``measure_aux_prefix_rule.py`` on top of the per-record proofs in
``prove_aux_prefix.py``, and this tool **recomputes the prefix length with the
same rule and refuses any record where the two disagree**. Bank 59 uses the
game's own dialogue grammar (``extract_script.split_prefix_body``: ``08 xx``
speaker, ``01`` indent, ``17 xx [08 xx] 18`` window, ``18`` marker); banks 5D/5E
use one code unit. Bank 5C is not in the rule at all — its records are
continuation text, so there is no prefix to preserve.

The two ways to be wrong are not symmetric, which is why preserving is safe:
a prefix taken too long leaves one Japanese glyph in front of the Korean
(cosmetic), while one taken too short overwrites a speaker/portrait id
(functional). Everything ambiguous is resolved toward preserving more.

After running, ``verify_nondialogue_text`` check (iii) is the gate that matters:
these records are inside its scan scope and it is what catches a length or
terminator mistake.

Report: ``out/patch/aux_ko_report.json``.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from apply_name75_ko import ext3_bank_room  # noqa: E402
from apply_safe_unit import padded_token_payload  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from measure_aux_prefix_rule import (  # noqa: E402
    BANK_RULES,
    TEXT_INITIAL_EXCEPTIONS,
    prefix_len,
)
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
from patch_3byte_dict_token import (  # noqa: E402
    EXP3_SEG0,
    INDEX_BASE,
    list_free_ext3_indices,
    token_from_ext3_index,
    write_ext3_dictionary_slots,
)

TOKEN_LEN = 4
DEFAULT_ROM = ROOT / "out/patch/monoeye_ko_ui_work.wsc"
DEFAULT_ELIGIBLE = ROOT / "out/script/aux_block_eligible.json"
DEFAULT_CATALOG = ROOT / "data/aux_text_ko.json"
DEFAULT_PREFIX_RULE = ROOT / "out/script/aux_prefix_rule.json"
DEFAULT_BODY_CATALOG = ROOT / "data/aux_body_ko.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_REPORT = ROOT / "out/patch/aux_ko_report.json"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    ap.add_argument("--out-rom", type=Path, default=None, help="default: --rom")
    ap.add_argument("--base-rom", type=Path, default=None, help="default: original")
    ap.add_argument("--eligible", type=Path, default=DEFAULT_ELIGIBLE)
    ap.add_argument("--names", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument(
        "--prefix-rule",
        type=Path,
        default=DEFAULT_PREFIX_RULE,
        help="enable prefix-preserving rewrite from this measured rule file",
    )
    ap.add_argument(
        "--no-prefix-rule",
        action="store_true",
        help="ignore the prefix rule and keep the original text-initial-only set",
    )
    ap.add_argument(
        "--body-names",
        type=Path,
        default=DEFAULT_BODY_CATALOG,
        help="translations keyed by the BODY text (prefix stripped)",
    )
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--meta", type=Path, default=ROOT / "out/patch/ext_dictionary_meta.json")
    ap.add_argument(
        "--ext3-meta", type=Path, default=ROOT / "out/patch/ext3_dictionary_meta.json"
    )
    ap.add_argument("--out-report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

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
    sb_base, sb_rom = stock_base(base), stock_base(rom)

    eligible = json.loads(args.eligible.read_text(encoding="utf-8"))["records"]
    catalog = {
        r["jp"]: r["ko"]
        for r in json.loads(args.names.read_text(encoding="utf-8"))["entries"]
        if r.get("jp") and r.get("ko")
    }

    def load_catalog(path: Path) -> Dict[str, str]:
        if not path.exists():
            return {}
        spec = json.loads(path.read_text(encoding="utf-8"))
        return {
            r["jp"]: r["ko"]
            for r in spec.get("entries", [])
            if r.get("jp") and r.get("ko")
        }

    body_catalog = load_catalog(args.body_names)

    # Worklist: (logical, bank, prefix_bytes, expected_prefix_hex).
    # prefix_bytes 0 is the original text-initial set; > 0 comes from the
    # measured rule and is re-derived below before anything is written.
    worklist: List[dict] = []
    for rec in eligible:
        worklist.append(
            {
                "logical": int(rec["abs"], 16),
                "abs": rec["abs"],
                "bank": rec["bank"],
                "k": 0,
                "expect_prefix": "",
                "from_prefix_rule": False,
            }
        )
    seen_logical = {w["logical"] for w in worklist}

    prefix_rule_used = False
    if not args.no_prefix_rule and args.prefix_rule.exists():
        spec = json.loads(args.prefix_rule.read_text(encoding="utf-8"))
        if not spec.get("ok"):
            raise SystemExit(
                f"refusing: {args.prefix_rule} is not ok — rerun "
                "tools/measure_aux_prefix_rule.py"
            )
        prefix_rule_used = True
        for bank_key, rows in spec.get("records", {}).items():
            for r in rows:
                logical = int(r["abs"], 16)
                if logical in seen_logical:
                    continue
                seen_logical.add(logical)
                worklist.append(
                    {
                        "logical": logical,
                        "abs": r["abs"],
                        "bank": bank_key,
                        "k": int(r["prefix_bytes"]),
                        "expect_prefix": r["prefix_hex"],
                        "from_prefix_rule": True,
                    }
                )

    planned: List[dict] = []
    skipped: collections.Counter = collections.Counter()
    skip_rows: List[dict] = []

    def skip(reason: str, row: dict) -> None:
        skipped[reason] += 1
        if len(skip_rows) < 60:
            skip_rows.append({"reason": reason, **row})

    for rec in worklist:
        logical = rec["logical"]
        k = rec["k"]
        got_base = read_encoded_z_safe(base, sb_base + logical, max_len=128)
        if not got_base:
            skip("no_record", {"abs": rec["abs"]})
            continue
        payload = got_base[0]
        jp_full = d_base.expand(payload, tbl)
        info = {"abs": rec["abs"], "bank": rec["bank"], "jp": jp_full[:40], "k": k}

        if k:
            # Never trust the rule file: re-derive the split here. A stale or
            # hand-edited file must not be able to move the cut point.
            bank = logical >> 16
            rule = BANK_RULES.get(bank)
            if rule is None:
                skip("no_rule_for_bank", info)
                continue
            k_check = prefix_len(payload, rule)
            if k_check != k or payload[:k].hex().upper() != rec["expect_prefix"]:
                skip("prefix_rule_mismatch", {**info, "k_recomputed": k_check})
                continue
            body = payload[k:]
            key = d_base.expand(body, tbl)
            ko = body_catalog.get(key)
        elif rec.get("from_prefix_rule"):
            expected_full = TEXT_INITIAL_EXCEPTIONS.get(logical)
            if expected_full is None or jp_full != expected_full:
                skip("text_initial_exception_mismatch", info)
                continue
            body = payload
            key = jp_full
            ko = body_catalog.get(key)
        else:
            body = payload
            key = jp_full
            ko = catalog.get(key)

        if not ko:
            skip("no_translation", info)
            continue
        if len(body) < TOKEN_LEN:
            skip("too_short", info)
            continue
        if k == 0 and payload[0] < 0xE0 and logical not in TEXT_INITIAL_EXCEPTIONS:
            # Only the legacy set demands a provably text-initial byte; explicit
            # text-initial exceptions are bound to an exact original sentence.
            skip("ambiguous_leading_byte", info)
            continue
        if any(
            is_ext3_magic(payload[i], payload[i + 1]) for i in range(len(payload) - 1)
        ):
            skip("original_has_ext3_magic", info)
            continue
        got_tgt = read_encoded_z_safe(rom, sb_rom + logical, max_len=128)
        if not got_tgt or got_tgt[0] != payload:
            skip("already_changed", info)
            continue
        ko_n = normalize_ko_text(ko)
        enc = try_encode_ko_text(
            ko_n, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if enc is None:
            skip("encode_fail", {**info, "ko": ko})
            continue
        if b"\x00" in enc:
            skip("ko_contains_nul", {**info, "ko": ko})
            continue
        planned.append(
            {
                "logical": logical,
                "abs": rec["abs"],
                "bank": rec["bank"],
                "jp": key,
                "jp_full": jp_full,
                "ko": ko_n,
                "payload_len": len(payload),
                "prefix": payload[:k],
                "phrase": enc,
            }
        )
        if args.limit and len(planned) >= args.limit:
            break

    # --- allocate ext3 indices, respecting per-bank phrase room --------------
    room = ext3_bank_room(rom, num_banks)
    free_by_bank: Dict[int, List[int]] = {}
    for idx in list_free_ext3_indices(rom, num_banks=num_banks):
        free_by_bank.setdefault((idx - INDEX_BASE) >> 12, []).append(idx)

    unique: Dict[bytes, int] = {}
    slot_payload: Dict[int, bytes] = {}
    need_total = sum(len(p) + 1 for p in {r["phrase"] for r in planned})
    room_total = sum(room.values())
    if need_total > room_total:
        raise SystemExit(
            f"refusing: need {need_total} ext3 phrase bytes, only {room_total} free"
        )
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
                    f"refusing: no ext3 bank has {need} bytes plus a free slot"
                )
            unique[phrase] = idx
            slot_payload[idx] = phrase
        row["index"] = idx

    report = {
        "generated_by": "tools/apply_aux_ko.py",
        "rom_in": str(args.rom),
        "rom_out": str(out_rom),
        "marker": f"{marker:04X}",
        "eligible_records": len(eligible),
        "prefix_rule_used": prefix_rule_used,
        "prefix_rule": str(args.prefix_rule) if prefix_rule_used else None,
        "worklist_records": len(worklist),
        "records_planned": len(planned),
        "planned_prefix_preserved": sum(1 for r in planned if r["prefix"]),
        "unique_phrases": len(unique),
        "ext3_phrase_bytes_needed": need_total,
        "ext3_bank_room_after": {
            f"{EXP3_SEG0 + b:02X}": r for b, r in sorted(room.items())
        },
        "skipped": dict(skipped),
        "skipped_sample": skip_rows,
    }

    if args.dry_run or not planned:
        report["dry_run"] = True
        report["applied"] = []
        args.out_report.parent.mkdir(parents=True, exist_ok=True)
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"{'DRY RUN' if args.dry_run else 'NOTHING TO DO'} | planned="
            f"{len(planned)} unique={len(unique)} skipped={dict(skipped)}"
        )
        return 0

    write_info = write_ext3_dictionary_slots(rom, slot_payload, num_banks=num_banks)
    if write_info.get("skipped_overflow"):
        raise SystemExit(
            f"refusing: ext3 phrase overflow ({write_info['skipped_overflow']})"
        )

    applied: List[dict] = []
    for row in planned:
        token = token_from_ext3_index(row["index"], num_banks=num_banks)
        span = row["payload_len"]
        prefix = row["prefix"]
        k = len(prefix)
        body_span = span - k
        # 0x01 (ideographic space) padding, never 0x00 — a NUL here ends the
        # zstring early and the sequential walk would shift every later record.
        body = padded_token_payload(b"", token, bytes(body_span))
        assert len(body) == body_span, (len(body), body_span)
        at = sb_rom + row["logical"]
        # The prefix bytes are left exactly as they are: this is the whole point
        # of the mode, and it is asserted rather than assumed.
        assert bytes(rom[at : at + k]) == prefix, row["abs"]
        rom[at + k : at + span] = body
        rom[at + span] = 0
        applied.append(
            {
                "abs": row["abs"],
                "bank": row["bank"],
                "index": f"{row['index']:04X}",
                "jp": row["jp"][:40],
                "ko": row["ko"],
                "payload_len": span,
                "prefix_bytes": k,
                "prefix_hex": prefix.hex().upper(),
                "pad": body_span - len(token),
            }
        )

    d_new = make_dictionary_ext3(bytes(rom), meta, meta3)
    decode_fail: List[dict] = []
    for row in applied:
        logical = int(row["abs"], 16)
        k = row["prefix_bytes"]
        got = read_encoded_z_safe(rom, sb_rom + logical, max_len=128)
        if not got:
            row["ok"] = False
            row["decode"] = "<no record>"
            decode_fail.append(row)
            continue
        raw = got[0]
        # Two things must hold: the preserved prefix is byte-identical, and the
        # body alone decodes to exactly the intended Korean.
        prefix_ok = raw[:k].hex().upper() == row["prefix_hex"]
        text = d_new.expand(raw[k:], tbl)
        row["ok"] = prefix_ok and text.rstrip("　 \t") == row["ko"].rstrip("　 \t")
        if not row["ok"]:
            row["decode"] = text[:60]
            row["prefix_ok"] = prefix_ok
            decode_fail.append(row)

    report["applied"] = applied
    report["applied_count"] = len(applied)
    report["decode_fail"] = len(decode_fail)
    report["decode_fail_sample"] = decode_fail[:20]
    report["by_bank"] = dict(collections.Counter(r["bank"] for r in applied))
    report["prefix_preserved"] = sum(1 for r in applied if r["prefix_bytes"])
    report["prefix_len_histogram"] = dict(
        collections.Counter(str(r["prefix_bytes"]) for r in applied)
    )
    report["ext3_write"] = write_info

    if decode_fail:
        report["ok"] = False
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"REFUSING TO WRITE ROM | {len(decode_fail)} record(s) did not decode "
            f"back to Korean. See {args.out_report}"
        )
        return 1

    report["ok"] = True
    report["checksum"] = f"{update_ws_checksum(rom):04X}"
    out_rom.write_bytes(rom)
    args.out_report.parent.mkdir(parents=True, exist_ok=True)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"aux OK | records={len(applied)} unique={len(unique)} decode_fail=0 "
        f"checksum={report['checksum']}"
    )
    print(f"  by bank: {report['by_bank']}")
    print(
        f"  prefix-preserved: {report['prefix_preserved']} "
        f"(길이분포 {report['prefix_len_histogram']})"
    )
    print(f"  skipped: {dict(skipped)}")
    for row in applied[:12]:
        print(f"  {row['abs']} [{row['index']}] {row['jp'][:26]} -> {row['ko'][:26]}")
    print(f"wrote {out_rom}")
    print(f"NOW RUN: python tools/verify_nondialogue_text.py --target {out_rom}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
