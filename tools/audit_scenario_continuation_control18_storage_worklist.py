#!/usr/bin/env python3
"""Classify source-proven continuation-18 records by safe storage strategy.

This audit consumes the provenance-aware runtime contract.  It does not decide
whether a leading 0x18 is visible text; that question is already answered by
Original + catalog provenance.  It instead asks how to store the translated
body without making the structural 0x18 leak as TBL glyph `こ`.

Strategies:
* ordinary_native: exact Korean text can be composed from existing ordinary
  F0-FF dictionary tokens whose payloads contain no ext3/event portals,
  compact3, or direct Hangul-run marker.  The structural 0x18 can stay intact.
* portal16: current direct E518 text needs a scalable native-loop wrapper.
  A future four-byte semantic-zero portal `E5 04 <u16 index>` can address a
  fixed-stride bank27 helper (`E5 18 xx yy 00`) without reclaiming F0-FF IDs.

No ROM bytes are modified.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_global_event_runtime_risk_v2 import semantic_e5_usage  # noqa: E402
from expand_dictionary import payload_has_hangul_marker  # noqa: E402
from monoeye_rom import Tbl, is_compact3_magic, token_from_dict_index  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
LEADING18_AUDIT = ROOT / "out/patch/scenario_continuation_leading18_audit.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_JSON = ROOT / "out/patch/scenario_continuation_control18_storage_worklist.json"
OUT_CSV = ROOT / "docs/SCENARIO_CONTINUATION_CONTROL18_STORAGE_WORKLIST.csv"
OUT_MD = ROOT / "docs/SCENARIO_CONTINUATION_CONTROL18_STORAGE_WORKLIST.md"

CURRENT_EVENT_MAGIC = bytes.fromhex("E51D")
PROPOSED_PORTAL16 = bytes.fromhex("E504")
PORTAL16_BANK = 0x27
PORTAL16_HELPER_STRIDE = 5
PORTAL16_HELPER_BASE = 0x2000


def raw_native_safe(dictionary: Any, index: int) -> bool:
    try:
        raw = bytes(dictionary.raw_entry(index, max_len=2048))
    except Exception:  # noqa: BLE001
        return False
    if b"\xE5\x18" in raw or CURRENT_EVENT_MAGIC in raw or PROPOSED_PORTAL16 in raw:
        return False
    if payload_has_hangul_marker(raw):
        return False
    for i in range(max(0, len(raw) - 1)):
        if is_compact3_magic(raw[i], raw[i + 1]):
            return False
    return True


def build_native_text_index(dictionary: Any, tbl: Tbl) -> tuple[dict[str, list[int]], dict[str, list[tuple[str, int]]]]:
    by_text: dict[str, list[int]] = defaultdict(list)
    by_first: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for index in range(4096):
        if not raw_native_safe(dictionary, index):
            continue
        try:
            text = dictionary.expand(bytes(token_from_dict_index(index)), tbl).rstrip("\u3000 \t")
        except Exception:  # noqa: BLE001
            continue
        if not text:
            continue
        by_text[text].append(index)
    for text, indexes in by_text.items():
        for index in indexes:
            by_first[text[0]].append((text, index))
    for key in by_first:
        by_first[key].sort(key=lambda item: (-len(item[0]), item[1]))
    return dict(by_text), dict(by_first)


def native_solution(text: str, capacity: int, by_first: dict[str, list[tuple[str, int]]]) -> list[int] | None:
    max_tokens = capacity // 2
    if not text or max_tokens <= 0:
        return None

    @lru_cache(maxsize=None)
    def solve(pos: int, left: int) -> tuple[int, ...] | None:
        if pos == len(text):
            return ()
        if left <= 0:
            return None
        candidates = by_first.get(text[pos], [])
        best: tuple[int, ...] | None = None
        for token_text, index in candidates:
            if not text.startswith(token_text, pos):
                continue
            tail = solve(pos + len(token_text), left - 1)
            if tail is None:
                continue
            got = (index,) + tail
            if best is None or len(got) < len(best) or (len(got) == len(best) and got < best):
                best = got
        return best

    got = solve(0, max_tokens)
    return None if got is None else list(got)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--main", type=Path, default=MAIN)
    ap.add_argument("--contracts", type=Path, default=CONTRACTS)
    ap.add_argument("--leading18-audit", type=Path, default=LEADING18_AUDIT)
    ap.add_argument("--out-json", type=Path, default=OUT_JSON)
    ap.add_argument("--out-csv", type=Path, default=OUT_CSV)
    ap.add_argument("--out-md", type=Path, default=OUT_MD)
    args = ap.parse_args(argv)

    rom = args.main.read_bytes()
    contracts_doc = json.loads(args.contracts.read_text(encoding="utf-8"))
    contracts = list(contracts_doc.get("contracts") or [])
    leading_doc = json.loads(args.leading18_audit.read_text(encoding="utf-8"))
    priority_by_addr = {str(r["address"]): str(r.get("priority") or "") for r in leading_doc.get("rows") or []}

    dictionary = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    tbl = Tbl.load(TBL)
    _by_text, by_first = build_native_text_index(dictionary, tbl)

    e5_count, e5_source = semantic_e5_usage(rom, dictionary)
    portal16_trail = PROPOSED_PORTAL16[1]
    portal16_semantic = {
        kind: int(e5_source.get((portal16_trail, kind), 0))
        for kind in ("script", "aux", "name75", "native_dictionary", "ext3_phrase")
    }

    bank = rom[PORTAL16_BANK << 16:(PORTAL16_BANK + 1) << 16]
    bank27_all_ff = len(bank) == 0x10000 and all(value == 0xFF for value in bank)

    rows: list[dict[str, Any]] = []
    for r in contracts:
        if r.get("route") != "scenario_continuation":
            continue
        if "runtime-safe rehome required" not in str(r.get("conflict") or ""):
            continue
        body = bytes.fromhex(str(r.get("baseline_body_hex") or ""))
        if len(body) < 4 or body[:2] != b"\xE5\x18":
            continue
        capacity = int(r.get("body_capacity") or 0)
        text = str(r.get("baseline_text") or "")
        solution = native_solution(text, capacity, by_first)
        native_body = None
        if solution is not None:
            payload = b"".join(bytes(token_from_dict_index(i)) for i in solution)
            if len(payload) <= capacity:
                # Individual dictionary entries can carry trailing full-width
                # spaces that were stripped while building the search index.
                # Accept a native composition only if the full concatenated
                # payload renders byte-for-byte to the current target text.
                try:
                    rendered = dictionary.expand(payload, tbl).rstrip("\u3000 \t")
                except Exception:  # noqa: BLE001
                    rendered = ""
                if rendered == text:
                    native_body = (payload + b"\x01" * (capacity - len(payload))).hex().upper()
        strategy = "ordinary_native" if native_body else "portal16"
        rows.append({
            "address": r["address"],
            "bundle_id": r.get("bundle_id"),
            "priority": priority_by_addr.get(str(r["address"]), ""),
            "text": text,
            "body_capacity": capacity,
            "source_prefix_hex": r.get("source_prefix_hex"),
            "current_body_hex": r.get("baseline_body_hex"),
            "current_ext3_token": body[:4].hex().upper(),
            "next_control": (r.get("baseline_boundary") or {}).get("next_control"),
            "nul_run": (r.get("baseline_boundary") or {}).get("nul_run"),
            "strategy": strategy,
            "native_tokens": [] if solution is None else [f"{i:04X}" for i in solution],
            "native_token_count": 0 if solution is None else len(solution),
            "native_body_hex": native_body,
        })

    counts = Counter(r["strategy"] for r in rows)
    unique_portal16_tokens = sorted({r["current_ext3_token"] for r in rows if r["strategy"] == "portal16"})
    portal16_helper_bytes = len(unique_portal16_tokens) * PORTAL16_HELPER_STRIDE
    if portal16_helper_bytes > 0x10000:
        raise SystemExit(f"portal16 helper pool would overflow bank27: {portal16_helper_bytes}")

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_scenario_continuation_control18_storage_worklist.py",
        "read_only": True,
        "inputs": {
            "main": str(args.main.relative_to(ROOT)),
            "contracts": str(args.contracts.relative_to(ROOT)),
            "leading18_audit": str(args.leading18_audit.relative_to(ROOT)),
        },
        "counts": {
            "source_proven_direct_18_E518": len(rows),
            "ordinary_native_recoverable": counts.get("ordinary_native", 0),
            "portal16_required": counts.get("portal16", 0),
            "portal16_unique_ext3_helpers": len(unique_portal16_tokens),
            "portal16_helper_bytes": portal16_helper_bytes,
        },
        "portal16_design_probe": {
            "magic": PROPOSED_PORTAL16.hex().upper(),
            "semantic_usage_total": int(e5_count[portal16_trail]),
            "semantic_usage_by_domain": portal16_semantic,
            "semantic_zero": int(e5_count[portal16_trail]) == 0,
            "encoding": "E5 04 <low+1> <high+1>; index=(high-1)*255+(low-1), both parameter bytes nonzero",
            "helper_bank": f"{PORTAL16_BANK:02X}",
            "helper_base": f"{PORTAL16_HELPER_BASE:04X}",
            "helper_stride": PORTAL16_HELPER_STRIDE,
            "helper_payload": "existing E5 18 xx yy + 00",
            "bank27_all_ff": bank27_all_ff,
            "capacity": (0x10000 - PORTAL16_HELPER_BASE) // PORTAL16_HELPER_STRIDE,
        },
        "policy": [
            "Preserve double-NUL source-proven structural 18; single-NUL visible-source-こ rows are a separate leak class and are excluded from this worklist.",
            "Prefer existing ordinary native F0-FF tokens when the full Korean text can be reproduced within the original body capacity.",
            "For unsolved rows, test one 16-bit native-loop portal representative before any bulk rewrite.",
            "Never place direct Hangul-run marker bytes in the special helper; helper contains only the already-existing E518 token and NUL.",
            "Keep terminator, NUL run, following 08/17 controls, and record extent byte-exact.",
        ],
        "rows": rows,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    fields = [
        "address", "bundle_id", "priority", "strategy", "text", "body_capacity",
        "source_prefix_hex", "current_ext3_token", "native_tokens", "native_token_count",
        "native_body_hex", "nul_run", "next_control",
    ]
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            cooked = dict(row)
            cooked["native_tokens"] = " ".join(row["native_tokens"])
            w.writerow(cooked)

    md = [
        "# Scenario continuation structural-18 storage worklist",
        "",
        f"Source-proven `18 + direct E518`: **{len(rows):,}**",
        f"Existing ordinary native token sequence로 복구 가능: **{counts.get('ordinary_native', 0):,}**",
        f"16-bit native-loop portal 필요: **{counts.get('portal16', 0):,}**",
        f"Portal helper 고유 E518 phrase: **{len(unique_portal16_tokens):,}**",
        f"Bank27 helper 예상 사용량: **{portal16_helper_bytes:,} / 65,536 bytes**",
        "",
        "## Proposed scalable portal probe",
        "",
        f"- magic: `{PROPOSED_PORTAL16.hex().upper()}`; semantic consumers: **{int(e5_count[portal16_trail])}**",
        f"- bank27 all-FF: **{bank27_all_ff}**",
        "- record body: `E5 04 <low+1> <high+1>` (base-255 nonzero 16-bit index; 4 bytes, current E518 extent와 동일)",
        "- helper: bank27 fixed-stride 5 bytes = `E5 18 xx yy 00`",
        "- leading structural `18`, terminator, NUL/page boundary, next control은 보존",
        "",
        "## Runtime probe before bulk build",
        "",
        "1. `60B449` 같은 double-NUL structural-18 대표를 `18 + E504 <index>`로 바꾸고 선두 18은 보존한다.",
        "2. 직후 08/17 control, 초상, 페이지 관계가 유지되는지 확인한다.",
        "3. 과거 선두 18 삭제가 페이지를 합쳤던 `6017FC/601826` 계열을 함께 대표 검증한다.",
        "4. `60BB48` 같은 single-NUL visible-source-こ 행은 이 worklist에서 제외하고 별도 same-extent glyph removal로 처리한다.",
        "5. probe PASS 후 ordinary-native 군과 portal16 군을 한 후보에 일괄 반영한다.",
        "",
    ]
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")

    print(json.dumps({"counts": report["counts"], "portal16": report["portal16_design_probe"], "json": str(args.out_json.relative_to(ROOT)), "csv": str(args.out_csv.relative_to(ROOT)), "md": str(args.out_md.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
