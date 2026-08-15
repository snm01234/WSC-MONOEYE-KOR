#!/usr/bin/env python3
"""Promote event_cleanup_followup_guard_candidate as v1.0.1 hotfix.

This is a ROM-only promotion. The live main SaveRAM is preserved byte-exactly;
the runtime-tested candidate SaveRAM is never copied over the live save.

The release artifact is versioned explicitly:
  monoeye_ko_expanded_v1.0.1_hotfix.xdelta
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

from monoeye_rom import read_encoded_z_safe, stock_base, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
DIST = ROOT / "out/dist"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = PATCH / "event_cleanup_followup_guard_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BUILD_REPORT = PATCH / "event_cleanup_followup_guard_report.json"
AUDIT_REPORT = PATCH / "event_cleanup_followup_guard_audit.json"
FALSE_LEAD_REPORT = PATCH / "event_cleanup_followup_guard_false_lead_recurrence.json"
FALSE_SEGPTR_REPORT = PATCH / "event_cleanup_followup_guard_false_segptr.json"
PROMOTION_REPORT = PATCH / "event_cleanup_hotfix_1_0_1_promotion_report.json"
POST_AUDIT = PATCH / "event_cleanup_hotfix_1_0_1_postpromotion_audit.json"
POST_FALSE_LEAD = PATCH / "event_cleanup_hotfix_1_0_1_postpromotion_false_lead.json"
POST_FALSE_SEGPTR = PATCH / "event_cleanup_hotfix_1_0_1_postpromotion_false_segptr.json"

RELEASE_VERSION = "1.0.1"
RELEASE_TYPE = "hotfix"
BASE_VERSION = "1.0.0"
RELEASE_NAME = "monoeye_ko_expanded_v1.0.1_hotfix"

EXPECTED_TIP_SHA = "d7543ad4a62d9e7a9687583e85005dc4ca137e6fa62238eb70e58492248985c9"
EXPECTED_CANDIDATE_SHA = "c8ee51be9c5e33dfd88e7565453ff031a931aaf4948d9cd4aee35a7ec6892e86"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXPECTED_CHECKSUM = "7E8D"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

EVENT_FALSE_BYTES = (0x651C25, 0x6609DF, 0x6609F7, 0x6609FC, 0x6739DB, 0x6769C3)
ORPHANS = (0x594715, 0x60F3A6, 0x6106EF, 0x61165D, 0x638F52)
FALSE_LEADS = {
    0x5D3122: bytes.fromhex("E7BAF50D01010101010101"),
    0x5D313B: bytes.fromhex("E7BAF50D01010101010101"),
}
TRUE_METADATA = {
    0x5E6586: bytes.fromhex("90E518D4E101010101"),
    0x5E65A7: bytes.fromhex("90E518D4E201010101"),
}
GATO_ADDR = 0x5D1E3E
GATO_EXPECTED = bytes.fromhex("0FF65A") + b"\x01" * 14

LEGACY_DIST = (
    DIST / "monoeye_ko_expanded.xdelta",
    DIST / "monoeye_ko_expanded_xdelta.json",
    DIST / "monoeye_ko_expanded_XDELTA_README.md",
    DIST / "SHA256SUMS.txt",
)


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


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha_bytes(data)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {rel(path)}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {rel(path)}")
    if sha is not None and sha_path(path) != sha:
        raise PromotionError(f"SHA-256 drift: {rel(path)}")


def atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    with src.open("rb") as i, tmp.open("wb") as o:
        shutil.copyfileobj(i, o, 1024 * 1024)
        o.flush()
        os.fsync(o.fileno())
    os.replace(tmp, dst)


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def checksum_info(data: bytes) -> dict[str, Any]:
    stored = int(ws_header(data)["checksum"])
    computed = sum(data[:-2]) & 0xFFFF
    return {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "valid": stored == computed}


def zpayload(rom: bytes, logical: int, *, max_len: int = 128) -> bytes:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=max_len)
    if got is None:
        raise PromotionError(f"unreadable record: {logical:06X}")
    return bytes(got[0])


def verify_candidate(rom: bytes) -> dict[str, Any]:
    if len(rom) != ROM_SIZE:
        raise PromotionError("candidate size drift")
    if sha_bytes(rom) != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("candidate SHA drift")
    checksum = checksum_info(rom)
    if not checksum["valid"] or checksum["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum drift: {checksum}")

    original = ORIGINAL.read_bytes()
    so = stock_base(original)
    sc = stock_base(rom)
    event = {}
    for logical in EVENT_FALSE_BYTES:
        before = original[so + logical]
        after = rom[sc + logical]
        if before != 0x09 or after != 0x09:
            raise PromotionError(f"event structural restore missing at {logical:06X}")
        event[f"{logical:06X}"] = "09"

    orphans = {}
    for logical in ORPHANS:
        payload = rom[sc + logical : sc + logical + 4]
        if payload != b"\x01\x00\x17\x28":
            raise PromotionError(f"orphan-kana blanking drift at {logical:06X}: {payload.hex()}")
        orphans[f"{logical:06X}"] = payload.hex().upper()

    gato = zpayload(rom, GATO_ADDR)
    if gato != GATO_EXPECTED:
        raise PromotionError(f"Gato record drift: {gato.hex().upper()}")

    false_leads = {}
    for logical, expected in FALSE_LEADS.items():
        payload = zpayload(rom, logical)
        if payload != expected:
            raise PromotionError(f"false-lead fix drift at {logical:06X}")
        false_leads[f"{logical:06X}"] = payload.hex().upper()

    metadata = {}
    for logical, expected in TRUE_METADATA.items():
        payload = zpayload(rom, logical)
        if payload != expected:
            raise PromotionError(f"metadata repair drift at {logical:06X}")
        metadata[f"{logical:06X}"] = payload.hex().upper()

    return {
        "checksum": checksum,
        "event_structural_restores": event,
        "orphan_kana_blanked": orphans,
        "gato_5D1E3E": gato.hex().upper(),
        "false_lead_text_repairs": false_leads,
        "metadata_repairs": metadata,
    }


def run_tool(args: list[str], *, label: str) -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cp = subprocess.run(args, cwd=ROOT, env=env, check=False, capture_output=True, text=True)
    if cp.returncode != 0:
        raise PromotionError(f"{label} failed:\n{(cp.stderr or cp.stdout)[-4000:]}")


def rebuild_versioned_xdelta() -> dict[str, Any]:
    run_tool(
        [
            sys.executable,
            str(ROOT / "tools/make_main_tip_xdelta.py"),
            "--original", str(ORIGINAL),
            "--tip", str(TIP),
            "--out-dir", str(DIST),
            "--name", RELEASE_NAME,
            "--xdelta3", str(ROOT / "tools/vendor/xdelta3.exe"),
        ],
        label="versioned xdelta build",
    )
    xdelta = DIST / f"{RELEASE_NAME}.xdelta"
    meta_path = DIST / f"{RELEASE_NAME}_xdelta.json"
    readme = DIST / f"{RELEASE_NAME}_XDELTA_README.md"
    for path in (xdelta, meta_path, readme):
        require(path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if ((meta.get("main_tip") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("versioned xdelta metadata main TIP SHA mismatch")
    if meta.get("roundtrip_matches_main_tip") is not True:
        raise PromotionError("versioned xdelta round-trip failed")
    release = meta.get("release") or {}
    if release != {"version": RELEASE_VERSION, "type": RELEASE_TYPE, "base_version": BASE_VERSION}:
        raise PromotionError(f"release metadata drift: {release}")
    return {
        "xdelta": identity(xdelta),
        "metadata": identity(meta_path),
        "readme": identity(readme),
        "roundtrip_matches_main_tip": True,
    }


def write_sha256s(xdelta_path: Path) -> Path:
    sums = DIST / f"SHA256SUMS_v{RELEASE_VERSION}_hotfix.txt"
    text = "\n".join([
        "# SD Gundam G Generation: Mono-Eye Gundams Korean Patch",
        f"# Release: v{RELEASE_VERSION} ({RELEASE_TYPE}), based on v{BASE_VERSION}",
        "",
        "# Legally owned Japanese original source ROM (not distributed)",
        f"{EXPECTED_ORIGINAL_SHA}  {ORIGINAL.name}",
        "",
        "# Public patch asset",
        f"{sha_path(xdelta_path)}  {xdelta_path.name}",
        "",
        "# Expected patched ROM (not distributed)",
        f"{EXPECTED_CANDIDATE_SHA}  {TIP.name}",
        "",
    ])
    atomic_text(sums, text)
    return sums


def main() -> int:
    require(TIP, size=ROM_SIZE, sha=EXPECTED_TIP_SHA)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE_SHA)
    require(ORIGINAL, sha=EXPECTED_ORIGINAL_SHA)
    require(SAVE, size=SAVE_SIZE)
    for path in (BUILD_REPORT, AUDIT_REPORT, FALSE_LEAD_REPORT, FALSE_SEGPTR_REPORT):
        require(path)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    false_lead = json.loads(FALSE_LEAD_REPORT.read_text(encoding="utf-8"))
    false_segptr = json.loads(FALSE_SEGPTR_REPORT.read_text(encoding="utf-8"))
    if build.get("ok") is not True:
        raise PromotionError("candidate builder report not ok")
    if audit.get("ok") is not True or not all((audit.get("checks") or {}).values()):
        raise PromotionError("candidate independent audit not fully clean")
    if false_lead.get("ok") is not True or int(((false_lead.get("counts") or {}).get("reintroduced", -1))) != 0:
        raise PromotionError("false-lead recurrence audit not clean")
    if false_segptr.get("ok") is not True or int(false_segptr.get("sites_found", -1)) != 0:
        raise PromotionError("false segmented-pointer audit not clean")

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    proof_before = verify_candidate(candidate)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_v1.0.1_hotfix"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_rom = backup_dir / TIP.name
    shutil.copy2(TIP, backup_rom)
    if sha_path(backup_rom) != EXPECTED_TIP_SHA:
        raise PromotionError("rollback ROM backup mismatch")

    dist_backup = backup_dir / "dist_v1.0.0"
    dist_backup.mkdir(parents=True, exist_ok=True)
    backed_dist = []
    for path in LEGACY_DIST:
        if path.is_file():
            copy = dist_backup / path.name
            shutil.copy2(path, copy)
            backed_dist.append(identity(copy))

    try:
        atomic_copy(CANDIDATE, TIP)
        promoted = TIP.read_bytes()
        if sha_bytes(promoted) != EXPECTED_CANDIDATE_SHA:
            raise PromotionError("promoted main TIP does not match tested candidate")
        proof_after = verify_candidate(promoted)

        run_tool(
            [sys.executable, str(ROOT / "tools/audit_battle_false_lead_recurrence.py"),
             "--target", str(TIP), "--out", str(POST_FALSE_LEAD)],
            label="postpromotion false-lead audit",
        )
        post_false_lead = json.loads(POST_FALSE_LEAD.read_text(encoding="utf-8"))
        if post_false_lead.get("ok") is not True:
            raise PromotionError("postpromotion false-lead audit failed")

        run_tool(
            [sys.executable, str(ROOT / "tools/scan_false_segptr_writes.py"),
             "--target", str(TIP), "--out", str(POST_FALSE_SEGPTR)],
            label="postpromotion false-segptr audit",
        )
        post_false_segptr = json.loads(POST_FALSE_SEGPTR.read_text(encoding="utf-8"))
        if post_false_segptr.get("ok") is not True or int(post_false_segptr.get("sites_found", -1)) != 0:
            raise PromotionError("postpromotion false-segptr audit failed")

        if SAVE.read_bytes() != save_before:
            raise PromotionError("live SaveRAM changed during ROM promotion")

        xdelta_info = rebuild_versioned_xdelta()
        xdelta_path = DIST / f"{RELEASE_NAME}.xdelta"
        sums = write_sha256s(xdelta_path)

        post = {
            "schema_version": 1,
            "generated_by": "tools/promote_event_cleanup_hotfix_1_0_1.py",
            "ok": True,
            "release": {"version": RELEASE_VERSION, "type": RELEASE_TYPE, "base_version": BASE_VERSION},
            "tip": identity(TIP, promoted),
            "tip_checksum": checksum_info(promoted),
            "proof": proof_after,
            "runtime_user_validation": {
                "sanc_kingdom_tallgeese3_event_error": "confirmed resolved; event proceeds without 12288/4353 error",
                "gato_5D1E3E": "confirmed Korean line '결국、가치관이 다른 듯하군……' renders normally",
                "karama_6106EF": "confirmed no stray な; next event proceeds normally",
                "green_noah_61165D": "confirmed no stray な; proceeds directly to '……실례합니다.'",
            },
            "post_false_lead": identity(POST_FALSE_LEAD),
            "post_false_segptr": identity(POST_FALSE_SEGPTR),
            "live_saveram": identity(SAVE, save_before),
            "live_saveram_unchanged": True,
        }
        atomic_json(POST_AUDIT, post)

        promotion = {
            "schema_version": 1,
            "generated_by": "tools/promote_event_cleanup_hotfix_1_0_1.py",
            "ok": True,
            "published": True,
            "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "release": {"version": RELEASE_VERSION, "type": RELEASE_TYPE, "base_version": BASE_VERSION},
            "authorization": "사용자가 카라마・포인트 및 그린 노아 실측 정상 확인 후 v1.0.1 hotfix 메인 승격을 요청함",
            "old_tip": {"path": rel(TIP), "size": ROM_SIZE, "sha256": EXPECTED_TIP_SHA},
            "new_tip": identity(TIP, promoted),
            "new_tip_checksum": checksum_info(promoted),
            "tested_candidate": identity(CANDIDATE, candidate),
            "candidate_proof": proof_before,
            "postpromotion_proof": proof_after,
            "backup_rom": identity(backup_rom),
            "backed_up_v1_0_0_dist": backed_dist,
            "xdelta": xdelta_info,
            "sha256s": identity(sums),
            "postpromotion_audit": identity(POST_AUDIT),
            "main_saveram_policy": "ROM-only promotion; live main SaveRAM preserved byte-exactly",
            "live_saveram": identity(SAVE, save_before),
        }
        atomic_json(PROMOTION_REPORT, promotion)
    except Exception:
        atomic_bytes(TIP, parent)
        raise

    print(json.dumps({
        "ok": True,
        "release": promotion["release"],
        "old_tip_sha256": EXPECTED_TIP_SHA.upper(),
        "new_tip": promotion["new_tip"],
        "checksum": promotion["new_tip_checksum"],
        "xdelta": promotion["xdelta"]["xdelta"],
        "sha256s": promotion["sha256s"],
        "live_saveram_unchanged": True,
        "rollback": rel(backup_rom),
        "report": rel(PROMOTION_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
