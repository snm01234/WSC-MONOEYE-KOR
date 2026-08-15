#!/usr/bin/env python3
"""Independent static audit for the ending-scene follow-up candidate."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_START,
    SEG_DICT,
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "main_tip_ending_scene_followup_candidate.wsc"
SAVE = ROOT / "sram/main_tip_ending_scene_followup_candidate.sav"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = PATCH / "main_tip_ending_scene_followup_candidate_report.json"
OUT = PATCH / "main_tip_ending_scene_followup_candidate_audit.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

EXPECTED_MAIN = "1886d04e697baba17b454089dbd07cc556d3e49c5808e23e321cf6863773bc3d"
EXPECTED_CANDIDATE = "2ec5a8e57ff58afa9076ba68ed10f703c6a9dbf6caa8d58587d99cd9654ffbce"
EXPECTED_SAVE = "b9c8a95318050a86de48f1fa782b9de80f466a527ad253a7f4393a62b8710053"
GRAPHICS = 0x63AE59
TRANSLATION = 0x63B5ED
TARGET_GRAPHICS = "시그……！！"
TARGET_TRANSLATION = "그녀의　희생을　헛되게　하지　않으려면、"
EXPECTED_GRAPHICS_BODY = bytes.fromhex("FB2F010101")
EXPECTED_TRANSLATION_BODY = bytes.fromhex("E518159901010101010101010101")


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record(rom: bytes, logical: int) -> tuple[bytes, int]:
    base = stock_base(rom)
    got = read_encoded_z_safe(rom, base + logical, max_len=256)
    if got is None:
        raise AuditError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1] - base)


def active_dictionary(rom: bytes) -> Dictionary:
    meta = load_ext_meta(EXT_META)
    meta3 = load_ext_meta(EXT3_META)
    base = make_dictionary(rom, meta)
    return Dictionary(
        rom,
        count=base.count,
        ext_ptr_off=base.ext_ptr_off,
        ext_seg=base.ext_seg,
        stock_count=base.stock_count,
        ext_in_expansion=base.ext_in_expansion,
        ext3_ptr_off=0,
        ext3_seg=int(str(meta3.get("exp_seg0") or "11"), 16),
        ext3_banks=int(meta3.get("num_banks") or 0),
        ext3_alias_page_count=5,
        ext3_alias_local_start=0x0600,
        ext3_alias_seg=0x21,
    )


def main() -> int:
    main = bytes(load_rom(MAIN))
    candidate = bytes(load_rom(CANDIDATE))
    save = SAVE.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    d = active_dictionary(candidate)
    sb = stock_base(candidate)

    gp, gb, _ = split_prefix_body(record(candidate, GRAPHICS)[0])
    tp, tb, _ = split_prefix_body(record(candidate, TRANSLATION)[0])
    graphics_render = d.expand(gb, tbl).rstrip("　 \t")
    translation_render = d.expand(tb, tbl).rstrip("　 \t")

    stock_bank = sb + SEG_DICT * BANK_SIZE
    stock_ptr = struct.unpack_from("<H", candidate, stock_bank + DICT_PTR_START + 0x0B2F * 2)[0]
    stock_payload = d.raw_entry(0x0B2F)
    stock_render = d.expand(stock_payload, tbl).rstrip("　 \t")

    ext3_seg, ext3_local = d._ext3_bank_local(0x02599)
    ext3_ptr = struct.unpack_from("<H", candidate, ext3_seg * BANK_SIZE + ext3_local * 2)[0]
    ext3_payload = d.raw_entry(0x02599)
    ext3_render = d.expand(ext3_payload, tbl).rstrip("　 \t")

    main_gp, main_gb, _ = split_prefix_body(record(main, GRAPHICS)[0])
    main_tp, main_tb, _ = split_prefix_body(record(main, TRANSLATION)[0])

    stored_checksum = struct.unpack_from("<H", candidate, len(candidate) - 2)[0]
    computed_checksum = sum(candidate[:-2]) & 0xFFFF

    checks = {
        "main_identity_exact": sha(main) == EXPECTED_MAIN,
        "candidate_identity_exact": sha(candidate) == EXPECTED_CANDIDATE,
        "candidate_save_exact": sha(save) == EXPECTED_SAVE and save == live_save,
        "builder_report_ok": report.get("ok") is True and all((report.get("checks") or {}).values()),
        "graphics_prefix_preserved": gp == bytes.fromhex("173418") == main_gp,
        "graphics_body_exact_native": gb == EXPECTED_GRAPHICS_BODY and gb[:2] != b"\xE5\x18",
        "graphics_render_exact": graphics_render == TARGET_GRAPHICS,
        "stock_pointer_exact": stock_ptr == 0xFFF4,
        "stock_phrase_exact": stock_render == TARGET_GRAPHICS,
        "translation_prefix_preserved": tp == bytes.fromhex("171C18") == main_tp,
        "translation_body_exact_ext3": tb == EXPECTED_TRANSLATION_BODY,
        "translation_render_exact": translation_render == TARGET_TRANSLATION,
        "translation_exact_20_cells": len(translation_render) == 20,
        "translation_regular_ext3_mapping": (ext3_seg, ext3_local) == (0x12, 0x0599),
        "translation_ext3_payload_exact": ext3_render == TARGET_TRANSLATION,
        "target_extents_preserved": len(gb) == len(main_gb) and len(tb) == len(main_tb),
        "target_terminators_preserved": record(candidate, GRAPHICS)[1] == record(main, GRAPHICS)[1] and record(candidate, TRANSLATION)[1] == record(main, TRANSLATION)[1],
        "checksum_exact": stored_checksum == computed_checksum == 0x1C50,
        "main_unchanged_after_build": sha(MAIN.read_bytes()) == EXPECTED_MAIN,
        "live_save_unchanged_after_build": sha(LIVE_SAVE.read_bytes()) == EXPECTED_SAVE,
    }
    if not all(checks.values()):
        failed = [name for name, ok in checks.items() if not ok]
        raise AuditError("audit failed: " + ", ".join(failed))

    result = {
        "schema_version": 1,
        "generated_by": "tools/audit_main_tip_ending_scene_followup_candidate.py",
        "ok": True,
        "checks": checks,
        "graphics": {
            "abs": f"{GRAPHICS:06X}",
            "body_hex": gb.hex().upper(),
            "render": graphics_render,
            "stock_index": "0B2F",
            "stock_pointer": f"{stock_ptr:04X}",
        },
        "translation": {
            "abs": f"{TRANSLATION:06X}",
            "body_hex": tb.hex().upper(),
            "render": translation_render,
            "cells": len(translation_render),
            "ext3_index": "02599",
            "physical": f"{ext3_seg:02X}:{ext3_local:04X}",
            "pointer": f"{ext3_ptr:04X}",
        },
        "checksum": f"{stored_checksum:04X}",
        "runtime_validation_still_required": True,
        "reason": "Static audit can prove the reported frame no longer performs E5 18 expansion-dictionary mapping, but only emulator play can prove that this removes the visible upper-art glitch."
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
