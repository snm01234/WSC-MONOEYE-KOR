#!/usr/bin/env python3
"""Promote Galmuri11 page21-restore ending credits and bank-62 scouting tail.

ROM-only merge onto the current main TIP. Live SaveRAM is never replaced.

The page21-restore test ROM is the user-validated ending-credits chain
(cinematic guard + Galmuri11 Bitmap + page16-exit clear + Bitmap page21
``091-0AA``). The scouting tail candidate restores leftover ``62:D800–FFFF``
event structures to the original ROM. The two diffs overlap only at the
WonderSwan checksum, which is recomputed after the merge.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base, update_ws_checksum  # noqa: E402


PATCH = ROOT / "out/patch"
SRAM = ROOT / "sram"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = SRAM / "monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
ENDING = (
    PATCH
    / "ending_credits_galmuri11_bitmap_page21_end_restore_candidate"
    / "monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page21_end_restore_test.wsc"
)
ENDING_REPORT = (
    PATCH
    / "ending_credits_galmuri11_bitmap_page21_end_restore_candidate"
    / "ending_credits_galmuri11_bitmap_page21_end_restore_report.json"
)
ENDING_AUDIT = (
    PATCH
    / "ending_credits_galmuri11_bitmap_page21_end_restore_candidate"
    / "ending_credits_galmuri11_bitmap_page21_end_restore_audit.json"
)
SCOUT = PATCH / "scouting_map_event_structure_tail_repair_candidate.wsc"
SCOUT_REPORT = PATCH / "scouting_map_event_structure_tail_repair_report.json"
SCOUT_AUDIT = PATCH / "scouting_map_event_structure_tail_repair_audit.json"
PROMOTION_REPORT = PATCH / "ending_credits_page21_and_scouting_tail_promotion_report.json"
POST_AUDIT = PATCH / "ending_credits_page21_and_scouting_tail_postpromotion_audit.json"
POST_FALSE = PATCH / "ending_credits_page21_and_scouting_tail_postpromotion_false_segptr.json"

EXPECTED_TIP_SHA = "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde"
EXPECTED_ENDING_SHA = "6ca50bb617b290619ebb47696aec4446fd1b7c59407e20e36726a54a122d1e0e"
EXPECTED_SCOUT_SHA = "28bda08981ac09ef6e1bfde884a712150f95f010f6903cc446071d52229f6e42"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"

ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768
RECORD = struct.Struct("<BBBBHHHHHH")
ATLAS_BASE = 0x500000
PAGE21_FIRST = 0x091
STOCK_D652 = bytes.fromhex("C706561B000F")
RESTORE_LOGICAL_START = 0x62D800
RESTORE_LOGICAL_END = 0x630000
PRIOR_REPAIR = (0x62D675, 0x62D6CF)
EXPECTED_SCOUT_BYTES = 671
EXPECTED_ENDING_PAYLOAD_BYTES = 39_547
NAME75_ZEDAN = 0x75BDFA
NAME75_SAHARA = 0x75BDB2


class PromotionError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha256(payload)}


def require(path: Path, *, size: int, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {rel(path)}")
    if path.stat().st_size != size:
        raise PromotionError(f"size drift: {rel(path)}")
    if expected_sha is not None and sha_path(path) != expected_sha:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def checksum_info(rom: bytes | bytearray) -> dict[str, Any]:
    stored = int.from_bytes(bytes(rom[-2:]), "little")
    computed = sum(rom[:-2]) & 0xFFFF
    return {
        "stored": f"{stored:04X}",
        "computed": f"{computed:04X}",
        "valid": stored == computed,
    }


def jump_target(ip: int, instruction: bytes) -> int:
    if len(instruction) != 3 or instruction[0] not in (0xE8, 0xE9):
        raise PromotionError(f"not a near call/jump at {ip:04X}: {instruction.hex()}")
    displacement = struct.unpack_from("<h", instruction, 1)[0]
    return (ip + 3 + displacement) & 0xFFFF


def load_ok_report(path: Path, candidate_sha: str, key: str) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("ok") is not True:
        raise PromotionError(f"{rel(path)} is not ok")
    if key == "ending":
        got = str((report.get("candidate") or {}).get("sha256") or "").lower()
    else:
        got = str(((report.get("output") or {}).get("rom") or {}).get("sha256") or "").lower()
    if got != candidate_sha:
        raise PromotionError(f"{rel(path)} SHA binding drifted: {got}")
    return report


def merge_roms(main: bytes, ending: bytes, scout: bytes) -> tuple[bytes, dict[str, Any]]:
    if len(main) != ROM_SIZE or len(ending) != ROM_SIZE or len(scout) != ROM_SIZE:
        raise PromotionError("ROM size mismatch during merge")
    merged = bytearray(main)
    conflicts: list[int] = []
    ending_bytes = 0
    scout_bytes = 0
    for index in range(ROM_SIZE - 2):
        ending_changed = ending[index] != main[index]
        scout_changed = scout[index] != main[index]
        if ending_changed and scout_changed:
            if ending[index] != scout[index]:
                conflicts.append(index)
            else:
                merged[index] = ending[index]
                ending_bytes += 1
                scout_bytes += 1
        elif ending_changed:
            merged[index] = ending[index]
            ending_bytes += 1
        elif scout_changed:
            merged[index] = scout[index]
            scout_bytes += 1
    if conflicts:
        raise PromotionError(f"conflicting payload overlap at {conflicts[0]:08X}")
    if ending_bytes != EXPECTED_ENDING_PAYLOAD_BYTES:
        raise PromotionError(f"ending payload byte count drifted: {ending_bytes}")
    if scout_bytes != EXPECTED_SCOUT_BYTES:
        raise PromotionError(f"scouting payload byte count drifted: {scout_bytes}")
    checksum = update_ws_checksum(merged)
    result = bytes(merged)
    info = checksum_info(result)
    if not info["valid"] or info["stored"] != f"{checksum:04X}":
        raise PromotionError(f"merged checksum invalid: {info}")
    return result, {
        "ending_payload_bytes": ending_bytes,
        "scouting_payload_bytes": scout_bytes,
        "checksum": info,
        "changed_bytes_vs_main": ending_bytes + scout_bytes + 2,
    }


def prove_merged(merged: bytes, main: bytes, ending: bytes, scout: bytes, original: bytes) -> dict[str, Any]:
    sb = stock_base(merged)
    orig_base = 0
    rec = RECORD.unpack_from(merged, ATLAS_BASE + 21 * RECORD.size)
    if rec[9] != PAGE21_FIRST:
        raise PromotionError(f"page21 first_tile drifted: {rec[9]:03X}")
    if rec[2] != 13 or rec[3] != 5 or rec[4] != 26 or rec[8] != 6:
        raise PromotionError(f"page21 bar contract drifted: {rec}")
    if merged[0xFED652 : 0xFED652 + 6] != STOCK_D652:
        raise PromotionError("D652 is not stock")
    if jump_target(0xD1CA, merged[0xFED1CA:0xFED1CD]) != 0xFD5D:
        raise PromotionError("page16-exit redirect lost")
    if jump_target(0xCA6E, merged[0xFECA6E:0xFECA71]) != 0xCBD1:
        raise PromotionError("idle overlay suppression lost")
    rec17 = RECORD.unpack_from(merged, ATLAS_BASE + 17 * RECORD.size)
    if rec17[9] != 0x06C:
        raise PromotionError("page17 16-to-17 range lost")

    tail = slice(sb + RESTORE_LOGICAL_START, sb + RESTORE_LOGICAL_END)
    orig_tail = slice(orig_base + RESTORE_LOGICAL_START, orig_base + RESTORE_LOGICAL_END)
    if merged[tail] != original[orig_tail]:
        raise PromotionError("bank-62 tail does not match original")
    if merged[tail] != scout[tail]:
        raise PromotionError("bank-62 tail does not match scouting candidate")
    prior = slice(sb + PRIOR_REPAIR[0], sb + PRIOR_REPAIR[1])
    if merged[prior] != main[prior] or merged[prior] != original[orig_base + PRIOR_REPAIR[0] : orig_base + PRIOR_REPAIR[1]]:
        raise PromotionError("prior 62:D675–D6CF repair drifted")
    if merged[sb + NAME75_ZEDAN : sb + NAME75_ZEDAN + 5] != main[sb + NAME75_ZEDAN : sb + NAME75_ZEDAN + 5]:
        raise PromotionError("name75 Zedan changed")
    if merged[sb + NAME75_SAHARA : sb + NAME75_SAHARA + 5] != main[sb + NAME75_SAHARA : sb + NAME75_SAHARA + 5]:
        raise PromotionError("name75 Sahara changed")

    if merged[0x500000:0x510000] != ending[0x500000:0x510000]:
        raise PromotionError("bank50 atlas does not match ending candidate")
    if merged[0xFE0000:0xFF0000] != ending[0xFE0000:0xFF0000]:
        raise PromotionError("bank7E code does not match ending candidate")
    if merged[0xE20000 : sb + RESTORE_LOGICAL_START] != main[0xE20000 : sb + RESTORE_LOGICAL_START]:
        raise PromotionError("bank62 before D800 drifted")

    for index in range(ROM_SIZE - 2):
        expected = main[index]
        if ending[index] != main[index]:
            expected = ending[index]
        if scout[index] != main[index]:
            expected = scout[index]
        if merged[index] != expected:
            raise PromotionError(f"merged byte drifted at {index:08X}")

    return {
        "page21_first_tile": f"{rec[9]:03X}",
        "page17_first_tile": f"{rec17[9]:03X}",
        "stock_D652": True,
        "page16_exit_clear": True,
        "idle_overlay_suppressed": True,
        "bank62_tail_matches_original": True,
        "prior_repair_kept": True,
        "name75_untouched": True,
        "bank50_matches_ending": True,
        "bank7E_matches_ending": True,
    }


def run_false_segptr(target: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "scan_false_segptr_writes.py"),
            "--target",
            str(target),
            "--out",
            str(POST_FALSE),
        ],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise PromotionError(f"false-segptr scan failed: {completed.returncode}")
    report = json.loads(POST_FALSE.read_text(encoding="utf-8"))
    sites_found = int(report.get("sites_found", -1))
    if report.get("ok") is not True or sites_found != 0:
        raise PromotionError(f"false-segptr sites found: {sites_found}")
    return {
        "ok": True,
        "sites_found": sites_found,
        "ext3_token_prefixes_ignored": int(report.get("ext3_token_prefixes_ignored") or 0),
        "report": identity(POST_FALSE),
    }


def rebuild_xdelta() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_main_tip_xdelta.py")],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PromotionError(
            "xdelta rebuild failed: "
            + (completed.stderr or completed.stdout or str(completed.returncode))[-800:]
        )
    meta_path = ROOT / "out/dist/monoeye_ko_expanded_xdelta.json"
    if not meta_path.is_file():
        raise PromotionError("xdelta metadata missing after rebuild")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    patch = ROOT / "out/dist/monoeye_ko_expanded.xdelta"
    return {
        "ok": True,
        "path": rel(patch),
        "size": patch.stat().st_size,
        "sha256": sha_path(patch),
        "metadata": rel(meta_path),
        "result_sha256": str(((meta.get("main_tip") or {}).get("sha256") or "")).lower(),
        "roundtrip_matches_main_tip": meta.get("roundtrip_matches_main_tip") is True,
    }


def main() -> int:
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)
    require(ENDING, size=ROM_SIZE, expected_sha=EXPECTED_ENDING_SHA)
    require(SCOUT, size=ROM_SIZE, expected_sha=EXPECTED_SCOUT_SHA)
    require(ORIGINAL, size=ORIGINAL_SIZE, expected_sha=EXPECTED_ORIGINAL_SHA)
    require(SAVE, size=SAVE_SIZE)
    require(ENDING_REPORT, size=ENDING_REPORT.stat().st_size)
    require(ENDING_AUDIT, size=ENDING_AUDIT.stat().st_size)
    require(SCOUT_REPORT, size=SCOUT_REPORT.stat().st_size)
    require(SCOUT_AUDIT, size=SCOUT_AUDIT.stat().st_size)

    ending_report = load_ok_report(ENDING_REPORT, EXPECTED_ENDING_SHA, "ending")
    scout_report = load_ok_report(SCOUT_REPORT, EXPECTED_SCOUT_SHA, "scout")
    ending_audit = json.loads(ENDING_AUDIT.read_text(encoding="utf-8"))
    scout_audit = json.loads(SCOUT_AUDIT.read_text(encoding="utf-8"))
    if ending_audit.get("ok") is not True:
        raise PromotionError("ending candidate audit is not ok")
    if scout_audit.get("ok") is not True:
        raise PromotionError("scouting candidate audit is not ok")
    if str((ending_report.get("parent") or {}).get("sha256") or "") != (
        "f3198ca1f29b3d4584c49186f1a02274046912feebed530c56d5fb4c852bcf77"
    ):
        raise PromotionError("ending parent SHA drifted")
    scout_parent = str(
        ((scout_report.get("input") or {}).get("main_tip") or {}).get("sha256") or ""
    ).lower()
    if scout_parent != EXPECTED_TIP_SHA:
        raise PromotionError("scouting parent SHA drifted")

    main_rom = TIP.read_bytes()
    ending = ENDING.read_bytes()
    scout = SCOUT.read_bytes()
    original = ORIGINAL.read_bytes()
    save_before = SAVE.read_bytes()
    merged, merge_info = merge_roms(main_rom, ending, scout)
    proof = prove_merged(merged, main_rom, ending, scout, original)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_ending_credits_page21_and_scouting_tail"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if sha_path(backup_rom) != EXPECTED_TIP_SHA:
        raise PromotionError("rollback backup mismatch")

    try:
        atomic_bytes(TIP, merged)
        promoted = TIP.read_bytes()
        if sha256(promoted) != sha256(merged):
            raise PromotionError("promoted TIP does not match merged image")
        post_proof = prove_merged(promoted, main_rom, ending, scout, original)
        false_segptr = run_false_segptr(TIP)
        post_checks = {
            "tip_matches_merged_image": sha256(promoted) == sha256(merged),
            "checksum_valid": checksum_info(promoted)["valid"],
            "ending_proof": all(post_proof.values()),
            "false_segptr_clean": false_segptr["ok"] is True,
            "rollback_rom_exact": sha_path(backup_rom) == EXPECTED_TIP_SHA,
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
        }
        if not all(post_checks.values()):
            raise PromotionError(f"post-promotion audit failed: {post_checks}")
    except Exception:
        atomic_bytes(TIP, main_rom)
        raise

    xdelta: dict[str, Any] | None = None
    xdelta_error: str | None = None
    try:
        xdelta = rebuild_xdelta()
        if xdelta["result_sha256"] != sha256(merged):
            raise PromotionError(f"xdelta result SHA drifted: {xdelta['result_sha256']}")
        if xdelta.get("roundtrip_matches_main_tip") is not True:
            raise PromotionError("xdelta round-trip did not match the new TIP")
    except Exception as exc:
        xdelta_error = str(exc)

    promoted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    audit = {
        "schema_version": 1,
        "generated_by": "tools/promote_ending_credits_page21_and_scouting_tail_candidate.py",
        "ok": True,
        "tip": identity(TIP, promoted),
        "tip_checksum": checksum_info(promoted),
        "rollback_rom": identity(backup_rom),
        "proof": post_proof,
        "merge": merge_info,
        "false_segptr": false_segptr,
        "checks": post_checks,
    }
    atomic_json(POST_AUDIT, audit)

    for report_path, report in (
        (ENDING_REPORT, ending_report),
        (SCOUT_REPORT, scout_report),
    ):
        report["status"] = "promoted_to_current_main"
        report["promotion"] = "promoted"
        report["published"] = True
        report["promoted_at"] = promoted_at
        atomic_json(report_path, report)

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_ending_credits_page21_and_scouting_tail_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": promoted_at,
        "authorization": (
            "사용자가 page21_end_restore 테스트 ROM 수정사항과 "
            "scouting_map_event_structure_tail_repair_candidate.wsc 변경사항의 "
            "메인 TIP 승격을 요청함"
        ),
        "old_tip": {
            "path": "out/patch/monoeye_ko_expanded.wsc",
            "size": ROM_SIZE,
            "sha256": EXPECTED_TIP_SHA,
        },
        "new_tip": identity(TIP, promoted),
        "new_tip_checksum": checksum_info(promoted),
        "backup_rom": identity(backup_rom),
        "sources": {
            "ending_credits": identity(ENDING, ending),
            "scouting_tail": identity(SCOUT, scout),
        },
        "merge": merge_info,
        "proof": post_proof,
        "false_segptr": false_segptr,
        "xdelta": xdelta,
        "xdelta_error": xdelta_error,
        "postpromotion_audit": identity(POST_AUDIT),
        "main_saveram_policy": "ROM-only promotion; live main SaveRAM remained byte-identical",
        "live_saveram": identity(SAVE, save_before),
    }
    atomic_json(PROMOTION_REPORT, promotion)
    print(
        json.dumps(
            {
                "ok": True,
                "old_tip_sha256": EXPECTED_TIP_SHA,
                "new_tip": promotion["new_tip"],
                "checksum": promotion["new_tip_checksum"],
                "merge": merge_info,
                "proof": post_proof,
                "xdelta": xdelta if xdelta is not None else {"ok": False, "error": xdelta_error},
                "live_saveram_unchanged": True,
                "rollback": rel(backup_rom),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if xdelta_error:
        raise PromotionError(xdelta_error)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"PROMOTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
