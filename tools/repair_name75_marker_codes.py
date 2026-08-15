#!/usr/bin/env python3
"""
Restore the unit status icon code in already-applied name75 ext3 phrases.

Symptom this repairs
--------------------
A strengthened unit's row in the unit list drew the wrong icon after its name.

Cause: ``monoeye.tbl`` decodes E6C5, E6C9 and E736 all to the placeholder '█',
so ``Tbl.char_to_code['█']`` is E6C5 and any decode/re-encode round trip rewrites
the icon to E6C5. Measured on the tip, 181 name75 records carry a '█'-class code
and 168 came back wrong:

    E736             -> E6C5              155 records
    E6C5, E6C9       -> E6C5, E6C5         13 records
    E6C5, E6C9, E736 -> E6C5, E6C5, E6C5   13 records

``tools/apply_name75_ko.py`` now pins these codes to the original record's own
codes (see :mod:`tbl_code_prefs`), but records already written cannot be re-applied
— the writer refuses a record that no longer holds the original bytes. This tool
repairs them where they live instead: the codes are the same width, so each wrong
2-byte code inside the ext3 phrase is overwritten with the original one. Phrase
length, the record body and the pointer table are untouched.

Fail-closed
-----------
* target must be a 16 MiB expanded ROM with ext3 installed
* the target record must be exactly an ext3 token (``E5 18 xx yy``) plus ``0x01``
  padding — anything else is reported, not patched
* the phrase must hold the *same number* of '█'-class codes as the original,
  otherwise the positional mapping is unproven and the phrase is refused
* one ext3 index shared by records that need different codes is refused
* ``--dry-run`` is the default; ``--commit`` backs the target up first

Report: ``out/patch/name75_marker_code_repair.json``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import NAME75_RANGES, _walk_zstring_range  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    find_rom,
    is_ext3_magic,
    is_expanded_rom,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
    ws_header,
)
from tbl_code_prefs import find_codes, flatten_codes, marker_codes  # noqa: E402

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_eventfix_work.wsc"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_META = ROOT / "out/patch/ext_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/name75_marker_code_repair.json"
BACKUP_ROOT = ROOT / "out/patch/backup"

TOKEN_LEN = 4
PAD_BYTE = 0x01


def code_offsets(phrase: bytes, wanted: frozenset[int]) -> List[tuple[int, int]]:
    """[(offset_in_phrase, code)] for '█'-class codes, scanned two bytes at a time."""
    out: List[tuple[int, int]] = []
    i = 0
    while i < len(phrase) - 1:
        code = (phrase[i] << 8) | phrase[i + 1]
        if code in wanted:
            out.append((i, code))
            i += 2
            continue
        i += 1
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=None, help="original ROM (auto-detected)")
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="default")
    args = ap.parse_args(argv)

    if args.commit and args.dry_run:
        raise SystemExit("--commit and --dry-run are mutually exclusive")
    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a report over a .wsc")

    jp_path = args.jp or find_rom(ROOT)
    orig = bytes(load_rom(jp_path))
    rom = bytearray(load_rom(args.target))
    if not is_expanded_rom(rom):
        raise SystemExit("refusing: target is not a 16 MiB expanded ROM")
    sb = stock_base(rom)

    tbl = Tbl.load(args.tbl)
    meta = load_ext_meta(args.meta)
    meta3 = load_ext_meta(args.ext3_meta)
    if int(meta3.get("num_banks") or 0) <= 0:
        raise SystemExit("refusing: ext3 banks are not installed (num_banks=0)")
    d_base = Dictionary(orig)
    d_tgt = make_dictionary_ext3(bytes(rom), meta, meta3)
    markers = marker_codes(tbl)

    # --- plan ---------------------------------------------------------------
    want_by_index: Dict[int, List[int]] = {}
    rows: List[dict] = []
    refused: List[dict] = []
    already_ok = 0
    for lo, hi in NAME75_RANGES:
        for logical, payload, _kind in _walk_zstring_range(
            orig, lo, hi, region="name75", max_len=64
        ):
            flat = flatten_codes(payload, d_base)
            want = find_codes(flat, markers)
            if not want:
                continue
            got_rec = read_encoded_z_safe(rom, sb + logical, max_len=64)
            body = got_rec[0] if got_rec else b""
            if body == payload:
                continue  # not localized
            if len(body) < TOKEN_LEN or not is_ext3_magic(body[0], body[1]):
                refused.append(
                    {
                        "abs": f"{logical:06X}",
                        "reason": "target record is not an ext3 token",
                        "body_hex": body[:8].hex(),
                    }
                )
                continue
            if any(b != PAD_BYTE for b in body[TOKEN_LEN:]):
                refused.append(
                    {
                        "abs": f"{logical:06X}",
                        "reason": "ext3 token followed by non-0x01 padding",
                        "body_hex": body[:16].hex(),
                    }
                )
                continue
            index = dict_index_from_ext3_token(body[0], body[1], body[2], body[3])
            prev = want_by_index.get(index)
            if prev is not None and prev != want:
                refused.append(
                    {
                        "abs": f"{logical:06X}",
                        "index": f"{index:04X}",
                        "reason": "ext3 phrase shared by records needing different "
                        "icon codes",
                        "want": [f"{c:04X}" for c in want],
                        "other": [f"{c:04X}" for c in prev],
                    }
                )
                continue
            want_by_index[index] = want

            phrase_abs = d_tgt.entry_abs(index)
            phrase = d_tgt.raw_entry(index, max_len=128)
            found = code_offsets(phrase, markers)
            got = [c for _off, c in found]
            if len(got) != len(want):
                refused.append(
                    {
                        "abs": f"{logical:06X}",
                        "index": f"{index:04X}",
                        "reason": "icon code count differs between the original "
                        "record and the phrase",
                        "want": [f"{c:04X}" for c in want],
                        "got": [f"{c:04X}" for c in got],
                    }
                )
                continue
            if got == want:
                already_ok += 1
                continue
            rows.append(
                {
                    "abs": f"{logical:06X}",
                    "index": f"{index:04X}",
                    "phrase_abs": f"{phrase_abs:07X}",
                    "jp": d_base.expand(payload, tbl)[:40],
                    "want": [f"{c:04X}" for c in want],
                    "got": [f"{c:04X}" for c in got],
                    "writes": [
                        {"phrase_off": off, "from": f"{c:04X}", "to": f"{w:04X}"}
                        for (off, c), w in zip(found, want)
                        if c != w
                    ],
                }
            )

    # --- apply --------------------------------------------------------------
    bytes_written = 0
    for row in rows:
        phrase_abs = int(row["phrase_abs"], 16)
        for w in row["writes"]:
            at = phrase_abs + w["phrase_off"]
            code = int(w["to"], 16)
            rom[at] = (code >> 8) & 0xFF
            rom[at + 1] = code & 0xFF
            bytes_written += 2

    # --- verify -------------------------------------------------------------
    d_after = make_dictionary_ext3(bytes(rom), meta, meta3)
    verify_fail: List[dict] = []
    for row in rows:
        index = int(row["index"], 16)
        got = [f"{c:04X}" for c in find_codes(d_after.raw_entry(index, 128), markers)]
        if got != row["want"]:
            verify_fail.append({**row, "after": got})

    by_change: Dict[str, int] = defaultdict(int)
    for row in rows:
        for w in row["writes"]:
            by_change[f"{w['from']}->{w['to']}"] += 1

    checksum_before = f"{ws_header(load_rom(args.target))['checksum']:04X}"
    backup = None
    checksum_after = None
    ok = not verify_fail and not refused
    if args.commit and ok:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        (BACKUP_ROOT / stamp).mkdir(parents=True, exist_ok=True)
        backup = BACKUP_ROOT / stamp / args.target.name
        shutil.copy2(args.target, backup)
        checksum_after = f"{update_ws_checksum(rom):04X}"
        args.target.write_bytes(rom)

    report = {
        "ok": ok,
        "generated_by": "tools/repair_name75_marker_codes.py",
        "mode": "commit" if args.commit else "dry-run",
        "original": str(jp_path),
        "target": str(args.target),
        "marker_codes": [f"{c:04X}" for c in sorted(markers)],
        "records_with_marker_already_correct": already_ok,
        "phrases_repaired": len(rows),
        "codes_rewritten": bytes_written // 2,
        "by_change": dict(by_change),
        "verify_failures": verify_fail,
        "refused": refused,
        "checksum_before": checksum_before,
        "checksum_after": checksum_after,
        "backup": str(backup) if backup else None,
        "revert": f"copy {backup} back" if backup else "no write performed",
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"icon codes ({', '.join(report['marker_codes'])})")
    print(f"phrases already correct : {already_ok}")
    print(f"phrases repaired        : {len(rows)}  ({bytes_written // 2} codes)")
    for k, v in sorted(by_change.items()):
        print(f"    {k}: {v}")
    print(f"refused                 : {len(refused)}")
    for row in refused[:10]:
        print(f"    {row.get('abs')} {row['reason']}")
    if verify_fail:
        print(f"VERIFY FAILED on {len(verify_fail)} phrase(s) — ROM not written")
    elif args.commit and ok:
        print(f"backup   : {backup}")
        print(f"checksum : {checksum_before} → {checksum_after}")
    else:
        print("dry-run: nothing written. Add --commit to apply.")
    print(f"-> {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
