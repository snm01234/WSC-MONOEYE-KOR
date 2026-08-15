#!/usr/bin/env python3
"""Combine validated Domon scenario repairs: 626509 こ-shift + 62663E がけはう.

Runtime-proven pieces merged into one candidate from the current main TIP:

1. ``626509`` — drop leading raw ``18``(=``こ``) and slide exclusive Korean
   portal ``E5 18 3C 20`` forward (``614F0A`` pattern). User confirmed
   ``이……　멍청한　놈이！！`` only.
2. ``62663E`` — restore pristine native two-token grammar for ``오우！！``
   (``17 34 18 | F0 FD F0 44``) so the bogus follow line ``がけはう`` stays gone.
   Already validated on ``domon_runtime_structure_followup_candidate.wsc``.

``626102`` from the older followup is intentionally NOT included (wrong bind for
the こ scene). Main TIP and live SaveRAM are never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_scenario_page_boundary_guard_candidate import original_unit_kinds, safe_unreachable_slots  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base, token_from_dict_index, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/domon_scenario_626509_62663E_combined_candidate.wsc"
OUT_SAVE = ROOT / "sram/domon_scenario_626509_62663E_combined_candidate.sav"
REPORT = ROOT / "out/patch/domon_scenario_626509_62663E_combined_report.json"

MAIN_SHA = "2db6e70feb8925980551e962965fe268c3dbddbc7014e8a0e3fece72a7c3b483"
EXT = {"stock_count": 3831, "slot_count": 265, "ext_ptr_off": "0000", "ext_seg": "10", "ext_in_expansion": True}
EXT3 = {"num_banks": 16, "exp_seg0": "11"}

SLOT_OU = 0x00FD
ABS_FOOL = 0x626509
TERM_FOOL = 0x626518
CUR_FOOL = bytes.fromhex("18E5183C2001010101010101010101")
NEW_FOOL = bytes.fromhex("E5183C200101010101010101010101")
PAIR_ABS = 0x626501
PAIR_CUR = bytes.fromhex("173418E518F299")
FOLLOW_FOOL = 0x62651A
FOLLOW_FOOL_CUR = bytes.fromhex("17280106")

ABS_OU = 0x62663E
TERM_OU = 0x626645
CUR_OU = bytes.fromhex("173418E5181CF8")
# filled after encoding 오우 token
EXPECTED_FOOL = "이……　멍청한　놈이！！"
EXPECTED_OU = "오우！！"


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rr(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def enc(tbl: Tbl, text: str) -> bytes:
    raw = try_encode_ko_text(normalize_ko_text(text), tbl, hangul_marker_code=0xEC8D, hangul_marker_mode="run")
    if not raw or b"\x00" in raw:
        raise RuntimeError(f"encode failed {text!r}")
    return bytes(raw)


def trim(text: str) -> str:
    return text.rstrip("　 \t")


def tok(index: int) -> bytes:
    value = token_from_dict_index(index)
    if len(value) != 2 or 0 in value:
        raise RuntimeError(f"bad token {index:04X}")
    return value


def main() -> int:
    parent = MAIN.read_bytes()
    save = SAVE.read_bytes()
    if sha(parent) != MAIN_SHA:
        raise RuntimeError("main identity drift")
    if len(save) != 32768:
        raise RuntimeError("SaveRAM size drift")

    tbl = Tbl.load(TBL_PATH)
    d = make_dictionary_ext3(parent, EXT, EXT3)
    sb = stock_base(parent)

    if rr(parent, ABS_FOOL) != (CUR_FOOL, TERM_FOOL):
        raise RuntimeError("626509 drift")
    if rr(parent, PAIR_ABS)[0] != PAIR_CUR:
        raise RuntimeError("626501 drift")
    if rr(parent, FOLLOW_FOOL)[0] != FOLLOW_FOOL_CUR:
        raise RuntimeError("62651A drift")
    if rr(parent, ABS_OU) != (CUR_OU, TERM_OU):
        raise RuntimeError("62663E drift")
    if trim(d.expand(CUR_FOOL[1:], tbl)) != EXPECTED_FOOL:
        raise RuntimeError("626509 Korean portal drift")
    if trim(d.expand(CUR_OU[3:], tbl)) != EXPECTED_OU:
        raise RuntimeError("62663E Korean portal drift")

    safe = {int(r["index"]): r for r in safe_unreachable_slots(parent, d)}
    if SLOT_OU not in safe:
        raise RuntimeError("slot 00FD no longer safe")
    ou_raw = enc(tbl, "오우")
    row = safe[SLOT_OU]
    if len(ou_raw) > int(row["old_len"]):
        raise RuntimeError("slot 00FD too small for 오우")

    new_ou = bytes.fromhex("173418") + tok(SLOT_OU) + tok(0x0044)
    if len(new_ou) != len(CUR_OU):
        raise RuntimeError("62663E extent mismatch")
    if original_unit_kinds(new_ou[3:]) != ["dict", "dict"]:
        raise RuntimeError("62663E must stay native two-token like pristine")

    cand = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # stock fragment 오우
    start = int(row["entry_abs"])
    old_len = int(row["old_len"])
    before_slot = d.expand_index(SLOT_OU, tbl)
    cand[start : start + len(ou_raw)] = ou_raw
    cand[start + len(ou_raw)] = 0
    allowed.append((start, start + old_len + 1))

    # 626509 lead-18 shift
    cand[sb + ABS_FOOL : sb + ABS_FOOL + len(NEW_FOOL)] = NEW_FOOL
    allowed.append((sb + ABS_FOOL, sb + ABS_FOOL + len(NEW_FOOL)))

    # 62663E native 오우！！
    cand[sb + ABS_OU : sb + ABS_OU + len(new_ou)] = new_ou
    allowed.append((sb + ABS_OU, sb + ABS_OU + len(new_ou)))

    checksum = update_ws_checksum(cand)
    allowed.append((len(cand) - 2, len(cand)))
    cb = bytes(cand)
    fd = make_dictionary_ext3(cb, EXT, EXT3)

    # verifies
    g509, t509 = rr(cb, ABS_FOOL)
    if g509 != NEW_FOOL or t509 != TERM_FOOL or g509[:1] == b"\x18":
        raise RuntimeError("626509 verify failed")
    if trim(fd.expand(g509, tbl)) != EXPECTED_FOOL:
        raise RuntimeError("626509 render failed")
    if rr(cb, PAIR_ABS)[0] != PAIR_CUR or rr(cb, FOLLOW_FOOL)[0] != FOLLOW_FOOL_CUR:
        raise RuntimeError("626509 neighbors changed")
    if cb[sb + TERM_FOOL : sb + FOLLOW_FOOL + len(FOLLOW_FOOL_CUR)] != parent[sb + TERM_FOOL : sb + FOLLOW_FOOL + len(FOLLOW_FOOL_CUR)]:
        raise RuntimeError("626509 post boundary drift")

    g63, t63 = rr(cb, ABS_OU)
    if g63 != new_ou or t63 != TERM_OU or b"\xE5\x18" in g63[3:]:
        raise RuntimeError("62663E verify failed")
    if trim(fd.expand(g63[3:], tbl)) != EXPECTED_OU:
        raise RuntimeError("62663E render failed")
    if cb[sb + TERM_OU] != 0:
        raise RuntimeError("62663E terminator lost")

    # slot refs only at 626641
    selected = {SLOT_OU}
    ext = external_occurrence_map(cb, ext3_aware=True, wanted=selected)
    nested = nested_occurrence_map(fd, wanted=selected, ext3_aware=True)
    raw = _raw_pair_hits(cb, sorted(selected))
    expected = [ABS_OU + 3]
    ea = sorted(int(str(x["token_abs"]), 16) for x in ext.get(SLOT_OU, []))
    ra = sorted(int(str(x["token_abs"]), 16) for x in raw.get(SLOT_OU, []))
    if ea != expected or ra != expected or nested.get(SLOT_OU):
        raise RuntimeError(f"00FD reference proof failed: {ea} {ra} {nested.get(SLOT_OU)}")

    outside = [run for run in diff_runs(parent, cb) if not covered(run, allowed)]
    if outside:
        raise RuntimeError(f"diff escape {outside[:4]}")

    OUT.write_bytes(cb)
    shutil.copy2(SAVE, OUT_SAVE)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "tools/build_domon_scenario_626509_62663E_combined_candidate.py",
        "status": "pending_user_runtime_validation",
        "parent_sha256": sha(parent),
        "candidate_sha256": sha(cb),
        "checksum": f"{checksum:04X}",
        "saveram_sha256": sha(OUT_SAVE.read_bytes()),
        "main_unchanged": True,
        "live_saveram_unchanged": SAVE.read_bytes() == save,
        "merged_from": {
            "626509": "domon_scenario_626509_ko_lead18_shift_candidate (user-confirmed)",
            "62663E": "domon_runtime_structure_followup_candidate native two-token 오우！！ (user-confirmed がけはう gone)",
            "excluded": "626102 from followup (wrong こ bind)",
        },
        "slots": [
            {
                "index": "00FD",
                "before": before_slot,
                "after": "오우",
                "raw": ou_raw.hex().upper(),
                "entry_abs": start,
                "old_len": old_len,
            }
        ],
        "patches": [
            {
                "abs": "626509",
                "before": CUR_FOOL.hex().upper(),
                "after": NEW_FOOL.hex().upper(),
                "terminator": f"{TERM_FOOL:06X}",
                "render": EXPECTED_FOOL,
            },
            {
                "abs": "62663E",
                "before": CUR_OU.hex().upper(),
                "after": new_ou.hex().upper(),
                "terminator": f"{TERM_OU:06X}",
                "render": EXPECTED_OU,
            },
        ],
        "runtime_gate": [
            "cold boot with paired SaveRAM",
            "……윽！ then 이……　멍청한　놈이！！ without leading こ",
            "오우！！ without follow-up がけはう",
            "both scenes continue past their follow controls",
        ],
        "unexpected_diff_runs": 0,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rom": str(OUT.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(cb), "checksum": f"{checksum:04X}", "save": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/")}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
