#!/usr/bin/env python3
"""Build the current-main global dialogue boundary/retranslation candidate.

Scope:
- source-boundary retranslation for scenario rows whose current Korean text
  spills into or is clipped against the following physical row;
- current-main bank59 rows that still exceed 20 visible cells after proven
  structural prefix handling;
- one direct scenario zstring (63BE2E) that is present in the ROM but omitted
  from the current runtime-contract inventory.

The build is ROM-only and fail-closed.  It preserves every record length,
terminator, control prefix, NUL run, portrait/event byte, and the live SaveRAM.
Only private ext3 phrase allocations, leaf-token retargets, and the WonderSwan
checksum may change.
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
SPEC = ROOT / "data/global_dialogue_boundary_retranslation_ko.json"
CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/global_dialogue_boundary_retranslation_candidate.wsc"
OUT_SAVE = ROOT / "sram/global_dialogue_boundary_retranslation_candidate.sav"
OUT_REPORT = ROOT / "out/patch/global_dialogue_boundary_retranslation_report.json"

EXPECTED_PARENT_SHA = "f8a9bb8deb7992b11b690e6a8244f6d0a9906745a2b8d28627fdb90ae9b873a5"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
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
    # U+30FB KATAKANA MIDDLE DOT is intentionally used as a name separator in
    # Korean text (e.g. 그레이트・데긴) and is not an untranslated residual.
    return sum(
        ch != "・" and (("\u3040" <= ch <= "\u30ff") or ("\u3400" <= ch <= "\u9fff"))
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


def read_raw_ext3_record(
    parent: bytes,
    dictionary: Any,
    tbl: Tbl,
    address: str,
    *,
    prefix_hex: str = "",
) -> dict[str, Any]:
    logical = int(address, 16)
    sb = stock_base(parent)
    got = read_encoded_z_safe(parent, sb + logical, max_len=256)
    if got is None:
        raise BuildError(f"{address}: raw zstring unreadable")
    payload = bytes(got[0])
    prefix = bytes.fromhex(prefix_hex)
    if prefix and not payload.startswith(prefix):
        raise BuildError(
            f"{address}: required prefix drifted {payload[:len(prefix)].hex().upper()} != {prefix.hex().upper()}"
        )
    token_at = len(prefix)
    if len(payload) < token_at + 4 or payload[token_at : token_at + 2] != b"\xE5\x18":
        raise BuildError(f"{address}: expected ext3 leaf token after prefix")
    token = payload[token_at : token_at + 4]
    try:
        body_text = dictionary.expand(token, tbl).rstrip("　 \t")
        static_text = dictionary.expand(payload, tbl).rstrip("　 \t")
    except Exception as exc:  # noqa: BLE001
        raise BuildError(f"{address}: raw record decode failed: {exc}") from exc
    return {
        "address": address,
        "logical": logical,
        "payload": payload,
        "prefix": prefix,
        "token_at": token_at,
        "old_token": token,
        "body_text": body_text,
        "static_text": static_text,
        "file_at": sb + logical,
    }


def main() -> int:
    parent = MAIN.read_bytes()
    live_save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE:
        raise BuildError(f"unexpected main size: {len(parent)}")
    if sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"main SHA drifted: {sha(parent)}")
    if len(live_save) != SAVE_SIZE:
        raise BuildError(f"unexpected SaveRAM size: {len(live_save)}")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if str(spec.get("parent_sha256") or "").lower() != EXPECTED_PARENT_SHA:
        raise BuildError("spec parent SHA mismatch")

    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))["contracts"]
    by = {str(row.get("address") or "").upper(): row for row in contracts}
    tbl = Tbl.load(TBL_PATH)
    ext = load_ext_meta(EXT_META)
    ext3 = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext, ext3)

    scenario_changed: list[dict[str, Any]] = []
    raw_changed: list[dict[str, Any]] = []
    all_addresses: set[str] = set()

    for item in spec.get("scenario") or []:
        address = str(item["address"]).upper()
        if address in all_addresses:
            raise BuildError(f"duplicate target: {address}")
        all_addresses.add(address)
        after = base.norm_spaces(str(item["ko"]))
        widths = semantic_widths(after)
        if max(widths, default=0) > LINE_LIMIT:
            raise BuildError(f"{address}: scenario translation exceeds {LINE_LIMIT} cells: {widths}")

        if item.get("storage") == "direct_missing_contract":
            if address in by:
                raise BuildError(f"{address}: expected missing contract but contract now exists")
            raw = read_raw_ext3_record(parent, dictionary, tbl, address)
            raw.update(
                {
                    "scope": "scenario_missing_contract",
                    "before": raw["body_text"],
                    "after": after,
                    "after_cells": widths,
                }
            )
            if raw["body_text"] != after:
                raw_changed.append(raw)
            continue

        row = by.get(address)
        if row is None:
            raise BuildError(f"{address}: runtime contract missing")
        if row.get("route") not in {"scenario_first", "scenario_continuation"}:
            raise BuildError(f"{address}: unexpected route {row.get('route')!r}")
        current, old_token, portal, _idx = base.current_row_text(parent, dictionary, tbl, row)
        current = base.norm_spaces(current)
        source_jp = str(row.get("original_japanese") or "")
        if not source_jp:
            raise BuildError(f"{address}: source Japanese missing from runtime contract")
        if current == after:
            continue
        scenario_changed.append(
            {
                "address": address,
                "scope": "scenario_contract",
                "jp": source_jp,
                "before": current,
                "after": after,
                "after_cells": widths,
                "old_token": old_token.hex().upper(),
                "portal_kind": portal.get("kind"),
            }
        )

    for item in spec.get("bank59") or []:
        address = str(item["address"]).upper()
        if address in all_addresses:
            raise BuildError(f"duplicate target: {address}")
        all_addresses.add(address)
        after = base.norm_spaces(str(item["ko"]))
        widths = semantic_widths(after)
        if max(widths, default=0) > LINE_LIMIT:
            raise BuildError(f"{address}: bank59 translation exceeds {LINE_LIMIT} cells: {widths}")
        raw = read_raw_ext3_record(
            parent,
            dictionary,
            tbl,
            address,
            prefix_hex=str(item.get("preserve_prefix_hex") or ""),
        )
        raw.update(
            {
                "scope": "bank59_current_runtime",
                "before": raw["body_text"],
                "after": after,
                "after_cells": widths,
                "structural_prefix_hex": raw["prefix"].hex().upper(),
            }
        )
        if raw["body_text"] != after:
            raw_changed.append(raw)

    phrase_texts = [row["after"] for row in scenario_changed] + [row["after"] for row in raw_changed]
    if not phrase_texts:
        raise BuildError("no text changes selected")

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
    for item in scenario_changed:
        row = by[item["address"]]
        token = phrase_tokens[base.norm_spaces(item["after"])]
        ret = base.retarget_dialogue_row(out, parent, row, token, helper_targets, allowed)
        ret.update(item)
        retargets.append(ret)

    raw_retargets: list[dict[str, Any]] = []
    for item in raw_changed:
        token = phrase_tokens[base.norm_spaces(item["after"])]
        payload = item["payload"]
        token_at = int(item["token_at"])
        new_payload = bytearray(payload)
        new_payload[token_at : token_at + 4] = token
        at = int(item["file_at"])
        if bytes(parent[at : at + len(payload)]) != payload:
            raise BuildError(f"{item['address']}: parent raw record drifted")
        if parent[at + len(payload)] != 0:
            raise BuildError(f"{item['address']}: raw record terminator drifted")
        out[at : at + len(payload)] = new_payload
        allowed.append((at + token_at, at + token_at + 4))
        raw_retargets.append(
            {
                "address": item["address"],
                "scope": item["scope"],
                "before": item["before"],
                "after": item["after"],
                "after_cells": item["after_cells"],
                "static_before": item["static_text"],
                "structural_prefix_hex": item.get("structural_prefix_hex") or item["prefix"].hex().upper(),
                "old_token": item["old_token"].hex().upper(),
                "new_token": token.hex().upper(),
                "payload_length": len(payload),
                "terminator_preserved": True,
            }
        )

    update_ws_checksum(out)
    candidate = bytes(out)
    allowed.append((len(candidate) - 2, len(candidate)))
    cand_dict = make_dictionary_ext3(candidate, ext, ext3)

    for item in scenario_changed:
        row = by[item["address"]]
        token = target_token(candidate, row)
        idx = dict_index_from_ext3_token(*token)
        rendered = cand_dict.expand_index(idx, tbl).rstrip("　 \t")
        want = base.norm_spaces(item["after"])
        if rendered != want:
            raise BuildError(f"{item['address']}: candidate render mismatch {rendered!r} != {want!r}")
        if jchar_count(rendered):
            raise BuildError(f"{item['address']}: Japanese residual remains in candidate phrase")
        if max(semantic_widths(rendered), default=0) > LINE_LIMIT:
            raise BuildError(f"{item['address']}: post-render width overflow")

    for item in raw_changed:
        at = int(item["file_at"])
        got = read_encoded_z_safe(candidate, at, max_len=256)
        if got is None:
            raise BuildError(f"{item['address']}: candidate raw record unreadable")
        payload = bytes(got[0])
        if len(payload) != len(item["payload"]):
            raise BuildError(f"{item['address']}: raw payload length changed")
        prefix = item["prefix"]
        if prefix and not payload.startswith(prefix):
            raise BuildError(f"{item['address']}: structural prefix changed")
        token_at = int(item["token_at"])
        token = payload[token_at : token_at + 4]
        idx = dict_index_from_ext3_token(*token)
        rendered = cand_dict.expand_index(idx, tbl).rstrip("　 \t")
        want = base.norm_spaces(item["after"])
        if rendered != want:
            raise BuildError(f"{item['address']}: raw candidate render mismatch {rendered!r} != {want!r}")
        if jchar_count(rendered):
            raise BuildError(f"{item['address']}: Japanese residual remains in raw candidate phrase")
        if max(semantic_widths(rendered), default=0) > LINE_LIMIT:
            raise BuildError(f"{item['address']}: raw post-render width overflow")

    runs = base.diff_runs(parent, candidate)
    unexpected = [run for run in runs if not base.covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected parent->candidate diffs: {unexpected}")
    if MAIN.read_bytes() != parent:
        raise BuildError("main TIP changed during candidate build")
    if MAIN_SAVE.read_bytes() != live_save:
        raise BuildError("live SaveRAM changed during candidate build")
    if int(ws_header(candidate)["checksum"]) != (sum(candidate[:-2]) & 0xFFFF):
        raise BuildError("WonderSwan checksum invalid")

    OUT_ROM.write_bytes(candidate)
    OUT_SAVE.write_bytes(live_save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_global_dialogue_boundary_retranslation_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "parent": identity(MAIN, parent),
        "output": {
            "rom": identity(OUT_ROM, candidate),
            "save": identity(OUT_SAVE, live_save),
        },
        "checksum": f"{int(ws_header(candidate)['checksum']):04X}",
        "counts": {
            "spec_scenario_rows": len(spec.get("scenario") or []),
            "scenario_contract_rows_changed": len(scenario_changed),
            "raw_rows_changed": len(raw_changed),
            "bank59_rows_changed": sum(row["scope"] == "bank59_current_runtime" for row in raw_changed),
            "missing_contract_rows_changed": sum(row["scope"] == "scenario_missing_contract" for row in raw_changed),
            "new_ext3_phrases": len(allocations),
            "portal16_helpers_retargeted": len(helper_targets),
            "unexpected_diff_runs": 0,
        },
        "scenario_retargets": retargets,
        "raw_retargets": raw_retargets,
        "ext3_allocations": allocations,
        "storage_policy": {
            "main_tip_untouched": True,
            "main_saveram_untouched": True,
            "candidate_saveram_is_byte_exact_copy": True,
            "record_lengths_changed": False,
            "terminators_changed": False,
            "nul_runs_changed": False,
            "portrait_or_event_metadata_changed": False,
            "raw_structural_prefixes_preserved": True,
            "new_phrases_use_previously_unobserved_ext3_slots": True,
        },
        "checks": {
            "all_changed_phrases_at_or_below_20_cells": True,
            "all_changed_phrases_free_of_japanese": True,
            "main_tip_unchanged": True,
            "main_saveram_unchanged": True,
            "checksum_valid": True,
            "unexpected_diff_runs": [],
        },
        "diff": {
            "runs": [[f"{a:08X}", f"{b:08X}"] for a, b in runs],
            "bytes": sum(b - a for a, b in runs),
        },
        "promotion": "blocked pending candidate audits",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "candidate": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"),
                "candidate_sha256": sha(candidate),
                "scenario_changed": len(scenario_changed),
                "raw_changed": len(raw_changed),
                "new_ext3_phrases": len(allocations),
                "diff_runs": len(runs),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
