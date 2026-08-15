#!/usr/bin/env python3
"""Independent read-only audit of the unsupported-compact3 rollback candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_ui_compact3_rollback_candidate import (
    APPEND_OFFSET,
    TARGETS,
    build_pad_skip_walker,
)
from extract_script import split_prefix_body
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base, token_from_dict_index
from patch_3byte_dict_token import (
    CAVE3,
    LEAF,
    SITE1,
    SITE1_MOVES,
    SITE1_RETURN,
    SITE2_MOVES,
    find_site2,
    sab,
)

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/ui_compact3_rollback_candidate.wsc"
SAVE = ROOT / "sram/ui_compact3_rollback_candidate.sav"
BUILD_REPORT = ROOT / "out/patch/ui_compact3_rollback_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/ui_compact3_rollback_audit.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=PARENT)
    parser.add_argument("--candidate", type=Path, default=CANDIDATE)
    parser.add_argument("--build-report", type=Path, default=BUILD_REPORT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    parent = bytes(load_rom(args.parent))
    candidate = bytes(load_rom(args.candidate))
    build = json.loads(args.build_report.read_text(encoding="utf-8"))
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(candidate)

    failures: list[dict[str, Any]] = []
    candidate_sha = sha256(candidate)
    if candidate_sha != ((build.get("candidate") or {}).get("sha256")):
        failures.append({"kind": "candidate_report_binding"})
    if ext3_meta.get("compact3") is not False:
        failures.append({"kind": "compact3_metadata_not_false"})

    report_rows = {int(row["abs"], 16): row for row in build.get("records") or []}
    target_checks: list[dict[str, Any]] = []
    selected_slots: set[int] = set()
    for logical, (expected, category, dialogue) in TARGETS.items():
        row = report_rows.get(logical)
        if row is None:
            failures.append({"kind": "missing_record", "abs": f"{logical:06X}"})
            continue
        got = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        if got is None:
            failures.append({"kind": "unreadable_record", "abs": f"{logical:06X}"})
            continue
        payload = bytes(got[0])
        if dialogue:
            prefix, body, kind = split_prefix_body(payload)
            if kind != "dialogue":
                prefix, body = b"", payload
        else:
            prefix, body = b"", payload
        index = int(row["slot"], 16)
        selected_slots.add(index)
        expected_body = token_from_dict_index(index) + b"\x01"
        rendered = dictionary.expand(body, tbl)
        check = {
            "abs": f"{logical:06X}",
            "category": category,
            "prefix_hex": prefix.hex().upper(),
            "body_hex": body.hex().upper(),
            "expected_body_hex": expected_body.hex().upper(),
            "contains_e519": b"\xE5\x19" in body,
            "rendered": rendered,
            "expected_static": expected + "　",
            "ok": body == expected_body and b"\xE5\x19" not in body and rendered == expected + "　",
        }
        target_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    general = report_rows[0x75B3CA]
    general_slot = int(general["slot"], 16)
    general_token = token_from_dict_index(general_slot)
    general_word = int.from_bytes(general_token, "big")
    site2, site2_return = find_site2(candidate)
    expected_w1 = build_pad_skip_walker(SITE1_MOVES, SITE1_RETURN, general_word)
    expected_w2 = build_pad_skip_walker(SITE2_MOVES, site2_return, general_word)
    cave_file = sab(candidate, CAVE3)
    actual_w1 = candidate[cave_file + APPEND_OFFSET : cave_file + APPEND_OFFSET + len(expected_w1)]
    actual_w2_start = cave_file + APPEND_OFFSET + len(expected_w1)
    actual_w2 = candidate[actual_w2_start : actual_w2_start + len(expected_w2)]
    walker = {
        "general_slot": f"{general_slot:04X}",
        "general_token_hex": general_token.hex().upper(),
        "walker1_exact": actual_w1 == expected_w1,
        "walker2_exact": actual_w2 == expected_w2,
        "site1_hook_changed_from_parent": candidate[sab(candidate, SITE1):sab(candidate, SITE1)+5] != parent[sab(parent, SITE1):sab(parent, SITE1)+5],
        "site2_hook_changed_from_parent": candidate[sab(candidate, site2):sab(candidate, site2)+5] != parent[sab(parent, site2):sab(parent, site2)+5],
        "leaf_hook_preserved": candidate[sab(candidate, LEAF):sab(candidate, LEAF)+6] == parent[sab(parent, LEAF):sab(parent, LEAF)+6],
    }
    walker["ok"] = all(
        (
            walker["walker1_exact"],
            walker["walker2_exact"],
            walker["site1_hook_changed_from_parent"],
            walker["site2_hook_changed_from_parent"],
            walker["leaf_hook_preserved"],
        )
    )
    if not walker["ok"]:
        failures.append({"kind": "walker_contract", **walker})

    wanted = set(selected_slots)
    current_external = external_occurrence_map(candidate, ext3_aware=True, wanted=wanted)
    current_nested = nested_occurrence_map(dictionary, wanted=wanted, ext3_aware=True)
    consumer_checks: list[dict[str, Any]] = []
    for index in sorted(wanted):
        expected_addresses = {
            logical for logical, row in report_rows.items() if int(row["slot"], 16) == index
        }
        actual_addresses = {
            int(value["record_abs"], 16)
            for value in current_external.get(index, [])
        }
        # The aux scanner does not cover every 5F body; missing scoped addresses
        # are reported, while any additional actual address is a hard failure.
        unexpected = sorted(actual_addresses - expected_addresses)
        nested = sorted(current_nested.get(index) or [])
        check = {
            "index": f"{index:04X}",
            "expected": [f"{value:06X}" for value in sorted(expected_addresses)],
            "actual_scoped": [f"{value:06X}" for value in sorted(actual_addresses)],
            "unexpected": [f"{value:06X}" for value in unexpected],
            "nested_parents": [f"{value:04X}" for value in nested],
            "ok": not unexpected and not nested,
        }
        consumer_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    general_consumers = next(row for row in consumer_checks if row["index"] == f"{general_slot:04X}")
    if general_consumers["actual_scoped"] != ["75B3CA"]:
        failures.append({"kind": "general_token_not_unique", **general_consumers})

    identity = {
        "parent_sha256": sha256(parent),
        "candidate_sha256": candidate_sha,
        "candidate_size": len(candidate),
        "main_tip_unchanged": sha256(parent) == "ec295935607b4843bc654c2709995262bade543d6c0be64556a45b6b240d4833",
        "saveram_policy": "mutable_live_test_data_not_a_gate",
    }
    if identity["candidate_size"] != 16_777_216:
        failures.append({"kind": "size_identity", **identity})
    if not identity["main_tip_unchanged"]:
        failures.append({"kind": "parent_changed", **identity})

    document = {
        "schema_version": 1,
        "generated_by": "tools/audit_ui_compact3_rollback_candidate.py",
        "target": {
            "path": str(args.candidate.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            "size": len(candidate),
            "sha256": candidate_sha,
        },
        "counts": {
            "target_records": len(target_checks),
            "target_exact": sum(1 for row in target_checks if row["ok"]),
            "target_e519_residuals": sum(1 for row in target_checks if row["contains_e519"]),
            "selected_slots": len(selected_slots),
            "consumer_failures": sum(1 for row in consumer_checks if not row["ok"]),
            "failures": len(failures),
        },
        "identity": identity,
        "walker": walker,
        "target_checks": target_checks,
        "consumer_checks": consumer_checks,
        "failures": failures,
        "ok": not failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"counts": document["counts"], "walker": walker, "ok": document["ok"]}, ensure_ascii=False, indent=2))
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
