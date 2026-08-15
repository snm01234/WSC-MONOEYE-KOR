#!/usr/bin/env python3
"""Independent static audit for near_all_geunjeon_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_terrain_space_abaoaqu_compact_glyph_candidate import (  # noqa: E402
    compact_glyph_offset,
    hangul_glyph_offset,
    read_glyph,
    select_steal_codes,
)
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base, token_from_dict_index  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CANDIDATE = PATCH / "near_all_geunjeon_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/near_all_geunjeon_candidate.sav"
BUILD_REPORT = PATCH / "near_all_geunjeon_candidate_report.json"
OUT = PATCH / "near_all_geunjeon_candidate_audit.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
EXPECTED_MAIN = "92fea67dc128d28a6c95e91faaeb21c8632547d23b8baace57cf904f3df3a40c"
EXPECTED_CANDIDATE = "b490dcbd87afa816475f3024d2d55d96fe77897afb82601b8939dce3e7321ed0"
TARGET = 0x75B3FD
NEIGHBOR = 0x75B401
SLOT = 0x0B68
STOLEN = (0xE511, 0xE51B)
HANGUL = {"근": 0xE8B0, "전": 0xE745}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=32)
    if got is None:
        return b"", -1
    return bytes(got[0]), int(got[1]) - sb


def main() -> int:
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    original = ORIGINAL.read_bytes()
    save = SAVE.read_bytes()
    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    em = load_ext_meta(EXT_META)
    e3 = load_ext_meta(EXT3_META)
    pd = make_dictionary_ext3(parent, em, e3)
    cd = make_dictionary_ext3(candidate, em, e3)
    od = Dictionary(original)

    checks: dict[str, bool] = {}
    checks["main_identity_exact"] = sha(parent) == EXPECTED_MAIN
    checks["candidate_identity_exact"] = sha(candidate) == EXPECTED_CANDIDATE
    checks["build_report_ok"] = report.get("ok") is True
    checks["candidate_save_exact_live"] = CANDIDATE_SAVE.read_bytes() == save

    target, target_term = payload_at(candidate, TARGET)
    neighbor, neighbor_term = payload_at(candidate, NEIGHBOR)
    checks["target_body_exact"] = target == token_from_dict_index(SLOT) + b"\x01"
    checks["target_terminator_exact"] = target_term == 0x75B400
    checks["neighbor_75B401_exact"] = neighbor == bytes.fromhex("E08F86") and neighbor_term == 0x75B404
    checks["slot_payload_exact"] = bytes(cd.raw_entry(SLOT)) == bytes.fromhex("E511E51B")
    checks["slot_pointer_unchanged"] = cd.ptrs[SLOT] == pd.ptrs[SLOT]

    external = external_occurrence_map(parent, ext3_aware=True, wanted={SLOT})
    nested = nested_occurrence_map(pd, wanted={SLOT}, ext3_aware=True)
    raw = _raw_pair_hits(parent, [SLOT])
    checks["slot_unreachable_in_parent"] = not external.get(SLOT) and not nested.get(SLOT) and not raw.get(SLOT)

    selected = select_steal_codes(parent, original, pd, od, tbl)
    checks["stolen_codes_still_proven_unused"] = tuple(int(r["code"]) for r in selected[:2]) == STOLEN
    glyph_rows = []
    for ch, code in zip(("근", "전"), STOLEN):
        source = read_glyph(candidate, hangul_glyph_offset(candidate, HANGUL[ch]))
        dst_off = compact_glyph_offset(candidate, code)
        actual = bytes(candidate[dst_off:dst_off+16])
        ok = actual == source
        checks[f"glyph_{ch}_exact"] = ok
        glyph_rows.append({"hangul": ch, "code": f"{code:04X}", "offset": f"{dst_off:07X}", "exact": ok})

    stored = int.from_bytes(candidate[-2:], "little")
    checks["checksum_valid"] = stored == (sum(candidate[:-2]) & 0xFFFF) == 0x1DCE
    checks["main_unchanged"] = sha(MAIN.read_bytes()) == EXPECTED_MAIN
    checks["live_save_unchanged"] = SAVE.read_bytes() == save
    ok = all(checks.values())
    out = {
        "schema_version": 1,
        "generated_by": "tools/audit_near_all_geunjeon_candidate.py",
        "ok": ok,
        "checks": checks,
        "target": {"abs": "75B3FD", "runtime_text": "근전", "body_hex": target.hex().upper(), "slot": "0B68"},
        "neighbor": {"abs": "75B401", "text": "射全", "body_hex": neighbor.hex().upper(), "unchanged": checks["neighbor_75B401_exact"]},
        "glyphs": glyph_rows,
        "checksum": f"{stored:04X}",
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
