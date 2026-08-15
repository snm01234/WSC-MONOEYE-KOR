#!/usr/bin/env python3
"""Find ext_dict patches that likely hit event/control bytes, not dialogue."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from event_record_heuristics import looks_like_event_body  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    dict_index_from_token,
    is_dict_token,
    load_rom,
    read_encoded_z_safe,
)


def main() -> None:
    marked = load_rom(ROOT / "out/patch/monoeye_ko_marked.wsc")
    exp = load_rom(ROOT / "out/patch/monoeye_ko_expanded.wsc")
    meta = load_ext_meta(ROOT / "out/patch/ext_dictionary_meta.json")
    d = make_dictionary(exp, meta)
    tbl = Tbl.load(ROOT / "out/patch/hangul_patch.tbl")
    sheet = json.loads(
        (ROOT / "out/script/translations_full.json").read_text(encoding="utf-8")
    )["lines"]
    stock = int(meta["stock_count"])
    slots = int(meta["slot_count"])

    suspects = []
    for line in sheet:
        abs_off = int(line["abs"], 16)
        be = bytes(exp[abs_off : abs_off + 2])
        bm_rec = read_encoded_z_safe(marked, abs_off)
        if bm_rec is None:
            continue
        m_payload = bm_rec[0]
        if bytes(exp[abs_off : abs_off + len(m_payload)]) == m_payload:
            continue
        if not is_dict_token(be[0]):
            continue
        idx = dict_index_from_token(be[0], be[1])
        if not (stock <= idx < stock + slots):
            continue
        _p, body, _ = split_prefix_body(m_payload)
        eventish = looks_like_event_body(body)
        # Also flag when zero-pad destroyed trailing opcodes.
        e_payload = bytes(exp[abs_off : abs_off + len(m_payload)])
        zero_pad = e_payload[2:].count(0) >= max(2, (len(m_payload) - 2) // 2)
        if eventish or (zero_pad and len(m_payload) >= 6 and looks_like_event_body(body)):
            suspects.append(
                {
                    "abs": f"{abs_off:06X}",
                    "jp": line.get("jp"),
                    "ko": line.get("ko"),
                    "marked": m_payload.hex(),
                    "expanded": e_payload.hex(),
                    "decode": d.expand(be, tbl),
                    "eventish": eventish,
                    "zero_pad": zero_pad,
                }
            )

    out = ROOT / "out/patch/ext_false_dialogue_suspects.json"
    out.write_text(
        json.dumps({"count": len(suspects), "suspects": suspects}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"suspects={len(suspects)} -> {out}")
    for row in suspects[:30]:
        print(
            f"{row['abs']} eventish={row['eventish']} zeropad={row['zero_pad']} "
            f"jp={row['jp']!r} -> {row['ko']!r}"
        )
        print(f"  marked={row['marked']}")
        print(f"  exp   ={row['expanded']}")


if __name__ == "__main__":
    main()
