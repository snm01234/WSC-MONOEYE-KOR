#!/usr/bin/env python3
"""Static smoke for free-space tip + opening/steal body allowlist."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_script_ko import JAGD_GUARD_ABS, JAGD_GUARD_GOOD  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base  # noqa: E402
from patch_opening_narration import OPENING_INTERSTITIALS  # noqa: E402
from verify_script_banks_allowlist import verify_script_banks_allowlist  # noqa: E402

OPENING_LO = 0x6040A5
OPENING_HI = 0x607000
DIALOGUE_BANKS = range(0x60, 0x6A)


def bank_diff(a, b, seg: int) -> int:
    sa = stock_base(a) + (seg << 16)
    sb = stock_base(b) + (seg << 16)
    return sum(1 for x, y in zip(a[sa : sa + 0x10000], b[sb : sb + 0x10000]) if x != y)


def rebuild_pointer_allowlist(tip: bytes, jp: bytes) -> list[int]:
    """
    Grandfather every tip≠JP byte in dialogue banks 60–69.

    Free-space sole-ptr sites, opening dedicated bodies, and late→early steal
    retargets all live here. Rebuilding from the tip avoids depending on deleted
    report JSON after cleanup.
    """
    st = stock_base(tip)
    sj = stock_base(jp)
    out: list[int] = []
    for seg in DIALOGUE_BANKS:
        for i in range(0x10000):
            logical = (seg << 16) | i
            if tip[st + logical] != jp[sj + logical]:
                out.append(logical)
    return out


def collect_opening_body_abs(tip: bytes, jp: bytes) -> list[int]:
    abs_candidates: set[int] = set()
    for path in (
        ROOT / "out/script/translations_ep3_window.json",
        ROOT / "data/translations_seed_hook96.json",
        ROOT / "out/patch/steal_late_ext_to_early_report.json",
        ROOT / "out/patch/opening_dedicated_free_space_report.json",
        ROOT / "out/patch/build_script_ko_opening_dedicated_report.json",
    ):
        if not path.exists():
            continue
        full = json.loads(path.read_text(encoding="utf-8"))
        lines = full.get("lines") if isinstance(full, dict) else full
        if isinstance(lines, list):
            for row in lines:
                if isinstance(row, dict) and row.get("abs"):
                    abs_candidates.add(int(row["abs"], 16))
        if isinstance(full, dict):
            for row in full.get("applied") or []:
                if row.get("ok") is False:
                    continue
                if row.get("abs"):
                    abs_candidates.add(int(row["abs"], 16))
            for row in full.get("patches_sample") or []:
                if row.get("abs"):
                    abs_candidates.add(int(row["abs"], 16))
            for sample in full.get("sample_steal") or []:
                for a in sample.get("early_abs") or []:
                    abs_candidates.add(int(a, 16))
    for abs_s, _jp, _ko in OPENING_INTERSTITIALS:
        abs_candidates.add(int(abs_s, 16))

    st = stock_base(tip)
    sj = stock_base(jp)
    body_abs: list[int] = []
    for a in sorted(abs_candidates):
        if not (OPENING_LO <= a <= OPENING_HI):
            continue
        rt = read_encoded_z_safe(tip, st + a, max_len=0x400)
        rj = read_encoded_z_safe(jp, sj + a, max_len=0x400)
        if rt and (not rj or rt[0] != rj[0]):
            body_abs.append(a)
    return body_abs


def main() -> int:
    tip = load_rom(ROOT / "out/patch/monoeye_ko_expanded.wsc")
    jp = load_rom(ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc")
    tbl = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")

    allow = rebuild_pointer_allowlist(tip, jp)
    # Persist for other tools.
    allow_path = ROOT / "out/patch/free_space_pointer_allowlist.json"
    allow_path.write_text(
        json.dumps(
            {"pointer_allowlist": [f"{a:06X}" for a in allow], "n": len(allow)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    body_abs = collect_opening_body_abs(tip, jp)
    v = verify_script_banks_allowlist(
        tip, jp, allowlist_logical=allow, body_abs=body_abs
    )
    # With full tip≠JP grandfather as allowlist, verify should be clean;
    # body_abs remains informational for opening samples.
    fo = stock_base(tip) + JAGD_GUARD_ABS
    jagd = bytes(tip[fo : fo + 3]) == JAGD_GUARD_GOOD
    unit = {
        f"{seg:02X}": bank_diff(tip, jp, seg)
        for seg in list(range(0x50, 0x5E)) + list(range(0x6A, 0x70))
    }

    st = stock_base(tip)
    opening = {}
    for a in (0x6040A5, 0x6040B5, 0x6040CB, 0x6040DD, 0x604116, 0x604247, 0x60430A):
        r = read_encoded_z_safe(tip, st + a, max_len=80)
        if not r:
            opening[f"{a:06X}"] = None
            continue
        _prefix, payload, _kind = split_prefix_body(r[0])
        opening[f"{a:06X}"] = (
            Dictionary(tip).expand(payload, tbl) if payload else None
        )

    hangul_ok = all(
        t and any("\uac00" <= c <= "\ud7a3" for c in t) for t in opening.values()
    )

    report = {
        "allowlist_verify": {
            "ok": v.get("ok"),
            "diff_bytes_60_69": v.get("diff_bytes_60_69"),
            "illegal_diff_count": v.get("illegal_diff_count"),
            "event_body_break_count": v.get("event_body_break_count"),
            "body_abs_n": len(body_abs),
            "ptr_allow_n": len(allow),
        },
        "jagd_ok": jagd,
        "unit_banks_vs_jp_nonzero": {k: n for k, n in unit.items() if n},
        "opening_samples": opening,
        "opening_has_hangul": hangul_ok,
        "playtest": [
            "Opening narration Hangul (incl. 6040B5)",
            "Stage1 early/mid Hangul after late→early steal",
            "Stage 2 Rick Dom OK (unit banks tip≡JP)",
        ],
        "ok_static": bool(
            v.get("ok") and jagd and hangul_ok and all(n == 0 for n in unit.values())
        ),
    }
    out = ROOT / "out/patch/free_space_smoke_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("ok_static", "jagd_ok", "opening_has_hangul", "allowlist_verify")
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    for a, t in opening.items():
        print(f"  {a} {t!r}")
    print("→", out)
    return 0 if report["ok_static"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
