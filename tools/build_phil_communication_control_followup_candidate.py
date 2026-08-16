#!/usr/bin/env python3
"""Build Phil communication control-state + Harry terminology follow-up candidate.

Parent is the user-tested stage18 extended terminology candidate. This builder
makes two surgical scenario-continuation repairs in the Phil communication
scene: it drops the leading raw 0x18 that currently leaks as visible ``こ`` at
6017FC and 601826, slides the existing E5 18 ext3 portal forward by one byte,
and fills the vacated tail byte with 0x01. Record extents, NUL terminators,
following controls, and the middle 601813 continuation remain byte-exact.

The same candidate also standardizes the remaining Harry Ord spellings from
하리 to 해리 in ordinary/ext3 and five-page alias dictionary phrases. The
parent candidate, its TBL, and the live SaveRAM are inputs only.
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

from apply_ext_dict_unit import load_ext_meta  # noqa: E402
from build_dialogue_20cell_candidate import encode  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from build_stage17t_global_20cell_followup_candidate import active_dictionary  # noqa: E402
from monoeye_rom import BANK_SIZE, Tbl, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT = PATCH / "stage18_extended_terminology_followup_candidate.wsc"
PARENT_TBL = PATCH / "stage18_extended_terminology_followup_candidate.tbl"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC = ROOT / "data/phil_communication_control_followup_ko.json"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

OUT = PATCH / "phil_communication_control_followup_candidate.wsc"
OUT_TBL = PATCH / "phil_communication_control_followup_candidate.tbl"
OUT_SAVE = ROOT / "sram/phil_communication_control_followup_candidate.sav"
REPORT = PATCH / "phil_communication_control_followup_candidate_report.json"

EXPECTED_PARENT = "a5ba7d566cfdfc20ae55177c2a3849aa2dc08b080cc6c87f745ae8d254a83f4a"
EXPECTED_TBL = "9d31d1fd1a5022b54f83866d285f4d656461b30d4046ef2b6261a9441abca914"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def trim(text: str) -> str:
    return text.rstrip("\u3000 \t")


def identity(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
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


def record_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=512)
    if got is None:
        raise BuildError(f"unreadable record: {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def main() -> int:
    parent = PARENT.read_bytes()
    tbl_bytes = PARENT_TBL.read_bytes()
    save = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        raise BuildError(f"parent candidate identity drift: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL:
        raise BuildError(f"parent TBL identity drift: {sha(tbl_bytes)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drift: {len(save)}")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("parent_sha256") != EXPECTED_PARENT or spec.get("parent_tbl_sha256") != EXPECTED_TBL:
        raise BuildError("spec parent identity drift")

    tbl = Tbl.load(PARENT_TBL)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dict = active_dictionary(parent, ext_meta, ext3_meta)
    sb = stock_base(parent)
    cand = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    control_rows: list[dict[str, Any]] = []

    # 1) Phil communication continuation repairs: drop only the visible lead 18,
    # keep the existing E5 18 portal and record extent, and add one trailing pad.
    for row in spec.get("control_rewrites") or []:
        logical = int(row["abs"], 16)
        before, term = record_at(parent, logical)
        expected_before = bytes.fromhex(row["before_payload_hex"])
        after = bytes.fromhex(row["after_payload_hex"])
        expected_term = int(row["terminator"], 16)
        if before != expected_before or term != expected_term:
            raise BuildError(f"control target drift {logical:06X}")
        if len(after) != len(before):
            raise BuildError(f"control rewrite extent changed {logical:06X}")
        if before[:5] != b"\x18\xE5\x18" + before[3:5]:
            raise BuildError(f"control rewrite is not lead18+ext3 {logical:06X}")
        if after[:4] != before[1:5] or after[-1:] != b"\x01":
            raise BuildError(f"control rewrite is not a one-byte lead shift {logical:06X}")
        before_render = trim(parent_dict.expand(before, tbl))
        after_render = trim(parent_dict.expand(after, tbl))
        if before_render != row["before_render"]:
            raise BuildError(f"current Phil render drift {logical:06X}: {before_render!r}")
        if after_render != row["after_render"]:
            raise BuildError(f"shifted Phil render mismatch {logical:06X}: {after_render!r}")
        cand[sb + logical : sb + logical + len(after)] = after
        allowed.append((sb + logical, sb + logical + len(after)))
        control_rows.append({
            "abs": f"{logical:06X}",
            "before": before.hex().upper(),
            "after": after.hex().upper(),
            "terminator": f"{term:06X}",
            "before_render": before_render,
            "after_render": after_render,
        })

    # 2) Remaining ordinary/ext3 Harry phrases. All are same-or-shorter and
    # therefore can be rewritten in place without touching dictionary pointers.
    dict_rows: list[dict[str, Any]] = []
    for row in spec.get("dictionary_rewrites") or []:
        index = int(row["index"], 16)
        before_text = trim(parent_dict.expand_index(index, tbl))
        if before_text != row["before"]:
            raise BuildError(f"dictionary source drift {index:05X}: {before_text!r}")
        raw_before = parent_dict.raw_entry(index)
        raw_after = encode(str(row["after"]), tbl)
        if len(raw_after) > len(raw_before):
            raise BuildError(f"dictionary rewrite grows {index:05X}: {len(raw_before)}->{len(raw_after)}")
        abs_off = parent_dict.entry_abs(index)
        span = len(raw_before) + 1
        cand[abs_off : abs_off + span] = raw_after + b"\x00" * (span - len(raw_after))
        allowed.append((abs_off, abs_off + span))
        dict_rows.append({
            "index": f"{index:05X}", "entry_abs": f"{abs_off:07X}",
            "before": row["before"], "after": row["after"],
            "old_bytes": len(raw_before), "new_bytes": len(raw_after),
        })

    # 3) Runtime five-page alias phrases. Same-or-shorter in-place only; alias
    # pointer tables stay byte-exact.
    alias_rows: list[dict[str, Any]] = []
    for row in spec.get("alias_dictionary_rewrites") or []:
        seg = int(row["segment"], 16)
        local = int(row["local"], 16)
        expected_ptr = int(row["expected_pointer"], 16)
        bank_start = seg * BANK_SIZE
        bank = bytearray(cand[bank_start : bank_start + BANK_SIZE])
        ptr = struct.unpack_from("<H", bank, local * 2)[0]
        if ptr != expected_ptr:
            raise BuildError(f"alias pointer drift {seg:02X}:{local:04X}: {ptr:04X}")
        end = bank.find(b"\x00", ptr)
        if end < 0:
            raise BuildError(f"unterminated alias {seg:02X}:{local:04X}")
        raw_before = bytes(bank[ptr:end])
        before_text = trim(parent_dict.expand(raw_before, tbl))
        if before_text != row["before"]:
            raise BuildError(f"alias source drift {seg:02X}:{local:04X}: {before_text!r}")
        raw_after = encode(str(row["after"]), tbl)
        if len(raw_after) > len(raw_before):
            raise BuildError(f"alias rewrite grows {seg:02X}:{local:04X}")
        span = len(raw_before) + 1
        bank[ptr : ptr + span] = raw_after + b"\x00" * (span - len(raw_after))
        cand[bank_start : bank_start + BANK_SIZE] = bank
        allowed.append((bank_start + ptr, bank_start + ptr + span))
        alias_rows.append({
            "segment": f"{seg:02X}", "local": f"{local:04X}", "pointer": f"{ptr:04X}",
            "before": row["before"], "after": row["after"],
            "old_bytes": len(raw_before), "new_bytes": len(raw_after),
        })

    checksum = update_ws_checksum(cand)
    allowed.append((len(cand) - 2, len(cand)))
    result = bytes(cand)
    if (sum(result[:-2]) & 0xFFFF) != int.from_bytes(result[-2:], "little"):
        raise BuildError("WonderSwan checksum invalid")
    unexpected = [run for run in diff_runs(parent, result) if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:12]}")

    final_dict = active_dictionary(result, ext_meta, ext3_meta)

    # Verify repaired Phil lines and their terminators.
    for row in spec.get("control_rewrites") or []:
        logical = int(row["abs"], 16)
        raw, term = record_at(result, logical)
        if raw != bytes.fromhex(row["after_payload_hex"]) or term != int(row["terminator"], 16):
            raise BuildError(f"final control record drift {logical:06X}")
        if raw[:1] == b"\x18" or trim(final_dict.expand(raw, tbl)) != row["after_render"]:
            raise BuildError(f"final Phil lead/render failed {logical:06X}")

    # Middle continuation and later first records are deliberate byte-exact
    # regression anchors. They must not be rewritten while fixing the lead 18s.
    for row in spec.get("preserve_records") or []:
        logical = int(row["abs"], 16)
        raw, term = record_at(result, logical)
        if raw != bytes.fromhex(row["payload_hex"]) or term != int(row["terminator"], 16):
            raise BuildError(f"preserved Phil record changed {logical:06X}")
        # scenario_first prefixes vary (17/34/18, 17/2A/18, ...).  For
        # translated rows the first E5 18 portal is the authoritative body
        # start; native rows such as the ellipsis pause keep their 3-byte
        # 17/xx/18 prefix byte-exact and decode after that prefix.
        portal = raw.find(b"\xE5\x18")
        if portal >= 0:
            render_raw = raw[portal:]
        elif len(raw) >= 3 and raw[0] == 0x17 and raw[2] == 0x18:
            render_raw = raw[3:]
        else:
            render_raw = raw
        rendered = trim(final_dict.expand(render_raw, tbl))
        if rendered != row["render"]:
            raise BuildError(f"preserved Phil render drift {logical:06X}: {rendered!r}")

    for row in spec.get("preserve_bytes") or []:
        lo = int(row["start"], 16)
        hi = int(row["end_exclusive"], 16)
        block = result[sb + lo : sb + hi]
        if sha(block) != row["sha256"]:
            raise BuildError(f"preserved Phil control block changed {lo:06X}-{hi:06X}")

    for row in spec.get("dictionary_rewrites") or []:
        rendered = trim(final_dict.expand_index(int(row["index"], 16), tbl))
        if rendered != row["after"]:
            raise BuildError(f"final Harry dictionary render mismatch {row['index']}: {rendered!r}")
    for row in spec.get("alias_dictionary_rewrites") or []:
        seg = int(row["segment"], 16)
        local = int(row["local"], 16)
        bank_start = seg * BANK_SIZE
        ptr = int.from_bytes(result[bank_start + local * 2 : bank_start + local * 2 + 2], "little")
        end = result.find(b"\x00", bank_start + ptr)
        if end < 0:
            raise BuildError(f"final alias unterminated {seg:02X}:{local:04X}")
        rendered = trim(final_dict.expand(result[bank_start + ptr : end], tbl))
        if rendered != row["after"]:
            raise BuildError(f"final Harry alias render mismatch {seg:02X}:{local:04X}: {rendered!r}")

    # Explicit user-visible Harry runtime anchor.
    raw_628230, _ = record_at(result, 0x628230)
    body_628230 = raw_628230[3:] if raw_628230.startswith(b"\x17\x34\x18") else raw_628230
    if trim(final_dict.expand(body_628230, tbl)) != "토레스、　해리　중위　기체를　회수해！":
        raise BuildError("628230 Harry lieutenant runtime anchor failed")

    # Inputs must remain immutable.
    if PARENT.read_bytes() != parent or PARENT_TBL.read_bytes() != tbl_bytes or LIVE_SAVE.read_bytes() != save:
        raise BuildError("parent candidate/TBL/live SaveRAM changed during build")

    atomic_bytes(OUT, result)
    atomic_text(OUT_TBL, PARENT_TBL.read_text(encoding="utf-8"))
    OUT_SAVE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_phil_communication_control_followup_candidate.py",
        "status": "pending_user_runtime_validation",
        "ok": True,
        "parent": {"path": rel(PARENT), "sha256": sha(parent)},
        "candidate": identity(OUT),
        "tbl": identity(OUT_TBL),
        "saveram": identity(OUT_SAVE),
        "checksum": f"{checksum:04X}",
        "control_rewrites": control_rows,
        "harry_dictionary_rewrites": dict_rows,
        "harry_alias_rewrites": alias_rows,
        "counts": {
            "control_rewrites": len(control_rows),
            "dictionary_rewrites": len(dict_rows),
            "five_page_alias_rewrites": len(alias_rows),
            "changed_bytes": sum(1 for a, b in zip(parent, result) if a != b),
            "changed_runs": len(diff_runs(parent, result)),
            "unexpected_diff_runs": 0,
        },
        "runtime_gate": [
            "Phil communication: 디아나 님！ is followed by 저희들은 지구만을 생각하고、 달의 with no leading こ",
            "next continuation renders 등한시하는 폐하의 뜻에는…… with no glyph corruption",
            "따라갈 수 없다고 말씀드렸습니다！！ renders with no leading こ",
            "the following pause/response has no 亻 or other control glyph and the event continues normally",
            "Harry lieutenant references render 해리, not 하리",
        ],
        "saveram_policy": "paired test SaveRAM copied from current live sram/monoeye_ko_expanded.sav at build time",
    }
    atomic_json(REPORT, report)
    print(json.dumps({
        "ok": True,
        "rom": identity(OUT),
        "tbl": identity(OUT_TBL),
        "save": identity(OUT_SAVE),
        "checksum": f"{checksum:04X}",
        "counts": report["counts"],
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
