#!/usr/bin/env python3
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
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
PARENT_CAND = PATCH / "dialogue_runtime_followup_candidate.wsc"
PARENT_SAVE = ROOT / "sram/dialogue_runtime_followup_candidate.sav"
CAND = PATCH / "dialogue_runtime_followup_portrait_candidate.wsc"
CAND_SAVE = ROOT / "sram/dialogue_runtime_followup_portrait_candidate.sav"
FINAL = PATCH / "dialogue_runtime_followup_portrait_final_status.json"
PORTRAIT = PATCH / "dialogue_runtime_followup_portrait_report.json"
POST_WIDTH = PATCH / "dialogue_runtime_followup_portrait_postpromotion_width_audit.json"
POST_WIDTH_CSV = SCRIPT / "dialogue_runtime_followup_portrait_postpromotion_width_offenders.csv"
POST_TERM = PATCH / "dialogue_runtime_followup_portrait_postpromotion_terminator_audit.json"
POST_FALSE = PATCH / "dialogue_runtime_followup_portrait_postpromotion_false_segptr.json"
POST_COLL = PATCH / "dialogue_runtime_followup_portrait_postpromotion_collision.json"
REPORT = PATCH / "dialogue_runtime_followup_portrait_promotion_report.json"
TBL = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
EXPECTED_PARENT = "8e80bc7e722652b9c6b31282c272966ae92f9d3c82975344c577556bf5b9145a"
EXPECTED_CAND = "4e4cdcabdf88ddfa1c14f792ebf97e796e0e7cfa9a72f712599aa38cc955e49d"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ident(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def req(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def render(rom: bytes, dictionary, tbl: Tbl, address: int, prefix: bytes = b"") -> str:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + address, max_len=256)
    req(got is not None, f"unreadable {address:06X}")
    payload = bytes(got[0])
    if prefix:
        req(payload.startswith(prefix), f"prefix missing {address:06X}")
        body = payload[len(prefix):]
    else:
        _, body, _ = split_prefix_body(payload)
    return dictionary.expand(body, tbl).rstrip("\u3000 \t")


def main() -> int:
    for p in (TIP, SAVE, CAND, CAND_SAVE, FINAL, PORTRAIT, PARENT_CAND, PARENT_SAVE):
        req(p.is_file(), f"missing required artifact: {p}")
    req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_PARENT, "main parent identity drifted")
    req(CAND.stat().st_size == ROM_SIZE and sha(CAND) == EXPECTED_CAND, "candidate identity drifted")
    req(SAVE.stat().st_size == SAVE_SIZE and CAND_SAVE.stat().st_size == SAVE_SIZE, "SaveRAM size wrong")
    req(SAVE.read_bytes() == CAND_SAVE.read_bytes(), "candidate SaveRAM is not current live SaveRAM")
    req(checksum_ok(CAND.read_bytes()), "candidate checksum invalid")

    final = load(FINAL)
    req(final.get("ok") is True and final.get("status") == "candidate_ready_for_direct_promotion", "final gate not clean")
    req(str(((final.get("candidate") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "final candidate hash mismatch")
    fc = final.get("counts") or {}
    req(int(fc.get("followup_quality_targets", -1)) == 16, "quality target count drifted")
    req(int(fc.get("portrait_structure_targets", -1)) == 358, "portrait target count drifted")
    req(int(fc.get("safe_long_token_backed_missing_after", -1)) == 0, "long portrait loss remains")
    req(int(fc.get("runtime_width_offenders", -1)) == 0 and int(fc.get("runtime_width_max_cells", 999)) <= 20, "width gate not clean")

    portrait = load(PORTRAIT)
    targets = portrait.get("targets") or []
    req(portrait.get("ok") is True and len(targets) == 358, "portrait report not clean")
    anchor = portrait.get("screenshot_anchor_5D7084") or {}
    req(anchor.get("broken_visible_if_first_byte_consumed") == "こやナ", "anchor signature drifted")

    save_before = ident(SAVE)
    candidate_bytes = CAND.read_bytes()
    cand_ident = ident(CAND)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_runtime_followup_portrait"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copyfile(TIP, backup)
    req(sha(backup) == EXPECTED_PARENT, "backup verification failed")

    staged = TIP.with_name(f".{TIP.name}.runtimefollowup.{os.getpid()}.tmp")
    shutil.copyfile(CAND, staged)
    req(sha(staged) == EXPECTED_CAND, "staged candidate hash mismatch")
    os.replace(staged, TIP)

    try:
        req(TIP.stat().st_size == ROM_SIZE and sha(TIP) == EXPECTED_CAND, "promoted TIP hash mismatch")
        req(TIP.read_bytes() == candidate_bytes, "promoted TIP differs from candidate")
        req(checksum_ok(TIP.read_bytes()), "promoted checksum invalid")
        req(ident(SAVE) == save_before, "live SaveRAM changed")

        subprocess.run([
            sys.executable, str(ROOT / "tools/audit_dialogue_20cell_candidate.py"),
            "--rom", str(TIP), "--out", str(POST_WIDTH), "--out-csv", str(POST_WIDTH_CSV),
        ], cwd=ROOT, check=True)
        width = load(POST_WIDTH)
        wp = width.get("population") or {}
        req(width.get("ok") is True, "postpromotion width audit failed")
        req(str(((width.get("rom") or {}).get("sha256") or "")).lower() == EXPECTED_CAND, "post width hash mismatch")
        req(int(wp.get("records", -1)) == 15405 and int(wp.get("offender_records", -1)) == 0 and int(wp.get("max_line_cells", 999)) <= 20, "post width regression")

        subprocess.run([
            sys.executable, str(ROOT / "tools/audit_p2_local_terminator_moves.py"),
            "--target", str(TIP), "--out", str(POST_TERM),
        ], cwd=ROOT, check=True)
        term = load(POST_TERM)
        tc = term.get("counts") or {}
        req(int(tc.get("runtime_risk", -1)) == 0 and int(tc.get("separator_nul_lost", -1)) == 0, "post P2 regression")

        subprocess.run([
            sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"),
            "--target", str(TIP), "--lo-bank", "0x5D", "--hi-bank", "0x75", "--out", str(POST_FALSE),
        ], cwd=ROOT, check=True)
        false = load(POST_FALSE)
        req(false.get("ok") is True and int(false.get("sites_found", -1)) == 0, "post false-segptr regression")

        subprocess.run([
            sys.executable, str(ROOT / "tools/audit_speaker_dictlead_nul_collisions.py"),
            "--target", str(TIP), "--out", str(POST_COLL),
        ], cwd=ROOT, check=True)
        coll = load(POST_COLL)
        cc = coll.get("counts") or {}
        req(coll.get("ok") is True and int(cc.get("japanese_or_mixed_remaining", -1)) == 0 and int(cc.get("over_20", -1)) == 0, "post speaker-collision regression")

        promoted = TIP.read_bytes()
        sb = stock_base(promoted)
        for row in targets:
            logical = int(row["abs"], 16)
            payload = bytes.fromhex(row["after_payload_hex"])
            at = sb + logical
            req(promoted[at:at + len(payload)] == payload, f"portrait target drift {row['abs']}")
            req(promoted[at + len(payload)] == 0, f"portrait terminator drift {row['abs']}")

        tbl = Tbl.load(TBL)
        dictionary = make_dictionary_ext3(promoted, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
        req(render(promoted, dictionary, tbl, 0x6046E4) == "어이、", "6046E4 post render mismatch")
        req(render(promoted, dictionary, tbl, 0x5D84F4, bytes.fromhex("40")) == "납작하게　만들어　주마！！", "5D84F4 post render mismatch")
        req(render(promoted, dictionary, tbl, 0x5D7084, bytes.fromhex("35")) == "아직　무대가　안　갖춰졌다는　건가……", "5D7084 post render mismatch")
    except Exception:
        rollback = TIP.with_name(f".{TIP.name}.rollback.{os.getpid()}.tmp")
        shutil.copyfile(backup, rollback)
        os.replace(rollback, TIP)
        raise

    after = ident(TIP)
    save_after = ident(SAVE)
    report = {
        "schema_version": 1,
        "ok": True,
        "promoted": True,
        "status": "promoted_runtime_followup_and_battle_portrait_repair",
        "before": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_PARENT},
        "after": after,
        "backup": ident(backup),
        "live_saveram": save_after,
        "source_candidate": cand_ident,
        "candidate_final_gate": ident(FINAL),
        "portrait_build_report": ident(PORTRAIT),
        "post_width_audit": ident(POST_WIDTH),
        "post_terminator_audit": ident(POST_TERM),
        "post_false_segptr": ident(POST_FALSE),
        "post_speaker_collision": ident(POST_COLL),
        "checksum": f"{int.from_bytes(TIP.read_bytes()[-2:], 'little'):04X}",
        "counts": {
            "quality_followup_targets": 16,
            "portrait_structure_targets": 358,
            "runtime_width_records": 15405,
            "runtime_width_offenders": 0,
        },
        "checks": {
            "promoted_tip_exact_candidate": after["sha256"] == EXPECTED_CAND,
            "live_saveram_unchanged": save_after == save_before,
            "post_width_zero": int((load(POST_WIDTH).get("population") or {}).get("offender_records", -1)) == 0,
            "post_p2_zero": int((load(POST_TERM).get("counts") or {}).get("runtime_risk", -1)) == 0,
            "post_false_segptr_zero": int(load(POST_FALSE).get("sites_found", -1)) == 0,
            "post_hidden_japanese_zero": int((load(POST_COLL).get("counts") or {}).get("japanese_or_mixed_remaining", -1)) == 0,
            "post_6046E4_exact": True,
            "post_5D84F4_exact": True,
            "post_5D7084_exact": True,
        },
        "cleanup": {"files": [], "reclaimed_bytes": 0},
    }
    req(all(report["checks"].values()), "promotion report checks not all true")
    atomic_json(REPORT, report)

    cleanup = []
    reclaimed = 0
    for p in (PARENT_CAND, PARENT_SAVE, CAND, CAND_SAVE):
        if p.exists():
            size = p.stat().st_size
            p.unlink()
            cleanup.append({"path": str(p.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size
    for p in (
        SCRIPT / "dialogue_runtime_followup_parent_width_offenders_runtimeaware.csv",
        SCRIPT / "dialogue_runtime_followup_portrait_width_offenders.csv",
        POST_WIDTH_CSV,
    ):
        if p.exists() and p.stat().st_size <= 4096:
            size = p.stat().st_size
            p.unlink()
            cleanup.append({"path": str(p.relative_to(ROOT)).replace("\\", "/"), "bytes": size})
            reclaimed += size
    report["cleanup"] = {"files": cleanup, "reclaimed_bytes": reclaimed}
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
