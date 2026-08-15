#!/usr/bin/env python3
"""Combine the independently built name-mapping and spirit-text candidates.

Both inputs must be byte-for-byte descendants of the same guarded main TIP.
Only the terminal WonderSwan checksum bytes may overlap with different values;
the checksum is recomputed after applying both non-checksum change sets.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
NAME = PATCH / "main_tip_name_mapping_consistency_candidate.wsc"
SPIRIT = PATCH / "spirit_mental_cmd_mixed_quote_candidate.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT = PATCH / "main_tip_name_mapping_spirit_combined_candidate.wsc"
OUT_SAVE = ROOT / "sram/main_tip_name_mapping_spirit_combined_candidate.sav"
REPORT = PATCH / "main_tip_name_mapping_spirit_combined_candidate_report.json"

EXPECTED_MAIN = "b0438b51f0a6f5fab94418433f3fac6c551ab1ee8f434627ee4e42816928216a"
EXPECTED_NAME = "15d34aa387b78e87110b43723b2ccd3cccf9301601a2a57f165a6e652e86e590"
EXPECTED_SPIRIT = "f730c831a70a6f55fe563c121b645eb6e54a088671d4411191ae0f5ed8518dfe"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | bytearray | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else bytes(data)
    return {"path": rel(path), "size": len(payload), "sha256": sha(payload)}


def atomic_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_copy(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with source.open("rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, target)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def changed(base: bytes, candidate: bytes) -> set[int]:
    return {offset for offset, pair in enumerate(zip(base, candidate)) if pair[0] != pair[1]}


def checksum_valid(data: bytes | bytearray) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def main() -> int:
    for path in (MAIN, NAME, SPIRIT):
        if not path.is_file() or path.stat().st_size != ROM_SIZE:
            raise BuildError(f"missing or wrong-sized ROM: {path}")
    if not MAIN_SAVE.is_file() or MAIN_SAVE.stat().st_size != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong-sized")

    base = MAIN.read_bytes()
    name = NAME.read_bytes()
    spirit = SPIRIT.read_bytes()
    identities = {"main": sha(base), "name": sha(name), "spirit": sha(spirit)}
    expected = {"main": EXPECTED_MAIN, "name": EXPECTED_NAME, "spirit": EXPECTED_SPIRIT}
    if identities != expected:
        raise BuildError(f"input identity drift: {identities}")
    if not all(checksum_valid(data) for data in (base, name, spirit)):
        raise BuildError("one or more input checksums are invalid")

    name_changes = changed(base, name)
    spirit_changes = changed(base, spirit)
    overlap = sorted(name_changes & spirit_changes)
    conflicting = [offset for offset in overlap if name[offset] != spirit[offset]]
    if any(offset < ROM_SIZE - 2 for offset in conflicting):
        raise BuildError(f"non-checksum candidate conflict: {conflicting[:20]}")

    combined = bytearray(base)
    for offset in name_changes - {ROM_SIZE - 2, ROM_SIZE - 1}:
        combined[offset] = name[offset]
    for offset in spirit_changes - {ROM_SIZE - 2, ROM_SIZE - 1}:
        combined[offset] = spirit[offset]
    checksum = sum(combined[:-2]) & 0xFFFF
    combined[-2:] = checksum.to_bytes(2, "little")
    if not checksum_valid(combined):
        raise BuildError("combined checksum failed")

    atomic_bytes(OUT, bytes(combined))
    atomic_copy(MAIN_SAVE, OUT_SAVE)
    output_sha = sha(combined)
    checks = {
        "inputs_share_exact_parent": True,
        "non_checksum_conflicts_zero": not any(offset < ROM_SIZE - 2 for offset in conflicting),
        "name_delta_preserved": all(combined[offset] == name[offset] for offset in name_changes if offset < ROM_SIZE - 2),
        "spirit_delta_preserved": all(combined[offset] == spirit[offset] for offset in spirit_changes if offset < ROM_SIZE - 2),
        "checksum_exact": checksum_valid(combined),
        "main_unchanged": sha(MAIN.read_bytes()) == EXPECTED_MAIN,
        "save_matches_live": sha(OUT_SAVE.read_bytes()) == sha(MAIN_SAVE.read_bytes()),
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_main_tip_name_mapping_spirit_combined_candidate.py",
        "ok": all(checks.values()),
        "promotion_allowed": all(checks.values()),
        "parent": identity(MAIN, base),
        "sources": {"name_mapping": identity(NAME, name), "spirit_mental_cmd": identity(SPIRIT, spirit)},
        "candidate": identity(OUT, combined),
        "candidate_save": identity(OUT_SAVE),
        "applied_count": 54,
        "change_counts": {
            "name_bytes_including_checksum": len(name_changes),
            "spirit_bytes_including_checksum": len(spirit_changes),
            "overlap_bytes": len(overlap),
            "conflicting_overlap_bytes": len(conflicting),
            "non_checksum_conflicts": sum(offset < ROM_SIZE - 2 for offset in conflicting),
            "combined_bytes_including_checksum": len(changed(base, bytes(combined))),
        },
        "overlap": [
            {
                "offset": f"{offset:08X}",
                "base": f"{base[offset]:02X}",
                "name": f"{name[offset]:02X}",
                "spirit": f"{spirit[offset]:02X}",
                "checksum_byte": offset >= ROM_SIZE - 2,
            }
            for offset in overlap
        ],
        "checksum": f"{checksum:04X}",
        "checks": checks,
    }
    atomic_json(REPORT, report)
    if not report["ok"]:
        raise BuildError(f"combined checks failed: {checks}")
    print(json.dumps({"ok": True, "candidate_sha256": output_sha, "checksum": f"{checksum:04X}"}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
