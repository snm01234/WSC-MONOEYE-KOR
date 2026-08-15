#!/usr/bin/env python3
"""Find dict slots where distinct original JP strings share one tip KO payload."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from expand_dictionary import build_dict_token_locs  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe  # noqa: E402

MARKER_HI, MARKER_LO = 0xE3, 0xDB
EARLY = (0x6040A5, 0x607000)


def has_marker(payload: bytes) -> bool:
    return any(
        payload[i] == MARKER_HI and payload[i + 1] == MARKER_LO
        for i in range(len(payload) - 1)
    )


def jp_at(rom_bytes, dictionary, tbl, abs_off: int):
    got = read_encoded_z_safe(rom_bytes, abs_off)
    if got is None:
        return None
    body = split_prefix_body(got[0])[1]
    try:
        return dictionary.expand(body, tbl).rstrip("\u3000")
    except Exception:
        return None


def main() -> int:
    rom = load_rom(ROOT / "out/patch/monoeye_ko_expanded.wsc")
    base = load_rom(ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc")
    marked_path = ROOT / "out/patch/monoeye_ko_marked.wsc"
    marked = marked_path.read_bytes() if marked_path.exists() else base
    meta = load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json")
    tbl = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")
    d = make_dictionary(rom, meta)
    db = Dictionary(base)
    dm = Dictionary(marked)
    stock = int(meta["stock_count"])

    print("building locs...")
    locs = build_dict_token_locs(rom, regions=("script", "name75", "aux"))

    true_collisions = []
    for idx, refs in locs.items():
        try:
            raw = d.raw_entry(idx)
        except Exception:
            continue
        if not raw or not has_marker(raw):
            continue
        tip = d.expand(raw, tbl).rstrip("\u3000")
        script_refs = [
            r for r in refs if r.region == "script" and r.kind == "dialogue"
        ]
        if len(script_refs) < 2:
            continue

        jp_map: dict[str, list[int]] = {}
        for r in script_refs:
            jp = jp_at(marked, dm, tbl, r.abs)
            if jp is None:
                jp = jp_at(base, db, tbl, r.abs)
            if jp is None:
                continue
            jp_map.setdefault(jp, []).append(r.abs)
        if len(jp_map) < 2:
            continue

        early_abs = [
            a
            for alist in jp_map.values()
            for a in alist
            if EARLY[0] <= a <= EARLY[1]
        ]
        other_abs = [
            a
            for alist in jp_map.values()
            for a in alist
            if not (EARLY[0] <= a <= EARLY[1])
        ]
        aux_n = sum(1 for r in refs if r.region != "script")
        sole_residue = False
        if idx < stock:
            try:
                br = db.raw_entry(idx)
                sole_residue = (
                    bool(br)
                    and not has_marker(br)
                    and len(raw) > len(br) + 4
                )
            except Exception:
                pass

        true_collisions.append(
            {
                "dict_index": idx,
                "ext": idx >= stock,
                "tip_ko": tip[:70],
                "distinct_original_jp": len(jp_map),
                "jp_groups": [
                    {
                        "jp": jp[:50],
                        "abs": [f"{a:06X}" for a in alist[:6]],
                        "n": len(alist),
                    }
                    for jp, alist in sorted(
                        jp_map.items(), key=lambda kv: -len(kv[1])
                    )[:6]
                ],
                "early_consumers": len(early_abs),
                "other_consumers": len(other_abs),
                "aux_or_name": aux_n,
                "ref_total": len(refs),
                "stock_sole_residue": sole_residue,
            }
        )

    true_collisions.sort(
        key=lambda x: (
            -x["distinct_original_jp"],
            -x["ref_total"],
            x["dict_index"],
        )
    )

    # Also: remaining stock sole-style multi-ref even if JP groups collapsed
    sole_left = []
    for idx, refs in locs.items():
        if idx >= stock or len(refs) < 2:
            continue
        try:
            raw = d.raw_entry(idx)
            br = db.raw_entry(idx)
        except Exception:
            continue
        if not has_marker(raw) or has_marker(br):
            continue
        if len(raw) <= len(br) + 2:
            continue
        tip = d.expand(raw, tbl).rstrip("\u3000")
        sole_left.append(
            {
                "dict_index": idx,
                "refs": len(refs),
                "regions": sorted({r.region for r in refs}),
                "tip_ko": tip[:60],
                "base_len": len(br),
                "tip_len": len(raw),
                "sample": [
                    f"{r.abs:06X}:{r.region}" for r in refs[:8]
                ],
            }
        )
    sole_left.sort(key=lambda x: -x["refs"])

    out = {
        "true_collision_count": len(true_collisions),
        "with_early_and_other": sum(
            1
            for c in true_collisions
            if c["early_consumers"] and c["other_consumers"]
        ),
        "collision_stock_sole_residue": sum(
            1 for c in true_collisions if c["stock_sole_residue"]
        ),
        "collision_ext": sum(1 for c in true_collisions if c["ext"]),
        "collision_stock": sum(1 for c in true_collisions if not c["ext"]),
        "remaining_stock_sole_style_multiref": len(sole_left),
        "top_collisions": true_collisions[:80],
        "top_sole_residue": sole_left[:80],
    }
    path = ROOT / "out/patch/invasion_true_collisions.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"true_collisions={out['true_collision_count']} "
        f"early+other={out['with_early_and_other']} "
        f"sole_residue_in_collision={out['collision_stock_sole_residue']} "
        f"remaining_sole_style={out['remaining_stock_sole_style_multiref']} "
        f"→ {path}"
    )
    for c in true_collisions[:12]:
        print(
            f"  idx={c['dict_index']} ext={c['ext']} jps={c['distinct_original_jp']} "
            f"early={c['early_consumers']} other={c['other_consumers']} "
            f"aux={c['aux_or_name']} sole={c['stock_sole_residue']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
