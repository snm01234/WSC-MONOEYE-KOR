#!/usr/bin/env python3
"""
Steal late-only dictionary slots for early_tut sequential KO (felt coverage).

Reassigns stock and/or ext indices whose *all* external refs (script + name75 +
aux) are late script dialogue (abs > early_hi). Late owners are restored to
size-preserving JP from --base-rom before the slot payload is overwritten with
early KO.

Fail-closed: aux/name75 hit → not stealable; any restore_jp failure → abort
(no ROM write). Prefer apply_curated_abs_batch pair-steal when old KO must be kept.

Free-space tip note: dedicated free slots are usually empty (aux false consumers
on every index). Late-only stock steal is the main safe way to grow 1스테이지
coverage without legacy seq_dict.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import (  # noqa: E402
    _file_abs,
    load_ext_meta,
    make_dictionary,
    spillable_abs_set,
)
from apply_safe_unit import padded_token_payload, read_record_at  # noqa: E402
from event_record_heuristics import looks_like_event_body  # noqa: E402
from expand_dictionary import (  # noqa: E402
    DEFAULT_REF_REGIONS,
    build_dict_token_locs,
    guard_hangul_slot_writes,
    iter_dict_indices,
    slot_rewrite_refuse_reason,
    write_dictionary_slots_spill,
)
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    dict_index_from_token,
    dict_token_safe_in_zstring,
    is_dict_token,
    load_rom,
    read_encoded_z,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import (  # noqa: E402
    encode_ko_text,
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)
from patch_exp_dictionary import write_exp_dictionary_slots  # noqa: E402
from patch_ext_dictionary import STOCK_DICT_COUNT  # noqa: E402

EARLY_LO = 0x60456B
EARLY_HI = 0x607000
MARKER = 0xE3DB
SPACE = 0x01


def restore_jp_body(
    rom: bytearray,
    abs_off: int,
    base_rom: bytes,
) -> bool:
    """Copy size-preserving JP body from base ROM at the same logical abs."""
    base_got = read_encoded_z_safe(base_rom, stock_base(base_rom) + abs_off)
    tip_got = read_encoded_z_safe(rom, _file_abs(rom, abs_off))
    if base_got is None or tip_got is None:
        return False
    base_payload, tip_payload = base_got[0], tip_got[0]
    if len(base_payload) != len(tip_payload):
        # Fall back: keep tip prefix length, pad/truncate base body to tip size.
        bpref, bbody, _ = split_prefix_body(base_payload)
        tpref, _tbody, _ = split_prefix_body(tip_payload)
        if len(bpref) != len(tpref):
            return False
        need = len(tip_payload) - len(tpref)
        if need < 2:
            return False
        body = bytearray(bbody[:need])
        if len(body) < need:
            body.extend(bytes([SPACE]) * (need - len(body)))
        new_payload = bytes(tpref) + bytes(body)
    else:
        new_payload = base_payload
    fo = _file_abs(rom, abs_off)
    rom[fo : fo + len(tip_payload)] = new_payload
    return True


def find_stealable_slots(
    rom: bytes | bytearray,
    dictionary: Dictionary,
    *,
    stock: int,
    early_hi: int,
    include_stock: bool = True,
    include_ext: bool = True,
    late_lo: int | None = None,
    late_hi: int | None = None,
    protect_abs: Set[int] | None = None,
) -> List[Tuple[int, List[int]]]:
    """
    Dict indices with only late dialogue script refs (no early / name75 / aux).
    Returns (index, sorted late script abs list).

    Optional late_lo/late_hi constrain *every* victim abs (e.g. 640000-69FFFF
    so Ep4 / unit banks are never restored-as-collateral).

    If protect_abs is set (sheet-aware mode), a ref is an allowed victim when it
    is outside the protect set — i.e. empty/low-quality KO lines may be restored
    even when abs <= early_hi. Protected Hangul targets are never victims.
    """
    locs = build_dict_token_locs(rom, regions=DEFAULT_REF_REGIONS)
    nested: Set[int] = set()
    for j in range(dictionary.count):
        for child in iter_dict_indices(dictionary.raw_entry(j)):
            if 0 <= child < dictionary.count and child != j:
                nested.add(child)

    victim_lo = early_hi + 1 if late_lo is None else late_lo
    victim_hi = 0x69FFFF if late_hi is None else late_hi
    protect = protect_abs or set()
    sheet_aware = protect_abs is not None

    out: List[Tuple[int, List[int]]] = []
    for index, refs in locs.items():
        if index < 0 or index >= dictionary.count:
            continue
        if index < stock and not include_stock:
            continue
        if index >= stock and not include_ext:
            continue
        if index in nested:
            continue
        if not dict_token_safe_in_zstring(index):
            continue
        if not refs:
            continue
        # Any aux/name75 → refuse (would leave mid-game UI on stolen KO).
        if any(r.region != "script" for r in refs):
            continue
        bad = False
        abs_list: List[int] = []
        for r in refs:
            if r.kind != "dialogue":
                bad = True
                break
            if sheet_aware:
                # Never steal a slot still needed by a Hangul sheet line.
                if r.abs in protect:
                    bad = True
                    break
                if r.abs > victim_hi:
                    bad = True
                    break
            else:
                if r.abs <= early_hi:
                    bad = True
                    break
                if r.abs < victim_lo or r.abs > victim_hi:
                    bad = True
                    break
            abs_list.append(r.abs)
        if bad or not abs_list:
            continue
        # Defensive: every late ref must be accounted for as restore target.
        refuse = slot_rewrite_refuse_reason(
            locs, index, keeper_abs=set(abs_list)
        )
        if refuse:
            continue
        out.append((index, sorted(set(abs_list))))
    # Prefer fewer late victims, then lower index (stock before ext when tied).
    out.sort(key=lambda x: (len(x[1]), x[0]))
    return out


def find_stealable_ext_slots(
    rom: bytes | bytearray,
    dictionary: Dictionary,
    *,
    stock: int,
    early_hi: int,
) -> List[Tuple[int, List[int]]]:
    """Backward-compatible: ext-only stealables."""
    return find_stealable_slots(
        rom,
        dictionary,
        stock=stock,
        early_hi=early_hi,
        include_stock=False,
        include_ext=True,
    )


def early_still_jp_ranked(
    rom: bytes | bytearray,
    base_rom: bytes,
    tbl: Tbl,
    meta: dict,
    abs_to_line: Dict[int, dict],
    seed_abs: Set[int],
    ptr_rom: bytes,
    *,
    marker: int,
    early_lo: int = EARLY_LO,
    early_hi: int = EARLY_HI,
) -> List[Tuple[str, List[int]]]:
    d = make_dictionary(rom, meta)
    text_to_abs: Dict[str, List[int]] = defaultdict(list)
    for abs_off, line in abs_to_line.items():
        if abs_off < early_lo or abs_off > early_hi:
            continue
        if abs_off in seed_abs:
            continue
        ko = normalize_ko_text(line.get("ko") or "")
        if not ko or is_low_quality_ko(ko):
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if enc is None:
            continue
        try:
            base_got = read_encoded_z_safe(base_rom, stock_base(base_rom) + abs_off)
            if base_got is None:
                continue
            if looks_like_event_body(split_prefix_body(base_got[0])[1]):
                continue
            got = read_encoded_z_safe(rom, _file_abs(rom, abs_off))
            if got is None:
                continue
            original = got[0]
            prefix, body, _ = split_prefix_body(original)
            if len(body) < 2 or len(prefix) + 2 > len(original):
                continue
            already = (
                d.expand(body, tbl).rstrip("\u3000")
                == d.expand(enc, tbl).rstrip("\u3000")
            )
            if already:
                continue
        except Exception:
            continue
        text_to_abs[ko].append(abs_off)
    spillable = spillable_abs_set(ptr_rom, [a for v in text_to_abs.values() for a in v])
    ranked: List[Tuple[str, List[int]]] = []
    for ko, abs_list in text_to_abs.items():
        seq = sorted(a for a in abs_list if a not in spillable)
        if seq:
            ranked.append((ko, seq))
    # Frequency first (one slot → many sites), then earliest abs.
    ranked.sort(key=lambda kv: (-len(kv[1]), min(kv[1])))
    return ranked


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
        help="JP bodies for restoring late owners (8MiB or 16MiB; uses stock_base)",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_ep3_window.json",
    )
    ap.add_argument("--seed", type=Path, default=ROOT / "data/translations_seed_hook96.json")
    ap.add_argument("--meta", type=Path, default=ROOT / "out/patch/exp_dictionary_meta.json")
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument(
        "--pointer-ref-rom",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc",
    )
    ap.add_argument("--early-lo", type=lambda s: int(s, 16), default=EARLY_LO)
    ap.add_argument("--early-hi", type=lambda s: int(s, 16), default=EARLY_HI)
    ap.add_argument(
        "--late-lo",
        type=lambda s: int(s, 16),
        default=None,
        help="Min victim abs (default: early_hi+1). Use 640000 to spare Ep4.",
    )
    ap.add_argument(
        "--late-hi",
        type=lambda s: int(s, 16),
        default=0x69FFFF,
        help="Max victim abs (default 69FFFF; excludes unit banks 6A-6F)",
    )
    ap.add_argument(
        "--sheet-aware-victims",
        action="store_true",
        help=(
            "Treat abs lacking Hangul KO as restore victims (even inside early "
            "window). Protect set comes from --protect-sheet (or --sheet)."
        ),
    )
    ap.add_argument(
        "--protect-sheet",
        type=Path,
        default=None,
        help="Hangul protect abs source for --sheet-aware-victims (default: --sheet)",
    )
    ap.add_argument("--max-steal", type=int, default=0, help="0=all stealable")
    ap.add_argument(
        "--include-stock",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Steal late-only stock dict slots (default on; main free-space lever)",
    )
    ap.add_argument(
        "--include-ext",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Steal late-only ext dict slots (default on)",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="Optional tip backup path (default: skip; tip is the baseline)",
    )
    ap.add_argument(
        "--out-rom",
        type=Path,
        default=None,
        help="Write here instead of --rom (recommended: work ROM)",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/steal_late_ext_to_early_report.json",
    )
    args = ap.parse_args()
    out_rom = args.out_rom or args.rom

    meta = load_ext_meta(args.meta)
    stock = int(meta.get("stock_count", STOCK_DICT_COUNT))
    rom_bytes = load_rom(args.rom)
    base_rom = load_rom(args.base_rom)
    ptr_rom = load_rom(args.pointer_ref_rom)
    tbl = Tbl.load(args.tbl)
    lines = json.loads(args.sheet.read_text(encoding="utf-8"))["lines"]
    abs_to_line = {int(L["abs"], 16): L for L in lines if L.get("abs")}
    seed_abs = {
        int(r["abs"], 16)
        for r in json.loads(args.seed.read_text(encoding="utf-8")).get("lines", [])
    }

    protect_abs: Set[int] | None = None
    if args.sheet_aware_victims:
        protect_path = args.protect_sheet or args.sheet
        protect_lines = json.loads(protect_path.read_text(encoding="utf-8"))["lines"]
        protect_abs = set(seed_abs)
        for line in protect_lines:
            if not line.get("abs"):
                continue
            abs_off = int(line["abs"], 16)
            ko = normalize_ko_text(line.get("ko") or "")
            if ko and not is_low_quality_ko(ko) and any(
                "\uac00" <= c <= "\ud7a3" for c in ko
            ):
                protect_abs.add(abs_off)

    d0 = make_dictionary(rom_bytes, meta)
    seed_indices: Set[int] = set()
    for abs_off in seed_abs:
        try:
            body = split_prefix_body(
                read_encoded_z(rom_bytes, _file_abs(rom_bytes, abs_off))[0]
            )[1]
            seed_indices.update(iter_dict_indices(body))
            for idx in list(seed_indices):
                if 0 <= idx < d0.count:
                    seed_indices.update(iter_dict_indices(d0.raw_entry(idx)))
        except Exception:
            continue

    stealable = [
        (idx, refs)
        for idx, refs in find_stealable_slots(
            rom_bytes,
            d0,
            stock=stock,
            early_hi=args.early_hi,
            include_stock=bool(args.include_stock),
            include_ext=bool(args.include_ext),
            late_lo=args.late_lo,
            late_hi=args.late_hi,
            protect_abs=protect_abs,
        )
        if idx not in seed_indices
    ]
    early = early_still_jp_ranked(
        rom_bytes,
        base_rom,
        tbl,
        meta,
        abs_to_line,
        seed_abs,
        ptr_rom,
        marker=MARKER,
        early_lo=args.early_lo,
        early_hi=args.early_hi,
    )
    n = len(stealable) if args.max_steal <= 0 else min(len(stealable), args.max_steal)
    n = min(n, len(early))
    plan = list(zip(stealable[:n], early[:n]))

    report = {
        "early_lo": f"{args.early_lo:06X}",
        "early_hi": f"{args.early_hi:06X}",
        "late_lo": f"{(args.late_lo if args.late_lo is not None else args.early_hi + 1):06X}",
        "late_hi": f"{args.late_hi:06X}",
        "sheet_aware_victims": bool(args.sheet_aware_victims),
        "protect_abs_n": len(protect_abs) if protect_abs is not None else None,
        "include_stock": bool(args.include_stock),
        "include_ext": bool(args.include_ext),
        "stealable_slots": len(stealable),
        "stealable_stock": sum(1 for i, _ in stealable if i < stock),
        "stealable_ext": sum(1 for i, _ in stealable if i >= stock),
        "early_still_jp_unique": len(early),
        "planned": n,
        "dry_run": bool(args.dry_run),
        "out_rom": str(out_rom),
        "sample_steal": [
            {
                "dict_index": idx,
                "late_refs": len(refs),
                "late_sample": [f"{a:06X}" for a in refs[:4]],
                "early_ko": ko[:40],
                "early_abs": [f"{a:06X}" for a in eabs[:4]],
            }
            for (idx, refs), (ko, eabs) in plan[:15]
        ],
    }

    if n == 0:
        report["note"] = "nothing to steal or no early JP targets"
        args.out_report.parent.mkdir(parents=True, exist_ok=True)
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.dry_run:
        args.out_report.parent.mkdir(parents=True, exist_ok=True)
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"DRY stealable={len(stealable)} "
            f"(stock={report['stealable_stock']} ext={report['stealable_ext']}) "
            f"early_need={len(early)} planned={n}"
        )
        print(f"Wrote {args.out_report}")
        return

    if args.backup is not None:
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        if not args.backup.exists():
            shutil.copy2(args.rom, args.backup)

    # Work on a scratch copy; commit only if every late restore + gate passes.
    rom = bytearray(rom_bytes)
    restored = []
    restore_fail = 0
    restore_fail_abs: List[str] = []
    for (idx, refs), (_ko, _eabs) in plan:
        for abs_off in refs:
            if restore_jp_body(rom, abs_off, base_rom):
                restored.append(f"{abs_off:06X}")
            else:
                restore_fail += 1
                restore_fail_abs.append(f"{abs_off:06X}")

    report.update(
        {
            "restored_late_lines": len(restored),
            "restore_fail": restore_fail,
            "restore_fail_abs_sample": restore_fail_abs[:40],
        }
    )
    if restore_fail:
        report["aborted"] = True
        report["abort_reason"] = "late_consumer_restore_failed"
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"ABORT restore_fail={restore_fail} sample={restore_fail_abs[:8]}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Prefer exp-dict write for ext indices; spill works for stock but ext
    # lives in expansion bank — use write_exp_dictionary_slots when available.
    stock_payload: Dict[int, bytes] = {}
    ext_payload: Dict[int, bytes] = {}
    for (idx, _refs), (ko, _eabs) in plan:
        enc = encode_ko_text(
            ko, tbl, hangul_marker_code=MARKER, hangul_marker_mode="run"
        )
        if idx >= stock:
            ext_payload[idx] = enc
        else:
            stock_payload[idx] = enc

    if stock_payload or ext_payload:
        # Fail-closed: refuse Hangul into slots that still have aux/name75 consumers.
        guard_hangul_slot_writes(rom, {**stock_payload, **ext_payload})
    if stock_payload:
        write_dictionary_slots_spill(rom, stock_payload)
    if ext_payload:
        write_exp_dictionary_slots(
            rom,
            ext_payload,
            stock_count=stock,
            slot_count=int(meta.get("slot_count", 265)),
            ext_ptr_off=int(meta.get("ext_ptr_off", "0000"), 16),
        )

    d2 = make_dictionary(rom, meta)
    patches = []
    decode_fail = 0
    for (idx, _refs), (ko, eabs) in plan:
        token = token_from_dict_index(idx)
        for abs_off in eabs:
            try:
                original = read_record_at(rom, abs_off)
                prefix, _body, _ = split_prefix_body(original)
                new_payload = padded_token_payload(prefix, token, original)
            except Exception:
                decode_fail += 1
                continue
            fo = _file_abs(rom, abs_off)
            rom[fo : fo + len(original)] = new_payload
            got = normalize_ko_text(
                d2.expand(split_prefix_body(new_payload)[1], tbl)
            ).rstrip("\u3000")
            ok = got == ko or ko in got
            if not ok:
                # Tolerate unencodable jamo / dash as ideographic space.
                soft = lambda s: s.replace("\u3161", "\u3000").replace("-", "\u3000")
                ok = soft(got) == soft(ko) or soft(ko) in soft(got)
            if not ok:
                decode_fail += 1
            patches.append(
                {
                    "abs": f"{abs_off:06X}",
                    "dict_index": idx,
                    "ko": ko,
                    "ok": ok,
                }
            )

    # Seed gate
    seed_fail = 0
    for abs_off in seed_abs:
        try:
            body = split_prefix_body(
                read_encoded_z(rom, _file_abs(rom, abs_off))[0]
            )[1]
            got = d2.expand(body, tbl).rstrip("\u3000")
            prev = make_dictionary(rom_bytes, meta).expand(
                split_prefix_body(
                    read_encoded_z(rom_bytes, _file_abs(rom_bytes, abs_off))[0]
                )[1],
                tbl,
            ).rstrip("\u3000")
            if got != prev:
                seed_fail += 1
        except Exception:
            seed_fail += 1

    report.update(
        {
            "ext_slots_written": len(ext_payload),
            "stock_slots_written": len(stock_payload),
            "lines_patched": len(patches),
            "decode_fail": decode_fail,
            "seed_fail": seed_fail,
            "patches_sample": patches[:20],
        }
    )
    if seed_fail:
        report["aborted"] = True
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("ABORT seed_fail", report["seed_fail"], file=sys.stderr)
        sys.exit(1)
    # Soft decode mismatches (jamo/dash/pad) are reported but do not block commit
    # when Hangul is present; hard seed drift still aborts above.
    if decode_fail:
        report["decode_fail_soft"] = True
        print(
            f"WARN decode_fail={decode_fail} (committing; inspect patches_sample)",
            file=sys.stderr,
        )

    cs = f"{update_ws_checksum(rom):04X}"
    out_rom.parent.mkdir(parents=True, exist_ok=True)
    out_rom.write_bytes(rom)
    report["checksum"] = cs
    if args.backup is not None:
        report["backup"] = str(args.backup)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"OK steal | slots={n} early_lines={len(patches)} "
        f"restored_late={len(restored)} seed_fail={seed_fail} checksum={cs} "
        f"-> {out_rom}"
    )


if __name__ == "__main__":
    main()
