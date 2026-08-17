#!/usr/bin/env python3
"""Build one whole-game candidate that rehomes all 220 exact4 event-risk records.

Strategy
--------
* 155 records: replace top-level E5 18 with an exact two-token native body that
  renders the current Korean text.
* 65 records that cannot be represented by two existing native tokens: replace
  the four-byte body with ``E5 1D <helper_id> 01``.  E51D is the already
  runtime-proven STAGE22t event-safe native-loop portal.  The parameterized form
  consumes the two trailing bytes and indexes a bank26 helper table.
* Each parameter helper contains the record's existing four-byte E5 18 token,
  followed by NUL.  Thus the event-facing record no longer ends in direct E5 18;
  the existing Korean ext3 phrase is invoked one level inside the native-loop
  helper, where it terminates before control bytes are resumed.
* The promoted two-byte STAGE22t form ``... E51D 00`` remains helper index 0 and
  continues to map to bank26:2000 (F36A F16E = `어？`).

This is a runtime-test candidate.  It does not modify the main TIP or live SAV.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    is_compact3_magic,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    ws_header,
)

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
WORKLIST = PATCH / "global_event_runtime_risk_priority_worklist.json"
CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"

OUT_ROM = PATCH / "global_event_native_rehome_220_candidate.wsc"
OUT_SAVE = ROOT / "sram/global_event_native_rehome_220_candidate.sav"
OUT_REPORT = PATCH / "global_event_native_rehome_220_report.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MAIN_SHA = "fbd7ad5f36d1248aab27b9a3a1e90b4ef2ec0676567b6bb42b76979e3c9b3260"
ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"

# Promoted STAGE22t event-safe portal state.
EVENT_MAGIC = bytes.fromhex("E51D")
WRAM_INDEX = 0x19F8
WRAM_FLAG = 0x19FA
SPECIAL_FLAG = 2
EXP_SEG = 0x26
FIXED_HELPER_OFF = 0x2000
FIXED_HELPER = bytes.fromhex("F36AF16E00")
PARAM_PTR_TABLE = 0x2100
PARAM_DATA_START = 0x2200
PARAM_DATA_LIMIT = 0x2600

# Existing active walkers and dictionary trampoline in the promoted main.
WALKER1 = 0x7FFDF8
WALKER1_EXT3 = 0x7FFDFE
WALKER1_NORMAL = 0x7FFE24
WALKER2 = 0x7FFE4A
WALKER2_EXT3 = 0x7FFE50
WALKER2_NORMAL = 0x7FFE76
DICT_TRAMP = 0x7AFFED
DICT_TRAMP_EXPECT = bytes.fromhex("9A50FD00F0C3")
WALKER1_EXPECT = bytes.fromhex("EA10FD00F0")
WALKER2_EXPECT = bytes.fromhex("EA30FD00F0")
EXISTING_DICT_HELPER = 0xFC8C

# Bank7E is fixed at CPU segment E000.  The promoted main has an exact FF tail
# 7E:FD83-FFFF and no far transfer target into it.  Put the generalized E51D
# dispatch there rather than enlarging the crowded 7F runtime cave.
RUNTIME_SEG = 0xE000
RUNTIME_LOGICAL_BANK = 0x7E
RUNTIME_CAVE_OFF = 0xFD83
RUNTIME_CAVE_END = 0x10000
CODE_SEG_F = 0xF000
BANK_MAP_SEG = 0x8000
BANK_MAP_OFF = 0xDEB5


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha(payload)}


def far_jmp(off: int, seg: int) -> bytes:
    return b"\xEA" + struct.pack("<HH", off & 0xFFFF, seg & 0xFFFF)


def far_call(off: int, seg: int) -> bytes:
    return b"\x9A" + struct.pack("<HH", off & 0xFFFF, seg & 0xFFFF)


def near_call(src_ip: int, dst_ip: int) -> bytes:
    disp = (dst_ip - (src_ip + 3)) & 0xFFFF
    return b"\xE8" + struct.pack("<H", disp)


def patch_rel8(blob: bytearray, opcode_pos: int, target_pos: int) -> None:
    disp = target_pos - (opcode_pos + 2)
    if not -128 <= disp <= 127:
        raise BuildError(f"rel8 overflow {disp}")
    blob[opcode_pos + 1] = disp & 0xFF


def build_common(start_ip: int) -> bytes:
    """Decode promoted fixed E51D or parameterized E51D <id> 01.

    At walker entry [bp-8] points immediately after the two-byte E51D unit.
    Fixed STAGE22t therefore sees NUL and keeps the source pointer unchanged.
    Parameterized records see a non-zero helper id and consume exactly two more
    bytes, preserving the original four-byte record body extent.
    """
    out = bytearray()
    out += bytes.fromhex("C45EF8")       # les bx,[bp-8]
    out += bytes.fromhex("268A07")       # mov al,es:[bx]
    out += bytes.fromhex("08C0")         # or al,al
    fixed_jz = len(out); out += b"\x74\x00"
    out += bytes.fromhex("8346F802")     # add word [bp-8],2
    out += bytes.fromhex("30E4")         # xor ah,ah
    out += b"\xA3" + struct.pack("<H", WRAM_INDEX)  # mov [19F8],ax
    mark_jmp = len(out); out += b"\xEB\x00"
    fixed = len(out)
    out += b"\xC7\x06" + struct.pack("<H", WRAM_INDEX) + b"\x00\x00"
    mark = len(out)
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + bytes([SPECIAL_FLAG])
    out += bytes.fromhex("BA00F0")       # mov dx,F000
    out += b"\xC3"                      # ret
    patch_rel8(out, fixed_jz, fixed)
    patch_rel8(out, mark_jmp, mark)
    return bytes(out)


def build_handler(start_ip: int, common_ip: int, ext3_ip: int, normal_ip: int) -> bytes:
    """Preserve E518 and normal paths; intercept only E51D."""
    out = bytearray()
    out += b"\x81\xFA" + EVENT_MAGIC[::-1]       # cmp dx,E51D
    special_je = len(out); out += b"\x74\x00"
    out += bytes.fromhex("81FA18E5")               # cmp dx,E518
    normal_jne = len(out); out += b"\x75\x00"
    out += far_jmp(ext3_ip, CODE_SEG_F)
    normal = len(out)
    out += far_jmp(normal_ip, CODE_SEG_F)
    special = len(out)
    call_ip = (start_ip + len(out)) & 0xFFFF
    out += near_call(call_ip, common_ip)
    out += far_jmp(normal_ip, CODE_SEG_F)
    patch_rel8(out, special_je, special)
    patch_rel8(out, normal_jne, normal)
    return bytes(out)


def build_wrapper(start_ip: int) -> bytes:
    """Resolve E51D helper index through bank26 pointer table."""
    out = bytearray()
    out += b"\x80\x3E" + struct.pack("<H", WRAM_FLAG) + bytes([SPECIAL_FLAG])
    normal_jne = len(out); out += b"\x75\x00"
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x00"
    out += b"\xB0" + bytes([EXP_SEG])
    out += far_call(BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\x8B\x1E" + struct.pack("<H", WRAM_INDEX)  # mov bx,[19F8]
    out += bytes.fromhex("D1E3")                           # shl bx,1
    out += b"\x26\x8B\x87" + struct.pack("<H", PARAM_PTR_TABLE)
    out += b"\xCB"
    normal = len(out)
    out += far_call(EXISTING_DICT_HELPER, CODE_SEG_F)
    out += b"\xCB"
    patch_rel8(out, normal_jne, normal)
    return bytes(out)


def raw_native_safe(dictionary: Any, index: int) -> bool:
    raw = bytes(dictionary.raw_entry(index, max_len=2048))
    if b"\xE5\x18" in raw or EVENT_MAGIC in raw:
        return False
    for i in range(max(0, len(raw) - 2)):
        if is_compact3_magic(raw[i], raw[i + 1]):
            return False
    return True


def choose_native_body(row: dict[str, Any], dictionary: Any, tbl: Tbl) -> tuple[bytes, str]:
    target_text = str(row["rendered_text"])
    source = bytes.fromhex(str(row["source_body_hex"]))
    if len(source) == 4 and dictionary.expand(source, tbl) == target_text:
        left = ((source[0] & 0x0F) << 8) | source[1]
        right = ((source[2] & 0x0F) << 8) | source[3]
        if raw_native_safe(dictionary, left) and raw_native_safe(dictionary, right):
            return source, "source_body_exact"
    for solution in row.get("native_pair_solutions") or []:
        left = int(str(solution["left_index"]), 16)
        right = int(str(solution["right_index"]), 16)
        if not (raw_native_safe(dictionary, left) and raw_native_safe(dictionary, right)):
            continue
        body = token_from_dict_index(left) + token_from_dict_index(right)
        if len(body) == 4 and dictionary.expand(body, tbl) == target_text:
            return body, f"native_pair_{left:04X}_{right:04X}"
    raise BuildError(f"no safe native pair for {row['address']}")


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    if len(a) != len(b):
        raise BuildError("ROM size mismatch")
    out: list[tuple[int, int]] = []
    st: int | None = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and st is None:
            st = i
        elif x == y and st is not None:
            out.append((st, i)); st = None
    if st is not None:
        out.append((st, len(a)))
    return out


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    lo, hi = run
    return any(a <= lo and hi <= b for a, b in allowed)


def main() -> int:
    main_before = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_before = LIVE_SAVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("promoted main identity drifted")
    if sha(original) != ORIGINAL_SHA:
        raise BuildError("original identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing/wrong size")

    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    rows = list(work.get("exact4") or [])
    if len(rows) != 220:
        raise BuildError(f"expected 220 exact4 rows, got {len(rows)}")
    if sum(bool(r.get("native_pair_solvable")) for r in rows) != 155:
        raise BuildError("155/65 worklist split drifted")

    manifest = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    contracts = {str(r["address"]): r for r in manifest.get("contracts") or []}
    sb = stock_base(main_before)
    dictionary = make_dictionary_ext3(
        main_before, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    tbl = Tbl.load(TBL_PATH)

    # Current promoted runtime signatures.
    if main_before[sb + WALKER1:sb + WALKER1 + 5] != WALKER1_EXPECT:
        raise BuildError("walker1 promoted signature drifted")
    if main_before[sb + WALKER2:sb + WALKER2 + 5] != WALKER2_EXPECT:
        raise BuildError("walker2 promoted signature drifted")
    if main_before[sb + DICT_TRAMP:sb + DICT_TRAMP + 6] != DICT_TRAMP_EXPECT:
        raise BuildError("dictionary trampoline promoted signature drifted")

    # Fixed bank7E cave proof and no existing far transfer into the tail.
    cave_logical = (RUNTIME_LOGICAL_BANK << 16) + RUNTIME_CAVE_OFF
    cave_end_logical = (RUNTIME_LOGICAL_BANK << 16) + RUNTIME_CAVE_END
    cave = main_before[sb + cave_logical:sb + cave_end_logical]
    if len(cave) != RUNTIME_CAVE_END - RUNTIME_CAVE_OFF or any(x != 0xFF for x in cave):
        raise BuildError("bank7E runtime tail cave is no longer all-FF")
    tail_refs: list[str] = []
    stock = main_before[sb:]
    for i in range(len(stock) - 4):
        if stock[i] not in (0x9A, 0xEA) or stock[i + 3:i + 5] != b"\x00\xE0":
            continue
        off = stock[i + 1] | (stock[i + 2] << 8)
        if off >= RUNTIME_CAVE_OFF:
            tail_refs.append(f"{i:06X}->{off:04X}")
    if tail_refs:
        raise BuildError(f"bank7E runtime cave has live far refs: {tail_refs[:8]}")

    # Existing STAGE22 helper remains index 0.
    bank26 = main_before[EXP_SEG << 16:(EXP_SEG + 1) << 16]
    if bank26[FIXED_HELPER_OFF:FIXED_HELPER_OFF + len(FIXED_HELPER)] != FIXED_HELPER:
        raise BuildError("promoted STAGE22 E51D fixed helper drifted")
    if any(x != 0xFF for x in bank26[PARAM_PTR_TABLE:PARAM_DATA_LIMIT]):
        raise BuildError("bank26 parameter helper reservation is no longer free")

    out = bytearray(main_before)
    allowed: list[tuple[int, int]] = []

    # Assign one parameter helper per distinct current top-level ext3 token.
    unsolved = [r for r in rows if not r.get("native_pair_solvable")]
    unique_ext3 = sorted({str(r["candidate_body_hex"]).upper() for r in unsolved})
    if len(unique_ext3) > 254:
        raise BuildError("parameter helper id overflow")
    helper_id = {token: i + 1 for i, token in enumerate(unique_ext3)}

    helper_ptrs: dict[int, int] = {0: FIXED_HELPER_OFF}
    cursor = PARAM_DATA_START
    for token_hex in unique_ext3:
        raw = bytes.fromhex(token_hex)
        if len(raw) != 4 or raw[:2] != bytes.fromhex("E518") or 0 in raw[2:4]:
            raise BuildError(f"unsafe nested ext3 helper token {token_hex}")
        idx = helper_id[token_hex]
        helper_ptrs[idx] = cursor
        payload = raw + b"\x00"
        if cursor + len(payload) > PARAM_DATA_LIMIT:
            raise BuildError("bank26 parameter helper pool overflow")
        abs_at = (EXP_SEG << 16) + cursor
        out[abs_at:abs_at + len(payload)] = payload
        cursor += len(payload)
    allowed.append(((EXP_SEG << 16) + PARAM_DATA_START, (EXP_SEG << 16) + cursor))

    table = bytearray()
    for idx in range(len(unique_ext3) + 1):
        table += struct.pack("<H", helper_ptrs[idx])
    table_at = (EXP_SEG << 16) + PARAM_PTR_TABLE
    out[table_at:table_at + len(table)] = table
    allowed.append((table_at, table_at + len(table)))

    target_reports: list[dict[str, Any]] = []
    native_count = 0
    wrapped_count = 0
    for row in rows:
        address = str(row["address"])
        contract = contracts.get(address)
        if contract is None:
            raise BuildError(f"missing runtime contract {address}")
        body_start = int(str(contract["body_start"]), 16)
        body_end = int(str(contract["body_end_exclusive"]), 16)
        if body_end - body_start != 4:
            raise BuildError(f"body extent not 4 at {address}")
        current = bytes(out[sb + body_start:sb + body_end])
        expected = bytes.fromhex(str(row["candidate_body_hex"]))
        if current != expected:
            raise BuildError(
                f"current body drifted at {address}: {current.hex().upper()} != {expected.hex().upper()}"
            )
        if row.get("native_pair_solvable"):
            new_body, method = choose_native_body(row, dictionary, tbl)
            native_count += 1
            helper = None
        else:
            idx = helper_id[str(row["candidate_body_hex"]).upper()]
            new_body = EVENT_MAGIC + bytes([idx, 0x01])
            method = f"event_safe_E51D_param_{idx:02X}"
            helper = {
                "id": idx,
                "pointer": f"{helper_ptrs[idx]:04X}",
                "nested_ext3": str(row["candidate_body_hex"]).upper(),
            }
            wrapped_count += 1
        if len(new_body) != 4:
            raise BuildError(f"new body extent drift {address}")
        out[sb + body_start:sb + body_end] = new_body
        allowed.append((sb + body_start, sb + body_end))
        target_reports.append({
            "address": address,
            "body_start": f"{body_start:06X}",
            "before": expected.hex().upper(),
            "after": new_body.hex().upper(),
            "source_body": str(row["source_body_hex"]).upper(),
            "rendered_text": row.get("rendered_text"),
            "priority": row.get("priority"),
            "route": row.get("route"),
            "next_control": row.get("next_control"),
            "method": method,
            "helper": helper,
        })

    if native_count != 155 or wrapped_count != 65:
        raise BuildError(f"unexpected split {native_count}/{wrapped_count}")

    # Build generalized E51D runtime in fixed bank7E tail.
    h1_ip = RUNTIME_CAVE_OFF
    h2_ip = h1_ip + 30
    common_ip = h2_ip + 30
    common = build_common(common_ip)
    wrapper_ip = common_ip + len(common)
    handler1 = build_handler(h1_ip, common_ip, WALKER1_EXT3 & 0xFFFF, WALKER1_NORMAL & 0xFFFF)
    handler2 = build_handler(h2_ip, common_ip, WALKER2_EXT3 & 0xFFFF, WALKER2_NORMAL & 0xFFFF)
    if len(handler1) != 30 or len(handler2) != 30:
        raise BuildError(f"handler size drift {len(handler1)}/{len(handler2)}")
    wrapper = build_wrapper(wrapper_ip)
    runtime_blob = handler1 + handler2 + common + wrapper
    if RUNTIME_CAVE_OFF + len(runtime_blob) > RUNTIME_CAVE_END:
        raise BuildError("bank7E runtime cave overflow")
    cave_file = sb + cave_logical
    out[cave_file:cave_file + len(runtime_blob)] = runtime_blob
    allowed.append((cave_file, cave_file + len(runtime_blob)))

    # Redirect both walkers and the native dictionary trampoline to bank7E.
    w1_file = sb + WALKER1
    w2_file = sb + WALKER2
    out[w1_file:w1_file + 5] = far_jmp(h1_ip, RUNTIME_SEG)
    out[w2_file:w2_file + 5] = far_jmp(h2_ip, RUNTIME_SEG)
    allowed += [(w1_file, w1_file + 5), (w2_file, w2_file + 5)]

    tramp_file = sb + DICT_TRAMP
    new_tramp = far_call(wrapper_ip, RUNTIME_SEG) + b"\xC3"
    out[tramp_file:tramp_file + 6] = new_tramp
    allowed.append((tramp_file, tramp_file + 6))

    update_ws_checksum(out)
    candidate = bytes(out)
    allowed.append((len(candidate) - 2, len(candidate)))

    # Hard structural checks on all 220 records.
    failures: list[str] = []
    for row, tr in zip(rows, target_reports):
        contract = contracts[str(row["address"])]
        body_start = int(str(contract["body_start"]), 16)
        body_end = int(str(contract["body_end_exclusive"]), 16)
        actual = candidate[sb + body_start:sb + body_end]
        if actual.hex().upper() != tr["after"]:
            failures.append(f"body_write:{row['address']}")
        term = int(str(contract["source_terminator"]), 16)
        if candidate[sb + term] != 0:
            failures.append(f"terminator:{row['address']}")
        boundary = contract.get("source_boundary") or {}
        if int(boundary.get("nul_run") or 0) != 2:
            failures.append(f"source_nul_run:{row['address']}")
        next_addr = int(str(boundary.get("next_address")), 16)
        if candidate[sb + term:sb + next_addr] != main_before[sb + term:sb + next_addr]:
            failures.append(f"separator_changed:{row['address']}")
        next_control_hex = str(boundary.get("next_control") or "")
        if next_control_hex:
            next_control = bytes.fromhex(next_control_hex)
            if candidate[sb + next_addr:sb + next_addr + len(next_control)] != next_control:
                failures.append(f"following_control_changed:{row['address']}")
    if failures:
        raise BuildError(f"record structural failures: {failures[:12]}")

    # STAGE22 fixed portal remains physically unchanged.
    stage22 = 0x638CD5
    if candidate[sb + stage22:sb + stage22 + 7] != main_before[sb + stage22:sb + stage22 + 7]:
        raise BuildError("STAGE22 fixed E51D record changed")

    runs = diff_runs(main_before, candidate)
    unexpected = [r for r in runs if not covered(r, allowed)]
    if unexpected:
        raise BuildError(f"unexpected whole-ROM diff runs: {unexpected[:12]}")
    checksum_ok = int(ws_header(candidate)["checksum"]) == (sum(candidate[:-2]) & 0xFFFF)
    if not checksum_ok:
        raise BuildError("WonderSwan checksum invalid")
    if bytes(load_rom(MAIN)) != main_before or LIVE_SAVE.read_bytes() != save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_global_event_native_rehome_220_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "input": {
            "main": identity(MAIN, main_before),
            "original": identity(ORIGINAL, original),
            "save": identity(LIVE_SAVE, save_before),
            "worklist": rel(WORKLIST),
        },
        "output": {
            "rom": identity(OUT_ROM, candidate),
            "save": identity(OUT_SAVE, save_before),
            "checksum": f"{ws_header(candidate)['checksum']:04X}",
        },
        "counts": {
            "targets": len(rows),
            "direct_native_pair": native_count,
            "event_safe_parameterized": wrapped_count,
            "unique_nested_ext3_helpers": len(unique_ext3),
            "bank26_helper_bytes": cursor - PARAM_DATA_START,
            "runtime_blob_bytes": len(runtime_blob),
        },
        "runtime": {
            "segment": f"{RUNTIME_SEG:04X}",
            "bank7e_cave": f"7E:{RUNTIME_CAVE_OFF:04X}-{RUNTIME_CAVE_OFF + len(runtime_blob) - 1:04X}",
            "walker1": f"7F:FDF8 -> E000:{h1_ip:04X}",
            "walker2": f"7F:FE4A -> E000:{h2_ip:04X}",
            "common": f"E000:{common_ip:04X}",
            "dict_wrapper": f"E000:{wrapper_ip:04X}",
            "dict_trampoline": f"7A:FFED -> E000:{wrapper_ip:04X}",
            "fixed_E51D_helper_index": 0,
            "parameter_encoding": "E5 1D <helper_id 01..FE> 01",
            "parameter_pointer_table": f"26:{PARAM_PTR_TABLE:04X}",
            "parameter_data": f"26:{PARAM_DATA_START:04X}-{cursor - 1:04X}",
            "parameter_helpers": "nested existing E5 18 token + NUL; no direct Hangul bytes added",
            "old_E518_ext3_bodies_preserved": True,
        },
        "diff": {
            "runs": len(runs),
            "bytes": sum(b - a for a, b in runs),
            "unexpected_runs": [],
        },
        "checks": {
            "all_220_body_extents_preserved": True,
            "all_220_terminators_preserved": True,
            "all_220_double_nul_and_following_controls_preserved": True,
            "top_level_direct_E518_removed_from_all_220": all(
                not bytes.fromhex(x["after"]).startswith(bytes.fromhex("E518"))
                for x in target_reports
            ),
            "stage22_fixed_E51D_record_unchanged": True,
            "bank7e_cave_was_all_ff": True,
            "bank7e_cave_had_no_far_refs": True,
            "checksum_valid": checksum_ok,
            "main_tip_unchanged": bytes(load_rom(MAIN)) == main_before,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == save_before,
        },
        "helpers": [
            {
                "id": helper_id[token],
                "pointer": f"{helper_ptrs[helper_id[token]]:04X}",
                "nested_ext3": token,
            }
            for token in unique_ext3
        ],
        "targets": target_reports,
        "promotion": "blocked_pending_representative_runtime_matrix",
    }
    OUT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "ok": True,
        "candidate": report["output"]["rom"],
        "save": report["output"]["save"],
        "checksum": report["output"]["checksum"],
        "counts": report["counts"],
        "runtime": report["runtime"],
        "diff": report["diff"],
        "report": rel(OUT_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
