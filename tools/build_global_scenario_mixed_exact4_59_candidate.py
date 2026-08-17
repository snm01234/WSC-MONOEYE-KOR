#!/usr/bin/env python3
"""Build one candidate for all unique mixed-exact4 scenario risks.

The user requested the 9 F191081D clones plus the 59 mixed exact4 rows.  The
9-clone set is a subset of the 59-row set, so this builder patches all 59 unique
addresses once and reports 68 requested category memberships / 9 overlaps.

Storage policy:
* Prefer two existing safe native dictionary tokens when they reproduce the
  current Korean text exactly.
* Otherwise reuse the already-promoted parameterized E51D event-safe route:
  E5 1D <helper_id> 01.  New helper IDs are appended after the existing 220
  candidate's helper table and each helper contains only the existing E5 18
  phrase token + NUL (no direct Hangul glyph bytes).
* Existing runtime code, existing helper IDs, record extents, terminators,
  double-NUL separators and following 08/17 controls remain byte-exact.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    is_compact3_magic,
    load_rom,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    ws_header,
)

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
RISK = PATCH / "global_scenario_control_portrait_state_risk.json"
CONTRACTS = ROOT / "out/script/dialogue_runtime_contracts.json"
PREV_220 = PATCH / "global_event_native_rehome_220_report.json"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"

OUT_ROM = PATCH / "global_scenario_mixed_exact4_59_candidate.wsc"
OUT_SAVE = ROOT / "sram/global_scenario_mixed_exact4_59_candidate.sav"
OUT_REPORT = PATCH / "global_scenario_mixed_exact4_59_report.json"
OUT_REVIEW_JSON = PATCH / "global_scenario_mixed_exact4_59_review_sheet.json"
OUT_REVIEW_CSV = ROOT / "docs/GLOBAL_SCENARIO_MIXED_EXACT4_59_REVIEW_SHEET.csv"
OUT_REVIEW_MD = ROOT / "docs/GLOBAL_SCENARIO_MIXED_EXACT4_59_REVIEW_SHEET.md"

ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MAIN_SHA = "714200ffdcad34d01c12c8f560b8ca71163c165803e5e9894feb30f523e166c6"
ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"

EVENT_MAGIC = bytes.fromhex("E51D")
EXP_SEG = 0x26
FIXED_HELPER_OFF = 0x2000
FIXED_HELPER = bytes.fromhex("F36AF16E00")
PARAM_PTR_TABLE = 0x2100
PARAM_DATA_LIMIT = 0x2600
RUNTIME_START = 0x7EFD83
RUNTIME_END = 0x7EFE08
RUNTIME_SHA = "ddf099e94619d90a1caa8408c3f6f9b8a639ec45799bfe93d3ebb9949834b3cd"

REPRESENTATIVES = {
    "F191081D 동일 / STAGE4 실측": "60B400",
    "08xx 대표": "6184FD",
    "1728 대표": "61AA81",
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def identity(path: Path, data: bytes | None = None) -> dict[str, Any]:
    payload = path.read_bytes() if data is None else data
    return {"path": rel(path), "size": len(payload), "sha256": sha(payload)}


def diff_runs(a: bytes, b: bytes) -> list[tuple[int, int]]:
    if len(a) != len(b):
        raise BuildError("ROM size mismatch")
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(a)))
    return out


def covered(run: tuple[int, int], allowed: list[tuple[int, int]]) -> bool:
    lo, hi = run
    return any(a <= lo and hi <= b for a, b in allowed)


def raw_native_safe(dictionary: Any, index: int) -> bool:
    raw = bytes(dictionary.raw_entry(index, max_len=2048))
    if b"\xE5\x18" in raw or EVENT_MAGIC in raw:
        return False
    for i in range(max(0, len(raw) - 1)):
        if is_compact3_magic(raw[i], raw[i + 1]):
            return False
    return True


def native_text_map(dictionary: Any, tbl: Tbl) -> dict[str, list[int]]:
    by_text: dict[str, list[int]] = {}
    for index in range(4096):
        if not raw_native_safe(dictionary, index):
            continue
        try:
            text = dictionary.expand(bytes(token_from_dict_index(index)), tbl)
        except Exception:  # noqa: BLE001
            continue
        if not text:
            continue
        by_text.setdefault(text, []).append(index)
    return by_text


def choose_native_pair(text: str, by_text: dict[str, list[int]], dictionary: Any, tbl: Tbl) -> tuple[bytes, dict[str, Any]] | None:
    # Deterministic: shorter left text first, then token index.
    for left_text in sorted(by_text, key=lambda x: (len(x), x)):
        if not text.startswith(left_text):
            continue
        right_text = text[len(left_text) :]
        if not right_text or right_text not in by_text:
            continue
        for left in by_text[left_text]:
            for right in by_text[right_text]:
                body = bytes(token_from_dict_index(left)) + bytes(token_from_dict_index(right))
                if len(body) != 4:
                    continue
                if dictionary.expand(body, tbl) != text:
                    continue
                return body, {
                    "left_index": f"{left:04X}",
                    "right_index": f"{right:04X}",
                    "left_text": left_text,
                    "right_text": right_text,
                }
    return None


def md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def main() -> int:
    main_before = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    save_before = LIVE_SAVE.read_bytes()
    if len(main_before) != ROM_SIZE or sha(main_before) != MAIN_SHA:
        raise BuildError("current main TIP identity drifted")
    if sha(original) != ORIGINAL_SHA:
        raise BuildError("original ROM identity drifted")
    if len(save_before) != SAVE_SIZE:
        raise BuildError("live SaveRAM missing/wrong size")

    risk = json.loads(RISK.read_text(encoding="utf-8"))
    rows = list(((risk.get("tiers") or {}).get("B_exact4_mixed") or {}).get("rows") or [])
    clone_rows = list(((risk.get("tiers") or {}).get("A_runtime_clone") or {}).get("rows") or [])
    if len(rows) != 59 or len(clone_rows) != 9:
        raise BuildError(f"risk inventory drifted: mixed={len(rows)} clone={len(clone_rows)}")
    row_addrs = {str(r["address"]) for r in rows}
    clone_addrs = {str(r["address"]) for r in clone_rows}
    if not clone_addrs <= row_addrs:
        raise BuildError("9-clone set is not a subset of 59 mixed rows")

    manifest = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    all_contracts = list(manifest.get("contracts") or [])
    contracts = {str(r["address"]): r for r in all_contracts}
    sb = stock_base(main_before)
    dictionary = make_dictionary_ext3(main_before, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    tbl = Tbl.load(TBL_PATH)
    by_text = native_text_map(dictionary, tbl)

    # Existing generalized E51D runtime must be exactly the promoted implementation.
    runtime_blob = main_before[sb + RUNTIME_START : sb + RUNTIME_END]
    if len(runtime_blob) != RUNTIME_END - RUNTIME_START or sha(runtime_blob) != RUNTIME_SHA:
        raise BuildError("promoted E51D runtime blob drifted")

    prev = json.loads(PREV_220.read_text(encoding="utf-8"))
    old_helpers = list(prev.get("helpers") or [])
    if len(old_helpers) != 58:
        raise BuildError("existing helper inventory drifted")
    if main_before[(EXP_SEG << 16) + FIXED_HELPER_OFF : (EXP_SEG << 16) + FIXED_HELPER_OFF + len(FIXED_HELPER)] != FIXED_HELPER:
        raise BuildError("fixed E51D helper drifted")

    # Verify all existing table entries/helpers byte-exact and find append points.
    expected_ptrs: dict[int, int] = {0: FIXED_HELPER_OFF}
    for h in old_helpers:
        expected_ptrs[int(h["id"])] = int(str(h["pointer"]), 16)
    if sorted(expected_ptrs) != list(range(59)):
        raise BuildError("existing helper IDs are not contiguous 0..58")
    for idx, ptr in expected_ptrs.items():
        at = (EXP_SEG << 16) + PARAM_PTR_TABLE + idx * 2
        got = struct.unpack("<H", main_before[at:at + 2])[0]
        if got != ptr:
            raise BuildError(f"existing helper pointer drift id={idx}: {got:04X}!={ptr:04X}")
    for h in old_helpers:
        ptr = int(str(h["pointer"]), 16)
        token = bytes.fromhex(str(h["nested_ext3"]))
        got = main_before[(EXP_SEG << 16) + ptr : (EXP_SEG << 16) + ptr + 5]
        if got != token + b"\x00":
            raise BuildError(f"existing helper body drift id={h['id']}")

    last_old_ptr = max(int(str(h["pointer"]), 16) for h in old_helpers)
    old_data_end = last_old_ptr + 5
    old_table_end = PARAM_PTR_TABLE + (len(old_helpers) + 1) * 2
    if any(x != 0xFF for x in main_before[(EXP_SEG << 16) + old_table_end : (EXP_SEG << 16) + 0x2200]):
        raise BuildError("new helper table reservation is not free")
    if any(x != 0xFF for x in main_before[(EXP_SEG << 16) + old_data_end : (EXP_SEG << 16) + PARAM_DATA_LIMIT]):
        raise BuildError("new helper data reservation is not free")

    # Classify 59 unique rows.
    plans: list[dict[str, Any]] = []
    nested_tokens: set[str] = set()
    for r in rows:
        address = str(r["address"])
        contract = contracts.get(address)
        if contract is None:
            raise BuildError(f"missing runtime contract {address}")
        body_start = int(str(contract["body_start"]), 16)
        body_end = int(str(contract["body_end_exclusive"]), 16)
        if body_end - body_start != 4:
            raise BuildError(f"body extent is not four bytes at {address}")
        before = main_before[sb + body_start : sb + body_end]
        expected = bytes.fromhex(str(r["current_body_hex"]))
        if before != expected or not before.startswith(b"\xE5\x18"):
            raise BuildError(f"current body drift at {address}")
        text = str(r.get("current_text") or "")
        native = choose_native_pair(text, by_text, dictionary, tbl)
        if native is None:
            nested_tokens.add(before.hex().upper())
            plans.append({"row": r, "contract": contract, "body_start": body_start, "body_end": body_end, "before": before, "text": text, "mode": "wrapper"})
        else:
            body, meta = native
            plans.append({"row": r, "contract": contract, "body_start": body_start, "body_end": body_end, "before": before, "text": text, "mode": "native", "after": body, "native": meta})

    new_tokens = sorted(nested_tokens)
    if len(new_tokens) != 30:
        raise BuildError(f"expected 30 unique new nested helpers, got {len(new_tokens)}")
    first_new_id = 59
    helper_id = {token: first_new_id + i for i, token in enumerate(new_tokens)}
    if max(helper_id.values()) > 0xFE:
        raise BuildError("helper id overflow")

    out = bytearray(main_before)
    allowed: list[tuple[int, int]] = []

    cursor = old_data_end
    new_helper_rows: list[dict[str, Any]] = []
    new_table_start = (EXP_SEG << 16) + PARAM_PTR_TABLE + first_new_id * 2
    new_data_start = (EXP_SEG << 16) + old_data_end
    for token_hex in new_tokens:
        idx = helper_id[token_hex]
        raw = bytes.fromhex(token_hex)
        if len(raw) != 4 or raw[:2] != b"\xE5\x18" or 0 in raw[2:4]:
            raise BuildError(f"unsafe nested token {token_hex}")
        ptr = cursor
        payload = raw + b"\x00"
        if cursor + len(payload) > PARAM_DATA_LIMIT:
            raise BuildError("helper data overflow")
        data_at = (EXP_SEG << 16) + cursor
        out[data_at:data_at + len(payload)] = payload
        table_at = (EXP_SEG << 16) + PARAM_PTR_TABLE + idx * 2
        out[table_at:table_at + 2] = struct.pack("<H", ptr)
        new_helper_rows.append({"id": idx, "pointer": f"{ptr:04X}", "nested_ext3": token_hex})
        cursor += len(payload)
    # Table/data entries are physically contiguous, so diff_runs() coalesces
    # them into one run each.  Allow the exact contiguous append ranges.
    new_table_end = (EXP_SEG << 16) + PARAM_PTR_TABLE + (max(helper_id.values()) + 1) * 2
    new_data_end = (EXP_SEG << 16) + cursor
    allowed.append((new_table_start, new_table_end))
    allowed.append((new_data_start, new_data_end))

    target_reports: list[dict[str, Any]] = []
    native_count = 0
    wrapped_count = 0
    for plan in plans:
        r = plan["row"]
        address = str(r["address"])
        if plan["mode"] == "native":
            after = bytes(plan["after"])
            method = "existing_native_pair"
            helper = None
            native_count += 1
        else:
            token_hex = plan["before"].hex().upper()
            idx = helper_id[token_hex]
            after = EVENT_MAGIC + bytes([idx, 0x01])
            method = f"event_safe_E51D_param_{idx:02X}"
            helper = next(h for h in new_helper_rows if h["id"] == idx)
            wrapped_count += 1
        if len(after) != 4 or after.startswith(b"\xE5\x18"):
            raise BuildError(f"unsafe resulting body at {address}")
        out[sb + plan["body_start"] : sb + plan["body_end"]] = after
        allowed.append((sb + plan["body_start"], sb + plan["body_end"]))
        target_reports.append({
            "address": address,
            "source_body_hex": str(r["source_body_hex"]),
            "before": plan["before"].hex().upper(),
            "after": after.hex().upper(),
            "text": plan["text"],
            "next_control": r.get("next_control"),
            "source_pair_grammar": r.get("source_pair_grammar"),
            "is_F191081D_clone": address in clone_addrs,
            "method": method,
            "native": plan.get("native"),
            "helper": helper,
        })

    if native_count != 25 or wrapped_count != 34:
        raise BuildError(f"unexpected native/wrapper split {native_count}/{wrapped_count}")

    update_ws_checksum(out)
    candidate = bytes(out)
    allowed.append((len(candidate) - 2, len(candidate)))

    # Structural hard checks: record size, terminator, separator and next control.
    failures: list[str] = []
    report_by_addr = {r["address"]: r for r in target_reports}
    for plan in plans:
        address = str(plan["row"]["address"])
        c = plan["contract"]
        term = int(str(c["source_terminator"]), 16)
        srcb = c.get("source_boundary") or {}
        curb = c.get("baseline_boundary") or {}
        if int(srcb.get("nul_run") or 0) != 2 or srcb.get("next_control") != curb.get("next_control"):
            failures.append(f"source_boundary:{address}")
            continue
        next_addr = int(str(srcb["next_address"]), 16)
        if candidate[sb + term] != 0:
            failures.append(f"terminator:{address}")
        if candidate[sb + term:sb + next_addr] != main_before[sb + term:sb + next_addr]:
            failures.append(f"separator:{address}")
        ctrl_hex = str(srcb.get("next_control") or "")
        if not ctrl_hex or ctrl_hex[:2] not in {"08", "17"}:
            failures.append(f"control_shape:{address}")
        else:
            ctrl = bytes.fromhex(ctrl_hex)
            if candidate[sb + next_addr:sb + next_addr + len(ctrl)] != main_before[sb + next_addr:sb + next_addr + len(ctrl)]:
                failures.append(f"following_control:{address}")
        actual = candidate[sb + plan["body_start"] : sb + plan["body_end"]]
        if actual.hex().upper() != report_by_addr[address]["after"]:
            failures.append(f"body:{address}")
    if failures:
        raise BuildError(f"structural failures: {failures[:20]}")

    # Runtime blob and all old helpers must remain byte-exact.
    if candidate[sb + RUNTIME_START : sb + RUNTIME_END] != runtime_blob:
        raise BuildError("E51D runtime code changed")
    for idx, ptr in expected_ptrs.items():
        at = (EXP_SEG << 16) + PARAM_PTR_TABLE + idx * 2
        if struct.unpack("<H", candidate[at:at + 2])[0] != ptr:
            raise BuildError(f"old pointer changed id={idx}")
    for h in old_helpers:
        ptr = int(str(h["pointer"]), 16)
        token = bytes.fromhex(str(h["nested_ext3"]))
        if candidate[(EXP_SEG << 16) + ptr : (EXP_SEG << 16) + ptr + 5] != token + b"\x00":
            raise BuildError(f"old helper changed id={h['id']}")

    runs = diff_runs(main_before, candidate)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"unexpected ROM diff: {unexpected[:20]}")
    checksum_ok = int(ws_header(candidate)["checksum"]) == (sum(candidate[:-2]) & 0xFFFF)
    if not checksum_ok:
        raise BuildError("WonderSwan checksum invalid")
    if bytes(load_rom(MAIN)) != main_before or LIVE_SAVE.read_bytes() != save_before:
        raise BuildError("main TIP or live SaveRAM changed during build")

    OUT_ROM.write_bytes(candidate)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)

    # Build review contexts: five scenario dialogue rows before and after each representative.
    scenario_rows = sorted(
        [r for r in all_contracts if str(r.get("route") or "").startswith("scenario_")],
        key=lambda r: int(str(r["address"]), 16),
    )
    target_by_addr = {r["address"]: r for r in target_reports}
    review_groups: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    for group, anchor in REPRESENTATIVES.items():
        if anchor not in target_by_addr:
            raise BuildError(f"review representative missing from target set: {anchor}")
        pos = next(i for i, r in enumerate(scenario_rows) if str(r["address"]) == anchor)
        if pos < 5 or pos + 5 >= len(scenario_rows):
            raise BuildError(f"not enough context around {anchor}")
        context: list[dict[str, Any]] = []
        for i in range(pos - 5, pos + 6):
            r = scenario_rows[i]
            addr = str(r["address"])
            relidx = i - pos
            target_info = target_by_addr.get(addr)
            item = {
                "relative": relidx,
                "address": addr,
                "target": addr == anchor,
                "also_modified": target_info is not None,
                "original_japanese": r.get("original_japanese"),
                "korean": r.get("baseline_text"),
                "route": r.get("route"),
                "next_control": (r.get("baseline_boundary") or {}).get("next_control"),
                "before_body": None if target_info is None else target_info["before"],
                "candidate_body": None if target_info is None else target_info["after"],
                "method": None if target_info is None else target_info["method"],
            }
            context.append(item)
            csv_rows.append({"group": group, **item})
        review_groups.append({"group": group, "anchor": anchor, "target_detail": target_by_addr[anchor], "context": context})

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_global_scenario_mixed_exact4_59_candidate.py",
        "ok": True,
        "status": "runtime_test_pending",
        "input": {
            "main": identity(MAIN, main_before),
            "original": identity(ORIGINAL, original),
            "live_save": identity(LIVE_SAVE, save_before),
            "risk_report": rel(RISK),
            "previous_220_report": rel(PREV_220),
        },
        "output": {
            "rom": identity(OUT_ROM, candidate),
            "save": identity(OUT_SAVE, save_before),
            "checksum": f"{ws_header(candidate)['checksum']:04X}",
            "review_markdown": rel(OUT_REVIEW_MD),
            "review_csv": rel(OUT_REVIEW_CSV),
        },
        "counts": {
            "requested_category_memberships": 68,
            "F191081D_clone_memberships": 9,
            "mixed_exact4_memberships": 59,
            "overlap": 9,
            "unique_targets": 59,
            "next_08xx": sum(str(r.get("next_control") or "").startswith("08") for r in target_reports),
            "next_1728_or_17xx": sum(str(r.get("next_control") or "").startswith("17") for r in target_reports),
            "existing_native_pair": native_count,
            "event_safe_E51D_parameterized": wrapped_count,
            "new_unique_nested_helpers": len(new_tokens),
            "existing_helper_ids_preserved": len(old_helpers) + 1,
        },
        "helper_extension": {
            "existing_ids": "00..3A",
            "new_ids": f"{min(helper_id.values()):02X}..{max(helper_id.values()):02X}",
            "old_data_end_exclusive": f"26:{old_data_end:04X}",
            "new_data_end_exclusive": f"26:{cursor:04X}",
            "runtime_code_changed": False,
            "new_helpers": new_helper_rows,
        },
        "checks": {
            "all_59_top_level_E518_removed": all(not bytes.fromhex(r["after"]).startswith(b"\xE5\x18") for r in target_reports),
            "all_target_extents_4_bytes": all(len(bytes.fromhex(r["after"])) == 4 for r in target_reports),
            "all_double_nul_and_following_controls_preserved": not failures,
            "old_runtime_blob_byte_exact": True,
            "old_helper_ids_byte_exact": True,
            "unexpected_diff_runs": 0,
            "checksum_valid": checksum_ok,
            "main_tip_unchanged": bytes(load_rom(MAIN)) == main_before,
            "live_saveram_unchanged": LIVE_SAVE.read_bytes() == save_before,
        },
        "targets": target_reports,
        "review_representatives": review_groups,
        "promotion": "blocked_pending_representative_runtime_review",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    OUT_REVIEW_JSON.write_text(json.dumps({"groups": review_groups}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    OUT_REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_REVIEW_CSV.open("w", encoding="utf-8-sig", newline="") as fp:
        fields = ["group", "relative", "address", "target", "also_modified", "original_japanese", "korean", "route", "next_control", "before_body", "candidate_body", "method"]
        w = csv.DictWriter(fp, fieldnames=fields)
        w.writeheader()
        w.writerows(csv_rows)

    md: list[str] = [
        "# mixed exact4 제어/초상 위험 후보 — 대표 검수 시트",
        "",
        f"후보 ROM: `{rel(OUT_ROM)}`  ",
        f"SHA-256: `{sha(candidate).upper()}`  ",
        f"paired SaveRAM: `{rel(OUT_SAVE)}`",
        "",
        "요청한 `9 + 59 = 68`은 분류 membership 수이며, `F191081D` 9건이 59건 안에 모두 포함되므로 실제 고유 수정 주소는 **59건**이다.",
        f"59건 처리: **native pair {native_count}건 / parameterized E51D {wrapped_count}건**. 다음 제어는 `08xx` 25건, `17xx` 34건이다.",
        "",
        "공통 확인: 대상 대사 뒤 제어문이 글리프로 노출되지 않는지, 다음 화자/초상이 정상인지, 대사가 반복/스킵되지 않는지 확인한다.",
        "",
        "> 참고: 주변 `scenario_continuation` 행의 선두 `こ`는 현재 정적 runtime-contract의 quarantine 해석값일 수 있으며, 이번 대표 대상의 기대 화면 출력으로 간주하지 않는다.",
        "",
    ]
    for group in review_groups:
        td = group["target_detail"]
        md += [
            f"## {md_cell(group['group'])} — `{group['anchor']}`",
            "",
            f"- 현재 body: `{td['before']}`",
            f"- 후보 body: `{td['after']}`",
            f"- 저장 방식: `{td['method']}`",
            f"- 직후 제어: `{td['next_control']}`",
            "",
            "| 상대 | 주소 | 원문 | 현재 한글 | route | 직후 control | 후보 변경 |",
            "|---:|---|---|---|---|---|---|",
        ]
        for c in group["context"]:
            change = "**대표 대상**" if c["target"] else (f"동시 수정 `{c['candidate_body']}`" if c["also_modified"] else "-")
            md.append(
                f"| {c['relative']:+d} | `{c['address']}` | {md_cell(c['original_japanese'])} | {md_cell(c['korean'])} | `{md_cell(c['route'])}` | `{md_cell(c['next_control'])}` | {change} |"
            )
        md.append("")
    md += [
        "## 판정 기준",
        "",
        "- `60B400`: `……네？` 다음에 `はせ` 계열 제어문이 나오지 않고, 후속 샤아 계열 대사의 초상이 시그가 아닌 정상 화자로 전환되는지 확인.",
        "- `6184FD`: `08 63` 계열 화자/초상 전환이 대사로 노출되지 않는지 확인.",
        "- `61AA81`: `17 28` 경계에서 제어문 노출, 이벤트 반복/스킵, 진행 중단이 없는지 확인.",
        "- 세 대표가 모두 정상이라도 메인 승격 전에는 전체 정적 감사 결과를 함께 확인한다.",
    ]
    OUT_REVIEW_MD.write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")

    print(json.dumps({
        "ok": True,
        "candidate": report["output"]["rom"],
        "save": report["output"]["save"],
        "checksum": report["output"]["checksum"],
        "counts": report["counts"],
        "helper_extension": {k: v for k, v in report["helper_extension"].items() if k != "new_helpers"},
        "review_markdown": rel(OUT_REVIEW_MD),
        "review_csv": rel(OUT_REVIEW_CSV),
        "report": rel(OUT_REPORT),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
