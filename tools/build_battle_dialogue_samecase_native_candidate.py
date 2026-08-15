#!/usr/bin/env python3
"""Build a narrow battle-dialogue same-case native-token candidate.

The measured battle failures were not caused by the Korean text itself.  They
were caused by a battle record being presented to the consumer as a body-only
``E5 18`` ext3 token (or as a visible lead followed by one).  This builder
only handles the already catalogued body-only/duplicate-lead families and
only when the *current* ext3 phrase has an exact, existing, safe native stock
dictionary token.  It does not allocate dictionary slots, rewrite expansion
banks, or touch the Garrod native two-token work.

The candidate is based on the current main TIP.  Record extents and NUL
terminators are preserved byte-for-byte; only the payload bytes of selected
records are changed.  Rows for which no exact native stock token exists are
reported as deferred rather than guessed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import make_dictionary_ext3  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    dict_index_from_ext3_token,
    dict_token_safe_in_zstring,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)


PATCH = ROOT / "out/patch"
SCRIPT = ROOT / "out/script"
PARENT_ROM = PATCH / "monoeye_ko_expanded.wsc"
PARENT_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
BODYONLY_CSV = SCRIPT / "battle_dialogue_bodyonly_e518_stock_rehome_targets.csv"
DUPLICATE_CSV = SCRIPT / "battle_dialogue_duplicate_lead_stock_rehome_targets.csv"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
OUT_ROM = PATCH / "battle_dialogue_samecase_native_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_dialogue_samecase_native_candidate.sav"
OUT_APPLIED = SCRIPT / "battle_dialogue_samecase_native_applied.csv"
OUT_DEFERRED = SCRIPT / "battle_dialogue_samecase_native_deferred.csv"
OUT_REPORT = PATCH / "battle_dialogue_samecase_native_candidate_report.json"

EXPECTED_PARENT_SHA = (
    "55c2e1f3467d28e041ad0e145cad68091cf78d50f8d58f6ce6a65259acd59ca9"
)
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MAX_RECORD_LEN = 256
EXT3_META = {"num_banks": 16, "exp_seg0": "11"}


class BuildError(RuntimeError):
    pass


def sha(payload: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(payload)).hexdigest()


def relpath(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_text(path: Path, text: str) -> None:
    atomic_bytes(path, text.encode("utf-8"))


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def read_rows(path: Path, family: str) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["family"] = family
    return rows


def current_record(
    rom: bytes, logical_abs: int, *, stock: int
) -> tuple[bytes, int]:
    result = read_encoded_z_safe(
        rom, stock + logical_abs, max_len=MAX_RECORD_LEN
    )
    if result is None:
        raise BuildError(f"no bounded NUL-terminated record at {logical_abs:06X}")
    return result


def ext3_phrase(dictionary: Dictionary, tbl: Tbl, payload: bytes) -> str:
    if len(payload) < 4 or payload[:2] != b"\xE5\x18":
        raise BuildError(f"not an E5 18 body: {payload[:8].hex().upper()}")
    index = dict_index_from_ext3_token(*payload[:4])
    raw = dictionary.raw_entry(index)
    return clean(dictionary.expand(raw, tbl))


def native_phrase(dictionary: Dictionary, tbl: Tbl, index: int) -> str:
    raw = dictionary.raw_entry(index)
    return clean(dictionary.expand(raw, tbl))


def build_native_exact_map(
    dictionary: Dictionary, tbl: Tbl
) -> dict[str, list[int]]:
    """Return safe, exact native stock phrase -> token index candidates."""
    result: dict[str, list[int]] = defaultdict(list)
    for index in range(dictionary.stock_count):
        if not dict_token_safe_in_zstring(index):
            continue
        token = token_from_dict_index(index)
        if len(token) != 2 or 0 in token:
            continue
        try:
            text = native_phrase(dictionary, tbl, index)
        except Exception:  # noqa: BLE001 - malformed unused stock slots
            continue
        if not text or "<BADDICT:" in text:
            continue
        result[text].append(index)
    for indexes in result.values():
        indexes.sort()
    return dict(result)


def choose_native_index(
    row: dict[str, str],
    phrase: str,
    dictionary: Dictionary,
    tbl: Tbl,
    exact: dict[str, list[int]],
) -> tuple[int | None, bool]:
    """Prefer the catalogued stock id, otherwise reuse the lowest exact id."""
    preferred: int | None = None
    try:
        preferred = int(str(row.get("stock_index") or ""), 16)
    except ValueError:
        preferred = None

    if preferred is not None and preferred in exact.get(phrase, []):
        return preferred, True
    candidates = exact.get(phrase, [])
    if candidates:
        return candidates[0], False
    return None, False


def csv_text(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    import io

    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(path, buf.getvalue())


def main() -> int:
    parent = bytes(load_rom(PARENT_ROM))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"parent identity drifted: {sha(parent)}")
    save = PARENT_SAVE.read_bytes()
    if len(save) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drifted: {len(save)}")

    tbl = Tbl.load(TBL_PATH)
    stock = stock_base(parent)
    ext3 = make_dictionary_ext3(parent, {}, EXT3_META)
    native = Dictionary(parent)
    exact = build_native_exact_map(native, tbl)

    family_sources = (
        ("bodyonly_e518", BODYONLY_CSV),
        ("duplicate_lead_e518", DUPLICATE_CSV),
    )
    rows: list[dict[str, str]] = []
    for family, path in family_sources:
        rows.extend(read_rows(path, family))
    seen: dict[int, str] = {}
    for row in rows:
        logical = int(row["abs"], 16)
        old = seen.get(logical)
        if old is not None:
            raise BuildError(f"duplicate target address {logical:06X}: {old}/{row['family']}")
        seen[logical] = row["family"]

    candidate = bytearray(parent)
    applied: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    already_native: list[dict[str, Any]] = []
    allowed_ranges: list[tuple[int, int]] = []

    for row in rows:
        logical = int(row["abs"], 16)
        at = stock + logical
        payload, term = current_record(parent, logical, stock=stock)
        if term != at + len(payload):
            raise BuildError(f"terminator offset drift at {logical:06X}")
        if parent[term] != 0:
            raise BuildError(f"terminator is not NUL at {logical:06X}")

        if payload[:2] != b"\xE5\x18":
            already_native.append(
                {
                    "family": row["family"],
                    "abs": f"{logical:06X}",
                    "current_payload_hex": payload.hex().upper(),
                    "reason": "already_rehomed_or_not_current_E518",
                }
            )
            continue

        if len(payload) < 4 or any(byte != 0x01 for byte in payload[4:]):
            raise BuildError(
                f"E5 18 record is not token-plus-padding at {logical:06X}: "
                f"{payload.hex().upper()}"
            )
        phrase = ext3_phrase(ext3, tbl, payload)
        index, preferred_exact = choose_native_index(
            row, phrase, native, tbl, exact
        )
        if index is None:
            deferred.append(
                {
                    "family": row["family"],
                    "abs": f"{logical:06X}",
                    "current_payload_hex": payload.hex().upper(),
                    "record_length": len(payload),
                    "current_render": phrase,
                    "reason": "no_exact_safe_native_stock_token",
                    "catalog_stock_index": row.get("stock_index", ""),
                }
            )
            continue

        token = token_from_dict_index(index)
        if len(token) != 2 or 0 in token or not dict_token_safe_in_zstring(index):
            raise BuildError(f"unsafe native token selected at {logical:06X}: {index:04X}")
        replacement = token + (b"\x01" * (len(payload) - len(token)))
        rendered = clean(native.expand(replacement, tbl))
        if rendered != phrase:
            raise BuildError(
                f"native render mismatch at {logical:06X}: {phrase!r} != {rendered!r}"
            )

        # The only mutation is the same-sized record payload.  Pin the NUL and
        # the following control/separator bytes before writing it.
        boundary_before = parent[term : term + 8]
        candidate[at : at + len(payload)] = replacement
        boundary_after = candidate[term : term + 8]
        if boundary_before != boundary_after:
            raise BuildError(f"terminator/next-control drift at {logical:06X}")

        allowed_ranges.append((at, at + len(payload)))
        applied.append(
            {
                "family": row["family"],
                "abs": f"{logical:06X}",
                "current_payload_hex": payload.hex().upper(),
                "new_payload_hex": replacement.hex().upper(),
                "record_length": len(payload),
                "terminator_file_offset": f"{term:07X}",
                "terminator_hex": parent[term : term + 1].hex().upper(),
                "next_boundary_hex": boundary_before.hex().upper(),
                "current_render": phrase,
                "stock_index": f"{index:04X}",
                "stock_token_hex": token.hex().upper(),
                "catalog_stock_index": row.get("stock_index", ""),
                "catalog_index_exact": preferred_exact,
            }
        )

    checksum = update_ws_checksum(candidate)
    result = bytes(candidate)

    def in_allowed(offset: int) -> bool:
        return any(start <= offset < end for start, end in allowed_ranges)

    unexpected = [
        offset
        for offset, (before, after) in enumerate(zip(parent, result))
        if before != after and offset < len(result) - 2 and not in_allowed(offset)
    ]
    if unexpected:
        raise BuildError(f"unexpected diff at {unexpected[0]:07X}")

    # Re-check all selected rows on the final ROM, including terminators and
    # native rendering.  Deferred rows must remain byte-exact.
    final_native = Dictionary(result)
    final_ext3 = make_dictionary_ext3(result, {}, EXT3_META)
    final_failures: list[str] = []
    for item in applied:
        logical = int(item["abs"], 16)
        at = stock + logical
        payload, term = current_record(result, logical, stock=stock)
        if payload.hex().upper() != item["new_payload_hex"]:
            final_failures.append(f"payload:{item['abs']}")
            continue
        if result[term] != 0 or term != at + len(payload):
            final_failures.append(f"terminator:{item['abs']}")
            continue
        if clean(final_native.expand(payload, tbl)) != item["current_render"]:
            final_failures.append(f"render:{item['abs']}")
    for item in deferred:
        logical = int(item["abs"], 16)
        at = stock + logical
        payload, _term = current_record(result, logical, stock=stock)
        if payload.hex().upper() != item["current_payload_hex"]:
            final_failures.append(f"deferred_changed:{item['abs']}")
        if payload[:2] != b"\xE5\x18":
            final_failures.append(f"deferred_kind:{item['abs']}")
    if final_failures:
        raise BuildError(f"final verification failed: {final_failures[:10]}")

    # Keep the dictionary and all non-target bytes immutable (the footer
    # checksum is expected to be the sole non-record difference).
    dict_start = stock + 0x5F0000
    dict_end = dict_start + 0x10000
    dictionary_changed = parent[dict_start:dict_end] != result[dict_start:dict_end]
    if dictionary_changed:
        raise BuildError("dictionary bank changed unexpectedly")

    atomic_bytes(OUT_ROM, result)
    atomic_bytes(OUT_SAVE, save)
    csv_text(
        OUT_APPLIED,
        applied,
        [
            "family",
            "abs",
            "current_payload_hex",
            "new_payload_hex",
            "record_length",
            "terminator_file_offset",
            "terminator_hex",
            "next_boundary_hex",
            "current_render",
            "stock_index",
            "stock_token_hex",
            "catalog_stock_index",
            "catalog_index_exact",
        ],
    )
    csv_text(
        OUT_DEFERRED,
        deferred,
        [
            "family",
            "abs",
            "current_payload_hex",
            "record_length",
            "current_render",
            "reason",
            "catalog_stock_index",
        ],
    )

    report = {
        "schema_version": 1,
        "generated_by": relpath(Path(__file__)),
        "ok": True,
        "purpose": (
            "same-case battle dialogue cleanup: rehome only current E5 18 "
            "body/duplicate records with exact existing native stock tokens"
        ),
        "parent": {"path": relpath(PARENT_ROM), "size": len(parent), "sha256": sha(parent)},
        "candidate": {
            "path": relpath(OUT_ROM),
            "size": len(result),
            "sha256": sha(result),
            "ws_checksum": f"{checksum:04X}",
        },
        "candidate_save": {
            "path": relpath(OUT_SAVE),
            "size": len(save),
            "sha256": sha(save),
        },
        "inputs": {
            "bodyonly_targets": relpath(BODYONLY_CSV),
            "duplicate_targets": relpath(DUPLICATE_CSV),
            "tbl": relpath(TBL_PATH),
            "ext3_decoder": EXT3_META,
            "native_dictionary_stock_count": native.stock_count,
            "native_exact_phrase_count": len(exact),
        },
        "counts": {
            "catalogued_total": len(rows),
            "already_native_or_non_e518": len(already_native),
            "current_e518": len(applied) + len(deferred),
            "applied": len(applied),
            "deferred": len(deferred),
            "applied_bodyonly": sum(x["family"] == "bodyonly_e518" for x in applied),
            "applied_duplicate_lead": sum(x["family"] == "duplicate_lead_e518" for x in applied),
            "deferred_bodyonly": sum(x["family"] == "bodyonly_e518" for x in deferred),
            "deferred_duplicate_lead": sum(x["family"] == "duplicate_lead_e518" for x in deferred),
            "unexpected_diff_offsets": len(unexpected),
            "dictionary_changed": int(dictionary_changed),
        },
        "checks": {
            "parent_identity_exact": True,
            "record_extents_preserved": True,
            "terminators_preserved": True,
            "next_boundary_preserved": True,
            "native_token_two_bytes_only": True,
            "native_render_exact": True,
            "deferred_rows_byte_exact": True,
            "dictionary_unchanged": True,
            "unexpected_diff_offsets_zero": True,
            "no_new_dictionary_allocation": True,
            "main_tip_unchanged": True,
        },
        "deferred_reason": (
            "The current native stock dictionary has no exact safe token for "
            "these current phrases.  They are not rewritten or assigned a new "
            "ext3/expansion slot in this candidate."
        ),
        "applied": applied,
        "deferred": deferred,
        "already_native": already_native,
    }
    atomic_text(OUT_REPORT, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "candidate": report["candidate"],
                "candidate_save": report["candidate_save"],
                "counts": report["counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
