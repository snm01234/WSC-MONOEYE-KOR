#!/usr/bin/env python3
"""Build a narrow Stage 4 Mian/Brad/Char context retranslation candidate.

The normal scenario rows reuse the proven ext3/control18 portal retarget builder.
The one special 60B400 row is already on the runtime-proven parameterized E51D
native loop, so its 4-byte record is left byte-exact and only its unique helper
leaf is retargeted to a newly allocated private ext3 phrase.
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
SPEC = ROOT / "data/stage4_mian_context_followup_retranslation_ko.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/stage4_mian_context_followup_retranslation_candidate.wsc"
OUT_SAVE = ROOT / "sram/stage4_mian_context_followup_retranslation_candidate.sav"
OUT_REPORT = ROOT / "out/patch/stage4_mian_context_followup_retranslation_report.json"
TMP_ROM = ROOT / "out/patch/.stage4_mian_context_followup_standard.tmp.wsc"
TMP_SAVE = ROOT / "sram/.stage4_mian_context_followup_standard.tmp.sav"
TMP_REPORT = ROOT / "out/patch/.stage4_mian_context_followup_standard.tmp.json"
EXPECTED_PARENT_SHA = "173fd84de45929756e56d84a076e33380abd6540a5f5698235e776384013c5cf"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


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
    if len(special_rows) != 1 or str(special_rows[0].get("address") or "").upper() != "60B400":
        raise BuildError("expected exactly one special 60B400 row")

    # First apply ordinary ext3/control18 scenario retargets.
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
    special = special_rows[0]
    logical = int(str(special["address"]), 16)
    body_at = stock_base(intermediate) + logical + 3  # first-row 17 34 18 prefix
    expected_body = bytes.fromhex(str(special["expected_body_hex"]))
    actual_body = intermediate[body_at : body_at + len(expected_body)]
    if actual_body != expected_body:
        raise BuildError(
            f"60B400 event-safe body drifted: {actual_body.hex().upper()} != {expected_body.hex().upper()}"
        )
    before_runtime = drc._decode(dictionary, expected_body, tbl, target=True).rstrip("　 \t")
    expected_before = base.norm_spaces(str(special["before"]))
    if base.norm_spaces(before_runtime) != expected_before:
        raise BuildError(f"60B400 current runtime text drifted: {before_runtime!r}")

    param = int(str(special["param_index"]), 16)
    if param != expected_body[2]:
        raise BuildError("60B400 parameter index does not match record body")
    seg_base = drc.EVENT_SAFE_NATIVE2_PARAM_SEG << 16
    ptr_at = seg_base + drc.EVENT_SAFE_NATIVE2_PARAM_PTR_TABLE + param * 2
    off = int.from_bytes(intermediate[ptr_at : ptr_at + 2], "little")
    if not drc.EVENT_SAFE_NATIVE2_PARAM_DATA_MIN <= off < drc.EVENT_SAFE_NATIVE2_PARAM_DATA_MAX:
        raise BuildError(f"60B400 helper pointer invalid: {off:04X}")
    helper_at = seg_base + off
    got = read_encoded_z_safe(intermediate, helper_at, max_len=16)
    if got is None:
        raise BuildError("60B400 helper is unterminated")
    old_helper = bytes(got[0])
    if len(old_helper) != 4 or not old_helper.startswith(b"\xE5\x18"):
        raise BuildError(f"60B400 helper is not a four-byte ext3 leaf: {old_helper.hex().upper()}")
    if intermediate.count(expected_body) != 1:
        raise BuildError("E51D5101 is no longer unique in the candidate")

    out = bytearray(intermediate)
    allowed: list[tuple[int, int]] = []
    target_text = base.norm_spaces(str(special["ko"]))
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
        raise BuildError("60B400 event-safe record body changed")
    final_dictionary = make_dictionary_ext3(candidate, ext, ext3)
    after_runtime = drc._decode(final_dictionary, expected_body, tbl, target=True).rstrip("　 \t")
    if base.norm_spaces(after_runtime) != target_text:
        raise BuildError(f"60B400 final runtime render mismatch: {after_runtime!r} != {target_text!r}")
    if int(ws_header(candidate)["checksum"]) != (sum(candidate[:-2]) & 0xFFFF):
        raise BuildError("WonderSwan checksum invalid")
    if MAIN.read_bytes() != parent or MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("main TIP or live SaveRAM changed during final stage")

    OUT_ROM.write_bytes(candidate)
    OUT_SAVE.write_bytes(live_save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage4_mian_context_followup_retranslation_candidate.py",
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
        "event_safe_60B400": {
            "address": "60B400",
            "record_body_preserved": candidate[body_at : body_at + 4].hex().upper(),
            "parameter_index": f"{param:02X}",
            "helper_pointer_at": f"{ptr_at:08X}",
            "helper_payload_at": f"{helper_at:08X}",
            "old_helper_token": old_helper.hex().upper(),
            "new_helper_token": new_token.hex().upper(),
            "before": before_runtime,
            "after": after_runtime,
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
        "event_safe_60B400": report["event_safe_60B400"],
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
