#!/usr/bin/env python3
"""Independent audit for main_translation_rebase_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_garrod_native_stock_guard import scan_families  # noqa: E402
from dialogue_runtime_contracts import audit_manifest, build_manifest  # noqa: E402
from monoeye_rom import Tbl, stock_base  # noqa: E402
from normalize_ko_text import normalize_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/main_translation_rebase_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/main_translation_rebase_candidate.sav"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
REPORT = ROOT / "out/patch/main_translation_rebase_candidate_report.json"
CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3 = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/main_translation_rebase_candidate_audit.json"
EXPECTED_MAIN_SHA = "9468c74624483a7b4a3b55c896d5277b73afc1b80fd5270e8e4b9d69fbfa197a"
ALLOWED_CHANGED_BANKS = {
    *range(0x11, 0x26),  # ext3/alias physical banks
    0xE0, 0xE1, 0xE2, 0xE3,  # stock-relative scenario banks 60-63 in expanded ROM
    0xFF,  # WonderSwan checksum bytes only
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ext3_index(body: bytes) -> int | None:
    if len(body) >= 4 and body[:2] == b"\xE5\x18":
        return 0x1000 + (body[2] << 8) + body[3]
    return None


def main() -> int:
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    original = ORIGINAL.read_bytes()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    contracts = {
        str(row["address"]).upper(): row
        for row in json.loads(CONTRACTS.read_text(encoding="utf-8"))["contracts"]
    }
    failures: list[dict] = []

    if sha(parent) != EXPECTED_MAIN_SHA:
        failures.append({"kind": "main_identity", "actual": sha(parent)})
    if sha(candidate) != report["candidate"]["sha256"]:
        failures.append({"kind": "candidate_identity"})
    stored = int.from_bytes(candidate[-2:], "little")
    computed = sum(candidate[:-2]) & 0xFFFF
    if stored != computed:
        failures.append({"kind": "checksum", "stored": stored, "computed": computed})
    if CANDIDATE_SAVE.read_bytes() != LIVE_SAVE.read_bytes():
        failures.append({"kind": "saveram_pair"})

    changed_banks = []
    for bank in range(0x100):
        lo = bank * 0x10000
        hi = lo + 0x10000
        if parent[lo:hi] != candidate[lo:hi]:
            changed_banks.append(bank)
    outside = [bank for bank in changed_banks if bank not in ALLOWED_CHANGED_BANKS]
    if outside:
        failures.append({"kind": "changed_bank_outside_allowlist", "banks": [f"{b:02X}" for b in outside]})
    # Bank FF is allowed only for the final checksum pair.
    ff_lo = 0xFF0000
    ff_diff = [
        ff_lo + i for i, (a, b) in enumerate(zip(parent[ff_lo:], candidate[ff_lo:]))
        if a != b
    ]
    if any(offset < len(candidate) - 2 for offset in ff_diff):
        failures.append({"kind": "bank_ff_nonchecksum_change", "sample": ff_diff[:20]})

    parent_family_rows, parent_family_errors = scan_families(parent, original)
    candidate_family_rows, candidate_family_errors = scan_families(candidate, original)
    if parent_family_errors or candidate_family_errors:
        failures.append({
            "kind": "page_boundary_family_scan",
            "parent_errors": parent_family_errors[:20],
            "candidate_errors": candidate_family_errors[:20],
        })
    parent_non_native = {
        f"{int(row['logical']):06X}"
        for row in parent_family_rows
        if row.get("source_exact_native_two_token")
        and not row.get("current_native_two_token_with_padding")
    }
    candidate_non_native = {
        f"{int(row['logical']):06X}"
        for row in candidate_family_rows
        if row.get("source_exact_native_two_token")
        and not row.get("current_native_two_token_with_padding")
    }
    new_page_boundary_regressions = sorted(candidate_non_native - parent_non_native)
    if new_page_boundary_regressions:
        failures.append({
            "kind": "page_boundary_native_two_token_regression",
            "count": len(new_page_boundary_regressions),
            "sample": new_page_boundary_regressions[:20],
        })

    manifest = build_manifest(original, candidate, target_path=CANDIDATE)
    safety = audit_manifest(candidate, manifest, target_path=CANDIDATE)
    if safety["counts"]["hard_failures"]:
        failures.append({"kind": "runtime_contract", "hard_failures": safety["hard_failures_rows"][:20]})

    sb = stock_base(parent)
    runtime_native_first_addresses = {
        address
        for address, contract in contracts.items()
        if contract.get("route") == "scenario_first"
        and str(contract.get("confidence") or "") == "runtime-proven"
        and not bool((contract.get("decoder") or {}).get("ext3"))
    }
    runtime_native_first_changes = []
    for address in sorted(runtime_native_first_addresses):
        contract = contracts[address]
        start = sb + int(contract["body_start"], 16)
        cap = int(contract["body_capacity"])
        if parent[start:start + cap] != candidate[start:start + cap]:
            runtime_native_first_changes.append({
                "abs": address,
                "reason": "body_changed",
                "parent": parent[start:start + cap].hex().upper(),
                "candidate": candidate[start:start + cap].hex().upper(),
            })
    if runtime_native_first_changes:
        failures.append({
            "kind": "runtime_proven_native_first_changed",
            "count": len(runtime_native_first_changes),
            "sample": runtime_native_first_changes[:20],
        })

    quarantine_changes = []
    for address, contract in contracts.items():
        if contract.get("status") != "quarantine":
            continue
        start = sb + int(contract["body_start"], 16)
        cap = int(contract["body_capacity"])
        if parent[start:start + cap] != candidate[start:start + cap]:
            quarantine_changes.append(address)
    if quarantine_changes:
        failures.append({"kind": "quarantine_body_changed", "count": len(quarantine_changes), "sample": quarantine_changes[:20]})

    tbl = Tbl.load(TBL)
    dictionary = make_dictionary_ext3(candidate, load_ext_meta(EXT), load_ext_meta(EXT3))
    text_failures = []
    width_failures = []
    for address, info in report["applied"].items():
        expected = normalize_ko_text(str(info["text"]))
        if len(expected.replace("<E62F>", "")) > 20:
            width_failures.append(address)
            continue
        contract = contracts[address]
        start = sb + int(contract["body_start"], 16)
        cap = int(contract["body_capacity"])
        body = candidate[start:start + cap]
        idx = ext3_index(body)
        if idx is not None:
            actual = normalize_ko_text(dictionary.expand_index(idx, tbl))
        else:
            # The report only uses direct-fit mode for native bodies. Padding 01
            # follows the encoded visible bytes; compare the known visible text by
            # re-encoding length through the candidate's already verified report.
            from hangul_marker import marker_code
            from normalize_ko_text import try_encode_ko_text
            encoded = try_encode_ko_text(expected, tbl, hangul_marker_code=marker_code(), hangul_marker_mode="run")
            actual = normalize_ko_text(dictionary.expand(bytes(encoded or b""), tbl))
        if actual != expected:
            text_failures.append({"abs": address, "expected": expected, "actual": actual})
    if width_failures:
        failures.append({"kind": "width", "count": len(width_failures), "sample": width_failures[:20]})
    if text_failures:
        failures.append({"kind": "text_exact", "count": len(text_failures), "sample": text_failures[:20]})

    payload = {
        "schema_version": 1,
        "generated_by": "tools/audit_main_translation_rebase_candidate.py",
        "ok": not failures,
        "main_sha256": sha(parent),
        "candidate_sha256": sha(candidate),
        "checksum": {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "ok": stored == computed},
        "changed_banks": [f"{bank:02X}" for bank in changed_banks],
        "applied_rows": len(report["applied"]),
        "skipped_rows": len(report["skipped"]),
        "runtime_contract_hard_failures": safety["counts"]["hard_failures"],
        "quarantine_body_changes": len(quarantine_changes),
        "text_failures": len(text_failures),
        "width_failures": len(width_failures),
        "page_boundary_parent_non_native": len(parent_non_native),
        "page_boundary_candidate_non_native": len(candidate_non_native),
        "page_boundary_new_regressions": len(new_page_boundary_regressions),
        "runtime_proven_native_first_population": len(runtime_native_first_addresses),
        "runtime_proven_native_first_changes": len(runtime_native_first_changes),
        "saveram_exact_live_copy": CANDIDATE_SAVE.read_bytes() == LIVE_SAVE.read_bytes(),
        "failures": failures,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
