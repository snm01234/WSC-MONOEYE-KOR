#!/usr/bin/env python3
"""Build the exact-continuation native 2-token recovery candidate.

Parent: current main TIP FC7C3A...

Scope:
1. Retarget every syntactically parsed script consumer of duplicate bank10 IDs
   0F70/0F72/0FC0 to byte-identical canonical IDs 0F00/0F01/0F07.
2. Repoint five audited IDs to self-contained Korean helper phrases stored in a
   deliberately roomy 0x100-byte bank10 tail pool.  The helpers are spread out
   so the normal append cursor advances across almost the whole reserved pool.
3. Rewrite the nine exact-continuation records to Original-shaped
   ``18 + 2-byte dict + 2-byte dict`` payloads.
4. Update only the WonderSwan checksum in addition to the explicit scope.

The main TIP and live SaveRAM are never modified.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p2_duplicate_detachment import external_occurrence_map, nested_occurrence_map  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_remaining_dialogue_candidate import covered, diff_runs  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from mixed_residual_reference_union import iter_token_refs_with_offsets  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    load_rom,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
ID_AUDIT = ROOT / "out/patch/exact_continuation_native_recovery_id_audit.json"
OUT = ROOT / "out/patch/exact_continuation_native_recovery_candidate.wsc"
OUT_SAVE = ROOT / "sram/exact_continuation_native_recovery_candidate.sav"
REPORT = ROOT / "out/patch/exact_continuation_native_recovery_candidate_report.json"
TEST_MATRIX = ROOT / "out/patch/exact_continuation_native_recovery_test_matrix.json"
TEST_MATRIX_MD = ROOT / "docs/EXACT_CONTINUATION_NATIVE_RECOVERY_TEST_MATRIX.md"

EXPECTED_MAIN_SHA = "fc7c3a426c866f8b60f5056571349c79d6ba11a2632beee4209dfebbf8a0c5e9"
EXPECTED_ORIGINAL_SHA = "376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
EXPECTED_CURRENT_TAIL = 0x123B
POOL_ALIGN = 0x20
POOL_SIZE = 0x100

# Duplicate reclaim -> canonical keeper.  Every syntactic script consumer is
# retargeted before the reclaim ID is repointed.
DUPLICATES: dict[int, int] = {
    0x0F70: 0x0F00,
    0x0F72: 0x0F01,
    0x0FC0: 0x0F07,
}

# Five reclaimed IDs -> helper.  Physical offsets are relative to POOL_START.
# The last, longest helper sits near the end so append-style writers advance
# their cursor across almost the complete 0x100-byte pool instead of reusing
# the intentionally generous gaps.
HELPERS: dict[int, dict[str, Any]] = {
    0x0F59: {"text": "설마", "pool_delta": 0x00},
    0x0F6D: {"text": "큭", "pool_delta": 0x40},
    0x0F70: {"text": "후후", "pool_delta": 0x80},
    0x0FC0: {"text": "이걸로", "pool_delta": 0xC0},
    0x0F72: {"text": "명심해라", "pool_delta": 0xF0},
}

# address -> (expected current body, helper ID, second 2-byte token index, text)
TARGETS: dict[int, tuple[bytes, int, int, str]] = {
    0x609A83: (bytes.fromhex("18E5186258"), 0x0F59, 0x0191, "설마……"),
    0x60D194: (bytes.fromhex("18E518D53D"), 0x0F6D, 0x0191, "큭……"),
    0x60F27C: (bytes.fromhex("18E5186261"), 0x0F70, 0x0191, "후후……"),
    0x61010E: (bytes.fromhex("18E518499C"), 0x0F72, 0x0191, "명심해라……"),
    0x61802F: (bytes.fromhex("18E5182993"), 0x0F70, 0x0F07, "후후후후……"),
    0x62439F: (bytes.fromhex("18E51821A2"), 0x0F59, 0x0191, "설마……"),
    0x628AB8: (bytes.fromhex("18E5182993"), 0x0F70, 0x0F07, "후후후후……"),
    0x62CC7D: (bytes.fromhex("18E5182993"), 0x0F70, 0x0F07, "후후후후……"),
    0x63A9F8: (bytes.fromhex("18E5183BC1"), 0x0FC0, 0x0191, "이걸로……"),
}

ORIGINAL_BODIES: dict[int, bytes] = {
    0x609A83: bytes.fromhex("18F845F191"),
    0x60D194: bytes.fromhex("18F4A3F191"),
    0x60F27C: bytes.fromhex("18F312F191"),
    0x61010E: bytes.fromhex("18F89EF191"),
    0x61802F: bytes.fromhex("18FA22F191"),
    0x62439F: bytes.fromhex("18F845F191"),
    0x628AB8: bytes.fromhex("18FA22F191"),
    0x62CC7D: bytes.fromhex("18FA22F191"),
    0x63A9F8: bytes.fromhex("18F62DF191"),
}

# Bundle IDs and nearby Japanese context are taken from the authoritative
# runtime-contract manifest bound to the current main.  They identify the
# physical scenario bundle without guessing a stage number.
BUNDLE_CONTEXT: dict[int, dict[str, str]] = {
    0x609A83: {"bundle_id": "scenario_609A78", "catalog_japanese": "ギレン総帥が？まさか……"},
    0x60D194: {"bundle_id": "scenario_60D17C", "catalog_japanese": "た、隊長……？隊長、隊長－っ！！クッ……"},
    0x60F27C: {"bundle_id": "scenario_60F25F", "catalog_japanese": "……そう。君にとても良い話を伝えに来たのさ。ふふ……"},
    0x61010E: {"bundle_id": "scenario_6100F5", "catalog_japanese": "旗艦グワデンより各部隊へ。作戦目標について捕捉する。いいか……"},
    0x61802F: {"bundle_id": "scenario_618015", "catalog_japanese": "……そちらはアイン・レヴィがうまくやってくれているよ。ふふふふ……"},
    0x62439F: {"bundle_id": "scenario_624396", "catalog_japanese": "………………まさか……"},
    0x628AB8: {"bundle_id": "scenario_628A80", "catalog_japanese": "……パプテマス・シロッコ。彼にもうひと働きしてもらうとしよう。彼のオモチャももうすぐ使えるようになるはずだしね。ふふふふ……"},
    0x62CC7D: {"bundle_id": "scenario_62CC20", "catalog_japanese": "元サンクキングダムの女王にして、現在は連邦政府の外務次官……連邦に対してだけではなく、コロニ－にも強い影響力を持っている。そう、あなたの権力はあなた自身が思っている以上に強いのですよ。ふふふふ……"},
    0x63A9F8: {"bundle_id": "scenario_63A9EF", "catalog_japanese": "………………これで……"},
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def align_up(value: int, align: int) -> int:
    return (value + align - 1) & ~(align - 1)


def read_record(rom: bytes | bytearray, logical: int) -> tuple[bytes, int]:
    sb = stock_base(rom)
    got = read_encoded_z_safe(rom, sb + logical, max_len=128)
    if got is None:
        raise BuildError(f"unreadable record {logical:06X}")
    return bytes(got[0]), int(got[1]) - sb


def next_control(rom: bytes | bytearray, terminator_logical: int) -> tuple[int, bytes]:
    sb = stock_base(rom)
    p = terminator_logical + 1
    while p < terminator_logical + 32 and rom[sb + p] == 0:
        p += 1
    return p, bytes(rom[sb + p:sb + p + 12])


def encoded_helper(text: str, tbl: Tbl) -> bytes:
    raw = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=marker_code(),
        hangul_marker_mode="run",
    )
    if raw is None:
        raise BuildError(f"cannot encode helper: {text!r}")
    out = bytes(raw)
    marker = marker_code().to_bytes(2, "big")
    if not out.startswith(marker) or b"\x00" in out:
        raise BuildError(f"helper marker/encoding contract failed: {text!r} {out.hex()}")
    return out


def dictionary_tail(dictionary: Any) -> int:
    end = 0
    for index in range(int(dictionary.stock_count), int(dictionary.count)):
        ptr = int(dictionary.ptrs[index])
        raw = bytes(dictionary.raw_entry(index))
        end = max(end, ptr + len(raw) + 1)
    return end


def load_audit() -> dict[str, Any]:
    if not ID_AUDIT.is_file():
        raise BuildError("Phase A ID audit is missing")
    audit = json.loads(ID_AUDIT.read_text(encoding="utf-8"))
    if audit.get("ok") is not True or audit.get("rom_written") is not False:
        raise BuildError("Phase A ID audit did not pass read-only")
    if ((audit.get("main") or {}).get("sha256")) != EXPECTED_MAIN_SHA:
        raise BuildError("Phase A ID audit is not bound to current main")
    if set(audit.get("reclaimable_ids") or []) != {f"{i:04X}" for i in HELPERS}:
        raise BuildError("Phase A reclaimable ID set drifted")
    return audit


def main() -> int:
    audit = load_audit()
    main_rom = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    live_save = LIVE_SAVE.read_bytes()
    if len(main_rom) != ROM_SIZE or sha(main_rom) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main TIP identity drifted: {sha(main_rom)}")
    if sha(original) != EXPECTED_ORIGINAL_SHA:
        raise BuildError("Original ROM identity drifted")
    if len(live_save) != SAVE_SIZE:
        raise BuildError("live SaveRAM size drifted")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    dictionary = make_dictionary_ext3(main_rom, ext_meta, ext3_meta)
    if int(ext_meta.get("stock_count", -1)) != 0x0EF7 or int(ext_meta.get("slot_count", -1)) != 265:
        raise BuildError("bank10 dictionary geometry drifted")
    if str(ext_meta.get("ext_seg", "")).upper() != "10" or not bool(ext_meta.get("ext_in_expansion")):
        raise BuildError("bank10 expansion dictionary metadata drifted")
    ext_ptr_off = int(str(ext_meta.get("ext_ptr_off", "0000")), 16)

    current_tail = dictionary_tail(dictionary)
    if current_tail != EXPECTED_CURRENT_TAIL:
        raise BuildError(f"bank10 phrase tail drifted: {current_tail:04X}")
    pool_start = align_up(current_tail, POOL_ALIGN)
    pool_end = pool_start + POOL_SIZE
    if pool_end > 0x10000:
        raise BuildError("helper pool would exceed bank10")
    # Expansion bank10 lives in the prepended 8 MiB at file 0x100000.
    # Do not depend on monoeye_rom's mutable global stock-base state here,
    # because loading the 8 MiB Original after the 16 MiB main resets it.
    bank10_abs = 0x10 * 0x10000
    if main_rom[bank10_abs + pool_start:bank10_abs + pool_end] != b"\xFF" * POOL_SIZE:
        raise BuildError("requested roomy bank10 helper pool is not pristine FF")

    # Verify the five ID strategies again at build time.
    wanted = set(HELPERS) | set(DUPLICATES.values())
    external_before = external_occurrence_map(main_rom, ext3_aware=True, wanted=wanted)
    nested_before = nested_occurrence_map(dictionary, wanted=set(HELPERS), ext3_aware=True)
    for index in (0x0F59, 0x0F6D):
        if external_before.get(index) or nested_before.get(index):
            raise BuildError(f"true-free slot {index:04X} gained a consumer")
    for reclaim, keeper in DUPLICATES.items():
        if bytes(dictionary.raw_entry(reclaim)) != bytes(dictionary.raw_entry(keeper)):
            raise BuildError(f"duplicate payload drift {reclaim:04X}/{keeper:04X}")
        if nested_before.get(reclaim):
            raise BuildError(f"duplicate {reclaim:04X} gained nested consumer")
        refs = external_before.get(reclaim, [])
        if not refs or any(str(row.get("region")) != "script" for row in refs):
            raise BuildError(f"duplicate {reclaim:04X} consumer class drifted")

    # Verify all nine current/Original record shapes and following control rows.
    boundary_before: dict[int, dict[str, Any]] = {}
    for logical, (before, _helper, _second, _text) in TARGETS.items():
        cur, term = read_record(main_rom, logical)
        src, src_term = read_record(original, logical)
        if cur != before or src != ORIGINAL_BODIES[logical] or term != src_term:
            raise BuildError(f"target record drift at {logical:06X}")
        next_abs, control = next_control(main_rom, term)
        if not control.startswith(b"\x17"):
            raise BuildError(f"target {logical:06X} is no longer followed by 0x17 control row")
        boundary_before[logical] = {
            "terminator": term,
            "next_control_abs": next_abs,
            "next_control_hex": control.hex().upper(),
        }

    candidate = bytearray(main_rom)
    sb = stock_base(candidate)
    allowed: list[tuple[int, int]] = []
    duplicate_retargets: list[dict[str, Any]] = []

    # Phase C-0: detach duplicate IDs by canonical 2-byte -> 2-byte rewrite.
    for reclaim, keeper in DUPLICATES.items():
        old_token = bytes(token_from_dict_index(reclaim))
        new_token = bytes(token_from_dict_index(keeper))
        if len(old_token) != 2 or len(new_token) != 2:
            raise BuildError("duplicate canonical token is not 2 bytes")
        for row in external_before.get(reclaim, []):
            logical_token = int(str(row["token_abs"]), 16)
            file_off = sb + logical_token
            if bytes(candidate[file_off:file_off + 2]) != old_token:
                raise BuildError(f"duplicate token site drifted {logical_token:06X}")
            candidate[file_off:file_off + 2] = new_token
            allowed.append((file_off, file_off + 2))
            duplicate_retargets.append(
                {
                    "reclaim": f"{reclaim:04X}",
                    "keeper": f"{keeper:04X}",
                    "record_abs": str(row["record_abs"]),
                    "token_abs": str(row["token_abs"]),
                    "before_hex": old_token.hex().upper(),
                    "after_hex": new_token.hex().upper(),
                    "region": str(row.get("region")),
                    "kind": str(row.get("kind")),
                }
            )

    # Phase B: roomy bank10 helper pool.  Only five pointers and five phrase
    # payloads are written; gaps remain FF but are protected from normal append
    # reuse because the final live pointer is near pool_end.
    helper_rows: list[dict[str, Any]] = []
    for index, spec in HELPERS.items():
        raw = encoded_helper(str(spec["text"]), tbl)
        phrase_off = pool_start + int(spec["pool_delta"])
        next_deltas = sorted(int(x["pool_delta"]) for x in HELPERS.values() if int(x["pool_delta"]) > int(spec["pool_delta"]))
        slot_end = pool_start + (next_deltas[0] if next_deltas else POOL_SIZE)
        if phrase_off + len(raw) + 1 > slot_end:
            raise BuildError(f"helper {index:04X} exceeds reserved subslot")
        if candidate[bank10_abs + phrase_off:bank10_abs + phrase_off + len(raw) + 1] != b"\xFF" * (len(raw) + 1):
            raise BuildError(f"helper destination not pristine {index:04X}")

        local = index - int(ext_meta["stock_count"])
        ptr_file = bank10_abs + ext_ptr_off + local * 2
        old_ptr = struct.unpack_from("<H", candidate, ptr_file)[0]
        struct.pack_into("<H", candidate, ptr_file, phrase_off)
        allowed.append((ptr_file, ptr_file + 2))

        phrase_file = bank10_abs + phrase_off
        candidate[phrase_file:phrase_file + len(raw)] = raw
        candidate[phrase_file + len(raw)] = 0
        allowed.append((phrase_file, phrase_file + len(raw) + 1))
        helper_rows.append(
            {
                "index": f"{index:04X}",
                "token_hex": bytes(token_from_dict_index(index)).hex().upper(),
                "text": str(spec["text"]),
                "old_pointer": f"{old_ptr:04X}",
                "new_pointer": f"{phrase_off:04X}",
                "raw_hex": raw.hex().upper(),
                "capacity_until_next_reserved_helper": slot_end - phrase_off,
                "used_bytes_including_nul": len(raw) + 1,
            }
        )

    # Phase C: exact nine native pairs.
    target_rows: list[dict[str, Any]] = []
    for logical, (before, helper, second, expected_text) in TARGETS.items():
        after = b"\x18" + bytes(token_from_dict_index(helper)) + bytes(token_from_dict_index(second))
        if len(after) != 5 or after[0] != 0x18 or b"\xE5\x18" in after[1:]:
            raise BuildError(f"native-pair shape failed at {logical:06X}")
        file_off = sb + logical
        if bytes(candidate[file_off:file_off + 5]) != before:
            raise BuildError(f"candidate target drift before rewrite {logical:06X}")
        candidate[file_off:file_off + 5] = after
        allowed.append((file_off, file_off + 5))
        target_rows.append(
            {
                "abs": f"{logical:06X}",
                "before_hex": before.hex().upper(),
                "after_hex": after.hex().upper(),
                "original_hex": ORIGINAL_BODIES[logical].hex().upper(),
                "helper_id": f"{helper:04X}",
                "second_token_id": f"{second:04X}",
                "expected": expected_text,
                "bundle_id": BUNDLE_CONTEXT[logical]["bundle_id"],
                "catalog_japanese": BUNDLE_CONTEXT[logical]["catalog_japanese"],
            }
        )

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)
    result_dictionary = make_dictionary_ext3(result, ext_meta, ext3_meta)

    # Final helper and record semantics.
    for spec in helper_rows:
        index = int(spec["index"], 16)
        expected = str(spec["text"])
        if bytes(result_dictionary.raw_entry(index)).hex().upper() != spec["raw_hex"]:
            raise BuildError(f"helper raw mismatch {index:04X}")
        if result_dictionary.expand_index(index, tbl) != expected:
            raise BuildError(f"helper render mismatch {index:04X}")

    expected_helper_consumers: dict[int, set[int]] = {i: set() for i in HELPERS}
    for logical, (_before, helper, _second, _text) in TARGETS.items():
        expected_helper_consumers[helper].add(logical)

    external_after = external_occurrence_map(result, ext3_aware=True, wanted=set(HELPERS))
    nested_after = nested_occurrence_map(result_dictionary, wanted=set(HELPERS), ext3_aware=True)
    for index, expected_records in expected_helper_consumers.items():
        refs = external_after.get(index, [])
        actual_records = {int(str(row["record_abs"]), 16) for row in refs}
        if actual_records != expected_records or any(str(row.get("region")) != "script" for row in refs):
            raise BuildError(
                f"helper consumer allowlist mismatch {index:04X}: actual={sorted(actual_records)} expected={sorted(expected_records)}"
            )
        if nested_after.get(index):
            raise BuildError(f"helper unexpectedly nested {index:04X}")

    for row in target_rows:
        logical = int(row["abs"], 16)
        payload, term = read_record(result, logical)
        rendered = result_dictionary.expand(payload[1:], tbl).rstrip("\u3000 \t")
        if rendered != row["expected"]:
            raise BuildError(f"render mismatch {logical:06X}: {rendered!r}")
        if term != boundary_before[logical]["terminator"]:
            raise BuildError(f"terminator moved {logical:06X}")
        next_abs, control = next_control(result, term)
        if next_abs != boundary_before[logical]["next_control_abs"] or control != bytes.fromhex(boundary_before[logical]["next_control_hex"]):
            raise BuildError(f"following 0x17 control row changed {logical:06X}")
        row["rendered"] = rendered
        row["terminator"] = f"{term:06X}"
        row["next_control_abs"] = f"{next_abs:06X}"
        row["next_control_hex"] = control.hex().upper()

    final_tail = dictionary_tail(result_dictionary)
    minimum_reserved = final_tail - pool_start
    if final_tail < pool_start + 0xF0:
        raise BuildError("roomy helper placement did not advance dictionary tail enough")

    runs = diff_runs(main_rom, result)
    unexpected = [run for run in runs if not covered(run, allowed)]
    if unexpected:
        raise BuildError(f"diff escaped exact-recovery scope: {unexpected[:12]}")
    if MAIN.read_bytes() != main_rom or LIVE_SAVE.read_bytes() != live_save:
        raise BuildError("main TIP or live SaveRAM changed during build")

    OUT.write_bytes(result)
    shutil.copy2(LIVE_SAVE, OUT_SAVE)
    if OUT_SAVE.read_bytes() != live_save:
        raise BuildError("candidate SaveRAM is not byte-exact live copy")

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_exact_continuation_native_recovery_candidate.py",
        "status": "candidate_pending_static_gates_and_user_runtime_validation",
        "parent": {"path": str(MAIN.relative_to(ROOT)), "sha256": sha(main_rom), "size": len(main_rom)},
        "original": {"path": str(ORIGINAL.relative_to(ROOT)), "sha256": sha(original)},
        "phase_a_audit": {"path": str(ID_AUDIT.relative_to(ROOT)), "sha256": hashlib.sha256(ID_AUDIT.read_bytes()).hexdigest(), "ok": audit.get("ok")},
        "candidate": {"path": str(OUT.relative_to(ROOT)), "sha256": sha(result), "size": len(result), "checksum": f"{checksum:04X}"},
        "saveram": {"path": str(OUT_SAVE.relative_to(ROOT)), "sha256": sha(live_save), "byte_exact_to_live_main": True},
        "bank10_helper_pool": {
            "current_tail_before": f"{current_tail:04X}",
            "pool_start": f"{pool_start:04X}",
            "declared_pool_end": f"{pool_end:04X}",
            "declared_pool_size": POOL_SIZE,
            "final_live_tail": f"{final_tail:04X}",
            "effective_reserved_span_from_pool_start": minimum_reserved,
            "policy": "0x100-byte roomy pool; helper payloads are intentionally separated and final helper is near pool end so normal append scans do not immediately reuse the gaps",
            "helpers": helper_rows,
        },
        "duplicate_retargets": {
            "count": len(duplicate_retargets),
            "expected_by_reclaim": {"0F70": 4, "0F72": 7, "0FC0": 10},
            "rows": duplicate_retargets,
        },
        "targets": target_rows,
        "guards": {
            "exact_target_count": len(target_rows) == 9,
            "all_target_payloads_five_bytes": all(len(bytes.fromhex(r["after_hex"])) == 5 for r in target_rows),
            "all_target_payloads_native_two_token": all(
                len(list(iter_token_refs_with_offsets(bytes.fromhex(r["after_hex"])[1:], ext3_aware=True))) == 2
                and all(length == 2 for _idx, length, _off in iter_token_refs_with_offsets(bytes.fromhex(r["after_hex"])[1:], ext3_aware=True))
                for r in target_rows
            ),
            "direct_e518_in_targets": 0,
            "compact3_in_targets": 0,
            "next_0x17_boundaries_byte_exact": True,
            "helper_consumer_allowlists_exact": True,
            "helper_nested_consumers": 0,
            "bank10_pool_was_pristine_ff": True,
            "unexpected_diff_runs": 0,
            "main_tip_unchanged": True,
            "live_saveram_unchanged": True,
        },
        "diff": {
            "runs": len(runs),
            "allowed_ranges": len(allowed),
            "unexpected_runs": 0,
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    # Runtime matrix deliberately avoids guessing stage numbers that are not
    # statically proven.  Bundle IDs and Japanese context make the encounter
    # locatable; caller/stage mapping can be appended by the dedicated audit.
    matrix_rows = []
    for row in target_rows:
        matrix_rows.append(
            {
                "abs": row["abs"],
                "bundle_id": row["bundle_id"],
                "catalog_japanese": row["catalog_japanese"],
                "expected": row["expected"],
                "payload_hex": row["after_hex"],
                "next_control_abs": row["next_control_abs"],
                "checks": [
                    "대사 자체가 목표 한글로 정상 출력",
                    "독립 こ/한자/히라가나가 앞뒤에 붙지 않음",
                    "직후 0x17 제어행이 화면 글리프로 노출되지 않음",
                    "다음 초상/대사/이벤트 진행 정상",
                    "반복 진입 시 replay/loop 없음",
                ],
            }
        )
    matrix = {
        "schema_version": 1,
        "candidate_sha256": sha(result),
        "status": "pending_caller_stage_mapping_and_user_runtime_validation",
        "rows": matrix_rows,
    }
    TEST_MATRIX.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    md = [
        "# Exact continuation native recovery — 실측 매트릭스",
        "",
        f"Candidate SHA-256: `{sha(result)}`",
        "",
        "> stage/event 번호는 정적 caller 매핑으로 확인되는 항목만 추후 기입한다. 주소와 목표 문구를 기준으로 우선 실측한다.",
        "",
        "| 주소 | bundle | 목표 출력 | native payload | 다음 control | 실측 |",
        "|---|---|---|---|---|---|",
    ]
    for row in target_rows:
        md.append(f"| `{row['abs']}` | `{row['bundle_id']}` | `{row['expected']}` | `{row['after_hex']}` | `{row['next_control_abs']}` | ☐ |")
    md += [
        "",
        "## 문맥 식별자",
        "",
    ]
    for row in target_rows:
        md.append(f"- `{row['abs']}` / `{row['bundle_id']}` — {row['catalog_japanese']}")
    md += [
        "",
        "## 공통 확인 항목",
        "",
        "- 해당 대사 자체가 정상 한글로 출력되는지",
        "- 독립 `こ`/한자/히라가나가 붙지 않는지",
        "- 직후 `0x17` 제어행이 화면 글리프로 노출되지 않는지",
        "- 다음 초상/대사/이벤트 진행이 정상인지",
        "- 반복 진입 시 이벤트 replay/loop가 생기지 않는지",
        "",
    ]
    TEST_MATRIX_MD.write_text("\n".join(md), encoding="utf-8", newline="\n")

    print(json.dumps({
        "ok": True,
        "candidate": report["candidate"],
        "bank10_helper_pool": report["bank10_helper_pool"],
        "duplicate_retargets": report["duplicate_retargets"]["count"],
        "targets": len(target_rows),
        "unexpected_diff_runs": 0,
        "report": str(REPORT.relative_to(ROOT)),
        "test_matrix": str(TEST_MATRIX.relative_to(ROOT)),
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
