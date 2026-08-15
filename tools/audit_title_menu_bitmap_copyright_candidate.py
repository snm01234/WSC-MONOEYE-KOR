#!/usr/bin/env python3
"""Independently audit the title-menu Bitmap + copyright candidate."""
from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_DIR = ROOT / "out/patch/title_menu_bitmap_copyright_candidate"
CANDIDATE = (
    CANDIDATE_DIR / "monoeye_ko_expanded_title_menu_bitmap_copyright_test.wsc"
)
CANDIDATE_SAVE = (
    CANDIDATE_DIR / "monoeye_ko_expanded_title_menu_bitmap_copyright_test.sav"
)
BUILD_REPORT = CANDIDATE_DIR / "title_menu_bitmap_copyright_report.json"
REPORT = CANDIDATE_DIR / "title_menu_bitmap_copyright_audit.json"
COPYRIGHT = ROOT / "data/title_copyright_ko.json"
LABELS = ROOT / "data/menu_plate_labels_ko.json"

EXPECTED_MAIN_SHA256 = (
    "c0a2b429e9162c9648c21fbbab0dcd28b70c0cdcc0966b11407cef2db54b2631"
)
EXPECTED_SAVE_SHA256 = (
    "7edaa450d28eaeeebea61bd59b710480e333a805c4872ec8d8adeb5efd780d99"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STOCK_BASE = 0x800000
MENU_LO = 0x720080
MENU_HI = 0x7248FF
COPYRIGHT_LO = 0x5519DC
COPYRIGHT_BYTES = 1792
PLATE_COUNT = 29
PLATE_SIZE = 0x280


class AuditError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict:
    raw = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(raw), "sha256": sha256(raw)}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def ws_checksum(rom: bytes) -> tuple[int, int, bool]:
    stored = struct.unpack_from("<H", rom, len(rom) - 2)[0]
    computed = sum(rom[:-2]) & 0xFFFF
    return stored, computed, stored == computed


def changed_offsets(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise AuditError("diff inputs have different sizes")
    return [i for i, (a, b) in enumerate(zip(before, after)) if a != b]


def main() -> int:
    required = (
        MAIN,
        LIVE_SAVE,
        CANDIDATE,
        CANDIDATE_SAVE,
        BUILD_REPORT,
        COPYRIGHT,
        LABELS,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise AuditError(f"missing inputs: {missing}")

    parent = MAIN.read_bytes()
    live_save = LIVE_SAVE.read_bytes()
    candidate = CANDIDATE.read_bytes()
    candidate_save = CANDIDATE_SAVE.read_bytes()
    build = json.loads(BUILD_REPORT.read_text(encoding="utf-8"))
    spec = json.loads(COPYRIGHT.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))

    if len(parent) != ROM_SIZE or sha256(parent) != EXPECTED_MAIN_SHA256:
        raise AuditError(f"main TIP drifted: {sha256(parent)}")
    if len(live_save) != SAVE_SIZE or sha256(live_save) != EXPECTED_SAVE_SHA256:
        raise AuditError(f"live SaveRAM drifted: {sha256(live_save)}")
    if sha256(parent) != sha256(MAIN.read_bytes()):
        raise AuditError("main TIP changed during audit")
    if sha256(candidate) != build["candidate"]["sha256"]:
        raise AuditError("candidate SHA-256 does not match the build report")
    if candidate_save != live_save:
        raise AuditError("paired SaveRAM is not byte-exact with live SaveRAM")
    stored, computed, valid = ws_checksum(candidate)
    if not valid:
        raise AuditError(f"candidate checksum invalid: {stored:04X} != {computed:04X}")
    if f"{stored:04X}" != build["checksum"]:
        raise AuditError("checksum drifted from build report")

    diffs = changed_offsets(parent, candidate)
    allowed = set(range(STOCK_BASE + MENU_LO, STOCK_BASE + MENU_HI + 1))
    allowed.update(
        range(STOCK_BASE + COPYRIGHT_LO, STOCK_BASE + COPYRIGHT_LO + COPYRIGHT_BYTES)
    )
    allowed.update((ROM_SIZE - 2, ROM_SIZE - 1))
    outside = [off for off in diffs if off not in allowed]
    if outside:
        raise AuditError(
            f"unexpected diffs: {[f'{off:06X}' for off in outside[:12]]}"
        )

    plate_diffs = [
        off for off in diffs if STOCK_BASE + MENU_LO <= off <= STOCK_BASE + MENU_HI
    ]
    copyright_diffs = [
        off
        for off in diffs
        if STOCK_BASE + COPYRIGHT_LO
        <= off
        < STOCK_BASE + COPYRIGHT_LO + COPYRIGHT_BYTES
    ]
    if len(build["plates"]) != PLATE_COUNT:
        raise AuditError(f"expected {PLATE_COUNT} plates, got {len(build['plates'])}")
    if any(item.get("clipped_px") for item in build["plates"]):
        raise AuditError("build report records clipped plate ink")
    if build["font_size"] != 16:
        raise AuditError("plate font size is not 16")
    if "Galmuri11Bitmap-Regular" not in build["font"]:
        raise AuditError("plate font is not Galmuri11Bitmap Regular")

    expected_labels = {row["jp"]: row["ko"] for row in labels["labels"]}
    for item in build["plates"]:
        if expected_labels.get(item["jp"]) != item["text"]:
            raise AuditError(f"plate label drifted: {item}")
        if item["size"] != 16:
            raise AuditError(f"plate {item['plate']} was not rasterised at 16 px")

    x0 = int(spec["keep_first_copyright_x1"])
    x1 = int(spec["keep_english_x0"])
    parent_blob = parent[
        STOCK_BASE + COPYRIGHT_LO : STOCK_BASE + COPYRIGHT_LO + COPYRIGHT_BYTES
    ]
    cand_blob = candidate[
        STOCK_BASE + COPYRIGHT_LO : STOCK_BASE + COPYRIGHT_LO + COPYRIGHT_BYTES
    ]
    if parent_blob == cand_blob:
        raise AuditError("copyright strip did not change")
    if not build["copyright"]["keep_first_copyright_exact"]:
        raise AuditError("first © columns were not preserved")
    if not build["copyright"]["keep_english_exact"]:
        raise AuditError("English ©BANDAI 2002 columns were not preserved")
    if build["copyright"]["ko"] != spec["ko"]:
        raise AuditError("copyright Korean text drifted")
    if build["copyright"]["last_stroke_x"] >= x1:
        raise AuditError("Korean copyright ink reached the English zone")
    if "Galmuri9Bitmap-Regular" not in build["copyright"].get("font", ""):
        raise AuditError("copyright font is not Galmuri9 Bitmap Regular")
    if build["copyright"].get("size") != 12:
        raise AuditError("copyright font size is not 12")
    if build["copyright"].get("stroke_y", [None])[0] != 1:
        raise AuditError("copyright Hangul top is not aligned to BANDAI y=1")
    if build["copyright"].get("stroke_height", 99) > 10:
        raise AuditError("copyright Hangul is taller than BANDAI")

    # Independent column preservation: decode both blobs and compare keep zones.
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    from build_id_command_plaques_ko_candidate import decode_grid  # noqa: E402

    before = decode_grid(parent_blob, 28, 2)
    after = decode_grid(cand_blob, 28, 2)
    for y in range(16):
        for x in list(range(0, x0)) + list(range(x1, 224)):
            if before[y][x] != after[y][x]:
                raise AuditError(f"reserved copyright pixel changed at {x},{y}")
    jp_changed = sum(
        1
        for y in range(16)
        for x in range(x0, x1)
        if before[y][x] != after[y][x]
    )
    if jp_changed == 0:
        raise AuditError("Japanese copyright zone did not change")

    payload = {
        "generated_by": "tools/audit_title_menu_bitmap_copyright_candidate.py",
        "ok": True,
        "parent": identity(MAIN, parent),
        "live_save": identity(LIVE_SAVE, live_save),
        "candidate": identity(CANDIDATE, candidate),
        "candidate_save": identity(CANDIDATE_SAVE, candidate_save),
        "checksum": {"stored": f"{stored:04X}", "computed": f"{computed:04X}", "valid": valid},
        "diff_bytes": len(diffs),
        "menu_plate_diff_bytes": len(plate_diffs),
        "copyright_diff_bytes": len(copyright_diffs),
        "checksum_diff_bytes": sum(1 for off in diffs if off >= ROM_SIZE - 2),
        "outside_declared_ranges": 0,
        "plates": PLATE_COUNT,
        "copyright_jp_pixels_changed": jp_changed,
        "main_tip_unchanged": True,
        "live_save_unchanged": True,
        "paired_save_byte_exact": True,
        "keep_first_copyright": True,
        "keep_english": True,
        "font": build["font"],
        "font_size": build["font_size"],
        "copyright_font": build["copyright"]["font"],
        "copyright_size": build["copyright"]["size"],
        "copyright_stroke_y": build["copyright"]["stroke_y"],
        "copyright_stroke_height": build["copyright"]["stroke_height"],
    }
    atomic_json(REPORT, payload)
    print(f"audit ok -> {rel(REPORT)}")
    print(
        f"diff {len(diffs)} B  plates {len(plate_diffs)}  "
        f"copyright {len(copyright_diffs)}  checksum {payload['checksum_diff_bytes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
