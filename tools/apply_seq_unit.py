#!/usr/bin/env python3
"""
Sequential substitution + sticky-marker compression unit.

Feasibility (2026-07-14):
  - Phrase→inplace without new dict slots: ~0–3% fit (not primary).
  - Dict-token sequential: works but free slots = 0 (hard stop).
  - Sticky Hangul marker (run-length): halves marker overhead; enables denser
    dict spill and cheaper shift payloads.
  - Capacity-limited in-bank SHIFT on non-seed banks: the practical sequential
    expansion path when bodies must grow (pointer-patched).

This tool:
  1) Upgrades the Hangul store cave to sticky mode
  2) Optionally densifies existing Hangul dict payloads to sticky encoding
  3) Applies capacity-limited shift replacements (sticky KO) on selected banks
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from expand_dictionary import write_dictionary_slots_spill  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    MAX_SAFE_RECORD_LEN,
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z,
    read_encoded_z_safe,
    update_ws_checksum,
)
from normalize_ko_text import (  # noqa: E402
    encode_ko_text,
    hangul_count,
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)
from patch_font_hangul_hook import upgrade_store_cave_sticky  # noqa: E402
from rebuild_script_banks import (  # noqa: E402
    filter_replacements_to_bank_capacity,
    shift_replacements_in_text_banks,
)


def densify_hangul_dict_slots(
    rom: bytearray,
    tbl: Tbl,
    *,
    marker: int,
    protect_indices: Set[int] | None = None,
) -> Dict[int, bytes]:
    """Rewrite Hangul dict entries to sticky-marker encoding (same expand text)."""
    d = Dictionary(rom)
    protect = protect_indices or set()
    payload: Dict[int, bytes] = {}
    for idx in range(d.count):
        if idx in protect:
            continue
        raw = bytes(d.raw_entry(idx))
        if not raw or raw[0:2] != bytes([(marker >> 8) & 0xFF, marker & 0xFF]):
            # Also catch entries that use markers mid-stream.
            if b"\xE3\xDB" not in raw:
                continue
        plain = d.expand_index(idx, tbl)
        if hangul_count(plain) < 1:
            continue
        sticky = encode_ko_text(
            plain, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if sticky != raw and d.expand(sticky, tbl) == plain:
            payload[idx] = sticky
    return payload


def plan_inplace_sticky(
    rom: bytes | bytearray,
    tbl: Tbl,
    lines_by_abs: Dict[int, dict],
    *,
    marker: int,
    seed_abs: Set[int],
    banks: Set[int] | None,
    max_lines: int,
) -> Dict[int, bytes]:
    """Size-preserving body rewrites where sticky KO fits in the original body."""
    d = Dictionary(rom)
    out: Dict[int, bytes] = {}
    for abs_off, line in sorted(lines_by_abs.items()):
        if max_lines > 0 and len(out) >= max_lines:
            break
        if abs_off in seed_abs:
            continue
        if banks is not None and (abs_off >> 16) not in banks:
            continue
        ko = normalize_ko_text(line.get("ko") or "")
        if not ko or is_low_quality_ko(ko):
            continue
        sticky = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if sticky is None:
            continue
        try:
            got = read_encoded_z_safe(rom, abs_off)
            if got is None:
                continue
            original, _ = got
            if len(original) > MAX_SAFE_RECORD_LEN:
                continue
            prefix, body, _ = split_prefix_body(original)
            if len(body) < 1 or len(sticky) < 1 or len(sticky) > len(body):
                continue
            # Refuse large zero-pads: false "records" into binary look huge.
            if len(body) - len(sticky) > 32:
                continue
            each = encode_ko_text(
                ko, tbl, hangul_marker_code=marker, hangul_marker_mode="each"
            )
            if d.expand(body, tbl) == d.expand(each, tbl):
                continue
            new_body = sticky + (b"\x01" * (len(body) - len(sticky)))
            new_payload = bytes(prefix) + new_body
            if len(new_payload) != len(original):
                continue
            out[abs_off] = new_payload
        except Exception:
            continue
    return out


def plan_shift_replacements(
    rom: bytes | bytearray,
    tbl: Tbl,
    lines_by_abs: Dict[int, dict],
    *,
    marker: int,
    banks: Set[int],
    seed_abs: Set[int],
    max_lines: int,
) -> Tuple[Dict[int, bytes], List[int]]:
    """
    Build prefix+stickyKO replacements for not-yet-matching quality lines.
    Prefer earlier abs within each bank (opening/dialogue head first).
    """
    d = Dictionary(rom)
    candidates: List[Tuple[int, bytes, int]] = []  # abs, payload, growth
    for abs_off, line in lines_by_abs.items():
        if abs_off in seed_abs:
            continue
        seg = abs_off >> 16
        if seg not in banks:
            continue
        ko = normalize_ko_text(line.get("ko") or "")
        if not ko or is_low_quality_ko(ko):
            continue
        sticky = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if sticky is None:
            continue
        try:
            got = read_encoded_z_safe(rom, abs_off)
            if got is None:
                continue
            original, _ = got
            prefix, body, _ = split_prefix_body(original)
            each = encode_ko_text(
                ko, tbl, hangul_marker_code=marker, hangul_marker_mode="each"
            )
            if d.expand(body, tbl) == d.expand(each, tbl):
                continue
            new_payload = bytes(prefix) + sticky
            growth = len(new_payload) - len(original)
            candidates.append((abs_off, new_payload, growth))
        except Exception:
            continue

    candidates.sort(key=lambda t: (t[0], t[2]))
    if max_lines > 0:
        candidates = candidates[:max_lines]

    replacements = {abs_off: payload for abs_off, payload, _ in candidates}
    flexible = set(replacements)
    kept, dropped = filter_replacements_to_bank_capacity(rom, replacements, flexible)
    return kept, dropped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_patch_pad3.tbl",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out" / "script" / "translations_full.json",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data" / "translations_seed_hook96.json",
    )
    ap.add_argument(
        "--map",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_char_map.json",
    )
    ap.add_argument("--hangul-marker", default="E3DB")
    ap.add_argument(
        "--banks",
        default="61-63",
        help="Hex bank list/ranges, e.g. 61-63,68,69 (seed bank 60 avoided by default)",
    )
    ap.add_argument("--max-lines", type=int, default=400)
    ap.add_argument("--skip-densify", action="store_true")
    ap.add_argument(
        "--skip-shift",
        action="store_true",
        default=True,
        help="Skip full-bank shift (default: on — abs-unstable)",
    )
    ap.add_argument(
        "--allow-shift",
        action="store_true",
        help="Enable capacity-limited full-bank shift (moves all abs in bank)",
    )
    ap.add_argument(
        "--inplace-banks",
        default="60-63",
        help="Banks for sticky inplace (size-preserving). 'all' or e.g. 60-63",
    )
    ap.add_argument("--max-inplace", type=int, default=500)
    ap.add_argument("--skip-inplace", action="store_true")
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out" / "patch" / "seq_compress_report.json",
    )
    args = ap.parse_args()
    if args.allow_shift:
        args.skip_shift = False

    marker = int(args.hangul_marker, 16)
    rom = bytearray(load_rom(args.rom))
    tbl = Tbl.load(args.tbl)
    mapping = json.loads(args.map.read_text(encoding="utf-8"))
    pad = mapping.get("padding_store") or {}
    base_index = int(pad["base_code"], 16) - 0xDF20
    count = int(pad["count"])

    report: dict = {
        "sticky_upgrade": None,
        "dict_densified": 0,
        "bytes_saved_dict": 0,
        "inplace_patched": 0,
        "shift": None,
        "dropped_capacity": 0,
        "notes": [],
    }

    # 1) Sticky store cave
    sticky = upgrade_store_cave_sticky(rom, base_index=base_index, count=count)
    report["sticky_upgrade"] = sticky
    report["notes"].append("Store cave upgraded to sticky Hangul-run tagging.")

    seed_abs = {
        int(row["abs"], 16)
        for row in json.loads(args.seed.read_text(encoding="utf-8")).get("lines", [])
    }

    # 2) Densify existing Hangul dict payloads (never touch seed-referenced slots —
    # opening titles use each-marker encoding; sticky densify made first Hangul
    # runs look Japanese when tagging is imperfect).
    protect: Set[int] = set()
    d_protect = Dictionary(rom)
    for abs_off in seed_abs:
        try:
            payload, _ = read_encoded_z(rom, abs_off)
            _p, body, _ = split_prefix_body(payload)
            if len(body) >= 2 and 0xF0 <= body[0] <= 0xFF:
                protect.add(((body[0] - 0xF0) << 8) | body[1])
        except Exception:
            continue

    if not args.skip_densify:
        d0 = Dictionary(rom)
        before = 0
        slot_payload = densify_hangul_dict_slots(
            rom, tbl, marker=marker, protect_indices=protect
        )
        for idx, encoded in slot_payload.items():
            before += len(d0.raw_entry(idx))
        if slot_payload:
            write_dictionary_slots_spill(rom, slot_payload)
            after = sum(len(v) for v in slot_payload.values())
            report["dict_densified"] = len(slot_payload)
            report["bytes_saved_dict"] = before - after
            report["notes"].append(
                f"Densified {len(slot_payload)} Hangul dict slots "
                f"(saved {before - after} bytes; protected_seed={len(protect)})."
            )
        else:
            report["notes"].append("No Hangul dict slots needed densify.")

    lines_by_abs = {
        int(line["abs"], 16): line
        for line in json.loads(args.sheet.read_text(encoding="utf-8"))["lines"]
    }

    # 3) Size-preserving sticky inplace (sequential-safe)
    if not args.skip_inplace:
        inplace_banks: Set[int] | None
        if args.inplace_banks.strip().lower() in {"all", "*"}:
            inplace_banks = None
        else:
            inplace_banks = set()
            for part in args.inplace_banks.split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    a, b = part.split("-", 1)
                    inplace_banks.update(range(int(a, 16), int(b, 16) + 1))
                else:
                    inplace_banks.add(int(part, 16))
        inplace = plan_inplace_sticky(
            rom,
            tbl,
            lines_by_abs,
            marker=marker,
            seed_abs=seed_abs,
            banks=inplace_banks,
            max_lines=args.max_inplace,
        )
        for abs_off, payload in inplace.items():
            rom[abs_off : abs_off + len(payload)] = payload
        report["inplace_patched"] = len(inplace)
        report["notes"].append(
            f"Sticky inplace patched {len(inplace)} sequential lines "
            f"(size-preserving)."
        )

    # 4) Optional capacity-limited sequential shift on selected banks
    banks: Set[int] = set()
    for part in args.banks.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            banks.update(range(int(a, 16), int(b, 16) + 1))
        else:
            banks.add(int(part, 16))

    if not args.skip_shift and banks:
        kept, dropped = plan_shift_replacements(
            rom,
            tbl,
            lines_by_abs,
            marker=marker,
            banks=banks,
            seed_abs=seed_abs,
            max_lines=args.max_lines,
        )
        report["dropped_capacity"] = len(dropped)
        if kept:
            # Protect seed: never include seed abs (already filtered).
            bank_report = shift_replacements_in_text_banks(rom, kept)
            report["shift"] = {
                "planned": len(kept),
                "banks": bank_report.get("banks"),
                "pointer_fixes": bank_report.get("pointer_fixes"),
                "relocated_records": bank_report.get("relocated_records"),
                "mapping_changed_sample": dict(
                    list((bank_report.get("mapping_changed") or {}).items())[:20]
                ),
            }
            report["notes"].append(
                f"Shift-applied {len(kept)} sticky lines across banks "
                f"{sorted(f'{b:02X}' for b in banks)}; "
                f"dropped_capacity={len(dropped)}."
            )
        else:
            report["notes"].append("No shift replacements fit bank capacity.")

    # Verify seed + matching sample
    d2 = Dictionary(rom)
    seed_fail = 0
    for row in json.loads(args.seed.read_text(encoding="utf-8"))["lines"]:
        abs_off = int(row["abs"], 16)
        ko = normalize_ko_text(row["ko"])
        body = split_prefix_body(read_encoded_z(rom, abs_off)[0])[1]
        exp = encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        # Accept either sticky or legacy each-marker encoding as match.
        exp_each = encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="each"
        )
        got = d2.expand(body, tbl)
        if got != d2.expand(exp, tbl) and got != d2.expand(exp_each, tbl):
            seed_fail += 1
    report["seed_fail"] = seed_fail

    match = 0
    for abs_off, line in lines_by_abs.items():
        ko = normalize_ko_text(line.get("ko") or "")
        if not ko:
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        enc_each = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="each"
        )
        if enc is None and enc_each is None:
            continue
        try:
            body = split_prefix_body(read_encoded_z(rom, abs_off)[0])[1]
            got = d2.expand(body, tbl)
            ok = False
            if enc is not None and got == d2.expand(enc, tbl):
                ok = True
            if enc_each is not None and got == d2.expand(enc_each, tbl):
                ok = True
            if ok:
                match += 1
        except Exception:
            pass
    report["matching_old_abs"] = match
    report["checksum"] = f"{update_ws_checksum(rom):04X}"

    args.out_rom.write_bytes(rom)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Seq unit OK | sticky={sticky['store_abs']} densify={report['dict_densified']} "
        f"saved={report['bytes_saved_dict']} inplace={report['inplace_patched']} "
        f"shift={report.get('shift') and report['shift']['planned']} "
        f"seed_fail={seed_fail} matching={match} checksum={report['checksum']}"
    )
    for note in report["notes"]:
        print(f"  - {note}")
    print(f"Wrote {args.out_rom}")
    print(f"Wrote {args.out_report}")
    if seed_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
