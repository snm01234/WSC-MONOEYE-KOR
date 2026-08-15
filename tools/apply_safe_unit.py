#!/usr/bin/env python3
"""
Apply the next *safe* Korean patch unit on top of a working seed ROM.

Respects PATCH_PROGRESS.md precautions:
  - no full dictionary rebuild (spill writes only)
  - no bulk inplace
  - optional small glyph append (does not fill all E740–E7FF)
  - only rewrite lines we actually patch

Units (in order):
  1) reuse   — point additional abs at existing seed KO dict entries (0 new slots)
  2) shared  — unanimous fully-covered clusters (spill + pure-token bodies)
  3) multi   — multi-KO fully-covered clusters (split across reclaimed slots)
  4) glyphs  — append N high-frequency missing Hangul into the next E7xx codes
  5) reclaim — reclaim-by-exclude for unique KO texts
  6) sole    — retarget sole-referenced dict indices to new KO in spill
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_translations_expanded import (  # noqa: E402
    load_translation_lines,
    read_record_at,
)
from expand_dictionary import (  # noqa: E402
    DEFAULT_REF_REGIONS,
    build_dict_token_locs,
    iter_dict_indices,
    reclaimable_slots,
    write_dictionary_slots_spill,
)
from extract_script import split_prefix_body  # noqa: E402
from font_pipeline import find_system_font  # noqa: E402
from hangul_allocator import HANGUL_PRIMARY_END  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    compact_font_file_offset,
    dict_index_from_token,
    encode_compact_font_record,
    is_dict_token,
    load_rom,
    read_encoded_z,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import (  # noqa: E402
    encode_ko_text,
    hangul_count,
    is_low_quality_ko,
    normalize_ko_text,
    try_encode_ko_text,
)
from build_hangul_font import render_compact_glyph  # noqa: E402


def padded_token_payload(prefix: bytes, token: bytes, original: bytes) -> bytes:
    from monoeye_rom import MAX_SAFE_RECORD_LEN

    if len(original) > MAX_SAFE_RECORD_LEN:
        raise RuntimeError(
            f"refusing pad into oversized record ({len(original)}>{MAX_SAFE_RECORD_LEN})"
        )
    if len(token) >= 2 and token[-1] == 0x00:
        # Trail 0x00 is also the zstring NUL — record ends inside the token.
        raise RuntimeError(
            f"refusing zstring-unsafe dict token {token.hex()} (trail 00)"
        )
    new_payload = bytearray(prefix) + bytearray(token)
    pad = len(original) - len(new_payload)
    if pad < 0:
        raise RuntimeError("prefix+token longer than original record")
    if pad > 64:
        raise RuntimeError(f"refusing large zero-pad ({pad} bytes)")
    # Use space (0x01), not NUL — early 00 makes the next sequential dialogue
    # line read as empty (first line OK, second line missing).
    new_payload.extend(b"\x01" * pad)
    return bytes(new_payload)


def build_ref_maps(rom: bytes | bytearray, dictionary: Dictionary):
    """
    script_locs / nested dict_refs / non_script_hit.

    Scans DEFAULT_REF_REGIONS (script + name75 + aux). Indices with any
    name75/aux consumer are in ``non_script_hit`` — shared/sole rewrite must refuse.
    """
    locs = build_dict_token_locs(rom, regions=DEFAULT_REF_REGIONS)
    script_locs: Dict[int, List[int]] = defaultdict(list)
    non_script_hit: Set[int] = set()
    for idx, refs in locs.items():
        if not (0 <= idx < dictionary.count):
            continue
        for ref in refs:
            if ref.region == "script":
                script_locs[idx].append(ref.abs)
            else:
                non_script_hit.add(idx)
    dict_refs: Counter = Counter()
    for i in range(dictionary.count):
        for idx in iter_dict_indices(dictionary.raw_entry(i)):
            if 0 <= idx < dictionary.count:
                dict_refs[idx] += 1
    return script_locs, dict_refs, non_script_hit


def next_hangul_code(tbl: Tbl) -> int:
    used = [c for c in tbl.code_to_char if c >= 0xE740]
    return (max(used) + 1) if used else 0xE740


def append_glyphs(
    rom: bytearray,
    tbl: Tbl,
    chars: List[str],
    font_path: str,
) -> List[dict]:
    reports = []
    code = next_hangul_code(tbl)
    for ch in chars:
        if ch in tbl.char_to_code:
            continue
        if code > HANGUL_PRIMARY_END:
            break
        off = compact_font_file_offset(code)
        pixels = render_compact_glyph(ch, font_path)
        rom[off : off + 16] = encode_compact_font_record(pixels)
        tbl.code_to_char[code] = ch
        tbl.char_to_code[ch] = code
        reports.append({"char": ch, "code": f"{code:04X}", "file_offset": off})
        code += 1
    return reports


def missing_hangul_freq(lines: List[dict], tbl: Tbl, skip_abs: Set[int]) -> Counter:
    counts: Counter = Counter()
    for line in lines:
        abs_off = int(line["abs"], 16)
        if abs_off in skip_abs:
            continue
        for ch in normalize_ko_text(line["ko"].replace(" ", "　")):
            if "가" <= ch <= "힣" and ch not in tbl.char_to_code:
                counts[ch] += 1
    return counts


def parse_banks(spec: str) -> Set[int] | None:
    spec = (spec or "").strip().lower()
    if not spec or spec in {"all", "*"}:
        return None
    banks: Set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        banks.add(int(part, 16) if part.startswith("0x") else int(part, 16))
    return banks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_marked.wsc",
    )
    ap.add_argument("--tbl", type=Path, default=ROOT / "out" / "patch" / "hangul_patch_pad3.tbl")
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
    ap.add_argument("--out-rom", type=Path, default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc")
    ap.add_argument("--out-tbl", type=Path, default=ROOT / "out" / "patch" / "hangul_patch_pad3.tbl")
    ap.add_argument("--out-report", type=Path, default=ROOT / "out" / "patch" / "safe_unit_report.json")
    ap.add_argument("--add-glyphs", type=int, default=0, help="Append N Hangul glyphs (0=safe default)")
    ap.add_argument(
        "--allow-stock-glyphs",
        action="store_true",
        help="Allow --add-glyphs to overwrite stock E7xx (UNSAFE on marked/pad PoC)",
    )
    ap.add_argument(
        "--hangul-marker",
        type=lambda s: int(s, 16),
        default=0xE3DB,
        help="Marker code prefixed before each Hangul (0 to disable)",
    )
    ap.add_argument("--max-new-slots", type=int, default=0, help="Max new unique KO dict entries (0=safe default)")
    ap.add_argument(
        "--banks",
        default="60",
        help="Comma-separated script banks to patch with new slots (hex). "
        "'all' = every bank. Reuse always applies to all banks.",
    )
    ap.add_argument(
        "--min-hangul",
        type=int,
        default=2,
        help="Minimum Hangul syllables for new-slot KO candidates",
    )
    ap.add_argument(
        "--no-reuse",
        action="store_true",
        help="Do not retarget additional abs to existing seed KO dict entries",
    )
    ap.add_argument(
        "--allow-sole",
        action="store_true",
        help="Allow reclaiming sole-referenced dict slots (risky; off by default)",
    )
    ap.add_argument(
        "--no-shared-rewrite",
        action="store_true",
        help="Do not rewrite multi-ref dict slots whose every script ref shares one KO",
    )
    ap.add_argument(
        "--max-shared-slots",
        type=int,
        default=500,
        help="Max shared/cluster dict slots to rewrite in one unit (default 500)",
    )
    ap.add_argument(
        "--abs-limit",
        type=lambda s: int(s, 16),
        default=None,
        help="Only patch new-slot abs below this hex address (e.g. 601000)",
    )
    args = ap.parse_args()
    bank_filter = parse_banks(args.banks)
    marker = None if args.hangul_marker == 0 else args.hangul_marker

    if args.add_glyphs > 0 and not args.allow_stock_glyphs:
        raise SystemExit(
            "--add-glyphs writes stock E7xx compact slots and breaks the marked/pad "
            "Hangul path. Keep --add-glyphs 0, or expand padding capacity first. "
            "Pass --allow-stock-glyphs only for explicit unsafe experiments."
        )

    rom = bytearray(load_rom(args.rom))
    tbl = Tbl.load(args.tbl)
    font_path = find_system_font()
    if not font_path:
        raise SystemExit("No Hangul system font found")

    seed_lines = json.loads(args.seed.read_text(encoding="utf-8"))["lines"]
    seed_abs = {int(x["abs"], 16) for x in seed_lines}
    sheet = load_translation_lines(args.sheet)
    abs_to_line = {int(l["abs"], 16): l for l in sheet}

    report: dict = {
        "base_rom": str(args.rom),
        "hangul_marker": f"{marker:04X}" if marker is not None else None,
        "glyphs_added": [],
        "reuse_patches": [],
        "shared_rewrite_patches": [],
        "new_slot_patches": [],
        "notes": [],
    }

    # Precompute sole owners on the base ROM (informational / future reclaim).
    dictionary0 = Dictionary(rom)
    script_locs0, dict_refs0, non_script0 = build_ref_maps(rom, dictionary0)
    sole_abs0 = {
        locs[0]
        for idx, locs in script_locs0.items()
        if len(locs) == 1 and dict_refs0[idx] == 0 and idx not in non_script0
    }

    # Treat already-correct KO abs as patched so multi-pass units stay idempotent.
    already_ok: Set[int] = set()
    for abs_off, line in abs_to_line.items():
        if abs_off in seed_abs:
            continue
        ko = normalize_ko_text(line["ko"])
        if not ko or try_encode_ko_text(ko, tbl, hangul_marker_code=marker) is None:
            continue
        try:
            payload, _ = read_encoded_z(rom, abs_off)
            _prefix, body, _ = split_prefix_body(payload)
            got = dictionary0.expand(body, tbl)
            expect = dictionary0.expand(
                encode_ko_text(ko, tbl, hangul_marker_code=marker), tbl
            )
        except Exception:
            continue
        if got == expect:
            already_ok.add(abs_off)
    if already_ok:
        report["notes"].append(
            f"Pre-marked {len(already_ok)} abs already decoding to sheet KO."
        )
    report["already_ok_count"] = len(already_ok)

    def pick_progress_glyphs(budget: int) -> List[str]:
        """Spend glyph budget to unlock assignable targets (sole first, then early abs)."""
        available = set(tbl.char_to_code.keys())
        chosen: List[str] = []
        chosen_set: Set[str] = set()

        def missing_for(abs_off: int) -> List[str] | None:
            line = abs_to_line.get(abs_off)
            if not line or abs_off in seed_abs:
                return None
            if bank_filter is not None and (abs_off >> 16) not in bank_filter:
                return None
            ko = normalize_ko_text(line["ko"])
            if is_low_quality_ko(ko) or hangul_count(ko) < args.min_hangul:
                return None
            miss: List[str] = []
            seen_local: Set[str] = set()
            for ch in ko:
                if "가" <= ch <= "힣" and ch not in available:
                    if ch not in seen_local:
                        miss.append(ch)
                        seen_local.add(ch)
            return miss

        def unlock_from(pool: List[int], max_lines: int | None = None) -> int:
            got = 0
            while len(chosen) < budget:
                if max_lines is not None and got >= max_lines:
                    break
                best_abs = None
                best_miss: List[str] | None = None
                for abs_off in pool:
                    miss = missing_for(abs_off)
                    if miss is None or len(miss) == 0:
                        continue
                    if len(chosen) + len(miss) > budget:
                        continue
                    if best_miss is None or len(miss) < len(best_miss) or (
                        len(miss) == len(best_miss) and abs_off < best_abs  # type: ignore[operator]
                    ):
                        best_abs = abs_off
                        best_miss = miss
                if best_miss is None:
                    break
                for ch in best_miss:
                    chosen.append(ch)
                    chosen_set.add(ch)
                    available.add(ch)
                got += 1
            return got

        free_n = len(reclaimable_slots(rom, dictionary0, set()))
        sole_pool = sorted(
            a for a in sole_abs0 if bank_filter is None or (a >> 16) in bank_filter
        )
        early_pool = sorted(
            a
            for a in abs_to_line
            if a not in seed_abs
            and (bank_filter is None or (a >> 16) in bank_filter)
        )
        # Prefer abs order for the free-slot reservation (not cheapest-first).
        # Skip lines that need too many new glyphs so one Bing line can't eat the budget.
        n_free_reserved = 0
        for abs_off in early_pool:
            if n_free_reserved >= max(free_n, 1):
                break
            miss = missing_for(abs_off)
            if miss is None:
                continue
            if len(miss) == 0:
                # Already encodable — counts as reserved target.
                n_free_reserved += 1
                continue
            if len(miss) > 6:
                continue
            if len(chosen) + len(miss) > budget:
                continue
            for ch in miss:
                chosen.append(ch)
                chosen_set.add(ch)
                available.add(ch)
            n_free_reserved += 1

        n_sole = unlock_from(sole_pool)
        n_early = unlock_from(early_pool)
        report["notes"].append(
            f"Glyph budget: free_reserved~={n_free_reserved} sole_lines~={n_sole} "
            f"extra_early~={n_early} (budget {budget}, free_slots={free_n})."
        )

        if len(chosen) < budget:
            missing = missing_hangul_freq(sheet, tbl, seed_abs)
            for ch, _ in missing.most_common():
                if ch in chosen_set or ch in tbl.char_to_code:
                    continue
                chosen.append(ch)
                chosen_set.add(ch)
                if len(chosen) >= budget:
                    break
        return chosen

    # --- Unit: append a few glyphs ---
    glyph_info: List[dict] = []
    if args.add_glyphs > 0:
        to_add = pick_progress_glyphs(args.add_glyphs)
        glyph_info = append_glyphs(rom, tbl, to_add, font_path)
        report["notes"].append(
            f"Appended {len(glyph_info)} glyphs starting after existing Hangul codes "
            f"(primary window only; left unused E7xx original bytes untouched)."
        )
    else:
        report["notes"].append("Skipped glyph append (--add-glyphs 0).")
    report["glyphs_added"] = glyph_info


    # Persist TBL early so encoding uses new chars.
    lines_tbl = ["# Mono-Eye Hangul patch TBL (safe incremental)"]
    for code, ch in sorted(tbl.code_to_char.items()):
        lines_tbl.append(f"{code:02X}={ch}" if code <= 0xFF else f"{code:04X}={ch}")
    args.out_tbl.write_text("\n".join(lines_tbl) + "\n", encoding="utf-8")

    dictionary = Dictionary(rom)

    # Map seed KO -> dict index from current ROM bodies.
    seed_text_to_idx: Dict[str, int] = {}
    for line in seed_lines:
        abs_off = int(line["abs"], 16)
        payload, _ = read_encoded_z(rom, abs_off)
        prefix, body, _ = split_prefix_body(payload)
        if len(body) >= 2 and is_dict_token(body[0]):
            seed_text_to_idx[normalize_ko_text(line["ko"])] = dict_index_from_token(
                body[0], body[1]
            )

    # --- Unit: reuse existing seed dict tokens ---
    patched_abs: Set[int] = set(seed_abs) | set(already_ok)
    if not args.no_reuse:
        for abs_off, line in abs_to_line.items():
            if abs_off in patched_abs:
                continue
            ko = normalize_ko_text(line["ko"])
            idx = seed_text_to_idx.get(ko)
            if idx is None:
                continue
            original = read_record_at(rom, abs_off)
            prefix, body, _ = split_prefix_body(original)
            if len(body) < 2:
                continue
            token = token_from_dict_index(idx)
            new_payload = padded_token_payload(prefix, token, original)
            rom[abs_off : abs_off + len(original)] = new_payload
            patched_abs.add(abs_off)
            report["reuse_patches"].append(
                {"abs": f"{abs_off:06X}", "ko": ko, "dict_index": idx}
            )
    else:
        report["notes"].append("Skipped seed-KO reuse (--no-reuse).")

    # --- Unit: unanimous fully-covered dict clusters ---
    # All script refs have the same encodable KO and no nested dict refs.
    # Spill-write KO into the slot, then force each abs body to a pure token
    # (compound JP tails are dropped — sheet KO is the full line).
    if args.no_shared_rewrite:
        report["notes"].append("Skipped shared dict rewrite (--no-shared-rewrite).")
    else:
        dictionary = Dictionary(rom)
        script_locs, dict_refs, non_script_hit = build_ref_maps(rom, dictionary)
        shared_payload: Dict[int, bytes] = {}
        shared_plan: List[Tuple[str, int, List[int]]] = []

        for idx in range(dictionary.count):
            if len(shared_plan) >= args.max_shared_slots:
                break
            locs = script_locs.get(idx) or []
            if len(locs) < 1 or dict_refs[idx] != 0 or idx in non_script_hit:
                continue
            active = [a for a in locs if a not in patched_abs]
            if not active:
                continue
            kos: List[str] = []
            skip = False
            # All refs (including already-patched) must agree on KO + leading token
            # so rewriting the shared slot stays consistent.
            for abs_off in locs:
                line = abs_to_line.get(abs_off)
                if not line:
                    skip = True
                    break
                original = read_record_at(rom, abs_off)
                prefix, body, _ = split_prefix_body(original)
                if len(prefix) + 2 > len(original):
                    skip = True
                    break
                if (
                    len(body) < 2
                    or not is_dict_token(body[0])
                    or dict_index_from_token(body[0], body[1]) != idx
                ):
                    skip = True
                    break
                ko = normalize_ko_text(line["ko"])
                if is_low_quality_ko(ko) or hangul_count(ko) < args.min_hangul:
                    skip = True
                    break
                if try_encode_ko_text(ko, tbl, hangul_marker_code=marker) is None:
                    skip = True
                    break
                kos.append(ko)
            if skip or not kos or len(set(kos)) != 1:
                continue
            ko = kos[0]
            shared_payload[idx] = encode_ko_text(ko, tbl, hangul_marker_code=marker)
            shared_plan.append((ko, idx, list(active)))

        if shared_payload:
            write_dictionary_slots_spill(rom, shared_payload)
            for ko, idx, abs_list in shared_plan:
                token = token_from_dict_index(idx)
                for abs_off in abs_list:
                    original = read_record_at(rom, abs_off)
                    prefix, _body, _ = split_prefix_body(original)
                    new_payload = padded_token_payload(prefix, token, original)
                    rom[abs_off : abs_off + len(original)] = new_payload
                    patched_abs.add(abs_off)
                    report["shared_rewrite_patches"].append(
                        {
                            "abs": f"{abs_off:06X}",
                            "ko": ko,
                            "dict_index": idx,
                            "mode": "cluster_unanimous",
                        }
                    )
            report["notes"].append(
                f"Cluster unanimous: {len(shared_payload)} slots → "
                f"{len(report['shared_rewrite_patches'])} lines."
            )
        else:
            report["notes"].append("Cluster unanimous: no candidates.")

    # --- Unit: multi-KO fully-covered clusters (split) ---
    # Every script ref is encodable quality KO (possibly different texts), leading
    # token is this idx, no nested dict refs. Reclaim slots by excluding the
    # cluster abs set, then assign one slot per unique KO and force pure tokens.
    if not args.no_shared_rewrite and args.max_shared_slots > 0:
        dictionary = Dictionary(rom)
        script_locs, dict_refs, non_script_hit = build_ref_maps(rom, dictionary)
        clusters: List[Tuple[int, Dict[str, List[int]]]] = []
        for idx in range(dictionary.count):
            locs = script_locs.get(idx) or []
            if len(locs) < 2 or dict_refs[idx] != 0 or idx in non_script_hit:
                continue
            active = [a for a in locs if a not in patched_abs]
            if len(active) < 1:
                continue
            ko_to_abs: Dict[str, List[int]] = defaultdict(list)
            skip = False
            for abs_off in locs:
                line = abs_to_line.get(abs_off)
                if not line:
                    skip = True
                    break
                original = read_record_at(rom, abs_off)
                prefix, body, _ = split_prefix_body(original)
                if len(prefix) + 2 > len(original):
                    skip = True
                    break
                if (
                    len(body) < 2
                    or not is_dict_token(body[0])
                    or dict_index_from_token(body[0], body[1]) != idx
                ):
                    skip = True
                    break
                ko = normalize_ko_text(line["ko"])
                if is_low_quality_ko(ko) or hangul_count(ko) < args.min_hangul:
                    skip = True
                    break
                if try_encode_ko_text(ko, tbl, hangul_marker_code=marker) is None:
                    skip = True
                    break
                ko_to_abs[ko].append(abs_off)
            if skip or len(ko_to_abs) < 2:
                continue
            clusters.append((idx, dict(ko_to_abs)))

        multi_payload: Dict[int, bytes] = {}
        multi_plan: List[Tuple[str, int, List[int]]] = []
        # Small clusters first so limited free slots still unlock some groups.
        clusters.sort(key=lambda c: (len(c[1]), -sum(len(v) for v in c[1].values())))
        planned_abs: Set[int] = set()
        for idx, ko_to_abs in clusters:
            if len(multi_plan) >= args.max_shared_slots:
                break
            cluster_abs = {a for abs_list in ko_to_abs.values() for a in abs_list}
            if cluster_abs & planned_abs:
                continue
            exclude = planned_abs | cluster_abs
            usable = [
                i
                for i in reclaimable_slots(rom, dictionary, exclude)
                if i not in multi_payload
            ]
            uniq = sorted(
                ko_to_abs.keys(),
                key=lambda k: (-len(ko_to_abs[k]), k),
            )
            if len(usable) < len(uniq):
                continue
            # Prefer keeping the original cluster index for the top KO.
            if idx in usable:
                usable = [idx] + [i for i in usable if i != idx]
            for ko, slot in zip(uniq, usable):
                multi_payload[slot] = encode_ko_text(
                    ko, tbl, hangul_marker_code=marker
                )
                multi_plan.append((ko, slot, list(ko_to_abs[ko])))
            planned_abs |= cluster_abs

        if multi_payload:
            write_dictionary_slots_spill(rom, multi_payload)
            before = len(report["shared_rewrite_patches"])
            for ko, slot, abs_list in multi_plan:
                token = token_from_dict_index(slot)
                for abs_off in abs_list:
                    original = read_record_at(rom, abs_off)
                    prefix, _body, _ = split_prefix_body(original)
                    new_payload = padded_token_payload(prefix, token, original)
                    rom[abs_off : abs_off + len(original)] = new_payload
                    patched_abs.add(abs_off)
                    report["shared_rewrite_patches"].append(
                        {
                            "abs": f"{abs_off:06X}",
                            "ko": ko,
                            "dict_index": slot,
                            "mode": "cluster_multi_split",
                        }
                    )
            report["notes"].append(
                f"Cluster multi-split: {len(multi_payload)} slots → "
                f"{len(report['shared_rewrite_patches']) - before} lines."
            )
        else:
            report["notes"].append("Cluster multi-split: no fully-funded candidates.")

    # --- Unit: new KO via free + optional sole-owned slots ---
    if args.max_new_slots <= 0:
        report["notes"].append("Skipped new dict slots (--max-new-slots 0).")
        assignments = []
        slot_payload = {}
    else:
        dictionary = Dictionary(rom)
        script_locs, dict_refs, non_script_hit = build_ref_maps(rom, dictionary)
        sole = [
            (idx, locs[0])
            for idx, locs in script_locs.items()
            if len(locs) == 1 and dict_refs[idx] == 0 and idx not in non_script_hit
        ]
        sole_abs_to_idx = {abs_off: idx for idx, abs_off in sole}
        free_true = list(reclaimable_slots(rom, dictionary, set()))

        def in_bank_filter(abs_off: int) -> bool:
            if bank_filter is not None and (abs_off >> 16) not in bank_filter:
                return False
            if args.abs_limit is not None and abs_off >= args.abs_limit:
                return False
            return True

        # Candidate unique texts among unpatched encodable quality lines.
        text_to_abs: Dict[str, List[int]] = defaultdict(list)
        for abs_off, line in abs_to_line.items():
            if abs_off in patched_abs:
                continue
            if not in_bank_filter(abs_off):
                continue
            ko = normalize_ko_text(line["ko"])
            if is_low_quality_ko(ko) or hangul_count(ko) < args.min_hangul:
                continue
            if try_encode_ko_text(ko, tbl, hangul_marker_code=marker) is None:
                continue
            original = read_record_at(rom, abs_off)
            _prefix, body, _ = split_prefix_body(original)
            if len(body) < 2:
                continue
            text_to_abs[ko].append(abs_off)

        # Prefer high-frequency texts for reclaim (covers more abs → frees more slots),
        # then near-opening abs, then more Hangul.
        ranked = sorted(
            text_to_abs.items(),
            key=lambda kv: (
                -len(kv[1]),
                min(kv[1]),
                -hangul_count(kv[0]),
            ),
        )

        # Slot pool: truly free first; sole only with --allow-sole.
        available_slots: List[int] = list(free_true)
        if args.allow_sole:
            for idx, abs_off in sole:
                if abs_off in patched_abs or not in_bank_filter(abs_off):
                    continue
                line = abs_to_line.get(abs_off)
                if not line:
                    continue
                ko = normalize_ko_text(line["ko"])
                if ko not in text_to_abs:
                    continue
                available_slots.append(idx)
        else:
            report["notes"].append("Sole dict reclaim disabled (pass --allow-sole to enable).")

        seen_slot: Set[int] = set()
        slots: List[int] = []
        for idx in available_slots:
            if idx not in seen_slot:
                seen_slot.add(idx)
                slots.append(idx)

        slot_payload: Dict[int, bytes] = {}
        assignments: List[Tuple[str, int, List[int]]] = []

        # Pass A: reclaim-by-exclude first (main capacity unlock).
        remaining = args.max_new_slots
        if remaining > 0 and ranked:
            lo, hi = 0, min(remaining, len(ranked))
            best_texts: List[str] = []
            best_slots: List[int] = []
            while lo < hi:
                mid = (lo + hi + 1) // 2
                texts_k = [ko for ko, _ in ranked[:mid]]
                exclude = {
                    abs_off
                    for ko in texts_k
                    for abs_off in text_to_abs[ko]
                    if abs_off not in patched_abs
                }
                free_k = reclaimable_slots(rom, dictionary, exclude)
                usable = [i for i in free_k if i not in slot_payload]
                if len(usable) >= mid:
                    lo = mid
                    best_texts = texts_k
                    best_slots = usable
                else:
                    hi = mid - 1

            slot_iter = iter(best_slots)
            reclaimed = 0
            for ko in best_texts:
                if len(assignments) >= args.max_new_slots:
                    break
                try:
                    chosen = next(slot_iter)
                except StopIteration:
                    break
                slot_payload[chosen] = encode_ko_text(
                    ko, tbl, hangul_marker_code=marker
                )
                targets = [a for a in text_to_abs[ko] if a not in patched_abs]
                if not targets:
                    continue
                assignments.append((ko, chosen, targets))
                reclaimed += 1

            report["notes"].append(
                f"Reclaim-by-exclude: claimed {reclaimed}/{len(best_texts)} unique "
                f"(usable_slots≈{len(best_slots)})."
            )
        else:
            report["notes"].append("Reclaim-by-exclude skipped (no budget or candidates).")

        # Pass B: sole reclaim (optional) for remaining budget.
        if args.allow_sole:
            for abs_off in sorted(
                a for abs_list in text_to_abs.values() for a in abs_list
            ):
                if len(assignments) >= args.max_new_slots:
                    break
                if abs_off in patched_abs:
                    continue
                idx = sole_abs_to_idx.get(abs_off)
                if idx is None or idx in slot_payload:
                    continue
                if idx not in slots:
                    continue
                line = abs_to_line[abs_off]
                ko = normalize_ko_text(line["ko"])
                if ko not in text_to_abs:
                    continue
                if any(a[0] == ko for a in assignments):
                    continue
                slot_payload[idx] = encode_ko_text(ko, tbl, hangul_marker_code=marker)
                assignments.append((ko, idx, [abs_off]))
        else:
            report["notes"].append(
                "Sole dict reclaim disabled (pass --allow-sole to enable)."
            )
        expanded_assignments: List[Tuple[str, int, List[int]]] = []
        used_abs: Set[int] = set()
        for ko, idx, abs_list in assignments:
            extra = [
                a
                for a in text_to_abs.get(ko, [])
                if a not in used_abs and a not in patched_abs
            ]
            merged = []
            for a in abs_list + extra:
                if a not in used_abs:
                    merged.append(a)
                    used_abs.add(a)
            expanded_assignments.append((ko, idx, merged))
        assignments = expanded_assignments

        report["notes"].append(
            f"New-slot banks={args.banks!r}; quality filter on; "
            f"free_slots={len(free_true)} allow_sole={args.allow_sole} "
            f"abs_limit={args.abs_limit}"
        )
        report["assigned_texts"] = [
            {"ko": ko, "dict_index": idx, "abs_count": len(abs_list)}
            for ko, idx, abs_list in assignments
        ]

        if slot_payload:
            write_dictionary_slots_spill(rom, slot_payload)

        for ko, idx, abs_list in assignments:
            token = token_from_dict_index(idx)
            for abs_off in abs_list:
                if abs_off in patched_abs:
                    continue
                original = read_record_at(rom, abs_off)
                prefix, body, _ = split_prefix_body(original)
                if len(body) < 2:
                    continue
                new_payload = padded_token_payload(prefix, token, original)
                rom[abs_off : abs_off + len(original)] = new_payload
                patched_abs.add(abs_off)
                report["new_slot_patches"].append(
                    {
                        "abs": f"{abs_off:06X}",
                        "ko": ko,
                        "dict_index": idx,
                    }
                )

    # Verify patched rows decode
    d2 = Dictionary(rom)
    fails = []
    checks = (
        report["reuse_patches"]
        + report["shared_rewrite_patches"]
        + report["new_slot_patches"]
    )
    for row in checks:
        abs_off = int(row["abs"], 16)
        payload, _ = read_encoded_z(rom, abs_off)
        _prefix, body, _ = split_prefix_body(payload)
        decoded = d2.expand(body, tbl)
        expect = d2.expand(
            encode_ko_text(row["ko"], tbl, hangul_marker_code=marker), tbl
        )
        row["decode_check"] = decoded
        row["ok"] = decoded == expect
        if not row["ok"]:
            fails.append(row["abs"])

    report["checksum"] = f"{update_ws_checksum(rom):04X}"
    report["lines_patched_total"] = (
        len(seed_abs) + len(already_ok) + len(checks)
    )
    report["reuse_count"] = len(report["reuse_patches"])
    report["shared_rewrite_count"] = len(report["shared_rewrite_patches"])
    report["shared_rewrite_slots"] = len(
        {p["dict_index"] for p in report["shared_rewrite_patches"]}
    )
    report["new_slot_line_count"] = len(report["new_slot_patches"])
    report["new_unique_texts"] = len(assignments)
    report["decode_failures"] = fails
    report["hangul_glyph_count"] = sum(
        1 for c in tbl.code_to_char if isinstance(c, int) and c >= 0xE740
    )

    args.out_rom.write_bytes(rom)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # refresh hangul map snippet
    map_path = ROOT / "out" / "patch" / "hangul_char_map.json"
    if map_path.exists():
        mapping = json.loads(map_path.read_text(encoding="utf-8"))
        mapping["new_char_count"] = report["hangul_glyph_count"]
        mapping["safe_unit_glyphs_added"] = glyph_info
        map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Safe unit OK | glyphs+={len(glyph_info)} reuse={report['reuse_count']} "
        f"shared={report['shared_rewrite_count']} "
        f"new_unique={report['new_unique_texts']} new_lines={report['new_slot_line_count']} "
        f"decode_fail={len(fails)} checksum={report['checksum']}"
    )
    print(f"Wrote {args.out_rom}")
    print(f"Wrote {args.out_tbl}")
    print(f"Wrote {args.out_report}")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
