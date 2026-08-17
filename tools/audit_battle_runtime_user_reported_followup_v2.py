#!/usr/bin/env python3
"""Independent exact audit for the 2026-08-16 battle-runtime follow-up v2.

This intentionally does not use the retired battle-prefix heuristics.  It checks
only address-bound facts proven by Original ROM structure, the reviewed bank5F
catalog, and the two user-reported runtime cases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import detect_ext3_alias_page_count, load_ext_meta, make_dictionary_ext3
from build_broad_stage2_dialogue_voice_candidate import payload_at
from monoeye_rom import Dictionary, Tbl
from normalize_ko_text import normalize_ko_text

PATCH = ROOT / "out/patch"
DEFAULT_TARGET = PATCH / "battle_runtime_user_reported_followup_v2_candidate.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BANK5F_SPEC = ROOT / "data/bank5f_runtime_battle_voice_ko.json"
PLACEHOLDER_CATALOG = ROOT / "data/broad_stage2_placeholder_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
EXPECTED_SIZE = 16_777_216
BANK5F_PREFIXES = {0xA1, 0x9B, 0x8A}
USO = (0x5D2514, 0x5E595C)
USO_PAYLOAD = bytes.fromhex("E518474B010101")
USO_TEXT = "않으면！！"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    target = args.target.read_bytes()
    original = ORIGINAL.read_bytes()
    if len(target) != EXPECTED_SIZE:
        raise SystemExit(f"target size drifted: {len(target)}")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(target, ext_meta, ext3_meta)

    failures: list[dict[str, Any]] = []
    bank5f_rows: list[dict[str, Any]] = []
    spec = {str(k).upper(): dict(v) for k, v in json.loads(BANK5F_SPEC.read_text(encoding="utf-8"))["targets"].items()}
    if len(spec) != 75:
        failures.append({"family": "bank5f", "reason": f"catalog_population:{len(spec)}"})

    compact3 = 0
    for address, row in sorted(spec.items()):
        logical = int(address, 16)
        source_payload, _ = payload_at(original, logical)
        prefix = source_payload[:1] if source_payload and source_payload[0] in BANK5F_PREFIXES else b""
        live, _term = payload_at(target, logical)
        reasons: list[str] = []
        if prefix and not live.startswith(prefix):
            reasons.append(f"prefix:{live[:1].hex().upper()}!={prefix.hex().upper()}")
            body = live
        else:
            body = live[len(prefix):]
        rendered = clean(dictionary.expand(body, tbl))
        expected = normalize_ko_text(str(row["after"]))
        if rendered != expected:
            reasons.append(f"render:{rendered!r}!={expected!r}")
        if b"\xE5\x19" in body:
            compact3 += 1
            reasons.append("compact3_remaining")
        if address not in {"5F044F", "5F047D"}:
            if not body.startswith(b"\xE5\x18"):
                reasons.append("expected_ext3_missing")
            elif (body[2] >> 4) != 9:
                reasons.append(f"ext3_not_page9:{body[2]:02X}{body[3]:02X}")
        if reasons:
            failures.append({"family": "bank5f", "abs": address, "reasons": reasons})
        bank5f_rows.append({
            "abs": address,
            "prefix_hex": prefix.hex().upper(),
            "payload_hex": live.hex().upper(),
            "rendered": rendered,
            "expected": expected,
        })

    uso_rows: list[dict[str, Any]] = []
    for logical in USO:
        live, _term = payload_at(target, logical)
        rendered = clean(dictionary.expand(live, tbl))
        reasons: list[str] = []
        if live != USO_PAYLOAD:
            reasons.append(f"payload:{live.hex().upper()}")
        if rendered != USO_TEXT:
            reasons.append(f"render:{rendered!r}")
        if live.startswith(b"\x9B"):
            reasons.append("visible_9b_reintroduced")
        if reasons:
            failures.append({"family": "uso", "abs": f"{logical:06X}", "reasons": reasons})
        uso_rows.append({"abs": f"{logical:06X}", "payload_hex": live.hex().upper(), "rendered": rendered})

    placeholder_doc = json.loads(PLACEHOLDER_CATALOG.read_text(encoding="utf-8"))
    placeholders = [
        dict(row)
        for row in (placeholder_doc.get("lines") or [])
        if str(row.get("abs") or "").upper().startswith(("5D", "5E"))
    ]
    if len(placeholders) != 66:
        failures.append({"family": "sentinel", "reason": f"population:{len(placeholders)}"})
    sentinel_rows: list[dict[str, Any]] = []
    for row in placeholders:
        address = str(row["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(row.get("prefix_hex") or ""))
        body = bytes.fromhex(str(row.get("body_hex") or ""))
        live, _term = payload_at(target, logical)
        expected = prefix + body
        if live != expected:
            failures.append({
                "family": "sentinel",
                "abs": address,
                "reason": f"payload:{live.hex().upper()}!={expected.hex().upper()}",
            })
        sentinel_rows.append({"abs": address, "payload_hex": live.hex().upper()})

    haman_payload, _ = payload_at(target, 0x5DB482)
    if haman_payload != bytes.fromhex("577981"):
        failures.append({"family": "haman", "abs": "5DB482", "reason": haman_payload.hex().upper()})

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_runtime_user_reported_followup_v2.py",
        "ok": not failures,
        "target": {"path": str(args.target), "size": len(target), "sha256": sha(target)},
        "counts": {
            "bank5f": len(bank5f_rows),
            "bank5f_compact3_remaining": compact3,
            "uso": len(uso_rows),
            "sentinels": len(sentinel_rows),
            "failures": len(failures),
            "alias_pages": detect_ext3_alias_page_count(target),
        },
        "screen_proven_colony_laser": {
            "pattern1": [bank5f_rows[[r["abs"] for r in bank5f_rows].index("5F044F")]["rendered"], bank5f_rows[[r["abs"] for r in bank5f_rows].index("5F0454")]["rendered"]],
            "pattern2": [bank5f_rows[[r["abs"] for r in bank5f_rows].index("5F0463")]["rendered"], bank5f_rows[[r["abs"] for r in bank5f_rows].index("5F046F")]["rendered"]],
            "pattern3": [bank5f_rows[[r["abs"] for r in bank5f_rows].index("5F047D")]["rendered"], bank5f_rows[[r["abs"] for r in bank5f_rows].index("5F0482")]["rendered"]],
        },
        "uso_rows": uso_rows,
        "haman_5DB482_hex": haman_payload.hex().upper(),
        "failures": failures,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
