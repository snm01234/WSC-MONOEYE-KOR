# v1.3.1 공개 소스 / 배포 대상 선정

이 문서는 v1.3.1에서 **공개 저장소에 포함할 정본 소스·검증 파일**과 **로컬 실측/후보 이력**, **GitHub Release 첨부 파일**을 분리합니다.

## 1. 공개 소스 커밋 대상

v1.3.1 커밋에는 다음 변경을 포함합니다.

- 루트 안내/버전
  - `.gitignore`
  - `README.md`
  - `PATCH_GUIDE.md`
  - `VERSION`
  - `RELEASE_NOTES_v1.3.1.md`
- 공개 개발/배포 문서
  - `docs/DEVELOPMENT.md`
  - `docs/REPOSITORY_POLICY.md`
  - `docs/RELEASE_CHECKLIST.md`
  - `docs/RELEASE_SOURCE_SELECTION_v1.3.1.md`
- 공용 검증/배포 도구 변경
  - `tools/make_main_tip_xdelta.py` — 기본 릴리스를 v1.3.1 / base v1.3으로 갱신
  - `tools/test_dialogue_runtime_contracts.py` — 현재 runtime-visible anchor와 현재 메인 manifest 기준으로 self-contained 회귀 테스트 정비
- 공개 배포 메타데이터
  - `out/dist/monoeye_ko_expanded_v1.3.1.xdelta`
  - `out/dist/monoeye_ko_expanded_v1.3.1_xdelta.json`
  - `out/dist/monoeye_ko_expanded_v1.3.1_XDELTA_README.md`
  - `out/dist/SHA256SUMS_v1.3.1.txt`

기존 v1.3 공개 소스와 활성 `tools/`, `data/`, `docs/`는 그대로 유지합니다.

## 2. v1.3.1 로컬 forensic 이력

아래 파일은 실측·후보 비교·승격 과정의 one-off 이력으로, 공개 릴리스 소스에 추가하지 않습니다.

- `tools/build_sera_sig_followup_glyph_reset_candidate.py`
- `tools/build_dialogue_hangul_marker_runtime_fix_candidate.py`
- `tools/build_dialogue_structural_followup_candidate.py`
- `tools/build_stage3_reflow_idhelp_followup_candidate.py`
- `tools/promote_v1_3_1_dialogue_followup_candidate.py`
- `docs/BROTHER_TERM_CONTEXT_REVIEW_20260820.md`
- `docs/DIALOGUE_BOUNDARY_DUPLICATION_AUDIT_20260820.md`
- `docs/DIALOGUE_TWO_LINE_TRUNCATION_REFLOW_20260820.md`
- `out/patch/`의 candidate, user-validation, promotion, runtime-contract, post-promotion 감사 JSON

이 파일들은 현재 메인 ROM을 검증·롤백하는 로컬 forensic 자료입니다. 특히 `兄` 문맥 시트 등에는 게임 원문이 다량 포함될 수 있어 공개 저장소 정책상 제외합니다.

## 3. GitHub Release 첨부 파일

**Release asset은 아래 2개만 권장합니다.**

1. `monoeye_ko_expanded_v1.3.1.xdelta`
2. `SHA256SUMS_v1.3.1.txt`

다음 파일은 저장소에는 유지하지만 Release asset으로 중복 첨부하지 않습니다.

- `monoeye_ko_expanded_v1.3.1_xdelta.json` — 빌드/검증 메타데이터
- `monoeye_ko_expanded_v1.3.1_XDELTA_README.md` — 기술 적용 정보
- `RELEASE_NOTES_v1.3.1.md` — GitHub Release 본문

## 4. 절대 배포하지 않는 파일

- 일본판 원본 ROM
- `out/patch/monoeye_ko_expanded.wsc` 등 패치 완료 ROM
- candidate/probe/test ROM
- `.sav`, `.srm`, savestate
- emulator 실행 파일/프로필
- `PATCH_PROGRESS.md`
- `out/patch/`, `out/script/`의 대규모 생성 감사/중간 산출물
- `legacy/`의 실제 바이너리/과거 작업 산출물

## 5. v1.3.1 배포 정본

- 기준 버전: **v1.3**
- 원본 SHA-256: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
- v1.3.1 결과 ROM SHA-256: `8CDC239822B82DB874EEFCCFD7AEBEEF67AE318B2CE32D1B1D69D6CB8C02A2C`
- WonderSwan checksum: `CA9E`
- v1.3.1 xdelta SHA-256: `CC456DACE99F2F25B7B2AEECD835F64F04AF12AA1E0E96E944F14B2C334A078F`
- xdelta 크기: `1,616,690` bytes
- 원본 → xdelta → 메인TIP byte-exact round-trip: PASS
- dialogue runtime safety: 24,925 contracts / hard failure 0 / review item 0
- terminology audit: clean
- runtime contract unit tests: 6 / 6 PASS

## 6. SaveRAM

v1.3.1 승격은 ROM-only로 수행했습니다. `sram/monoeye_ko_expanded.sav`는 승격 전후 SHA-256이 동일하며 공개 배포 대상이 아닙니다.
