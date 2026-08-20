# v1.3.2 공개 소스 / 배포 대상 선정

이 문서는 v1.3.2에서 **공개 저장소에 포함할 정본 소스·검증 파일**, **로컬 forensic 이력**, **GitHub Release 첨부 파일**을 구분합니다.

## 1. 공개 소스 커밋 대상

v1.3.2 커밋에는 다음 변경을 포함합니다.

### 루트 안내 / 버전 / 배포 문서

- `.gitignore`
- `README.md`
- `PATCH_GUIDE.md`
- `VERSION`
- `RELEASE_NOTES_v1.3.2.md`
- `docs/DEVELOPMENT.md`
- `docs/REPOSITORY_POLICY.md`
- `docs/RELEASE_CHECKLIST.md`
- `docs/RELEASE_SOURCE_SELECTION_v1.3.2.md`

### 활성 번역 / 구조 사양

- `data/mixed_residual_values/script_001.json`
- `data/name75_base_ko.json`
- `data/name75_base_ko_values.json`
- `data/name75_terms_ko.json`
- `data/bank59_proven_control_prefixes.json`
- `data/scenario_runtime_contract_supplement.json`
- `data/global_dialogue_boundary_retranslation_ko.json`
- `data/stage4_johnny_gato_dialogue_followup_ko.json`
- `data/stage4_epilogue_context_retranslation_ko.json`
- `data/stage4_mian_context_followup_retranslation_ko.json`
- `data/stage4_gihren_degin_context_retranslation_ko.json`

이 파일들은 v1.3.1 이후 현재 메인TIP에 실제로 반영된 행 경계 복구, Stage 4 문맥 재번역, `파라스 아테네` 표기 통일을 재현하거나 감사하는 데 필요한 활성 사양입니다.

### 공용 빌드 / 감사 도구

- `tools/make_main_tip_xdelta.py`
- `tools/dialogue_runtime_contracts.py`
- `tools/audit_bank59_event_width.py`
- `tools/audit_current_untranslated_dialogue.py`
- `tools/audit_global_dialogue_20cell.py`
- `tools/test_dialogue_runtime_safety_gate.py`
- `tools/audit_encyclopedia_name_consistency.py`
- `tools/build_global_dialogue_boundary_retranslation_candidate.py`
- `tools/build_stage4_johnny_gato_dialogue_followup_candidate.py`
- `tools/build_stage4_epilogue_context_retranslation_candidate.py`
- `tools/build_pallas_athene_name_unify_candidate.py`
- `tools/build_stage4_mian_context_followup_retranslation_candidate.py`
- `tools/build_stage4_gihren_degin_context_retranslation_candidate.py`

개별 builder는 v1.3.1 메인에서 v1.3.2까지의 승인된 변경 경로를 재현하기 위해 공개합니다. 반면 메인 파일 교체·백업·실측 승인 상태를 다루는 promotion helper는 로컬 운영 도구로 분리합니다.

### 공개 배포 메타데이터

- `out/dist/monoeye_ko_expanded_v1.3.2.xdelta`
- `out/dist/monoeye_ko_expanded_v1.3.2_xdelta.json`
- `out/dist/monoeye_ko_expanded_v1.3.2_XDELTA_README.md`
- `out/dist/SHA256SUMS_v1.3.2.txt`

## 2. v1.3.2 로컬 forensic / review-only 이력

다음은 공개 릴리스 소스에 추가하지 않습니다.

### 사용자 검토 후 채택하지 않은 전역 말투 감사

- `data/dialogue_speaker_register_policy_ko.json`
- `docs/DIALOGUE_REGISTER_GLOBAL_REVIEW_QUEUE.csv`
- `docs/DIALOGUE_SPEAKER_TONE_REVIEW_SHEET.csv`
- `tools/audit_dialogue_register_consistency.py`

전역 존대/반말 자동 분류는 화자·청자 관계를 잘못 묶는 오탐이 많아 v1.3.2 번역 기준으로 채택하지 않았습니다. Stage 4에서 실제 문맥이 확인된 장면만 별도 사양으로 반영했습니다.

### review-only 도감/이름 시트

- `docs/ENCYCLOPEDIA_NAME_AUTO_UNIFY_CANDIDATES.csv`
- `docs/ENCYCLOPEDIA_NAME_CONSISTENCY_ALL.csv`
- `docs/ENCYCLOPEDIA_NAME_REVIEW_EXCEPTIONS.csv`
- `docs/ENCYCLOPEDIA_NAME_UNREFERENCED.csv`

감사 도구 자체는 공개하지만, 대량의 원문/현재 번역 대조값을 담은 review sheet는 로컬 검토 자료로 유지합니다. 실제 정본 변경은 사용자가 확인한 `파라스 아테네`만 활성 `data/`에 반영했습니다.

### 개별 승격 / 실측 이력

- `tools/promote_global_dialogue_boundary_retranslation_candidate.py`
- `tools/promote_stage4_johnny_gato_dialogue_followup_candidate.py`
- `tools/promote_stage4_epilogue_context_retranslation_candidate.py`
- `tools/promote_pallas_athene_name_unify_candidate.py`
- `tools/promote_stage4_mian_context_followup_retranslation_candidate.py`
- `tools/promote_stage4_gihren_degin_context_retranslation_candidate.py`
- `docs/STAGE4_JOHNNY_GATO_DIALOGUE_FOLLOWUP_TEST_MATRIX.md`
- `out/patch/`의 candidate, promotion report, runtime manifest, post-promotion audit JSON

이 파일들은 로컬 롤백/실측 provenance로 보존하되 공개 릴리스 정본으로 취급하지 않습니다.

## 3. GitHub Release 첨부 파일

**Release asset은 아래 2개만 권장합니다.**

1. `monoeye_ko_expanded_v1.3.2.xdelta`
2. `SHA256SUMS_v1.3.2.txt`

다음 파일은 저장소에는 유지하지만 Release asset으로 중복 첨부하지 않습니다.

- `monoeye_ko_expanded_v1.3.2_xdelta.json` — 빌드/검증 메타데이터
- `monoeye_ko_expanded_v1.3.2_XDELTA_README.md` — 기술 적용 정보
- `RELEASE_NOTES_v1.3.2.md` — GitHub Release 본문

## 4. 절대 배포하지 않는 파일

- 일본판 원본 ROM
- `out/patch/monoeye_ko_expanded.wsc` 등 패치 완료 ROM
- candidate/probe/test ROM
- `.sav`, `.srm`, savestate
- emulator 실행 파일/프로필
- `PATCH_PROGRESS.md`
- `out/patch/`, `out/script/`의 대규모 생성 감사/중간 산출물
- `legacy/`의 실제 바이너리/과거 작업 산출물

## 5. v1.3.2 배포 정본

- 기준 버전: **v1.3.1**
- 원본 SHA-256: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
- v1.3.2 결과 ROM SHA-256: `E91CDE50CBE15386561495FB53FD51C26A279AD0614AAD57811D0169EFBC0BDB`
- WonderSwan checksum: `2B76`
- v1.3.2 xdelta SHA-256: `F7651DFB452CC17F49F454A7E1601A43BAFD33302451009295CD986BFE3E3CDB`
- xdelta 크기: `1,619,507` bytes
- 원본 → xdelta → 메인TIP byte-exact round-trip: PASS
- dialogue runtime safety: 24,954 contracts / hard failure 0 / review item 0
- 20셀 감사: 16,629행 / over 20 0 / unreadable 0
- 확정 사용자 가시 미번역 residual: 0
- terminology audit: clean
- runtime safety unit tests: 5 / 5 PASS
- runtime contract unit tests: 6 / 6 PASS

## 6. SaveRAM

v1.3.2에 포함된 모든 승격은 ROM-only로 수행했습니다. `sram/monoeye_ko_expanded.sav`는 각 승격 전후 byte-exact로 유지되었으며 공개 배포 대상이 아닙니다.
