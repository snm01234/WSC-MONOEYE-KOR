#!/usr/bin/env python3
"""
Propose still-Japanese dictionary slots that the non-dialogue surfaces read.

READ-ONLY. This tool never opens a .wsc for writing.

``audit_nondialogue_ko.py`` reports ~2,700 UI-facing slots still Japanese, but
most are grammar fragments the compressor happens to share with the menus
(``します`` x407, ``この`` x265, ``した`` x234). Translating those produces
gibberish, which is why the shipped catalogs are curated by hand.

This tool does the curation mechanically. A slot becomes a candidate when it is:

  * referenced from aux (50-5F, 76) or name75 — i.e. a menu / HUD / unit-table
    consumer actually reads it, measured on the ORIGINAL per DICT_INVASION_GUARD
  * still Japanese on the target
  * term-shaped: kanji-only, katakana-only, or fullwidth-alnum — never containing
    hiragana, which is the grammar-fragment signature
  * at least ``--min-chars`` characters, so single-glyph shards are excluded

and it is additionally classified ``safe`` or ``glued``:

  glued — in some consumer the slot's own span touches katakana or a long-vowel
          mark, so localizing it would half-translate a longer word. This is the
          exact class that produced ``불가－ジ`` from ``ダメ``, so candidates are
          separated rather than mixed. Span attribution is reused from
          scan_fragment_composition_hazard.build_span_finder.

Output is a catalog skeleton with empty ``ko`` fields, ranked by consumer count:
``out/script/ui_facing_term_candidates.json`` (+ ``.md`` for review).
Nothing is applied; fill in ``ko`` and feed it through apply_proper_nouns.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import (  # noqa: E402
    DEFAULT_REF_REGIONS,
    build_dict_token_locs,
    iter_dict_indices,
)
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    find_rom,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)
from scan_fragment_composition_hazard import build_span_finder, is_glue  # noqa: E402

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_ui_work.wsc"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_OUT = ROOT / "out/script/ui_facing_term_candidates.json"

HANGUL = re.compile(r"[\uac00-\ud7a3]")
HIRAGANA = re.compile(r"[\u3040-\u309f]")
KANJI = re.compile(r"[\u4e00-\u9fff]")
KATAKANA = re.compile(r"[\u30a0-\u30ff]")
# Characters allowed inside a term-shaped phrase alongside the main script.
TERM_EXTRA = set("－ー・（）／　")
FULLWIDTH_ALNUM = re.compile(r"[\uff10-\uff19\uff21-\uff3a\uff41-\uff5a]")


def term_shape(text: str) -> str | None:
    """kanji / katakana / alnum / mixed_term, or None when not term-shaped."""
    if not text or HIRAGANA.search(text):
        return None
    core = [ch for ch in text if ch not in TERM_EXTRA]
    if not core:
        return None
    has_kanji = any(KANJI.match(ch) for ch in core)
    has_kata = any(KATAKANA.match(ch) for ch in core)
    has_alnum = any(FULLWIDTH_ALNUM.match(ch) for ch in core)
    if not (has_kanji or has_kata):
        return "alnum" if has_alnum else None
    # Reject anything with characters outside the recognised term scripts
    # (control placeholders like <E7E5>, block glyphs, stray punctuation).
    for ch in core:
        if not (
            KANJI.match(ch) or KATAKANA.match(ch) or FULLWIDTH_ALNUM.match(ch)
        ):
            return None
    if has_kanji and has_kata:
        return "mixed_term"
    return "kanji" if has_kanji else "katakana"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_TARGET, help="target ROM")
    ap.add_argument("--base-rom", type=Path, default=None, help="default: original")
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--meta", type=Path, default=ROOT / "out/patch/ext_dictionary_meta.json")
    ap.add_argument(
        "--ext3-meta", type=Path, default=ROOT / "out/patch/ext3_dictionary_meta.json"
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--min-chars", type=int, default=2)
    ap.add_argument("--min-consumers", type=int, default=1)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this miner is read-only")

    base_path = args.base_rom or find_rom(ROOT)
    original = bytes(load_rom(base_path))
    target = bytes(load_rom(args.rom))
    tbl = Tbl.load(args.tbl)
    d = Dictionary(original)
    d_tgt = make_dictionary_ext3(
        target, load_ext_meta(args.meta), load_ext_meta(args.ext3_meta)
    )

    # Terms already covered by any shipped catalog (or quarantined on purpose).
    known: set[str] = set()
    for path in sorted((ROOT / "data").glob("*_ko.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in list(spec.get("entries") or []) + list(spec.get("fragments") or []):
            if row.get("jp"):
                known.add(row["jp"])
    quarantine = ROOT / "data/_quarantine_fragments.json"
    if quarantine.exists():
        for row in json.loads(quarantine.read_text(encoding="utf-8")).get("removed", []):
            if row.get("jp"):
                known.add(row["jp"])

    locs = build_dict_token_locs(original, regions=DEFAULT_REF_REGIONS)
    spans_of = build_span_finder(d, tbl)
    sb = stock_base(original)

    parents: Dict[int, List[int]] = {}
    for idx in range(d.count):
        for child in iter_dict_indices(d.raw_entry(idx)):
            if child < d.count:
                parents.setdefault(child, []).append(idx)

    record_cache: Dict[int, tuple[bytes, str]] = {}

    def record(logical: int) -> tuple[bytes, str]:
        hit = record_cache.get(logical)
        if hit is not None:
            return hit
        payload, text = b"", ""
        try:
            got = read_encoded_z_safe(original, sb + logical, max_len=128)
            if got:
                payload = got[0]
                text = d.expand(payload, tbl)
        except Exception:
            payload, text = b"", ""
        record_cache[logical] = (payload, text)
        return payload, text

    def is_glued(idx: int, jp: str) -> tuple[bool, List[str]]:
        """True when this slot's own span touches katakana in some consumer."""
        evidence: List[str] = []
        pairs = [(d.raw_entry(p), f"dict:{p:04X}") for p in parents.get(idx, [])[:40]]
        for ref in locs.get(idx, [])[:60]:
            payload, text = record(ref.abs)
            if text:
                pairs.append((payload, f"{ref.region}:{ref.abs:06X}"))
        for payload, tag in pairs:
            try:
                text = d.expand(payload, tbl)
            except Exception:
                continue
            for at, end in spans_of(payload, idx):
                if text[at:end] != jp:
                    continue
                before = text[at - 1] if at > 0 else ""
                after = text[end] if end < len(text) else ""
                if is_glue(before) or is_glue(after):
                    if len(evidence) < 3:
                        lo = max(0, at - 6)
                        evidence.append(f"{tag} {text[lo:end + 6]!r}")
        return bool(evidence), evidence

    safe: List[dict] = []
    glued: List[dict] = []

    for idx in range(d.count):
        refs = locs.get(idx) or []
        aux_refs = [r for r in refs if r.region != "script"]
        if len(aux_refs) < args.min_consumers:
            continue
        jp = d.expand_index(idx, tbl)
        shape = term_shape(jp)
        if shape is None:
            continue
        core_len = len([c for c in jp if c not in TERM_EXTRA])
        if core_len < args.min_chars:
            continue
        if jp in known:
            continue
        if HANGUL.search(d_tgt.expand_index(idx, tbl)):
            continue
        bad, evidence = is_glued(idx, jp)
        row = {
            "jp": jp,
            "ko": "",
            "index": f"{idx:04X}",
            "shape": shape,
            "aux_consumers": len(aux_refs),
            "script_consumers": len(refs) - len(aux_refs),
            "regions": sorted({r.region for r in aux_refs}),
        }
        if bad:
            row["glue_evidence"] = evidence
            glued.append(row)
        else:
            safe.append(row)

    safe.sort(key=lambda r: -r["aux_consumers"])
    glued.sort(key=lambda r: -r["aux_consumers"])

    payload = {
        "_note": (
            "Candidate UI-facing dictionary terms with empty ko. Fill in "
            "'safe_candidates' and apply with tools/apply_proper_nouns.py. "
            "'glued_candidates' would half-translate a longer word — only take "
            "one together with whole-word entries for the words that contain it."
        ),
        "generated_by": "tools/mine_ui_facing_terms.py",
        "original": str(base_path),
        "target": str(args.rom),
        "min_chars": args.min_chars,
        "safe_count": len(safe),
        "glued_count": len(glued),
        "safe_candidates": safe,
        "glued_candidates": glued,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    md = [
        "# UI-facing term candidates",
        "",
        f"safe: **{len(safe)}** · glued: **{len(glued)}**",
        "",
        "| jp | idx | shape | aux | script |",
        "|---|---|---|---:|---:|",
    ]
    for row in safe[:400]:
        md.append(
            f"| `{row['jp']}` | {row['index']} | {row['shape']} | "
            f"{row['aux_consumers']} | {row['script_consumers']} |"
        )
    args.out.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")

    if not args.quiet:
        print(f"target : {args.rom}")
        print(f"safe   : {len(safe)} candidate term(s)")
        print(f"glued  : {len(glued)} (would half-translate a longer word)")
        print("\ntop safe candidates:")
        for row in safe[:35]:
            print(
                f"  {row['index']} x{row['aux_consumers']:<4d} "
                f"{row['shape']:10s} {row['jp']}"
            )
        print(f"\nwrote {args.out}")
        print(f"wrote {args.out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
