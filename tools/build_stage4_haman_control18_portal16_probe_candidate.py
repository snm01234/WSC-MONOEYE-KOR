#!/usr/bin/env python3
"""Build a STAGE4 probe that preserves structural 0x18 and replaces direct E518.

The runtime-proven narrow candidate removed the leading 0x18.  That fixes
60BB48, but historical 6017FC/601826 evidence shows physical deletion can merge
page groups.  This probe therefore preserves 0x18 and tests a scalable native-
loop portal instead:

    18 E5 18 72 3C 01  ->  18 E5 04 01 01 01

E504 was semantic-zero on the promoted parent main.  The two parameter bytes
are base-255 nonzero digits; index 0 is encoded 01 01.  The helper is placed at
bank27:2000 and contains only the existing E518 token plus NUL:

    27:2000 = E5 18 72 3C 00

An extended dispatcher is written into an unused bank7E tail cave.  Existing
E51D fixed/parameterized behavior is reproduced unchanged, while flag 3 maps
bank27 and returns helper_base + index*5.  No main TIP or live SaveRAM is
modified.
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

from audit_global_event_runtime_risk_v2 import semantic_e5_usage  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "stage4_haman_control18_portal16_probe_candidate.wsc"
OUT_SAVE = ROOT / "sram/stage4_haman_control18_portal16_probe_candidate.sav"
OUT_REPORT = PATCH / "stage4_haman_control18_portal16_probe_report.json"

EXPECTED_MAIN_SHA = "cfb90aaa7af2b9336fb63c70a8e7ec760ac51425d80017d5daf82e6118d86bca"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

TARGET = 0x60BB48
BEFORE_PAYLOAD = bytes.fromhex("18E518723C01")
AFTER_PAYLOAD = bytes.fromhex("18E504010101")
TARGET_TERM = 0x60BB4E
TARGET_NEXT = 0x60BB50
HELPER_TOKEN = bytes.fromhex("E518723C")

EVENT_MAGIC = bytes.fromhex("E51D")
PORTAL16_MAGIC = bytes.fromhex("E504")
WRAM_INDEX = 0x19F8
WRAM_FLAG = 0x19FA
E51D_FLAG = 2
PORTAL16_FLAG = 3
E51D_BANK = 0x26
PORTAL16_BANK = 0x27
E51D_PTR_TABLE = 0x2100
PORTAL16_HELPER_BASE = 0x2000
PORTAL16_HELPER_STRIDE = 5

WALKER1 = 0x7FFDF8
WALKER1_EXT3 = 0x7FFDFE
WALKER1_NORMAL = 0x7FFE24
WALKER2 = 0x7FFE4A
WALKER2_EXT3 = 0x7FFE50
WALKER2_NORMAL = 0x7FFE76
DICT_TRAMP = 0x7AFFED
EXISTING_DICT_HELPER = 0xFC8C
BANK_MAP_SEG = 0x8000
BANK_MAP_OFF = 0xDEB5
CODE_SEG_F = 0xF000

CURRENT_WALKER1 = bytes.fromhex("EA83FD00E0")
CURRENT_WALKER2 = bytes.fromhex("EAA1FD00E0")
CURRENT_TRAMP = bytes.fromhex("9AE3FD00E0C3")

RUNTIME_SEG = 0xE000
RUNTIME_BANK = 0x7E
RUNTIME_START = 0xFE20
RUNTIME_LIMIT = 0x10000


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
    return b"\xE8" + struct.pack("<H", (dst_ip - (src_ip + 3)) & 0xFFFF)


def patch_rel8(blob: bytearray, opcode_pos: int, target_pos: int) -> None:
    disp = target_pos - (opcode_pos + 2)
    if not -128 <= disp <= 127:
        raise BuildError(f"rel8 overflow: {disp}")
    blob[opcode_pos + 1] = disp & 0xFF


def build_e51d_common(start_ip: int) -> bytes:
    out = bytearray()
    out += bytes.fromhex("C45EF8")       # les bx,[bp-8]
    out += bytes.fromhex("268A07")       # mov al,es:[bx]
    out += bytes.fromhex("08C0")         # or al,al
    fixed_jz = len(out); out += b"\x74\x00"
    out += bytes.fromhex("8346F802")     # add word [bp-8],2
    out += bytes.fromhex("30E4")         # xor ah,ah
    out += b"\xA3" + struct.pack("<H", WRAM_INDEX)
    mark_jmp = len(out); out += b"\xEB\x00"
    fixed = len(out)
    out += b"\xC7\x06" + struct.pack("<H", WRAM_INDEX) + b"\x00\x00"
    mark = len(out)
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + bytes([E51D_FLAG])
    out += bytes.fromhex("BA00F0")
    out += b"\xC3"
    patch_rel8(out, fixed_jz, fixed)
    patch_rel8(out, mark_jmp, mark)
    return bytes(out)


def build_portal16_common() -> bytes:
    # Decode two nonzero base-255 digits from ES:[BX]:
    # index = (high-1)*255 + (low-1)
    return bytes.fromhex(
        "C45EF8"      # les bx,[bp-8]
        "268A4701"    # mov al,es:[bx+1]
        "FEC8"        # dec al
        "88C2"        # mov dl,al
        "30E4"        # xor ah,ah
        "86C4"        # xchg al,ah => high*256
        "30F6"        # xor dh,dh
        "29D0"        # sub ax,dx => high*255
        "268A17"      # mov dl,es:[bx]
        "FECA"        # dec dl
        "30F6"        # xor dh,dh
        "01D0"        # add ax,dx
        "8346F802"    # add word [bp-8],2
        "A3F819"      # mov [19F8],ax
        "C606FA1903"  # mov byte [19FA],3
        "BA00F0"      # mov dx,F000
        "C3"          # ret
    )


def build_handler(start_ip: int, e51d_common_ip: int, p16_common_ip: int, ext3_ip: int, normal_ip: int) -> bytes:
    out = bytearray()
    out += bytes.fromhex("81FA1DE5")  # cmp dx,E51D
    e51d_je = len(out); out += b"\x74\x00"
    out += bytes.fromhex("81FA04E5")  # cmp dx,E504
    p16_je = len(out); out += b"\x74\x00"
    out += bytes.fromhex("81FA18E5")  # cmp dx,E518
    normal_jne = len(out); out += b"\x75\x00"
    out += far_jmp(ext3_ip, CODE_SEG_F)
    normal = len(out)
    out += far_jmp(normal_ip, CODE_SEG_F)
    e51d = len(out)
    call_ip = (start_ip + len(out)) & 0xFFFF
    out += near_call(call_ip, e51d_common_ip)
    out += far_jmp(normal_ip, CODE_SEG_F)
    p16 = len(out)
    call_ip = (start_ip + len(out)) & 0xFFFF
    out += near_call(call_ip, p16_common_ip)
    out += far_jmp(normal_ip, CODE_SEG_F)
    patch_rel8(out, e51d_je, e51d)
    patch_rel8(out, p16_je, p16)
    patch_rel8(out, normal_jne, normal)
    return bytes(out)


def build_wrapper(start_ip: int) -> bytes:
    out = bytearray()
    out += b"\x80\x3E" + struct.pack("<H", WRAM_FLAG) + bytes([E51D_FLAG])
    e51d_je = len(out); out += b"\x74\x00"
    out += b"\x80\x3E" + struct.pack("<H", WRAM_FLAG) + bytes([PORTAL16_FLAG])
    p16_je = len(out); out += b"\x74\x00"
    out += far_call(EXISTING_DICT_HELPER, CODE_SEG_F)
    out += b"\xCB"

    e51d = len(out)
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x00"
    out += b"\xB0" + bytes([E51D_BANK])
    out += far_call(BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\x8B\x1E" + struct.pack("<H", WRAM_INDEX)
    out += bytes.fromhex("D1E3")
    out += b"\x26\x8B\x87" + struct.pack("<H", E51D_PTR_TABLE)
    out += b"\xCB"

    p16 = len(out)
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x00"
    out += b"\xB0" + bytes([PORTAL16_BANK])
    out += far_call(BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\x8B\x1E" + struct.pack("<H", WRAM_INDEX)  # bx=index
    out += bytes.fromhex("89D8")                          # ax=bx
    out += bytes.fromhex("D1E3D1E3")                      # bx=index*4
    out += bytes.fromhex("01C3")                          # bx+=ax => index*5
    out += b"\x81\xC3" + struct.pack("<H", PORTAL16_HELPER_BASE)
    out += bytes.fromhex("89D8")                          # ax=helper offset
    out += b"\xCB"

    patch_rel8(out, e51d_je, e51d)
    patch_rel8(out, p16_je, p16)
    return bytes(out)


def disassemble(blob: bytes, start: int) -> list[str]:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_16
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    return [f"{ins.address:04X}: {ins.mnemonic} {ins.op_str}".rstrip() for ins in md.disasm(blob, start)]


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
    parent = bytes(load_rom(MAIN))
    save = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main identity drifted: {sha(parent)}")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drifted: {len(save)}")
    sb = stock_base(parent)

    if parent[sb + WALKER1:sb + WALKER1 + 5] != CURRENT_WALKER1:
        raise BuildError("walker1 signature drifted")
    if parent[sb + WALKER2:sb + WALKER2 + 5] != CURRENT_WALKER2:
        raise BuildError("walker2 signature drifted")
    if parent[sb + DICT_TRAMP:sb + DICT_TRAMP + 6] != CURRENT_TRAMP:
        raise BuildError("dictionary trampoline signature drifted")

    current_payload = parent[sb + TARGET:sb + TARGET + len(BEFORE_PAYLOAD)]
    if current_payload != BEFORE_PAYLOAD:
        raise BuildError(f"60BB48 payload drifted: {current_payload.hex().upper()}")
    if parent[sb + TARGET_TERM:sb + TARGET_NEXT] != b"\x00\x00":
        raise BuildError("60BB48 double-NUL drifted")

    dictionary = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    e5_count, e5_source = semantic_e5_usage(parent, dictionary)
    if e5_count[PORTAL16_MAGIC[1]] != 0:
        raise BuildError(f"E504 is no longer semantic-zero: {e5_count[PORTAL16_MAGIC[1]]}")

    bank27 = parent[PORTAL16_BANK << 16:(PORTAL16_BANK + 1) << 16]
    if len(bank27) != 0x10000 or any(x != 0xFF for x in bank27):
        raise BuildError("bank27 is no longer all-FF")

    runtime_file = sb + (RUNTIME_BANK << 16) + RUNTIME_START
    runtime_tail = parent[runtime_file:sb + ((RUNTIME_BANK + 1) << 16)]
    if any(x != 0xFF for x in runtime_tail):
        raise BuildError("bank7E FE20+ cave is no longer all-FF")

    # Fail closed if any current far call/jump already targets the new cave.
    stock = parent[sb:]
    refs: list[str] = []
    for i in range(len(stock) - 4):
        if stock[i] not in (0x9A, 0xEA) or stock[i + 3:i + 5] != b"\x00\xE0":
            continue
        off = stock[i + 1] | (stock[i + 2] << 8)
        if off >= RUNTIME_START:
            refs.append(f"{i:06X}->{off:04X}")
    if refs:
        raise BuildError(f"new bank7E cave already referenced: {refs[:8]}")

    # Lay out the new extended dispatcher.
    h1_ip = RUNTIME_START
    e51d_common = build_e51d_common(0)
    p16_common = build_portal16_common()
    # Handler size is independent of destination addresses; build once to size it.
    dummy_h = build_handler(h1_ip, 0xFFFF, 0xFFFF, WALKER1_EXT3 & 0xFFFF, WALKER1_NORMAL & 0xFFFF)
    h2_ip = h1_ip + len(dummy_h)
    dummy_h2 = build_handler(h2_ip, 0xFFFF, 0xFFFF, WALKER2_EXT3 & 0xFFFF, WALKER2_NORMAL & 0xFFFF)
    e51d_ip = h2_ip + len(dummy_h2)
    p16_ip = e51d_ip + len(e51d_common)
    wrapper_ip = p16_ip + len(p16_common)
    handler1 = build_handler(h1_ip, e51d_ip, p16_ip, WALKER1_EXT3 & 0xFFFF, WALKER1_NORMAL & 0xFFFF)
    handler2 = build_handler(h2_ip, e51d_ip, p16_ip, WALKER2_EXT3 & 0xFFFF, WALKER2_NORMAL & 0xFFFF)
    wrapper = build_wrapper(wrapper_ip)
    runtime_blob = handler1 + handler2 + e51d_common + p16_common + wrapper
    runtime_end = RUNTIME_START + len(runtime_blob)
    if runtime_end > RUNTIME_LIMIT:
        raise BuildError(f"extended runtime cave overflow: {runtime_end:04X}")

    # Basic disassembly sanity: all bytes must decode and the final instruction is retf.
    disasm = disassemble(runtime_blob, RUNTIME_START)
    if not disasm or not disasm[-1].startswith(f"{runtime_end - 1:04X}: retf"):
        raise BuildError(f"runtime disassembly did not end at retf: {disasm[-5:]}")

    out = bytearray(parent)
    allowed: list[tuple[int, int]] = []

    out[sb + TARGET:sb + TARGET + len(AFTER_PAYLOAD)] = AFTER_PAYLOAD
    allowed.append((sb + TARGET, sb + TARGET + len(AFTER_PAYLOAD)))

    helper_at = (PORTAL16_BANK << 16) + PORTAL16_HELPER_BASE
    helper = HELPER_TOKEN + b"\x00"
    out[helper_at:helper_at + len(helper)] = helper
    allowed.append((helper_at, helper_at + len(helper)))

    out[runtime_file:runtime_file + len(runtime_blob)] = runtime_blob
    allowed.append((runtime_file, runtime_file + len(runtime_blob)))

    w1_file = sb + WALKER1
    w2_file = sb + WALKER2
    tramp_file = sb + DICT_TRAMP
    out[w1_file:w1_file + 5] = far_jmp(h1_ip, RUNTIME_SEG)
    out[w2_file:w2_file + 5] = far_jmp(h2_ip, RUNTIME_SEG)
    out[tramp_file:tramp_file + 6] = far_call(wrapper_ip, RUNTIME_SEG) + b"\xC3"
    allowed += [(w1_file, w1_file + 5), (w2_file, w2_file + 5), (tramp_file, tramp_file + 6)]

    update_ws_checksum(out)
    candidate = bytes(out)
    allowed.append((len(candidate) - 2, len(candidate)))

    if candidate[sb + TARGET] != 0x18:
        raise BuildError("structural 18 was not preserved")
    if candidate[sb + TARGET_TERM:sb + TARGET_NEXT] != b"\x00\x00":
        raise BuildError("terminator/double-NUL changed")
    if candidate[helper_at:helper_at + 5] != helper:
        raise BuildError("portal16 helper write failed")
    if int(ws_header(candidate)["checksum"]) != (sum(candidate[:-2]) & 0xFFFF):
        raise BuildError("WonderSwan checksum invalid")

    runs = diff_runs(parent, candidate)
    unexpected = [r for r in runs if not covered(r, allowed)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:12]}")
    if MAIN.read_bytes() != parent or LIVE_SAVE.read_bytes() != save:
        raise BuildError("main TIP or live SaveRAM changed during build")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_stage4_haman_control18_portal16_probe_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "input": {"main": identity(MAIN, parent), "save": identity(LIVE_SAVE, save)},
        "output": {"rom": identity(OUT_ROM, candidate), "save": identity(OUT_SAVE, save), "checksum": f"{ws_header(candidate)['checksum']:04X}"},
        "target": {
            "address": f"{TARGET:06X}",
            "before": BEFORE_PAYLOAD.hex().upper(),
            "after": AFTER_PAYLOAD.hex().upper(),
            "leading_18_preserved": True,
            "terminator": f"{TARGET_TERM:06X}",
            "double_nul": f"{TARGET_TERM:06X}-{TARGET_NEXT - 1:06X}",
            "helper_index": 0,
            "helper_encoding": "01 01 (base-255 nonzero index 0)",
            "helper": f"27:{PORTAL16_HELPER_BASE:04X} {helper.hex().upper()}",
        },
        "portal16": {
            "magic": PORTAL16_MAGIC.hex().upper(),
            "semantic_usage_on_parent": int(e5_count[PORTAL16_MAGIC[1]]),
            "semantic_usage_by_domain": {kind: int(e5_source.get((PORTAL16_MAGIC[1], kind), 0)) for kind in ("script", "aux", "name75", "native_dictionary", "ext3_phrase")},
            "parameter_encoding": "two nonzero base-255 digits: index=(high-1)*255+(low-1)",
            "helper_bank": "27",
            "helper_base": f"{PORTAL16_HELPER_BASE:04X}",
            "helper_stride": PORTAL16_HELPER_STRIDE,
        },
        "runtime": {
            "segment": f"{RUNTIME_SEG:04X}",
            "cave": f"7E:{RUNTIME_START:04X}-{runtime_end - 1:04X}",
            "bytes": len(runtime_blob),
            "handler1": f"{h1_ip:04X}",
            "handler2": f"{h2_ip:04X}",
            "e51d_common": f"{e51d_ip:04X}",
            "portal16_common": f"{p16_ip:04X}",
            "wrapper": f"{wrapper_ip:04X}",
            "disassembly": disasm,
            "existing_E51D_semantics_retained": True,
        },
        "diff": {"runs": len(runs), "bytes": sum(b - a for a, b in runs), "unexpected_runs": []},
        "checks": {
            "bank27_parent_all_ff": True,
            "new_runtime_parent_all_ff": True,
            "new_runtime_had_no_existing_far_refs": True,
            "structural_18_preserved": True,
            "terminator_double_nul_preserved": True,
            "main_tip_unchanged": MAIN.read_bytes() == parent,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == save,
        },
        "promotion": "blocked; portal16 requires runtime proof before any bulk continuation rewrite",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "candidate": report["output"]["rom"], "save": report["output"]["save"], "target": report["target"], "runtime": {k: v for k, v in report["runtime"].items() if k != "disassembly"}, "diff": report["diff"], "report": rel(OUT_REPORT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
