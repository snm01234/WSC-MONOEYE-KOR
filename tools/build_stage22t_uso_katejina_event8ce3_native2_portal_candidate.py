#!/usr/bin/env python3
"""Build a minimal STAGE22t Uso/Katejina Event Error 3000:8CE3 probe.

Goal
----
Do not reclaim any existing F0-FF dictionary index.  Instead reserve one
currently-unused 2-byte text unit, E5 1B, as a dedicated portal into expansion
bank 0x26.  The portal deliberately enters the *stock/native dictionary phrase
loop*, not the E5 18 ext3 leaf path.

Target 63:8CD5 (original `……え？`, main `……어？`):

    main      17 34 18 | E5 18 52 F1
    candidate 17 34 18 | F1 91 E5 1B

F191 remains the ordinary native token for `……`; E51B expands to `어？` through
one dedicated phrase at expansion bank26:2000.  Record extent, NUL terminator,
and all following event/control bytes are preserved.

This is a runtime-test candidate only.  Main TIP and live SaveRAM are not
modified.
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
from expand_dictionary import AUX_TOKEN_BANKS, NAME75_RANGES, SCRIPT_TOKEN_BANKS, _walk_zstring_range  # noqa: E402
from monoeye_rom import BANK_SIZE, EXT3_INDEX_BASE, Tbl, encode_plaintext, load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
OUT_ROM = PATCH / "stage22t_uso_katejina_event8ce3_native2_portal_v2_candidate.wsc"
OUT_SAVE = ROOT / "sram/stage22t_uso_katejina_event8ce3_native2_portal_v2_candidate.sav"
OUT_REPORT = PATCH / "stage22t_uso_katejina_event8ce3_native2_portal_v2_report.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MAIN_SHA = "f68b3261beecc32047d17952e36bc2b891cd5d66410f9fc9293487571a0fc8e2"
ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"

TARGET = 0x638CD5
TARGET_MAIN = bytes.fromhex("173418E51852F1")
TARGET_ORIGINAL = bytes.fromhex("173418F1912B1D")
TARGET_AFTER = bytes.fromhex("173418F191E51B")
TARGET_TERM = 0x638CDC
ERROR_SITE = 0x638CE3
ERROR_CONTROL = bytes.fromhex("1728081E00171C081D00173418")

# New 2-byte portal. E518 is ext3, E519 is the rejected compact3 experiment;
# E51B is typed-text-unused on the accepted main and absent from all expansion
# bytes before this candidate.
MAGIC2 = bytes.fromhex("E51B")
SPECIAL_FLAG = 2
WRAM_FLAG = 0x19FA

# Expansion bank26 is completely FF on the accepted main.  Keep a roomy 0x100
# byte logical reservation for this family but write only the live phrase.
EXP_SEG = 0x26
PHRASE_OFF = 0x2000
RESERVED_END = 0x2100
PHRASE_TEXT = "어？"
# Do not put a direct Hangul glyph in the expansion phrase.  Runtime v1 proved
# that the event itself advanced but the following Uso text/name rendering was
# corrupted.  Use only already-proven native dictionary tokens inside bank26:
#   F36A -> 어   (stock dict raw EC8D E786)
#   F16E -> ？   (stock dict raw 1D)
# This forces each glyph through the ordinary nested native dictionary path,
# including its established bank save/restore behavior.
PHRASE_PAYLOAD = bytes.fromhex("F36AF16E00")

# Active current ext3 walkers/leaf.  We replace only the first 5 bytes of each
# walker with a far jump to tiny prehandlers placed in an unreachable retired
# cave.  The original ext3 body and current F8C1 padding guard remain intact.
WALKER1 = 0x7FFDF8
WALKER1_EXT3 = 0x7FFDFE
WALKER1_NORMAL = 0x7FFE24
WALKER2 = 0x7FFE4A
WALKER2_EXT3 = 0x7FFE50
WALKER2_NORMAL = 0x7FFE76
PRE1 = 0x7FFD10
PRE2 = 0x7FFD30
DICT_WRAP = 0x7FFD50
CAVE_END = 0x7FFD70
CODE_SEG = 0xF000

# 7A:0700 calls the same-CS trampoline at 7A:FFED; retarget only its far-call
# operand from 7F:FC8C to our wrapper.  Non-special dictionary loads immediately
# delegate to the existing helper, so all native/bank10 semantics stay intact.
DICT_TRAMP = 0x7AFFED
DICT_TRAMP_CURRENT = bytes.fromhex("9A8CFC00F0C3")
EXISTING_DICT_HELPER_OFF = 0xFC8C

BANK_MAP_SEG = 0x8000
BANK_MAP_OFF = 0xDEB5


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sab(data: bytes | bytearray, logical: int) -> int:
    return stock_base(data) + logical


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha(payload)}


def far_jmp(off: int, seg: int = CODE_SEG) -> bytes:
    return b"\xEA" + struct.pack("<HH", off & 0xFFFF, seg & 0xFFFF)


def far_call(off: int, seg: int) -> bytes:
    return b"\x9A" + struct.pack("<HH", off & 0xFFFF, seg & 0xFFFF)


def near_jmp(src_ip: int, dst_ip: int) -> bytes:
    disp = (dst_ip - (src_ip + 3)) & 0xFFFF
    return b"\xE9" + struct.pack("<H", disp)


def build_prehandler(start: int, ext3_target: int, normal_target: int) -> bytes:
    """Recognise E51B, otherwise reproduce the overwritten E518 dispatch."""
    ip = start & 0xFFFF
    out = bytearray()
    out += b"\x81\xFA" + MAGIC2[::-1]  # cmp dx, E51Bh
    out += b"\x75\x0B"  # jne check_ext3; special block is 11 bytes
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + bytes([SPECIAL_FLAG])
    out += b"\xBA\x00\xF0"  # mov dx,F000h -> stock dictionary route
    jmp_at = (ip + len(out)) & 0xFFFF
    out += near_jmp(jmp_at, normal_target & 0xFFFF)
    # check_ext3: preserve the accepted E518 path exactly.
    out += b"\x81\xFA\x18\xE5"  # cmp dx,E518h
    out += b"\x75\x03"  # jne normal near-jmp
    jmp_at = (ip + len(out)) & 0xFFFF
    out += near_jmp(jmp_at, ext3_target & 0xFFFF)
    jmp_at = (ip + len(out)) & 0xFFFF
    out += near_jmp(jmp_at, normal_target & 0xFFFF)
    return bytes(out)


def build_dict_wrapper() -> bytes:
    out = bytearray()
    out += b"\x80\x3E" + struct.pack("<H", WRAM_FLAG) + bytes([SPECIAL_FLAG])
    out += b"\x75\x10"  # jne normal; special block below is 16 bytes
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x00"
    out += b"\xB0" + bytes([EXP_SEG])
    out += far_call(BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xB8" + struct.pack("<H", PHRASE_OFF)
    out += b"\xCB"  # retf
    out += far_call(EXISTING_DICT_HELPER_OFF, CODE_SEG)
    out += b"\xCB"
    return bytes(out)


def typed_magic_sites(data: bytes, magic: bytes) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def scan(region: str, logical: int, payload: bytes) -> None:
        pos = 0
        while True:
            pos = payload.find(magic, pos)
            if pos < 0:
                break
            found.append({"region": region, "logical": f"{logical + pos:06X}"})
            pos += 1

    for seg in SCRIPT_TOKEN_BANKS:
        lo = seg * BANK_SIZE
        for logical, payload, _ in _walk_zstring_range(data, lo, lo + BANK_SIZE, region="script"):
            scan("script", logical, payload)
    for seg in AUX_TOKEN_BANKS:
        lo = seg * BANK_SIZE
        for logical, payload, _ in _walk_zstring_range(data, lo, lo + BANK_SIZE, region="aux", max_len=128):
            scan("aux", logical, payload)
    for lo, hi in NAME75_RANGES:
        for logical, payload, _ in _walk_zstring_range(data, lo, hi, region="name75", max_len=64):
            scan("name75", logical, payload)
    return found


def dictionary_magic_sites(data: bytes, magic: bytes) -> list[dict[str, Any]]:
    """Fail-closed ownership scan inside all native + ext3 dictionary phrases."""
    dictionary = make_dictionary_ext3(
        data,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    found: list[dict[str, Any]] = []
    indices = list(range(dictionary.count))
    indices.extend(range(EXT3_INDEX_BASE, EXT3_INDEX_BASE + dictionary.ext3_count))
    for index in indices:
        raw = bytes(dictionary.raw_entry(index, max_len=2048))
        pos = raw.find(magic)
        if pos < 0:
            continue
        found.append({
            "index": f"{index:04X}",
            "kind": "native_dictionary" if index < EXT3_INDEX_BASE else "ext3_phrase",
            "raw_hex": raw.hex().upper(),
            "payload_offset": pos,
        })
    return found


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


def covered(run: tuple[int, int], allow: list[tuple[int, int]]) -> bool:
    a, b = run
    return any(lo <= a and b <= hi for lo, hi in allow)


def main() -> int:
    main_before = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_before = LIVE_SAVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("main TIP identity drifted")
    if sha(original) != ORIGINAL_SHA:
        raise BuildError("original ROM identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing/wrong size")

    sb = stock_base(main_before)
    if main_before[sb + TARGET:sb + TARGET + len(TARGET_MAIN)] != TARGET_MAIN:
        raise BuildError("63:8CD5 main payload drifted")
    if original[TARGET:TARGET + len(TARGET_ORIGINAL)] != TARGET_ORIGINAL:
        raise BuildError("63:8CD5 original payload drifted")
    if main_before[sb + TARGET_TERM] != 0 or original[TARGET_TERM] != 0:
        raise BuildError("63:8CD5 terminator drifted")
    if main_before[sb + ERROR_SITE:sb + ERROR_SITE + len(ERROR_CONTROL)] != ERROR_CONTROL:
        raise BuildError("reported 63:8CE3 control bytes drifted")
    if original[ERROR_SITE:ERROR_SITE + len(ERROR_CONTROL)] != ERROR_CONTROL:
        raise BuildError("original 63:8CE3 control bytes drifted")

    typed_before = typed_magic_sites(main_before, MAGIC2)
    dictionary_before = dictionary_magic_sites(main_before, MAGIC2)
    exp_before = main_before[:0x800000]
    if typed_before:
        raise BuildError(f"{MAGIC2.hex().upper()} already used by typed text: {typed_before[:8]}")
    if dictionary_before:
        raise BuildError(
            f"{MAGIC2.hex().upper()} already owned inside dictionary phrases: "
            f"{dictionary_before[:8]}"
        )
    # Raw expansion hits are also blocked because this portal is global at runtime.
    # Stock-half raw hits are advisory only after the typed+dictionary ownership scans.
    if exp_before.count(MAGIC2):
        raise BuildError(f"{MAGIC2.hex().upper()} already occurs in expansion bytes")
    raw_stock_hits = main_before[sb:].count(MAGIC2)

    bank26 = main_before[EXP_SEG << 16:(EXP_SEG + 1) << 16]
    if any(b != 0xFF for b in bank26):
        raise BuildError("expansion bank26 is no longer empty")

    pre1 = build_prehandler(PRE1, WALKER1_EXT3, WALKER1_NORMAL)
    pre2 = build_prehandler(PRE2, WALKER2_EXT3, WALKER2_NORMAL)
    wrapper = build_dict_wrapper()
    if PRE1 + len(pre1) > PRE2 or PRE2 + len(pre2) > DICT_WRAP or DICT_WRAP + len(wrapper) > CAVE_END:
        raise BuildError("retired cave layout overflow")

    # The retired cave must have no far transfer targets in the current stock half.
    stale_refs: list[str] = []
    stock = main_before[sb:]
    for i in range(len(stock) - 4):
        if stock[i] not in (0x9A, 0xEA) or stock[i + 3:i + 5] != b"\x00\xF0":
            continue
        off = stock[i + 1] | (stock[i + 2] << 8)
        if (PRE1 & 0xFFFF) <= off < (CAVE_END & 0xFFFF):
            stale_refs.append(f"{i:06X}->{off:04X}")
    if stale_refs:
        raise BuildError(f"retired cave has live far refs: {stale_refs[:8]}")

    # Current active handler entry signatures and dictionary trampoline.
    if main_before[sb + WALKER1:sb + WALKER1 + 6] != bytes.fromhex("81FA18E57526"):
        raise BuildError("walker1 signature drifted")
    if main_before[sb + WALKER2:sb + WALKER2 + 6] != bytes.fromhex("81FA18E57526"):
        raise BuildError("walker2 signature drifted")
    if main_before[sb + DICT_TRAMP:sb + DICT_TRAMP + len(DICT_TRAMP_CURRENT)] != DICT_TRAMP_CURRENT:
        raise BuildError("native dictionary trampoline drifted")

    out = bytearray(main_before)
    allow: list[tuple[int, int]] = []

    # New expansion phrase; leave the rest of the 0x100-byte reservation FF.
    # v2 deliberately stores only nested native tokens, no direct Hangul bytes.
    phrase = PHRASE_PAYLOAD
    exp_at = (EXP_SEG << 16) + PHRASE_OFF
    out[exp_at:exp_at + len(phrase)] = phrase
    allow.append((exp_at, exp_at + len(phrase)))

    # Install prehandlers/wrapper into the unreachable retired cave.
    for logical, blob in ((PRE1, pre1), (PRE2, pre2), (DICT_WRAP, wrapper)):
        at = sb + logical
        out[at:at + len(blob)] = blob
        allow.append((at, at + len(blob)))

    # Active walker entry redirects.
    for logical, dest in ((WALKER1, PRE1), (WALKER2, PRE2)):
        at = sb + logical
        out[at:at + 5] = far_jmp(dest)
        allow.append((at, at + 5))

    # Retarget same-CS dictionary trampoline's far-call operand to our wrapper.
    tramp_at = sb + DICT_TRAMP
    new_tramp = far_call(DICT_WRAP & 0xFFFF, CODE_SEG) + b"\xC3"
    out[tramp_at:tramp_at + len(new_tramp)] = new_tramp
    allow.append((tramp_at, tramp_at + len(new_tramp)))

    # One dialogue record only.
    target_at = sb + TARGET
    out[target_at:target_at + len(TARGET_AFTER)] = TARGET_AFTER
    allow.append((target_at, target_at + len(TARGET_AFTER)))

    update_ws_checksum(out)
    candidate = bytes(out)
    allow.append((len(candidate) - 2, len(candidate)))

    # Static proof.
    if candidate[sb + TARGET:sb + TARGET + len(TARGET_AFTER)] != TARGET_AFTER:
        raise BuildError("target write failed")
    if candidate[sb + TARGET_TERM] != 0:
        raise BuildError("target terminator moved")
    if candidate[sb + ERROR_SITE:sb + ERROR_SITE + len(ERROR_CONTROL)] != ERROR_CONTROL:
        raise BuildError("63:8CE3 control bytes changed")
    if candidate[exp_at:exp_at + len(phrase)] != phrase:
        raise BuildError("expansion phrase write failed")
    if candidate[(EXP_SEG << 16) + RESERVED_END - 1] != 0xFF:
        raise BuildError("roomy bank26 reservation tail unexpectedly occupied")

    # Existing active ext3 bodies after the redirected 5-byte entries remain byte-exact.
    ext3_body_checks = {
        "walker1_ext3_body_unchanged": candidate[sb + WALKER1_EXT3:sb + WALKER1_NORMAL] == main_before[sb + WALKER1_EXT3:sb + WALKER1_NORMAL],
        "walker2_ext3_body_unchanged": candidate[sb + WALKER2_EXT3:sb + WALKER2_NORMAL] == main_before[sb + WALKER2_EXT3:sb + WALKER2_NORMAL],
        "leaf_handler_unchanged": candidate[sb + 0x7FFE9D:sb + 0x7FFF18] == main_before[sb + 0x7FFE9D:sb + 0x7FFF18],
        "existing_dict_helper_unchanged": candidate[sb + 0x7FFC8C:sb + 0x7FFCAB] == main_before[sb + 0x7FFC8C:sb + 0x7FFCAB],
    }
    if not all(ext3_body_checks.values()):
        raise BuildError(f"existing runtime body changed: {ext3_body_checks}")

    runs = diff_runs(main_before, candidate)
    unexpected = [r for r in runs if not covered(r, allow)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:8]}")

    checksum_ok = int(ws_header(candidate)["checksum"]) == (sum(candidate[:-2]) & 0xFFFF)
    if not checksum_ok:
        raise BuildError("candidate checksum invalid")
    if bytes(load_rom(MAIN)) != main_before or LIVE_SAVE.read_bytes() != save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage22t_uso_katejina_event8ce3_native2_portal_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "input": {
            "main": identity(MAIN, main_before),
            "original": identity(ORIGINAL, original),
            "save": identity(LIVE_SAVE, save_before),
        },
        "output": {
            "rom": identity(OUT_ROM, candidate),
            "save": identity(OUT_SAVE, save_before),
            "checksum": f"{ws_header(candidate)['checksum']:04X}",
        },
        "strategy": {
            "existing_dictionary_ids_reclaimed": 0,
            "new_2byte_portal": MAGIC2.hex().upper(),
            "portal_semantics": "2-byte unit rerouted to stock/native dictionary phrase loop; ext3 leaf flag path is not used",
            "expansion_bank": f"{EXP_SEG:02X}",
            "phrase_offset": f"{PHRASE_OFF:04X}",
            "reserved_pool": f"{EXP_SEG:02X}:{PHRASE_OFF:04X}-{RESERVED_END - 1:04X}",
            "reserved_pool_bytes": RESERVED_END - PHRASE_OFF,
            "phrase": PHRASE_TEXT,
            "phrase_hex": phrase.hex().upper(),
            "phrase_storage": "nested_native_only",
            "phrase_tokens": ["F36A=어", "F16E=？"],
            "direct_hangul_bytes_in_expansion_phrase": False,
            "typed_magic_consumers_before": typed_before,
            "dictionary_magic_consumers_before": dictionary_before,
            "semantic_magic_consumers_before": len(typed_before) + len(dictionary_before),
            "expansion_raw_magic_hits_before": exp_before.count(MAGIC2),
            "stock_raw_magic_hits_advisory_only": raw_stock_hits,
        },
        "target": {
            "address": f"{TARGET:06X}",
            "main_hex": TARGET_MAIN.hex().upper(),
            "original_hex": TARGET_ORIGINAL.hex().upper(),
            "candidate_hex": TARGET_AFTER.hex().upper(),
            "render_plan": f"F191 => …… ; {MAGIC2.hex().upper()} => 어？",
            "terminator": f"{TARGET_TERM:06X}",
            "reported_error_site": f"{ERROR_SITE:06X}",
            "reported_error_control_hex": ERROR_CONTROL.hex().upper(),
        },
        "runtime": {
            "prehandler1": {"logical": f"{PRE1:06X}", "hex": pre1.hex().upper()},
            "prehandler2": {"logical": f"{PRE2:06X}", "hex": pre2.hex().upper()},
            "dict_wrapper": {"logical": f"{DICT_WRAP:06X}", "hex": wrapper.hex().upper()},
            "active_walker1_redirect": f"{WALKER1:06X}->{PRE1:06X}",
            "active_walker2_redirect": f"{WALKER2:06X}->{PRE2:06X}",
            "native_dict_trampoline": f"{DICT_TRAMP:06X}->{DICT_WRAP:06X}",
            "existing_runtime_checks": ext3_body_checks,
        },
        "diff": {
            "runs": len(runs),
            "bytes": sum(b - a for a, b in runs),
            "unexpected_runs": [],
        },
        "checks": {
            "target_extent_preserved": len(TARGET_MAIN) == len(TARGET_AFTER),
            "target_direct_e518_removed": b"\xE5\x18" not in TARGET_AFTER,
            "target_native_f191_kept": TARGET_AFTER[3:5] == bytes.fromhex("F191"),
            "target_uses_new_2byte_portal": TARGET_AFTER[5:7] == MAGIC2,
            "terminator_preserved": candidate[sb + TARGET_TERM] == 0,
            "error_control_byte_exact": candidate[sb + ERROR_SITE:sb + ERROR_SITE + len(ERROR_CONTROL)] == ERROR_CONTROL,
            "no_existing_dictionary_id_reclaim": True,
            "bank26_was_empty": True,
            "roomy_pool_tail_ff": candidate[(EXP_SEG << 16) + RESERVED_END - 1] == 0xFF,
            "existing_ext3_leaf_unchanged": ext3_body_checks["leaf_handler_unchanged"],
            "checksum_valid": checksum_ok,
            "main_tip_unchanged": bytes(load_rom(MAIN)) == main_before,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == save_before,
        },
        "promotion": "blocked_pending_user_runtime_verification",
        "test_protocol": [
            "Load stage22t_uso_katejina_event8ce3_native2_portal_v2_candidate.wsc with the paired SaveRAM.",
            "Reproduce STAGE22t Uso/Katejina dialogue around `……어？`.",
            f"Confirm `……어？` renders correctly (no {MAGIC2.hex().upper()} glyph/garbage).",
            "Confirm Event Error 12288 / 36067 (3000:8CE3) no longer occurs.",
            "Confirm the following Uso name/dialogue renders normally; this is the v1 regression gate.",
            "Confirm the next Katejina line/event progresses normally without control glyph leakage or replay.",
        ],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "ok": True,
        "candidate": report["output"]["rom"],
        "save": report["output"]["save"],
        "checksum": report["output"]["checksum"],
        "target": report["target"],
        "strategy": report["strategy"],
        "diff": report["diff"],
        "report": rel(OUT_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
