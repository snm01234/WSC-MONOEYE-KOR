# v1.3 공개 소스 / 배포 대상 선정

이 문서는 v1.3에서 **메인TIP 재현·검증에 필요한 공개 소스**와 **로컬 실측/후보 이력**, **GitHub Release 첨부 파일**을 분리합니다.

## 1. 공개 소스 커밋 대상

v1.3 커밋에는 다음 계열만 포함합니다.

- 루트 안내/버전: `.gitignore`, `README.md`, `PATCH_GUIDE.md`, `VERSION`, `RELEASE_NOTES_v1.3.md`
- 법적/정책 문서: `LICENSE`, `NOTICE.md`, `docs/LEGAL_NOTICE.md`, `docs/REPOSITORY_POLICY.md`, `docs/RELEASE_CHECKLIST.md`, `docs/DEVELOPMENT.md`
- v1.3에서 실제 변경된 활성 번역/구조 사양:
  - `data/battle_voice_user_reported_followup_ko.json`
  - `data/scenario_user_reported_followup_ko.json`
  - `data/id_command_preemptive_runtime_fix.json`
  - `data/encyclopedia_ms_batch02_ko.json`
  - `data/name75_base_ko.json`
  - `data/name75_base_ko_values.json`
  - `data/name75_terms_ko.json`
  - `data/runtime_text_residual_new_ko_id_batch03.json`
- 현재 권위 있는 공용 검증/배포 도구:
  - `tools/dialogue_runtime_contracts.py`
  - `tools/audit_dialogue_runtime_safety_gate.py`
  - `tools/audit_gundam_terminology_standard.py`
  - `tools/make_main_tip_xdelta.py`
  - `tools/apply_main_tip_xdelta.py`
  - `tools/xdelta3_tool.py`
  - `tools/monoeye_rom.py`
- 공개 배포 메타데이터:
  - `out/dist/monoeye_ko_expanded_v1.3.xdelta`
  - `out/dist/monoeye_ko_expanded_v1.3_xdelta.json`
  - `out/dist/monoeye_ko_expanded_v1.3_XDELTA_README.md`
  - `out/dist/SHA256SUMS_v1.3.txt`

기존 저장소에 이미 추적된 공용 빌드/번역 도구와 활성 `data/` 파일은 그대로 유지합니다. 이번 정리는 **v1.3에서 새로 생긴 단발 후보/진단 소스를 추가 공개하지 않는 것**에 초점을 둡니다.

## 2. 공개 소스에서 제외하는 v1.3 단발 이력

다음 계열은 최종 메인TIP을 만들기 위한 정본이 아니라 실측·후보 비교·승격 과정에서만 사용한 one-off 파일입니다. `.gitignore`로 공개 소스 대상에서 제외하고 로컬에서만 보존합니다.

- `tools/bizhawk_preemptive_*.lua`
- `tools/bizhawk_ws_domain_probe.lua`
- 도감 가나 색인의 폐기된 parser/render 후보 빌더
- `선제`의 centered/style-match/state40 probe 후보 빌더
- `move_icon`, `fire_wind`, `zero_leila` 후보 빌더
- 각 후보의 `promote_*` 단발 승격 도구
- `verify_id_command_preemptive_state40_video_diff.py`

이 파일들은 최종 v1.3 ROM이나 xdelta를 다시 적용하는 데 필요하지 않으며, 향후 forensic 확인이 필요할 때만 로컬 이력으로 사용합니다.

## 3. GitHub Release 첨부 파일

**실제 Release asset은 아래 2개만 권장합니다.**

1. `monoeye_ko_expanded_v1.3.xdelta`
2. `SHA256SUMS_v1.3.txt`

다음 파일은 저장소에는 유지하지만 Release asset으로 중복 첨부할 필요는 없습니다.

- `monoeye_ko_expanded_v1.3_xdelta.json` — 빌드/검증 메타데이터
- `monoeye_ko_expanded_v1.3_XDELTA_README.md` — 기술 적용 정보
- `RELEASE_NOTES_v1.3.md` — GitHub Release 본문으로 사용

## 4. 절대 배포하지 않는 파일

- 일본판 원본 ROM
- `out/patch/monoeye_ko_expanded.wsc` 등 패치 완료 ROM
- candidate/probe/test ROM
- `.sav`, `.srm`, savestate
- emulator 실행 파일/프로필
- `PATCH_PROGRESS.md`
- `out/patch/`, `out/script/`의 대규모 생성 감사/중간 산출물
- `legacy/`의 실제 바이너리/과거 작업 산출물

## 5. v1.3 배포 정본

- 원본 SHA-256: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
- v1.3 결과 ROM SHA-256: `26B780799C3C9CF0A554006C1B778025EC57A6F7C3B8FD7279D2CE654350FBC9`
- v1.3 xdelta SHA-256: `0471E51F7D1796D840F82B85278F54DAD4A97D833B260A05960CED39C5C15267`
- xdelta 크기: `1,615,611` bytes
- 원본 → xdelta → 메인TIP byte-exact round-trip: PASS
