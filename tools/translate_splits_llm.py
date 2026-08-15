#!/usr/bin/env python3
"""
Batch translate untranslated Japanese dialogue lines in out/script/splits/*.csv to Korean.

Applies deterministic patterns for exclamations/punctuation, uses fast multi-threaded
translation for dialogue bodies, and normalizes game punctuation.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

csv.field_size_limit(2147483647)

ROOT = Path(__file__).resolve().parents[1]
from translation_source_policy import reject_legacy_generator
SPLITS_DIR = ROOT / "out" / "script" / "splits"

# Deterministic mappings for emotion/sound/punctuation-only lines
DETERMINISTIC_MAP = {
    "……………": "……………",
    "…………": "…………",
    "……": "……",
    "……っ！": "……윽！",
    "……っ！！": "……윽！！",
    "……っ！？": "……윽！？",
    "……はっ！！": "……핫！！",
    "……はっ！": "……핫！",
    "……くっ！": "……큭！",
    "……くっ！！": "……큭！！",
    "……あ……": "……아……",
    "……う……": "……으……",
    "……ん……": "……음……",
    "……な……": "……뭣……",
    "……バカな！": "……말도 안 돼！",
    "……バカな……": "……말도 안 돼……",
    "……なに！？": "……뭐라고！？",
    "……なに？": "……뭐지？",
    "……何！？": "……뭐라고！？",
    "……何？": "……뭐지？",
    "……ええ": "……예",
    "……ええ。": "……예.",
    "……はい。": "……예.",
    "……はい！": "……네！",
    "……いいえ。": "……아닙니다.",
    "……フッ……": "……훗……",
    "……フフフ……": "……후후후……",
}

def normalize_game_punctuation(text: str) -> str:
    if not text:
        return ""
    text = text.replace("...", "……").replace("‥", "……")
    text = text.replace("!", "！").replace("?", "？")
    while "。。。" in text:
        text = text.replace("。。。", "……")
    text = text.replace("！　！", "！！").replace("？　？", "？？")
    text = text.replace("　……", "……").replace("……　", "……")
    text = text.replace(" ", "　")
    return text

def translate_google_single(text: str) -> str:
    if not text.strip():
        return ""
    if text in DETERMINISTIC_MAP:
        return DETERMINISTIC_MAP[text]
    
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=ja&tl=ko&dt=t&q={urllib.parse.quote(text)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                res = "".join(item[0] for item in data[0] if item and item[0])
                res = normalize_game_punctuation(res)
                return res
        except Exception:
            if attempt < 4:
                time.sleep(0.2 * (attempt + 1))
    return ""

def process_file(filepath: Path) -> tuple[int, int]:
    rows = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for r in reader:
            rows.append(r)
            
    untranslated = [r for r in rows if r.get("jp", "").strip() and not r.get("ko", "").strip()]
    if not untranslated:
        return 0, len(rows)

    # Batch translate untranslated lines in this file
    jp_texts = [r["jp"].strip() for r in untranslated]
    
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(translate_google_single, jp_texts))

    for r, translated_ko in zip(untranslated, results):
        if translated_ko:
            r["ko"] = translated_ko

    with open(filepath, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return len(untranslated), len(rows)

def main():
    reject_legacy_generator(Path(__file__))
    split_files = sorted(SPLITS_DIR.glob("split_*.csv"))
    print(f"Found {len(split_files)} split CSV files.")
    
    total_untranslated = 0
    total_rows = 0
    files_processed = 0

    for idx, filepath in enumerate(split_files, 1):
        count_updated, count_rows = process_file(filepath)
        total_rows += count_rows
        total_untranslated += count_updated
        if count_updated > 0:
            files_processed += 1
            print(f"[{idx}/{len(split_files)}] {filepath.name}: Translated {count_updated} lines.")

    print("=" * 60)
    print(f"Translation complete! Processed {files_processed} files, updated {total_untranslated} lines out of {total_rows} total rows.")

if __name__ == "__main__":
    main()
