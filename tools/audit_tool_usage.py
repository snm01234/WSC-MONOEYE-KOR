#!/usr/bin/env python3
"""
Audit which tools are still load-bearing, to decide what can be archived.

READ-ONLY. Nothing is moved or deleted; this only produces the classification.

Signals collected per ``tools/*.py``:

``imported_by``    other tools that ``import`` it. A module with importers is a
                   library — moving it breaks callers, so it must stay.
``imports``        what it depends on.
``doc_refs``       mentions in ``PATCH_PROGRESS.md``, ``README.md`` and ``docs/``.
                   A tool named in the current pipeline or guard docs stays.
``rule_refs``      mentions in ``.cursor/rules/`` and ``.kiro/`` (specs, steering).
``has_main``       defines a CLI entry point (``__main__``), i.e. runnable.
``writes_rom``     contains a ROM write (``write_bytes`` on a ``.wsc`` path or
                   ``update_ws_checksum``). These are the risky ones to keep
                   around unguarded.
``mtime``

Proposed class:

``library``     imported by something else → keep
``documented``  referenced by a doc / rule / spec → keep
``verify``      name starts with ``verify_``/``smoke_``/``scan_``/``analyze_``/
                ``diff_``/``audit_`` → keep (diagnostics are cheap and useful)
``orphan``      runnable, nobody imports it, no doc mentions it → archive candidate
``temp``        name looks like scratch (``_tmp``, ``scratch``, ``test_``, ``probe``,
                ``hyp``) → archive candidate

The archive candidates are a *proposal*. Anything that writes a ROM is flagged so a
one-shot repair that has already been applied is not confused with a live step.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Set

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

DOC_PATHS = [ROOT / "PATCH_PROGRESS.md", ROOT / "README.md"]
DOC_DIRS = [ROOT / "docs"]
RULE_DIRS = [ROOT / ".cursor", ROOT / ".kiro"]

VERIFY_PREFIXES = (
    "verify_",
    "smoke_",
    "scan_",
    "analyze_",
    "diff_",
    "audit_",
    "measure_",
    "probe_",
    "diag_",
    "snapshot_",
)
TEMP_MARKERS = ("_tmp", "scratch", "hyp", "_test", "test_")
ROM_WRITE_HINTS = ("update_ws_checksum", "write_bytes")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def collect_doc_text() -> str:
    parts: List[str] = []
    for p in DOC_PATHS:
        if p.exists():
            parts.append(read_text(p))
    for d in DOC_DIRS:
        if d.exists():
            parts.extend(read_text(p) for p in d.rglob("*.md"))
    return "\n".join(parts)


def collect_rule_text() -> str:
    parts: List[str] = []
    for d in RULE_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.suffix in {".md", ".mdc", ".json", ".yaml", ".yml"}:
                parts.append(read_text(p))
    return "\n".join(parts)


def module_imports(path: Path) -> Set[str]:
    src = read_text(path)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        # fall back to a regex so a broken file still contributes edges
        return set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", src, re.M))
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "out/patch/tool_audit.json")
    args = ap.parse_args(argv)

    files = sorted(p for p in TOOLS.glob("*.py"))
    names = {p.stem for p in files}
    doc_text = collect_doc_text()
    rule_text = collect_rule_text()

    imports: Dict[str, Set[str]] = {}
    for p in files:
        imports[p.stem] = {m for m in module_imports(p) if m in names and m != p.stem}

    imported_by: Dict[str, Set[str]] = {n: set() for n in names}
    for src, deps in imports.items():
        for dep in deps:
            imported_by[dep].add(src)

    rows: List[dict] = []
    for p in files:
        name = p.stem
        src = read_text(p)
        doc_refs = doc_text.count(f"{name}.py")
        rule_refs = rule_text.count(f"{name}.py")
        has_main = "__main__" in src
        writes_rom = any(h in src for h in ROM_WRITE_HINTS)

        if imported_by[name]:
            klass = "library"
        elif doc_refs or rule_refs:
            klass = "documented"
        elif name.startswith(VERIFY_PREFIXES):
            klass = "verify"
        elif any(m in name for m in TEMP_MARKERS):
            klass = "temp"
        else:
            klass = "orphan"

        rows.append(
            {
                "name": name,
                "class": klass,
                "imported_by": sorted(imported_by[name]),
                "imports": sorted(imports[name]),
                "doc_refs": doc_refs,
                "rule_refs": rule_refs,
                "has_main": has_main,
                "writes_rom": writes_rom,
                "bytes": p.stat().st_size,
                "mtime": p.stat().st_mtime,
            }
        )

    by_class: Dict[str, List[str]] = {}
    for r in rows:
        by_class.setdefault(r["class"], []).append(r["name"])

    report = {
        "generated_by": "tools/audit_tool_usage.py",
        "read_only": True,
        "total_tools": len(rows),
        "counts": {k: len(v) for k, v in sorted(by_class.items())},
        "archive_candidates": sorted(by_class.get("orphan", []) + by_class.get("temp", [])),
        "archive_candidates_that_write_rom": sorted(
            r["name"]
            for r in rows
            if r["class"] in ("orphan", "temp") and r["writes_rom"]
        ),
        "by_class": {k: sorted(v) for k, v in sorted(by_class.items())},
        "tools": sorted(rows, key=lambda r: (r["class"], r["name"])),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"tools: {len(rows)}")
    for k, v in report["counts"].items():
        print(f"  {k:12s} {v}")
    print(f"\narchive candidates: {len(report['archive_candidates'])} "
          f"({len(report['archive_candidates_that_write_rom'])} of them write a ROM)")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
