#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "scenario_continuation_global_structural_fix_candidate.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
AUDIT = PATCH / "scenario_continuation_global_structural_fix_audit.json"
REPORT = PATCH / "scenario_continuation_global_structural_fix_promotion_report.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "cfb90aaa7af2b9336fb63c70a8e7ec760ac51425d80017d5daf82e6118d86bca"
EXPECTED_CANDIDATE = "24aa886359bb41e70161d47c66c90d683c91f0287c3be2eca856c7f520e7f1bf"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
RUNTIME_RESULTS = {"A": "pass", "B": "pass", "C": "pass", "D": "not_tested", "E": "not_tested", "F": "not_tested", "G": "pass", "H": "pass"}

class PromotionError(RuntimeError):
    pass


def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def ident(path: Path) -> dict:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha_path(path)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if sha is not None and sha_path(path) != sha:
        raise PromotionError(f"sha drift: {path}: {sha_path(path)} != {sha}")


def checksum_valid(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    with src.open("rb") as sf, tmp.open("wb") as df:
        shutil.copyfileobj(sf, df, 1024 * 1024)
        df.flush(); os.fsync(df.fileno())
    os.replace(tmp, dst)


def atomic_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(LIVE_SAVE, size=SAVE_SIZE)
    require(AUDIT)
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate checksum invalid")

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if audit.get("ok") is not True:
        raise PromotionError("candidate audit not clean")
    if str((audit.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("audit not bound to candidate")
    counts = audit.get("counts") or {}
    expected_zero = ["structural_storage_risk", "visible_ko_leak_risk", "terminator_drift", "render_mismatch", "boundary_invariant_mismatch"]
    if any(int(counts.get(k, -1)) != 0 for k in expected_zero):
        raise PromotionError(f"audit zero gate failed: {counts}")
    if int(counts.get("changed", -1)) != 2746 or int(counts.get("e504_portals", -1)) != 2739:
        raise PromotionError(f"audit scope drift: {counts}")
    if not all((audit.get("checks") or {}).values()):
        raise PromotionError("one or more audit checks false")

    before = {"tip": ident(MAIN), "saveram": ident(LIVE_SAVE)}
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_scenario_continuation_global_structural_fix"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / MAIN.name
    shutil.copy2(MAIN, backup)
    require(backup, size=ROM_SIZE, sha=EXPECTED_PARENT)

    try:
        atomic_copy(CANDIDATE, MAIN)
        require(MAIN, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
        require(LIVE_SAVE, size=SAVE_SIZE, sha=before["saveram"]["sha256"])
        if not checksum_valid(MAIN):
            raise PromotionError("promoted checksum invalid")
    except Exception:
        atomic_copy(backup, MAIN)
        raise

    after = {"tip": ident(MAIN), "saveram": ident(LIVE_SAVE)}
    checks = {
        "main_matches_candidate": after["tip"]["sha256"] == EXPECTED_CANDIDATE,
        "checksum_valid": checksum_valid(MAIN),
        "saveram_unchanged": after["saveram"] == before["saveram"],
        "rollback_preserved": sha_path(backup) == EXPECTED_PARENT,
        "candidate_audit_clean": audit.get("ok") is True,
        "runtime_A_B_C_G_H_pass": all(RUNTIME_RESULTS[x] == "pass" for x in ("A", "B", "C", "G", "H")),
    }
    if not all(checks.values()):
        atomic_copy(backup, MAIN)
        raise PromotionError(f"post-promotion gate failed: {checks}")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/promote_scenario_continuation_global_structural_fix_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "authorization": "user explicitly requested main promotion after substantial runtime measurement",
        "runtime_validation": RUNTIME_RESULTS,
        "runtime_note": "A/B/C/G/H user-runtime PASS; D/E/F not separately measured before promotion. Full static fail-closed audit remained clean across all 2,746 changed continuations.",
        "before": before,
        "after": after,
        "checks": checks,
        "rollback": ident(backup),
        "audit": ident(AUDIT),
        "scope": {
            "single_nul_visible_ko_removed": 6,
            "double_nul_structural18": 2740,
            "ordinary_native": 1,
            "e504_portal16": 2739,
            "total_changed_continuations": 2746,
        },
        "saveram_policy": "ROM only promoted; live SaveRAM preserved byte-exact",
    }
    atomic_json(REPORT, payload)
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
