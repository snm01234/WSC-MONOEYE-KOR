#!/usr/bin/env python3
"""Build the STAGE14n Ple/Ple Two terminology + untranslated-line follow-up.

Scope:
- translate 5933AE: でもね……どんなに不愉快でも、
- standardize context-proven プル => 플
- standardize all rendered Ple Two variants => 플투
- fix 636C68 fragment 주、 도……？ => 쥬、 도……？

The live main TIP is used only as the parent and is never modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from build_gundam_terminology_candidate import ext3_bank_cursor  # noqa: E402
from monoeye_rom import (  # noqa: E402
    Tbl,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    write_le16,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
SPEC = ROOT / "data/stage14n_ple_terminology_followup_ko.json"
TBL_PATH = PATCH / "hangul_patch_pad3.tbl"
EXT_META = PATCH / "exp_dictionary_meta.json"
EXT3_META = PATCH / "ext3_dictionary_meta.json"
OUT_ROM = PATCH / "stage14n_ple_terminology_followup_candidate.wsc"
OUT_SAVE = ROOT / "sram/stage14n_ple_terminology_followup_candidate.sav"
OUT_REPORT = PATCH / "stage14n_ple_terminology_followup_candidate_report.json"

EXPECTED_MAIN_SHA = "24aa886359bb41e70161d47c66c90d683c91f0287c3be2eca856c7f520e7f1bf"
EXPECTED_TBL_SHA = "9d31d1fd1a5022b54f83866d285f4d656461b30d4046ef2b6261a9441abca914"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
MARKER = 0xEC8D
NEW_LINE_EXT3_INDEX = 0x3423
NEW_LINE_PREFIX = bytes.fromhex("171C18")
PLE_INDICES = {0x16DD, 0x36BA, 0x46BA, 0x46C3, 0x56B9}
JUDAU_INDEX = 0x6017
PLE_TWO_BAD = ("플루츠－", "플루츠-", "플루츠", "푸루투", "푸르츠", "풀투", "풀츠－", "풀츠", "플　투", "플 투")
DIRECT_STRING_TARGETS = {
    0x01009B9: ("풀츠－！！", "플투！！"),
    0x0DEEB5C: ("풀츠－！！", "플투！！"),
    0x01BDA60: ("풀　구조", "플　구조"),
    0x016236A: ("플　투！！", "플투！！"),
    0x01A4AEF: ("플　투의　최후", "플투의　최후"),
    0x01A5ED4: ("플　투의　죽음", "플투의　죽음"),
    0x01E34CD: ("플　투를　구해줘！』", "플투를　구해줘！』"),
}


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def encode(tbl: Tbl, text: str) -> bytes:
    raw = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=MARKER,
        hangul_marker_mode="run",
    )
    if raw is None or b"\x00" in raw:
        raise BuildError(f"cannot encode {text!r}")
    return bytes(raw)


def canonical_ple_two(text: str) -> str:
    out = text
    for bad in PLE_TWO_BAD:
        out = out.replace(bad, "플투")
    if "플투" in out and "망설임" in out:
        out = out.replace("（풀　없음）", "（플　없음）")
        out = out.replace("（풀）", "（플）")
    return out


def all_ext3_indices(dictionary) -> range:
    return range(0x1000, 0x1000 + dictionary.ext3_count)


def patch_phrase(candidate: bytearray, tbl: Tbl, ext_meta: dict, ext3_meta: dict, index: int, after: str) -> dict[str, Any]:
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    before = strip_pad(dictionary.expand_index(index, tbl))
    raw = bytes(dictionary.raw_entry(index))
    encoded = encode(tbl, after)
    if len(encoded) > len(raw):
        raise BuildError(f"phrase grows at {index:05X}: {len(raw)} -> {len(encoded)}")
    entry_abs = int(dictionary.entry_abs(index))
    candidate[entry_abs : entry_abs + len(encoded)] = encoded
    candidate[entry_abs + len(encoded)] = 0
    tail_start = entry_abs + len(encoded) + 1
    tail_end = entry_abs + len(raw) + 1
    if tail_end > tail_start:
        candidate[tail_start:tail_end] = b"\xFF" * (tail_end - tail_start)
    verify = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    rendered = strip_pad(verify.expand_index(index, tbl))
    if rendered != after:
        raise BuildError(f"verify failed {index:05X}: {rendered!r} != {after!r}")
    return {
        "index": f"{index:05X}",
        "entry_abs": f"{entry_abs:07X}",
        "before": before,
        "after": rendered,
        "old_len": len(raw),
        "new_len": len(encoded),
        "allowed": [entry_abs, entry_abs + len(raw) + 1],
    }


def patch_direct_z(candidate: bytearray, tbl: Tbl, ext_meta: dict, ext3_meta: dict, file_abs: int, expected: str, after: str) -> dict[str, Any]:
    got = read_encoded_z_safe(candidate, file_abs, max_len=128)
    if got is None:
        raise BuildError(f"direct string unreadable at {file_abs:07X}")
    raw, term = bytes(got[0]), int(got[1])
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    before = strip_pad(dictionary.expand(raw, tbl))
    if before != expected:
        raise BuildError(f"direct string drift {file_abs:07X}: {before!r} != {expected!r}")
    encoded = encode(tbl, after)
    if len(encoded) > len(raw):
        raise BuildError(f"direct string grows at {file_abs:07X}: {len(raw)} -> {len(encoded)}")
    candidate[file_abs : file_abs + len(encoded)] = encoded
    candidate[file_abs + len(encoded)] = 0
    tail_start = file_abs + len(encoded) + 1
    tail_end = term + 1
    if tail_end > tail_start:
        candidate[tail_start:tail_end] = b"\xFF" * (tail_end - tail_start)
    verify = read_encoded_z_safe(candidate, file_abs, max_len=128)
    if verify is None:
        raise BuildError(f"direct string verify unreadable at {file_abs:07X}")
    rendered = strip_pad(make_dictionary_ext3(candidate, ext_meta, ext3_meta).expand(bytes(verify[0]), tbl))
    if rendered != after or int(verify[1]) != file_abs + len(encoded):
        raise BuildError(f"direct string verify failed at {file_abs:07X}: {rendered!r}")
    return {
        "file_abs": f"{file_abs:07X}",
        "before": before,
        "after": rendered,
        "old_len": len(raw),
        "new_len": len(encoded),
        "allowed": [file_abs, term + 1],
    }


def main() -> int:
    parent = MAIN.read_bytes()
    tbl_bytes = TBL_PATH.read_bytes()
    save = MAIN_SAVE.read_bytes()
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN_SHA:
        raise BuildError(f"main identity drifted: {sha(parent)}")
    if sha(tbl_bytes) != EXPECTED_TBL_SHA:
        raise BuildError("active TBL identity drifted")
    if len(save) != SAVE_SIZE:
        raise BuildError(f"SaveRAM size drifted: {len(save)}")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    if spec.get("review_status") != "user_confirmed":
        raise BuildError("spec is not user-confirmed")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    candidate = bytearray(parent)
    allowed: list[tuple[int, int]] = []
    phrase_rows: list[dict[str, Any]] = []
    direct_rows: list[dict[str, Any]] = []

    # 1) Context-proven Ple proper-name entries: 풀 -> 플.
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    expected_ple = {
        0x16DD: "……나는、　엘피・풀！！",
        0x36BA: "뭐야！？……풀！？",
        0x46BA: "아니、　풀이　아니야……！",
        0x46C3: "……풀－－！！",
        0x56B9: "풀은　아니지만……",
    }
    for index in sorted(PLE_INDICES):
        before = strip_pad(dictionary.expand_index(index, tbl))
        if before != expected_ple[index]:
            raise BuildError(f"Ple anchor drift {index:05X}: {before!r}")
        after = before.replace("풀", "플")
        row = patch_phrase(candidate, tbl, ext_meta, ext3_meta, index, after)
        phrase_rows.append(row)
        allowed.append(tuple(row.pop("allowed")))
        dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)

    # 2) Whole-rendered-dictionary Ple Two standardization.
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    targets: list[tuple[int, str, str, int]] = []
    seen_physical: set[int] = set()
    for index in all_ext3_indices(dictionary):
        try:
            before = strip_pad(dictionary.expand_index(index, tbl))
            entry_abs = int(dictionary.entry_abs(index))
        except Exception:
            continue
        after = canonical_ple_two(before)
        if after == before or entry_abs in seen_physical:
            continue
        seen_physical.add(entry_abs)
        targets.append((index, before, after, entry_abs))
    for index, before, after, _entry_abs in targets:
        current = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
        now = strip_pad(current.expand_index(index, tbl))
        if now != before:
            raise BuildError(f"Ple Two target drift {index:05X}: {now!r} != {before!r}")
        row = patch_phrase(candidate, tbl, ext_meta, ext3_meta, index, after)
        phrase_rows.append(row)
        allowed.append(tuple(row.pop("allowed")))

    # 2b) Direct/unindexed rendered copies of the same terminology.
    for file_abs, (before, after) in DIRECT_STRING_TARGETS.items():
        row = patch_direct_z(candidate, tbl, ext_meta, ext3_meta, file_abs, before, after)
        direct_rows.append(row)
        allowed.append(tuple(row.pop("allowed")))

    # 3) Split-name fragment at 636C68.
    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    before_judau = strip_pad(dictionary.expand_index(JUDAU_INDEX, tbl))
    if before_judau != "주、　도……？":
        raise BuildError(f"Judau fragment drift: {before_judau!r}")
    row = patch_phrase(candidate, tbl, ext_meta, ext3_meta, JUDAU_INDEX, "쥬、　도……？")
    phrase_rows.append(row)
    allowed.append(tuple(row.pop("allowed")))

    # 4) Previously untranslated STAGE14n line. Preserve 17 1C 18 prefix and extent.
    sb = stock_base(candidate)
    logical = int(spec["untranslated_dialogue"]["abs"], 16)
    got = read_encoded_z_safe(candidate, sb + logical, max_len=64)
    if got is None:
        raise BuildError("untranslated record is unreadable")
    old_payload, old_term_file = bytes(got[0]), int(got[1])
    expected_old = bytes.fromhex("171C18F688F191F93AF6DFF25E07")
    if old_payload != expected_old:
        raise BuildError(f"untranslated record drift: {old_payload.hex().upper()}")

    dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    seg, local = dictionary._ext3_bank_local(NEW_LINE_EXT3_INDEX)
    if seg != 0x13 or local != 0x423:
        raise BuildError(f"unexpected ext3 mapping for new line: seg={seg:02X} local={local:03X}")
    token = token_from_dict_index(NEW_LINE_EXT3_INDEX)
    if token in bytes(parent):
        raise BuildError(f"reserved ext3 token already referenced: {token.hex().upper()}")
    ptr_abs = seg * 0x10000 + local * 2
    if int.from_bytes(candidate[ptr_abs:ptr_abs + 2], "little") != 0x2000:
        raise BuildError("reserved ext3 pointer is not empty")

    line_text = str(spec["untranslated_dialogue"]["ko"])
    line_encoded = encode(tbl, line_text)
    cursor = ext3_bank_cursor(candidate, seg)
    need = len(line_encoded) + 1
    bank_base = seg * 0x10000
    if cursor + need > 0x10000:
        raise BuildError("ext3 bank13 tail overflow")
    if any(byte != 0xFF for byte in candidate[bank_base + cursor : bank_base + cursor + need]):
        raise BuildError(f"ext3 bank13 tail is not free at {cursor:04X}")
    candidate[bank_base + cursor : bank_base + cursor + len(line_encoded)] = line_encoded
    candidate[bank_base + cursor + len(line_encoded)] = 0
    write_le16(candidate, ptr_abs, cursor)
    allowed.extend([(bank_base + cursor, bank_base + cursor + need), (ptr_abs, ptr_abs + 2)])

    new_payload = NEW_LINE_PREFIX + token + b"\x01" * (len(old_payload) - len(NEW_LINE_PREFIX) - len(token))
    if len(new_payload) != len(old_payload):
        raise BuildError("translated record extent changed")
    candidate[sb + logical : sb + logical + len(new_payload)] = new_payload
    allowed.append((sb + logical, sb + logical + len(old_payload)))

    verify_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    verify_got = read_encoded_z_safe(candidate, sb + logical, max_len=64)
    if verify_got is None or int(verify_got[1]) != old_term_file:
        raise BuildError("translated record terminator changed")
    verify_payload = bytes(verify_got[0])
    rendered_line = strip_pad(verify_dictionary.expand(verify_payload[len(NEW_LINE_PREFIX):], tbl))
    if rendered_line != line_text:
        raise BuildError(f"translated line verify failed: {rendered_line!r}")

    # Canonicalization residual gate.
    final_dictionary = make_dictionary_ext3(candidate, ext_meta, ext3_meta)
    bad_rows: list[dict[str, str]] = []
    for index in all_ext3_indices(final_dictionary):
        try:
            text = strip_pad(final_dictionary.expand_index(index, tbl))
        except Exception:
            continue
        if any(bad in text for bad in PLE_TWO_BAD):
            bad_rows.append({"index": f"{index:05X}", "text": text})
    if bad_rows:
        raise BuildError(f"Ple Two residuals remain: {bad_rows[:8]}")
    raw_candidate = bytes(candidate)
    raw_bad_hits: list[str] = []
    for bad in PLE_TWO_BAD:
        encoded_bad = encode(tbl, bad)
        if encoded_bad in raw_candidate:
            raw_bad_hits.append(bad)
    if raw_bad_hits:
        raise BuildError(f"raw Ple Two residuals remain: {raw_bad_hits}")
    if encode(tbl, "풀　구조") in raw_candidate:
        raise BuildError("direct Ple residual '풀 구조' remains")
    for index in PLE_INDICES:
        if "풀" in strip_pad(final_dictionary.expand_index(index, tbl)):
            raise BuildError(f"Ple residual remains at {index:05X}")
    if strip_pad(final_dictionary.expand_index(JUDAU_INDEX, tbl)) != "쥬、　도……？":
        raise BuildError("Judau fragment did not canonicalize")

    checksum = update_ws_checksum(candidate)
    allowed.append((len(candidate) - 2, len(candidate)))
    result = bytes(candidate)

    def covered(offset: int) -> bool:
        return any(left <= offset < right for left, right in allowed)

    changed = [i for i, (a, b) in enumerate(zip(parent, result)) if a != b]
    unexpected = [i for i in changed if not covered(i)]
    if unexpected:
        raise BuildError(f"diff escaped allowlist: {[f'{x:07X}' for x in unexpected[:16]]}")
    if MAIN.read_bytes() != parent or MAIN_SAVE.read_bytes() != save:
        raise BuildError("live main or SaveRAM changed during candidate build")

    OUT_ROM.write_bytes(result)
    shutil.copy2(MAIN_SAVE, OUT_SAVE)
    report = {
        "schema_version": 1,
        "ok": True,
        "parent_sha256": sha(parent),
        "candidate_sha256": sha(result),
        "checksum": f"{checksum:04X}",
        "translated_record": {
            "abs": f"{logical:06X}",
            "before_raw": old_payload.hex().upper(),
            "after_raw": new_payload.hex().upper(),
            "after": rendered_line,
            "ext3_index": f"{NEW_LINE_EXT3_INDEX:05X}",
            "ext3_entry_abs": f"{bank_base + cursor:07X}",
        },
        "phrase_rewrites": phrase_rows,
        "direct_rewrites": direct_rows,
        "counts": {
            "phrase_rewrites": len(phrase_rows),
            "direct_rewrites": len(direct_rows),
            "ple_two_targets": len(targets),
            "changed_bytes": len(changed),
            "unexpected_changed_bytes": len(unexpected),
        },
        "verification": {
            "ple_two_residuals": 0,
            "ple_context_residuals": 0,
            "judau_fragment": "쥬、　도……？",
            "main_unchanged": True,
            "save_exact": OUT_SAVE.read_bytes() == save,
        },
    }
    tmp = OUT_REPORT.with_name(f".{OUT_REPORT.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, OUT_REPORT)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
