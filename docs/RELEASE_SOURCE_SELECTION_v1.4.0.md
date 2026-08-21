# v1.4.0 공개 소스 / 배포 대상 선정

이 문서는 v1.4.0에서 공개 저장소에 포함할 정본 소스·검증 파일, 로컬 forensic 이력, GitHub Release 첨부 파일을 구분합니다.

## 1. 공개 소스 커밋 대상

### 루트 안내 / 버전 / 배포 문서

- `.gitignore`
- `README.md`
- `PATCH_GUIDE.md`
- `VERSION`
- `NOTICE.md`
- `RELEASE_NOTES_v1.4.0.md`
- `docs/DEVELOPMENT.md`
- `docs/REPOSITORY_POLICY.md`
- `docs/RELEASE_CHECKLIST.md`
- `docs/RELEASE_SOURCE_SELECTION_v1.4.0.md`

### v1.3.2 이후 활성 번역 사양

- `data/stage5_lalah_amuro_context_retranslation_ko.json`
- `tools/build_stage5_lalah_amuro_context_retranslation_candidate.py`

Stage 5 `빛나는 우주`의 라라아–아무로 문맥 교정 3행과 `60E087 그런데 왜！？` 유지 정책을 기록합니다. builder는 v1.3.2 정본 SHA에 fail-closed로 결속되어 관련 시나리오 행만 재구성합니다.

### Galmuri11 Condensed 주 대사 폰트 빌드 소스

- `tools/build_galmuri11_16x16_blitter_test_candidate.py`
- `tools/build_dialogue_galmuri11bitmap_condensed_8x16_exactlut_poc.py`
- `tools/build_dialogue_galmuri11bitmap_condensed_stemspace14_poc.py`
- `tools/build_dialogue_galmuri11bitmap_condensed_stemspace14_legacy_ui_sync_candidate.py`

위 파일은 최종 v1.4.0 폰트 렌더 구조를 재현하는 데 필요한 공용 경로입니다. 중간 16×16/weight/bridge/stemrepeat 비교 POC와 진단 도구는 공개 정본이 아닙니다.

최종 정책은 다음과 같습니다.

- 실제 텍스트 셀: 8×16 / 64-byte 4bpp
- font: Galmuri11Bitmap Condensed 2.40.3
- content height: 14
- weight: stemspace14
- stock `7A:027C` LUT 3모드와 byte-exact 동치
- 1,345개 한글 글리프 충돌 0
- legacy compact alias `공/분/근/전/사`를 동일 native 8×16 glyph로 런타임 매핑

### 폰트 로컬 빌드 의존성과 라이선스

v1.4.0 빌드는 로컬 작업 트리의 `assets/fonts/galmuri_tmp/Galmuri11Bitmap-Condensed-2.40.3.ttf`를 사용했습니다. Galmuri는 SIL Open Font License 1.1 적용 대상이며 프로젝트 MIT License와 별개입니다.

현재 저장소 정책상 `assets/fonts/`의 폰트 바이너리는 공개 Git/Release asset에 포함하지 않습니다. 폰트 빌더를 재실행하려는 개발자는 OFL 1.1 조건에 맞는 Galmuri 2.40.3 파일을 별도로 준비해 같은 경로에 배치해야 합니다. 공개 xdelta에는 완성 폰트 파일 자체가 아니라 ROM에 반영된 래스터 결과만 포함됩니다.

### 공용 빌드 / 감사 도구

- `tools/make_main_tip_xdelta.py`
- `tools/apply_main_tip_xdelta.py`
- `tools/dialogue_runtime_contracts.py`
- `tools/audit_dialogue_runtime_safety_gate.py`
- `tools/audit_global_dialogue_20cell.py`
- `tools/audit_current_untranslated_dialogue.py`
- `tools/audit_gundam_terminology_standard.py`
- `tools/audit_encyclopedia_name_consistency.py`
- `tools/test_dialogue_runtime_safety_gate.py`
- `tools/test_dialogue_runtime_contracts.py`

### 공개 배포 메타데이터

- `out/dist/monoeye_ko_expanded_v1.4.0.xdelta`
- `out/dist/monoeye_ko_expanded_v1.4.0_xdelta.json`
- `out/dist/monoeye_ko_expanded_v1.4.0_XDELTA_README.md`
- `out/dist/SHA256SUMS_v1.4.0.txt`

## 2. 로컬 forensic / review-only 이력

다음은 개발 과정의 원인 분석에는 유용하지만 공개 릴리스 정본에는 추가하지 않습니다.

- 16×16 Regular/Bold exact-LUT POC
- Galmuri11@8 제어군
- bridge14 / stemrepeat14 / weight130 / weight140 POC
- LUT capture/probe 스크립트와 emulator framebuffer 비교 도구
- `tools/build_dialogue_galmuri11bitmap_condensed_stemspace14_onebyte_sync_candidate.py`
- `tools/promote_dialogue_galmuri11bitmap_condensed_stemspace14_legacy_ui_sync_candidate.py`
- Stage 5 개별 promotion/rollback helper
- `out/patch/`의 candidate, promotion report, runtime manifest, post-promotion audit JSON
- `PATCH_PROGRESS.md`

사용자에게 채택된 최종 폰트 구현은 `...stemspace14_legacy_ui_sync_candidate.py`로 대표하고, 실패/비교 POC는 로컬 forensic history로만 유지합니다.

## 3. GitHub Release 첨부 파일

Release asset은 아래 2개만 권장합니다.

1. `monoeye_ko_expanded_v1.4.0.xdelta`
2. `SHA256SUMS_v1.4.0.txt`

다음은 저장소에는 유지하되 Release asset으로 중복 첨부하지 않습니다.

- `monoeye_ko_expanded_v1.4.0_xdelta.json`
- `monoeye_ko_expanded_v1.4.0_XDELTA_README.md`
- `RELEASE_NOTES_v1.4.0.md`

## 4. 절대 배포하지 않는 파일

- 일본판 원본 ROM
- `out/patch/monoeye_ko_expanded.wsc` 등 패치 완료 ROM
- candidate/probe/test ROM
- `.sav`, `.srm`, savestate
- emulator 실행 파일/프로필
- `PATCH_PROGRESS.md`
- `out/patch/`, `out/script/`의 대규모 생성 감사/중간 산출물
- 라이선스가 확인되지 않은 외부 자산

## 5. v1.4.0 배포 정본

- 기준 버전: **v1.3.2**
- 원본 SHA-256: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
- v1.4.0 결과 ROM SHA-256: `D1806D8E3D14B1B31246CAF745D6068022A7EE80492BF8D2485FA6458882E7FB`
- WonderSwan checksum: `27A1`
- v1.4.0 xdelta SHA-256: `0A3F4784AB39549031F0D2D7718C116735688BD9A5A57BE10EC0F0FAE6A7853D`
- xdelta 크기: `930,239` bytes
- 원본 → xdelta → 메인TIP byte-exact round-trip: PASS
- dialogue runtime safety: 24,954 contracts / hard failure 0 / review item 0
- 20셀 감사: 16,629행 / over 20 0 / unreadable 0
- 확정 사용자 가시 미번역 residual: 0
- terminology audit: clean
- runtime safety + runtime contract unit tests: 11 / 11 PASS
- 도감 이름 auto-unify candidate: 0

## 6. SaveRAM

v1.4.0의 Stage 5 교정과 폰트 승격은 ROM-only입니다. `sram/monoeye_ko_expanded.sav` SHA-256은 승격 전후 `B4E71F1EED3ABBBC4CEAAAEDBFBF4D6ED05E74DDF44F06D90DFFA5A1A41B2F11`로 byte-exact 유지되며 공개 배포 대상이 아닙니다.
