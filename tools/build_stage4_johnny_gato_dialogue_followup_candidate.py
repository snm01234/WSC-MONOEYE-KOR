#!/usr/bin/env python3
"""Build the Stage 4 Johnny Ridden / Char / Gato dialogue follow-up candidate.

The parent is always the currently promoted main TIP.  This narrow build:
- corrects source-proven mistranslations and broken two-row continuations in the
  Stage 4 red-comet / crimson-lightning conversation;
- restores one real Japanese text record at 60A0BD that the legacy extractor
  skipped because literal glyph bytes 17 19 (がん) were mistaken for control;
- preserves record boundaries, portrait/event metadata, terminators and NUL runs;
- allocates new private ext3 phrases and retargets only the proven leaf token or
  portal16 helper for each contracted row.

It never modifies the main TIP.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_stage3_reflow_idhelp_followup_candidate as base  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from dialogue_runtime_contracts import semantic_widths  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    dict_index_from_ext3_token,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
    ws_header,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC = ROOT / "data/stage4_johnny_gato_dialogue_followup_ko.json"
CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/stage4_johnny_gato_dialogue_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/stage4_johnny_gato_dialogue_followup_candidate.sav"
OUT_REPORT = ROOT / "out/patch/stage4_johnny_gato_dialogue_followup_report.json"

EXPECTED_PARENT_SHA = "8cdc239822b82db874eeefccfd7aebeef67ae318b2ce32d1b1d69d6cb8c02a2c"
ROM_SIZE = 16_777_216
LINE_LIMIT = 20


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "size": len(payload),
        "sha256": sha(payload),
    }


def jchar_count(text: str) -> int:
    return sum(
        ("\u3040" <= ch <= "\u30ff") or ("\u3400" <= ch <= "\u9fff")
        for ch in text
    )


def target_token(candidate: bytes, row: dict[str, Any]) -> bytes:
    portal = (row.get("baseline_portals") or [])[0]
    kind = portal.get("kind")
    if kind == "control18_portal16":
        hi = int(portal["helper_index"])
        at = (
            (base.PORTAL16_BANK << 16)
            + base.PORTAL16_HELPER_BASE
            + hi * base.PORTAL16_HELPER_STRIDE
        )
        return candidate[at : at + 4]
    if kind == "ext3":
        body_start = int(str(row.get("body_start") or row["address"]), 16)
        at = stock_base(candidate) + body_start
        return candidate[at : at + 4]
    raise BuildError(f"{row.get('address')}: unsupported portal kind {kind!r}")


def main() -> int:
    parent = MAIN.read_bytes()
    live_save = MAIN_SAVE.read_bytes()
    if len(live_save) != 32_768:
        raise BuildError(f"unexpected main SaveRAM size: {len(live_save)}")
    if len(parent) != ROM_SIZE:
        raise BuildError(f"unexpected main size: {len(parent)}")
    if sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"main SHA drifted: {sha(parent)}")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("parent_main_sha256") != EXPECTED_PARENT_SHA:
        raise BuildError("spec parent SHA mismatch")
    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))["contracts"]
    by = {str(row.get("address") or "").upper(): row for row in contracts}

    tbl = Tbl.load(TBL_PATH)
    ext = load_ext_meta(EXT_META)
    ext3 = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext, ext3)

    changed: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    for item in spec["targets"]:
        address = str(item["abs"]).upper()
        row = by.get(address)
        if row is None:
            raise BuildError(f"missing runtime contract: {address}")
        current, old_token, portal, _idx = base.current_row_text(parent, dictionary, tbl, row)
        if current != item["before"]:
            raise BuildError(f"{address}: current text drifted {current!r} != {item['before']!r}")
        source_jp = str(row.get("original_japanese") or "")
        if source_jp != item["jp"]:
            raise BuildError(f"{address}: source JP drifted {source_jp!r} != {item['jp']!r}")
        widths = semantic_widths(item["after"])
        if max(widths, default=0) > LINE_LIMIT:
            raise BuildError(f"{address}: after text exceeds {LINE_LIMIT} cells: {widths}")
        result = {
            "address": address,
            "jp": item["jp"],
            "before": item["before"],
            "after": item["after"],
            "after_cells": widths,
            "reason": item["reason"],
            "old_token": old_token.hex().upper(),
            "portal_kind": portal.get("kind"),
        }
        if item["before"] == item["after"]:
            anchors.append(result)
        else:
            changed.append(result)

    gap = spec["untracked_text_record"]
    gap_addr = int(str(gap["abs"]), 16)
    sb = stock_base(parent)
    gap_read = read_encoded_z_safe(parent, sb + gap_addr, max_len=64)
    if gap_read is None:
        raise BuildError("untracked 60A0BD record is unreadable")
    gap_payload = bytes(gap_read[0])
    expected_gap = bytes.fromhex(gap["source_payload_hex"])
    if gap_payload != expected_gap:
        raise BuildError(f"60A0BD source payload drifted: {gap_payload.hex().upper()}")
    gap_jp = dictionary.expand(gap_payload, tbl).rstrip("　 \t")
    if gap_jp != gap["jp"]:
        raise BuildError(f"60A0BD source decode drifted: {gap_jp!r}")
    if str(gap["abs"]).upper() in by:
        raise BuildError("60A0BD unexpectedly acquired a runtime contract; re-review structure")
    gap_widths = semantic_widths(gap["after"])
    if max(gap_widths, default=0) > LINE_LIMIT:
        raise BuildError("60A0BD translation exceeds 20 cells")

    phrase_texts = [row["after"] for row in changed] + [gap["after"]]
    out = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    phrase_tokens, allocations = base.allocate_phrases(
        out,
        parent,
        dictionary,
        tbl,
        marker_code(),
        phrase_texts,
        allowed,
    )

    helper_targets: dict[int, bytes] = {}
    retargets: list[dict[str, Any]] = []
    for item in changed:
        address = item["address"]
        row = by[address]
        token = phrase_tokens[base.norm_spaces(item["after"])]
        ret = base.retarget_dialogue_row(out, parent, row, token, helper_targets, allowed)
        ret.update(item)
        retargets.append(ret)

    # 60A0BD is a real text zstring omitted by the old extractor.  Replace its
    # entire payload size-preservingly with a private ext3 token + 01 padding.
    gap_token = phrase_tokens[base.norm_spaces(gap["after"])]
    if len(gap_payload) < len(gap_token):
        raise BuildError("60A0BD payload cannot hold ext3 token")
    gap_new = gap_token + b"\x01" * (len(gap_payload) - len(gap_token))
    gap_at = sb + gap_addr
    if bytes(parent[gap_at : gap_at + len(gap_payload)]) != gap_payload:
        raise BuildError("60A0BD parent bytes drifted")
    if parent[gap_at + len(gap_payload)] != 0:
        raise BuildError("60A0BD terminator drifted")
    out[gap_at : gap_at + len(gap_payload)] = gap_new
    allowed.append((gap_at, gap_at + len(gap_payload)))

    update_ws_checksum(out)
    candidate = bytes(out)
    allowed.append((len(candidate) - 2, len(candidate)))

    cand_dict = make_dictionary_ext3(candidate, ext, ext3)
    post_rows: list[dict[str, Any]] = []
    for item in changed:
        row = by[item["address"]]
        token = target_token(candidate, row)
        idx = dict_index_from_ext3_token(*token)
        rendered = cand_dict.expand_index(idx, tbl).rstrip("　 \t")
        want = base.norm_spaces(item["after"])
        if rendered != want:
            raise BuildError(f"{item['address']}: candidate render mismatch {rendered!r} != {want!r}")
        if jchar_count(rendered):
            raise BuildError(f"{item['address']}: Japanese residual in candidate render")
        post_rows.append({"address": item["address"], "rendered": rendered, "cells": semantic_widths(rendered)})

    gap_post = read_encoded_z_safe(candidate, sb + gap_addr, max_len=64)
    if gap_post is None:
        raise BuildError("60A0BD candidate record unreadable")
    if len(gap_post[0]) != len(gap_payload):
        raise BuildError("60A0BD payload length changed")
    gap_rendered = cand_dict.expand(bytes(gap_post[0]), tbl).rstrip("　 \t")
    if gap_rendered != base.norm_spaces(gap["after"]):
        raise BuildError(f"60A0BD candidate render mismatch: {gap_rendered!r}")
    if jchar_count(gap_rendered):
        raise BuildError("60A0BD still contains Japanese")

    # Anchors must remain byte/render-identical.
    for item in anchors:
        current, _tok, _portal, _idx = base.current_row_text(candidate, cand_dict, tbl, by[item["address"]])
        if current != item["after"]:
            raise BuildError(f"anchor changed unexpectedly at {item['address']}")

    runs = base.diff_runs(parent, candidate)
    unexpected = [run for run in runs if not base.covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected parent->candidate diffs: {unexpected}")
    if MAIN.read_bytes() != parent:
        raise BuildError("main TIP changed during candidate build")
    if MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("main SaveRAM changed during candidate build")
    if int(ws_header(candidate)["checksum"]) != (sum(candidate[:-2]) & 0xFFFF):
        raise BuildError("WonderSwan checksum invalid")

    OUT_ROM.write_bytes(candidate)
    OUT_SAVE.write_bytes(live_save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage4_johnny_gato_dialogue_followup_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "parent": identity(MAIN, parent),
        "output": {
            "rom": identity(OUT_ROM, candidate),
            "save": identity(OUT_SAVE, live_save),
        },
        "checksum": f"{int(ws_header(candidate)['checksum']):04X}",
        "counts": {
            "contract_rows_changed": len(changed),
            "context_anchors_unchanged": len(anchors),
            "legacy_extractor_gap_records_fixed": 1,
            "new_ext3_phrases": len(allocations),
            "portal16_helpers_retargeted": len(helper_targets),
            "unexpected_diff_runs": 0,
        },
        "changed_rows": retargets,
        "context_anchors": anchors,
        "gap_record": {
            "address": str(gap["abs"]).upper(),
            "jp": gap["jp"],
            "before_payload": gap_payload.hex().upper(),
            "after_payload": gap_new.hex().upper(),
            "after": gap_rendered,
            "after_cells": gap_widths,
            "terminator_preserved": True,
            "payload_length_preserved": True,
            "classification": "literal_17_19_text_false_lead_skipped_by_legacy_extractor",
        },
        "post_render": post_rows,
        "ext3_allocations": allocations,
        "storage_policy": {
            "main_tip_untouched": True,
            "main_saveram_untouched": True,
            "candidate_saveram_is_byte_exact_copy": True,
            "record_lengths_changed": False,
            "terminators_changed": False,
            "nul_runs_changed": False,
            "portrait_or_event_metadata_changed": False,
            "contracted_rows_use_new_private_ext3_phrases": True,
            "untracked_60A0BD_rewrite_is_size_preserving": True,
        },
        "checks": {
            "all_changed_rows_at_or_below_20_cells": True,
            "all_target_renders_free_of_japanese": True,
            "source_japanese_matches_runtime_contract": True,
            "new_phrase_tokens_previously_unobserved": True,
            "main_tip_unchanged": True,
            "main_saveram_unchanged": True,
            "checksum_valid": True,
            "unexpected_diff_runs": [],
        },
        "diff": {
            "runs": [[f"{a:08X}", f"{b:08X}"] for a, b in runs],
            "bytes": sum(b - a for a, b in runs),
        },
        "promotion": "blocked pending representative runtime validation",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "candidate": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
        "candidate_sha256": sha(candidate),
        "changed_rows": len(changed),
        "gap_records_fixed": 1,
        "new_ext3_phrases": len(allocations),
        "diff_runs": len(runs),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
