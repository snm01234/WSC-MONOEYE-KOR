#!/usr/bin/env python3
"""Promote the bank69 7-byte resource restore candidate to the current main TIP.

This is intentionally NOT a release/version bump. It only:
- verifies the current v1.0.1 main TIP and tested candidate identities,
- verifies the seven requested bytes equal the Japanese original,
- verifies candidate delta is exactly seven payload bytes plus ROM checksum,
- preserves live SaveRAM byte-exactly,
- creates a timestamped rollback copy of the old main TIP,
- atomically replaces out/patch/monoeye_ko_expanded.wsc,
- writes a promotion report and post-promotion false-segptr report.

VERSION, out/dist, release notes, xdelta artifacts, and tags are not modified.
Default mode is read-only verification. Pass --apply to perform promotion.
"""
from __future__ import annotations

import argparse
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
TIP = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "bank69_7byte_resource_restore_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
VERSION = ROOT / "VERSION"
PRE_FALSE_SEGPTR = PATCH / "bank69_7byte_resource_restore_candidate_false_segptr.json"
PROMOTION_REPORT = PATCH / "bank69_7byte_resource_restore_promotion_report.json"
POST_FALSE_SEGPTR = PATCH / "bank69_7byte_resource_restore_postpromotion_false_segptr.json"

EXPECTED_TIP_SHA = "c8ee51be9c5e33dfd88e7565453ff031a931aaf4948d9cd4aee35a7ec6892e86"
EXPECTED_CANDIDATE_SHA = "4033c2bdc8f9d627beabaae65e69c43010f0523448ba30ec08f610529e0feb33"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXPECTED_VERSION = "1.0.1"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STOCK_BASE = 0x800000
CHECKSUM_OFFSETS = {0xFFFFFE, 0xFFFFFF}
RESTORES = {
    0x696A7E: (0xF8, 0x7F),
    0x696B8E: (0xF7, 0x27),
    0x696C6F: (0xF8, 0x7F),
    0x696C73: (0xF8, 0x7F),
    0x696C77: (0xF8, 0x7F),
    0x696C7B: (0xF8, 0x7F),
    0x696C7F: (0xF8, 0x7F),
}


class PromotionError(RuntimeError):
    pass


def sha_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha_bytes(data)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {rel(path)}")
    if sha is not None and sha_path(path) != sha:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def ws_checksum(data: bytes) -> dict:
    stored = int.from_bytes(data[-2:], "little")
    computed = sum(data[:-2]) & 0xFFFF
    return {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "valid": stored == computed}


def atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    with src.open("rb") as i, tmp.open("wb") as o:
        shutil.copyfileobj(i, o, 1024 * 1024)
        o.flush()
        os.fsync(o.fileno())
    os.replace(tmp, dst)


def atomic_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def verify_inputs() -> dict:
    require(TIP, size=ROM_SIZE, sha=EXPECTED_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE_SHA)
    require(ORIGINAL, sha=EXPECTED_ORIGINAL_SHA)
    require(SAVE, size=SAVE_SIZE)
    require(VERSION)
    require(PRE_FALSE_SEGPTR)

    version = VERSION.read_text(encoding="utf-8").strip()
    if version != EXPECTED_VERSION:
        raise PromotionError(f"VERSION drift: {version!r} != {EXPECTED_VERSION!r}")

    false_segptr = json.loads(PRE_FALSE_SEGPTR.read_text(encoding="utf-8"))
    if false_segptr.get("ok") is not True or int(false_segptr.get("sites_found", -1)) != 0:
        raise PromotionError("candidate false segmented-pointer audit is not clean")

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(original) != 8_388_608:
        raise PromotionError("unexpected original ROM size")

    checksum = ws_checksum(candidate)
    if not checksum["valid"] or checksum["stored"] != "7AE7":
        raise PromotionError(f"candidate checksum drift: {checksum}")

    restore_rows = []
    expected_file_offsets = set()
    for logical, (before, after) in RESTORES.items():
        file_off = STOCK_BASE + logical
        expected_file_offsets.add(file_off)
        if parent[file_off] != before:
            raise PromotionError(f"parent site drift at {logical:06X}: {parent[file_off]:02X} != {before:02X}")
        if candidate[file_off] != after:
            raise PromotionError(f"candidate site drift at {logical:06X}: {candidate[file_off]:02X} != {after:02X}")
        if original[logical] != after:
            raise PromotionError(f"candidate is not exact to original at {logical:06X}")
        restore_rows.append({
            "site": f"{logical >> 16:02X}:{logical & 0xFFFF:04X}",
            "physical": f"{file_off:08X}",
            "parent": f"{before:02X}",
            "candidate": f"{after:02X}",
            "original": f"{original[logical]:02X}",
            "exact_original_restore": True,
        })

    all_diff = [i for i, (a, b) in enumerate(zip(parent, candidate)) if a != b]
    nonchecksum = [i for i in all_diff if i not in CHECKSUM_OFFSETS]
    checksum_diff = [i for i in all_diff if i in CHECKSUM_OFFSETS]
    if set(nonchecksum) != expected_file_offsets:
        raise PromotionError(
            "candidate delta is not exactly the seven requested bytes: "
            f"got {[f'{x:08X}' for x in nonchecksum]}"
        )
    if set(checksum_diff) != CHECKSUM_OFFSETS:
        raise PromotionError(f"unexpected checksum delta: {[f'{x:08X}' for x in checksum_diff]}")

    return {
        "version": version,
        "parent": identity(TIP, parent),
        "candidate": identity(CANDIDATE, candidate),
        "original": identity(ORIGINAL, original),
        "candidate_checksum": checksum,
        "restores": restore_rows,
        "delta": {
            "total_changed_bytes": len(all_diff),
            "nonchecksum_changed_bytes": len(nonchecksum),
            "checksum_changed_bytes": len(checksum_diff),
            "unexpected_nonchecksum_changed_bytes": 0,
        },
        "pre_false_segptr_sites": 0,
        "version_bump": False,
        "release_artifacts_modified": False,
    }


def run_post_false_segptr() -> dict:
    cp = subprocess.run(
        [sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"),
         "--target", str(TIP), "--out", str(POST_FALSE_SEGPTR)],
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0:
        raise PromotionError(f"postpromotion false-segptr audit failed:\n{(cp.stderr or cp.stdout)[-3000:]}")
    obj = json.loads(POST_FALSE_SEGPTR.read_text(encoding="utf-8"))
    if obj.get("ok") is not True or int(obj.get("sites_found", -1)) != 0:
        raise PromotionError("postpromotion false-segptr audit is not clean")
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="perform the ROM-only main TIP promotion")
    args = ap.parse_args()

    proof = verify_inputs()
    save_before = SAVE.read_bytes()
    version_before = VERSION.read_bytes()

    if not args.apply:
        print(json.dumps({
            "ok": True,
            "mode": "check_only",
            "ready_to_promote": True,
            "proof": proof,
            "note": "Re-run with --apply to promote. VERSION/out/dist remain untouched.",
        }, ensure_ascii=False, indent=2))
        return 0

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_bank69_7byte_resource_restore"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if sha_path(backup_rom) != EXPECTED_TIP_SHA:
        raise PromotionError("rollback ROM backup mismatch")

    try:
        atomic_copy(CANDIDATE, TIP)
        promoted = TIP.read_bytes()
        if sha_bytes(promoted) != EXPECTED_CANDIDATE_SHA or promoted != candidate:
            raise PromotionError("promoted main TIP does not match tested candidate")
        if not ws_checksum(promoted)["valid"]:
            raise PromotionError("promoted TIP checksum invalid")
        if SAVE.read_bytes() != save_before:
            raise PromotionError("live SaveRAM changed during ROM promotion")
        if VERSION.read_bytes() != version_before or VERSION.read_text(encoding="utf-8").strip() != EXPECTED_VERSION:
            raise PromotionError("VERSION changed during non-versioned promotion")

        post_false = run_post_false_segptr()
        if SAVE.read_bytes() != save_before:
            raise PromotionError("live SaveRAM changed during postpromotion audit")

        report = {
            "schema_version": 1,
            "generated_by": "tools/promote_bank69_7byte_resource_restore.py",
            "ok": True,
            "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "authorization": "사용자가 bank69 고립 7바이트 원본 복구 후보를 메인TIP에 반영하되 버전은 올리지 말고 기록만 남기도록 요청함",
            "release_version_unchanged": EXPECTED_VERSION,
            "version_bump": False,
            "dist_release_rebuild": False,
            "old_tip": proof["parent"],
            "new_tip": identity(TIP, promoted),
            "new_tip_checksum": ws_checksum(promoted),
            "tested_candidate": proof["candidate"],
            "exact_original_restores": proof["restores"],
            "delta": proof["delta"],
            "backup_rom": identity(backup_rom),
            "live_saveram": identity(SAVE, save_before),
            "live_saveram_unchanged": True,
            "post_false_segptr": {
                "path": rel(POST_FALSE_SEGPTR),
                "sites_found": int(post_false.get("sites_found", -1)),
                "ok": post_false.get("ok") is True,
            },
            "not_modified": ["VERSION", "out/dist/*", "release notes", "git tags"],
        }
        atomic_json(PROMOTION_REPORT, report)
    except Exception:
        atomic_bytes(TIP, parent)
        raise

    print(json.dumps({
        "ok": True,
        "old_tip_sha256": EXPECTED_TIP_SHA,
        "new_tip_sha256": EXPECTED_CANDIDATE_SHA,
        "version": EXPECTED_VERSION,
        "version_bump": False,
        "live_saveram_unchanged": True,
        "rollback": rel(backup_rom),
        "report": rel(PROMOTION_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
