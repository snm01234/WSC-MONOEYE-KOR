#!/usr/bin/env python3
"""Create a fresh, explicitly unreviewed MT draft for all uncovered rows.

This path is intentionally separate from the reviewed/canonical translation
assets. It never overwrites the master sheet and never promotes a ROM. Existing
approved C000/E001 rows are preserved; only pending rows are translated.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "out/script/uncovered_translation_sheet.csv"
OUT = ROOT / "out/script/uncovered_translation_sheet_auto_draft.csv"
BATCH_DIR = ROOT / "out/script/uncovered_batches_auto_draft"
CACHE = ROOT / "out/script/uncovered_auto_draft_translation_cache.json"
REPORT = ROOT / "out/patch/uncovered_auto_draft_translation_report.json"
DRAFT_MANIFEST = ROOT / "out/patch/uncovered_auto_draft_batch_manifest.json"
SOURCE_MANIFEST = ROOT / "out/patch/uncovered_translation_batch_manifest.json"

MARKER_RE = re.compile(r"<<<M(\d{6})>>>")
JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")

# Fresh MT occasionally preserves battle cries or malformed leading characters.
# These are narrow, address-bound draft fixes, not reviewed canonical text.
MANUAL_DRAFT_OVERRIDES = {
    "595F71": "한번、천천히　생각해　보면　좋을　거야。",
    "598CEF": "혼자　도맡아　지휘했던　인물이라던가……",
    "5D265F": "천운은　내게　있다！",
    "5D5E2F": "케에에엣！！",
    "5D9747": "이　멍청한　놈이이！！",
    "5D9DA3": "크윽……！",
    "5DA0CE": "크윽……！",
    "5DA3F9": "크윽……！",
    "5DAE97": "누우우웃……！！",
    "5E09F3": "으아아앗！！",
    "5E1C78": "쓰러뜨려　주마아앗！！",
    "5E1F68": "카아아앗！！",
    "5E1F8E": "누오오옷！！",
    "5E1FFA": "이놈으으읏！！",
    "5EBB7A": "으랴앗！",
    "5950E0": "맵　좌상까지　가라！！",
    "595159": "방해하지　마！！",
    "5955B9": "맵　좌상으로　가는　것을　도와라！",
    "5D2FAC": "……젠장！",
    "5D4363": "맞았다……！",
    "5D464C": "……큭、연방놈！",
    "5D9266": "버텨라！！",
    "5D9B52": "방해다아앗！！",
    "5DC1C9": "젠장……！",
    "5E11EA": "쏴라、풀투！！",
    "5E4163": "큭……루움의　재현인가……",
    "5E915A": "크윽、함을　후퇴시켜라！！",
    "5EA20D": "큭、각오를　굳힐까……",
    "5EAA6C": "크크크크크……",
    "5EB5E8": "크크크크크……",
}


class DraftError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_game_text(text: str) -> str:
    value = unicodedata.normalize("NFC", text or "")
    value = value.replace("\r", " ").replace("\n", " ").strip()
    value = value.replace("...", "……").replace("‥", "……")
    value = value.replace("!", "！").replace("?", "？")
    value = value.replace(",", "、")
    value = re.sub(r"！\s*！", "！！", value)
    value = re.sub(r"？\s*？", "？？", value)
    value = re.sub(r"\s+", "　", value)
    while "。。。" in value:
        value = value.replace("。。。", "……")
    value = value.replace("　……", "……").replace("……　", "……")
    return value.strip("　")


def google_request(text: str, timeout: float = 30.0) -> str:
    query = urllib.parse.urlencode(
        {
            "client": "gtx",
            "sl": "ja",
            "tl": "ko",
            "dt": "t",
            "q": text,
        }
    )
    url = "https://translate.googleapis.com/translate_a/single?" + query
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MonoEyeDraft/1.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return "".join(str(piece[0] or "") for piece in payload[0] if piece and piece[0] is not None)


def parse_marked(text: str, expected: list[int]) -> dict[int, str] | None:
    matches = list(MARKER_RE.finditer(text))
    if [int(match.group(1)) for match in matches] != expected:
        return None
    out: dict[int, str] = {}
    for position, match in enumerate(matches):
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        out[int(match.group(1))] = text[start:end].strip()
    return out


def translate_pack(items: list[tuple[int, str]], *, retries: int = 5) -> dict[int, str]:
    blob = "\n".join(f"<<<M{index:06d}>>>\n{text}" for index, text in items)
    expected = [index for index, _text in items]
    last_error = ""
    for attempt in range(retries):
        try:
            translated = google_request(blob)
            parsed = parse_marked(translated, expected)
            if parsed is not None:
                return parsed
            last_error = "marker mismatch"
        except Exception as exc:  # network boundary
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.6 * (attempt + 1))
    if len(items) == 1:
        index, text = items[0]
        for attempt in range(retries):
            try:
                return {index: google_request(text)}
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(0.8 * (attempt + 1))
        raise DraftError(f"translation failed for row {index}: {last_error}")
    middle = len(items) // 2
    return {
        **translate_pack(items[:middle], retries=retries),
        **translate_pack(items[middle:], retries=retries),
    }


def load_cache() -> dict[str, str]:
    if not CACHE.exists():
        return {}
    payload = json.loads(CACHE.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("engine") != "google_translate_fresh_draft":
        raise DraftError("auto-draft cache identity drifted")
    entries = payload.get("entries") or {}
    return {str(key): str(value) for key, value in entries.items()}


def write_cache(entries: dict[str, str]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "engine": "google_translate_fresh_draft",
                "canonical": False,
                "review_status": "unreviewed_draft",
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def source_for(row: dict[str, str]) -> str:
    current = str(row.get("current_text") or "").strip()
    original = str(row.get("original_jp") or "").strip()
    # Mixed rows already contain established Korean proper nouns. Feeding the
    # mixed form preserves those names while translating the remaining grammar.
    return current if current and HANGUL_RE.search(current) else original


def cache_key(row: dict[str, str], source: str) -> str:
    return sha256(
        (str(row.get("source_body_sha256") or "") + "\0" + source).encode("utf-8")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack-chars", type=int, default=3200)
    parser.add_argument("--pack-items", type=int, default=32)
    parser.add_argument("--pause", type=float, default=0.12)
    args = parser.parse_args(argv)

    with MASTER.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    if len(rows) != 1893 or len({row["abs"] for row in rows}) != 1893:
        raise DraftError("master sheet population drifted")

    cache = load_cache()
    pending_indices: list[int] = []
    sources: dict[int, str] = {}
    keys: dict[int, str] = {}
    for index, row in enumerate(rows):
        if str(row.get("workflow_status") or "") != "pending_translation":
            continue
        source = source_for(row)
        if not source:
            raise DraftError(f"empty source at {row.get('abs')}")
        pending_indices.append(index)
        sources[index] = source
        keys[index] = cache_key(row, source)

    todo = [index for index in pending_indices if not cache.get(keys[index], "").strip()]
    cursor = 0
    while cursor < len(todo):
        pack: list[tuple[int, str]] = []
        chars = 0
        while cursor < len(todo) and len(pack) < args.pack_items:
            index = todo[cursor]
            text = sources[index]
            estimated = len(text) + 24
            if pack and chars + estimated > args.pack_chars:
                break
            pack.append((index, text))
            chars += estimated
            cursor += 1
        translated = translate_pack(pack)
        for index, value in translated.items():
            cache[keys[index]] = normalize_game_text(value)
        write_cache(cache)
        print(f"translated {cursor}/{len(todo)} fresh; cache={len(cache)}", flush=True)
        time.sleep(args.pause)

    # A second individual pass often resolves names or fragments left in kana.
    residual_before: list[int] = []
    for index in pending_indices:
        value = normalize_game_text(cache.get(keys[index], ""))
        if not value or JP_RE.search(value):
            residual_before.append(index)
    for done, index in enumerate(residual_before, start=1):
        value = normalize_game_text(google_request(str(rows[index]["original_jp"])))
        if value and JP_RE.search(value):
            value = normalize_game_text(google_request(value))
        cache[keys[index]] = value
        if done % 20 == 0 or done == len(residual_before):
            write_cache(cache)
            print(f"residual retry {done}/{len(residual_before)}", flush=True)
        time.sleep(args.pause)

    residuals: list[dict[str, Any]] = []
    empty: list[str] = []
    for index in pending_indices:
        row = rows[index]
        address = str(row["abs"]).upper()
        ko = normalize_game_text(MANUAL_DRAFT_OVERRIDES.get(address, cache.get(keys[index], "")))
        if not ko:
            empty.append(str(row["abs"]))
        if JP_RE.search(ko):
            residuals.append(
                {
                    "abs": row["abs"],
                    "source": sources[index],
                    "original_jp": row["original_jp"],
                    "draft": ko,
                }
            )
        row["ko"] = ko
        row["translation_source"] = "google_translate_fresh_draft"
        row["review_status"] = "unreviewed_draft"
        row["workflow_status"] = "draft_auto"
        note = "user-authorized unreviewed draft candidate only; never promote without review/runtime validation"
        row["notes"] = (str(row.get("notes") or "") + "; " + note).strip("; ")

    if empty or residuals:
        write_cache(cache)
        REPORT.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generated_by": "tools/build_uncovered_auto_draft_sheet.py",
                    "ok": False,
                    "empty": empty,
                    "japanese_residuals": residuals,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        raise DraftError(f"draft incomplete: empty={len(empty)} residual={len(residuals)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["batch_id"])].append(row)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    for batch_id, batch_rows in grouped.items():
        with (BATCH_DIR / f"{batch_id}.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(batch_rows)

    source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    draft_batches: list[dict[str, Any]] = []
    for batch in source_manifest.get("batches") or []:
        batch_id = str(batch["batch_id"])
        batch_rows = grouped[batch_id]
        draft_batches.append(
            {
                **batch,
                "sheet": f"out/script/uncovered_batches_auto_draft/{batch_id}.csv",
                "draft_ready_records": len(batch_rows),
                "status": "draft_ready",
                "provenance": "approved rows preserved; pending rows are fresh unreviewed Google MT",
            }
        )
    draft_manifest = {
        "schema_version": 1,
        "generated_by": "tools/build_uncovered_auto_draft_sheet.py",
        "ok": True,
        "canonical": False,
        "promotion_allowed": False,
        "source_manifest": str(SOURCE_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "master_sheet": str(OUT.relative_to(ROOT)).replace("\\", "/"),
        "counts": {
            "records": len(rows),
            "preserved_approved_or_candidate": len(rows) - len(pending_indices),
            "fresh_auto_draft": len(pending_indices),
            "japanese_residuals": 0,
            "empty": 0,
            "batches": len(draft_batches),
        },
        "batches": draft_batches,
    }
    DRAFT_MANIFEST.write_text(
        json.dumps(draft_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_uncovered_auto_draft_sheet.py",
        "ok": True,
        "canonical": False,
        "promotion_allowed": False,
        "engine": "google_translate_fresh_draft",
        "master_sheet": str(OUT.relative_to(ROOT)).replace("\\", "/"),
        "batch_directory": str(BATCH_DIR.relative_to(ROOT)).replace("\\", "/"),
        "cache": str(CACHE.relative_to(ROOT)).replace("\\", "/"),
        "manifest": str(DRAFT_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "counts": draft_manifest["counts"],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
