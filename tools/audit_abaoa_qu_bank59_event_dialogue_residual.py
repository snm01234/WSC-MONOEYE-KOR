#!/usr/bin/env python3
"""Read-only residual audit for the A Baoa Qu bank59 candidate."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from mixed_residual_classification import japanese_character_count
from monoeye_rom import Tbl, load_rom, stock_base
from normalize_ko_text import normalize_ko_text

ROM = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_candidate.wsc"
WORKLIST = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_worklist.json"
CATALOG = ROOT / "data/abaoa_qu_bank59_event_dialogue_ko.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/abaoa_qu_bank59_event_dialogue_residual_audit.json"
EXPECTED_SHA = "fbfdd1fb231d1e684729db79ed42007c13a48db3c81cab4155fede29bba1b973"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be object: {path}")
    return value


def main() -> int:
    rom = bytes(load_rom(ROM))
    if sha256(rom) != EXPECTED_SHA:
        raise RuntimeError("candidate identity drifted")
    work = load(WORKLIST)
    catalog = load(CATALOG)
    sources = {str(row["abs"]).upper(): row for row in work.get("records") or []}
    targets = {str(row["abs"]).upper(): row for row in catalog.get("lines") or []}
    if set(sources) != set(targets) or len(sources) != 257:
        raise RuntimeError("target set drifted")
    tbl = Tbl.load(TBL)
    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)
    failures: list[dict[str, Any]] = []
    japanese_residuals: list[dict[str, Any]] = []
    for address in sorted(sources, key=lambda value: int(value, 16)):
        source = sources[address]
        logical = int(address, 16)
        prefix_len = len(bytes.fromhex(str(source.get("prefix_hex") or "")))
        payload_capacity = int(source["payload_capacity"])
        payload = rom[sb + logical : sb + logical + payload_capacity]
        rendered = dictionary.expand(payload[prefix_len:], tbl).rstrip("\u3000 \t")
        expected = normalize_ko_text(str(targets[address]["ko"]))
        if rendered != expected:
            failures.append({"abs": address, "expected": expected, "actual": rendered})
        count = japanese_character_count(rendered)
        if count:
            japanese_residuals.append({"abs": address, "rendered": rendered, "count": count})
    counts = {
        "targets_checked": len(sources),
        "exact_targets": len(sources) - len(failures),
        "render_failures": len(failures),
        "japanese_residual_records": len(japanese_residuals),
    }
    ok = not failures and not japanese_residuals
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_abaoa_qu_bank59_event_dialogue_residual.py",
        "read_only": True,
        "ok": ok,
        "rom": {"path": str(ROM.relative_to(ROOT)), "size": len(rom), "sha256": sha256(rom)},
        "scope": {"start": "590244", "end_exclusive": "59265F"},
        "counts": counts,
        "failures": failures,
        "japanese_residuals": japanese_residuals,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": ok, "counts": counts, "out": str(OUT.relative_to(ROOT))}, ensure_ascii=True, indent=2))
    if not ok:
        raise RuntimeError("candidate residual audit failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
