#!/usr/bin/env python3
"""Fail-closed audit for the whole-game scenario continuation structural candidate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MAIN=ROOT/'out/patch/monoeye_ko_expanded.wsc'
CAND=ROOT/'out/patch/scenario_continuation_global_structural_fix_candidate.wsc'
PARENT_MAN=ROOT/'out/script/dialogue_runtime_contracts.json'
CAND_MAN=ROOT/'out/patch/scenario_continuation_global_structural_fix_runtime_contracts.json'
BUILD=ROOT/'out/patch/scenario_continuation_global_structural_fix_report.json'
TERM=ROOT/'out/patch/scenario_continuation_global_structural_fix_terminology_audit.json'
SPEAKER=ROOT/'out/patch/scenario_continuation_global_structural_fix_speaker_audit.json'
BATTLE=ROOT/'out/patch/scenario_continuation_global_structural_fix_battle_audit.json'
OUT=ROOT/'out/patch/scenario_continuation_global_structural_fix_audit.json'
EXPECTED_MAIN='cfb90aaa7af2b9336fb63c70a8e7ec760ac51425d80017d5daf82e6118d86bca'


def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()

def main()->int:
    build=json.loads(BUILD.read_text(encoding='utf-8'))
    pm=json.loads(PARENT_MAN.read_text(encoding='utf-8'))
    cm=json.loads(CAND_MAN.read_text(encoding='utf-8'))
    term=json.loads(TERM.read_text(encoding='utf-8'))
    speaker=json.loads(SPEAKER.read_text(encoding='utf-8'))
    battle=json.loads(BATTLE.read_text(encoding='utf-8'))
    p={r['address']:r for r in pm['contracts']}; c={r['address']:r for r in cm['contracts']}
    changed=[r['address'] for r in build['changed_rows']]
    visible=set(build['visible_addresses'])
    render_bad=[]; boundary_bad=[]
    for a in changed:
        pr,cr=p[a],c[a]
        expected=str(pr.get('baseline_text') or '')
        if a in visible:
            if not expected.startswith('こ'):
                render_bad.append({'address':a,'reason':'parent_not_visible_ko','parent':expected})
                continue
            expected=expected[1:]
        if cr.get('baseline_text')!=expected:
            render_bad.append({'address':a,'expected':expected,'actual':cr.get('baseline_text')})
        pb=pr.get('baseline_boundary') or {}; cb=cr.get('baseline_boundary') or {}
        keys=('nul_run','next_address','next_control')
        if any(pb.get(k)!=cb.get(k) for k in keys):
            boundary_bad.append({'address':a,'parent':{k:pb.get(k) for k in keys},'candidate':{k:cb.get(k) for k in keys}})
    structural_risk=sum(bool(r.get('control18_storage_risk')) for r in cm['contracts'])
    visible_risk=sum(bool(r.get('visible_source_ko_leak_risk')) for r in cm['contracts'])
    e504=sum(any(x.get('kind')=='control18_portal16' for x in (r.get('baseline_portals') or [])) for r in cm['contracts'])
    term_drift=sum(r.get('source_terminator')!=r.get('baseline_terminator') for r in cm['contracts'])
    e51d_drift=[]
    for a in ('61035E','638CD5'):
        for k in ('baseline_payload_hex','baseline_body_hex','baseline_boundary'):
            if p[a].get(k)!=c[a].get(k): e51d_drift.append({'address':a,'field':k})
    terminology_hits=sum(len(term.get(k) or []) if isinstance(term.get(k),list) else int(term.get(k) or 0) for k in ('active_source_hits','dictionary_hits','five_bank_dictionary_hits','rendered_record_hits'))
    checks={
        'main_sha_unchanged':sha(MAIN)==EXPECTED_MAIN,
        'candidate_sha_matches_build':sha(CAND)==build['output']['rom']['sha256'],
        'changed_unique_2746':len(changed)==2746 and len(set(changed))==2746,
        'single_nul_visible_removed_6':build['counts']['single_nul_visible_ko_removed']==6,
        'double_nul_structural_2740':build['counts']['double_nul_structural18_total']==2740,
        'native_1':build['counts']['ordinary_native']==1,
        'portal16_2739':build['counts']['portal16']==2739 and e504==2739,
        'render_mismatch_0':not render_bad,
        'boundary_invariant_mismatch_0':not boundary_bad,
        'terminator_drift_0':term_drift==0,
        'control18_storage_risk_0':structural_risk==0,
        'visible_ko_leak_risk_0':visible_risk==0,
        'e51d_anchor_drift_0':not e51d_drift,
        'terminology_clean':terminology_hits==0,
        'speaker_mixed_0':int(speaker.get('japanese_or_mixed_remaining') or 0)==0 and int(speaker.get('over_20') or 0)==0,
        'battle_failures_0':bool(battle.get('ok')) and int((battle.get('counts') or {}).get('failures') or 0)==0,
    }
    report={'schema_version':1,'generated_by':'tools/audit_scenario_continuation_global_structural_fix_candidate.py','ok':all(checks.values()),'candidate':{'path':str(CAND.relative_to(ROOT)),'sha256':sha(CAND)},'counts':{'changed':len(changed),'structural_storage_risk':structural_risk,'visible_ko_leak_risk':visible_risk,'e504_portals':e504,'terminator_drift':term_drift,'render_mismatch':len(render_bad),'boundary_invariant_mismatch':len(boundary_bad)},'checks':checks,'render_mismatch':render_bad,'boundary_mismatch':boundary_bad,'e51d_drift':e51d_drift}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['ok'] else 2

if __name__=='__main__':
    raise SystemExit(main())
