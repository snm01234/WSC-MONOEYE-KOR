#!/usr/bin/env python3
"""Build whole-game Emma Sheen terminology fix on the runtime-approved mixed-exact4 candidate.

Scope:
* Parent ROM is the user-runtime-approved global_scenario_mixed_exact4_59 candidate.
* Replace every direct rendered Korean sequence `엠마` with equal-size `에마`.
* The current parent has exactly five such direct dictionary phrase occurrences.
* No runtime hook, dictionary pointer, record extent, control/portrait bytes, or SaveRAM are changed.
* Active source terminology standard / UI overrides are maintained separately in data/.
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
from monoeye_rom import Tbl, update_ws_checksum, ws_header  # noqa: E402

PATCH = ROOT / "out/patch"
PARENT = PATCH / "global_scenario_mixed_exact4_59_candidate.wsc"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"

OUT_ROM = PATCH / "global_scenario_mixed_exact4_59_ema_candidate.wsc"
OUT_SAVE = ROOT / "sram/global_scenario_mixed_exact4_59_ema_candidate.sav"
OUT_REPORT = PATCH / "global_scenario_mixed_exact4_59_ema_report.json"

PARENT_SHA = "3b5cc0de88874a1138d6336262c8ccc10f844b34d6779b9e7d4bbbabc5b642e7"
MAIN_SHA = "714200ffdcad34d01c12c8f560b8ca71163c165803e5e9894feb30f523e166c6"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Direct Hangul sequence observed in the live dictionary payloads.
# 엠마 = EC8D EA0A E7AF, 에마 = EC8D E74A E7AF.
BAD = bytes.fromhex("EC8DEA0AE7AF")
GOOD = bytes.fromhex("EC8DE74AE7AF")
EXPECTED_HITS = [0x0117765, 0x0117941, 0x0124D46, 0x01D86F9, 0x024E4C6]


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes) -> dict[str, Any]:
    return {"path": rel(path), "size": len(data), "sha256": sha(data)}


def find_all(blob: bytes, needle: bytes) -> list[int]:
    out: list[int] = []
    p = 0
    while True:
        i = blob.find(needle, p)
        if i < 0:
            return out
        out.append(i)
        p = i + 1


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    if len(a) != len(b):
        raise RuntimeError("size mismatch")
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


def main() -> int:
    parent = PARENT.read_bytes()
    main_before = MAIN.read_bytes()
    save_before = LIVE_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != PARENT_SHA:
        raise RuntimeError(f"parent identity drifted: {sha(parent)}")
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise RuntimeError(f"main identity drifted: {sha(main_before)}")
    if len(save_before) != SAVE_SIZE:
        raise RuntimeError(f"live SaveRAM size drifted: {len(save_before)}")

    hits = find_all(parent, BAD)
    if hits != EXPECTED_HITS:
        raise RuntimeError(f"엠마 raw hit set drifted: {[f'{x:07X}' for x in hits]}")
    if find_all(parent, GOOD) == []:
        raise RuntimeError("parent has no known 에마 direct sequence; encoding assumption not independently present")

    out = bytearray(parent)
    for pos in hits:
        out[pos:pos + len(BAD)] = GOOD
    update_ws_checksum(out)
    candidate = bytes(out)

    if find_all(candidate, BAD):
        raise RuntimeError("candidate still contains direct 엠마 sequence")
    expected_good_added = set(hits)
    good_hits_after = set(find_all(candidate, GOOD))
    if not expected_good_added.issubset(good_hits_after):
        raise RuntimeError("not all five positions became 에마")

    # Decode all dictionary/ext3 entries and prove no rendered 엠마 remains.
    tbl = Tbl.load(TBL)
    dictionary = make_dictionary_ext3(candidate, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    before_dict = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    changed_entries: list[dict[str, Any]] = []
    residual_entries: list[dict[str, Any]] = []
    all_indexes = list(range(4096)) + list(range(0x1000, 0x1000 + int(getattr(dictionary, "ext3_count", 0))))
    for index in all_indexes:
        try:
            before = before_dict.expand_index(index, tbl).rstrip("　 ")
            after = dictionary.expand_index(index, tbl).rstrip("　 ")
        except Exception:
            continue
        if "엠마" in after:
            residual_entries.append({"index": f"{index:05X}", "text": after})
        if before != after and ("엠마" in before or "에마" in after):
            changed_entries.append({
                "index": f"{index:05X}",
                "entry_abs": f"{int(dictionary.entry_abs(index)):07X}",
                "before": before,
                "after": after,
            })
    if residual_entries:
        raise RuntimeError(f"rendered 엠마 remains: {residual_entries[:8]}")
    if len(changed_entries) != 5:
        raise RuntimeError(f"expected five changed rendered dictionary entries, got {len(changed_entries)}")

    # Whole-ROM diff may contain five local equal-size glyph edits plus checksum.
    runs = diff_runs(parent, candidate)
    allowed = [(p, p + len(BAD)) for p in hits] + [(len(candidate) - 2, len(candidate))]
    unexpected = []
    for lo, hi in runs:
        if not any(a <= lo and hi <= b for a, b in allowed):
            unexpected.append((lo, hi))
    if unexpected:
        raise RuntimeError(f"unexpected diff runs: {[(hex(a), hex(b)) for a,b in unexpected]}")
    checksum_ok = int(ws_header(candidate)["checksum"]) == (sum(candidate[:-2]) & 0xFFFF)
    if not checksum_ok:
        raise RuntimeError("WonderSwan checksum invalid")
    if MAIN.read_bytes() != main_before or LIVE_SAVE.read_bytes() != save_before:
        raise RuntimeError("main TIP or live SaveRAM changed during build")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_emma_to_ema_global_candidate.py",
        "ok": True,
        "status": "promotion_pending_audits",
        "input": {
            "parent": identity(PARENT, parent),
            "main": identity(MAIN, main_before),
            "live_save": identity(LIVE_SAVE, save_before),
        },
        "output": {
            "rom": identity(OUT_ROM, candidate),
            "save": identity(OUT_SAVE, save_before),
            "checksum": f"{ws_header(candidate)['checksum']:04X}",
        },
        "scope": {
            "requested_name": "엠마 -> 에마",
            "raw_direct_hits": [f"{p:07X}" for p in hits],
            "raw_direct_hit_count": len(hits),
            "changed_dictionary_entries": changed_entries,
            "rendered_residual_엠마": 0,
            "runtime_code_changed": False,
            "dictionary_pointer_changed": False,
            "record_extent_changed": False,
            "control_portrait_bytes_changed": False,
        },
        "diff": {
            "runs": [[f"{a:07X}", f"{b:07X}"] for a, b in runs],
            "bytes": sum(b - a for a, b in runs),
            "unexpected_runs": [],
        },
        "checks": {
            "checksum_valid": checksum_ok,
            "main_tip_unchanged": MAIN.read_bytes() == main_before,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == save_before,
            "all_parent_엠마_direct_sequences_removed": not find_all(candidate, BAD),
            "all_changed_entries_render_에마": all("에마" in row["after"] for row in changed_entries),
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "ok": True,
        "candidate": report["output"]["rom"],
        "save": report["output"]["save"],
        "checksum": report["output"]["checksum"],
        "changed_entries": changed_entries,
        "report": rel(OUT_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
