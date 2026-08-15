#!/usr/bin/env python3
"""Build a focused runtime-regression repair candidate from the current main TIP.

Fixes the 2026-08-08 runtime reports without touching the main TIP:
- visible-text bytes/tokens that were incorrectly preserved as battle-voice prefixes;
- repeated first words in battle/indirect dialogue;
- Jerid's ``……はっ！`` mistranslation;
- exact-fit E5 18 single-line scenario records that leak the following control row;
- Sig's visible ``こ`` continuation byte plus the awkward two-line translation;
- Lila's one-line event/control leak;
- Despada's weapon label width overflow.

The candidate always pairs the current live SaveRAM with the test ROM.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_broad_japanese_residuals import current_strong_retired_slots
from build_dialogue_20cell_candidate import alias_bank_cursor, encode, ext3_index
from expand_dictionary import write_dictionary_slots_spill
from mixed_residual_reference_union import _working_two_byte_external_refs
from monoeye_rom import (
    BANK_SIZE,
    Tbl,
    patch_expansion_bank,
    read_encoded_z_safe,
    slice_expansion_bank,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAV = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/runtime_regression_bundle_candidate.wsc"
OUT_SAV = ROOT / "sram/runtime_regression_bundle_candidate.sav"
OUT_REPORT = ROOT / "out/patch/runtime_regression_bundle_report.json"
EXPECTED_MAIN_SHA = "b192ad1ed2e24b709bfa14e5ae7d72405e58a3eac8ae746f41864961148d2746"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
STOCK_SPILL_SLOT = 0x003B
STOCK_SPILL_TEXT = "사병……"

# Records whose leading unit is visible source text, not metadata.  The
# translated tail already contains the full Korean wording, so the preserved
# lead is what creates the on-screen duplicate/Japanese residue.
STRIP_LEADS: dict[int, bytes] = {
    0x5E4F43: bytes.fromhex("86"),       # 全 + 전 포문...
    0x5DAE3F: bytes.fromhex("F4F5"),     # 포격 + 포격수...
    0x5D45B5: bytes.fromhex("F4F5"),     # 포격 + 포격 허가...
    0x5D83D2: bytes.fromhex("F4F5"),     # 포격 + 포격에 대비...
    0x5D8E92: bytes.fromhex("F4F5"),     # 포격 + 포격을 시작...
    0x5D4DB6: bytes.fromhex("F1EA"),     # 적기 + 적기를...
    0x5D50B0: bytes.fromhex("F1EA"),
    0x5D56F8: bytes.fromhex("F919"),     # 전원대피 + 전원 대피...
    0x5D8EE2: bytes.fromhex("F8E7"),     # 디아나 + 디아나 님...
    0x5D94A7: bytes.fromhex("F20F"),     # 직격 + 직격을...
    0x5E1947: bytes.fromhex("F15C"),     # 사격 + 사격전을...
    0x5E5016: bytes.fromhex("F2E8"),     # 원군 + 원군의...
    0x5E6590: bytes.fromhex("F48A"),     # 자신 + 저는...
    0x5EBE4D: bytes.fromhex("F5C4"),     # 파워 + 파워가...
    0x5EC02B: bytes.fromhex("F1FC"),     # 통용 + 통할...
    0x5EA62A: bytes.fromhex("F1EA"),     # 적기 + 적기를...
    0x5E4FA7: bytes.fromhex("F177"),     # 우군 + corrected full line
    0x5E98DA: bytes.fromhex("F177"),
    0x5E9F91: bytes.fromhex("F177"),
    0x5E9666: bytes.fromhex("F4F5"),     # 포격 + 포격에 대비...
    0x5EA52F: bytes.fromhex("F4F5"),     # 포격 + corrected full line
    0x5EA659: bytes.fromhex("FAF5"),     # 우군부대 + corrected full line
}

# Existing live ext3 slots that are single-consumer and therefore safe to
# retarget to corrected wording.  Equal/shorter payloads are rewritten in
# place; longer payloads are appended inside the same expansion bank and only
# that local pointer is changed.
EXT3_TEXT_REWRITES: dict[bytes, str] = {
    bytes.fromhex("E518E460"): "우군부대의　지원은　아직인가！？",
    bytes.fromhex("E518E427"): "우군　지원에　나서라！！",
    bytes.fromhex("E51837E2"): "포격수、맞서　싸워라！！",
    bytes.fromhex("E518E44D"): "우군은　뭘　하고　있는　거냐！！",
    bytes.fromhex("E518F264"): "우군이　도착할　때까지　버텨라！",
    bytes.fromhex("E5189387"): "너무　집착했던　것　같다。",
    bytes.fromhex("E5184BF7"): "나는　혼자　싸우는　데",
    bytes.fromhex("E518EFAB"): "대형런처",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_at(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=256)
    if got is None:
        raise RuntimeError(f"cannot read zstring {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def replace_same_extent(rom: bytearray, logical: int, new_payload: bytes) -> dict[str, Any]:
    old, term = payload_at(rom, logical)
    if len(new_payload) > len(old):
        raise RuntimeError(
            f"record overflow {logical:06X}: {len(new_payload)} > {len(old)}"
        )
    sb = stock_base(rom)
    start = sb + logical
    rom[start : start + len(old)] = new_payload + b"\x01" * (len(old) - len(new_payload))
    after, after_term = payload_at(rom, logical)
    if after_term != term:
        raise RuntimeError(f"terminator moved at {logical:06X}: {term:06X}->{after_term:06X}")
    return {
        "abs": f"{logical:06X}",
        "old_hex": old.hex().upper(),
        "new_hex": after.hex().upper(),
        "terminator": f"{term:06X}",
        "capacity": len(old),
    }


def strip_visible_lead(rom: bytearray, logical: int, lead: bytes) -> dict[str, Any]:
    old, _ = payload_at(rom, logical)
    if not old.startswith(lead):
        raise RuntimeError(
            f"lead drift {logical:06X}: expected {lead.hex().upper()} got {old[:len(lead)].hex().upper()}"
        )
    row = replace_same_extent(rom, logical, old[len(lead):])
    row["removed_lead_hex"] = lead.hex().upper()
    return row


def rewrite_ext3_phrases(rom: bytearray, tbl: Tbl) -> list[dict[str, Any]]:
    dictionary = make_dictionary_ext3(
        bytes(rom), load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    jobs_by_seg: dict[int, list[tuple[int, int, bytes, str]]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for token, text in EXT3_TEXT_REWRITES.items():
        idx = ext3_index(token)
        if idx is None:
            raise RuntimeError(f"not ext3 token: {token.hex().upper()}")
        seg, local = dictionary._ext3_bank_local(idx)
        payload = encode(text, tbl)
        jobs_by_seg[int(seg)].append((int(idx), int(local), payload, text))

    for seg, jobs in sorted(jobs_by_seg.items()):
        bank = bytearray(slice_expansion_bank(rom, seg))
        cursor = alias_bank_cursor(bytes(bank))
        ptrs = [struct.unpack_from("<H", bank, i * 2)[0] for i in range(0x1000)]
        for idx, local, new_payload, text in sorted(jobs, key=lambda x: x[1]):
            old_ptr = ptrs[local]
            old_end = bank.find(b"\x00", old_ptr)
            if old_end < 0:
                raise RuntimeError(f"unterminated ext3 slot {idx:05X}")
            old_payload = bytes(bank[old_ptr:old_end])
            # Refuse an interior pointer alias before shortening in place.
            interior = [
                i for i, p in enumerate(ptrs)
                if i != local and old_ptr <= p <= old_end
            ]
            if len(new_payload) <= len(old_payload) and not interior:
                bank[old_ptr : old_ptr + len(old_payload) + 1] = (
                    new_payload + b"\x00" + b"\xFF" * (len(old_payload) - len(new_payload))
                )
                new_ptr = old_ptr
                strategy = "in_place"
            else:
                need = len(new_payload) + 1
                if cursor + need > BANK_SIZE:
                    raise RuntimeError(
                        f"expansion bank {seg:02X} overflow for slot {idx:05X}"
                    )
                new_ptr = cursor
                bank[cursor : cursor + len(new_payload)] = new_payload
                bank[cursor + len(new_payload)] = 0
                struct.pack_into("<H", bank, local * 2, cursor)
                ptrs[local] = cursor
                cursor += need
                strategy = "append_repoint"
            rows.append({
                "index": f"{idx:05X}",
                "segment": f"{seg:02X}",
                "local": f"{local:03X}",
                "old_pointer": f"{old_ptr:04X}",
                "new_pointer": f"{new_ptr:04X}",
                "old_payload_hex": old_payload.hex().upper(),
                "new_payload_hex": new_payload.hex().upper(),
                "target": text,
                "strategy": strategy,
            })
        patch_expansion_bank(rom, seg, bank)
    return rows


def main() -> int:
    parent = MAIN.read_bytes()
    if len(parent) != ROM_SIZE:
        raise SystemExit(f"unexpected main ROM size: {len(parent)}")
    if sha(parent).lower() != EXPECTED_MAIN_SHA:
        raise SystemExit(
            f"main SHA drifted: {sha(parent)} != {EXPECTED_MAIN_SHA}"
        )
    if not MAIN_SAV.exists() or MAIN_SAV.stat().st_size != SAVE_SIZE:
        raise SystemExit("current live SaveRAM missing or wrong size")

    original = ORIGINAL.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    candidate = bytearray(parent)

    # 1) Update single-consumer ext3 wording first; record tokens remain stable.
    ext3_rows = rewrite_ext3_phrases(candidate, tbl)

    # 2) Remove visible source-text leads that the old voice prefix audit masked.
    strip_rows = [
        strip_visible_lead(candidate, address, lead)
        for address, lead in sorted(STRIP_LEADS.items())
    ]

    # 3) Jerid: native stock composition avoids the shared "옛" ext3 phrase.
    #    F191 == "……", F751 == "흥！" on the current main.
    jerid_row = replace_same_extent(
        candidate, 0x6139F4,
        bytes.fromhex("173418")
        + token_from_dict_index(0x0191)
        + token_from_dict_index(0x0751),
    )

    # 4) Emma: exact-fit E5 18 token was followed immediately by a control row.
    #    Allocate one dedicated retired stock token for the whole "사병……"
    #    phrase, keeping the original 17 34 18 portrait/control prefix intact.
    before_dictionary = make_dictionary_ext3(
        bytes(candidate), load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    strong = current_strong_retired_slots(original, bytes(candidate), before_dictionary)
    if STOCK_SPILL_SLOT not in strong:
        raise SystemExit(
            f"stock spill slot {STOCK_SPILL_SLOT:04X} is no longer strong-retired"
        )
    stock_payload = encode(STOCK_SPILL_TEXT, tbl)
    stock_cursor_before = max(
        [0x99BA] + [
            p + len(before_dictionary.raw_entry(i)) + 1
            for i, p in enumerate(before_dictionary.ptrs)
            if p >= 0x99BA
        ]
    )
    _, stock_cursor_after = write_dictionary_slots_spill(
        candidate,
        {STOCK_SPILL_SLOT: stock_payload},
        spill_start=0x99BA,
        allow_aux_consumers=False,
        locs=_working_two_byte_external_refs(bytes(candidate)),
    )
    if stock_cursor_after > BANK_SIZE:
        raise SystemExit("stock spill overflow")
    emma_row = replace_same_extent(
        candidate, 0x613C81,
        bytes.fromhex("173418") + token_from_dict_index(STOCK_SPILL_SLOT),
    )

    # 5) Lila: restore native-width single-line records.  Exact-fit E5 18 at
    #    613E79 leaked the following 17 28 01 06 control row; a normal stock
    #    composition leaves the runtime on the stock text path.
    lila_short_row = replace_same_extent(
        candidate, 0x613E79,
        bytes.fromhex("173418")
        + token_from_dict_index(0x0913)
        + token_from_dict_index(0x0191),
    )  # native stock path: "전투　시작" + "……"; portrait/control prefix kept.

    # 6) Sig: 0x18 is visible Japanese "こ" in this continuation, not metadata.
    #    Move the ext3 token to byte 0; the rewritten slot above supplies the
    #    natural two-line text with 614EFB.
    sig_old, _ = payload_at(candidate, 0x614F0A)
    if not sig_old.startswith(bytes.fromhex("18E5189387")):
        raise SystemExit(f"Sig continuation drifted: {sig_old.hex().upper()}")
    sig_row = replace_same_extent(candidate, 0x614F0A, sig_old[1:])

    checksum = update_ws_checksum(candidate)
    OUT_ROM.write_bytes(candidate)
    shutil.copy2(MAIN_SAV, OUT_SAV)

    report = {
        "ok": True,
        "generated_by": "tools/build_runtime_regression_bundle_candidate.py",
        "inputs": {
            "main": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(parent)},
            "save": {"path": str(MAIN_SAV.relative_to(ROOT)), "sha256": sha(MAIN_SAV.read_bytes())},
        },
        "outputs": {
            "rom": {"path": str(OUT_ROM.relative_to(ROOT)), "sha256": sha(bytes(candidate)), "size": len(candidate)},
            "save": {"path": str(OUT_SAV.relative_to(ROOT)), "sha256": sha(OUT_SAV.read_bytes()), "size": OUT_SAV.stat().st_size},
            "ws_checksum": f"{checksum:04X}",
        },
        "changes": {
            "battle_visible_leads_removed": strip_rows,
            "ext3_phrase_rewrites": ext3_rows,
            "jerid": jerid_row,
            "emma": emma_row,
            "lila_short": lila_short_row,
            "sig_continuation": sig_row,
            "stock_spill": {
                "slot": f"{STOCK_SPILL_SLOT:04X}",
                "text": STOCK_SPILL_TEXT,
                "payload_hex": stock_payload.hex().upper(),
                "cursor_before": f"{stock_cursor_before:04X}",
                "cursor_after": f"{stock_cursor_after:04X}",
            },
        },
        "runtime_targets": [
            "5E4F43 indirect-attack second line no visible 全",
            "6139F4 Jerid renders ……흥！",
            "5EA62A / 5EA659 repeated first words removed",
            "613C81 Emma single-line uses stock-token path; following control row is not text",
            "75C3C7 Despada label compacted to 대형런처 so fixed 01 padding stays inside field",
            "614F0A visible こ removed; Sig wording corrected",
            "613E79 Lila single-line uses stock-token path; following control row remains control",
            "obvious same-family battle voice duplicated prefixes removed",
        ],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["outputs"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
