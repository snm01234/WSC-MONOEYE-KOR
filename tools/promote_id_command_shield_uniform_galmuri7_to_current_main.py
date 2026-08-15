#!/usr/bin/env python3
"""Promote the user-approved uniform Galmuri7 shield plaque into current main TIP.

The stock shield plaque intentionally displays its final stored tile twice.  This
script keeps that runtime layout intact: ``방패`` is confined to stored columns
0..2 and a standalone ``!`` occupies column 3, so the established
``0,1,2,3,3,right-cap`` composition displays ``방패!!``.  No runtime hook,
tilemap, SaveRAM, or BizHawk automation is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_id_command_plaques_ko_candidate as base  # noqa: E402

PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
TIP_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
WORD_FONT = ROOT / "assets/fonts/Galmuri7.ttf"
BANG_FONT = ROOT / "assets/fonts/galmuri_tmp/Galmuri11-Bold.ttf"
CANDIDATE = PATCH / "id_command_shield_uniform_galmuri7_candidate.wsc"
CANDIDATE_SAVE = ROOT / "sram/id_command_shield_uniform_galmuri7_candidate.sav"
PREVIEW = PATCH / "id_command_shield_uniform_galmuri7_candidate_preview.png"
BUILD_REPORT = PATCH / "id_command_shield_uniform_galmuri7_candidate_report.json"
PROMOTION_REPORT = PATCH / "id_command_shield_uniform_galmuri7_promotion_report.json"
POST_AUDIT = PATCH / "id_command_shield_uniform_galmuri7_postpromotion_audit.json"

EXPECTED_TIP_SHA = "d2b7301b0f51071a566dd473be4a528d1d13a4305fc251de5543133ab5b0db20"
EXPECTED_TIP_CHECKSUM = "D66F"
EXPECTED_SAVE_SHA = "9eff99e2408c7411d4b0e9d462f48f6590572fba1cef1bff5cd6bfb66fae20ad"
EXPECTED_WORD_FONT_SHA = "3882bd35066c26b0392cd4963ff9b3c151041dec34adc9d5633d137d1d9b9855"
EXPECTED_BANG_FONT_SHA = "5265b2f437fe81f0c8095b44c0173dd9a276b58a42552bf983f21c0e69e6e8af"
EXPECTED_OLD_SHIELD_SHA = "b59e8a7cf5ccbe04d461c98ae077148e6ec89d81b43e7878044d6d2a03adbc19"
EXPECTED_NEW_SHIELD_SHA = "1edb92935a2518a333ea7a7a7b11ab99fcc70ac82adc49cffd8b7d8928e24e39"
EXPECTED_NEW_TIP_SHA = "9468c74624483a7b4a3b55c896d5277b73afc1b80fd5270e8e4b9d69fbfa197a"
EXPECTED_NEW_CHECKSUM = "CD64"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
SHIELD_PHYSICAL = 0xCC4BB4
SHIELD_SIZE = 256
SUCCESS_PHYSICAL = 0xCC4654
SUCCESS_SIZE = 384
RUNTIME_HOOK = (0xF897A2, 16)
RUNTIME_CAVE = (0xF8FF19, 96)
PRIVATE_BLANK = (0xCCEB8C, 64)

BACKGROUND = 0xC
INK = 0xE
OUTLINE = 0xF
LIVE_PALETTE = {
    0x0: (0, 0, 0),
    0xA: (80, 136, 80),
    0xB: (170, 255, 187),
    0xC: (68, 255, 68),
    0xD: (0, 204, 0),
    0xE: (0, 68, 0),
    0xF: (255, 255, 255),
}


class PromotionError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    raw = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(raw),
        "sha256": sha(raw),
    }


def checksum(data: bytes) -> dict[str, Any]:
    computed = sum(data[:-2]) & 0xFFFF
    stored = int.from_bytes(data[-2:], "little")
    return {"computed": f"{computed:04X}", "stored": f"{stored:04X}", "valid": computed == stored}


def require(path: Path, *, size: int | None = None, expected_sha: str | None = None) -> bytes:
    if not path.is_file():
        raise PromotionError(f"missing file: {path}")
    raw = path.read_bytes()
    if size is not None and len(raw) != size:
        raise PromotionError(f"size drift: {path}: {len(raw)} != {size}")
    if expected_sha is not None and sha(raw) != expected_sha.lower():
        raise PromotionError(f"SHA-256 drift: {path}: {sha(raw)}")
    return raw


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    if len(a) != len(b):
        raise PromotionError("diff size mismatch")
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, (before, after) in enumerate(zip(a, b, strict=True)):
        if before != after and start is None:
            start = index
        elif before == after and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(a)))
    return runs


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def paste_mask(
    pixels: list[list[int]], mask: Image.Image, x: int, y: int, value: int
) -> None:
    source = mask.load()
    for yy in range(mask.height):
        for xx in range(mask.width):
            if source[xx, yy]:
                pixels[y + yy][x + xx] = value


def build_shield(old_block: bytes) -> tuple[bytes, list[list[int]], dict[str, Any]]:
    before = base.decode_grid(old_block, 4, 2)
    after = [row[:] for row in before]

    # Preserve the rounded left edge through x=5.  Its x=5 pixels under the
    # glyph (y=3..11) are background in the approved parent and are safe to draw on.
    for y in range(1, 15):
        for x in range(6, 32):
            after[y][x] = BACKGROUND
    for y in (0, 15):
        for x in range(6, 32):
            after[y][x] = OUTLINE

    word_font = ImageFont.truetype(str(WORD_FONT), 8)
    word_outer, word_inner = base.make_masks("방패", word_font, 1)
    if word_outer.size != (18, 9):
        raise PromotionError(f"unexpected Galmuri7 word mask: {word_outer.size}")
    paste_mask(after, word_outer, 5, 3, OUTLINE)
    paste_mask(after, word_inner, 5, 3, INK)

    bang_font = ImageFont.truetype(str(BANG_FONT), 10)
    bang_outer, bang_inner = base.make_masks("!", bang_font, 1)
    if bang_outer.size != (6, 12):
        raise PromotionError(f"unexpected exclamation mask: {bang_outer.size}")
    paste_mask(after, bang_outer, 25, 2, OUTLINE)
    paste_mask(after, bang_inner, 25, 2, INK)

    result = base.encode_grid(after, 4, 2)
    details = {
        "text": "방패!!",
        "word_font": {"path": "assets/fonts/Galmuri7.ttf", "size": 8, "stroke": 1},
        "word_mask": [18, 9],
        "word_origin": [5, 3],
        "word_extent": [5, 3, 22, 11],
        "bang_font": {"path": "assets/fonts/galmuri_tmp/Galmuri11-Bold.ttf", "size": 10, "stroke": 1},
        "bang_mask": [6, 12],
        "bang_origin": [25, 2],
        "bang_extent": [25, 2, 30, 13],
        "runtime_columns": [0, 1, 2, 3, 3, "right_cap"],
        "runtime_result": "방패!!",
    }
    return result, after, details


def build_proposed(old: bytes) -> tuple[bytes, list[list[int]], dict[str, Any]]:
    old_block = old[SHIELD_PHYSICAL : SHIELD_PHYSICAL + SHIELD_SIZE]
    if sha(old_block) != EXPECTED_OLD_SHIELD_SHA:
        raise PromotionError(f"shield parent block drift: {sha(old_block)}")
    new_block, pixels, details = build_shield(old_block)
    if sha(new_block) != EXPECTED_NEW_SHIELD_SHA:
        raise PromotionError(f"new shield block drift: {sha(new_block)}")

    candidate = bytearray(old)
    candidate[SHIELD_PHYSICAL : SHIELD_PHYSICAL + SHIELD_SIZE] = new_block
    ws_checksum = sum(candidate[:-2]) & 0xFFFF
    candidate[-2:] = ws_checksum.to_bytes(2, "little")
    result = bytes(candidate)
    if sha(result) != EXPECTED_NEW_TIP_SHA:
        raise PromotionError(f"new TIP drift: {sha(result)}")
    if f"{ws_checksum:04X}" != EXPECTED_NEW_CHECKSUM:
        raise PromotionError(f"new checksum drift: {ws_checksum:04X}")
    return result, pixels, details


def render_runtime(body: list[list[int]], right_cap: list[list[int]], scale: int = 10) -> Image.Image:
    runtime = [body[y][:32] + body[y][24:32] + right_cap[y] for y in range(16)]
    image = Image.new("RGB", (48, 16))
    target = image.load()
    for y, row in enumerate(runtime):
        for x, value in enumerate(row):
            target[x, y] = LIVE_PALETTE.get(value, (value * 17,) * 3)
    return image.resize((48 * scale, 16 * scale), Image.Resampling.NEAREST)


def save_preview(old: bytes, new_pixels: list[list[int]]) -> None:
    old_pixels = base.decode_grid(old[SHIELD_PHYSICAL : SHIELD_PHYSICAL + SHIELD_SIZE], 4, 2)
    success = base.decode_grid(old[SUCCESS_PHYSICAL : SUCCESS_PHYSICAL + SUCCESS_SIZE], 6, 2)
    right_cap = [row[40:48] for row in success]
    before = render_runtime(old_pixels, right_cap)
    after = render_runtime(new_pixels, right_cap)
    label_height = 32
    sheet = Image.new("RGB", (before.width, (before.height + label_height) * 2), (20, 20, 20))
    sheet.paste(before, (0, 0))
    sheet.paste(after, (0, before.height + label_height))
    label_font = ImageFont.truetype(str(BANG_FONT), 17)
    draw = ImageDraw.Draw(sheet)
    draw.text((4, before.height + 3), "before: 방패ㅐ", font=label_font, fill="white")
    draw.text((4, before.height * 2 + label_height + 3), "after: 방패!! / Galmuri7 8px", font=label_font, fill="white")
    PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    temporary = PREVIEW.with_name(f".{PREVIEW.name}.{os.getpid()}.tmp")
    sheet.save(temporary, format="PNG")
    os.replace(temporary, PREVIEW)


def validate(old: bytes, proposed: bytes, new_pixels: list[list[int]]) -> dict[str, Any]:
    old_block = old[SHIELD_PHYSICAL : SHIELD_PHYSICAL + SHIELD_SIZE]
    new_block = proposed[SHIELD_PHYSICAL : SHIELD_PHYSICAL + SHIELD_SIZE]
    old_pixels = base.decode_grid(old_block, 4, 2)
    runs = diff_runs(old, proposed)
    allowed = [
        (SHIELD_PHYSICAL, SHIELD_PHYSICAL + SHIELD_SIZE),
        (ROM_SIZE - 2, ROM_SIZE),
    ]
    unexpected = [
        (start, end)
        for start, end in runs
        if not any(lo <= start and end <= hi for lo, hi in allowed)
    ]

    success = base.decode_grid(old[SUCCESS_PHYSICAL : SUCCESS_PHYSICAL + SUCCESS_SIZE], 6, 2)
    right_cap = [row[40:48] for row in success]
    runtime = [new_pixels[y][:32] + new_pixels[y][24:32] + right_cap[y] for y in range(16)]
    checks = {
        "parent_tip_sha_bound": sha(old) == EXPECTED_TIP_SHA,
        "parent_checksum_valid": checksum(old) == {
            "computed": EXPECTED_TIP_CHECKSUM,
            "stored": EXPECTED_TIP_CHECKSUM,
            "valid": True,
        },
        "old_shield_block_bound": sha(old_block) == EXPECTED_OLD_SHIELD_SHA,
        "new_shield_block_bound": sha(new_block) == EXPECTED_NEW_SHIELD_SHA,
        "new_tip_sha_bound": sha(proposed) == EXPECTED_NEW_TIP_SHA,
        "new_checksum_valid": checksum(proposed) == {
            "computed": EXPECTED_NEW_CHECKSUM,
            "stored": EXPECTED_NEW_CHECKSUM,
            "valid": True,
        },
        "diff_allowlist_clean": not unexpected,
        "changed_bytes_expected": sum(end - start for start, end in runs) == 121,
        "diff_runs_expected": len(runs) == 45,
        "rounded_left_edge_x0_x4_exact": all(
            new_pixels[y][0:5] == old_pixels[y][0:5] for y in range(16)
        ),
        "word_confined_before_duplicate_column": all(
            new_pixels[y][23] in (BACKGROUND, OUTLINE) for y in range(16)
        ),
        "duplicate_column_identical": all(
            runtime[y][24:32] == runtime[y][32:40] for y in range(16)
        ),
        "runtime_width_48": all(len(row) == 48 for row in runtime),
        "success_right_cap_source_unchanged": (
            proposed[SUCCESS_PHYSICAL : SUCCESS_PHYSICAL + SUCCESS_SIZE]
            == old[SUCCESS_PHYSICAL : SUCCESS_PHYSICAL + SUCCESS_SIZE]
        ),
        "runtime_hook_unchanged": proposed[RUNTIME_HOOK[0] : sum(RUNTIME_HOOK)] == old[RUNTIME_HOOK[0] : sum(RUNTIME_HOOK)],
        "runtime_cave_unchanged": proposed[RUNTIME_CAVE[0] : sum(RUNTIME_CAVE)] == old[RUNTIME_CAVE[0] : sum(RUNTIME_CAVE)],
        "private_blank_area_unchanged": proposed[PRIVATE_BLANK[0] : sum(PRIVATE_BLANK)] == old[PRIVATE_BLANK[0] : sum(PRIVATE_BLANK)],
    }
    if not all(checks.values()):
        raise PromotionError(f"static validation failed: {checks}")
    return {
        "checks": checks,
        "diff": {
            "changed_bytes_including_checksum": sum(end - start for start, end in runs),
            "run_count": len(runs),
            "allowlist": [
                {"start": f"{start:08X}", "end_exclusive": f"{end:08X}"}
                for start, end in allowed
            ],
            "unexpected": unexpected,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="back up and replace current main TIP")
    args = parser.parse_args()

    old = require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)
    live_save = require(TIP_SAVE, size=SAVE_SIZE, expected_sha=EXPECTED_SAVE_SHA)
    require(WORD_FONT, expected_sha=EXPECTED_WORD_FONT_SHA)
    require(BANG_FONT, expected_sha=EXPECTED_BANG_FONT_SHA)
    proposed, new_pixels, details = build_proposed(old)
    validation = validate(old, proposed, new_pixels)

    dry_run = {
        "mode": "dry_run",
        "ok": True,
        "current_main": identity(TIP, old),
        "proposed_main": {"size": len(proposed), "sha256": sha(proposed), "checksum": checksum(proposed)},
        "live_saveram": identity(TIP_SAVE, live_save),
        "layout": details,
        "validation": validation,
        "bizhawk_automation": "not_run",
    }
    if not args.commit:
        print(json.dumps(dry_run, ensure_ascii=False, indent=2))
        return 0

    save_before = identity(TIP_SAVE, live_save)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATCH / "backup" / f"{stamp}_pre_id_command_shield_uniform_galmuri7"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / TIP.name
    shutil.copy2(TIP, backup)
    require(backup, size=ROM_SIZE, expected_sha=EXPECTED_TIP_SHA)

    atomic_bytes(CANDIDATE, proposed)
    shutil.copy2(TIP_SAVE, CANDIDATE_SAVE)
    save_preview(old, new_pixels)
    if require(CANDIDATE, size=ROM_SIZE, expected_sha=EXPECTED_NEW_TIP_SHA) != proposed:
        raise PromotionError("candidate reread mismatch")
    if require(CANDIDATE_SAVE, size=SAVE_SIZE, expected_sha=EXPECTED_SAVE_SHA) != live_save:
        raise PromotionError("paired SaveRAM reread mismatch")

    build_report = {
        "schema_version": 1,
        "generated_by": "tools/promote_id_command_shield_uniform_galmuri7_to_current_main.py",
        "ok": True,
        "status": "user_approved_static_candidate",
        "parent": identity(TIP, old),
        "candidate": identity(CANDIDATE, proposed),
        "paired_saveram": identity(CANDIDATE_SAVE, live_save),
        "word_font": identity(WORD_FONT),
        "bang_font": identity(BANG_FONT),
        "preview": identity(PREVIEW),
        "layout": details,
        "validation": validation,
        "bizhawk_automation": "not_run_by_user_request",
    }
    atomic_json(BUILD_REPORT, build_report)

    try:
        atomic_bytes(TIP, proposed)
        new = require(TIP, size=ROM_SIZE, expected_sha=EXPECTED_NEW_TIP_SHA)
        post_checks = {
            "main_equals_candidate": new == CANDIDATE.read_bytes(),
            "main_checksum_valid": checksum(new) == {
                "computed": EXPECTED_NEW_CHECKSUM,
                "stored": EXPECTED_NEW_CHECKSUM,
                "valid": True,
            },
            "rollback_rom_exact": sha(backup.read_bytes()) == EXPECTED_TIP_SHA,
            "live_saveram_unchanged": identity(TIP_SAVE) == save_before,
            "shield_block_exact": sha(new[SHIELD_PHYSICAL : SHIELD_PHYSICAL + SHIELD_SIZE]) == EXPECTED_NEW_SHIELD_SHA,
            "outside_shield_preserved": all(
                before == after
                or SHIELD_PHYSICAL <= index < SHIELD_PHYSICAL + SHIELD_SIZE
                or index >= ROM_SIZE - 2
                for index, (before, after) in enumerate(zip(old, new, strict=True))
            ),
            "runtime_hook_unchanged": new[RUNTIME_HOOK[0] : sum(RUNTIME_HOOK)] == old[RUNTIME_HOOK[0] : sum(RUNTIME_HOOK)],
            "runtime_cave_unchanged": new[RUNTIME_CAVE[0] : sum(RUNTIME_CAVE)] == old[RUNTIME_CAVE[0] : sum(RUNTIME_CAVE)],
        }
        if not all(post_checks.values()):
            raise PromotionError(f"postpromotion audit failed: {post_checks}")
        post = {
            "schema_version": 1,
            "generated_by": "tools/promote_id_command_shield_uniform_galmuri7_to_current_main.py",
            "ok": True,
            "old_main": {"sha256": EXPECTED_TIP_SHA},
            "new_main": identity(TIP, new),
            "new_checksum": checksum(new),
            "rollback_rom": identity(backup),
            "live_saveram_before": save_before,
            "live_saveram_after": identity(TIP_SAVE),
            "checks": post_checks,
            "bizhawk_automation": "not_run_by_user_request",
        }
        atomic_json(POST_AUDIT, post)
    except Exception:
        atomic_bytes(TIP, old)
        raise

    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_id_command_shield_uniform_galmuri7_to_current_main.py",
        "mode": "commit",
        "ok": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "old_main": {"path": "out/patch/monoeye_ko_expanded.wsc", "size": ROM_SIZE, "sha256": EXPECTED_TIP_SHA},
        "new_main": identity(TIP),
        "rollback_rom": identity(backup),
        "scope": "shield plaque body 0xCC4BB4-0xCC4CB3 plus WonderSwan checksum only",
        "display": "방패!!",
        "layout": details,
        "build_report": identity(BUILD_REPORT),
        "postpromotion_audit": identity(POST_AUDIT),
        "live_saveram": identity(TIP_SAVE),
        "bizhawk_automation": "not_run_by_user_request",
    }
    atomic_json(PROMOTION_REPORT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
