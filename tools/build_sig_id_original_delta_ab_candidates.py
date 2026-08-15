#!/usr/bin/env python3
"""Analyze the original-vs-patched Sig ID-command failure and build A/B probes.

Observed runtime chronology:
- Original Japanese ROM: Sig Wedna(Z) ID command works.
- Old 8 MiB Korean ROM, before expansion-bank dictionary: Event Error
  12288 / 29688 (3000:73F8).
- Current expansion-dictionary ROM: unrelated dictionary-looking dialogue is
  walked, then Event Error 12288 / 30804 (3000:7854).

Both offsets 5F:73F8 and 5F:7854 are byte-identical normal dictionary payload in
Original, old 8 MiB, pre-ext3, and current stock halves.  Therefore the failure
is not corrupted payload at those addresses: the event interpreter is running
while ROM1 bank 5F (dictionary) is mapped.

This tool emits two current-main-based diagnostic candidates:
A) dictionary IRQ guard: make the stock dictionary bank-map interval atomic
   (PUSHF/CLI before map, POPF after restore).  This tests asynchronous/reentrant
   ID-command processing while the text decoder temporarily owns ROM1.
B) original dictionary loader: restore only 7A:0700 to the original five-byte
   pointer load.  This disables the old extended FF-page helper while keeping
   the rest of the current ROM unchanged, separating helper-induced bank-state
   failure from the general text path.

Candidates are diagnostic only.  Main TIP and live SaveRAM are never modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import stock_base, update_ws_checksum, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
OLD8 = PATCH / "monoeye_ko_expanded_8mb.wsc"
PRE_EXT3 = PATCH / "monoeye_ko_expanded.pre_ext3.wsc"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"

OUT_A = PATCH / "sig_id_dictionary_irq_guard_candidate.wsc"
SAVE_A = ROOT / "sram/sig_id_dictionary_irq_guard_candidate.sav"
OUT_B = PATCH / "sig_id_original_dict_loader_probe_candidate.wsc"
SAVE_B = ROOT / "sram/sig_id_original_dict_loader_probe_candidate.sav"
REPORT = PATCH / "sig_id_original_delta_ab_report.json"

EXPECTED = {
    "original": "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0",
    "old8": "7b78a93526bb379659d61bbaaa91b479fd485e61465f2ffbda5d9e6f77acd4ef",
    "pre_ext3": "ce03b90aeaa24729312c8405c19f207b75c04cb022e4ba62ea84fba79bfdc4a2",
    "main": "25cedba62ea75021499dc1ad021af88bc5a7be9ad0f323adcddd870fecfe844c",
}
ROM8_SIZE = 8_388_608
ROM16_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Original dictionary branch in 7A:06CE.
DICT_MAP_ENTRY = 0x7A06E8
DICT_MAP_ENTRY_EXPECT = bytes.fromhex("9AB2DE008050B0DF9AB5DE0080")
DICT_MAP_CONTINUE = 0x7A06F5
DICT_RESTORE_EXIT = 0x7A074C
DICT_RESTORE_EXPECT = bytes.fromhex("589AB5DE0080EB50")
DICT_EPILOGUE = 0x7A07A4

# Fixed-bank 7F FF tail. Current non-FF runtime ends at 7F:FF17.
IRQ_CAVE = 0x7FFF18
IRQ_CAVE_LIMIT = 0x7FFFF0
SEG_7F = 0xF000
SEG_7A = 0xA000
BANK_GET = (0xDEB2, 0x8000)
BANK_SET = (0xDEB5, 0x8000)

# Old/current extended dictionary hook site.
DICT_LOAD = 0x7A0700
DICT_LOAD_HOOK = bytes.fromhex("E8EAF89090")
DICT_LOAD_ORIGINAL = bytes.fromhex("268B84CC7B")

# Error values are shown as decimal segment/offset.
ERROR_OLD = (0x3000, 0x73F8)
ERROR_CURRENT = (0x3000, 0x7854)
EVIDENCE_LOGICAL = (
    (0x5F73F8, 32),
    (0x5F7854, 32),
)


class BuildError(RuntimeError):
    pass


def digest(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": digest(payload)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(payload)
    os.replace(temp, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temp, path)


def far_jump(offset: int, segment: int) -> bytes:
    return b"\xEA" + (offset & 0xFFFF).to_bytes(2, "little") + (segment & 0xFFFF).to_bytes(2, "little")


def far_call(offset: int, segment: int) -> bytes:
    return b"\x9A" + (offset & 0xFFFF).to_bytes(2, "little") + (segment & 0xFFFF).to_bytes(2, "little")


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise BuildError("ROM size changed")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right and start is None:
            start = index
        elif left == right and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(before)))
    return runs


def covered(run: tuple[int, int], allowed: Iterable[tuple[int, int]]) -> bool:
    left, right = run
    return any(lo <= left and right <= hi for lo, hi in allowed)


def stock_half(data: bytes) -> bytes:
    base = stock_base(data)
    return data[base : base + ROM8_SIZE]


def file_at(data: bytes | bytearray, logical: int) -> int:
    return stock_base(data) + logical


def checksum_ok(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def common_error_payload_evidence(images: dict[str, bytes]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for logical, length in EVIDENCE_LOGICAL:
        payloads = {
            name: bytes(data[file_at(data, logical) : file_at(data, logical) + length])
            for name, data in images.items()
        }
        first = next(iter(payloads.values()))
        rows.append(
            {
                "logical": f"{logical:06X}",
                "length": length,
                "all_four_byte_identical": all(value == first for value in payloads.values()),
                "sha256": digest(first),
                "hex": first.hex().upper(),
                "classification": "stock bank-5F dictionary phrase payload before pointer table 5F:7BCC",
            }
        )
    return rows


def bank_diff_counts(original: bytes, target: bytes) -> dict[str, int]:
    o = stock_half(original)
    t = stock_half(target)
    return {
        f"{bank:02X}": sum(
            left != right
            for left, right in zip(o[bank << 16 : (bank + 1) << 16], t[bank << 16 : (bank + 1) << 16])
        )
        for bank in range(0x80)
        if o[bank << 16 : (bank + 1) << 16] != t[bank << 16 : (bank + 1) << 16]
    }


def build_irq_guard(parent: bytes) -> tuple[bytes, dict[str, Any]]:
    base = stock_base(parent)
    entry = base + DICT_MAP_ENTRY
    exit_site = base + DICT_RESTORE_EXIT
    cave = base + IRQ_CAVE
    if parent[entry : entry + len(DICT_MAP_ENTRY_EXPECT)] != DICT_MAP_ENTRY_EXPECT:
        raise BuildError("dictionary map entry identity drifted")
    if parent[exit_site : exit_site + len(DICT_RESTORE_EXPECT)] != DICT_RESTORE_EXPECT:
        raise BuildError("dictionary restore exit identity drifted")
    if any(value != 0xFF for value in parent[cave : base + IRQ_CAVE_LIMIT]):
        raise BuildError("IRQ guard cave is not all FF")

    # Entry: keep IF disabled across the complete map/read/recursive-expand/restore interval.
    entry_cave = bytearray()
    entry_cave += b"\x9C"  # pushf
    entry_cave += b"\xFA"  # cli
    entry_cave += far_call(*BANK_GET)
    entry_cave += b"\x50"  # push ax (saved ROM1 bank under original contract)
    entry_cave += b"\xB0\xDF"
    entry_cave += far_call(*BANK_SET)
    entry_cave += far_jump(DICT_MAP_CONTINUE & 0xFFFF, SEG_7A)

    exit_off = IRQ_CAVE + len(entry_cave)
    exit_cave = bytearray()
    exit_cave += b"\x58"  # pop saved bank; PUSHF remains below it
    exit_cave += far_call(*BANK_SET)
    exit_cave += b"\x9D"  # popf
    exit_cave += far_jump(DICT_EPILOGUE & 0xFFFF, SEG_7A)
    payload = bytes(entry_cave + exit_cave)
    if IRQ_CAVE + len(payload) > IRQ_CAVE_LIMIT:
        raise BuildError("IRQ guard cave overflow")

    out = bytearray(parent)
    entry_patch = far_jump(IRQ_CAVE & 0xFFFF, SEG_7F)
    out[entry : entry + len(DICT_MAP_ENTRY_EXPECT)] = entry_patch + b"\x90" * (len(DICT_MAP_ENTRY_EXPECT) - len(entry_patch))
    exit_patch = far_jump(exit_off & 0xFFFF, SEG_7F)
    out[exit_site : exit_site + len(DICT_RESTORE_EXPECT)] = exit_patch + b"\x90" * (len(DICT_RESTORE_EXPECT) - len(exit_patch))
    out[cave : cave + len(payload)] = payload
    checksum = update_ws_checksum(out)
    result = bytes(out)

    allowed = [
        (entry, entry + len(DICT_MAP_ENTRY_EXPECT)),
        (exit_site, exit_site + len(DICT_RESTORE_EXPECT)),
        (cave, cave + len(payload)),
        (len(parent) - 2, len(parent)),
    ]
    runs = diff_runs(parent, result)
    outside = [run for run in runs if not covered(run, allowed)]
    checks = {
        "entry_replaced_exact": result[entry : entry + len(DICT_MAP_ENTRY_EXPECT)] == entry_patch + b"\x90" * 8,
        "exit_replaced_exact": result[exit_site : exit_site + len(DICT_RESTORE_EXPECT)] == exit_patch + b"\x90" * 3,
        "cave_payload_exact": result[cave : cave + len(payload)] == payload,
        "pushf_cli_and_popf_balanced": payload.count(b"\x9C") == 1 and payload.count(b"\xFA") == 1 and payload.count(b"\x9D") == 1,
        "diffs_bounded": not outside,
        "checksum_valid": checksum_ok(result),
    }
    if not all(checks.values()):
        raise BuildError(f"IRQ candidate failed: {checks}, outside={outside}")
    return result, {
        "checks": checks,
        "entry_cave": f"7F:{IRQ_CAVE & 0xFFFF:04X}",
        "exit_cave": f"7F:{exit_off & 0xFFFF:04X}",
        "payload_hex": payload.hex().upper(),
        "payload_length": len(payload),
        "checksum": f"{checksum:04X}",
        "diff_runs": len(runs),
        "changed_bytes": sum(hi - lo for lo, hi in runs),
    }


def build_original_loader(parent: bytes) -> tuple[bytes, dict[str, Any]]:
    base = stock_base(parent)
    site = base + DICT_LOAD
    if parent[site : site + len(DICT_LOAD_HOOK)] != DICT_LOAD_HOOK:
        raise BuildError("current dictionary load hook identity drifted")
    out = bytearray(parent)
    out[site : site + len(DICT_LOAD_ORIGINAL)] = DICT_LOAD_ORIGINAL
    checksum = update_ws_checksum(out)
    result = bytes(out)
    allowed = [(site, site + 5), (len(parent) - 2, len(parent))]
    runs = diff_runs(parent, result)
    outside = [run for run in runs if not covered(run, allowed)]
    checks = {
        "original_loader_exact": result[site : site + 5] == DICT_LOAD_ORIGINAL,
        "extended_helper_bytes_preserved_but_unreferenced_from_0700": result[file_at(result, 0x7FFC8C) : file_at(result, 0x7FFC8C) + 4] == parent[file_at(parent, 0x7FFC8C) : file_at(parent, 0x7FFC8C) + 4],
        "diffs_bounded": not outside,
        "checksum_valid": checksum_ok(result),
    }
    if not all(checks.values()):
        raise BuildError(f"loader candidate failed: {checks}, outside={outside}")
    return result, {
        "checks": checks,
        "before_hex": DICT_LOAD_HOOK.hex().upper(),
        "after_hex": DICT_LOAD_ORIGINAL.hex().upper(),
        "checksum": f"{checksum:04X}",
        "diff_runs": len(runs),
        "changed_bytes": sum(hi - lo for lo, hi in runs),
    }


def main() -> int:
    paths = {
        "original": ORIGINAL,
        "old8": OLD8,
        "pre_ext3": PRE_EXT3,
        "main": MAIN,
    }
    images = {name: path.read_bytes() for name, path in paths.items()}
    sizes = {"original": ROM8_SIZE, "old8": ROM8_SIZE, "pre_ext3": ROM16_SIZE, "main": ROM16_SIZE}
    for name, data in images.items():
        if len(data) != sizes[name] or digest(data) != EXPECTED[name]:
            raise BuildError(f"{name} identity drifted")
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("live main SaveRAM missing or wrong size")

    evidence = common_error_payload_evidence(images)
    if not all(row["all_four_byte_identical"] for row in evidence):
        raise BuildError("error-offset payload is no longer identical across comparison ROMs")

    candidate_a, audit_a = build_irq_guard(images["main"])
    candidate_b, audit_b = build_original_loader(images["main"])
    atomic_bytes(OUT_A, candidate_a)
    shutil.copy2(MAIN_SAVE, SAVE_A)
    atomic_bytes(OUT_B, candidate_b)
    shutil.copy2(MAIN_SAVE, SAVE_B)

    main_unchanged = MAIN.read_bytes() == images["main"]
    save_unchanged = MAIN_SAVE.read_bytes() == save_snapshot
    if not main_unchanged or not save_unchanged:
        raise BuildError("main TIP or live SaveRAM changed during build")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_sig_id_original_delta_ab_candidates.py",
        "ok": True,
        "published": False,
        "status": "two_static_verified_diagnostic_candidates_pending_user_runtime_bisection",
        "inputs": {name: identity(path, images[name]) for name, path in paths.items()},
        "runtime_observation": {
            "original": "ID command succeeds",
            "old_pre_dictionary_expansion": {
                "decimal": [12288, 29688],
                "hex_segment_offset": [f"{ERROR_OLD[0]:04X}", f"{ERROR_OLD[1]:04X}"],
            },
            "current": {
                "symptom": "dictionary-like records auto-advance before failure",
                "decimal": [12288, 30804],
                "hex_segment_offset": [f"{ERROR_CURRENT[0]:04X}", f"{ERROR_CURRENT[1]:04X}"],
            },
        },
        "decisive_evidence": {
            "error_offsets_land_in_bank_5f_dictionary_payload": evidence,
            "stock_pointer_table_starts": "5F:7BCC",
            "interpretation": (
                "The event interpreter is consuming valid, unchanged dictionary payload while ROM1 bank 5F is mapped. "
                "Dictionary expansion changes the stopping offset but is not the original corruption source."
            ),
        },
        "original_delta": {
            "old8_bank_changed_bytes": bank_diff_counts(images["original"], images["old8"]),
            "pre_ext3_bank_changed_bytes": bank_diff_counts(images["original"], images["pre_ext3"]),
            "current_bank_changed_bytes": bank_diff_counts(images["original"], images["main"]),
            "common_runtime_sites": [
                {
                    "site": "7A:0700",
                    "original": DICT_LOAD_ORIGINAL.hex().upper(),
                    "patched": DICT_LOAD_HOOK.hex().upper(),
                    "role": "stock/extended dictionary pointer loader and ROM1 bank switch helper",
                },
                {
                    "site": "7A:073C/0740 and 7A:0818",
                    "role": "Hangul marker dispatch wrappers around the leaf decoder",
                },
                {
                    "site": "7A:07A0",
                    "role": "Hangul glyph-index tag store using WRAM state 19FF",
                },
            ],
        },
        "candidate_a": {
            "purpose": "test ROM1 dictionary-window reentrancy by making the complete map/expand/restore interval interrupt-atomic",
            "rom": identity(OUT_A, candidate_a),
            "save": identity(SAVE_A, save_snapshot),
            "audit": audit_a,
            "expected_interpretation": {
                "fixed": "ID-command/audio/event reentry occurred while bank 5F was temporarily mapped",
                "unchanged": "failure is not interrupt reentry; test candidate B next",
            },
        },
        "candidate_b": {
            "purpose": "separate the historical extended dictionary pointer helper from the rest of the text runtime",
            "rom": identity(OUT_B, candidate_b),
            "save": identity(SAVE_B, save_snapshot),
            "audit": audit_b,
            "known_visual_side_effect": "ordinary FF-page extended dictionary phrases may display incorrectly; judge only ID-command activation and Event Error",
            "expected_interpretation": {
                "fixed": "the extended dictionary 7A:0700 helper path is causal",
                "unchanged": "the common Hangul marker/decoder hook or upstream ID-command state must be traced next",
            },
        },
        "build_integrity": {
            "main_tip_unchanged": main_unchanged,
            "main_saveram_unchanged": save_unchanged,
            "candidate_saves_are_build_time_snapshots": True,
        },
        "test_order": [
            "Test candidate A first with the same Sig Wedna(Z) spirit.",
            "If A is unchanged, test candidate B and ignore unrelated FF-page text rendering defects.",
            "For each candidate record whether the ID command activates, whether unrelated dialogue streams, and the two Event Error numbers.",
        ],
        "promotion": "blocked; both outputs are diagnostic probes",
    }
    atomic_json(REPORT, report)
    print(
        json.dumps(
            {
                "ok": True,
                "evidence": evidence,
                "candidate_a": report["candidate_a"]["rom"],
                "candidate_b": report["candidate_b"]["rom"],
                "audit_a": audit_a,
                "audit_b": audit_b,
                "report": rel(REPORT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
