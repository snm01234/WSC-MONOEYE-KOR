#!/usr/bin/env python3
"""Build the exhaustive safe battle-dialogue false-leading-text cleanup candidate.

Parent: promoted battle-dialogue structure repair TIP.
Scope: the 264 rows classified safe_text_lead by the exhaustive structure
analysis. Those rows were wrongly given their original sentence-initial Japanese
code unit as if it were speaker metadata. The candidate removes only that false
lead, keeps the existing approved Korean E5-18 body token whenever the Korean is
already complete, and uses a new reviewed full-body Korean phrase for the 57
rows whose old remainder translation would otherwise start mid-sentence.

Protected controls, unresolved one-byte rows, terminators, neighbour records,
runtime code, and all non-target battle/system data are write-gated byte-exact.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3  # noqa: E402
from build_remaining_dialogue_candidate import encode_phrase  # noqa: E402
from mixed_residual_classification import is_japanese_character  # noqa: E402
from monoeye_rom import Tbl, load_rom, stock_base, update_ws_checksum  # noqa: E402

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SAFE_CSV = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
AMBIG_CSV = ROOT / "out/script/battle_dialogue_false_lead_ambiguous.csv"
ANALYSIS = PATCH / "battle_dialogue_false_lead_structure_analysis.json"
CATALOG = ROOT / "data/battle_dialogue_false_lead_fullbody_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "battle_dialogue_false_lead_cleanup_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_dialogue_false_lead_cleanup_candidate.sav"
SRAM_SAVE = ROOT / "sram/battle_dialogue_false_lead_cleanup_candidate.sav"
REPORT = PATCH / "battle_dialogue_false_lead_cleanup_report.json"
EXPECTED_TIP = "bac5e179ae496dd2b70912da0b1987b2dc6f7551e9f4d9de2d48c8c2152f7c88"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TARGETS = 264
EXPECTED_OVERRIDES = 57
SCREEN_EXPECTED = {
    "5D0C39": "죽어서……버리는……？",
    "5D11C6": "아、안　돼……！！",
    "5D1449": "함을　가까이　붙여라！",
    "5D5D58": "이　정도의　싸움으로는　만족할　수　없다！！",
    "5EBB7A": "으랴앗！",
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def ident(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def clean(value: str) -> str:
    return value.rstrip("\u3000 \t")


def visible_japanese(value: str) -> bool:
    # Ignore explicit engine control notation used in reviewed strings.
    value = value.replace("<E62F>", "")
    return any(is_japanese_character(ch) for ch in value)


def main() -> int:
    parent = bytes(load_rom(TIP))
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_TIP:
        raise BuildError("main TIP identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")
    analysis = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    if analysis.get("ok") is not True or int((analysis.get("counts") or {}).get("safe_text_lead", -1)) != EXPECTED_TARGETS:
        raise BuildError("structure analysis is not the expected successful 264-target partition")

    with SAFE_CSV.open(encoding="utf-8-sig", newline="") as handle:
        targets = list(csv.DictReader(handle))
    if len(targets) != EXPECTED_TARGETS or any(row.get("final_disposition") != "safe_text_lead" for row in targets):
        raise BuildError("safe-target CSV population drifted")
    with AMBIG_CSV.open(encoding="utf-8-sig", newline="") as handle:
        protected_or_ambiguous = list(csv.DictReader(handle))

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if catalog.get("translation_source") != "llm" or catalog.get("review_status") != "approved":
        raise BuildError("full-body correction catalog is not approved")
    overrides = {str(row["abs"]).upper(): row for row in catalog.get("entries") or []}
    if len(overrides) != EXPECTED_OVERRIDES:
        raise BuildError(f"full-body correction population drifted: {len(overrides)}")
    target_by_abs = {row["abs"]: row for row in targets}
    if not set(overrides) <= set(target_by_abs):
        raise BuildError("full-body correction contains a non-safe target")
    for address, row in overrides.items():
        source = target_by_abs[address]
        if row.get("jp") != source.get("original_full_text"):
            raise BuildError(f"override Japanese binding drifted at {address}")
        if visible_japanese(str(row.get("ko") or "")):
            raise BuildError(f"override still contains Japanese at {address}")

    tbl = Tbl.load(TBL_PATH)
    correction_rows: list[dict[str, Any]] = []
    for address, row in sorted(overrides.items()):
        ko = str(row["ko"])
        correction_rows.append({"abs": address, "ko": ko, "encoded": encode_phrase(ko, tbl)})
    assignments, states = allocate_ext3(parent, correction_rows)

    candidate = bytearray(parent)
    dict_allowed: list[tuple[int, int]] = []
    # Apply only newly allocated five-bank alias pointer/phrase bytes. Reused
    # existing phrases produce no dictionary write.
    for _page, state in states.items():
        start = int(state["start"])
        before_cursor = int(state["cursor_before"])
        after_cursor = int(state["cursor"])
        bank_before = parent[start:start + 0x10000]
        bank_after = bytes(state["bank"])
        for i, (a, b) in enumerate(zip(bank_before, bank_after)):
            if a != b:
                candidate[start + i] = b
                dict_allowed.append((start + i, start + i + 1))
        # cursor values are recorded later for audit/debug; per-byte allowed
        # extents above avoid over-whitelisting untouched dictionary space.
        if after_cursor < before_cursor:
            raise BuildError("five-bank phrase cursor moved backwards")

    sb = stock_base(parent)
    target_ranges: list[tuple[int, int]] = []
    target_reports: list[dict[str, Any]] = []
    for row in targets:
        address = row["abs"]
        logical = int(address, 16)
        current_payload = bytes.fromhex(row["candidate_payload_hex"])
        lead = bytes.fromhex(row["lead_hex"])
        at = sb + logical
        live = parent[at:at + len(current_payload)]
        if live != current_payload:
            raise BuildError(f"promoted TIP no longer matches repaired payload at {address}")
        if parent[at + len(current_payload)] != 0:
            raise BuildError(f"terminator drift at {address}")
        if not live.startswith(lead) or len(live) < len(lead) + 4:
            raise BuildError(f"false lead boundary drift at {address}")
        old_token = live[len(lead):len(lead) + 4]
        if old_token[:2] != b"\xE5\x18":
            raise BuildError(f"translated body is no longer an E5-18 token at {address}")

        if address in overrides:
            ko = str(overrides[address]["ko"])
            token = bytes(assignments[ko]["token"])
            source_kind = "reviewed_full_body_override"
        else:
            ko = str(row["current_korean_body"])
            token = old_token
            source_kind = "existing_approved_korean_body_token"
        if len(token) != 4 or token[:2] != b"\xE5\x18":
            raise BuildError(f"replacement token shape invalid at {address}")
        rebuilt = token + bytes([0x01]) * (len(live) - 4)
        if len(rebuilt) != len(live):
            raise BuildError(f"payload capacity changed at {address}")
        candidate[at:at + len(live)] = rebuilt
        target_ranges.append((at, at + len(live)))
        target_reports.append({
            "abs": address,
            "evidence": row["evidence"],
            "lead_hex_removed": row["lead_hex"],
            "lead_text_removed": row["lead_text"],
            "before_payload_hex": live.hex().upper(),
            "after_payload_hex": rebuilt.hex().upper(),
            "ko": ko,
            "translation_source": source_kind,
        })

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    # Build a dictionary from the completed candidate and verify every target as
    # a whole visible body: no preserved metadata/prefix is allowed here.
    dictionary = make_dictionary_ext3(candidate_bytes, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    target_failures: list[dict[str, str]] = []
    screen_checks: list[dict[str, Any]] = []
    for row in target_reports:
        logical = int(row["abs"], 16)
        before_len = len(bytes.fromhex(row["before_payload_hex"]))
        payload = candidate_bytes[sb + logical:sb + logical + before_len]
        try:
            rendered = clean(dictionary.expand(payload, tbl))
        except Exception as exc:  # noqa: BLE001
            rendered = f"<decode:{type(exc).__name__}>"
        expected = clean(str(row["ko"]))
        ok = rendered == expected and not visible_japanese(rendered) and candidate_bytes[sb + logical + before_len] == 0
        row["candidate_render"] = rendered
        row["render_exact"] = ok
        if not ok:
            target_failures.append({"abs": row["abs"], "expected": expected, "rendered": rendered})
        if row["abs"] in SCREEN_EXPECTED:
            screen_checks.append({
                "abs": row["abs"],
                "expected": SCREEN_EXPECTED[row["abs"]],
                "rendered": rendered,
                "ok": rendered == SCREEN_EXPECTED[row["abs"]],
            })

    if {row["abs"] for row in screen_checks} != set(SCREEN_EXPECTED) or not all(row["ok"] for row in screen_checks):
        raise BuildError(f"screen anchor verification failed: {screen_checks}")

    # Every protected/unresolved record is a hard byte-exact guard. Their
    # dictionary tokens may render via shared dictionaries, but their record
    # bytes/terminators must not move at all.
    ambiguous_failures: list[str] = []
    for row in protected_or_ambiguous:
        logical = int(row["abs"], 16)
        payload = bytes.fromhex(row["candidate_payload_hex"] or row.get("current_payload_hex") or "")
        if not payload:
            # Leakage CSV always carries candidate_payload_hex for previous
            # repaired rows; this is defensive only.
            continue
        at = sb + logical
        if candidate_bytes[at:at + len(payload)] != parent[at:at + len(payload)] or candidate_bytes[at + len(payload)] != parent[at + len(payload)]:
            ambiguous_failures.append(row["abs"])

    # Diff guard: only target payload bytes, newly allocated dictionary bytes,
    # and the WonderSwan footer checksum may differ.
    changed = [i for i, (a, b) in enumerate(zip(parent, candidate_bytes)) if a != b]
    checksum_range = (len(parent) - 2, len(parent))
    unexpected: list[int] = []
    for pos in changed:
        if checksum_range[0] <= pos < checksum_range[1]:
            continue
        if any(lo <= pos < hi for lo, hi in target_ranges):
            continue
        if any(lo <= pos < hi for lo, hi in dict_allowed):
            continue
        unexpected.append(pos)
    if target_failures or ambiguous_failures or unexpected:
        raise BuildError(
            f"static write guard failed target={len(target_failures)} ambiguous={len(ambiguous_failures)} unexpected={len(unexpected)}"
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save)
    atomic_bytes(SRAM_SAVE, save)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_dialogue_false_lead_cleanup_candidate.py",
        "ok": True,
        "promotion_allowed": False,
        "purpose": "remove structurally proven visible Japanese sentence leads accidentally restored as battle speaker metadata",
        "inputs": {
            "main_tip": ident(TIP, parent),
            "structure_analysis": ident(ANALYSIS),
            "safe_targets": ident(SAFE_CSV),
            "protected_ambiguous": ident(AMBIG_CSV),
            "fullbody_catalog": ident(CATALOG),
            "main_saveram": ident(MAIN_SAVE, save),
        },
        "outputs": {
            "candidate_rom": ident(OUT_ROM, candidate_bytes),
            "candidate_saveram": ident(OUT_SAVE, save),
            "sram_mirror": ident(SRAM_SAVE, save),
        },
        "counts": {
            "safe_targets": len(target_reports),
            "fullbody_overrides": len(overrides),
            "existing_body_token_reused": len(target_reports) - len(overrides),
            "protected_or_ambiguous_rows": len(protected_or_ambiguous),
            "new_dictionary_changed_bytes": len({lo for lo, _hi in dict_allowed}),
            "changed_bytes_total": len(changed),
            "unexpected_changed_bytes": len(unexpected),
            "target_failures": len(target_failures),
            "ambiguous_record_failures": len(ambiguous_failures),
        },
        "checks": {
            "safe_target_full_render_exact": not target_failures,
            "safe_target_japanese_zero": all(not visible_japanese(row["candidate_render"]) for row in target_reports),
            "screen_5_exact": all(row["ok"] for row in screen_checks) and len(screen_checks) == 5,
            "protected_and_unresolved_record_bytes_exact": not ambiguous_failures,
            "terminators_exact": all(candidate_bytes[sb + int(row["abs"], 16) + len(bytes.fromhex(row["after_payload_hex"]))] == 0 for row in target_reports),
            "non_target_write_guard_exact": not unexpected,
            "candidate_saveram_exact_main_snapshot": OUT_SAVE.read_bytes() == save == SRAM_SAVE.read_bytes(),
        },
        "screen_checks": screen_checks,
        "checksum": f"{checksum:04X}",
        "targets": target_reports,
        "remaining_gate": "user emulator validation; do not promote to main TIP yet",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "ok": True,
        "candidate": report["outputs"]["candidate_rom"],
        "counts": report["counts"],
        "screen_checks": screen_checks,
        "checksum": report["checksum"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
