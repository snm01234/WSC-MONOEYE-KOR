#!/usr/bin/env python3
"""Restore marked-baseline bytes for ext_dict false-dialogue / event hits."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from event_record_heuristics import looks_like_event_body  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    dict_index_from_token,
    dict_token_safe_in_zstring,
    is_dict_token,
    load_rom,
    read_encoded_z_safe,
    update_ws_checksum,
)
from normalize_ko_text import encode_ko_text, normalize_ko_text  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--base",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_marked.wsc",
    )
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_full.json",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data/translations_seed_hook96.json",
    )
    ap.add_argument(
        "--meta",
        type=Path,
        default=ROOT / "out/patch/ext_dictionary_meta.json",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out/patch/hangul_patch.tbl",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/ext_false_dialogue_repair.json",
    )
    ap.add_argument(
        "--also-zeropad",
        action="store_true",
        help="Also restore records whose trailing bytes were zero-padded away",
    )
    args = ap.parse_args()

    rom = bytearray(load_rom(args.rom))
    base = load_rom(args.base)
    meta = load_ext_meta(args.meta)
    tbl = Tbl.load(args.tbl)
    stock = int(meta["stock_count"])
    slots = int(meta["slot_count"])
    sheet = json.loads(args.sheet.read_text(encoding="utf-8"))["lines"]
    seed_abs = {
        int(row["abs"], 16)
        for row in json.loads(args.seed.read_text(encoding="utf-8"))["lines"]
    }

    restored = []
    for line in sheet:
        abs_off = int(line["abs"], 16)
        if abs_off in seed_abs:
            continue
        base_got = read_encoded_z_safe(base, abs_off)
        if base_got is None:
            continue
        payload = base_got[0]
        cur = bytes(rom[abs_off : abs_off + len(payload)])
        if cur == payload:
            continue
        _base_prefix, base_body, _ = split_prefix_body(payload)
        _cur_prefix, cur_body, _ = split_prefix_body(cur)
        if len(cur_body) < 2 or not is_dict_token(cur_body[0]):
            continue
        idx = dict_index_from_token(cur_body[0], cur_body[1])
        if not (stock <= idx < stock + slots):
            continue
        zero_pad = cur_body[2:].count(0) >= max(2, (len(base_body) - 2) // 2)
        # Restore event/control false-dialogue only. Do NOT use bare zeropad
        # alone — that also matches legitimate size-preserving KO pads.
        eventish = looks_like_event_body(base_body) or payload.startswith(
            (b"\x01\x0C\x01", b"\x02\x80")
        )
        # Ext index 0xF00 → token FF 00: zstring ends inside the token and
        # orphan zeros break the Sig/Blade event stream.
        nul_token = not dict_token_safe_in_zstring(idx)
        hotspot = abs_off in {
            0x65CB0F,
            0x65CB23,
            0x65CBC3,
            0x65CBD7,
            0x690A0D,
            0x690A19,
            0x6044F9,
        }
        if not (
            eventish
            or hotspot
            or nul_token
            or (args.also_zeropad and zero_pad and eventish)
        ):
            continue
        rom[abs_off : abs_off + len(payload)] = payload
        reason = (
            "nul_token"
            if nul_token
            else ("event_body" if eventish else "hotspot")
        )
        restored.append(
            {
                "abs": f"{abs_off:06X}",
                "jp": line.get("jp"),
                "ko": line.get("ko"),
                "restored": payload.hex(),
                "was": cur.hex(),
                "reason": reason,
                "dict_index": idx,
            }
        )

    d = make_dictionary(rom, meta)
    marker = 0xE3DB
    seed_fail = 0
    for row in json.loads(args.seed.read_text(encoding="utf-8"))["lines"]:
        abs_off = int(row["abs"], 16)
        ko = normalize_ko_text(row["ko"])
        body = split_prefix_body(read_encoded_z_safe(rom, abs_off)[0])[1]
        exp = encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if d.expand(body, tbl) != d.expand(exp, tbl):
            seed_fail += 1

    report = {
        "restored_count": len(restored),
        "seed_fail": seed_fail,
        "checksum": f"{update_ws_checksum(rom):04X}",
        "restored": restored,
        "notes": [
            "Restored marked baseline over ext_dict false dialogue/event hits.",
            "Error 51983=0xCB0F maps to 65:CB0F hotspot.",
            "Error 2573=0x0A0D maps to bank69 offset cluster (02 80 xx control).",
            "Ext index 0xF00 (token FF 00) is zstring-unsafe — restores nul_token hits.",
        ],
    }
    args.out_rom.write_bytes(rom)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Restored {len(restored)} records | seed_fail={seed_fail} "
        f"checksum={report['checksum']}"
    )
    for row in restored[:25]:
        print(f"  {row['abs']} {row['jp']!r}")
    print(f"Wrote {args.out_rom}")
    print(f"Wrote {args.out_report}")
    if seed_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
