#!/usr/bin/env python3
"""Promote the user-validated terrain + 62663E nested-wrapper candidate.

ROM-only promotion. The live main SaveRAM is preserved byte-exactly. The
candidate SaveRAM may have changed during the user's runtime test and is not a
promotion input.
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
from build_scenario_page_boundary_guard_candidate import original_unit_kinds  # noqa: E402
from build_terrain_gakehau_selective_candidate import (  # noqa: E402
    DIALOGUE_ABS,
    DIALOGUE_AFTER,
    DIALOGUE_TERM,
    NATIVE_TWO_TOKEN_NEIGHBORS,
    OU_SECOND_CONSUMER,
    OU_WRAPPER_SLOT,
    ORIGINAL,
    TERRAIN_END,
    TERRAIN_START,
    TERRAIN_STRIDE,
    far_target,
    read_record,
    record_text,
)
from monoeye_rom import Tbl, stock_base  # noqa: E402

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "terrain_gakehau_selective_candidate.wsc"
BUILD_REPORT = PATCH / "terrain_gakehau_selective_candidate_report.json"
AUDIT_REPORT = PATCH / "terrain_gakehau_selective_candidate_audit.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
PROMOTION_REPORT = PATCH / "terrain_gakehau_selective_promotion_report.json"
POST_AUDIT = PATCH / "terrain_gakehau_selective_postpromotion_audit.json"
POST_FALSE = PATCH / "terrain_gakehau_selective_postpromotion_false_segptr.json"

EXPECTED_TIP_SHA = "2ec5a8e57ff58afa9076ba68ed10f703c6a9dbf6caa8d58587d99cd9654ffbce"
EXPECTED_CANDIDATE_SHA = "92fea67dc128d28a6c95e91faaeb21c8632547d23b8baace57cf904f3df3a40c"
EXPECTED_CHECKSUM = "26D7"
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


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha256(data)}


def require(path: Path, *, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {rel(path)}")
    if expected_sha is not None and sha_path(path) != expected_sha:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def atomic_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    with source.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, target)


def atomic_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def checksum_info(rom: bytes) -> dict[str, Any]:
    stored = int.from_bytes(rom[-2:], "little")
    computed = sum(rom[:-2]) & 0xFFFF
    return {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "valid": stored == computed}


def verify_payload(rom: bytes) -> dict[str, Any]:
    original = ORIGINAL.read_bytes()
    sb = stock_base(rom)
    osb = stock_base(original)
    terrain = rom[sb + TERRAIN_START : sb + TERRAIN_END]
    pristine = original[osb + TERRAIN_START : osb + TERRAIN_END]
    if terrain != pristine:
        raise PromotionError("terrain descriptor table is not Original-exact")
    targets = {
        "abao": far_target(terrain[0:TERRAIN_STRIDE]),
        "space": far_target(terrain[3 * TERRAIN_STRIDE : 4 * TERRAIN_STRIDE]),
    }
    if targets != {"abao": 0x75E58C, "space": 0x75E59A}:
        raise PromotionError(f"terrain targets drifted: {targets}")

    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    rendered = {key: record_text(rom, dictionary, tbl, logical) for key, logical in targets.items()}
    if rendered != {"abao": "아・바오아・쿠", "space": "우주"}:
        raise PromotionError(f"terrain render drifted: {rendered}")

    payload, term = read_record(rom, DIALOGUE_ABS)
    if payload != DIALOGUE_AFTER or term != DIALOGUE_TERM:
        raise PromotionError("62663E payload/terminator drifted")
    if original_unit_kinds(payload[3:]) != ["dict", "dict"]:
        raise PromotionError("62663E no longer has native two-token body")
    if dictionary.raw_entry(OU_WRAPPER_SLOT) != bytes.fromhex("F0FD"):
        raise PromotionError("08A6 wrapper drifted")
    text = dictionary.expand(payload[3:], tbl)
    if text != "오우！！":
        raise PromotionError(f"62663E render drifted: {text!r}")

    secondary = rom[sb + OU_SECOND_CONSUMER : sb + OU_SECOND_CONSUMER + 2]
    if secondary != bytes.fromhex("F8A6"):
        raise PromotionError("secondary F8A6 consumer drifted")

    neighbors: dict[str, Any] = {}
    for logical in NATIVE_TWO_TOKEN_NEIGHBORS:
        p, t = read_record(rom, logical)
        if original_unit_kinds(p[3:]) != ["dict", "dict"]:
            raise PromotionError(f"neighbor native-two-token contract lost at {logical:06X}")
        neighbors[f"{logical:06X}"] = {"payload_hex": p.hex().upper(), "terminator": f"{t:06X}"}

    return {
        "terrain": {"range": f"{TERRAIN_START:06X}-{TERRAIN_END - 1:06X}", "targets": {k: f"{v:06X}" for k, v in targets.items()}, "rendered": rendered},
        "gakehau": {"abs": f"{DIALOGUE_ABS:06X}", "payload_hex": payload.hex().upper(), "terminator": f"{term:06X}", "wrapper_raw": dictionary.raw_entry(OU_WRAPPER_SLOT).hex().upper(), "render": text, "neighbors": neighbors},
    }


def run_false_segptr(target: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "scan_false_segptr_writes.py"), "--target", str(target), "--out", str(POST_FALSE)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PromotionError("false-segptr scan failed: " + (completed.stderr or completed.stdout)[-1000:])
    report = json.loads(POST_FALSE.read_text(encoding="utf-8"))
    if report.get("ok") is not True or int(report.get("sites_found", -1)) != 0:
        raise PromotionError(f"false-segptr scan not clean: {report.get('sites_found')}")
    return {"ok": True, "sites_found": 0, "ext3_token_prefixes_ignored": int(report.get("ext3_token_prefixes_ignored") or 0), "report": identity(POST_FALSE)}


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
        raise PromotionError("xdelta rebuild failed: " + (completed.stderr or completed.stdout)[-1000:])
    meta_path = ROOT / "out/dist/monoeye_ko_expanded_xdelta.json"
    patch = ROOT / "out/dist/monoeye_ko_expanded.xdelta"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    result_sha = str(((meta.get("main_tip") or {}).get("sha256") or "")).lower()
    roundtrip = meta.get("roundtrip_matches_main_tip") is True
    if result_sha != EXPECTED_CANDIDATE_SHA or not roundtrip:
        raise PromotionError(f"xdelta verification failed: sha={result_sha} roundtrip={roundtrip}")
    return {"ok": True, "path": rel(patch), "size": patch.stat().st_size, "sha256": sha_path(patch), "metadata": rel(meta_path), "result_sha256": result_sha, "roundtrip_matches_main_tip": roundtrip}


def main() -> int:
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, expected_sha=EXPECTED_CANDIDATE_SHA)
    require(SAVE, size=SAVE_SIZE)
    require(BUILD_REPORT)
    require(AUDIT_REPORT)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    if build.get("ok") is not True:
        raise PromotionError("builder report is not ok")
    # The latest audit may show candidate_save_exact_live=false because the user
    # played the paired candidate SaveRAM. ROM-only checks must still be clean.
    rom_audit_checks = {k: v for k, v in (audit.get("checks") or {}).items() if k != "candidate_save_exact_live"}
    if not rom_audit_checks or not all(value is True for value in rom_audit_checks.values()):
        raise PromotionError(f"candidate ROM audit has failures: {rom_audit_checks}")
    if str(((build.get("parent") or {}).get("sha256") or "")).lower() != EXPECTED_TIP_SHA:
        raise PromotionError("build parent SHA drifted")
    if str(((build.get("candidate") or {}).get("sha256") or "")).lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build candidate SHA drifted")
    if str(((build.get("candidate") or {}).get("checksum") or "")).upper() != EXPECTED_CHECKSUM:
        raise PromotionError("build checksum drifted")

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    info = checksum_info(candidate)
    if not info["valid"] or info["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {info}")
    proof_before = verify_payload(candidate)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_terrain_gakehau_selective"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if sha_path(backup_rom) != EXPECTED_TIP_SHA:
        raise PromotionError("rollback backup mismatch")

    try:
        atomic_copy(CANDIDATE, TIP)
        promoted = TIP.read_bytes()
        if sha256(promoted) != EXPECTED_CANDIDATE_SHA:
            raise PromotionError("promoted TIP does not match tested candidate")
        proof_after = verify_payload(promoted)
        false_segptr = run_false_segptr(TIP)
        post_checks = {
            "tip_matches_tested_candidate": sha256(promoted) == EXPECTED_CANDIDATE_SHA,
            "checksum_valid": checksum_info(promoted)["valid"] and checksum_info(promoted)["stored"] == EXPECTED_CHECKSUM,
            "terrain_structure_exact": proof_after["terrain"]["targets"] == {"abao": "75E58C", "space": "75E59A"},
            "terrain_render_exact": proof_after["terrain"]["rendered"] == {"abao": "아・바오아・쿠", "space": "우주"},
            "gakehau_native_wrapper_exact": proof_after["gakehau"]["payload_hex"] == DIALOGUE_AFTER.hex().upper() and proof_after["gakehau"]["wrapper_raw"] == "F0FD",
            "gakehau_render_exact": proof_after["gakehau"]["render"] == "오우！！",
            "false_segptr_clean": false_segptr["ok"] is True,
            "rollback_rom_exact": sha_path(backup_rom) == EXPECTED_TIP_SHA,
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
        }
        if not all(post_checks.values()):
            raise PromotionError(f"post-promotion checks failed: {post_checks}")
    except Exception:
        atomic_bytes(TIP, parent)
        raise

    xdelta = rebuild_xdelta()
    promoted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_terrain_gakehau_selective_candidate.py",
        "ok": True,
        "tip": identity(TIP, promoted),
        "tip_checksum": checksum_info(promoted),
        "rollback_rom": identity(backup_rom),
        "proof": proof_after,
        "false_segptr": false_segptr,
        "checks": post_checks,
        "user_runtime_validation": {
            "terrain_space_abaoaqu": "confirmed normal",
            "dialogue_62663E_gakehau": "confirmed no unwanted hiragana/control leakage",
        },
        "save_note": "candidate SaveRAM changed during runtime validation and is intentionally ignored; live main SaveRAM remained byte-exact",
    }
    atomic_json(POST_AUDIT, post)

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_terrain_gakehau_selective_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": promoted_at,
        "authorization": "사용자가 terrain_gakehau_selective_candidate 실측 확인 후 메인 TIP 승격을 요청함",
        "old_tip": {"path": rel(TIP), "size": ROM_SIZE, "sha256": EXPECTED_TIP_SHA},
        "new_tip": identity(TIP, promoted),
        "new_tip_checksum": checksum_info(promoted),
        "backup_rom": identity(backup_rom),
        "tested_candidate": identity(CANDIDATE, candidate),
        "runtime_user_validation": {
            "terrain": "우주 / 아・바오아・쿠 정상 표시 확인",
            "gakehau": "오우！！ 뒤 がけはう 또는 유사 제어문 노출 없음 확인",
        },
        "selected_changes_only": build.get("selected_changes_only"),
        "proof": proof_after,
        "false_segptr": false_segptr,
        "xdelta": xdelta,
        "postpromotion_audit": identity(POST_AUDIT),
        "main_saveram_policy": "ROM-only promotion; live main SaveRAM remained byte-identical",
        "live_saveram": identity(SAVE, save_before),
        "candidate_saveram_note": "runtime-tested candidate SaveRAM is not promoted and may differ from live main SaveRAM",
    }
    atomic_json(PROMOTION_REPORT, promotion)
    print(json.dumps({
        "ok": True,
        "old_tip_sha256": EXPECTED_TIP_SHA,
        "new_tip": promotion["new_tip"],
        "checksum": promotion["new_tip_checksum"],
        "proof": promotion["proof"],
        "xdelta": xdelta,
        "live_saveram_unchanged": True,
        "rollback": rel(backup_rom),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
