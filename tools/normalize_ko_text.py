#!/usr/bin/env python3
"""Normalize Korean draft text so it can be encoded with the game TBL."""

from __future__ import annotations

import re
from typing import Dict

from monoeye_rom import Tbl

# ASCII / Western punctuation → game glyphs already present in monoeye.tbl
PUNCT_MAP: Dict[str, str] = {
    ",": "、",
    ".": "。",
    "!": "！",
    "?": "？",
    "/": "／",
    "-": "－",
    "(": "（",
    ")": "）",
    "'": "’",
    '"': "”",
    ":": "：",
    ";": "；",
    "~": "～",
    "%": "％",
    "&": "＆",
    "·": "・",
    "‘": "’",
    "’": "’",
    "“": "”",
    "—": "－",
    "─": "－",
    "―": "－",
    # Hangul jamo / prolonged mark → game katakana long vowel
    "ㅡ": "ー",
    "\xa0": "　",
    "\u200b": "",
}

# Prefer fullwidth digits / Latin already in the JP font table.
DIGIT_MAP = {str(i): "０１２３４５６７８９"[i] for i in range(10)}

# Missing lowercase fullwidth letters → uppercase fullwidth fallback.
MISSING_FW_LOWER = {"ｇ": "Ｇ", "ｊ": "Ｊ", "ｑ": "Ｑ", "ｕ": "Ｕ", "ｙ": "Ｙ"}

# Angle-bracket placeholders produced by Dictionary.expand / Tbl.decode_char.
TAG_RE = re.compile(
    r"<BADDICT:[0-9A-Fa-f]{4}>|<(?:TRUNC:)?([0-9A-Fa-f]{2})>|<(?:TRUNC:)?([0-9A-Fa-f]{4})>"
)


def _to_fullwidth_alnum(ch: str) -> str:
    if ch in DIGIT_MAP:
        return DIGIT_MAP[ch]
    o = ord(ch)
    if 0x41 <= o <= 0x5A:  # A-Z
        return chr(0xFF21 + (o - 0x41))
    if 0x61 <= o <= 0x7A:  # a-z
        fw = chr(0xFF41 + (o - 0x61))
        return MISSING_FW_LOWER.get(fw, fw)
    return ch


def normalize_ko_text(text: str) -> str:
    """Map Western punctuation/digits/Latin and keep control tags intact."""
    text = text.replace("...", "……")
    text = text.replace("！　！", "！！").replace("？　？", "？？")
    text = text.replace(" ", "　")
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "<":
            m = TAG_RE.match(text, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
        ch = text[i]
        if ch in PUNCT_MAP:
            mapped = PUNCT_MAP[ch]
            if mapped:
                out.append(mapped)
        elif ch.isascii() and (ch.isalnum()):
            out.append(_to_fullwidth_alnum(ch))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def encode_ko_text(
    text: str,
    tbl: Tbl,
    *,
    hangul_marker_code: int | None = None,
    hangul_marker_mode: str = "run",
) -> bytes:
    """
    Encode normalized KO, restoring <FF>/<E7E5>-style control placeholders
    to raw bytes. BADDICT tags are dropped (already broken in source JP).

    If hangul_marker_code is set, emit that two-byte marker for Hangul:
      - mode \"run\" (default): once per contiguous Hangul run (sticky hook)
      - mode \"each\": before every Hangul syllable (legacy one-shot flag)
    """
    if hangul_marker_mode not in {"run", "each"}:
        raise ValueError(f"Unknown hangul_marker_mode: {hangul_marker_mode}")
    text = normalize_ko_text(text)
    out = bytearray()
    marker = (
        bytes([(hangul_marker_code >> 8) & 0xFF, hangul_marker_code & 0xFF])
        if hangul_marker_code is not None
        else b""
    )
    in_hangul_run = False
    i = 0
    while i < len(text):
        if text[i] == "<":
            m = TAG_RE.match(text, i)
            if m:
                token = m.group(0)
                if token.startswith("<BADDICT:"):
                    i = m.end()
                    continue
                hexpart = m.group(1) or m.group(2)
                value = int(hexpart, 16)
                if len(hexpart) == 2:
                    out.append(value)
                else:
                    out.append((value >> 8) & 0xFF)
                    out.append(value & 0xFF)
                in_hangul_run = False
                i = m.end()
                continue
        ch = text[i]
        # Drop rare Hangul jamo / other unmapped leftovers by skipping.
        if ch not in tbl.char_to_code:
            # try fullwidth / punct already applied; last resort skip
            if "ㄱ" <= ch <= "ㅎ" or "ㅏ" <= ch <= "ㅣ":
                i += 1
                continue
            raise KeyError(f"Character not in TBL: {ch!r}")
        if marker and "가" <= ch <= "힣":
            if hangul_marker_mode == "each" or not in_hangul_run:
                out.extend(marker)
            in_hangul_run = True
        else:
            in_hangul_run = False
        out.extend(tbl.encode_char(ch))
        i += 1
    return bytes(out)


def try_encode_ko_text(
    text: str,
    tbl: Tbl,
    *,
    hangul_marker_code: int | None = None,
    hangul_marker_mode: str = "run",
) -> bytes | None:
    try:
        return encode_ko_text(
            text,
            tbl,
            hangul_marker_code=hangul_marker_code,
            hangul_marker_mode=hangul_marker_mode,
        )
    except KeyError:
        return None


def hangul_count(text: str) -> int:
    return sum(1 for ch in text if "가" <= ch <= "힣")


# High-frequency Bing/decode stubs that pollute unique-KO ranking.
_KO_STUB_EXACT = {
    "！래",
    "는／은",
    "우하",
    "타하",
    "…기…",
    "기…",
    "…기계",
    "기계",
    "레！",
    "기！",
    "시는",
    "。。。。。。！！",
}

_BING_META_RE = re.compile(
    r"해당\s*일본어|일본어\s*텍스트|문자\s*그대로|정확한\s*의미|"
    r"번역할\s*수\s*없|번역\s*불가|의미를\s*파악|원문을\s*그|"
    r"기계\s*번역|번역기|번역\s*할\s*수\s*없|의미\s*를\s*확정"
)

_PARTICLE_SCAFFOLD_RE = re.compile(
    r"을（를）|를（을）|은（는）|는（은）|이（가）|가（이）|"
    r"의（으로서）|으로서（의）"
)


def is_low_quality_ko(ko: str) -> bool:
    """Reject Bing garbage / stub strings that dominate frequency ranking."""
    if not ko:
        return True
    plain = ko.replace("　", " ").replace("\n", " ")
    hn = hangul_count(ko)
    if hn < 2:
        return True
    if len(ko) < 3:
        return True
    if ko in _KO_STUB_EXACT or plain in _KO_STUB_EXACT:
        return True
    # Leftover kana from failed JP→KO conversion.
    if re.search(r"[\u3040-\u30ff]", ko):
        return True
    if re.search(r"([가-힣])\1{4,}", ko):
        return True
    # Particle-leading fragments (を/は mis-parsed as 을/는 …).
    if re.match(r"^[을를은는](　|\s)", ko):
        return True
    if re.match(r"^을[가-힣]{2,}", ko) and "　" in ko:
        return True
    if _PARTICLE_SCAFFOLD_RE.search(ko):
        return True
    # の → "학교" mistranslation scaffolding (very common Bing garbage).
    if ko.count("학교") >= 2:
        return True
    uniq = len({ch for ch in ko if "가" <= ch <= "힣"})
    if hn >= 8 and uniq <= 3:
        return True
    if hn >= 15 and uniq <= 6:
        return True
    if hn >= 10 and uniq / hn < 0.4:
        return True
    # Short stubs: isolated single syllables ("나","에！") or bang-leading ("！래").
    # Keep real digraphs: "안돼！","로라！","좋아！","……그래。".
    if hn <= 2 and not re.search(r"[가-힣]{2,}", ko):
        return True
    # Leading heavy punct (not ellipsis-only openers like "……그래。").
    if re.match(r"^[！？。・、，]", ko) and hn <= 3:
        return True
    if re.match(r"^[！？]", ko):
        return True
    # Bing meta / refusal / machine-translation scaffolding.
    if _BING_META_RE.search(plain):
        return True
    if "해당" in ko and ("일본어" in ko or "텍스트" in ko):
        return True
    if "문자　그대로" in ko or "문자 그대로" in ko:
        return True
    if "정확한　의미" in ko or "정확한 의미" in ko:
        return True
    # Control / decode tags left in draft KO (fake records, broken expand).
    if "<FF>" in ko.upper() or "<BADDICT" in ko.upper():
        return True
    if ko.count("<") >= 2:
        return True
    # Ellipsis / punct-only with no real word (isolated syllable only).
    # Keep short replies like "……그래。" / "……알겠다。".
    if (
        hn <= 2
        and not re.search(r"[가-힣]{2,}", ko)
        and re.fullmatch(r"[。．…・！？\s　\.]+[가-힣]?[。．…・！？\s　\.]*", ko)
    ):
        return True
    return False
