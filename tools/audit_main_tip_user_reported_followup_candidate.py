#!/usr/bin/env python3
"""Independent read-only audit for the user-reported follow-up candidate."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import COMPACT_FONT_RECORD_SIZE, Tbl, read_encoded_z_safe, stock_base  # noqa: E402
from patch_font_hangul_hook import STORE_SITE, build_store_cave  # noqa: E402
from patch_pad3_expansion import PAD12_SLOTS  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "main_tip_user_reported_followup_candidate.wsc"
CANDIDATE_TBL = PATCH / "main_tip_user_reported_followup_candidate.tbl"
CANDIDATE_SAVE = ROOT / "sram/main_tip_user_reported_followup_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BUILD_REPORT = PATCH / "main_tip_user_reported_followup_candidate_report.json"
OUT = PATCH / "main_tip_user_reported_followup_candidate_audit.json"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_MAIN = "e22ccc450c64f7751d61a80d6cd52f94363d981e5d5a7e1802afa57dfd224862"
EXPECTED_DICTIONARY = {
    0x0FF20: "스크류　윕",
    0x0FEDC: "히트　팬",
    0x0FF0A: "빔　팬",
    0x0C70C: "병기　스크류　윕을",
    0x017A6: "면、면、며언！！",
    0x0F4A0: "네놈이이！！",
    0x01181: "네놈이이！！",
    0x0513F: "네놈이이！",
    0x061A2: "네놈이이！！",
    0x040E7: "이、　네놈이이！！",
}
LORAN = (0x5E4477, 0x5E4620)
LORAN_BYTES = bytes.fromhex("E518380F01010101010101010101")
LORAN_TEXT = "…모두！<E62F>대피하세요！"
DIANA = (0x5D8DFB, 0x5D8F0A)
DIANA_BYTES = bytes.fromhex("45F55A")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locate_store(rom: bytes) -> int:
    sb = stock_base(rom)
    site = sb + STORE_SITE
    rel = struct.unpack_from("<H", rom, site + 1)[0]
    ip = (STORE_SITE + 3 + rel) & 0xFFFF
    return sb + ((STORE_SITE & 0xFF0000) | ip)


def main() -> int:
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    tbl = Tbl.load(CANDIDATE_TBL)
    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    sb = stock_base(candidate)
    dictionary = make_dictionary_ext3(
        candidate, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )

    dictionary_checks = {
        f"{index:05X}": dictionary.expand_index(index, tbl).rstrip("　 \t") == expected
        for index, expected in EXPECTED_DICTIONARY.items()
    }
    loran_checks = {}
    for logical in LORAN:
        got = read_encoded_z_safe(candidate, sb + logical, max_len=64)
        rendered = dictionary.expand(got[0], tbl).rstrip("　 \t") if got else "<unreadable>"
        loran_checks[f"{logical:06X}"] = (
            got is not None and got[0] == LORAN_BYTES and rendered == LORAN_TEXT
        )
    diana_checks = {}
    for logical in DIANA:
        got = read_encoded_z_safe(candidate, sb + logical, max_len=16)
        body = dictionary.expand(got[0][1:], tbl).rstrip("　 \t") if got else "<unreadable>"
        diana_checks[f"{logical:06X}"] = (
            got is not None and got[0] == DIANA_BYTES and body == "저는"
        )

    glyph_checks = {}
    for ch, code in {"윕": 0xEC82, "팬": 0xEC83}.items():
        slot = code - 0xE740
        offset = (slot - PAD12_SLOTS) * COMPACT_FONT_RECORD_SIZE
        blob = candidate[offset : offset + COMPACT_FONT_RECORD_SIZE]
        glyph_checks[ch] = (
            tbl.code_to_char.get(code) == ch
            and tbl.char_to_code.get(ch) == code
            and blob != b"\xFF" * COMPACT_FONT_RECORD_SIZE
            and blob != b"\x00" * COMPACT_FONT_RECORD_SIZE
        )

    store = locate_store(candidate)
    sticky = build_store_cave(0xE740 - 0xDF20, 1348)
    checks = {
        "parent_identity": sha(parent) == EXPECTED_MAIN,
        "candidate_identity_matches_report": sha(candidate)
        == report["outputs"]["candidate_rom"]["sha256"],
        "candidate_tbl_identity_matches_report": sha(CANDIDATE_TBL.read_bytes())
        == report["outputs"]["candidate_tbl"]["sha256"],
        "build_report_ok": report.get("ok") is True
        and report.get("promotion_allowed") is True,
        "checksum_valid": (sum(candidate[:-2]) & 0xFFFF)
        == int.from_bytes(candidate[-2:], "little"),
        "dictionary_exact_all": all(dictionary_checks.values()),
        "loran_duplicate_exact_both": all(loran_checks.values()),
        "diana_duplicate_exact_both": all(diana_checks.values()),
        "new_glyphs_exact_both": all(glyph_checks.values()),
        "marker_still_ec8d": tbl.code_to_char.get(0xEC8D) == "",
        "sticky_window_exact_1348": candidate[store : store + len(sticky)] == sticky,
        "build_diff_allowlist_clean": report["checks"]["diff_allowlist_clean"] is True
        and int(report["diff"]["unexpected_runs"]) == 0,
        "candidate_saveram_exact_live": CANDIDATE_SAVE.read_bytes()
        == MAIN_SAVE.read_bytes(),
    }
    result = {
        "schema_version": 1,
        "generated_by": "tools/audit_main_tip_user_reported_followup_candidate.py",
        "ok": all(checks.values()),
        "candidate_sha256": sha(candidate),
        "candidate_tbl_sha256": sha(CANDIDATE_TBL.read_bytes()),
        "checks": checks,
        "dictionary_checks": dictionary_checks,
        "loran_checks": loran_checks,
        "diana_checks": diana_checks,
        "glyph_checks": glyph_checks,
    }
    OUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
