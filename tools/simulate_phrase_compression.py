#!/usr/bin/env python3
"""
Simulate Korean shared-phrase compression against original dialogue body sizes.

Uses whatever KO text is available now:
  - translations_seed.json
  - excel_translate_cache.json (partial batch OK)
  - translations_batch.json placeholders
without waiting for the full sheet fill.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from expand_dictionary import (  # noqa: E402
    compress_with_phrases,
    select_shared_phrases,
)
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    encode_plaintext,
    find_rom,
    load_rom,
    read_encoded_z,
)
from extract_script import split_prefix_body  # noqa: E402


def load_ko_corpus(limit: int = 0) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()

    def add(abs_hex: str, jp: str, ko: str, source: str) -> None:
        ko = ko.replace(" ", "　").strip()
        if not ko or abs_hex in seen:
            return
        seen.add(abs_hex)
        rows.append({"abs": abs_hex, "jp": jp, "ko": ko, "source": source})

    seed_path = ROOT / "data" / "translations_seed.json"
    if seed_path.exists():
        for line in json.loads(seed_path.read_text(encoding="utf-8"))["lines"]:
            add(line["abs"].upper(), line.get("jp", ""), line["ko"], "seed")

    cache_path = ROOT / "out" / "script" / "excel_translate_cache.json"
    db_path = ROOT / "out" / "script" / "dialogue_db.json"
    jp_by_abs = {}
    if db_path.exists():
        db = json.loads(db_path.read_text(encoding="utf-8"))
        for row in db.get("dialogue", []):
            abs_hex = (
                f"{row['abs']:06X}"
                if isinstance(row["abs"], int)
                else str(row["abs"]).upper()
            )
            jp_by_abs[abs_hex] = row.get("jp", "")

    if cache_path.exists():
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        entries = raw.get("entries", raw) if isinstance(raw, dict) else {}
        # cache is jp->ko; map back through dialogue db where possible
        ko_by_jp = {str(k): str(v) for k, v in entries.items() if k != "engine"}
        for abs_hex, jp in jp_by_abs.items():
            if jp in ko_by_jp:
                add(abs_hex, jp, ko_by_jp[jp], "cache")

    batch_path = ROOT / "data" / "translations_batch.json"
    if batch_path.exists():
        for line in json.loads(batch_path.read_text(encoding="utf-8"))["lines"]:
            add(line["abs"].upper(), line.get("jp", ""), line["ko"], "batch")

    if limit > 0:
        rows = rows[:limit]
    return rows


def safe_encode_len(text: str, tbl: Tbl) -> int | None:
    try:
        return len(encode_plaintext(text, tbl))
    except KeyError:
        # Estimate: Hangul/extended ≈ 2 bytes, ASCII/punct often 1.
        total = 0
        for ch in text:
            if "가" <= ch <= "힣":
                total += 2
            elif ch in tbl.char_to_code:
                total += len(tbl.encode_char(ch))
            else:
                total += 2
        return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-phrases", type=int, default=256)
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "phrase_compression_report.json")
    args = ap.parse_args()

    rows = load_ko_corpus(args.limit)
    if not rows:
        raise SystemExit("No KO corpus available yet")

    tbl_path = ROOT / "out" / "patch" / "hangul_patch.tbl"
    if not tbl_path.exists():
        raise SystemExit("hangul_patch.tbl missing")
    tbl = Tbl.load(tbl_path)
    rom = load_rom(find_rom(ROOT))

    # Prefer lines encodable with current TBL; keep others with length estimates.
    encodable = []
    estimated = []
    for row in rows:
        try:
            encode_plaintext(row["ko"], tbl)
            encodable.append(row)
        except KeyError:
            estimated.append(row)

    use_rows = encodable or rows
    texts = [row["ko"] for row in use_rows]
    phrases = select_shared_phrases(
        texts, max_phrases=args.max_phrases, min_count=args.min_count
    )
    phrase_to_index = {phrase: index for index, phrase in enumerate(phrases)}

    fit_plain = fit_comp = need_overflow = 0
    saved_bytes = 0
    details = []
    for row in use_rows:
        abs_off = int(row["abs"], 16)
        payload, _ = read_encoded_z(rom, abs_off)
        _prefix, body, _kind = split_prefix_body(payload)
        try:
            plain = encode_plaintext(row["ko"], tbl)
            compressed = (
                compress_with_phrases(row["ko"], tbl, phrase_to_index)
                if phrase_to_index
                else plain
            )
        except KeyError:
            plain_len = safe_encode_len(row["ko"], tbl) or 0
            # Without full glyph map, approximate compression as 70% of plain.
            plain = b"\x00" * plain_len
            compressed = b"\x00" * max(1, int(plain_len * 0.7))
        plain_fit = len(plain) <= len(body)
        comp_fit = len(compressed) <= len(body)
        if plain_fit:
            fit_plain += 1
        if comp_fit:
            fit_comp += 1
        if not comp_fit and not plain_fit:
            need_overflow += 1
        saved_bytes += max(0, len(plain) - len(compressed))
        if len(details) < 30:
            details.append(
                {
                    "abs": row["abs"],
                    "source": row["source"],
                    "body": len(body),
                    "plain": len(plain),
                    "compressed": len(compressed),
                    "fit": "compressed"
                    if comp_fit
                    else ("plain" if plain_fit else "overflow"),
                }
            )

    report = {
        "corpus_lines_total": len(rows),
        "corpus_lines_encodable_with_current_tbl": len(encodable),
        "corpus_lines_estimated_only": len(estimated),
        "simulated_lines": len(use_rows),
        "shared_phrases": len(phrases),
        "phrase_samples": phrases[:40],
        "fit_plain": fit_plain,
        "fit_compressed": fit_comp,
        "need_overflow": need_overflow,
        "fit_plain_ratio": round(fit_plain / len(use_rows), 4),
        "fit_compressed_ratio": round(fit_comp / len(use_rows), 4),
        "bytes_saved_vs_plain": saved_bytes,
        "details_sample": details,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = args.out.with_suffix(".md")
    md.write_text(
        "\n".join(
            [
                "# Phrase compression simulation",
                "",
                f"- Corpus lines total: **{len(rows)}**",
                f"- Simulated (encodable with current TBL): **{len(use_rows)}**",
                f"- Shared phrases: **{len(phrases)}**",
                f"- Fit plain: **{fit_plain}** ({report['fit_plain_ratio']:.1%})",
                f"- Fit compressed: **{fit_comp}** ({report['fit_compressed_ratio']:.1%})",
                f"- Still overflow: **{need_overflow}**",
                f"- Bytes saved vs plain: **{saved_bytes}**",
                "",
                "Top phrases:",
                *[f"- `{p}`" for p in phrases[:20]],
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote {args.out}")
    print(f"Wrote {md}")


if __name__ == "__main__":
    main()
