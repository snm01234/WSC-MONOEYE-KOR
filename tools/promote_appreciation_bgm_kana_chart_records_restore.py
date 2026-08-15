#!/usr/bin/env python3
"""Promote the user-runtime-confirmed UI75 kana-chart raw-record restore.

Restores only the nine dual-use raw kana/index records at 75:B889..B8BF from
E5 18 ext3 portals back to their pre-encyclopedia-kana byte sequences.  The
unused ext3 phrases/pointers are intentionally left allocated because the
runtime-tested candidate did so; no render-only kana hook is promoted.

Promotion is ROM-only. Live SaveRAM is snapshotted and required byte-identical.
A rollback ROM is created, false segmented-pointer scan must be clean, and the
main xdelta is rebuilt with round-trip verification.
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
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "appreciation_bgm_kana_chart_records_restore_probe.wsc"
GOOD_BACKUP = PATCH / "backup/20260815_012352_pre_encyclopedia_kana_index/monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = PATCH / "appreciation_bgm_kana_chart_records_restore_promotion_report.json"
POST_FALSE = PATCH / "appreciation_bgm_kana_chart_records_restore_postpromotion_false_segptr.json"

EXPECTED_TIP_SHA = "d925ee07b4ea844a1bd89deff52bc88ec21e04bf8ab5a0df8d9fecdd651bd8b1"
EXPECTED_CANDIDATE_SHA = "1ef9a6446f6d77c63ad95fb167d5131e1b4062f289b24522bcf54a79c3cb00fe"
EXPECTED_CHECKSUM = "29EE"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STOCK_BASE = 0x800000
RECORDS = [
    (0x75B889, bytes.fromhex("262977582C")),
    (0x75B88F, bytes.fromhex("5A623DE0225E")),
    (0x75B896, bytes.fromhex("652D2F7CC3")),
    (0x75B89C, bytes.fromhex("30E03F4B4A3E")),
    (0x75B8A3, bytes.fromhex("4360E40775E007")),
    (0x75B8AB, bytes.fromhex("7EE09563E0D3E092")),
    (0x75B8B4, bytes.fromhex("37643FB95D")),
    (0x75B8BA, bytes.fromhex("9CB3E2E7")),
    (0x75B8BF, bytes.fromhex("2EE0F53C07A7")),
]

class PromotionError(RuntimeError):
    pass

def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()

def sha_path(path: Path) -> str:
    return sha(path.read_bytes())

def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")

def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha(data)}

def checksum_info(rom: bytes) -> dict[str, Any]:
    stored = int.from_bytes(rom[-2:], "little")
    computed = sum(rom[:-2]) & 0xFFFF
    return {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "valid": stored == computed}

def atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    with src.open("rb") as fi, tmp.open("wb") as fo:
        shutil.copyfileobj(fi, fo, 1024 * 1024)
        fo.flush(); os.fsync(fo.fileno())
    os.replace(tmp, dst)

def atomic_bytes(dst: Path, data: bytes) -> None:
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data); os.replace(tmp, dst)

def atomic_json(dst: Path, obj: dict[str, Any]) -> None:
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")
    os.replace(tmp, dst)

def expected_nonchecksum_offsets() -> list[int]:
    out=[]
    for logical, payload in RECORDS:
        start=STOCK_BASE+logical
        out.extend(range(start,start+len(payload)))
    return out

def verify_raw_chart(rom: bytes) -> None:
    for logical, expected in RECORDS:
        off=STOCK_BASE+logical
        actual=rom[off:off+len(expected)]
        if actual != expected:
            raise PromotionError(f"raw chart mismatch {logical:06X}: {actual.hex().upper()} != {expected.hex().upper()}")
        if rom[off+len(expected)] != 0:
            raise PromotionError(f"raw chart terminator drift {logical:06X}")
def run_false_segptr() -> dict[str, Any]:
    env=os.environ.copy(); env["PYTHONIOENCODING"]="utf-8"
    cp=subprocess.run([sys.executable,str(ROOT/"tools/scan_false_segptr_writes.py"),"--target",str(TIP),"--out",str(POST_FALSE)],cwd=ROOT,env=env,check=False,capture_output=True,text=True)
    if cp.returncode != 0:
        raise PromotionError("false-segptr scan failed: "+(cp.stderr or cp.stdout)[-1000:])
    obj=json.loads(POST_FALSE.read_text(encoding="utf-8"))
    if obj.get("ok") is not True or int(obj.get("sites_found",-1)) != 0:
        raise PromotionError(f"false-segptr not clean: {obj.get('sites_found')}")
    return {"ok":True,"sites_found":0,"report":identity(POST_FALSE)}
def rebuild_xdelta() -> dict[str, Any]:
    env=os.environ.copy(); env["PYTHONIOENCODING"]="utf-8"
    cp=subprocess.run([sys.executable,str(ROOT/"tools/make_main_tip_xdelta.py")],cwd=ROOT,env=env,check=False,capture_output=True,text=True)
    if cp.returncode != 0:
        raise PromotionError("xdelta rebuild failed: "+(cp.stderr or cp.stdout)[-1200:])
    meta_path=ROOT/"out/dist/monoeye_ko_expanded_xdelta.json"; delta_path=ROOT/"out/dist/monoeye_ko_expanded.xdelta"
    meta=json.loads(meta_path.read_text(encoding="utf-8"))
    result_sha=str(((meta.get("main_tip") or {}).get("sha256") or "")).lower()
    rt=meta.get("roundtrip_matches_main_tip") is True
    if result_sha != EXPECTED_CANDIDATE_SHA or not rt:
        raise PromotionError(f"xdelta verify failed sha={result_sha} roundtrip={rt}")
    return {"ok":True,"path":rel(delta_path),"sha256":sha_path(delta_path),"metadata":rel(meta_path),"roundtrip_matches_main_tip":True}

def main() -> int:
    if not TIP.is_file() or TIP.stat().st_size != ROM_SIZE or sha_path(TIP) != EXPECTED_TIP_SHA:
        raise PromotionError("main TIP identity drifted")
    if not CANDIDATE.is_file() or CANDIDATE.stat().st_size != ROM_SIZE or sha_path(CANDIDATE) != EXPECTED_CANDIDATE_SHA:
        raise PromotionError("runtime-tested candidate identity drifted")
    if not GOOD_BACKUP.is_file() or GOOD_BACKUP.stat().st_size != ROM_SIZE:
        raise PromotionError("pre-kana good backup missing")
    if not SAVE.is_file() or SAVE.stat().st_size != SAVE_SIZE:
        raise PromotionError("live SaveRAM missing/wrong size")

    old=TIP.read_bytes(); candidate=CANDIDATE.read_bytes(); good=GOOD_BACKUP.read_bytes(); save_before=SAVE.read_bytes()
    ci=checksum_info(candidate)
    if not ci["valid"] or ci["stored"] != EXPECTED_CHECKSUM:
        raise PromotionError(f"candidate checksum invalid: {ci}")
    verify_raw_chart(candidate)
    for logical, payload in RECORDS:
        off=STOCK_BASE+logical
        if candidate[off:off+len(payload)] != good[off:off+len(payload)]:
            raise PromotionError(f"candidate not byte-exact pre-kana backup at {logical:06X}")

    changed=[i for i,(a,b) in enumerate(zip(old,candidate)) if a!=b]
    non=[i for i in changed if i < ROM_SIZE-2]
    expected=expected_nonchecksum_offsets()
    if non != expected:
        raise PromotionError(f"candidate scope drifted: got {len(non)} expected {len(expected)}")
    if len(non) != 52:
        raise PromotionError(f"unexpected non-checksum byte count {len(non)}")

    stamp=datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir=PATCH/"backup"/f"{stamp}_pre_appreciation_bgm_kana_chart_restore"
    backup_dir.mkdir(parents=True,exist_ok=False)
    backup_rom=backup_dir/TIP.name
    shutil.copy2(TIP,backup_rom)
    if sha_path(backup_rom) != EXPECTED_TIP_SHA:
        raise PromotionError("rollback backup mismatch")

    try:
        atomic_copy(CANDIDATE,TIP)
        promoted=TIP.read_bytes()
        if sha(promoted) != EXPECTED_CANDIDATE_SHA:
            raise PromotionError("promoted TIP differs from tested candidate")
        verify_raw_chart(promoted)
        if SAVE.read_bytes() != save_before:
            raise PromotionError("live SaveRAM changed during promotion")
        false_segptr=run_false_segptr()
        xdelta=rebuild_xdelta()
    except Exception:
        atomic_bytes(TIP,old)
        raise

    promoted_at=datetime.now().astimezone().isoformat(timespec="seconds")
    report={
        "schema_version":1,
        "generated_by":Path(__file__).name,
        "ok":True,
        "published":True,
        "promoted_at":promoted_at,
        "authorization":"사용자가 appreciation_bgm_kana_chart_records_restore_probe.wsc 실측에서 감상모드 BGM 깨짐이 수정됨을 확인하고 메인TIP 승격을 요청함",
        "old_tip":{"path":rel(TIP),"size":ROM_SIZE,"sha256":EXPECTED_TIP_SHA},
        "new_tip":identity(TIP,promoted),
        "checksum":checksum_info(promoted),
        "tested_candidate":identity(CANDIDATE,candidate),
        "rollback_rom":identity(backup_rom),
        "change":{"logical_ranges":[f"{logical>>16:02X}:{logical&0xffff:04X}" for logical,_ in RECORDS],"non_checksum_byte_count":52,"reason":"restore dual-use raw kana/index chart; do not promote render-only hook"},
        "runtime_user_validation":"appreciation BGM navigation confirmed clean for both raw-record restore and full boundary rollback probes",
        "pre_kana_backup_match":True,
        "false_segptr":false_segptr,
        "xdelta":xdelta,
        "live_saveram":identity(SAVE,save_before),
        "live_saveram_unchanged":SAVE.read_bytes()==save_before,
    }
    atomic_json(REPORT,report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"PROMOTION FAILED: {exc}",file=sys.stderr)
        raise SystemExit(1)
