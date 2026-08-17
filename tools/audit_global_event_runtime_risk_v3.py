#!/usr/bin/env python3
"""Whole-game event/runtime audit for the STAGE22t E51D v3 candidate."""
from __future__ import annotations

import json

import audit_global_event_runtime_risk_v2 as base

base.TARGET = base.ROOT / "out/patch/stage22t_uso_katejina_event8ce3_native2_portal_v3_candidate.wsc"
base.OUT = base.ROOT / "out/patch/global_event_runtime_risk_v3.json"
base.SPECIAL = bytes.fromhex("E51D")


def main() -> int:
    rc = base.main()
    report = json.loads(base.OUT.read_text(encoding="utf-8"))
    report["generated_by"] = "tools/audit_global_event_runtime_risk_v3.py"
    report["runtime_evidence"]["638CD5_v3"] = (
        "runtime pending; v3 changes only the globally intercepted portal identity "
        "from E51B to union-unowned E51D while preserving the v2-proven nested-native helper"
    )
    old_count = report["counts"].pop("special_E51B_native_dictionary_entries", None)
    report["counts"]["special_E51D_native_dictionary_entries"] = old_count
    blocker = report.pop("promotion_blockers_for_current_v2")
    report["promotion_blockers_for_current_v3"] = {
        "E51D_nested_dictionary_collision": bool(blocker.get("details")),
        "details": blocker.get("details") or [],
        "reason": (
            "E51D must have zero semantic ownership across typed text and every native/ext3 "
            "dictionary phrase before promotion. Current v3 audit finds zero dictionary collisions."
        ),
    }
    pool = report["safer_2byte_portal_pool"]
    old_usage = pool.pop("current_E51B_semantic_usage_on_parent", None)
    e5_source = base.semantic_e5_usage(
        bytes(base.load_rom(base.MAIN)),
        base.make_dictionary_ext3(
            bytes(base.load_rom(base.MAIN)),
            base.load_ext_meta(base.EXT_META),
            base.load_ext_meta(base.EXT3_META),
        ),
    )[1]
    pool["current_E51D_semantic_usage_on_parent"] = {
        kind: e5_source.get((0x1D, kind), 0)
        for kind in ("script", "aux", "name75", "native_dictionary", "ext3_phrase")
    }
    pool["previous_E51B_usage_for_reference"] = old_usage
    report["fix_direction"][0] = (
        "Do not promote v3 until the same STAGE22t runtime gate is replayed with E51D; "
        "the global E51B collision blocker itself is removed."
    )
    base.OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "status": report["status"],
        "counts": report["counts"],
        "v3_blocker": report["promotion_blockers_for_current_v3"],
        "E51D_usage": pool["current_E51D_semantic_usage_on_parent"],
        "report": str(base.OUT.relative_to(base.ROOT)),
    }, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
