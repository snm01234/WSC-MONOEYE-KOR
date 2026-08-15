#!/usr/bin/env python3
"""Independent read-only audit for battle_dialogue_structure_repair_candidate."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, le16, load_rom, stock_base  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT = PATCH / "monoeye_ko_expanded.wsc"
SAFE = PATCH / "backup/20260807_123035_pre_residual_voice_ko/runtime_text_id_scenario_voice_proven_candidate.wsc"
CANDIDATE = PATCH / "battle_dialogue_structure_repair_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/battle_dialogue_structure_repair_candidate.sav"
SRAM_SAVE = ROOT / "sram/battle_dialogue_structure_repair_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
INVENTORY = ROOT / "out/script/battle_dialogue_structure_inventory.csv"
BUILD_REPORT = PATCH / "battle_dialogue_structure_repair_report.json"
OUT = PATCH / "battle_dialogue_structure_repair_audit.json"
TBL = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
EXPECTED_PARENT = "0656db10b4146b03fd1d3d38dfaaf9fade33ab71bf9cd1f37a5b76fd27f1f606"
EXPECTED_SAFE = "5919fbf7bb25d692ca0593a63961fe34148a69ba9c0ef7b5a04b153b1c7414c4"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean(value: str) -> str:
    return value.rstrip("\u3000 \t")


def identity(path: Path, data: bytes) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def main() -> int:
    parent = bytes(load_rom(PARENT))
    safe = bytes(load_rom(SAFE))
    candidate = bytes(load_rom(CANDIDATE))
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        failures.append({"kind": "parent_identity"})
    if len(safe) != ROM_SIZE or sha(safe) != EXPECTED_SAFE:
        failures.append({"kind": "safe_identity"})
    if len(candidate) != ROM_SIZE:
        failures.append({"kind": "candidate_size"})
    if sha(candidate) != ((build.get("outputs") or {}).get("candidate_rom") or {}).get("sha256"):
        failures.append({"kind": "candidate_build_binding"})

    tbl = Tbl.load(TBL)
    dictionary = make_dictionary_ext3(candidate, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(candidate)
    with INVENTORY.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    targets = [row for row in rows if row.get("action") == "repair"]
    non_targets = [row for row in rows if row.get("action") != "repair"]

    target_ranges: list[tuple[int, int]] = []
    target_checks: list[dict[str, Any]] = []
    target_by_abs = {row["record_start"]: row for row in targets}
    for row in targets:
        logical = int(row["record_start"], 16)
        before = bytes.fromhex(row["current_payload_hex"])
        after = bytes.fromhex(row["candidate_payload_hex"])
        at = sb + logical
        got = candidate[at:at+len(after)]
        metadata_prefix = bytes.fromhex((row.get("metadata_hex") or "") + (row.get("prefix_hex") or ""))
        body = got[len(metadata_prefix):]
        try:
            render = clean(dictionary.expand(body, tbl))
        except Exception as exc:  # noqa: BLE001
            render = f"<decode:{type(exc).__name__}>"
        check = {
            "abs": row["record_start"],
            "metadata_prefix_hex": metadata_prefix.hex().upper(),
            "candidate_payload_exact": got == after,
            "safe_metadata_prefix_exact": safe[at:at+len(metadata_prefix)] == metadata_prefix,
            "candidate_metadata_prefix_exact": got.startswith(metadata_prefix),
            "body_token_e518": body.startswith(b"\xE5\x18") and len(body) >= 4,
            "render": render,
            "expected_render": clean(row.get("candidate_render") or ""),
            "render_exact": render == clean(row.get("candidate_render") or ""),
            "terminator_exact": candidate[at+len(after)] == parent[at+len(before)] == safe[at+len(before)] == 0,
        }
        check["ok"] = all(
            check[key]
            for key in (
                "candidate_payload_exact", "safe_metadata_prefix_exact",
                "candidate_metadata_prefix_exact", "body_token_e518",
                "render_exact", "terminator_exact",
            )
        )
        target_checks.append(check)
        if not check["ok"]:
            failures.append({"kind": "target", **check})
        target_ranges.append((at, at + len(after)))

    # Non-target inventory records including text-initial exceptions and every
    # quarantined short/fixed row must be byte-exact to the current main TIP.
    non_target_changed: list[str] = []
    for row in non_targets:
        logical = int(row["record_start"], 16)
        payload = bytes.fromhex(row["current_payload_hex"])
        at = sb + logical
        if candidate[at:at+len(payload)+1] != parent[at:at+len(payload)+1]:
            non_target_changed.append(row["record_start"])
            if len(non_target_changed) <= 20:
                failures.append({"kind": "non_target_record_changed", "abs": row["record_start"]})

    changed = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    csum_range = (len(parent)-2, len(parent))
    outside = [
        i for i in changed
        if not (csum_range[0] <= i < csum_range[1])
        and not any(lo <= i < hi for lo, hi in target_ranges)
    ]
    if outside:
        failures.append({"kind": "diff_confinement", "count": len(outside), "sample": [f"{v:08X}" for v in outside[:20]]})

    # Immediate partner lines are protected whenever they are not also a target;
    # target partners are independently checked above.
    partner_failures: list[dict[str, str]] = []
    all_by_abs = {row["record_start"]: row for row in rows}
    for row in targets:
        for key in ("previous_record", "next_record"):
            partner_abs = row.get(key) or ""
            if not partner_abs or partner_abs in target_by_abs:
                continue
            partner = all_by_abs.get(partner_abs)
            if partner is None:
                continue
            logical = int(partner_abs, 16)
            payload = bytes.fromhex(partner["current_payload_hex"])
            at = sb + logical
            if candidate[at:at+len(payload)+1] != parent[at:at+len(payload)+1]:
                partner_failures.append({"target": row["record_start"], "partner": partner_abs})
    if partner_failures:
        failures.append({"kind": "partner_structure", "count": len(partner_failures), "sample": partner_failures[:20]})

    stored = le16(candidate, len(candidate)-2)
    computed = sum(candidate[:-2]) & 0xFFFF
    if stored != computed:
        failures.append({"kind": "checksum", "stored": f"{stored:04X}", "computed": f"{computed:04X}"})

    main_save = MAIN_SAVE.read_bytes()
    out_save = CANDIDATE_SAVE.read_bytes()
    sram_save = SRAM_SAVE.read_bytes()
    save_ok = len(main_save) == len(out_save) == len(sram_save) == SAVE_SIZE and main_save == out_save == sram_save
    if not save_ok:
        failures.append({"kind": "saveram_pair"})

    anchors = []
    for abs_value in ("5E9BDE", "5E9CC4"):
        row = target_by_abs.get(abs_value)
        chk = next((c for c in target_checks if c["abs"] == abs_value), None)
        anchors.append({
            "abs": abs_value,
            "target_present": row is not None,
            "metadata_hex": "" if row is None else row.get("metadata_hex", ""),
            "render_exact": bool(chk and chk["render_exact"]),
            "ok": row is not None and bool(chk and chk["ok"]),
        })
    if not all(row["ok"] for row in anchors):
        failures.append({"kind": "user_anchor", "anchors": anchors})

    checks = {
        "battle_dialogue_target_render_exact": all(row["render_exact"] for row in target_checks),
        "portrait_speaker_metadata_exact": all(row["safe_metadata_prefix_exact"] and row["candidate_metadata_prefix_exact"] for row in target_checks),
        "bundle_partner_structure_exact": not partner_failures,
        "non_target_battle_system_structure_exact": not non_target_changed and not outside,
        "terminator_exact": all(row["terminator_exact"] for row in target_checks),
        "checksum_exact": stored == computed,
        "candidate_saveram_exact": save_ok,
        "main_tip_unchanged": sha(parent) == EXPECTED_PARENT,
    }
    document = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_dialogue_structure_repair_candidate.py",
        "read_only": True,
        "ok": not failures and all(checks.values()),
        "failures": failures,
        "parent": identity(PARENT, parent),
        "safe_structure": identity(SAFE, safe),
        "candidate": {**identity(CANDIDATE, candidate), "checksum": f"{stored:04X}"},
        "saveram": {"sha256": sha(out_save), "size": len(out_save), "main_exact": save_ok, "sram_mirror_exact": save_ok},
        "counts": {
            "inventory_records": len(rows),
            "targets": len(targets),
            "targets_exact": sum(row["ok"] for row in target_checks),
            "non_targets": len(non_targets),
            "non_target_changed": len(non_target_changed),
            "partner_failures": len(partner_failures),
            "changed_bytes": len(changed),
            "outside_allowed_changed_bytes": len(outside),
        },
        "checks": checks,
        "anchors": anchors,
        "target_failures": [row for row in target_checks if not row["ok"]][:50],
        "partner_failures": partner_failures[:50],
    }
    OUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": document["ok"], "checks": checks, "counts": document["counts"], "anchors": anchors}, ensure_ascii=False, indent=2))
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
