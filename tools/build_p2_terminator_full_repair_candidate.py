#!/usr/bin/env python3
"""Repair all remaining historical P2 +1 terminator moves without new runtime formats.

Parent is the promoted Sig-terminator + cannon TIP.  The historical local-ext3
stage consumed a second structural NUL at 27 records.  611DF0 is already fixed
in the parent after runtime confirmation; this candidate repairs the remaining
26 records.

Every repaired record is restored to its original payload capacity and becomes:
    original prefix | one native F0..FE stock token | 01 | 00 | 00
The first 00 is the original terminator; the second 00 is the structural separator.

Long Korean lines are represented by 11 retired stock wrapper slots whose
pointers never move and whose payloads contain only existing native stock tokens
and/or already-supported Korean glyph bytes.  Other lines use existing exact
native tokens directly.  No ext3 token, FF-page token, runtime hook, stock
pointer rewrite, or expansion-bank write is introduced.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_terminology_retranslation_candidate import stock_storage_proof
from expand_dictionary import iter_dict_indices
from extract_script import split_prefix_body
from mixed_residual_reference_union import build_reference_union
from monoeye_rom import (
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
AUDIT_IN = ROOT / "out/patch/sig_terminator_cannon_postpromotion_terminator_audit.json"
OUT_ROM = ROOT / "out/patch/p2_terminator_full_repair_candidate.wsc"
OUT_SAVE = ROOT / "sram/p2_terminator_full_repair_candidate.sav"
OUT_REPORT = ROOT / "out/patch/p2_terminator_full_repair_report.json"
OUT_TERM_AUDIT = ROOT / "out/patch/p2_terminator_full_repair_terminator_audit.json"

EXPECTED_PARENT = "f9183b7835717ecff033d483bd220f99facc3b7e40a9fb32d5649584b0569145"

# Retired stock wrapper slots.  Payloads were independently minimized using only
# existing stock native tokens plus supported Korean glyph bytes.  Each payload
# is <= the slot's existing physical phrase storage and the slot has no current
# external/native/ext3 consumer in EXPECTED_PARENT.
WRAPPERS = {
    0x05B4: ("부탁한다。", "EC80E7ADEC80E887EC80E7B1F42F"),
    0x0D35: ("훈련　스테이지？", "EC80EA76EC80E8F501FAAB1D"),
    0x0B9D: ("뭐지？", "EC80E7EAF24D1D"),
    0x002A: ("으………", "FEFB02"),
    0x005F: ("음……", "EC80E7DCF191"),
    0x001E: ("이상합니다！", "F3D1F36CF05C"),
    0x0581: ("그런　것이다。", "F171EC80E806F336F42F"),
    0x0EF9: ("해냈군。", "F179EC80E983EC80E7470A"),
    0x0E43: ("속였군！", "EC80E845EC80E8C7EC80E74703"),
    0x0DB0: ("물론이다。", "EC80E85DEC80E85BF053F42F"),
    0x0EF3: ("그건　아니지만。", "F171EC80E7E901F5C5F4180A"),
}

# address -> (target text, native stock slot).  Repeated lines intentionally
# share one wrapper/exact token.
TARGETS = {
    0x605BF5: ("부탁한다。", 0x05B4),
    0x605EB3: ("부탁한다。", 0x05B4),
    0x60659C: ("훈련　스테이지？", 0x0D35),
    0x60AEA9: ("무슨　일이지？", 0x051E),
    0x60C7E9: ("뭐지？", 0x0B9D),
    0x60E6B8: ("으………", 0x002A),
    0x613369: ("예！", 0x068E),
    0x6136A6: ("음……", 0x005F),
    0x61E184: ("그래！", 0x0938),
    0x61E20F: ("그런　것인가。", 0x03C9),
    0x620ADC: ("이상합니다！", 0x001E),
    0x622D74: ("그런　것이다。", 0x0581),
    0x62333D: ("잘　들어라！", 0x0A35),
    0x623E24: ("해냈군。", 0x0EF9),
    0x628524: ("감사합니다。", 0x0308),
    0x629C72: ("감사히　받아두겠습니다。", 0x0936),
    0x62C7B8: ("어떻게　하시겠습니까？", 0x03BD),
    0x63265D: ("음……", 0x005F),
    0x632A06: ("죄송합니다。", 0x06CF),
    0x6332AA: ("속였군！", 0x0E43),
    0x6343D9: ("물론이다。", 0x0DB0),
    0x6343F2: ("물론！", 0x0ACB),
    0x63478C: ("모를　것이다！", 0x08DC),
    0x635866: ("그건　아니지만。", 0x0EF3),
    0x635C0C: ("그건　아니지만。", 0x0EF3),
    0x63AEEE: ("포기하지　마라！", 0x092C),
}

CANNON_EXPECT = {
    0x75C3D3: "메가　캐논　포",
    0x75C7B2: "배부　빔　캐논",
    0x75C7E5: "빔　캐논",
    0x75CBC7: "메가　캐논",
}


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def record(rom: bytes, logical: int):
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=128)
    if got is None:
        raise RuntimeError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1])


def ext3_nested_counts(dictionary, wanted: set[int]) -> dict[int, list[int]]:
    out = {s: [] for s in wanted}
    for parent in range(0x1000, 0x1000 + int(dictionary.ext3_count)):
        try:
            raw = bytes(dictionary.raw_entry(parent))
        except Exception:
            continue
        for child in set(iter_dict_indices(raw)):
            if child in out:
                out[child].append(parent)
    return out


def main() -> int:
    parent = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    live_sav = MAIN_SAVE.read_bytes()
    if sha(parent) != EXPECTED_PARENT:
        raise RuntimeError(f"parent identity drifted: {sha(parent)}")
    if len(TARGETS) != 26:
        raise RuntimeError("target count drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    union = build_reference_union(original, parent, ext_meta=ext_meta, ext3_meta=ext3_meta)
    sb = stock_base(parent)

    # Bind the scope to the postpromotion structural audit: exactly these 26
    # addresses must still be runtime-risk rows; 611DF0 must already be clean.
    audit = json.loads(AUDIT_IN.read_text(encoding="utf-8"))
    risk_rows = {int(r["abs"], 16): r for r in audit.get("rows", []) if r.get("runtime_risk")}
    if set(risk_rows) != set(TARGETS):
        raise RuntimeError(f"risk target set drifted: {sorted(set(risk_rows)^set(TARGETS))}")
    sig_rows = [r for r in audit.get("rows", []) if r.get("abs") == "611DF0"]
    if len(sig_rows) != 1 or sig_rows[0].get("runtime_risk") is not False:
        raise RuntimeError("parent Sig terminator fix is not clean")

    # Wrapper slots must be truly retired in the current runtime.  Their current
    # pointers stay fixed; only bytes inside their private old phrase storage are replaced.
    wrapper_slots = set(WRAPPERS)
    nested3 = ext3_nested_counts(dictionary, wrapper_slots)
    wrapper_proofs = []
    wrapper_payloads = {}
    child_tokens = set()
    for slot, (text, payload_hex) in sorted(WRAPPERS.items()):
        consumers = [c for c in union.consumers_for(slot) if "working" in c.seen_in]
        native_parents = sorted(union.nested_parents.get(slot) or ())
        if consumers or native_parents or nested3[slot]:
            raise RuntimeError(f"wrapper slot {slot:04X} is live")
        proof = stock_storage_proof(dictionary, slot)
        payload = bytes.fromhex(payload_hex)
        if not proof["ok"] or len(payload) > int(proof["old_len"]):
            raise RuntimeError(f"wrapper storage unsafe {slot:04X}: {proof}, need={len(payload)}")
        if dictionary.expand(payload, tbl) != text:
            raise RuntimeError(f"wrapper source expansion mismatch {slot:04X}")
        # Record child native tokens so no wrapper can recursively depend on a slot we overwrite.
        for child in iter_dict_indices(payload):
            if child < 0x0F00:
                child_tokens.add(child)
        wrapper_payloads[slot] = payload
        wrapper_proofs.append({
            "slot": f"{slot:04X}", "text": text, "payload_hex": payload.hex().upper(),
            "payload_len": len(payload), "storage_len": int(proof["old_len"]),
            "ptr": proof["ptr"], "entry_abs": int(proof["entry_abs"]),
        })
    if child_tokens & wrapper_slots:
        raise RuntimeError(f"wrapper dependency cycle risk: {sorted(child_tokens & wrapper_slots)}")

    # Exact/direct tokens must already render exactly before any writes.
    direct_slots = {slot for _a, (_t, slot) in TARGETS.items() if slot not in WRAPPERS}
    for logical, (text, slot) in TARGETS.items():
        if slot in WRAPPERS:
            continue
        got = dictionary.expand_index(slot, tbl)
        if got != text:
            raise RuntimeError(f"direct token {slot:04X} at {logical:06X} renders {got!r}, expected {text!r}")

    candidate = bytearray(parent)
    # In-place wrapper phrase writes, pointer table unchanged.
    for proof in wrapper_proofs:
        slot = int(proof["slot"], 16)
        payload = wrapper_payloads[slot]
        entry = int(proof["entry_abs"])
        old_len = int(proof["storage_len"])
        candidate[entry:entry + len(payload)] = payload
        candidate[entry + len(payload)] = 0
        # Keep inaccessible tail bytes as-is; no other pointer enters this storage.
        if len(payload) > old_len:
            raise RuntimeError("unreachable capacity assertion")

    # Restore each historical boundary and replace the 4-byte ext3 body with one
    # native stock token + one padding byte, exactly fitting the original body span.
    target_rows = []
    for logical, (text, slot) in sorted(TARGETS.items()):
        row = risk_rows[logical]
        old_term = int(row["old_terminator"], 16)
        new_term = int(row["new_terminator"], 16)
        next_start = int(row["next_record_start"], 16)
        if new_term != old_term + 1 or next_start != old_term + 2:
            raise RuntimeError(f"unexpected boundary geometry {logical:06X}")
        payload, term = record(parent, logical)
        if term - sb != new_term:
            raise RuntimeError(f"parent terminator drift {logical:06X}")
        prefix, body, _ = split_prefix_body(payload)
        original_payload_len = old_term - logical
        if original_payload_len - len(prefix) != 3:
            raise RuntimeError(f"original body span is not 3 bytes at {logical:06X}")
        if not body.startswith(bytes.fromhex("E518")) or len(body) != 4:
            raise RuntimeError(f"parent body is not the historical 4-byte ext3 form at {logical:06X}")
        token = bytes(token_from_dict_index(slot))
        if len(token) != 2 or token[0] == 0xFF:
            raise RuntimeError(f"target token is not stock F0..FE at {logical:06X}: {token.hex()}")
        new_payload = prefix + token + b"\x01"
        if len(new_payload) != original_payload_len:
            raise RuntimeError(f"restored payload length mismatch {logical:06X}")
        # Full record + original terminator + structural separator.
        start = sb + logical
        candidate[start:sb + next_start] = new_payload + b"\x00\x00"
        target_rows.append({
            "abs": f"{logical:06X}", "before_payload": payload.hex().upper(),
            "after_payload": new_payload.hex().upper(), "prefix": prefix.hex().upper(),
            "slot": f"{slot:04X}", "target_text": text,
            "old_terminator": f"{old_term:06X}", "separator": f"{new_term:06X}=00",
        })

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)
    final_dict = make_dictionary_ext3(result, ext_meta, ext3_meta)

    # Candidate-bound verification: every repaired record terminates at the old
    # location, has the second NUL, contains no ext3/FF lead, and renders exactly.
    failures = []
    for logical, (text, slot) in sorted(TARGETS.items()):
        row = risk_rows[logical]
        old_term = int(row["old_terminator"], 16)
        new_term = int(row["new_terminator"], 16)
        payload, term = record(result, logical)
        prefix, body, _ = split_prefix_body(payload)
        rendered = final_dict.expand(body, tbl).rstrip("　")
        ok = (
            term - sb == old_term
            and result[sb + new_term] == 0
            and len(body) == 3
            and body[0] in range(0xF0, 0xFF)
            and body[0] != 0xFF
            and b"\xE5\x18" not in body
            and rendered == text
        )
        if not ok:
            failures.append({
                "abs": f"{logical:06X}", "payload": payload.hex().upper(),
                "term": f"{term-sb:06X}", "rendered": rendered, "expected": text,
            })
    if failures:
        raise RuntimeError("target verification failed: " + json.dumps(failures, ensure_ascii=False))

    # Parent-confirmed Sig and cannon fixes must remain exact.
    if result[sb + 0x611DF0:sb + 0x611DF8] != parent[sb + 0x611DF0:sb + 0x611DF8]:
        raise RuntimeError("confirmed Sig 611DF0 fix changed")
    cannon = []
    for logical, expected in CANNON_EXPECT.items():
        payload, _term = record(result, logical)
        _prefix, body, _ = split_prefix_body(payload)
        rendered = final_dict.expand(body, tbl).rstrip("　")
        cannon.append({"abs": f"{logical:06X}", "rendered": rendered, "ok": rendered == expected})
    if not all(r["ok"] for r in cannon):
        raise RuntimeError("cannon regression")

    OUT_ROM.write_bytes(result)
    shutil.copyfile(MAIN_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != live_sav:
        raise RuntimeError("candidate SaveRAM differs from current live SaveRAM")

    # Structural audit must become 0/27 risk.
    subprocess.run([
        sys.executable, str(ROOT / "tools/audit_p2_local_terminator_moves.py"),
        "--target", str(OUT_ROM), "--out", str(OUT_TERM_AUDIT),
    ], cwd=ROOT, check=True)
    term_audit = json.loads(OUT_TERM_AUDIT.read_text(encoding="utf-8"))
    counts = term_audit.get("counts") or {}
    if int(counts.get("runtime_risk", -1)) != 0 or int(counts.get("current_still_expanded", -1)) != 0:
        raise RuntimeError(f"terminator risk remains: {counts}")

    diff_count = sum(a != b for a, b in zip(parent, result))
    report = {
        "ok": True,
        "status": "candidate_static_verified_all_27_p2_terminator_risks_cleared",
        "generated_by": "tools/build_p2_terminator_full_repair_candidate.py",
        "main_tip_modified": False,
        "inputs": {"parent_sha256": sha(parent), "live_sav_sha256": sha(live_sav)},
        "counts": {
            "repaired_remaining_records": 26,
            "total_historical_moves": 27,
            "runtime_risk_after": int(counts.get("runtime_risk", -1)),
            "wrapper_slots": len(WRAPPERS),
            "direct_exact_slots": len(direct_slots),
            "changed_bytes_including_checksum": diff_count,
        },
        "wrapper_proofs": wrapper_proofs,
        "targets": target_rows,
        "terminator_audit": counts,
        "preserved": {
            "sig_611df0_parent_exact": True,
            "cannon_4": cannon,
            "new_runtime_code": 0,
            "stock_pointer_changes": 0,
            "ext3_new_tokens": 0,
            "ff_page_new_tokens": 0,
        },
        "outputs": {
            "rom": str(OUT_ROM), "rom_sha256": sha(result),
            "sav": str(OUT_SAVE), "sav_sha256": sha(live_sav),
            "checksum": f"{checksum:04X}",
            "terminator_audit": str(OUT_TERM_AUDIT),
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True, "candidate": str(OUT_ROM), "sha256": sha(result),
        "checksum": f"{checksum:04X}", "repaired": 26,
        "runtime_risk_after": counts.get("runtime_risk"), "changed_bytes": diff_count,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
