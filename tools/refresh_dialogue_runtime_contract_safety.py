#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from dialogue_runtime_contracts import audit_manifest

TARGET = ROOT / 'out/patch/monoeye_ko_expanded.wsc'
MANIFEST = ROOT / 'out/script/dialogue_runtime_contracts.json'
OUT = ROOT / 'out/patch/dialogue_runtime_contract_candidate_safety.json'

def main() -> int:
    rom = TARGET.read_bytes()
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    safety = audit_manifest(rom, manifest, target_path=TARGET)
    OUT.write_text(json.dumps(safety, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'ok': safety['ok'], 'sha256': safety['target']['sha256'], 'hard_failures': safety['counts']['hard_failures']}, ensure_ascii=False))
    return 0 if safety['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
