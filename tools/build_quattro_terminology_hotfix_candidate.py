#!/usr/bin/env python3
"""Rewrite residual forbidden 콰트로 spellings to canonical 크와트로.

Live tip already stores stock index 0x0B96 as 크와트로. Two unused-looking ext3
phrases still spell 콰트로 with direct Hangul glyphs, and one orphaned stock
residue still contains the same glyph run. Replace those glyph runs with the
stock token FB96 (index 0x0B96) so dictionary/audit surfaces stay canonical
without length growth.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_gundam_terminology_standard import (  # noqa: E402
    dictionary_hits,
    entries as standard_entries,
    forbidden_index,
    rendered_record_hits,
)
from expand_dictionary import DEFAULT_REF_REGIONS, build_dict_token_locs  # noqa: E402
from monoeye_rom import Tbl, load_rom, token_from_dict_index, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/quattro_terminology_hotfix_candidate.wsc"
OUT_SAVE = ROOT / "sram/quattro_terminology_hotfix_candidate.sav"
OUT_REPORT = ROOT / "out/patch/quattro_terminology_hotfix_candidate_report.json"

EXPECTED_MAIN = "edb0b2502753a6682b63ea535f65fd3fa017923b21cdb8ed06d8a30f32edf248"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STOCK_QUATTRO = 0x0B96
BAD_GLYPH_RUN = bytes.fromhex("EC8DE8E6E7E1E748")  # 콰트로
ORPHAN_ABS = 0x0DFD313

TARGETS = {
    0x1594: "선행한　크와트로　대위는　현재、",
    0x15C2: "크와트로　대위！？",
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def replace_bad_run(raw: bytes) -> bytes:
    token = token_from_dict_index(STOCK_QUATTRO)
    if BAD_GLYPH_RUN not in raw:
        raise BuildError(f"missing 콰트로 glyph run in {raw.hex().upper()}")
    if raw.count(BAD_GLYPH_RUN) != 1:
        raise BuildError(f"unexpected 콰트로 glyph run count in {raw.hex().upper()}")
    return raw.replace(BAD_GLYPH_RUN, token, 1)


def patch_index(rom: bytearray, dictionary, index: int, expected: str, tbl: Tbl) -> dict[str, Any]:
    before = dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
    raw = bytes(dictionary.raw_entry(index))
    entry_abs = int(dictionary.entry_abs(index))
    refs = build_dict_token_locs(bytes(rom), regions=DEFAULT_REF_REGIONS).get(index, [])
    if refs:
        raise BuildError(f"refusing live rewrite for {index:05X}; refs={refs}")
    encoded = replace_bad_run(raw)
    if len(encoded) > len(raw):
        raise BuildError(f"encoded grew for {index:05X}")
    rom[entry_abs : entry_abs + len(encoded)] = encoded
    rom[entry_abs + len(encoded)] = 0
    # Keep old tail inert if the payload shrank.
    if len(encoded) < len(raw):
        tail = entry_abs + len(encoded) + 1
        end = entry_abs + len(raw) + 1
        rom[tail:end] = b"\xFF" * (end - tail)
    after_dict = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    after = after_dict.expand_index(index, tbl).rstrip("\u3000 \t")
    if after != expected:
        raise BuildError(f"verify failed {index:05X}: {after!r} != {expected!r}")
    return {
        "index": f"{index:05X}",
        "entry_abs": f"{entry_abs:07X}",
        "before": before,
        "after": after,
        "old_raw_len": len(raw),
        "new_raw_len": len(encoded),
        "refs": 0,
        "mode": "inplace_stock_token",
    }


def neutralize_orphan(rom: bytearray, tbl: Tbl, dictionary) -> dict[str, Any]:
    if rom[ORPHAN_ABS : ORPHAN_ABS + len(BAD_GLYPH_RUN)] != BAD_GLYPH_RUN:
        raise BuildError(f"orphan glyph run missing at {ORPHAN_ABS:07X}")
    # Confirm no live dictionary pointer lands on this residue.
    for index in range(dictionary.count):
        try:
            if int(dictionary.entry_abs(index)) == ORPHAN_ABS:
                raise BuildError(f"orphan abs is live stock index {index:04X}")
        except BuildError:
            raise
        except Exception:
            continue
    token = token_from_dict_index(STOCK_QUATTRO)
    old = bytes(rom[ORPHAN_ABS : ORPHAN_ABS + len(BAD_GLYPH_RUN) + 1])
    rom[ORPHAN_ABS : ORPHAN_ABS + len(token)] = token
    rom[ORPHAN_ABS + len(token)] = 0
    # Preserve following entry start; fill only the shrunk glyph bytes.
    fill_end = ORPHAN_ABS + len(BAD_GLYPH_RUN)
    rom[ORPHAN_ABS + len(token) + 1 : fill_end + 1] = b"\xFF" * (fill_end - (ORPHAN_ABS + len(token)))
    # Decode via temporary expand of the token alone.
    after = dictionary.expand(token, tbl)
    if after != "크와트로":
        raise BuildError(f"orphan verify failed: {after!r}")
    return {
        "entry_abs": f"{ORPHAN_ABS:07X}",
        "before_raw": old.hex().upper(),
        "after_raw": bytes(rom[ORPHAN_ABS : ORPHAN_ABS + len(token) + 1]).hex().upper(),
        "after": after,
        "mode": "orphan_stock_token",
    }


def main() -> int:
    parent = bytearray(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN:
        raise BuildError("main TIP identity drifted")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("main SaveRAM missing/size drift")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    stock_text = dictionary.expand_index(STOCK_QUATTRO, tbl).rstrip("\u3000 \t")
    if stock_text != "크와트로":
        raise BuildError(f"stock 0x0B96 drifted: {stock_text!r}")

    rows = [patch_index(parent, dictionary, index, expected, tbl) for index, expected in TARGETS.items()]
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    orphan = neutralize_orphan(parent, tbl, dictionary)

    # No raw 콰트로 glyph run may remain.
    if BAD_GLYPH_RUN in parent:
        raise BuildError("residual 콰트로 glyph run still present")

    update_ws_checksum(parent)
    bad = [row for row in forbidden_index(standard_entries()) if row[0] == "quattro"]
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    dict_hits = dictionary_hits(bytes(parent), tbl, dictionary, bad)
    rendered_hits = rendered_record_hits(bytes(parent), tbl, dictionary, bad)
    if dict_hits or rendered_hits:
        raise BuildError(f"quattro residual remains: dict={dict_hits} rendered={rendered_hits}")

    atomic_bytes(OUT_ROM, bytes(parent))
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_quattro_terminology_hotfix_candidate.py",
        "ok": True,
        "status": "built_quattro_terminology_hotfix",
        "parent": {"path": str(MAIN.relative_to(ROOT)).replace("\\", "/"), "sha256": EXPECTED_MAIN, "size": ROM_SIZE},
        "candidate": {"path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(parent), "size": ROM_SIZE},
        "save_pair": {"path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(MAIN_SAVE.read_bytes()), "size": SAVE_SIZE},
        "stock_index": f"{STOCK_QUATTRO:04X}",
        "stock_token": token_from_dict_index(STOCK_QUATTRO).hex().upper(),
        "ext3_rewrites": rows,
        "orphan_neutralize": orphan,
        "audit": {
            "dictionary_hits": dict_hits,
            "rendered_record_hits": rendered_hits,
        },
        "checksum": f"{int.from_bytes(bytes(parent)[-2:], 'little'):04X}",
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({k: report[k] for k in ("status", "candidate", "checksum", "ext3_rewrites", "orphan_neutralize", "audit")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
