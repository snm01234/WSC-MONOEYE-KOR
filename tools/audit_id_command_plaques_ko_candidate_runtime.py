#!/usr/bin/env python3
"""Audit the candidate's first post-savestate ROM reload of an ID plaque."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_id_command_plaques_ko_candidate as build  # noqa: E402


CANDIDATE = ROOT / "out/patch/id_command_plaques_ko_candidate.wsc"
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CAPTURE_DIR = ROOT / "out/patch/id_command_plaques_ko_candidate_runtime"
OLD_VRAM_SHOT = CAPTURE_DIR / "id_command_ko_attack_r1_s01.png"
OLD_VRAM_REPEAT = CAPTURE_DIR / "id_command_ko_attack_r2_s01.png"
RELOAD_SHOT = CAPTURE_DIR / "id_command_ko_long_s02.png"
LONG_LOG = CAPTURE_DIR / "id_command_ko_long.log"
OUT_REPORT = ROOT / "out/patch/id_command_plaques_ko_candidate_runtime_audit.json"
OUT_CROP = CAPTURE_DIR / "frame25450_up_accuracy_crop_8x.png"
EXPECTED_CANDIDATE_SHA256 = "9ba9804dac603d84efe75bff6efecfebd2b55ef7bd602671c375f97791f61d75"
BBOX = (84, 44, 132, 60)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def pixel_mismatches(left: Image.Image, right: Image.Image) -> list[tuple[int, int]]:
    if left.size != right.size:
        raise RuntimeError(f"image geometry mismatch: {left.size} != {right.size}")
    return [
        (x, y)
        for y in range(left.height)
        for x in range(left.width)
        if left.getpixel((x, y)) != right.getpixel((x, y))
    ]


def body_pixels(rom: bytes, logical: int) -> list[list[int]]:
    base = build.stock_base(rom)
    return build.compose_body(
        rom,
        base,
        rom[base + logical : base + logical + build.BODY_BYTES],
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    candidate = CANDIDATE.read_bytes()
    stock = STOCK.read_bytes()
    if sha256(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise RuntimeError("candidate SHA-256 drift")
    if (sum(candidate[:-2]) & 0xFFFF) != int.from_bytes(candidate[-2:], "little"):
        raise RuntimeError("candidate WonderSwan checksum invalid")

    old_shot = Image.open(OLD_VRAM_SHOT).convert("RGB")
    old_repeat = Image.open(OLD_VRAM_REPEAT).convert("RGB")
    reload_shot = Image.open(RELOAD_SHOT).convert("RGB")
    old_crop = old_shot.crop(BBOX)
    reload_crop = reload_shot.crop(BBOX)
    stock_attack = build.render_plaque(body_pixels(stock, 0x4C5D54), 1)
    candidate_attack = build.render_plaque(body_pixels(candidate, 0x4C5D54), 1)
    candidate_accuracy = build.render_plaque(body_pixels(candidate, 0x4C54F4), 1)

    old_vs_stock = pixel_mismatches(old_crop, stock_attack)
    old_vs_candidate = pixel_mismatches(old_crop, candidate_attack)
    reload_vs_candidate = pixel_mismatches(reload_crop, candidate_accuracy)
    reload_crop.resize((48 * 8, 16 * 8), Image.Resampling.NEAREST).save(OUT_CROP)

    long_log = LONG_LOG.read_text(encoding="utf-8", errors="replace")
    checks = {
        "candidate_sha256_bound": sha256(candidate) == EXPECTED_CANDIDATE_SHA256,
        "candidate_checksum_valid": (sum(candidate[:-2]) & 0xFFFF) == int.from_bytes(candidate[-2:], "little"),
        "quickstate_old_vram_repeat_deterministic": old_shot.tobytes() == old_repeat.tobytes(),
        "frame25330_old_vram_exact_stock_attack": len(old_vs_stock) == 0,
        "frame25330_not_candidate_attack": len(old_vs_candidate) > 0,
        "frame25450_log_present": "SHOT 02 frame=25450" in long_log,
        "frame25450_candidate_accuracy_exact": len(reload_vs_candidate) == 0,
        "frame25450_full_48x16_compared": reload_crop.size == (48, 16),
    }
    if not all(checks.values()):
        raise RuntimeError(f"runtime audit failed: {checks}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_id_command_plaques_ko_candidate_runtime.py",
        "ok": True,
        "candidate": {
            "path": rel(CANDIDATE),
            "size": len(candidate),
            "sha256": sha256(candidate),
            "ws_checksum": f"{int.from_bytes(candidate[-2:], 'little'):04X}",
        },
        "savestate_caveat": (
            "QuickSave6 restores already-uploaded VRAM. Its frame 25330 plaque is therefore "
            "the stock Japanese ↑攻撃 asset, not evidence against the candidate ROM."
        ),
        "old_vram_control": {
            "frame": 25330,
            "capture": rel(OLD_VRAM_SHOT),
            "repeat_capture": rel(OLD_VRAM_REPEAT),
            "bbox_exclusive": list(BBOX),
            "stock_attack_mismatch_pixels": len(old_vs_stock),
            "candidate_attack_mismatch_pixels": len(old_vs_candidate),
        },
        "post_state_rom_reload": {
            "frame": 25450,
            "visible_plaque": "↑명중",
            "logical_asset": "4C54F4-4C5633",
            "physical_asset": "CC54F4-CC5633",
            "capture": rel(RELOAD_SHOT),
            "crop_8x": rel(OUT_CROP),
            "bbox_exclusive": list(BBOX),
            "pixels_compared": 48 * 16,
            "mismatch_pixels": len(reload_vs_candidate),
            "pixel_exact": len(reload_vs_candidate) == 0,
        },
        "runtime_scope": {
            "proven_korean_asset": "↑명중 (body_plus_shared_cap)",
            "proven_pipeline": "candidate ROM → packed-4bpp upload → VRAM → 12 OBJ sprites → framebuffer",
            "remaining_assets": 22,
            "remaining_requirement": "reach each distinct command/result state for per-label runtime A/B",
        },
        "checks": checks,
    }
    build.atomic_bytes(
        OUT_REPORT,
        (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
