#!/usr/bin/env python3
"""Diagnostic scan: alternate live sources for Master Asia こい/이 멍청한 놈이！！."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base, token_from_dict_index  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIG = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
A = ROOT / "out/patch/domon_ko_leak_ab_a_jp_restore_candidate.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/domon_ko_leak_live_source_scan.json"
INV = ROOT / "out/script/battle_dialogue_structure_inventory.csv"
EXT = {"stock_count": 3831, "slot_count": 265, "ext_ptr_off": "0000", "ext_seg": "10", "ext_in_expansion": True}
EXT3 = {"num_banks": 16, "exp_seg0": "11"}


def expand_body(d: Dictionary, body: bytes, tbl: Tbl) -> str:
    try:
        return d.expand(body, tbl).rstrip("　 \t")
    except Exception as e:  # noqa: BLE001
        return f"<expand_err:{e}>"


def strip_prefix(body: bytes) -> bytes:
    if len(body) >= 3 and body[:3] == bytes.fromhex("173418"):
        return body[3:]
    if body and body[0] == 0x18:
        return body[1:]
    if body and body[0] == 0x4A:
        return body[1:]
    return body


def dump_rec(rom: bytes, sb: int, logical: int, od: Dictionary, kd: Dictionary, tbl: Tbl, max_len: int = 96):
    got = read_encoded_z_safe(rom, sb + logical, max_len=max_len)
    if got is None:
        return None
    raw, end = got
    body = bytes(raw)
    body2 = strip_prefix(body)
    return {
        "abs": f"{logical:06X}",
        "raw_hex": body[:32].hex().upper(),
        "raw_full": body.hex().upper(),
        "raw_len": len(body),
        "term": f"{(end - sb):06X}",
        "jp_expand": expand_body(od, body2, tbl),
        "ko_expand": expand_body(kd, body2, tbl),
    }


def find_all(hay: bytes, needle: bytes, sb: int, limit: int = 120) -> list[dict]:
    locs: list[dict] = []
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            break
        logical = i - sb if i >= sb else i
        locs.append(
            {
                "file_off": f"{i:06X}",
                "abs_guess": f"{logical:06X}",
                "ctx32": hay[i : i + 32].hex().upper(),
                "before8": hay[max(0, i - 8) : i].hex().upper(),
            }
        )
        start = i + 1
        if len(locs) >= limit:
            break
    return locs


def walk_range(rom: bytes, sb: int, start: int, end: int, od: Dictionary, kd: Dictionary, tbl: Tbl, interesting_only: bool = False):
    out: list[dict] = []
    pos = start
    while pos < end:
        got = read_encoded_z_safe(rom, sb + pos, max_len=96)
        if got is None:
            pos += 1
            continue
        raw, term = got
        body = bytes(raw)
        if not body:
            pos += 1
            continue
        body2 = strip_prefix(body)
        jp = expand_body(od, body2, tbl)
        ko = expand_body(kd, body2, tbl)
        blob = ko + jp
        interesting = any(
            s in blob
            for s in (
                "멍청",
                "うつけ",
                "たわけ",
                "윽",
                "この",
                "こい",
                "오우",
                "アジア",
                "아시아",
                "東方",
                "도몬",
                "こざか",
            )
        )
        rec = {
            "abs": f"{pos:06X}",
            "raw_hex": body[:32].hex().upper(),
            "raw_len": len(body),
            "term": f"{(term - sb):06X}",
            "jp": jp,
            "ko": ko,
            "interesting": interesting,
        }
        if not interesting_only or interesting:
            out.append(rec)
        pos = (term - sb) + 1
    return out


def main() -> None:
    main_rom = MAIN.read_bytes()
    orig = ORIG.read_bytes()
    a_rom = A.read_bytes() if A.exists() else None
    sb = stock_base(main_rom)
    tbl = Tbl.load(TBL)
    od = Dictionary(orig, stock_count=3831)
    kd = make_dictionary_ext3(main_rom, EXT, EXT3)

    print("stock_base", f"{sb:06X}")
    print("main_sha", hashlib.sha256(main_rom).hexdigest())
    if a_rom:
        print("a_sha", hashlib.sha256(a_rom).hexdigest())

    slot_probe = {}
    for idx in [0x024B, 0x00CF, 0x013E, 0x0143, 0x0146, 0x0053, 0x0044, 0x0191, 0x0EFD, 0x0585, 0x00FD]:
        tok = token_from_dict_index(idx)
        slot_probe[f"{idx:04X}"] = {
            "token": tok.hex().upper(),
            "jp": expand_body(od, tok, tbl),
            "ko": expand_body(kd, tok, tbl),
        }
        print("slot", f"{idx:04X}", slot_probe[f"{idx:04X}"])

    enc_meng = try_encode_ko_text(
        normalize_ko_text("멍청한"), tbl, hangul_marker_code=0xEC8D, hangul_marker_mode="run"
    )
    enc_full = try_encode_ko_text(
        normalize_ko_text("이　멍청한　놈이！！"), tbl, hangul_marker_code=0xEC8D, hangul_marker_mode="run"
    )
    print("enc_meng", enc_meng.hex().upper() if enc_meng else None)
    print("enc_full", enc_full.hex().upper() if enc_full else None)

    needles = {
        "E5183787": bytes.fromhex("E5183787"),
        "E51828D7": bytes.fromhex("E51828D7"),
        "E518382C": bytes.fromhex("E518382C"),
        "4AE5183787": bytes.fromhex("4AE5183787"),
        "4AF36214": bytes.fromhex("4AF36214"),
        "F36214F081": bytes.fromhex("F36214F081"),
        "F362F191": bytes.fromhex("F362F191"),
        "tok_024B": token_from_dict_index(0x024B),
        "tok_F362": bytes.fromhex("F362"),
        "JP_utuke_tail": bytes.fromhex("14F081200517F1FB1009F044"),
        "F38F289F": bytes.fromhex("F38F289F"),
        "07F362": bytes.fromhex("07F362"),
        "18E006F671": bytes.fromhex("18E006F671"),
    }
    if enc_meng:
        needles["hangul_멍청한"] = enc_meng
    if enc_full:
        needles["hangul_full"] = enc_full

    needle_hits = {}
    for name, needle in needles.items():
        locs = find_all(main_rom, needle, sb)
        needle_hits[name] = {"count": len(locs), "locs": locs}
        print(name, len(locs))
        for loc in locs[:12]:
            print(" ", loc["abs_guess"], loc["before8"], loc["ctx32"][:48])

    # Known + needle-derived candidate records
    cand_abs = {
        0x5D956C,
        0x5D9590,
        0x5D95AD,
        0x5D9747,
        0x5D976B,
        0x5D9788,
        0x626509,
        0x62607C,
        0x626102,
        0x62663E,
        0x62605E,
        0x618812,
        0x61882B,
    }
    for pack in needle_hits.values():
        for loc in pack["locs"]:
            a = int(loc["abs_guess"], 16)
            bank = a >> 16
            if bank in range(0x59, 0x64) or 0x5D0000 <= a <= 0x5EFFFF:
                # snap to nearby record starts by trying a-0..a-8
                for off in range(0, 12):
                    cand_abs.add(a - off)

    records = []
    for a in sorted(cand_abs):
        if a < 0:
            continue
        rec = dump_rec(main_rom, sb, a, od, kd, tbl)
        if not rec:
            continue
        blob = rec["ko_expand"] + rec["jp_expand"]
        if any(
            s in blob
            for s in ("멍청", "うつけ", "たわけ", "こざか", "この……", "이……", "약삭", "오우", "윽")
        ) or a in {
            0x5D956C,
            0x5D9747,
            0x626509,
            0x62607C,
            0x626102,
            0x62663E,
        }:
            records.append(rec)
            print("REC", rec["abs"], rec["raw_hex"][:40], "|", rec["ko_expand"][:50], "|", rec["jp_expand"][:50])

    print("=== walk 5D94F0-5D9600 ===")
    walk_a = walk_range(main_rom, sb, 0x5D94F0, 0x5D9600, od, kd, tbl, interesting_only=False)
    for r in walk_a:
        print(r["abs"], r["raw_hex"][:40], "->", r["ko"][:50], "/", r["jp"][:50])

    print("=== walk 5D96AC-5D9800 ===")
    walk_b = walk_range(main_rom, sb, 0x5D96AC, 0x5D9800, od, kd, tbl, interesting_only=False)
    for r in walk_b:
        print(r["abs"], r["raw_hex"][:40], "->", r["ko"][:50], "/", r["jp"][:50])

    print("=== walk scenario Master Asia pockets ===")
    walk_c = []
    for start, end in [(0x626050, 0x626140), (0x6264E0, 0x6266B0), (0x6187F0, 0x618860)]:
        part = walk_range(main_rom, sb, start, end, od, kd, tbl, interesting_only=False)
        walk_c.extend(part)
        for r in part:
            if r["interesting"] or r["abs"] in {"626509", "62607C", "62663E", "626102", "61882B", "618812"}:
                print(r["abs"], r["raw_hex"][:48], "->", r["ko"][:60], "/", r["jp"][:60])

    # Inventory scan for phrase matches
    match_recs = []
    with INV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    abs_col = list(rows[0].keys())[0]
    targets = (
        "이　멍청한　놈이！！",
        "멍청한　놈이",
        "このうつけものがぁぁ－っ！！",
        "たわけ者",
        "うつけもの",
        "こざかしい",
    )
    for row in rows:
        try:
            a = int(row[abs_col], 16)
        except Exception:
            continue
        got = read_encoded_z_safe(main_rom, sb + a, max_len=96)
        if not got:
            continue
        body = bytes(got[0])
        body2 = strip_prefix(body)
        ko = expand_body(kd, body2, tbl)
        jp = expand_body(od, body2, tbl)
        blob = ko + "|" + jp
        if any(t in blob for t in targets):
            match_recs.append(
                {
                    "abs": f"{a:06X}",
                    "raw_hex": body[:32].hex().upper(),
                    "ko": ko,
                    "jp": jp,
                    "inv_row_excerpt": {k: row[k] for k in list(row.keys())[:10]},
                }
            )
    print("inventory phrase matches", len(match_recs))
    for m in match_recs:
        print(m["abs"], m["raw_hex"][:40], m["ko"], "/", m["jp"])

    # A ROM: confirm family restored, then list remaining Korean phrase sources
    a_family = {}
    a_remaining = []
    if a_rom:
        akd = make_dictionary_ext3(a_rom, EXT, EXT3)
        for a in [0x5D956C, 0x5D9590, 0x5D95AD, 0x5D9747, 0x5D976B, 0x5D9788]:
            rec = dump_rec(a_rom, sb, a, od, akd, tbl)
            a_family[f"{a:06X}"] = rec
            print("A", rec)
        # Find remaining portals / hangul full on A
        for name in ("E5183787", "hangul_full", "tok_024B", "F36214F081", "07F362", "F38F289F"):
            needle = needles.get(name)
            if not needle:
                continue
            locs = find_all(a_rom, needle, sb)
            print("A", name, len(locs))
            for loc in locs[:20]:
                print("  A", loc["abs_guess"], loc["ctx32"][:48])
        # Expand scenario candidates on A
        for a in [0x626509, 0x62607C, 0x626102, 0x62663E]:
            rec = dump_rec(a_rom, sb, a, od, akd, tbl)
            a_remaining.append(rec)
            print("A_alt", rec)

    # Search pointers: look for little-endian logical pointers to 5D9747 / 5D956C in nearby banks
    ptr_hits = {}
    for target in (0x5D9747, 0x5D956C, 0x626509, 0x62607C, 0x5D71ED):
        le = target.to_bytes(3, "little")  # 24-bit?
        le2 = (target & 0xFFFF).to_bytes(2, "little")
        # bank-local offset
        off16 = (target & 0xFFFF).to_bytes(2, "little")
        for label, needle in (("le24", le), ("off16", off16)):
            locs = find_all(main_rom, needle, sb, limit=80)
            # filter to code-ish regions / pointer tables: banks 00-20 and near 5D/5E headers
            filtered = []
            for loc in locs:
                fo = int(loc["file_off"], 16)
                # keep if in low ROM or battle banks meta areas
                if fo < 0x200000 or (0x5D0000 <= (fo - sb) <= 0x5EFFFF) or (0x590000 <= (fo - sb) <= 0x5AFFFF):
                    filtered.append(loc)
            ptr_hits[f"{target:06X}_{label}"] = {"count_all": len(locs), "filtered": filtered[:40]}
            print("PTR", f"{target:06X}", label, "all", len(locs), "filtered", len(filtered))
            for loc in filtered[:15]:
                print(" ", loc["file_off"], loc["abs_guess"], loc["before8"], loc["ctx32"][:32])

    # Look for ……윽！ immediately before phrase candidates in same page walks
    euk_near = []
    for r in walk_b + walk_a + walk_c:
        if "윽" in r["ko"] or "うっ" in r["jp"] or "くっ" in r["jp"]:
            euk_near.append(r)

    # Uncovered sheet
    unc_hits = []
    for p in [
        ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv",
        ROOT / "out/script/uncovered_translation_sheet.csv",
    ]:
        if not p.exists():
            continue
        with p.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                blob = " ".join((v or "") for v in row.values())
                if any(s in blob for s in ("うつけ", "이　멍청한", "멍청한　놈이", "たわけ者め", "こざかしいわ")):
                    unc_hits.append({"file": p.name, **row})

    print("uncovered", len(unc_hits))
    for h in unc_hits[:20]:
        # print compact
        keys = [k for k in h if k in ("file", "abs", "jp", "ko") or "abs" in k.lower()]
        print({k: h.get(k) for k in list(h.keys())[:15]})

    # Ranked suspects for report
    ranked = []
    # 1) scenario 626509
    for a, why, rank in [
        (0x626509, "scenario JP ',この……うつけものがぁっ！！' — contains literal この/こ path; A did not touch bank62", 1),
        (0x62607C, "scenario '……たわけ者めがぁっ！！' -> KO '……멍청한　놈이　감히！！' similar insult line in Master Asia event", 2),
        (0x5D71ED, "battle voice '……윽！' metadata 94 — possible first line of two-line box", 3),
        (0x5D4675, "battle voice '……윽！！'", 4),
        (0x61882B, "scenario '……윽！！' near Domon lines per PATCH_PROGRESS", 5),
    ]:
        rec = dump_rec(main_rom, sb, a, od, kd, tbl)
        if rec:
            ranked.append({"rank": rank, "why": why, **rec})

    # Add any inventory match not in disproven family as suspects
    disproven = {"5D956C", "5D9590", "5D95AD", "5D9747", "5D976B", "5D9788"}
    for m in match_recs:
        if m["abs"] in disproven:
            continue
        ranked.append(
            {
                "rank": 10,
                "why": "inventory expand still matches phrase outside A-restored family",
                **m,
            }
        )

    # Also check if 024B on MAIN still holds full phrase (v2) vs fragment (v3/B)
    # Find all consumers of tok_024B and expand parent records heuristically
    tok24 = token_from_dict_index(0x024B)
    consumers_024B = []
    for loc in find_all(main_rom, tok24, sb, limit=200):
        a = int(loc["abs_guess"], 16)
        for off in range(0, 16):
            rec = dump_rec(main_rom, sb, a - off, od, kd, tbl)
            if not rec:
                continue
            if tok24.hex().upper() in rec["raw_full"]:
                consumers_024B.append(rec)
                break
    # dedupe
    seen = set()
    uniq_cons = []
    for c in consumers_024B:
        if c["abs"] in seen:
            continue
        seen.add(c["abs"])
        uniq_cons.append(c)
    print("024B consumers", len(uniq_cons))
    for c in uniq_cons[:40]:
        print(" ", c["abs"], c["raw_hex"][:40], c["ko_expand"][:60])

    # E5 18 37 87 consumers (old full phrase portal) — should be the 4A family on main
    portal_cons = []
    for loc in needle_hits["E5183787"]["locs"]:
        a = int(loc["abs_guess"], 16)
        for off in range(0, 8):
            rec = dump_rec(main_rom, sb, a - off, od, kd, tbl)
            if rec and "E5183787" in rec["raw_full"]:
                portal_cons.append(rec)
                break

    report = {
        "schema_version": 1,
        "generated_by": "tools/_scan_domon_ko_leak_live_source.py",
        "main_sha256": hashlib.sha256(main_rom).hexdigest(),
        "a_sha256": hashlib.sha256(a_rom).hexdigest() if a_rom else None,
        "stock_base": f"{sb:06X}",
        "runtime_fact": "A/B both still showed こい　멍청한　놈이！！; 5D956C/5D9747 family is NOT live",
        "slot_probe": slot_probe,
        "needle_hits": {k: {"count": v["count"], "locs": v["locs"][:50]} for k, v in needle_hits.items()},
        "inventory_phrase_matches": match_recs,
        "walk_5D94F0": walk_a,
        "walk_5D96AC": walk_b,
        "walk_scenario_pockets": [r for r in walk_c if r["interesting"]],
        "a_family": a_family,
        "a_alt_candidates": a_remaining,
        "ptr_hits": ptr_hits,
        "euk_near": euk_near,
        "consumers_024B": uniq_cons,
        "portal_E5183787_records": portal_cons,
        "ranked_suspects": ranked,
        "uncovered_hits_count": len(unc_hits),
        "uncovered_sample": [
            {k: h.get(k) for k in list(h.keys())[:18]} for h in unc_hits[:30]
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
