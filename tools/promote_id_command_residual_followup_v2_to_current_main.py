#!/usr/bin/env python3
"""Selectively promote the user-approved ID-command v2 plaque data into current main TIP.

Only the nine residual/follow-up bank-4C assets are copied from
id_command_residual_plaques_ko_followup_v2_candidate.wsc.  All other current-main
bytes are preserved, so later unrelated work is not rolled back.  The known
shield duplicate-column runtime issue is intentionally preserved for a future
savestate-assisted fix; none of the later shield experiment hooks/caves are promoted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(r"D:\monoeye")
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
V2 = PATCH / "id_command_residual_plaques_ko_followup_v2_candidate.wsc"
SPEC = ROOT / "data/id_command_residual_plaques_ko_followup_v2.json"
BUILD_REPORT = PATCH / "id_command_residual_plaques_ko_followup_v2_candidate_report.json"
V2_AUDIT = PATCH / "id_command_residual_plaques_ko_followup_v2_candidate_audit.json"
PROMOTION_REPORT = PATCH / "id_command_residual_followup_v2_selective_promotion_report.json"
POST_AUDIT = PATCH / "id_command_residual_followup_v2_selective_postpromotion_audit.json"

EXPECTED_TIP_SHA = "d479f3ec861dc1da9b5da83dbf6900711367b6e09b3b975c4c72adbc3e633316"
EXPECTED_V2_SHA = "93b1b0222a672c5ee8e059f567380985f55c29ef558f6af5b981d5d5edecbf30"
EXPECTED_SPEC_SHA = "0d3d6e231b2bc00c109884fc860b41e43b1ffa384646495c0c0799659f09c9fb"
EXPECTED_BUILD_REPORT_SHA = "711bd33aa23efc371fc535d51dd821378f725b35f5f6e1b35786cae13f89a8fa"
EXPECTED_V2_AUDIT_SHA = "35d75978489091449f5f506379ef3808f6ecdc95651406abc30abf9478ce0cf6"
EXPECTED_NEW_SHA = "42051b189eff4d23d509b83da7aad81384ee932adbc06964990dc1a8578608ad"
EXPECTED_NEW_CHECKSUM = "A221"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STOCK_BASE = 0x800000

# Exact v2 residual/follow-up storage ranges.  No runtime/code-hook addresses are included.
TARGETS = [
    ("seal", 0x4C4A74, 320),
    ("shield", 0x4C4BB4, 256),
    ("sure_hit", 0x4C50F4, 320),
    ("evade", 0x4C53B4, 320),
    ("move_down", 0x4CBEAA, 384),
    ("pursuit", 0x4CC32A, 320),
    ("penetrate", 0x4CE86A, 384),
    ("preemptive", 0x4CE9EA, 256),
    ("hp_recovery", 0x4CC52A, 384),
]
EXPECTED_TARGET_CHANGED_BYTES = {
    "seal": 215,
    "shield": 171,
    "sure_hit": 222,
    "evade": 215,
    "move_down": 204,
    "pursuit": 174,
    "hp_recovery": 208,
    "penetrate": 245,
    "preemptive": 199,
}
EXPECTED_TARGET_DIFF_BYTES = sum(EXPECTED_TARGET_CHANGED_BYTES.values())  # 1853
EXPECTED_TOTAL_CHANGED_BYTES = EXPECTED_TARGET_DIFF_BYTES + 2  # checksum bytes also change


class PromotionError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def digest(path: Path) -> str:
    return sha(path.read_bytes())


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path) -> dict[str, Any]:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": digest(path)}


def require(path: Path, *, size: int | None = None, expected_sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}: {path.stat().st_size} != {size}")
    if expected_sha is not None and digest(path).lower() != expected_sha.lower():
        raise PromotionError(f"SHA-256 drift: {path}: {digest(path)}")


def update_checksum(buf: bytearray) -> int:
    value = sum(buf[:-2]) & 0xFFFF
    buf[-2:] = value.to_bytes(2, "little")
    return value


def checksum(data: bytes) -> dict[str, Any]:
    computed = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    return {"computed": f"{computed:04X}", "stored": f"{stored:04X}", "valid": computed == stored}


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, (x, y) in enumerate(zip(a, b, strict=True)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(a)))
    return runs


def in_allowlist(start: int, end: int, allowed: list[tuple[int, int]]) -> bool:
    return any(start >= lo and end <= hi for lo, hi in allowed)


def atomic_bytes(path: Path, payload: bytes) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, path)


def build_proposed(old: bytes, v2: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    out = bytearray(old)
    rows: list[dict[str, Any]] = []
    for name, logical, size in TARGETS:
        p = STOCK_BASE + logical
        before = old[p:p+size]
        source = v2[p:p+size]
        changed = sum(a != b for a, b in zip(before, source, strict=True))
        if changed != EXPECTED_TARGET_CHANGED_BYTES[name]:
            raise PromotionError(f"target drift {name}: changed={changed}, expected={EXPECTED_TARGET_CHANGED_BYTES[name]}")
        out[p:p+size] = source
        rows.append({
            "name": name,
            "logical_range": f"{logical:06X}-{logical+size-1:06X}",
            "physical_range": f"{p:08X}-{p+size-1:08X}",
            "size": size,
            "changed_bytes_current_to_v2": changed,
            "v2_block_sha256": sha(source),
        })
    ws = update_checksum(out)
    result = bytes(out)
    if sha(result) != EXPECTED_NEW_SHA:
        raise PromotionError(f"proposed SHA drift: {sha(result)}")
    if f"{ws:04X}" != EXPECTED_NEW_CHECKSUM:
        raise PromotionError(f"proposed checksum drift: {ws:04X}")
    return result, rows


def validate() -> dict[str, Any]:
    require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)
    require(V2, size=ROM_SIZE, expected_sha=EXPECTED_V2_SHA)
    require(TIP_SAVE, size=SAVE_SIZE)
    require(SPEC, expected_sha=EXPECTED_SPEC_SHA)
    require(BUILD_REPORT, expected_sha=EXPECTED_BUILD_REPORT_SHA)
    require(V2_AUDIT, expected_sha=EXPECTED_V2_AUDIT_SHA)

    old = TIP.read_bytes()
    v2 = V2.read_bytes()
    proposed, rows = build_proposed(old, v2)

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    spec_names = [row["name"] for row in spec.get("targets", [])]
    expected_names = [name for name, _, _ in TARGETS]
    if spec_names != expected_names:
        raise PromotionError(f"v2 spec target order/inventory drift: {spec_names}")
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    audit = json.loads(V2_AUDIT.read_text(encoding="utf-8"))
    if build.get("ok") is not True or audit.get("ok") is not True:
        raise PromotionError("v2 build/audit is not green")
    if (build.get("candidate") or {}).get("sha256", "").lower() != EXPECTED_V2_SHA:
        raise PromotionError("v2 build report binding mismatch")

    allowed = [(STOCK_BASE + logical, STOCK_BASE + logical + size) for _, logical, size in TARGETS]
    allowed.append((ROM_SIZE - 2, ROM_SIZE))
    runs = diff_runs(old, proposed)
    unexpected = [(s, e) for s, e in runs if not in_allowlist(s, e, allowed)]
    changed = sum(e-s for s, e in runs)
    if unexpected:
        raise PromotionError(f"proposed diff outside ID allowlist: {unexpected}")
    if changed != EXPECTED_TOTAL_CHANGED_BYTES:
        raise PromotionError(f"proposed changed-byte drift: {changed}")

    # Explicitly prove that every byte outside the nine ID ranges (except checksum) stays current-main exact.
    target_mask = bytearray(ROM_SIZE)
    for _, logical, size in TARGETS:
        p = STOCK_BASE + logical
        target_mask[p:p+size] = b"\x01" * size
    outside_changed = [
        i for i, (a, b) in enumerate(zip(old[:-2], proposed[:-2], strict=True))
        if a != b and not target_mask[i]
    ]
    if outside_changed:
        raise PromotionError(f"outside-main preservation failed at {outside_changed[:16]}")

    # Later shield experiments patched F8:97A2/F8:FF19 and private blank tiles. Those must remain current-main exact.
    experiment_checks = {
        "runtime_hook_site_preserved_from_current_main": proposed[0xF897A2:0xF897B2] == old[0xF897A2:0xF897B2],
        "runtime_cave_preserved_from_current_main": proposed[0xF8FF19:0xF8FF19+96] == old[0xF8FF19:0xF8FF19+96],
        "experimental_blank_tile_area_not_promoted": proposed[STOCK_BASE+0x4CEB8C:STOCK_BASE+0x4CEBCC] == old[STOCK_BASE+0x4CEB8C:STOCK_BASE+0x4CEBCC],
    }
    if not all(experiment_checks.values()):
        raise PromotionError(f"later shield experiment leaked into promotion: {experiment_checks}")

    return {
        "current_main": identity(TIP),
        "v2_source": identity(V2),
        "main_saveram": identity(TIP_SAVE),
        "spec": identity(SPEC),
        "v2_build_report": identity(BUILD_REPORT),
        "v2_audit": identity(V2_AUDIT),
        "targets": rows,
        "proposed": {"sha256": sha(proposed), "checksum": checksum(proposed)},
        "diff": {"runs": len(runs), "changed_bytes_including_checksum": changed, "unexpected": unexpected},
        "experiment_exclusion_checks": experiment_checks,
        "policy": "selective nine-range promotion only; preserve current main everywhere else; shield duplicate-column bug intentionally deferred",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    validation = validate()
    if not args.commit:
        print(json.dumps({"mode": "dry_run", "ok": True, "validation": validation}, ensure_ascii=False, indent=2))
        return 0

    old = TIP.read_bytes()
    v2 = V2.read_bytes()
    proposed, rows = build_proposed(old, v2)
    save_before = identity(TIP_SAVE)
    v2_before = identity(V2)

    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_id_command_residual_followup_v2_selective"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    require(backup, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)

    try:
        atomic_bytes(TIP, proposed)
        require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_NEW_SHA)
        new = TIP.read_bytes()
        checks = {
            "main_sha_expected": digest(TIP) == EXPECTED_NEW_SHA,
            "main_checksum_valid": checksum(new)["valid"] and checksum(new)["stored"] == EXPECTED_NEW_CHECKSUM,
            "rollback_rom_exact": digest(backup) == EXPECTED_TIP_SHA,
            "main_saveram_unchanged": identity(TIP_SAVE) == save_before,
            "v2_source_unchanged": identity(V2) == v2_before,
            "all_nine_targets_match_v2": all(
                new[STOCK_BASE+logical:STOCK_BASE+logical+size] == v2[STOCK_BASE+logical:STOCK_BASE+logical+size]
                for _, logical, size in TARGETS
            ),
            "outside_targets_preserved_from_old_main": all(
                (a == b) or any(STOCK_BASE+logical <= i < STOCK_BASE+logical+size for _, logical, size in TARGETS) or i >= ROM_SIZE-2
                for i, (a, b) in enumerate(zip(old, new, strict=True))
            ),
            "runtime_hook_site_not_promoted": new[0xF897A2:0xF897B2] == old[0xF897A2:0xF897B2],
            "runtime_cave_not_promoted": new[0xF8FF19:0xF8FF19+96] == old[0xF8FF19:0xF8FF19+96],
            "experimental_blank_tiles_not_promoted": new[STOCK_BASE+0x4CEB8C:STOCK_BASE+0x4CEBCC] == old[STOCK_BASE+0x4CEB8C:STOCK_BASE+0x4CEBCC],
        }
        if not all(checks.values()):
            raise PromotionError(f"postpromotion audit failed: {checks}")
        post = {
            "schema_version": 1,
            "generated_by": "tools/promote_id_command_residual_followup_v2_to_current_main.py",
            "ok": True,
            "old_main": {"sha256": sha(old)},
            "new_main": identity(TIP),
            "new_checksum": checksum(new),
            "rollback_rom": identity(backup),
            "main_saveram_before": save_before,
            "main_saveram_after": identity(TIP_SAVE),
            "targets": rows,
            "checks": checks,
        }
        atomic_json(POST_AUDIT, post)
    except Exception:
        atomic_bytes(TIP, old)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_id_command_residual_followup_v2_to_current_main.py",
        "mode": "commit",
        "ok": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_main": {"path": rel(TIP), "size": ROM_SIZE, "sha256": EXPECTED_TIP_SHA},
        "new_main": identity(TIP),
        "v2_source": identity(V2),
        "rollback_rom": identity(backup),
        "promotion_scope": "nine v2 ID-command residual/follow-up plaque storage ranges only",
        "deferred_known_issue": "shield displays duplicated final Hangul tile at runtime (방패ㅐ); defer until a shield savestate is available",
        "excluded": "all later shield A/B/C/D and runtime-hook experiments",
        "validation": validation,
        "postpromotion_audit": identity(POST_AUDIT),
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
