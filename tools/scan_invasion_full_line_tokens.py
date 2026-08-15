#!/usr/bin/env python3
"""
Detect full-line dict-token invasion (not shared phrase fragments).

Only counts script/dialogue consumers whose body is a pure dict token
(+ SPACE pad). If two such consumers have different sheet JP, the slot
is a true full-line collision / invasion.

READ-ONLY: every ROM is opened for reading only and the report path must not
be a ``.wsc``. All inputs are explicit CLI paths (the historic tip defaults are
kept as argparse defaults) and every ROM read is recorded in ``inputs`` with
path/size/sha256 so a gate run is reproducible.

Gate contract: the report always carries a boolean ``ok`` and an integer
``early_and_other``; ``ok`` is true only when ``early_and_other == 0``, and the
process exit code mirrors ``ok``. Detail rows stay in ``top_early_other``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from expand_dictionary import build_dict_token_locs  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    dict_index_from_token,
    is_dict_token,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)
from normalize_ko_text import normalize_ko_text  # noqa: E402

EARLY = (0x6040A5, 0x607000)
SPACE = 0x01

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_ORIGINAL = ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc"
DEFAULT_META = ROOT / "out/patch/exp_dictionary_meta.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_SHEET = ROOT / "out/script/translations_quality_all.json"
DEFAULT_SHEET_FALLBACK = ROOT / "out/script/translation_sheet.csv"
DEFAULT_OUT = ROOT / "out/patch/invasion_full_line_tokens.json"


def file_identity(path: Path, data: bytes | bytearray | None = None) -> dict[str, Any]:
    """path/size/sha256 identity of an input the scan actually read."""
    payload = bytes(data) if data is not None else Path(path).read_bytes()
    return {
        "path": str(Path(path).resolve()),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def make_console_encoding_safe() -> None:
    """Downgrade unencodable console characters instead of raising.

    The tip/JP samples printed below are not encodable on a cp949 console; the
    exit code must reflect the scan result, never a print failure.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            pass


def safe_print(*parts: Any) -> None:
    """Print without letting a console codec (cp949) turn output into an error."""
    text = " ".join(str(p) for p in parts)
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        stream.write(
            text.encode(encoding, errors="backslashreplace").decode(
                encoding, errors="replace"
            )
            + "\n"
        )


def has_marker(payload: bytes) -> bool:
    return any(
        payload[i] == 0xE3 and payload[i + 1] == 0xDB
        for i in range(len(payload) - 1)
    )


def pure_token_idx(body: bytes, expect_idx: int) -> bool:
    core = bytes(b for b in body if b != SPACE)
    if len(core) != 2 or not is_dict_token(core[0]):
        return False
    return dict_index_from_token(core[0], core[1]) == expect_idx


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--target",
        "--rom",
        dest="target",
        type=Path,
        default=DEFAULT_TARGET,
        help="ROM to scan (candidate / tip). READ-ONLY.",
    )
    ap.add_argument(
        "--original",
        "--base-rom",
        dest="original",
        type=Path,
        default=DEFAULT_ORIGINAL,
        help="Reference ROM used for stock sole-residue detection. READ-ONLY.",
    )
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument(
        "--sheet",
        type=Path,
        default=DEFAULT_SHEET,
        help="Translation sheet; falls back to translations_ep3_window.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="JSON report path (never a .wsc).",
    )
    ap.add_argument(
        "--top", type=int, default=60, help="max rows kept in report['top']"
    )
    ap.add_argument(
        "--top-early-other",
        type=int,
        default=40,
        help="max rows kept in report['top_early_other']",
    )
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    make_console_encoding_safe()
    args = build_parser().parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this tool is read-only")

    rom = load_rom(args.target)
    base = load_rom(args.original)
    inputs = {
        "target": file_identity(args.target, rom),
        "original": file_identity(args.original, base),
    }
    meta = load_ext_meta(args.meta)
    tbl = Tbl.load(args.tbl)
    d = make_dictionary(rom, meta)
    db = Dictionary(base)
    stock = int(meta["stock_count"])
    sb = stock_base(rom)

    # Prefer full quality sheet so missing-JP abs don't inflate "distinct" counts.
    sheet_path = args.sheet
    if not sheet_path.exists():
        sheet_path = DEFAULT_SHEET_FALLBACK
    sheet_raw = json.loads(sheet_path.read_text(encoding="utf-8"))
    sheet = sheet_raw["lines"] if isinstance(sheet_raw, dict) else sheet_raw
    jp_by = {int(r["abs"], 16): (r.get("jp") or "") for r in sheet if r.get("abs")}
    ko_by = {
        int(r["abs"], 16): normalize_ko_text(r.get("ko") or "")
        for r in sheet
        if r.get("abs") and r.get("ko")
    }

    safe_print("building locs...")
    locs = build_dict_token_locs(rom, regions=("script", "name75", "aux"))

    invasions = []
    for idx, refs in locs.items():
        try:
            raw = d.raw_entry(idx)
        except Exception:
            continue
        if not raw or not has_marker(raw):
            continue
        tip = d.expand(raw, tbl).rstrip("\u3000")

        pure_consumers = []
        aux_n = 0
        for r in refs:
            if r.region != "script":
                aux_n += 1
                continue
            if r.kind != "dialogue":
                continue
            got = read_encoded_z_safe(rom, sb + r.abs)
            if got is None:
                continue
            _prefix, body, _ = split_prefix_body(got[0])
            if not pure_token_idx(body, idx):
                continue
            jp = jp_by.get(r.abs, "")
            pure_consumers.append((r.abs, jp, ko_by.get(r.abs, "")))

        if len(pure_consumers) < 2:
            continue
        jp_map: dict[str, list[int]] = defaultdict(list)
        for abs_off, jp, _ko in pure_consumers:
            if not jp:
                jp = f"<no_jp:{abs_off:06X}>"
            jp_map[jp].append(abs_off)
        if len(jp_map) < 2:
            continue

        early = [a for a, jp, _ in pure_consumers if EARLY[0] <= a <= EARLY[1]]
        other = [a for a, jp, _ in pure_consumers if not (EARLY[0] <= a <= EARLY[1])]
        sole = False
        if idx < stock:
            try:
                br = db.raw_entry(idx)
                sole = bool(br) and not has_marker(br) and len(raw) > len(br) + 4
            except Exception:
                pass

        if sole:
            cause = "sole_reclaim_residue"
        elif idx >= stock:
            cause = "ext_full_line_overshare"
        else:
            cause = "stock_full_line_overshare"

        invasions.append(
            {
                "dict_index": idx,
                "ext": idx >= stock,
                "tip_ko": tip[:70],
                "pure_consumers": len(pure_consumers),
                "distinct_jp": len(jp_map),
                "early": len(early),
                "other": len(other),
                "aux_or_name_refs": aux_n,
                "cause": cause,
                "stock_sole_residue": sole,
                "groups": [
                    {
                        "jp": jp[:45],
                        "n": len(alist),
                        "abs": [f"{a:06X}" for a in alist[:5]],
                        "sheet_ko": (ko_by.get(alist[0], "") or "")[:40],
                    }
                    for jp, alist in sorted(
                        jp_map.items(), key=lambda kv: -len(kv[1])
                    )[:6]
                ],
            }
        )

    invasions.sort(
        key=lambda x: (
            0 if x["cause"] == "sole_reclaim_residue" else 1,
            -x["distinct_jp"],
            -x["pure_consumers"],
        )
    )
    causes: dict[str, int] = defaultdict(int)
    for i in invasions:
        causes[i["cause"]] += 1

    early_other = [i for i in invasions if i["early"] and i["other"]]
    inputs["meta"] = file_identity(args.meta)
    inputs["tbl"] = file_identity(args.tbl)
    inputs["sheet"] = file_identity(sheet_path)
    out = {
        "description": (
            "Full-line token collisions: multiple pure-token dialogue lines "
            "with different JP share one Hangul dict slot (visible wrong lines)."
        ),
        # Stable gate field: the scan passes only when no slot mixes an
        # early-dialogue-band consumer with a later consumer.
        "ok": len(early_other) == 0,
        "generated_by": "tools/scan_invasion_full_line_tokens.py",
        "read_only": True,
        "inputs": inputs,
        "out": str(args.out.resolve()),
        "invasion_slots": len(invasions),
        "early_and_other": len(early_other),
        "cause_counts": dict(causes),
        "cause_counts_early_other": {
            k: sum(1 for i in early_other if i["cause"] == k) for k in causes
        },
        "top": invasions[: args.top],
        "top_early_other": early_other[: args.top_early_other],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    safe_print(
        f"full_line_invasions={len(invasions)} early+other={len(early_other)} "
        f"causes={dict(causes)} -> {args.out}"
    )
    for i in invasions[:15]:
        safe_print(
            f"  idx={i['dict_index']} cause={i['cause']} jps={i['distinct_jp']} "
            f"pure={i['pure_consumers']} early={i['early']} other={i['other']} aux={i['aux_or_name_refs']}"
        )
        safe_print("   tip=", i["tip_ko"][:55])
        for g in i["groups"][:2]:
            safe_print(f"   jp n={g['n']} abs={g['abs']}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
