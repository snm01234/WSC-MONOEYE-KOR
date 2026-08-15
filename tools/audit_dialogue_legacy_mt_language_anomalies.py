#!/usr/bin/env python3
"""Language-model-like static anomaly ranking for remaining legacy-MT dialogue.

The model is deliberately local and deterministic: it learns character 3/4-gram
frequencies only from recently source-grounded Korean dialogue batches, then
ranks unreviewed current-main strings that look unlike that reviewed corpus.
It is not a translation source and never changes a ROM.
"""
from __future__ import annotations

import glob
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "out/script/dialogue_legacy_source_retranslation_worklist.json"
OUT = ROOT / "out/script/dialogue_legacy_mt_language_anomalies.json"

SPACE_RE = re.compile(r"[ \u3000]+")


def norm(text: str) -> str:
    text = SPACE_RE.sub(" ", str(text or "").strip())
    return f"^^{text}$$"


def strings_from_doc(path: Path) -> list[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = []
    if isinstance(doc.get("targets"), dict):
        out.extend(str(v) for v in doc["targets"].values())
    for group in doc.get("groups") or []:
        out.extend(str(v) for v in group.get("rows") or [])
    return out


def corpus_strings() -> list[str]:
    paths = []
    paths.extend(Path(p) for p in glob.glob(str(ROOT / "data/dialogue_20cell_llm_batches/batch*.json")))
    paths.extend(Path(p) for p in glob.glob(str(ROOT / "data/dialogue_singleton_rewrite_batch*.json")))
    paths.extend(Path(p) for p in glob.glob(str(ROOT / "data/dialogue_readability_batches/output_*.json")))
    paths.extend(Path(p) for p in glob.glob(str(ROOT / "data/dialogue_legacy_mt_literal_batch*.json")))
    out: list[str] = []
    for path in paths:
        out.extend(strings_from_doc(path))
    return [s for s in out if s]


def load_done() -> set[str]:
    done: set[str] = set()
    for raw in glob.glob(str(ROOT / "data/dialogue_legacy_mt_literal_batch*.json")):
        doc = json.loads(Path(raw).read_text(encoding="utf-8"))
        done.update(str(k).upper() for k in (doc.get("targets") or {}))
    return done


def main() -> int:
    corpus = corpus_strings()
    counts3: Counter[str] = Counter()
    counts4: Counter[str] = Counter()
    total3 = total4 = 0
    for text in corpus:
        s = norm(text)
        for i in range(max(0, len(s) - 2)):
            counts3[s[i:i+3]] += 1
            total3 += 1
        for i in range(max(0, len(s) - 3)):
            counts4[s[i:i+4]] += 1
            total4 += 1

    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    done = load_done()
    remaining = [r for r in work.get("records") or [] if str(r["abs"]).upper() not in done]
    vocab3 = max(1, len(counts3))
    vocab4 = max(1, len(counts4))

    ranked = []
    for row in remaining:
        text = str(row.get("current_render") or "")
        s = norm(text)
        # Add-one smoothed average surprise. 4-grams get a little more weight
        # because malformed glued Korean is particularly visible there.
        lp3 = []
        lp4 = []
        unseen3 = unseen4 = 0
        for i in range(max(0, len(s) - 2)):
            g = s[i:i+3]
            c = counts3.get(g, 0)
            unseen3 += c == 0
            lp3.append(-math.log((c + 1) / (total3 + vocab3)))
        for i in range(max(0, len(s) - 3)):
            g = s[i:i+4]
            c = counts4.get(g, 0)
            unseen4 += c == 0
            lp4.append(-math.log((c + 1) / (total4 + vocab4)))
        avg3 = sum(lp3) / len(lp3) if lp3 else 0.0
        avg4 = sum(lp4) / len(lp4) if lp4 else 0.0
        score = 0.4 * avg3 + 0.6 * avg4
        ranked.append({
            "abs": str(row["abs"]).upper(),
            "route": row.get("route"),
            "jp": row.get("jp"),
            "current": text,
            "score": round(score, 6),
            "unseen3_ratio": round(unseen3 / len(lp3), 4) if lp3 else 0.0,
            "unseen4_ratio": round(unseen4 / len(lp4), 4) if lp4 else 0.0,
        })

    ranked.sort(key=lambda x: (-float(x["score"]), -float(x["unseen4_ratio"]), int(x["abs"], 16)))
    report = {
        "schema_version": 1,
        "corpus_strings": len(corpus),
        "corpus_unique_trigrams": len(counts3),
        "corpus_unique_fourgrams": len(counts4),
        "remaining": len(remaining),
        "top_n": min(400, len(ranked)),
        "rows": ranked[:400],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("corpus_strings", "corpus_unique_trigrams", "corpus_unique_fourgrams", "remaining", "top_n")}, ensure_ascii=False, indent=2))
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
