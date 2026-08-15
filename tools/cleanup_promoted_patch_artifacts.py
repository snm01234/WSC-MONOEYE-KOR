#!/usr/bin/env python3
"""Remove obsolete promoted-test artifacts from out/patch.

Dry-run is the default. Pass --commit to delete the exact classified set.
The script refuses to run against an unexpected main TIP.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
REPORT = PATCH / "postpromotion_cleanup_report.json"
EXPECTED_TIP_SHA256 = "6cf6184e7e989c02b25b3e61fc28fc9e9eca354c20726b5efc7eec10918dce05"
EXPECTED_TIP_SIZE = 16_777_216
EXPECTED_SAVE_SIZE = 32_768

PRESERVE_ROM_SAVE = {
    "monoeye_ko_expanded.wsc",
    "monoeye_ko_expanded.sav",
    "monoeye_ko_expanded_8mb.wsc",
    "monoeye_ko_expanded_8mb.sav",
    "monoeye_ko_expanded.pre_ext3.wsc",
}
FINAL_AUDIO_PREFIX = "id_command_audio_sample54_table_repair_"


class CleanupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def classify() -> list[tuple[Path, str]]:
    targets: list[tuple[Path, str]] = []
    for path in sorted(PATCH.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in {".wsc", ".sav"} and path.name not in PRESERVE_ROM_SAVE:
            targets.append((path, "obsolete_candidate_or_probe_rom_saveram"))
            continue
        if (
            path.name.startswith("id_command_audio_")
            and not path.name.startswith(FINAL_AUDIO_PREFIX)
            and suffix in {".json", ".log"}
        ):
            targets.append((path, "superseded_id_audio_diagnostic"))
            continue
        if suffix == ".log":
            targets.append((path, "completed_smoke_log"))
    return targets


def validate_baseline() -> dict[str, object]:
    tip = PATCH / "monoeye_ko_expanded.wsc"
    save = ROOT / "sram/monoeye_ko_expanded.sav"
    if not tip.is_file() or tip.stat().st_size != EXPECTED_TIP_SIZE:
        raise CleanupError("main TIP missing or wrong size")
    if sha256(tip) != EXPECTED_TIP_SHA256:
        raise CleanupError("main TIP SHA-256 drifted")
    if not save.is_file() or save.stat().st_size != EXPECTED_SAVE_SIZE:
        raise CleanupError("main SaveRAM missing or wrong size")
    missing = sorted(name for name in PRESERVE_ROM_SAVE if not (PATCH / name).is_file())
    if missing:
        raise CleanupError(f"required preserved baseline missing: {missing}")
    return {
        "tip": {
            "path": "out/patch/monoeye_ko_expanded.wsc",
            "size": tip.stat().st_size,
            "sha256": sha256(tip),
        },
        "saveram": {
            "path": "sram/monoeye_ko_expanded.sav",
            "size": save.stat().st_size,
            "sha256": sha256(save),
        },
        "preserved_rom_save": sorted(PRESERVE_ROM_SAVE),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    baseline = validate_baseline()
    targets = classify()
    rows = [
        {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "size": path.stat().st_size,
            "reason": reason,
        }
        for path, reason in targets
    ]
    total = sum(row["size"] for row in rows)

    if not args.commit:
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "ok": True,
                    "baseline": baseline,
                    "delete_count": len(rows),
                    "recover_bytes": total,
                    "recover_mib": round(total / 1048576, 2),
                    "targets": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    deleted: list[dict[str, object]] = []
    for path, reason in targets:
        size = path.stat().st_size
        path.unlink()
        deleted.append(
            {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "size": size,
                "reason": reason,
            }
        )

    remaining_rom_save = sorted(
        path.name
        for path in PATCH.iterdir()
        if path.is_file() and path.suffix.lower() in {".wsc", ".sav"}
    )
    if remaining_rom_save != sorted(PRESERVE_ROM_SAVE):
        raise CleanupError(f"unexpected remaining ROM/SaveRAM set: {remaining_rom_save}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/cleanup_promoted_patch_artifacts.py",
        "ok": True,
        "committed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "baseline": validate_baseline(),
        "deleted_count": len(deleted),
        "recovered_bytes": sum(int(row["size"]) for row in deleted),
        "recovered_mib": round(sum(int(row["size"]) for row in deleted) / 1048576, 2),
        "deleted": deleted,
        "remaining_rom_save": remaining_rom_save,
        "preserved_evidence": [
            "out/patch/id_command_audio_sample54_table_repair_report.json",
            "out/patch/id_command_audio_sample54_table_repair_structure.json",
            "out/patch/id_command_audio_sample54_table_repair_false_segptr.json",
            "out/patch/id_command_audio_sample54_table_repair_user_validation.json",
            "out/patch/id_command_audio_sample54_table_repair_postpromotion_audit.json",
            "out/patch/id_command_audio_sample54_table_repair_promotion_report.json",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
