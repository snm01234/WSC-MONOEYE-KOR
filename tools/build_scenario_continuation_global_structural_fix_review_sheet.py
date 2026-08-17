#!/usr/bin/env python3
"""Build a representative runtime review sheet for the global continuation fix."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "out/patch/scenario_continuation_global_structural_fix_runtime_contracts.json"
REPORT = ROOT / "out/patch/scenario_continuation_global_structural_fix_report.json"
OUT_JSON = ROOT / "out/patch/scenario_continuation_global_structural_fix_review_sheet.json"
OUT_CSV = ROOT / "docs/SCENARIO_CONTINUATION_GLOBAL_STRUCTURAL_FIX_REVIEW_SHEET.csv"
OUT_MD = ROOT / "docs/SCENARIO_CONTINUATION_GLOBAL_STRUCTURAL_FIX_REVIEW_SHEET.md"

GROUPS = [
    {
        "id": "A",
        "title": "single-NUL 실제 こ 제거 / STAGE4 기준점",
        "focus": ["60BB48"],
        "check": "`그건 샤아 대령님을 좋아한다는` 다음 줄이 `뜻입니까！？`로 나오고 선두 `こ`가 없어야 함.",
    },
    {
        "id": "B",
        "title": "single-NUL 실제 こ 제거 + 직후 17 28",
        "focus": ["63687C"],
        "check": "선두 `こ` 없이 현재 한글 문장이 출력되고 직후 `17 28` 제어가 노출/중단 없이 처리되어야 함.",
    },
    {
        "id": "C",
        "title": "double-NUL structural 18 + E504 + 08xx",
        "focus": ["60B449"],
        "check": "`과거의 나도 그랬다。`가 정상 출력되고 직후 `08 0A`가 글자로 노출되지 않으며 다음 초상/대사가 정상이어야 함.",
    },
    {
        "id": "D",
        "title": "double-NUL structural 18 + E504 + 17 28",
        "focus": ["600455"],
        "check": "대사 직후 `17 28` page/control 진행이 정상이고 반복·스킵·제어문 노출이 없어야 함.",
    },
    {
        "id": "E",
        "title": "과거 page-merge 실패 기준 / structural 18 보존",
        "focus": ["6017FC", "601826"],
        "check": "`디아나 님！` 페이지가 독립 유지되고 이어지는 세 문장이 병합/`こ`/`亻` 없이 정상 진행되어야 함.",
    },
    {
        "id": "F",
        "title": "ordinary-native 단일 예외",
        "focus": ["615115"],
        "check": "E504가 아닌 기존 native token으로 복구된 유일한 항목. 한글 문구와 직후 `17 28` 진행이 정상이어야 함.",
    },
    {
        "id": "G",
        "title": "기존 E51D parameter 회귀",
        "focus": ["61035E"],
        "check": "기존 실측 anchor `가토오오오！！` 계열이 그대로 정상이어야 함.",
    },
    {
        "id": "H",
        "title": "기존 E51D fixed / STAGE22 회귀",
        "focus": ["638CD5"],
        "check": "웃소/카테지나 `……어？`와 후속 이벤트가 기존처럼 정상이어야 함.",
    },
]


def context_for(
    by_addr: dict[str, dict[str, Any]],
    ordered_scenario: list[dict[str, Any]],
    focus: list[str],
) -> list[dict[str, Any]]:
    """Return global scenario context: five dialogue rows before/after every focus.

    Context deliberately crosses bundle boundaries.  The previous sheet clipped
    context to one bundle, which hid nearby dialogue needed for semantic review.
    For multiple focus rows, overlapping +/-5 windows are merged while keeping
    original scenario address order.
    """
    index_by_addr = {str(r["address"]): i for i, r in enumerate(ordered_scenario)}
    focus_set = set(focus)
    selected: set[int] = set()
    for address in focus:
        if address not in by_addr or address not in index_by_addr:
            raise KeyError(f"review focus missing from scenario manifest: {address}")
        idx = index_by_addr[address]
        lo = max(0, idx - 5)
        hi = min(len(ordered_scenario), idx + 6)
        selected.update(range(lo, hi))

    rows = [ordered_scenario[i] for i in sorted(selected)]
    out=[]
    for r in rows:
        b=r.get("baseline_boundary") or {}
        out.append({
            "address": r["address"],
            "is_focus": r["address"] in focus_set,
            "bundle_id": r.get("bundle_id"),
            "route": r.get("route"),
            "text": r.get("baseline_text"),
            "control_prefix_hex": r.get("control_prefix_hex"),
            "next_control": b.get("next_control"),
            "nul_run": b.get("nul_run"),
            "portals": r.get("baseline_portals") or [],
        })
    return out


def main() -> int:
    doc=json.loads(MANIFEST.read_text(encoding="utf-8"))
    report=json.loads(REPORT.read_text(encoding="utf-8"))
    scenario=[r for r in doc["contracts"] if str(r.get("route") or "").startswith("scenario_")]
    scenario.sort(key=lambda r:int(r["address"],16))
    by_addr={r["address"]:r for r in scenario}
    changed={r["address"]:r for r in report["changed_rows"]}

    groups=[]
    csv_rows=[]
    for g in GROUPS:
        context=context_for(by_addr,scenario,g["focus"])
        focus_meta=[]
        for a in g["focus"]:
            r=by_addr[a]
            focus_meta.append({
                "address":a,
                "strategy": (changed.get(a) or {}).get("strategy","regression_anchor"),
                "body": r.get("baseline_body_hex"),
                "control_prefix": r.get("control_prefix_hex"),
                "next_control": (r.get("baseline_boundary") or {}).get("next_control"),
                "portals": r.get("baseline_portals") or [],
            })
        groups.append({**g,"focus_meta":focus_meta,"context":context})
        for i,row in enumerate(context):
            csv_rows.append({
                "group":g["id"],"title":g["title"],"check":g["check"],
                "address":row["address"],"focus":row["is_focus"],"bundle_id":row["bundle_id"],"text":row["text"],
                "route":row["route"],"control_prefix_hex":row["control_prefix_hex"],
                "next_control":row["next_control"],"nul_run":row["nul_run"],
            })

    out={
        "schema_version":1,
        "candidate":report["output"]["rom"],
        "counts":report["counts"],
        "groups":groups,
    }
    OUT_JSON.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    with OUT_CSV.open("w",encoding="utf-8-sig",newline="") as fh:
        fields=["group","title","check","address","focus","bundle_id","text","route","control_prefix_hex","next_control","nul_run"]
        w=csv.DictWriter(fh,fieldnames=fields);w.writeheader();w.writerows(csv_rows)

    md=[
        "# Scenario continuation 전역 구조 수정 대표 실측 시트","",
        f"후보 ROM: `{report['output']['rom']['path']}`  ",
        f"SHA-256: `{report['output']['rom']['sha256'].upper()}`  ",
        f"paired SaveRAM: `{report['output']['save']['path']}`","",
        "이번 후보는 single-NUL 실제 일본어 `こ` 잔류 6건을 제거하고, double-NUL structural `18 + direct E518` 2,740건을 1건 native + 2,739건 E504로 rehome한다.",
        "","## 판정 기준","",
        "- A/B: 실제 일본어 글자 `こ` 제거 경로. 선두 `18`이 없어져야 정상.",
        "- C/D/E: structural `18` 보존 + E504 경로. `18`은 유지되지만 화면에는 `こ`로 나오면 안 됨.",
        "- F: ordinary-native 예외 1건.",
        "- G/H: 새 dispatcher가 기존 E51D 동작을 깨지 않았는지 빠른 회귀 확인.","",
    ]
    for g in groups:
        md += [f"## {g['id']}. {g['title']}","",g["check"],""]
        md.append("| 구분 | 주소 | bundle | 대사 | route | prefix | 다음 control | NUL |")
        md.append("|---|---|---|---|---|---|---|---:|")
        for r in g["context"]:
            mark="**대상**" if r["is_focus"] else "문맥"
            text=str(r.get("text") or "").replace("|","\\|")
            md.append(f"| {mark} | `{r['address']}` | `{r.get('bundle_id') or ''}` | {text} | `{r['route']}` | `{r.get('control_prefix_hex') or ''}` | `{r.get('next_control') or ''}` | {r.get('nul_run')} |")
        md.append("")
        md.append("대상 저장 방식:")
        for fm in g["focus_meta"]:
            portals=", ".join(str(x.get("kind")) for x in fm["portals"]) or "none"
            md.append(f"- `{fm['address']}`: `{fm['strategy']}` / body `{fm['body']}` / portal `{portals}`")
        md.append("")
    md += ["## 최소 PASS 조건","",
           "A~F에서 `こ`/제어문 노출, 페이지 병합, 초상 오판, 대사 반복·스킵, Event Error가 없어야 한다. G/H는 기존 정상 anchor가 그대로 유지되면 된다.",
           "모두 정상이라면 `A~H PASS`처럼 알려주면 된다."]
    OUT_MD.write_text("\n".join(md)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"json":str(OUT_JSON.relative_to(ROOT)),"csv":str(OUT_CSV.relative_to(ROOT)),"md":str(OUT_MD.relative_to(ROOT)),"groups":len(groups)},ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
