#!/usr/bin/env python3
"""
Build unified Korean ROM (out/patch/monoeye_ko_all.wsc) from the full sheet.

Pipeline (no legacy seq_dict):
  1) accepts the provenance-reviewed canonical sheet; optional reviewed-split merge
  2) quality-cleaned reviewed JSON (blank low-quality KO; drop empty / bank 6A–6F)
  3) cold rebuild 8MiB→16MiB onto the *work* ROM only (tip untouched)
  4) free-space sole-ptr relocation (bank 30–4F)
  5) chained stage dedicated dict (Ep1-3 → Ep4 → Ep5-8)
  6) opening interstitial + seed
  7) smoke verify; optional --promote-tip
"""
from __future__ import annotations

import argparse
import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from script_translation_scope import SCRIPT_GRAPHICS_BLOCKS, formatted_ranges, script_graphics_reason
from translation_source_policy import assert_translation_source_allowed
TOOLS = ROOT / "tools"
PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"

SRC_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
SRC_8MB_BACKUP = PATCH / "monoeye_ko_expanded_8mb.wsc"
TIP = PATCH / "monoeye_ko_expanded.wsc"
OUT_ROM = PATCH / "monoeye_ko_all.wsc"
SHEET_CSV = SCRIPT / "translation_sheet_llm_reviewed.csv"
FULL_JSON = SCRIPT / "translations_llm_reviewed_full.json"
QUALITY_JSON = SCRIPT / "translations_llm_reviewed_quality.json"
REPORT_JSON = PATCH / "monoeye_ko_all_build_report.json"

TBL_PAD3 = PATCH / "hangul_patch_pad3.tbl"
EXP_META = PATCH / "exp_dictionary_meta.json"
SEED = ROOT / "data" / "translations_seed_hook96.json"

# Dialogue-only stage windows. Banks 64-69 are fixed-stride data tables, not
# dialogue; they are excluded before any stage-specific applier sees the sheet.
STAGE_WINDOWS = [
    ("Ep1-3", "6040A5", "62FFFF"),
    ("Ep4", "630000", "63FFFF"),
]

MAX_DIALOGUE_ABS = 0x6A0000  # exclusive
DATA_BANK_LO = 0x640000
DATA_BANK_HI = 0x69FFFF
EXCLUSION_ARTIFACT = PATCH / "p0_bank64_69_translation_exclusions.json"

def run_cmd(cmd: list[str], *, allow_fail: bool = False) -> int:
    print(f"-> {' '.join(cmd)}")
    env = dict(**__import__("os").environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    res = subprocess.run(cmd, cwd=ROOT, env=env)
    if res.returncode != 0 and not allow_fail:
        raise RuntimeError(f"Command failed rc={res.returncode}: {' '.join(cmd)}")
    return res.returncode


def filter_apply_sheet(quality_path: Path, out_path: Path) -> dict:
    """Keep non-empty KO dialogue rows and emit an explicit bank64-69 exclusion."""
    data = json.loads(quality_path.read_text(encoding="utf-8"))
    lines = data["lines"] if isinstance(data, dict) else data
    kept: list[dict] = []
    excluded: list[dict] = []
    in_data_band = 0
    empty_in_data_band = 0
    skipped = {
        "empty_ko": 0,
        "unit_bank": 0,
        "script_graphics_block": 0,
        "data_bank_64_69": 0,
        "bad_abs": 0,
    }
    for row in lines:
        abs_s = (row.get("abs") or "").strip()
        ko = (row.get("ko") or "").strip()
        if not abs_s:
            skipped["bad_abs"] += 1
            continue
        try:
            abs_off = int(abs_s, 16)
        except ValueError:
            skipped["bad_abs"] += 1
            continue
        graphics_reason = script_graphics_reason(abs_off)
        if graphics_reason:
            skipped["script_graphics_block"] += 1
            excluded.append(
                {
                    "abs": f"{abs_off:06X}",
                    "id": row.get("id") or "",
                    "kind": row.get("kind") or "",
                    "jp": row.get("jp") or "",
                    "ko": ko,
                    "reason": graphics_reason,
                }
            )
            continue
        if abs_off >= MAX_DIALOGUE_ABS:
            skipped["unit_bank"] += 1
            continue
        if DATA_BANK_LO <= abs_off <= DATA_BANK_HI:
            in_data_band += 1
            if not ko:
                empty_in_data_band += 1
                skipped["empty_ko"] += 1
                continue
            skipped["data_bank_64_69"] += 1
            excluded.append(
                {
                    "abs": f"{abs_off:06X}",
                    "id": row.get("id") or "",
                    "kind": row.get("kind") or "",
                    "jp": row.get("jp") or "",
                    "ko": ko,
                    "reason": "excluded_fixed_stride_data_block",
                }
            )
            continue
        if not ko:
            skipped["empty_ko"] += 1
            continue
        kept.append(
            {
                "abs": f"{abs_off:06X}",
                "jp": row.get("jp") or "",
                "ko": ko,
                "id": row.get("id") or "",
                "kind": row.get("kind") or "",
            }
        )
    source_hash = hashlib.sha256(quality_path.read_bytes()).hexdigest()
    exclusion_payload = {
        "schema": 1,
        "generated_by": "tools/build_monoeye_ko_all.py::filter_apply_sheet",
        "status": "explicit_non_dialogue_translation_exclusion",
        "source": str(quality_path).replace("\\", "/"),
        "source_sha256": source_hash,
        "ranges": formatted_ranges(SCRIPT_GRAPHICS_BLOCKS) + ["640000-69FFFF"],
        "source_rows_in_range": in_data_band,
        "empty_rows_in_range": empty_in_data_band,
        "excluded_rows": len(excluded),
        "expected_nonempty_rows": 7210,
        "per_bank": {
            f"{bank:02X}": sum(1 for row in excluded if int(row["abs"], 16) >> 16 == bank)
            for bank in range(0x64, 0x6A)
        },
        "records": excluded,
        "note": (
            "Rows in the bank-62 event/graphics structure block and fixed-stride "
            "banks 64-69 are retained here as audit evidence but never reach an "
            "applier. This preserves the latest valid dialogue translations while "
            "blocking machine translations generated from non-text structures."
        ),
    }
    EXCLUSION_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    EXCLUSION_ARTIFACT.write_text(
        json.dumps(exclusion_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload = {
        "description": "Quality-cleaned full-sheet KO for free-space apply (dialogue through 63FFFF only)",
        "source": str(quality_path).replace("\\", "/"),
        "line_count": len(kept),
        "skipped": skipped,
        "exclusion_artifact": str(EXCLUSION_ARTIFACT).replace("\\", "/"),
        "lines": kept,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(
        f"Apply sheet {out_path.name}: lines={len(kept)} "
        f"skipped={skipped} exclusion={EXCLUSION_ARTIFACT.name}"
    )
    return payload

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-rom", type=Path, default=OUT_ROM)
    ap.add_argument("--sheet-csv", type=Path, default=SHEET_CSV)
    ap.add_argument(
        "--merge-reviewed-splits",
        action="store_true",
        help="Merge out/script/llm_reviewed_splits into the reviewed canonical sheet before building",
    )
    ap.add_argument(
        "--promote-tip",
        action="store_true",
        help="After smoke pass, copy work ROM over monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--skip-cold-rebuild",
        action="store_true",
        help="Reuse existing work ROM base (debug only)",
    )
    args = ap.parse_args()

    print("=" * 60)
    print("  UNIFIED MONOEYE KO ALL BUILD (Chained Stage Pipeline)")
    print("=" * 60)

    # ── 0. Optional reviewed splits → canonical reviewed sheet ──
    if args.merge_reviewed_splits:
        print("\n=== PHASE 0: Merge reviewed splits → reviewed canonical sheet ===")
        run_cmd(
            [
                sys.executable,
                str(TOOLS / "merge_csv.py"),
                "--splits-dir",
                str(SCRIPT / "llm_reviewed_splits"),
                "--out",
                str(args.sheet_csv),
            ]
        )
    source_policy = assert_translation_source_allowed(
        args.sheet_csv,
        role=(
            "merged reviewed canonical translation sheet"
            if args.merge_reviewed_splits
            else "unified cold-rebuild translation sheet"
        ),
    )
    print(f"Translation source policy: {source_policy}")

    # ── 1. CSV → full JSON → quality-cleaned apply JSON ──
    print("\n=== PHASE 1: Sheet → quality apply JSON ===")
    run_cmd(
        [
            sys.executable,
            str(TOOLS / "sheet_to_translations.py"),
            "--sheet",
            str(args.sheet_csv),
            "--out",
            str(FULL_JSON),
        ]
    )
    run_cmd(
        [
            sys.executable,
            str(TOOLS / "audit_ko_quality.py"),
            "--sheet",
            str(FULL_JSON),
            "--write-cleaned",
            str(QUALITY_JSON),
            "--blank-low",
            "--report",
            str(SCRIPT / "ko_quality_all_report.json"),
        ]
    )
    apply_json = SCRIPT / "translations_llm_reviewed_apply.json"
    apply_meta = filter_apply_sheet(QUALITY_JSON, apply_json)

    # ── 2. Per-stage filtered sheets ──
    print("\n=== PHASE 1a: Per-stage filtered JSONs ===")
    stage_jsons: list[Path] = []
    for stage_name, lo_hex, hi_hex in STAGE_WINDOWS:
        stage_json = SCRIPT / f"translations_llm_reviewed_{stage_name}.json"
        stage_jsons.append(stage_json)
        run_cmd(
            [
                sys.executable,
                str(TOOLS / "filter_sheet_abs.py"),
                "--sheet",
                str(apply_json),
                "--out",
                str(stage_json),
                "--min-abs",
                lo_hex,
                "--max-abs",
                hi_hex,
                "--description",
                f"{stage_name} window ({lo_hex}-{hi_hex})",
            ]
        )

    # ── 3. Cold rebuild onto work ROM only (never tip) ──
    work = args.out_rom
    if not args.skip_cold_rebuild:
        if not SRC_8MB_BACKUP.exists():
            raise SystemExit(f"missing 8MiB backup: {SRC_8MB_BACKUP}")
        print("\n=== PHASE 1b: Cold rebuild → work ROM (tip untouched) ===")
        run_cmd(
            [
                sys.executable,
                str(TOOLS / "patch_pad3_expansion.py"),
                "-i",
                str(SRC_8MB_BACKUP),
                "-o",
                str(work),
            ]
        )
        run_cmd(
            [
                sys.executable,
                str(TOOLS / "patch_exp_dictionary.py"),
                "--rom",
                str(work),
                "--out",
                str(work),
            ]
        )
        print("\n=== PHASE 1c: Free-space reset on work ROM ===")
        run_cmd(
            [
                sys.executable,
                str(TOOLS / "snapshot_free_space_base.py"),
                "--rom",
                str(work),
                "--out",
                str(work),
            ]
        )
    else:
        if not work.exists():
            raise SystemExit(f"--skip-cold-rebuild but missing {work}")
        print(f"\n[skip] cold rebuild; using existing {work}")

    # ── 4. Sole-pointer free-space relocation ──
    print("\n=== PHASE 2: Free-space sole-ptr relocation ===")
    reloc_report = PATCH / "monoeye_ko_all_reloc_report.json"
    run_cmd(
        [
            sys.executable,
            str(TOOLS / "apply_free_space_script_ko.py"),
            "--rom",
            str(work),
            "--out-rom",
            str(work),
            "--sheet",
            str(apply_json),
            "--jp",
            str(SRC_JP),
            "--max-ptr-hits",
            "1",
            "--out-report",
            str(reloc_report),
        ]
    )

    # ── 5. Chained dedicated per stage ──
    total_fail = 0
    total_lines = 0
    total_skipped_no_slot = 0
    stage_reports: list[dict] = []

    for i, (stage_name, lo_hex, hi_hex) in enumerate(STAGE_WINDOWS):
        stage_json = stage_jsons[i]
        print(
            f"\n=== PHASE 3-{stage_name}: Dedicated ({lo_hex}-{hi_hex}) ==="
        )
        stage_report = PATCH / f"monoeye_ko_all_{stage_name}_dedicated_report.json"
        cmd = [
            sys.executable,
            str(TOOLS / "apply_opening_dedicated.py"),
            "--rom",
            str(work),
            "--out-rom",
            str(work),
            "--tbl",
            str(TBL_PAD3),
            "--sheet",
            str(stage_json),
            "--seed",
            str(SEED),
            "--meta",
            str(EXP_META),
            "--lo",
            lo_hex,
            "--hi",
            hi_hex,
            "--max-rounds",
            "16",
            "--out-report",
            str(stage_report),
        ]
        if stage_name == "Ep1-3":
            cmd.append("--include-seed-abs")
        rc = run_cmd(cmd, allow_fail=True)

        entry: dict = {
            "stage": stage_name,
            "lo": lo_hex,
            "hi": hi_hex,
            "rc": rc,
        }
        if stage_report.exists():
            sr = json.loads(stage_report.read_text(encoding="utf-8"))
            fail_n = len(sr.get("decode_failures") or [])
            lines_n = int(sr.get("lines_patched") or 0)
            skip_n = int(sr.get("skipped_no_slot") or 0)
            total_fail += fail_n
            total_lines += lines_n
            total_skipped_no_slot += skip_n
            entry.update(
                {
                    "lines_patched": lines_n,
                    "fail": fail_n,
                    "skipped_no_slot": skip_n,
                    "plain_inplace": sr.get("plain_inplace"),
                    "reuse_existing": sr.get("reuse_existing"),
                    "window_unanimous": sr.get("window_unanimous"),
                    "pool_peak": sr.get("pool_total"),
                }
            )
            print(
                f"  [{stage_name}] lines={lines_n} fail={fail_n} "
                f"no_slot={skip_n} rc={rc}"
            )
        else:
            entry["error"] = True
            print(f"  [{stage_name}] WARNING: no report (rc={rc})")
        stage_reports.append(entry)

    # ── 6. Opening interstitials ──
    print("\n=== PHASE 4: Opening interstitial narration ===")
    run_cmd(
        [
            sys.executable,
            str(TOOLS / "patch_opening_narration.py"),
            "--rom",
            str(work),
            "--out-rom",
            str(work),
            "--tbl",
            str(TBL_PAD3),
        ]
    )

    # ── 7. Smoke ──
    print("\n=== PHASE 5: Static smoke ===")
    smoke_rc = run_cmd(
        [
            sys.executable,
            str(TOOLS / "verify_all_stages_smoke.py"),
            "--rom",
            str(work),
            "--report",
            str(REPORT_JSON),
            "--sheet",
            str(apply_json),
        ],
        allow_fail=True,
    )

    smoke_ok = False
    if REPORT_JSON.exists():
        smoke = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
        smoke_ok = bool(smoke.get("overall_ok"))

    promoted = False
    if args.promote_tip and smoke_ok and total_fail == 0:
        print("\n=== PHASE 6: Promote work ROM → tip ===")
        shutil.copy2(work, TIP)
        promoted = True
        print(f"  tip ← {work}")
    elif args.promote_tip:
        print(
            "\n[skip] --promote-tip requested but smoke/decode not clean "
            f"(smoke_ok={smoke_ok} decode_fail={total_fail})"
        )

    summary = {
        "out_rom": str(work),
        "apply_lines": apply_meta.get("line_count"),
        "apply_skipped": apply_meta.get("skipped"),
        "total_lines_patched": total_lines,
        "total_decode_failures": total_fail,
        "total_skipped_no_slot": total_skipped_no_slot,
        "smoke_rc": smoke_rc,
        "smoke_ok": smoke_ok,
        "promoted_tip": promoted,
        "stages": stage_reports,
    }
    summary_path = PATCH / "monoeye_ko_all_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("\n" + "=" * 60)
    status = "SUCCESS" if (smoke_ok and total_fail == 0) else "WARNING"
    print(f"  [{status}] work ROM: {work}")
    print(f"  Apply sheet lines: {apply_meta.get('line_count')}")
    print(f"  Total lines patched: {total_lines}")
    print(f"  skipped_no_slot: {total_skipped_no_slot}")
    print(f"  decode_fail: {total_fail} smoke_ok={smoke_ok} promoted={promoted}")
    for sr in stage_reports:
        print(
            f"    {sr['stage']}: lines={sr.get('lines_patched', '?')} "
            f"fail={sr.get('fail', '?')} no_slot={sr.get('skipped_no_slot', '?')}"
        )
    print(f"  Summary: {summary_path}")
    print("=" * 60)

    return 0 if (smoke_ok and total_fail == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
