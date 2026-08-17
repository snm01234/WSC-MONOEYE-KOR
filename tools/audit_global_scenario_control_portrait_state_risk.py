#!/usr/bin/env python3
"""Read-only audit for scenario text/control/portrait-state boundary risks.

This audit was added after a runtime-proven STAGE4 Solomon scene failure:

    60:B400  visible `……네？`
    60:B409  structural `08 34 00`
    60:B40C  next dialogue

The control bytes themselves are source-exact, but the preceding record was
re-encoded from mixed native/raw source grammar `F191 081D` to a top-level
four-byte direct ext3 token.  Runtime then exposed the structural 08-row as
visible glyphs and failed to apply the following portrait/speaker state.

The goal here is not to call every E5 18 record a bug.  It separates:

* A: exact4 mixed-grammar records most similar to the user-proven failure;
* B: broader control-adjacent direct E5 18 inventory (review only);
* C: scenario continuations whose leading 18 is context-sensitive;
* D: existing 08 actor-id/NUL parser-collision audit results;
* hard byte drift: source/current NUL + following-control differences.

No ROM bytes are written.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from monoeye_rom import Tbl, token_from_dict_index

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
SPEAKER_AUDIT = ROOT / "out/patch/stage4_global_speaker_dictlead_nul_collision_audit.json"
OUT = ROOT / "out/patch/global_scenario_control_portrait_state_risk.json"
TBL_TARGET = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"

ANCHOR = 0x60B400
ANCHOR_CONTROL = 0x60B409
ANCHOR_FOLLOW = 0x60B40C
ANCHOR_SOURCE_BODY = bytes.fromhex("F191081D")
ANCHOR_CONTROL_BYTES = bytes.fromhex("083400")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def pair_kind(pair: bytes) -> str:
    if len(pair) != 2:
        return "short"
    lead = pair[0]
    if 0xF0 <= lead <= 0xFF:
        return "native_dict"
    if lead in {0x08, 0x17, 0x18}:
        return "context_sensitive_control_or_glyph"
    if 0xE0 <= lead <= 0xEF:
        return "kanji_or_raw2"
    return "raw2"


def row_view(row: dict[str, Any]) -> dict[str, Any]:
    source_body = bytes.fromhex(str(row.get("source_body_hex") or ""))
    baseline_body = bytes.fromhex(str(row.get("baseline_body_hex") or ""))
    boundary = row.get("baseline_boundary") or {}
    source_boundary = row.get("source_boundary") or {}
    return {
        "address": row["address"],
        "bundle_id": row.get("bundle_id"),
        "route": row.get("route"),
        "status": row.get("status"),
        "confidence": row.get("confidence"),
        "original_japanese": row.get("original_japanese"),
        "current_text": row.get("baseline_text"),
        "source_body_hex": source_body.hex().upper(),
        "current_body_hex": baseline_body.hex().upper(),
        "source_pair_grammar": [pair_kind(source_body[:2]), pair_kind(source_body[2:4])]
        if len(source_body) == 4
        else [],
        "nul_run": boundary.get("nul_run"),
        "next_address": boundary.get("next_address"),
        "next_control": boundary.get("next_control"),
        "source_next_control": source_boundary.get("next_control"),
        "source_nul_run": source_boundary.get("nul_run"),
    }


def is_direct_ext3(body: bytes) -> bool:
    return len(body) >= 4 and body[:2] == b"\xE5\x18"


def is_exact4_mixed_candidate(row: dict[str, Any]) -> bool:
    if row.get("route") != "scenario_first" or row.get("status") != "active":
        return False
    source = bytes.fromhex(str(row.get("source_body_hex") or ""))
    current = bytes.fromhex(str(row.get("baseline_body_hex") or ""))
    boundary = row.get("baseline_boundary") or {}
    next_control = str(boundary.get("next_control") or "")
    return (
        len(source) == 4
        and len(current) == 4
        and is_direct_ext3(current)
        and int(boundary.get("nul_run") or 0) == 2
        and next_control[:2] in {"08", "17"}
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--original", type=Path, default=ORIGINAL)
    ap.add_argument("--target", type=Path, default=TARGET)
    ap.add_argument("--contracts", type=Path, default=CONTRACTS)
    ap.add_argument("--speaker-audit", type=Path, default=SPEAKER_AUDIT)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    original = args.original.read_bytes()
    target = args.target.read_bytes()
    stock_base = len(target) - len(original)
    if stock_base < 0:
        raise SystemExit("target smaller than original")

    contracts_doc = load_json(args.contracts)
    contracts = list(contracts_doc.get("contracts") or [])

    # Check whether the runtime-proven Stage4 phrase can be represented by two
    # existing ordinary native dictionary tokens.  If not, a four-byte
    # parameterized E51D wrapper is preferable to reclaiming dictionary IDs.
    dictionary = make_dictionary_ext3(target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    tbl_target = Tbl.load(TBL_TARGET)
    native_text: list[tuple[int, str]] = []
    for index in range(4096):
        try:
            text = dictionary.expand(bytes(token_from_dict_index(index)), tbl_target)
        except Exception:  # noqa: BLE001
            continue
        native_text.append((index, text))
    stage4_target_text = str(next(r for r in contracts if r.get("address") == "60B400").get("baseline_text") or "")
    native_pair_solutions: list[dict[str, str]] = []
    by_text: dict[str, list[int]] = collections.defaultdict(list)
    for index, text in native_text:
        by_text[text].append(index)
    for left_index, left_text in native_text:
        if not stage4_target_text.startswith(left_text):
            continue
        right_text = stage4_target_text[len(left_text) :]
        for right_index in by_text.get(right_text, []):
            native_pair_solutions.append({
                "left_index": f"{left_index:04X}",
                "right_index": f"{right_index:04X}",
                "left": left_text,
                "right": right_text,
            })
    by_addr = {int(str(r["address"]), 16): r for r in contracts}
    anchor = by_addr.get(ANCHOR)
    if anchor is None:
        raise SystemExit("runtime contract for 60B400 missing")

    # Hard structural drift: control/NUL boundaries must remain source-exact.
    boundary_drift = []
    for row in contracts:
        if not str(row.get("route") or "").startswith("scenario_"):
            continue
        src = row.get("source_boundary") or {}
        cur = row.get("baseline_boundary") or {}
        if src.get("nul_run") != cur.get("nul_run") or src.get("next_control") != cur.get("next_control"):
            boundary_drift.append(row_view(row))

    # Tier A/B: exact four-byte source grammar replaced by direct ext3 and
    # immediately followed by an event/speaker control after a double-NUL gap.
    exact4 = [r for r in contracts if is_exact4_mixed_candidate(r)]
    exact4_views = [row_view(r) for r in exact4]
    exact4_anchor_source = [r for r in exact4 if bytes.fromhex(str(r.get("source_body_hex") or "")) == ANCHOR_SOURCE_BODY]
    exact4_context_sensitive = []
    for row in exact4:
        source = bytes.fromhex(str(row.get("source_body_hex") or ""))
        kinds = (pair_kind(source[:2]), pair_kind(source[2:4]))
        if "context_sensitive_control_or_glyph" in kinds:
            exact4_context_sensitive.append(row)

    # Broader inventory.  This is deliberately not labelled as confirmed bugs.
    broad_scenario_first = []
    for row in contracts:
        if row.get("route") != "scenario_first" or row.get("status") != "active":
            continue
        body = bytes.fromhex(str(row.get("baseline_body_hex") or ""))
        boundary = row.get("baseline_boundary") or {}
        next_control = str(boundary.get("next_control") or "")
        if is_direct_ext3(body) and int(boundary.get("nul_run") or 0) == 2 and next_control[:2] in {"08", "17"}:
            broad_scenario_first.append(row)

    continuation_18_ext3 = []
    continuation_exact_native2 = []
    for row in contracts:
        if row.get("route") != "scenario_continuation":
            continue
        source = bytes.fromhex(str(row.get("source_body_hex") or ""))
        body = bytes.fromhex(str(row.get("baseline_body_hex") or ""))
        boundary = row.get("baseline_boundary") or {}
        next_control = str(boundary.get("next_control") or "")
        if not (body.startswith(b"\x18\xE5\x18") and int(boundary.get("nul_run") or 0) >= 1 and next_control[:2] in {"08", "17"}):
            continue
        continuation_18_ext3.append(row)
        if (
            len(source) == 5
            and source[0] == 0x18
            and 0xF0 <= source[1] <= 0xFF
            and 0xF0 <= source[3] <= 0xFF
        ):
            continuation_exact_native2.append(row)

    # Existing actor-id/NUL collision audit is a separate structural family.
    speaker_doc: dict[str, Any] = {}
    if args.speaker_audit.is_file():
        speaker_doc = load_json(args.speaker_audit)
    speaker_counts = speaker_doc.get("counts") or {}

    # Prove the STAGE4 08 34 row itself was not patched.
    target_anchor_control = target[stock_base + ANCHOR_CONTROL : stock_base + ANCHOR_CONTROL + 3]
    original_anchor_control = original[ANCHOR_CONTROL : ANCHOR_CONTROL + 3]
    anchor_control_exact = target_anchor_control == original_anchor_control == ANCHOR_CONTROL_BYTES

    # Whole-script 08 34 00 preservation is useful evidence that this is state
    # leakage rather than broad corruption of actor-id rows.
    control_0834_sites = [
        logical
        for logical in range(0x600000, 0x640000 - 2)
        if original[logical : logical + 3] == ANCHOR_CONTROL_BYTES
    ]
    changed_0834 = [
        logical
        for logical in control_0834_sites
        if target[stock_base + logical : stock_base + logical + 3] != ANCHOR_CONTROL_BYTES
    ]

    grammar_counts = collections.Counter(tuple(v["source_pair_grammar"]) for v in exact4_views)
    next_lead_counts = collections.Counter(str(v.get("next_control") or "")[:2] for v in exact4_views)
    source_body_counts = collections.Counter(v["source_body_hex"] for v in exact4_views)

    anchor_view = row_view(anchor)
    anchor_view.update({
        "reported_visible_control_glyphs": "はせ 계열",
        "reported_following_portrait": "Sig shown instead of intended Char Aznable",
        "control_record": f"{ANCHOR_CONTROL:06X}",
        "control_record_original_hex": original_anchor_control.hex().upper(),
        "control_record_target_hex": target_anchor_control.hex().upper(),
        "following_dialogue": f"{ANCHOR_FOLLOW:06X}",
        "following_text": (by_addr.get(ANCHOR_FOLLOW) or {}).get("baseline_text"),
        "control_byte_exact": anchor_control_exact,
    })

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_global_scenario_control_portrait_state_risk.py",
        "read_only": True,
        "status": "plan_required",
        "inputs": {
            "original": {"path": str(args.original.relative_to(ROOT)), "size": len(original), "sha256": sha(original)},
            "target": {"path": str(args.target.relative_to(ROOT)), "size": len(target), "sha256": sha(target)},
            "contracts": str(args.contracts.relative_to(ROOT)),
            "speaker_collision_audit": str(args.speaker_audit.relative_to(ROOT)) if args.speaker_audit.is_file() else None,
        },
        "runtime_anchor": anchor_view,
        "root_cause_hypothesis": {
            "confidence": "strong",
            "statement": (
                "60B400 changed from mixed source grammar F191 081D to top-level direct E5 18. "
                "The following 08 34 00 actor/control row is byte-exact, so the most likely failure is parser/state leakage: "
                "the control row is consumed/displayed as text and therefore the next portrait/speaker update is not executed."
            ),
            "important_context_rule": "08 is context-sensitive: inside a source text body 08 xx may be visible glyph data, while at a record boundary 08 actor_id 00 is structural speaker/portrait state.",
        },
        "counts": {
            "scenario_boundary_drift": len(boundary_drift),
            "exact4_mixed_control_adjacent": len(exact4),
            "exact4_anchor_source_F191081D": len(exact4_anchor_source),
            "exact4_contains_context_sensitive_pair": len(exact4_context_sensitive),
            "exact4_next_08": next_lead_counts.get("08", 0),
            "exact4_next_17": next_lead_counts.get("17", 0),
            "broad_scenario_first_direct_E518_doubleNUL_control_adjacent": len(broad_scenario_first),
            "continuation_18E518_control_adjacent": len(continuation_18_ext3),
            "continuation_exact_18_native_native_to_18E518": len(continuation_exact_native2),
            "original_083400_sites": len(control_0834_sites),
            "current_083400_changed": len(changed_0834),
            "speaker_dictlead_nul_collisions": int(speaker_counts.get("speaker_dictlead_nul_collisions", 0)),
            "speaker_collision_hidden_dialogues": int(speaker_counts.get("immediate_hidden_dialogues", 0)),
            "speaker_collision_current_mixed_remaining": int(speaker_counts.get("japanese_or_mixed_remaining", 0)),
            "stage4_existing_native_pair_solutions": len(native_pair_solutions),
        },
        "stage4_storage_decision": {
            "target_text": stage4_target_text,
            "existing_native_pair_solutions": native_pair_solutions,
            "decision": "parameterized_E51D_event_safe_wrapper" if not native_pair_solutions else "ordinary_native_pair_possible",
            "reason": (
                "No existing two-token native composition reproduces the current Korean phrase exactly; reuse the already runtime-proven parameterized E51D outer route instead of reclaiming F0-FF IDs."
                if not native_pair_solutions
                else "At least one existing native two-token composition is available."
            ),
        },
        "hard_good_news": {
            "all_scenario_source_boundaries_preserved": len(boundary_drift) == 0,
            "stage4_083400_control_bytes_source_exact": anchor_control_exact,
            "all_083400_rows_source_exact": len(changed_0834) == 0,
            "existing_speaker_dictlead_collision_audit_clean": bool(speaker_doc.get("ok")) if speaker_doc else None,
        },
        "tiers": {
            "A_runtime_clone": {
                "interpretation": "Highest priority. Same exact source body as runtime-proven STAGE4 failure, current top-level direct E518, double-NUL, immediate 08/17 control.",
                "rows": [row_view(r) for r in exact4_anchor_source],
            },
            "B_exact4_mixed": {
                "interpretation": "Strong structural suspects, not all proven bugs. 59 mixed/raw four-byte source bodies were excluded from the prior 220 two-native-token rehome.",
                "grammar_counts": {" + ".join(k): v for k, v in sorted(grammar_counts.items())},
                "source_body_clone_counts": dict(source_body_counts.most_common()),
                "rows": exact4_views,
            },
            "C_continuation_18": {
                "interpretation": "Large context-sensitive continuation population. Do not bulk rewrite. Prioritize only exact/runtime-proven grammar and caller clones.",
                "control_adjacent_count": len(continuation_18_ext3),
                "strong_exact_rows": [row_view(r) for r in continuation_exact_native2],
            },
            "D_actor_control_parser": {
                "interpretation": "Separate 08 actor_id 00 dictionary-lead/NUL collision family. Current dedicated audit is clean but must remain a regression gate.",
                "counts": speaker_counts,
            },
        },
        "recommended_fix_plan": [
            "Do not modify 08 34 00 itself: it is source-exact and is evidence of the intended portrait/speaker transition.",
            "Phase 1: rehome the 9 F191081D clones first, including 60B400 and structurally closest 60A452 (same following 08 34). Preserve record extent, double-NUL, and following actor control byte-exact.",
            "Prefer the already-promoted parameterized E51D event-safe outer route when two existing native tokens cannot reproduce the Korean body exactly. Do not reclaim F0-FF dictionary IDs.",
            "For helpers, reuse the current translated phrase through the proven nested helper route; never place direct Hangul marker/glyph bytes directly in an event-sensitive helper.",
            "Phase 2: review the remaining exact4 mixed-grammar rows as one global candidate, with higher priority for the 25 rows followed by 08 actor/speaker controls and for repeated source-body clones.",
            "Phase 3: keep the 646 control-adjacent 18E518 continuations quarantined; only promote exact caller/runtime clones. The 3 exact 18+native+native residuals remain separately reviewed because two have false-target history.",
            "Phase 4: promote predecessor-aware actor-control checks into the canonical runtime audit: any event-sensitive direct E518 immediately before 08 actor_id 00 becomes a review/hard-gate according to its source grammar.",
            "Runtime matrix after candidate build: STAGE4 60B400 (glyph leak + Char portrait), 60A452 (same F191081D + 08 34), one different 08xx clone, one 17 28 clone, Gato parameterized E51D anchor 61035E, and Stage22 fixed E51D anchor 638CD5.",
        ],
        "boundary_drift": boundary_drift,
        "changed_083400": [f"{x:06X}" for x in changed_0834],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "status": report["status"],
        "counts": report["counts"],
        "anchor": report["runtime_anchor"],
        "report": str(args.out.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
