#!/usr/bin/env python3
"""Build two diagnostic ROMs to isolate ending-credit localization regressions.

C1 reverts only the post-2026-08-14 15:23 ending-credit resource/lifecycle
adjustments, returning the ending subsystem to the initially promoted 21-page
Korean implementation while preserving every later unrelated Main-TIP change.

C2 reverts the complete ending-credit localization subsystem to the ROM state
immediately before the 21-page Korean ending-credit promotion, again preserving
every later unrelated Main-TIP change.

These are diagnostic-only candidates.  They do not mutate Main TIP or live
SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from monoeye_rom import update_ws_checksum  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"

PRE_ENDING = PATCH / "backup/20260814_152320_pre_ending_credits_all_prepared/monoeye_ko_expanded.wsc"
INITIAL_21P = PATCH / "backup/20260815_001759_pre_ending_credits_page21_and_scouting_tail/monoeye_ko_expanded.wsc"
FINAL_ENDING = PATCH / "ending_credits_galmuri11_bitmap_page21_end_restore_candidate/monoeye_ko_expanded_ending_credits_galmuri11_bitmap_page21_end_restore_test.wsc"

C1 = PATCH / "ending_scene_steproll_postadjust_revert_candidate.wsc"
C2 = PATCH / "ending_scene_steproll_full_revert_candidate.wsc"
C1_SAVE = ROOT / "sram/ending_scene_steproll_postadjust_revert_candidate.sav"
C2_SAVE = ROOT / "sram/ending_scene_steproll_full_revert_candidate.sav"
REPORT = PATCH / "ending_scene_steproll_revert_ab_report.json"

EXPECTED = {
    "main": "6d7d855846b3caa5ce2369ee3fd56ba5fed3f2659cfd32d8292158c501448052",
    "pre": "3012695f01cab7a12f022efe897a8fca90a244648570dd6fd2d05f036d8f807f",
    "initial": "6c0357283aa06c44a146740e8392c4cdfc9dd7c26ee18a6bc7722bf4ac632cde",
    "final": "6ca50bb617b290619ebb47696aec4446fd1b7c59407e20e36726a54a122d1e0e",
}
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict:
    raw = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(raw), "sha256": sha(raw)}


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def changed_positions(a: bytes, b: bytes, include_checksum: bool = False) -> list[int]:
    end = len(a) if include_checksum else len(a) - 2
    return [i for i in range(end) if a[i] != b[i]]


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(a)))
    return out


def banks_for(indices: list[int]) -> dict[str, int]:
    c = Counter(i >> 16 for i in indices)
    return {f"{bank:02X}": count for bank, count in sorted(c.items())}


def patch_subset(current: bytes, final: bytes, target: bytes, positions: list[int]) -> bytes:
    result = bytearray(current)
    for i in positions:
        if current[i] != final[i]:
            raise BuildError(f"current no longer matches finalized ending source at {i:08X}")
        result[i] = target[i]
    update_ws_checksum(result)
    return bytes(result)


def validate_candidate(current: bytes, candidate: bytes, allowed: set[int]) -> list[tuple[int, int]]:
    diffs = [i for i in range(ROM_SIZE - 2) if current[i] != candidate[i]]
    outside = [i for i in diffs if i not in allowed]
    if outside:
        raise BuildError(f"candidate diff leaked outside declared set at {outside[0]:08X}")
    stored = int.from_bytes(candidate[-2:], "little")
    computed = sum(candidate[:-2]) & 0xFFFF
    if stored != computed:
        raise BuildError("candidate checksum invalid")
    return diff_runs(current, candidate)


def main() -> int:
    paths = [MAIN, LIVE_SAVE, PRE_ENDING, INITIAL_21P, FINAL_ENDING]
    if not all(p.is_file() for p in paths):
        missing = [str(p) for p in paths if not p.is_file()]
        raise BuildError(f"missing inputs: {missing}")

    current = MAIN.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    pre = PRE_ENDING.read_bytes()
    initial = INITIAL_21P.read_bytes()
    final = FINAL_ENDING.read_bytes()
    for name, data in (("main", current), ("pre", pre), ("initial", initial), ("final", final)):
        if len(data) != ROM_SIZE:
            raise BuildError(f"{name} size drifted: {len(data)}")
        if sha(data) != EXPECTED[name]:
            raise BuildError(f"{name} identity drifted: {sha(data)}")
    if len(live_save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drifted: {len(live_save)}")

    post_adjust = changed_positions(initial, final)
    full_ending = changed_positions(pre, final)
    if len(post_adjust) != 39_547:
        raise BuildError(f"post-adjust diff count drifted: {len(post_adjust)}")
    if len(full_ending) != 62_708:
        raise BuildError(f"full ending diff count drifted: {len(full_ending)}")
    # Post-adjust work can legitimately restore some initially changed bytes back
    # to their pre-localization value, so its delta need not be a strict subset
    # of PRE_ENDING -> FINAL_ENDING.  What matters is that Current still carries
    # FINAL_ENDING at every site touched by either diagnostic delta.
    diagnostic_sites = set(post_adjust) | set(full_ending)
    conflicts = [i for i in diagnostic_sites if current[i] != final[i]]
    if conflicts:
        raise BuildError(f"current ending subsystem drifted at {conflicts[0]:08X}")

    c1 = patch_subset(current, final, initial, post_adjust)
    c2 = patch_subset(current, final, pre, full_ending)
    c1_runs = validate_candidate(current, c1, set(post_adjust))
    c2_runs = validate_candidate(current, c2, set(full_ending))

    # C1 must exactly reproduce the initial 21-page ending bytes on its subset.
    if any(c1[i] != initial[i] for i in post_adjust):
        raise BuildError("C1 failed to restore initial 21-page ending state")
    # C2 must exactly reproduce pre-localization bytes on the entire ending set.
    if any(c2[i] != pre[i] for i in full_ending):
        raise BuildError("C2 failed to restore pre-localization ending state")

    atomic_bytes(C1, c1)
    atomic_bytes(C2, c2)
    atomic_copy(LIVE_SAVE, C1_SAVE)
    atomic_copy(LIVE_SAVE, C2_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_ending_scene_steproll_revert_ab_candidates.py",
        "ok": True,
        "status": "diagnostic_runtime_validation_required",
        "evidence": {
            "current_main_matches_finalized_ending_at_all_nonchecksum_ending_sites": True,
            "full_ending_change_bytes": len(full_ending),
            "post_initial_resource_lifecycle_adjustment_bytes": len(post_adjust),
            "full_ending_change_banks": banks_for(full_ending),
            "post_adjust_change_banks": banks_for(post_adjust),
        },
        "inputs": {
            "current_main": identity(MAIN, current),
            "pre_ending_localization": identity(PRE_ENDING, pre),
            "initial_21page_korean": identity(INITIAL_21P, initial),
            "finalized_ending_source": identity(FINAL_ENDING, final),
            "live_saveram": identity(LIVE_SAVE, live_save),
        },
        "candidates": {
            "C1_postadjust_revert": {
                **identity(C1, c1),
                "checksum": f"{int.from_bytes(c1[-2:], 'little'):04X}",
                "paired_saveram": identity(C1_SAVE),
                "changed_nonchecksum_bytes": len(post_adjust),
                "changed_banks": banks_for(post_adjust),
                "meaning": (
                    "Undo only the resource relocation/lifecycle changes made after the initial "
                    "21-page Korean ending-credit promotion. Korean ending credits remain enabled."
                ),
                "interpretation_if_fixed": (
                    "The post-promotion ending resource/lifecycle adjustment scripts introduced the glitch."
                ),
                "diff_runs": [[f"{a:08X}", f"{b:08X}"] for a, b in c1_runs],
            },
            "C2_full_ending_revert": {
                **identity(C2, c2),
                "checksum": f"{int.from_bytes(c2[-2:], 'little'):04X}",
                "paired_saveram": identity(C2_SAVE),
                "changed_nonchecksum_bytes": len(full_ending),
                "changed_banks": banks_for(full_ending),
                "meaning": (
                    "Undo the complete ending-credit Korean atlas/hooks/lifecycle subsystem back to "
                    "the 2026-08-14 15:23 pre-promotion state; later unrelated Main-TIP work stays intact."
                ),
                "interpretation_if_C1_fails_C2_fixes": (
                    "The initial cinematic ending-credit localization design itself introduced the glitch."
                ),
                "diff_runs": [[f"{a:08X}", f"{b:08X}"] for a, b in c2_runs],
            },
        },
        "runtime_order": [
            "Test C1 first from cold reset with its paired SaveRAM and replay the same ending scene.",
            "If C1 is still misaligned, test C2 the same way.",
            "Judge the middle cinematic graphic alignment; C2 intentionally removes Korean ending credits."
        ],
        "main_tip_unchanged": sha(MAIN.read_bytes()) == EXPECTED["main"],
        "live_saveram_unchanged": LIVE_SAVE.read_bytes() == live_save,
        "promotion": "blocked_diagnostic_only",
    }
    atomic_json(REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
