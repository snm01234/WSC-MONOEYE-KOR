#!/usr/bin/env python3
"""Download / materialize Data Crystal TBL into data/monoeye.tbl."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "monoeye.tbl"
URL = "https://datacrystal.tcrf.net/wiki/SD_Gundam_G_Generation:_Mono-Eye_Gundams/TBL"


def extract_from_html(html: str) -> str:
    # Prefer <pre> / syntaxhighlight blocks
    blocks = re.findall(r"<pre[^>]*>(.*?)</pre>", html, flags=re.S | re.I)
    if not blocks:
        blocks = re.findall(
            r"<div class=\"mw-highlight[^\"]*\"[^>]*>.*?<pre[^>]*>(.*?)</pre>",
            html,
            flags=re.S | re.I,
        )
    best = ""
    for b in blocks:
        text = re.sub(r"<[^>]+>", "", b)
        text = (
            text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&nbsp;", "\u00a0")
            .replace("&#039;", "'")
            .replace("&#160;", "\u00a0")
            .replace("&#12288;", "\u3000")  # ideographic space
        )
        # Preserve trailing spaces on TBL values (HTML often collapses them)
        if text.count("=") > best.count("="):
            best = text
    if best.count("=") < 100:
        raise RuntimeError("Could not locate TBL block in wiki HTML")
    lines = []
    for line in best.splitlines():
        # Do not strip RHS — only trim newline/CR
        line = line.strip("\r\n")
        if not line or "=" not in line:
            continue
        left, right = line.split("=", 1)
        left = left.strip()
        if re.fullmatch(r"[0-9A-Fa-f]{2,4}", left):
            lines.append(f"{left.upper()}={right}")
    # Known wiki/HTML casualties
    fixed = {}
    for line in lines:
        left, right = line.split("=", 1)
        fixed[left] = right
    if fixed.get("01", "") == "":
        fixed["01"] = "\u3000"
    lines = [f"{k}={v}" for k, v in fixed.items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching {URL}")
    req = urllib.request.Request(URL, headers={"User-Agent": "monoeye-tools/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    tbl = extract_from_html(html)
    OUT.write_text("# SD Gundam G Generation: Mono-Eye Gundams\n# Source: Data Crystal\n" + tbl, encoding="utf-8")
    print(f"Wrote {OUT} ({tbl.count(chr(10))} entries approx)")


if __name__ == "__main__":
    main()
