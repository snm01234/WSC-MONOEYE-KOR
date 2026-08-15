#!/usr/bin/env python3
"""Audit the transparent-background cleanup test ROM against the promoted TIP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAV = ROOT / "sram/monoeye_ko_expanded.sav"
OUT = PATCH / "intermission_transition_transparent_clean_candidate"
CANDIDATE = OUT / "intermission_confirm_atlas_clean_candidate.wsc"
SAV = ROOT / "sram/intermission_confirm_atlas_clean_candidate.sav"
FULL_REPORT = PATCH / "intermission_transition_transparent_clean_candidate_stage1/full_cleanup_report.json"
CONFIRM_REPORT = OUT / "confirm_focus_atlas_candidate_report.json"
OLD_ANALYSIS = PATCH / "intermission_transition_patched_state_residual_analysis.json"
NEW_ANALYSIS = PATCH / "intermission_transition_transparent_clean_candidate_stage1/live_residual_analysis.json"
OLD_PREVIEW = PATCH / "intermission_confirm_atlas_clean_candidate_16/previews/after_full.png"
NEW_PREVIEW = PATCH / "intermission_transition_transparent_clean_candidate_stage1/previews/after_full.png"
DIFF_PREVIEW = OUT / "transparent_cleanup_pixel_diff.png"
REPORT = OUT / "transparent_cleanup_audit.json"

TIP_SHA = "3b0a07f82d97a90055957dc310b6a9dc713c4d4c6aa4c75586b286e255412da9"
CANDIDATE_SHA = "0ef10eaeec6da9386e1e4e3491bf3a29687428c85f048f6cab63acbbe1b22495"
BASE = 0x800000
FULL_START = 0x54B780
FULL_END = 0x54E7D4


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ident(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def main() -> int:
    required = (
        TIP,
        TIP_SAV,
        CANDIDATE,
        SAV,
        FULL_REPORT,
        CONFIRM_REPORT,
        OLD_ANALYSIS,
        NEW_ANALYSIS,
        OLD_PREVIEW,
        NEW_PREVIEW,
    )
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"missing: {path}")
    if sha(TIP) != TIP_SHA or sha(CANDIDATE) != CANDIDATE_SHA:
        raise RuntimeError("ROM hash binding drifted")

    tip = TIP.read_bytes()
    candidate = CANDIDATE.read_bytes()
    changed = [i for i, pair in enumerate(zip(tip, candidate)) if pair[0] != pair[1]]
    outside = [
        i
        for i in changed
        if not (BASE + FULL_START <= i < BASE + FULL_END or i >= len(candidate) - 2)
    ]
    full = json.loads(FULL_REPORT.read_text(encoding="utf-8"))
    confirm = json.loads(CONFIRM_REPORT.read_text(encoding="utf-8"))
    old_analysis = json.loads(OLD_ANALYSIS.read_text(encoding="utf-8"))
    new_analysis = json.loads(NEW_ANALYSIS.read_text(encoding="utf-8"))

    old_image = Image.open(OLD_PREVIEW).convert("RGB")
    new_image = Image.open(NEW_PREVIEW).convert("RGB")
    if old_image.size != new_image.size or old_image.size != (896, 576):
        raise RuntimeError("preview scale/size drifted")
    old_px = old_image.load()
    new_px = new_image.load()
    changed_scaled = [
        (x, y)
        for y in range(old_image.height)
        for x in range(old_image.width)
        if old_px[x, y] != new_px[x, y]
    ]
    if len(changed_scaled) % 16:
        raise RuntimeError("preview difference is not aligned to the 4x scale")
    changed_framebuffer = len(changed_scaled) // 16
    original_points = sorted({(x // 4, y // 4) for x, y in changed_scaled}, key=lambda p: (p[1], p[0]))
    if len(original_points) != changed_framebuffer:
        raise RuntimeError("preview difference contains partial 4x blocks")

    diff = new_image.copy()
    dp = diff.load()
    changed_set = set(changed_scaled)
    for y in range(diff.height):
        for x in range(diff.width):
            if (x, y) in changed_set:
                dp[x, y] = (255, 0, 0)
            else:
                r, g, b = dp[x, y]
                grey = (r + g + b) // 6
                dp[x, y] = (grey, grey, grey)
    diff.save(DIFF_PREVIEW)

    checksum = int.from_bytes(candidate[-2:], "little")
    checks = {
        "tip_is_promoted_16_plus_12_build": sha(TIP) == TIP_SHA,
        "candidate_hash_bound": sha(CANDIDATE) == CANDIDATE_SHA,
        "candidate_checksum_valid": (sum(candidate[:-2]) & 0xFFFF) == checksum,
        "candidate_diff_only_full_transition_asset_and_checksum": not outside,
        "candidate_diff_exactly_28_rom_bytes": len(changed) == 28,
        "confirmation_atlas_byte_identical_to_main": tip[BASE + 0x547CFC : BASE + 0x549A1C]
        == candidate[BASE + 0x547CFC : BASE + 0x549A1C],
        "normal_focus_atlas_byte_identical_to_main": tip[BASE + 0x542000 : BASE + 0x544400]
        == candidate[BASE + 0x542000 : BASE + 0x544400],
        "runtime_hook_byte_identical_to_main": tip[BASE + 0x7A0600 : BASE + 0x7A1000]
        == candidate[BASE + 0x7A0600 : BASE + 0x7A1000],
        "matching_sav_is_current_main_copy": SAV.read_bytes() == TIP_SAV.read_bytes(),
        "all_sixteen_labels_processed": full["verification"]["all_16_labels_processed"] is True,
        "transparent_cleanup_enabled": full["verification"]["transparent_cleanup_enabled"] is True,
        "old_state_had_34_unexpected_save_ink_pixels": old_analysis["unexpected_stock_ink_pixels_total"] == 34
        and old_analysis["labels_with_unexpected_stock_ink"] == ["save"],
        "new_state_has_zero_unexpected_ink_pixels": new_analysis["unexpected_stock_ink_pixels_total"] == 0
        and new_analysis["all_expected_korean_masks_present"] is True,
        "preview_clears_exactly_40_outline_pixels": changed_framebuffer == 40
        and all(old_px[x * 4, y * 4] == (17, 17, 17) and new_px[x * 4, y * 4] == (0, 0, 0) for x, y in original_points),
        "preview_changes_confined_to_save_text_interior": all(88 <= x <= 134 and y in {127, 128, 133} for x, y in original_points),
        "panel_decoration_unchanged": full["verification"]["panel_decoration_unchanged"] is True,
        "confirmation_stage_preserved_full_asset": confirm["verification"]["full_16_label_asset_preserved"] is True,
    }
    if not all(checks.values()):
        raise RuntimeError(f"audit failed: {checks}")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/audit_intermission_transition_transparent_clean_candidate.py",
        "ok": True,
        "published": False,
        "base_tip": ident(TIP),
        "candidate": ident(CANDIDATE),
        "matching_sav": ident(SAV),
        "wonder_swan_checksum": f"{checksum:04X}",
        "changed_rom_bytes_including_checksum": len(changed),
        "outside_allowlist_bytes": len(outside),
        "old_unexpected_save_ink_pixels_in_safe_core": 34,
        "new_unexpected_ink_pixels_in_all_sixteen_safe_cores": 0,
        "cleared_outline_pixels_in_full_preview": changed_framebuffer,
        "cleared_preview_points": [list(point) for point in original_points],
        "diff_preview": ident(DIFF_PREVIEW),
        "checks": checks,
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
