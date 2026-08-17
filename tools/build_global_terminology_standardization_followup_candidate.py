#!/usr/bin/env python3
"""Build the user-confirmed whole-game terminology standardization follow-up.

Parent is the runtime-tested battle follow-up v3 candidate.  This pass does not
retranslate arbitrary text.  It only canonicalizes the user-confirmed residual
spellings that remain reachable through the current dictionary/runtime:

* 쿼트로 -> 크와트로
* 라 카일람 -> 라 카이람
* 스웨손/스엣손/스웻손/스웨슨/스엣슨/스웻슨 -> 스에손 (full name: 스에손 스테로)

The eight residual dictionary phrases were discovered by the project-wide
terminology auditor.  Equal-size Hangul phrases are rewritten in place.  The
three 쿼트로 phrases would grow by one Hangul syllable, so they replace only the
bad direct glyph run with the already-live stock token FB96 = 크와트로.

No record pointer, dictionary pointer, battle metadata, runtime hook, or SaveRAM
is changed.  The live main TIP is never modified by this builder.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import detect_ext3_alias_page_count, load_ext_meta, make_dictionary_ext3
from audit_gundam_terminology_standard import (
    dictionary_hits,
    entries as terminology_entries,
    five_bank_dictionary_hits,
    forbidden_index,
    rendered_record_hits,
    source_hits,
)
from monoeye_rom import Tbl, token_from_dict_index, update_ws_checksum

PATCH = ROOT / "out/patch"
PARENT = PATCH / "battle_runtime_user_reported_followup_v3_candidate.wsc"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "global_terminology_standardization_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/global_terminology_standardization_followup_candidate.sav"
OUT_REPORT = PATCH / "global_terminology_standardization_followup_candidate_report.json"

EXPECTED_PARENT_SHA = "7b182710052e443a3047798c6bdb6403cfbdd817e790682a01e94d90b2b9757d"
EXPECTED_MAIN_SHA = "dbc0e567fb3d7d3cd207a7e1dc6a737fbca5d131c299e8b0efc977daee546458"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
QUATTRO_STOCK = 0x0B96
BAD_QUATTRO_RUN = bytes.fromhex("EC8DE934E7E1E748")  # 쿼트로

# index -> (expected current text, canonical text, expected current raw, canonical raw)
# canonical_raw=None means replace BAD_QUATTRO_RUN by live FB96 stock token.
TARGETS: dict[int, tuple[str, str, str, str | None]] = {
    0x01C3F: (
        "쿼트로　대위、　당신은　너무하십니다。",
        "크와트로　대위、　당신은　너무하십니다。",
        "EC8DE934E7E1E74801EC8DE75CE7550701EC8DE7FFE7D2E78E01EC8DE7D3E77AE79FE9AAE75BE74D0A",
        None,
    ),
    0x0C37F: (
        "……쿼트로　대위。",
        "……크와트로　대위。",
        "0202EC8DE934E7E1E74801EC8DE75CE7550A",
        None,
    ),
    0x0C3A2: (
        "쿼트로　대위……！！",
        "크와트로　대위……！！",
        "EC8DE934E7E1E74801EC8DE75CE75502020303",
        None,
    ),
    0x04BB6: (
        "라　카일람　이하　전　함대로",
        "라　카이람　이하　전　함대로",
        "EC8DE7A101EC8DE7C1E7BDE7D501EC8DE743E79F01EC8DE74501EC8DE765E75CE748",
        "EC8DE7A101EC8DE7C1E743E7D501EC8DE743E79F01EC8DE74501EC8DE765E75CE748",
    ),
    0x0E06E: (
        "우리는　라　카일람　이하、　전함대를　가",
        "우리는　라　카이람　이하、　전함대를　가",
        "EC8DE761E777E76C01EC8DE7A101EC8DE7C1E7BDE7D501EC8DE743E79F0701EC8DE745E765E75CE77601EC8DE7A0",
        "EC8DE761E777E76C01EC8DE7A101EC8DE7C1E743E7D501EC8DE743E79F0701EC8DE745E765E75CE77601EC8DE7A0",
    ),
    0x0207D: (
        "스엣손　사망",
        "스에손　사망",
        "EC8DE782E9ADE7ED01EC8DE751E881",
        "EC8DE782E74AE7ED01EC8DE751E881",
    ),
    0x0362B: (
        "스웨슨・스테로",
        "스에손　스테로",
        "EC8DE782E78DE8252AEC8DE782E7CCE748",
        "EC8DE782E74AE7ED01EC8DE782E7CCE748",
    ),
    0x0C2F9: (
        "스엣슨、　메리벨！！",
        "스에손、　메리벨！！",
        "EC8DE782E9ADE8250701EC8DE80EE777E8D70303",
        "EC8DE782E74AE7ED0701EC8DE80EE777E8D70303",
    ),
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def clean(text: str) -> str:
    return text.rstrip("\u3000 \t")


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def aliases_at(dictionary, entry_abs: int) -> list[int]:
    aliases: list[int] = []
    ranges = [range(dictionary.count)]
    if getattr(dictionary, "ext3_count", 0) > 0:
        ranges.append(range(0x1000, 0x1000 + dictionary.ext3_count))
    for indexes in ranges:
        for index in indexes:
            try:
                if int(dictionary.entry_abs(index)) == entry_abs:
                    aliases.append(index)
            except Exception:
                continue
    return sorted(set(aliases))


def main() -> int:
    parent = PARENT.read_bytes()
    main_before = MAIN.read_bytes()
    save_before = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT_SHA:
        raise BuildError(f"v3 parent identity drifted: {sha(parent)}")
    if len(main_before) != ROM_SIZE or sha(main_before) != EXPECTED_MAIN_SHA:
        raise BuildError(f"live main identity drifted: {sha(main_before)}")
    if len(save_before) != SAVE_SIZE:
        raise BuildError(f"live SaveRAM size drifted: {len(save_before)}")
    if detect_ext3_alias_page_count(parent) != 5:
        raise BuildError("v3 parent no longer exposes five-bank alias runtime")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    if clean(dictionary.expand_index(QUATTRO_STOCK, tbl)) != "크와트로":
        raise BuildError("live stock FB96 is not 크와트로")
    quattro_token = token_from_dict_index(QUATTRO_STOCK)
    if quattro_token != bytes.fromhex("FB96"):
        raise BuildError(f"unexpected Quattro stock token {quattro_token.hex().upper()}")

    candidate = bytearray(parent)
    rows: list[dict[str, Any]] = []
    allowed: list[tuple[int, int]] = []

    for index, (before_text, after_text, before_raw_hex, after_raw_hex) in TARGETS.items():
        # Recreate the dictionary for each row so five-bank aliases and any
        # shortened prior row are decoded from the current scratch image.
        current_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        before = clean(current_dictionary.expand_index(index, tbl))
        raw = bytes(current_dictionary.raw_entry(index))
        entry_abs = int(current_dictionary.entry_abs(index))
        expected_raw = bytes.fromhex(before_raw_hex)
        if before != before_text:
            raise BuildError(f"text drift {index:05X}: {before!r} != {before_text!r}")
        if raw != expected_raw:
            raise BuildError(f"raw drift {index:05X}: {raw.hex().upper()} != {before_raw_hex}")

        aliases = aliases_at(current_dictionary, entry_abs)
        if after_raw_hex is None:
            if raw.count(BAD_QUATTRO_RUN) != 1:
                raise BuildError(f"Quattro run count drift {index:05X}")
            new_raw = raw.replace(BAD_QUATTRO_RUN, quattro_token, 1)
            mode = "replace_bad_quattro_run_with_live_stock_FB96"
        else:
            new_raw = bytes.fromhex(after_raw_hex)
            if len(new_raw) != len(raw):
                raise BuildError(f"equal-size terminology target changed extent {index:05X}")
            mode = "equal_size_inplace"

        if len(new_raw) > len(raw):
            raise BuildError(f"terminology rewrite grew {index:05X}")
        candidate[entry_abs : entry_abs + len(new_raw)] = new_raw
        candidate[entry_abs + len(new_raw)] = 0
        if len(new_raw) < len(raw):
            tail_start = entry_abs + len(new_raw) + 1
            tail_end = entry_abs + len(raw) + 1
            candidate[tail_start:tail_end] = b"\xFF" * (tail_end - tail_start)
        allowed.append((entry_abs, entry_abs + len(raw) + 1))

        verify_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        after = clean(verify_dictionary.expand_index(index, tbl))
        if after != after_text:
            raise BuildError(f"post-write render mismatch {index:05X}: {after!r} != {after_text!r}")
        rows.append({
            "index": f"{index:05X}",
            "entry_abs": f"{entry_abs:07X}",
            "aliases": [f"{value:05X}" for value in aliases],
            "before": before,
            "after": after,
            "before_raw": raw.hex().upper(),
            "after_raw": new_raw.hex().upper(),
            "mode": mode,
        })

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)

    # Only the eight phrase extents and checksum may differ from runtime-tested v3.
    def covered(offset: int) -> bool:
        return any(left <= offset < right for left, right in allowed)

    changed = [i for i, (a, b) in enumerate(zip(parent, result)) if a != b]
    unexpected = [i for i in changed if not covered(i)]
    if unexpected:
        raise BuildError(f"unexpected diff outside terminology scope: {[f'{x:07X}' for x in unexpected[:32]]}")

    # Global terminology audit uses the just-updated user standard and active sources.
    bad = forbidden_index(terminology_entries())
    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)
    active_source_hits = source_hits(bad)
    dict_hits = dictionary_hits(result, tbl, result_dictionary, bad)
    five_hits = five_bank_dictionary_hits(result, tbl, result_dictionary, bad)
    record_hits = rendered_record_hits(result, tbl, result_dictionary, bad)
    if active_source_hits or dict_hits or five_hits or record_hits:
        raise BuildError(
            "global terminology audit not clean: "
            f"sources={len(active_source_hits)} dict={len(dict_hits)} five={len(five_hits)} records={len(record_hits)}"
        )
    if detect_ext3_alias_page_count(result) != 5:
        raise BuildError("candidate lost five-bank alias runtime")
    if MAIN.read_bytes() != main_before or MAIN_SAVE.read_bytes() != save_before:
        raise BuildError("live main TIP or SaveRAM changed during candidate build")

    atomic_bytes(OUT_ROM, result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != save_before:
        raise BuildError("candidate SaveRAM differs from current main SaveRAM")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_global_terminology_standardization_followup_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_pending_immediate_promotion",
        "parent": {"path": str(PARENT.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(parent), "size": len(parent)},
        "live_main_before": {"path": str(MAIN.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(main_before), "size": len(main_before)},
        "candidate": {"path": str(OUT_ROM.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(result), "size": len(result), "checksum": f"{checksum:04X}"},
        "candidate_save": {"path": str(OUT_SAVE.relative_to(ROOT)).replace("\\", "/"), "sha256": sha(save_before), "size": len(save_before)},
        "scope": {
            "whole_game": True,
            "terms": {
                "quattro": {"canonical": "크와트로", "forbidden": ["콰트로", "쿼트로"]},
                "ra_cailum": {"canonical": "라 카이람", "forbidden": ["라 카일람", "라・카일람", "라카일람"]},
                "sweeson_stero": {"canonical": "스에손 스테로", "allowed_short": "스에손", "forbidden": ["스웨손", "스엣손", "스웻손", "스웨슨", "스엣슨", "스웻슨"]},
            },
        },
        "rewrites": rows,
        "counts": {
            "phrase_rewrites": len(rows),
            "changed_bytes": len(changed),
            "unexpected_changed_bytes": len(unexpected),
            "active_source_hits_after": len(active_source_hits),
            "dictionary_hits_after": len(dict_hits),
            "five_bank_hits_after": len(five_hits),
            "rendered_record_hits_after": len(record_hits),
        },
        "verification": {
            "global_terminology_clean": True,
            "alias_pages": detect_ext3_alias_page_count(result),
            "live_main_unchanged": True,
            "save_exact": True,
        },
    }
    atomic_json(OUT_REPORT, report)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
