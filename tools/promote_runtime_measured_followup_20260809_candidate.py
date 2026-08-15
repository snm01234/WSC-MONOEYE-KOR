#!/usr/bin/env python3
"""Promote the 2026-08-09 runtime-measured structural follow-up candidate.

ROM-only transaction. The current main ROM and live SaveRAM are backed up,
the exact audited candidate replaces only the main ROM, live SaveRAM is kept
byte-exact, and focused structural proofs are rerun on the promoted main.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3
from audit_garrod_native_stock_guard import build_report as build_garrod_guard
from check_runtime_measured_followup_20260809_candidate import check_false_leads, check_false_segptr
from extract_script import split_prefix_body
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base
from scan_script_record_structure import scan as scan_script_structure

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "runtime_measured_followup_20260809_candidate.wsc"
CAND_SAVE = ROOT / "sram/runtime_measured_followup_20260809_candidate.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
SPEC = ROOT / "data/runtime_measured_followup_20260809_ko.json"
BUILD_REPORT = PATCH / "runtime_measured_followup_20260809_candidate_report.json"
STRUCTURAL_AUDIT = PATCH / "runtime_measured_followup_20260809_structural_audit.json"
APPROVAL = PATCH / "runtime_measured_followup_20260809_user_validation.json"
PROMOTION = PATCH / "runtime_measured_followup_20260809_promotion_report.json"

EXPECTED_MAIN = "fb6629d89cfd0dd8f48a621af5c0175d4602f113abec5d781b54b32b14efa86d"
EXPECTED_CAND = "f11b11c05e94e6b3007061e00586bbf74424be1e36c07571f43cb6a4584bafaf"
EXPECTED_CHECKSUM = 0x2147
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXT_META = {
    "stock_count": 3831,
    "slot_count": 265,
    "ext_ptr_off": "0000",
    "ext_seg": "10",
    "ext_in_expansion": True,
}
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}
OITA_EXCLUDED = (0x63CF7C, 0x63CF8A)
EXACT_NATIVE_TWO_TOKEN = {0x63E6E4, 0x63EB4A, 0x63F0BD, 0x63F483, 0x63F67C}
SPEAKER_SEQUENCE = (0x622832, 0x622848, 0x622850)


def digest_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def digest_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": digest_path(path),
    }


def req(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def checksum(path: Path) -> int:
    data = path.read_bytes()
    req(len(data) == ROM_SIZE, f"wrong ROM size: {path}")
    expected = sum(data[:-2]) & 0xFFFF
    actual = int.from_bytes(data[-2:], "little")
    req(expected == actual, f"checksum invalid: {path} expected={expected:04X} actual={actual:04X}")
    return actual


def atomic_json(path: Path, doc: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    req(got is not None, f"unreadable zstring {logical:06X}")
    assert got is not None
    return bytes(got[0]), int(got[1] - sb)


def strip_pad(text: str) -> str:
    return text.rstrip("　 \t")


def has_linguistic_japanese(text: str) -> bool:
    return any(
        ("\u3041" <= ch <= "\u3096")
        or ("\u30a1" <= ch <= "\u30fa")
        or ("\u3400" <= ch <= "\u4dbf")
        or ("\u4e00" <= ch <= "\u9fff")
        for ch in text
    )


def direct_target_proof(promoted: bytes, parent: bytes, original: bytes) -> dict[str, Any]:
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(promoted, EXT_META, EXT3_META)
    source_dictionary = Dictionary(original)
    spec = load_json(SPEC)
    rows = []
    for row in spec.get("entries") or []:
        logical = int(str(row["abs"]), 16)
        payload, term = payload_at(promoted, logical)
        parent_payload, parent_term = payload_at(parent, logical)
        source_payload, source_term = payload_at(original, logical)
        source_prefix, source_body, kind = split_prefix_body(source_payload)
        req(kind == "dialogue", f"source kind drift {logical:06X}: {kind}")
        req(payload.startswith(source_prefix), f"prefix drift {logical:06X}")
        req(len(payload) == len(parent_payload), f"payload extent drift {logical:06X}")
        req(term == parent_term == source_term, f"terminator drift {logical:06X}")
        body = payload[len(source_prefix):]
        rendered = strip_pad(dictionary.expand(body, tbl))
        expected = str(row["ko"])
        req(rendered == expected, f"render mismatch {logical:06X}: {rendered!r} != {expected!r}")
        req(not has_linguistic_japanese(rendered), f"Japanese residual {logical:06X}: {rendered!r}")
        req(b"\xE5\x18" not in body, f"E5 18 remained in focused native-only body {logical:06X}")
        if logical in EXACT_NATIVE_TWO_TOKEN:
            req(
                len(body) == 4
                and 0xF0 <= body[0] <= 0xFE
                and 0xF0 <= body[2] <= 0xFE
                and len(source_body) == 4
                and 0xF0 <= source_body[0] <= 0xFE
                and 0xF0 <= source_body[2] <= 0xFE,
                f"exact native-two-token grammar not restored {logical:06X}",
            )
        source_text = strip_pad(source_dictionary.expand(source_body, tbl))
        rows.append({
            "abs": f"{logical:06X}",
            "prefix_hex": source_prefix.hex().upper(),
            "terminator": f"{term:06X}",
            "rendered": rendered,
            "source": source_text,
            "native_two_token_restored": logical not in EXACT_NATIVE_TWO_TOKEN or len(body) == 4,
        })

    excluded = []
    for logical in OITA_EXCLUDED:
        before, before_term = payload_at(parent, logical)
        after, after_term = payload_at(promoted, logical)
        req(before == after and before_term == after_term, f"excluded Oita record changed {logical:06X}")
        excluded.append({"abs": f"{logical:06X}", "byte_exact": True, "terminator": f"{after_term:06X}"})

    p_before, p_term = payload_at(parent, 0x6226BE)
    p_after, c_term = payload_at(promoted, 0x6226BE)
    psb = stock_base(parent)
    csb = stock_base(promoted)
    req(p_after.startswith(b"\x18"), "6226BE prefix drift")
    req(c_term == p_term, "6226BE terminator drift")
    req(
        parent[psb + p_term:psb + p_term + 12] == promoted[csb + c_term:csb + c_term + 12],
        "6226BE following control bytes changed",
    )
    req(b"\xE5\x18" not in p_after[1:], "6226BE ext3 body reintroduced")

    speaker = []
    for logical in SPEAKER_SEQUENCE:
        src, _ = payload_at(original, logical)
        cur, term = payload_at(promoted, logical)
        prefix, _body, _kind = split_prefix_body(src)
        req(cur.startswith(prefix), f"speaker prefix drift {logical:06X}")
        speaker.append({"abs": f"{logical:06X}", "prefix_hex": prefix.hex().upper(), "terminator": f"{term:06X}"})
    req([r["prefix_hex"] for r in speaker] == ["173418", "173418", ""], "Domon/Touhou speaker sequence drift")

    return {
        "targets": rows,
        "oita_excluded": excluded,
        "portrait_6226BE": {
            "prefix": "18",
            "terminator": f"{c_term:06X}",
            "next_12_bytes_byte_exact": True,
            "ext3_removed_from_body": True,
        },
        "speaker_sequence": speaker,
    }


def speaker_collision_proof(promoted: bytes, original: bytes) -> dict[str, Any]:
    sb = stock_base(promoted)
    collisions = 0
    hidden = 0
    mismatches: list[str] = []
    for logical in range(0x600000, 0x640000 - 4):
        if original[logical] != 0x08 or not (0xF0 <= original[logical + 1] <= 0xFF) or original[logical + 2] != 0:
            continue
        collisions += 1
        next_logical = logical + 3
        source = read_encoded_z_safe(original, next_logical, max_len=256)
        current = read_encoded_z_safe(promoted, sb + next_logical, max_len=256)
        if source is None or current is None:
            continue
        source_payload, source_term = bytes(source[0]), int(source[1])
        current_payload, current_term = bytes(current[0]), int(current[1] - sb)
        source_prefix, source_body, kind = split_prefix_body(source_payload)
        if kind != "dialogue" or not source_body:
            continue
        hidden += 1
        current_prefix, _current_body, _kind = split_prefix_body(current_payload)
        if current_prefix != source_prefix or current_term != source_term:
            mismatches.append(f"{next_logical:06X}")
    req(not mismatches, f"speaker dictionary-lead collision structure mismatch: {mismatches[:10]}")
    return {"speaker_collisions": collisions, "hidden_dialogues": hidden, "structural_mismatches": len(mismatches)}


def main() -> int:
    for path in (TIP, SAVE, CAND, CAND_SAVE, ORIGINAL, SPEC, BUILD_REPORT, STRUCTURAL_AUDIT, APPROVAL):
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and digest_path(TIP) == EXPECTED_MAIN, "main TIP identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and digest_path(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE and CAND_SAVE.stat().st_size == SAVE_SIZE, "SaveRAM size drift")
    req(SAVE.read_bytes() == CAND_SAVE.read_bytes(), "candidate SaveRAM no longer mirrors live SaveRAM")
    req(checksum(CAND) == EXPECTED_CHECKSUM, "candidate checksum drifted")

    build = load_json(BUILD_REPORT)
    audit = load_json(STRUCTURAL_AUDIT)
    approval = load_json(APPROVAL)
    req(str((build.get("parent") or {}).get("sha256") or "").lower() == EXPECTED_MAIN, "build parent mismatch")
    req(str((build.get("candidate") or {}).get("sha256") or "").lower() == EXPECTED_CAND, "build candidate mismatch")
    req(audit.get("status") == "ready_for_promotion", "structural audit not ready")
    req(str(((audit.get("candidate") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "structural audit candidate mismatch")
    req(approval.get("approved") is True and approval.get("promotion_authorized") is True, "promotion authorization missing")
    req(str(approval.get("main_tip_sha256") or "").lower() == EXPECTED_MAIN, "approval main mismatch")
    req(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "approval candidate mismatch")

    parent = TIP.read_bytes()
    candidate = CAND.read_bytes()
    original = ORIGINAL.read_bytes()
    pre_direct = direct_target_proof(candidate, parent, original)
    pre_leads = check_false_leads(candidate)
    req(pre_leads.get("reintroduced") == 0, "candidate false-visible-lead regression")
    pre_segptr = check_false_segptr(original, candidate)
    req(pre_segptr.get("sites_found") == 0, "candidate false segmented pointer regression")
    pre_garrod = build_garrod_guard(CAND, ORIGINAL, expected_target_sha=None)
    req(pre_garrod.get("status") == "pass", "candidate Garrod/native-stock structural guard failed")
    gc = pre_garrod.get("counts") or {}
    req(int(gc.get("source_exact_native_two_token_current_non_native", -1)) == 0, "exact native-two-token risk remains")
    req(int(gc.get("current_ext3_source_mixed_grammar", -1)) == 18, "mixed review-only population drift")
    pre_structure = scan_script_structure(ORIGINAL, CAND, 0x600000, 0x69FFFF)
    parent_structure = scan_script_structure(ORIGINAL, TIP, 0x600000, 0x69FFFF)
    req(pre_structure.get("by_kind") == parent_structure.get("by_kind"), "candidate introduced script-structure issue type")
    req(pre_structure.get("issues") == parent_structure.get("issues") == 1, "script structure issue population drift")
    req(
        [(x.get("abs"), x.get("kind"), x.get("delta")) for x in pre_structure.get("first_issues") or []]
        == [(x.get("abs"), x.get("kind"), x.get("delta")) for x in parent_structure.get("first_issues") or []],
        "candidate script structure issue set differs from parent",
    )
    pre_speaker = speaker_collision_proof(candidate, original)

    save_before = SAVE.read_bytes()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_runtime_measured_followup_structural"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    backup_save = backup_dir / SAVE.name
    shutil.copy2(TIP, backup_rom)
    shutil.copy2(SAVE, backup_save)
    req(digest_path(backup_rom) == EXPECTED_MAIN, "rollback ROM backup verification failed")
    req(backup_save.read_bytes() == save_before, "rollback SaveRAM backup verification failed")
    atomic_json(backup_dir / "backup_manifest.json", {
        "schema_version": 1,
        "generated_by": "tools/promote_runtime_measured_followup_20260809_candidate.py",
        "reason": "pre_runtime_measured_followup_structural",
        "main_rom": ident(backup_rom),
        "live_saveram": ident(backup_save),
        "candidate_sha256": EXPECTED_CAND,
        "user_validation": ident(APPROVAL),
        "structural_audit": ident(STRUCTURAL_AUDIT),
    })

    staged = TIP.with_name(f".{TIP.name}.runtime_followup.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(digest_path(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(digest_path(TIP) == EXPECTED_CAND, "promoted main SHA mismatch")
        req(checksum(TIP) == EXPECTED_CHECKSUM, "promoted main checksum mismatch")
        req(SAVE.read_bytes() == save_before, "live SaveRAM changed during promotion")
        promoted = TIP.read_bytes()
        post_direct = direct_target_proof(promoted, parent, original)
        req(post_direct == pre_direct, "direct target proof changed after promotion")
        post_leads = check_false_leads(promoted)
        req(post_leads.get("reintroduced") == 0, "post-promotion false-visible-lead regression")
        post_segptr = check_false_segptr(original, promoted)
        req(post_segptr.get("sites_found") == 0, "post-promotion false segmented pointer regression")
        post_garrod = build_garrod_guard(TIP, ORIGINAL, expected_target_sha=None)
        req(post_garrod.get("status") == "pass", "post-promotion native-stock guard failed")
        post_structure = scan_script_structure(ORIGINAL, TIP, 0x600000, 0x69FFFF)
        req(post_structure.get("issues") == pre_structure.get("issues"), "post-promotion script structure drift")
        req(post_structure.get("first_issues") == pre_structure.get("first_issues"), "post-promotion script issue set drift")
        post_speaker = speaker_collision_proof(promoted, original)
        req(post_speaker == pre_speaker, "post-promotion speaker collision proof drift")
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup_rom, rollback)
        os.replace(rollback, TIP)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_runtime_measured_followup_20260809_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_runtime_measured_followup_and_proven_same_structure_repairs",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_MAIN},
        "after": ident(TIP),
        "checksum": f"{EXPECTED_CHECKSUM:04X}",
        "backup_rom": ident(backup_rom),
        "backup_saveram": ident(backup_save),
        "backup_manifest": ident(backup_dir / "backup_manifest.json"),
        "live_saveram_before": {"path": "sram/monoeye_ko_expanded.sav", "size": SAVE_SIZE, "sha256": digest_bytes(save_before)},
        "live_saveram_after": ident(SAVE),
        "candidate": ident(CAND),
        "candidate_saveram": ident(CAND_SAVE),
        "user_validation": ident(APPROVAL),
        "build_report": ident(BUILD_REPORT),
        "structural_audit": ident(STRUCTURAL_AUDIT),
        "post_proofs": {
            "targets": len(post_direct["targets"]),
            "oita_excluded_byte_exact": all(row["byte_exact"] for row in post_direct["oita_excluded"]),
            "false_visible_leads_guarded": post_leads.get("total_guarded"),
            "false_visible_leads_reintroduced": post_leads.get("reintroduced"),
            "false_segmented_pointers": post_segptr.get("sites_found"),
            "garrod_guard_status": post_garrod.get("status"),
            "garrod_counts": post_garrod.get("counts"),
            "script_structure_issues": post_structure.get("issues"),
            "script_structure_first_issues": post_structure.get("first_issues"),
            "speaker_collision_proof": post_speaker,
            "portrait_6226BE": post_direct["portrait_6226BE"],
            "domon_touhou_speaker_sequence": post_direct["speaker_sequence"],
        },
        "checks": {
            "main_exact_candidate": digest_path(TIP) == EXPECTED_CAND,
            "checksum_exact": checksum(TIP) == EXPECTED_CHECKSUM,
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
            "target_count_17": len(post_direct["targets"]) == 17,
            "false_visible_lead_zero": post_leads.get("reintroduced") == 0,
            "false_segptr_zero": post_segptr.get("sites_found") == 0,
            "native_two_token_guard_pass": post_garrod.get("status") == "pass",
            "speaker_structure_zero_mismatch": post_speaker.get("structural_mismatches") == 0,
            "no_new_script_structure_issue": post_structure.get("first_issues") == parent_structure.get("first_issues"),
        },
    }
    req(all(report["checks"].values()), "post-promotion checks failed")
    atomic_json(PROMOTION, report)
    print(json.dumps({
        "ok": True,
        "main_sha256": digest_path(TIP),
        "checksum": report["checksum"],
        "backup_rom": report["backup_rom"]["path"],
        "backup_saveram": report["backup_saveram"]["path"],
        "save_unchanged": report["checks"]["live_saveram_unchanged"],
        "targets": report["post_proofs"]["targets"],
        "false_visible_leads_reintroduced": report["post_proofs"]["false_visible_leads_reintroduced"],
        "false_segmented_pointers": report["post_proofs"]["false_segmented_pointers"],
        "native_two_token_guard": report["post_proofs"]["garrod_guard_status"],
        "speaker_structure_mismatches": report["post_proofs"]["speaker_collision_proof"]["structural_mismatches"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
