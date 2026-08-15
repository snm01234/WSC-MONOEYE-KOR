#!/usr/bin/env python3
"""Independent static audit for the eight residual-plaque Korean test ROM."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/id_command_residual_plaques_ko_candidate.wsc"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAVE = ROOT / "sram/id_command_residual_plaques_ko_candidate.sav"
SPEC = ROOT / "data/id_command_residual_plaques_ko.json"
BUILD_REPORT = ROOT / "out/patch/id_command_residual_plaques_ko_candidate_report.json"
OUT = ROOT / "out/patch/id_command_residual_plaques_ko_candidate_audit.json"

EXPECTED_PARENT = "cef2d40d7a0568e3add4025d8ebc6f5e6340f0a2b545a5f88decc6d28e3375f5"
EXPECTED_STOCK = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
EXPECTED_SAVE = "697826d2e0d506ae441526706dc6b289c91bc28a7d49cffda713390685367ae1"
BASE = 0x800000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def diff_runs(left: bytes, right: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = index
        elif a == b and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(left)))
    return runs


def merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    output: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if output and start <= output[-1][1]:
            output[-1] = (output[-1][0], max(output[-1][1], end))
        else:
            output.append((start, end))
    return output


def target_size(storage: str) -> int:
    if storage == "body_40x16_plus_shared_right_cap":
        return 320
    if storage == "body_32x16_plus_shared_right_cap":
        return 256
    if storage == "body_32x16_plus_shared_both_caps":
        return 256
    if storage == "full_40x16":
        return 320
    if storage == "sparse_40x16_insert_shared_mid_column":
        return 320
    if storage == "full_48x16":
        return 384
    raise ValueError(storage)


def main() -> int:
    parent = MAIN.read_bytes()
    candidate = CANDIDATE.read_bytes()
    stock = STOCK.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    rows = list(spec.get("plaques") or [])
    manifest_by_logical = {
        str(row["logical"]): row for row in build.get("targets") or []
    }

    failures: list[str] = []
    if sha256(parent) != EXPECTED_PARENT:
        failures.append("parent hash drift")
    if sha256(stock) != EXPECTED_STOCK:
        failures.append("stock hash drift")
    if sha256(live_save) != EXPECTED_SAVE:
        failures.append("live SaveRAM hash drift")
    if len(parent) != len(candidate) or len(parent) != 16_777_216:
        failures.append("ROM size mismatch")
    if len(rows) != 8 or len(manifest_by_logical) != 8:
        failures.append("target inventory mismatch")

    intervals: list[tuple[int, int]] = []
    targets: list[dict[str, Any]] = []
    shared_left_top = stock[0x4C44D4 : 0x4C44D4 + 32]
    shared_left_bottom = stock[0x4C4594 : 0x4C4594 + 32]
    common_right_top = stock[0x4C46F4 : 0x4C46F4 + 32]
    common_right_bottom = stock[0x4C47B4 : 0x4C47B4 + 32]
    for row in rows:
        logical_text = str(row["logical"])
        logical = int(logical_text, 16)
        size = target_size(str(row["storage"]))
        physical = BASE + logical
        source = parent[physical : physical + size]
        stock_source = stock[logical : logical + size]
        target = candidate[physical : physical + size]
        manifest = manifest_by_logical.get(logical_text) or {}
        source_stock_exact = source == stock_source
        changed = source != target
        report_hashes_exact = (
            manifest.get("source_sha256") == sha256(source)
            and manifest.get("target_sha256") == sha256(target)
        )
        if not source_stock_exact:
            failures.append(f"source not stock-exact: {logical_text}")
        if not changed:
            failures.append(f"target unchanged: {logical_text}")
        if not report_hashes_exact:
            failures.append(f"build report hash mismatch: {logical_text}")
        if "plus_shared_right_cap" in row["storage"]:
            cap_preserved = manifest.get("shared_right_cap_preserved") is True
            outer_sides_preserved = manifest.get("side_regions_preserved") is True
            expected_display_geometry = f"{int(row['display_width'])}x16"
            display_geometry = (
                manifest.get("display_geometry") == expected_display_geometry
            )
            if not cap_preserved or not outer_sides_preserved or not display_geometry:
                failures.append(f"shared-cap proof missing: {logical_text}")
        elif "plus_shared_both_caps" in row["storage"]:
            caps_preserved = (
                manifest.get("shared_left_cap_preserved") is True
                and manifest.get("shared_right_cap_preserved") is True
            )
            expected_display_geometry = f"{int(row['display_width'])}x16"
            display_geometry = (
                manifest.get("display_geometry") == expected_display_geometry
            )
            if not caps_preserved or not display_geometry:
                failures.append(f"both-cap proof missing: {logical_text}")
        elif row["storage"] == "sparse_40x16_insert_shared_mid_column":
            sparse_proof = (
                manifest.get("shared_mid_column_preserved") is True
                and manifest.get("private_outer_columns_preserved") is True
                and manifest.get("display_geometry") == "48x16"
                and manifest.get("shared_mid_top_logical") == "4CB80A"
                and manifest.get("shared_mid_bottom_logical") == "4CB8AA"
            )
            if not sparse_proof:
                failures.append(f"sparse shared-column proof missing: {logical_text}")
        intervals.append((physical, physical + size))
        targets.append(
            {
                "logical": logical_text,
                "logical_range": f"{logical:06X}-{logical + size - 1:06X}",
                "storage": row["storage"],
                "jp": row["jp"],
                "ko": row["ko"],
                "source_stock_exact": source_stock_exact,
                "changed": changed,
                "report_hashes_exact": report_hashes_exact,
                "source_sha256": sha256(source),
                "target_sha256": sha256(target),
            }
        )

    allowed = merge(intervals + [(len(candidate) - 2, len(candidate))])
    runs = diff_runs(parent, candidate)
    unexpected = [
        (start, end)
        for start, end in runs
        if not any(lo <= start and end <= hi for lo, hi in allowed)
    ]
    checksum_valid = (
        (sum(candidate[:-2]) & 0xFFFF)
        == int.from_bytes(candidate[-2:], "little")
    )
    if unexpected:
        failures.append("diff outside eight target blocks/checksum")
    if not checksum_valid:
        failures.append("checksum invalid")
    if candidate_save != live_save:
        failures.append("paired SaveRAM differs from live SaveRAM")
    if sha256(candidate) != str(build.get("candidate", {}).get("sha256")):
        failures.append("candidate hash differs from build report")
    pursuit_source = parent[BASE + 0x4CC32A : BASE + 0x4CC32A + 320]
    pursuit_target = candidate[BASE + 0x4CC32A : BASE + 0x4CC32A + 320]
    pursuit_shared_top_parent = parent[BASE + 0x4CB80A : BASE + 0x4CB80A + 32]
    pursuit_shared_bottom_parent = parent[BASE + 0x4CB8AA : BASE + 0x4CB8AA + 32]
    pursuit_shared_top_candidate = candidate[BASE + 0x4CB80A : BASE + 0x4CB80A + 32]
    pursuit_shared_bottom_candidate = candidate[BASE + 0x4CB8AA : BASE + 0x4CB8AA + 32]
    pursuit_sparse_structure_preserved = (
        pursuit_source[128:160] == common_right_top
        and pursuit_source[288:320] == common_right_bottom
        and pursuit_target[0:32] == pursuit_source[0:32]
        and pursuit_target[160:192] == pursuit_source[160:192]
        and pursuit_target[128:160] == pursuit_source[128:160]
        and pursuit_target[288:320] == pursuit_source[288:320]
        and pursuit_shared_top_candidate == pursuit_shared_top_parent
        and pursuit_shared_bottom_candidate == pursuit_shared_bottom_parent
    )
    if not pursuit_sparse_structure_preserved:
        failures.append("追撃 sparse private/shared tile proof failed")
    if not any(
        row["logical"] == "4C4A74"
        and row["jp"] == "封印!"
        and row["ko"] == "봉인!"
        for row in targets
    ):
        failures.append("封印! corrected label/translation missing")
    if not any(
        row["logical"] == "4C4BB4"
        and row["logical_range"] == "4C4BB4-4C4CB3"
        and row["jp"] == "盾!"
        for row in targets
    ):
        failures.append("盾! corrected 32x16 range/label missing")
    if not (
        any(
            row["logical"] == "4CE9EA"
            and row["logical_range"] == "4CE9EA-4CEAE9"
            and row["jp"] == "先制"
            and row["ko"] == "선제"
            for row in targets
        )
    ):
        failures.append("先制 corrected eight-tile range/label missing")
    preview_path = ROOT / str(build.get("previews", {}).get("comparison_sheet", ""))
    if not preview_path.is_file():
        failures.append("comparison preview missing")

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_id_command_residual_plaques_ko_candidate.py",
        "ok": not failures,
        "status": (
            "static_audit_passed_pending_user_runtime_test"
            if not failures
            else "failed"
        ),
        "parent_sha256": sha256(parent),
        "candidate_sha256": sha256(candidate),
        "candidate_ws_checksum": f"{int.from_bytes(candidate[-2:], 'little'):04X}",
        "paired_saveram_sha256": sha256(candidate_save),
        "targets": targets,
        "counts": {
            "targets": len(targets),
            "body_40x16_plus_shared_right_cap": sum(
                row["storage"] == "body_40x16_plus_shared_right_cap"
                for row in targets
            ),
            "body_32x16_plus_shared_right_cap": sum(
                row["storage"] == "body_32x16_plus_shared_right_cap"
                for row in targets
            ),
            "body_32x16_plus_shared_both_caps": sum(
                row["storage"] == "body_32x16_plus_shared_both_caps"
                for row in targets
            ),
            "full_40x16": sum(
                row["storage"] == "full_40x16" for row in targets
            ),
            "sparse_40x16_insert_shared_mid_column": sum(
                row["storage"] == "sparse_40x16_insert_shared_mid_column"
                for row in targets
            ),
            "full_48x16": sum(
                row["storage"] == "full_48x16" for row in targets
            ),
            "diff_runs_including_checksum": len(runs),
            "changed_bytes_including_checksum": sum(end - start for start, end in runs),
        },
        "checks": {
            "all_sources_stock_exact": all(
                row["source_stock_exact"] for row in targets
            ),
            "all_targets_changed": all(row["changed"] for row in targets),
            "all_report_hashes_exact": all(
                row["report_hashes_exact"] for row in targets
            ),
            "pursuit_sparse_private_and_shared_structure_preserved": pursuit_sparse_structure_preserved,
            "diff_allowlist_clean": not unexpected,
            "checksum_valid": checksum_valid,
            "paired_saveram_exact": candidate_save == live_save,
            "main_unchanged": sha256(MAIN.read_bytes()) == EXPECTED_PARENT,
            "live_saveram_unchanged": sha256(LIVE_SAVE.read_bytes()) == EXPECTED_SAVE,
            "runtime_bank_7a_exact": (
                candidate[0xFA0000:0xFB0000] == parent[0xFA0000:0xFB0000]
            ),
            "runtime_bank_7f_exact_except_checksum": (
                candidate[0xFF0000:0xFFFFFE] == parent[0xFF0000:0xFFFFFE]
            ),
        },
        "unexpected_diff_runs": [
            {"start": f"{start:08X}", "end_exclusive": f"{end:08X}"}
            for start, end in unexpected
        ],
        "failures": failures,
        "promotion": "blocked_pending_user_visual_verification",
    }
    if not all(report["checks"].values()):
        report["ok"] = False
        if "one or more summary checks failed" not in report["failures"]:
            report["failures"].append("one or more summary checks failed")
        report["status"] = "failed"
    atomic_json(OUT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
