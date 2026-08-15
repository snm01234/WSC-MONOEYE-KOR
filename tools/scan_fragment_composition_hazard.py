#!/usr/bin/env python3
"""
Find catalog terms that are mid-word fragments of longer Japanese words.

READ-ONLY. This tool never opens a .wsc for writing.

The dictionary is a compressor: a slot holding ``ダメ`` is not only the word
"no good", it is also the first half of ``ダメ－ジ`` (damage) in every record that
composes the longer word from that slot plus ``－ジ``. Localizing the slot to
``불가`` therefore renders ``불가－ジ`` on 154 battle/UI records. Measured hazards
in the shipped catalogs: ``ダメ→불가`` and ``リ－→리`` (the latter turning
``ジ－クフリ－ド`` into ``ジ－クフ리ド``).

This is invisible to the invasion guard, which asks *who reads this slot*, and
invisible to the coverage audit, which asks *is the slot Korean now*. The
question here is different: **does the slot sit inside a longer word?**

Rule. A hit is a hazard when the slot's Japanese text is kana (optionally with
the long-vowel mark) and, in some consumer's ORIGINAL expansion, the character
immediately before or after the match is katakana or a long-vowel mark. Kana
followed by a hiragana particle (``サイコミュは``, ``エゥ－ゴの``) is normal mixed
text and is not flagged; kanji terms (``攻撃力が``) are never flagged.

Consumers are enumerated on the ORIGINAL ROM per docs/DICT_INVASION_GUARD.md,
and nested dictionary parents are included — ``ダメ－ジ`` is itself a slot whose
payload references the ``ダメ`` slot, so the damage shows up one level up.

Exit 1 when any hazard is found, so this can gate a UI apply.
Report: ``out/patch/fragment_composition_hazard.json``.
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

DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_OUT = ROOT / "out/patch/fragment_composition_hazard.json"

# Every shipped dictionary-slot catalog. This list used to be hardcoded and
# went stale twice — ``ui_mined_terms_ko`` (129 rows) and
# ``ui_proper_nouns_ko`` (44 rows) shipped without ever being scanned, and the
# scan still reported ok/0 for them, which is the worst possible failure mode
# for a safety check. Discover the catalogs instead, and exclude only the files
# that are not dictionary-slot catalogs.
NON_SLOT_CATALOGS = frozenset(
    {
        "ui_inplace_ko",  # size-preserving in-place records, not dict slots
        "ui_spill_ko",  # in-place spill strings, not dict slots
        "name75_terms_ko",  # ext3 record rewrite (apply_name75_ko)
        "name75_base_ko",  # lexicon input, not applied directly
        "aux_text_ko",  # ext3 record rewrite (apply_aux_ko)
    }
)


def discover_catalogs() -> Sequence[str]:
    """Names of every dictionary-slot catalog under ``data/``."""
    out = []
    for path in sorted((ROOT / "data").glob("*_ko.json")):
        if path.stem in NON_SLOT_CATALOGS:
            continue
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if spec.get("entries") or spec.get("fragments"):
            out.append(path.stem)
    return tuple(out)

LONG_VOWEL = "ー－"
# U+30FB KATAKANA MIDDLE DOT lives inside the katakana block but is a name
# separator, not a letter: ``ハマ－ン・カ－ン`` is two words, so a term ending
# right before it is NOT mid-word. Treating it as glue false-flagged every
# personal name in the catalog.
NOT_A_LETTER = "・"
KATAKANA = re.compile(r"[\u30a0-\u30ff]")
KANA_ONLY = re.compile(rf"^[\u3040-\u30ff{LONG_VOWEL}]+$")

MAX_SITES_PER_TERM = 6
MAX_LISTED = 200


def is_glue(ch: str) -> bool:
    """A character that, adjacent to a kana slot, means we are inside a word."""
    if not ch or ch in NOT_A_LETTER:
        return False
    return bool(KATAKANA.match(ch)) or ch in LONG_VOWEL


def hazard_boundaries(
    container: str, needle: str, spans: Sequence[tuple[int, int]]
) -> List[dict]:
    """
    Mid-word occurrences of ``needle``, restricted to spans the slot produced.

    ``spans`` are the character ranges in ``container`` that this dictionary slot
    actually contributed. Plain substring matching is not enough: ``はい`` occurs
    inside ``シグはいいの``, but there the characters are plaintext bytes, not the
    ``はい`` slot, so localizing the slot would not touch them. Matching without
    provenance produced phantom hazards and would have deleted good entries.
    """
    out: List[dict] = []
    for at, end in spans:
        if container[at:end] != needle:
            continue
        before = container[at - 1] if at > 0 else ""
        after = container[end] if end < len(container) else ""
        glue_before = bool(before) and is_glue(before)
        glue_after = bool(after) and is_glue(after)
        if glue_before or glue_after:
            lo = max(0, at - 6)
            out.append(
                {
                    "at": at,
                    "before": before,
                    "after": after,
                    "side": "both"
                    if glue_before and glue_after
                    else ("before" if glue_before else "after"),
                    "context": container[lo : end + 6],
                }
            )
    return out


def build_span_finder(d: Dictionary, tbl: Tbl):
    """Return f(payload, target_index) -> spans that ``target_index`` contributed.

    Walks a payload the way ``Dictionary.expand`` does, tracking the output
    character offset, and descends into nested slots so a fragment referenced two
    levels down is still attributed correctly.
    """
    from monoeye_rom import (
        dict_index_from_ext3_token,
        dict_index_from_token,
        is_dict_token,
        is_ext3_magic,
        is_kanji_lead,
    )

    text_len: Dict[int, int] = {}

    def length_of(index: int) -> int:
        hit = text_len.get(index)
        if hit is None:
            try:
                hit = len(d.expand_index(index, tbl))
            except Exception:
                hit = 0
            text_len[index] = hit
        return hit

    def walk(
        payload: bytes, target: int, base: int, depth: int, out: List[tuple[int, int]]
    ) -> int:
        """Append spans; return the character length consumed by ``payload``."""
        if depth > 12:
            return 0
        pos = base
        i = 0
        n = len(payload)
        while i < n:
            b = payload[i]
            if b == 0:
                break
            if is_dict_token(b) and i + 1 < n:
                idx = dict_index_from_token(b, payload[i + 1])
                if idx == target:
                    out.append((pos, pos + length_of(idx)))
                    pos += length_of(idx)
                elif idx < d.count:
                    try:
                        sub = d.raw_entry(idx)
                    except Exception:
                        sub = b""
                    pos += walk(sub, target, pos, depth + 1, out)
                else:
                    pos += length_of(idx)
                i += 2
                continue
            if is_kanji_lead(b) and i + 1 < n:
                if is_ext3_magic(b, payload[i + 1]) and i + 3 < n:
                    idx = dict_index_from_ext3_token(
                        b, payload[i + 1], payload[i + 2], payload[i + 3]
                    )
                    if idx == target:
                        out.append((pos, pos + length_of(idx)))
                    pos += length_of(idx)
                    i += 4
                    continue
                pos += 1
                i += 2
                continue
            pos += 1
            i += 1
        return pos - base

    def spans(payload: bytes, target: int) -> List[tuple[int, int]]:
        out: List[tuple[int, int]] = []
        walk(payload, target, 0, 0, out)
        return out

    return spans


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-rom", type=Path, default=None, help="default: original")
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--catalog",
        action="append",
        default=None,
        help="restrict to these catalog names (repeatable)",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this scan is read-only")

    base_path = args.base_rom or find_rom(ROOT)
    original = bytes(load_rom(base_path))
    tbl = Tbl.load(args.tbl)
    d = Dictionary(original)

    names = args.catalog or list(discover_catalogs())

    # Catalog term → Korean, and the catalogs it came from.
    terms: Dict[str, dict] = {}
    for name in names:
        path = ROOT / f"data/{name}.json"
        if not path.exists():
            continue
        spec = json.loads(path.read_text(encoding="utf-8"))
        for row in list(spec.get("entries") or []) + list(spec.get("fragments") or []):
            jp, ko = row.get("jp"), row.get("ko")
            if not jp or not ko:
                continue
            hit = terms.setdefault(jp, {"ko": ko, "catalogs": []})
            hit["catalogs"].append(name)

    # Only kana terms can be glued mid-word by this mechanism.
    kana_terms = {jp: v for jp, v in terms.items() if KANA_ONLY.match(jp)}

    by_phrase: Dict[str, List[int]] = {}
    for idx in range(d.count):
        by_phrase.setdefault(d.expand_index(idx, tbl), []).append(idx)

    # Nested parents: dict slots whose payload references a given index.
    parents: Dict[int, List[int]] = {}
    for idx in range(d.count):
        for child in iter_dict_indices(d.raw_entry(idx)):
            if child < d.count:
                parents.setdefault(child, []).append(idx)

    locs = build_dict_token_locs(original, regions=DEFAULT_REF_REGIONS)

    sb = stock_base(original)
    spans_of = build_span_finder(d, tbl)
    record_cache: Dict[int, tuple[bytes, str]] = {}

    def record(logical: int) -> tuple[bytes, str]:
        """Original payload + expanded text of a consumer record, memoized."""
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

    hazards: List[dict] = []
    checked = 0
    for jp, info in sorted(kana_terms.items()):
        idxs = by_phrase.get(jp) or []
        if not idxs:
            continue
        checked += 1
        sites: List[dict] = []
        for idx in idxs:
            # Nested dictionary parents.
            for parent in parents.get(idx, []):
                try:
                    payload = d.raw_entry(parent)
                except Exception:
                    continue
                text = d.expand(payload, tbl)
                for b in hazard_boundaries(text, jp, spans_of(payload, idx)):
                    sites.append(
                        {
                            "kind": "dict_parent",
                            "ref": f"{parent:04X}",
                            "word": text[:40],
                            **b,
                        }
                    )
            # Direct consumers in script / name75 / aux.
            for ref in locs.get(idx, []):
                payload, text = record(ref.abs)
                if not text:
                    continue
                for b in hazard_boundaries(text, jp, spans_of(payload, idx)):
                    sites.append(
                        {
                            "kind": ref.region,
                            "ref": f"{ref.abs:06X}",
                            "word": text[:40],
                            **b,
                        }
                    )
        if sites:
            hazards.append(
                {
                    "jp": jp,
                    "ko": info["ko"],
                    "catalogs": sorted(set(info["catalogs"])),
                    "indices": [f"{i:04X}" for i in idxs],
                    "hit_count": len(sites),
                    "sites": sites[:MAX_SITES_PER_TERM],
                }
            )

    hazards.sort(key=lambda h: -h["hit_count"])
    report = {
        "ok": not hazards,
        "generated_by": "tools/scan_fragment_composition_hazard.py",
        "read_only": True,
        "original": str(base_path),
        "catalogs": names,
        "catalog_terms": len(terms),
        "kana_terms_checked": checked,
        "hazard_terms": len(hazards),
        "hazard_hits": sum(h["hit_count"] for h in hazards),
        "hazards": hazards[:MAX_LISTED],
        "rule": (
            "kana term whose ORIGINAL occurrence is adjacent to katakana or a "
            "long-vowel mark, i.e. the term is a piece of a longer word"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        print(f"original      : {base_path}")
        print(f"catalog terms : {len(terms)} ({checked} kana terms checked)")
        print(
            f"hazards       : {len(hazards)} term(s), "
            f"{report['hazard_hits']} occurrence(s) → "
            f"{'ok' if report['ok'] else 'FAIL'}"
        )
        for h in hazards[:20]:
            print(
                f"  {h['jp']} → {h['ko']}  x{h['hit_count']}  "
                f"{h['catalogs']} idx={h['indices']}"
            )
            for s in h["sites"][:3]:
                print(
                    f"      {s['kind']:8s} {s['ref']} glue={s['side']:6s} "
                    f"{s['context']!r}"
                )
        print(f"\nwrote {args.out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
