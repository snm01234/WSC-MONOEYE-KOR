#!/usr/bin/env python3
"""Promote the user-validated dialogue context retranslation candidate.

ROM-only atomic transaction. The live SaveRAM is preserved. Pre-promotion
candidate/context/width/terminator/segptr/terminology gates are required. A
rollback backup is verified before the swap. Post-promotion structural gates
are rerun against the promoted TIP and the transaction rolls back on failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND = PATCH / "dialogue_legacy_mt_literal_candidate.wsc"
CAND_SAVE = ROOT / "sram/dialogue_legacy_mt_literal_candidate.sav"
ACCEPT = PATCH / "dialogue_legacy_mt_literal_acceptance_audit.json"
CONTEXT = SCRIPT / "dialogue_context_review_completion.json"
WIDTH = PATCH / "dialogue_legacy_mt_literal_width_audit.json"
TERM = PATCH / "dialogue_legacy_mt_literal_terminator_audit.json"
FALSE_SEG = PATCH / "dialogue_legacy_mt_literal_false_segptr.json"
TERMS = PATCH / "dialogue_legacy_mt_literal_terminology_audit.json"
SMOKE = PATCH / "dialogue_legacy_mt_literal_smoke.json"
TBL = PATCH / "hangul_patch_pad3.tbl"
POST_TERM = PATCH / "dialogue_context_retranslation_postpromotion_terminator_audit.json"
POST_FALSE = PATCH / "dialogue_context_retranslation_postpromotion_false_segptr.json"
POST_TERMS = PATCH / "dialogue_context_retranslation_postpromotion_terminology_audit.json"
POST_WIDTH = PATCH / "dialogue_context_retranslation_postpromotion_width_audit.json"
POST_WIDTH_CSV = SCRIPT / "dialogue_context_retranslation_postpromotion_width_offenders.csv"
POST_SMOKE = PATCH / "dialogue_context_retranslation_postpromotion_smoke.json"
REPORT = PATCH / "dialogue_context_retranslation_promotion_report.json"

EXPECTED_PARENT = "6425767be35813bf09e1fd2b223b98a9cd05d804cba254456e5d93f00a0a4f3c"
EXPECTED_CAND = "163e8e6e4984e866b1a64d92f44765197df30c6281c92adf75acd6e552ad928a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
KNOWN_WIDTH_OFFENDERS = {"630695", "63CFEA"}
UNIT_SEGS = tuple(range(0x50, 0x5E)) + tuple(range(0x6A, 0x70))


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def req(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(message)


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_checked(*args: str, allow_one: bool = False) -> int:
    proc = subprocess.run([sys.executable, *args], cwd=ROOT, check=False)
    allowed = {0, 1} if allow_one else {0}
    req(proc.returncode in allowed, f"command failed {proc.returncode}: {' '.join(args)}")
    return proc.returncode


def unit_banks_equal(a: bytes, b: bytes) -> bool:
    sb = len(a) - 0x800000
    if len(a) != len(b):
        return False
    for seg in UNIT_SEGS:
        start = sb + (seg << 16)
        end = start + 0x10000
        if a[start:end] != b[start:end]:
            return False
    return True


def width_ok(path: Path) -> bool:
    doc = load(path)
    offenders = {str(r.get("abs") or "").upper() for r in (doc.get("offenders") or [])}
    pop = doc.get("population") or {}
    scopes = pop.get("by_scope") or {}
    return (
        int(pop.get("records", -1)) == 15405
        and int(pop.get("offender_records", -1)) == 2
        and offenders == KNOWN_WIDTH_OFFENDERS
        and int((scopes.get("battle_voice") or {}).get("over_20_records", -1)) == 0
        and int((scopes.get("id_indirect_ui") or {}).get("over_20_records", -1)) == 0
    )


def main() -> int:
    required = (TIP, SAVE, CAND, CAND_SAVE, ACCEPT, CONTEXT, WIDTH, TERM, FALSE_SEG, TERMS, SMOKE, TBL)
    for path in required:
        req(path.is_file(), f"missing required artifact: {path}")

    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size wrong")
    req(CAND_SAVE.stat().st_size == SAVE_SIZE and CAND_SAVE.read_bytes() == SAVE.read_bytes(), "candidate SaveRAM mismatch")
    req(checksum_ok(CAND.read_bytes()), "candidate WonderSwan checksum invalid")

    accept = load(ACCEPT)
    req(accept.get("overall_ok") is True, "candidate acceptance failed")
    req(str(accept.get("candidate_sha256") or "").lower() == EXPECTED_CAND, "acceptance candidate mismatch")
    req(int(accept.get("targets", -1)) == 1512, "candidate target population drifted")
    context = load(CONTEXT)
    req(context.get("overall_ok") is True, "context completion audit failed")
    cs = context.get("summary") or {}
    req(int(cs.get("fixed_context_records", -1)) == 7194, "context scope drifted")
    req(int(cs.get("unresolved_semantic_residuals", -1)) == 0, "semantic residual remains")
    req(cs.get("structural_deferred") == ["630695"], "structural defer set drifted")
    req(width_ok(WIDTH), "pre-promotion width gate failed")
    term = load(TERM)
    tc = term.get("counts") or {}
    req(int(tc.get("runtime_risk", -1)) == 0 and int(tc.get("separator_nul_lost", -1)) == 0, "pre terminator risk")
    false = load(FALSE_SEG)
    req(int(false.get("sites_found", -1)) == 0, "pre false segmented pointer remains")
    terms = load(TERMS)
    req(terms.get("status") == "clean", "pre terminology audit failed")
    smoke = load(SMOKE)
    req(smoke.get("opening_required_ok") is True and smoke.get("hangul_ok") is True, "pre smoke opening/Hangul gate failed")

    before_save = ident(SAVE)
    before_tip = ident(TIP)
    cand_ident = ident(CAND)
    cand_save_ident = ident(CAND_SAVE)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_dialogue_context_retranslation"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    req(sha(backup) == EXPECTED_PARENT, "rollback backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.dialogue_context.{os.getpid()}.tmp")
    shutil.copy2(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate verification failed")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(checksum_ok(TIP.read_bytes()), "promoted TIP checksum invalid")
        req(ident(SAVE) == before_save, "live SaveRAM changed during promotion")
        req(unit_banks_equal(TIP.read_bytes(), backup.read_bytes()), "candidate changed unit/table banks vs parent")

        run_checked(str(ROOT / "tools/audit_p2_local_terminator_moves.py"), "--target", str(TIP), "--out", str(POST_TERM))
        post_term = load(POST_TERM)
        ptc = post_term.get("counts") or {}
        req(int(ptc.get("runtime_risk", -1)) == 0 and int(ptc.get("separator_nul_lost", -1)) == 0, "post terminator risk")

        run_checked(str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE))
        req(int(load(POST_FALSE).get("sites_found", -1)) == 0, "post false segmented pointer regression")

        run_checked(str(ROOT / "tools/audit_gundam_terminology_standard.py"), "--tip", str(TIP), "--tbl", str(TBL), "--out", str(POST_TERMS))
        req(load(POST_TERMS).get("status") == "clean", "post terminology regression")

        run_checked(str(ROOT / "tools/audit_dialogue_20cell_candidate.py"), "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV), allow_one=True)
        req(width_ok(POST_WIDTH), "post width gate failed")

        run_checked(str(ROOT / "tools/verify_all_stages_smoke.py"), "--rom", str(TIP), "--report", str(POST_SMOKE), allow_one=True)
        post_smoke = load(POST_SMOKE)
        req(post_smoke.get("opening_required_ok") is True and post_smoke.get("hangul_ok") is True, "post smoke opening/Hangul regression")
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copy2(backup, rollback)
        os.replace(rollback, TIP)
        raise

    after = ident(TIP)
    save_after = ident(SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_dialogue_context_retranslation_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_dialogue_context_retranslation_user_verified",
        "user_authorized_at": "2026-08-09T00:55:00+09:00",
        "before": before_tip,
        "after": after,
        "backup": ident(backup),
        "live_saveram": save_after,
        "source_candidate_before_cleanup": cand_ident,
        "source_candidate_saveram_before_cleanup": cand_save_ident,
        "context_scope_records": 7194,
        "direct_retranslation_targets": 1512,
        "unresolved_semantic_residuals": 0,
        "structural_deferred": ["630695"],
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "checks": {
            "promoted_tip_exact_candidate": after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == before_save,
            "unit_table_banks_equal_parent": unit_banks_equal(TIP.read_bytes(), backup.read_bytes()),
            "post_p2_runtime_risk_zero": int((load(POST_TERM).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_false_segmented_pointer_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
            "post_terminology_clean": load(POST_TERMS).get("status") == "clean",
            "post_width_known_only": width_ok(POST_WIDTH),
            "post_opening_required_ok": bool(load(POST_SMOKE).get("opening_required_ok")),
            "post_hangul_samples_ok": bool(load(POST_SMOKE).get("hangul_ok")),
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(all(report["checks"].values()), "promotion report checks failed")
    atomic_json(REPORT, report)

    cleanup = []
    reclaimed = 0
    for path in (CAND, CAND_SAVE, POST_WIDTH_CSV):
        if path.exists():
            size = path.stat().st_size
            path.unlink()
            cleanup.append({"path": str(path.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size
    report["cleanup"] = {"files": cleanup, "reclaimed_bytes": reclaimed}
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
