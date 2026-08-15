#!/usr/bin/env python3
"""Fail-closed audit for runtime_structure_terminology_candidate.wsc."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

DEFAULT_TARGET = ROOT / "out/patch/runtime_structure_terminology_candidate.wsc"
DEFAULT_OUT = ROOT / "out/patch/runtime_structure_terminology_candidate_audit.json"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CAND_SAVE = ROOT / "sram/runtime_structure_terminology_candidate.sav"
REPORT = ROOT / "out/patch/runtime_structure_terminology_candidate_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
EXPECTED_MAIN_SHA = "f4f0ee2c0546e0794dae262b6246a190525763b6174d3423bec3ca20d8d2f212"
EXPECTED_TARGET_SHA = "6136fe7294f186952cfb1366bb4a38179484f4d86fe6f85af23beb3cb35e0ae0"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_at(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable {logical:06X}")
    return bytes(got[0]), int(got[1]) - stock_base(rom)


def strip_pad(text: str) -> str:
    return text.rstrip("　 \t")


def trailing_01(payload: bytes) -> int:
    n = 0
    for b in reversed(payload):
        if b != 1:
            break
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    target = args.target.read_bytes()
    parent = MAIN.read_bytes()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    d = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))

    failures: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, **detail: Any) -> None:
        row = {"name": name, "ok": bool(ok), **detail}
        checks.append(row)
        if not ok:
            failures.append(row)

    check("target_identity", sha(target) == EXPECTED_TARGET_SHA, sha256=sha(target))
    check("main_identity_unchanged", sha(parent) == EXPECTED_MAIN_SHA, sha256=sha(parent))
    check(
        "candidate_saveram_matches_main",
        CAND_SAVE.read_bytes() == MAIN_SAVE.read_bytes(),
        size=CAND_SAVE.stat().st_size,
    )
    stored = int.from_bytes(target[-2:], "little")
    calculated = sum(target[:-2]) & 0xFFFF
    check("checksum", stored == calculated == 0xDAB9, stored=f"{stored:04X}", calculated=f"{calculated:04X}")

    record_expect = {
        0x61E23D: ("18F2B801010101010101010101", "……음、　우선　티파를"),
        0x61E24B: ("F2C5010101010101010101010101", "안전한　곳에　데려가야겠지？"),
        0x59971D: ("173418F2B701010101010101010101010101010101", "내　이　손이　새빨갛게　타오른다！！"),
    }
    for logical, (expected_hex, expected_text) in record_expect.items():
        payload, term = payload_at(target, logical)
        prefix_len = 1 if logical == 0x61E23D else 3 if logical == 0x59971D else 0
        body = payload[prefix_len:]
        rendered = strip_pad(d.expand(body, tbl))
        check(
            f"native_record_{logical:06X}",
            payload.hex().upper() == expected_hex and rendered == expected_text and not body.startswith(b"\xE5\x18"),
            payload=payload.hex().upper(),
            rendered=rendered,
            terminator=f"{term:06X}",
        )

    for logical in (0x5D5982, 0x5D5B1F):
        payload, term = payload_at(target, logical)
        check(
            f"visible_82_removed_{logical:06X}",
            not payload.startswith(b"\x82") and payload.startswith(bytes.fromhex("E5184332")),
            payload=payload.hex().upper(),
            terminator=f"{term:06X}",
        )

    phrase_expect = {
        0x032D1: "킹・오브・하트의　이름을　걸고！！",
        0x0189D: "대차병！！",
        0x0FEFB: "십이왕방패대차병",
        0x102BD: "십이왕방패！　대차병！！",
    }
    for index, expected in phrase_expect.items():
        got = strip_pad(d.expand_index(index, tbl))
        check(f"phrase_{index:05X}", got == expected, rendered=got)

    ple_hits = []
    for index in list(range(d.count)) + list(range(0x1000, 0x1000 + d.ext3_count)):
        try:
            text = strip_pad(d.expand_index(index, tbl))
        except Exception:
            continue
        if "풀투" in text:
            ple_hits.append({"index": f"{index:05X}", "text": text})
    check("ple_two_residual_zero", not ple_hits, residuals=ple_hits)

    weapon_rows = report["weapon_padding"]["records"]
    weapon_bad = []
    for row in weapon_rows:
        logical = int(row["abs"], 16)
        payload, _term = payload_at(target, logical)
        if trailing_01(payload) > 1:
            weapon_bad.append({"abs": row["abs"], "pad": trailing_01(payload)})
    check(
        "weapon_padding_generalized",
        len(weapon_rows) == 126 and not weapon_bad,
        rewritten=len(weapon_rows),
        bad=weapon_bad,
        visible_pad_before_total=report["weapon_padding"]["visible_pad_before_total"],
        visible_pad_after_total=report["weapon_padding"]["visible_pad_after_total"],
    )
    skipped = report["weapon_padding"]["skipped_records"]
    check(
        "weapon_padding_fail_closed_skip",
        len(skipped) == 1 and skipped[0]["abs"] == "75D3CF" and payload_at(target, 0x75D3CF)[0] == payload_at(parent, 0x75D3CF)[0],
        skipped=skipped,
    )

    screen_examples = {}
    for logical in (0x75C9E6, 0x75CA18, 0x75C3C7, 0x75CB03):
        payload, term = payload_at(target, logical)
        screen_examples[f"{logical:06X}"] = {
            "rendered": strip_pad(d.expand(payload, tbl)),
            "payload": payload.hex().upper(),
            "visible_trailing_pad": trailing_01(payload),
            "terminator": f"{term:06X}",
        }
    check(
        "screen_weapon_examples",
        screen_examples["75C9E6"]["visible_trailing_pad"] == 0
        and screen_examples["75CA18"]["visible_trailing_pad"] <= 1
        and screen_examples["75C3C7"]["rendered"] == "대형　미사일　런처"
        and screen_examples["75CB03"]["rendered"] == "트리플　메가소닉　포",
        examples=screen_examples,
    )

    result = {
        "schema_version": 1,
        "generated_by": "tools/audit_runtime_structure_terminology_candidate.py",
        "ok": not failures,
        "target": {"path": str(args.target), "sha256": sha(target), "size": len(target)},
        "checks": checks,
        "failures": failures,
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "checks": len(checks), "failures": len(failures)}, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
