#!/usr/bin/env python3
"""Independent read-only audit of the reviewed 5D/5E prefix candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    le16,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

PATCH = ROOT / "out/patch"
PARENT = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "aux_false_prefix_cleanup_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/aux_false_prefix_cleanup_candidate.sav"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC_PATH = ROOT / "data/aux_false_prefix_cleanup_ko.json"
BUILD_REPORT = PATCH / "aux_false_prefix_cleanup_build_report.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META_PATH = PATCH / "ext_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"
DEFAULT_OUT = PATCH / "aux_false_prefix_cleanup_audit.json"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_TARGETS = 308


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff"
        for ch in text
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=PARENT)
    parser.add_argument("--candidate", type=Path, default=CANDIDATE)
    parser.add_argument("--candidate-save", type=Path, default=CANDIDATE_SAVE)
    parser.add_argument("--main-save", type=Path, default=MAIN_SAVE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    parent = bytes(load_rom(args.parent))
    candidate = bytes(load_rom(args.candidate))
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    if len(parent) != ROM_SIZE or len(candidate) != ROM_SIZE:
        failures.append({"kind": "rom_size"})
    if sha256(parent) != str(spec["parent_sha256"]).lower():
        failures.append({"kind": "parent_identity"})
    candidate_sha = sha256(candidate)
    if candidate_sha != ((build.get("candidate") or {}).get("sha256")):
        failures.append({"kind": "build_binding"})

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    candidate_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    sb = stock_base(parent)
    targets = list(spec.get("targets") or [])
    if len(targets) != EXPECTED_TARGETS:
        failures.append({"kind": "target_count", "actual": len(targets)})

    target_checks: list[dict[str, Any]] = []
    ranges: list[tuple[int, int]] = []
    target_abs = {row["abs"] for row in targets}
    for row in targets:
        logical = int(row["abs"], 16)
        file_start = sb + logical
        before = read_encoded_z_safe(parent, file_start, max_len=256)
        after = read_encoded_z_safe(candidate, file_start, max_len=256)
        if before is None or after is None:
            check = {"abs": row["abs"], "ok": False, "reason": "unreadable"}
            target_checks.append(check)
            failures.append(check)
            continue
        before_payload, before_term = bytes(before[0]), int(before[1])
        after_payload, after_term = bytes(after[0]), int(after[1])
        before_text = strip_pad(parent_dictionary.expand(before_payload, tbl))
        after_text = strip_pad(candidate_dictionary.expand(after_payload, tbl))
        lead = bytes.fromhex(row["lead_hex"])
        check = {
            "abs": row["abs"],
            "bank": row["bank"],
            "lead_hex": row["lead_hex"],
            "lead_text": row["lead_text"],
            "before_hex": before_payload.hex().upper(),
            "after_hex": after_payload.hex().upper(),
            "before_text": before_text,
            "after_text": after_text,
            "before_has_expected_lead": before_payload.startswith(lead),
            "after_starts_with_old_lead": after_payload.startswith(lead),
            "boundary_preserved": before_term == after_term
            and len(before_payload) == len(after_payload),
            "japanese_residual": has_japanese(after_text),
        }
        check["ok"] = (
            check["before_hex"] == row["expected_before_hex"]
            and check["after_hex"] == row["after_hex"]
            and before_text == row["expected_before_text"]
            and after_text == row["ko"]
            and check["before_has_expected_lead"]
            and not check["after_starts_with_old_lead"]
            and check["boundary_preserved"]
            and not check["japanese_residual"]
            and f"{after_term:06X}" == row["terminator_file"]
        )
        target_checks.append(check)
        if not check["ok"]:
            failures.append({"kind": "target", **check})
        ranges.append((file_start, file_start + len(before_payload)))

    control_checks: list[dict[str, Any]] = []
    for row in spec.get("manual_control_exclusions") or []:
        logical = int(row["abs"], 16)
        before = read_encoded_z_safe(parent, sb + logical, max_len=256)
        after = read_encoded_z_safe(candidate, sb + logical, max_len=256)
        ok = before is not None and after is not None and before == after
        check = {
            "abs": row["abs"],
            "lead_hex": row["lead_hex"],
            "lead_text": row["lead_text"],
            "ok": ok,
        }
        control_checks.append(check)
        if not ok:
            failures.append({"kind": "manual_control_changed", **check})

    fixed_checks: list[dict[str, Any]] = []
    for row in spec.get("already_fixed") or []:
        logical = int(row["abs"], 16)
        before = read_encoded_z_safe(parent, sb + logical, max_len=64)
        after = read_encoded_z_safe(candidate, sb + logical, max_len=64)
        rendered = "" if after is None else strip_pad(candidate_dictionary.expand(after[0], tbl))
        ok = before is not None and after is not None and before == after and not has_japanese(rendered)
        check = {"abs": row["abs"], "rendered": rendered, "ok": ok}
        fixed_checks.append(check)
        if not ok:
            failures.append({"kind": "already_fixed_regression", **check})

    changed = [
        offset
        for offset, (before, after) in enumerate(zip(parent, candidate))
        if before != after
    ]
    allowed = ranges + [(len(parent) - 2, len(parent))]
    unexpected = [
        offset
        for offset in changed
        if not any(start <= offset < end for start, end in allowed)
    ]
    if unexpected:
        failures.append(
            {
                "kind": "diff_confinement",
                "sample": [f"{value:06X}" for value in unexpected[:30]],
            }
        )

    # Every target must be unique and target ranges must not overlap.
    if len(target_abs) != len(targets):
        failures.append({"kind": "duplicate_target_abs"})
    sorted_ranges = sorted(ranges)
    overlaps = [
        (sorted_ranges[i - 1], sorted_ranges[i])
        for i in range(1, len(sorted_ranges))
        if sorted_ranges[i][0] < sorted_ranges[i - 1][1]
    ]
    if overlaps:
        failures.append({"kind": "overlapping_targets", "count": len(overlaps)})

    stored_checksum = le16(candidate, len(candidate) - 2)
    computed_checksum = sum(candidate[:-2]) & 0xFFFF
    if stored_checksum != computed_checksum:
        failures.append({"kind": "checksum"})

    save = args.candidate_save.read_bytes()
    main_save = args.main_save.read_bytes()
    save_ok = len(save) == len(main_save) == SAVE_SIZE and save == main_save
    if not save_ok:
        failures.append({"kind": "save_pair"})

    document = {
        "schema_version": 1,
        "generated_by": "tools/audit_aux_false_prefix_cleanup_candidate.py",
        "ok": not failures,
        "failures": failures,
        "parent": {
            "path": str(args.parent.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            "size": len(parent),
            "sha256": sha256(parent),
        },
        "candidate": {
            "path": str(args.candidate.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            "size": len(candidate),
            "sha256": candidate_sha,
            "checksum": f"{stored_checksum:04X}",
        },
        "save": {
            "size": len(save),
            "sha256": sha256(save),
            "matches_main": save_ok,
        },
        "counts": {
            "targets": len(target_checks),
            "targets_exact": sum(1 for row in target_checks if row["ok"]),
            "target_japanese_residuals": sum(
                1 for row in target_checks if row.get("japanese_residual")
            ),
            "manual_controls": len(control_checks),
            "manual_controls_preserved": sum(1 for row in control_checks if row["ok"]),
            "already_fixed_preserved": sum(1 for row in fixed_checks if row["ok"]),
            "changed_bytes": len(changed),
            "unexpected_changed_bytes": len(unexpected),
            "failures": len(failures),
        },
        "target_checks": target_checks,
        "manual_control_checks": control_checks,
        "already_fixed_checks": fixed_checks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"ok": document["ok"], "counts": document["counts"]}, ensure_ascii=False, indent=2))
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
