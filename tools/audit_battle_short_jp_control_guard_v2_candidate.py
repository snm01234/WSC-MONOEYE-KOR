#!/usr/bin/env python3
"""Independent audit for battle_short_jp_control_guard_v2_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE = ROOT / "out/patch/battle_short_jp_control_guard_v2_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/battle_short_jp_control_guard_v2_candidate.sav"
BUILD_REPORT = ROOT / "out/patch/battle_short_jp_control_guard_v2_candidate_report.json"
STRUCTURE = ROOT / "out/patch/battle_short_jp_control_guard_v2_structure.json"
FALSE_SEGPTR = ROOT / "out/patch/battle_short_jp_control_guard_v2_false_segptr.json"
OUT = ROOT / "out/patch/battle_short_jp_control_guard_v2_candidate_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META_PATH = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META_PATH = ROOT / "out/patch/ext3_dictionary_meta.json"

EXPECTED_MAIN_SHA256 = "33b77347f1c969c2751b24b3ec3479e63c3b5146df4015cbad3bdc0d7eaab4e1"
EXPECTED_CANDIDATE_SHA256 = "5bd6ac50ae7a80b922c79dfa43eaa3b43af053005467f53d9a57dc4c8e7444fc"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STOCK_INDEX = 0x0360
STOCK_PTR = 0x4386


class AuditError(RuntimeError):
    pass


def digest(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve()).replace("\\", "/")
    return {"path": shown, "size": len(payload), "sha256": digest(payload)}


def atomic_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    base = stock_base(rom)
    result = read_encoded_z_safe(rom, base + logical, max_len=64)
    if result is None:
        raise AuditError(f"unreadable record {logical:06X}")
    return bytes(result[0]), int(result[1]) - base


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
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


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    left, right = run
    return any(left >= lo and right <= hi for lo, hi in allowed)


def main() -> int:
    main_rom = MAIN.read_bytes()
    main_save = MAIN_SAVE.read_bytes()
    candidate = CANDIDATE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    if len(main_rom) != ROM_SIZE or digest(main_rom) != EXPECTED_MAIN_SHA256:
        raise AuditError("main TIP identity drifted")
    if len(main_save) != SAVE_SIZE:
        raise AuditError("main SaveRAM size drifted")
    if len(candidate) != ROM_SIZE or digest(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("candidate identity drifted")
    if candidate_save != main_save:
        raise AuditError("candidate SaveRAM no longer matches current live main SaveRAM")

    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    structure = json.loads(STRUCTURE.read_text(encoding="utf-8"))
    false_segptr = json.loads(FALSE_SEGPTR.read_text(encoding="utf-8"))
    if build.get("ok") is not True or build.get("published") is not False:
        raise AuditError("build report status is invalid")
    if str((build.get("candidate") or {}).get("sha256") or "") != EXPECTED_CANDIDATE_SHA256:
        raise AuditError("build report candidate binding mismatch")

    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        candidate,
        load_ext_meta(EXT_META_PATH),
        load_ext_meta(EXT3_META_PATH),
    )
    stock = Dictionary(candidate)

    short_payload, short_term = payload_at(candidate, 0x594E1E)
    gato_payload, gato_term = payload_at(candidate, 0x5951F6)
    short_text = dictionary.expand(short_payload[3:], tbl).rstrip("　 \t")
    gato_text_ext3 = dictionary.expand(gato_payload[3:], tbl).rstrip("　 \t")
    gato_text_stock = stock.expand(gato_payload[3:], tbl).rstrip("　 \t")

    control_rows: list[dict[str, Any]] = []
    control_ok = True
    for logical, expected in {
        0x5951FF: bytes.fromhex("17280828"),
        0x595204: bytes.fromhex("171C080F"),
    }.items():
        payload, terminator = payload_at(candidate, logical)
        prefix, body, kind = split_prefix_body(payload)
        ok = payload == expected and prefix == expected and kind == "control" and not body
        control_ok &= ok
        control_rows.append(
            {
                "abs": f"{logical:06X}",
                "payload_hex": payload.hex().upper(),
                "terminator": f"{terminator:06X}",
                "kind": kind,
                "prefix_hex": prefix.hex().upper(),
                "body_bytes": len(body),
                "ok": ok,
            }
        )

    phrase_ext3 = dictionary.expand_index(STOCK_INDEX, tbl)
    phrase_stock = stock.expand_index(STOCK_INDEX, tbl)
    raw_phrase = stock.raw_entry(STOCK_INDEX)
    external = external_occurrence_map(candidate, ext3_aware=True, wanted={STOCK_INDEX})
    nested = nested_occurrence_map(dictionary, wanted={STOCK_INDEX}, ext3_aware=True)
    token_offsets = {
        int(str(row.get("token_abs") or "0"), 16)
        for row in external.get(STOCK_INDEX, [])
    }

    base = stock_base(candidate)
    slot_file = base + 0x5F0000 + STOCK_PTR
    allowed = [
        (base + 0x594E21, base + 0x594E26),
        (base + 0x5951F9, base + 0x5951FD),
        (slot_file, slot_file + 7),
        (len(candidate) - 2, len(candidate)),
    ]
    runs = diff_runs(main_rom, candidate)
    unaccounted = [run for run in runs if not covered(run, allowed)]

    checks = {
        "short_record_existing_ext3_exact": (
            short_payload == bytes.fromhex("173418E518183001")
            and short_term == 0x594E26
            and short_text == "……흥！"
        ),
        "gato_record_inplace_stock_exact": (
            gato_payload == bytes.fromhex("173418F3600101")
            and gato_term == 0x5951FD
            and gato_text_ext3 == "가토－－！！"
            and gato_text_stock == "가토－－！！"
            and b"\xE5\x18" not in gato_payload[3:]
        ),
        "stock_slot_pointer_unchanged": stock.ptrs[STOCK_INDEX] == STOCK_PTR,
        "stock_slot_nested_components_exact": (
            raw_phrase == bytes.fromhex("F58CF206F044")
            and phrase_ext3 == "가토－－！！"
            and phrase_stock == "가토－－！！"
        ),
        "dedicated_reference_exact": (
            token_offsets == {0x5951F9}
            and len(external.get(STOCK_INDEX, [])) == 1
            and not nested.get(STOCK_INDEX)
        ),
        "control_records_exact": control_ok,
        "diffs_allowlisted": not unaccounted,
        "checksum_valid": (sum(candidate[:-2]) & 0xFFFF) == int.from_bytes(candidate[-2:], "little"),
        "structure_scan_clean": (
            structure.get("ok") is True
            and int(structure.get("issues") or 0) == 0
            and str(((structure.get("inputs") or {}).get("target") or {}).get("sha256") or "")
            == EXPECTED_CANDIDATE_SHA256
        ),
        "false_segptr_scan_clean": (
            false_segptr.get("ok") is True
            and int(false_segptr.get("sites_found") or 0) == 0
            and str(((false_segptr.get("inputs") or {}).get("target") or {}).get("sha256") or "")
            == EXPECTED_CANDIDATE_SHA256
        ),
        "main_tip_unchanged": digest(MAIN.read_bytes()) == EXPECTED_MAIN_SHA256,
        "main_saveram_unchanged": MAIN_SAVE.read_bytes() == main_save,
    }
    if not all(checks.values()):
        raise AuditError(f"v2 audit failed: {checks}")

    report = {
        "schema_version": 2,
        "generated_by": "tools/audit_battle_short_jp_control_guard_v2_candidate.py",
        "ok": True,
        "published": False,
        "main_tip": identity(MAIN, main_rom),
        "candidate": identity(CANDIDATE, candidate),
        "candidate_saveram": identity(CANDIDATE_SAVE, candidate_save),
        "build_report": identity(BUILD_REPORT),
        "structure_report": identity(STRUCTURE),
        "false_segptr_report": identity(FALSE_SEGPTR),
        "checks": checks,
        "decoded": {
            "594E1E": short_text,
            "5951F6_ext3_decoder": gato_text_ext3,
            "5951F6_stock_decoder": gato_text_stock,
            "stock_0360": phrase_stock,
            "stock_0360_raw": raw_phrase.hex().upper(),
        },
        "control_records": control_rows,
        "diff_runs": [
            {"start": f"{left:08X}", "end_exclusive": f"{right:08X}", "length": right - left}
            for left, right in runs
        ],
        "unaccounted_diff_runs": [
            {"start": f"{left:08X}", "end_exclusive": f"{right:08X}"}
            for left, right in unaccounted
        ],
        "promotion": "blocked_pending_user_visual_verification",
    }
    atomic_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
