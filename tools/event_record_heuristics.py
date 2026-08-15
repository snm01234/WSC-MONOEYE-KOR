#!/usr/bin/env python3
"""Heuristics for sheet abs that are event/control bytes, not dialogue."""


def _looks_like_paired_02_template(body: bytes) -> bool:
    """True for short binary (xx 02)+ … 42? trails misread as '…ろ…' dialogue."""
    if not (6 <= len(body) <= 12) or body.count(0x02) < 2:
        return False
    i = 0
    pairs = 0
    while i + 1 < len(body) and body[i + 1] == 0x02:
        pairs += 1
        i += 2
    if pairs < 2:
        return False
    rest = body[i:]
    if not rest:
        return True
    if rest[0] == 0x42 and len(rest) <= 3:
        return True
    return len(rest) <= 3 and rest.endswith((b"\x02", b"\x03", b"\x08"))


def looks_like_event_body(body: bytes) -> bool:
    if not body:
        return True
    # Classic false cluster around 65:CB0F (error 51983 = 0xCB0F).
    if body.startswith(b"\x01\x0C\x01\xDB") or body.startswith(b"\x01\x0C\x01"):
        return True
    # Bank 69 control stream mislabeled as "…機な/を/は" dialogue.
    # Pattern: 02 80 xx (3-byte event/param), often clustered near 69:0A0D
    # (error param 2573 = 0x0A0D).
    if body.startswith(b"\x02\x80") and len(body) <= 4:
        return True
    # Short (xx 02)+ templates sheet OCR turns into "ル…よ…戦ろ…" garbage.
    if _looks_like_paired_02_template(body):
        return True
    # Short "了/中継/かし" mis-decodes: DF/E7/F4 templates with 01 xx control.
    if len(body) <= 8 and body[:1] in (b"\xDF", b"\xE7", b"\xF4") and 0x01 in body[1:3]:
        return True
    if len(body) <= 8 and body.startswith((b"\x03\x08\x01", b"\x03\x08\x14", b"\x28\x01")):
        return True
    ones = body.count(0x01)
    textish = sum(1 for b in body if b >= 0x80 or 0xF0 <= b <= 0xFF)
    if len(body) <= 12 and ones >= 2 and textish <= max(1, len(body) // 4):
        return True
    if len(body) <= 10 and ones >= 3:
        return True
    return False
