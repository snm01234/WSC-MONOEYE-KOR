#!/usr/bin/env python3
"""Independent read-only audit for the 2026-08-02 UI/menu/dialogue candidate."""
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
from extract_script import split_prefix_body
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
SPEC = ROOT / "data/ui_menu_dialogue_followup_ko.json"
BUILD_REPORT = ROOT / "out/patch/ui_menu_dialogue_followup_report.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_TARGET = ROOT / "out/patch/ui_menu_dialogue_followup_candidate.wsc"
DEFAULT_SAVE = ROOT / "sram/ui_menu_dialogue_followup_candidate.sav"
DEFAULT_OUT = ROOT / "out/patch/ui_menu_dialogue_followup_audit.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def has_japanese(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in text)


def payload_at(rom: bytes, logical: int) -> bytes:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise ValueError(f"unreadable zstring at {logical:06X}")
    return bytes(got[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--save", type=Path, default=DEFAULT_SAVE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    parent = bytes(load_rom(MAIN))
    target = bytes(load_rom(args.target))
    save = args.save.read_bytes()
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    report = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    d_target = make_dictionary_ext3(target, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    target_checks: list[dict[str, Any]] = []
    report_by_abs = {str(row["abs"]): row for row in report.get("records") or []}
    for source in spec.get("records") or []:
        address = str(source["abs"])
        logical = int(address, 16)
        row = report_by_abs.get(address)
        if row is None:
            failures.append({"abs": address, "reason": "missing_build_report_row"})
            continue
        payload = payload_at(target, logical)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        if prefix and payload[: len(prefix)] != prefix:
            failures.append({"abs": address, "reason": "prefix_changed"})
            continue
        body = payload[len(prefix) :]
        rendered = clean(d_target.expand(body, tbl))
        expected = clean(normalize_ko_text(str(source["ko"])))
        check = {
            "abs": address,
            "category": source["category"],
            "expected": expected,
            "rendered": rendered,
            "payload_hex": payload.hex().upper(),
            "prefix_hex": prefix.hex().upper(),
            "japanese_residual": has_japanese(rendered),
            "ok": rendered == expected and not has_japanese(rendered),
        }
        target_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    shared_checks: list[dict[str, Any]] = []
    for source in spec.get("shared_dictionary") or []:
        index = int(str(source["index"]), 16)
        rendered = clean(d_target.expand_index(index, tbl))
        expected = clean(normalize_ko_text(str(source["ko"])))
        check = {
            "index": f"{index:04X}",
            "before": clean(d_parent.expand_index(index, tbl)),
            "expected": expected,
            "rendered": rendered,
            "ok": rendered == expected,
        }
        shared_checks.append(check)
        if not check["ok"]:
            failures.append(check)

    examples: dict[str, str] = {}
    for logical in (
        0x75B390,
        0x75B393,
        0x75B396,
        0x75B3CA,
        0x75B3CE,
        0x75B3E1,
        0x75B405,
        0x75B321,
        0x75B325,
        0x75B43A,
        0x75B6FF,
        0x600E66,
        0x600EA6,
        0x6028A4,
    ):
        payload = payload_at(target, logical)
        prefix, body, kind = split_prefix_body(payload)
        if kind != "dialogue":
            prefix, body = b"", payload
        examples[f"{logical:06X}"] = clean(d_target.expand(body, tbl))

    expected_examples = {
        "75B390": "아군",
        "75B393": "우군",
        "75B396": "적군",
        "75B3CA": "범용",
        "75B3CE": "우주",
        "75B3E1": "ID효과：",
        "75B405": "ID커맨드효과",
        "75B321": "불러오기",
        "75B325": "저장",
        "75B43A": "조종계",
        "75B6FF": "노멀",
        "600E66": "하하하핫！",
        "600EA6": "하하하핫！",
        "6028A4": "감사히\u3000받아두겠습니다。",
    }
    example_checks = {
        key: examples.get(key) == normalize_ko_text(value)
        for key, value in expected_examples.items()
    }
    if not all(example_checks.values()):
        failures.append(
            {
                "kind": "example_render",
                "failed": [key for key, ok in example_checks.items() if not ok],
                "actual": examples,
            }
        )

    general_payload = payload_at(target, 0x75B3CA)
    general_format = {
        "payload_hex": general_payload.hex().upper(),
        "length": len(general_payload),
        "uses_compact3": len(general_payload) == 3
        and general_payload[:2] == b"\xE5\x19",
        "contains_padding": b"\x01" in general_payload,
    }
    general_format["ok"] = (
        general_format["uses_compact3"] and not general_format["contains_padding"]
    )
    if not general_format["ok"]:
        failures.append({"kind": "general_compact3", **general_format})

    dialogue_prefix_checks: list[dict[str, Any]] = []
    for address in ("600E66", "600EA6", "6028A4"):
        logical = int(address, 16)
        before = payload_at(parent, logical)
        after = payload_at(target, logical)
        before_prefix, _before_body, before_kind = split_prefix_body(before)
        after_prefix, _after_body, after_kind = split_prefix_body(after)
        ok = before_kind == after_kind == "dialogue" and before_prefix == after_prefix
        check = {
            "abs": address,
            "before_prefix_hex": before_prefix.hex().upper(),
            "after_prefix_hex": after_prefix.hex().upper(),
            "ok": ok,
        }
        dialogue_prefix_checks.append(check)
        if not ok:
            failures.append(check)

    candidate_sha = sha256(target)
    report_sha = ((report.get("candidate_rom") or {}).get("sha256"))
    identity = {
        "target_size": len(target),
        "target_sha256": candidate_sha,
        "report_sha256": report_sha,
        "report_bound": candidate_sha == report_sha,
        "save_size": len(save),
        "save_sha256": sha256(save),
        "save_matches_main": save == (ROOT / "sram/monoeye_ko_expanded.sav").read_bytes(),
    }
    if len(target) != 16_777_216 or not identity["report_bound"]:
        failures.append({"kind": "candidate_identity", **identity})
    if len(save) != 32_768 or not identity["save_matches_main"]:
        failures.append({"kind": "save_pair", **identity})

    document = {
        "schema_version": 1,
        "generated_by": "tools/audit_ui_menu_dialogue_followup.py",
        "target": {
            "path": str(args.target.resolve()),
            "size": len(target),
            "sha256": candidate_sha,
        },
        "counts": {
            "target_records": len(target_checks),
            "target_exact": sum(1 for row in target_checks if row["ok"]),
            "target_failures": len([row for row in target_checks if not row["ok"]]),
            "shared_dictionary": len(shared_checks),
            "shared_exact": sum(1 for row in shared_checks if row["ok"]),
            "japanese_residuals_in_targets": sum(
                1 for row in target_checks if row["japanese_residual"]
            ),
        },
        "identity": identity,
        "general_compact3": general_format,
        "shared_dictionary": shared_checks,
        "dialogue_prefix": dialogue_prefix_checks,
        "examples": examples,
        "example_checks": example_checks,
        "targets": target_checks,
        "failures": failures,
        "ok": not failures,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "counts": document["counts"],
                "general_compact3": general_format,
                "examples": examples,
                "ok": document["ok"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
