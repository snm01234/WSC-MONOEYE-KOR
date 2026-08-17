#!/usr/bin/env python3
"""Build P1 event-safe native-pair batch01 candidate.

Scope is intentionally tiny: two identical scenario-first `도몬……` records
at 62:56CC and 62:5730.  Both are exact4 P1 risks:

    current body : E5 18 19 99
    source body  : F5 89 F1 91

On the current promoted main dictionary, F589 renders `도몬` and F191 renders
`……`, so restoring the exact source body preserves Korean text while recovering
the Original native two-token grammar.  Prefix, extent, double-NUL separator,
and following 17 28 01 06 control bytes are all preserved byte-exact.

Runtime-test candidate only.  Main TIP and live SaveRAM are never modified.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
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
OUT_ROM = PATCH / "p1_event_native_pair_batch01_candidate.wsc"
OUT_SAVE = ROOT / "sram/p1_event_native_pair_batch01_candidate.sav"
OUT_REPORT = PATCH / "p1_event_native_pair_batch01_report.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

MAIN_SHA = "fbd7ad5f36d1248aab27b9a3a1e90b4ef2ec0676567b6bb42b76979e3c9b3260"
ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
PREFIX = bytes.fromhex("173418")
CURRENT_BODY = bytes.fromhex("E5181999")
SOURCE_BODY = bytes.fromhex("F589F191")
CONTROL = bytes.fromhex("17280106")
TARGETS = (0x6256CC, 0x625730)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha(payload)}


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    if len(a) != len(b):
        raise BuildError("ROM size mismatch")
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(a)))
    return out


def covered(run: tuple[int, int], allow: list[tuple[int, int]]) -> bool:
    a, b = run
    return any(lo <= a and b <= hi for lo, hi in allow)


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_before = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != MAIN_SHA:
        raise BuildError("promoted main identity drifted")
    if sha(original) != ORIGINAL_SHA:
        raise BuildError("original identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing or wrong size")

    sb = stock_base(parent)
    dictionary = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    tbl = Tbl.load(TBL_PATH)
    rendered = dictionary.expand(SOURCE_BODY, tbl)
    if rendered != "도몬……":
        raise BuildError(f"source native pair no longer renders target Korean: {rendered!r}")
    if dictionary.expand(token_from_dict_index(0x0589), tbl) != "도몬":
        raise BuildError("F589 dictionary meaning drifted")
    if dictionary.expand(token_from_dict_index(0x0191), tbl) != "……":
        raise BuildError("F191 dictionary meaning drifted")

    target_rows: list[dict[str, Any]] = []
    out = bytearray(parent)
    allow: list[tuple[int, int]] = []
    for logical in TARGETS:
        full = PREFIX + CURRENT_BODY
        source_full = PREFIX + SOURCE_BODY
        at = sb + logical
        if parent[at:at + len(full)] != full:
            raise BuildError(f"current payload drift at {logical:06X}")
        if original[logical:logical + len(source_full)] != source_full:
            raise BuildError(f"Original payload drift at {logical:06X}")
        term = logical + len(full)
        # Exact double-NUL separator followed by the runtime-sensitive control row.
        parent_tail = parent[sb + term:sb + term + 2 + len(CONTROL)]
        source_tail = original[term:term + 2 + len(CONTROL)]
        expected_tail = b"\x00\x00" + CONTROL
        if parent_tail != expected_tail or source_tail != expected_tail:
            raise BuildError(f"boundary/control drift at {logical:06X}")

        body_at = at + len(PREFIX)
        out[body_at:body_at + len(SOURCE_BODY)] = SOURCE_BODY
        allow.append((body_at, body_at + len(SOURCE_BODY)))
        target_rows.append({
            "address": f"{logical:06X}",
            "prefix_hex": PREFIX.hex().upper(),
            "main_body_hex": CURRENT_BODY.hex().upper(),
            "candidate_body_hex": SOURCE_BODY.hex().upper(),
            "original_body_hex": SOURCE_BODY.hex().upper(),
            "rendered": rendered,
            "terminator": f"{term:06X}",
            "separator": f"{term:06X}-{term + 1:06X}=00 00",
            "following_control": f"{term + 2:06X}=17 28 01 06",
        })

    update_ws_checksum(out)
    candidate = bytes(out)
    allow.append((len(candidate) - 2, len(candidate)))

    # Exact target and surrounding boundary verification.
    for logical in TARGETS:
        at = sb + logical
        term = logical + len(PREFIX) + len(SOURCE_BODY)
        if candidate[at:at + 7] != PREFIX + SOURCE_BODY:
            raise BuildError(f"candidate target mismatch at {logical:06X}")
        if candidate[sb + term:sb + term + 2 + len(CONTROL)] != b"\x00\x00" + CONTROL:
            raise BuildError(f"candidate boundary/control changed at {logical:06X}")
        # Control/non-body neighborhood must be byte-exact to parent except body.
        if candidate[sb + term:sb + term + 0x10] != parent[sb + term:sb + term + 0x10]:
            raise BuildError(f"post-target bytes changed at {logical:06X}")

    runs = diff_runs(parent, candidate)
    unexpected = [r for r in runs if not covered(r, allow)]
    if unexpected:
        raise BuildError(f"unexpected diff runs: {unexpected[:8]}")
    checksum_ok = int(ws_header(candidate)["checksum"]) == (sum(candidate[:-2]) & 0xFFFF)
    if not checksum_ok:
        raise BuildError("WonderSwan checksum invalid")
    if bytes(load_rom(MAIN)) != parent or LIVE_SAVE.read_bytes() != save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_p1_event_native_pair_batch01_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "input": {
            "main": identity(MAIN, parent),
            "original": identity(ORIGINAL, original),
            "save": identity(LIVE_SAVE, save_before),
        },
        "output": {
            "rom": identity(OUT_ROM, candidate),
            "save": identity(OUT_SAVE, save_before),
            "checksum": f"{ws_header(candidate)['checksum']:04X}",
        },
        "strategy": {
            "class": "P1 exact4 source-two-native -> direct-ext3 recovery",
            "targets": len(TARGETS),
            "new_portals": 0,
            "dictionary_reclaims": 0,
            "dictionary_writes": 0,
            "runtime_hook_changes": 0,
            "event_control_changes": 0,
            "body_strategy": "restore Original body bytes F589 F191; current dictionary already renders them as 도몬……",
        },
        "targets": target_rows,
        "diff": {
            "runs": len(runs),
            "changed_bytes": sum(b - a for a, b in runs),
            "unexpected_runs": [],
        },
        "checks": {
            "source_native_body_restored_exact": True,
            "Korean_render_preserved": rendered == "도몬……",
            "prefixes_preserved": True,
            "record_extents_preserved": True,
            "double_nul_preserved": True,
            "following_17280106_preserved": True,
            "main_tip_unchanged": bytes(load_rom(MAIN)) == parent,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == save_before,
            "checksum_valid": checksum_ok,
        },
        "promotion": "blocked_pending_user_runtime_verification",
        "test_protocol": [
            "Use p1_event_native_pair_batch01_candidate.wsc with the paired SaveRAM.",
            "In the local scenario around 62:56CC, confirm `도몬……` renders normally and the event continues through the following 17 28 01 06 control row.",
            "In the second nearby occurrence around 62:5730, confirm the same `도몬……` line and following event progression are normal.",
            "Confirm no control glyph leakage, portrait corruption, replay, early event termination, or Event Error occurs.",
        ],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "ok": True,
        "candidate": report["output"]["rom"],
        "save": report["output"]["save"],
        "checksum": report["output"]["checksum"],
        "targets": report["targets"],
        "diff": report["diff"],
        "report": rel(OUT_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
