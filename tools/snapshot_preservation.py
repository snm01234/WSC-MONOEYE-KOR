#!/usr/bin/env python3
"""
Preservation snapshot for the ext3 bank/UI corruption fix (Property 2/4/5/6).

READ-ONLY. This tool never opens a .wsc for writing.

Capture mode (default) measures everything that must survive the stock-invasion
repair and stores it in ``out/patch/preservation_snapshot.json``:

* expansion region usage — glyph pool bank ``00``, extended dictionary bank
  ``10``, ext3 banks ``11–1C`` (per-bank used bytes + slot counts + slot sum),
  script spill bank ``30``
* content hashes — whole expansion region ``0x000000–0x7FFFFF`` plus per-bank
  hashes, so a later byte-identity check is one comparison per bank
* approved Hangul UI sites ``75:B6A6/B7C5/B7CD/B7D5/BA40`` (35 B) verbatim
* glyph padding banks ``3F``/``40``/``41`` and code caves ``7A:FFB5+``,
  ``7F:FC4C+``, ``7F:FD10+`` as hashes + lengths
* header fields, ``6D937C`` guard, ``72:0000–17FF`` (with original identity)
* every ``.sav`` size in the workspace
* the existing band-coverage measurement, stored verbatim
* seeded property-style samples: random offset/length ranges over the expansion
  region plus boundary ranges (bank boundaries, cave boundaries, pointer-table
  boundaries). ``pytest``/``hypothesis`` are not installed, so the sampler is
  in-tool with a fixed seed — the documented fallback in the spec.

Compare mode (``--compare snapshot.json``) re-measures the candidate ROM and
diffs it against the stored snapshot. Anything under ``strict`` that differs
fails (exit 1) unless explicitly allowed with ``--allow``; anything under
``advisory`` (rom hash, header checksum, ``5F`` pointer-table samples) is
reported only. Tasks 4.3 / 8.2 / 11.3 call this mode.

Measured values are recorded as measured. Design figures (bank00 13,056 B,
bank10 4,618 B, ext3 slot sum 14,304, bank30 3,017 B) are only compared against
and any mismatch is reported as a note — never substituted for a measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_END,
    DICT_PTR_START,
    ROM_SIZE,
    ROM_SIZE_16MB,
    le16,
    load_rom,
    stock_base,
    ws_header,
)

SCHEMA = 1
EXPANSION_START = 0x000000
EXPANSION_END = 0x7FFFFF
EXPANSION_LEN = ROM_SIZE

# --- expansion region layout (conventions shared with diff_stock_3way.py) ----

GLYPH_POOL_SEG = 0x00           # patch_pad3_expansion PAD3_BANK / PAD3_OFF
GLYPH_RECORD_SIZE = 16          # compact font record
EXT_DICT_SEG = 0x10             # exp_dictionary_meta ext_seg
EXT3_SEG0 = 0x11
EXT3_BANKS = 12                 # ext3_dictionary_meta num_banks
EXT3_SLOTS_PER_BANK = 0x1000
EXT3_PTR_OFF = 0x0000
EXT3_EMPTY_AT = EXT3_SLOTS_PER_BANK * 2   # ptr value meaning "empty slot"
SCRIPT_SPILL_SEG = 0x30

HASHED_EXPANSION_SEGS: Tuple[int, ...] = (
    (GLYPH_POOL_SEG, EXT_DICT_SEG)
    + tuple(range(EXT3_SEG0, EXT3_SEG0 + EXT3_BANKS))
    + (SCRIPT_SPILL_SEG,)
)

# --- design figures (compared against, never substituted) -------------------

DESIGN = {
    "bank00_used_bytes": 13056,
    "bank10_used_bytes": 4618,
    # 16 ext3 banks (0x11-0x20). Grew from 12 banks / 14,304 slots when the
    # opening and every other dialogue band were re-homed off the shared stock
    # 5F dictionary onto private ext3 slots.
    "ext3_slot_sum": 16758,
    "bank30_used_bytes": 3017,
    "ui_sites_total_bytes": 35,
    "save_size": 32768,
    # Exact-match ratio over the five ep3-window bands, measured on the
    # post-restore tip with ext3 wiring. This is the regression gate value; the
    # ≈97.9% in docs/SCRIPT_COVERAGE_STATUS.md is a different (Hangul-ratio,
    # wider band) metric — see COVERAGE_EXT3_BLIND_SPOT.
    "coverage_band_sum_ratio": 0.96664,
}

DESIGN_HEADER = {
    "rom_size_code": 0x09,
    "sram_size_code": 0x02,
    "flags": 0x04,
    "mapper": 0x00,
    "game_id": 0x2F,
    "developer": 0x01,
    "color": 0x01,
    "version": 0x00,
}

# --- preserved stock-space sites (bugfix.md §Preservation Requirements) ------

UI_APPROVED: Tuple[Tuple[int, int], ...] = (
    (0x75B6A6, 7),
    (0x75B7C5, 7),
    (0x75B7CD, 7),
    (0x75B7D5, 6),
    (0x75BA40, 8),
)

GLYPH_PADDING_SEGS: Tuple[int, ...] = (0x3F, 0x40, 0x41)

CAVES: Tuple[Tuple[str, int, int], ...] = (
    ("cave_7A_FFB5", 0x7AFFB5, 0x7AFFFF),
    ("cave_7F_FC4E", 0x7FFC4E, 0x7FFD0F),
    ("cave_7F_FD10", 0x7FFD10, 0x7FFFEF),
)

JAGD_GUARD_LOGICAL = 0x6D937C
JAGD_GUARD_EXPECT = "3fa660"

TITLE_GFX_LOGICAL = 0x720000
TITLE_GFX_LEN = 0x1800  # 72:0000–17FF

DICT_PTR_TABLE_LOGICAL = (0x5F0000 + DICT_PTR_START, 0x5F0000 + DICT_PTR_END)

HEADER_STRICT_FIELDS = (
    "rom_size_code",
    "sram_size_code",
    "flags",
    "mapper",
    "game_id",
    "developer",
    "color",
    "version",
)

DEFAULT_ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_SNAPSHOT = ROOT / "out/patch/preservation_snapshot.json"
DEFAULT_COVERAGE_OUT = ROOT / "out/patch/preservation_coverage_measure.json"

# --- sampler settings -------------------------------------------------------

COVERAGE_EXT3_BLIND_SPOT = (
    "measure_band_coverage.py reports EXACT-match coverage over the five ep3-window "
    "bands of out/script/translations_ep3_window.json (opening .. bank62, 8,244 quality "
    "lines). Measured history on this lineage: 87.74% before the stock-invasion repair, "
    "87.14% after the shared 5F pointers were handed back to the original table (the "
    "dialogue that had hijacked them reverted to Japanese), then 96.66% once those lines "
    "were re-homed onto private ext3 slots. Two earlier numbers are not comparable to "
    "these: the ≈97.9% in docs/SCRIPT_COVERAGE_STATUS.md is a Hangul-ratio over the wider "
    "apply-sheet band 6040A5-69FFFF, and the 4.7% this tool once printed was an artifact "
    "of it having no ext3 wiring, so every E5 18 xx yy token expanded to <BADDICT:…> and "
    "counted as Japanese (fixed by apply_ext_dict_unit.attach_ext3)."
)

DEFAULT_SEED = 20250216
DEFAULT_RANDOM_SAMPLES = 256
SAMPLE_MAX_LEN = 4096
BOUNDARY_HALO = 8


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hex(value: int, width: int = 6) -> str:
    return f"{value:0{width}X}"


# --- measurement primitives -------------------------------------------------


def expansion_bank(rom: bytes, seg: int) -> bytes:
    start = seg * BANK_SIZE
    return rom[start : start + BANK_SIZE]


def used_bytes(bank: bytes) -> int:
    """Bytes written into a freshly-FF expansion bank."""
    return sum(1 for b in bank if b != 0xFF)


def glyph_pool_stats(rom: bytes) -> dict:
    bank = expansion_bank(rom, GLYPH_POOL_SEG)
    records = BANK_SIZE // GLYPH_RECORD_SIZE
    occupied = 0
    last = -1
    for i in range(records):
        rec = bank[i * GLYPH_RECORD_SIZE : (i + 1) * GLYPH_RECORD_SIZE]
        if not all(b == 0xFF for b in rec):
            occupied += 1
            last = i
    return {
        "seg": _hex(GLYPH_POOL_SEG, 2),
        "used_bytes": used_bytes(bank),
        "glyph_records_occupied": occupied,
        "last_occupied_record": last,
        "record_size": GLYPH_RECORD_SIZE,
        "sha256": sha(bank),
    }


def ext_dict_stats(rom: bytes, meta: dict | None) -> dict:
    bank = expansion_bank(rom, EXT_DICT_SEG)
    slot_count = int((meta or {}).get("slot_count") or 0)
    ptr_off = int(str((meta or {}).get("ext_ptr_off") or "0"), 16)
    used_slots = 0
    empty_slots = 0
    ptrs: List[int] = []
    for i in range(slot_count):
        p = le16(bank, ptr_off + i * 2)
        ptrs.append(p)
        if p >= BANK_SIZE or bank[p] == 0:
            empty_slots += 1
        else:
            used_slots += 1
    return {
        "seg": _hex(EXT_DICT_SEG, 2),
        "used_bytes": used_bytes(bank),
        "ptr_off": _hex(ptr_off, 4),
        "slot_count": slot_count,
        "used_slots": used_slots,
        "empty_slots": empty_slots,
        "ptr_table_sha256": sha(bank[ptr_off : ptr_off + slot_count * 2]),
        "sha256": sha(bank),
    }


def ext3_stats(rom: bytes, num_banks: int) -> dict:
    per_bank: List[dict] = []
    slot_sum = 0
    for bi in range(num_banks):
        seg = EXT3_SEG0 + bi
        bank = expansion_bank(rom, seg)
        used_slots = 0
        empty_slots = 0
        phrase_end = EXT3_EMPTY_AT + 1
        for local in range(EXT3_SLOTS_PER_BANK):
            p = le16(bank, EXT3_PTR_OFF + local * 2)
            if p == EXT3_EMPTY_AT or p >= BANK_SIZE or bank[p] == 0:
                empty_slots += 1
                continue
            used_slots += 1
            end = p
            while end < BANK_SIZE and bank[end] != 0:
                end += 1
            phrase_end = max(phrase_end, end + 1)
        ub = used_bytes(bank)
        slot_sum += used_slots
        per_bank.append(
            {
                "seg": _hex(seg, 2),
                "used_bytes": ub,
                "used_pct_of_bank": round(ub / BANK_SIZE, 5),
                "used_slots": used_slots,
                "empty_slots": empty_slots,
                "phrase_end": _hex(phrase_end, 4),
                "ptr_table_sha256": sha(
                    bank[EXT3_PTR_OFF : EXT3_PTR_OFF + EXT3_SLOTS_PER_BANK * 2]
                ),
                "sha256": sha(bank),
            }
        )
    return {
        "seg0": _hex(EXT3_SEG0, 2),
        "num_banks": num_banks,
        "slots_per_bank": EXT3_SLOTS_PER_BANK,
        "slot_sum": slot_sum,
        "slot_capacity": num_banks * EXT3_SLOTS_PER_BANK,
        "used_bytes_total": sum(b["used_bytes"] for b in per_bank),
        "per_bank": per_bank,
    }


def script_spill_stats(rom: bytes) -> dict:
    bank = expansion_bank(rom, SCRIPT_SPILL_SEG)
    return {
        "seg": _hex(SCRIPT_SPILL_SEG, 2),
        "used_bytes": used_bytes(bank),
        "sha256": sha(bank),
    }


def expansion_section(rom: bytes, exp_meta: dict | None, ext3_meta: dict | None) -> dict:
    num_banks = int((ext3_meta or {}).get("num_banks") or EXT3_BANKS)
    region = rom[EXPANSION_START : EXPANSION_END + 1]
    banks = {
        _hex(seg, 2): sha(expansion_bank(rom, seg)) for seg in HASHED_EXPANSION_SEGS
    }
    return {
        "region": {
            "start": _hex(EXPANSION_START),
            "end": _hex(EXPANSION_END),
            "len": len(region),
            "sha256": sha(region),
        },
        "bank_sha256": banks,
        "bank00_glyph_pool": glyph_pool_stats(rom),
        "bank10_ext_dict": ext_dict_stats(rom, exp_meta),
        "ext3_bank11_1C": ext3_stats(rom, num_banks),
        "bank30_script_spill": script_spill_stats(rom),
    }


def ui_sites_section(rom: bytes) -> dict:
    sb = stock_base(rom)
    sites = []
    total = 0
    for logical, length in UI_APPROVED:
        data = rom[sb + logical : sb + logical + length]
        total += length
        sites.append(
            {
                "site": f"{logical >> 16:02X}:{logical & 0xFFFF:04X}",
                "logical": _hex(logical),
                "len": length,
                "hex": data.hex(),
            }
        )
    return {"sites": sites, "total_bytes": total}


def glyph_padding_section(rom: bytes, jp: bytes | None) -> dict:
    sb = stock_base(rom)
    out: List[dict] = []
    for seg in GLYPH_PADDING_SEGS:
        start = sb + seg * BANK_SIZE
        data = rom[start : start + BANK_SIZE]
        entry = {
            "seg": _hex(seg, 2),
            "len": len(data),
            "sha256": sha(data),
            "non_ff_bytes": used_bytes(data),
        }
        if jp is not None:
            jsb = stock_base(jp)
            jdata = jp[jsb + seg * BANK_SIZE : jsb + (seg + 1) * BANK_SIZE]
            pad_idx = [i for i, b in enumerate(jdata) if b == 0xFF]
            entry["original_ff_bytes"] = len(pad_idx)
            entry["original_ff_subset_sha256"] = sha(bytes(data[i] for i in pad_idx))
            entry["original_nonff_subset_identical"] = all(
                data[i] == jdata[i] for i in range(BANK_SIZE) if jdata[i] != 0xFF
            )
        out.append(entry)
    return {"banks": out}


def caves_section(rom: bytes) -> dict:
    sb = stock_base(rom)
    out = []
    for name, lo, hi in CAVES:
        data = rom[sb + lo : sb + hi + 1]
        out.append(
            {
                "name": name,
                "start": _hex(lo),
                "end": _hex(hi),
                "len": len(data),
                "sha256": sha(data),
                "head_hex": data[:16].hex(),
                "non_ff_bytes": used_bytes(data),
            }
        )
    return {"caves": out}


def header_section(rom: bytes) -> Tuple[dict, dict]:
    h = ws_header(rom)
    strict = {k: _hex(h[k], 2) for k in HEADER_STRICT_FIELDS}
    strict["maintenance"] = _hex(h["maintenance"], 2)
    advisory = {"checksum": _hex(h["checksum"], 4)}
    return strict, advisory


def jagd_section(rom: bytes) -> dict:
    sb = stock_base(rom)
    data = rom[sb + JAGD_GUARD_LOGICAL : sb + JAGD_GUARD_LOGICAL + 3]
    return {
        "logical": _hex(JAGD_GUARD_LOGICAL),
        "hex": data.hex(),
        "expected": JAGD_GUARD_EXPECT,
        "ok": data.hex() == JAGD_GUARD_EXPECT,
    }


def title_gfx_section(rom: bytes, jp: bytes | None) -> dict:
    sb = stock_base(rom)
    data = rom[sb + TITLE_GFX_LOGICAL : sb + TITLE_GFX_LOGICAL + TITLE_GFX_LEN]
    out = {
        "range": "72:0000-17FF",
        "logical": _hex(TITLE_GFX_LOGICAL),
        "len": len(data),
        "sha256": sha(data),
    }
    if jp is not None:
        jsb = stock_base(jp)
        jdata = jp[jsb + TITLE_GFX_LOGICAL : jsb + TITLE_GFX_LOGICAL + TITLE_GFX_LEN]
        out["identical_to_original"] = data == jdata
        out["original_sha256"] = sha(jdata)
    return out


def saves_section(root: Path) -> dict:
    entries: List[dict] = []
    for p in sorted(root.rglob("*.sav")):
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover
            rel = str(p)
        entries.append({"path": rel, "size": p.stat().st_size})
    sizes = {e["size"] for e in entries}
    return {
        "count": len(entries),
        "all_expected_size": sizes == {DESIGN["save_size"]} if entries else False,
        "expected_size": DESIGN["save_size"],
        "distinct_sizes": sorted(sizes),
        "files": entries,
    }


# --- band coverage (delegated to the existing measurement) -------------------


def run_band_coverage(rom: Path, out_json: Path, *, skip: bool) -> dict:
    """Invoke tools/measure_band_coverage.py and keep its result verbatim."""
    if skip:
        return {"status": "skipped", "reason": "--skip-coverage"}
    cmd = [
        sys.executable,
        str(ROOT / "tools/measure_band_coverage.py"),
        "--rom",
        str(rom),
        "--out",
        str(out_json),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    result: dict = {
        "status": "ok" if proc.returncode == 0 else "failed",
        "command": ["python", "tools/measure_band_coverage.py", "--rom", rom.name],
        "returncode": proc.returncode,
        "out_json": str(out_json.relative_to(ROOT)) if out_json.is_relative_to(ROOT)
        else str(out_json),
        "stdout": proc.stdout.strip().splitlines(),
    }
    if proc.returncode != 0:
        result["stderr"] = proc.stderr.strip().splitlines()[-20:]
        return result
    if out_json.exists():
        result["result"] = json.loads(out_json.read_text(encoding="utf-8"))
    return result


def coverage_band_summary(coverage: dict) -> dict:
    """Band ok/quality totals pulled out of the verbatim measurement."""
    res = coverage.get("result") or {}
    bands = res.get("bands") or {}
    ok = sum(int(b.get("ok") or 0) for b in bands.values())
    quality = sum(int(b.get("quality") or 0) for b in bands.values())
    return {
        "bands": {
            name: {
                "ok": b.get("ok"),
                "quality": b.get("quality"),
                "ratio": b.get("ratio"),
            }
            for name, b in sorted(bands.items())
        },
        "total_ok": ok,
        "total_quality": quality,
        "total_ratio": round(ok / quality, 5) if quality else None,
        "bank30_nonzero": res.get("bank30_nonzero"),
    }


# --- seeded property-style sampler ------------------------------------------


def _clip_range(start: int, length: int, lo: int, hi: int) -> Tuple[int, int] | None:
    """Clip [start, start+length) into [lo, hi]; None when empty."""
    s = max(start, lo)
    e = min(start + length, hi + 1)
    if e <= s:
        return None
    return s, e - s


def build_samples(
    *, seed: int, count: int, num_banks: int
) -> Tuple[List[dict], List[dict]]:
    """Deterministic (strict, advisory) sample range lists.

    ``space`` is ``file`` for expansion-region ranges (absolute file offset in a
    16 MiB image) and ``stock`` for logical stock ranges resolved through
    ``stock_base(rom)``, so stock samples still work against an 8 MiB candidate.
    """
    rng = random.Random(seed)
    strict: List[dict] = []
    advisory: List[dict] = []

    def add(
        bucket: List[dict],
        sid: str,
        domain: str,
        space: str,
        start: int,
        length: int,
    ) -> None:
        bucket.append(
            {
                "id": sid,
                "domain": domain,
                "space": space,
                "start": _hex(start, 7 if space == "file" else 6),
                "len": length,
            }
        )

    # (1) random ranges over the whole expansion region
    for i in range(count):
        length = rng.randint(1, SAMPLE_MAX_LEN)
        start = rng.randint(EXPANSION_START, EXPANSION_END - length + 1)
        add(strict, f"rand_exp_{i:04d}", "expansion_region", "file", start, length)

    # (2) denser random ranges inside the payload banks we care about
    payload_segs = (
        [GLYPH_POOL_SEG, EXT_DICT_SEG]
        + list(range(EXT3_SEG0, EXT3_SEG0 + num_banks))
        + [SCRIPT_SPILL_SEG]
    )
    for seg in payload_segs:
        for i in range(8):
            length = rng.randint(1, SAMPLE_MAX_LEN)
            start = seg * BANK_SIZE + rng.randint(0, BANK_SIZE - length)
            add(
                strict,
                f"rand_bank{seg:02X}_{i}",
                f"expansion_bank_{seg:02X}",
                "file",
                start,
                length,
            )

    # (3) boundary values — expansion bank boundaries (straddling)
    for seg in range(0x00, 0x80):
        base = seg * BANK_SIZE
        rc = _clip_range(base - BOUNDARY_HALO, BOUNDARY_HALO * 2, EXPANSION_START, EXPANSION_END)
        if rc:
            add(strict, f"bnd_bank{seg:02X}_lo", "bank_boundary", "file", *rc)
        rc = _clip_range(
            base + BANK_SIZE - BOUNDARY_HALO, BOUNDARY_HALO * 2, EXPANSION_START, EXPANSION_END
        )
        if rc:
            add(strict, f"bnd_bank{seg:02X}_hi", "bank_boundary", "file", *rc)

    # (4) boundary values — ext3 pointer-table / phrase-region seam per bank
    for bi in range(num_banks):
        seg = EXT3_SEG0 + bi
        base = seg * BANK_SIZE + EXT3_EMPTY_AT
        rc = _clip_range(base - BOUNDARY_HALO, BOUNDARY_HALO * 2, EXPANSION_START, EXPANSION_END)
        if rc:
            add(strict, f"bnd_ext3_{seg:02X}_seam", "ext3_ptr_seam", "file", *rc)

    # (5) boundary values — code caves (stock space)
    for name, lo, hi in CAVES:
        add(strict, f"bnd_{name}_lo", "cave_boundary", "stock", lo - BOUNDARY_HALO, BOUNDARY_HALO * 2)
        add(strict, f"bnd_{name}_hi", "cave_boundary", "stock", hi - BOUNDARY_HALO + 1, BOUNDARY_HALO * 2)
        add(strict, f"body_{name}", "cave_body", "stock", lo, hi - lo + 1)

    # (6) approved Hangul UI sites (stock space) — must stay Hangul, not revert
    for logical, length in UI_APPROVED:
        add(
            strict,
            f"ui_{logical:06X}",
            "ui_approved",
            "stock",
            logical - BOUNDARY_HALO,
            length + BOUNDARY_HALO * 2,
        )

    # (7) boundary values — dictionary pointer table (advisory: task 5 restores
    #     individual 5F pointers on purpose, so a diff here is not a failure)
    lo, hi = DICT_PTR_TABLE_LOGICAL
    advisory.append(
        {
            "id": "bnd_dict_ptr_lo",
            "domain": "dict_ptr_boundary",
            "space": "stock",
            "start": _hex(lo - BOUNDARY_HALO),
            "len": BOUNDARY_HALO * 2,
        }
    )
    advisory.append(
        {
            "id": "bnd_dict_ptr_hi",
            "domain": "dict_ptr_boundary",
            "space": "stock",
            "start": _hex(hi - BOUNDARY_HALO + 1),
            "len": BOUNDARY_HALO * 2,
        }
    )
    return strict, advisory


def resolve_sample(rom: bytes, sample: dict) -> Tuple[int, int]:
    start = int(sample["start"], 16)
    length = int(sample["len"])
    if sample["space"] == "stock":
        start += stock_base(rom)
    return start, length


def hash_samples(rom: bytes, samples: Sequence[dict]) -> List[dict]:
    out: List[dict] = []
    for s in samples:
        start, length = resolve_sample(rom, s)
        data = rom[start : start + length]
        rec = dict(s)
        rec["sha256"] = sha(data)
        rec["read_len"] = len(data)
        out.append(rec)
    return out


def sample_first_diff(rom: bytes, sample: dict, ref: bytes | None) -> dict | None:
    """Cheap counterexample shrink: first/last differing byte inside a range."""
    if ref is None:
        return None
    start, length = resolve_sample(rom, sample)
    rstart, _ = resolve_sample(ref, sample)
    a = rom[start : start + length]
    b = ref[rstart : rstart + length]
    diffs = [i for i in range(min(len(a), len(b))) if a[i] != b[i]]
    if not diffs:
        return None
    return {
        "first_diff_at": f"+{diffs[0]}",
        "last_diff_at": f"+{diffs[-1]}",
        "diff_bytes": len(diffs),
        "candidate_hex": a[diffs[0] : diffs[0] + 8].hex(),
        "baseline_hex": b[diffs[0] : diffs[0] + 8].hex(),
    }


# --- snapshot assembly ------------------------------------------------------


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def design_notes(snap: dict) -> List[str]:
    """Compare measured values against the design figures; note mismatches."""
    notes: List[str] = []
    s = snap["strict"]
    exp = s["expansion"]
    checks = (
        ("bank00 used bytes", exp["bank00_glyph_pool"]["used_bytes"], DESIGN["bank00_used_bytes"]),
        ("bank10 used bytes", exp["bank10_ext_dict"]["used_bytes"], DESIGN["bank10_used_bytes"]),
        ("ext3 slot sum", exp["ext3_bank11_1C"]["slot_sum"], DESIGN["ext3_slot_sum"]),
        ("bank30 used bytes", exp["bank30_script_spill"]["used_bytes"], DESIGN["bank30_used_bytes"]),
        ("approved UI bytes", s["ui_approved"]["total_bytes"], DESIGN["ui_sites_total_bytes"]),
    )
    for name, got, want in checks:
        if got != want:
            notes.append(f"design mismatch: {name} measured {got}, design says {want}")
    for field, want in DESIGN_HEADER.items():
        got = s["header"].get(field)
        if got != f"{want:02X}":
            notes.append(
                f"design mismatch: header {field} measured {got}, design says {want:02X}"
            )
    if not s["jagd_guard"]["ok"]:
        notes.append(
            f"design mismatch: 6D937C measured {s['jagd_guard']['hex']}, "
            f"design says {JAGD_GUARD_EXPECT}"
        )
    title = s["title_graphics"]
    if title.get("identical_to_original") is False:
        notes.append("design mismatch: 72:0000-17FF differs from the original ROM")
    saves = s["saves"]
    if not saves["all_expected_size"]:
        notes.append(
            f"design mismatch: .sav sizes {saves['distinct_sizes']}, "
            f"design says {DESIGN['save_size']} for all"
        )
    cov = s.get("coverage_bands") or {}
    ratio = cov.get("total_ratio")
    if ratio is not None and abs(ratio - DESIGN["coverage_band_sum_ratio"]) > 0.005:
        notes.append(
            f"design mismatch: band coverage sum measured {ratio:.3%}, "
            f"design says ≈{DESIGN['coverage_band_sum_ratio']:.1%}"
        )
        notes.append(COVERAGE_EXT3_BLIND_SPOT)
    if not notes:
        notes.append("all measured values match the design figures")
    return notes


def capture(
    rom_path: Path,
    *,
    jp_path: Path | None,
    seed: int,
    samples: int,
    skip_coverage: bool,
    coverage_out: Path,
) -> dict:
    rom = bytes(load_rom(rom_path))
    jp = bytes(jp_path.read_bytes()) if jp_path and jp_path.exists() else None
    exp_meta = read_json(ROOT / "out/patch/exp_dictionary_meta.json")
    ext3_meta = read_json(ROOT / "out/patch/ext3_dictionary_meta.json")
    num_banks = int((ext3_meta or {}).get("num_banks") or EXT3_BANKS)

    if len(rom) != ROM_SIZE_16MB:
        raise SystemExit(
            f"expansion-region snapshot needs a 16 MiB ROM, got {len(rom):#x}"
        )

    header_strict, header_advisory = header_section(rom)
    coverage = run_band_coverage(rom_path, coverage_out, skip=skip_coverage)
    strict_samples, advisory_samples = build_samples(
        seed=seed, count=samples, num_banks=num_banks
    )

    snap = {
        "schema": SCHEMA,
        "meta": {
            "generated_by": "tools/snapshot_preservation.py",
            "read_only": True,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "rom": {
                "path": str(rom_path),
                "size": len(rom),
                "stock_base": _hex(stock_base(rom), 7),
            },
            "original": {"path": str(jp_path) if jp else None,
                         "size": len(jp) if jp else None},
            "ext3_meta": {
                "num_banks": num_banks,
                "applied_uniques": (ext3_meta or {}).get("applied_uniques"),
                "applied_sites": (ext3_meta or {}).get("applied_sites"),
            },
            "sampler": {
                "seed": seed,
                "random_samples": samples,
                "max_sample_len": SAMPLE_MAX_LEN,
                "boundary_halo": BOUNDARY_HALO,
                "note": "pytest/hypothesis not installed — seeded in-tool sampler "
                "(documented spec fallback)",
            },
            "design_figures": DESIGN | {"header": DESIGN_HEADER},
        },
        "strict": {
            "rom_size": len(rom),
            "expansion": expansion_section(rom, exp_meta, ext3_meta),
            "ui_approved": ui_sites_section(rom),
            "glyph_padding": glyph_padding_section(rom, jp),
            "caves": caves_section(rom),
            "header": header_strict,
            "jagd_guard": jagd_section(rom),
            "title_graphics": title_gfx_section(rom, jp),
            "saves": saves_section(ROOT),
            "coverage_bands": coverage_band_summary(coverage),
            "samples": hash_samples(rom, strict_samples),
        },
        "advisory": {
            "rom_sha256": sha(rom),
            "header_checksum": header_advisory["checksum"],
            "coverage_measurement": coverage,
            "samples": hash_samples(rom, advisory_samples),
        },
    }
    snap["meta"]["design_notes"] = design_notes(snap)
    return snap


# --- compare mode -----------------------------------------------------------


def flatten(obj, prefix: str = "") -> Dict[str, object]:
    out: Dict[str, object] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = None
            if isinstance(v, dict):
                for id_field in ("id", "site", "name", "seg", "path"):
                    if id_field in v:
                        key = str(v[id_field])
                        break
            out.update(flatten(v, f"{prefix}[{key if key else i}]"))
    else:
        out[prefix] = obj
    return out


def diff_flat(
    baseline: Dict[str, object], candidate: Dict[str, object]
) -> List[dict]:
    diffs: List[dict] = []
    for key in sorted(set(baseline) | set(candidate)):
        b = baseline.get(key, "<absent>")
        c = candidate.get(key, "<absent>")
        if b != c:
            diffs.append({"key": key, "baseline": b, "candidate": c})
    return diffs


def compare(
    snapshot_path: Path,
    rom_path: Path,
    *,
    jp_path: Path | None,
    allow: Iterable[str],
    skip_coverage: bool,
    coverage_out: Path,
) -> dict:
    baseline = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if baseline.get("schema") != SCHEMA:
        raise SystemExit(
            f"snapshot schema {baseline.get('schema')} != {SCHEMA}; recapture needed"
        )
    seed = int(baseline["meta"]["sampler"]["seed"])
    samples = int(baseline["meta"]["sampler"]["random_samples"])
    candidate = capture(
        rom_path,
        jp_path=jp_path,
        seed=seed,
        samples=samples,
        skip_coverage=skip_coverage,
        coverage_out=coverage_out,
    )

    strict_diffs = diff_flat(flatten(baseline["strict"]), flatten(candidate["strict"]))
    advisory_diffs = diff_flat(
        flatten({k: v for k, v in baseline["advisory"].items() if k != "coverage_measurement"}),
        flatten({k: v for k, v in candidate["advisory"].items() if k != "coverage_measurement"}),
    )

    allow_list = list(allow)

    def allowed(key: str) -> str | None:
        for a in allow_list:
            if key == a or key.startswith(a):
                return a
        return None

    failing: List[dict] = []
    allowed_diffs: List[dict] = []
    for d in strict_diffs:
        rule = allowed(d["key"])
        if rule:
            allowed_diffs.append(d | {"allowed_by": rule})
        else:
            failing.append(d)

    # Cheap shrink for failing sample ranges: locate the differing bytes.
    rom = bytes(load_rom(rom_path))
    base_rom_path = Path(baseline["meta"]["rom"]["path"])
    ref = None
    if base_rom_path.exists() and base_rom_path.resolve() != rom_path.resolve():
        try:
            ref = bytes(base_rom_path.read_bytes())
        except OSError:  # pragma: no cover
            ref = None
    by_id = {s["id"]: s for s in baseline["strict"]["samples"]}
    for d in failing:
        key = d["key"]
        if not key.startswith("samples[") or not key.endswith(".sha256"):
            continue
        sid = key[len("samples[") : key.index("]")]
        s = by_id.get(sid)
        if s is None:
            continue
        d["sample"] = {k: s[k] for k in ("domain", "space", "start", "len")}
        shrink = sample_first_diff(rom, s, ref)
        if shrink:
            d["shrink"] = shrink

    return {
        "ok": not failing,
        "generated_by": "tools/snapshot_preservation.py --compare",
        "read_only": True,
        "snapshot": str(snapshot_path),
        "candidate_rom": str(rom_path),
        "baseline_rom": str(base_rom_path),
        "allow": allow_list,
        "counts": {
            "strict_diffs": len(strict_diffs),
            "strict_failing": len(failing),
            "strict_allowed": len(allowed_diffs),
            "advisory_diffs": len(advisory_diffs),
            "strict_keys_compared": len(flatten(baseline["strict"])),
        },
        "failing": failing,
        "allowed": allowed_diffs,
        "advisory": advisory_diffs,
        "candidate_design_notes": candidate["meta"]["design_notes"],
    }


# --- reporting --------------------------------------------------------------


def print_capture(snap: dict) -> None:
    s = snap["strict"]
    exp = s["expansion"]
    m = snap["meta"]
    print(f"rom          : {m['rom']['path']}  ({m['rom']['size']} B, "
          f"stock_base {m['rom']['stock_base']})")
    print(f"expansion    : {exp['region']['start']}–{exp['region']['end']} "
          f"sha256 {exp['region']['sha256'][:16]}…")
    print("\nexpansion usage (measured / design)")
    print(f"  bank00 glyph pool   {exp['bank00_glyph_pool']['used_bytes']:>7} B "
          f"/ {DESIGN['bank00_used_bytes']} B   "
          f"records {exp['bank00_glyph_pool']['glyph_records_occupied']}")
    b10 = exp["bank10_ext_dict"]
    print(f"  bank10 ext dict     {b10['used_bytes']:>7} B / "
          f"{DESIGN['bank10_used_bytes']} B   slots {b10['used_slots']}/{b10['slot_count']}")
    e3 = exp["ext3_bank11_1C"]
    print(f"  ext3 11–1C          {e3['used_bytes_total']:>7} B          "
          f"slot sum {e3['slot_sum']} / design {DESIGN['ext3_slot_sum']} "
          f"(capacity {e3['slot_capacity']})")
    for b in e3["per_bank"]:
        print(f"    {b['seg']}  used {b['used_bytes']:>5} B ({b['used_pct_of_bank']:.3%})  "
              f"slots {b['used_slots']:>5}  phrase_end {b['phrase_end']}")
    b30 = exp["bank30_script_spill"]
    print(f"  bank30 script spill {b30['used_bytes']:>7} B / {DESIGN['bank30_used_bytes']} B")

    print(f"\napproved Hangul UI ({s['ui_approved']['total_bytes']} B)")
    for site in s["ui_approved"]["sites"]:
        print(f"  {site['site']}  len {site['len']}  {site['hex']}")

    print("\nglyph padding banks")
    for b in s["glyph_padding"]["banks"]:
        extra = ""
        if "original_nonff_subset_identical" in b:
            extra = (f"  orig FF {b['original_ff_bytes']} B  "
                     f"orig-data untouched={b['original_nonff_subset_identical']}")
        print(f"  {b['seg']}  non-FF {b['non_ff_bytes']:>6} B  "
              f"sha {b['sha256'][:12]}…{extra}")

    print("\ncode caves")
    for c in s["caves"]["caves"]:
        print(f"  {c['name']:14s} {c['start']}–{c['end']}  len {c['len']:>4}  "
              f"non-FF {c['non_ff_bytes']:>4}  sha {c['sha256'][:12]}…")

    print("\nheader")
    print("  " + "  ".join(f"{k}={v}" for k, v in s["header"].items()))
    print(f"  checksum={snap['advisory']['header_checksum']} (advisory — updated on repair)")

    j = s["jagd_guard"]
    print(f"\n6D937C       : {j['hex']} (expect {j['expected']}) ok={j['ok']}")
    t = s["title_graphics"]
    print(f"72:0000-17FF : sha {t['sha256'][:16]}… "
          f"identical_to_original={t.get('identical_to_original')}")
    sv = s["saves"]
    print(f"saves        : {sv['count']} files, sizes {sv['distinct_sizes']} "
          f"all_expected={sv['all_expected_size']}")

    cov = s["coverage_bands"]
    if cov.get("bands"):
        print("\nband coverage (verbatim from measure_band_coverage.py)")
        for name, b in cov["bands"].items():
            print(f"  {name:12s} ok={b['ok']:>4}/{b['quality']:<4} ratio={b['ratio']}")
        print(f"  {'TOTAL':12s} ok={cov['total_ok']:>4}/{cov['total_quality']:<4} "
              f"ratio={cov['total_ratio']:.3%}  bank30_nonzero={cov['bank30_nonzero']}")
    else:
        print(f"\nband coverage: {snap['advisory']['coverage_measurement'].get('status')}")

    print(f"\nsamples      : strict {len(s['samples'])} ranges, "
          f"advisory {len(snap['advisory']['samples'])} ranges "
          f"(seed {snap['meta']['sampler']['seed']})")
    print("design check :")
    for note in snap["meta"]["design_notes"]:
        print(f"  - {note}")


def print_compare(rep: dict) -> None:
    c = rep["counts"]
    print(f"snapshot     : {rep['snapshot']}")
    print(f"candidate    : {rep['candidate_rom']}")
    print(f"baseline rom : {rep['baseline_rom']}")
    print(f"strict keys  : {c['strict_keys_compared']} compared")
    print(f"strict diffs : {c['strict_diffs']} "
          f"(failing {c['strict_failing']}, allowed {c['strict_allowed']})")
    print(f"advisory     : {c['advisory_diffs']} diffs (reported only)")
    for d in rep["allowed"][:40]:
        print(f"  allowed  {d['key']}: {d['baseline']} → {d['candidate']} "
              f"[{d['allowed_by']}]")
    for d in rep["advisory"][:40]:
        print(f"  advisory {d['key']}: {d['baseline']} → {d['candidate']}")
    if rep["failing"]:
        print(f"\nFAILING strict diffs ({len(rep['failing'])}):")
        for d in rep["failing"][:80]:
            print(f"  {d['key']}")
            print(f"      baseline : {d['baseline']}")
            print(f"      candidate: {d['candidate']}")
            if d.get("sample"):
                s = d["sample"]
                print(f"      range    : {s['space']} {s['start']} len {s['len']} "
                      f"({s['domain']})")
            if d.get("shrink"):
                sh = d["shrink"]
                print(f"      shrink   : {sh['first_diff_at']}..{sh['last_diff_at']} "
                      f"({sh['diff_bytes']} B) base {sh['baseline_hex']} "
                      f"→ cand {sh['candidate_hex']}")
        if len(rep["failing"]) > 80:
            print(f"  … {len(rep['failing']) - 80} more")
    print("\ncandidate design check:")
    for note in rep["candidate_design_notes"]:
        print(f"  - {note}")
    print(f"\nok={rep['ok']}")


# --- main -------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=DEFAULT_ROM, help="target ROM (16 MiB tip)")
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP, help="original 8 MiB reference")
    ap.add_argument("--out", type=Path, default=DEFAULT_SNAPSHOT)
    ap.add_argument(
        "--compare",
        type=Path,
        default=None,
        help="re-measure and diff against a stored snapshot (exit 1 on strict diff)",
    )
    ap.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="KEY_PREFIX",
        help="strict key (or prefix) whose difference is explicitly allowed",
    )
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--samples", type=int, default=DEFAULT_RANDOM_SAMPLES)
    ap.add_argument("--skip-coverage", action="store_true")
    ap.add_argument("--coverage-out", type=Path, default=DEFAULT_COVERAGE_OUT)
    ap.add_argument(
        "--compare-out",
        type=Path,
        default=ROOT / "out/patch/preservation_compare_report.json",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    for p in (args.out, args.compare_out, args.coverage_out):
        if p.suffix.lower() == ".wsc":
            raise SystemExit("refusing to write a .wsc — this tool is read-only")
    if not args.rom.exists():
        raise SystemExit(f"missing ROM: {args.rom}")

    if args.compare:
        if not args.compare.exists():
            raise SystemExit(f"missing snapshot: {args.compare}")
        rep = compare(
            args.compare,
            args.rom,
            jp_path=args.jp,
            allow=args.allow,
            skip_coverage=args.skip_coverage,
            coverage_out=args.coverage_out,
        )
        args.compare_out.parent.mkdir(parents=True, exist_ok=True)
        args.compare_out.write_text(
            json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if not args.quiet:
            print_compare(rep)
        print(f"\n→ {args.compare_out}")
        return 0 if rep["ok"] else 1

    snap = capture(
        args.rom,
        jp_path=args.jp,
        seed=args.seed,
        samples=args.samples,
        skip_coverage=args.skip_coverage,
        coverage_out=args.coverage_out,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not args.quiet:
        print_capture(snap)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
