#!/usr/bin/env python3
"""Independent static audit for global_event_native_rehome_220_candidate.wsc."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from dialogue_runtime_contracts import _decode, build_manifest  # noqa: E402
from monoeye_rom import Tbl, load_rom, stock_base, ws_header  # noqa: E402

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/global_event_native_rehome_220_candidate.wsc"
REPORT = ROOT / "out/patch/global_event_native_rehome_220_report.json"
WORKLIST = ROOT / "out/patch/global_event_runtime_risk_priority_worklist.json"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/patch/global_event_native_rehome_220_audit.json"

EXPECTED_MAIN_SHA = "fbd7ad5f36d1248aab27b9a3a1e90b4ef2ec0676567b6bb42b76979e3c9b3260"
EXPECTED_CANDIDATE_SHA = "714200ffdcad34d01c12c8f560b8ca71163c165803e5e9894feb30f523e166c6"
EVENT_MAGIC = bytes.fromhex("E51D")
EXP_SEG = 0x26
PTR_TABLE = 0x2100
DATA_MIN = 0x2200
DATA_MAX = 0x2600

EVENT_ALLOWLIST = [
    (0x643200, 0x643202), (0x64500E, 0x645010), (0x645019, 0x64501B),
    (0x64501D, 0x64501F), (0x64B2B9, 0x64B2BB), (0x651F16, 0x651F18),
    (0x6649C4, 0x6649C6), (0x66A145, 0x66A147), (0x66BB3B, 0x66BB3D),
    (0x66E004, 0x66E006), (0x66F18A, 0x66F18C), (0x673E06, 0x673E08),
    (0x673EA0, 0x673EA2), (0x67AF01, 0x67AF09), (0x67C0EC, 0x67C0F4),
    (0x67EBFB, 0x67EBFD), (0x67EC02, 0x67EC04), (0x67EC83, 0x67EC85),
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def diff_runs(a: bytes, b: bytes, lo: int, hi: int) -> list[tuple[int, int]]:
    out = []
    st = None
    for i in range(lo, hi):
        if a[i] != b[i] and st is None:
            st = i
        elif a[i] == b[i] and st is not None:
            out.append((st, i)); st = None
    if st is not None:
        out.append((st, hi))
    return out


def allowed_event(run: tuple[int, int]) -> bool:
    lo, hi = run
    return any(a <= lo and hi <= b for a, b in EVENT_ALLOWLIST)


def main() -> int:
    original = bytes(load_rom(ORIGINAL))
    main_rom = bytes(load_rom(MAIN))
    cand = bytes(load_rom(CANDIDATE))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []

    def fail(reason: str, **extra: Any) -> None:
        failures.append({"reason": reason, **extra})

    if sha(main_rom) != EXPECTED_MAIN_SHA:
        fail("main_sha", got=sha(main_rom))
    if sha(cand) != EXPECTED_CANDIDATE_SHA:
        fail("candidate_sha", got=sha(cand))
    if len(cand) != 16_777_216:
        fail("candidate_size", got=len(cand))
    if int(ws_header(cand)["checksum"]) != (sum(cand[:-2]) & 0xFFFF):
        fail("checksum")

    sb = stock_base(cand)
    manifest = build_manifest(original, cand, target_path=CANDIDATE)
    by_addr = {r["address"]: r for r in manifest["contracts"]}
    dictionary = make_dictionary_ext3(cand, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    tbl = Tbl.load(TBL_PATH)

    target_rows = report.get("targets") or []
    if len(target_rows) != 220:
        fail("target_count", got=len(target_rows))
    methods = {"native": 0, "param": 0}
    exact4_remaining = []
    render_failures = []
    param_rows = []
    for item in target_rows:
        addr = str(item["address"])
        row = by_addr.get(addr)
        if row is None:
            fail("missing_contract", address=addr)
            continue
        body = bytes.fromhex(str(row["baseline_body_hex"]))
        source = bytes.fromhex(str(row["source_body_hex"]))
        if body.startswith(bytes.fromhex("E518")):
            exact4_remaining.append(addr)
        if item["method"].startswith("event_safe"):
            methods["param"] += 1
            param_rows.append(addr)
            if not (len(body) == 4 and body[:2] == EVENT_MAGIC and body[2] != 0 and body[3] == 1):
                fail("param_body_shape", address=addr, body=body.hex().upper())
        else:
            methods["native"] += 1
            if not (len(body) == 4 and 0xF0 <= body[0] <= 0xFF and 0xF0 <= body[2] <= 0xFF):
                fail("native_body_shape", address=addr, body=body.hex().upper())
        rendered = _decode(dictionary, body, tbl, target=True)
        if rendered != item.get("rendered_text"):
            render_failures.append({"address": addr, "got": rendered, "expected": item.get("rendered_text")})
        if row["source_terminator"] != row["baseline_terminator"]:
            fail("terminator_drift", address=addr)
        sbd = row.get("source_boundary") or {}
        bbd = row.get("baseline_boundary") or {}
        if sbd.get("nul_run") != bbd.get("nul_run") or sbd.get("next_control") != bbd.get("next_control"):
            fail("boundary_drift", address=addr, source=sbd, baseline=bbd)
        if len(source) != 4:
            fail("source_extent_not4", address=addr)

    if methods != {"native": 155, "param": 65}:
        fail("method_split", got=methods)
    if exact4_remaining:
        fail("top_level_E518_remaining", count=len(exact4_remaining), sample=exact4_remaining[:10])
    if render_failures:
        fail("render_mismatch", count=len(render_failures), sample=render_failures[:10])

    # Recompute the original 220-risk signature across the entire manifest.
    exact4_global = []
    control18 = []
    control_adjacent = 0
    for row in manifest["contracts"]:
        body = bytes.fromhex(str(row.get("baseline_body_hex") or ""))
        source = bytes.fromhex(str(row.get("source_body_hex") or ""))
        boundary = row.get("baseline_boundary") or {}
        next_lead = boundary.get("next_lead")
        if body.startswith(bytes.fromhex("E518")) and next_lead in {"08", "17", "18"}:
            control_adjacent += 1
        if (
            len(body) == 4 and body.startswith(bytes.fromhex("E518"))
            and len(source) == 4 and 0xF0 <= source[0] <= 0xFF and 0xF0 <= source[2] <= 0xFF
            and next_lead in {"08", "17"} and boundary.get("nul_run") == 2
        ):
            exact4_global.append(row["address"])
        if (
            len(source) == 5 and source[0] == 0x18 and 0xF0 <= source[1] <= 0xFF and 0xF0 <= source[3] <= 0xFF
            and len(body) == 5 and body[:3] == bytes.fromhex("18E518") and next_lead in {"08", "17"}
        ):
            control18.append(row["address"])
    if exact4_global:
        fail("global_exact4_not_zero", sample=exact4_global[:10])
    if sorted(control18) != ["624305", "6253F6", "6335A6"]:
        fail("control18_set_drift", got=control18)

    # Helper table roundtrip and nested E518 payload validation.
    base26 = EXP_SEG << 16
    helper_fail = []
    for helper in report.get("helpers") or []:
        idx = int(helper["id"])
        ptr_at = base26 + PTR_TABLE + idx * 2
        ptr = cand[ptr_at] | (cand[ptr_at + 1] << 8)
        if not DATA_MIN <= ptr < DATA_MAX:
            helper_fail.append({"id": idx, "reason": "pointer", "ptr": f"{ptr:04X}"})
            continue
        expected = bytes.fromhex(str(helper["nested_ext3"])) + b"\x00"
        got = cand[base26 + ptr:base26 + ptr + len(expected)]
        if got != expected:
            helper_fail.append({"id": idx, "reason": "payload"})
    if helper_fail:
        fail("helper_roundtrip", sample=helper_fail[:10])

    # Promoted STAGE22 fixed E51D body must remain unchanged and decode correctly.
    stage22 = by_addr.get("638CD5")
    if stage22 is None:
        fail("stage22_contract_missing")
    else:
        b = bytes.fromhex(stage22["baseline_body_hex"])
        if b != bytes.fromhex("F191E51D"):
            fail("stage22_body_drift", body=b.hex().upper())
        elif _decode(dictionary, b, tbl, target=True) != "……어？":
            fail("stage22_decode_drift", rendered=_decode(dictionary, b, tbl, target=True))

    # Runtime redirects and fixed-bank tail code identity.
    if cand[sb + 0x7FFDF8:sb + 0x7FFDFD] != bytes.fromhex("EA83FD00E0"):
        fail("walker1_redirect")
    if cand[sb + 0x7FFE4A:sb + 0x7FFE4F] != bytes.fromhex("EAA1FD00E0"):
        fail("walker2_redirect")
    if cand[sb + 0x7AFFED:sb + 0x7AFFF3] != bytes.fromhex("9AE3FD00E0C3"):
        fail("dict_trampoline_redirect")
    runtime_end = 0x7EFD83 + int(report["counts"]["runtime_blob_bytes"])
    if any(x != 0xFF for x in cand[sb + runtime_end:sb + 0x7F0000]):
        fail("bank7e_tail_after_runtime_not_ff")

    # No unknown executable/data change in banks64-69.
    event_unknown = []
    for bank in range(0x64, 0x6A):
        lo, hi = bank << 16, (bank + 1) << 16
        for run in diff_runs(original, cand[sb:], lo, hi):
            if not allowed_event(run):
                event_unknown.append((run[0], run[1]))
    if event_unknown:
        fail("event_bank_unknown_diff", sample=[(f"{a:06X}", f"{b:06X}") for a, b in event_unknown[:10]])

    output = {
        "schema_version": 1,
        "generated_by": "tools/audit_global_event_native_rehome_220_candidate.py",
        "ok": not failures,
        "candidate": {"path": str(CANDIDATE.relative_to(ROOT)), "sha256": sha(cand), "size": len(cand)},
        "counts": {
            "targets": len(target_rows),
            "direct_native_pair": methods["native"],
            "event_safe_parameterized": methods["param"],
            "top_level_exact4_risk_remaining": len(exact4_global),
            "control_adjacent_direct_ext3": control_adjacent,
            "control18_not_in_220": len(control18),
            "render_failures": len(render_failures),
            "event_bank_unknown_diff": len(event_unknown),
            "failures": len(failures),
        },
        "expected_delta": {
            "exact4": "220 -> 0",
            "control_adjacent_direct_ext3": "8591 -> 8371",
            "control18": "3 unchanged; excluded from this 220 batch",
        },
        "stage22_fixed_portal": "PASS" if stage22 is not None and not any(f["reason"].startswith("stage22") for f in failures) else "FAIL",
        "failures": failures,
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": output["ok"], "counts": output["counts"], "stage22": output["stage22_fixed_portal"], "report": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
