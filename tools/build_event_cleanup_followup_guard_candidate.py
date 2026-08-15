#!/usr/bin/env python3
"""Build the next cumulative event/battle runtime-regression guard candidate.

Parent (user-validated Gato fix + event-bank cleanup, before the later stale
55-row metadata experiment):
  out/patch/event_cleanup_gato_5d1e3e_candidate.wsc

The immediately preceding runtime-regression candidate restored 55 one-byte
leads from a stale structure inventory.  The independent false-lead recurrence
guard proves all 55 are already runtime/structure-proven visible text leads, so
this corrected pass deliberately starts before that experiment and does NOT
carry those 55 restores forward.

This pass resolves the two remaining *true* whole-E518 metadata regressions,
restores newly-fit short/fixed metadata, and lifts the runtime-proven Karama
orphan-kana rule to the exact structural family found in stock scenario/event
banks.

Battle metadata proof
---------------------
After the previous 55 one-byte metadata restores, four whole-record E5 18 rows
remained in the historical quarantine set:

  5D:870B  original begins 41='私'; exact original duplicate 5D:886F is a
             runtime-proven text-initial exception -> keep whole-record text.
  5D:B42B  original begins 8A='見'; exact original duplicate 5D:B650 is a
             runtime-proven text-initial exception -> keep whole-record text.
  5E:6586  original 90 + 'すみません、ライデン少佐！'; the exact body-only
             duplicate 5E:66D6 omits 90, and the surrounding block uses metadata
             90.  Decoding 90 as text produces nonsense '連すみません...' ->
             restore metadata 90 while preserving the current Korean E5 18 body.
  5E:65A7  original 90 + 'やられてたまるか！'; exact same structured form
             exists at 5E:A239 with metadata 90, while body-only 5E:C13F omits
             it. -> restore metadata 90 while preserving current Korean body.

Scenario/event orphan-kana proof
--------------------------------
The Japanese original contains exactly five instances in banks 59-63 of the
structural signature:

    00 00 | 06 00 | 17 28 ...
             な NUL   event/control

at 59:4715, 60:F3A6, 61:06EF, 61:165D, 63:8F52.  The user runtime-validated
61:06EF: replacing only 06 with 01 removes the visible stray 'な' and the next
event continues normally.  This pass applies that byte-exact same rule to the
remaining four family members.  61:055C is explicitly *not* part of this
family; it is embedded inside a control sequence and remains byte-exact.

No main TIP is modified by this builder.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
    ws_header,
)

PATCH = ROOT / "out/patch"
PARENT = PATCH / "event_cleanup_gato_5d1e3e_candidate.wsc"
PARENT_SAVE = ROOT / "sram/event_cleanup_gato_5d1e3e_candidate.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
INVENTORY = ROOT / "legacy/release_core_20260815/out/script/battle_dialogue_structure_inventory.csv"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
JP_TBL_PATH = ROOT / "data/monoeye_verified.tbl"
EXT_META_PATH = PATCH / "ext_dictionary_meta.json"
EXT3_META_PATH = PATCH / "ext3_dictionary_meta.json"

OUT_ROM = PATCH / "event_cleanup_followup_guard_candidate.wsc"
OUT_SAVE = ROOT / "sram/event_cleanup_followup_guard_candidate.sav"
OUT_REPORT = PATCH / "event_cleanup_followup_guard_report.json"

EXPECTED_PARENT_SHA = "ca4867914852328e0eb4e184a9f27bd831e5eae3f61b4a94c253d702a3a43dab"
ROM_SIZE = 16_777_216
ORIGINAL_SIZE = 8_388_608
SAVE_SIZE = 32_768

METADATA_TARGETS = {
    0x5E6586: {
        "metadata": 0x90,
        "expected_parent": "E518D4E10101010101",
        "expected_ko": "죄송합니다、라이덴　소령！",
        "body_only_duplicate": 0x5E66D6,
    },
    0x5E65A7: {
        "metadata": 0x90,
        "expected_parent": "E518D4E20101010101",
        "expected_ko": "당할　수　있겠나！",
        "structured_duplicate": 0x5EA239,
        "body_only_duplicate": 0x5EC13F,
    },
}

TEXT_INITIAL_PROTECT = {
    0x5D870B: {"duplicate": 0x5D886F, "lead": 0x41, "jp_prefix": "私"},
    0x5DB42B: {"duplicate": 0x5DB650, "lead": 0x8A, "jp_prefix": "見"},
}

ORPHAN_FAMILY = (0x594715, 0x60F3A6, 0x6106EF, 0x61165D, 0x638F52)
RUNTIME_VALIDATED_ORPHAN = 0x6106EF
NEW_ORPHAN_TARGETS = ORPHAN_FAMILY
CONTROL_PROTECT_LO = 0x610552
CONTROL_PROTECT_HI = 0x610567
FALSE_LEAD_RECURRENCE_TARGETS = (0x5D3122, 0x5D313B)
FALSE_LEAD_EXPECTED = bytes.fromhex("5DF50D") + b"\x01" * 8
FALSE_LEAD_KO = "모니터、어디냐！？"


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def checksum_valid(data: bytes) -> bool:
    return int(ws_header(data)["checksum"]) == (sum(data[:-2]) & 0xFFFF)


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def diff_positions(a: bytes, b: bytes) -> list[int]:
    if len(a) != len(b):
        raise BuildError("ROM size changed")
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


def zpayload(data: bytes, sb: int, logical: int, max_len: int = 128) -> tuple[bytes, int]:
    got = read_encoded_z_safe(data, sb + logical, max_len=max_len)
    if got is None:
        raise BuildError(f"unreadable record at {logical:06X}")
    return bytes(got[0]), int(got[1])


def scan_orphan_family(original: bytes, sb: int) -> list[int]:
    """Find the exact 00 00 | 06 00 | 17 28 structural family in banks 59-63."""
    hits: list[int] = []
    for bank in range(0x59, 0x64):
        lo = sb + (bank << 16)
        data = original[lo : lo + 0x10000]
        for i in range(2, len(data) - 4):
            if data[i - 2 : i] == b"\x00\x00" and data[i : i + 4] == b"\x06\x00\x17\x28":
                hits.append((bank << 16) | i)
    return hits


def main() -> int:
    parent = PARENT.read_bytes()
    original = ORIGINAL.read_bytes()
    save = PARENT_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent drifted: size={len(parent)} sha={sha(parent)}")
    if len(original) != ORIGINAL_SIZE:
        raise BuildError("original ROM size drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError("parent SaveRAM missing/wrong size")

    sb = stock_base(parent)
    so = stock_base(original)
    tbl = Tbl.load(TBL_PATH)
    jp_tbl = Tbl.load(JP_TBL_PATH)
    ext_meta = load_ext_meta(EXT_META_PATH)
    ext3_meta = load_ext_meta(EXT3_META_PATH)
    d_parent = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    d_original = Dictionary(original)

    with INVENTORY.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_abs = {int(row["record_start"], 16): row for row in rows}

    # Resolve the two false-positive quarantine rows first.  Their original
    # leading code units are visible Japanese text and exact body-only duplicate
    # records are already runtime-proven text-initial exceptions.
    protected_text_initial: list[dict[str, Any]] = []
    for logical, spec in TEXT_INITIAL_PROTECT.items():
        duplicate = int(spec["duplicate"])
        raw, term = zpayload(parent, sb, logical)
        dup_raw, _ = zpayload(parent, sb, duplicate)
        orig_raw, _ = zpayload(original, so, logical)
        orig_dup, _ = zpayload(original, so, duplicate)
        row_dup = by_abs[duplicate]
        if not raw.startswith(b"\xE5\x18"):
            raise BuildError(f"protected text-initial parent shape drifted at {logical:06X}")
        if orig_raw != orig_dup or orig_raw[0] != int(spec["lead"]):
            raise BuildError(f"original duplicate proof failed at {logical:06X}")
        jp_text = d_original.expand(orig_raw, jp_tbl)
        if not jp_text.startswith(str(spec["jp_prefix"])):
            raise BuildError(f"visible Japanese lead proof failed at {logical:06X}: {jp_text!r}")
        if row_dup.get("classification") != "text_initial_exception" or "runtime-proven" not in row_dup.get("reason", ""):
            raise BuildError(f"runtime-proven duplicate classification drifted at {duplicate:06X}")
        if strip_pad(d_parent.expand(raw, tbl)) != strip_pad(d_parent.expand(dup_raw, tbl)):
            raise BuildError(f"Korean duplicate rendering drifted at {logical:06X}")
        protected_text_initial.append({
            "abs": f"{logical:06X}",
            "duplicate": f"{duplicate:06X}",
            "original_jp": jp_text,
            "lead_hex": f"{int(spec['lead']):02X}",
            "classification": "intentional_text_initial_whole_E518_keep",
        })

    candidate = bytearray(parent)
    restored_metadata: list[dict[str, Any]] = []

    # Independent false-lead recurrence audit found two pre-existing regressions
    # in the safe Gato parent. 5D is printable モ and the original phrase is
    # モニタ－どれなんだ！？; current F50D renders Korean 니터、어디냐！？.
    # Replace only the Japanese モ with direct Korean 모 and preserve F50D.
    false_lead_fixed: list[dict[str, Any]] = []
    direct_mo = tbl.encode_char("모")
    if len(direct_mo) != 2:
        raise BuildError(f"direct Korean 모 encoding drifted: {direct_mo.hex().upper()}")
    for logical in FALSE_LEAD_RECURRENCE_TARGETS:
        before, term = zpayload(parent, sb, logical, max_len=32)
        if before != FALSE_LEAD_EXPECTED:
            raise BuildError(f"false-lead recurrence target drifted at {logical:06X}: {before.hex().upper()}")
        if strip_pad(d_parent.expand(before[1:3], tbl)) != "니터、어디냐！？":
            raise BuildError(f"false-lead tail token drifted at {logical:06X}")
        after = direct_mo + before[1:3] + b"\x01" * (len(before) - 4)
        if strip_pad(d_parent.expand(after[:4], tbl)) != FALSE_LEAD_KO:
            raise BuildError(f"false-lead corrected render failed at {logical:06X}")
        boundary = parent[term : term + 8]
        candidate[sb + logical : sb + logical + len(after)] = after
        if candidate[term : term + 8] != boundary:
            raise BuildError(f"false-lead boundary changed at {logical:06X}")
        false_lead_fixed.append({
            "abs": f"{logical:06X}",
            "before": before.hex().upper(),
            "after": after.hex().upper(),
            "render": FALSE_LEAD_KO,
        })

    # Restore only the two now-proven metadata bytes, keeping current translation
    # portals byte-exact and consuming one trailing 01 padding byte.
    for logical, spec in METADATA_TARGETS.items():
        before, term = zpayload(parent, sb, logical)
        expected = bytes.fromhex(str(spec["expected_parent"]))
        if before != expected or len(before) < 5 or before[4:] != b"\x01" * (len(before) - 4):
            raise BuildError(f"metadata target drifted at {logical:06X}: {before.hex().upper()}")
        meta = int(spec["metadata"])
        orig_raw, _ = zpayload(original, so, logical)
        if not orig_raw or orig_raw[0] != meta:
            raise BuildError(f"original metadata proof drifted at {logical:06X}")
        body_before = before[:4]
        ko_before = strip_pad(d_parent.expand(body_before, tbl))
        if ko_before != str(spec["expected_ko"]):
            raise BuildError(f"current Korean body drifted at {logical:06X}: {ko_before!r}")

        body_dup, _ = zpayload(original, so, int(spec["body_only_duplicate"]))
        if orig_raw[1:] != body_dup:
            raise BuildError(f"body-only duplicate proof failed at {logical:06X}")
        structured_dup = spec.get("structured_duplicate")
        if structured_dup is not None:
            structured_raw, _ = zpayload(original, so, int(structured_dup))
            if structured_raw != orig_raw:
                raise BuildError(f"structured duplicate proof failed at {logical:06X}")

        after = bytes([meta]) + body_before + b"\x01" * (len(before) - 5)
        if len(after) != len(before):
            raise BuildError(f"metadata record length drift at {logical:06X}")
        boundary = parent[term : term + 12]
        start = sb + logical
        candidate[start : start + len(after)] = after
        if candidate[term : term + 12] != boundary:
            raise BuildError(f"terminator/next boundary changed at {logical:06X}")
        restored_metadata.append({
            "abs": f"{logical:06X}",
            "metadata": f"{meta:02X}",
            "before": before.hex().upper(),
            "after": after.hex().upper(),
            "ko_body": ko_before,
            "body_only_duplicate": f"{int(spec['body_only_duplicate']):06X}",
            "structured_duplicate": f"{int(structured_dup):06X}" if structured_dup is not None else None,
        })

    # The old structure-repair pass quarantined short/fixed records because a
    # 4-byte E5 18 portal could not coexist with one metadata byte in their
    # 3-byte payload. Later translation passes rehomed exactly 3,499 of these to
    # native 2-byte Korean tokens + one 01 pad byte, so that capacity blocker no
    # longer exists. Restore the authoritative one-byte metadata and consume
    # only the pad byte.
    short_metadata_restored: list[dict[str, Any]] = []
    short_render_counts: dict[str, int] = {}
    for row in rows:
        if (
            row.get("classification") != "battle_voice_structured"
            or row.get("safe_structure_exact") != "yes"
            or row.get("action") != "quarantine"
            or row.get("reason") != "short/fixed body capacity < 4"
            or len(row.get("authoritative_structure_hex", "")) != 2
        ):
            continue
        logical = int(row["record_start"], 16)
        meta = bytes.fromhex(row["authoritative_structure_hex"])
        before, term = zpayload(parent, sb, logical, max_len=32)
        if before[:1] == meta:
            continue
        if int(row["body_capacity"]) != 2 or len(before) != 3 or before[2] != 0x01:
            raise BuildError(
                f"short metadata target no longer has native2+pad shape at {logical:06X}: {before.hex().upper()}"
            )
        token = before[:2]
        rendered = strip_pad(d_parent.expand(token, tbl))
        expected_render = strip_pad(row.get("current_render", ""))
        if rendered != expected_render:
            raise BuildError(
                f"short native token render drift at {logical:06X}: {rendered!r} != {expected_render!r}"
            )
        original_payload, _ = zpayload(original, so, logical, max_len=32)
        original_body = bytes.fromhex(row["body_hex_original"])
        if len(original_body) != 2 or original_payload != meta + original_body:
            raise BuildError(f"short original structure drift at {logical:06X}")
        after = meta + token
        boundary = parent[term : term + 8]
        candidate[sb + logical : sb + logical + 3] = after
        if candidate[term : term + 8] != boundary:
            raise BuildError(f"short target boundary changed at {logical:06X}")
        short_render_counts[expected_render] = short_render_counts.get(expected_render, 0) + 1
        short_metadata_restored.append({
            "abs": f"{logical:06X}",
            "metadata": meta.hex().upper(),
            "before": before.hex().upper(),
            "after": after.hex().upper(),
            "render": expected_render,
        })

    if len(short_metadata_restored) != 3499:
        raise BuildError(f"expected 3499 newly-fit short metadata rows, got {len(short_metadata_restored)}")
    expected_short_render_counts = {
        "미사용": 3430,
        "크리스": 33,
        "버니": 33,
        "이런　곳에서": 1,
        "티파": 1,
        "레코아": 1,
    }
    if short_render_counts != expected_short_render_counts:
        raise BuildError(f"short metadata render partition drifted: {short_render_counts}")

    # The runtime-proven Karama site lifts the exact structural family, not a
    # generic "single kana" heuristic.  The original must contain exactly these
    # five sites before any candidate bytes are changed.
    family = scan_orphan_family(original, so)
    if family != list(ORPHAN_FAMILY):
        raise BuildError("orphan structural family drifted: " + ",".join(f"{x:06X}" for x in family))
    if parent[sb + RUNTIME_VALIDATED_ORPHAN : sb + RUNTIME_VALIDATED_ORPHAN + 2] != b"\x06\x00":
        raise BuildError("safe parent Karama orphan bytes drifted")

    orphan_applied: list[dict[str, str]] = []
    for logical in NEW_ORPHAN_TARGETS:
        start = sb + logical
        # The Karama title body was already rewritten before this safe parent,
        # so its two preceding bytes are title padding.  The orphan record and
        # following event-control bytes are still the same 06 00 | 17 28.
        if parent[start : start + 4] != b"\x06\x00\x17\x28":
            raise BuildError(f"orphan family parent record/control drifted at {logical:06X}")
        if logical != RUNTIME_VALIDATED_ORPHAN and parent[start - 2 : start] != b"\x00\x00":
            raise BuildError(f"orphan family parent prefix drifted at {logical:06X}")
        candidate[start] = 0x01
        orphan_applied.append({"abs": f"{logical:06X}", "before": "06 00", "after": "01 00"})

    # 61:055C was once mislisted as a one-character row but is inside event
    # control bytes.  Protect the entire local control window byte-exactly.
    control_before = parent[sb + CONTROL_PROTECT_LO : sb + CONTROL_PROTECT_HI]
    control_expected = bytes.fromhex("085A00171C1728010600080000080100171D082D00")
    if control_before != control_expected:
        raise BuildError(f"61:055C control window drifted: {control_before.hex().upper()}")

    checksum = update_ws_checksum(candidate)
    out = bytes(candidate)
    if out[sb + CONTROL_PROTECT_LO : sb + CONTROL_PROTECT_HI] != control_before:
        raise BuildError("61:055C protected control window changed")

    # Candidate metadata bodies remain identical in meaning and physical token.
    d_out = make_dictionary_ext3(out, ext_meta, ext3_meta)
    for item in restored_metadata:
        logical = int(item["abs"], 16)
        payload, term = zpayload(out, sb, logical)
        if payload[0] != int(item["metadata"], 16):
            raise BuildError(f"metadata postcondition failed at {logical:06X}")
        if payload[1:5] != bytes.fromhex(item["before"])[:4]:
            raise BuildError(f"body portal changed at {logical:06X}")
        if strip_pad(d_out.expand(payload[1:5], tbl)) != item["ko_body"]:
            raise BuildError(f"body render changed at {logical:06X}")
        if out[term] != 0:
            raise BuildError(f"terminator lost at {logical:06X}")

    for item in false_lead_fixed:
        logical = int(item["abs"], 16)
        payload, term = zpayload(out, sb, logical, max_len=32)
        if payload != bytes.fromhex(item["after"]):
            raise BuildError(f"false-lead postcondition failed at {logical:06X}")
        if strip_pad(d_out.expand(payload[:4], tbl)) != item["render"]:
            raise BuildError(f"false-lead render changed at {logical:06X}")
        if out[term] != 0:
            raise BuildError(f"false-lead terminator lost at {logical:06X}")

    for item in short_metadata_restored:
        logical = int(item["abs"], 16)
        payload, term = zpayload(out, sb, logical, max_len=32)
        if payload != bytes.fromhex(item["after"]):
            raise BuildError(f"short metadata postcondition failed at {logical:06X}")
        if strip_pad(d_out.expand(payload[1:3], tbl)) != item["render"]:
            raise BuildError(f"short body render changed at {logical:06X}")
        if out[term] != 0:
            raise BuildError(f"short terminator lost at {logical:06X}")

    # The 15 short/fixed rows whose historical 'authoritative structure' is two
    # bytes are not portrait metadata: runtime analysis established real control
    # metadata as one byte, and each two-byte value is printable source text.
    short_two_byte_protected = []
    for row in rows:
        if (
            row.get("classification") == "battle_voice_structured"
            and row.get("safe_structure_exact") == "yes"
            and row.get("action") == "quarantine"
            and row.get("reason") == "short/fixed body capacity < 4"
            and len(row.get("authoritative_structure_hex", "")) == 4
        ):
            logical = int(row["record_start"], 16)
            before, _ = zpayload(parent, sb, logical, max_len=32)
            auth = bytes.fromhex(row["authoritative_structure_hex"])
            if before[:2] != auth:
                short_two_byte_protected.append(f"{logical:06X}")
    if len(short_two_byte_protected) != 15:
        raise BuildError(f"short two-byte text-start partition drifted: {short_two_byte_protected}")

    # After this pass the only four historically quarantined whole-record E518
    # rows become two protected text-initial exceptions.  No unresolved true
    # metadata candidate remains in that set.
    unresolved_whole: list[str] = []
    protected_whole: list[str] = []
    for logical in (*TEXT_INITIAL_PROTECT.keys(), *METADATA_TARGETS.keys()):
        payload, _ = zpayload(out, sb, logical)
        if payload.startswith(b"\xE5\x18"):
            if logical in TEXT_INITIAL_PROTECT:
                protected_whole.append(f"{logical:06X}")
            else:
                unresolved_whole.append(f"{logical:06X}")
    if unresolved_whole or protected_whole != ["5D870B", "5DB42B"]:
        raise BuildError(f"whole-E518 quarantine resolution failed: unresolved={unresolved_whole} protected={protected_whole}")

    # Every member of the exact singleton family must now be blank while its NUL
    # and following 17 28 control bytes remain untouched.
    for logical in ORPHAN_FAMILY:
        start = sb + logical
        if out[start : start + 4] != b"\x01\x00\x17\x28":
            raise BuildError(f"orphan family postcondition failed at {logical:06X}")

    if not checksum_valid(out):
        raise BuildError("WonderSwan checksum invalid")

    diffs = diff_positions(parent, out)
    non_checksum = [x for x in diffs if x < len(out) - 2]
    allowed: set[int] = {sb + x for x in NEW_ORPHAN_TARGETS}
    for item in false_lead_fixed:
        logical = int(item["abs"], 16)
        before = bytes.fromhex(item["before"])
        after = bytes.fromhex(item["after"])
        allowed.update(sb + logical + i for i, (a, b) in enumerate(zip(before, after)) if a != b)
    for item in restored_metadata:
        logical = int(item["abs"], 16)
        before = bytes.fromhex(item["before"])
        after = bytes.fromhex(item["after"])
        allowed.update(sb + logical + i for i, (a, b) in enumerate(zip(before, after)) if a != b)
    for item in short_metadata_restored:
        logical = int(item["abs"], 16)
        before = bytes.fromhex(item["before"])
        after = bytes.fromhex(item["after"])
        allowed.update(sb + logical + i for i, (a, b) in enumerate(zip(before, after)) if a != b)
    if set(non_checksum) != allowed:
        unexpected = sorted(set(non_checksum) ^ allowed)
        raise BuildError("unexpected candidate delta: " + ",".join(f"{x:08X}" for x in unexpected[:20]))

    atomic_bytes(OUT_ROM, out)
    atomic_bytes(OUT_SAVE, save)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_event_cleanup_followup_guard_candidate.py",
        "ok": True,
        "status": "static_verified_candidate_pending_runtime_spotcheck",
        "parent": {"path": rel(PARENT), "sha256": sha(parent).upper()},
        "output": {"path": rel(OUT_ROM), "size": len(out), "sha256": sha(out).upper(), "checksum": f"{checksum:04X}"},
        "save": {"path": rel(OUT_SAVE), "size": len(save), "sha256": sha(save).upper()},
        "false_lead_recurrence": {
            "fixed": false_lead_fixed,
            "note": "safe Gato parent contained two visible Japanese モ regressions; they are text, not metadata",
        },
        "battle_metadata": {
            "restored": restored_metadata,
            "short_fixed_metadata_restored_count": len(short_metadata_restored),
            "short_fixed_render_partition": short_render_counts,
            "short_fixed_restored": short_metadata_restored,
            "short_two_byte_text_starts_protected": short_two_byte_protected,
            "protected_text_initial": protected_text_initial,
            "conclusion": "2 whole-E518 plus 3499 newly-fit short/fixed true metadata rows restored; text-start exceptions protected",
        },
        "orphan_kana_family": {
            "original_signature": "00 00 | 06 00 | 17 28",
            "original_exact_family": [f"{x:06X}" for x in ORPHAN_FAMILY],
            "runtime_proven_anchor": "6106EF",
            "newly_blanked": orphan_applied,
            "all_candidate_signature": "01 00 | 17 28",
        },
        "protected_control": {
            "range": "610552-610566",
            "contains": "61055C",
            "reason": "embedded event-control byte, not standalone kana-family member",
            "byte_exact": True,
        },
        "checks": {
            "parent_identity_exact": True,
            "two_preexisting_false_lead_regressions_fixed": True,
            "original_orphan_family_exactly_five": True,
            "two_metadata_body_tokens_preserved": True,
            "3499_short_fixed_native2_metadata_restored": True,
            "15_short_fixed_two_byte_text_starts_protected": True,
            "two_intentional_text_initial_cases_protected": True,
            "remaining_unresolved_whole_E518_true_metadata": 0,
            "five_orphan_family_members_blank_and_control_preserved": True,
            "61055C_control_window_unchanged": True,
            "unexpected_nonchecksum_delta": 0,
            "checksum_valid": True,
        },
        "nonchecksum_diff_bytes_vs_parent": len(non_checksum),
        "promotion": "blocked_pending_user_runtime_verification",
        "test_protocol": [
            "Recheck a battle path using the 5E:6586/65A7 voice block if reachable: portrait/sprite must be normal and Korean body unchanged.",
            "Spot-check scenes around 第3戦闘ライン、レビル艦隊, グリーン・ノア, and the 63:8F52 event family: no stray な should appear and event flow must continue.",
            "If those paths are difficult to reach, retain this candidate until normal play reaches them; static proof is fail-closed and no unrelated bytes are changed.",
        ],
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps({
        "ok": True,
        "rom": report["output"],
        "save": report["save"],
        "false_lead_fixed": [x["abs"] for x in false_lead_fixed],
        "metadata_restored": [x["abs"] for x in restored_metadata],
        "short_fixed_metadata_restored": len(short_metadata_restored),
        "short_fixed_render_partition": short_render_counts,
        "text_initial_protected": [x["abs"] for x in protected_text_initial],
        "orphan_newly_blanked": [x["abs"] for x in orphan_applied],
        "nonchecksum_diff_bytes": len(non_checksum),
        "report": rel(OUT_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
