#!/usr/bin/env python3
"""
Repair full-line dictionary overshare / invasion on tip ROM.

For each Hangul dict slot used as a *pure token* by multiple dialogue lines:

  Distinct JP groups:
  • Pick a keeper JP group (prefer early-band lines whose sheet KO matches
    the slot text; else earliest early; else largest group).
  • Leave keeper consumers on the slot (early KO stays fixed).
  • Restore all other pure-token consumers to size-preserving JP from
    --base-rom (original JP ROM preferred).
  • Cap aux/name75 restores (MAX_AUX_RESTORE) to avoid nuking hot tokens.

Same JP, wrong tip KO:
  • Rewrite the slot payload to the unanimous sheet KO (rewrite_slot).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from expand_dictionary import (  # noqa: E402
    build_dict_token_locs,
    write_dictionary_slots_spill,
)
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    dict_index_from_token,
    is_dict_token,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    update_ws_checksum,
)
from normalize_ko_text import (  # noqa: E402
    encode_ko_text,
    normalize_ko_text,
    try_encode_ko_text,
)
from patch_exp_dictionary import write_exp_dictionary_slots  # noqa: E402
from steal_late_ext_to_early import restore_jp_body  # noqa: E402

EARLY = (0x6040A5, 0x607000)
SPACE = 0x01
# Avoid mass-rewriting false-positive aux hits on ultra-hot tokens.
MAX_AUX_RESTORE = 24
MARKER = 0xE3DB


def has_marker(payload: bytes) -> bool:
    return any(
        payload[i] == 0xE3 and payload[i + 1] == 0xDB
        for i in range(len(payload) - 1)
    )


def file_abs(rom: bytes | bytearray, logical: int) -> int:
    return stock_base(rom) + logical


def pure_token_idx(body: bytes, expect_idx: int) -> bool:
    core = bytes(b for b in body if b != SPACE)
    if len(core) != 2 or not is_dict_token(core[0]):
        return False
    return dict_index_from_token(core[0], core[1]) == expect_idx


def load_sheet(path: Path) -> Tuple[Dict[int, str], Dict[int, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = data["lines"] if isinstance(data, dict) else data
    jp_by: Dict[int, str] = {}
    ko_by: Dict[int, str] = {}
    for row in lines:
        if not row.get("abs"):
            continue
        abs_off = int(row["abs"], 16)
        jp_by[abs_off] = row.get("jp") or ""
        ko = normalize_ko_text(row.get("ko") or "")
        if ko:
            ko_by[abs_off] = ko
    return jp_by, ko_by


def choose_keep_jp(
    consumers: Sequence[Tuple[int, str, str]],
    tip_ko: str,
) -> str:
    """
    consumers: (abs, jp, sheet_ko)
    Returns the JP string whose pure-token consumers keep the slot.
    """
    early = [(a, jp, sk) for a, jp, sk in consumers if EARLY[0] <= a <= EARLY[1]]
    # 1) Early lines whose sheet KO matches tip
    matched = [
        (a, jp, sk)
        for a, jp, sk in early
        if sk and sk.rstrip("\u3000") == tip_ko.rstrip("\u3000") and jp
    ]
    if matched:
        matched.sort(key=lambda t: t[0])
        return matched[0][1]
    # 2) Any early with JP
    early_jp = [(a, jp) for a, jp, _sk in early if jp]
    if early_jp:
        early_jp.sort(key=lambda t: t[0])
        return early_jp[0][1]
    # 3) Largest JP group overall
    counts: Dict[str, int] = defaultdict(int)
    for _a, jp, _sk in consumers:
        if jp:
            counts[jp] += 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]


def collect_overshares(
    rom: bytes | bytearray,
    dictionary,
    tbl: Tbl,
    locs,
    jp_by: Dict[int, str],
    ko_by: Dict[int, str],
    *,
    stock: int,
    base_dict: Optional[Dictionary] = None,
) -> List[dict]:
    out: List[dict] = []
    for idx, refs in locs.items():
        try:
            raw = dictionary.raw_entry(idx)
        except Exception:
            continue
        if not raw or not has_marker(raw):
            continue
        tip = dictionary.expand(raw, tbl).rstrip("\u3000")
        pure: List[Tuple[int, str, str]] = []
        aux_pure: List[int] = []
        for r in refs:
            got = read_encoded_z_safe(rom, file_abs(rom, r.abs))
            if got is None:
                continue
            _prefix, body, _ = split_prefix_body(got[0])
            if not pure_token_idx(body, idx):
                continue
            if r.region != "script":
                aux_pure.append(r.abs)
                continue
            if r.kind != "dialogue":
                continue
            pure.append((r.abs, jp_by.get(r.abs, ""), ko_by.get(r.abs, "")))

        if len(pure) < 2:
            continue

        # Group key: sheet JP, or unique abs if JP missing (outside sheet).
        groups: Dict[str, List[int]] = defaultdict(list)
        for abs_off, jp, _sk in pure:
            key = jp if jp else f"__ABS__{abs_off:06X}"
            groups[key].append(abs_off)
        rewrite_ko = ""
        action = "restore_consumers"

        if len(groups) < 2:
            # Same JP share — check whether slot text is the wrong KO.
            keep_jp = next((jp for _a, jp, _k in pure if jp), "")
            keep_abs = {a for a, _, _ in pure}
            sheet_kos = sorted(
                {
                    sk.rstrip("\u3000")
                    for _a, _jp, sk in pure
                    if sk and not is_low_quality_safe(sk)
                }
            )
            if sheet_kos and tip.rstrip("\u3000") not in sheet_kos:
                # Unrelated KO parked on a shared phrase slot.
                rewrite_ko = sheet_kos[0]
                action = "rewrite_slot"
                restore_abs = sorted(set(aux_pure[:MAX_AUX_RESTORE]))
            elif aux_pure:
                restore_abs = sorted(set(aux_pure[:MAX_AUX_RESTORE]))
                action = "restore_aux_only"
            else:
                continue
        else:
            keep_jp = choose_keep_jp(pure, tip)
            if keep_jp and not keep_jp.startswith("__ABS__"):
                keep_abs = {a for a, jp, _k in pure if jp == keep_jp}
            else:
                early_abs = sorted(
                    a for a, _, _ in pure if EARLY[0] <= a <= EARLY[1]
                )
                keep_one = early_abs[0] if early_abs else min(a for a, _, _ in pure)
                keep_abs = {keep_one}
                keep_jp = f"__ABS__{keep_one:06X}"
            # Never detach early-band dialogue (초반 슬롯/대사 고정).
            restore_script = {
                a
                for a, _, _ in pure
                if a not in keep_abs and not (EARLY[0] <= a <= EARLY[1])
            }
            restore_aux = set(aux_pure[:MAX_AUX_RESTORE]) if aux_pure else set()
            restore_abs = sorted(restore_script | restore_aux)
            # If keepers' sheet KO disagrees with tip, fix the slot too.
            keep_sheet = {
                sk.rstrip("\u3000")
                for a, _jp, sk in pure
                if a in keep_abs and sk and not is_low_quality_safe(sk)
            }
            if keep_sheet and tip.rstrip("\u3000") not in keep_sheet:
                rewrite_ko = sorted(keep_sheet)[0]
                action = "rewrite_and_restore"

        if not restore_abs and not rewrite_ko:
            continue

        sole = False
        if base_dict is not None and idx < stock:
            try:
                br = base_dict.raw_entry(idx)
                sole = bool(br) and not has_marker(br) and len(raw) > len(br) + 4
            except Exception:
                pass

        out.append(
            {
                "dict_index": idx,
                "ext": idx >= stock,
                "action": action,
                "tip_ko": tip[:70],
                "rewrite_ko": rewrite_ko[:70],
                "keep_jp": (
                    keep_jp[:50] if not str(keep_jp).startswith("__ABS__") else keep_jp
                ),
                "keep_abs": [f"{a:06X}" for a in sorted(keep_abs)],
                "restore_abs": [f"{a:06X}" for a in restore_abs],
                "pure_n": len(pure),
                "aux_pure_n": len(aux_pure),
                "distinct_jp": len(groups),
                "stock_sole_residue": sole,
            }
        )
    out.sort(
        key=lambda x: (
            0 if x.get("rewrite_ko") else 1,
            -len(x["restore_abs"]),
            x["dict_index"],
        )
    )
    return out


def is_low_quality_safe(ko: str) -> bool:
    try:
        from normalize_ko_text import is_low_quality_ko

        return bool(is_low_quality_ko(ko))
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
        help="JP restore source (original ROM preferred)",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_quality_all.json",
        help="JP/KO forensic sheet for keeper selection (not an apply source)",
    )
    ap.add_argument("--meta", type=Path, default=ROOT / "out/patch/exp_dictionary_meta.json")
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument(
        "--backup",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.pre_overshare_repair.wsc",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/repair_dict_overshare_report.json",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-slots", type=int, default=0, help="0=all")
    args = ap.parse_args()

    if not args.base_rom.exists():
        args.base_rom = ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc"

    rom_bytes = load_rom(args.rom)
    base_rom = args.base_rom.read_bytes()
    meta = load_ext_meta(args.meta)
    tbl = Tbl.load(args.tbl)
    d = make_dictionary(rom_bytes, meta)
    db = Dictionary(load_rom(ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc"))
    stock = int(meta["stock_count"])
    jp_by, ko_by = load_sheet(args.sheet)

    print("scanning locs + overshares...")
    locs = build_dict_token_locs(rom_bytes, regions=("script", "name75", "aux"))
    plans = collect_overshares(
        rom_bytes, d, tbl, locs, jp_by, ko_by, stock=stock, base_dict=db
    )
    if args.max_slots > 0:
        plans = plans[: args.max_slots]

    report = {
        "base_rom": str(args.base_rom),
        "slots_planned": len(plans),
        "consumers_to_restore": sum(len(p["restore_abs"]) for p in plans),
        "keepers": sum(len(p["keep_abs"]) for p in plans),
        "dry_run": bool(args.dry_run),
        "plans": plans[:80],
        "plans_truncated": max(0, len(plans) - 80),
    }

    if args.dry_run:
        args.out_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"DRY slots={len(plans)} restore_sites={report['consumers_to_restore']} "
            f"keepers={report['keepers']} → {args.out_report}"
        )
        return 0

    args.backup.parent.mkdir(parents=True, exist_ok=True)
    if not args.backup.exists():
        shutil.copy2(args.rom, args.backup)

    rom = bytearray(rom_bytes)
    restored_ok = 0
    restored_fail = 0
    rewritten = 0
    rewrite_fail = 0
    fail_abs: List[str] = []
    stock_payload: Dict[int, bytes] = {}
    ext_payload: Dict[int, bytes] = {}

    for plan in plans:
        rk = plan.get("rewrite_ko") or ""
        if rk:
            enc = try_encode_ko_text(
                rk, tbl, hangul_marker_code=MARKER, hangul_marker_mode="run"
            )
            if enc is None:
                rewrite_fail += 1
            else:
                idx = int(plan["dict_index"])
                blob = encode_ko_text(
                    rk, tbl, hangul_marker_code=MARKER, hangul_marker_mode="run"
                )
                if idx >= stock:
                    ext_payload[idx] = blob
                else:
                    stock_payload[idx] = blob
                rewritten += 1

    if stock_payload:
        write_dictionary_slots_spill(
            rom, stock_payload, allow_aux_consumers=True
        )
    if ext_payload:
        ext_ptr_off = int(meta["ext_ptr_off"], 16)
        write_exp_dictionary_slots(
            rom,
            ext_payload,
            ext_ptr_off=ext_ptr_off,
            stock_count=stock,
            slot_count=int(meta["slot_count"]),
            allow_aux_consumers=True,
        )

    for plan in plans:
        for abs_hex in plan["restore_abs"]:
            abs_off = int(abs_hex, 16)
            if restore_jp_body(rom, abs_off, base_rom):
                restored_ok += 1
            else:
                restored_fail += 1
                fail_abs.append(abs_hex)

    report.update(
        {
            "slots_rewritten": rewritten,
            "rewrite_fail": rewrite_fail,
            "restored_ok": restored_ok,
            "restored_fail": restored_fail,
            "fail_abs_sample": fail_abs[:40],
            "checksum": f"{update_ws_checksum(rom):04X}",
        }
    )
    args.rom.write_bytes(rom)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"overshare repair OK | slots={len(plans)} rewrite={rewritten} "
        f"restored={restored_ok} fail={restored_fail}/{rewrite_fail} "
        f"checksum={report['checksum']} → {args.rom}"
    )
    return 1 if restored_fail and restored_ok == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
