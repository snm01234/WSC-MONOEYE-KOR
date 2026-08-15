#!/usr/bin/env python3
"""Read-only: original vs main vs 5D-candidate for Heero portrait/quote lookup.

The native-only 5D body rewrite did not change the measured Sig portrait +
empty box.  This hunts for pointer/table/face data that original still has
and the patched ROMs lost or retargeted.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import NAME75_RANGES, _walk_zstring_range
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CAND = ROOT / "out/patch/battle_metadata5d_native_only_candidate.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
INV = ROOT / "out/script/battle_dialogue_structure_inventory.csv"
OUT = ROOT / "out/patch/_heero_orig_vs_current_diag.json"

HEERO_BLOCK = (0x5E00C2, 0x5E03A2)  # metadata 5C tail then Heero 5D run
HEERO_BLOCK2 = (0x5E5E34, 0x5E5F40)
NAME_NEEDLES = ("ヒイロ", "シグ", "ウイングゼロカスタム", "ウイングゼロ")
FACE_NEEDLES = ("フェイス", "ヒイロ１", "シグ１")
QUOTE_PTRS = (
    0x5E00C8,
    0x5E00E1,
    0x5E00F5,
    0x5E0109,
    0x5E0143,
    0x5E016F,
    0x5E0274,
    0x5E5E34,
    0x5D0018,
    0x5D0003,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rec(rom: bytes, logical: int, n: int = 48) -> dict:
    base = stock_base(rom)
    start = base + logical
    raw = bytes(rom[start : start + n])
    got = read_encoded_z_safe(rom, start, max_len=128)
    live = bytes(got[0]) if got else b""
    term = got[1] if got else None
    return {
        "raw48": raw.hex().upper(),
        "zhex": live.hex().upper(),
        "zlen": len(live),
        "term": term,
        "first": f"{live[0]:02X}" if live else "",
        "starts_e518": live.startswith(b"\xE5\x18"),
        "has_5d": live[:1] == b"\x5D",
        "meta_then_e518": live.startswith(b"\x5D\xE5\x18"),
        "has_native_token": (
            (len(live) >= 1 and live[0] in range(0xF0, 0xFF))
            or (len(live) >= 2 and live[1] in range(0xF0, 0xFF))
        ),
    }


def decode(rom: bytes, d, tbl: Tbl, logical: int) -> str:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=128)
    if not got:
        return ""
    body = bytes(got[0])
    if body[:1] in (b"\x5D", b"\x5C", b"\x5E", b"\x01", b"\x0F"):
        body = body[1:]
    return d.expand(body, tbl).rstrip("\u3000 ")


def find_le16(rom: bytes, value: int, logical_lo: int, logical_hi: int) -> list[int]:
    base = stock_base(rom)
    needle = value.to_bytes(2, "little")
    start = base + logical_lo
    end = base + logical_hi
    hits = []
    pos = start
    data = bytes(rom)
    while True:
        found = data.find(needle, pos, end)
        if found < 0:
            break
        hits.append(found - base)
        pos = found + 1
    return hits


def walk_names(rom: bytes, d, tbl: Tbl, ranges: list[tuple[int, int, str]]) -> list[dict]:
    rows = []
    for lo, hi, region in ranges:
        for logical, payload, kind in _walk_zstring_range(
            rom, lo, hi, region=region, max_len=96
        ):
            text = d.expand(bytes(payload), tbl)
            if any(n in text for n in NAME_NEEDLES + FACE_NEEDLES):
                rows.append(
                    {
                        "abs": f"{logical:06X}",
                        "region": region,
                        "kind": kind,
                        "jp": text,
                        "hex": bytes(payload).hex().upper(),
                        "len": len(payload),
                    }
                )
    return rows


def context_diff(orig: bytes, cur: bytes, logical: int, before: int = 16, after: int = 48) -> dict:
    ob = stock_base(orig)
    cb = stock_base(cur)
    o = bytes(orig[ob + logical - before : ob + logical + after])
    c = bytes(cur[cb + logical - before : cb + logical + after])
    diffs = [i - before for i, (a, b) in enumerate(zip(o, c)) if a != b]
    return {
        "abs": f"{logical:06X}",
        "orig": o.hex().upper(),
        "cur": c.hex().upper(),
        "same": o == c,
        "diff_rel": diffs[:40],
        "diff_count": len(diffs),
    }


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    main_rom = bytes(load_rom(MAIN))
    cand = bytes(load_rom(CAND)) if CAND.exists() else b""
    tbl = Tbl.load(TBL_PATH)
    orig_d = Dictionary(original)
    main_d = make_dictionary_ext3(main_rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    cand_d = make_dictionary_ext3(cand, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)) if cand else None

    # --- quote records: original / main / candidate ---
    quote_rows = []
    with INV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            logical = int(row["record_start"], 16)
            in_heero = HEERO_BLOCK[0] <= logical < HEERO_BLOCK[1] or HEERO_BLOCK2[0] <= logical < HEERO_BLOCK2[1]
            if not in_heero and (row.get("metadata_hex") or "").upper() != "5D":
                continue
            if not in_heero:
                continue
            o = rec(original, logical)
            m = rec(main_rom, logical)
            c = rec(cand, logical) if cand else {}
            quote_rows.append(
                {
                    "abs": f"{logical:06X}",
                    "inv_meta": row.get("metadata_hex"),
                    "action": row.get("action"),
                    "inv_current": row.get("current_payload_hex"),
                    "inv_render": row.get("current_render"),
                    "orig": o,
                    "main": m,
                    "cand": c,
                    "jp": decode(original, orig_d, tbl, logical),
                    "ko_main": decode(main_rom, main_d, tbl, logical),
                    "ko_cand": decode(cand, cand_d, tbl, logical) if cand_d else "",
                    "orig_eq_main_raw": o["raw48"] == m["raw48"],
                    "main_eq_cand_raw": bool(c) and m["raw48"] == c["raw48"],
                }
            )

    # Lost metadata: original starts with 5D, current does not
    lost_meta = [
        r
        for r in quote_rows
        if r["orig"]["has_5d"] and not r["main"]["has_5d"]
    ]
    still_e518_on_cand = [
        r["abs"]
        for r in quote_rows
        if r.get("cand") and (r["cand"].get("starts_e518") or r["cand"].get("meta_then_e518"))
    ]
    cand_native = [
        r["abs"]
        for r in quote_rows
        if r.get("cand") and r["cand"].get("has_native_token") and not r["cand"].get("starts_e518")
    ]

    # --- LE16 / far pointers to quote starts ---
    ptr_report = []
    scan_ranges = [
        (0x000000, 0x200000, "low_code"),
        (0x5C0000, 0x5D0000, "bank5c"),
        (0x5D0000, 0x5F0000, "bank5d5e"),
        (0x750000, 0x760000, "bank75"),
        (0x7A0000, 0x7F0000, "prog"),
    ]
    for addr in QUOTE_PTRS:
        off16 = addr & 0xFFFF
        bank = addr >> 16
        entry = {"target": f"{addr:06X}", "off16": f"{off16:04X}", "bank": f"{bank:02X}", "sites": []}
        for lo, hi, label in scan_ranges:
            orig_hits = find_le16(original, off16, lo, hi)
            main_hits = find_le16(main_rom, off16, lo, hi)
            lost = sorted(set(orig_hits) - set(main_hits))
            gained = sorted(set(main_hits) - set(orig_hits))
            if not orig_hits and not main_hits:
                continue
            # keep only sites where neighboring 2 bytes look like a table, or site itself differs
            interesting = []
            for site in orig_hits[:80]:
                octx = context_diff(original, main_rom, site, before=8, after=8)
                interesting.append(
                    {
                        "site": f"{site:06X}",
                        "label": label,
                        "same": octx["same"],
                        "orig": octx["orig"],
                        "cur": octx["cur"],
                    }
                )
            changed = [x for x in interesting if not x["same"]]
            if lost or gained or changed:
                entry["sites"].append(
                    {
                        "range": label,
                        "orig_count": len(orig_hits),
                        "main_count": len(main_hits),
                        "lost": [f"{x:06X}" for x in lost[:30]],
                        "gained": [f"{x:06X}" for x in gained[:30]],
                        "changed_context": changed[:20],
                    }
                )
        ptr_report.append(entry)

    # 24-bit logical far pointers: bank, lo, hi or lo, hi, bank
    far_hits = []
    for addr in QUOTE_PTRS:
        patterns = [
            bytes([addr & 0xFF, (addr >> 8) & 0xFF, (addr >> 16) & 0xFF]),
            bytes([(addr >> 16) & 0xFF, addr & 0xFF, (addr >> 8) & 0xFF]),
        ]
        for i, pat in enumerate(patterns):
            for rom_name, rom in (("orig", original), ("main", main_rom)):
                base = stock_base(rom)
                data = bytes(rom)
                pos = base
                end = base + 0x800000
                found_at = []
                while True:
                    found = data.find(pat, pos, end)
                    if found < 0:
                        break
                    found_at.append(found - base)
                    pos = found + 1
                    if len(found_at) >= 40:
                        break
                far_hits.append(
                    {
                        "target": f"{addr:06X}",
                        "pat": pat.hex().upper(),
                        "kind": "le24" if i == 0 else "bank_le16",
                        "rom": rom_name,
                        "count": len(found_at),
                        "sites": [f"{x:06X}" for x in found_at[:20]],
                    }
                )

    # --- name/face zstrings in original, compare payloads ---
    name_ranges = [
        (0x5C0000, 0x5C7900, "bank5c"),
        (0x75B000, 0x75E800, "bank75"),
        (0x5F3662, 0x5F7BCC, "dict5f"),
    ]
    orig_names = walk_names(original, orig_d, tbl, name_ranges)
    name_cmp = []
    for row in orig_names:
        logical = int(row["abs"], 16)
        m = rec(main_rom, logical, n=64)
        o = rec(original, logical, n=64)
        name_cmp.append(
            {
                **row,
                "main_hex": m["zhex"],
                "main_text": decode(main_rom, main_d, tbl, logical),
                "payload_same": o["zhex"] == m["zhex"],
                "ctx": context_diff(original, main_rom, logical, before=24, after=64),
            }
        )

    # --- 08 xx face commands near Heero scenario lines ---
    heero_event_addrs = []
    sheet = ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv"
    if sheet.exists():
        with sheet.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                jp = row.get("original_jp") or row.get("jp") or ""
                if "ヒイロ" in jp:
                    try:
                        heero_event_addrs.append(int(row.get("record_start") or row.get("abs") or "0", 16))
                    except ValueError:
                        pass

    event_08 = []
    for addr in heero_event_addrs[:80]:
        if addr < 0x590000 or addr >= 0x700000:
            continue
        octx = context_diff(original, main_rom, addr, before=32, after=64)
        # count 08 xx in orig vs cur window
        o = bytes.fromhex(octx["orig"])
        c = bytes.fromhex(octx["cur"])
        def faces(buf: bytes) -> list[str]:
            out = []
            for i in range(len(buf) - 1):
                if buf[i] == 0x08:
                    out.append(f"{buf[i+1]:02X}")
            return out
        event_08.append(
            {
                "abs": f"{addr:06X}",
                "same": octx["same"],
                "orig_08": faces(o),
                "cur_08": faces(c),
                "diff_count": octx["diff_count"],
            }
        )

    # --- bank 5C monotonic u16 table entries that look like F0-FE tokens and differ ---
    table_corrupt = []
    ob = stock_base(original)
    mb = stock_base(main_rom)
    start, end = 0x5C0000, 0x5C8000
    i = start
    while i + 4 < end:
        ov = int.from_bytes(original[ob + i : ob + i + 2], "little")
        mv = int.from_bytes(main_rom[mb + i : mb + i + 2], "little")
        if ov != mv and original[ob + i] in range(0xF0, 0xFF):
            table_corrupt.append(
                {
                    "abs": f"{i:06X}",
                    "orig": f"{ov:04X}",
                    "main": f"{mv:04X}",
                    "orig_bytes": original[ob + i : ob + i + 2].hex().upper(),
                    "main_bytes": main_rom[mb + i : mb + i + 2].hex().upper(),
                }
            )
        i += 2
        if len(table_corrupt) >= 80:
            break

    # Heero block first-byte histogram orig vs main vs cand
    def first_bytes(rom: bytes, lo: int, hi: int) -> Counter:
        c: Counter = Counter()
        for r in quote_rows:
            a = int(r["abs"], 16)
            if lo <= a < hi:
                key = "orig" if rom is original else "main" if rom is main_rom else "cand"
                fb = r[key]["first"] if key in r else ""
                c[fb] += 1
        return c

    summary = {
        "identities": {
            "original": sha(ORIGINAL),
            "main": sha(MAIN),
            "candidate": sha(CAND) if CAND.exists() else None,
        },
        "heero_quote_count": len(quote_rows),
        "lost_5d_metadata_on_main": [
            {"abs": r["abs"], "orig_first": r["orig"]["first"], "main_first": r["main"]["first"], "jp": r["jp"], "main_z": r["main"]["zhex"][:40]}
            for r in lost_meta
        ],
        "lost_5d_count": len(lost_meta),
        "still_e518_on_candidate": still_e518_on_cand,
        "candidate_native_count": len(cand_native),
        "main_starts_e518": [r["abs"] for r in quote_rows if r["main"]["starts_e518"]],
        "main_meta_then_e518": [r["abs"] for r in quote_rows if r["main"]["meta_then_e518"]],
        "empty_ko_main": [r["abs"] for r in quote_rows if r["orig"]["has_5d"] and not r["ko_main"]],
        "empty_ko_cand": [r["abs"] for r in quote_rows if r["orig"]["has_5d"] and not r["ko_cand"]],
        "name_hits": len(name_cmp),
        "name_payload_changed": [r for r in name_cmp if not r["payload_same"]],
        "event_08_changed": [r for r in event_08 if not r["same"] or r["orig_08"] != r["cur_08"]],
        "event_08_sample": event_08[:20],
        "ptr_changed": [p for p in ptr_report if p["sites"]],
        "far_ptr_orig_vs_main": [
            {
                "target": a["target"],
                "kind": a["kind"],
                "orig_count": next(x["count"] for x in far_hits if x["target"] == a["target"] and x["kind"] == a["kind"] and x["rom"] == "orig"),
                "main_count": next(x["count"] for x in far_hits if x["target"] == a["target"] and x["kind"] == a["kind"] and x["rom"] == "main"),
                "orig_sites": next(x["sites"] for x in far_hits if x["target"] == a["target"] and x["kind"] == a["kind"] and x["rom"] == "orig"),
                "main_sites": next(x["sites"] for x in far_hits if x["target"] == a["target"] and x["kind"] == a["kind"] and x["rom"] == "main"),
            }
            for a in far_hits
            if a["rom"] == "orig"
        ],
        "bank5c_f0fe_u16_diffs": table_corrupt[:40],
        "bank5c_f0fe_u16_diff_count": len(table_corrupt),
        "quote_sample": [
            {
                "abs": r["abs"],
                "jp": r["jp"],
                "ko_main": r["ko_main"],
                "ko_cand": r["ko_cand"],
                "orig_first": r["orig"]["first"],
                "main_first": r["main"]["first"],
                "cand_first": r.get("cand", {}).get("first"),
                "orig_z": r["orig"]["zhex"][:48],
                "main_z": r["main"]["zhex"][:48],
                "cand_z": r.get("cand", {}).get("zhex", "")[:48],
            }
            for r in quote_rows[:25]
        ],
    }

    # Compact far ptr mismatches
    far_mismatch = []
    for row in summary["far_ptr_orig_vs_main"]:
        if row["orig_count"] != row["main_count"] or row["orig_sites"] != row["main_sites"]:
            far_mismatch.append(row)
    summary["far_ptr_mismatches"] = far_mismatch

    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(
        {
            "out": str(OUT),
            "heero_quote_count": summary["heero_quote_count"],
            "lost_5d_count": summary["lost_5d_count"],
            "still_e518_on_candidate": len(still_e518_on_cand),
            "candidate_native_count": summary["candidate_native_count"],
            "empty_ko_main": len(summary["empty_ko_main"]),
            "empty_ko_cand": len(summary["empty_ko_cand"]),
            "name_changed": len(summary["name_payload_changed"]),
            "event_08_changed": len(summary["event_08_changed"]),
            "ptr_targets_with_changes": len(summary["ptr_changed"]),
            "far_ptr_mismatches": len(far_mismatch),
            "bank5c_f0fe_u16_diff_count": summary["bank5c_f0fe_u16_diff_count"],
            "lost_5d_sample": summary["lost_5d_metadata_on_main"][:12],
            "name_changed_sample": [
                {"abs": r["abs"], "jp": r["jp"], "main_text": r["main_text"]}
                for r in summary["name_payload_changed"][:15]
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
