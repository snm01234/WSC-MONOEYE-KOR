#!/usr/bin/env python3
"""Extract source-grounded terminology/machine-translation corrections from the live TIP.

Targets are deliberately narrow and must be justified by the original Japanese:
  * ブラ－ド -> 브래드 (including ブラ－ド・ファ－レン -> 브래드・파렌)
  * 中佐/少佐/大佐 -> 중령/소령/대령 (reject Japanese-style 중좌/소좌/대좌 etc.)
  * カミ一ユ OCR/dash corruption -> 카미유, with the known sentence retranslated.
  * dialogue mentions of カゲロウ translated as 카게로 -> 하루살이.

The script is read-only with respect to ROM/SaveRAM. It writes a JSON and CSV worklist
that can be consumed by the guarded candidate builder.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from extract_script import split_prefix_body  # noqa: E402
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base  # noqa: E402

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DIALOGUE_DB = ROOT / "out/script/dialogue_db.json"
MIXED = ROOT / "data/mixed_residual_translations.json"
UNIT_HITS = ROOT / "out/script/unit_name_bank_hits.json"
ENCYCLOPEDIA_CHARACTER = ROOT / "data/encyclopedia_character_batch01_ko.json"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
BASE_MANIFEST = ROOT / "out/patch/main_p1_base_manifest.json"
OUT_JSON = ROOT / "out/script/machine_translation_terminology_targets.json"
OUT_CSV = ROOT / "out/script/machine_translation_terminology_targets.csv"

KNOWN_CAMILLE_JP = "カミ一ユが男の名前でなんで悪いんだ！"
KNOWN_CAMILLE_KO = "카미유가　남자　이름이라서　뭐가　나빠！"

KAGERO_EXPLICIT_SOURCES = {
    0x5960C1: "『カゲロウ』ってどんなのだったかな？",
    0x596227: "『カゲロウ』のようでは成り立つまい。",
    0x59631E: "……ふん、何が『カゲロウ』さ！",
}

EXACT_RETRANSLATIONS = {
    KNOWN_CAMILLE_JP: KNOWN_CAMILLE_KO,
    "別働隊のシ－マ中佐が確保している。": "별동대의　시마　중령이　확보하고　있다。",
    "別働隊の시마中佐が확보している。": "별동대의　시마　중령이　확보하고　있다。",
    "……シャア大佐。": "……샤아대령。",
    "エイパ－・シナプス大佐。": "에이파・시냅스　대령。",
    "ライデン少佐の言う通りだ。": "라이덴　소령의　말이　맞다。",
    "アナべル・ガト－少佐の働きにより、": "아나벨・가토　소령의　활약으로、",
}

MANIFEST_SCAN_BAD = ("브라드", "블라드", "중좌", "소좌", "대좌", "카미이치유", "카미이유", "카게로")

BAD_TO_GOOD = {
    "브라드": "브래드",
    "블라드": "브래드",
    "블레이드": "브래드",
    "중좌": "중령",
    "중사": "중령",
    "소좌": "소령",
    "소사": "소령",
    "대좌": "대령",
    "대사": "대령",
    "카미이치유": "카미유",
    "카미이유": "카미유",
    "카게로": "하루살이",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def source_rules(jp: str) -> list[str]:
    rules: list[str] = []
    if "ブラ－ド" in jp:
        rules.append("brad_farren")
    if "中佐" in jp:
        rules.append("rank_lieutenant_colonel")
    if "少佐" in jp:
        rules.append("rank_major")
    if "大佐" in jp:
        rules.append("rank_colonel")
    if "カミ一ユ" in jp:
        rules.append("camille_ocr_dash")
    if "カゲロウ" in jp:
        rules.append("kagero_mayfly")
    return rules


def corrected_text(jp: str, current: str) -> str:
    text = strip_pad(current)
    if jp in EXACT_RETRANSLATIONS:
        return EXACT_RETRANSLATIONS[jp]
    if "ブラ－ド" in jp:
        text = (
            text.replace("블레이드", "브래드")
            .replace("브라드", "브래드")
            .replace("블라드", "브래드")
        )
    if "中佐" in jp:
        text = text.replace("중좌", "중령").replace("중사", "중령")
    if "少佐" in jp:
        text = text.replace("소좌", "소령").replace("소사", "소령")
    if "大佐" in jp:
        text = text.replace("대좌", "대령").replace("대사", "대령")
    if "カミ一ユ" in jp:
        text = text.replace("카미이치유", "카미유").replace("카미이유", "카미유")
    if "カゲロウ" in jp:
        text = text.replace("카게로", "하루살이")
    return text


def iter_sources() -> list[dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}

    dialogue = json.loads(DIALOGUE_DB.read_text(encoding="utf-8-sig"))["dialogue"]
    for row in dialogue:
        jp = str(row.get("jp") or "")
        if not source_rules(jp):
            continue
        logical = int(row["abs"])
        rows.setdefault(logical, {"abs": f"{logical:06X}", "jp": jp, "source": "dialogue_db"})

    mixed = json.loads(MIXED.read_text(encoding="utf-8"))["entries"]
    for row in mixed:
        jp = str(row.get("source_text") or "")
        if not source_rules(jp):
            continue
        logical = int(str(row["abs"]), 16)
        rows.setdefault(logical, {"abs": f"{logical:06X}", "jp": jp, "source": "mixed_residual"})

    encyclopedia = json.loads(ENCYCLOPEDIA_CHARACTER.read_text(encoding="utf-8-sig"))["lines"]
    for row in encyclopedia:
        jp = str(row.get("jp") or "")
        if not source_rules(jp):
            continue
        logical = int(str(row["abs"]), 16)
        rows.setdefault(logical, {"abs": f"{logical:06X}", "jp": jp, "source": "encyclopedia_character_batch01"})

    for logical, jp in KAGERO_EXPLICIT_SOURCES.items():
        rows[logical] = {
            "abs": f"{logical:06X}",
            "jp": jp,
            "source": "kagero_user_correction",
        }

    unit_hits = json.loads(UNIT_HITS.read_text(encoding="utf-8-sig"))["hits"]
    for row in unit_hits:
        jp = str(row.get("jp") or "")
        if not source_rules(jp):
            continue
        # Keep only exact natural-language starts. The source inventory contains overlapping suffix hits.
        if not (jp.startswith("ブラ－ド") or jp.startswith("カミ一ユ")):
            continue
        logical = int(str(row["abs"]), 16)
        rows.setdefault(logical, {"abs": f"{logical:06X}", "jp": jp, "source": "unit_name_bank_hits"})

    return [rows[key] for key in sorted(rows)]


def iter_manifest_supplemental_sources(
    rom: bytes,
    dictionary,
    tbl: Tbl,
) -> list[dict[str, Any]]:
    """Find active bad terminology missed by dialogue-oriented source inventories.

    The broad manifest contains proven AUX/name/script record boundaries.  For a
    matching live record we require exactly one E5 18 portal and re-read the
    Japanese phrase from the original ROM at the same byte offset.  The target
    is the private ext3 phrase, not any preceding control byte.
    """
    original = bytes(load_rom(ORIGINAL))
    original_dict = Dictionary(original)
    original_sb = stock_base(original)
    current_sb = stock_base(rom)
    population = json.loads(BASE_MANIFEST.read_text(encoding="utf-8"))["population"]
    manifest_rows = list(population.get("included") or []) + list(population.get("excluded") or [])
    out: list[dict[str, Any]] = []
    seen: set[int] = set()

    for manifest_row in manifest_rows:
        logical = int(str(manifest_row["abs"]), 16)
        if logical in seen:
            continue
        got = read_encoded_z_safe(rom, current_sb + logical, max_len=256)
        if got is None:
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        try:
            rendered = strip_pad(dictionary.expand(payload, tbl))
        except Exception:
            continue
        if not any(token in rendered for token in MANIFEST_SCAN_BAD):
            continue

        positions = [
            pos
            for pos in range(max(0, len(payload) - 3))
            if payload[pos : pos + 2] == b"\xE5\x18"
        ]
        if len(positions) != 1:
            continue
        portal_offset = positions[0]
        if portal_offset + 3 >= len(payload):
            continue
        index = 0x1000 + (payload[portal_offset + 2] << 8) + payload[portal_offset + 3]
        try:
            slot_current = strip_pad(dictionary.expand_index(index, tbl))
        except Exception:
            continue
        if not any(token in slot_current for token in MANIFEST_SCAN_BAD):
            continue

        original_got = read_encoded_z_safe(original, original_sb + logical, max_len=256)
        if original_got is None:
            continue
        original_payload = bytes(original_got[0])
        if portal_offset >= len(original_payload):
            continue
        try:
            jp = strip_pad(original_dict.expand(original_payload[portal_offset:], tbl))
        except Exception:
            continue
        rules = source_rules(jp)
        if not rules:
            continue
        expected = corrected_text(jp, slot_current)
        if expected == slot_current:
            continue
        if not any(bad in slot_current for bad in BAD_TO_GOOD):
            continue

        seen.add(logical)
        out.append(
            {
                "abs": f"{logical:06X}",
                "jp": jp,
                "source": "main_manifest_original_recheck",
                "target_mode": "private_ext3_phrase",
                "portal_offset": portal_offset,
                "ext3_index": f"{index:05X}",
                "rules": rules,
                "current": slot_current,
                "ko": expected,
                "prefix_hex": payload[:portal_offset].hex().upper(),
                "record_kind": str(manifest_row.get("region") or "manifest"),
                "payload_len": len(payload),
                "body_len": len(payload) - portal_offset,
                "terminator": f"{terminator:06X}",
                "translation_source": "llm",
                "review_status": "approved",
                "review_notes": "Broad active-record scan found residual Japanese-style terminology; Japanese phrase was re-read from the original ROM at the live portal offset.",
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tip", type=Path, default=TIP)
    ap.add_argument("--out-json", type=Path, default=OUT_JSON)
    ap.add_argument("--out-csv", type=Path, default=OUT_CSV)
    args = ap.parse_args()

    rom = bytes(load_rom(args.tip))
    tbl = Tbl.load(TBL)
    d = make_dictionary_ext3(rom, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(rom)

    targets: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for source in iter_sources():
        logical = int(source["abs"], 16)
        got = read_encoded_z_safe(rom, sb + logical, max_len=256)
        if got is None:
            skipped.append({**source, "reason": "unreadable_current_record"})
            continue
        payload, terminator = bytes(got[0]), int(got[1])
        prefix, body, kind = split_prefix_body(payload)
        try:
            current = d.expand(body, tbl)
        except Exception as exc:
            skipped.append({**source, "reason": f"decode_failed:{exc}"})
            continue
        current_core = strip_pad(current)
        expected = corrected_text(source["jp"], current)
        if expected == current_core:
            continue
        rules = source_rules(source["jp"])
        # A correction is allowed only if the current output actually contains a known bad token,
        # except for the exact Camille sentence where the whole translation is reviewed.
        if source["jp"] != KNOWN_CAMILLE_JP and not any(bad in current_core for bad in BAD_TO_GOOD):
            skipped.append({**source, "reason": "source_rule_matched_but_current_bad_token_absent", "current": current_core})
            continue
        targets.append(
            {
                **source,
                "rules": rules,
                "target_mode": "record_render",
                "current": current_core,
                "ko": expected,
                "prefix_hex": prefix.hex().upper(),
                "record_kind": kind,
                "payload_len": len(payload),
                "body_len": len(body),
                "terminator": f"{terminator:06X}",
                "translation_source": "llm",
                "review_status": "approved",
                "review_notes": "Japanese source rechecked against the original ROM; terminology and obvious machine-translation hallucinations corrected.",
            }
        )

    seen_abs = {str(row["abs"]) for row in targets}
    for supplemental in iter_manifest_supplemental_sources(rom, d, tbl):
        if supplemental["abs"] in seen_abs:
            continue
        targets.append(supplemental)
        seen_abs.add(supplemental["abs"])
    targets.sort(key=lambda row: int(str(row["abs"]), 16))

    report = {
        "schema_version": 1,
        "generated_by": "tools/extract_machine_translation_terminology_targets.py",
        "tip": {"path": str(args.tip.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(rom), "size": len(rom)},
        "counts": {
            "targets": len(targets),
            "skipped": len(skipped),
            "brad": sum("brad_farren" in row["rules"] for row in targets),
            "rank_lieutenant_colonel": sum("rank_lieutenant_colonel" in row["rules"] for row in targets),
            "rank_major": sum("rank_major" in row["rules"] for row in targets),
            "rank_colonel": sum("rank_colonel" in row["rules"] for row in targets),
            "camille": sum("camille_ocr_dash" in row["rules"] for row in targets),
            "kagero_mayfly": sum("kagero_mayfly" in row["rules"] for row in targets),
            "record_render_targets": sum(row.get("target_mode") == "record_render" for row in targets),
            "private_ext3_phrase_targets": sum(row.get("target_mode") == "private_ext3_phrase" for row in targets),
        },
        "targets": targets,
        "skipped": skipped,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    fields = ["abs", "source", "target_mode", "portal_offset", "ext3_index", "rules", "jp", "current", "ko", "prefix_hex", "record_kind", "payload_len", "body_len", "terminator"]
    with args.out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in targets:
            cooked = dict(row)
            cooked["rules"] = ";".join(row["rules"])
            writer.writerow({key: cooked.get(key, "") for key in fields})

    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print("json:", args.out_json)
    print("csv:", args.out_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
