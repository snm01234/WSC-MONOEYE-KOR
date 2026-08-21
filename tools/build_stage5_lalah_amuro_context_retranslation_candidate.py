#!/usr/bin/env python3
"""Build a narrow Stage 5 Lalah-Amuro dialogue-register retranslation candidate.

Ordinary scenario rows use the proven ext3 retarget builder. 60E087 is a
runtime-proven parameterized E51D continuation record, so its four record bytes
remain byte-exact and only its unique parameter helper leaf is retargeted.
"""
from __future__ import annotations

import json
from pathlib import Path

import build_global_dialogue_boundary_retranslation_candidate as global_builder
import build_stage3_reflow_idhelp_followup_candidate as base
import dialogue_runtime_contracts as drc
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from hangul_marker import marker_code
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base, update_ws_checksum, ws_header

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC = ROOT / "data/stage5_lalah_amuro_context_retranslation_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/stage5_lalah_amuro_context_retranslation_candidate.wsc"
OUT_SAVE = ROOT / "sram/stage5_lalah_amuro_context_retranslation_candidate.sav"
OUT_REPORT = ROOT / "out/patch/stage5_lalah_amuro_context_retranslation_report.json"
TMP_ROM = ROOT / "out/patch/.stage5_lalah_amuro_standard.tmp.wsc"
TMP_SAVE = ROOT / "sram/.stage5_lalah_amuro_standard.tmp.sav"
TMP_REPORT = ROOT / "out/patch/.stage5_lalah_amuro_standard.tmp.json"
EXPECTED_PARENT_SHA = "e91cde50cbe15386561495fb53fd51c26a279ad0614aad57811d0169efbc0bdb"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
SPECIAL_ADDRESS = "60E087"
SPECIAL_EXPECTED_BODY = "E51D0A01"
SPECIAL_PARAM = "0A"


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return global_builder.sha(data)


def ident(path: Path, data: bytes | None = None) -> dict[str, object]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def main() -> int:
    parent = MAIN.read_bytes()
    live_save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"main identity drifted: {len(parent)} {sha(parent)}")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if str(spec.get("parent_sha256") or "").lower() != EXPECTED_PARENT_SHA:
        raise BuildError("spec parent SHA mismatch")
    special_rows = list(spec.get("event_safe_native2_param") or [])
    if len(special_rows) != 1:
        raise BuildError("expected exactly one event-safe continuation row")
    special = special_rows[0]
    if str(special.get("address") or "").upper() != SPECIAL_ADDRESS:
        raise BuildError(f"unexpected event-safe address: {special.get('address')}")
    if str(special.get("expected_body_hex") or "").upper() != SPECIAL_EXPECTED_BODY:
        raise BuildError("special body contract mismatch in spec")
    if str(special.get("param_index") or "").upper() != SPECIAL_PARAM:
        raise BuildError("special parameter contract mismatch in spec")

    # Apply the ordinary ext3 scenario rows first.
    global_builder.SPEC = SPEC
    global_builder.OUT_ROM = TMP_ROM
    global_builder.OUT_SAVE = TMP_SAVE
    global_builder.OUT_REPORT = TMP_REPORT
    global_builder.EXPECTED_PARENT_SHA = EXPECTED_PARENT_SHA
    global_builder.main()
    intermediate = TMP_ROM.read_bytes()
    standard_report = json.loads(TMP_REPORT.read_text(encoding="utf-8"))
    if not standard_report.get("ok"):
        raise BuildError("standard scenario retarget stage failed")
    if MAIN.read_bytes() != parent or MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("main TIP or live SaveRAM changed during standard stage")

    tbl = Tbl.load(TBL_PATH)
    ext = load_ext_meta(EXT_META)
    ext3 = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(intermediate, ext, ext3)

    logical = int(SPECIAL_ADDRESS, 16)
    body_at = stock_base(intermediate) + logical
    expected_body = bytes.fromhex(SPECIAL_EXPECTED_BODY)
    actual_body = intermediate[body_at : body_at + len(expected_body)]
    if actual_body != expected_body:
        raise BuildError(
            f"{SPECIAL_ADDRESS} event-safe body drifted: "
            f"{actual_body.hex().upper()} != {SPECIAL_EXPECTED_BODY}"
        )
    if intermediate.count(expected_body) != 1:
        raise BuildError(f"{SPECIAL_EXPECTED_BODY} is no longer unique in the candidate")

    before_runtime = drc._decode(dictionary, expected_body, tbl, target=True).rstrip("　 \t")
    expected_before = base.norm_spaces(str(special["before"]))
    if base.norm_spaces(before_runtime) != expected_before:
        raise BuildError(f"{SPECIAL_ADDRESS} current runtime text drifted: {before_runtime!r}")

    param = int(SPECIAL_PARAM, 16)
    if param != expected_body[2]:
        raise BuildError("parameter index does not match record body")
    seg_base = drc.EVENT_SAFE_NATIVE2_PARAM_SEG << 16
    ptr_at = seg_base + drc.EVENT_SAFE_NATIVE2_PARAM_PTR_TABLE + param * 2
    off = int.from_bytes(intermediate[ptr_at : ptr_at + 2], "little")
    if not drc.EVENT_SAFE_NATIVE2_PARAM_DATA_MIN <= off < drc.EVENT_SAFE_NATIVE2_PARAM_DATA_MAX:
        raise BuildError(f"helper pointer invalid: {off:04X}")
    helper_at = seg_base + off
    got = read_encoded_z_safe(intermediate, helper_at, max_len=16)
    if got is None:
        raise BuildError("event-safe helper is unterminated")
    old_helper = bytes(got[0])
    if len(old_helper) != 4 or not old_helper.startswith(b"\xE5\x18"):
        raise BuildError(f"event-safe helper is not a four-byte ext3 leaf: {old_helper.hex().upper()}")

    out = bytearray(intermediate)
    allowed: list[tuple[int, int]] = []
    target_text = base.norm_spaces(str(special["ko"]))
    widths = drc.semantic_widths(target_text)
    if max(widths, default=0) > 20:
        raise BuildError(f"{SPECIAL_ADDRESS} target exceeds 20 cells: {widths}")
    phrase_tokens, allocations = base.allocate_phrases(
        out,
        intermediate,
        dictionary,
        tbl,
        marker_code(),
        [target_text],
        allowed,
    )
    new_token = phrase_tokens[target_text]
    out[helper_at : helper_at + 4] = new_token
    allowed.append((helper_at, helper_at + 4))
    update_ws_checksum(out)
    candidate = bytes(out)
    allowed.append((len(candidate) - 2, len(candidate)))

    unexpected = [run for run in base.diff_runs(intermediate, candidate) if not base.covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected special-stage diffs: {unexpected}")
    if candidate[body_at : body_at + 4] != expected_body:
        raise BuildError(f"{SPECIAL_ADDRESS} event-safe record body changed")

    final_dictionary = make_dictionary_ext3(candidate, ext, ext3)
    after_runtime = drc._decode(final_dictionary, expected_body, tbl, target=True).rstrip("　 \t")
    if base.norm_spaces(after_runtime) != target_text:
        raise BuildError(
            f"{SPECIAL_ADDRESS} final runtime render mismatch: {after_runtime!r} != {target_text!r}"
        )
    if int(ws_header(candidate)["checksum"]) != (sum(candidate[:-2]) & 0xFFFF):
        raise BuildError("WonderSwan checksum invalid")
    if MAIN.read_bytes() != parent or MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("main TIP or live SaveRAM changed during final stage")

    OUT_ROM.write_bytes(candidate)
    OUT_SAVE.write_bytes(live_save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage5_lalah_amuro_context_retranslation_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "scope": spec.get("scope"),
        "parent": ident(MAIN, parent),
        "candidate": ident(OUT_ROM, candidate),
        "candidate_save": ident(OUT_SAVE, live_save),
        "checksum": f"{int(ws_header(candidate)['checksum']):04X}",
        "standard_stage": {
            "scenario_spec_rows": int((standard_report.get("counts") or {}).get("spec_scenario_rows", 0)),
            "scenario_rows_changed": int((standard_report.get("counts") or {}).get("scenario_contract_rows_changed", 0)),
            "new_ext3_phrases": int((standard_report.get("counts") or {}).get("new_ext3_phrases", 0)),
            "portal16_helpers_retargeted": int((standard_report.get("counts") or {}).get("portal16_helpers_retargeted", 0)),
            "unexpected_diff_runs": int((standard_report.get("counts") or {}).get("unexpected_diff_runs", -1)),
            "retargets": standard_report.get("scenario_retargets") or [],
        },
        "event_safe_60E087": {
            "address": SPECIAL_ADDRESS,
            "record_body_preserved": candidate[body_at : body_at + 4].hex().upper(),
            "parameter_index": f"{param:02X}",
            "helper_pointer_at": f"{ptr_at:08X}",
            "helper_payload_at": f"{helper_at:08X}",
            "old_helper_token": old_helper.hex().upper(),
            "new_helper_token": new_token.hex().upper(),
            "before": before_runtime,
            "after": after_runtime,
            "after_cells": widths,
            "allocations": allocations,
            "body_occurrences": candidate.count(expected_body),
        },
        "checks": {
            "main_tip_unchanged": MAIN.read_bytes() == parent,
            "main_saveram_unchanged": MAIN_SAVE.read_bytes() == live_save,
            "candidate_saveram_byte_exact": OUT_SAVE.read_bytes() == live_save,
            "event_safe_record_body_byte_exact": candidate[body_at : body_at + 4] == expected_body,
            "event_safe_param_unique": candidate.count(expected_body) == 1,
            "event_safe_runtime_render_exact": base.norm_spaces(after_runtime) == target_text,
            "special_stage_unexpected_diffs_zero": not unexpected,
            "checksum_valid": int(ws_header(candidate)["checksum"]) == (sum(candidate[:-2]) & 0xFFFF),
        },
    }
    if not all(report["checks"].values()):
        raise BuildError(f"final checks failed: {report['checks']}")
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for path in (TMP_ROM, TMP_SAVE, TMP_REPORT):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    print(json.dumps({
        "ok": True,
        "candidate_sha256": report["candidate"]["sha256"],
        "checksum": report["checksum"],
        "scenario_rows_changed": report["standard_stage"]["scenario_rows_changed"],
        "event_safe_60E087": report["event_safe_60E087"],
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
