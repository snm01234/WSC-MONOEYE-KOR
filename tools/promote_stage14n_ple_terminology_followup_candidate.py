#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from dialogue_runtime_contracts import audit_manifest

PATCH = ROOT / "out/patch"
MAIN = PATCH / "monoeye_ko_expanded.wsc"
CANDIDATE = PATCH / "stage14n_ple_terminology_followup_candidate.wsc"
LIVE_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TERM_AUDIT = PATCH / "stage14n_ple_terminology_followup_terminology_audit.json"
UNTRANSLATED_AUDIT = PATCH / "stage14n_ple_terminology_followup_untranslated_audit.json"
RUNTIME_MANIFEST = PATCH / "stage14n_ple_terminology_followup_runtime_contracts.json"
REPORT = PATCH / "stage14n_ple_terminology_followup_promotion_report.json"
BACKUPS = PATCH / "backup"

EXPECTED_PARENT = "24aa886359bb41e70161d47c66c90d683c91f0287c3be2eca856c7f520e7f1bf"
EXPECTED_CANDIDATE = "c7bb4b5c936653888062f2389351c586fc483dedacdba209918b327e440e2131"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768


class PromotionError(RuntimeError):
    pass


def sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")


def ident(path: Path) -> dict:
    return {"path": rel(path), "size": path.stat().st_size, "sha256": sha_path(path)}


def require(path: Path, *, size: int | None = None, sha: str | None = None) -> None:
    if not path.is_file():
        raise PromotionError(f"missing: {path}")
    if size is not None and path.stat().st_size != size:
        raise PromotionError(f"size drift: {path}")
    if sha is not None and sha_path(path) != sha:
        raise PromotionError(f"sha drift: {path}: {sha_path(path)} != {sha}")


def checksum_valid(path: Path) -> bool:
    data = path.read_bytes()
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    with src.open("rb") as sf, tmp.open("wb") as df:
        shutil.copyfileobj(sf, df, 1024 * 1024)
        df.flush()
        os.fsync(df.fileno())
    os.replace(tmp, dst)


def atomic_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main() -> int:
    require(MAIN, size=ROM_SIZE, sha=EXPECTED_PARENT)
    require(CANDIDATE, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
    require(LIVE_SAVE, size=SAVE_SIZE)
    require(TERM_AUDIT)
    require(UNTRANSLATED_AUDIT)
    require(RUNTIME_MANIFEST)
    if not checksum_valid(CANDIDATE):
        raise PromotionError("candidate checksum invalid")

    term = json.loads(TERM_AUDIT.read_text(encoding="utf-8"))
    if term.get("status") != "clean":
        raise PromotionError("terminology audit not clean")
    if str((term.get("tip") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("terminology audit not bound to candidate")
    term_counts = term.get("counts") or {}
    if any(int(term_counts.get(key, -1)) != 0 for key in (
        "active_source_hits", "dictionary_hits", "five_bank_dictionary_hits", "rendered_record_hits"
    )):
        raise PromotionError(f"terminology zero gate failed: {term_counts}")

    untranslated = json.loads(UNTRANSLATED_AUDIT.read_text(encoding="utf-8"))
    if untranslated.get("ok") is not True or untranslated.get("result") != "clean_no_sentence_like_japanese_residual":
        raise PromotionError("untranslated sentence audit not clean")
    if str((untranslated.get("candidate") or {}).get("sha256", "")).lower() != EXPECTED_CANDIDATE:
        raise PromotionError("untranslated audit not bound to candidate")
    reviewed = (((untranslated.get("checks") or {}).get("reviewed_translation_population") or {}))
    if int(reviewed.get("japanese_residuals", -1)) != 0:
        raise PromotionError("reviewed translation population still has Japanese residues")
    scenario = (((untranslated.get("checks") or {}).get("scenario_runtime_contracts") or {}))
    if int(scenario.get("sentence_like_japanese_residuals", -1)) != 0:
        raise PromotionError("scenario sentence-like Japanese residues remain")

    manifest = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
    runtime = audit_manifest(CANDIDATE.read_bytes(), manifest, target_path=CANDIDATE)
    runtime_counts = runtime.get("counts") or {}
    if runtime.get("ok") is not True:
        raise PromotionError(f"runtime contract audit failed: {runtime_counts}")
    if int(runtime_counts.get("hard_failures", -1)) != 0 or int(runtime_counts.get("review_items", -1)) != 0:
        raise PromotionError(f"runtime contract zero gate failed: {runtime_counts}")

    before = {"tip": ident(MAIN), "saveram": ident(LIVE_SAVE)}
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUPS / f"{stamp}_pre_stage14n_ple_terminology_followup"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup = backup_dir / MAIN.name
    shutil.copy2(MAIN, backup)
    require(backup, size=ROM_SIZE, sha=EXPECTED_PARENT)

    try:
        atomic_copy(CANDIDATE, MAIN)
        require(MAIN, size=ROM_SIZE, sha=EXPECTED_CANDIDATE)
        require(LIVE_SAVE, size=SAVE_SIZE, sha=before["saveram"]["sha256"])
        if not checksum_valid(MAIN):
            raise PromotionError("promoted checksum invalid")
    except Exception:
        atomic_copy(backup, MAIN)
        raise

    after = {"tip": ident(MAIN), "saveram": ident(LIVE_SAVE)}
    checks = {
        "main_matches_candidate": after["tip"]["sha256"] == EXPECTED_CANDIDATE,
        "checksum_valid": checksum_valid(MAIN),
        "saveram_unchanged": after["saveram"] == before["saveram"],
        "rollback_preserved": sha_path(backup) == EXPECTED_PARENT,
        "terminology_clean": term.get("status") == "clean",
        "untranslated_sentence_audit_clean": untranslated.get("ok") is True,
        "runtime_contracts_clean": runtime.get("ok") is True,
    }
    if not all(checks.values()):
        atomic_copy(backup, MAIN)
        raise PromotionError(f"post-promotion gate failed: {checks}")

    payload = {
        "schema_version": 1,
        "generated_by": "tools/promote_stage14n_ple_terminology_followup_candidate.py",
        "ok": True,
        "published": True,
        "promoted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "authorization": "user requested promotion if no analogous sentence-like Japanese residue remained",
        "before": before,
        "after": after,
        "checks": checks,
        "rollback": ident(backup),
        "audits": {
            "terminology": ident(TERM_AUDIT),
            "untranslated_sentence": ident(UNTRANSLATED_AUDIT),
            "runtime_manifest": ident(RUNTIME_MANIFEST),
            "runtime_counts": runtime_counts,
        },
        "scope": {
            "stage14n_missing_line": "でもね……どんなに不愉快でも、 -> 하지만……아무리　불쾌해도、",
            "ple": "풀 -> 플 where the character name is intended",
            "ple_two": "variant spellings standardized to 플투",
            "judau_split_fragment": "주、　도……？ -> 쥬、　도……？",
            "grwajib": "explicitly excluded; no change"
        },
        "saveram_policy": "ROM only promoted; current live SaveRAM preserved byte-exact"
    }
    atomic_json(REPORT, payload)
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PromotionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
