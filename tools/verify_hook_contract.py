#!/usr/bin/env python3
"""
Static hook-contract gate for the ext3 / Hangul render hooks (req 2.2, 2.6).

READ-ONLY. This tool never opens a .wsc for writing.

Three checks, all static — no emulator involved:

1. leaf cave (``7F:FD10+``, ``patch_3byte_dict_token.build_handlers`` leaf part)
   * push / pop symmetry per execution path (the reproduced stock prologue
     ``55 8B EC 83 EC 08`` is excluded and reported separately — its ``pop bp``
     legitimately lives in the stock epilogue)
   * an explicit bank restore inside the cave: a ``DEB5`` remap call that runs
     *after* the phrase-pointer fetch, not only the one that maps the expansion
     bank in
   * no delegation of the restore to stock code: leaving through a far jmp into
     the stock stream with an unbalanced stack means the stock ``pop ax`` +
     ``DEB5`` pair is doing the cave's job (bugfix.md B4)
   Decoding is a conservative linear length-decoder; an unknown opcode fails the
   check rather than being skipped (fail-closed).

2. marker code must not occur in the original stock space. The gate metric is the
   text/data bank scan (``50–5F``, ``75``, ``76``) — the banks the shared render
   hook actually walks — and the whole ``00–7F`` count is reported alongside.

3. hook WRAM addresses (``19FF``, ``19FA``, ``19F8``, ``1A6E``) must not appear as
   operands in stock code banks ``70–7F``. Nonzero is a failure, but each address
   carries a verdict that separates a *candidate* operand-scan hit (a byte pair
   that may just be data or a displacement — only an emulator write watchpoint
   can confirm) from *confirmed* stock usage.

Report: ``out/patch/hook_contract_report.json``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, stock_base  # noqa: E402
from patch_3byte_dict_token import (  # noqa: E402
    BANK_MAP_OFF,
    BANK_SAVE_OFF,
    CAVE3,
    CAVE3_MAX,
    FAD0_OFF,
    LEAF,
    LEAF_CONTINUE,
    LEAF_EXPECT,
    LEAF_STREAM,
    MAGIC,
    WRAM_FLAG,
    WRAM_INDEX,
)
from patch_font_hangul_hook import STORE_SITE, TAG_FLAG  # noqa: E402

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/hook_contract_report.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
CHAR_MAP = ROOT / "out/patch/hangul_char_map.json"

CODE_SEG_7A = 0xA000
STOCK_CODE_BANKS = range(0x70, 0x80)
TEXT_DATA_BANKS = tuple(range(0x50, 0x60)) + (0x75, 0x76)

GLYPH_INDEX_BUFFER = 0x1A6E  # stock glyph index buffer, written by stock 7A:07A0

WRAM_WATCH: Tuple[Tuple[int, str], ...] = (
    (TAG_FLAG, "font hook Hangul run flag (patch_font_hangul_hook.TAG_FLAG)"),
    (WRAM_FLAG, "ext3 hook flag (patch_3byte_dict_token.WRAM_FLAG)"),
    (WRAM_INDEX, "ext3 hook index (patch_3byte_dict_token.WRAM_INDEX)"),
    (GLYPH_INDEX_BUFFER, "stock glyph index buffer base (shared by design)"),
)

CANDIDATE_NOTE = (
    "operand scan hit — a matching byte pair can also be data or a displacement; "
    "only an emulator write watchpoint (task 10.3) can confirm a real conflict"
)
CONFIRMED_NOTE = (
    "confirmed stock usage — this address is the stock glyph index buffer the "
    "stock code at 7A:07A0 writes through; the hook shares it by construction"
)


# --- minimal linear length decoder -----------------------------------------

_PREFIXES = {0x26, 0x2E, 0x36, 0x3E, 0xF0, 0xF2, 0xF3}
_ALU_RM = set()
for _base in (0x00, 0x08, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38):
    _ALU_RM |= {_base, _base + 1, _base + 2, _base + 3}
_ALU_AL_IMM8 = {0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C, 0x34, 0x3C}
_ALU_AX_IMM16 = {0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D}
_PUSH_SEG = {0x06, 0x0E, 0x16, 0x1E}
_POP_SEG = {0x07, 0x17, 0x1F}
_ONE_BYTE = (
    {0x27, 0x2F, 0x37, 0x3F, 0x9B, 0x98, 0x99, 0x9C, 0x9D, 0x9E, 0x9F}
    | {0xC3, 0xCB, 0xCC, 0xCE, 0xCF, 0xC9, 0xD7, 0x60, 0x61}
    | set(range(0x40, 0x60))
    | set(range(0x90, 0x98))
    | set(range(0xA4, 0xA8))
    | set(range(0xAA, 0xB0))
    | set(range(0xEC, 0xF0))
    | {0xF4, 0xF5}
    | set(range(0xF8, 0xFE))
)
_MODRM_ONLY = (
    _ALU_RM
    | {0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x8E, 0x8F}
    | {0xC4, 0xC5, 0xD0, 0xD1, 0xD2, 0xD3, 0xFE, 0xFF}
    | set(range(0xD8, 0xE0))
)


def _modrm_extra(modrm: int) -> int:
    mod = modrm >> 6
    rm = modrm & 0x07
    if mod == 0:
        return 2 if rm == 6 else 0
    if mod == 1:
        return 1
    if mod == 2:
        return 2
    return 0


def decode_one(buf: bytes, i: int) -> dict:
    """Decode one instruction; ``kind`` is 'push'/'pop'/'other'/'unknown'."""
    start = i
    prefixes: List[int] = []
    while i < len(buf) and buf[i] in _PREFIXES:
        prefixes.append(buf[i])
        i += 1
    if i >= len(buf):
        return {"start": start, "len": i - start, "kind": "unknown", "op": None}
    op = buf[i]
    i += 1

    def done(kind: str, **extra) -> dict:
        return {
            "start": start,
            "len": i - start,
            "kind": kind,
            "op": op,
            "prefixes": prefixes,
            **extra,
        }

    if op in _ONE_BYTE:
        if 0x50 <= op <= 0x57:
            return done("push", reg=op - 0x50)
        if 0x58 <= op <= 0x5F:
            return done("pop", reg=op - 0x58)
        if op == 0x9C:
            return done("push", mnem="pushf")
        if op == 0x9D:
            return done("pop", mnem="popf")
        if op == 0x60:
            return done("push", mnem="push_all")
        if op == 0x61:
            return done("pop", mnem="pop_all")
        if op in (0xC3, 0xCB):
            return done("ret", mnem="retf" if op == 0xCB else "ret")
        return done("other")
    if op in _PUSH_SEG:
        return done("push", mnem="push_seg")
    if op in _POP_SEG:
        return done("pop", mnem="pop_seg")
    if op in _ALU_AL_IMM8 or op in {0xA8, 0xCD, 0xD4, 0xD5, 0x6A, 0xE4, 0xE5, 0xE6, 0xE7}:
        i += 1
        return done("push" if op == 0x6A else "other")
    if op in _ALU_AX_IMM16 or op in {0xA9, 0x68, 0xC2, 0xCA, 0xA0, 0xA1, 0xA2, 0xA3}:
        i += 2
        return done("push" if op == 0x68 else "other")
    if 0xB0 <= op <= 0xB7:
        i += 1
        return done("other")
    if 0xB8 <= op <= 0xBF:
        i += 2
        return done("other")
    if 0x70 <= op <= 0x7F or op in {0xE0, 0xE1, 0xE2, 0xE3, 0xEB}:
        rel = buf[i] if i < len(buf) else 0
        i += 1
        target = i + (rel - 256 if rel > 127 else rel)
        return done("jcc" if op != 0xEB else "jmp", rel_target=target)
    if op in {0xE8, 0xE9}:
        rel = int.from_bytes(buf[i : i + 2], "little")
        i += 2
        target = (i + (rel - 0x10000 if rel > 0x7FFF else rel)) & 0xFFFFFFFF
        return done("call" if op == 0xE8 else "jmp", rel_target=target)
    if op in {0x9A, 0xEA}:
        off = int.from_bytes(buf[i : i + 2], "little")
        seg = int.from_bytes(buf[i + 2 : i + 4], "little")
        i += 4
        return done(
            "callfar" if op == 0x9A else "jmpfar", far_off=off, far_seg=seg
        )
    if op == 0xC8:
        i += 3
        return done("other")
    if op in _MODRM_ONLY:
        modrm = buf[i]
        i += 1 + _modrm_extra(modrm)
        if op == 0xFF and ((modrm >> 3) & 7) == 6:
            return done("push", mnem="push_mem")
        if op == 0x8F:
            return done("pop", mnem="pop_mem")
        return done("other", modrm=modrm)
    if op in {0x80, 0x82, 0x83, 0xC0, 0xC1, 0xC6, 0x6B}:
        modrm = buf[i]
        i += 1 + _modrm_extra(modrm) + 1
        return done("other", modrm=modrm)
    if op in {0x81, 0xC7, 0x69}:
        modrm = buf[i]
        i += 1 + _modrm_extra(modrm) + 2
        return done("other", modrm=modrm)
    if op in {0xF6, 0xF7}:
        modrm = buf[i]
        i += 1 + _modrm_extra(modrm)
        if ((modrm >> 3) & 7) == 0:
            i += 1 if op == 0xF6 else 2
        return done("other", modrm=modrm)
    return {"start": start, "len": max(1, i - start), "kind": "unknown", "op": op}


def decode_range(buf: bytes, start: int, end: int) -> Tuple[List[dict], List[dict]]:
    """Linear decode of ``buf[start:end]``; returns (instructions, unknowns)."""
    out: List[dict] = []
    unknown: List[dict] = []
    i = start
    while i < end:
        ins = decode_one(buf, i)
        if ins["kind"] == "unknown" or ins["len"] <= 0:
            unknown.append({"at": i, "op": ins.get("op"), "hex": buf[i : i + 4].hex()})
            break
        ins["bytes"] = buf[i : i + ins["len"]].hex()
        out.append(ins)
        i += ins["len"]
    return out, unknown


# --- check 1: leaf cave contract --------------------------------------------


def leaf_bounds(rom: bytes, meta_path: Path | None = None) -> dict:
    """Cave/leaf addresses from the installer meta, with constants as fallback."""
    cave, cave_len, leaf = CAVE3, CAVE3_MAX, None
    source = "patch_3byte_dict_token constants"
    meta_file = meta_path if meta_path is not None else EXT3_META
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        cave = int(meta.get("cave", f"{CAVE3:06X}"), 16)
        cave_len = int(meta.get("cave_len") or CAVE3_MAX)
        parts = meta.get("parts") or {}
        if parts.get("leaf"):
            leaf = int(parts["leaf"], 16)
        source = str(meta_file.relative_to(ROOT)) if meta_file.is_relative_to(ROOT) else str(meta_file)
    if leaf is None:
        # leaf part begins with `cmp byte [WRAM_FLAG],1`
        sb = stock_base(rom)
        needle = b"\x80\x3E" + WRAM_FLAG.to_bytes(2, "little") + b"\x01"
        blob = rom[sb + cave : sb + cave + cave_len]
        j = blob.find(needle)
        leaf = cave + j if j >= 0 else cave
    return {"cave": cave, "cave_len": cave_len, "leaf": leaf, "source": source}


def find_stock_restore(jp: bytes) -> dict:
    """Locate the stock `pop ax` + far call DEB5 the current leaf leans on."""
    sb = stock_base(jp)
    bank = bytes(jp[sb + 0x7A0000 : sb + 0x7B0000])
    needle = b"\x58\x9A" + BANK_MAP_OFF.to_bytes(2, "little") + b"\x00\x80"
    sites: List[int] = []
    j = bank.find(needle)
    while j >= 0:
        sites.append(j)
        j = bank.find(needle, j + 1)
    if not sites:
        return {"found": False}
    stream = LEAF_STREAM & 0xFFFF
    after = [s for s in sites if s >= stream]
    pick = after[0] if after else sites[0]
    return {
        "found": True,
        "pop_ax_at": f"7A:{pick:04X}",
        "deb5_call_at": f"7A:{pick + 1:04X}",
        "reached_from_stream": f"7A:{stream:04X}",
        "all_pop_ax_deb5_sites": [f"7A:{s:04X}" for s in sites],
        "documented_as": "7A:074B",
        "note": "measured: the pop ax the current leaf leans on sits at "
        f"7A:{pick:04X} with the DEB5 remap at 7A:{pick + 1:04X}; bugfix.md cites "
        "7A:074B for the same instruction (off by one byte)",
    }


def check_leaf_cave(target: bytes, jp: bytes, meta_path: Path | None = None) -> dict:
    sb = stock_base(target)
    b = leaf_bounds(target, meta_path)
    cave, cave_len, leaf = b["cave"], b["cave_len"], b["leaf"]
    blob = bytes(target[sb + cave : sb + cave + cave_len])
    leaf_off = leaf - cave
    instrs, unknowns = decode_range(blob, leaf_off, len(blob))

    prologue = LEAF_EXPECT  # 55 8B EC 83 EC 08 — reproduced stock prologue
    prologue_starts = set()
    j = blob.find(prologue, leaf_off)
    while j >= 0:
        prologue_starts.add(j)
        j = blob.find(prologue, j + 1)

    deb5 = BANK_MAP_OFF
    deb2 = BANK_SAVE_OFF
    paths: List[dict] = []
    cur: List[dict] = []
    for ins in instrs:
        cur.append(ins)
        if ins["kind"] in ("jmpfar", "ret"):
            paths.append(cur)
            cur = []
    if cur:
        paths.append(cur)

    path_reports: List[dict] = []
    for pi, path in enumerate(paths):
        push = pop = 0
        prologue_push = 0
        deb5_at: List[int] = []
        deb2_at: List[int] = []
        fad0_at: List[int] = []
        for ins in path:
            if ins["kind"] == "push":
                if ins["start"] in prologue_starts:
                    prologue_push += 1
                else:
                    push += 1
            elif ins["kind"] == "pop":
                pop += 1
            elif ins["kind"] == "callfar":
                off = ins.get("far_off")
                if off == deb5:
                    deb5_at.append(ins["start"])
                elif off == deb2:
                    deb2_at.append(ins["start"])
                elif off == FAD0_OFF:
                    fad0_at.append(ins["start"])
        last = path[-1]
        term = {"kind": last["kind"]}
        if last["kind"] == "jmpfar":
            term["target"] = f"{last['far_seg']:04X}:{last['far_off']:04X}"
            term["stock_stream"] = (
                last["far_seg"] == CODE_SEG_7A
                and last["far_off"] in (LEAF_STREAM & 0xFFFF, LEAF_CONTINUE & 0xFFFF)
            )
        fetch_pos = max(fad0_at) if fad0_at else (min(deb5_at) if deb5_at else None)
        restore_after_fetch = bool(
            fetch_pos is not None and any(p > fetch_pos for p in deb5_at)
        )
        path_reports.append(
            {
                "path": pi,
                "start": f"{cave + path[0]['start']:06X}",
                "end": f"{cave + last['start'] + last['len'] - 1:06X}",
                "instructions": len(path),
                "push_own": push,
                "pop": pop,
                "push_stock_prologue_excluded": prologue_push,
                "balanced": push == pop,
                "stack_delta": push - pop,
                "deb2_bank_save_calls": [f"{cave + p:06X}" for p in deb2_at],
                "deb5_bank_map_calls": [f"{cave + p:06X}" for p in deb5_at],
                "phrase_fetch_calls_FAD0": [f"{cave + p:06X}" for p in fad0_at],
                "bank_restore_after_fetch": restore_after_fetch,
                "terminator": term,
                "delegates_restore_to_stock": bool(
                    term.get("stock_stream") and push != pop
                ),
            }
        )

    ext3_paths = [
        p for p in path_reports if p["deb5_bank_map_calls"] or p["deb2_bank_save_calls"]
    ]
    unbalanced = [p for p in path_reports if not p["balanced"]]
    delegating = [p for p in path_reports if p["delegates_restore_to_stock"]]
    restoring = [p for p in ext3_paths if p["bank_restore_after_fetch"]]

    failures: List[str] = []
    if unknowns:
        failures.append(
            f"cave decode incomplete at {cave + unknowns[0]['at']:06X} "
            f"(opcode {unknowns[0]['op']}) — fail-closed"
        )
    if unbalanced:
        failures.append(
            "push/pop asymmetry in path(s) "
            + ", ".join(f"{p['path']} (delta {p['stack_delta']:+d})" for p in unbalanced)
        )
    if ext3_paths and not restoring:
        failures.append(
            "no bank restore inside the cave: every ext3 path maps the expansion "
            "bank with DEB5 but none calls DEB5 again after the phrase fetch"
        )
    if delegating:
        failures.append(
            "restore delegated to stock code: path(s) "
            + ", ".join(str(p["path"]) for p in delegating)
            + " leave through a far jmp into the stock stream with an unbalanced stack"
        )

    return {
        "ok": not failures,
        "cave": f"{cave:06X}",
        "cave_len": cave_len,
        "leaf_part": f"{leaf:06X}",
        "leaf_hook_site": f"{LEAF:06X}",
        "bounds_source": b["source"],
        "decode_complete": not unknowns,
        "unknown_opcodes": unknowns,
        "paths": path_reports,
        "stock_restore_site": find_stock_restore(jp),
        "failures": failures,
        "note": "the reproduced stock prologue 55 8B EC 83 EC 08 is excluded from "
        "push_own (its pop bp lives in the stock epilogue) and reported as "
        "push_stock_prologue_excluded.",
    }


# --- check 2: marker code must not exist in stock ---------------------------


def marker_code() -> int:
    if CHAR_MAP.exists():
        pad = json.loads(CHAR_MAP.read_text(encoding="utf-8")).get("padding_store") or {}
        if pad.get("marker_code"):
            return int(pad["marker_code"], 16)
    return 0xE3DB


def count_pair(jp: bytes, pair: bytes, banks: Sequence[int]) -> List[int]:
    sb = stock_base(jp)
    hits: List[int] = []
    for seg in banks:
        start, end = sb + (seg << 16), sb + ((seg + 1) << 16)
        i = jp.find(pair, start, end)
        while i >= 0:
            hits.append(i - sb)
            i = jp.find(pair, i + 1, end)
    return hits


def check_marker_zero(jp: bytes) -> dict:
    mc = marker_code()
    pair = mc.to_bytes(2, "big")
    text_hits = count_pair(jp, pair, TEXT_DATA_BANKS)
    all_hits = count_pair(jp, pair, range(0x00, 0x80))
    ext3_pair = MAGIC.to_bytes(2, "big")
    ext3_text = count_pair(jp, ext3_pair, TEXT_DATA_BANKS)
    ext3_all = count_pair(jp, ext3_pair, range(0x00, 0x80))

    failures: List[str] = []
    if text_hits:
        failures.append(
            f"marker code {mc:04X} occurs {len(text_hits)} time(s) in the original "
            "text/data banks 50-5F,75,76 — the shared render hook can consume "
            "original data as a marker"
        )
    return {
        "ok": not failures,
        "marker_code": f"{mc:04X}",
        "gate_scope": "original stock text/data banks 50-5F, 75, 76",
        "marker_hits_text_data_banks": len(text_hits),
        "marker_sites_text_data_banks": [
            f"{h >> 16:02X}:{h & 0xFFFF:04X}" for h in text_hits
        ],
        "marker_hits_all_stock_banks": len(all_hits),
        "marker_sites_all_stock_banks": [
            f"{h >> 16:02X}:{h & 0xFFFF:04X}" for h in all_hits
        ],
        "reference_ext3_magic": {
            "code": f"{MAGIC:04X}",
            "hits_text_data_banks": len(ext3_text),
            "hits_all_stock_banks": len(ext3_all),
            "note": "selection criterion for a replacement marker: zero hits",
        },
        "failures": failures,
    }


# --- check 3: WRAM operand collisions --------------------------------------


def check_wram(jp: bytes) -> dict:
    entries: List[dict] = []
    failures: List[str] = []
    for addr, desc in WRAM_WATCH:
        pair = addr.to_bytes(2, "little")
        hits = count_pair(jp, pair, STOCK_CODE_BANKS)
        confirmed = addr == GLYPH_INDEX_BUFFER
        verdict = (
            "confirmed_stock_usage"
            if confirmed and hits
            else ("candidate_conflict_needs_watchpoint" if hits else "clear")
        )
        entries.append(
            {
                "addr": f"{addr:04X}",
                "role": desc,
                "operand_hits_70_7F": len(hits),
                "sites": [f"{h >> 16:02X}:{h & 0xFFFF:04X}" for h in hits],
                "verdict": verdict,
                "evidence": (
                    CONFIRMED_NOTE
                    if verdict == "confirmed_stock_usage"
                    else (CANDIDATE_NOTE if hits else "no operand byte pair in 70-7F")
                ),
            }
        )
        if hits:
            failures.append(
                f"{addr:04X}: {len(hits)} operand hit(s) in stock code banks 70-7F "
                f"({verdict})"
            )
    return {
        "ok": not failures,
        "scan": "LE16 operand byte pair in the ORIGINAL ROM, stock code banks 70-7F",
        "stock_store_site": f"{STORE_SITE:06X}",
        "addresses": entries,
        "failures": failures,
        "note": "nonzero is a failure by contract. 'candidate' hits are byte-pair "
        "matches that may be data or displacements — an emulator write watchpoint "
        "(task 10.3) is the only way to confirm; 'confirmed' means the address is "
        "documented stock state.",
    }


# --- main -------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--meta", type=Path, default=None, help="candidate ext3 metadata")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this gate is read-only")
    for p in (args.jp, args.target):
        if not p.exists():
            raise SystemExit(f"missing input ROM: {p}")

    jp = bytes(load_rom(args.jp))
    tgt = bytes(load_rom(args.target))

    c1 = check_leaf_cave(tgt, jp, args.meta)
    c2 = check_marker_zero(jp)
    c3 = check_wram(jp)
    failures = c1["failures"] + c2["failures"] + c3["failures"]

    report = {
        "ok": not failures,
        "generated_by": "tools/verify_hook_contract.py",
        "read_only": True,
        "original": str(args.jp),
        "target": str(args.target),
        "ext3_meta": str(args.meta or EXT3_META),
        "failures": failures,
        "check_1_leaf_cave": c1,
        "check_2_marker_zero_in_stock": c2,
        "check_3_wram_operand_collision": c3,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        print(f"target : {args.target}")
        print(
            f"\ncheck 1 leaf cave {c1['cave']} (leaf part {c1['leaf_part']}, "
            f"bounds from {c1['bounds_source']}) → {'ok' if c1['ok'] else 'FAIL'}"
        )
        for p in c1["paths"]:
            term = p["terminator"]
            print(
                f"  path {p['path']} {p['start']}–{p['end']}  push_own {p['push_own']} "
                f"pop {p['pop']} (prologue push excluded {p['push_stock_prologue_excluded']}) "
                f"delta {p['stack_delta']:+d} balanced={p['balanced']}"
            )
            print(
                f"    DEB2 {p['deb2_bank_save_calls']} DEB5 {p['deb5_bank_map_calls']} "
                f"FAD0 {p['phrase_fetch_calls_FAD0']} "
                f"restore_after_fetch={p['bank_restore_after_fetch']} "
                f"term={term.get('kind')} {term.get('target', '')} "
                f"stock_stream={term.get('stock_stream')} "
                f"delegates={p['delegates_restore_to_stock']}"
            )
        sr = c1["stock_restore_site"]
        if sr.get("found"):
            print(
                f"  stock restore relied on: pop ax at {sr['pop_ax_at']}, DEB5 at "
                f"{sr['deb5_call_at']} (doc {sr['documented_as']})"
            )
        print(
            f"\ncheck 2 marker {c2['marker_code']} in original stock → "
            f"{'ok' if c2['ok'] else 'FAIL'}: "
            f"{c2['marker_hits_text_data_banks']} hit(s) in 50-5F,75,76 "
            f"({c2['marker_hits_all_stock_banks']} in all of 00-7F)"
        )
        print(f"  sites: {', '.join(c2['marker_sites_text_data_banks'])}")
        r = c2["reference_ext3_magic"]
        print(
            f"  reference: ext3 magic {r['code']} → {r['hits_text_data_banks']} "
            f"hit(s) in 50-5F,75,76, {r['hits_all_stock_banks']} in 00-7F"
        )
        print(f"\ncheck 3 WRAM operands in stock 70-7F → {'ok' if c3['ok'] else 'FAIL'}")
        for e in c3["addresses"]:
            print(
                f"  {e['addr']} {e['operand_hits_70_7F']:>3} hit(s)  {e['verdict']}"
            )
            if e["sites"]:
                print(f"      {', '.join(e['sites'])}")
        print(f"\n→ {args.out}")
        print(f"ok={report['ok']}")
        for f in failures:
            print(f"  FAIL {f}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
