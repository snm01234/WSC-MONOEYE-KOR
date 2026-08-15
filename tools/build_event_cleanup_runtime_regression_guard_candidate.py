#!/usr/bin/env python3
"""Build a cumulative runtime-regression cleanup candidate.

Parent: event_cleanup_gato_5d1e3e_candidate.wsc (user-validated Gato 5D:1E3E fix)

Adds two narrowly proven fixes:
1) Restore 55 battle-voice records whose authoritative one-byte speaker/portrait
   metadata had already been proven safe in the 2026-08-07 structure inventory,
   but whose current bytes have regressed byte-for-byte to the pre-repair
   whole-record E5 18 form.  The existing body token is preserved; only the
   authoritative metadata byte is reinserted and one trailing 01 padding byte
   is consumed, preserving record extent and terminator.
2) 61:06EF is the runtime-proven stray standalone 'な' shown immediately after
   the UC.0080 Karama Point system title.  Replace only 06 with 01 (blank cell),
   preserving its one-byte record extent and following NUL.

Unproven singleton fragments (60:F3A6, 61:055C, 61:165D, 63:8F52, etc.) and
safe-snapshot-mismatch battle rows remain untouched/quarantined.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, read_encoded_z_safe, stock_base, update_ws_checksum, ws_header

PATCH = ROOT / "out/patch"
PARENT = PATCH / "event_cleanup_gato_5d1e3e_candidate.wsc"
PARENT_SAVE = ROOT / "sram/event_cleanup_gato_5d1e3e_candidate.sav"
INVENTORY = ROOT / "legacy/release_core_20260815/out/script/battle_dialogue_structure_inventory.csv"
OUT_ROM = PATCH / "event_cleanup_runtime_regression_guard_candidate.wsc"
OUT_SAVE = ROOT / "sram/event_cleanup_runtime_regression_guard_candidate.sav"
OUT_REPORT = PATCH / "event_cleanup_runtime_regression_guard_report.json"
EXPECTED_PARENT_SHA = "ca4867914852328e0eb4e184a9f27bd831e5eae3f61b4a94c253d702a3a43dab"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
KARAMA_ORPHAN = 0x6106EF


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def checksum_valid(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def diff_positions(a: bytes, b: bytes) -> list[int]:
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


def main() -> int:
    parent = bytes(load_rom(PARENT))
    save = PARENT_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise RuntimeError(f"parent drifted: {sha(parent)}")
    if len(save) != SAVE_SIZE:
        raise RuntimeError("paired SaveRAM missing/wrong size")
    sb = stock_base(parent)
    candidate = bytearray(parent)

    with INVENTORY.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    restored = []
    for row in rows:
        if (
            row.get("classification") != "battle_voice_structured"
            or row.get("safe_structure_exact") != "yes"
            or row.get("action") != "repair"
            or len(row.get("metadata_hex", "")) != 2
        ):
            continue
        logical = int(row["record_start"], 16)
        rec = read_encoded_z_safe(parent, sb + logical, max_len=128)
        if rec is None:
            continue
        live, term = rec
        if not live.startswith(b"\xE5\x18"):
            continue
        before = bytes(live)
        if len(before) != int(row["body_capacity"]) + 1:
            raise RuntimeError(f"regressed record extent/body-capacity mismatch at {logical:06X}")
        if before[4:] != b"\x01" * (len(before) - 4):
            raise RuntimeError(f"non-padding tail in regressed row {logical:06X}")
        meta = bytes.fromhex(row["metadata_hex"])
        # Preserve the *current* 4-byte body portal exactly.  Later translation
        # passes may have retargeted the portal since the 2026-08-07 inventory,
        # so using stale candidate_payload_hex would incorrectly roll text back.
        after = meta + before[:4] + b"\x01" * (len(before) - 5)
        if len(after) != len(before):
            raise RuntimeError(f"record extent drift at {logical:06X}")
        if after[:1] != meta or after[1:5] != before[:4]:
            raise RuntimeError(f"metadata/body preservation contract failed at {logical:06X}")
        before_hex = before.hex().upper()
        after_hex = after.hex().upper()
        start = sb + logical
        boundary = parent[term:term + 8]
        candidate[start:start + len(after)] = after
        if candidate[term:term + 8] != boundary:
            raise RuntimeError(f"terminator/next-boundary changed at {logical:06X}")
        restored.append({
            "abs": f"{logical:06X}",
            "metadata": row["metadata_hex"].upper(),
            "before": before_hex,
            "after": after_hex,
            "render_forensic": row.get("candidate_render") or row.get("current_render") or "",
        })

    if len(restored) != 55:
        raise RuntimeError(f"expected 55 regressed proven metadata rows, found {len(restored)}")

    # Runtime-proven standalone kana leak after Karama Point title.
    k = sb + KARAMA_ORPHAN
    if parent[k:k + 2] != b"\x06\x00":
        raise RuntimeError(f"61:06EF drifted: {parent[k:k+2].hex().upper()}")
    candidate[k] = 0x01

    checksum = update_ws_checksum(candidate)
    out = bytes(candidate)

    # Independent postconditions.
    post_fail = []
    for item in restored:
        logical = int(item["abs"], 16)
        rec = read_encoded_z_safe(out, sb + logical, max_len=128)
        if rec is None or bytes(rec[0]).hex().upper() != item["after"]:
            post_fail.append(item["abs"])
    if post_fail:
        raise RuntimeError(f"postbuild restored-record mismatch: {post_fail[:5]}")
    if out[k:k + 2] != b"\x01\x00":
        raise RuntimeError("Karama orphan blanking failed")
    if not checksum_valid(out):
        raise RuntimeError("checksum invalid")

    diffs = diff_positions(parent, out)
    non_checksum = [i for i in diffs if i < len(out) - 2]
    expected_nonchecksum = {sb + KARAMA_ORPHAN}
    for item in restored:
        logical = int(item["abs"], 16)
        before = bytes.fromhex(item["before"])
        after = bytes.fromhex(item["after"])
        expected_nonchecksum.update(sb + logical + j for j, (x, y) in enumerate(zip(before, after)) if x != y)
    if set(non_checksum) != expected_nonchecksum:
        raise RuntimeError("unexpected non-checksum diff outside declared targets")

    atomic_bytes(OUT_ROM, out)
    atomic_bytes(OUT_SAVE, save)

    quarantine_battle = [
        "5D870B", "5DB42B", "5E6586", "5E65A7"
    ]
    singleton_watch = [
        "60F3A6", "61055C", "61165D", "638F52"
    ]
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_event_cleanup_runtime_regression_guard_candidate.py",
        "ok": True,
        "promotion": "blocked_pending_runtime_verification",
        "parent": {"path": str(PARENT.relative_to(ROOT)).replace('\\','/'), "sha256": sha(parent).upper()},
        "output": {"path": str(OUT_ROM.relative_to(ROOT)).replace('\\','/'), "sha256": sha(out).upper(), "checksum": f"{checksum:04X}"},
        "save": {"path": str(OUT_SAVE.relative_to(ROOT)).replace('\\','/'), "sha256": sha(save).upper()},
        "restored_proven_battle_metadata_count": len(restored),
        "restored_proven_battle_metadata": restored,
        "karama_orphan": {"abs": "6106EF", "before": "06 00", "after": "01 00", "runtime_evidence": "user screenshot: stray な after UC.0080 Karama Point"},
        "quarantine": {
            "battle_safe_snapshot_mismatch_whole_E518": quarantine_battle,
            "scenario_singleton_watch_same_fragment_family": singleton_watch,
            "note": "not modified without runtime/caller proof",
        },
        "checks": {
            "55_authoritative_metadata_rows_restored": True,
            "body_E518_token_preserved_for_all_55": True,
            "record_extent_terminator_next_boundary_preserved": True,
            "karama_singleton_extent_preserved": True,
            "unexpected_nonchecksum_diff": 0,
            "checksum_valid": True,
        },
        "test_protocol": [
            "Recheck Anavel Gato battle: portrait and 결국、가치관이 다른 듯하군…… remain normal.",
            "Re-enter the UC.0080 Karama Point title: stray な must be absent; next event/dialogue must continue normally.",
            "Spot-check several battle characters that previously showed portrait/sprite corruption; the 55 proven metadata regressions are now restored.",
        ],
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({
        "ok": True,
        "rom": report["output"],
        "save": report["save"],
        "restored": len(restored),
        "karama": report["karama_orphan"],
        "quarantine": report["quarantine"],
        "nonchecksum_diff_bytes": len(non_checksum),
        "report": str(OUT_REPORT.relative_to(ROOT)).replace('\\','/'),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
