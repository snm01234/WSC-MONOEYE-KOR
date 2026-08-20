#!/usr/bin/env python3
"""Single runtime dialogue contract for extraction, builders, and audits.

The old pipeline decided whether the first code unit was visible text or
speaker/control metadata several different ways.  This module is the only
place allowed to make that decision.  Generic byte parsing is intentionally
conservative: a role is authoritative only when it is backed by a caller/
screen ledger, an explicit record grammar, or a maintained regression anchor.
Everything else is emitted as ``quarantine`` and may not be auto-written.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import _walk_zstring_range, payload_has_hangul_marker  # noqa: E402
from mixed_residual_classification import is_japanese_character  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    find_rom,
    is_compact3_magic,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    dict_index_from_ext3_token,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)

DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_MANIFEST = ROOT / "out/script/dialogue_runtime_contracts.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
JP_TBL_PATH = ROOT / "data/monoeye_verified.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
SAFE_LEADS = ROOT / "out/script/battle_dialogue_false_lead_safe_targets.csv"
AMBIGUOUS_LEADS = ROOT / "out/script/battle_dialogue_false_lead_ambiguous.csv"
DUPLICATE_LEADS = ROOT / "out/script/battle_dialogue_duplicate_lead_stock_rehome_targets.csv"
QUALITY_SOURCE = ROOT / "out/script/translations_quality_all.json"
SCENARIO_SUPPLEMENT = ROOT / "data/scenario_runtime_contract_supplement.json"
BANK5F_SPEC = ROOT / "data/bank5f_runtime_battle_voice_ko.json"

LINE_LIMIT = 20
ROM_SIZE = 16_777_216

# Highest-priority runtime evidence.  These rows were observed as visible text
# even though an older structure ledger called the same byte metadata.
# The exact pair below was observed rendering its E5 18 Korean tail correctly
# while also exposing the stale 86=全 lead.  That runtime capture proves ext3
# decoder support for these two body-only routes, not merely their lead role.
RUNTIME_VISIBLE_EXT3_PROVEN = {0x5DC23D, 0x5E9885}

RUNTIME_VISIBLE_OVERRIDES: dict[int, bytes] = {
    0x5D5982: bytes.fromhex("82"),
    0x5D5B1F: bytes.fromhex("82"),
    # Screen-proven 全 false-lead family.  These records must be treated as
    # body-only even if the historical first-code-unit ledger is unavailable.
    0x5DC23D: bytes.fromhex("86"),
    0x5E4F43: bytes.fromhex("86"),
    0x5E9885: bytes.fromhex("86"),
    0x5EB3AA: bytes.fromhex("82"),
    0x5EAB36: bytes.fromhex("AD"),
    0x5EB6B2: bytes.fromhex("AD"),
    0x5EC27C: bytes.fromhex("AD"),
}

# Opposite-direction anchors: these bytes are real speaker/portrait metadata.
EXPLICIT_METADATA_ANCHORS: dict[int, bytes] = {
    0x5D7084: bytes.fromhex("35"),
    0x5E9BDE: bytes.fromhex("8F"),
    0x5E9CC4: bytes.fromhex("8F"),
}

SCREEN_VOICE_PREFIXES: dict[int, bytes] = {
    0x5D014E: bytes.fromhex("02F191"),
    0x5D0211: bytes.fromhex("02F191"),
    0x5D03ED: bytes.fromhex("02F191"),
}

# Visible-lead rows whose current special-route storage is enforced now.  The
# remaining safe-lead rows keep their body-only role as a permanent anchor but
# stay quarantined until their exact caller proves ext3 support or receives a
# native data-path rehome.
ACTIVE_VISIBLE_ANCHORS = {
    0x5D01F4,
    0x5D0C39,
    0x5D11C6,
    0x5D1449,
    0x5D5D58,
    0x5EBB7A,
    *RUNTIME_VISIBLE_OVERRIDES.keys(),
}

ACTIVE_ID_CONTINUATIONS = {0x5C9794, 0x5C97C0}
SEMANTIC_SCENARIO_CONTINUATIONS = {
    0x6088B3,
    0x60EA3A,
    0x612229,
    0x61C463,
    0x61C506,
}
# User-runtime-proven continuation records where a leading 0x18 is a parser/control
# prefix, not visible `こ`, and the following body must stay on native stock-token
# grammar.  Direct E5 18 portals at these anchors leak/corrupt the following row.
SCENARIO_CONTINUATION_CONTROL18_NATIVE_ONLY = {0x63449B, 0x635855, 0x635BFB}
# Runtime-proven native-only continuation records that have no leading 0x18.
# STAGE21t Doctor J's `그건 아니지만。` follow-up used a mixed Hangul-marker
# stock wrapper and corrupted at runtime; preserve ordinary stock-token +
# punctuation grammar at both duplicated scenario copies.
SCENARIO_CONTINUATION_NATIVE_ONLY = {0x635866, 0x635C0C}
# User screenshots prove these structural-18 continuations execute through the
# promoted E504 portal16 path.  Their private helper phrases may be retargeted
# without changing the scenario record or its page/control boundary.
SCENARIO_CONTINUATION_PORTAL16_RUNTIME = {0x62B839, 0x62BAAE}
ACTIVE_SCENARIO_CONTINUATIONS = (
    {0x61E23D, 0x626509}
    | SEMANTIC_SCENARIO_CONTINUATIONS
    | SCENARIO_CONTINUATION_CONTROL18_NATIVE_ONLY
    | SCENARIO_CONTINUATION_NATIVE_ONLY
    | SCENARIO_CONTINUATION_PORTAL16_RUNTIME
)
SCENARIO_CONTINUATION_EXT3_PROVEN = (
    {0x626509} | SEMANTIC_SCENARIO_CONTINUATIONS | SCENARIO_CONTINUATION_PORTAL16_RUNTIME
)
# User-runtime-proven scenario-first records whose parser state depends on the
# promoted native token grammar.  A generic scenario-first E5 18 portal is not
# safe here even though the surrounding 17 xx 18 first-line grammar is valid.
#
# 61E234: native two-token predecessor is required for the following page group.
# 623DC6: STAGE16t Bright first line leaked the following 17 28 01 06 control row.
# 623DD7: STAGE16t citizen first line left the event state able to replay the dialogue block.
# 624271: STAGE16t Scirocco short line leaked text and corrupted the following Katejina portrait state.
# 62663E: E5 18 conversion reintroduces bogus follow line `がけはう`.
# 627FB5: E5 18 leaf was previously observed to leak follow/control text.
# 638CD5 is runtime-proven on the event-safe 2-byte E51D native-loop portal.
# It is classified here with native-only first-line routes because direct E5 18
# is forbidden for this caller; the special portal still enters the ordinary
# native dictionary phrase loop and preserves the original 4-byte body extent.
EVENT_SAFE_PORTAL_ANCHORS = {0x638CD5}
# Promoted STAGE22t event-safe two-byte portal. E51D had zero semantic
# ownership across script/aux/name75/native-dictionary/ext3 phrases before the
# portal was installed. Runtime maps it to expansion bank26:2000 whose payload
# is nested-native-only F36A F16E = `어？`.
EVENT_SAFE_NATIVE2_MAGIC = bytes.fromhex("E51D")
EVENT_SAFE_NATIVE2_HELPER = bytes.fromhex("F36AF16E")
# Whole-game exact4 rehome candidates extend the proven two-byte portal with a
# four-byte parameter form: E5 1D <helper_id> 01.  The helper id is resolved by
# a bank26 pointer table; id 0 remains the promoted fixed `어？` helper.
EVENT_SAFE_NATIVE2_PARAM_SEG = 0x26
EVENT_SAFE_NATIVE2_PARAM_PTR_TABLE = 0x2100
EVENT_SAFE_NATIVE2_PARAM_DATA_MIN = 0x2200
EVENT_SAFE_NATIVE2_PARAM_DATA_MAX = 0x2600
# User-runtime representative anchor for the promoted parameterized form.
# 61035E is the first `가토오오오！！` record in the user-confirmed Gato scene.
EVENT_SAFE_NATIVE2_PARAM_RUNTIME_ANCHORS = {0x61035E}
# Scalable continuation-safe probe namespace. E504 is semantic-zero across
# script/aux/name75/native-dictionary/ext3 phrase ownership on the promoted
# parent main. The four-byte form E5 04 <lo+1> <hi+1> carries a base-255
# nonzero 16-bit helper index so no NUL can truncate the zstring. Helpers are
# fixed-stride five-byte records in expansion bank27: E5 18 xx yy 00.
CONTROL18_PORTAL16_MAGIC = bytes.fromhex("E504")
CONTROL18_PORTAL16_SEG = 0x27
CONTROL18_PORTAL16_HELPER_BASE = 0x2000
CONTROL18_PORTAL16_HELPER_STRIDE = 5
SCENARIO_FIRST_NATIVE_ONLY = {0x61E234, 0x623DC6, 0x623DD7, 0x624271, 0x62663E, 0x627FB5, 0x63463A} | EVENT_SAFE_PORTAL_ANCHORS
ENFORCED_REPAIR_ADDRESSES = (
    ACTIVE_VISIBLE_ANCHORS
    | ACTIVE_ID_CONTINUATIONS
    | ACTIVE_SCENARIO_CONTINUATIONS
    | EVENT_SAFE_PORTAL_ANCHORS
    | set(EXPLICIT_METADATA_ANCHORS)
)


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrefixDecision:
    prefix: bytes
    body: bytes
    kind: str
    reason: str


@dataclass(frozen=True)
class VoiceDecision:
    role: str
    route: str
    prefix: bytes
    status: str
    evidence: str
    confidence: str
    ext3_supported: bool
    width_enforced: bool
    conflict: str = ""


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def structural_prefix(
    payload: bytes,
    *,
    role: str = "unknown",
    explicit_prefix: bytes | None = None,
) -> PrefixDecision:
    """Split only grammar-proven controls from a record body.

    In particular, a head ``18`` is visible text for continuation/body-only
    roles.  It is consumed only after a parsed ``08``/``17`` chain or when an
    exact route contract supplies ``explicit_prefix``.
    """
    if explicit_prefix is not None:
        if not payload.startswith(explicit_prefix):
            raise ContractError(
                f"required prefix {explicit_prefix.hex().upper()} missing from "
                f"{payload[:len(explicit_prefix)].hex().upper()}"
            )
        return PrefixDecision(
            explicit_prefix,
            payload[len(explicit_prefix):],
            "dialogue",
            "route-specific exact prefix",
        )
    if not payload:
        return PrefixDecision(b"", b"", "other", "empty payload")

    i = 0
    prefix = bytearray()
    saw_control = False
    while i + 1 < len(payload) and payload[i] == 0x08:
        prefix.extend(payload[i:i + 2])
        i += 2
        saw_control = True
    if i < len(payload) and payload[i] == 0x01:
        if i + 1 < len(payload) and payload[i + 1] == 0x17:
            prefix.append(0x01)
            i += 1
            saw_control = True
    while i + 1 < len(payload) and payload[i] == 0x17:
        prefix.extend(payload[i:i + 2])
        i += 2
        saw_control = True
        if i + 1 < len(payload) and payload[i] == 0x08:
            prefix.extend(payload[i:i + 2])
            i += 2
    if saw_control and i < len(payload) and payload[i] == 0x18:
        prefix.append(0x18)
        i += 1
        return PrefixDecision(bytes(prefix), payload[i:], "dialogue", "control chain followed by 18")
    if prefix:
        return PrefixDecision(bytes(prefix), payload[i:], "control", "control chain without dialogue marker")
    return PrefixDecision(b"", payload, "dialogue", f"{role or 'unknown'} body starts at byte 0")


def _csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@lru_cache(maxsize=1)
def load_voice_evidence() -> dict[str, Any]:
    safe_rows = _csv_rows(SAFE_LEADS)
    ambiguous_rows = _csv_rows(AMBIGUOUS_LEADS)
    duplicate_rows = _csv_rows(DUPLICATE_LEADS)
    safe = {int(row["abs"], 16): row for row in safe_rows if row.get("abs")}
    protected = {
        int(row["abs"], 16): row
        for row in ambiguous_rows
        if row.get("abs") and row.get("final_disposition") == "protected_control"
    }
    duplicates = {
        int(row["abs"], 16): row for row in duplicate_rows if row.get("abs")
    }
    contract = {
        "safe": safe,
        "protected": protected,
        "duplicates": duplicates,
        "counts": {
            "safe_visible": len(safe),
            "protected_metadata": len(protected),
            "duplicate_visible": len(duplicates),
        },
    }
    return contract


def voice_decision(payload: bytes, logical: int, evidence: dict[str, Any] | None = None) -> VoiceDecision:
    ev = evidence or load_voice_evidence()
    safe = ev["safe"]
    protected = ev["protected"]

    if logical in RUNTIME_VISIBLE_OVERRIDES:
        stale = protected.get(logical)
        conflict = ""
        if stale:
            conflict = "runtime-visible evidence overrides stale protected-control ledger"
        return VoiceDecision(
            "continuation", "battle_body_only", b"", "active",
            "runtime screen/raw-byte visible override", "runtime-proven",
            logical in RUNTIME_VISIBLE_EXT3_PROVEN, True, conflict,
        )
    if logical in safe:
        active = logical in ACTIVE_VISIBLE_ANCHORS
        return VoiceDecision(
            "continuation", "battle_body_only", b"", "active" if active else "quarantine",
            str(safe[logical].get("evidence") or "independent visible-lead ledger"),
            "runtime-proven" if safe[logical].get("screen_anchor") else "high",
            False,
            active,
            "" if active else "role resolved; decoder/storage route still unproven",
        )
    if logical in EXPLICIT_METADATA_ANCHORS:
        return VoiceDecision(
            "first", "battle_tagged", EXPLICIT_METADATA_ANCHORS[logical], "active",
            "explicit opposite-direction portrait metadata anchor", "runtime-proven", True, False,
        )
    if logical in SCREEN_VOICE_PREFIXES:
        return VoiceDecision(
            "first", "battle_tagged", SCREEN_VOICE_PREFIXES[logical], "active",
            "screen-proven exact battle prefix", "runtime-proven", True, True,
        )
    if logical in protected:
        prefix = bytes.fromhex(str(protected[logical].get("lead_hex") or ""))
        return VoiceDecision(
            "first", "battle_tagged", prefix, "active",
            str(protected[logical].get("evidence") or "protected-control ledger"), "high", True, False,
        )
    return VoiceDecision(
        "unknown", "battle_unknown", b"", "quarantine",
        "no caller/screen-independent role proof", "unresolved", False, False,
        "metadata/text role unresolved",
    )


def voice_prefix(payload: bytes, logical: int) -> tuple[bytes, str]:
    """Compatibility helper used by the residual-family analyzer."""
    decision = voice_decision(payload, logical)
    if decision.prefix and not payload.startswith(decision.prefix):
        # Do not silently strip a missing prefix.  Keeping byte 0 visible makes
        # the drift observable to the contract audit.
        return b"", f"{decision.evidence}; required prefix missing"
    return decision.prefix, decision.evidence


def read_record(rom: bytes, logical: int, max_len: int = 256) -> tuple[bytes, int] | None:
    base = stock_base(rom)
    got = read_encoded_z_safe(rom, base + logical, max_len=max_len)
    if got is None:
        return None
    return bytes(got[0]), int(got[1]) - base


def _stock_unit_kinds(body: bytes) -> list[str]:
    """Parse the native stock code-unit grammar needed for page-state guards."""
    kinds: list[str] = []
    i = 0
    while i < len(body):
        value = body[i]
        if 0xF0 <= value <= 0xFF and i + 1 < len(body):
            kinds.append("dict")
            i += 2
        elif 0xE0 <= value <= 0xE7 and i + 1 < len(body):
            kinds.append("glyph2")
            i += 2
        else:
            kinds.append("char1")
            i += 1
    return kinds


def _exact_native_two_dict(body: bytes) -> bool:
    return len(body) == 4 and _stock_unit_kinds(body) == ["dict", "dict"]


def _native_two_dict_with_padding(body: bytes) -> bool:
    return len(body) >= 4 and _exact_native_two_dict(body[:4]) and all(value == 0x01 for value in body[4:])


def boundary_signature(rom: bytes, terminator: int, limit: int = 8) -> dict[str, Any]:
    base = stock_base(rom)
    pos = terminator
    nuls = 0
    while base + pos < len(rom) and nuls < limit and rom[base + pos] == 0:
        nuls += 1
        pos += 1
    lead = rom[base + pos] if base + pos < len(rom) else None
    control = ""
    if lead in {0x08, 0x17} and base + pos + 1 < len(rom):
        control = bytes(rom[base + pos:base + pos + 2]).hex().upper()
    contract = {
        "nul_run": nuls,
        "next_address": f"{pos:06X}",
        "next_lead": "" if lead is None else f"{lead:02X}",
        "next_control": control,
    }
    return contract


def scan_portals(payload: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(payload):
        lead = payload[i]
        if i + 1 < len(payload) and payload[i:i + 2] == EVENT_SAFE_NATIVE2_MAGIC:
            param = (
                i + 3 < len(payload)
                and payload[i + 2] != 0
                and payload[i + 3] == 0x01
            )
            take = 4 if param else 2
            out.append({
                "kind": "event_safe_native2_param" if param else "event_safe_native2",
                "offset": i,
                "raw": payload[i:i + take].hex().upper(),
            })
            i += take
            continue
        if i + 3 < len(payload) and payload[i:i + 2] == CONTROL18_PORTAL16_MAGIC:
            lo, hi = payload[i + 2], payload[i + 3]
            valid = lo != 0 and hi != 0
            index = ((hi - 1) * 255 + (lo - 1)) if valid else None
            out.append({
                "kind": "control18_portal16" if valid else "invalid_control18_portal16",
                "offset": i,
                "raw": payload[i:i + 4].hex().upper(),
                "helper_index": index,
            })
            i += 4
            continue
        if i + 1 < len(payload) and is_ext3_magic(lead, payload[i + 1]):
            raw = payload[i:i + 4]
            out.append({
                "kind": "ext3" if len(raw) == 4 else "truncated_ext3",
                "offset": i,
                "raw": raw.hex().upper(),
                "embedded_nul": len(raw) < 4 or 0 in raw[2:4],
            })
            i += max(1, len(raw))
            continue
        if i + 1 < len(payload) and is_compact3_magic(lead, payload[i + 1]):
            raw = payload[i:i + 3]
            out.append({"kind": "compact3", "offset": i, "raw": raw.hex().upper()})
            i += max(1, len(raw))
            continue
        if is_dict_token(lead) or is_kanji_lead(lead):
            i += 2 if i + 1 < len(payload) else 1
        else:
            i += 1
    return out


def split_lines(text: str) -> list[str]:
    return text.split("<E62F>")


def physical_widths(text: str) -> list[int]:
    return [len(line) for line in split_lines(text)]


def semantic_widths(text: str) -> list[int]:
    return [len(line.rstrip("\u3000 \t")) for line in split_lines(text)]


def has_japanese(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in text)


def _event_safe_native2_helper(dictionary: Any, index: int) -> bytes:
    """Resolve a promoted/candidate E51D helper payload.

    Index 0 is the promoted STAGE22t fixed helper.  Non-zero indices exist only
    on the whole-game exact4 candidate family and are stored in expansion bank26.
    """
    if index == 0:
        return EVENT_SAFE_NATIVE2_HELPER
    rom = dictionary.rom
    base = EVENT_SAFE_NATIVE2_PARAM_SEG << 16
    ptr_at = base + EVENT_SAFE_NATIVE2_PARAM_PTR_TABLE + index * 2
    if ptr_at + 1 >= len(rom):
        raise ContractError(f"E51D helper pointer out of range: {index}")
    off = int(rom[ptr_at]) | (int(rom[ptr_at + 1]) << 8)
    if not EVENT_SAFE_NATIVE2_PARAM_DATA_MIN <= off < EVENT_SAFE_NATIVE2_PARAM_DATA_MAX:
        raise ContractError(f"E51D helper pointer invalid: {index} -> {off:04X}")
    got = read_encoded_z_safe(rom, base + off, max_len=256)
    if got is None:
        raise ContractError(f"E51D helper unterminated: {index} -> {off:04X}")
    return bytes(got[0])


def _control18_portal16_helper(dictionary: Any, lo: int, hi: int) -> bytes:
    if lo == 0 or hi == 0:
        raise ContractError(f"E504 portal16 contains NUL digit: {lo:02X} {hi:02X}")
    index = (hi - 1) * 255 + (lo - 1)
    base = CONTROL18_PORTAL16_SEG << 16
    off = CONTROL18_PORTAL16_HELPER_BASE + index * CONTROL18_PORTAL16_HELPER_STRIDE
    if off < CONTROL18_PORTAL16_HELPER_BASE or off + CONTROL18_PORTAL16_HELPER_STRIDE > 0x10000:
        raise ContractError(f"E504 portal16 helper out of range: {index} -> {off:04X}")
    got = read_encoded_z_safe(dictionary.rom, base + off, max_len=16)
    if got is None:
        raise ContractError(f"E504 portal16 helper unterminated: {index} -> {off:04X}")
    payload = bytes(got[0])
    if len(payload) != 4 or not payload.startswith(b"\xE5\x18"):
        raise ContractError(f"E504 portal16 helper malformed: {index} -> {payload.hex().upper()}")
    return payload


def _is_event_safe_native2_param(payload: bytes) -> bool:
    return (
        len(payload) >= 4
        and payload[:2] == EVENT_SAFE_NATIVE2_MAGIC
        and payload[2] != 0
        and payload[3] == 0x01
    )


def _expand_event_safe_native2(payload: bytes, dictionary: Any | None = None) -> bytes:
    """Apply E51D runtime semantics without touching Original decode.

    The walk is encoded-unit aware, so E51D occurring as bytes inside an E518
    four-byte token cannot be mistaken for the special portal.  Parameterized
    E51D consumes exactly four bytes, matching the candidate runtime walker.
    """
    out = bytearray()
    i = 0
    while i < len(payload):
        lead = payload[i]
        if i + 1 < len(payload) and payload[i:i + 2] == EVENT_SAFE_NATIVE2_MAGIC:
            if (
                dictionary is not None
                and i + 3 < len(payload)
                and payload[i + 2] != 0
                and payload[i + 3] == 0x01
            ):
                out += _event_safe_native2_helper(dictionary, payload[i + 2])
                i += 4
            else:
                out += EVENT_SAFE_NATIVE2_HELPER
                i += 2
            continue
        if i + 3 < len(payload) and payload[i:i + 2] == CONTROL18_PORTAL16_MAGIC:
            if dictionary is None:
                out += payload[i:i + 4]
            else:
                out += _control18_portal16_helper(dictionary, payload[i + 2], payload[i + 3])
            i += 4
            continue
        if i + 1 < len(payload) and is_ext3_magic(lead, payload[i + 1]):
            take = min(4, len(payload) - i)
            out += payload[i:i + take]
            i += take
            continue
        if i + 1 < len(payload) and is_compact3_magic(lead, payload[i + 1]):
            take = min(3, len(payload) - i)
            out += payload[i:i + take]
            i += take
            continue
        if is_dict_token(lead) or is_kanji_lead(lead):
            take = 2 if i + 1 < len(payload) else 1
            out += payload[i:i + take]
            i += take
            continue
        out.append(lead)
        i += 1
    return bytes(out)


def _decode(dictionary: Any, payload: bytes, tbl: Tbl, *, target: bool = False) -> str:
    if target:
        payload = _expand_event_safe_native2(payload, dictionary)
    return dictionary.expand(payload, tbl)


def _record_contract(
    *,
    original: bytes,
    target: bytes,
    logical: int,
    family: str,
    bundle_id: str,
    line_role: str,
    route: str,
    status: str,
    evidence: str,
    confidence: str,
    source_prefix: bytes,
    target_prefix: bytes,
    ext3_supported: bool,
    width_enforced: bool,
    jp_dictionary: Dictionary,
    dictionary: Any,
    jp_tbl: Tbl,
    tbl: Tbl,
    conflict: str = "",
    catalog_jp: str = "",
) -> dict[str, Any]:
    source = read_record(original, logical)
    current = read_record(target, logical)
    if source is None or current is None:
        raise ContractError(f"unreadable contract record {logical:06X}")
    source_payload, source_term = source
    current_payload, current_term = current
    source_offset = len(source_prefix) if source_payload.startswith(source_prefix) else 0
    current_offset = len(target_prefix) if current_payload.startswith(target_prefix) else 0
    source_body = source_payload[source_offset:]
    current_body = current_payload[current_offset:]
    try:
        original_text = _decode(jp_dictionary, source_body, jp_tbl).rstrip("\u3000 \t")
    except Exception as exc:  # noqa: BLE001
        original_text = f"<decode-error:{type(exc).__name__}>"
    try:
        current_text = _decode(dictionary, current_body, tbl, target=True)
    except Exception as exc:  # noqa: BLE001
        current_text = f"<decode-error:{type(exc).__name__}>"
    contract = {
        "bundle_id": bundle_id,
        "address": f"{logical:06X}",
        "address_int": logical,
        "family": family,
        "line_role": line_role,
        "route": route,
        "status": status,
        "confidence": confidence,
        "evidence": evidence,
        "conflict": conflict,
        "metadata_hex": target_prefix.hex().upper() if route == "battle_tagged" else "",
        "control_prefix_hex": target_prefix.hex().upper() if route != "battle_tagged" else "",
        "source_prefix_hex": source_prefix.hex().upper(),
        "body_start": f"{logical + current_offset:06X}",
        "body_end_exclusive": f"{logical + len(current_payload):06X}",
        "record_extent": len(source_payload),
        "body_capacity": len(source_payload) - source_offset,
        "source_payload_hex": source_payload.hex().upper(),
        "baseline_payload_hex": current_payload.hex().upper(),
        "source_body_hex": source_body.hex().upper(),
        "baseline_body_hex": current_body.hex().upper(),
        "source_terminator": f"{source_term:06X}",
        "baseline_terminator": f"{current_term:06X}",
        "terminator_hex": "00",
        "source_boundary": boundary_signature(original, source_term),
        "baseline_boundary": boundary_signature(target, current_term),
        "decoder": {
            "native_stock": True,
            "ext3": ext3_supported,
            "compact3": False,
        },
        "line_limit": LINE_LIMIT,
        "width_enforced": width_enforced,
        "original_japanese": original_text,
        "catalog_japanese": catalog_jp,
        "baseline_text": current_text.rstrip("\u3000 \t"),
        "baseline_physical_cells": physical_widths(current_text),
        "baseline_semantic_cells": semantic_widths(current_text),
        "baseline_portals": scan_portals(current_body),
        "baseline_direct_hangul_marker": payload_has_hangul_marker(current_body),
    }
    # Existing unresolved content/storage defects are represented explicitly as
    # quarantine instead of being accepted as a permanent failure baseline.
    # The small screen/anchor population above remains active so the candidate
    # builder must actually repair it.
    if status == "active" and logical not in ENFORCED_REPAIR_ADDRESSES:
        portals = contract["baseline_portals"]
        semantic = contract["baseline_semantic_cells"]
        physical = contract["baseline_physical_cells"]
        reason = ""
        if any(item.get("kind") == "compact3" for item in portals):
            reason = "baseline compact3 requires a native route rewrite"
        elif has_japanese(current_text):
            reason = "baseline Japanese/placeholder body requires source-grounded review"
        elif any(int(value) > LINE_LIMIT for value in semantic):
            reason = "baseline semantic width exceeds 20 cells"
        elif width_enforced and any(int(value) > LINE_LIMIT for value in physical):
            reason = "baseline physical padding exceeds 20 cells; filler route unmeasured"
        if reason:
            contract["status"] = "quarantine"
            contract["conflict"] = "; ".join(value for value in (conflict, reason) if value)
    return contract


def _quality_rows() -> dict[int, dict[str, Any]]:
    if not QUALITY_SOURCE.is_file():
        return {}
    doc = json.loads(QUALITY_SOURCE.read_text(encoding="utf-8"))
    out: dict[int, dict[str, Any]] = {}
    for row in doc.get("lines") or []:
        address = str(row.get("abs") or "").upper()
        if row.get("kind") != "dialogue" or address[:2] not in {"60", "61", "62", "63"}:
            continue
        try:
            out[int(address, 16)] = row
        except ValueError:
            continue
    return out


def _source_proves_continuation_control18(
    *,
    logical: int,
    source_payload: bytes,
    quality: dict[int, dict[str, Any]],
    jp_dictionary: Dictionary,
    jp_tbl: Tbl,
    predecessor_nul_run: int,
) -> bool:
    """Return True when Original provenance proves a leading 0x18 is structural.

    The authoritative translation/extraction row stores the Japanese body
    without a continuation prefix.  If Original starts with 0x18 and decoding
    Original after that byte reproduces the catalog Japanese exactly, 0x18 is
    not visible `こ`; it is the continuation marker.  This is stronger than a
    caller-only heuristic and preserves genuine text-initial `こ` rows because
    those fail the body-equality test.
    """
    if not source_payload.startswith(b"\x18"):
        return False
    # Runtime/original evidence now shows a hard page-boundary distinction:
    # single-NUL predecessor => the leading 0x18 is the visible Japanese glyph `こ`;
    # double-NUL predecessor => 0x18 is the structural continuation/page-head prefix.
    if predecessor_nul_run < 2:
        return False
    catalog = str((quality.get(logical) or {}).get("jp") or "").rstrip("\u3000 \t")
    if not catalog:
        return False
    try:
        decoded = _decode(jp_dictionary, source_payload[1:], jp_tbl).rstrip("\u3000 \t")
    except Exception:  # noqa: BLE001
        return False
    return decoded == catalog


def _scenario_contracts(
    original: bytes,
    target: bytes,
    jp_dictionary: Dictionary,
    dictionary: Any,
    jp_tbl: Tbl,
    tbl: Tbl,
) -> list[dict[str, Any]]:
    quality = _quality_rows()
    addresses = sorted(quality)
    address_set = set(addresses)
    assigned: set[int] = set()
    rows: list[dict[str, Any]] = []
    base = stock_base(original)

    for first in addresses:
        if first in assigned:
            continue
        source = read_record(original, first)
        if source is None:
            continue
        payload, term = source
        decision = structural_prefix(payload, role="scenario")
        if not decision.prefix or 0x17 not in decision.prefix:
            continue
        chain = [first]
        current_term = term
        for _ in range(7):
            pos = current_term + 1
            while pos < current_term + 4 and original[base + pos] == 0:
                pos += 1
            if pos not in address_set or pos in assigned:
                break
            following = read_record(original, pos)
            if following is None:
                break
            follow_payload, follow_term = following
            follow_decision = structural_prefix(follow_payload, role="scenario_continuation")
            if follow_decision.prefix:
                break
            chain.append(pos)
            current_term = follow_term
        bundle_id = f"scenario_{first:06X}"
        bundle_jp = "".join(str(quality[a].get("jp") or "") for a in chain)
        for index, logical in enumerate(chain):
            assigned.add(logical)
            source_record = read_record(original, logical)
            target_record = read_record(target, logical)
            if source_record is None or target_record is None:
                raise ContractError(f"scenario record vanished at {logical:06X}")
            source_payload, _source_term = source_record
            target_payload, target_term = target_record
            control18_native = (
                index > 0 and logical in SCENARIO_CONTINUATION_CONTROL18_NATIVE_ONLY
            )
            predecessor_nul_run = 0
            if index > 0:
                previous_record = read_record(original, chain[index - 1])
                if previous_record is not None:
                    predecessor_nul_run = int(boundary_signature(original, previous_record[1]).get("nul_run") or 0)
            source_control18_proven = (
                index > 0
                and _source_proves_continuation_control18(
                    logical=logical,
                    source_payload=source_payload,
                    quality=quality,
                    jp_dictionary=jp_dictionary,
                    jp_tbl=jp_tbl,
                    predecessor_nul_run=predecessor_nul_run,
                )
            )
            source_visible_ko_proven = (
                index > 0
                and source_payload.startswith(b"\x18")
                and predecessor_nul_run == 1
            )
            src_decision = structural_prefix(
                source_payload,
                role="scenario_first" if index == 0 else "scenario_continuation",
                explicit_prefix=b"\x18" if control18_native or source_control18_proven else None,
            )
            # A repaired target may intentionally remove the physical 0x18
            # while preserving the source semantic contract.  Consume it from
            # the target only when it is still physically present.
            target_keeps_source_prefix = (
                bool(src_decision.prefix)
                and target_payload.startswith(src_decision.prefix)
            )
            target_decision = structural_prefix(
                target_payload,
                role="scenario_first" if index == 0 else "scenario_continuation",
                explicit_prefix=(
                    src_decision.prefix
                    if index == 0 or control18_native or (source_control18_proven and target_keeps_source_prefix)
                    else None
                ),
            )
            if index == 0:
                double_nul_native_iteration_guard = (
                    boundary_signature(target, target_term)["nul_run"] >= 2
                    and _exact_native_two_dict(src_decision.body)
                    and _native_two_dict_with_padding(target_decision.body)
                )
                event_safe_param = _is_event_safe_native2_param(target_decision.body)
                native_only = (
                    logical in SCENARIO_FIRST_NATIVE_ONLY
                    or double_nul_native_iteration_guard
                    or event_safe_param
                )
                status = "active"
                confidence = (
                    "runtime-proven"
                    if event_safe_param and logical in EVENT_SAFE_NATIVE2_PARAM_RUNTIME_ANCHORS
                    else "promoted-static"
                    if event_safe_param
                    else "runtime-proven" if native_only else "explicit-grammar"
                )
                evidence = (
                    "user-runtime-proven parameterized E51D event-safe native-loop portal (Gato representative anchor)"
                    if event_safe_param and logical in EVENT_SAFE_NATIVE2_PARAM_RUNTIME_ANCHORS
                    else "promoted parameterized E51D event-safe native-loop portal; representative 61035E runtime gate PASS"
                    if event_safe_param
                    else "double-NUL page boundary requires promoted native two-token iteration grammar"
                    if double_nul_native_iteration_guard
                    else (
                        "user-runtime-proven native-only scenario-first grammar"
                        if native_only
                        else "17 xx 18 first-line grammar and Original boundary"
                    )
                )
                route = "scenario_first"
                ext3 = not native_only
                width = False
            else:
                explicit = logical in ACTIVE_SCENARIO_CONTINUATIONS
                direct_ext3_after_control18 = (
                    source_control18_proven
                    and target_keeps_source_prefix
                    and target_decision.body.startswith(b"\xE5\x18")
                )
                visible_source_ko_leak = (
                    source_visible_ko_proven
                    and target_payload.startswith(b"\x18")
                )
                status = "active" if explicit else "quarantine"
                confidence = (
                    "user-confirmed-semantic"
                    if logical in SEMANTIC_SCENARIO_CONTINUATIONS
                    else "runtime-proven"
                    if logical in SCENARIO_CONTINUATION_CONTROL18_NATIVE_ONLY
                    else "runtime-proven" if explicit
                    else "source-proven" if source_control18_proven
                    else "unresolved"
                )
                evidence = (
                    "user-confirmed visible Japanese false lead; original semantic boundary proves text-initial こ"
                    if logical in SEMANTIC_SCENARIO_CONTINUATIONS
                    else "user-runtime-proven control-18 continuation; native stock grammar required"
                    if logical in SCENARIO_CONTINUATION_CONTROL18_NATIVE_ONLY
                    else "user-runtime-proven E504 portal16 continuation with private helper storage"
                    if logical in SCENARIO_CONTINUATION_PORTAL16_RUNTIME
                    else "explicit continuation regression anchor"
                    if explicit
                    else "Original payload + catalog Japanese prove leading 18 is a structural continuation prefix"
                    if source_control18_proven
                    else "physical adjacency proves continuation; byte-18 text/control meaning awaits caller trace"
                )
                route = "scenario_continuation"
                ext3 = logical in SCENARIO_CONTINUATION_EXT3_PROVEN
                width = explicit and logical not in SCENARIO_CONTINUATION_PORTAL16_RUNTIME
                conflict = (
                    "source-proven structural 18 prefix followed by current direct E518 storage; runtime-safe rehome required"
                    if direct_ext3_after_control18 and not explicit
                    else "source-visible Japanese こ byte remains physically present in translated continuation"
                    if visible_source_ko_leak
                    else ""
                )
            contract = _record_contract(
                original=original,
                target=target,
                logical=logical,
                family="scenario_bundle",
                bundle_id=bundle_id,
                line_role="first" if index == 0 else "continuation",
                route=route,
                status=status,
                evidence=evidence,
                confidence=confidence,
                source_prefix=src_decision.prefix,
                target_prefix=target_decision.prefix,
                ext3_supported=ext3,
                width_enforced=width,
                jp_dictionary=jp_dictionary,
                dictionary=dictionary,
                jp_tbl=jp_tbl,
                tbl=tbl,
                conflict=conflict if index > 0 else "",
                catalog_jp=bundle_jp,
            )
            if index > 0:
                contract["predecessor_source_nul_run"] = int(predecessor_nul_run)
                contract["source_structural_prefix_proven"] = bool(source_control18_proven)
                contract["source_visible_ko_proven"] = bool(source_visible_ko_proven)
                contract["control18_storage_risk"] = bool(direct_ext3_after_control18)
                contract["visible_source_ko_leak_risk"] = bool(visible_source_ko_leak)
                contract["target_physically_keeps_source_prefix"] = bool(target_keeps_source_prefix)
            rows.append(contract)
    return rows


def _scenario_supplement_contracts(
    original: bytes,
    target: bytes,
    jp_dictionary: Dictionary,
    dictionary: Any,
    jp_tbl: Tbl,
    tbl: Tbl,
    scenario_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add only explicit unambiguous direct zstrings missed by QUALITY_SOURCE.

    The supplement never infers a control-leading record.  Each entry must be
    the exact ``source_boundary.next_address`` of one already-built scenario
    contract, must start with no 0x08/0x17/0x18 structural lead in Original,
    and must decode byte-exactly to the ledger Japanese sentence.
    """
    if not SCENARIO_SUPPLEMENT.is_file():
        return []
    doc = json.loads(SCENARIO_SUPPLEMENT.read_text(encoding="utf-8"))
    existing = {int(row["address_int"]): row for row in scenario_rows}
    out: list[dict[str, Any]] = []
    for item in doc.get("records") or []:
        logical = int(str(item["address"]), 16)
        expected_jp = str(item.get("source_jp") or "")
        if logical in existing:
            row = existing[logical]
            if str(row.get("original_japanese") or "") != expected_jp:
                raise ContractError(f"scenario supplement source drifted at {logical:06X}")
            continue

        candidates = scenario_rows + out
        predecessors = [
            row
            for row in candidates
            if str((row.get("source_boundary") or {}).get("next_address") or "").upper()
            == f"{logical:06X}"
        ]
        if len(predecessors) != 1:
            raise ContractError(
                f"scenario supplement predecessor ambiguity at {logical:06X}: {len(predecessors)}"
            )
        predecessor = predecessors[0]
        source = read_record(original, logical)
        current = read_record(target, logical)
        if source is None or current is None:
            raise ContractError(f"scenario supplement record unreadable at {logical:06X}")
        source_payload, _source_term = source
        target_payload, _target_term = current
        if not source_payload or source_payload[0] in {0x08, 0x17, 0x18}:
            raise ContractError(f"scenario supplement became control-leading at {logical:06X}")
        try:
            decoded_jp = _decode(jp_dictionary, source_payload, jp_tbl).rstrip("\u3000 \t")
        except Exception as exc:  # noqa: BLE001
            raise ContractError(f"scenario supplement JP decode failed at {logical:06X}: {exc}") from exc
        if decoded_jp != expected_jp:
            raise ContractError(
                f"scenario supplement JP mismatch at {logical:06X}: {decoded_jp!r} != {expected_jp!r}"
            )
        # This ledger only supplements direct zstrings.  The target may use a
        # normal native/ext3 dictionary token, but no structural prefix is
        # consumed or invented here.
        contract = _record_contract(
            original=original,
            target=target,
            logical=logical,
            family="scenario_bundle",
            bundle_id=str(predecessor.get("bundle_id") or f"scenario_{logical:06X}"),
            line_role="continuation",
            route="scenario_continuation",
            status="quarantine",
            evidence="explicit direct-zstring supplement + predecessor source_boundary.next_address",
            confidence="source-proven",
            source_prefix=b"",
            target_prefix=b"",
            ext3_supported=bool(target_payload.startswith(b"\xE5\x18")),
            width_enforced=False,
            jp_dictionary=jp_dictionary,
            dictionary=dictionary,
            jp_tbl=jp_tbl,
            tbl=tbl,
            catalog_jp=expected_jp,
        )
        contract["supplemental_direct_record"] = True
        out.append(contract)
        existing[logical] = contract
    return out


def build_manifest(original: bytes, target: bytes, *, target_path: Path) -> dict[str, Any]:
    if len(target) != ROM_SIZE:
        raise ContractError(f"target size drifted: {len(target)}")
    tbl = Tbl.load(TBL_PATH)
    jp_tbl = Tbl.load(JP_TBL_PATH)
    jp_dictionary = Dictionary(original)
    dictionary = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    evidence = load_voice_evidence()
    contracts: list[dict[str, Any]] = []

    # ID bundles have an explicit first/continuation grammar.
    from analyze_runtime_text_residual_families import enumerate_id_bundles, enumerate_voice_runs  # noqa: E402

    id_rows, id_bundle_count = enumerate_id_bundles(original, target)
    for row in id_rows:
        logical = int(row["record_start_int"])
        first = row.get("line_role") == "first"
        active = first or logical in ACTIVE_ID_CONTINUATIONS
        prefix = bytes.fromhex(str(row.get("prefix_hex") or "")) if first else b""
        contracts.append(_record_contract(
            original=original,
            target=target,
            logical=logical,
            family="id_command_bundle",
            bundle_id=f"id_{str(row.get('bundle_start') or logical)}",
            line_role="first" if first else "continuation",
            route="id_first" if first else "id_continuation",
            status="active" if active else "quarantine",
            evidence=(
                "explicit ID first-line grammar"
                if first
                else "runtime screen continuation anchor" if active
                else "continuation role proven; decoder route still unproven"
            ),
            confidence="explicit-grammar" if first else "runtime-proven" if active else "high",
            source_prefix=prefix,
            target_prefix=prefix,
            ext3_supported=first,
            width_enforced=active,
            jp_dictionary=jp_dictionary,
            dictionary=dictionary,
            jp_tbl=jp_tbl,
            tbl=tbl,
        ))

    # Scenario 1/continuation bundles are rebuilt from the latest ROM and
    # Original boundaries, not from the old generated 20-cell snapshot.
    scenario_rows = _scenario_contracts(original, target, jp_dictionary, dictionary, jp_tbl, tbl)
    scenario_rows.extend(
        _scenario_supplement_contracts(
            original,
            target,
            jp_dictionary,
            dictionary,
            jp_tbl,
            tbl,
            scenario_rows,
        )
    )
    contracts.extend(scenario_rows)

    # Battle voice rows use only the independent evidence ledgers above.
    voice_rows, voice_runs = enumerate_voice_runs(original, original, jp_dictionary, jp_tbl)
    for row in voice_rows:
        logical = int(row["record_start_int"])
        source = read_record(original, logical)
        current = read_record(target, logical)
        if source is None or current is None:
            continue
        decision = voice_decision(source[0], logical, evidence)
        source_prefix = decision.prefix if source[0].startswith(decision.prefix) else b""
        target_prefix = decision.prefix if current[0].startswith(decision.prefix) else b""
        contracts.append(_record_contract(
            original=original,
            target=target,
            logical=logical,
            family="battle_voice",
            bundle_id=f"battle_{logical:06X}",
            line_role=decision.role,
            route=decision.route,
            status=decision.status,
            evidence=decision.evidence,
            confidence=decision.confidence,
            source_prefix=source_prefix,
            target_prefix=target_prefix,
            ext3_supported=decision.ext3_supported,
            width_enforced=decision.width_enforced,
            jp_dictionary=jp_dictionary,
            dictionary=dictionary,
            jp_tbl=jp_tbl,
            tbl=tbl,
            conflict=decision.conflict,
        ))

    # Independently specified bank-5F battle voice family.
    if BANK5F_SPEC.is_file():
        spec = json.loads(BANK5F_SPEC.read_text(encoding="utf-8"))
        for address, item in sorted((spec.get("targets") or {}).items()):
            logical = int(address, 16)
            source = read_record(original, logical)
            if source is None:
                continue
            prefix = source[0][:1] if source[0] and source[0][0] in {0xA1, 0x9B, 0x8A} else b""
            contracts.append(_record_contract(
                original=original,
                target=target,
                logical=logical,
                family="bank5f_battle_voice",
                bundle_id=f"bank5f_{logical:06X}",
                line_role="first",
                route="battle_tagged",
                status="active",
                evidence="independent bank5f runtime catalog",
                confidence="high",
                source_prefix=prefix,
                target_prefix=prefix,
                ext3_supported=True,
                width_enforced=True,
                jp_dictionary=jp_dictionary,
                dictionary=dictionary,
                jp_tbl=jp_tbl,
                tbl=tbl,
                catalog_jp=str(item.get("source_jp") or ""),
            ))

    # One address may be visible through overlapping source catalogs.  Keep the
    # most explicit route, and fail if two equally strong contracts disagree.
    priority = {
        "id_command_bundle": 4,
        "scenario_bundle": 3,
        "bank5f_battle_voice": 2,
        "battle_voice": 1,
    }
    by_address: dict[int, dict[str, Any]] = {}
    duplicate_conflicts: list[dict[str, Any]] = []
    for row in contracts:
        logical = int(row["address_int"])
        old = by_address.get(logical)
        if old is None or priority[row["family"]] > priority[old["family"]]:
            by_address[logical] = row
        elif priority[row["family"]] == priority[old["family"]] and (
            row["route"], row["line_role"]
        ) != (old["route"], old["line_role"]):
            duplicate_conflicts.append({
                "address": f"{logical:06X}",
                "left": {"family": old["family"], "route": old["route"]},
                "right": {"family": row["family"], "route": row["route"]},
            })
            old["status"] = "quarantine"
            old["conflict"] = "overlapping contracts disagree"
    final_rows = [by_address[key] for key in sorted(by_address)]
    status_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    for row in final_rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        route_counts[row["route"]] = route_counts.get(row["route"], 0) + 1
    return {
        "schema_version": 1,
        "generated_by": "tools/dialogue_runtime_contracts.py",
        "policy": {
            "precedence": [
                "caller/control-flow or runtime screen evidence",
                "maintained independent measurement ledger",
                "Original sentence completeness and adjacent bundle grammar",
            ],
            "line_limit": LINE_LIMIT,
            "standalone_18_is_never_a_generic_control": True,
            "compact3_allowed": False,
            "special_routes_requiring_native_or_explicit_runtime_proof": [
                "battle_body_only", "id_continuation",
            ],
        },
        "baseline_target": {
            "path": str(target_path.resolve()),
            "size": len(target),
            "sha256": sha(target),
        },
        "original": {
            "path": str(find_rom(ROOT).resolve()),
            "size": len(original),
            "sha256": sha(original),
        },
        "sources": {
            "safe_visible_leads": str(SAFE_LEADS),
            "ambiguous_leads": str(AMBIGUOUS_LEADS),
            "duplicate_visible_leads": str(DUPLICATE_LEADS),
            "scenario_supplement": str(SCENARIO_SUPPLEMENT),
            "evidence_counts": evidence["counts"],
        },
        "counts": {
            "contracts": len(final_rows),
            "id_bundles": id_bundle_count,
            "voice_runs": sum(bool(row.get("accepted")) for row in voice_runs),
            "status": dict(sorted(status_counts.items())),
            "routes": dict(sorted(route_counts.items())),
            "duplicate_conflicts": len(duplicate_conflicts),
        },
        "duplicate_conflicts": duplicate_conflicts,
        "contracts": final_rows,
    }


def _payload_marker_recursive(payload: bytes, dictionary: Any, depth: int = 0) -> bool:
    if payload_has_hangul_marker(payload):
        return True
    if EVENT_SAFE_NATIVE2_MAGIC in payload:
        expanded = _expand_event_safe_native2(payload, dictionary)
        if expanded != payload and _payload_marker_recursive(expanded, dictionary, depth + 1):
            return True
    if depth >= 6:
        return False
    i = 0
    while i < len(payload):
        lead = payload[i]
        if i + 3 < len(payload) and payload[i:i + 2] == CONTROL18_PORTAL16_MAGIC:
            try:
                raw = _control18_portal16_helper(dictionary, payload[i + 2], payload[i + 3])
                if _payload_marker_recursive(raw, dictionary, depth + 1):
                    return True
            except Exception:  # noqa: BLE001
                pass
            i += 4
            continue
        if i + 1 < len(payload) and is_ext3_magic(lead, payload[i + 1]):
            if i + 3 < len(payload):
                try:
                    raw = bytes(dictionary.raw_entry(
                        dict_index_from_ext3_token(lead, payload[i + 1], payload[i + 2], payload[i + 3])
                    ))
                    if _payload_marker_recursive(raw, dictionary, depth + 1):
                        return True
                except Exception:  # noqa: BLE001
                    pass
            i += 4
            continue
        if is_dict_token(lead) and i + 1 < len(payload):
            try:
                from monoeye_rom import dict_index_from_token
                raw = bytes(dictionary.raw_entry(dict_index_from_token(lead, payload[i + 1])))
                if _payload_marker_recursive(raw, dictionary, depth + 1):
                    return True
            except Exception:  # noqa: BLE001
                pass
            i += 2
            continue
        i += 2 if is_kanji_lead(lead) and i + 1 < len(payload) else 1
    return False


def audit_manifest(target: bytes, manifest: dict[str, Any], *, target_path: Path) -> dict[str, Any]:
    tbl = Tbl.load(TBL_PATH)
    dictionary = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    hard: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    checked = 0
    quarantined = 0

    def fail(row: dict[str, Any], reason: str, **extra: Any) -> None:
        hard.append({"abs": row["address"], "route": row["route"], "reason": reason, **extra})

    for row in manifest.get("contracts") or []:
        logical = int(row["address_int"])
        current = read_record(target, logical)
        if current is None:
            fail(row, "record_unreadable_or_missing_terminator")
            continue
        payload, term = current
        baseline = bytes.fromhex(str(row["baseline_payload_hex"]))
        if row["status"] == "quarantine":
            quarantined += 1
            if payload != baseline or f"{term:06X}" != row["baseline_terminator"]:
                fail(row, "quarantine_record_changed")
            continue
        checked += 1
        if len(payload) != int(row["record_extent"]) or f"{term:06X}" != row["source_terminator"]:
            fail(row, "record_extent_or_terminator_changed", target_terminator=f"{term:06X}")
        prefix_hex = row.get("metadata_hex") or row.get("control_prefix_hex") or ""
        prefix = bytes.fromhex(str(prefix_hex)) if prefix_hex else b""
        if prefix and not payload.startswith(prefix):
            fail(row, "required_metadata_or_control_prefix_missing", expected=prefix.hex().upper())
            offset = 0
        else:
            offset = len(prefix)
        body = payload[offset:]
        portals = scan_portals(body)
        ext3 = [item for item in portals if item["kind"] in {"ext3", "truncated_ext3"}]
        compact = [item for item in portals if item["kind"] == "compact3"]
        if compact:
            fail(row, "compact3_forbidden", portals=compact)
        if any(item.get("embedded_nul") for item in ext3):
            fail(row, "embedded_nul_or_truncated_ext3", portals=ext3)
        if ext3 and not bool((row.get("decoder") or {}).get("ext3")):
            fail(row, "unproven_ext3_on_special_route", portals=ext3)
        try:
            rendered = _decode(dictionary, body, tbl, target=True)
        except Exception as exc:  # noqa: BLE001
            fail(row, "body_decode_failed", error=type(exc).__name__)
            continue
        physical = physical_widths(rendered)
        semantic = semantic_widths(rendered)
        if has_japanese(rendered):
            fail(row, "japanese_or_control_glyph_in_visible_body", rendered=rendered)
        if any(width > LINE_LIMIT for width in semantic):
            fail(row, "semantic_line_over_20", widths=semantic)
        if row.get("width_enforced") and any(width > LINE_LIMIT for width in physical):
            fail(row, "physical_line_over_20", widths=physical, semantic=semantic)
        if any("\uac00" <= char <= "\ud7a3" for char in rendered):
            if not _payload_marker_recursive(body, dictionary):
                fail(row, "hangul_marker_missing_from_native_payload_closure")
        target_boundary = boundary_signature(target, term)
        baseline_boundary = row.get("baseline_boundary") or {}
        if (
            target_boundary.get("nul_run") != baseline_boundary.get("nul_run")
            or target_boundary.get("next_control") != baseline_boundary.get("next_control")
        ):
            fail(row, "separator_or_next_control_changed", target=target_boundary, baseline=baseline_boundary)

    counts: dict[str, Any] = {
        "contracts": len(manifest.get("contracts") or []),
        "active_checked": checked,
        "quarantine_checked": quarantined,
        "hard_failures": len(hard),
        "review_items": len(review),
    }
    by_reason: dict[str, int] = {}
    for item in hard:
        reason = str(item.get("reason") or "")
        by_reason[reason] = by_reason.get(reason, 0) + 1
    counts["hard_by_reason"] = dict(sorted(by_reason.items()))
    return {
        "schema_version": 1,
        "generated_by": "tools/dialogue_runtime_contracts.py::audit_manifest",
        "ok": not hard,
        "target": {"path": str(target_path.resolve()), "size": len(target), "sha256": sha(target)},
        "manifest_baseline": manifest.get("baseline_target") or {},
        "counts": counts,
        "hard_failures_rows": hard,
        "review_items": review,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--out", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--audit", action="store_true", help="audit target immediately after generating the manifest")
    args = parser.parse_args(argv)
    original = bytes(load_rom(find_rom(ROOT)))
    target = bytes(load_rom(args.target))
    manifest = build_manifest(original, target, target_path=args.target)
    write_manifest(args.out, manifest)
    output: dict[str, Any] = {"manifest": str(args.out), "counts": manifest["counts"]}
    exit_code = 0
    if args.audit:
        report = audit_manifest(target, manifest, target_path=args.target)
        output["audit"] = report["counts"]
        exit_code = 0 if report["ok"] else 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
