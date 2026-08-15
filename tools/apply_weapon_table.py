#!/usr/bin/env python3
"""
Localize bank-75 unit/weapon display strings.

Strategy:
  1) Spill weapon *fragments* into dictionary slots (existing exact match, or
     unused stock/ext indices for new fragments).
  2) For each catalog *full* weapon name site in bank 75, prefer rewriting the
     zstring body to a single dict token pointing at full KO (size-preserving).
  3) Otherwise retokenize the site using longest-match fragments so composite
     names like ビ－ム+ライフル become 빔+라이플.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from apply_ext_dict_unit import load_ext_meta, make_dictionary  # noqa: E402
from expand_dictionary import write_dictionary_slots_spill  # noqa: E402
from hangul_marker import resolve_marker  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    dict_token_safe_in_zstring,
    find_rom,
    load_rom,
    read_encoded_z_safe,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402
from patch_ext_dictionary import write_ext_dictionary_slots  # noqa: E402

TABLE_START = 0x75C000
TABLE_END = 0x75E800
TEXT_BANKS = list(range(0x5C, 0x70)) + [0x75, 0x76, 0x50, 0x51]


def tokens_in_region(rom: bytes, start: int, end: int) -> set[int]:
    used: set[int] = set()
    abs_off = start
    while abs_off < end:
        r = read_encoded_z_safe(rom, abs_off, max_len=64)
        if r and len(r[0]) >= 1:
            raw = r[0]
            i = 0
            while i < len(raw) - 1:
                b = raw[i]
                if 0xF0 <= b <= 0xFE:
                    used.add(((b - 0xF0) << 8) | raw[i + 1])
                    i += 2
                elif b == 0xFF:
                    used.add(0xF00 | raw[i + 1])
                    i += 2
                else:
                    i += 1
            abs_off = r[1] + 1 if r[1] >= abs_off else abs_off + len(raw) + 1
            continue
        abs_off += 1
    return used


def tokens_in_payload(raw: bytes) -> set[int]:
    used: set[int] = set()
    i = 0
    while i < len(raw) - 1:
        b = raw[i]
        if 0xF0 <= b <= 0xFE:
            used.add(((b - 0xF0) << 8) | raw[i + 1])
            i += 2
        elif b == 0xFF:
            used.add(0xF00 | raw[i + 1])
            i += 2
        else:
            i += 1
    return used


def collect_protected_dict_indices(
    rom: bytes,
    d: Dictionary,
    tbl: Tbl,
    seed_path: Path | None = None,
    sheet_path: Path | None = None,
    unit_names_path: Path | None = None,
) -> set[int]:
    """Indices that must not be repurposed (dialogue / seed / unit proper nouns)."""
    from extract_script import split_prefix_body
    from monoeye_rom import read_encoded_z

    protected: set[int] = set()

    def protect_abs(abs_off: int) -> None:
        try:
            raw, _ = read_encoded_z(rom, abs_off)
        except Exception:
            return
        body = split_prefix_body(raw)[1]
        protected.update(tokens_in_payload(body))

    if sheet_path and sheet_path.exists():
        for row in json.loads(sheet_path.read_text(encoding="utf-8")).get("lines", []):
            protect_abs(int(row["abs"], 16))
    if seed_path and seed_path.exists():
        for row in json.loads(seed_path.read_text(encoding="utf-8")).get("lines", []):
            protect_abs(int(row["abs"], 16))

    # Keep unit/pilot/ship proper-noun slots (shared with bank 5C composites).
    if unit_names_path and unit_names_path.exists():
        names = {
            row["jp"]
            for row in json.loads(unit_names_path.read_text(encoding="utf-8")).get(
                "entries", []
            )
            if row.get("jp")
        }
        for idx in range(d.count):
            if d.expand_index(idx, tbl) in names:
                protected.add(idx)
    return protected


def collect_reclaimable_indices(rom: bytes, protected: set[int]) -> list[int]:
    """Dict indices referenced in the weapon table but not in protected text."""
    table = tokens_in_region(rom, TABLE_START, TABLE_END)
    out = [
        i
        for i in sorted(table - protected)
        if dict_token_safe_in_zstring(i)
    ]
    return out


def walk_table(rom: bytes, d: Dictionary, tbl: Tbl) -> list[tuple[int, bytes, str]]:
    rows: list[tuple[int, bytes, str]] = []
    abs_off = TABLE_START
    while abs_off < TABLE_END:
        r = read_encoded_z_safe(rom, abs_off, max_len=64)
        if r and len(r[0]) >= 2:
            plain = d.expand(r[0], tbl)
            if plain and "<BAD" not in plain:
                rows.append((abs_off, r[0], plain))
                abs_off = r[1] + 1 if r[1] >= abs_off else abs_off + len(r[0]) + 1
                continue
        abs_off += 1
    return rows


def encode_token_body(index: int, capacity: int) -> bytes | None:
    if not dict_token_safe_in_zstring(index):
        return None
    tok = token_from_dict_index(index)
    # capacity is original payload length (excluding NUL)
    if len(tok) > capacity:
        return None
    return tok + bytes(capacity - len(tok))


def longest_fragment_encode(
    jp: str, frag_index: dict[str, int], tbl: Tbl
) -> bytes | None:
    """Greedy longest-match tokenize jp using fragment dict indices.

    Unmatched single punctuation / alnum glyphs fall back to plaintext encode.
    """
    from monoeye_rom import encode_plaintext

    keys = sorted(frag_index.keys(), key=len, reverse=True)
    out = bytearray()
    i = 0
    while i < len(jp):
        hit = None
        for k in keys:
            if jp.startswith(k, i):
                hit = k
                break
        if hit is not None:
            out += token_from_dict_index(frag_index[hit])
            i += len(hit)
            continue
        # Allow punctuation / digits / Latin that the TBL already has.
        ch = jp[i]
        if ch in "・（）ⅡⅢⅠ－ー／＋％＆" or ("０" <= ch <= "９") or (
            "Ａ" <= ch <= "Ｚ"
        ) or ("ａ" <= ch <= "ｚ") or ch in "█":
            try:
                out += encode_plaintext(ch, tbl)
            except Exception:
                return None
            i += 1
            continue
        return None
    return bytes(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--out-rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--base-rom", type=Path, default=None)
    ap.add_argument("--names", type=Path, default=ROOT / "data/weapon_names_ko.json")
    ap.add_argument("--tbl", type=Path, default=ROOT / "out/patch/hangul_patch_pad3.tbl")
    ap.add_argument("--meta", type=Path, default=ROOT / "out/patch/ext_dictionary_meta.json")
    ap.add_argument(
        "--out-report",
        type=Path,
        default=ROOT / "out/patch/weapon_table_report.json",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data/translations_seed_hook96.json",
    )
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out/script/translations_full.json",
    )
    ap.add_argument(
        "--unit-names",
        type=Path,
        default=ROOT / "data/unit_names_ko.json",
    )
    ap.add_argument(
        "--enable-bank75-spill",
        action="store_true",
        help="DANGEROUS: retarget LE16 refs into bank75 string table. "
        "Heuristic false-positives corrupt stage/unit tables and crash on map load. "
        "Default OFF — fragment dict spill only.",
    )
    args = ap.parse_args()

    spec = json.loads(args.names.read_text(encoding="utf-8"))
    marker = resolve_marker(spec.get("marker"), source=str(args.names.name))
    rom = bytearray(load_rom(args.rom))
    base = load_rom(args.base_rom) if args.base_rom else load_rom(find_rom(ROOT))
    tbl = Tbl.load(args.tbl)
    meta = load_ext_meta(args.meta)
    d_base = make_dictionary(base, meta) if meta.get("slot_count") else Dictionary(base)
    d_rom = make_dictionary(rom, meta) if meta.get("slot_count") else Dictionary(rom)

    full_map = {
        row["jp"]: normalize_ko_text(row["ko"])
        for row in spec.get("entries", [])
        if row.get("jp") and row.get("ko")
    }
    frag_map = {
        row["jp"]: normalize_ko_text(row["ko"])
        for row in spec.get("fragments", [])
        if row.get("jp") and row.get("ko")
    }

    # Exact existing slots on base ROM for fragments.
    index_by_jp: dict[str, list[int]] = {}
    for idx in range(d_base.count):
        plain = d_base.expand_index(idx, tbl)
        if plain in frag_map or plain in full_map:
            index_by_jp.setdefault(plain, []).append(idx)

    stock = int(meta.get("stock_count", d_rom.stock_count if hasattr(d_rom, "stock_count") else 3831))
    slot_count = int(meta.get("slot_count", 0) or 0)
    # No dict-index reclaim: raw/table heuristics falsely mark dialogue slots free
    # and clobber opening seed lines. New fragments go to bank-75 trail spill.
    free_pool: list[int] = []
    free_stock_n0 = 0
    free_ext_n0 = 0

    stock_spill: dict[int, bytes] = {}
    ext_spill: dict[int, bytes] = {}
    frag_index: dict[str, int] = {}
    claimed: list[dict] = []
    missing_frag: list[str] = []
    encode_fail: list[str] = []
    BANK75_SPILL_START = 0x75FE93
    BANK75_SPILL_END = 0x75FFFF

    def encode_ko(jp: str, ko: str) -> bytes | None:
        enc = try_encode_ko_text(
            ko, tbl, hangul_marker_code=marker, hangul_marker_mode="run"
        )
        if enc is None:
            encode_fail.append(jp)
        return enc

    def write_slot(idx: int, jp: str, ko: str, enc: bytes) -> None:
        if idx < stock:
            stock_spill[idx] = enc
        else:
            ext_spill[idx] = enc
        claimed.append({"index": f"{idx:04X}", "jp": jp, "ko": ko, "bytes": len(enc)})

    # Priority fragments that unlock the most weapon strings when retokenized.
    # Longer keys first in matching; order here is allocation priority.
    FRAG_PRIORITY = [
        "ビ－ム",
        "マシン",
        "粒子",
        # べル @0003 intentionally omitted — early slot, high dialogue risk
        "ライフル",
        "バルカン",
        "バズ－カ",
        "バズ",
        "ハイパ－",
        "ニュ－",
        "キャノン",
        "サ－",
        "ガン",
        "砲",
        "ランチャ－",
        "ロケット",
        "ガトリング",
        "スプレ－",
        "ナギナタ",
        "ソ－ド",
        "ピストル",
        "ナイフ",
        "クロ－",
        "ヒ－ト",
        "ビット",
        "ポッド",
        "メガ",
        "大型",
        "小型",
        "対空",
        "機関砲",
        "機銃",
        "剣",
        "ファウスト",
        "バスタ－",
        "拡散",
        "収束",
        "試作",
        "ロング",
        "ダブル",
        "ツイン",
        "アトミック",
        "フォ－ルディング",
        "パンツァ－",
        "シュツルム",
        "ジャイアント",
        "シ－ルド",
        "ア－ム",
        "ホ－ク",
        "カノン",
        "レ－ザ－",
        # Shared MS/weapon terms already in unit catalog (safe exact slots).
        "ミサイル",
        "ファンネル",
        "ザク",
        "ジム",
        "ガンダム",
        "ゲルググ",
    ]
    # Never spill-translate these even if an exact dict slot exists — they are
    # common dialogue words (opening seed uses コンテナ @095A).
    FRAG_BLOCKLIST = {
        "コンテナ",
        "システム",
        "武装",
        "拡散",
        "フィ－ルド",
        "マザ－",
        "デビル",
        "式",
        "用",
        "型",
        "改",
    }

    # 1) Existing exact-slot fragments first (free).
    for jp in FRAG_PRIORITY:
        if jp in FRAG_BLOCKLIST:
            continue
        if jp not in frag_map and jp not in (
            "ミサイル",
            "ファンネル",
            "ザク",
            "ジム",
            "ガンダム",
            "ゲルググ",
        ):
            continue
        ko = frag_map.get(jp)
        idxs = index_by_jp.get(jp) or [
            i for i in range(min(d_base.count, stock)) if d_base.expand_index(i, tbl) == jp
        ]
        if not idxs:
            continue
        idx = idxs[0]
        if ko:
            enc = encode_ko(jp, ko)
            if enc is None:
                missing_frag.append(jp)
                continue
            write_slot(idx, jp, ko, enc)
        frag_index[jp] = idx

    for jp, ko in frag_map.items():
        if jp not in frag_index:
            missing_frag.append(jp)

    # Weapon frags intentionally appear in name75 weapon-table zstrings.
    if stock_spill:
        write_dictionary_slots_spill(
            rom, stock_spill, allow_aux_consumers=True
        )
    if ext_spill:
        write_ext_dictionary_slots(
            rom,
            ext_spill,
            ext_ptr_off=int(meta.get("ext_ptr_off", "E22B"), 16),
            stock_count=stock,
            slot_count=slot_count,
            allow_aux_consumers=True,
        )

    applied_spill: list[dict] = []
    skipped_spill: list[dict] = []
    cursor = BANK75_SPILL_START

    if args.enable_bank75_spill:
        # Kept for experiments only. Heuristic LE16 retarget has corrupted
        # stage/unit tables (thousands of false-positive pointer writes).
        sites = walk_table(base, d_base, tbl)
        ptr_map: dict[int, list[int]] = {}
        for bank in range(0x50, 0x80):
            bstart = bank * BANK_SIZE
            chunk = rom[bstart : bstart + BANK_SIZE]
            for i in range(0, BANK_SIZE - 1, 2):
                val = chunk[i] | (chunk[i + 1] << 8)
                if TABLE_START & 0xFFFF <= val < TABLE_END & 0xFFFF:
                    good = 0
                    for delta in (-4, -2, 2, 4):
                        q = i + delta
                        if 0 <= q < BANK_SIZE - 1:
                            v2 = chunk[q] | (chunk[q + 1] << 8)
                            if TABLE_START & 0xFFFF <= v2 < TABLE_END & 0xFFFF:
                                good += 1
                    if good:
                        ptr_map.setdefault(val, []).append(bstart + i)

        spilled_jp: dict[str, int] = {}
        for abs_off, raw, jp in sites:
            if jp not in full_map:
                continue
            ko = full_map[jp]
            if jp in spilled_jp:
                new_abs = spilled_jp[jp]
            else:
                enc = encode_ko(jp, ko.replace("　", "")) or encode_ko(jp, ko)
                if enc is None:
                    skipped_spill.append(
                        {"abs": f"{abs_off:06X}", "jp": jp, "reason": "encode"}
                    )
                    continue
                blob = enc + b"\x00"
                if cursor + len(blob) > BANK75_SPILL_END:
                    skipped_spill.append(
                        {"abs": f"{abs_off:06X}", "jp": jp, "reason": "spill_full"}
                    )
                    continue
                rom[cursor : cursor + len(blob)] = blob
                new_abs = cursor
                spilled_jp[jp] = new_abs
                cursor += len(blob)

            old_off = abs_off & 0xFFFF
            new_off = new_abs & 0xFFFF
            ptrs = ptr_map.get(old_off, [])
            if not ptrs:
                skipped_spill.append(
                    {"abs": f"{abs_off:06X}", "jp": jp, "reason": "no_pointer"}
                )
                continue
            for p in ptrs:
                rom[p] = new_off & 0xFF
                rom[p + 1] = (new_off >> 8) & 0xFF
            rom[abs_off : abs_off + len(raw) + 1] = bytes(len(raw) + 1)
            applied_spill.append(
                {
                    "abs": f"{abs_off:06X}",
                    "mode": "spill75",
                    "new_abs": f"{new_abs:06X}",
                    "jp": jp,
                    "ko": ko,
                    "ptrs": len(ptrs),
                }
            )
    else:
        print(
            "bank75 spill DISABLED (default) — fragment dict spill only. "
            "Use --enable-bank75-spill only for isolated experiments."
        )

    report = {
        "free_stock_before": free_stock_n0,
        "free_ext_before": free_ext_n0,
        "free_pool_left": len(free_pool),
        "slots_claimed": claimed,
        "missing_fragments": missing_frag,
        "encode_fail": encode_fail,
        "bank75_spill_enabled": bool(args.enable_bank75_spill),
        "bank75_spill_used": cursor - BANK75_SPILL_START if args.enable_bank75_spill else 0,
        "applied_spill": len(applied_spill),
        "skipped_spill": len(skipped_spill),
        "applied_spill_rows": applied_spill[:80],
        "skipped_spill_rows": skipped_spill[:80],
        "frag_slots": {k: f"{v:04X}" for k, v in frag_index.items()},
        "checksum": f"{update_ws_checksum(rom):04X}",
        "notes": [
            "bank75 LE16 spill disabled by default after stage-load crash: "
            "neighbor-density heuristic retargeted non-string tables.",
        ],
    }
    args.out_rom.write_bytes(rom)
    args.out_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Weapon table OK | frag_slots={len(frag_index)} spill={report['applied_spill']} "
        f"bank75={report['bank75_spill_enabled']} checksum={report['checksum']}"
    )
    print(f"  frag_slots={report['frag_slots']}")
    print(f"Wrote {args.out_rom}")


if __name__ == "__main__":
    main()
