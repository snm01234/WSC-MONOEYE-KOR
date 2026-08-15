#!/usr/bin/env python3
"""Promote the user-tested ending-scene follow-up candidate to current main TIP.

User validation:
- 63B5ED Korean ending dialogue is correct in runtime.
- The 63AE59 native-stock rehome did not change the reported upper-art glitch;
  that graphics issue remains unresolved, but the user explicitly approved
  promotion of the tested candidate as-is.

ROM-only promotion. Live main SaveRAM is never replaced. Distribution xdelta is
rebuilt and round-trip checked.
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base  # noqa: E402

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "main_tip_ending_scene_followup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/main_tip_ending_scene_followup_candidate.sav"
BUILD_REPORT = PATCH / "main_tip_ending_scene_followup_candidate_report.json"
AUDIT_REPORT = PATCH / "main_tip_ending_scene_followup_candidate_audit.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
PROMOTION_REPORT = PATCH / "main_tip_ending_scene_followup_promotion_report.json"
POST_AUDIT = PATCH / "main_tip_ending_scene_followup_postpromotion_audit.json"
POST_FALSE = PATCH / "main_tip_ending_scene_followup_postpromotion_false_segptr.json"

EXPECTED_TIP_SHA = "1886d04e697baba17b454089dbd07cc556d3e49c5808e23e321cf6863773bc3d"
EXPECTED_CANDIDATE_SHA = "2ec5a8e57ff58afa9076ba68ed10f703c6a9dbf6caa8d58587d99cd9654ffbce"
EXPECTED_SAVE_SHA = "b9c8a95318050a86de48f1fa782b9de80f466a527ad253a7f4393a62b8710053"
EXPECTED_CHECKSUM = "1C50"
GRAPHICS_LOGICAL = 0x63AE59
GRAPHICS_PREFIX = bytes.fromhex("173418")
GRAPHICS_BODY = bytes.fromhex("FB2F010101")
GRAPHICS_TEXT = "시그……！！"
TRANSLATION_LOGICAL = 0x63B5ED
TRANSLATION_PREFIX = bytes.fromhex("171C18")
TRANSLATION_BODY = bytes.fromhex("E518159901010101010101010101")
TRANSLATION_TEXT = "그녀의　희생을　헛되게　하지　않으려면、"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha256(payload)}


def require(path: Path, *, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {rel(path)}")
    if expected_sha is not None and sha_path(path) != expected_sha:
        raise PromotionError(f"SHA drift: {rel(path)}")


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temp.unlink(missing_ok=True)
    with source.open("rb") as src, temp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(temp, target)


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def checksum_info(rom: bytes) -> dict[str, Any]:
    stored = int.from_bytes(rom[-2:], "little")
    computed = sum(rom[:-2]) & 0xFFFF
    return {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "valid": stored == computed}


def record(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=128)
    if got is None:
        raise PromotionError(f"unreadable record: {logical:06X}")
    return bytes(got[0]), int(got[1])


def verify_candidate_semantics(rom: bytes) -> dict[str, Any]:
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    graphics, graphics_term = record(rom, GRAPHICS_LOGICAL)
    translation, translation_term = record(rom, TRANSLATION_LOGICAL)
    if graphics != GRAPHICS_PREFIX + GRAPHICS_BODY:
        raise PromotionError(f"graphics record drift: {graphics.hex().upper()}")
    if translation != TRANSLATION_PREFIX + TRANSLATION_BODY:
        raise PromotionError(f"translation record drift: {translation.hex().upper()}")
    graphics_render = dictionary.expand(graphics[len(GRAPHICS_PREFIX):], tbl).rstrip("\u3000 ")
    translation_render = dictionary.expand(translation[len(TRANSLATION_PREFIX):], tbl).rstrip("\u3000 ")
    if graphics_render != GRAPHICS_TEXT:
        raise PromotionError(f"graphics text drift: {graphics_render!r}")
    if translation_render != TRANSLATION_TEXT:
        raise PromotionError(f"translation drift: {translation_render!r}")
    if graphics[len(GRAPHICS_PREFIX):len(GRAPHICS_PREFIX)+2] == b"\xE5\x18":
        raise PromotionError("graphics record unexpectedly returned to E5 18")
    return {
        "graphics": {
            "abs": f"{GRAPHICS_LOGICAL:06X}",
            "render": graphics_render,
            "body_hex": GRAPHICS_BODY.hex().upper(),
            "terminator_file": f"{graphics_term:07X}",
            "runtime_user_result": "upper-art glitch unchanged; unresolved",
        },
        "translation": {
            "abs": f"{TRANSLATION_LOGICAL:06X}",
            "render": translation_render,
            "body_hex": TRANSLATION_BODY.hex().upper(),
            "terminator_file": f"{translation_term:07X}",
            "runtime_user_result": "confirmed correct",
        },
    }


def run_false_segptr(target: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"), "--target", str(target), "--out", str(POST_FALSE)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PromotionError("false-segptr scan failed: " + (completed.stderr or completed.stdout)[-800:])
    report = json.loads(POST_FALSE.read_text(encoding="utf-8"))
    sites = int(report.get("sites_found", -1))
    if report.get("ok") is not True or sites != 0:
        raise PromotionError(f"false-segptr sites found: {sites}")
    return {
        "ok": True,
        "sites_found": 0,
        "ext3_token_prefixes_ignored": int(report.get("ext3_token_prefixes_ignored") or 0),
        "report": identity(POST_FALSE),
    }


def rebuild_xdelta() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools/make_main_tip_xdelta.py")],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PromotionError("xdelta rebuild failed: " + (completed.stderr or completed.stdout)[-800:])
    metadata = ROOT / "out/dist/monoeye_ko_expanded_xdelta.json"
    patch = ROOT / "out/dist/monoeye_ko_expanded.xdelta"
    meta = json.loads(metadata.read_text(encoding="utf-8"))
    result_sha = str(((meta.get("main_tip") or {}).get("sha256") or "")).lower()
    roundtrip = meta.get("roundtrip_matches_main_tip") is True
    if result_sha != EXPECTED_CANDIDATE_SHA or not roundtrip:
        raise PromotionError(f"xdelta verification failed: sha={result_sha}, roundtrip={roundtrip}")
    return {
        "ok": True,
        "path": rel(patch),
        "size": patch.stat().st_size,
        "sha256": sha_path(patch),
        "metadata": rel(metadata),
        "result_sha256": result_sha,
        "roundtrip_matches_main_tip": roundtrip,
    }


def main() -> int:
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, expected_sha=EXPECTED_CANDIDATE_SHA)
    require(SAVE, size=SAVE_SIZE, expected_sha=EXPECTED_SAVE_SHA)
    # The user runtime-tested this candidate and BizHawk updated the paired
    # SaveRAM afterward. Promotion is ROM-only, so only its size is relevant;
    # the live main SaveRAM remains guarded byte-exact below.
    require(CANDIDATE_SAVE, size=SAVE_SIZE)
    require(BUILD_REPORT)
    require(AUDIT_REPORT)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    if build.get("ok") is not True or audit.get("ok") is not True:
        raise PromotionError("candidate build/audit not clean")
    if str(((build.get("outputs") or {}).get("candidate_rom") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build report candidate SHA drifted")
    if str(((audit.get("checks") or {}).get("candidate_identity_exact"))).lower() != "true":
        raise PromotionError("candidate audit identity did not pass")

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    info = checksum_info(candidate)
    if not info["valid"] or info["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {info}")
    proof_before = verify_candidate_semantics(candidate)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_ending_scene_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if sha_path(backup_rom) != EXPECTED_TIP_SHA:
        raise PromotionError("rollback backup mismatch")

    try:
        atomic_copy(CANDIDATE, TIP)
        promoted = TIP.read_bytes()
        if sha256(promoted) != EXPECTED_CANDIDATE_SHA:
            raise PromotionError("promoted TIP differs from tested candidate")
        proof_after = verify_candidate_semantics(promoted)
        false_segptr = run_false_segptr(TIP)
        post_checks = {
            "tip_matches_tested_candidate": sha256(promoted) == EXPECTED_CANDIDATE_SHA,
            "checksum_valid": checksum_info(promoted)["valid"] and checksum_info(promoted)["stored"] == EXPECTED_CHECKSUM,
            "translation_runtime_user_confirmed": True,
            "graphics_issue_explicitly_remains_unresolved": True,
            "graphics_record_no_ext3": proof_after["graphics"]["body_hex"].startswith("FB2F"),
            "false_segptr_clean": false_segptr["ok"] is True,
            "rollback_rom_exact": sha_path(backup_rom) == EXPECTED_TIP_SHA,
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
        }
        if not all(post_checks.values()):
            raise PromotionError(f"post-promotion audit failed: {post_checks}")
        xdelta = rebuild_xdelta()
    except Exception:
        atomic_bytes(TIP, parent)
        raise

    promoted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_main_tip_ending_scene_followup_candidate.py",
        "ok": True,
        "tip": identity(TIP, promoted),
        "tip_checksum": checksum_info(promoted),
        "rollback_rom": identity(backup_rom),
        "proof": proof_after,
        "false_segptr": false_segptr,
        "checks": post_checks,
        "known_unresolved": {
            "ending_upper_art_glitch": "unchanged in user runtime test; this promotion does not claim the graphics issue is fixed",
        },
    }
    atomic_json(POST_AUDIT, post)

    build["status"] = "promoted_to_current_main_partial_runtime_validation"
    build["promotion"] = "promoted_by_explicit_user_request"
    build["published"] = True
    build["promoted_at"] = promoted_at
    build["runtime_validation"] = {
        "translation_63B5ED": "user_confirmed_correct",
        "graphics_63AE59": "user_confirmed_no_change_glitch_unresolved",
    }
    atomic_json(BUILD_REPORT, build)

    audit["status"] = "promoted_to_current_main_partial_runtime_validation"
    audit["promotion"] = "promoted_by_explicit_user_request"
    audit["promoted_at"] = promoted_at
    audit["runtime_validation_still_required"] = False
    audit["runtime_result"] = {
        "translation_63B5ED": "confirmed_correct",
        "graphics_63AE59": "glitch_unchanged_unresolved",
    }
    atomic_json(AUDIT_REPORT, audit)

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_main_tip_ending_scene_followup_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": promoted_at,
        "authorization": "사용자가 63B5ED 번역 정상 출력을 확인했고, 엔딩 그래픽 글리치는 변함없음을 인지한 상태에서 테스트 후보를 메인 TIP으로 승격 요청함",
        "old_tip": {"path": rel(TIP), "size": ROM_SIZE, "sha256": EXPECTED_TIP_SHA},
        "new_tip": identity(TIP, promoted),
        "new_tip_checksum": checksum_info(promoted),
        "backup_rom": identity(backup_rom),
        "tested_candidate": identity(CANDIDATE, candidate),
        "runtime_user_validation": {
            "63B5ED_translation": "confirmed correct: 그녀의 희생을 헛되게 하지 않으려면、",
            "63AE59_graphics": "no visible change; upper-art glitch remains unresolved",
        },
        "proof_before_promotion": proof_before,
        "proof_after_promotion": proof_after,
        "false_segptr": false_segptr,
        "xdelta": xdelta,
        "postpromotion_audit": identity(POST_AUDIT),
        "main_saveram_policy": "ROM-only promotion; live main SaveRAM remained byte-identical",
        "live_saveram": identity(SAVE, save_before),
        "known_unresolved": ["특정 엔딩 화면 상단 그래픽 글리치 - 63AE59 E5 18 제거로도 실측 변화 없음; 별도 원인 분석 필요"],
    }
    atomic_json(PROMOTION_REPORT, promotion)

    print(json.dumps({
        "ok": True,
        "published": True,
        "old_tip_sha256": EXPECTED_TIP_SHA,
        "new_tip": promotion["new_tip"],
        "checksum": promotion["new_tip_checksum"],
        "runtime_user_validation": promotion["runtime_user_validation"],
        "known_unresolved": promotion["known_unresolved"],
        "xdelta": xdelta,
        "rollback": rel(backup_rom),
        "live_saveram_unchanged": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
