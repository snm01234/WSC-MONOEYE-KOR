#!/usr/bin/env python3
"""
Safe sole-fit stock dictionary reclaim for early sequential KO coverage.

Finds stock dict indices with exactly one *external* consumer, and that
consumer must be a script dialogue record (not bank75 names / aux UI text).
References are gathered via expand_dictionary.build_dict_token_locs
(script + name75 + aux). Nested phrase tokens remain banned.

Mode2 expands JP into the sole owner (SPACE pad), spill-writes KO into the
reclaimed slot, and retargets early sequential lines with dict tokens.

Does NOT touch shared JP slots, force-format ext dict, or wipe bank30.
Spill writes only — no full dictionary rebuild.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_safe_unit import padded_token_payload, read_record_at  # noqa: E402
from event_record_heuristics import looks_like_event_body  # noqa: E402
from expand_dictionary import (  # noqa: E402
    DEFAULT_REF_REGIONS,
    DictTokenRef,
    build_dict_token_locs,
    iter_dict_indices,
    write_dictionary_slots_spill,
)
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    dict_index_from_token,
    dict_token_safe_in_zstring,
    is_dict_token,
    is_kanji_lead,
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
from patch_ext_dictionary import STOCK_DICT_COUNT  # noqa: E402

SPACE = 0x01
DEFAULT_MARKER = 0xE3DB
# Prefer early_tut; ep3 window is the wider sheet default.
DEFAULT_LO = 0x60456B
DEFAULT_HI = 0x607000
DEFAULT_SHEET = ROOT / "out" / "script" / "translations_ep3_window.json"


def file_abs(rom: bytes | bytearray, logical_abs: int) -> int:
    return stock_base(rom) + logical_abs


def walk_dialogue(
    rom: bytes | bytearray, seg_lo: int = 0x60, seg_hi: int = 0x6F
) -> Iterable[Tuple[int, bytes, bytes, int]]:
    """Yield (logical_abs, prefix, body, logical_term) for dialogue records."""
    sb = stock_base(rom)
    for seg in range(seg_lo, seg_hi + 1):
        if seg in (0x5E, 0x5F):
            continue
        off = sb + (seg << 16)
        end = off + 0x10000
        while off < end:
            if rom[off] == 0:
                off += 1
                continue
            got = read_encoded_z_safe(rom, off)
            if got is None:
                off += 1
                continue
            payload, term = got
            prefix, body, kind = split_prefix_body(payload)
            logical = off - sb
            if kind == "dialogue":
                yield logical, prefix, body, term - sb
            off = term + 1


def payload_has_hangul_marker(payload: bytes, marker: int = DEFAULT_MARKER) -> bool:
    """True if payload looks like already-patched KO (hangul marker present)."""
    hi, lo = (marker >> 8) & 0xFF, marker & 0xFF
    i = 0
    while i < len(payload) - 1:
        if payload[i] == hi and payload[i + 1] == lo:
            return True
        i += 1
    return False


def body_is_pure_sole_token(body: bytes, idx: int) -> bool:
    """
    Body is only this dict token plus SPACE pads — safe to expand JP over it
    without destroying trailing dialogue bytes.
    """
    core = bytes(b for b in body if b != SPACE)
    return (
        len(core) == 2
        and is_dict_token(core[0])
        and dict_index_from_token(core[0], core[1]) == idx
    )


def build_script_locs(rom: bytes | bytearray) -> Dict[int, List[int]]:
    """Legacy helper: dialogue-only locs in banks 60–6F (tests / diagnostics)."""
    locs: Dict[int, List[int]] = defaultdict(list)
    for abs_off, _prefix, body, _term in walk_dialogue(rom):
        i = 0
        while i < len(body) - 1:
            if is_dict_token(body[i]):
                idx = dict_index_from_token(body[i], body[i + 1])
                locs[idx].append(abs_off)
                i += 2
            elif is_kanji_lead(body[i]):
                i += 2
            else:
                i += 1
    return locs


def build_dict_nested_refs(dictionary: Dictionary) -> Set[int]:
    nested: Set[int] = set()
    for i in range(dictionary.count):
        try:
            raw = dictionary.raw_entry(i)
        except Exception:
            continue
        for idx in iter_dict_indices(raw):
            if 0 <= idx < dictionary.count:
                nested.add(idx)
    return nested


def _sole_dialogue_owner(
    refs: Sequence[DictTokenRef],
) -> Optional[int]:
    """
    Return owner abs if refs are exactly one script/dialogue consumer.

    Any name75/aux (or extra script) hit → not sole.
    """
    if len(refs) != 1:
        return None
    ref = refs[0]
    if ref.region != "script" or ref.kind != "dialogue":
        return None
    return ref.abs


def owner_displace_healthy(
    rom: bytes | bytearray,
    owner: int,
    idx: int,
    jp: bytes,
    *,
    base_rom: bytes | None = None,
) -> Tuple[bool, str]:
    """Fail-closed checks before Mode2 inline displace."""
    got = read_encoded_z_safe(rom, file_abs(rom, owner))
    if got is None:
        return False, "owner_unreadable"
    prefix, body, kind = split_prefix_body(got[0])
    if kind != "dialogue":
        return False, "owner_not_dialogue"
    if looks_like_event_body(body):
        return False, "owner_event_body"
    if not body_is_pure_sole_token(body, idx):
        return False, "owner_not_pure_sole_token"
    if len(jp) > len(body):
        return False, "jp_longer_than_body"
    # Reject already-trashed owners (e.g. 63C21D padding / leftover bytes).
    space_n = sum(1 for b in body if b == SPACE)
    if len(body) >= 4 and space_n * 2 >= len(body):
        return False, "owner_mostly_space_pad"
    if base_rom is not None:
        base_got = read_encoded_z_safe(base_rom, owner)
        if base_got is None:
            return False, "base_owner_unreadable"
        _bp, bbody, bkind = split_prefix_body(base_got[0])
        if bkind != "dialogue" or looks_like_event_body(bbody):
            return False, "base_owner_not_safe_dialogue"
        if not body_is_pure_sole_token(bbody, idx):
            return False, "base_owner_not_pure_sole_token"
    return True, "ok"


def _reject_counts(refs: Sequence[DictTokenRef]) -> Dict[str, int]:
    out: Dict[str, int] = defaultdict(int)
    if len(refs) != 1:
        out["rejected_not_unique"] += 1
        for r in refs:
            if r.region != "script":
                out[f"rejected_region_{r.region}"] += 1
        return out
    ref = refs[0]
    if ref.region != "script":
        out[f"rejected_region_{ref.region}"] += 1
    elif ref.kind != "dialogue":
        out["rejected_not_dialogue"] += 1
    return out


def consumer_locs(
    rom: bytes | bytearray,
    *,
    ref_regions: Sequence[str] = DEFAULT_REF_REGIONS,
    ref_rom: bytes | None = None,
):
    """Token references, unioned across the work ROM and the pristine original.

    Enumerating consumers on the work ROM alone is not safe. The aux scan walks
    zstrings, so a single corrupted terminator in banks 50-5F hides every record
    behind it — the shared slots those records read then look *sole* and get
    reclaimed, which is exactly how the intermission / HUD text ended up being
    driven by dialogue phrases (design hypothesis A3). Scanning the original as
    well and taking the union is fail-closed: a reference seen in either image
    keeps the slot off the reclaim list.
    """
    locs = build_dict_token_locs(rom, regions=ref_regions)
    if ref_rom is None:
        return locs
    merged = {idx: list(refs) for idx, refs in locs.items()}
    for idx, refs in build_dict_token_locs(ref_rom, regions=ref_regions).items():
        merged.setdefault(idx, []).extend(refs)
    return merged


def find_sole_fit_slots(
    rom: bytes | bytearray,
    dictionary: Dictionary,
    *,
    seed_abs: Set[int],
    ref_rom: bytes | None = None,
    stock_count: int = STOCK_DICT_COUNT,
    marker: int = DEFAULT_MARKER,
    prefer_owner_outside: Optional[Tuple[int, int]] = None,
    base_rom: bytes | None = None,
    ref_regions: Sequence[str] = DEFAULT_REF_REGIONS,
    reject_stats: Optional[Dict[str, int]] = None,
) -> List[Tuple[int, int, bytes]]:
    """
    Mode-2 candidates: (dict_index, owner_abs, jp_payload).

    Safe means: exactly one external ref and it is script/dialogue; not nested;
    zstring-safe token; slot still JP; owner pure sole-token + healthy displace.
    """
    locs = consumer_locs(rom, ref_regions=ref_regions, ref_rom=ref_rom)
    nested = build_dict_nested_refs(dictionary)
    stats = reject_stats if reject_stats is not None else {}
    candidates: List[Tuple[int, int, bytes, int]] = []
    for idx, refs in locs.items():
        owner = _sole_dialogue_owner(refs)
        if owner is None:
            for k, v in _reject_counts(refs).items():
                stats[k] = stats.get(k, 0) + v
            continue
        if idx >= stock_count or idx in nested:
            stats["rejected_nested_or_ext"] = stats.get("rejected_nested_or_ext", 0) + 1
            continue
        if not dict_token_safe_in_zstring(idx):
            stats["rejected_nul_token"] = stats.get("rejected_nul_token", 0) + 1
            continue
        if owner in seed_abs:
            stats["rejected_seed_owner"] = stats.get("rejected_seed_owner", 0) + 1
            continue
        try:
            jp = dictionary.raw_entry(idx)
        except Exception:
            stats["rejected_slot_read"] = stats.get("rejected_slot_read", 0) + 1
            continue
        if not jp or payload_has_hangul_marker(jp, marker):
            stats["rejected_slot_not_jp"] = stats.get("rejected_slot_not_jp", 0) + 1
            continue
        ok, reason = owner_displace_healthy(
            rom, owner, idx, jp, base_rom=base_rom
        )
        if not ok:
            stats[f"rejected_{reason}"] = stats.get(f"rejected_{reason}", 0) + 1
            continue
        outside = 0
        if prefer_owner_outside is not None:
            lo, hi = prefer_owner_outside
            outside = 0 if lo <= owner <= hi else 1
        candidates.append((idx, owner, jp, outside))

    candidates.sort(key=lambda t: (-t[3], t[1], t[0]))
    return [(idx, owner, jp) for idx, owner, jp, _out in candidates]


def find_sole_owner_ko_slots(
    rom: bytes | bytearray,
    dictionary: Dictionary,
    tbl: Tbl,
    abs_to_line: Dict[int, dict],
    *,
    seed_abs: Set[int],
    lo: int,
    hi: int,
    ref_rom: bytes | None = None,
    marker: int = DEFAULT_MARKER,
    stock_count: int = STOCK_DICT_COUNT,
    base_rom: bytes | None = None,
    ref_regions: Sequence[str] = DEFAULT_REF_REGIONS,
    reject_stats: Optional[Dict[str, int]] = None,
) -> List[Tuple[str, int, int]]:
    """
    Mode-1 candidates: (ko, dict_index, owner_abs).

    Sole external consumer must be script/dialogue inside [lo,hi]; still needs KO.
    """
    locs = consumer_locs(rom, ref_regions=ref_regions, ref_rom=ref_rom)
    nested = build_dict_nested_refs(dictionary)
    base = base_rom if base_rom is not None else bytes(rom)
    stats = reject_stats if reject_stats is not None else {}
    out: List[Tuple[str, int, int]] = []
    for idx, refs in locs.items():
        owner = _sole_dialogue_owner(refs)
        if owner is None:
            for k, v in _reject_counts(refs).items():
                stats[k] = stats.get(k, 0) + v
            continue
        if idx >= stock_count or idx in nested:
            stats["rejected_nested_or_ext"] = stats.get("rejected_nested_or_ext", 0) + 1
            continue
        if not dict_token_safe_in_zstring(idx):
            continue
        if owner in seed_abs or owner < lo or owner > hi:
            continue
        line = abs_to_line.get(owner)
        if not line:
            continue
        ko = normalize_ko_text(line.get("ko") or "")
        if not ko or is_low_quality_ko(ko):
            continue
        if "<E" in ko.upper() or "<BADDICT" in ko.upper():
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if enc is None:
            continue
        try:
            base_got = read_encoded_z_safe(base, owner)
            if base_got is None:
                continue
            if looks_like_event_body(split_prefix_body(base_got[0])[1]):
                continue
            got = read_encoded_z_safe(rom, file_abs(rom, owner))
            if got is None:
                continue
            original = got[0]
            prefix, body, kind = split_prefix_body(original)
            if kind != "dialogue" or len(body) < 2:
                continue
            if len(prefix) + 2 > len(original):
                continue
            if dictionary.expand(body, tbl) == dictionary.expand(enc, tbl):
                continue
        except Exception:
            continue
        out.append((ko, idx, owner))
    out.sort(key=lambda t: (t[2], t[1]))
    return out


def early_abs_band(abs_off: int) -> int:
    if abs_off <= 0x607000:
        return 0
    if abs_off <= 0x60FFFF:
        return 1
    if abs_off <= 0x61FFFF:
        return 2
    return 3


def collect_early_targets(
    rom: bytes | bytearray,
    dictionary: Dictionary,
    tbl: Tbl,
    abs_to_line: Dict[int, dict],
    *,
    seed_abs: Set[int],
    lo: int,
    hi: int,
    marker: int,
    base_rom: bytes | None = None,
) -> List[Tuple[str, List[int]]]:
    """Unique quality KO texts still needing coverage in [lo, hi], early-abs ranked."""
    text_to_abs: Dict[str, List[int]] = defaultdict(list)
    base = base_rom if base_rom is not None else bytes(rom)
    for abs_off, line in abs_to_line.items():
        if abs_off in seed_abs or abs_off < lo or abs_off > hi:
            continue
        ko = normalize_ko_text(line.get("ko") or "")
        if not ko or is_low_quality_ko(ko):
            continue
        if "<E" in ko.upper() or "<BADDICT" in ko.upper():
            continue
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if enc is None:
            continue
        try:
            base_got = read_encoded_z_safe(base, abs_off)
            if base_got is None:
                continue
            base_body = split_prefix_body(base_got[0])[1]
            if looks_like_event_body(base_body):
                continue
            got = read_encoded_z_safe(rom, file_abs(rom, abs_off))
            if got is None:
                continue
            original = got[0]
            prefix, body, kind = split_prefix_body(original)
            if kind != "dialogue" or len(body) < 2:
                continue
            if len(prefix) + 2 > len(original):
                continue
            if dictionary.expand(body, tbl) == dictionary.expand(enc, tbl):
                continue
        except Exception:
            continue
        text_to_abs[ko].append(abs_off)

    ranked = sorted(
        text_to_abs.items(),
        key=lambda kv: (early_abs_band(min(kv[1])), min(kv[1]), -len(kv[1])),
    )
    return ranked


def inline_jp_into_owner(
    rom: bytearray, owner: int, jp_payload: bytes
) -> dict:
    """Expand JP into sole owner body; pad with SPACE. Returns displace info."""
    op, _ = read_encoded_z(rom, file_abs(rom, owner))
    opref, obody, okind = split_prefix_body(op)
    if okind != "dialogue" or len(jp_payload) > len(obody):
        raise RuntimeError(f"cannot inline JP at {owner:06X}")
    new_body = jp_payload + bytes([SPACE]) * (len(obody) - len(jp_payload))
    abs_off = file_abs(rom, owner)
    rom[abs_off : abs_off + len(opref) + len(new_body)] = opref + new_body
    return {
        "owner": f"{owner:06X}",
        "jp_len": len(jp_payload),
        "body_len": len(obody),
    }


def apply_sole_reclaim(
    rom: bytearray,
    tbl: Tbl,
    *,
    abs_to_line: Dict[int, dict],
    seed_abs: Set[int],
    lo: int = DEFAULT_LO,
    hi: int = DEFAULT_HI,
    marker: int = DEFAULT_MARKER,
    max_slots: int = 0,
    stock_count: int = STOCK_DICT_COUNT,
    base_rom: bytes | None = None,
    ref_rom: bytes | None = None,
    skip_abs: Optional[Set[int]] = None,
    dry_run: bool = False,
) -> dict:
    """
    Reclaim sole-fit stock slots for early sequential KO.

    Mutates rom unless dry_run. Returns a JSON-serializable report.
    skip_abs: already-covered lines to ignore as targets / sole owners
    (separate from seed_abs, which is the seed regression gate).
    """
    protect = set(seed_abs) | (set(skip_abs) if skip_abs else set())
    dictionary = Dictionary(rom)
    reject_stats: Dict[str, int] = {}

    # Invasion guard: battle/UI banks 50–5F must be in the ref scan.
    # Script-only scans falsely mark shared fragments as sole (dict[21] leak class).
    if "aux" not in DEFAULT_REF_REGIONS or "name75" not in DEFAULT_REF_REGIONS:
        raise RuntimeError(
            "sole reclaim refuse: DEFAULT_REF_REGIONS must include "
            f"'script'+'name75'+'aux' (got {DEFAULT_REF_REGIONS!r})"
        )

    # Mode 1 first: sole owners inside the early window get their own KO.
    # Mode 2: pure-token sole JP slots → displace owner, reuse for other early uniques.
    owner_ko_all = find_sole_owner_ko_slots(
        rom,
        dictionary,
        tbl,
        abs_to_line,
        seed_abs=protect,
        lo=lo,
        hi=hi,
        ref_rom=ref_rom,
        marker=marker,
        stock_count=stock_count,
        base_rom=base_rom,
        reject_stats=reject_stats,
    )
    sole_fit_all = find_sole_fit_slots(
        rom,
        dictionary,
        seed_abs=protect,
        ref_rom=ref_rom,
        stock_count=stock_count,
        marker=marker,
        prefer_owner_outside=(lo, hi),
        base_rom=base_rom,
        reject_stats=reject_stats,
    )
    mode1_indices = {idx for _ko, idx, _owner in owner_ko_all}
    sole_fit_all = [t for t in sole_fit_all if t[0] not in mode1_indices]

    targets = collect_early_targets(
        rom,
        dictionary,
        tbl,
        abs_to_line,
        seed_abs=protect | {o for _k, _i, o in owner_ko_all},
        lo=lo,
        hi=hi,
        marker=marker,
        base_rom=base_rom,
    )
    mode1_kos = {ko for ko, _idx, _owner in owner_ko_all}
    targets = [(ko, abs_list) for ko, abs_list in targets if ko not in mode1_kos]

    owner_ko = list(owner_ko_all)
    sole_fit = list(sole_fit_all)
    mode2_budget = len(sole_fit)
    if max_slots > 0:
        mode1_keep = min(len(owner_ko), max_slots)
        owner_ko = owner_ko[:mode1_keep]
        mode2_budget = min(len(sole_fit), max(0, max_slots - len(owner_ko)))
    chosen_targets = targets[:mode2_budget]
    chosen_slots = sole_fit[: len(chosen_targets)]

    report: dict = {
        "enabled": True,
        "window": {"lo": f"{lo:06X}", "hi": f"{hi:06X}"},
        "ref_regions": list(DEFAULT_REF_REGIONS),
        "sole_owner_ko_available": len(owner_ko_all),
        "sole_fit_available": len(sole_fit_all),
        "early_uniques_needing_ko": len(targets),
        "reject_stats": dict(sorted(reject_stats.items())),
        "mode1_assigned": 0,
        "mode2_assigned": 0,
        "assigned": 0,
        "lines_patched": 0,
        "displaced": [],
        "applied": [],
        "skipped": [],
        "indices": [],
        "dry_run": bool(dry_run),
        "note": (
            "mode1=sole owner→own KO; mode2=inline JP into pure-token owner, "
            "reuse slot for other early uniques. Sole = exactly one external "
            "ref and it must be script/dialogue (name75/aux ban)."
        ),
    }

    # Snapshot seed decode for regression gate.
    seed_input_decode: Dict[int, str] = {}
    for abs_off in seed_abs:
        try:
            body = split_prefix_body(read_encoded_z(rom, file_abs(rom, abs_off))[0])[1]
            seed_input_decode[abs_off] = dictionary.expand(body, tbl).rstrip("\u3000")
        except Exception:
            continue

    # Plan: (mode, ko, idx, abs_list, owner|None, jp|None)
    plan: List[Tuple[str, str, int, List[int], Optional[int], Optional[bytes]]] = []
    used_indices: Set[int] = set()
    used_owners: Set[int] = set()

    for ko, idx, owner in owner_ko:
        if idx in used_indices or owner in used_owners:
            continue
        plan.append(("owner_ko", ko, idx, [owner], owner, None))
        used_indices.add(idx)
        used_owners.add(owner)

    for (ko, abs_list), (idx, owner, jp) in zip(chosen_targets, chosen_slots):
        if idx in used_indices or owner in used_owners:
            report["skipped"].append(
                {
                    "mode": "displace_reuse",
                    "ko": ko,
                    "slot": idx,
                    "reason": "index_or_owner_busy",
                    "owner": f"{owner:06X}",
                }
            )
            continue
        plan.append(("displace_reuse", ko, idx, abs_list, owner, jp))
        used_indices.add(idx)
        used_owners.add(owner)

    report["mode1_assigned"] = sum(1 for m, *_ in plan if m == "owner_ko")
    report["mode2_assigned"] = sum(1 for m, *_ in plan if m == "displace_reuse")

    if dry_run:
        report["assigned"] = len(plan)
        report["lines_patched"] = sum(len(a[3]) for a in plan)
        report["indices"] = [a[2] for a in plan]
        report["sample"] = [
            {
                "mode": mode,
                "ko": ko,
                "dict_index": idx,
                "owner": f"{owner:06X}" if owner is not None else None,
                "abs_count": len(abs_list),
                "abs_sample": [f"{a:06X}" for a in abs_list[:5]],
            }
            for mode, ko, idx, abs_list, owner, _jp in plan[:20]
        ]
        if not plan:
            report["note"] = (
                "no safe sole slots on tip (mode2 needs pure-token JP owners; "
                "mode1 needs sole owners inside window still missing KO)"
            )
        return report

    if not plan:
        report["note"] = "no sole-fit / sole-owner-ko candidates"
        report["checksum"] = f"{update_ws_checksum(rom):04X}"
        return report

    slot_payload: Dict[int, bytes] = {}
    for mode, ko, idx, abs_list, owner, jp in plan:
        if mode == "displace_reuse":
            assert owner is not None and jp is not None
            info = inline_jp_into_owner(rom, owner, jp)
            info["slot"] = idx
            info["mode"] = mode
            report["displaced"].append(info)
        slot_payload[idx] = encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )

    spill_end = None
    if slot_payload:
        _ptrs, spill_end = write_dictionary_slots_spill(rom, slot_payload)

    d2 = Dictionary(rom)
    patches = []
    fails = 0
    for mode, ko, idx, abs_list, _owner, _jp in plan:
        token = token_from_dict_index(idx)
        for abs_off in abs_list:
            try:
                original = read_record_at(rom, abs_off)
                prefix, _body, _ = split_prefix_body(original)
                new_payload = padded_token_payload(prefix, token, original)
            except Exception:
                fails += 1
                continue
            foff = file_abs(rom, abs_off)
            rom[foff : foff + len(original)] = new_payload
            got = d2.expand(split_prefix_body(new_payload)[1], tbl)
            ok = got.rstrip("\u3000") == ko or got == ko
            if not ok:
                fails += 1
            patches.append(
                {
                    "abs": f"{abs_off:06X}",
                    "dict_index": idx,
                    "ko": ko,
                    "mode": mode,
                    "ok": ok,
                    "decode": got,
                }
            )

    seed_fail = 0
    for abs_off, prev in seed_input_decode.items():
        try:
            body = split_prefix_body(read_encoded_z(rom, file_abs(rom, abs_off))[0])[1]
            got = d2.expand(body, tbl).rstrip("\u3000")
        except Exception:
            seed_fail += 1
            continue
        if got != prev:
            seed_fail += 1

    report.update(
        {
            "assigned": len(slot_payload),
            "lines_patched": len(patches),
            "indices": sorted(slot_payload),
            "spill_end": spill_end,
            "decode_fail": fails,
            "seed_fail": seed_fail,
            "applied": patches,
            "checksum": f"{update_ws_checksum(rom):04X}",
        }
    )
    return report


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
    ap.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data" / "translations_seed_hook96.json",
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_marked.wsc",
        help="Baseline ROM for event-body heuristics (logical abs)",
    )
    ap.add_argument(
        "--lo",
        type=lambda s: int(s, 16),
        default=DEFAULT_LO,
        help="Inclusive low logical abs for early targets (default early_tut)",
    )
    ap.add_argument(
        "--hi",
        type=lambda s: int(s, 16),
        default=DEFAULT_HI,
        help="Inclusive high logical abs for early targets",
    )
    ap.add_argument(
        "--max-slots",
        type=int,
        default=0,
        help="Cap reclaimed slots (0=all sole-fit available)",
    )
    ap.add_argument("--hangul-marker", default="E3DB")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Report available sole-fit / planned assigns; do not write ROM",
    )
    ap.add_argument(
        "--ref-rom",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
        help="pristine ORIGINAL 8 MiB ROM used, together with the work ROM, to "
        "enumerate dictionary consumers. A corrupted aux zstring terminator in "
        "the work ROM hides the records behind it and makes shared slots look "
        "sole; the union with the original is fail-closed. Pass an empty path "
        "only if you accept that risk.",
    )
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out" / "patch" / "sole_reclaim_early_report.json",
    )
    args = ap.parse_args()

    marker = int(args.hangul_marker, 16)
    rom = bytearray(load_rom(args.rom))
    tbl = Tbl.load(args.tbl)
    seed_rows = json.loads(args.seed.read_text(encoding="utf-8")).get("lines", [])
    seed_abs = {int(row["abs"], 16) for row in seed_rows}
    lines = json.loads(args.sheet.read_text(encoding="utf-8"))["lines"]
    abs_to_line = {int(line["abs"], 16): line for line in lines if line.get("abs")}
    base_rom = args.base_rom.read_bytes() if args.base_rom.exists() else None
    ref_rom = None
    if args.ref_rom and str(args.ref_rom) and args.ref_rom.exists():
        ref_rom = args.ref_rom.read_bytes()
        if len(ref_rom) != 0x800000:
            raise SystemExit(
                f"--ref-rom must be the pristine 8 MiB original, got "
                f"{len(ref_rom):#x} ({args.ref_rom})"
            )

    report = apply_sole_reclaim(
        rom,
        tbl,
        abs_to_line=abs_to_line,
        seed_abs=seed_abs,
        lo=args.lo,
        hi=args.hi,
        marker=marker,
        max_slots=args.max_slots,
        base_rom=base_rom,
        ref_rom=ref_rom,
        dry_run=bool(args.dry_run),
    )
    report["ref_rom"] = str(args.ref_rom) if ref_rom is not None else None
    report["rom"] = str(args.rom)
    report["sheet"] = str(args.sheet)
    report["tbl"] = str(args.tbl)

    args.out_report.parent.mkdir(parents=True, exist_ok=True)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.dry_run:
        args.out_rom.write_bytes(rom)

    print(
        f"Sole-reclaim {'DRY' if args.dry_run else 'OK'} | "
        f"mode1_avail={report.get('sole_owner_ko_available', 0)} "
        f"mode2_avail={report.get('sole_fit_available', 0)} "
        f"early_need={report['early_uniques_needing_ko']} "
        f"assigned={report['assigned']} "
        f"(m1={report.get('mode1_assigned', 0)} m2={report.get('mode2_assigned', 0)}) "
        f"lines={report['lines_patched']} "
        f"seed_fail={report.get('seed_fail', 0)} "
        f"decode_fail={report.get('decode_fail', 0)}"
    )
    if report.get("checksum"):
        print(f"checksum={report['checksum']}")
    print(f"Wrote {args.out_report}")
    if not args.dry_run:
        print(f"Wrote {args.out_rom}")
    if int(report.get("seed_fail") or 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
