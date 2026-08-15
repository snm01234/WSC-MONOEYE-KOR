#!/usr/bin/env python3
"""Dict invasion via distinct sheet JP sharing one tip Hangul slot."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from expand_dictionary import build_dict_token_locs  # noqa: E402
from monoeye_rom import Tbl, load_rom  # noqa: E402
from normalize_ko_text import normalize_ko_text  # noqa: E402

EARLY = (0x6040A5, 0x607000)


def has_marker(payload: bytes) -> bool:
    return any(
        payload[i] == 0xE3 and payload[i + 1] == 0xDB
        for i in range(len(payload) - 1)
    )


def main() -> int:
    rom = load_rom(ROOT / "out/patch/monoeye_ko_expanded.wsc")
    meta = load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json")
    tbl = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")
    d = make_dictionary(rom, meta)
    stock = int(meta["stock_count"])
    base = load_rom(ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc")
    from monoeye_rom import Dictionary

    db = Dictionary(base)

    sheet = json.loads(
        (ROOT / "out/script/translations_ep3_window.json").read_text(encoding="utf-8")
    )["lines"]
    jp_by = {int(r["abs"], 16): (r.get("jp") or "") for r in sheet}
    ko_by = {
        int(r["abs"], 16): normalize_ko_text(r.get("ko") or "")
        for r in sheet
        if r.get("ko")
    }

    print("building locs...")
    locs = build_dict_token_locs(rom, regions=("script", "name75", "aux"))

    coll = []
    for idx, refs in locs.items():
        try:
            raw = d.raw_entry(idx)
        except Exception:
            continue
        if not raw or not has_marker(raw):
            continue
        tip = d.expand(raw, tbl).rstrip("\u3000")
        script = [r for r in refs if r.region == "script" and r.kind == "dialogue"]
        if len(script) < 2:
            continue
        jp_map: dict[str, list[int]] = defaultdict(list)
        for r in script:
            jp = jp_by.get(r.abs)
            if not jp:
                continue
            jp_map[jp].append(r.abs)
        if len(jp_map) < 2:
            continue

        early = [
            a
            for alist in jp_map.values()
            for a in alist
            if EARLY[0] <= a <= EARLY[1]
        ]
        other = [
            a
            for alist in jp_map.values()
            for a in alist
            if not (EARLY[0] <= a <= EARLY[1])
        ]
        aux = sum(1 for r in refs if r.region != "script")
        sole = False
        if idx < stock:
            try:
                br = db.raw_entry(idx)
                sole = bool(br) and not has_marker(br) and len(raw) > len(br) + 4
            except Exception:
                pass

        groups = []
        for jp, alist in sorted(jp_map.items(), key=lambda kv: -len(kv[1]))[:5]:
            groups.append(
                {
                    "jp": jp[:45],
                    "n": len(alist),
                    "abs": [f"{x:06X}" for x in alist[:4]],
                    "sheet_ko": (ko_by.get(alist[0], "") or "")[:35],
                }
            )
        coll.append(
            {
                "dict_index": idx,
                "ext": idx >= stock,
                "tip_ko": tip[:60],
                "distinct_jp": len(jp_map),
                "early": len(early),
                "other": len(other),
                "aux": aux,
                "refs": len(refs),
                "stock_sole_residue": sole,
                "groups": groups,
            }
        )

    coll.sort(key=lambda x: (-x["distinct_jp"], -x["refs"]))
    early_other = [c for c in coll if c["early"] and c["other"]]
    sole_res = [c for c in coll if c["stock_sole_residue"]]
    early_other_sole = [c for c in early_other if c["stock_sole_residue"]]

    # Classify cause heuristic
    for c in early_other:
        if c["stock_sole_residue"]:
            c["likely_cause"] = "sole_reclaim_residue"
        elif c["ext"]:
            c["likely_cause"] = "ext_slot_overshare_or_steal"
        else:
            c["likely_cause"] = "stock_shared_phrase_or_reclaim"

    cause_counts: dict[str, int] = defaultdict(int)
    for c in early_other:
        cause_counts[c["likely_cause"]] += 1

    out = {
        "collision_slots": len(coll),
        "early_and_other_consumers": len(early_other),
        "among_them_sole_residue": len(early_other_sole),
        "all_sole_residue_collisions": len(sole_res),
        "cause_counts_early_other": dict(cause_counts),
        "top_early_other": early_other[:50],
        "top_sole_residue": sole_res[:40],
    }
    path = ROOT / "out/patch/invasion_by_sheet_jp.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"collisions={len(coll)} early+other={len(early_other)} "
        f"sole_in_early_other={len(early_other_sole)} "
        f"causes={dict(cause_counts)} → {path}"
    )
    for c in early_other[:12]:
        print(
            f"  idx={c['dict_index']} ext={c['ext']} jps={c['distinct_jp']} "
            f"early={c['early']} other={c['other']} aux={c['aux']} "
            f"cause={c['likely_cause']}"
        )
        print("   tip:", c["tip_ko"][:50])
        for g in c["groups"][:2]:
            print(f"   jp[{g['n']}]: {g['jp'][:40]}")
            print(f"      abs={g['abs']} sheet_ko={g['sheet_ko'][:30]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
