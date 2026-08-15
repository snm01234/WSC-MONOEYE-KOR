#!/usr/bin/env python3
"""Rank still-unreviewed legacy-source dialogue for machine-translation residue.

Read-only with respect to ROM/data. The report is intended to prove whether the
legacy-MT retranslation sweep is actually exhausted, rather than treating the
absence of one heuristic as completion.
"""
from __future__ import annotations

import glob
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "out/script/dialogue_legacy_source_retranslation_worklist.json"
OUT = ROOT / "out/script/dialogue_legacy_mt_remaining_audit.json"

# Strong indicators that appeared in confirmed legacy MT failures.  Avoid very
# broad words (e.g. 대령님) that can also be valid Korean.
LEXICAL_PATTERNS = {
    "jp_pronoun_transliteration": re.compile(r"(?:오마에|키사마|코이츠)"),
    "jp_expletive_transliteration": re.compile(r"치쿠쇼"),
    "bad_acknowledgement": re.compile(r"(?:^|[　 ])라져(?:$|[　。！？])"),
    "bright_literal": re.compile(r"(?:^|[　 ])밝은(?:$|[　 ])"),
    "devil_gundam_literal": re.compile(r"악마[　 ]*건담"),
    "known_mt_register": re.compile(r"(?:백퍼센트|９９％|99％|결재|고결|기형|뇌파|도살|참격|권능|전술상[　 ]*결함|주파수막|상소|지당한[　 ]*전술|사격[　 ]*궤적|시체[　 ]*패배|격전지[　 ]*완수|고차원적인|위엄[　 ]*어린|종말을|영적[　 ]*주파수)"),
    "known_bad_loan_or_typo": re.compile(r"(?:타카가|조베|상송|만산국|하마[　 ]*님[　 ]*님|독수리의|대충[　 ]*부카이|실화인가|옛썰)"),
}

JP_RE = re.compile(r"[ぁ-んァ-ン一-龥]")
HANGUL_RE = re.compile(r"[가-힣]")
PUNCT_SPACE_RE = re.compile(r"[\s　、。！？!?…・「」『』（）()\-－―～~：:；;,.\"'0-9０-９Ａ-ＺA-ZＺ]+")
REPEAT_WORD_RE = re.compile(r"([가-힣]{2,8})(?:[　 ]*)\1")


def compact_len(text: str) -> int:
    return len(PUNCT_SPACE_RE.sub("", text or ""))


def load_done() -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in sorted(glob.glob(str(ROOT / "data/dialogue_legacy_mt_literal_batch*.json"))):
        doc = json.loads(Path(raw).read_text(encoding="utf-8"))
        for address, ko in (doc.get("targets") or {}).items():
            out[str(address).upper()] = str(ko)
    return out


def main() -> int:
    work = json.loads(WORKLIST.read_text(encoding="utf-8"))
    done = load_done()
    remaining = [r for r in work.get("records") or [] if str(r["abs"]).upper() not in done]

    rows = []
    reason_counts: Counter[str] = Counter()
    for row in remaining:
        jp = str(row.get("jp") or "")
        ko = str(row.get("current_render") or "")
        jn = compact_len(jp)
        kn = compact_len(ko)
        ratio = (kn / jn) if jn else 1.0
        reasons: list[str] = []
        for label, rx in LEXICAL_PATTERNS.items():
            if rx.search(ko):
                reasons.append(label)
        if JP_RE.search(ko):
            reasons.append("japanese_residual")
        if jn >= 8 and ratio < 0.58:
            reasons.append("strong_undertranslation")
        elif jn >= 10 and ratio < 0.68:
            reasons.append("moderate_undertranslation")
        if jn >= 3 and ratio > 1.85:
            reasons.append("strong_expansion")
        elif jn >= 5 and ratio > 1.62:
            reasons.append("moderate_expansion")
        if REPEAT_WORD_RE.search(ko):
            reasons.append("repeated_korean_word")
        if jp and not HANGUL_RE.search(ko) and any(ch not in "…！？!?。、・『』「」（）()　 " for ch in ko):
            reasons.append("no_hangul_in_render")

        if reasons:
            for reason in set(reasons):
                reason_counts[reason] += 1
            score = 0
            score += 100 * sum(r in {"japanese_residual", "jp_pronoun_transliteration", "jp_expletive_transliteration", "bad_acknowledgement", "bright_literal", "devil_gundam_literal", "bidan_literal", "known_bad_loan_or_typo"} for r in reasons)
            score += 35 * sum(r == "known_mt_register" for r in reasons)
            score += 25 * sum(r == "strong_undertranslation" for r in reasons)
            score += 18 * sum(r == "strong_expansion" for r in reasons)
            score += 10 * sum(r in {"moderate_undertranslation", "moderate_expansion", "repeated_korean_word"} for r in reasons)
            rows.append({
                "abs": str(row["abs"]).upper(),
                "route": row.get("route"),
                "jp": jp,
                "current": ko,
                "jp_compact": jn,
                "ko_compact": kn,
                "ko_jp_ratio": round(ratio, 3),
                "reasons": sorted(set(reasons)),
                "score": score,
            })

    rows.sort(key=lambda x: (-int(x["score"]), float(x["ko_jp_ratio"]), int(x["abs"], 16)))
    report = {
        "schema_version": 1,
        "worklist_total": len(work.get("records") or []),
        "already_batched": len(done),
        "remaining": len(remaining),
        "remaining_by_route": dict(Counter(str(r.get("route")) for r in remaining)),
        "flagged": len(rows),
        "reason_counts": dict(reason_counts),
        "rows": rows,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("worklist_total", "already_batched", "remaining", "remaining_by_route", "flagged", "reason_counts")}, ensure_ascii=False, indent=2))
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
