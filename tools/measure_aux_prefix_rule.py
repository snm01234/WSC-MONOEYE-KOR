#!/usr/bin/env python3
"""
Fix the prefix length for the proven aux records, per bank, and validate it.

READ-ONLY. This tool never opens a .wsc for writing.

``prove_aux_prefix.py`` established THAT a prefix exists for 1,484 records but
not how long it is. Bank 59 samples show two lengths:

    59:0723  'がせこまあ、そう怒るな。'   prefix 'がせこ'
    59:62D2  'こ私は今からブリッジに戻る。' prefix 'こ'

A failed attempt, kept as a warning
-----------------------------------
The first version of this tool derived the prefix statistically: for each code
unit, compare how often it appears at the front against everywhere else, then
grow the frontier to a fixed point. It does not work, and it fails toward the
dangerous side:

  * ``こ`` (276 leads) and ``は`` (221 leads) scored 0.41 / 0.54 because those
    kana are also common *inside* bodies. They were classified as text, so their
    records got prefix length 0.
  * ``がせこ`` was cut to ``がせ`` — two bytes instead of three.
  * In 5D/5E the statistic promoted real words (``キサマ``, ``艦長``) to prefix.
  * Bank 59's validation metric never moved (831 → 831), so it could not even
    detect its own error.

Root cause: the prefix bytes *are* ordinary text characters. The same value
serves both roles, so no per-byte positional split can separate them.

What actually decides it
------------------------
Two separate facts, one per bank group.

**Bank 59 — the game's own dialogue grammar.** Measured leading sequences:

    17 34 18  'がせこ'   348 records at the front,  0 occurrences inside a body
    17 28 08  'がけは'    43 records at the front,  0 occurrences inside a body

and single leads ``18`` (276) and ``08`` (221). Those are exactly the script
control bytes this project already parses: ``17 xx [08 xx] 18`` window+dialogue,
``08 xx`` speaker, ``01`` indent, ``18`` dialogue marker. So bank 59 is not a new
format — reuse ``extract_script.split_prefix_body`` rather than re-deriving it.

**Banks 5D/5E — one code unit.** Both proofs in ``prove_aux_prefix`` were run at
a one-unit offset, so the records they cleared are cleared *for that offset*:
Test A found 193 multi-lead bodies at k=1 versus 9 at k=2 and 27 at k=3, and
Test B's flagged leads are single glyphs (``－``, ``ュ``). Sequence samples agree:
``1E F4FA 3A1C`` = ``す`` + ``艦長やられる``, ``64 F336 05 E1B4`` = ``ミ`` +
``子供の遊び``. Bank 5C is excluded entirely (its "prefixes" are continuation
text).

Validation
----------
For every record the computed prefix must satisfy:

  * prefix bytes drawn only from the control alphabet (bank 59), or exactly one
    code unit (5D/5E)
  * the exact prefix byte sequence occurs **zero** times inside any body in the
    same bank — a structural marker does not appear in running text
  * the body is at least 4 bytes, so an ext3 token fits
  * the body does not begin with a character that cannot start a word

Report: ``out/script/aux_prefix_rule.json``.
Exit 1 when any bank fails the interior-occurrence check.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from expand_dictionary import _walk_zstring_range  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Dictionary,
    Tbl,
    find_rom,
    is_dict_token,
    is_ext3_magic,
    is_kanji_lead,
    load_rom,
)
from prove_aux_prefix import IMPOSSIBLE_INITIAL, NON_TEXT_MARKERS  # noqa: E402

DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_PROOF = ROOT / "out/script/aux_prefix_proof.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_OUT = ROOT / "out/script/aux_prefix_rule.json"

MIN_BODY_BYTES = 4  # an ext3 token needs 4 bytes

# Bank 59 uses the script dialogue grammar; 5D/5E use a one-unit voice id.
RULE_SCRIPT_GRAMMAR = "script_grammar"
RULE_ONE_UNIT = "one_code_unit"
BANK_RULES: Dict[int, str] = {
    0x59: RULE_SCRIPT_GRAMMAR,
    0x5D: RULE_ONE_UNIT,
    0x5E: RULE_ONE_UNIT,
}
CONTROL_BYTES = {0x01, 0x08, 0x17, 0x18}

# A leading byte can be both a script marker and a printable kana. These sites
# were confirmed from the original sentence plus runtime output to begin with
# real text, so they must not be split as control prefixes.
TEXT_INITIAL_EXCEPTIONS: Dict[int, str] = {
    0x590A2B: "こだわりすぎではないか……？",
}


def code_units(payload: bytes) -> List[Tuple[int, int]]:
    """[(offset, length)] of each code unit, honouring multi-byte leads."""
    out: List[Tuple[int, int]] = []
    i = 0
    n = len(payload)
    while i < n:
        b = payload[i]
        if b == 0:
            break
        if is_dict_token(b):
            size = 2
        elif is_kanji_lead(b):
            size = 4 if (i + 1 < n and is_ext3_magic(b, payload[i + 1])) else 2
        else:
            size = 1
        if i + size > n:
            break
        out.append((i, size))
        i += size
    return out


def prefix_len(payload: bytes, rule: str) -> int:
    """Byte length of the prefix under the bank's rule. 0 means 'no rule hit'."""
    if rule == RULE_SCRIPT_GRAMMAR:
        prefix, _body, _kind = split_prefix_body(payload)
        return len(prefix)
    if rule == RULE_ONE_UNIT:
        units = code_units(payload)
        return units[0][1] if units else 0
    raise ValueError(f"unknown rule {rule}")


def reads_like_start(text: str) -> bool:
    if not text:
        return False
    if any(m in text[:10] for m in NON_TEXT_MARKERS):
        return False
    return text[0] not in IMPOSSIBLE_INITIAL


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original-rom", type=Path, default=None)
    ap.add_argument("--blocks", type=Path, default=DEFAULT_BLOCKS)
    ap.add_argument("--proof", type=Path, default=DEFAULT_PROOF)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this measurement is read-only")

    base = args.original_rom or find_rom(ROOT)
    original = bytes(load_rom(base))
    tbl = Tbl.load(args.tbl)
    d = Dictionary(original)

    proof = json.loads(args.proof.read_text(encoding="utf-8"))
    if not proof.get("ok"):
        raise SystemExit("aux_prefix_proof.json is not ok — run prove_aux_prefix first")
    proven = {
        bank: {int(s, 16) for s in sites}
        for bank, sites in proof["proven_records"].items()
    }

    blocks = json.loads(args.blocks.read_text(encoding="utf-8"))["blocks"]
    per_bank: Dict[int, List[Tuple[int, bytes]]] = collections.defaultdict(list)
    seen: set[int] = set()
    wanted = {int(b, 16) for b in proven}
    for blk in blocks:
        lo, hi = int(blk["start"], 16), int(blk["end"], 16)
        if (lo >> 16) not in wanted:
            continue
        for logical, payload, _kind in _walk_zstring_range(
            original, lo, hi + 1, region="aux", max_len=128
        ):
            if logical in seen or not payload:
                continue
            seen.add(logical)
            per_bank[logical >> 16].append((logical, payload))

    report: dict = {
        "generated_by": "tools/measure_aux_prefix_rule.py",
        "read_only": True,
        "original": str(base),
        "proof": str(args.proof),
        "bank_rules": {f"{b:02X}": r for b, r in BANK_RULES.items()},
        "rejected_approach": (
            "per-byte positional statistics — こ/は score 0.41/0.54 because they "
            "are common inside bodies too, so their records got prefix 0 and "
            "がせこ was cut to がせ. See the module docstring."
        ),
        "text_initial_exceptions": {
            f"{logical:06X}": expected
            for logical, expected in sorted(TEXT_INITIAL_EXCEPTIONS.items())
        },
        "by_bank": {},
        "records": {},
    }

    ok = True
    totals = collections.Counter()
    for bank, rows in sorted(per_bank.items()):
        bank_key = f"{bank:02X}"
        rule = BANK_RULES.get(bank)
        proven_set = proven.get(bank_key, set())
        if rule is None:
            report["by_bank"][bank_key] = {"skipped": "no rule for this bank"}
            continue

        klen: collections.Counter = collections.Counter()
        rows_out: List[dict] = []
        skipped: collections.Counter = collections.Counter()
        prefix_seqs: collections.Counter = collections.Counter()
        bad_alphabet: List[str] = []

        for logical, payload in rows:
            if logical not in proven_set:
                continue
            text_initial_exception = logical in TEXT_INITIAL_EXCEPTIONS
            k = 0 if text_initial_exception else prefix_len(payload, rule)
            if k == 0 and not text_initial_exception:
                skipped["no_prefix_found"] += 1
                continue
            body = payload[k:]
            if len(body) < MIN_BODY_BYTES:
                skipped["body_too_small"] += 1
                continue
            if rule == RULE_SCRIPT_GRAMMAR and k:
                # Every prefix byte must be a control byte or its operand; the
                # operand follows 08/17, so check the markers only.
                markers = {payload[0]}
                if not markers <= CONTROL_BYTES:
                    bad_alphabet.append(f"{logical:06X}")
                    skipped["prefix_not_control"] += 1
                    continue
            try:
                full = d.expand(payload, tbl)
                body_txt = d.expand(body, tbl)
            except Exception:  # noqa: BLE001
                skipped["decode_fail"] += 1
                continue
            if text_initial_exception and full != TEXT_INITIAL_EXCEPTIONS[logical]:
                skipped["text_initial_exception_mismatch"] += 1
                ok = False
                continue
            # Fail closed: if the body still does not begin like a word, the
            # prefix is probably longer than the rule says. Skip rather than
            # write a record whose split we cannot defend.
            if not reads_like_start(body_txt):
                skipped["body_bad_start"] += 1
                continue
            klen[k] += 1
            prefix_seqs[payload[:k]] += 1
            rows_out.append(
                {
                    "abs": f"{logical:06X}",
                    "prefix_bytes": k,
                    "prefix_hex": payload[:k].hex().upper(),
                    "body_jp": body_txt,
                    "full_jp": full,
                    "body_reads_like_start": reads_like_start(body_txt),
                }
            )

        # Interior-occurrence check. Only meaningful for MULTI-BYTE prefixes: a
        # structural marker like 17 34 18 never appears inside running text, but
        # a one-byte prefix is also an ordinary character (10 = '－', 18 = 'こ')
        # and of course occurs in bodies. Single-byte prefixes are validated
        # instead by the per-record proofs in prove_aux_prefix (Tests A and B),
        # which is what cleared those records in the first place.
        interior_hits: Dict[str, int] = {}
        for seq, _n in prefix_seqs.most_common():
            if len(seq) < 2:
                continue
            hits = 0
            for _lg, payload in rows:
                k = prefix_len(payload, rule)
                hits += payload[max(k, 1) :].count(seq)
            if hits:
                interior_hits[seq.hex().upper()] = hits
        if interior_hits:
            ok = False

        starts_ok = sum(1 for r in rows_out if r["body_reads_like_start"])
        report["by_bank"][bank_key] = {
            "rule": rule,
            "records_in_bank": len(rows),
            "proven_records": len(proven_set),
            "usable": len(rows_out),
            "skipped": dict(skipped),
            "prefix_len_histogram": {
                str(k): v for k, v in sorted(klen.items(), key=lambda kv: kv[0])
            },
            "top_prefix_sequences": [
                {
                    "hex": s.hex().upper(),
                    "render": (lambda: (lambda t: t)(d.expand(s, tbl)))()
                    if s
                    else "",
                    "count": n,
                }
                for s, n in prefix_seqs.most_common(8)
            ],
            "prefix_interior_occurrences": interior_hits,
            "prefix_alphabet_violations": bad_alphabet[:10],
            "body_reads_like_start": {"ok": starts_ok, "of": len(rows_out)},
        }
        report["records"][bank_key] = rows_out
        totals["usable"] += len(rows_out)
        totals["starts_ok"] += starts_ok

    report["totals"] = dict(totals)
    report["ok"] = bool(ok and totals["usable"] > 0)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        print(f"original : {base.name}")
        for bank, row in report["by_bank"].items():
            if "skipped" not in row:
                print(f"\n===== 뱅크 {bank} : {row.get('skipped')}")
                continue
            print(f"\n===== 뱅크 {bank}  규칙 {row['rule']} =====")
            print(
                f"  증명 {row['proven_records']}  →  적용가능 {row['usable']}"
                f"   제외 {row['skipped']}"
            )
            print(f"  prefix 바이트 길이 : {row['prefix_len_histogram']}")
            for s in row["top_prefix_sequences"][:6]:
                print(f"    {s['hex']:12s} {s['render']!r:12s} x{s['count']}")
            b = row["body_reads_like_start"]
            print(f"  본문이 단어 시작으로 읽힘 : {b['ok']}/{b['of']}")
            if row["prefix_interior_occurrences"]:
                print(
                    f"  !! prefix 시퀀스가 본문 내부에 등장 : "
                    f"{row['prefix_interior_occurrences']}"
                )
            else:
                print("  prefix 시퀀스 본문 내부 등장 : 0 → 구조적 마커 확인")
        print(
            f"\n합계 적용가능 {report['totals'].get('usable', 0)} · "
            f"본문 정상시작 {report['totals'].get('starts_ok', 0)} → "
            f"{'ok' if report['ok'] else 'FAIL'}"
        )
        print(f"wrote {args.out}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
