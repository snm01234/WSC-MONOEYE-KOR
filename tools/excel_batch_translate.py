#!/usr/bin/env python3
"""
Batch-translate translation_sheet.csv JP→KO.

Engines (auto order):
  1) Excel =TRANSLATE() — Microsoft 365 cloud function
  2) Azure Translator Text API — AZURE_TRANSLATOR_KEY
  3) Bing/Microsoft Translator — same family as Excel TRANSLATE (no key)
  4) Google Translate via deep-translator — last-resort fallback
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
from script_translation_scope import translation_exclusion_reason
from translation_source_policy import reject_legacy_generator

csv.field_size_limit(10_000_000)


def normalize_game_punctuation(text: str) -> str:
    text = text.replace("...", "……").replace("‥", "……")
    text = text.replace("!", "！").replace("?", "？")
    while "。。。" in text:
        text = text.replace("。。。", "……")
    # Collapse MT artifacts like "！　！" / spaced ellipsis.
    text = text.replace("！　！", "！！").replace("？　？", "？？")
    text = text.replace("　……", "……").replace("……　", "……")
    text = text.replace(" ", "　")
    return text


def load_seed_overrides(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        line["jp"]: line["ko"].replace(" ", "　")
        for line in payload.get("lines", [])
        if line.get("jp") and line.get("ko")
    }


def translate_excel(
    texts: list[str],
    *,
    source_lang: str = "ja",
    target_lang: str = "ko",
    chunk_size: int = 150,
    settle_seconds: float = 1.5,
    max_wait_seconds: float = 180.0,
) -> list[str]:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.ScreenUpdating = False
    results = [""] * len(texts)
    try:
        wb = excel.Workbooks.Add()
        ws = wb.Worksheets(1)
        ws.Range("A1").Value = "テスト"
        ws.Range("B1").Formula = f'=TRANSLATE(A1,"{source_lang}","{target_lang}")'
        try:
            excel.CalculateUntilAsyncQueriesDone()
        except Exception:
            excel.Calculate()
        probe = str(ws.Range("B1").Text or "")
        wb.Close(SaveChanges=False)
        if not probe or probe.startswith("#"):
            raise RuntimeError(f"Excel TRANSLATE unavailable: {probe!r}")
        print(f"Excel TRANSLATE probe OK: テスト -> {probe}")

        for start in range(0, len(texts), chunk_size):
            chunk = texts[start : start + chunk_size]
            wb = excel.Workbooks.Add()
            ws = wb.Worksheets(1)
            for index, text in enumerate(chunk, start=1):
                ws.Cells(index, 1).Value = text
                ws.Cells(index, 2).Formula = (
                    f'=TRANSLATE(A{index},"{source_lang}","{target_lang}")'
                )
            try:
                excel.CalculateUntilAsyncQueriesDone()
            except Exception:
                excel.Calculate()
            deadline = time.time() + max_wait_seconds
            while time.time() < deadline:
                pending = False
                for index in range(1, len(chunk) + 1):
                    value = str(ws.Cells(index, 2).Text or "")
                    if value in {"", "#BUSY!", "#GETTING_DATA", "#CONNECT!"}:
                        pending = True
                        break
                if not pending:
                    break
                time.sleep(settle_seconds)
                try:
                    excel.CalculateUntilAsyncQueriesDone()
                except Exception:
                    pass
            for offset in range(len(chunk)):
                value = str(ws.Cells(offset + 1, 2).Text or "").strip()
                results[start + offset] = "" if value.startswith("#") else value
            wb.Close(SaveChanges=False)
            print(f"Excel translated {min(start + len(chunk), len(texts))}/{len(texts)}")
        return results
    finally:
        excel.Quit()
        pythoncom.CoUninitialize()


def translate_azure(
    texts: list[str],
    *,
    source_lang: str = "ja",
    target_lang: str = "ko",
    chunk_size: int = 50,
) -> list[str]:
    key = os.environ.get("AZURE_TRANSLATOR_KEY") or os.environ.get(
        "TRANSLATOR_TEXT_SUBSCRIPTION_KEY"
    )
    if not key:
        raise RuntimeError("AZURE_TRANSLATOR_KEY not set")
    region = os.environ.get("AZURE_TRANSLATOR_REGION") or os.environ.get(
        "TRANSLATOR_TEXT_REGION", "global"
    )
    endpoint = os.environ.get(
        "AZURE_TRANSLATOR_ENDPOINT",
        "https://api.cognitive.microsofttranslator.com",
    ).rstrip("/")
    url = f"{endpoint}/translate"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-type": "application/json",
        "X-ClientTraceId": str(uuid.uuid4()),
    }
    if region and region != "global":
        headers["Ocp-Apim-Subscription-Region"] = region

    results: list[str] = []
    for start in range(0, len(texts), chunk_size):
        chunk = texts[start : start + chunk_size]
        body = [{"text": text} for text in chunk]
        response = requests.post(
            url,
            params={"api-version": "3.0", "from": source_lang, "to": target_lang},
            headers=headers,
            json=body,
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        for item in payload:
            results.append(item["translations"][0]["text"])
        print(f"Azure translated {min(start + len(chunk), len(texts))}/{len(texts)}")
        time.sleep(0.2)
    return results


def translate_google(
    texts: list[str],
    *,
    source_lang: str = "ja",
    target_lang: str = "ko",
    chunk_size: int = 100,
    sleep_seconds: float = 0.15,
    on_chunk=None,
) -> list[str]:
    try:
        from deep_translator import GoogleTranslator
    except ImportError as exc:
        raise RuntimeError("pip install deep-translator") from exc

    translator = GoogleTranslator(source=source_lang, target=target_lang)
    sep = "\n<<<MONOEYE>>>\n"
    results: list[str] = []

    def translate_group(group: list[str]) -> list[str]:
        nonlocal translator
        packs: list[list[str]] = []
        current: list[str] = []
        current_len = 0
        for text in group:
            # Keep packs under Google's practical request size.
            extra = len(text) + len(sep)
            if current and current_len + extra > 4000:
                packs.append(current)
                current = []
                current_len = 0
            current.append(text)
            current_len += extra
        if current:
            packs.append(current)

        out: list[str] = []
        for part in packs:
            blob = sep.join(part)
            for attempt in range(6):
                try:
                    translated_blob = translator.translate(blob)
                    if not translated_blob:
                        raise RuntimeError("empty packed translation")
                    pieces = translated_blob.split(sep)
                    if len(pieces) != len(part):
                        pieces = []
                        for text in part:
                            one = translator.translate(text)
                            pieces.append(one or "")
                            time.sleep(sleep_seconds)
                    out.extend(item.strip() if item else "" for item in pieces)
                    break
                except Exception as exc:
                    if attempt >= 5:
                        # Last resort: empty strings rather than aborting the whole job.
                        print(f"Google giving up on pack size={len(part)}: {exc}", flush=True)
                        out.extend("" for _ in part)
                        break
                    wait = sleep_seconds * (attempt + 2)
                    print(f"Google retry after error: {exc} (sleep {wait:.1f}s)", flush=True)
                    time.sleep(wait)
                    translator = GoogleTranslator(source=source_lang, target=target_lang)
            time.sleep(sleep_seconds)
        return out

    for start in range(0, len(texts), chunk_size):
        chunk = texts[start : start + chunk_size]
        translated = [item or "" for item in translate_group(chunk)]
        if len(translated) != len(chunk):
            raise RuntimeError("google chunk size mismatch")
        results.extend(translated)
        print(
            f"Google translated {min(start + len(chunk), len(texts))}/{len(texts)}",
            flush=True,
        )
        if on_chunk is not None:
            on_chunk(start, chunk, translated)
    return results


def translate_bing(
    texts: list[str],
    *,
    source_lang: str = "ja",
    target_lang: str = "ko",
    workers: int = 8,
    chunk_size: int = 200,
    on_chunk=None,
) -> list[str]:
    """Microsoft Translator via Bing endpoint (same family as Excel TRANSLATE)."""
    import translators as ts
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = [""] * len(texts)

    def one(index: int, text: str) -> tuple[int, str]:
        if not text.strip():
            return index, ""
        for attempt in range(5):
            try:
                out = ts.translate_text(
                    text,
                    translator="bing",
                    from_language=source_lang,
                    to_language=target_lang,
                )
                return index, out or ""
            except Exception:
                if attempt >= 4:
                    return index, ""
                time.sleep(0.4 * (attempt + 1))
        return index, ""

    for start in range(0, len(texts), chunk_size):
        chunk = texts[start : start + chunk_size]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(one, start + offset, text)
                for offset, text in enumerate(chunk)
            ]
            for future in as_completed(futures):
                index, value = future.result()
                results[index] = value
        done = min(start + len(chunk), len(texts))
        print(f"Bing translated {done}/{len(texts)}", flush=True)
        if on_chunk is not None:
            on_chunk(start, chunk, results[start:done])
    return results


def translate_texts(texts: list[str], engine: str, on_chunk=None) -> tuple[list[str], str]:
    if not texts:
        return [], engine
    if engine == "excel":
        return translate_excel(texts), "excel"
    if engine == "azure":
        return translate_azure(texts), "azure"
    if engine == "bing":
        return translate_bing(texts, workers=12, on_chunk=on_chunk), "bing"
    if engine == "google":
        return translate_google(texts, on_chunk=on_chunk), "google"
    if engine != "auto":
        raise ValueError(f"Unknown engine: {engine}")

    try:
        return translate_excel(texts), "excel"
    except Exception as excel_exc:
        print(f"Excel engine skipped: {excel_exc}", flush=True)
    try:
        return translate_azure(texts), "azure"
    except Exception as azure_exc:
        print(f"Azure engine skipped: {azure_exc}", flush=True)
    try:
        return translate_bing(texts, workers=12, on_chunk=on_chunk), "bing"
    except Exception as bing_exc:
        print(f"Bing engine skipped: {bing_exc}", flush=True)
    return translate_google(texts, on_chunk=on_chunk), "google"


def main() -> None:
    reject_legacy_generator(Path(__file__))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sheet",
        type=Path,
        default=ROOT / "out" / "script" / "translation_sheet.csv",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out" / "script" / "translation_sheet.csv",
    )
    ap.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "out" / "script" / "excel_translate_cache.json",
    )
    ap.add_argument(
        "--seed",
        type=Path,
        default=ROOT / "data" / "translations_seed.json",
    )
    ap.add_argument(
        "--engine",
        choices=["auto", "excel", "azure", "bing", "google"],
        default="auto",
    )
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Replace existing non-empty KO with cache/MT output (default preserves latest sheet KO)",
    )
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--no-resume", action="store_false", dest="resume")
    args = ap.parse_args()

    with args.sheet.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    unique_jp: list[str] = []
    seen: set[str] = set()
    excluded_rows = 0
    for row in rows:
        try:
            abs_off = int((row.get("abs") or "").strip(), 16)
        except ValueError:
            abs_off = -1
        if abs_off >= 0 and translation_exclusion_reason(abs_off):
            excluded_rows += 1
            continue
        if not args.overwrite_existing and (row.get("ko") or "").strip():
            continue
        jp = row.get("jp") or ""
        if jp and jp not in seen:
            seen.add(jp)
            unique_jp.append(jp)
    if args.limit > 0:
        unique_jp = unique_jp[: args.limit]

    cache: dict[str, str] = {}
    if args.resume and args.cache.exists():
        raw_cache = json.loads(args.cache.read_text(encoding="utf-8"))
        if isinstance(raw_cache, dict) and isinstance(raw_cache.get("entries"), dict):
            cache = {str(k): str(v) for k, v in raw_cache["entries"].items()}
        elif isinstance(raw_cache, dict):
            cache = {str(k): str(v) for k, v in raw_cache.items() if k != "engine"}
        print(f"Loaded cache entries: {len(cache)}")

    for jp, ko in load_seed_overrides(args.seed).items():
        cache[jp] = ko

    todo = [jp for jp in unique_jp if not (cache.get(jp) or "").strip()]
    print(f"Sheet rows={len(rows)} unique_target={len(unique_jp)} todo={len(todo)}")

    used_engine = "cache-only"
    if todo:
        def persist_chunk(start: int, chunk: list[str], translated: list[str]) -> None:
            for jp, ko in zip(chunk, translated):
                cache[jp] = normalize_game_punctuation(ko or "")
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            args.cache.write_text(
                json.dumps(
                    {"engine": used_engine_box[0], "entries": cache},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"  cache saved ({len(cache)} entries, chunk@{start})")

        used_engine_box = ["pending"]
        translated, used_engine = translate_texts(
            todo, args.engine, on_chunk=persist_chunk
        )
        used_engine_box[0] = used_engine
        for jp, ko in zip(todo, translated):
            cache[jp] = normalize_game_punctuation(ko or "")
        args.cache.write_text(
            json.dumps(
                {"engine": used_engine, "entries": cache},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Wrote cache via engine={used_engine} ({len(cache)} entries)")

    filled = 0
    missing = 0
    excluded_cleared = 0
    existing_preserved = 0
    for row in rows:
        try:
            abs_off = int((row.get("abs") or "").strip(), 16)
        except ValueError:
            abs_off = -1
        exclusion = translation_exclusion_reason(abs_off) if abs_off >= 0 else None
        if exclusion:
            row["ko"] = ""
            if "notes" in row:
                row["notes"] = exclusion
            excluded_cleared += 1
            continue
        existing = (row.get("ko") or "").strip()
        if existing and not args.overwrite_existing:
            existing_preserved += 1
            filled += 1
            continue
        jp = row.get("jp") or ""
        ko = cache.get(jp, "")
        if ko:
            row["ko"] = ko
            filled += 1
        else:
            missing += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    meta = {
        "engine": used_engine,
        "rows": len(rows),
        "unique_jp": len(unique_jp),
        "filled": filled,
        "missing": missing,
        "excluded_rows": excluded_rows,
        "excluded_cleared": excluded_cleared,
        "existing_preserved": existing_preserved,
        "overwrite_existing": args.overwrite_existing,
        "out": str(args.out),
    }
    (ROOT / "out" / "script" / "batch_translate_report.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {args.out}")
    print(json.dumps(meta, ensure_ascii=False))
    if missing and args.limit == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
