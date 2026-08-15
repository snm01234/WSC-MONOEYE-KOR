#!/usr/bin/env python3
"""Build narrow A/B probes for the two direct PCM wrappers.

The global PCM mute probe removed the ID-command noise, while muting the
sequenced voice branch at 7F:0574 did not. The remaining known calls to the PCM
start routine F000:0A46 are the direct wrappers at 78:7871 and 78:7886.

This tool creates three diagnostic-only candidates:
  A: mute the fixed-rate wrapper call at 78:787A only
  B: mute the parameterized wrapper call at 78:788E only
  AB: mute both direct wrapper calls

No main ROM or live SaveRAM is modified.
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
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base, update_ws_checksum

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
REPORT = ROOT / "out/patch/id_command_audio_direct_pcm_ab_report.json"

EXPECTED_MAIN_SHA256 = "9d5607ec320829ca0dc2dd8247fe2ca7da9040edef2cea4aa8fbd16f139ef358"
EXPECTED_SAVE_SIZE = 32768
CALL_BYTES = bytes.fromhex("9A460A00F0")  # LCALL F000:0A46
NOP_CALL = b"\x90" * len(CALL_BYTES)

PATCHES = {
    "A_fixed_rate": {
        "logical": 0x78787A,
        "wrapper": "78:7871",
        "setup": "MOV CX,0002",
        "rom": ROOT / "out/patch/id_command_audio_direct_fixed_rate_mute_probe_candidate.wsc",
        "save": ROOT / "sram/id_command_audio_direct_fixed_rate_mute_probe_candidate.sav",
    },
    "B_parameterized_rate": {
        "logical": 0x78788E,
        "wrapper": "78:7886",
        "setup": "MOV CX,BX",
        "rom": ROOT / "out/patch/id_command_audio_direct_parameterized_mute_probe_candidate.wsc",
        "save": ROOT / "sram/id_command_audio_direct_parameterized_mute_probe_candidate.sav",
    },
}


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve()),
        "size": len(data),
        "sha256": sha256(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def build_candidate(
    parent: bytes,
    save_snapshot: bytes,
    *,
    name: str,
    logicals: list[int],
    out_rom: Path,
    out_save: Path,
) -> dict[str, Any]:
    sb = stock_base(parent)
    candidate = bytearray(parent)
    patched: list[dict[str, Any]] = []
    expected_functional: set[int] = set()

    for logical in logicals:
        physical = sb + logical
        before = parent[physical : physical + len(CALL_BYTES)]
        if before != CALL_BYTES:
            raise BuildError(
                f"direct PCM call drifted at {logical:06X}: "
                f"expected {CALL_BYTES.hex().upper()} got {before.hex().upper()}"
            )
        candidate[physical : physical + len(CALL_BYTES)] = NOP_CALL
        expected_functional.update(range(physical, physical + len(CALL_BYTES)))
        patched.append(
            {
                "logical_abs": f"{logical:06X}",
                "physical_abs": f"{physical:07X}",
                "before": CALL_BYTES.hex().upper(),
                "after": NOP_CALL.hex().upper(),
                "before_instruction": "LCALL F000:0A46",
                "after_instruction": "NOP x5",
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    functional_diffs = {
        index
        for index, (left, right) in enumerate(zip(parent, candidate_bytes))
        if left != right and index < len(parent) - 2
    }
    if functional_diffs != expected_functional:
        raise BuildError(
            f"unexpected functional diff set for {name}: "
            f"expected={sorted(expected_functional)} actual={sorted(functional_diffs)}"
        )
    if sha256(MAIN.read_bytes()) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP changed during build")
    if MAIN_SAVE.read_bytes() != save_snapshot:
        raise BuildError("main SaveRAM changed during build")

    atomic_bytes(out_rom, candidate_bytes)
    shutil.copy2(MAIN_SAVE, out_save)
    return {
        "name": name,
        "candidate": identity(out_rom, candidate_bytes),
        "candidate_save": {
            **identity(out_save, save_snapshot),
            "policy": "test-only snapshot; never promote SaveRAM",
        },
        "patches": patched,
        "diff": {
            "functional_changed_bytes": len(functional_diffs),
            "checksum": f"{checksum:04X}",
            "stored_checksum_bytes": candidate_bytes[-2:].hex().upper(),
        },
    }


def main() -> int:
    parent = bytes(load_rom(MAIN))
    save_snapshot = MAIN_SAVE.read_bytes()
    if sha256(parent) != EXPECTED_MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    if len(save_snapshot) != EXPECTED_SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    a = PATCHES["A_fixed_rate"]
    b = PATCHES["B_parameterized_rate"]
    results = [
        build_candidate(
            parent,
            save_snapshot,
            name="A_fixed_rate_only",
            logicals=[int(a["logical"])],
            out_rom=Path(a["rom"]),
            out_save=Path(a["save"]),
        ),
        build_candidate(
            parent,
            save_snapshot,
            name="B_parameterized_only",
            logicals=[int(b["logical"])],
            out_rom=Path(b["rom"]),
            out_save=Path(b["save"]),
        ),
        build_candidate(
            parent,
            save_snapshot,
            name="AB_both_direct_wrappers",
            logicals=[int(a["logical"]), int(b["logical"])],
            out_rom=ROOT / "out/patch/id_command_audio_direct_both_mute_probe_candidate.wsc",
            out_save=ROOT / "sram/id_command_audio_direct_both_mute_probe_candidate.sav",
        ),
    ]

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_id_command_audio_direct_pcm_ab_probes.py",
        "ok": True,
        "canonical": False,
        "promotion_allowed": False,
        "status": "static_verified_pending_user_runtime_test",
        "evidence": {
            "global_pcm_mute": "noise disappeared",
            "sequenced_voice_branch_mute": "noise remained",
            "inference": "noise is started by a direct PCM path or an undiscovered indirect call",
        },
        "parent": identity(MAIN, parent),
        "wrappers": {
            "A_fixed_rate": {
                "entry": "78:7871",
                "call": "78:787A",
                "rate_setup": "CX=0002",
            },
            "B_parameterized_rate": {
                "entry": "78:7886",
                "call": "78:788E",
                "rate_setup": "CX=BX",
            },
        },
        "candidates": results,
        "runtime_interpretation": {
            "A_only_removes_noise": "ID command uses fixed-rate direct wrapper 78:7871.",
            "B_only_removes_noise": "ID command uses parameterized direct wrapper 78:7886.",
            "A_and_B_each_remove_noise": "Both wrappers converge on the same offending command path; trace their shared caller/table.",
            "neither_individual_but_AB_removes_noise": "Two direct PCM starts overlap during the ID command.",
            "AB_still_has_noise": "An indirect/hidden call reaches F000:0A46, or noise begins after PCM is already active; runtime WRAM trace is required.",
        },
        "promotion": "blocked_diagnostic_probes",
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
