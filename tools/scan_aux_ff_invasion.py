#!/usr/bin/env python3
"""
Scan tip ROM for Hangul dict slots that invade battle/UI aux zstrings.

Focus: FF-page ext tokens (and stock Hangul with aux hits) whose tip KO
leaks into banks 50-5F / 76 via build_dict_token_locs(..., regions including aux).

READ-ONLY: every ROM is opened for reading only and the report path must not be
a ``.wsc``. All inputs are explicit CLI paths (the historic tip defaults are kept
as argparse defaults) and every file the scan reads is recorded in ``inputs``
with path/size/sha256, so the Accepted_Baseline and the Candidate can be scanned
with identical arguments and compared.

Gate contract: ``counts.ext_ff_page_confirmed`` is always present as an integer;
the gate compares the candidate value against the baseline value. Console output
is encoding-safe (cp949 consoles cannot encode the Korean/Japanese samples), so
the exit code reflects the scan itself, never a print failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from expand_dictionary import (  # noqa: E402
    AUX_TOKEN_BANKS,
    _walk_zstring_range,
    build_dict_token_locs,
)
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
)
from normalize_ko_text import normalize_ko_text  # noqa: E402
from patch_ext_dictionary import STOCK_DICT_COUNT  # noqa: E402

MARKER = 0xE3DB
EARLY_LO, EARLY_HI = 0x6040A5, 0x607000
HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
JP_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
BADDICT_RE = re.compile(r"<BADDICT:", re.I)
SPACE_RE = re.compile(r"[\s\u3000]+")


DEFAULT_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_BASE_ROM = ROOT / "out/patch/monoeye_ko_expanded_8mb.wsc"
DEFAULT_ORIGINAL_ROM = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_SHEET = ROOT / "out/script/translations_quality_all.json"
DEFAULT_SHEET_FALLBACK = ROOT / "out/script/translation_sheet.csv"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_META = ROOT / "out/patch/exp_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/aux_ff_invasion_scan.json"


def file_identity(
    path: Path, data: bytes | bytearray | None = None
) -> Dict[str, Any]:
    """path/size/sha256 identity of an input the scan actually read."""
    target = Path(path)
    if data is None and not target.is_file():
        return {"path": str(target), "present": False, "size": None, "sha256": None}
    payload = bytes(data) if data is not None else target.read_bytes()
    return {
        "path": str(target.resolve()),
        "present": True,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def make_console_encoding_safe() -> None:
    """Downgrade unencodable console characters instead of raising.

    A cp949 console cannot encode the Korean/Japanese/BADDICT samples this scan
    prints; without this the process dies with UnicodeEncodeError after the JSON
    report was already written, and the exit code would report a print failure
    as a scan failure.
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


def compact(text: str) -> str:
    return SPACE_RE.sub("", text or "")


def tip_fragment(tip_ko: str, *, min_chars: int = 4) -> str:
    c = compact(tip_ko)
    if len(c) < min_chars:
        return c
    if len(c) > 24:
        return c[:24]
    return c


def contains_tip(expand: str, tip_ko: str) -> bool:
    frag = tip_fragment(tip_ko)
    if len(frag) < 3:
        return False
    return frag in compact(expand)


def hangul_count(text: str) -> int:
    return len(HANGUL_RE.findall(text or ""))


def dialogue_like_tip(tip_ko: str) -> bool:
    """Story/help sentence vs short UI noun (공격력/파일럿)."""
    n = hangul_count(tip_ko)
    if n >= 8:
        return True
    if n >= 4 and any(ch in (tip_ko or "") for ch in "。！？、…"):
        return True
    return False


def uniq_refs(refs):
    seen = set()
    out = []
    for r in refs:
        key = (r.region, r.abs)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out



def has_hangul_marker(payload: bytes, marker: int = MARKER) -> bool:
    hi, lo = (marker >> 8) & 0xFF, marker & 0xFF
    return any(payload[i] == hi and payload[i + 1] == lo for i in range(len(payload) - 1))


def has_hangul(text: str) -> bool:
    return bool(HANGUL_RE.search(text or ""))


def has_jp(text: str) -> bool:
    return bool(JP_RE.search(text or ""))


def load_sheet(path: Path) -> Tuple[Dict[int, str], Dict[int, str]]:
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = data["lines"] if isinstance(data, dict) else data
    jp_by: Dict[int, str] = {}
    ko_by: Dict[int, str] = {}
    for row in lines:
        abs_s = row.get("abs")
        if not abs_s:
            continue
        abs_i = int(abs_s, 16)
        jp = row.get("jp") or ""
        ko = normalize_ko_text(row.get("ko") or "")
        if jp:
            jp_by[abs_i] = jp
        if ko:
            ko_by[abs_i] = ko
    return jp_by, ko_by


def token_bytes(idx: int) -> Tuple[int, int]:
    tok = token_from_dict_index(idx)
    return tok[0], tok[1]


def expand_aux_at(
    rom: bytes | bytearray,
    d: Dictionary,
    tbl: Tbl,
    abs_off: int,
    *,
    max_len: int = 128,
) -> str:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + abs_off, max_len=max_len)
    if got is None or not got[0]:
        return ""
    try:
        return d.expand(got[0], tbl).rstrip("\u3000")
    except Exception:
        return ""


def stock_only_expand(
    d_stock: Optional[Dictionary],
    tbl: Tbl,
    tip_d: Dictionary,
    rom: bytes | bytearray,
    abs_off: int,
) -> str:
    """Expand aux zstring using stock-only Dictionary (base/original bounds)."""
    if d_stock is None:
        return ""
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + abs_off, max_len=128)
    if got is None or not got[0]:
        return ""
    try:
        return d_stock.expand(got[0], tbl).rstrip("\u3000")
    except Exception:
        return ""


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        "--target",
        dest="rom",
        type=Path,
        default=DEFAULT_ROM,
        help="ROM to scan (candidate / baseline / tip). READ-ONLY.",
    )
    ap.add_argument(
        "--base-rom",
        type=Path,
        default=DEFAULT_BASE_ROM,
        help="Stock-only / pre-ext reference when present. READ-ONLY.",
    )
    ap.add_argument(
        "--original-rom",
        type=Path,
        default=DEFAULT_ORIGINAL_ROM,
        help="Vanilla ROM for Dictionary(base) stock expand comparison. READ-ONLY.",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=DEFAULT_SHEET,
        help="Translation sheet; falls back to translations_ep3_window.json",
    )
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META)
    ap.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="JSON report path (never a .wsc).",
    )
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--sample-around", type=lambda s: int(s, 0), default=0x530337)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    make_console_encoding_safe()
    args = build_parser().parse_args(argv)
    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this tool is read-only")

    rom = load_rom(args.rom)
    tbl = Tbl.load(args.tbl)
    meta = load_ext_meta(args.meta) if args.meta.exists() else {}
    d = make_dictionary(rom, meta) if meta else Dictionary(rom)
    stock = int(meta.get("stock_count", STOCK_DICT_COUNT)) if meta else STOCK_DICT_COUNT

    d_base: Optional[Dictionary] = None
    base_bytes: Optional[bytes] = None
    if args.base_rom.exists():
        base_bytes = bytes(load_rom(args.base_rom))
        d_base = Dictionary(base_bytes)
    d_orig: Optional[Dictionary] = None
    original_bytes: Optional[bytes] = None
    if args.original_rom.exists():
        original_bytes = bytes(load_rom(args.original_rom))
        d_orig = Dictionary(original_bytes)

    sheet_path = args.sheet
    if not sheet_path.exists():
        sheet_path = DEFAULT_SHEET_FALLBACK
    jp_by, ko_by = load_sheet(sheet_path)

    inputs = {
        "rom": file_identity(args.rom, rom),
        "base_rom": file_identity(args.base_rom, base_bytes),
        "original_rom": file_identity(args.original_rom, original_bytes),
        "sheet": file_identity(sheet_path),
        "tbl": file_identity(args.tbl),
        "meta": file_identity(args.meta),
    }

    safe_print("building locs (script+name75+aux)...")
    locs = build_dict_token_locs(rom, regions=("script", "name75", "aux"))

    findings: List[dict] = []
    stock_aux_findings: List[dict] = []
    baddict_samples: List[dict] = []
    raw_ext_aux_hangul = 0
    raw_stock_aux_hangul = 0

    # Prefer original ROM for stock-only expand (BADDICT/JP baseline).
    d_stock = d_orig if d_orig is not None else d_base

    for idx, refs in sorted(locs.items()):
        refs = uniq_refs(refs)
        if not refs:
            continue
        try:
            raw = d.raw_entry(idx)
        except Exception:
            continue
        if not raw:
            continue
        try:
            tip_text = d.expand(raw, tbl).rstrip("\u3000")
        except Exception:
            tip_text = ""
        hangul_payload = has_hangul_marker(raw) or has_hangul(tip_text)
        if not hangul_payload or not has_hangul(tip_text):
            continue

        aux_refs = [r for r in refs if r.region == "aux"]
        if not aux_refs:
            continue

        is_ext = idx >= stock
        if is_ext:
            raw_ext_aux_hangul += 1
        else:
            raw_stock_aux_hangul += 1

        name_refs = [r for r in refs if r.region == "name75"]
        script_refs = [r for r in refs if r.region == "script"]
        early_script = [r for r in script_refs if EARLY_LO <= r.abs <= EARLY_HI]
        lead, trail = token_bytes(idx)
        ff_page = lead == 0xFF

        confirmed_aux: List[dict] = []
        mix_jp_hangul = 0
        stock_baddict = 0
        for r in aux_refs:
            tip_aux = expand_aux_at(rom, d, tbl, r.abs)
            if not contains_tip(tip_aux, tip_text):
                continue
            stock_aux = ""
            if d_stock is not None:
                stock_aux = expand_aux_at(rom, d_stock, tbl, r.abs)

            tip_mix = has_hangul(tip_aux) and (
                has_jp(tip_aux) or bool(BADDICT_RE.search(tip_aux or ""))
            )
            if tip_mix:
                mix_jp_hangul += 1
            stock_poison = bool(BADDICT_RE.search(stock_aux or "")) or (
                bool(stock_aux)
                and not has_hangul(stock_aux)
                and has_hangul(tip_aux)
            )
            if stock_poison:
                stock_baddict += 1
                if len(baddict_samples) < 40:
                    baddict_samples.append(
                        {
                            "dict_index": idx,
                            "aux_abs": f"{r.abs:06X}",
                            "tip_expand": tip_aux[:80],
                            "stock_expand": stock_aux[:80],
                        }
                    )
            confirmed_aux.append(
                {
                    "abs": f"{r.abs:06X}",
                    "bank": r.abs >> 16,
                    "tip_expand": tip_aux[:80],
                    "stock_expand": (stock_aux or "")[:80],
                    "tip_has_jp": has_jp(tip_aux),
                    "stock_has_baddict": bool(BADDICT_RE.search(stock_aux or "")),
                }
            )

        if not confirmed_aux:
            continue

        # Drop short intentional UI nouns unless they leave JP/Hangul stew or BADDICT stew.
        dlg = dialogue_like_tip(tip_text)
        if not dlg and mix_jp_hangul == 0 and not (is_ext and ff_page and stock_baddict):
            continue
        # FF padding tokens with tiny tips (……포우) flood banks — keep only if mix stew.
        if hangul_count(tip_text) < 4 and mix_jp_hangul == 0:
            continue
        if not dlg and mix_jp_hangul < 2 and hangul_count(tip_text) < 6:
            # Keep short UI terms out of "invasion" report (still counted in raw_*).
            continue

        early_jp: List[str] = []
        early_sheet_ko_disagree = 0
        for r in early_script[:12]:
            jp = jp_by.get(r.abs, "")
            sk = ko_by.get(r.abs, "")
            if jp:
                early_jp.append(jp[:50])
            if sk and tip_text and sk != tip_text and compact(sk) not in compact(tip_text):
                early_sheet_ko_disagree += 1

        severity = "medium"
        reasons: List[str] = ["confirmed_tip_ko_in_aux_expand"]
        if mix_jp_hangul:
            severity = "high"
            reasons.append("aux_expand_hangul_plus_jp_or_baddict")
        if is_ext and ff_page:
            reasons.append("ff_page_ext_aux_consumer")
            severity = "high"
        if stock_baddict:
            severity = "high"
            reasons.append("stock_dict_baddict_or_jp_vs_tip_hangul")
        if early_script:
            reasons.append("early_script_and_aux_share")
            if any(len(j) >= 8 and has_jp(j) for j in early_jp):
                severity = "high"
                reasons.append("early_sheet_jp_dialogue_vs_aux_ui")
        if early_sheet_ko_disagree:
            reasons.append("early_sheet_ko_disagrees_tip")
            severity = "high"
        if not is_ext:
            reasons.append("stock_hangul_aux_share")
            if d_base is not None and idx < d_base.count:
                try:
                    br = d_base.raw_entry(idx)
                    if br and not has_hangul_marker(br) and len(raw) > len(br) + 4:
                        reasons.append("stock_sole_residue_style")
                        severity = "high"
                except Exception:
                    pass

        early_kos = sorted(
            {
                ko_by[r.abs]
                for r in early_script
                if r.abs in ko_by and ko_by[r.abs]
            }
        )

        row = {
            "dict_index": idx,
            "token": f"{lead:02X}{trail:02X}",
            "ff_page": ff_page,
            "ext": is_ext,
            "tip_ko": tip_text[:80],
            "tip_len": len(raw),
            "aux_n": len(confirmed_aux),
            "aux_raw_token_hits": len(aux_refs),
            "name75_n": len(name_refs),
            "script_n": len(script_refs),
            "early_script_n": len(early_script),
            "aux_sample_abs": [a["abs"] for a in confirmed_aux[:8]],
            "aux_banks": sorted({a["bank"] for a in confirmed_aux}),
            "mix_jp_hangul_aux_n": mix_jp_hangul,
            "stock_baddict_aux_n": stock_baddict,
            "severity": severity,
            "dialogue_like_tip": dlg,
            "hangul_chars": hangul_count(tip_text),
            "reasons": reasons,
            "early_sheet_jp_samples": early_jp[:4],
            "early_distinct_sheet_ko": [k[:40] for k in early_kos[:4]],
            "aux_samples": confirmed_aux[:6],
        }
        if is_ext:
            findings.append(row)
        else:
            stock_aux_findings.append(row)

    def rank(f: dict) -> Tuple:
        sev = 0 if f["severity"] == "high" else 1
        return (
            sev,
            0 if f.get("dialogue_like_tip") else 1,
            -f["mix_jp_hangul_aux_n"],
            -f.get("hangul_chars", 0),
            -f["early_script_n"],
            -f["aux_n"],
            f["dict_index"],
        )

    findings.sort(key=rank)
    stock_aux_findings.sort(key=rank)

    # Sample bank-53 stew around 530337
    sample_abs = args.sample_around
    sample_lo = (sample_abs & ~0xFFFF) | max(0, (sample_abs & 0xFFFF) - 0x80)
    sample_hi = (sample_abs & ~0xFFFF) | min(0xFFFF, (sample_abs & 0xFFFF) + 0x180)
    bank53_samples: List[dict] = []
    for logical, payload, _kind in _walk_zstring_range(
        rom, sample_lo, sample_hi, region="aux", max_len=128
    ):
        try:
            tip_ex = d.expand(payload, tbl).rstrip("\u3000")
        except Exception:
            tip_ex = ""
        stock_ex = ""
        if d_stock is not None:
            try:
                stock_ex = d_stock.expand(payload, tbl).rstrip("\u3000")
            except Exception:
                stock_ex = ""
        if not has_hangul(tip_ex) and logical != sample_abs:
            continue
        bank53_samples.append(
            {
                "abs": f"{logical:06X}",
                "tip_expand": tip_ex[:100],
                "stock_expand": stock_ex[:100],
                "payload_hex": payload[:24].hex(),
                "has_hangul": has_hangul(tip_ex),
                "has_jp": has_jp(tip_ex),
            }
        )

    high_ext = [f for f in findings if f["severity"] == "high"]
    report = {
        "generated_by": "tools/scan_aux_ff_invasion.py",
        "read_only": True,
        "inputs": inputs,
        "out": str(args.out.resolve()),
        "rom": str(args.rom),
        "sheet": str(sheet_path),
        "stock_count": stock,
        "dict_count": d.count,
        "aux_banks": list(AUX_TOKEN_BANKS),
        "counts": {
            "ext_hangul_aux_token_hits_raw": raw_ext_aux_hangul,
            "ext_hangul_confirmed_in_aux": len(findings),
            "ext_high_severity": len(high_ext),
            "ext_ff_page_confirmed": sum(1 for f in findings if f["ff_page"]),
            "stock_hangul_aux_token_hits_raw": raw_stock_aux_hangul,
            "stock_hangul_confirmed_in_aux": len(stock_aux_findings),
            "stock_high_severity": sum(
                1 for f in stock_aux_findings if f["severity"] == "high"
            ),
            "stock_sole_residue_style": sum(
                1
                for f in stock_aux_findings
                if "stock_sole_residue_style" in f["reasons"]
            ),
            "ext_dialogue_like": sum(1 for f in findings if f.get("dialogue_like_tip")),
            "stock_dialogue_like": sum(
                1 for f in stock_aux_findings if f.get("dialogue_like_tip")
            ),
            "ext_bank53_hits": sum(
                1 for f in findings if 0x53 in f.get("aux_banks", [])
            ),
        },
        "top_ext": findings[: args.top],
        "top_stock": stock_aux_findings[: args.top],
        "all_ext_indices": [f["dict_index"] for f in findings],
        "all_stock_indices": [f["dict_index"] for f in stock_aux_findings],
        "baddict_vs_stock_samples": baddict_samples[:30],
        "bank53_hangul_stew_samples": bank53_samples[:40],
        "principles": [
            "FF-page ext indices (token FF xx) collide with raw FF-lead bytes in aux banks; never assign dialogue Hangul to a live aux/name75 consumer index.",
            "Any reclaim/steal/shared rewrite must scan refs with regions=(script,name75,aux); aux hit => refuse.",
            "Before overwriting a stolen slot, restore or retarget every former consumer; fail closed on restore_fail.",
            "Prefer true free slots / curated pair-steal; do not pad story owners with a shared UI fragment token.",
            "Stock Hangul with aux consumers + distinct early script KO is sole-residue / shared-rewrite poison — repair, do not bulk apply_safe_unit.",
            "Tip aux expand showing Hangul where stock-only Dictionary shows BADDICT/JP means original UI was invaded by ext dialogue.",
            "Confirm invasion by tip_ko substring in aux expand; raw FF padding token hits alone are not ownership.",
        ],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    c = report["counts"]
    safe_print(
        f"raw_ext_aux={c['ext_hangul_aux_token_hits_raw']} "
        f"confirmed_ext={c['ext_hangul_confirmed_in_aux']} "
        f"high={c['ext_high_severity']} ff_page={c['ext_ff_page_confirmed']} | "
        f"raw_stock_aux={c['stock_hangul_aux_token_hits_raw']} "
        f"confirmed_stock={c['stock_hangul_confirmed_in_aux']} "
        f"stock_high={c['stock_high_severity']} sole_style={c['stock_sole_residue_style']}"
    )
    safe_print(f"wrote {args.out}")
    safe_print(f"--- top {args.top} confirmed ext ---")
    for f in findings[: args.top]:
        safe_print(
            f"idx={f['dict_index']} tok={f['token']} sev={f['severity']} "
            f"aux_n={f['aux_n']} early_script_n={f['early_script_n']} "
            f"mix={f['mix_jp_hangul_aux_n']} baddict={f['stock_baddict_aux_n']} "
            f"aux_sample_abs={f['aux_sample_abs'][:4]} "
            f"tip_ko={f['tip_ko'][:40]!r}"
        )
    safe_print("--- top 10 stock confirmed ---")
    for f in stock_aux_findings[:10]:
        safe_print(
            f"idx={f['dict_index']} tok={f['token']} sev={f['severity']} "
            f"aux_n={f['aux_n']} early_script_n={f['early_script_n']} "
            f"sole={'stock_sole_residue_style' in f['reasons']} "
            f"tip_ko={f['tip_ko'][:40]!r} aux={f['aux_sample_abs'][:3]}"
        )
    safe_print("--- bank53 samples ---")
    for s in bank53_samples[:12]:
        safe_print(
            f"{s['abs']} hangul={s['has_hangul']} jp={s['has_jp']} "
            f"tip={s['tip_expand'][:55]!r}"
        )
        if s.get("stock_expand"):
            safe_print(f"         stock={s['stock_expand'][:55]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
