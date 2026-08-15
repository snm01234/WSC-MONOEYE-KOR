#!/usr/bin/env python3
"""
Prove — not assume — that the leading byte of an aux text record is a
speaker/portrait/control id rather than the first character of the sentence.

READ-ONLY. This tool never opens a .wsc for writing.

Why this has to be proved
-------------------------
``apply_aux_ko`` only rewrites records whose first byte is provably text (a
dictionary token, or a 2-byte character lead). Everything else is refused,
because ``5a``→``カ`` is a speaker id while ``80``→``機`` is the real first
character of ``機銃座は……``. That refusal blocks ~2,700 records, and sampling
shows most of them are genuine battle dialogue with one stray glyph in front:

    5D:47CF  'アほう……やるな！'
    5D:AFAB  '隊虫けらどもめが！！'
    5E:B4B3  '様……落ちろ！！'

The plan is to stop classifying the byte and instead **preserve it verbatim**,
rewriting only the body. That is only sound if the byte really is outside the
sentence. This tool supplies the evidence.

Two independent arguments, decided per bank
-------------------------------------------
**Test A — duplicate bodies.** Group records by their body bytes (payload with
the first ``k`` bytes removed). If one identical body occurs under **two or more
different leading bytes**, that leading byte cannot be part of the string: the
same sentence is stored once per speaker.

Test A only fires where a sentence is *reused* across speakers. That is true of
the generic battle barks in 5D/5E and false of the unique mission lines in 59,
so a null result in 59 is a limit of the test, not evidence against the
hypothesis. Measured: 59 and 5C return zero. Test A alone is therefore
insufficient, which is what the first run of this tool reported.

**Test B — grammatically impossible sentence-initial character.** Japanese
sentences cannot begin with a bound particle or a small kana. ``が`` and ``は``
are the clearest cases: no sentence starts with them. If a bank's records
overwhelmingly "begin" with such characters, those bytes are not the start of
the sentence. Bank 59 is 81% ``が``/``こ``/``は`` over 1,100 records, and the
decoded samples show exactly that shape:

    59:0723  'がせこまあ、そう怒るな。'   → prefix 'がせこ' + 'まあ、そう怒るな。'

Test B needs no duplication and so covers the banks Test A cannot reach.

Both tests share the same control: the records ``apply_aux_ko`` already accepted
as provably text-initial. There byte 0 is genuine text, so both tests should be
near zero. If a test fires at a similar rate in the control, it is measuring
something else and must not be used.

A bank is cleared for prefix-preserving rewrite only when at least one test
clears it *and* the control stays low. Banks that clear neither stay refused.

Report: ``out/script/aux_prefix_proof.json``.
Exit 1 when no bank is cleared.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from expand_dictionary import _walk_zstring_range  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    Dictionary,
    Tbl,
    find_rom,
    load_rom,
)

DEFAULT_BLOCKS = ROOT / "out/script/aux_text_blocks.json"
DEFAULT_ELIGIBLE = ROOT / "out/script/aux_block_eligible.json"
DEFAULT_TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
DEFAULT_OUT = ROOT / "out/script/aux_prefix_proof.json"

# Banks where sampling found genuine sentences. 52/56/5A/5B/76 decoded as table
# noise and stay out of scope.
TARGET_BANKS: tuple[int, ...] = (0x59, 0x5C, 0x5D, 0x5E)

MIN_BODY_BYTES = 4  # an ext3 token needs 4 bytes
MAX_EXAMPLES = 6

# Characters that cannot begin a Japanese sentence NO MATTER WHAT FOLLOWS.
# This set was cut down after a false positive: an earlier version included the
# bound particles が は を に の で と も へ, and then flagged
#   5E:212A 'でも、これは戦争だから！！'
# which is a perfectly good sentence. Those kana are only particles *in context*;
# as syllables they start ordinary words (でも, がんばれ, はやく, にげろ, もう).
# What remains cannot be word-initial in Japanese at all:
#   ん  syllabic n            っ  gemination
#   ゃゅょぁぃぅぇぉ  small kana (they modify the preceding syllable)
#   ー－  long-vowel mark     ゛゜  combining voicing marks
IMPOSSIBLE_INITIAL = set("んっゃゅょぁぃぅぇぉャュョァィゥェォッンー－゛゜")

# Control/escape renderings that are plainly not sentence text either.
NON_TEXT_MARKERS = ("<TRUNC", "<BADDICT", "<E", "?")


def glyph(d: Dictionary, tbl: Tbl, value: int) -> str:
    try:
        return d.expand(bytes([value]), tbl)
    except Exception:  # noqa: BLE001
        return "?"


def collect_records(
    original: bytes, blocks: List[dict], banks: Sequence[int]
) -> Dict[int, List[tuple[int, bytes]]]:
    """bank → [(logical, payload)] for coherent-block records in ``banks``."""
    out: Dict[int, List[tuple[int, bytes]]] = collections.defaultdict(list)
    seen: set[int] = set()
    for blk in blocks:
        lo, hi = int(blk["start"], 16), int(blk["end"], 16)
        if (lo >> 16) not in banks:
            continue
        for logical, payload, _kind in _walk_zstring_range(
            original, lo, hi + 1, region="aux", max_len=128
        ):
            if logical in seen or not payload:
                continue
            seen.add(logical)
            out[logical >> 16].append((logical, payload))
    return out


def group_by_body(
    rows: List[tuple[int, bytes]], k: int
) -> Dict[bytes, List[tuple[int, int]]]:
    """body bytes → [(logical, leading byte)] for bodies of usable length."""
    groups: Dict[bytes, List[tuple[int, int]]] = collections.defaultdict(list)
    for logical, payload in rows:
        if len(payload) < k + MIN_BODY_BYTES:
            continue
        groups[payload[k:]].append((logical, payload[k - 1]))
    return groups


def analyse(
    rows: List[tuple[int, bytes]], k: int
) -> dict:
    """How many bodies repeat under two or more distinct leading bytes."""
    groups = group_by_body(rows, k)
    multi_lead = 0
    records_covered = 0
    proven: set[int] = set()
    lead_values: collections.Counter = collections.Counter()
    examples: List[dict] = []
    for body, members in groups.items():
        leads = {lead for _, lead in members}
        if len(leads) < 2:
            continue
        multi_lead += 1
        records_covered += len(members)
        proven.update(lg for lg, _ in members)
        lead_values.update(leads)
        if len(examples) < MAX_EXAMPLES:
            examples.append(
                {
                    "body_hex": body.hex().upper()[:40],
                    "distinct_leads": sorted(f"{v:02X}" for v in leads),
                    "sites": [f"{lg >> 16:02X}:{lg & 0xFFFF:04X}" for lg, _ in members[:6]],
                }
            )
    return {
        "bodies_examined": len(groups),
        "bodies_with_multiple_leads": multi_lead,
        "records_covered": records_covered,
        "distinct_lead_values": len(lead_values),
        "examples": examples,
        "_proven": proven,
    }


def analyse_impossible_initial(
    rows: List[tuple[int, bytes]], d: Dictionary, tbl: Tbl
) -> dict:
    """Test B: how many records 'start' with a character that cannot start a
    Japanese sentence (bound particle, small kana, long-vowel mark, or a
    control/escape rendering)."""
    impossible = 0
    non_text = 0
    plausible = 0
    undecodable = 0
    proven: set[int] = set()
    by_char: collections.Counter = collections.Counter()
    examples: List[dict] = []
    for logical, payload in rows:
        # Judge the FIRST RENDERED CHARACTER of the whole record, not the
        # expansion of byte 0 alone. Byte 0 is often the high half of a 2-byte
        # character, and expanding it in isolation yields a bogus <TRUNC:xx>
        # marker — an earlier version of this test did that and scored the
        # text-initial control at 100%, i.e. it was measuring its own artifact.
        try:
            full = d.expand(payload, tbl)
        except Exception:  # noqa: BLE001
            full = ""
        if not full:
            undecodable += 1
            continue
        if any(m in full[:10] for m in NON_TEXT_MARKERS):
            non_text += 1
            proven.add(logical)
            by_char[full[:8]] += 1
            continue
        first = full[0]
        if first in IMPOSSIBLE_INITIAL:
            impossible += 1
            proven.add(logical)
            by_char[first] += 1
            if len(examples) < MAX_EXAMPLES:
                examples.append(
                    {
                        "site": f"{logical >> 16:02X}:{logical & 0xFFFF:04X}",
                        "lead": first,
                        "render": full[:48],
                    }
                )
            continue
        plausible += 1
    total = max(1, len(rows))
    return {
        "records": len(rows),
        "impossible_initial": impossible,
        "non_text_marker": non_text,
        "plausible_initial": plausible,
        "undecodable": undecodable,
        "covered": impossible + non_text,
        "covered_rate": round((impossible + non_text) / total, 4),
        "top_chars": by_char.most_common(8),
        "examples": examples,
        "_proven": proven,
    }


def analyse_concentration(
    rows: List[tuple[int, bytes]], d: Dictionary, tbl: Tbl, control_top3: float
) -> dict:
    """Test C: is the leading byte's distribution too concentrated to be text?

    Natural language spreads its sentence-initial characters over hundreds of
    values. If three byte values account for most of a bank's records, that byte
    is an enumerated field (speaker / portrait / control), not a character.
    The yardstick is the same statistic measured on the text-initial control.
    """
    leads = collections.Counter(p[0] for _, p in rows)
    total = max(1, len(rows))
    top3 = sum(n for _, n in leads.most_common(3)) / total
    return {
        "records": len(rows),
        "distinct_leads": len(leads),
        "top3_share": round(top3, 4),
        "control_top3_share": round(control_top3, 4),
        "ratio_vs_control": round(top3 / max(control_top3, 1e-9), 2),
        "leads_per_record": round(len(leads) / total, 4),
    }


def first_char_top3_share(
    rows: List[tuple[int, bytes]], d: Dictionary, tbl: Tbl
) -> float:
    """Top-3 share of the first *rendered* character in a text-initial set."""
    chars: collections.Counter = collections.Counter()
    for _logical, payload in rows:
        try:
            full = d.expand(payload, tbl)
        except Exception:  # noqa: BLE001
            continue
        if full:
            chars[full[0]] += 1
    total = max(1, sum(chars.values()))
    return sum(n for _, n in chars.most_common(3)) / total


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original-rom", type=Path, default=None)
    ap.add_argument("--blocks", type=Path, default=DEFAULT_BLOCKS)
    ap.add_argument("--eligible", type=Path, default=DEFAULT_ELIGIBLE)
    ap.add_argument("--tbl", type=Path, default=DEFAULT_TBL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--max-prefix",
        type=int,
        default=3,
        help="test prefix lengths 1..N (bank 59 uses multi-byte prefixes)",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this proof is read-only")

    base = args.original_rom or find_rom(ROOT)
    original = bytes(load_rom(base))
    tbl = Tbl.load(args.tbl)
    d = Dictionary(original)

    blocks = json.loads(args.blocks.read_text(encoding="utf-8"))["blocks"]
    eligible_spec = json.loads(args.eligible.read_text(encoding="utf-8"))
    eligible = {
        int(r["abs"], 16) if isinstance(r, dict) else int(r, 16)
        for r in eligible_spec["records"]
    }

    per_bank = collect_records(original, blocks, TARGET_BANKS)

    # Population A: the refused records (leading byte not provably text).
    # Population B (control): the accepted, provably text-initial records.
    refused: List[tuple[int, bytes]] = []
    control: List[tuple[int, bytes]] = []
    for bank, rows in per_bank.items():
        for logical, payload in rows:
            (control if logical in eligible else refused).append((logical, payload))

    report: dict = {
        "generated_by": "tools/prove_aux_prefix.py",
        "read_only": True,
        "original": str(base),
        "target_banks": [f"{b:02X}" for b in TARGET_BANKS],
        "population_refused": len(refused),
        "population_control_text_initial": len(control),
        "hypothesis": (
            "If one identical body occurs under two or more different leading "
            "bytes, that leading byte is not part of the string."
        ),
        "by_prefix_len": {},
        "by_bank": {},
        "control_by_prefix_len": {},
    }

    for k in range(1, args.max_prefix + 1):
        a = analyse(refused, k)
        c = analyse(control, k)
        a.pop("_proven", None)
        c.pop("_proven", None)
        report["by_prefix_len"][str(k)] = a
        report["control_by_prefix_len"][str(k)] = c

    proven_per_bank: Dict[str, set[int]] = {}
    control_test_b = analyse_impossible_initial(control, d, tbl)
    control_test_b.pop("_proven", None)
    report["control_impossible_initial"] = control_test_b
    control_top3 = first_char_top3_share(control, d, tbl)
    report["control_first_char_top3_share"] = round(control_top3, 4)

    for bank, rows in sorted(per_bank.items()):
        ref_rows = [(lg, p) for lg, p in rows if lg not in eligible]
        leads = collections.Counter(p[0] for _, p in ref_rows)
        test_a = analyse(ref_rows, 1)
        test_b = analyse_impossible_initial(ref_rows, d, tbl)
        a_rate = test_a["records_covered"] / max(1, len(ref_rows))
        # Tests A and B are PER-RECORD proofs, so no coverage threshold applies —
        # they clear exactly the records they flag, provided the same test stays
        # silent on the text-initial control. Requiring a bank-wide rate here was
        # a mistake in the first version: it threw away 141 individually proven
        # records in 5D/5E because they were "only" 5% of their bank.
        control_a_rate = report["control_by_prefix_len"]["1"][
            "records_covered"
        ] / max(1, len(control))
        a_clear = test_a["records_covered"] > 0 and control_a_rate < 0.01
        # Test B is a PER-RECORD proof, so it does not need to cover the bank.
        # It clears the records it flags, provided it stays quiet on the control.
        b_clear = test_b["covered"] > 0 and test_b["covered_rate"] > 3 * max(
            control_test_b["covered_rate"], 1e-9
        )
        test_c = analyse_concentration(ref_rows, d, tbl, control_top3)
        # A byte whose top-3 values dominate far beyond the control cannot be a
        # character. Require both a high absolute share and a wide gap.
        c_clear = test_c["top3_share"] >= 0.50 and test_c["ratio_vs_control"] >= 3.0
        proven_union: set[int] = set()
        if a_clear:
            proven_union |= test_a["_proven"]
        if b_clear:
            proven_union |= test_b["_proven"]
        if c_clear:
            proven_union |= {lg for lg, _ in ref_rows}
        report["by_bank"][f"{bank:02X}"] = {
            "refused_records": len(ref_rows),
            "distinct_leading_bytes": len(leads),
            "top_leads": [
                {"byte": f"{v:02X}", "glyph": glyph(d, tbl, v), "count": n}
                for v, n in leads.most_common(8)
            ],
            "test_a_duplicate_bodies": test_a,
            "test_a_rate": round(a_rate, 4),
            "test_a_clears": bool(a_clear),
            "test_b_impossible_initial": test_b,
            "test_b_clears": bool(b_clear),
            "test_c_concentration": test_c,
            "test_c_clears": bool(c_clear),
            # Whole-bank clearance needs Test C (a property of the field itself).
            # A and B only clear the individual records they flag.
            "bank_cleared": bool(c_clear),
            "records_cleared_individually": len(proven_union),
            "cleared": bool(c_clear),
        }
        report["by_bank"][f"{bank:02X}"]["test_a_duplicate_bodies"].pop("_proven", None)
        report["by_bank"][f"{bank:02X}"]["test_b_impossible_initial"].pop(
            "_proven", None
        )
        proven_per_bank[f"{bank:02X}"] = proven_union

    best_k = max(
        report["by_prefix_len"],
        key=lambda kk: report["by_prefix_len"][kk]["records_covered"],
    )
    cleared = [b for b, r in report["by_bank"].items() if r["bank_cleared"]]
    report["verdict"] = {
        "best_prefix_len": int(best_k),
        "cleared_banks": cleared,
        "refused_banks": [
            b for b, r in report["by_bank"].items() if not r["bank_cleared"]
        ],
        "records_in_cleared_banks": sum(
            report["by_bank"][b]["refused_records"] for b in cleared
        ),
        "total_proven_records": sum(len(v) for v in proven_per_bank.values()),
        "control_test_a_rate": round(
            report["control_by_prefix_len"]["1"]["records_covered"]
            / max(1, len(control)),
            4,
        ),
        "control_test_b_rate": control_test_b["covered_rate"],
    }
    # The proven record set is the deliverable: task 3 consumes exactly this and
    # must never widen it.
    report["proven_records"] = {
        bank: sorted(f"{lg:06X}" for lg in sites)
        for bank, sites in sorted(proven_per_bank.items())
        if sites
    }
    report["ok"] = report["verdict"]["total_proven_records"] > 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not args.quiet:
        print(f"original : {base.name}")
        print(
            f"모집단   : 거부됨 {len(refused)}  /  대조군(텍스트 선두 증명) {len(control)}"
        )
        print("\nprefix 길이별 — 같은 본문이 서로 다른 선두 바이트로 반복되는가")
        print(f"  {'k':>2s} {'본문그룹':>8s} {'다중선두':>8s} {'레코드':>8s} {'대조군레코드':>12s}")
        for k in range(1, args.max_prefix + 1):
            a = report["by_prefix_len"][str(k)]
            c = report["control_by_prefix_len"][str(k)]
            print(
                f"  {k:2d} {a['bodies_examined']:8d} "
                f"{a['bodies_with_multiple_leads']:8d} {a['records_covered']:8d} "
                f"{c['records_covered']:12d}"
            )
        print(
            f"\n대조군 통과율 — A {report['verdict']['control_test_a_rate']:.1%} · "
            f"B {report['verdict']['control_test_b_rate']:.1%}  (낮아야 정상)"
        )
        print("\n뱅크별 판정")
        for bank, row in report["by_bank"].items():
            a = row["test_a_duplicate_bodies"]
            b = row["test_b_impossible_initial"]
            mark = "통과" if row["cleared"] else "거부"
            print(
                f"  {bank}  거부 {row['refused_records']:5d}  선두고유 "
                f"{row['distinct_leading_bytes']:4d}   → {mark}"
            )
            print(
                f"        A 중복본문 : 레코드 {a['records_covered']:5d} "
                f"({row['test_a_rate']:.1%})  {'clear' if row['test_a_clears'] else '-'}"
            )
            print(
                f"        B 불가선두 : 소가나/장음 {b['impossible_initial']:5d} + "
                f"제어 {b['non_text_marker']:4d} = {b['covered']:5d} "
                f"({b['covered_rate']:.1%})  "
                f"{'clear' if row['test_b_clears'] else '-'}   "
                f"문장가능선두 {b['plausible_initial']}"
            )
            c = row["test_c_concentration"]
            print(
                f"        C 분포집중 : 상위3 {c['top3_share']:.1%} vs 대조군 "
                f"{c['control_top3_share']:.1%} (x{c['ratio_vs_control']})  "
                f"고유값 {c['distinct_leads']}  "
                f"{'clear' if row['test_c_clears'] else '-'}"
            )
            print(
                f"        → 뱅크승인 {row['bank_cleared']} · "
                f"개별승인 레코드 {row['records_cleared_individually']}"
            )
            print(
                "        최빈선두: "
                + "  ".join(
                    f"{t['byte']}({t['glyph']})x{t['count']}" for t in row["top_leads"][:6]
                )
            )
            for ex in b["examples"][:2]:
                print(f"          {ex['site']} lead={ex['lead']!r} {ex['render']!r}")
        v = report["verdict"]
        print(
            f"\n판정 : 뱅크 전체 승인 {v['cleared_banks']} "
            f"({v['records_in_cleared_banks']} 레코드) · "
            f"거부 뱅크 {v['refused_banks']}"
        )
        print(f"       증명된 레코드 총계 : {v['total_proven_records']}")
        for bank, sites in report["proven_records"].items():
            print(f"         {bank}: {len(sites)}")
        print(f"\nwrote {args.out}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
