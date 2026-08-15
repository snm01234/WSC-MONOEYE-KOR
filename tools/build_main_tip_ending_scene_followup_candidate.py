#!/usr/bin/env python3
"""Build a current-main candidate for two user-reported ending-scene issues.

1) 63AE59 (Sera: ``시그……！！``) currently renders through an E5 18 alias
   portal while special ending art is active. The event/speaker controls around
   the line are byte-exact to Original, so this candidate removes expansion-bank
   mapping from the reported frame by moving only the visible body to one proven
   unreachable ordinary stock dictionary slot.
2) 63B5ED is still pristine Japanese. It is translated into an exactly 20-cell
   Korean line through one true-free regular ext3 slot (not an alias slot).

The live main TIP and live SaveRAM are never overwritten. Graphics correction
requires emulator confirmation because the bank-switch interference diagnosis
is runtime-causal rather than a static image proof.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from analyze_p2_retired_slot_reclaim import _raw_pair_hits  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from audit_broad_japanese_residuals import current_strong_retired_slots  # noqa: E402
from build_p2_stock_spill_candidate import SPILL_FLOOR, _stock_phrase_cursor  # noqa: E402
from build_remaining_dialogue_candidate import encode_phrase  # noqa: E402
from expand_dictionary import write_dictionary_slots_spill  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from mixed_residual_reference_union import (  # noqa: E402
    _working_two_byte_external_refs,
    build_free_slot_inventory,
    build_reference_union,
    write_ext3_slots_guarded,
)
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META_PATH = PATCH / "exp_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"
SPEC = ROOT / "data/main_tip_ending_scene_followup_ko.json"
OUT_ROM = PATCH / "main_tip_ending_scene_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/main_tip_ending_scene_followup_candidate.sav"
REPORT = PATCH / "main_tip_ending_scene_followup_candidate_report.json"

EXPECTED_MAIN_SHA = "1886d04e697baba17b454089dbd07cc556d3e49c5808e23e321cf6863773bc3d"
EXPECTED_SAVE_SHA = "b9c8a95318050a86de48f1fa782b9de80f466a527ad253a7f4393a62b8710053"
EXPECTED_TBL_SHA = "cbeafbe074015bc79ad0dce1ade3be57a4d56bb1e5bf46102097cc6f0e17261c"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

GRAPHICS_LINE = 0x63AE59
UNTRANSLATED_LINE = 0x63B5ED
GRAPHICS_PREFIX = bytes.fromhex("173418")
UNTRANSLATED_PREFIX = bytes.fromhex("171C18")
GRAPHICS_CURRENT = "시그……！！"
UNTRANSLATED_SOURCE = "彼女の行為を無にしないためにも、"
UNTRANSLATED_KO = "그녀의　희생을　헛되게　하지　않으려면、"

# Deterministic slots; both are reproved free against the exact parent before use.
NATIVE_STOCK_INDEX = 0x0B2F
REGULAR_EXT3_INDEX = 0x02599
EXPECTED_GRAPHICS_EXT3_INDEX = 0x05D36

# Five-page alias runtime accepted earlier by emulator. The current offline hash
# detector no longer recognizes the composite leaf after later runtime changes,
# so this builder reconstructs the already-live mapping only for static decoding.
ALIAS_PAGE_COUNT = 5
ALIAS_LOCAL_START = 0x0600
ALIAS_SEG0 = 0x21

CONTROL_NEIGHBORS = (
    0x63AE3E,
    0x63AE43,
    0x63AE51,
    0x63AE56,
    0x63AE63,
    0x63AE68,
    0x63AE76,
    0x63AE7B,
)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha(data)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def active_dictionary(rom: bytes, ext_meta: dict[str, Any], ext3_meta: dict[str, Any]) -> Dictionary:
    base = make_dictionary(rom, ext_meta)
    num_banks = int(ext3_meta.get("num_banks") or 0)
    if num_banks != 16 or str(ext3_meta.get("exp_seg0") or "11").upper() != "11":
        raise BuildError("ext3 metadata drifted")
    return Dictionary(
        rom,
        count=base.count,
        ext_ptr_off=base.ext_ptr_off,
        ext_seg=base.ext_seg,
        stock_count=base.stock_count,
        ext_in_expansion=base.ext_in_expansion,
        ext3_ptr_off=0,
        ext3_seg=0x11,
        ext3_banks=num_banks,
        ext3_alias_page_count=ALIAS_PAGE_COUNT,
        ext3_alias_local_start=ALIAS_LOCAL_START,
        ext3_alias_seg=ALIAS_SEG0,
    )


def record(rom: bytes, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1] - sb)


def strip_pad(text: str) -> str:
    return text.rstrip("　 \t")


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(before):
        if before[i] == after[i]:
            i += 1
            continue
        start = i
        while i < len(before) and before[i] != after[i]:
            i += 1
        runs.append((start, i))
    return runs


def covered(run: tuple[int, int], intervals: list[tuple[int, int]]) -> bool:
    start, end = run
    cursor = start
    for lo, hi in sorted(intervals):
        if hi <= cursor:
            continue
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return cursor >= end


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save = MAIN_SAVE.read_bytes()
    tbl_bytes = TBL_PATH.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if len(save) != SAVE_SIZE or sha(save) != EXPECTED_SAVE_SHA:
        raise BuildError(f"live SaveRAM identity drifted: {sha(save)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError("active TBL identity drifted")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("review_status") != "user_reported_candidate":
        raise BuildError("ending follow-up catalog status drifted")
    spec_by_abs = {str(row["abs"]).upper(): row for row in spec.get("targets") or []}
    if set(spec_by_abs) != {f"{GRAPHICS_LINE:06X}", f"{UNTRANSLATED_LINE:06X}"}:
        raise BuildError("ending follow-up target set drifted")

    tbl = Tbl.load(TBL_PATH)
    source_tbl = Tbl.load(ROOT / "data/monoeye.tbl")
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    d_parent = active_dictionary(parent, ext_meta, ext3_meta)
    d_original = Dictionary(original)
    sb = stock_base(parent)

    # Identify the reported graphics frame exactly and prove the surrounding
    # event/speaker controls were never changed by the translation patch.
    graphics_payload, graphics_term = record(parent, GRAPHICS_LINE)
    graphics_original_payload, graphics_original_term = record(original, GRAPHICS_LINE)
    gp, gb, gkind = split_prefix_body(graphics_payload)
    ogp, ogb, ogkind = split_prefix_body(graphics_original_payload)
    if gp != GRAPHICS_PREFIX or ogp != GRAPHICS_PREFIX or gkind != "dialogue" or ogkind != "dialogue":
        raise BuildError("63AE59 prefix grammar drifted")
    if graphics_term != graphics_original_term or len(graphics_payload) != len(graphics_original_payload):
        raise BuildError("63AE59 boundary drifted from Original")
    if len(gb) < 4 or gb[:2] != b"\xE5\x18":
        raise BuildError("63AE59 no longer uses E5 18")
    graphics_ext3_index = dict_index_from_ext3_token(*gb[:4])
    if graphics_ext3_index != EXPECTED_GRAPHICS_EXT3_INDEX:
        raise BuildError(f"63AE59 ext3 index drifted: {graphics_ext3_index:05X}")
    graphics_render = strip_pad(d_parent.expand(gb, tbl))
    if graphics_render != GRAPHICS_CURRENT:
        raise BuildError(f"63AE59 current render drifted: {graphics_render!r}")
    alias_seg, alias_local = d_parent._ext3_bank_local(graphics_ext3_index)
    if (alias_seg, alias_local) != (0x25, 0x0736):
        raise BuildError(f"63AE59 alias mapping drifted: {alias_seg:02X}:{alias_local:04X}")

    control_proof = []
    for logical in CONTROL_NEIGHBORS:
        cur, cur_term = record(parent, logical)
        src, src_term = record(original, logical)
        same = cur == src and cur_term == src_term
        control_proof.append({
            "abs": f"{logical:06X}",
            "same_as_original": same,
            "payload_hex": cur.hex().upper(),
            "terminator": f"{cur_term:06X}",
        })
        if not same:
            raise BuildError(f"neighbor control/speaker record changed at {logical:06X}")

    # Identify the untranslated line from the pristine source/current body.
    untranslated_payload, untranslated_term = record(parent, UNTRANSLATED_LINE)
    untranslated_original_payload, untranslated_original_term = record(original, UNTRANSLATED_LINE)
    up, ub, ukind = split_prefix_body(untranslated_payload)
    oup, oub, oukind = split_prefix_body(untranslated_original_payload)
    if up != UNTRANSLATED_PREFIX or oup != UNTRANSLATED_PREFIX or ukind != "dialogue" or oukind != "dialogue":
        raise BuildError("63B5ED prefix grammar drifted")
    if untranslated_term != untranslated_original_term or untranslated_payload != untranslated_original_payload:
        raise BuildError("63B5ED is no longer pristine Japanese")
    untranslated_render = strip_pad(d_original.expand(oub, source_tbl))
    if untranslated_render != UNTRANSLATED_SOURCE:
        raise BuildError(f"63B5ED source drifted: {untranslated_render!r}")
    if len(UNTRANSLATED_KO) != 20:
        raise BuildError(f"63B5ED Korean line is not exactly 20 cells: {len(UNTRANSLATED_KO)}")

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # Rehome 63AE59 to an ordinary stock token. Reprove the selected slot is a
    # strong retired slot with no current external, nested, or raw-pair consumer.
    retired = current_strong_retired_slots(original, parent, d_parent)
    wanted = set(retired)
    external = external_occurrence_map(parent, ext3_aware=True, wanted=wanted)
    nested = nested_occurrence_map(d_parent, wanted=wanted, ext3_aware=True)
    raw_hits = _raw_pair_hits(parent, retired)
    safe_stock = [i for i in retired if not external.get(i) and not nested.get(i) and not raw_hits.get(i)]
    if NATIVE_STOCK_INDEX not in safe_stock:
        raise BuildError(f"stock slot {NATIVE_STOCK_INDEX:04X} is no longer strongly retired")
    if not dict_token_safe_in_zstring(NATIVE_STOCK_INDEX):
        raise BuildError("selected stock token is zstring-unsafe")
    stock_phrase = encode_phrase(GRAPHICS_CURRENT, tbl)
    stock_cursor_before = _stock_phrase_cursor(candidate)
    if stock_cursor_before != 0xFFF4:
        raise BuildError(f"stock spill cursor drifted: {stock_cursor_before:04X}")
    pointers_written, stock_cursor_after = write_dictionary_slots_spill(
        candidate,
        {NATIVE_STOCK_INDEX: stock_phrase},
        spill_start=SPILL_FLOOR,
        allow_aux_consumers=False,
        locs=_working_two_byte_external_refs(parent),
    )
    if stock_cursor_after != 0xFFFF or int(pointers_written[NATIVE_STOCK_INDEX]) != stock_cursor_before:
        raise BuildError("stock phrase allocation drifted")
    stock_bank = sb + SEG_DICT * BANK_SIZE
    stock_ptr_abs = stock_bank + DICT_PTR_START + NATIVE_STOCK_INDEX * 2
    allowed.extend([
        (stock_ptr_abs, stock_ptr_abs + 2),
        (stock_bank + stock_cursor_before, stock_bank + stock_cursor_after),
    ])
    native_token = token_from_dict_index(NATIVE_STOCK_INDEX)
    new_graphics_body = native_token + b"\x01" * (len(gb) - len(native_token))
    graphics_body_abs = sb + GRAPHICS_LINE + len(gp)
    candidate[graphics_body_abs:graphics_body_abs + len(gb)] = new_graphics_body
    allowed.append((graphics_body_abs, graphics_body_abs + len(gb)))

    # Allocate the untranslated line into a true-free regular ext3 slot. The
    # selected token must not occur anywhere in the whole current ROM, which also
    # covers nested dictionary payloads missed by narrower script scanners.
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    inventory = build_free_slot_inventory(parent, union=union, ext_meta=ext_meta, ext3_meta=ext3_meta)
    if REGULAR_EXT3_INDEX not in inventory.ext3_free:
        raise BuildError(f"ext3 slot {REGULAR_EXT3_INDEX:05X} is no longer true-free")
    raw_index = REGULAR_EXT3_INDEX - 0x1000
    page, local = raw_index >> 12, raw_index & 0x0FFF
    if page < ALIAS_PAGE_COUNT and local >= ALIAS_LOCAL_START:
        raise BuildError("selected untranslated slot unexpectedly belongs to alias range")
    ext3_token = token_from_dict_index(REGULAR_EXT3_INDEX)
    if parent.find(ext3_token) >= 0:
        raise BuildError("selected ext3 token bytes already occur in current ROM")
    untranslated_phrase = encode_phrase(UNTRANSLATED_KO, tbl)
    before_ext3 = bytes(candidate)
    ext3_write, ext3_guard = write_ext3_slots_guarded(
        candidate,
        {REGULAR_EXT3_INDEX: untranslated_phrase},
        union=union,
        num_banks=16,
    )
    if ext3_write.get("written") != 1 or ext3_write.get("skipped_overflow"):
        raise BuildError(f"ext3 write failed: {ext3_write}")
    ext3_seg = 0x11 + page
    ext3_bank_start = ext3_seg * BANK_SIZE
    ext3_end_cursor = int(ext3_write["by_bank"][f"{ext3_seg:02X}"])
    ext3_start_cursor = ext3_end_cursor - (len(untranslated_phrase) + 1)
    ext3_ptr_abs = ext3_bank_start + local * 2
    expected_ext3_changes = [
        (ext3_ptr_abs, ext3_ptr_abs + 2),
        (ext3_bank_start + ext3_start_cursor, ext3_bank_start + ext3_end_cursor),
    ]
    ext3_unexpected = []
    for lo, hi in diff_runs(before_ext3, bytes(candidate)):
        if not covered((lo, hi), expected_ext3_changes):
            ext3_unexpected.append((lo, hi))
    if ext3_unexpected:
        raise BuildError(f"ext3 writer changed unexpected ranges: {ext3_unexpected[:8]}")
    allowed.extend(expected_ext3_changes)

    new_untranslated_body = ext3_token + b"\x01" * (len(ub) - len(ext3_token))
    untranslated_body_abs = sb + UNTRANSLATED_LINE + len(up)
    candidate[untranslated_body_abs:untranslated_body_abs + len(ub)] = new_untranslated_body
    allowed.append((untranslated_body_abs, untranslated_body_abs + len(ub)))

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)

    # Candidate-bound semantic and structure verification.
    d_result = active_dictionary(result, ext_meta, ext3_meta)
    gp2, gb2, _ = split_prefix_body(record(result, GRAPHICS_LINE)[0])
    up2, ub2, _ = split_prefix_body(record(result, UNTRANSLATED_LINE)[0])
    graphics_after = strip_pad(d_result.expand(gb2, tbl))
    untranslated_after = strip_pad(d_result.expand(ub2, tbl))
    if graphics_after != GRAPHICS_CURRENT or gb2[:2] == b"\xE5\x18":
        raise BuildError(f"63AE59 native render verify failed: {graphics_after!r} {gb2.hex()}")
    if untranslated_after != UNTRANSLATED_KO or ub2[:4] != ext3_token:
        raise BuildError(f"63B5ED translation verify failed: {untranslated_after!r}")
    if gp2 != gp or up2 != up:
        raise BuildError("target prefix changed")
    if record(result, GRAPHICS_LINE)[1] != graphics_term or record(result, UNTRANSLATED_LINE)[1] != untranslated_term:
        raise BuildError("target terminator moved")
    for logical in CONTROL_NEIGHBORS:
        if record(result, logical) != record(parent, logical):
            raise BuildError(f"candidate changed neighbor control/speaker record {logical:06X}")

    runs = diff_runs(parent, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected candidate diff runs: {unexpected[:12]}")
    stored = int.from_bytes(result[-2:], "little")
    if stored != checksum or stored != (sum(result[:-2]) & 0xFFFF):
        raise BuildError("WonderSwan checksum verification failed")
    if MAIN.read_bytes() != parent or MAIN_SAVE.read_bytes() != save:
        raise BuildError("live main TIP or SaveRAM changed during build")

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_SAVE, save)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_main_tip_ending_scene_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_graphics_test_required",
        "inputs": {
            "main_tip": identity(MAIN, parent),
            "main_saveram": identity(MAIN_SAVE, save),
            "active_tbl": identity(TBL_PATH, tbl_bytes),
            "catalog": identity(SPEC),
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, result),
            "candidate_saveram": identity(OUT_SAVE, save),
        },
        "graphics_issue": {
            "abs": f"{GRAPHICS_LINE:06X}",
            "source_jp": strip_pad(d_original.expand(ogb, source_tbl)),
            "current_render": graphics_render,
            "before_body_hex": gb.hex().upper(),
            "before_ext3_index": f"{graphics_ext3_index:05X}",
            "before_runtime_mapping": f"expansion {alias_seg:02X}:{alias_local:04X}",
            "after_body_hex": gb2.hex().upper(),
            "after_stock_index": f"{NATIVE_STOCK_INDEX:04X}",
            "after_render": graphics_after,
            "stock_phrase_cursor": [f"{stock_cursor_before:04X}", f"{stock_cursor_after:04X}"],
            "neighbor_control_records_original_exact": control_proof,
            "diagnosis": (
                "The event/speaker controls around the frame are Original-exact, while the visible line alone invokes "
                "an E5 18 alias in physical expansion bank 25. Rehoming the line to an ordinary stock token removes "
                "ROM1 expansion-bank mapping during the reported special-art frame. This is a strong runtime-bank "
                "interference diagnosis and must be confirmed in emulator."
            ),
        },
        "translation_issue": {
            "abs": f"{UNTRANSLATED_LINE:06X}",
            "source_jp": untranslated_render,
            "target_ko": UNTRANSLATED_KO,
            "display_cells": len(UNTRANSLATED_KO),
            "body_capacity": len(ub),
            "before_body_hex": ub.hex().upper(),
            "after_body_hex": ub2.hex().upper(),
            "new_ext3_index": f"{REGULAR_EXT3_INDEX:05X}",
            "new_ext3_mapping": f"regular expansion {ext3_seg:02X}:{local:04X}",
            "ext3_guard": ext3_guard.as_dict(),
            "ext3_write": ext3_write,
            "after_render": untranslated_after,
        },
        "checks": {
            "main_identity_exact": sha(parent) == EXPECTED_MAIN_SHA,
            "live_saveram_exact": sha(save) == EXPECTED_SAVE_SHA,
            "graphics_line_identified_exactly": graphics_ext3_index == EXPECTED_GRAPHICS_EXT3_INDEX,
            "graphics_neighbor_controls_original_exact": all(row["same_as_original"] for row in control_proof),
            "graphics_target_no_e518_after": gb2[:2] != b"\xE5\x18",
            "graphics_text_preserved": graphics_after == GRAPHICS_CURRENT,
            "untranslated_source_was_pristine": untranslated_payload == untranslated_original_payload,
            "translation_exactly_20_cells": len(UNTRANSLATED_KO) == 20,
            "translation_render_exact": untranslated_after == UNTRANSLATED_KO,
            "target_extents_preserved": len(gb2) == len(gb) and len(ub2) == len(ub),
            "target_terminators_preserved": record(result, GRAPHICS_LINE)[1] == graphics_term and record(result, UNTRANSLATED_LINE)[1] == untranslated_term,
            "stock_slot_strongly_retired": NATIVE_STOCK_INDEX in safe_stock,
            "ext3_slot_true_free": REGULAR_EXT3_INDEX in inventory.ext3_free,
            "ext3_token_absent_before": parent.find(ext3_token) < 0,
            "ext3_guard_passed": ext3_guard.ok,
            "diff_allowlist_clean": not unexpected,
            "ws_checksum_valid": True,
            "main_tip_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": MAIN_SAVE.read_bytes() == save,
            "candidate_saveram_matches_live": OUT_SAVE.read_bytes() == save,
        },
        "diff": {
            "changed_runs": len(runs),
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "unexpected_runs": len(unexpected),
            "runs": [
                {"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}", "length": hi - lo}
                for lo, hi in runs
            ],
        },
        "ws_checksum": f"{checksum:04X}",
        "runtime_validation": {
            "required": True,
            "steps": [
                "Load main_tip_ending_scene_followup_candidate.wsc with the paired SaveRAM.",
                "Re-enter the ending scene from normal gameplay/SaveRAM rather than an old savestate, because savestates restore VRAM and ROM-bank runtime state.",
                "At Sera's '시그……！！' frame, confirm the upper special artwork no longer glitches.",
                "Later, confirm 63B5ED renders the Korean line with no Japanese residue and the next line/event continues normally."
            ]
        },
        "promotion": "blocked_pending_user_runtime_graphics_validation"
    }
    if not all(report["checks"].values()):
        raise BuildError("one or more final checks failed")
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "status": report["status"],
        "candidate": report["outputs"]["candidate_rom"],
        "candidate_saveram": report["outputs"]["candidate_saveram"],
        "graphics_issue": {
            "abs": report["graphics_issue"]["abs"],
            "before_ext3_index": report["graphics_issue"]["before_ext3_index"],
            "before_runtime_mapping": report["graphics_issue"]["before_runtime_mapping"],
            "after_stock_index": report["graphics_issue"]["after_stock_index"],
            "after_render": report["graphics_issue"]["after_render"],
        },
        "translation_issue": {
            "abs": report["translation_issue"]["abs"],
            "target_ko": report["translation_issue"]["target_ko"],
            "display_cells": report["translation_issue"]["display_cells"],
            "new_ext3_index": report["translation_issue"]["new_ext3_index"],
            "after_render": report["translation_issue"]["after_render"],
        },
        "diff": report["diff"],
        "promotion": report["promotion"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
