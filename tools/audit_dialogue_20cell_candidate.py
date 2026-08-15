#!/usr/bin/env python3
from __future__ import annotations

# RETIRED: this module's first-code-unit/prefix heuristic is not a runtime
# contract and must never authorize another build or promotion.  Direct CLI
# use exits with a clear message; imports from historical scripts fail closed.
if __name__ == "__main__":
    from legacy_dialogue_audit_quarantine import cli

    raise SystemExit(cli(__file__))
from legacy_dialogue_audit_quarantine import block

block(__file__)

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from measure_aux_prefix_rule import code_units
from monoeye_rom import Tbl, read_encoded_z_safe, stock_base

ROM = ROOT / "out/patch/dialogue_20cell_candidate.wsc"
QUALITY = ROOT / "out/script/translations_quality_all.json"
REVIEWED = ROOT / "out/script/uncovered_translation_sheet_llm_reviewed.csv"
VOICE = ROOT / "out/script/runtime_text_residual_voice_sheet.csv"
FALSE_PREFIX_A = ROOT / "data/aux_false_prefix_cleanup_ko.json"
FALSE_PREFIX_B = ROOT / "data/battle_dialogue_prefix_cleanup_ko.json"
FALSE_LEAD_SAFE = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
DUPLICATE_LEAD_SAFE = ROOT / "out/script/battle_dialogue_duplicate_lead_stock_rehome_targets.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/dialogue_20cell_width_audit.json"
OUT_CSV = ROOT / "out/script/dialogue_20cell_width_offenders.csv"
LIMIT = 20
SCREEN_PREFIXES = {
    "5D014E": bytes.fromhex("02F191"),
    "5D0211": bytes.fromhex("02F191"),
    "5D03ED": bytes.fromhex("02F191"),
}
# Runtime-screen evidence (2026-08-08) proved that these leading units are
# visible sentence text, not portrait/speaker metadata.  The old blanket
# `first_unit(original)` rule hid exactly the regressions users could see
# (e.g. 5E4F43 `全` and stock-token duplicated first words).  Keep this list
# explicit so future width/residual audits fail on the real rendered text.
RUNTIME_VISIBLE_LEADS = {
    "5D45B5", "5D4DB6", "5D50B0", "5D56F8", "5D83D2", "5D8E92",
    "5D8EE2", "5D94A7",
    "5DAE3F", "5E1947", "5E4F43", "5E4FA7", "5E5016", "5E6590",
    "5E9666", "5E98DA", "5E9F91", "5EA52F", "5EA62A", "5EA659", "5EBE4D",
    "5EC02B",
    # Runtime captures from 2026-08-09 proved these are visible text too:
    # FA29 expands to a duplicated 하만, 82 expands to Japanese 一, and AD
    # expands to 死 in the three identical "死ヌ！ / 死ヌゾォ！" voices.
    "5D4109", "5D5982", "5D5B1F", "5EB389", "5EB3AA",
    "5EAB36", "5EB6B2", "5EC27C",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def visible_lines(text: str) -> list[str]:
    # E62F is the known inline layout separator in battle-voice text.
    parts = text.split("<E62F>")
    return [part.strip("\u3000 ") for part in parts]


def first_unit(payload: bytes) -> bytes:
    units = code_units(payload)
    if not units:
        return b""
    off, size = units[0]
    return payload[:size] if off == 0 and size > 0 else b""


def load_battle_prefixes() -> dict[str, bytes]:
    false: set[str] = set()
    for path in (FALSE_PREFIX_A, FALSE_PREFIX_B):
        doc = json.loads(path.read_text(encoding="utf-8"))
        entries = doc.get("targets") or ([doc.get("record")] if doc.get("record") else [])
        for row in entries:
            if row and row.get("abs"):
                false.add(str(row["abs"]).upper())
    # These rows were proven to begin with visible sentence text. They must be
    # decoded as full text, never stripped as speaker/portrait metadata.
    with FALSE_LEAD_SAFE.open(encoding="utf-8-sig", newline="") as handle:
        false.update(str(row["abs"]).upper() for row in csv.DictReader(handle))
    # The 70-row duplicate-lead ledger was runtime-validated and promoted on
    # 2026-08-08.  Later rebuilds reintroduced 64 of those visible first words;
    # treating them as metadata here would hide the exact recurrence from both
    # width and terminology audits.
    with DUPLICATE_LEAD_SAFE.open(encoding="utf-8-sig", newline="") as handle:
        false.update(str(row["abs"]).upper() for row in csv.DictReader(handle))
    false.update(RUNTIME_VISIBLE_LEADS)
    prefixes: dict[str, bytes] = {}
    with VOICE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            address = str(row.get("record_start") or "").upper()
            if not address or row.get("bank") not in {"5D", "5E"}:
                continue
            if address in false:
                prefixes[address] = b""
                continue
            original = bytes.fromhex(row["original_payload_hex"])
            prefixes[address] = SCREEN_PREFIXES.get(address, first_unit(original))
    return prefixes


def decode(
    rom: bytes,
    dictionary,
    tbl: Tbl,
    address: str,
    *,
    battle_prefixes: dict[str, bytes] | None = None,
) -> tuple[bytes, str]:
    logical = int(address, 16)
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise RuntimeError(f"unreadable candidate record {address}")
    payload = bytes(got[0])
    if battle_prefixes is not None and address.upper() in battle_prefixes:
        prefix = battle_prefixes[address.upper()]
        body = payload[len(prefix):] if prefix and payload.startswith(prefix) else payload
    else:
        _, body, _ = split_prefix_body(payload)
    return payload, strip_pad(dictionary.expand(body, tbl))


def append_row(rows: list[dict[str, Any]], *, address: str, scope: str, source_jp: str, text: str) -> None:
    lines = visible_lines(text)
    lengths = [len(x) for x in lines]
    rows.append({
        "abs": address.upper(),
        "scope": scope,
        "source_jp": source_jp,
        "current_text": text,
        "line_count": len(lines),
        "line_cells": lengths,
        "max_line_cells": max(lengths, default=0),
        "over_20": any(n > LIMIT for n in lengths),
        "lines": lines,
    })


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROM)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--out-csv", type=Path, default=OUT_CSV)
    args = ap.parse_args(argv)
    if not args.rom.is_file():
        raise SystemExit(f"missing ROM: {args.rom}")
    rom = args.rom.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(EXT_META),
        load_ext_meta(EXT3_META),
    )
    rows: list[dict[str, Any]] = []
    battle_prefixes = load_battle_prefixes()

    quality = json.loads(QUALITY.read_text(encoding="utf-8"))
    for src in quality.get("lines") or []:
        address = str(src.get("abs") or "").upper()
        if src.get("kind") != "dialogue" or address[:2] not in {"60", "61", "62", "63"}:
            continue
        if not src.get("ko"):
            continue
        _, text = decode(rom, dictionary, tbl, address)
        append_row(
            rows,
            address=address,
            scope="scenario_60_63",
            source_jp=str(src.get("jp") or ""),
            text=text,
        )

    csv.field_size_limit(10_000_000)
    audited_battle_addresses: set[str] = set()
    with REVIEWED.open(encoding="utf-8-sig", newline="") as h:
        for src in csv.DictReader(h):
            scope = src.get("scope") or ""
            if scope not in {"bank59_event", "battle_voice", "id_indirect_ui"}:
                continue
            address = str(src.get("abs") or "").upper()
            _, text = decode(
                rom,
                dictionary,
                tbl,
                address,
                battle_prefixes=battle_prefixes if scope == "battle_voice" else None,
            )
            append_row(
                rows,
                address=address,
                scope=scope,
                source_jp=src.get("original_jp") or "",
                text=text,
            )
            if scope == "battle_voice":
                audited_battle_addresses.add(address)

    # The reviewed translation sheet contains only a quality-review subset of
    # battle voices.  Width is a runtime/layout property, so restricting this
    # gate to that subset allowed screen-visible overflows (for example
    # 5D3F27, 5D526D, and 5D671A) to pass unnoticed.  Audit every discovered
    # 5D/5E battle record and de-duplicate the rows already covered above.
    with VOICE.open(encoding="utf-8-sig", newline="") as h:
        for src in csv.DictReader(h):
            address = str(src.get("record_start") or "").upper()
            if (
                not address
                or (src.get("bank") or "").upper() not in {"5D", "5E"}
                or address in audited_battle_addresses
            ):
                continue
            _, text = decode(
                rom,
                dictionary,
                tbl,
                address,
                battle_prefixes=battle_prefixes,
            )
            append_row(
                rows,
                address=address,
                scope="battle_voice",
                source_jp=src.get("original_body") or "",
                text=text,
            )
            audited_battle_addresses.add(address)

    offenders = [r for r in rows if r["over_20"]]
    terminology_residuals: list[dict[str, Any]] = []
    for row in rows:
        source = str(row.get("source_jp") or "")
        current = str(row.get("current_text") or "")
        reasons: list[str] = []
        if "ジュド" in source and "주도" in current:
            reasons.append("judau_name_mistransliteration")
        if "キャラ" in source and "캬라" in current:
            reasons.append("chara_name_mistransliteration")
        if "하만하만" in current:
            reasons.append("duplicated_haman")
        if "남무아미타불" in current or "남무아비타불" in current:
            reasons.append("kato_machine_translation_residual")
        if "인되었다" in current:
            reasons.append("cross_record_tail_leak")
        if row["abs"] == "5EB3AA" and any(
            "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff"
            for ch in current
        ):
            reasons.append("visible_japanese_lead")
        if reasons:
            terminology_residuals.append({**row, "reasons": reasons})
    scopes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    dist = Counter()
    for row in rows:
        s = scopes[row["scope"]]
        s["records"] += 1
        s["lines"] += int(row["line_count"])
        s["over_20_records"] += int(row["over_20"])
        s["max_line_cells"] = max(s.get("max_line_cells", 0), int(row["max_line_cells"]))
        for n in row["line_cells"]:
            dist[int(n)] += 1

    # The screenshot-proven Camille line must remain exactly at the hard edge,
    # providing a canonical calibration case for the audit.
    camille = next((r for r in rows if r["abs"] == "6117CA"), None)
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_dialogue_20cell_candidate.py",
        "ok": not offenders and not terminology_residuals,
        "width_ok": not offenders,
        "terminology_ok": not terminology_residuals,
        "rom": {
            "path": str(args.rom.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            "size": len(rom),
            "sha256": sha(rom),
        },
        "line_limit": LIMIT,
        "population": {
            "records": len(rows),
            "lines": sum(int(r["line_count"]) for r in rows),
            "offender_records": len(offenders),
            "max_line_cells": max((int(r["max_line_cells"]) for r in rows), default=0),
            "by_scope": {k: dict(v) for k, v in sorted(scopes.items())},
            "length_distribution": {str(k): dist[k] for k in sorted(dist)},
        },
        "camille_6117ca": camille,
        "offenders": offenders,
        "terminology_residuals": terminology_residuals,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fields = ["abs", "scope", "max_line_cells", "line_cells", "source_jp", "current_text"]
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", encoding="utf-8-sig", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        for r in offenders:
            w.writerow({
                "abs": r["abs"],
                "scope": r["scope"],
                "max_line_cells": r["max_line_cells"],
                "line_cells": "/".join(map(str, r["line_cells"])),
                "source_jp": r["source_jp"],
                "current_text": r["current_text"],
            })
    print(json.dumps(report["population"], ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
