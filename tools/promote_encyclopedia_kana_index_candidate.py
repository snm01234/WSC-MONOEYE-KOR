#!/usr/bin/env python3
"""Promote the encyclopedia gojuon-index Hangul candidate to the main TIP.

ROM-only. Live SaveRAM is never replaced. Re-enter 도감 after loading the
promoted ROM; an already-open encyclopedia savestate may restore old tiles.
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

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base
from normalize_ko_text import normalize_ko_text

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_DIR = PATCH / "encyclopedia_kana_index_candidate"
CANDIDATE = CANDIDATE_DIR / "monoeye_ko_expanded_encyclopedia_kana_index_test.wsc"
BUILD_REPORT = CANDIDATE_DIR / "encyclopedia_kana_index_report.json"
AUDIT_REPORT = CANDIDATE_DIR / "encyclopedia_kana_index_audit.json"
CATALOG = ROOT / "data/encyclopedia_kana_index_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
PROMOTION_REPORT = PATCH / "encyclopedia_kana_index_promotion_report.json"
POST_AUDIT = PATCH / "encyclopedia_kana_index_postpromotion_audit.json"
POST_FALSE = PATCH / "encyclopedia_kana_index_postpromotion_false_segptr.json"

EXPECTED_TIP_SHA = "0ff2bc7398c5b677d02bc1d81df21d12dc7731d2d16d62c3cc7cd25b1c74ca11"
EXPECTED_CANDIDATE_SHA = (
    "b0438b51f0a6f5fab94418433f3fac6c551ab1ee8f434627ee4e42816928216a"
)
EXPECTED_CHECKSUM = "8C20"
EXPECTED_DIFF_BYTES = 188
EXPECTED_ROWS = 9
KEEP_LATIN = 0x75B8C6
KEEP_LATIN_HEX = "E1C0E0F5E1C907E132"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


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


def verify_catalog_renders(rom: bytes) -> list[dict[str, Any]]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows = list(catalog.get("records") or [])
    if len(rows) != EXPECTED_ROWS:
        raise PromotionError(f"catalog count drifted: {len(rows)}")
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    sb = stock_base(rom)
    latin = rom[sb + KEEP_LATIN : sb + KEEP_LATIN + len(bytes.fromhex(KEEP_LATIN_HEX))]
    if latin != bytes.fromhex(KEEP_LATIN_HEX):
        raise PromotionError("latin index row 75B8C6 changed")
    checked: list[dict[str, Any]] = []
    for row in rows:
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        payload_len = int(row["payload_len"])
        got = read_encoded_z_safe(rom, sb + logical, max_len=256)
        if got is None:
            raise PromotionError(f"{address} unreadable")
        payload, terminator = bytes(got[0]), int(got[1])
        if len(payload) != payload_len:
            raise PromotionError(f"{address} payload length drifted")
        actual = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(row["ko"])).rstrip("\u3000 \t")
        if actual != expected:
            raise PromotionError(f"{address} render mismatch: {actual!r} != {expected!r}")
        if any(is_japanese_character(ch) for ch in actual):
            raise PromotionError(f"{address} Japanese residual: {actual!r}")
        if len(actual) > int(row["max_visual_cells"]):
            raise PromotionError(f"{address} visual width exceeded")
        if terminator != sb + logical + payload_len or rom[terminator] != 0:
            raise PromotionError(f"{address} terminator moved")
        checked.append(
            {
                "abs": address,
                "expected": expected,
                "actual": actual,
                "visual_cells": len(actual),
                "ok": True,
            }
        )
    return checked


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
    require(CATALOG)

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_REPORT.read_text(encoding="utf-8"))
    if build.get("ok") is not True or audit.get("ok") is not True:
        raise PromotionError("candidate report/audit is not ok")
    if str((build.get("main_tip") or {}).get("sha256") or "").lower() != EXPECTED_TIP_SHA:
        raise PromotionError("build parent SHA drifted")
    if str((build.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("build candidate SHA drifted")
    if str((audit.get("candidate") or {}).get("sha256") or "").lower() != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("audit candidate SHA drifted")
    if str((build.get("diff") or {}).get("checksum") or "") != EXPECTED_CHECKSUM:
        raise PromotionError("build checksum drifted")
    if int((build.get("counts") or {}).get("targets") or -1) != EXPECTED_ROWS:
        raise PromotionError("applied count drifted")
    if int((build.get("diff") or {}).get("changed_bytes_from_parent") or -1) != EXPECTED_DIFF_BYTES:
        raise PromotionError("diff byte count drifted")
    if not audit.get("latin_index_row_unchanged"):
        raise PromotionError("audit latin row gate failed")
    if audit.get("false_segptr_sites") not in (0, None) and int(audit.get("false_segptr_sites") or -1) != 0:
        raise PromotionError("pre-promotion false-segptr was not clean")

    parent = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    save_before = SAVE.read_bytes()
    diffs = changed_offsets(parent, candidate)
    if len(diffs) != EXPECTED_DIFF_BYTES:
        raise PromotionError(f"live diff byte count drifted: {len(diffs)}")
    info = checksum_info(candidate)
    if not info["valid"] or info["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {info}")
    verify_catalog_renders(candidate)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_encyclopedia_kana_index"
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
        checked = verify_catalog_renders(promoted)
        false_segptr = run_false_segptr(TIP)
        post_checks = {
            "tip_matches_candidate": sha256(promoted) == EXPECTED_CANDIDATE_SHA,
            "checksum_valid": checksum_info(promoted)["valid"]
            and checksum_info(promoted)["stored"] == EXPECTED_CHECKSUM,
            "catalog_renders_exact": len(checked) == EXPECTED_ROWS,
            "latin_index_row_unchanged": True,
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
        "generated_by": "tools/promote_encyclopedia_kana_index_candidate.py",
        "ok": True,
        "tip": identity(TIP, promoted),
        "tip_checksum": checksum_info(promoted),
        "rollback_rom": identity(backup_rom),
        "records": checked,
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
        "generated_by": "tools/promote_encyclopedia_kana_index_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": promoted_at,
        "authorization": "사용자가 도감 가나 색인 한글 발음 테스트 ROM의 메인 TIP 승격을 요청함",
        "old_tip": {
            "path": "out/patch/monoeye_ko_expanded.wsc",
            "size": ROM_SIZE,
            "sha256": EXPECTED_TIP_SHA,
        },
        "new_tip": identity(TIP, promoted),
        "new_tip_checksum": checksum_info(promoted),
        "backup_rom": identity(backup_rom),
        "candidate": identity(CANDIDATE, candidate),
        "proof": {
            "diff_bytes": len(diffs),
            "targets": EXPECTED_ROWS,
            "checksum": checksum_info(promoted),
            "latin_index_row_unchanged": True,
        },
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
                "proof": promotion["proof"],
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
