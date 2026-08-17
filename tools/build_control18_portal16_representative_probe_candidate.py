#!/usr/bin/env python3
"""Build a four-anchor representative probe for the scalable E504 portal16 path.

Anchors:
- 60BB48: user-runtime-proven leading-18 leak in STAGE4.
- 60B449: same STAGE4 area, source-proven continuation followed by 08 0A.
- 6017FC / 601826: historical cases where physically deleting leading 18 merged
  page groups; portal16 keeps the structural 18 and changes storage only.

The parent main is not modified.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_stage4_haman_control18_portal16_probe_candidate as p  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_global_event_runtime_risk_v2 import semantic_e5_usage  # noqa: E402
from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

OUT_ROM = p.PATCH / "control18_portal16_representative_probe_candidate.wsc"
OUT_SAVE = ROOT / "sram/control18_portal16_representative_probe_candidate.sav"
OUT_REPORT = p.PATCH / "control18_portal16_representative_probe_report.json"

TARGETS = [
    {"address": 0x60BB48, "before": "18E518723C01", "capacity": 5, "term": 0x60BB4E, "next": 0x60BB50, "label": "STAGE4 Haman user anchor"},
    {"address": 0x60B449, "before": "18E5189310010101010101", "capacity": 10, "term": 0x60B454, "next": 0x60B456, "label": "STAGE4 immediate 080A control"},
    {"address": 0x6017FC, "before": "18E518B1FE0101010101010101010101010101010101", "capacity": 21, "term": 0x601812, "next": 0x601813, "label": "historical page-merge anchor A"},
    {"address": 0x601826, "before": "18E5183BDF01010101010101010101010101", "capacity": 17, "term": 0x601838, "next": 0x60183A, "label": "historical page-merge anchor B + 0845"},
]


def encode_index(index: int) -> bytes:
    if not 0 <= index < 255 * 255:
        raise p.BuildError(f"portal16 index out of range: {index}")
    return bytes([(index % 255) + 1, (index // 255) + 1])


def main() -> int:
    parent = bytes(load_rom(p.MAIN))
    save = p.LIVE_SAVE.read_bytes()
    if len(parent) != p.ROM_SIZE or p.sha(parent) != p.EXPECTED_MAIN_SHA:
        raise p.BuildError(f"main identity drifted: {p.sha(parent)}")
    if len(save) != p.SAVE_SIZE:
        raise p.BuildError("live SaveRAM size drifted")
    sb = stock_base(parent)

    if parent[sb + p.WALKER1:sb + p.WALKER1 + 5] != p.CURRENT_WALKER1:
        raise p.BuildError("walker1 signature drifted")
    if parent[sb + p.WALKER2:sb + p.WALKER2 + 5] != p.CURRENT_WALKER2:
        raise p.BuildError("walker2 signature drifted")
    if parent[sb + p.DICT_TRAMP:sb + p.DICT_TRAMP + 6] != p.CURRENT_TRAMP:
        raise p.BuildError("dictionary trampoline signature drifted")

    dictionary = make_dictionary_ext3(parent, load_ext_meta(p.EXT_META), load_ext_meta(p.EXT3_META))
    e5_count, e5_source = semantic_e5_usage(parent, dictionary)
    if e5_count[p.PORTAL16_MAGIC[1]] != 0:
        raise p.BuildError("E504 semantic ownership drifted")
    bank27 = parent[p.PORTAL16_BANK << 16:(p.PORTAL16_BANK + 1) << 16]
    if len(bank27) != 0x10000 or any(x != 0xFF for x in bank27):
        raise p.BuildError("bank27 is no longer all-FF")

    runtime_file = sb + (p.RUNTIME_BANK << 16) + p.RUNTIME_START
    if any(x != 0xFF for x in parent[runtime_file:sb + ((p.RUNTIME_BANK + 1) << 16)]):
        raise p.BuildError("bank7E FE20+ cave is no longer all-FF")

    e51d_common = p.build_e51d_common(0)
    p16_common = p.build_portal16_common()
    h1_ip = p.RUNTIME_START
    dummy1 = p.build_handler(h1_ip, 0xFFFF, 0xFFFF, p.WALKER1_EXT3 & 0xFFFF, p.WALKER1_NORMAL & 0xFFFF)
    h2_ip = h1_ip + len(dummy1)
    dummy2 = p.build_handler(h2_ip, 0xFFFF, 0xFFFF, p.WALKER2_EXT3 & 0xFFFF, p.WALKER2_NORMAL & 0xFFFF)
    e51d_ip = h2_ip + len(dummy2)
    p16_ip = e51d_ip + len(e51d_common)
    wrapper_ip = p16_ip + len(p16_common)
    handler1 = p.build_handler(h1_ip, e51d_ip, p16_ip, p.WALKER1_EXT3 & 0xFFFF, p.WALKER1_NORMAL & 0xFFFF)
    handler2 = p.build_handler(h2_ip, e51d_ip, p16_ip, p.WALKER2_EXT3 & 0xFFFF, p.WALKER2_NORMAL & 0xFFFF)
    wrapper = p.build_wrapper(wrapper_ip)
    runtime_blob = handler1 + handler2 + e51d_common + p16_common + wrapper
    runtime_end = p.RUNTIME_START + len(runtime_blob)
    if runtime_end > p.RUNTIME_LIMIT:
        raise p.BuildError("runtime cave overflow")
    disasm = p.disassemble(runtime_blob, p.RUNTIME_START)
    if not disasm or not disasm[-1].startswith(f"{runtime_end - 1:04X}: retf"):
        raise p.BuildError("runtime disassembly boundary mismatch")

    out = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    target_report = []
    for index, row in enumerate(TARGETS):
        address = int(row["address"])
        before = bytes.fromhex(str(row["before"]))
        if parent[sb + address:sb + address + len(before)] != before:
            raise p.BuildError(f"target drift {address:06X}")
        capacity = int(row["capacity"])
        if len(before) != 1 + capacity or before[0] != 0x18 or before[1:3] != b"\xE5\x18":
            raise p.BuildError(f"unexpected target grammar {address:06X}")
        digits = encode_index(index)
        body = p.PORTAL16_MAGIC + digits + b"\x01" * (capacity - 4)
        after = b"\x18" + body
        if len(after) != len(before) or 0 in after:
            raise p.BuildError(f"portal16 body extent/NUL failure {address:06X}")
        out[sb + address:sb + address + len(after)] = after
        allowed.append((sb + address, sb + address + len(after)))

        helper_token = before[1:5]
        helper = helper_token + b"\x00"
        helper_at = (p.PORTAL16_BANK << 16) + p.PORTAL16_HELPER_BASE + index * p.PORTAL16_HELPER_STRIDE
        out[helper_at:helper_at + 5] = helper
        allowed.append((helper_at, helper_at + 5))

        term, nxt = int(row["term"]), int(row["next"])
        if parent[sb + term:sb + nxt] != out[sb + term:sb + nxt]:
            raise p.BuildError(f"separator drift {address:06X}")
        target_report.append({
            "address": f"{address:06X}", "label": row["label"],
            "before": before.hex().upper(), "after": after.hex().upper(),
            "helper_index": index, "helper_digits": digits.hex().upper(),
            "helper": f"27:{p.PORTAL16_HELPER_BASE + index * 5:04X} {helper.hex().upper()}",
            "terminator": f"{term:06X}", "next_address": f"{nxt:06X}",
        })

    # Adjacent fixed-stride helpers merge into one whole-ROM diff run.
    helper_file_start = (p.PORTAL16_BANK << 16) + p.PORTAL16_HELPER_BASE
    allowed.append((helper_file_start, helper_file_start + len(TARGETS) * p.PORTAL16_HELPER_STRIDE))

    out[runtime_file:runtime_file + len(runtime_blob)] = runtime_blob
    allowed.append((runtime_file, runtime_file + len(runtime_blob)))
    w1_file, w2_file, tramp_file = sb + p.WALKER1, sb + p.WALKER2, sb + p.DICT_TRAMP
    out[w1_file:w1_file + 5] = p.far_jmp(h1_ip, p.RUNTIME_SEG)
    out[w2_file:w2_file + 5] = p.far_jmp(h2_ip, p.RUNTIME_SEG)
    out[tramp_file:tramp_file + 6] = p.far_call(wrapper_ip, p.RUNTIME_SEG) + b"\xC3"
    allowed += [(w1_file, w1_file + 5), (w2_file, w2_file + 5), (tramp_file, tramp_file + 6)]

    update_ws_checksum(out)
    candidate = bytes(out)
    allowed.append((len(candidate) - 2, len(candidate)))
    runs = p.diff_runs(parent, candidate)
    unexpected = [r for r in runs if not p.covered(r, allowed)]
    if unexpected:
        raise p.BuildError(f"unexpected diff runs: {unexpected[:12]}")
    if int(ws_header(candidate)["checksum"]) != (sum(candidate[:-2]) & 0xFFFF):
        raise p.BuildError("checksum invalid")
    if p.MAIN.read_bytes() != parent or p.LIVE_SAVE.read_bytes() != save:
        raise p.BuildError("main or live SaveRAM changed")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(p.LIVE_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_control18_portal16_representative_probe_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "input": {"main": p.identity(p.MAIN, parent), "save": p.identity(p.LIVE_SAVE, save)},
        "output": {"rom": p.identity(OUT_ROM, candidate), "save": p.identity(OUT_SAVE, save), "checksum": f"{ws_header(candidate)['checksum']:04X}"},
        "targets": target_report,
        "runtime": {"cave": f"7E:{p.RUNTIME_START:04X}-{runtime_end - 1:04X}", "bytes": len(runtime_blob), "wrapper": f"{wrapper_ip:04X}", "disassembly": disasm},
        "portal16": {"magic": "E504", "semantic_usage_parent": int(e5_count[p.PORTAL16_MAGIC[1]]), "helper_bank": "27", "helper_base": "2000", "stride": 5},
        "diff": {"runs": len(runs), "bytes": sum(b - a for a, b in runs), "unexpected": []},
        "promotion": "blocked pending representative runtime matrix",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "candidate": report["output"]["rom"], "save": report["output"]["save"], "targets": target_report, "runtime": {k: v for k, v in report["runtime"].items() if k != "disassembly"}, "diff": report["diff"], "report": p.rel(OUT_REPORT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
