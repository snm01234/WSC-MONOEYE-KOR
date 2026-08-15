#!/usr/bin/env python3
"""Build TIP-bound candidate applying approved E62F battle-voice translations.

Preserves every record prefix and NUL terminator. Body bytes are replaced with
a five-bank E5 18 alias token plus 0x01 padding. Hangul phrases (including the
inline ``<E62F>`` layout tag) are allocated into ext3 dictionary banks.

Does not modify the live main TIP or SaveRAM.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import analyze_ext3_multibank_alias_ranges as five
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from build_encyclopedia_character_all_remaining_candidate import allocate_ext3
from build_encyclopedia_character_five_bank_batch02_candidate import PAGES
from build_remaining_dialogue_candidate import covered, diff_runs, encode_phrase, verify_non_target_invariance
from mixed_residual_classification import is_japanese_character
from monoeye_rom import BANK_SIZE, Tbl, load_rom, stock_base, update_ws_checksum, ws_header
from normalize_ko_text import normalize_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SHEET = ROOT / "out/script/battle_voice_inline_control_translation_sheet.csv"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/battle_voice_inline_control_candidate.wsc"
OUT_SAVE = ROOT / "sram/battle_voice_inline_control_candidate.sav"
REPORT = ROOT / "out/patch/battle_voice_inline_control_candidate_report.json"

MAIN_SHA256 = "4e779568af535f25319595049c559165dbbaac96e67c4c5799a4b99163674e0a"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_ROWS = 268
TAG_RE = re.compile(r"<[^>]+>")


class BuildError(RuntimeError):
    pass


def sha256(data: bytes | bytearray) -> str:
    import hashlib

    return hashlib.sha256(bytes(data)).hexdigest()


def identity(path: Path, payload: bytes | None = None) -> dict[str, Any]:
    data = path.read_bytes() if payload is None else payload
    try:
        shown = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        shown = str(path.resolve())
    return {"path": shown, "size": len(data), "sha256": sha256(data)}


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def visible_has_japanese(text: str) -> bool:
    return any(is_japanese_character(ch) for ch in TAG_RE.sub("", text))


def load_rows(parent: bytes, tbl: Tbl) -> list[dict[str, Any]]:
    with SHEET.open(encoding="utf-8-sig", newline="") as stream:
        sources = [dict(row) for row in csv.DictReader(stream)]
    if len(sources) != EXPECTED_ROWS:
        raise BuildError(f"sheet row count drifted: {len(sources)}")
    sb = stock_base(parent)
    tip_sha = sha256(parent)
    prepared: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda row: int(str(row["abs"]), 16)):
        address = str(source["abs"]).upper()
        logical = int(address, 16)
        prefix = bytes.fromhex(str(source.get("prefix_hex") or ""))
        payload_capacity = int(source["payload_capacity"])
        body_capacity = int(source["body_capacity"])
        expected_payload = bytes.fromhex(str(source["current_payload_hex"]))
        expected_body_digest = str(source["source_body_sha256"]).lower()
        parent_tip_sha = str(source.get("parent_tip_sha256") or "").lower()
        if parent_tip_sha and parent_tip_sha != tip_sha:
            raise BuildError(f"sheet parent TIP digest drifted at {address}")
        if payload_capacity != len(expected_payload) or body_capacity != payload_capacity - len(prefix):
            raise BuildError(f"sheet boundary drifted at {address}")
        if body_capacity < 4:
            raise BuildError(f"short body requires separate allocation at {address}")
        current = parent[sb + logical : sb + logical + payload_capacity]
        if current != expected_payload or not current.startswith(prefix):
            raise BuildError(f"parent payload drifted at {address}")
        if parent[sb + logical + payload_capacity] != 0:
            raise BuildError(f"terminator drifted at {address}")
        if sha256(current[len(prefix) :]) != expected_body_digest:
            raise BuildError(f"body digest drifted at {address}")
        if source.get("translation_source") != "llm" or source.get("review_status") != "approved":
            raise BuildError(f"translation is not approved at {address}")
        control_count = int(source.get("inline_control_count") or 0)
        ko = normalize_ko_text(str(source.get("ko") or ""))
        if not ko or visible_has_japanese(ko):
            raise BuildError(f"invalid Korean target at {address}: {ko!r}")
        if ko.count("<E62F>") != control_count:
            raise BuildError(f"E62F tag count drifted at {address}")
        encoded = encode_phrase(ko, tbl)
        if not encoded or b"\x00" in encoded:
            raise BuildError(f"invalid encoded phrase at {address}")
        if control_count and encoded.count(b"\xe6\x2f") != control_count:
            raise BuildError(f"encoded E62F count drifted at {address}")
        prepared.append(
            {
                "abs": address,
                "logical": logical,
                "batch_id": str(source.get("batch_id") or ""),
                "scope": str(source.get("scope") or ""),
                "gap": str(source.get("gap") or ""),
                "jp": str(source.get("original_jp") or ""),
                "before": str(source.get("current_text") or ""),
                "ko": ko,
                "encoded": encoded,
                "prefix": prefix,
                "prefix_len": len(prefix),
                "payload_capacity": payload_capacity,
                "body_capacity": body_capacity,
                "inline_control_count": control_count,
                "boundary_review_required": str(source.get("boundary_review_required") or ""),
            }
        )
    return prepared


def main() -> int:
    parent = bytes(load_rom(MAIN))
    if len(parent) != ROM_SIZE or sha256(parent) != MAIN_SHA256:
        raise BuildError("main TIP identity drifted")
    save_snapshot = MAIN_SAVE.read_bytes()
    if len(save_snapshot) != SAVE_SIZE:
        raise BuildError("main SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    parent_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    rows = load_rows(parent, tbl)
    assignments, states = allocate_ext3(parent, rows)

    candidate = bytearray(parent)
    pointer_extents: list[tuple[int, int]] = []
    phrase_extents: list[tuple[int, int]] = []
    for page, state in states.items():
        start = int(state["start"])
        candidate[start : start + BANK_SIZE] = state["bank"]
        new_locals = {
            int(info["local"])
            for info in assignments.values()
            if int(info["page"]) == page and not bool(info["reused"])
        }
        pointer_extents.extend((start + local * 2, start + local * 2 + 2) for local in sorted(new_locals))
        if int(state["cursor"]) > int(state["cursor_before"]):
            phrase_extents.append((start + int(state["cursor_before"]), start + int(state["cursor"])))

    sb = stock_base(parent)
    target_extents: list[tuple[int, int]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        info = assignments[row["ko"]]
        token = bytes(info["token"])
        replacement = token + b"\x01" * (row["body_capacity"] - len(token))
        body_start = sb + row["logical"] + row["prefix_len"]
        candidate[body_start : body_start + row["body_capacity"]] = replacement
        target_extents.append((body_start, body_start + row["body_capacity"]))
        applied.append(
            {
                "abs": row["abs"],
                "batch_id": row["batch_id"],
                "scope": row["scope"],
                "gap": row["gap"],
                "jp": row["jp"],
                "before": row["before"],
                "after": row["ko"],
                "prefix_hex": row["prefix"].hex().upper(),
                "payload_capacity": row["payload_capacity"],
                "body_capacity": row["body_capacity"],
                "inline_control_count": row["inline_control_count"],
                "boundary_review_required": row["boundary_review_required"],
                "strategy": "five_bank_e518_alias_reuse" if info["reused"] else "five_bank_e518_alias_new",
                "page": int(info["page"]),
                "physical_bank": f"{int(info['segment']):02X}",
                "local": f"{int(info['local']):04X}",
                "pointer": f"{int(info['pointer']):04X}",
                "token_hex": token.hex().upper(),
            }
        )

    checksum = update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_dictionary = make_dictionary_ext3(candidate_bytes, ext_meta, ext3_meta)
    target_failures: list[dict[str, Any]] = []
    for row in rows:
        start = sb + row["logical"]
        payload = candidate_bytes[start : start + row["payload_capacity"]]
        actual = candidate_dictionary.expand(payload[row["prefix_len"] :], tbl).rstrip("\u3000 \t")
        reasons: list[str] = []
        if payload[: row["prefix_len"]] != row["prefix"]:
            reasons.append("prefix_changed")
        if actual != row["ko"]:
            reasons.append("render_mismatch")
        if visible_has_japanese(actual):
            reasons.append("japanese_residual")
        if actual.count("<E62F>") != row["inline_control_count"]:
            reasons.append("e62f_count_mismatch")
        if candidate_bytes[start + row["payload_capacity"]] != 0:
            reasons.append("terminator_changed")
        if reasons:
            target_failures.append(
                {"abs": row["abs"], "expected": row["ko"], "actual": actual, "reasons": reasons}
            )

    invariance = verify_non_target_invariance(
        parent,
        candidate_bytes,
        before_dictionary=parent_dictionary,
        after_dictionary=candidate_dictionary,
        tbl=tbl,
        excluded={row["logical"] for row in rows},
    )
    runs = diff_runs(parent, candidate_bytes)
    allowed = target_extents + pointer_extents + phrase_extents + [(len(parent) - 2, len(parent))]
    unaccounted = [
        {"start": f"{lo:07X}", "end_exclusive": f"{hi:07X}"}
        for lo, hi in runs
        if not covered((lo, hi), allowed)
    ]
    runtime_exact = (
        parent[sb + 0x7A0000 : sb + 0x7B0000] == candidate_bytes[sb + 0x7A0000 : sb + 0x7B0000]
        and parent[sb + 0x7F0000 : sb + 0x800000 - 2]
        == candidate_bytes[sb + 0x7F0000 : sb + 0x800000 - 2]
    )
    old_ext3_exact = all(
        parent[s * BANK_SIZE : (s + 1) * BANK_SIZE]
        == candidate_bytes[s * BANK_SIZE : (s + 1) * BANK_SIZE]
        for s in range(0x11, 0x21)
    )
    page_hits_parent = {p: five.scan_range_hits(parent, p) for p in range(PAGES)}
    page_hits_candidate = {p: five.scan_range_hits(candidate_bytes, p) for p in range(PAGES)}
    expected_page_counts = {
        p: len(page_hits_parent[p]) + sum(row["page"] == p for row in applied) for p in range(PAGES)
    }
    checks = {
        "main_tip_unchanged": sha256(MAIN.read_bytes()) == MAIN_SHA256,
        "all_targets_approved": len(rows) == EXPECTED_ROWS,
        "all_targets_render_exact": not target_failures,
        "non_target_invariance": invariance.get("ok") is True,
        "page_reference_counts_exact": all(
            len(page_hits_candidate[p]) == expected_page_counts[p] for p in range(PAGES)
        ),
        "runtime_banks_7a_7f_exact": runtime_exact,
        "old_ext3_banks_11_20_exact": old_ext3_exact,
        "diffs_bounded": not unaccounted,
        "screen_confirmed_5DA6E5": any(
            row["abs"] == "5DA6E5" and row["after"] == "좋아、지금이다！<E62F>쏴라！！" for row in applied
        ),
    }
    if not all(checks.values()):
        raise BuildError(
            json.dumps(
                {
                    "checks": checks,
                    "target_failures": target_failures[:20],
                    "unaccounted": unaccounted[:20],
                    "invariance_failures": (invariance.get("failures") or [])[:10],
                },
                ensure_ascii=False,
            )
        )

    atomic_bytes(OUT_ROM, candidate_bytes)
    atomic_bytes(OUT_SAVE, save_snapshot)
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_battle_voice_inline_control_candidate.py",
        "ok": True,
        "status": "candidate_static_verified_ready_for_promotion",
        "promotion_allowed": True,
        "main_tip": identity(MAIN, parent),
        "candidate": identity(OUT_ROM, candidate_bytes),
        "candidate_save": {
            **identity(OUT_SAVE, save_snapshot),
            "policy": "test-only current main SaveRAM snapshot; never promote",
        },
        "sheet": identity(SHEET),
        "checksum": f"{checksum:04X}",
        "ws_checksum": f"{ws_header(candidate_bytes)['checksum']:04X}",
        "counts": {
            "targets": len(rows),
            "unique_phrases": len(assignments),
            "reused_phrases": sum(1 for info in assignments.values() if info["reused"]),
            "new_phrases": sum(1 for info in assignments.values() if not info["reused"]),
            "boundary_review_required": sum(
                1 for row in rows if row["boundary_review_required"] == "yes"
            ),
            "target_failures": len(target_failures),
            "non_target_records_checked": int(invariance.get("records_checked") or 0),
            "non_target_failures": int(invariance.get("failure_count") or 0),
            "unaccounted_diff_runs": len(unaccounted),
            "changed_bytes": sum(hi - lo for lo, hi in runs),
            "diff_runs": len(runs),
        },
        "checks": checks,
        "applied": applied,
    }
    atomic_json(REPORT, report)
    print(json.dumps({k: report[k] for k in report if k != "applied"}, ensure_ascii=False, indent=2))
    print(f"applied_rows={len(applied)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
