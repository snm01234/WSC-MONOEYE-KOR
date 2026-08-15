#!/usr/bin/env python3
"""
Three-way stock address-space diff: original 8 MiB / pre_ext3 / target ROM.

READ-ONLY. This tool never opens a .wsc for writing.

Compares only the stock address space (logical banks 00–7F, file
``stock_base(rom) + logical``), so an 8 MiB and a 16 MiB image compare
correctly and the prepended expansion region (file 0x000000–0x7FFFFF of a
16 MiB image) is excluded by construction.

Contiguous differing bytes are merged into runs, each run is classified
INTENDED_APPROVED / UNINTENDED against the approved change list
(bugfix.md §Glossary intended), attributed to PRE / EXT3 / BOTH_CHANGED via the
three-way comparison, and tagged with a signature-based tool guess.

Exit code 1 when any UNINTENDED run exists (basis for verify_stock_noninvasion).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    DICT_DATA_START,
    DICT_PTR_END,
    DICT_PTR_START,
    Dictionary,
    Tbl,
    is_dict_token,
    le16,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    ws_header,
)

# --- stock address space ----------------------------------------------------

STOCK_SPAN = 0x800000  # logical banks 00–7F
HEADER_LOGICAL = 0x7FFFF0
HEADER_ROM_SIZE = HEADER_LOGICAL + 0x0A
HEADER_CHECKSUM = (HEADER_LOGICAL + 0x0E, HEADER_LOGICAL + 0x0F)

INTENDED = "INTENDED_APPROVED"
UNINTENDED = "UNINTENDED"

# --- approved (intended) change list — bugfix.md §Glossary / §Fix 1 ---------

DIALOGUE_LO = 0x6040A5
# Upper bound of the *approved* dialogue band. Banks 64–69 are fixed-stride data
# tables, not dialogue: bank 64/65 hold the per-stage (event-name, event-body)
# pointer pair tables (e.g. 64:4F79 -> 64:4FF1 'ＳＴＧ３<E62F>オ－プニング') and
# 66/67 the same for the late stages. PATCH_PROGRESS reduced the applied band to
# 0x63FFFF for exactly that reason, but this constant kept saying 0x69FFFF, so
# every byte written into 64–69 was auto-classified INTENDED "dialogue_record"
# and the gate reported unintended 0 B while stage event tables were destroyed.
DIALOGUE_HI = 0x63FFFF

DICT_BANK = 0x5F                    # dict strings + pointer table 5F:7BCC–99B9
GLYPH_BANKS = (0x3F, 0x40, 0x41)    # glyph padding, original byte must be FF
EXT_DICT_MIGRATE_LO = 0x5EE22B      # ext dict migrate region, original FF

UI_APPROVED: Dict[int, int] = {     # approved Hangul UI strings (3.11)
    0x75B6A6: 7,
    0x75B7C5: 7,
    0x75B7CD: 7,
    0x75B7D5: 6,
    0x75BA40: 8,
}

# Approved hook sites with their real patch footprint (site, length, name).
# Lengths come from the installers, not guessed: patch_font_hangul_hook
# (PRIMARY_SITE_LEN, STORE_SITE_LEN, DISPATCH_SITE, PARSER_B_CALL) and
# patch_3byte_dict_token (LEAF_EXPECT, SITE1..SITE1_RETURN, SITE2..SITE2_RETURN).
HOOK_FOOTPRINTS: Tuple[Tuple[int, int, str], ...] = (
    (0x7A0521, 10, "primary_blitter"),          # PRIMARY_SITE_LEN = 10
    (0x7A06CE, 6, "ext3_leaf"),                 # LEAF_EXPECT = 6 B
    (0x7A0700, 5, "ext_dict_ptr_fetch"),
    (0x7A0736, 13, "ext3_site1_stream_worker"),  # SITE1 → SITE1_RETURN 0x0743
    (0x7A07A0, 4, "glyph_index_store"),         # STORE_SITE_LEN = 4
    (0x7A080D, 14, "ext3_site2_parser_b"),      # SITE2 → SITE2_RETURN 0x081B
)
HOOK_SITES = tuple(site for site, _len, _name in HOOK_FOOTPRINTS)

CAVES: Tuple[Tuple[int, int, str], ...] = (
    (0x7AFFB5, 0x7AFFFF, "cave_7A_FFB5"),
    (0x7FFC4E, 0x7FFD0F, "cave_7F_FC4E"),
    (0x7FFD10, 0x7FFFEF, "cave_7F_FD10"),
)

# Menu button plates in bank 72: 80x16 packed-4bpp graphics, 0x280 each from
# 72:0080. Overwriting them is the *point* of tools/patch_menu_plates_ko.py, and
# the source was proven by measurement (single-tile mutation at 721020 moves
# exactly one on-screen 8x8 block) -- see docs/UI_MENU_NEXT_STEPS.md.
# The band covers all 29 plates: the three initial-menu labels (0-10) plus
# ノーマル / スペシャル / 通信モード / 鑑賞モード / 対戦モード / ユニット交換 (11-28).
# It stops at the last plate; the rest of bank 72 stays fenced off.
MENU_PLATE_LO = 0x720080
MENU_PLATE_HI = 0x7248FF            # plates 0-28

# Title-screen footer copyright strip. Unique 224x16 packed-4bpp blob measured
# from Beetle state27 FG tiles 0E3-11A; English ©BANDAI 2002 columns stay.
TITLE_COPYRIGHT_LO = 0x5519DC
TITLE_COPYRIGHT_HI = 0x5520DB

# Intermission label glyph tiles in bank 54. Unlike the plate atlas these are not a
# contiguous band -- bank 54 is shared UI data and the label characters are single
# 32-byte overlay tiles scattered through it -- so the approved set is an explicit
# address list published by tools/patch_intermission_labels_ko.py, which only lists
# cells that were validated against a native intermission capture.
INTERMISSION_TILES_JSON = ROOT / "data" / "intermission_glyph_tiles.json"


def _load_intermission_tiles() -> Tuple[frozenset, int]:
    try:
        blob = json.loads(INTERMISSION_TILES_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset(), 32
    return frozenset(int(t, 16) for t in blob.get("tiles", [])), int(blob.get("tile_bytes", 32))


INTERMISSION_TILES, INTERMISSION_TILE_BYTES = _load_intermission_tiles()

# Unit/weapon display-table records deliberately rewritten to an ext3 token by
# tools/apply_name75_ko.py. Read from that tool's report, not hardcoded, and only
# when the report says the run succeeded — so the approval is exactly the record
# set the writer verified, the same evidence-backed policy as INTERMISSION_TILES.
# This waives only the "these bytes changed" question. Record length and
# terminator are still enforced by verify_nondialogue_text check (iii), which is
# the check that matters here: the table is walked sequentially, so a shortened
# record would shift every entry after it.
NAME75_KO_REPORTS = (
    ROOT / "out" / "patch" / "name75_ko_report.json",
    ROOT / "out" / "patch" / "aux_ko_report.json",
    # mixed Korean/Japanese residual localization writes the same in-place ext3
    # record rewrite from its reviewed catalog. Same evidence-backed policy: the
    # approval is exactly the applied rows the writer reports, only when the run
    # succeeded, and it waives only "these bytes changed" — record length and
    # terminator stay under verify_nondialogue_text check (iii).
    ROOT / "out" / "patch" / "mixed_residual_localization_report.json",
    # Reviewed bank-5C MS encyclopedia in-place record rewrites.  The report's
    # successful applied rows waive only the intentional record-body bytes;
    # structure/terminators remain covered by verify_nondialogue_text.
    ROOT / "out" / "patch" / "encyclopedia_ms_batch01_report.json",
    # Widened rear bank-5C MS encyclopedia batch, accepted only through its
    # successful applied rows. Record structure remains independently gated.
    ROOT / "out" / "patch" / "encyclopedia_ms_batch02_report.json",
    # Runtime-safe character encyclopedia batch. The rejected E5 2F/bank21
    # report is deliberately not part of the approved change set.
    ROOT / "out" / "patch" / "encyclopedia_character_safe_batch01_report.json",
)


def _load_name75_ko_ranges() -> Tuple[Tuple[int, int], ...]:
    out: List[Tuple[int, int]] = []
    for path in NAME75_KO_REPORTS:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not blob.get("ok"):
            continue
        for row in blob.get("applied") or []:
            try:
                site = int(row["abs"], 16)
                ln = int(row["payload_len"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append((site, site + ln))
    return tuple(sorted(out))


NAME75_KO_RANGES = _load_name75_ko_ranges()

UNINTENDED_BAND_NAMES = (
    (0x50, 0x5D, "battle_ui_bank"),
    (0x5E, 0x5F, "aux_dict_bank"),
    (0x60, 0x63, "dialogue_bank_outside_band"),
    (0x64, 0x69, "data_table_bank_64_69"),
    (0x6A, 0x6F, "table_bank"),
    (0x70, 0x7F, "code_bank"),
)

PADDED_TOKEN_TOOLS = (
    "apply_safe_unit",
    "apply_seq_unit",
    "apply_ext_dict_unit",
    "apply_3byte_seq_ko",
)
PAD_BYTE = 0x01
PAD_MIN = 4  # trailing 0x01 count that makes the padded-token signature reliable


# --- run model --------------------------------------------------------------


@dataclass
class Run:
    logical: int
    length: int
    orig: bytes
    tgt: bytes
    classification: str
    category: str
    abs_jp: int
    abs_target: int
    attribution: str = "?"
    attributed_tool: str = "unknown"
    tool_candidates: List[str] = field(default_factory=list)
    note: str = ""
    orig_text: str | None = None
    target_text: str | None = None
    lost_terminator: List[int] = field(default_factory=list)

    @property
    def bank(self) -> int:
        return self.logical >> 16

    @property
    def off(self) -> int:
        return self.logical & 0xFFFF

    @property
    def site(self) -> str:
        return f"{self.bank:02X}:{self.off:04X}"

    def to_json(self, hex_cap: int) -> dict:
        cap = len(self.orig) if self.classification == UNINTENDED else hex_cap
        out = {
            "site": self.site,
            "bank": f"{self.bank:02X}",
            "off": f"{self.off:04X}",
            "logical": f"{self.logical:06X}",
            "abs_jp": f"{self.abs_jp:07X}",
            "abs_target": f"{self.abs_target:07X}",
            "len": self.length,
            "orig_hex": self.orig[:cap].hex(),
            "target_hex": self.tgt[:cap].hex(),
            "classification": self.classification,
            "category": self.category,
            "attribution": self.attribution,
            "attributed_tool": self.attributed_tool,
        }
        if cap < len(self.orig):
            out["hex_truncated_to"] = cap
        if self.tool_candidates:
            out["tool_candidates"] = self.tool_candidates
        if self.note:
            out["note"] = self.note
        if self.orig_text is not None:
            out["orig_text"] = self.orig_text
        if self.target_text is not None:
            out["target_text"] = self.target_text
        if self.lost_terminator:
            out["lost_terminator_at"] = [f"+{d}" for d in self.lost_terminator]
        return out


# --- classification ---------------------------------------------------------


def _band_name(bank: int) -> str:
    for lo, hi, name in UNINTENDED_BAND_NAMES:
        if lo <= bank <= hi:
            return name
    return "stock_data"


def classify_byte(
    logical: int,
    orig: int,
    tgt: int,
    baseline_ranges: Sequence[Tuple[int, int, str, bytes]] = (),
) -> Tuple[str, str]:
    """Classify one differing stock byte as INTENDED_APPROVED / UNINTENDED."""
    bank = logical >> 16
    off = logical & 0xFFFF

    if logical >= HEADER_LOGICAL:
        if logical == HEADER_ROM_SIZE:
            return INTENDED, "header_rom_size"
        if logical in HEADER_CHECKSUM:
            return INTENDED, "header_checksum"
        return UNINTENDED, "header_other"

    for lo, hi, _owner, expected in baseline_ranges:
        if lo <= logical < hi:
            if tgt == expected[logical - lo]:
                return INTENDED, "baseline_record_body"
            return UNINTENDED, "baseline_record_body_drift"

    if DIALOGUE_LO <= logical <= DIALOGUE_HI:
        return INTENDED, "dialogue_record"

    if bank == DICT_BANK:
        if DICT_PTR_START <= off <= DICT_PTR_END:
            return INTENDED, "dict_pointer_table"
        if DICT_DATA_START <= off < DICT_PTR_START:
            return INTENDED, "dict_string"
        return INTENDED, "dict_spill"

    if bank in GLYPH_BANKS:
        if orig == 0xFF:
            return INTENDED, "glyph_padding"
        return UNINTENDED, "glyph_overwrite"

    if bank == 0x5E and logical >= EXT_DICT_MIGRATE_LO:
        if orig == 0xFF:
            return INTENDED, "ext_dict_migrate"
        return UNINTENDED, "ext_dict_migrate_overwrite"

    if MENU_PLATE_LO <= logical <= MENU_PLATE_HI:
        return INTENDED, "menu_plate_graphics"

    if TITLE_COPYRIGHT_LO <= logical <= TITLE_COPYRIGHT_HI:
        return INTENDED, "title_copyright_graphics"

    if INTERMISSION_TILES:
        tile_start = logical - (logical % INTERMISSION_TILE_BYTES)
        if tile_start in INTERMISSION_TILES:
            return INTENDED, "intermission_label_graphics"

    for site, ln in UI_APPROVED.items():
        if site <= logical < site + ln:
            return INTENDED, "ui_string_approved"
    for lo, hi in NAME75_KO_RANGES:
        if lo <= logical < hi:
            return INTENDED, "name75_ko_record"

    for site, ln, _name in HOOK_FOOTPRINTS:
        if site <= logical < site + ln:
            return INTENDED, "hook_site"

    for lo, hi, name in CAVES:
        if lo <= logical <= hi:
            return INTENDED, name

    return UNINTENDED, _band_name(bank)


# --- diff scan --------------------------------------------------------------


def diff_positions(
    a: memoryview, b: memoryview, chunk: int = 0x2000
) -> Iterator[int]:
    """Yield offsets where a != b, skipping equal chunks."""
    n = min(len(a), len(b))
    i = 0
    while i < n:
        j = min(i + chunk, n)
        if a[i:j] != b[i:j]:
            ab = bytes(a[i:j])
            bb = bytes(b[i:j])
            for k in range(j - i):
                if ab[k] != bb[k]:
                    yield i + k
        i = j


def stock_view(rom: bytes | bytearray) -> memoryview:
    base = stock_base(rom)
    return memoryview(bytes(rom))[base : base + STOCK_SPAN]


def build_runs(
    jp_v: memoryview,
    tgt_v: memoryview,
    sb_jp: int,
    sb_tgt: int,
    baseline_ranges: Sequence[Tuple[int, int, str, bytes]] = (),
) -> List[Run]:
    """Merge contiguous differing bytes that share a classification into runs."""
    runs: List[Run] = []
    cur_start = -1
    cur_cls = ""
    cur_cat = ""
    prev = -2

    def flush(end: int) -> None:
        if cur_start < 0:
            return
        length = end - cur_start + 1
        runs.append(
            Run(
                logical=cur_start,
                length=length,
                orig=bytes(jp_v[cur_start : cur_start + length]),
                tgt=bytes(tgt_v[cur_start : cur_start + length]),
                classification=cur_cls,
                category=cur_cat,
                abs_jp=sb_jp + cur_start,
                abs_target=sb_tgt + cur_start,
            )
        )

    for pos in diff_positions(jp_v, tgt_v):
        cls, cat = classify_byte(
            pos, jp_v[pos], tgt_v[pos], baseline_ranges
        )
        if cur_start >= 0 and pos == prev + 1 and cls == cur_cls and cat == cur_cat:
            prev = pos
            continue
        flush(prev)
        cur_start, cur_cls, cur_cat = pos, cls, cat
        prev = pos
    flush(prev)
    return runs


# --- three-way attribution --------------------------------------------------


def attribute(run: Run, jp_v: memoryview, pre_v: memoryview, tgt_v: memoryview) -> str:
    lo, hi = run.logical, run.logical + run.length
    pre = pre_v[lo:hi]
    pre_eq_jp = pre == jp_v[lo:hi]
    pre_eq_tgt = pre == tgt_v[lo:hi]
    if pre_eq_jp:
        return "EXT3"
    if pre_eq_tgt:
        return "PRE"
    return "BOTH_CHANGED"


# --- signature-based tool attribution --------------------------------------


def _trailing_pad(data: bytes) -> int:
    n = 0
    for b in reversed(data):
        if b != PAD_BYTE:
            break
        n += 1
    return n


def guess_tool(run: Run) -> Tuple[str, List[str]]:
    o, t = run.orig, run.tgt

    # (d) far jmp installed at a known hook site
    for site in HOOK_SITES:
        if run.logical <= site < run.logical + run.length:
            if t[site - run.logical] == 0xEA:
                return "hook_installer", ["patch_3byte_dict_token", "patch_font_hook"]

    # (c) original all-FF => free-space writer (glyph / cave / dict migrate)
    if o and all(b == 0xFF for b in o):
        return "free_space_writer", []

    # (a) token + 0x01 padding payload
    pad = _trailing_pad(t)
    if pad >= PAD_MIN and run.length > pad:
        head = t[: run.length - pad]
        if any(is_dict_token(b) for b in head) or len(head) >= 2:
            return "padded_token_payload", list(PADDED_TOKEN_TOOLS)

    # (b) 2-byte tail replacement, original `xx 00`, only valid byte-swapped
    if run.length == 2:
        swap_only = is_dict_token(t[1]) and not is_dict_token(t[0])
        if o[1] == 0x00:
            if swap_only:
                return "legacy_le16_token_writer", []
            if is_dict_token(t[0]):
                # both orders decode as a token — order is ambiguous
                cand = ["legacy_le16_token_writer"] if is_dict_token(t[1]) else []
                return "token_tail_writer", cand
            return "two_byte_tail_replacement", []
        # same 2-byte signature but original did not end on a zstring NUL
        if swap_only:
            return "le16_token_writer_suspect", ["legacy_le16_token_writer"]
        if is_dict_token(t[0]):
            return "token_tail_writer", []
        return "two_byte_tail_replacement", []

    if run.length == 1:
        return "single_byte_overwrite", []

    if run.category in ("header_rom_size", "header_checksum"):
        return "header_writer", ["expand_rom_to_16mb", "update_ws_checksum"]
    if run.category == "dialogue_record":
        return "script_writer", ["build_script_ko", "apply_3byte_seq_ko"]
    if run.category.startswith("dict_"):
        return "dict_writer", ["expand_dictionary", "apply_3byte_seq_ko"]
    if run.category == "ui_string_approved":
        return "ui_string_writer", ["apply_ui_inplace_ko"]
    if run.category == "menu_plate_graphics":
        return "menu_plate_writer", ["patch_menu_plates_ko"]
    if run.category == "title_copyright_graphics":
        return "title_copyright_writer", ["build_title_menu_bitmap_copyright_candidate"]
    if run.category == "intermission_label_graphics":
        return "intermission_label_writer", ["patch_intermission_labels_ko"]
    if run.category.startswith("cave_"):
        return "cave_writer", ["patch_3byte_dict_token"]
    if run.category == "hook_site":
        return "hook_installer", ["patch_3byte_dict_token"]
    return "unknown", []


# --- decode notes for UNINTENDED runs ---------------------------------------


class Decoder:
    """Best-effort zstring decode of original vs target at a run start."""

    def __init__(self, jp: bytes, tgt: bytes, tbl_path: Path | None) -> None:
        self.ok = False
        if tbl_path is None or not tbl_path.exists():
            return
        try:
            self.tbl = Tbl.load(tbl_path)
            self.jp_dict = Dictionary(jp)
            self.tgt_dict = Dictionary(tgt)
            self.ok = True
        except Exception:  # pragma: no cover - decoding is informational only
            self.ok = False

    def _decode(self, rom: bytes, dic: Dictionary, abs_off: int) -> Tuple[str | None, int | None]:
        r = read_encoded_z_safe(rom, abs_off, max_len=256)
        if not r:
            return None, None
        payload, term = r
        try:
            text = dic.expand(payload, self.tbl)
        except Exception:  # pragma: no cover
            text = None
        return text, term - abs_off

    def annotate(self, run: Run, jp: bytes, tgt: bytes) -> None:
        run.lost_terminator = [
            i for i in range(run.length) if run.orig[i] == 0 and run.tgt[i] != 0
        ]
        parts: List[str] = []
        if self.ok:
            o_text, o_len = self._decode(jp, self.jp_dict, run.abs_jp)
            t_text, t_len = self._decode(tgt, self.tgt_dict, run.abs_target)
            if o_text is not None:
                run.orig_text = o_text
                parts.append(f"orig zstring len={o_len} {o_text!r}")
            else:
                parts.append("orig not a zstring within 256 B (dense table?)")
            if t_text is not None:
                run.target_text = t_text
                parts.append(f"target zstring len={t_len} {t_text!r}")
            else:
                parts.append("target not a zstring within 256 B")
        if run.lost_terminator:
            at = ", ".join(f"+{d}" for d in run.lost_terminator)
            parts.append(f"LOST 00 terminator at {at}")
        run.note = "; ".join(parts)


# --- dictionary 5F review ---------------------------------------------------


def dict_pointer_stats(jp: bytes, pre: bytes, tgt: bytes) -> dict:
    n = (DICT_PTR_END - DICT_PTR_START + 1) // 2
    base = (DICT_BANK << 16) + DICT_PTR_START
    fj, fp, ft = stock_base(jp) + base, stock_base(pre) + base, stock_base(tgt) + base
    match_jp = 0
    changed_pre_tgt = 0
    for i in range(n):
        pj = le16(jp, fj + i * 2)
        pp = le16(pre, fp + i * 2)
        pt = le16(tgt, ft + i * 2)
        if pj == pt:
            match_jp += 1
        if pp != pt:
            changed_pre_tgt += 1
    return {
        "pointer_count": n,
        "pointers_match_original": match_jp,
        "pointers_changed_pre_to_target": changed_pre_tgt,
        "gate_min_match": 3802,
        "gate_ok": match_jp >= 3802,
    }


# --- aggregation ------------------------------------------------------------


def summarize(runs: Sequence[Run]) -> dict:
    def bucket() -> dict:
        return {"runs": 0, "bytes": 0, "unintended_runs": 0, "unintended_bytes": 0}

    by_bank: Dict[str, dict] = {}
    by_cat: Dict[str, dict] = {}
    by_attr: Dict[str, dict] = {}
    by_tool: Dict[str, dict] = {}
    for r in runs:
        for key, table in (
            (f"{r.bank:02X}", by_bank),
            (r.category, by_cat),
            (r.attribution, by_attr),
            (r.attributed_tool, by_tool),
        ):
            b = table.setdefault(key, bucket())
            b["runs"] += 1
            b["bytes"] += r.length
            if r.classification == UNINTENDED:
                b["unintended_runs"] += 1
                b["unintended_bytes"] += r.length
    return {
        "by_bank": dict(sorted(by_bank.items())),
        "by_category": dict(sorted(by_cat.items())),
        "by_attribution": dict(sorted(by_attr.items())),
        "by_attributed_tool": dict(sorted(by_tool.items())),
    }


def pair_diff_by_bank(a: memoryview, b: memoryview) -> Tuple[int, Dict[str, int]]:
    """Raw byte-diff count between two stock views, plus a per-bank breakdown."""
    total = 0
    by_bank: Dict[str, int] = {}
    for pos in diff_positions(a, b):
        total += 1
        key = f"{pos >> 16:02X}"
        by_bank[key] = by_bank.get(key, 0) + 1
    return total, dict(sorted(by_bank.items()))


def rom_info(path: Path, rom: bytes) -> dict:
    h = ws_header(rom)
    return {
        "path": str(path),
        "size": len(rom),
        "stock_base": f"{stock_base(rom):#x}",
        "rom_size_code": f"{h['rom_size_code']:02X}",
        "sram_size_code": f"{h['sram_size_code']:02X}",
        "checksum": f"{h['checksum']:04X}",
    }


# --- main -------------------------------------------------------------------


def run_diff(
    jp_path: Path,
    pre_path: Path,
    tgt_path: Path,
    *,
    tbl_path: Path | None,
    hex_cap: int,
    decode: bool,
    max_per_cat: int,
    baseline_ranges: Sequence[Tuple[int, int, str, bytes]] = (),
) -> dict:
    jp = bytes(load_rom(jp_path))
    pre = bytes(load_rom(pre_path))
    tgt = bytes(load_rom(tgt_path))

    jp_v, pre_v, tgt_v = stock_view(jp), stock_view(pre), stock_view(tgt)
    sb_jp, sb_tgt = stock_base(jp), stock_base(tgt)

    runs = build_runs(
        jp_v, tgt_v, sb_jp, sb_tgt, baseline_ranges
    )
    for r in runs:
        r.attribution = attribute(r, jp_v, pre_v, tgt_v)
        r.attributed_tool, r.tool_candidates = guess_tool(r)

    unintended = [r for r in runs if r.classification == UNINTENDED]
    if decode and unintended:
        dec = Decoder(jp, tgt, tbl_path)
        for r in unintended:
            dec.annotate(r, jp, tgt)

    diff_bytes = sum(r.length for r in runs)
    un_bytes = sum(r.length for r in unintended)
    dict_runs = [r for r in runs if r.bank == DICT_BANK]

    pre_total, pre_by_bank = pair_diff_by_bank(pre_v, tgt_v)
    orig_pre_total, orig_pre_by_bank = pair_diff_by_bank(jp_v, pre_v)

    report = {
        "ok": not unintended,
        "generated_by": "tools/diff_stock_3way.py",
        "read_only": True,
        "compared_space": "stock logical banks 00–7F via stock_base(rom)+logical "
        "(expansion file 0x000000–0x7FFFFF excluded)",
        "inputs": {
            "original": rom_info(jp_path, jp),
            "pre_ext3": rom_info(pre_path, pre),
            "target": rom_info(tgt_path, tgt),
        },
        "counts": {
            "diff_bytes": diff_bytes,
            "runs": len(runs),
            "intended_runs": len(runs) - len(unintended),
            "intended_bytes": diff_bytes - un_bytes,
            "unintended_runs": len(unintended),
            "unintended_bytes": un_bytes,
        },
        **summarize(runs),
        "attribution_note": "PRE/EXT3/BOTH_CHANGED compare the target against "
        "pre_ext3; the labels only mean 'ext3 session' when the target descends "
        "from pre_ext3 (tip lineage). For other targets read them as "
        "'differs from pre_ext3'.",
        "original_to_pre_ext3": {
            "diff_bytes": orig_pre_total,
            "by_bank": orig_pre_by_bank,
        },
        "pre_ext3_to_target": {"diff_bytes": pre_total, "by_bank": pre_by_bank},
        "dict_5f_review": {
            "runs": len(dict_runs),
            "bytes": sum(r.length for r in dict_runs),
            "bytes_changed_pre_to_target": pre_by_bank.get(f"{DICT_BANK:02X}", 0),
            "bytes_ext3_session_runs": sum(
                r.length for r in dict_runs if r.attribution == "EXT3"
            ),
            **dict_pointer_stats(jp, pre, tgt),
            "note": "5F is dialogue/intermission/HUD shared — intended by 2.8 but "
            "flagged for review (bugfix.md §Fix 3, requirement 3.10)",
        },
        "unintended": [r.to_json(hex_cap) for r in unintended],
    }

    # Every UNINTENDED run is always listed in full. INTENDED runs are listed
    # too, but capped per category unless --all-runs, so the gate report stays
    # readable (dialogue alone is >16k runs). Omissions are counted explicitly.
    listed: List[dict] = []
    omitted: Dict[str, int] = {}
    seen: Dict[str, int] = {}
    for r in runs:
        if r.classification == UNINTENDED or max_per_cat <= 0:
            listed.append(r.to_json(hex_cap))
            continue
        n = seen.get(r.category, 0)
        if n < max_per_cat:
            seen[r.category] = n + 1
            listed.append(r.to_json(hex_cap))
        else:
            omitted[r.category] = omitted.get(r.category, 0) + 1
    report["runs_total"] = len(runs)
    report["runs_listed"] = len(listed)
    report["runs_omitted_by_category"] = dict(sorted(omitted.items()))
    report["runs"] = listed
    return report


def print_summary(report: dict) -> None:
    c = report["counts"]
    tgt = report["inputs"]["target"]
    print(f"target      : {tgt['path']}  ({tgt['size']} B, stock_base {tgt['stock_base']})")
    print(f"original    : {report['inputs']['original']['path']}")
    print(f"pre_ext3    : {report['inputs']['pre_ext3']['path']}")
    print(
        f"diff        : {c['diff_bytes']} B in {c['runs']} runs "
        f"| intended {c['intended_bytes']} B / {c['intended_runs']} runs "
        f"| UNINTENDED {c['unintended_bytes']} B / {c['unintended_runs']} runs"
    )

    print("\nper-bank (bank: diff_bytes runs [unintended_bytes])")
    row: List[str] = []
    for bank, b in report["by_bank"].items():
        cell = f"{bank}:{b['bytes']}/{b['runs']}"
        if b["unintended_bytes"]:
            cell += f"[!{b['unintended_bytes']}]"
        row.append(cell)
    for i in range(0, len(row), 6):
        print("  " + "  ".join(row[i : i + 6]))

    print("\nper-category")
    for cat, b in report["by_category"].items():
        flag = " UNINTENDED" if b["unintended_bytes"] else ""
        print(f"  {cat:34s} {b['bytes']:>7} B  {b['runs']:>5} runs{flag}")

    print("\nper-attribution")
    for attr, b in report["by_attribution"].items():
        print(
            f"  {attr:14s} {b['bytes']:>7} B  {b['runs']:>5} runs "
            f"(unintended {b['unintended_bytes']} B)"
        )

    print(
        f"\norig→pre_ext3: {report['original_to_pre_ext3']['diff_bytes']} B   "
        f"pre_ext3→target: {report['pre_ext3_to_target']['diff_bytes']} B"
    )

    d = report["dict_5f_review"]
    print(
        f"5F dict review: {d['bytes']} B / {d['runs']} runs vs original "
        f"(pre→target {d['bytes_changed_pre_to_target']} B); pointers match original "
        f"{d['pointers_match_original']}/{d['pointer_count']} "
        f"(gate >= {d['gate_min_match']}: {'ok' if d['gate_ok'] else 'VIOLATED'}), "
        f"changed pre→target {d['pointers_changed_pre_to_target']}"
    )

    un = report["unintended"]
    if not un:
        print("\nUNINTENDED runs: none")
        return
    print(f"\nUNINTENDED runs ({len(un)}):")
    print(
        f"  {'site':10s} {'abs':>8s} {'len':>4s}  {'orig':22s} {'target':22s} "
        f"{'attr':12s} tool"
    )
    for r in un:
        print(
            f"  {r['site']:10s} {r['abs_target']:>8s} {r['len']:>4d}  "
            f"{r['orig_hex'][:22]:22s} {r['target_hex'][:22]:22s} "
            f"{r['attribution']:12s} {r['attributed_tool']}"
        )
        if r.get("note"):
            print(f"      note: {r['note']}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--jp",
        type=Path,
        default=ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc",
        help="original 8 MiB reference ROM",
    )
    ap.add_argument(
        "--pre",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.pre_ext3.wsc",
        help="pre-ext3 tip",
    )
    ap.add_argument(
        "--target",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
        help="target ROM (8 MiB or 16 MiB)",
    )
    ap.add_argument(
        "--out", type=Path, default=ROOT / "out/patch/stock_noninvasion_report.json"
    )
    ap.add_argument("--tbl", type=Path, default=ROOT / "data/monoeye.tbl")
    ap.add_argument(
        "--hex-cap",
        type=int,
        default=32,
        help="hex bytes kept per side for INTENDED runs (UNINTENDED always full)",
    )
    ap.add_argument(
        "--max-intended-runs",
        type=int,
        default=200,
        help="cap on listed INTENDED runs per category (UNINTENDED always full)",
    )
    ap.add_argument(
        "--all-runs", action="store_true", help="list every run (large report)"
    )
    ap.add_argument("--no-decode", action="store_true", help="skip zstring decode notes")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this tool is read-only")
    for p in (args.jp, args.pre, args.target):
        if not p.exists():
            raise SystemExit(f"missing input ROM: {p}")

    report = run_diff(
        args.jp,
        args.pre,
        args.target,
        tbl_path=args.tbl,
        hex_cap=args.hex_cap,
        decode=not args.no_decode,
        max_per_cat=0 if args.all_runs else args.max_intended_runs,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not args.quiet:
        print_summary(report)
    print(f"\n→ {args.out}")
    print(f"ok={report['ok']} unintended_runs={report['counts']['unintended_runs']} "
          f"unintended_bytes={report['counts']['unintended_bytes']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
