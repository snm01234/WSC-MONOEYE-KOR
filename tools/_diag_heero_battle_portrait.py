#!/usr/bin/env python3
"""Read-only: Heero battle-voice metadata 5D vs live E5 18 body-only records."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
INV = ROOT / "out/script/battle_dialogue_speaker_portrait_metadata_inventory.csv"
VOICE = ROOT / "out/script/runtime_text_residual_voice_sheet.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/_heero_battle_portrait_diag.json"

HEERO_ANCHORS = (0x5E00C8, 0x5E0109, 0x5E0143, 0x5E016F, 0x5E0274)
SIG_ANCHORS = (0x5D0018,)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rec(rom: bytes, logical: int) -> tuple[str, int] | None:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=128)
    if not got:
        return None
    live, term = got
    return bytes(live).hex().upper(), term


def main() -> int:
    main = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    ext3 = make_dictionary_ext3(main, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    orig_d = Dictionary(original)

    rows = []
    with INV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("metadata_hex") or "").upper() != "5D":
                continue
            logical = int(row["record_start"], 16)
            cur = rec(main, logical)
            orig = rec(original, logical)
            cur_hex = cur[0] if cur else ""
            orig_hex = orig[0] if orig else ""
            cur_b = bytes.fromhex(cur_hex) if cur_hex else b""
            orig_b = bytes.fromhex(orig_hex) if orig_hex else b""
            starts_e518 = cur_b.startswith(b"\xE5\x18")
            has_meta = cur_b.startswith(b"\x5D")
            meta_then_e518 = cur_b.startswith(b"\x5D\xE5\x18")
            jp = orig_d.expand(orig_b[1:] if orig_b.startswith(b"\x5D") else orig_b, tbl)
            ko_src = cur_b[1:] if has_meta else cur_b
            ko = ext3.expand(ko_src, tbl).rstrip("\u3000 ")
            rows.append({
                "abs": f"{logical:06X}",
                "bank": row.get("bank"),
                "safe_structure_exact": row.get("safe_structure_exact"),
                "current_structure_exact": row.get("current_structure_exact"),
                "action": row.get("action"),
                "orig_hex": orig_hex,
                "cur_hex": cur_hex,
                "orig_len": len(orig_b),
                "cur_len": len(cur_b),
                "len_same": len(orig_b) == len(cur_b),
                "starts_e518_no_meta": starts_e518 and not has_meta,
                "has_5d_meta": has_meta,
                "meta_then_e518": meta_then_e518,
                "jp": jp,
                "ko": ko,
            })

    kinds = Counter()
    for row in rows:
        if row["starts_e518_no_meta"]:
            kinds["e518_body_only"] += 1
        elif row["meta_then_e518"]:
            kinds["5d_then_e518"] += 1
        elif row["has_5d_meta"]:
            kinds["5d_other"] += 1
        else:
            kinds["other"] += 1

    anchors = {}
    for addr in HEERO_ANCHORS + SIG_ANCHORS:
        cur = rec(main, addr)
        orig = rec(original, addr)
        anchors[f"{addr:06X}"] = {
            "orig": orig[0] if orig else None,
            "cur": cur[0] if cur else None,
        }

    voice_hits = []
    with VOICE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            start = int(row["record_start"], 16)
            if start in HEERO_ANCHORS or (row.get("original_body") or "").startswith("モ"):
                if start >= 0x5E00C8 and start <= 0x5E0400:
                    voice_hits.append({
                        "abs": f"{start:06X}",
                        "prefix": row.get("prefix_hex"),
                        "orig_body": row.get("original_body"),
                        "cur_body": row.get("current_body"),
                        "orig_hex": row.get("original_payload_hex"),
                        "cur_hex": row.get("current_payload_hex"),
                    })

    report = {
        "main_sha256": sha(MAIN.read_bytes()),
        "main_size": len(main),
        "metadata_5d_count": len(rows),
        "kinds": dict(kinds),
        "anchors": anchors,
        "e518_body_only_sample": [r for r in rows if r["starts_e518_no_meta"]][:20],
        "e518_body_only_addrs": [r["abs"] for r in rows if r["starts_e518_no_meta"]],
        "5d_then_e518_sample": [r for r in rows if r["meta_then_e518"]][:10],
        "voice_heero_block": voice_hits[:40],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "main_sha256": report["main_sha256"],
        "metadata_5d_count": report["metadata_5d_count"],
        "kinds": report["kinds"],
        "anchors": report["anchors"],
        "e518_body_only": kinds["e518_body_only"],
        "out": str(OUT),
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
