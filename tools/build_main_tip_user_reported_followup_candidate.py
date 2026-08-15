#!/usr/bin/env python3
"""Build the 2026-08-13 user-reported main-TIP correction candidate.

The builder is fail-closed against the exact current main TIP.  It adds the two
missing Hangul glyphs required by the requested weapon spellings, extends the
installed sticky glyph window by exactly two slots, rewrites only proven
private ext3 phrases, and applies four fixed-record runtime structure repairs.
It never overwrites the live main TIP or live SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_hangul_font import render_compact_glyph  # noqa: E402
from build_terminology_retranslation_candidate import (  # noqa: E402
    encode,
    ext3_storage_proof,
    inplace_phrase,
)
from font_pipeline import find_system_font  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import (  # noqa: E402
    COMPACT_FONT_RECORD_SIZE,
    Tbl,
    encode_compact_font_record,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)
from patch_font_hangul_hook import STORE_SITE, build_store_cave  # noqa: E402
from patch_pad3_expansion import PAD12_SLOTS  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
SPEC = ROOT / "data/main_tip_user_reported_followup_ko.json"
OUT_ROM = PATCH / "main_tip_user_reported_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/main_tip_user_reported_followup_candidate.sav"
OUT_TBL = PATCH / "main_tip_user_reported_followup_candidate.tbl"
REPORT = PATCH / "main_tip_user_reported_followup_candidate_report.json"

EXPECTED_MAIN_SHA = "e22ccc450c64f7751d61a80d6cd52f94363d981e5d5a7e1802afa57dfd224862"
EXPECTED_TBL_SHA = "d539fdd70a36a67a3a0183f09596b5b535b2501c51f7580190dc73a22543b98d"
EXPECTED_SAVE_SHA = "cb55d023078847eaed023449ce9786b7265193956344ada4d9bcea7a6b729706"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MARKER = 0xEC8D
GLYPH_BASE = 0xE740
OLD_STICKY_COUNT = 1346
NEW_STICKY_COUNT = 1348
NEW_GLYPHS = {"윕": 0xEC82, "팬": 0xEC83}

DICT_TARGETS = {
    0x0FF20: ("스크루・웨브", "스크류　윕"),
    0x0FEDC: ("히트　부채", "히트　팬"),
    0x0FF0A: ("빔　부채", "빔　팬"),
    0x0C70C: ("병기　스크루・웹을", "병기　스크류　윕을"),
    0x017A6: ("면、면、메엥！！", "면、면、며언！！"),
    0x0F4A0: ("이놈아아아！！", "네놈이이！！"),
    0x01181: ("네놈아아아！！", "네놈이이！！"),
    0x0513F: ("네놈아아아！！", "네놈이이！"),
    0x061A2: ("네놈아아아！！", "네놈이이！！"),
    0x040E7: ("이、　이놈아아앗！！", "이、　네놈이이！！"),
}

EXPECTED_TOKEN_SITES = {
    0x0FF20: (0x75C8CD,),
    0x0FEDC: (0x75CB51,),
    0x0FF0A: (0x75C9A2,),
    0x0C70C: (0x5C4F36,),
    0x017A6: (0x5D9D36, 0x5DA061, 0x5DA38C),
    0x0F4A0: (0x6117AD,),
    0x01181: (0x611FB7,),
    0x0513F: (0x632F4F,),
    0x061A2: (0x639D1A,),
    0x040E7: (0x62A4D2,),
}

LORAN_RECORDS = (0x5E4477, 0x5E4620)
LORAN_BEFORE = bytes.fromhex("69E518380F010101010101010101")
LORAN_AFTER = bytes.fromhex("E518380F01010101010101010101")
LORAN_RENDER = "…모두！<E62F>대피하세요！"

DIANA_RECORDS = (0x5D8DFB, 0x5D8F0A)
DIANA_BEFORE = bytes.fromhex("F55A01")
DIANA_AFTER = bytes.fromhex("45F55A")
DIANA_BODY_RENDER = "저는"


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": len(data),
        "sha256": sha(data),
    }


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def strip_pad(text: str) -> str:
    return text.rstrip("　 \t")


def ext3_token(index: int) -> bytes:
    if not 0x1000 <= index <= 0x10FFF:
        raise BuildError(f"invalid ext3 index {index:05X}")
    slot = index - 0x1000
    if (slot & 0xFF) == 0:
        raise BuildError(f"NUL ext3 trail for {index:05X}")
    return bytes((0xE5, 0x18, (slot >> 8) & 0xFF, slot & 0xFF))


def find_all(data: bytes, needle: bytes) -> list[int]:
    found: list[int] = []
    cursor = 0
    while True:
        cursor = data.find(needle, cursor)
        if cursor < 0:
            return found
        found.append(cursor)
        cursor += 1


def candidate_tbl(base: Tbl, text: str) -> tuple[Tbl, str]:
    if marker_code() != MARKER or base.code_to_char.get(MARKER) != "":
        raise BuildError("installed EC8D marker contract drifted")
    for ch, code in NEW_GLYPHS.items():
        if ch in base.char_to_code or code in base.code_to_char:
            raise BuildError(f"new glyph slot is not free: {ch}={code:04X}")

    codes = dict(base.code_to_char)
    codes.update({code: ch for ch, code in NEW_GLYPHS.items()})
    char_to_code: dict[str, int] = {}
    for code, ch in codes.items():
        if ch and ch not in char_to_code:
            char_to_code[ch] = code
    built = Tbl(codes, char_to_code)

    lines = text.splitlines()
    marker_line = next((i for i, line in enumerate(lines) if line == "EC8D="), None)
    if marker_line is None or any(line.startswith(("EC82=", "EC83=")) for line in lines):
        raise BuildError("TBL tail contract drifted")
    lines[marker_line:marker_line] = ["EC82=윕", "EC83=팬"]
    for i, line in enumerate(lines):
        if line.startswith("# Hangul marker moved EC80->EC8D;"):
            lines[i] = (
                "# Hangul marker EC8D; EC80/EC81 are terminology glyphs and "
                "EC82/EC83 are user-follow-up glyphs."
            )
            break
    return built, "\n".join(lines) + "\n"


def locate_store_cave(rom: bytes | bytearray) -> int:
    sb = stock_base(rom)
    site = sb + STORE_SITE
    if rom[site] != 0xE8:
        raise BuildError("installed glyph store hook is missing")
    rel = struct.unpack_from("<H", rom, site + 1)[0]
    store_ip = (STORE_SITE + 3 + rel) & 0xFFFF
    return sb + ((STORE_SITE & 0xFF0000) | store_ip)


def diff_runs(before: bytes, after: bytes) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    pos = 0
    while pos < len(before):
        if before[pos] == after[pos]:
            pos += 1
            continue
        start = pos
        while pos < len(before) and before[pos] != after[pos]:
            pos += 1
        runs.append((start, pos))
    return runs


def covered(run: tuple[int, int], intervals: list[tuple[int, int]]) -> bool:
    start, end = run
    cursor = start
    for lo, hi in sorted(intervals):
        if hi <= cursor:
            continue
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return cursor >= end


def main() -> int:
    parent = MAIN.read_bytes()
    save = MAIN_SAVE.read_bytes()
    tbl_text = TBL_PATH.read_text(encoding="utf-8")
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(parent)}")
    if len(save) != SAVE_SIZE or sha(save) != EXPECTED_SAVE_SHA:
        raise BuildError("live SaveRAM identity drifted")
    if sha(tbl_text.encode("utf-8")) != EXPECTED_TBL_SHA:
        raise BuildError("active TBL identity drifted")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("review_status") != "approved_for_main_tip":
        raise BuildError("follow-up data catalog is not approved")

    base_tbl = Tbl.load(TBL_PATH)
    new_tbl, new_tbl_text = candidate_tbl(base_tbl, tbl_text)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)

    # Prove every shared phrase has exactly the expected live direct consumers.
    token_site_report: list[dict[str, Any]] = []
    for index, expected_sites in EXPECTED_TOKEN_SITES.items():
        physical = find_all(parent, ext3_token(index))
        logical = tuple(pos - sb for pos in physical if sb <= pos < sb + 0x800000)
        if logical != expected_sites:
            raise BuildError(
                f"ext3 consumer drift for {index:05X}: "
                f"got={[f'{x:06X}' for x in logical]} "
                f"expected={[f'{x:06X}' for x in expected_sites]}"
            )
        token_site_report.append(
            {"index": f"{index:05X}", "logical_sites": [f"{x:06X}" for x in logical]}
        )

    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    # Add exactly two compact glyphs to the next two pristine pad3 slots.
    font_path = find_system_font()
    glyph_rows: list[dict[str, Any]] = []
    for ch, code in NEW_GLYPHS.items():
        slot = code - GLYPH_BASE
        offset = (slot - PAD12_SLOTS) * COMPACT_FONT_RECORD_SIZE
        old = bytes(candidate[offset : offset + COMPACT_FONT_RECORD_SIZE])
        if old != b"\xFF" * COMPACT_FONT_RECORD_SIZE:
            raise BuildError(f"pad3 glyph slot is not pristine: {ch}={code:04X}")
        record = encode_compact_font_record(render_compact_glyph(ch, font_path))
        if len(record) != COMPACT_FONT_RECORD_SIZE:
            raise BuildError(f"invalid rendered glyph size for {ch}")
        candidate[offset : offset + COMPACT_FONT_RECORD_SIZE] = record
        allowed.append((offset, offset + COMPACT_FONT_RECORD_SIZE))
        glyph_rows.append(
            {
                "char": ch,
                "code": f"{code:04X}",
                "slot": slot,
                "file_offset": f"{offset:06X}",
                "before_sha256": sha(old),
                "after_sha256": sha(record),
                "font": font_path,
            }
        )

    # Extend the installed sticky store window from EC81 through EC83.
    store_abs = locate_store_cave(candidate)
    old_store = build_store_cave(GLYPH_BASE - 0xDF20, OLD_STICKY_COUNT)
    new_store = build_store_cave(GLYPH_BASE - 0xDF20, NEW_STICKY_COUNT)
    if len(old_store) != len(new_store) or bytes(candidate[store_abs : store_abs + len(old_store)]) != old_store:
        raise BuildError("installed 1346-slot sticky cave drifted")
    candidate[store_abs : store_abs + len(new_store)] = new_store
    allowed.append((store_abs, store_abs + len(new_store)))

    # Rewrite proven private ext3 phrase storage in place.
    dictionary_rows: list[dict[str, Any]] = []
    for index, (expected_before, target) in DICT_TARGETS.items():
        current_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        before = strip_pad(current_dictionary.expand_index(index, base_tbl))
        if before != expected_before:
            raise BuildError(f"dictionary source drift at {index:05X}: {before!r}")
        proof = ext3_storage_proof(bytes(candidate), current_dictionary, index)
        encoded = encode(target, new_tbl)
        extent = inplace_phrase(candidate, proof, encoded)
        allowed.append(extent)
        after_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        after = strip_pad(after_dictionary.expand_index(index, new_tbl))
        if after != target:
            raise BuildError(f"dictionary verify failed at {index:05X}: {after!r}")
        dictionary_rows.append(
            {
                "index": f"{index:05X}",
                "before": before,
                "after": after,
                "entry_abs": f"{int(proof['entry_abs']):06X}",
                "old_len": int(proof["old_len"]),
                "new_len": len(encoded),
                "private_storage": bool(proof["ok"]),
            }
        )

    # Loran: remove the runtime-proven visible 69=み false metadata lead.
    record_rows: list[dict[str, Any]] = []
    for logical in LORAN_RECORDS:
        at = sb + logical
        if bytes(candidate[at : at + len(LORAN_BEFORE)]) != LORAN_BEFORE or candidate[at + len(LORAN_BEFORE)] != 0:
            raise BuildError(f"Loran record drift at {logical:06X}")
        candidate[at : at + len(LORAN_AFTER)] = LORAN_AFTER
        allowed.append((at, at + len(LORAN_AFTER)))
        record_rows.append(
            {
                "abs": f"{logical:06X}",
                "kind": "loran_false_visible_lead",
                "before_hex": LORAN_BEFORE.hex().upper(),
                "after_hex": LORAN_AFTER.hex().upper(),
                "expected_runtime_text": LORAN_RENDER,
            }
        )

    # Diana: restore the one-byte sprite/speaker metadata while retaining the
    # two-byte Korean body in the original fixed three-byte extent.
    for logical in DIANA_RECORDS:
        at = sb + logical
        if bytes(candidate[at : at + len(DIANA_BEFORE)]) != DIANA_BEFORE or candidate[at + len(DIANA_BEFORE)] != 0:
            raise BuildError(f"Diana record drift at {logical:06X}")
        candidate[at : at + len(DIANA_AFTER)] = DIANA_AFTER
        allowed.append((at, at + len(DIANA_AFTER)))
        record_rows.append(
            {
                "abs": f"{logical:06X}",
                "kind": "diana_restore_sprite_metadata",
                "before_hex": DIANA_BEFORE.hex().upper(),
                "after_hex": DIANA_AFTER.hex().upper(),
                "metadata_hex": "45",
                "expected_runtime_text": DIANA_BODY_RENDER,
            }
        )

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    candidate_bytes = bytes(candidate)

    # Final semantic verification on the completed candidate.
    final_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    semantic_checks: list[dict[str, Any]] = []
    for logical in LORAN_RECORDS:
        got = read_encoded_z_safe(candidate_bytes, sb + logical, max_len=64)
        if got is None:
            raise BuildError(f"candidate Loran record unreadable at {logical:06X}")
        rendered = strip_pad(final_dictionary.expand(got[0], new_tbl))
        ok = rendered == LORAN_RENDER and not rendered.startswith("み")
        semantic_checks.append({"abs": f"{logical:06X}", "rendered": rendered, "ok": ok})
        if not ok:
            raise BuildError(f"candidate Loran render failed at {logical:06X}: {rendered!r}")
    for logical in DIANA_RECORDS:
        got = read_encoded_z_safe(candidate_bytes, sb + logical, max_len=16)
        if got is None or not got[0].startswith(b"\x45"):
            raise BuildError(f"candidate Diana record unreadable at {logical:06X}")
        body = strip_pad(final_dictionary.expand(got[0][1:], new_tbl))
        ok = body == DIANA_BODY_RENDER and len(got[0]) == len(DIANA_AFTER)
        semantic_checks.append({"abs": f"{logical:06X}", "body_rendered": body, "ok": ok})
        if not ok:
            raise BuildError(f"candidate Diana body failed at {logical:06X}: {body!r}")

    runs = diff_runs(parent, candidate_bytes)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected ROM diff runs: {unexpected[:10]}")
    stored = int.from_bytes(candidate_bytes[-2:], "little")
    if (sum(candidate_bytes[:-2]) & 0xFFFF) != stored or stored != checksum:
        raise BuildError("candidate checksum verification failed")

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save)
    atomic_text(OUT_TBL, new_tbl_text)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_main_tip_user_reported_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified",
        "promotion_allowed": True,
        "inputs": {
            "main_tip": identity(MAIN, parent),
            "active_tbl": identity(TBL_PATH, tbl_text.encode("utf-8")),
            "main_saveram": identity(MAIN_SAVE, save),
            "catalog": identity(SPEC),
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, candidate_bytes),
            "candidate_tbl": identity(OUT_TBL, new_tbl_text.encode("utf-8")),
            "candidate_saveram": identity(OUT_SAVE, save),
        },
        "glyphs": glyph_rows,
        "sticky_window": {
            "store_abs": f"{store_abs:06X}",
            "before_count": OLD_STICKY_COUNT,
            "after_count": NEW_STICKY_COUNT,
            "glyph_code_end": "EC83",
            "marker": "EC8D",
        },
        "dictionary_entries": dictionary_rows,
        "token_sites": token_site_report,
        "record_entries": record_rows,
        "semantic_checks": semantic_checks,
        "checks": {
            "dictionary_private_storage_all": all(row["private_storage"] for row in dictionary_rows),
            "dictionary_render_exact_all": len(dictionary_rows) == len(DICT_TARGETS),
            "record_render_exact_all": all(row["ok"] for row in semantic_checks),
            "loran_visible_m_removed_both": all(not row.get("rendered", "").startswith("み") for row in semantic_checks if "rendered" in row),
            "diana_metadata_restored_both": all(candidate_bytes[sb + logical] == 0x45 for logical in DIANA_RECORDS),
            "fixed_record_lengths_and_terminators": all(
                candidate_bytes[sb + logical + len(payload)] == 0
                for logical, payload in [
                    *((logical, LORAN_AFTER) for logical in LORAN_RECORDS),
                    *((logical, DIANA_AFTER) for logical in DIANA_RECORDS),
                ]
            ),
            "diff_allowlist_clean": not unexpected,
            "ws_checksum_valid": True,
            "candidate_saveram_exact_live": OUT_SAVE.read_bytes() == save,
        },
        "diff": {
            "changed_bytes": sum(end - start for start, end in runs),
            "changed_runs": len(runs),
            "unexpected_runs": len(unexpected),
            "runs": [
                {"start": f"{start:06X}", "end": f"{end:06X}", "length": end - start}
                for start, end in runs
            ],
        },
        "ws_checksum": f"{checksum:04X}",
    }
    atomic_text(REPORT, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "ok": True,
        "candidate_rom": report["outputs"]["candidate_rom"],
        "candidate_tbl": report["outputs"]["candidate_tbl"],
        "dictionary_entries": len(dictionary_rows),
        "record_entries": len(record_rows),
        "glyphs": glyph_rows,
        "diff": report["diff"],
        "ws_checksum": report["ws_checksum"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
