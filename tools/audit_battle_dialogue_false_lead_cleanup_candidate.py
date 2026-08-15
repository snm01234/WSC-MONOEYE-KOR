#!/usr/bin/env python3
"""Independent static audit for battle_dialogue_false_lead_cleanup_candidate.wsc."""
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
from build_encyclopedia_character_five_bank_batch02_candidate import PAGES, inspect_bank, read_phrase  # noqa: E402
from mixed_residual_classification import is_japanese_character  # noqa: E402
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "battle_dialogue_false_lead_cleanup_candidate.wsc"
BUILD = PATCH / "battle_dialogue_false_lead_cleanup_report.json"
SAFE_CSV = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
AMBIG_CSV = ROOT / "out/script/battle_dialogue_false_lead_ambiguous.csv"
CATALOG = ROOT / "data/battle_dialogue_false_lead_fullbody_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT = PATCH / "battle_dialogue_false_lead_cleanup_audit.json"
EXPECTED_PARENT = "bac5e179ae496dd2b70912da0b1987b2dc6f7551e9f4d9de2d48c8c2152f7c88"
ROM_SIZE = 16_777_216
EXPECTED_TARGETS = 264
EXPECTED_OVERRIDES = 57
SCREEN_EXPECTED = {
    "5D0C39": "죽어서……버리는……？",
    "5D11C6": "아、안　돼……！！",
    "5D1449": "함을　가까이　붙여라！",
    "5D5D58": "이　정도의　싸움으로는　만족할　수　없다！！",
    "5EBB7A": "으랴앗！",
}


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def clean(value: str) -> str:
    return value.rstrip("\u3000 \t")


def visible_japanese(value: str) -> bool:
    value = value.replace("<E62F>", "")
    return any(is_japanese_character(ch) for ch in value)


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def main() -> int:
    parent = bytes(load_rom(PARENT))
    candidate = bytes(load_rom(CANDIDATE))
    failures: list[str] = []
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        failures.append("parent identity drift")
    if len(candidate) != ROM_SIZE:
        failures.append("candidate size drift")
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    expected_candidate = str(((build.get("outputs") or {}).get("candidate_rom") or {}).get("sha256") or "")
    if sha(candidate) != expected_candidate:
        failures.append("candidate/build-report identity mismatch")

    with SAFE_CSV.open(encoding="utf-8-sig", newline="") as handle:
        safe = list(csv.DictReader(handle))
    with AMBIG_CSV.open(encoding="utf-8-sig", newline="") as handle:
        ambiguous = list(csv.DictReader(handle))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    overrides = {str(row["abs"]): str(row["ko"]) for row in catalog.get("entries") or []}
    if len(safe) != EXPECTED_TARGETS:
        failures.append(f"safe target count {len(safe)}")
    if len(overrides) != EXPECTED_OVERRIDES:
        failures.append(f"override count {len(overrides)}")

    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(candidate, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(parent)
    target_ranges: list[tuple[int, int]] = []
    target_checks: list[dict[str, Any]] = []
    for row in safe:
        address = row["abs"]
        logical = int(address, 16)
        before = bytes.fromhex(row["candidate_payload_hex"])
        lead = bytes.fromhex(row["lead_hex"])
        at = sb + logical
        parent_payload = parent[at:at + len(before)]
        candidate_payload = candidate[at:at + len(before)]
        expected = clean(overrides.get(address, row["current_korean_body"]))
        try:
            rendered = clean(dictionary.expand(candidate_payload, tbl))
        except Exception as exc:  # noqa: BLE001
            rendered = f"<decode:{type(exc).__name__}>"
        ok = (
            parent_payload == before
            and parent_payload.startswith(lead)
            and not candidate_payload.startswith(lead)
            and candidate_payload[:2] == b"\xE5\x18"
            and len(candidate_payload) == len(parent_payload)
            and candidate[at + len(before)] == parent[at + len(before)] == 0
            and rendered == expected
            and not visible_japanese(rendered)
        )
        target_checks.append({
            "abs": address,
            "lead_hex": row["lead_hex"],
            "expected": expected,
            "rendered": rendered,
            "ok": ok,
        })
        if not ok:
            failures.append(f"target mismatch {address}")
        target_ranges.append((at, at + len(before)))

    ambiguous_failures: list[str] = []
    for row in ambiguous:
        logical = int(row["abs"], 16)
        payload = bytes.fromhex(row.get("candidate_payload_hex") or "")
        if not payload:
            continue
        at = sb + logical
        if candidate[at:at + len(payload) + 1] != parent[at:at + len(payload) + 1]:
            ambiguous_failures.append(row["abs"])
    if ambiguous_failures:
        failures.append(f"protected/unresolved records changed: {len(ambiguous_failures)}")

    # Independent five-bank audit: every parent-used alias pointer and its raw
    # phrase must remain exact. Candidate changes may only occupy slots that were
    # free on the parent and append phrase bytes after the parent cursor.
    five_bank_failures: list[str] = []
    five_bank_new_slots = 0
    five_bank_new_phrase_bytes = 0
    five_bank_ranges: list[tuple[int, int]] = []
    for page in range(PAGES):
        p = inspect_bank(parent, page)
        c = inspect_bank(candidate, page)
        p_bank = bytes(p["bank"])
        c_bank = bytes(c["bank"])
        start = int(p["start"])
        if int(p["start"]) != int(c["start"]):
            five_bank_failures.append(f"page{page}: start drift")
            continue
        for local in sorted(p["used_before"]):
            pp = int.from_bytes(p_bank[local * 2:local * 2 + 2], "little")
            cp = int.from_bytes(c_bank[local * 2:local * 2 + 2], "little")
            if pp != cp or read_phrase(p_bank, pp) != read_phrase(c_bank, cp):
                five_bank_failures.append(f"page{page}: existing slot {local:03X} changed")
        parent_used = set(p["used_before"])
        candidate_used = set(c["used_before"])
        new_slots = sorted(candidate_used - parent_used)
        five_bank_new_slots += len(new_slots)
        for local in new_slots:
            if local not in set(p["free"]):
                five_bank_failures.append(f"page{page}: new slot {local:03X} was not parent-free")
        pcursor = int(p["cursor_before"])
        ccursor = int(c["cursor_before"])
        # inspect_bank's cursor_before is the computed current high-water mark;
        # the candidate is allowed to extend, never overwrite below parent high-water.
        if ccursor < pcursor:
            five_bank_failures.append(f"page{page}: phrase cursor regressed")
        five_bank_new_phrase_bytes += max(0, ccursor - pcursor)
        if ccursor > pcursor:
            five_bank_ranges.append((start + pcursor, start + ccursor))
        for local in new_slots:
            five_bank_ranges.append((start + local * 2, start + local * 2 + 2))
    if five_bank_failures:
        failures.append(f"five-bank alias preservation failures: {len(five_bank_failures)}")

    # Diff-domain audit. Runtime banks 7A/7F and old ext3 banks 11-20 are also
    # asserted independently below.
    allowed = list(target_ranges) + list(five_bank_ranges)
    changed = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    unexpected = [
        i for i in changed
        if not (len(parent) - 2 <= i < len(parent))
        and not any(lo <= i < hi for lo, hi in allowed)
    ]
    if unexpected:
        failures.append(f"unexpected diff bytes: {len(unexpected)} first={unexpected[0]:08X}")

    old_ext3_exact = all(
        candidate[bank * BANK_SIZE:(bank + 1) * BANK_SIZE] == parent[bank * BANK_SIZE:(bank + 1) * BANK_SIZE]
        for bank in range(0x11, 0x21)
    )
    runtime_7a_exact = candidate[0x7A * BANK_SIZE:0x7B * BANK_SIZE] == parent[0x7A * BANK_SIZE:0x7B * BANK_SIZE]
    p7f = parent[0x7F * BANK_SIZE:0x80 * BANK_SIZE]
    c7f = candidate[0x7F * BANK_SIZE:0x80 * BANK_SIZE]
    runtime_7f_exact_except_checksum = c7f[:-2] == p7f[:-2]
    if not old_ext3_exact:
        failures.append("old ext3 banks11-20 changed")
    if not runtime_7a_exact or not runtime_7f_exact_except_checksum:
        failures.append("runtime bank7A/7F changed")
    if not checksum_ok(candidate):
        failures.append("WonderSwan checksum invalid")

    screen_checks = [row for row in target_checks if row["abs"] in SCREEN_EXPECTED]
    screen_exact = len(screen_checks) == 5 and all(row["rendered"] == SCREEN_EXPECTED[row["abs"]] and row["ok"] for row in screen_checks)
    if not screen_exact:
        failures.append("screen anchor set not exact")

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_dialogue_false_lead_cleanup_candidate.py",
        "read_only": True,
        "ok": not failures,
        "failures": failures,
        "parent": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": len(parent), "sha256": sha(parent)},
        "candidate": {"path": "out/patch/battle_dialogue_false_lead_cleanup_candidate.wsc", "size": len(candidate), "sha256": sha(candidate), "checksum": candidate[-2:].hex().upper()},
        "counts": {
            "safe_targets": len(safe),
            "safe_targets_exact": sum(row["ok"] for row in target_checks),
            "fullbody_overrides": len(overrides),
            "protected_or_unresolved_rows": len(ambiguous),
            "protected_or_unresolved_changed": len(ambiguous_failures),
            "five_bank_new_slots": five_bank_new_slots,
            "five_bank_new_phrase_bytes": five_bank_new_phrase_bytes,
            "changed_bytes": len(changed),
            "unexpected_changed_bytes": len(unexpected),
        },
        "checks": {
            "safe_target_render_exact": all(row["ok"] for row in target_checks),
            "safe_target_japanese_zero": all(not visible_japanese(row["rendered"]) for row in target_checks),
            "screen_5_exact": screen_exact,
            "protected_unresolved_record_exact": not ambiguous_failures,
            "five_bank_existing_alias_phrase_exact": not five_bank_failures,
            "diff_domain_exact": not unexpected,
            "old_ext3_11_20_exact": old_ext3_exact,
            "runtime_7a_exact": runtime_7a_exact,
            "runtime_7f_exact_except_checksum": runtime_7f_exact_except_checksum,
            "checksum_exact": checksum_ok(candidate),
        },
        "screen_checks": screen_checks,
        "target_failures": [row for row in target_checks if not row["ok"]],
        "ambiguous_failures": ambiguous_failures,
        "five_bank_failures": five_bank_failures,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": report["ok"], "counts": report["counts"], "checks": report["checks"], "failures": failures[:20]}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
