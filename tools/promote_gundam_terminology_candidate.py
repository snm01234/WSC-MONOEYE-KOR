#!/usr/bin/env python3
"""Build, verify, and atomically promote the Gundam terminology candidate.

This promotion is user-authorized for runtime validation. It treats the ROM,
active TBL, and Hangul marker metadata as one transaction because the candidate
moves the Hangul run marker from EC80 to EC8D and reuses EC80/EC81 as glyphs.
On any post-promotion failure, every promoted file is restored from the backup.
The live SaveRAM is never replaced.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "out/patch"
TIP = PATCH / "monoeye_ko_expanded.wsc"
SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL = PATCH / "hangul_patch_pad3.tbl"
CHAR_MAP = PATCH / "hangul_char_map.json"
PAD3_MAP = PATCH / "hangul_char_map_pad3.json"
APPROVAL = PATCH / "gundam_terminology_user_validation.json"
REPORT = PATCH / "gundam_terminology_promotion_report.json"
POST_AUDIT = PATCH / "gundam_terminology_postpromotion_audit.json"
POST_FALSE = PATCH / "gundam_terminology_postpromotion_false_segptr.json"

EXPECTED_PARENT = "be5cdb102a589faecd487780b99d3c30dd358e938e66cdb5aeb76ebcc8f4959c"
EXPECTED_CANDIDATE = "2fa34b87f1c975291c8bd60afa7df7fd4a92983fb84296f6216e01ad1f5fafef"
EXPECTED_SAVE = "8954611a8870bc5456accbeed0bb525ca2372bd5425ec274a75baf34d3bd5a01"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
OLD_MARKER = "EC80"
NEW_MARKER = "EC8D"
GLYPHS = {"잭": "EC80", "믹": "EC81"}
STICKY_COUNT = 1346


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ident(path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha(path),
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"invalid JSON root: {path}")
    return value


def atomic_bytes(path: Path, data: bytes, tag: str) -> None:
    tmp = path.with_name(f".{path.name}.{tag}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_text(path: Path, text: str, tag: str) -> None:
    tmp = path.with_name(f".{path.name}.{tag}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def checksum_ok(data: bytes) -> bool:
    return (sum(data[:-2]) & 0xFFFF) == int.from_bytes(data[-2:], "little")


def glyph_entry(*, ch: str, code: str, candidate_glyph_map: dict) -> dict:
    rows = ((candidate_glyph_map.get("glyphs") or []))
    match = next((row for row in rows if row.get("char") == ch and row.get("code") == code), None)
    require(match is not None, f"candidate glyph proof missing: {ch}={code}")
    code_i = int(code, 16)
    slot = code_i - 0xE740
    return {
        "code": code,
        "reuse": False,
        "pool": "padding_store_pad3",
        "glyph_index": code_i - 0xDF20,
        "file_offset": int(str(match["file_offset"]), 16),
        "stock_glyph_untouched": True,
        "pad3_slot": slot - 528,
        "promoted_by": "tools/promote_gundam_terminology_candidate.py",
    }


def update_maps(base_doc: dict, pad3_doc: dict, candidate_glyph_map: dict) -> tuple[dict, dict]:
    base = json.loads(json.dumps(base_doc, ensure_ascii=False))
    pad3 = json.loads(json.dumps(pad3_doc, ensure_ascii=False))

    for doc in (base, pad3):
        pad = doc.setdefault("padding_store", {})
        history = list(pad.get("marker_code_history") or [])
        if not any(str(row.get("code") or "").upper() == OLD_MARKER for row in history if isinstance(row, dict)):
            history.append(
                {
                    "code": OLD_MARKER,
                    "retired_because": "promoted Gundam terminology candidate reuses EC80 as the 잭 glyph; marker retargeted to EC8D",
                }
            )
        pad["marker_code_history"] = history
        pad["marker_code"] = NEW_MARKER
        pad["runtime_sticky_count"] = STICKY_COUNT
        pad["runtime_glyph_code_end"] = "EC81"
        pad["reserved_marker_code"] = NEW_MARKER
        mapping = doc.setdefault("mapping", {})
        for ch, code in GLYPHS.items():
            mapping[ch] = glyph_entry(ch=ch, code=code, candidate_glyph_map=candidate_glyph_map)

    # pad3 is the runtime padding map used by extension tools. Its previous 1186
    # count lagged behind the installed 1344-slot runtime. Move it to the exact
    # promoted contiguous glyph end so later extensions cannot overwrite EC80/81.
    pad3_pad = pad3.setdefault("padding_store", {})
    pad3_pad["count"] = STICKY_COUNT
    pad3_pad["pad_total_slots"] = STICKY_COUNT
    pad3["new_char_count"] = len(
        [ch for ch in (pad3.get("mapping") or {}) if len(ch) == 1 and "가" <= ch <= "힣"]
    )

    # The base map's count=528 describes the legacy pad1+pad2 physical seed and
    # is intentionally not rewritten. It is the marker source of truth, though.
    formula = str(base.get("glyph_formula") or "")
    if formula:
        formula = formula.replace("marker E3DB", f"marker {NEW_MARKER}").replace(
            "marker EC80", f"marker {NEW_MARKER}"
        )
        base["glyph_formula"] = formula
    return base, pad3


def main() -> int:
    for path in (TIP, SAVE, TBL, CHAR_MAP, PAD3_MAP, APPROVAL):
        require(path.is_file(), f"missing required file: {path}")
    require(TIP.stat().st_size == ROM_SIZE, "main TIP size drifted")
    require(sha(TIP) == EXPECTED_PARENT, "main TIP parent identity drifted")
    require(SAVE.stat().st_size == SAVE_SIZE, "live SaveRAM size drifted")
    require(sha(SAVE) == EXPECTED_SAVE, "live SaveRAM identity drifted before promotion")

    approval = load_json(APPROVAL)
    require(approval.get("approved") is True and approval.get("promotion_authorized") is True, "user promotion authorization missing")
    require(str(approval.get("parent_tip_sha256") or "").lower() == EXPECTED_PARENT, "approval parent binding mismatch")
    require(str(approval.get("candidate_sha256") or "").lower() == EXPECTED_CANDIDATE, "approval candidate binding mismatch")

    before = {
        "tip": ident(TIP),
        "save": ident(SAVE),
        "tbl": ident(TBL),
        "char_map": ident(CHAR_MAP),
        "pad3_map": ident(PAD3_MAP),
    }

    with tempfile.TemporaryDirectory(prefix="monoeye_gundam_term_") as td_raw:
        td = Path(td_raw)
        candidate = td / "gundam_terminology_candidate.wsc"
        candidate_save = td / "gundam_terminology_candidate.sav"
        candidate_tbl = td / "gundam_terminology_candidate.tbl"
        candidate_glyph_map_path = td / "gundam_terminology_candidate_glyph_map.json"
        candidate_report_path = td / "gundam_terminology_candidate_report.json"
        candidate_audit_path = td / "gundam_terminology_candidate_audit.json"

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/build_gundam_terminology_candidate.py"),
                "--out-rom", str(candidate),
                "--out-save", str(candidate_save),
                "--out-tbl", str(candidate_tbl),
                "--out-map", str(candidate_glyph_map_path),
                "--out-report", str(candidate_report_path),
            ],
            cwd=ROOT,
            check=True,
        )
        require(candidate.stat().st_size == ROM_SIZE, "candidate size invalid")
        require(sha(candidate) == EXPECTED_CANDIDATE, "candidate identity drifted")
        require(checksum_ok(candidate.read_bytes()), "candidate WonderSwan checksum invalid")
        require(candidate_save.read_bytes() == SAVE.read_bytes(), "candidate SaveRAM is not current live SaveRAM")

        build = load_json(candidate_report_path)
        audit = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/audit_gundam_terminology_standard.py"),
                "--tip", str(candidate),
                "--tbl", str(candidate_tbl),
                "--out", str(candidate_audit_path),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        candidate_audit = load_json(candidate_audit_path)
        require(candidate_audit.get("status") == "clean", f"candidate terminology audit failed: {audit.stdout}")
        counts = candidate_audit.get("counts") or {}
        require(
            int(counts.get("active_source_hits", -1)) == 0
            and int(counts.get("dictionary_hits", -1)) == 0
            and int(counts.get("rendered_record_hits", -1)) == 0,
            "candidate audit counts are not zero",
        )
        candidate_glyph_map = load_json(candidate_glyph_map_path)
        require(((candidate_glyph_map.get("marker") or {}).get("new") or "").upper() == NEW_MARKER, "candidate marker proof mismatch")
        require(int(((candidate_glyph_map.get("sticky") or {}).get("after_count") or -1)) == STICKY_COUNT, "candidate sticky count mismatch")

        new_base_map, new_pad3_map = update_maps(load_json(CHAR_MAP), load_json(PAD3_MAP), candidate_glyph_map)
        candidate_tbl_text = candidate_tbl.read_text(encoding="utf-8")
        require(f"{NEW_MARKER}=" in candidate_tbl_text, "candidate TBL missing new marker")
        require("EC80=잭" in candidate_tbl_text and "EC81=믹" in candidate_tbl_text, "candidate TBL missing new glyphs")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = PATCH / "backup" / f"{stamp}_pre_gundam_terminology"
        backup_dir.mkdir(parents=True, exist_ok=False)
        backup_paths = {
            TIP: backup_dir / TIP.name,
            TBL: backup_dir / TBL.name,
            CHAR_MAP: backup_dir / CHAR_MAP.name,
            PAD3_MAP: backup_dir / PAD3_MAP.name,
        }
        for src, dst in backup_paths.items():
            shutil.copy2(src, dst)
            require(sha(dst) == sha(src), f"backup verification failed: {src.name}")

        try:
            atomic_bytes(TIP, candidate.read_bytes(), "gundam_term")
            atomic_text(TBL, candidate_tbl_text, "gundam_term")
            atomic_text(CHAR_MAP, json.dumps(new_base_map, ensure_ascii=False, indent=2) + "\n", "gundam_term")
            atomic_text(PAD3_MAP, json.dumps(new_pad3_map, ensure_ascii=False, indent=2) + "\n", "gundam_term")

            require(sha(TIP) == EXPECTED_CANDIDATE, "promoted TIP is not exact candidate")
            require(checksum_ok(TIP.read_bytes()), "promoted TIP checksum invalid")
            require(ident(SAVE) == before["save"], "live SaveRAM changed during promotion")

            marker_probe = subprocess.run(
                [sys.executable, str(ROOT / "tools/hangul_marker.py")],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout.strip().upper()
            require(marker_probe == NEW_MARKER, f"installed marker metadata mismatch: {marker_probe}")

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/audit_gundam_terminology_standard.py"),
                    "--tip", str(TIP),
                    "--tbl", str(TBL),
                    "--out", str(POST_AUDIT),
                ],
                cwd=ROOT,
                check=True,
            )
            post = load_json(POST_AUDIT)
            post_counts = post.get("counts") or {}
            require(post.get("status") == "clean", "post-promotion terminology audit is not clean")
            require(
                int(post_counts.get("active_source_hits", -1)) == 0
                and int(post_counts.get("dictionary_hits", -1)) == 0
                and int(post_counts.get("rendered_record_hits", -1)) == 0,
                "post-promotion terminology residuals remain",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/scan_false_segptr_writes.py"),
                    "--target", str(TIP),
                    "--lo-bank", "0x5D",
                    "--hi-bank", "0x75",
                    "--out", str(POST_FALSE),
                ],
                cwd=ROOT,
                check=True,
            )
            false_doc = load_json(POST_FALSE)
            require(false_doc.get("ok") is True and int(false_doc.get("sites_found", -1)) == 0, "post-promotion false segmented pointer gate failed")
        except Exception:
            for target, backup in backup_paths.items():
                atomic_bytes(target, backup.read_bytes(), "rollback")
            raise

    after = {
        "tip": ident(TIP),
        "save": ident(SAVE),
        "tbl": ident(TBL),
        "char_map": ident(CHAR_MAP),
        "pad3_map": ident(PAD3_MAP),
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/promote_gundam_terminology_candidate.py",
        "ok": True,
        "promoted": True,
        "status": "promoted_for_user_runtime_validation",
        "authorized_by": "out/patch/gundam_terminology_user_validation.json",
        "before": before,
        "after": after,
        "backup_dir": str(backup_dir.relative_to(ROOT)).replace("\\", "/"),
        "backup_tip_sha256": sha(backup_paths[TIP]),
        "candidate_build": {
            "sha256": EXPECTED_CANDIDATE,
            "stock_dictionary": len(build.get("stock_dictionary") or []),
            "ext3_physical_groups": int(((build.get("ext3") or {}).get("physical_groups") or -1)),
            "ext3_append_repoint_groups": int(((build.get("ext3") or {}).get("append_repoint_groups") or -1)),
            "marker": f"{OLD_MARKER}->{NEW_MARKER}",
            "glyphs": GLYPHS,
        },
        "postpromotion": {
            "terminology_audit": str(POST_AUDIT.relative_to(ROOT)).replace("\\", "/"),
            "terminology_status": load_json(POST_AUDIT).get("status"),
            "terminology_counts": load_json(POST_AUDIT).get("counts"),
            "false_segmented_pointer_report": str(POST_FALSE.relative_to(ROOT)).replace("\\", "/"),
            "false_segmented_pointer_sites": int(load_json(POST_FALSE).get("sites_found", -1)),
            "installed_marker": NEW_MARKER,
            "live_saveram_unchanged": after["save"] == before["save"],
        },
        "rollback": {
            "tip": str(backup_paths[TIP].relative_to(ROOT)).replace("\\", "/"),
            "tbl": str(backup_paths[TBL].relative_to(ROOT)).replace("\\", "/"),
            "char_map": str(backup_paths[CHAR_MAP].relative_to(ROOT)).replace("\\", "/"),
            "pad3_map": str(backup_paths[PAD3_MAP].relative_to(ROOT)).replace("\\", "/"),
        },
    }
    atomic_text(REPORT, json.dumps(report, ensure_ascii=False, indent=2) + "\n", "report")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
