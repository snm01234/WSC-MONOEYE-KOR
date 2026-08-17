#!/usr/bin/env python3
"""Build the whole-game continuation structural fix candidate.

Parent: promoted main CFB90AAA...

Two disjoint fixes are applied:
1) single-NUL source-visible Japanese `こ` leakage (6 rows): remove the physical
   leading 0x18 and shift the existing translated body left one byte, padding
   the record tail with 0x01. Record extent and terminator remain fixed.
2) double-NUL structural continuation 0x18 + direct E518 storage (2740 rows):
   preserve 0x18. Prefer ordinary native tokens for the two rows that can be
   reproduced exactly; rehome the remaining 2738 rows to the scalable E504
   portal16 path. Helpers are deduplicated fixed-stride records in bank27.

The E504 dispatcher is the representative-probe runtime already validated by
runtime checks on structural 08xx/page-boundary cases. Existing E51D fixed and
parameterized behavior is retained.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_stage4_haman_control18_portal16_probe_candidate as p  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_global_event_runtime_risk_v2 import semantic_e5_usage  # noqa: E402
from monoeye_rom import load_rom, stock_base, update_ws_checksum, ws_header  # noqa: E402

WORKLIST = p.PATCH / "scenario_continuation_control18_storage_worklist.json"
CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
OUT_ROM = p.PATCH / "scenario_continuation_global_structural_fix_candidate.wsc"
OUT_SAVE = ROOT / "sram/scenario_continuation_global_structural_fix_candidate.sav"
OUT_REPORT = p.PATCH / "scenario_continuation_global_structural_fix_report.json"

EXPECTED_STRUCTURAL = 2740
EXPECTED_NATIVE = 1
EXPECTED_PORTAL = 2739
EXPECTED_VISIBLE = 6
EXPECTED_VISIBLE_ADDRS = {"608B55", "60A47A", "60BB48", "6339D9", "63687C", "636B03"}


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
        raise p.BuildError(f"live SaveRAM size drifted: {len(save)}")
    sb = stock_base(parent)

    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    structural_rows = list(work.get("rows") or [])
    if len(structural_rows) != EXPECTED_STRUCTURAL:
        raise p.BuildError(f"structural worklist drifted: {len(structural_rows)}")
    native_rows = [r for r in structural_rows if r.get("strategy") == "ordinary_native"]
    portal_rows = [r for r in structural_rows if r.get("strategy") == "portal16"]
    if len(native_rows) != EXPECTED_NATIVE or len(portal_rows) != EXPECTED_PORTAL:
        raise p.BuildError(f"strategy counts drifted: native={len(native_rows)} portal={len(portal_rows)}")

    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8")).get("contracts") or []
    visible_rows = [r for r in contracts if r.get("visible_source_ko_leak_risk")]
    visible_addrs = {str(r["address"]).upper() for r in visible_rows}
    if len(visible_rows) != EXPECTED_VISIBLE or visible_addrs != EXPECTED_VISIBLE_ADDRS:
        raise p.BuildError(f"visible-こ leak set drifted: {sorted(visible_addrs)}")

    # Parent runtime/cave/semantic identity gates.
    if parent[sb + p.WALKER1:sb + p.WALKER1 + 5] != p.CURRENT_WALKER1:
        raise p.BuildError("walker1 signature drifted")
    if parent[sb + p.WALKER2:sb + p.WALKER2 + 5] != p.CURRENT_WALKER2:
        raise p.BuildError("walker2 signature drifted")
    if parent[sb + p.DICT_TRAMP:sb + p.DICT_TRAMP + 6] != p.CURRENT_TRAMP:
        raise p.BuildError("dictionary trampoline signature drifted")
    dictionary = make_dictionary_ext3(parent, load_ext_meta(p.EXT_META), load_ext_meta(p.EXT3_META))
    e5_count, e5_source = semantic_e5_usage(parent, dictionary)
    if e5_count[p.PORTAL16_MAGIC[1]] != 0:
        raise p.BuildError(f"E504 semantic ownership drifted: {e5_count[p.PORTAL16_MAGIC[1]]}")
    bank27 = parent[p.PORTAL16_BANK << 16:(p.PORTAL16_BANK + 1) << 16]
    if len(bank27) != 0x10000 or any(x != 0xFF for x in bank27):
        raise p.BuildError("bank27 is no longer all-FF")
    runtime_file = sb + (p.RUNTIME_BANK << 16) + p.RUNTIME_START
    if any(x != 0xFF for x in parent[runtime_file:sb + ((p.RUNTIME_BANK + 1) << 16)]):
        raise p.BuildError("bank7E FE20+ cave is no longer all-FF")

    # Build the proven representative runtime dispatcher once.
    h1_ip = p.RUNTIME_START
    e51d_common = p.build_e51d_common(0)
    p16_common = p.build_portal16_common()
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
        raise p.BuildError(f"runtime cave overflow: {runtime_end:04X}")
    disasm = p.disassemble(runtime_blob, p.RUNTIME_START)
    if not disasm or not disasm[-1].startswith(f"{runtime_end - 1:04X}: retf"):
        raise p.BuildError("runtime disassembly boundary mismatch")

    # Deduplicate helpers by the existing four-byte E518 token.
    helper_tokens = sorted({str(r["current_ext3_token"]).upper() for r in portal_rows})
    helper_index = {token: i for i, token in enumerate(helper_tokens)}
    helper_end = p.PORTAL16_HELPER_BASE + len(helper_tokens) * p.PORTAL16_HELPER_STRIDE
    if helper_end > 0x10000:
        raise p.BuildError(f"bank27 helper overflow: {helper_end:04X}")

    out = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    changed_rows: list[dict[str, Any]] = []

    # A) Single-NUL genuine Japanese `こ`: remove only that source glyph.
    for row in visible_rows:
        address = int(str(row["address"]), 16)
        before = bytes.fromhex(str(row["baseline_payload_hex"]))
        if not before.startswith(b"\x18") or len(before) < 2:
            raise p.BuildError(f"visible row grammar drift {address:06X}")
        if parent[sb + address:sb + address + len(before)] != before:
            raise p.BuildError(f"visible row bytes drift {address:06X}")
        after = before[1:] + b"\x01"
        if len(after) != len(before):
            raise p.BuildError(f"visible row extent drift {address:06X}")
        out[sb + address:sb + address + len(after)] = after
        allowed.append((sb + address, sb + address + len(after)))
        changed_rows.append({"address": f"{address:06X}", "class": "single_nul_visible_ko", "strategy": "drop_visible_18", "before": before.hex().upper(), "after": after.hex().upper()})

    # B) Double-NUL structural-18 rows: preserve 18; native when possible.
    for row in native_rows:
        address = int(str(row["address"]), 16)
        capacity = int(row["body_capacity"])
        before_body = bytes.fromhex(str(row["current_body_hex"]))
        before = b"\x18" + before_body
        if parent[sb + address:sb + address + len(before)] != before:
            raise p.BuildError(f"native row bytes drift {address:06X}")
        native_body = bytes.fromhex(str(row["native_body_hex"]))
        if len(native_body) != capacity:
            raise p.BuildError(f"native row capacity drift {address:06X}")
        after = b"\x18" + native_body
        out[sb + address:sb + address + len(after)] = after
        allowed.append((sb + address, sb + address + len(after)))
        changed_rows.append({"address": f"{address:06X}", "class": "double_nul_structural18", "strategy": "ordinary_native", "before": before.hex().upper(), "after": after.hex().upper(), "native_tokens": row.get("native_tokens")})

    # C) Remaining structural rows: E504 portal16, preserving 18.
    for row in portal_rows:
        address = int(str(row["address"]), 16)
        capacity = int(row["body_capacity"])
        before_body = bytes.fromhex(str(row["current_body_hex"]))
        before = b"\x18" + before_body
        if parent[sb + address:sb + address + len(before)] != before:
            raise p.BuildError(f"portal row bytes drift {address:06X}")
        token = str(row["current_ext3_token"]).upper()
        idx = helper_index[token]
        digits = encode_index(idx)
        body = p.PORTAL16_MAGIC + digits + b"\x01" * (capacity - 4)
        if capacity < 4 or len(body) != capacity or 0 in body:
            raise p.BuildError(f"portal body invalid {address:06X}")
        after = b"\x18" + body
        out[sb + address:sb + address + len(after)] = after
        allowed.append((sb + address, sb + address + len(after)))
        changed_rows.append({"address": f"{address:06X}", "class": "double_nul_structural18", "strategy": "portal16", "before": before.hex().upper(), "after": after.hex().upper(), "helper_index": idx, "helper_digits": digits.hex().upper(), "ext3_token": token})

    # Helpers: one fixed-stride E518 phrase + NUL for each unique token.
    helper_file_start = (p.PORTAL16_BANK << 16) + p.PORTAL16_HELPER_BASE
    for token, idx in helper_index.items():
        helper = bytes.fromhex(token) + b"\x00"
        at = helper_file_start + idx * p.PORTAL16_HELPER_STRIDE
        out[at:at + 5] = helper
    if helper_tokens:
        allowed.append((helper_file_start, helper_file_start + len(helper_tokens) * p.PORTAL16_HELPER_STRIDE))

    # Runtime dispatcher and trampolines.
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

    if int(ws_header(candidate)["checksum"]) != (sum(candidate[:-2]) & 0xFFFF):
        raise p.BuildError("WonderSwan checksum invalid")
    runs = p.diff_runs(parent, candidate)
    unexpected = [r for r in runs if not p.covered(r, allowed)]
    if unexpected:
        raise p.BuildError(f"unexpected diff runs: {unexpected[:12]}")
    if p.MAIN.read_bytes() != parent or p.LIVE_SAVE.read_bytes() != save:
        raise p.BuildError("main TIP or live SaveRAM changed during build")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(p.LIVE_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scenario_continuation_global_structural_fix_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "input": {"main": p.identity(p.MAIN, parent), "save": p.identity(p.LIVE_SAVE, save)},
        "output": {"rom": p.identity(OUT_ROM, candidate), "save": p.identity(OUT_SAVE, save), "checksum": f"{ws_header(candidate)['checksum']:04X}"},
        "counts": {
            "single_nul_visible_ko_removed": len(visible_rows),
            "double_nul_structural18_total": len(structural_rows),
            "ordinary_native": len(native_rows),
            "portal16": len(portal_rows),
            "portal16_unique_helpers": len(helper_tokens),
            "portal16_helper_bytes": len(helper_tokens) * p.PORTAL16_HELPER_STRIDE,
        },
        "runtime": {"cave": f"7E:{p.RUNTIME_START:04X}-{runtime_end - 1:04X}", "bytes": len(runtime_blob), "wrapper": f"{wrapper_ip:04X}", "existing_E51D_semantics_retained": True, "disassembly": disasm},
        "portal16": {"magic": "E504", "semantic_usage_parent": int(e5_count[p.PORTAL16_MAGIC[1]]), "semantic_usage_by_domain": {kind: int(e5_source.get((p.PORTAL16_MAGIC[1], kind), 0)) for kind in ("script", "aux", "name75", "native_dictionary", "ext3_phrase")}, "helper_bank": "27", "helper_base": "2000", "stride": 5, "helper_end_exclusive": f"{helper_end:04X}"},
        "diff": {"runs": len(runs), "bytes": sum(b - a for a, b in runs), "unexpected": []},
        "visible_addresses": sorted(visible_addrs),
        "native_addresses": sorted(str(r["address"]) for r in native_rows),
        "changed_rows": changed_rows,
        "promotion": "blocked pending representative runtime sheet",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "output": report["output"], "counts": report["counts"], "runtime": {k: v for k, v in report["runtime"].items() if k != "disassembly"}, "portal16": report["portal16"], "diff": report["diff"], "report": p.rel(OUT_REPORT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
