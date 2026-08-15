#!/usr/bin/env python3
"""Promote the title-menu Bitmap + Korean copyright candidate to the main TIP.

ROM-only. Live SaveRAM is never replaced. Old savestates restore previous VRAM,
so the title must be re-entered from a cold boot or reset after promotion.
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

from build_id_command_plaques_ko_candidate import decode_grid  # noqa: E402


PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_DIR = PATCH / "title_menu_bitmap_copyright_candidate"
CANDIDATE = (
    CANDIDATE_DIR / "monoeye_ko_expanded_title_menu_bitmap_copyright_test.wsc"
)
BUILD_REPORT = CANDIDATE_DIR / "title_menu_bitmap_copyright_report.json"
AUDIT_REPORT = CANDIDATE_DIR / "title_menu_bitmap_copyright_audit.json"
COPYRIGHT_SPEC = ROOT / "data/title_copyright_ko.json"
PROMOTION_REPORT = PATCH / "title_menu_bitmap_copyright_promotion_report.json"
POST_AUDIT = PATCH / "title_menu_bitmap_copyright_postpromotion_audit.json"
POST_FALSE = PATCH / "title_menu_bitmap_copyright_postpromotion_false_segptr.json"

EXPECTED_TIP_SHA = "c0a2b429e9162c9648c21fbbab0dcd28b70c0cdcc0966b11407cef2db54b2631"
EXPECTED_CANDIDATE_SHA = (
    "0ff2bc7398c5b677d02bc1d81df21d12dc7731d2d16d62c3cc7cd25b1c74ca11"
)
EXPECTED_CHECKSUM = "B5F5"
EXPECTED_DIFF_BYTES = 4031
EXPECTED_PLATE_BYTES = 3599
EXPECTED_COPYRIGHT_BYTES = 430
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STOCK_BASE = 0x800000
MENU_LO = 0x720080
MENU_HI = 0x7248FF
COPYRIGHT_LO = 0x5519DC
COPYRIGHT_BYTES = 1792


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


def require(path: Path, *, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {rel(path)}")
    if expected_sha is not None and sha_path(path) != expected_sha:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    with source.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, target)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def checksum_info(rom: bytes) -> dict[str, Any]:
    stored = int.from_bytes(rom[-2:], "little")
    computed = sum(rom[:-2]) & 0xFFFF
    return {
        "stored": f"{stored:04X}",
        "computed": f"{computed:04X}",
        "valid": stored == computed,
    }


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise PromotionError("ROM size mismatch")
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


def prove_payload(parent: bytes, candidate: bytes, spec: dict[str, Any]) -> dict[str, Any]:
    diffs = changed_offsets(parent, candidate)
    allowed = set(range(STOCK_BASE + MENU_LO, STOCK_BASE + MENU_HI + 1))
    allowed.update(
        range(STOCK_BASE + COPYRIGHT_LO, STOCK_BASE + COPYRIGHT_LO + COPYRIGHT_BYTES)
    )
    allowed.update((ROM_SIZE - 2, ROM_SIZE - 1))
    outside = [off for off in diffs if off not in allowed]
    if outside:
        raise PromotionError(f"unexpected diffs: {[f'{off:06X}' for off in outside[:8]]}")
    plate_n = sum(1 for off in diffs if STOCK_BASE + MENU_LO <= off <= STOCK_BASE + MENU_HI)
    copy_n = sum(
        1
        for off in diffs
        if STOCK_BASE + COPYRIGHT_LO <= off < STOCK_BASE + COPYRIGHT_LO + COPYRIGHT_BYTES
    )
    if len(diffs) != EXPECTED_DIFF_BYTES:
        raise PromotionError(f"diff byte count drifted: {len(diffs)}")
    if plate_n != EXPECTED_PLATE_BYTES or copy_n != EXPECTED_COPYRIGHT_BYTES:
        raise PromotionError(f"range byte counts drifted: plates={plate_n} copyright={copy_n}")
    info = checksum_info(candidate)
    if not info["valid"] or info["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {info}")

    x0 = int(spec["keep_first_copyright_x1"])
    x1 = int(spec["keep_english_x0"])
    parent_blob = parent[
        STOCK_BASE + COPYRIGHT_LO : STOCK_BASE + COPYRIGHT_LO + COPYRIGHT_BYTES
    ]
    cand_blob = candidate[
        STOCK_BASE + COPYRIGHT_LO : STOCK_BASE + COPYRIGHT_LO + COPYRIGHT_BYTES
    ]
    before = decode_grid(parent_blob, 28, 2)
    after = decode_grid(cand_blob, 28, 2)
    for y in range(16):
        for x in list(range(0, x0)) + list(range(x1, 224)):
            if before[y][x] != after[y][x]:
                raise PromotionError(f"reserved copyright pixel changed at {x},{y}")
    jp_changed = sum(
        1 for y in range(16) for x in range(x0, x1) if before[y][x] != after[y][x]
    )
    if jp_changed == 0:
        raise PromotionError("Japanese copyright zone did not change")
    return {
        "diff_bytes": len(diffs),
        "menu_plate_bytes": plate_n,
        "copyright_bytes": copy_n,
        "checksum": info,
        "keep_first_copyright": True,
        "keep_english": True,
        "copyright_jp_pixels_changed": jp_changed,
        "outside_declared_ranges": 0,
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
    require(CANDIDATE, size=ROM_SIZE, expected_sha=EXPECTED_CANDIDATE_SHA)
    require(SAVE, size=SAVE_SIZE)
    require(BUILD_REPORT)
    require(AUDIT_REPORT)
    require(COPYRIGHT_SPEC)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    spec = json.loads(COPYRIGHT_SPEC.read_text(encoding="utf-8"))
    if build.get("ok") is not True or audit.get("ok") is not True:
        raise PromotionError("candidate report/audit is not ok")
    if str((build.get("parent") or {}).get("sha256") or "").lower() != EXPECTED_TIP_SHA:
        raise PromotionError("build parent SHA drifted")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build candidate SHA drifted")
    if str((audit.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("audit candidate SHA drifted")
    if build.get("checksum") != EXPECTED_CHECKSUM:
        raise PromotionError("build checksum drifted")
    if audit.get("copyright_font") != "Galmuri9Bitmap-Regular-2.40.3.ttf":
        raise PromotionError("copyright font drifted")
    if audit.get("copyright_stroke_height") != 9:
        raise PromotionError("copyright stroke height drifted")

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    proof = prove_payload(parent, candidate, spec)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_title_menu_bitmap_copyright"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if sha_path(backup_rom) != EXPECTED_TIP_SHA:
        raise PromotionError("rollback backup mismatch")

    try:
        atomic_copy(CANDIDATE, TIP)
        promoted = TIP.read_bytes()
        if sha256(promoted) != EXPECTED_CANDIDATE_SHA:
            raise PromotionError("promoted TIP does not match candidate")
        post_proof = prove_payload(parent, promoted, spec)
        false_segptr = run_false_segptr(TIP)
        post_checks = {
            "tip_matches_candidate": sha256(promoted) == EXPECTED_CANDIDATE_SHA,
            "checksum_valid": checksum_info(promoted)["valid"]
            and checksum_info(promoted)["stored"] == EXPECTED_CHECKSUM,
            "payload_proof": all(
                post_proof[key] == proof[key]
                for key in (
                    "diff_bytes",
                    "keep_first_copyright",
                    "keep_english",
                    "outside_declared_ranges",
                )
            ),
            "false_segptr_clean": false_segptr["ok"] is True,
            "rollback_rom_exact": sha_path(backup_rom) == EXPECTED_TIP_SHA,
            "live_saveram_unchanged": SAVE.read_bytes() == save_before,
        }
        if not all(post_checks.values()):
            raise PromotionError(f"post-promotion audit failed: {post_checks}")
    except Exception:
        atomic_bytes(TIP, parent)
        raise

    xdelta = rebuild_xdelta()
    if xdelta["result_sha256"] != EXPECTED_CANDIDATE_SHA:
        raise PromotionError(f"xdelta result SHA drifted: {xdelta['result_sha256']}")
    if xdelta.get("roundtrip_matches_main_tip") is not True:
        raise PromotionError("xdelta round-trip did not match the new TIP")

    promoted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    post = {
        "schema_version": 1,
        "generated_by": "tools/promote_title_menu_bitmap_copyright_candidate.py",
        "ok": True,
        "tip": identity(TIP, promoted),
        "tip_checksum": checksum_info(promoted),
        "rollback_rom": identity(backup_rom),
        "proof": post_proof,
        "false_segptr": false_segptr,
        "checks": post_checks,
    }
    atomic_json(POST_AUDIT, post)

    build["status"] = "promoted_to_current_main"
    build["promotion"] = "promoted"
    build["published"] = True
    build["promoted_at"] = promoted_at
    atomic_json(BUILD_REPORT, build)
    audit["status"] = "promoted_to_current_main"
    audit["promotion"] = "promoted"
    audit["promoted_at"] = promoted_at
    atomic_json(AUDIT_REPORT, audit)

    promotion = {
        "schema_version": 1,
        "generated_by": "tools/promote_title_menu_bitmap_copyright_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": promoted_at,
        "authorization": (
            "사용자가 타이틀 메뉴 Galmuri11 Bitmap + 저작권 Galmuri9 테스트 ROM의 "
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
        "candidate": identity(CANDIDATE, candidate),
        "proof": post_proof,
        "false_segptr": false_segptr,
        "xdelta": xdelta,
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
                "proof": post_proof,
                "xdelta": xdelta,
                "live_saveram_unchanged": True,
                "rollback": rel(backup_rom),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"PROMOTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
