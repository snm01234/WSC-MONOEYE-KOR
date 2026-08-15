#!/usr/bin/env python3
"""
Repair sole reclaim mode1 (owner_ko) overwrites of shared dictionary slots.

Root cause: sole_reclaim only scanned script banks 60–6F + name75, so slots
still referenced by battle/UI dialogue in banks 50–5F looked "sole". Overwriting
those stock fragments (e.g. dict[21]=86bb) made mid-game UI show unrelated KO
lines such as 617F4F「전체의　２～３할　정도일까。」.

This restores:
  1) dictionary payloads from an 8MB pointer-ref ROM for unsafe indices
  2) owner script records at the reported abs (token+SPACE pad → original JP)
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_safe_unit import read_record_at  # noqa: E402
from apply_sole_reclaim_early import _sole_dialogue_owner  # noqa: E402
from expand_dictionary import (  # noqa: E402
    build_dict_token_locs,
    write_dictionary_slots_spill,
)
from monoeye_rom import (  # noqa: E402
    load_rom,
    slice_bank,
    stock_base,
    update_ws_checksum,
)


def _slot_payload(rom: bytes | bytearray, idx: int) -> bytes:
    b5f = slice_bank(rom, 0x5F)
    ptr = struct.unpack_from("<H", b5f, 0x7BCC + idx * 2)[0]
    end = b5f.find(b"\x00", ptr)
    if end < 0:
        raise ValueError(f"dict[{idx}] missing NUL at ptr={ptr:04X}")
    return bytes(b5f[ptr:end])


def _collect_owner_ko(reports_dir: Path) -> List[dict]:
    out: List[dict] = []
    for path in sorted(reports_dir.glob("sole_reclaim*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data.get("applied") or []:
            if entry.get("mode") == "owner_ko" and entry.get("ok"):
                row = dict(entry)
                row["report"] = path.name
                out.append(row)
    return out


def find_unsafe_indices(
    base_rom: bytes | bytearray, owner_ko: List[dict]
) -> List[Tuple[int, str, int]]:
    """Return (dict_index, owner_abs_hex, ref_count) for non-sole slots."""
    locs = build_dict_token_locs(
        base_rom, regions=("script", "name75", "aux")
    )
    unsafe: List[Tuple[int, str, int]] = []
    seen: Set[int] = set()
    for entry in owner_ko:
        idx = int(entry["dict_index"])
        if idx in seen:
            continue
        refs = locs.get(idx, [])
        if len(refs) == 1 and _sole_dialogue_owner(refs) is not None:
            continue
        seen.add(idx)
        unsafe.append((idx, str(entry["abs"]), len(refs)))
    unsafe.sort(key=lambda t: t[0])
    return unsafe


def repair(
    rom: bytearray,
    base_rom: bytes,
    *,
    reports_dir: Path,
) -> dict:
    owner_ko = _collect_owner_ko(reports_dir)
    unsafe = find_unsafe_indices(base_rom, owner_ko)
    idx_to_owners: Dict[int, List[int]] = {}
    for entry in owner_ko:
        idx = int(entry["dict_index"])
        idx_to_owners.setdefault(idx, []).append(int(entry["abs"], 16))

    slot_payload: Dict[int, bytes] = {}
    restored_slots: List[dict] = []
    for idx, abs_hex, refs in unsafe:
        tip_payload = _slot_payload(rom, idx)
        base_payload = _slot_payload(base_rom, idx)
        if tip_payload == base_payload:
            continue
        slot_payload[idx] = base_payload
        restored_slots.append(
            {
                "dict_index": idx,
                "owner_abs": abs_hex,
                "refs": refs,
                "tip_len": len(tip_payload),
                "base_len": len(base_payload),
            }
        )

    spill_end = None
    if slot_payload:
        _ptrs, spill_end = write_dictionary_slots_spill(
            rom, slot_payload, allow_aux_consumers=True
        )

    restored_scripts: List[dict] = []
    sb = stock_base(rom)
    for idx, *_rest in unsafe:
        if idx not in slot_payload:
            continue
        for abs_off in idx_to_owners.get(idx, []):
            try:
                tip_rec = read_record_at(rom, abs_off)
                base_rec = read_record_at(base_rom, abs_off)
            except Exception as exc:
                restored_scripts.append(
                    {
                        "abs": f"{abs_off:06X}",
                        "dict_index": idx,
                        "ok": False,
                        "error": str(exc),
                    }
                )
                continue
            if tip_rec == base_rec:
                continue
            if len(base_rec) > len(tip_rec):
                # padded_token keeps length; refuse expand into next record.
                restored_scripts.append(
                    {
                        "abs": f"{abs_off:06X}",
                        "dict_index": idx,
                        "ok": False,
                        "error": "base_record_longer",
                        "tip_len": len(tip_rec),
                        "base_len": len(base_rec),
                    }
                )
                continue
            # SPACE pad (not NUL) — early 00 splits sequential dialogue walk.
            foff = sb + abs_off
            pad = bytes([0x01] * (len(tip_rec) - len(base_rec)))
            rom[foff : foff + len(tip_rec)] = base_rec + pad
            restored_scripts.append(
                {
                    "abs": f"{abs_off:06X}",
                    "dict_index": idx,
                    "ok": True,
                    "tip_len": len(tip_rec),
                    "base_len": len(base_rec),
                }
            )

    return {
        "owner_ko_reports": len(owner_ko),
        "unsafe_indices": len(unsafe),
        "slots_restored": len(restored_slots),
        "scripts_restored": sum(1 for r in restored_scripts if r.get("ok")),
        "script_restore_fail": sum(1 for r in restored_scripts if not r.get("ok")),
        "spill_end": spill_end,
        "restored_slots": restored_slots,
        "restored_scripts": restored_scripts,
        "checksum": f"{update_ws_checksum(rom):04X}",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded_8mb.wsc",
        help="pre-sole pointer/content reference (8MB tip)",
    )
    ap.add_argument(
        "--reports-dir",
        type=Path,
        default=ROOT / "out" / "patch",
    )
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=None,
        help="default: overwrite --rom",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out" / "patch" / "repair_unsafe_sole_owner_ko_report.json",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rom = bytearray(load_rom(args.rom))
    base = load_rom(args.base_rom)
    report = repair(rom, base, reports_dir=args.reports_dir)
    report["rom"] = str(args.rom)
    report["base_rom"] = str(args.base_rom)
    report["dry_run"] = bool(args.dry_run)

    args.out_report.parent.mkdir(parents=True, exist_ok=True)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.dry_run:
        out = args.out_rom or args.rom
        out.write_bytes(rom)
        print(f"wrote {out}")
    print(
        f"unsafe={report['unsafe_indices']} slots={report['slots_restored']} "
        f"scripts={report['scripts_restored']} fail={report['script_restore_fail']} "
        f"checksum={report['checksum']}"
    )
    print(f"report {args.out_report}")


if __name__ == "__main__":
    main()
