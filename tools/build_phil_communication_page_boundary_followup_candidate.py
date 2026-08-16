#!/usr/bin/env python3
"""Repair the Phil communication event page boundary after runtime feedback.

The failed predecessor candidate removed the leading 0x18 at 6017FC/601826.
Runtime proved that this merges ``디아나 님!`` with the following sentence.
This builder restores those page-head bytes exactly and instead repairs the
predecessor 6017F3: one synthetic E5 18 ext3 portal is replaced by two ordinary
native stock dictionary tokens.  This follows the runtime-proven page-boundary
lesson from the Garrod family: token iteration can matter even when payload
length and NUL addresses are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from analyze_p2_retired_slot_reclaim import _raw_pair_hits
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_remaining_dialogue_candidate import covered, diff_runs
from build_scenario_page_boundary_guard_candidate import encode_text, safe_unreachable_slots
from build_stage17t_global_20cell_followup_candidate import active_dictionary
from monoeye_rom import Tbl, load_rom, stock_base, token_from_dict_index, update_ws_checksum

PARENT = ROOT / "out/patch/phil_communication_control_followup_candidate.wsc"
PARENT_TBL = ROOT / "out/patch/phil_communication_control_followup_candidate.tbl"
PARENT_SAVE = ROOT / "sram/phil_communication_control_followup_candidate.sav"
REFERENCE = ROOT / "out/patch/stage18_extended_terminology_followup_candidate.wsc"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

OUT = ROOT / "out/patch/phil_communication_page_boundary_followup_candidate.wsc"
OUT_TBL = ROOT / "out/patch/phil_communication_page_boundary_followup_candidate.tbl"
OUT_SAVE = ROOT / "sram/phil_communication_page_boundary_followup_candidate.sav"
REPORT = ROOT / "out/patch/phil_communication_page_boundary_followup_candidate_report.json"

EXPECTED_PARENT_SHA = "d18e6cee0a84dbbe82698e25bb9d00407ab84edfdd9bb0c25647b045ca13c538"
EXPECTED_REFERENCE_SHA = "a5ba7d566cfdfc20ae55177c2a3849aa2dc08b080cc6c87f745ae8d254a83f4a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

ABS_FIRST = 0x6017F3
ABS_SECOND = 0x6017FC
ABS_MIDDLE = 0x601813
ABS_FOURTH = 0x601826
ABS_FOLLOW = 0x60183A

EXISTING_DIANA_SLOT = 0x08E7
NEW_FRAGMENT_SLOT = 0x0DB4
NEW_FRAGMENT = "　님！"
EXPECTED_FIRST_TEXT = "디아나　님！"
EXPECTED_SECOND_TEXT = "저희들은　지구만을　생각하고、　달의"
EXPECTED_MIDDLE_TEXT = "등한시하는　폐하의　뜻에는……"
EXPECTED_FOURTH_TEXT = "따라갈　수　없다고　말씀드렸습니다！！"


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def zpayload(rom: bytes, logical: int, max_len: int = 128) -> tuple[bytes, int]:
    sb = stock_base(rom)
    start = sb + logical
    end = rom.find(b"\x00", start, start + max_len)
    if end < 0:
        raise BuildError(f"unterminated record {logical:06X}")
    return rom[start:end], end - sb


def runtime_dictionary(rom: bytes):
    return active_dictionary(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )


def main() -> int:
    parent = bytes(load_rom(PARENT))
    reference = bytes(load_rom(REFERENCE))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent identity drifted: {sha(parent)}")
    if len(reference) != ROM_SIZE or sha(reference) != EXPECTED_REFERENCE_SHA:
        raise BuildError(f"reference identity drifted: {sha(reference)}")
    if not PARENT_TBL.is_file():
        raise BuildError("parent TBL missing")
    if not PARENT_SAVE.is_file() or PARENT_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("latest tested parent SaveRAM missing/wrong size")

    tbl = Tbl.load(PARENT_TBL)
    parent_dict = runtime_dictionary(parent)
    sb = stock_base(parent)

    # Parent runtime symptom must match the failed shift candidate exactly.
    p_first, t_first = zpayload(parent, ABS_FIRST)
    p_second, t_second = zpayload(parent, ABS_SECOND)
    p_middle, t_middle = zpayload(parent, ABS_MIDDLE)
    p_fourth, t_fourth = zpayload(parent, ABS_FOURTH)
    if p_first != bytes.fromhex("173418E518B1FD"):
        raise BuildError(f"6017F3 parent drift: {p_first.hex()}")
    if not p_second.startswith(bytes.fromhex("E518B1FE")) or len(p_second) != 22:
        raise BuildError(f"6017FC failed-shift parent drift: {p_second.hex()}")
    if not p_fourth.startswith(bytes.fromhex("E5183BDF")) or len(p_fourth) != 18:
        raise BuildError(f"601826 failed-shift parent drift: {p_fourth.hex()}")
    if (t_first, t_second, t_middle, t_fourth) != (0x6017FA, 0x601812, 0x601824, 0x601838):
        raise BuildError("Phil record terminator drift")

    # Restore the two continuation/page-head payloads exactly from the last
    # cumulative candidate before the mistaken lead-18 deletion.
    r_second, rt_second = zpayload(reference, ABS_SECOND)
    r_fourth, rt_fourth = zpayload(reference, ABS_FOURTH)
    if rt_second != t_second or rt_fourth != t_fourth:
        raise BuildError("reference terminator drift")
    if not r_second.startswith(bytes.fromhex("18E518B1FE")) or len(r_second) != 22:
        raise BuildError(f"6017FC reference drift: {r_second.hex()}")
    if not r_fourth.startswith(bytes.fromhex("18E5183BDF")) or len(r_fourth) != 18:
        raise BuildError(f"601826 reference drift: {r_fourth.hex()}")

    # Use the existing stock token 08E7='디아나' plus one proven unreachable
    # stock slot for '　님！', yielding exactly two native dictionary iterations.
    if parent_dict.expand_index(EXISTING_DIANA_SLOT, tbl) != "디아나":
        raise BuildError("existing 08E7 stock text drift")
    safe_pool = {int(row["index"]): row for row in safe_unreachable_slots(parent, parent_dict)}
    slot = safe_pool.get(NEW_FRAGMENT_SLOT)
    if slot is None:
        raise BuildError("planned 0DB4 stock slot is no longer proven unreachable")
    encoded_fragment = encode_text(tbl, NEW_FRAGMENT)
    if len(encoded_fragment) > int(slot["old_len"]):
        raise BuildError("0DB4 storage no longer fits new fragment")

    # Explicit reference proof for the selected slot before mutation.
    wanted = {NEW_FRAGMENT_SLOT}
    if external_occurrence_map(parent, ext3_aware=True, wanted=wanted).get(NEW_FRAGMENT_SLOT):
        raise BuildError("0DB4 gained an external reference")
    if nested_occurrence_map(parent_dict, wanted=wanted, ext3_aware=True).get(NEW_FRAGMENT_SLOT):
        raise BuildError("0DB4 gained a nested reference")
    if _raw_pair_hits(parent, [NEW_FRAGMENT_SLOT]).get(NEW_FRAGMENT_SLOT):
        raise BuildError("0DB4 gained a raw-pair reference")

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # 1) Write the new native stock fragment in-place, no pointer changes.
    slot_start = int(slot["entry_abs"])
    slot_old_len = int(slot["old_len"])
    candidate[slot_start : slot_start + len(encoded_fragment)] = encoded_fragment
    candidate[slot_start + len(encoded_fragment)] = 0
    allowed.append((slot_start, slot_start + slot_old_len + 1))

    # 2) Restore 6017FC/601826 page-head 18 exactly.
    candidate[sb + ABS_SECOND : sb + ABS_SECOND + len(r_second)] = r_second
    candidate[sb + ABS_FOURTH : sb + ABS_FOURTH + len(r_fourth)] = r_fourth
    allowed.extend([
        (sb + ABS_SECOND, sb + ABS_SECOND + len(r_second)),
        (sb + ABS_FOURTH, sb + ABS_FOURTH + len(r_fourth)),
    ])

    # 3) Replace the predecessor's one ext3 iteration with exactly two native
    # dictionary tokens.  Prefix, payload size, double-NUL addresses stay fixed.
    first_body = token_from_dict_index(EXISTING_DIANA_SLOT) + token_from_dict_index(NEW_FRAGMENT_SLOT)
    if len(first_body) != 4 or 0 in first_body:
        raise BuildError(f"unsafe native body: {first_body.hex()}")
    first_after = bytes.fromhex("173418") + first_body
    if len(first_after) != len(p_first):
        raise BuildError("6017F3 payload extent changed")
    candidate[sb + ABS_FIRST : sb + ABS_FIRST + len(first_after)] = first_after
    allowed.append((sb + ABS_FIRST, sb + ABS_FIRST + len(first_after)))

    # Pin the structural boundaries and untouched middle/following region.
    if bytes(candidate[sb + 0x6017FA : sb + 0x6017FC]) != bytes(parent[sb + 0x6017FA : sb + 0x6017FC]):
        raise BuildError("6017FA/FB double-NUL drift")
    if bytes(candidate[sb + ABS_MIDDLE : sb + t_middle + 1]) != bytes(parent[sb + ABS_MIDDLE : sb + t_middle + 1]):
        raise BuildError("601813 middle continuation changed")
    if bytes(candidate[sb + ABS_FOLLOW : sb + 0x601864]) != bytes(parent[sb + ABS_FOLLOW : sb + 0x601864]):
        raise BuildError("post-601826 control/dialogue region drift")

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    out = bytes(candidate)
    final_dict = runtime_dictionary(out)

    # Verify stock fragment raw marker encoding and semantic text.
    if bytes(final_dict.raw_entry(NEW_FRAGMENT_SLOT)) != encoded_fragment:
        raise BuildError("0DB4 raw marker encoding mismatch")
    if final_dict.expand_index(NEW_FRAGMENT_SLOT, tbl) != NEW_FRAGMENT:
        raise BuildError("0DB4 semantic expansion mismatch")

    f_first, ft_first = zpayload(out, ABS_FIRST)
    f_second, ft_second = zpayload(out, ABS_SECOND)
    f_middle, ft_middle = zpayload(out, ABS_MIDDLE)
    f_fourth, ft_fourth = zpayload(out, ABS_FOURTH)
    if (ft_first, ft_second, ft_middle, ft_fourth) != (t_first, t_second, t_middle, t_fourth):
        raise BuildError("candidate terminator moved")
    if f_first != first_after:
        raise BuildError("6017F3 final payload mismatch")
    if f_second != r_second or f_fourth != r_fourth:
        raise BuildError("restored continuation payload mismatch")
    if final_dict.expand(f_first[3:], tbl) != EXPECTED_FIRST_TEXT:
        raise BuildError(f"6017F3 render mismatch: {final_dict.expand(f_first[3:], tbl)!r}")

    # For the page-head continuations, decode visible body after the structural 18.
    if final_dict.expand(f_second[1:], tbl).rstrip("　") != EXPECTED_SECOND_TEXT:
        raise BuildError(f"6017FC body render mismatch: {final_dict.expand(f_second[1:], tbl)!r}")
    if final_dict.expand(f_middle, tbl).rstrip("　") != EXPECTED_MIDDLE_TEXT:
        raise BuildError(f"601813 body render mismatch: {final_dict.expand(f_middle, tbl)!r}")
    if final_dict.expand(f_fourth[1:], tbl).rstrip("　") != EXPECTED_FOURTH_TEXT:
        raise BuildError(f"601826 body render mismatch: {final_dict.expand(f_fourth[1:], tbl)!r}")

    # Finished-candidate reference proof: 0DB4 must be referenced exactly once,
    # by the second native token in 6017F3, and nowhere nested/raw elsewhere.
    ext = external_occurrence_map(out, ext3_aware=True, wanted=wanted).get(NEW_FRAGMENT_SLOT, [])
    raw = _raw_pair_hits(out, [NEW_FRAGMENT_SLOT]).get(NEW_FRAGMENT_SLOT, [])
    nested = nested_occurrence_map(final_dict, wanted=wanted, ext3_aware=True).get(NEW_FRAGMENT_SLOT, [])
    expected_token_abs = ABS_FIRST + 5
    ext_pos = sorted(int(str(x["token_abs"]), 16) for x in ext)
    raw_pos = sorted(int(str(x["token_abs"]), 16) for x in raw)
    if ext_pos != [expected_token_abs] or raw_pos != [expected_token_abs] or nested:
        raise BuildError(f"0DB4 final reference proof failed ext={ext_pos} raw={raw_pos} nested={nested}")

    runs = diff_runs(parent, out)
    outside = [run for run in runs if not covered(run, allowed)]
    if outside:
        raise BuildError(f"unexpected diff outside allowlist: {outside[:8]}")

    OUT.write_bytes(out)
    shutil.copy2(PARENT_TBL, OUT_TBL)
    shutil.copy2(PARENT_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_phil_communication_page_boundary_followup_candidate.py",
        "status": "runtime_test_pending",
        "parent": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(parent)},
        "reference": {"path": str(REFERENCE.relative_to(ROOT)), "sha256": sha(reference)},
        "candidate": {"path": str(OUT.relative_to(ROOT)), "sha256": sha(out), "checksum": f"{checksum:04X}", "size": len(out)},
        "tbl": {"path": str(OUT_TBL.relative_to(ROOT)), "sha256": sha(OUT_TBL.read_bytes())},
        "saveram": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(OUT_SAVE.read_bytes()), "size": OUT_SAVE.stat().st_size},
        "root_cause": "6017F3 was collapsed from original multi-native iteration grammar to one E5 18 portal before a double-NUL + 18 page-head boundary; runtime feedback proved deleting the next 18 only merged pages",
        "patches": {
            "6017F3": {"before": p_first.hex().upper(), "after": f_first.hex().upper(), "render": EXPECTED_FIRST_TEXT, "grammar": "17 34 18 + native stock + native stock"},
            "6017FC": {"before": p_second.hex().upper(), "after": f_second.hex().upper(), "visible_after_structural_18": EXPECTED_SECOND_TEXT},
            "601826": {"before": p_fourth.hex().upper(), "after": f_fourth.hex().upper(), "visible_after_structural_18": EXPECTED_FOURTH_TEXT},
            "stock_0DB4": {"entry_abs": f"{slot_start:07X}", "encoded_hex": encoded_fragment.hex().upper(), "text": NEW_FRAGMENT, "old_len": slot_old_len, "pointer_table_changes": 0},
        },
        "guards": {
            "6017FA_6017FB_double_nul_preserved": True,
            "601813_byte_exact": True,
            "60183A_to_601863_byte_exact": True,
            "record_extents_preserved": True,
            "terminators_preserved": True,
            "selected_stock_slot_parent_unreachable": True,
            "selected_stock_slot_final_reference_exact": True,
            "unexpected_diff_runs": len(outside),
        },
        "diff": {"runs": len(runs), "bytes": sum(b-a for a,b in runs)},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
