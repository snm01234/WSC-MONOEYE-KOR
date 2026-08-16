#!/usr/bin/env python3
"""Build the cumulative post-STAGE18 terminology follow-up candidate.

Parent is the user-validated STAGE18 terminology/control candidate. This build:
- keeps the validated 6002F1 Diana control-text repair byte-exact;
- fixes remaining Bera/Maitza variants in ordinary and five-page alias dictionaries;
- standardizes Hamma Hamma, Kakricon, Jamaican, V2 Assault Buster, and VSBR;
- records Sarah Zabiarov as a terminology guard (current ROM is already canonical);
- adds the single missing Hangul glyph `햄` at EC85 and extends the sticky glyph
  window from 1349 to 1350 slots;
- never modifies the live main TIP or live SaveRAM.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta  # noqa: E402
from build_hangul_font import render_compact_glyph  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_stage17t_global_20cell_followup_candidate import active_dictionary  # noqa: E402
from build_stage18_terminology_control_followup_candidate import payload_at, trim  # noqa: E402
from font_pipeline import find_system_font  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    COMPACT_FONT_RECORD_SIZE,
    Tbl,
    encode_compact_font_record,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402
from patch_font_hangul_hook import STORE_SITE, build_store_cave  # noqa: E402
from patch_pad3_expansion import PAD12_SLOTS  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT = PATCH / "stage18_terminology_control_followup_candidate.wsc"
PARENT_TBL = PATCH / "stage18_terminology_control_followup_candidate.tbl"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC = ROOT / "data/stage18_extended_terminology_followup_ko.json"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "stage18_extended_terminology_followup_candidate.wsc"
OUT_TBL = PATCH / "stage18_extended_terminology_followup_candidate.tbl"
OUT_SAVE = ROOT / "sram/stage18_extended_terminology_followup_candidate.sav"
REPORT = PATCH / "stage18_extended_terminology_followup_candidate_report.json"

EXPECTED_PARENT_SHA = "2ecc78d7da4e508e050de91737c7898818d3b3d2286b49b6366a7541ed7112c4"
EXPECTED_TBL_SHA = "5a6189f77e198dc3fd0b891778eb80512ba60537a74cf9b4353db2bcc520f47a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MARKER = 0xEC8D
GLYPH_BASE = 0xE740
NEW_GLYPH = "햄"
NEW_GLYPH_CODE = 0xEC85
OLD_STICKY_COUNT = 1349
NEW_STICKY_COUNT = 1350


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    return {"path": rel(path), "size": len(data), "sha256": sha(data)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(payload, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def build_candidate_tbl(base: Tbl, text: str) -> tuple[Tbl, str]:
    if marker_code() != MARKER or base.code_to_char.get(MARKER) != "":
        raise BuildError("installed EC8D marker contract drifted")
    if NEW_GLYPH in base.char_to_code or NEW_GLYPH_CODE in base.code_to_char:
        raise BuildError("EC85/햄 is not a free TBL glyph slot")

    codes = dict(base.code_to_char)
    codes[NEW_GLYPH_CODE] = NEW_GLYPH
    char_to_code: dict[str, int] = {}
    for code, ch in codes.items():
        if ch and ch not in char_to_code:
            char_to_code[ch] = code
    built = Tbl(codes, char_to_code)

    lines = text.splitlines()
    marker_line = next((i for i, line in enumerate(lines) if line == "EC8D="), None)
    if marker_line is None or any(line.startswith("EC85=") for line in lines):
        raise BuildError("TBL tail contract drifted")
    lines[marker_line:marker_line] = ["EC85=햄"]
    for i, line in enumerate(lines):
        if line.startswith("# Hangul marker EC8D;"):
            lines[i] = (
                "# Hangul marker EC8D; EC80/EC81 are terminology glyphs, "
                "EC82/EC83 are user-follow-up glyphs, EC84 is 뱀, and EC85 is 햄."
            )
            break
    return built, "\n".join(lines) + "\n"


def encode(text: str, tbl: Tbl) -> bytes:
    value = normalize_ko_text(text)
    out = try_encode_ko_text(
        value,
        tbl,
        hangul_marker_code=MARKER,
        hangul_marker_mode="run",
    )
    if out is None:
        missing = sorted({ch for ch in value if "가" <= ch <= "힣" and ch not in tbl.char_to_code})
        raise BuildError(f"cannot encode {text!r}; missing={missing}")
    if b"\x00" in out:
        raise BuildError(f"encoded text contains NUL: {text!r}")
    return out


def locate_store_cave(rom: bytes | bytearray) -> int:
    sb = stock_base(rom)
    site = sb + STORE_SITE
    if rom[site] != 0xE8:
        raise BuildError("installed glyph store hook is missing")
    rel16 = struct.unpack_from("<H", rom, site + 1)[0]
    store_ip = (STORE_SITE + 3 + rel16) & 0xFFFF
    return sb + ((STORE_SITE & 0xFF0000) | store_ip)


def main() -> int:
    parent = PARENT.read_bytes()
    tbl_bytes = PARENT_TBL.read_bytes()
    save = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent identity drift: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError("parent TBL identity drift")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drift: {len(save)}")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if str(spec.get("parent_sha256") or "").lower() != EXPECTED_PARENT_SHA:
        raise BuildError("spec parent SHA drift")

    base_tbl = Tbl.load(PARENT_TBL)
    tbl, tbl_text = build_candidate_tbl(base_tbl, tbl_bytes.decode("utf-8"))
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dict = active_dictionary(parent, ext_meta, ext3_meta)
    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    sb = stock_base(parent)

    # Add the missing compact Hangul glyph required by 햄머 햄머.
    font_path = find_system_font()
    if not font_path:
        raise BuildError("no project/system font available for 햄 glyph")
    slot = NEW_GLYPH_CODE - GLYPH_BASE
    glyph_offset = (slot - PAD12_SLOTS) * COMPACT_FONT_RECORD_SIZE
    glyph_before = bytes(candidate[glyph_offset : glyph_offset + COMPACT_FONT_RECORD_SIZE])
    if glyph_before != b"\xFF" * COMPACT_FONT_RECORD_SIZE:
        raise BuildError("EC85 compact font slot is not pristine")
    glyph_record = encode_compact_font_record(render_compact_glyph(NEW_GLYPH, font_path))
    if len(glyph_record) != COMPACT_FONT_RECORD_SIZE:
        raise BuildError("rendered 햄 glyph record size drift")
    candidate[glyph_offset : glyph_offset + COMPACT_FONT_RECORD_SIZE] = glyph_record
    allowed.append((glyph_offset, glyph_offset + COMPACT_FONT_RECORD_SIZE))

    # Extend the proven sticky Hangul window by exactly one slot: EC84 -> EC85.
    store_abs = locate_store_cave(candidate)
    old_store = build_store_cave(GLYPH_BASE - 0xDF20, OLD_STICKY_COUNT)
    new_store = build_store_cave(GLYPH_BASE - 0xDF20, NEW_STICKY_COUNT)
    if len(old_store) != len(new_store):
        raise BuildError("sticky store cave size changed")
    if bytes(candidate[store_abs : store_abs + len(old_store)]) != old_store:
        raise BuildError("installed 1349-slot sticky glyph store drifted")
    candidate[store_abs : store_abs + len(new_store)] = new_store
    allowed.append((store_abs, store_abs + len(new_store)))

    # Ordinary/extended dictionary rewrites. Every requested phrase is same or
    # shorter in bytes, so pointer tables remain byte-exact.
    dict_changes: list[dict[str, Any]] = []
    for row in spec.get("dictionary_rewrites") or []:
        index = int(row["index"], 16)
        before_text = trim(parent_dict.expand_index(index, base_tbl))
        expected_before = trim(normalize_ko_text(str(row["before"])))
        expected_after = trim(normalize_ko_text(str(row["after"])))
        if before_text != expected_before:
            raise BuildError(f"dictionary source drift {index:05X}: {before_text!r} != {expected_before!r}")
        raw_before = parent_dict.raw_entry(index)
        raw_after = encode(expected_after, tbl)
        if len(raw_after) > len(raw_before):
            raise BuildError(f"dictionary rewrite would grow {index:05X}: {len(raw_before)} -> {len(raw_after)}")
        abs_off = parent_dict.entry_abs(index)
        span = len(raw_before) + 1
        replacement = raw_after + b"\x00" * (span - len(raw_after))
        candidate[abs_off : abs_off + span] = replacement
        allowed.append((abs_off, abs_off + span))
        dict_changes.append({
            "index": f"{index:05X}",
            "entry_abs": f"{abs_off:07X}",
            "before": expected_before,
            "after": expected_after,
            "old_bytes": len(raw_before),
            "new_bytes": len(raw_after),
        })

    # Direct record byte rewrites are restricted to explicit same-length
    # one-byte text separators. This keeps the record's token grammar and
    # terminator byte-exact while enforcing the canonical visible spelling.
    direct_changes: list[dict[str, Any]] = []
    for row in spec.get("direct_record_byte_rewrites") or []:
        logical = int(row["abs"], 16)
        before_bytes = bytes.fromhex(str(row["before"]))
        after_bytes = bytes.fromhex(str(row["after"]))
        if len(before_bytes) != len(after_bytes):
            raise BuildError(f"direct byte rewrite length drift at {logical:06X}")
        file_pos = sb + logical
        actual = bytes(candidate[file_pos : file_pos + len(before_bytes)])
        if actual != before_bytes:
            raise BuildError(f"direct byte source drift {logical:06X}: {actual.hex().upper()}")
        candidate[file_pos : file_pos + len(after_bytes)] = after_bytes
        allowed.append((file_pos, file_pos + len(after_bytes)))
        direct_changes.append({
            "abs": f"{logical:06X}",
            "before": before_bytes.hex().upper(),
            "after": after_bytes.hex().upper(),
            "reason": row.get("reason") or "",
        })

    # Five-page runtime alias dictionary rewrites. All requested forms are
    # same-or-shorter, so no alias pointer is changed.
    alias_changes: list[dict[str, Any]] = []
    for row in spec.get("alias_dictionary_rewrites") or []:
        seg = int(row["segment"], 16)
        local = int(row["local"], 16)
        expected_ptr = int(row["expected_pointer"], 16)
        bank_start = seg * BANK_SIZE
        bank = bytearray(candidate[bank_start : bank_start + BANK_SIZE])
        ptr = struct.unpack_from("<H", bank, local * 2)[0]
        if ptr != expected_ptr:
            raise BuildError(f"alias pointer drift {seg:02X}:{local:04X}: {ptr:04X}")
        end = bank.find(b"\x00", ptr)
        if end < 0:
            raise BuildError(f"alias phrase unterminated {seg:02X}:{local:04X}")
        raw_before = bytes(bank[ptr:end])
        before_text = trim(parent_dict.expand(raw_before, base_tbl))
        expected_before = trim(normalize_ko_text(str(row["before"])))
        expected_after = trim(normalize_ko_text(str(row["after"])))
        if before_text != expected_before:
            raise BuildError(f"alias source drift {seg:02X}:{local:04X}: {before_text!r} != {expected_before!r}")
        raw_after = encode(expected_after, tbl)
        if len(raw_after) > len(raw_before):
            raise BuildError(f"alias rewrite would grow {seg:02X}:{local:04X}")
        span = len(raw_before) + 1
        bank[ptr : ptr + span] = raw_after + b"\x00" * (span - len(raw_after))
        candidate[bank_start : bank_start + BANK_SIZE] = bank
        allowed.append((bank_start + ptr, bank_start + ptr + span))
        alias_changes.append({
            "segment": f"{seg:02X}",
            "local": f"{local:04X}",
            "pointer": f"{ptr:04X}",
            "before": expected_before,
            "after": expected_after,
            "old_bytes": len(raw_before),
            "new_bytes": len(raw_after),
        })

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum verification failed")
    unexpected = [run for run in diff_runs(parent, result) if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:12]}")

    final_dict = active_dictionary(result, ext_meta, ext3_meta)
    for row in spec.get("dictionary_rewrites") or []:
        rendered = trim(final_dict.expand_index(int(row["index"], 16), tbl))
        expected_after = trim(normalize_ko_text(str(row["after"])))
        if rendered != expected_after:
            raise BuildError(f"final dictionary render mismatch {row['index']}: {rendered!r}")
    for row in spec.get("alias_dictionary_rewrites") or []:
        seg = int(row["segment"], 16)
        local = int(row["local"], 16)
        bank_start = seg * BANK_SIZE
        ptr = int.from_bytes(result[bank_start + local * 2 : bank_start + local * 2 + 2], "little")
        end = result.find(b"\x00", bank_start + ptr)
        if end < 0:
            raise BuildError(f"final alias unterminated {seg:02X}:{local:04X}")
        rendered = trim(final_dict.expand(result[bank_start + ptr : end], tbl))
        expected_after = trim(normalize_ko_text(str(row["after"])))
        if rendered != expected_after:
            raise BuildError(f"final alias render mismatch {seg:02X}:{local:04X}: {rendered!r}")

    # Runtime/display guards around the user-reported Crossbone scene and the
    # already validated STAGE18 Diana repair.
    p6002f1, _ = payload_at(result, 0x6002F1)
    if trim(final_dict.expand(p6002f1, tbl)) != "그것은　그대도　잘　알고　있을　터인데！":
        raise BuildError("validated 6002F1 Diana line regressed")
    p628299, _ = payload_at(result, 0x628299)
    if not p628299.startswith(b"\x18\xE5\x18"):
        raise BuildError("628299 control/text prefix changed")
    if trim(final_dict.expand(p628299[1:], tbl)) != "마이처　로나라는　인물을　중심으로":
        raise BuildError("628299 Maitza body did not standardize")
    p6282e2, _ = payload_at(result, 0x6282E2)
    portal = p6282e2.find(b"\xE5\x18")
    if portal < 0 or trim(final_dict.expand(p6282e2[portal:], tbl)) != "베라가　지도자가　된　뒤":
        raise BuildError("6282E2 runtime alias did not standardize")
    p6282ef, _ = payload_at(result, 0x6282EF)
    if trim(final_dict.expand(p6282ef, tbl)) != "크로스본　뱅가드는　변했어。":
        raise BuildError("6282EF continuation changed unexpectedly")
    p5c0efd, _ = payload_at(result, 0x5C0EFD)
    if trim(final_dict.expand(p5c0efd, tbl)) != "사라　자비아로프":
        raise BuildError("5C0EFD Sarah Zabiarov full-name spacing did not standardize")

    # The new glyph must be the only TBL/runtime font extension in this build.
    if tbl.char_to_code.get(NEW_GLYPH) != NEW_GLYPH_CODE:
        raise BuildError("candidate TBL does not map 햄 to EC85")
    final_store = build_store_cave(GLYPH_BASE - 0xDF20, NEW_STICKY_COUNT)
    if result[store_abs : store_abs + len(final_store)] != final_store:
        raise BuildError("candidate sticky glyph store is not 1350 slots")

    # Parent inputs and live SaveRAM are immutable.
    if PARENT.read_bytes() != parent or PARENT_TBL.read_bytes() != tbl_bytes or LIVE_SAVE.read_bytes() != save:
        raise BuildError("parent/TBL/live SaveRAM changed while building")

    atomic_bytes(OUT_ROM, result)
    atomic_text(OUT_TBL, tbl_text)
    atomic_bytes(OUT_SAVE, save)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage18_extended_terminology_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_runtime_validation_required",
        "promotion_performed": False,
        "inputs": {
            "parent_candidate": identity(PARENT, parent),
            "parent_tbl": identity(PARENT_TBL, tbl_bytes),
            "live_saveram_snapshot": identity(LIVE_SAVE, save),
            "spec": identity(SPEC),
        },
        "outputs": {
            "candidate_rom": identity(OUT_ROM, result),
            "candidate_tbl": identity(OUT_TBL),
            "candidate_saveram": identity(OUT_SAVE, save),
        },
        "new_glyph": {
            "char": NEW_GLYPH,
            "code": f"{NEW_GLYPH_CODE:04X}",
            "slot": slot,
            "file_offset": f"{glyph_offset:06X}",
            "font": font_path,
            "old_sticky_count": OLD_STICKY_COUNT,
            "new_sticky_count": NEW_STICKY_COUNT,
            "before_sha256": sha(glyph_before),
            "after_sha256": sha(glyph_record),
        },
        "changes": {
            "dictionary": dict_changes,
            "direct_record_bytes": direct_changes,
            "five_page_alias": alias_changes,
        },
        "checks": {
            "diana_6002F1_preserved": True,
            "maitza_628299_standardized_with_prefix_preserved": True,
            "bera_6282E2_runtime_alias_standardized": True,
            "crossbone_6282EF_unchanged": True,
            "sarah_5C0EFD_full_name_spacing_standardized": True,
            "dictionary_pointer_tables_unchanged": True,
            "five_page_alias_pointers_unchanged": True,
            "new_glyph_ec85_installed": True,
            "sticky_window_1350": True,
            "live_saveram_unchanged": True,
            "unexpected_diff_runs_zero": True,
        },
        "diff": {
            "changed_bytes": sum(end - start for start, end in diff_runs(parent, result)),
            "changed_runs": len(diff_runs(parent, result)),
            "unexpected_runs": 0,
        },
        "checksum": f"{checksum:04X}",
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "candidate": report["outputs"]["candidate_rom"],
        "tbl": report["outputs"]["candidate_tbl"],
        "save": report["outputs"]["candidate_saveram"],
        "new_glyph": report["new_glyph"],
        "counts": {"dictionary": len(dict_changes), "direct_record_bytes": len(direct_changes), "five_page_alias": len(alias_changes)},
        "diff": report["diff"],
        "checksum": report["checksum"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
