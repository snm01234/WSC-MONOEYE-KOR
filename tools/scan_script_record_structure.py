#!/usr/bin/env python3
"""
Scan the script banks for records whose *structure* differs from the original.

READ-ONLY.

An in-place localization must be size preserving: the record keeps its length and
its ``00`` terminator stays at the same offset. If a terminator moves, or a new
``00`` appears inside a record, the sequential walker that feeds the event
interpreter desynchronizes and starts reading payload/padding bytes as opcodes —
which is what an in-game "event error" reports.

For every record start walked on the ORIGINAL ROM this reports:
  * terminator moved (the record got longer or shorter),
  * a ``00`` byte appearing inside the target record where the original had none,
  * the record no longer terminating within ``max_len``.

Ordered by address so the first hit on the new-game path is easy to spot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import load_rom, read_encoded_z_safe, stock_base  # noqa: E402

DEFAULT_JP = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
DEFAULT_TARGET = ROOT / "out/patch/monoeye_ko_expanded.wsc"
DEFAULT_OUT = ROOT / "out/patch/script_record_structure.json"

DEFAULT_LO = 0x600000
DEFAULT_HI = 0x69FFFF
MAX_LEN = 256
MAX_LISTED = 400


def file_identity(path: Path, data: bytes | bytearray) -> Dict[str, Any]:
    """path/size/sha256 identity of an input the scan actually read."""
    payload = bytes(data)
    return {
        "path": str(Path(path).resolve()),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _load_local_expansion_approval(
    path: Path | None,
    *,
    baseline_path: Path | None,
    target_path: Path,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any] | None]:
    if path is None:
        return {}, None
    if baseline_path is None:
        raise SystemExit("--baseline is required with --approved-local-expansion-report")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("generated_by") not in {
        "tools/build_p2_local_ext3_expansion_candidate.py",
        "tools/build_p2_retired_slot_reclaim_candidate.py",
        "tools/build_p2_slot0208_stage_name_repair_candidate.py",
    }:
        raise SystemExit(f"not a local ext3 expansion approval report: {path}")
    if document.get("mode") != "pre_gate_detachment_approval" or document.get("ok") is not True:
        raise SystemExit(f"local expansion approval is not accepted: {path}")
    target_sha = hashlib.sha256(target_path.read_bytes()).hexdigest()
    approved_target = ((document.get("candidate_rom") or {}).get("sha256"))
    if approved_target != target_sha:
        raise SystemExit(
            f"local expansion approval is bound to {approved_target}, target is {target_sha}"
        )
    baseline_sha = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    approved_parent = (
        (document.get("local_expansion_parent_rom") or document.get("parent_rom") or {})
        .get("sha256")
    )
    if approved_parent != baseline_sha:
        raise SystemExit(
            f"local expansion parent is {approved_parent}, baseline is {baseline_sha}"
        )
    proof = document.get("proof") or {}
    required = (
        "local_expansion_rows_exact",
        "old_and_gap_nuls_verified",
        "next_record_boundaries_preserved",
        "new_terminators_exact",
        "following_records_byte_identical",
    )
    missing = [name for name in required if proof.get(name) is not True]
    if missing:
        raise SystemExit(f"local expansion approval lacks required proof {missing}: {path}")
    rows: dict[int, dict[str, Any]] = {}
    for row in (document.get("local_expansion") or {}).get("records") or []:
        try:
            logical = int(str(row["abs"]), 16)
            old_term = int(str(row["old_terminator"]), 16)
            new_term = int(str(row["new_terminator"]), 16)
            next_start = int(str(row["next_record_start"]), 16)
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"invalid local expansion row in {path}: {row!r}") from exc
        if logical in rows:
            raise SystemExit(f"duplicate local expansion row {logical:06X}: {path}")
        if new_term != old_term + 1 or next_start != new_term + 1:
            raise SystemExit(f"invalid local expansion boundary at {logical:06X}: {path}")
        rows[logical] = dict(row)
    if not rows:
        raise SystemExit(f"local expansion approval has no records: {path}")
    return rows, {
        "path": str(path),
        "candidate_sha256": approved_target,
        "parent_sha256": approved_parent,
        "records": len(rows),
    }


def scan(
    jp_path: Path,
    tgt_path: Path,
    lo: int,
    hi: int,
    *,
    baseline_path: Path | None = None,
    local_expansion_report: Path | None = None,
) -> dict:
    jp = bytes(load_rom(jp_path))
    tgt = bytes(load_rom(tgt_path))
    sj, st = stock_base(jp), stock_base(tgt)
    approved, approval_context = _load_local_expansion_approval(
        local_expansion_report,
        baseline_path=baseline_path,
        target_path=tgt_path,
    )
    baseline = bytes(load_rom(baseline_path)) if baseline_path is not None else None
    sb = stock_base(baseline) if baseline is not None else 0

    records = 0
    issues: List[dict] = []
    kinds: Dict[str, int] = {}
    approved_issues: List[dict] = []
    seen_approved: set[int] = set()

    cursor = lo
    while cursor <= hi:
        got = read_encoded_z_safe(jp, sj + cursor, max_len=MAX_LEN)
        if not got:
            cursor += 1
            continue
        jp_payload, jp_term_file = got
        jp_term = jp_term_file - sj
        records += 1

        tgot = read_encoded_z_safe(tgt, st + cursor, max_len=MAX_LEN)
        if not tgot:
            kind = "no_terminator_in_target"
            issues.append(
                {
                    "abs": f"{cursor:06X}",
                    "kind": kind,
                    "orig_len": len(jp_payload),
                    "orig_terminator": f"{jp_term:06X}",
                    "target_terminator": None,
                    "orig_hex": jp[sj + cursor : sj + cursor + len(jp_payload) + 1].hex(),
                    "target_hex": tgt[st + cursor : st + cursor + len(jp_payload) + 1].hex(),
                }
            )
            kinds[kind] = kinds.get(kind, 0) + 1
            cursor = jp_term + 1
            continue

        t_payload, t_term_file = tgot
        t_term = t_term_file - st
        if t_term != jp_term:
            kind = (
                "terminator_moved_earlier" if t_term < jp_term else "terminator_moved_later"
            )
            issue = {
                "abs": f"{cursor:06X}",
                "kind": kind,
                "orig_len": len(jp_payload),
                "target_len": len(t_payload),
                "orig_terminator": f"{jp_term:06X}",
                "target_terminator": f"{t_term:06X}",
                "delta": t_term - jp_term,
                "orig_hex": jp[sj + cursor : sj + cursor + len(jp_payload) + 1].hex(),
                "target_hex": tgt[st + cursor : st + cursor + len(jp_payload) + 2].hex(),
            }
            row = approved.get(cursor)
            approved_ok = False
            if row is not None and baseline is not None:
                old_term = int(str(row["old_terminator"]), 16)
                new_term = int(str(row["new_terminator"]), 16)
                next_start = int(str(row["next_record_start"]), 16)
                approved_ok = (
                    kind == "terminator_moved_later"
                    and jp_term == old_term
                    and t_term == new_term
                    and t_term == jp_term + 1
                    and next_start == t_term + 1
                    and baseline[sb + old_term] == 0
                    and baseline[sb + new_term] == 0
                    and tgt[st + old_term] != 0
                    and tgt[st + new_term] == 0
                )
            if approved_ok:
                seen_approved.add(cursor)
                approved_issues.append({**issue, "approval": "candidate_bound_local_expansion"})
            else:
                issues.append(issue)
                kinds[kind] = kinds.get(kind, 0) + 1
        elif cursor in approved:
            issues.append(
                {
                    "abs": f"{cursor:06X}",
                    "kind": "approved_local_expansion_missing",
                    "orig_len": len(jp_payload),
                    "target_len": len(t_payload),
                    "orig_terminator": f"{jp_term:06X}",
                    "target_terminator": f"{t_term:06X}",
                }
            )
            kinds["approved_local_expansion_missing"] = kinds.get(
                "approved_local_expansion_missing", 0
            ) + 1
        cursor = jp_term + 1

    missing_approved = sorted(set(approved) - seen_approved)
    for logical in missing_approved:
        issues.append(
            {
                "abs": f"{logical:06X}",
                "kind": "approved_local_expansion_not_walked",
            }
        )
        kinds["approved_local_expansion_not_walked"] = kinds.get(
            "approved_local_expansion_not_walked", 0
        ) + 1

    return {
        "ok": not issues,
        "generated_by": "tools/scan_script_record_structure.py",
        "read_only": True,
        "original": str(jp_path),
        "target": str(tgt_path),
        "inputs": {
            "original": file_identity(jp_path, jp),
            "target": file_identity(tgt_path, tgt),
        },
        "band": [f"{lo:06X}", f"{hi:06X}"],
        "records_walked": records,
        "approved_local_expansion": approval_context,
        "approved_terminator_moves": len(approved_issues),
        "approved_issue_sample": approved_issues[:MAX_LISTED],
        "issues": len(issues),
        "by_kind": kinds,
        "first_issues": issues[:MAX_LISTED],
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jp", type=Path, default=DEFAULT_JP)
    ap.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=DEFAULT_LO)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=DEFAULT_HI)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--baseline", type=Path, default=None)
    ap.add_argument("--approved-local-expansion-report", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.out.suffix.lower() == ".wsc":
        raise SystemExit("refusing to write a .wsc — this tool is read-only")
    rep = scan(
        args.jp,
        args.target,
        args.lo,
        args.hi,
        baseline_path=args.baseline,
        local_expansion_report=args.approved_local_expansion_report,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"target        : {rep['target']}")
    print(f"records walked: {rep['records_walked']}")
    print(f"issues        : {rep['issues']}  {rep['by_kind']}")
    for i in rep["first_issues"][:15]:
        print(
            f"  {i['abs']} {i['kind']} orig_term {i['orig_terminator']} "
            f"target_term {i.get('target_terminator')} delta {i.get('delta')}"
        )
        print(f"    orig   {i['orig_hex'][:60]}")
        print(f"    target {i['target_hex'][:60]}")
    print(f"\n→ {args.out}")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
