#!/usr/bin/env python3
"""Static acceptance audit for runtime_regression_bundle_candidate.wsc.

This audit is intentionally screen-contract oriented: it verifies the exact
records reported by the 2026-08-08 runtime captures, preserves portrait/control
prefixes and terminators, and checks that the repaired battle-voice rows no
longer contain visible duplicated/source leads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from extract_script import split_prefix_body
from monoeye_rom import Tbl, le16, read_encoded_z_safe, stock_base

PARENT = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CANDIDATE = ROOT / "out/patch/runtime_regression_bundle_candidate.wsc"
PARENT_SAV = ROOT / "sram/monoeye_ko_expanded.sav"
CANDIDATE_SAV = ROOT / "sram/runtime_regression_bundle_candidate.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/runtime_regression_bundle_audit.json"
EXPECTED_PARENT_SHA = "b192ad1ed2e24b709bfa14e5ae7d72405e58a3eac8ae746f41864961148d2746"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Rows where the first unit is proven runtime metadata and should be omitted
# from the visible text check.  These are intentionally *not* the rows fixed by
# stripping visible leads.
VISIBLE_AFTER_PREFIX = {
    0x5E4F39: bytes.fromhex("9F"),
    0x5EA61B: bytes.fromhex("94"),
    0x5EA652: bytes.fromhex("94"),
}

EXPECTED_VISIBLE = {
    0x5E4F39: "전원　제１전투　준비！",
    0x5E4F43: "전　포문……쏴라！！",
    0x5D45B5: "포격　허가를　부탁드립니다！",
    0x5D83D2: "포격에　대비하라！",
    0x5D8E92: "포격을　시작하겠습니다！！",
    0x5DAE3F: "포격수、뭘　하고　있는가！！",
    0x5E9666: "포격에　대비하라！！",
    0x5EA52F: "포격수、맞서　싸워라！！",
    0x5EA61B: "포격수、뭘　하고　있는가！！",
    0x5EA62A: "적기를　접근시키지　마라！！",
    0x5EA652: "아군은……",
    0x5EA659: "우군부대의　지원은　아직인가！？",
}

SCENARIO_EXPECTED = {
    0x6139F4: ("173418", "……흥！"),
    0x613C81: ("173418", "사병……"),
    0x613E79: ("173418", "전투　시작……"),
    0x614EFB: ("173418", "나는　혼자　싸우는　데"),
    0x614F0A: ("", "너무　집착했던　것　같다。"),
}

# These lines are the 7-2 / 7-3 block.  The candidate must not create a second
# physical copy or alter them while repairing the preceding 7-1 control leak.
LILA_FOLLOWUP_UNCHANGED = [0x613E8A, 0x613E94, 0x613E9E, 0x613EAA]

# Same-family duplicated visible leads repaired together with the two captures.
STRIPPED_VISIBLE_LEADS = {
    0x5D45B5: bytes.fromhex("F4F5"),
    0x5D83D2: bytes.fromhex("F4F5"),
    0x5D8E92: bytes.fromhex("F4F5"),
    0x5D4DB6: bytes.fromhex("F1EA"),
    0x5D50B0: bytes.fromhex("F1EA"),
    0x5D56F8: bytes.fromhex("F919"),
    0x5D8EE2: bytes.fromhex("F8E7"),
    0x5D94A7: bytes.fromhex("F20F"),
    0x5DAE3F: bytes.fromhex("F4F5"),
    0x5E1947: bytes.fromhex("F15C"),
    0x5E4F43: bytes.fromhex("86"),
    0x5E4FA7: bytes.fromhex("F177"),
    0x5E5016: bytes.fromhex("F2E8"),
    0x5E6590: bytes.fromhex("F48A"),
    0x5E98DA: bytes.fromhex("F177"),
    0x5E9F91: bytes.fromhex("F177"),
    0x5E9666: bytes.fromhex("F4F5"),
    0x5EA52F: bytes.fromhex("F4F5"),
    0x5EA62A: bytes.fromhex("F1EA"),
    0x5EA659: bytes.fromhex("FAF5"),
    0x5EBE4D: bytes.fromhex("F5C4"),
    0x5EC02B: bytes.fromhex("F1FC"),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def zpayload(rom: bytes, logical: int) -> tuple[bytes, int]:
    got = read_encoded_z_safe(rom, stock_base(rom) + logical, max_len=256)
    if got is None:
        raise RuntimeError(f"unreadable zstring {logical:06X}")
    return bytes(got[0]), int(got[1]) - stock_base(rom)


def decoded_full(rom: bytes, dictionary, tbl: Tbl, logical: int) -> str:
    payload, _ = zpayload(rom, logical)
    return dictionary.expand(payload, tbl).rstrip("\u3000 \t")


def decoded_visible(rom: bytes, dictionary, tbl: Tbl, logical: int) -> str:
    payload, _ = zpayload(rom, logical)
    prefix = VISIBLE_AFTER_PREFIX.get(logical, b"")
    if prefix:
        if not payload.startswith(prefix):
            raise RuntimeError(
                f"metadata prefix drift {logical:06X}: {payload[:len(prefix)].hex().upper()}"
            )
        payload = payload[len(prefix):]
    return dictionary.expand(payload, tbl).rstrip("\u3000 \t")


def add_check(checks: list[dict[str, Any]], name: str, ok: bool, **details: Any) -> None:
    checks.append({"name": name, "ok": bool(ok), **details})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parent", type=Path, default=PARENT)
    ap.add_argument("--candidate", type=Path, default=CANDIDATE)
    ap.add_argument("--parent-sav", type=Path, default=PARENT_SAV)
    ap.add_argument("--candidate-sav", type=Path, default=CANDIDATE_SAV)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    parent = args.parent.read_bytes()
    candidate = args.candidate.read_bytes()
    parent_sav = args.parent_sav.read_bytes()
    candidate_sav = args.candidate_sav.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    pd = make_dictionary_ext3(parent, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    cd = make_dictionary_ext3(candidate, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))

    checks: list[dict[str, Any]] = []
    add_check(checks, "rom_size", len(candidate) == ROM_SIZE, value=len(candidate))
    add_check(
        checks,
        "parent_identity",
        sha(parent).lower() == EXPECTED_PARENT_SHA,
        sha256=sha(parent),
    )
    stored_checksum = le16(candidate, len(candidate) - 2)
    calculated_checksum = sum(candidate[:-2]) & 0xFFFF
    add_check(
        checks,
        "ws_checksum",
        stored_checksum == calculated_checksum,
        stored=f"{stored_checksum:04X}",
        calculated=f"{calculated_checksum:04X}",
    )
    add_check(
        checks,
        "save_pair_exact",
        len(candidate_sav) == SAVE_SIZE and candidate_sav == parent_sav,
        candidate_size=len(candidate_sav),
        candidate_sha256=sha(candidate_sav),
        parent_sha256=sha(parent_sav),
    )

    for logical, expected in EXPECTED_VISIBLE.items():
        actual = decoded_visible(candidate, cd, tbl, logical)
        add_check(
            checks,
            f"visible_{logical:06X}",
            actual == expected,
            expected=expected,
            actual=actual,
        )

    # Every high-confidence visible lead is present on the parent and absent on
    # the candidate; terminator address must stay exactly fixed.
    for logical, lead in sorted(STRIPPED_VISIBLE_LEADS.items()):
        pp, pt = zpayload(parent, logical)
        cp, ct = zpayload(candidate, logical)
        add_check(
            checks,
            f"lead_removed_{logical:06X}",
            pp.startswith(lead) and not cp.startswith(lead) and pt == ct,
            lead_hex=lead.hex().upper(),
            parent_prefix_hex=pp[: len(lead)].hex().upper(),
            candidate_prefix_hex=cp[: len(lead)].hex().upper(),
            parent_terminator=f"{pt:06X}",
            candidate_terminator=f"{ct:06X}",
        )

    for logical, (expected_prefix, expected_text) in SCENARIO_EXPECTED.items():
        pp, pt = zpayload(parent, logical)
        cp, ct = zpayload(candidate, logical)
        prefix, body, _ = split_prefix_body(cp)
        actual = cd.expand(body, tbl).rstrip("\u3000 \t")
        add_check(
            checks,
            f"scenario_{logical:06X}",
            prefix.hex().upper() == expected_prefix
            and actual == expected_text
            and pt == ct,
            expected_prefix=expected_prefix,
            actual_prefix=prefix.hex().upper(),
            expected_text=expected_text,
            actual_text=actual,
            parent_terminator=f"{pt:06X}",
            candidate_terminator=f"{ct:06X}",
            parent_hex=pp.hex().upper(),
            candidate_hex=cp.hex().upper(),
        )

    # Emma/Lila next rows must still start exactly where they did on the parent;
    # this is the control-boundary invariant that previously caused kana leaks.
    for logical in (0x613C81, 0x613E79, 0x614F0A):
        _, pt = zpayload(parent, logical)
        _, ct = zpayload(candidate, logical)
        parent_next = parent[stock_base(parent) + pt + 1 : stock_base(parent) + pt + 6]
        candidate_next = candidate[stock_base(candidate) + ct + 1 : stock_base(candidate) + ct + 6]
        add_check(
            checks,
            f"post_control_boundary_{logical:06X}",
            pt == ct and parent_next == candidate_next,
            next_hex=candidate_next.hex().upper(),
        )

    for logical in LILA_FOLLOWUP_UNCHANGED:
        pp, pt = zpayload(parent, logical)
        cp, ct = zpayload(candidate, logical)
        add_check(
            checks,
            f"lila_followup_unchanged_{logical:06X}",
            pp == cp and pt == ct,
            parent_hex=pp.hex().upper(),
            candidate_hex=cp.hex().upper(),
        )

    # Weapon field: one exclusive ext3 token plus the original seven fixed 01
    # cells.  The compact four-cell label keeps that fixed field at 11 cells.
    weapon_payload, weapon_term = zpayload(candidate, 0x75C3C7)
    weapon_text = decoded_full(candidate, cd, tbl, 0x75C3C7)
    weapon_padding = len(weapon_payload) - len(weapon_payload.rstrip(b"\x01"))
    add_check(
        checks,
        "despada_weapon_width",
        weapon_text == "대형런처" and weapon_padding == 7 and len(weapon_text) + weapon_padding == 11,
        text=weapon_text,
        trailing_01=weapon_padding,
        visual_cells=len(weapon_text) + weapon_padding,
        terminator=f"{weapon_term:06X}",
    )

    # Sig's user-visible Japanese `こ` was byte 18.  Unlike the true metadata
    # 18 on 613E9E, 614F0A must now begin directly with E5 18.
    sig_payload, _ = zpayload(candidate, 0x614F0A)
    add_check(
        checks,
        "sig_visible_ko_removed",
        sig_payload.startswith(bytes.fromhex("E518")) and not sig_payload.startswith(bytes.fromhex("18E518")),
        payload_hex=sig_payload.hex().upper(),
    )
    lila_true_meta, _ = zpayload(candidate, 0x613E9E)
    parent_lila_true_meta, _ = zpayload(parent, 0x613E9E)
    add_check(
        checks,
        "lila_true_18_metadata_preserved",
        lila_true_meta == parent_lila_true_meta and lila_true_meta.startswith(bytes.fromhex("18E518")),
        payload_hex=lila_true_meta.hex().upper(),
    )

    failures = [row for row in checks if not row["ok"]]
    result = {
        "ok": not failures,
        "candidate": {
            "path": str(args.candidate.relative_to(ROOT)),
            "sha256": sha(candidate),
            "size": len(candidate),
        },
        "save": {
            "path": str(args.candidate_sav.relative_to(ROOT)),
            "sha256": sha(candidate_sav),
            "size": len(candidate_sav),
        },
        "counts": {"checks": len(checks), "failures": len(failures)},
        "checks": checks,
        "failures": failures,
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "counts": result["counts"], "candidate": result["candidate"]}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
