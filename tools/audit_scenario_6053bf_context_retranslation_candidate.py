#!/usr/bin/env python3
"""Independent audit for scenario_6053bf_context_retranslation_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from dialogue_runtime_contracts import audit_manifest  # noqa: E402
from monoeye_rom import BANK_SIZE, Tbl, stock_base  # noqa: E402
from normalize_ko_text import normalize_ko_text  # noqa: E402

PARENT = ROOT / "out/patch/main_translation_rebase_maincarry_candidate.wsc"
CANDIDATE = ROOT / "out/patch/scenario_6053bf_context_retranslation_candidate.wsc"
PARENT_SAVE = ROOT / "sram/main_translation_rebase_maincarry_candidate.sav"
CANDIDATE_SAVE = ROOT / "sram/scenario_6053bf_context_retranslation_candidate.sav"
PARENT_CONTRACTS = ROOT / "out/script/main_translation_rebase_candidate_contracts.json"
CANDIDATE_CONTRACTS = ROOT / "out/script/scenario_6053bf_context_retranslation_candidate_contracts.json"
TRANSLATIONS = ROOT / "data/scenario_6053bf_context_retranslation_ko.json"
REPORT = ROOT / "out/patch/scenario_6053bf_context_retranslation_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/scenario_6053bf_context_retranslation_candidate_audit.json"
EXPECTED_PARENT_SHA = "a1386fcf205d6281a3bc63d47ac15098faf824ccc932eb7c7d1794e2f23bd10d"
EXPECTED_CANDIDATE_SHA = "b6192a05fbfc37dc021ff2ccc9f1ee89ee50c0375c6ddfe807edc381f20e0662"
PROTECTED = {"6053BF", "61E234", "62663E", "627FB5"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parent = PARENT.read_bytes()
    candidate = CANDIDATE.read_bytes()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    translations = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))["targets"]
    pdoc = json.loads(PARENT_CONTRACTS.read_text(encoding="utf-8"))
    cdoc = json.loads(CANDIDATE_CONTRACTS.read_text(encoding="utf-8"))
    pc = {str(row["address"]).upper(): row for row in pdoc["contracts"]}
    cc = {str(row["address"]).upper(): row for row in cdoc["contracts"]}
    failures: list[dict[str, object]] = []

    if sha(parent) != EXPECTED_PARENT_SHA:
        failures.append({"kind": "parent_identity", "actual": sha(parent)})
    if sha(candidate) != EXPECTED_CANDIDATE_SHA or report["candidate"]["sha256"] != EXPECTED_CANDIDATE_SHA:
        failures.append({"kind": "candidate_identity", "actual": sha(candidate), "report": report["candidate"]["sha256"]})
    if CANDIDATE_SAVE.read_bytes() != PARENT_SAVE.read_bytes():
        failures.append({"kind": "saveram_not_parent_exact"})

    stored = int.from_bytes(candidate[-2:], "little")
    computed = sum(candidate[:-2]) & 0xFFFF
    if stored != computed:
        failures.append({"kind": "checksum", "stored": stored, "computed": computed})

    manifest_safety = audit_manifest(candidate, cdoc, target_path=CANDIDATE)
    if manifest_safety["counts"]["hard_failures"]:
        failures.append({"kind": "runtime_contract", "sample": manifest_safety["hard_failures_rows"][:20]})

    target_set = set(translations)
    if target_set != set(report["applied"]):
        failures.append({"kind": "target_report_population_mismatch"})
    if len(target_set) != 57:
        failures.append({"kind": "target_count", "actual": len(target_set)})

    # The only runtime-contract body changes must be the 57 focused portal retargets.
    body_changed = set()
    boundary_changed = []
    for address in pc:
        if pc[address].get("baseline_body_hex") != cc[address].get("baseline_body_hex"):
            body_changed.add(address)
        if pc[address].get("baseline_boundary") != cc[address].get("baseline_boundary"):
            boundary_changed.append(address)
    if body_changed != target_set:
        failures.append({
            "kind": "contract_body_population",
            "missing": sorted(target_set - body_changed)[:20],
            "extra": sorted(body_changed - target_set)[:20],
        })
    if boundary_changed:
        failures.append({"kind": "boundary_changed", "count": len(boundary_changed), "sample": boundary_changed[:20]})

    sb = stock_base(parent)
    portal_shape_failures = []
    protected_failures = []
    for address in sorted(target_set):
        info = report["applied"][address]
        before = bytes.fromhex(str(info["old_body_hex"]))
        after = bytes.fromhex(str(info["new_body_hex"]))
        off = int(info["portal_offset"])
        if len(before) != len(after) or before[:off] != after[:off] or before[off+4:] != after[off+4:]:
            portal_shape_failures.append(address)
            continue
        if before[off:off+2] != b"\xE5\x18" or after[off:off+2] != b"\xE5\x18":
            portal_shape_failures.append(address)
        if off == 1 and (before[0] != 0x18 or after[0] != 0x18):
            portal_shape_failures.append(address)
        start = sb + int(pc[address]["body_start"], 16)
        cap = int(pc[address]["body_capacity"])
        if parent[start:start+cap] != before or candidate[start:start+cap] != after:
            portal_shape_failures.append(address)
    if portal_shape_failures:
        failures.append({"kind": "portal_shape", "count": len(set(portal_shape_failures)), "sample": sorted(set(portal_shape_failures))[:20]})

    for address in sorted(PROTECTED):
        start = sb + int(pc[address]["body_start"], 16)
        cap = int(pc[address]["body_capacity"])
        if parent[start:start+cap] != candidate[start:start+cap]:
            protected_failures.append(address)
    if protected_failures:
        failures.append({"kind": "protected_runtime_body_changed", "addresses": protected_failures})

    # Decode all 57 newly allocated phrases independently.
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(candidate, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    text_failures = []
    width_failures = []
    for address, expected in sorted(translations.items()):
        expected = normalize_ko_text(str(expected))
        if len(expected.replace("<E62F>", "")) > 20:
            width_failures.append(address)
        slot = int(str(report["applied"][address]["new_slot"]), 16)
        actual = normalize_ko_text(dictionary.expand_index(slot, tbl))
        if actual != expected:
            text_failures.append({"abs": address, "expected": expected, "actual": actual})
    if text_failures:
        failures.append({"kind": "text_exact", "count": len(text_failures), "sample": text_failures[:20]})
    if width_failures:
        failures.append({"kind": "width", "count": len(width_failures), "sample": width_failures[:20]})

    # ROM diff banks: private ext3 storage, logical bank60/E0 portals, checksum FF.
    changed_banks = []
    for bank in range(0x100):
        lo = bank * BANK_SIZE
        hi = lo + BANK_SIZE
        if parent[lo:hi] != candidate[lo:hi]:
            changed_banks.append(bank)
    allowed = set(range(0x11, 0x26)) | {0xE0, 0xFF}
    outside = [b for b in changed_banks if b not in allowed]
    if outside:
        failures.append({"kind": "changed_bank_outside_allowlist", "banks": [f"{b:02X}" for b in outside]})

    payload = {
        "schema_version": 1,
        "generated_by": "tools/audit_scenario_6053bf_context_retranslation_candidate.py",
        "ok": not failures,
        "parent_sha256": sha(parent),
        "candidate_sha256": sha(candidate),
        "checksum": {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "ok": stored == computed},
        "targets": len(target_set),
        "contract_body_changed": len(body_changed),
        "contract_boundary_changed": len(boundary_changed),
        "protected_runtime_body_changes": len(protected_failures),
        "text_failures": len(text_failures),
        "width_failures": len(width_failures),
        "runtime_contract_hard_failures": manifest_safety["counts"]["hard_failures"],
        "saveram_parent_exact": CANDIDATE_SAVE.read_bytes() == PARENT_SAVE.read_bytes(),
        "changed_banks": [f"{b:02X}" for b in changed_banks],
        "failures": failures,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
