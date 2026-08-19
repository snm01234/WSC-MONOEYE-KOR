# GitHub 저장소 / 배포 정책

이 문서는 공개 저장소에 포함할 파일과 로컬에만 보관할 파일을 구분합니다.

## 공개 저장소에 포함

- 패치 사용 안내: `README.md`, `PATCH_GUIDE.md`, `VERSION`
- 라이선스/권리 고지: `LICENSE`, `NOTICE.md`, `docs/LEGAL_NOTICE.md`
- 공개 검토를 마친 개발 문서: `docs/*.md`
- README 소개용 한글패치 적용 화면 캡처: `docs/images/title_screen_ko.png`처럼 소수의 선별 스크린샷만 허용
- 패치 빌드/검증 도구: `tools/*.py`
- 재현 가능한 빌드에 필요한 활성 번역/구조 사양: `data/`
- 배포용 xdelta: `out/dist/monoeye_ko_expanded_v1.3.1.xdelta`
- xdelta 검증 정보: `out/dist/monoeye_ko_expanded_v1.3.1_xdelta.json`, `out/dist/SHA256SUMS_v1.3.1.txt`
- 릴리스 변경 사항: `RELEASE_NOTES_v1.3.1.md`
- v1.3.1 공개 소스/Release asset 선정표: `docs/RELEASE_SOURCE_SELECTION_v1.3.1.md`

루트 `LICENSE`의 MIT License는 프로젝트가 실제로 라이선스를 부여할 권한이 있는 자체 작성 코드·도구·문서 등에 적용합니다. 제3자 게임 콘텐츠와 원작 권리는 `NOTICE.md`에서 명시적으로 분리합니다.

문서나 도구 안에서 게임 원문을 예시로 인용해야 할 때는 기술적 설명에 필요한 최소 범위만 사용하고, 대사·도감 텍스트를 목록 형태로 대량 재현하지 않는 것을 원칙으로 합니다.

## 공개 저장소에 포함하지 않음

- 원본 게임 ROM
- 패치 완료 ROM 및 모든 candidate/probe/test ROM
- SaveRAM, savestate, emulator profile
- BizHawk/Oswan 등 emulator 실행 파일
- 임시 screenshot, VRAM dump, 디버그 PNG (README 소개용으로 선별한 최소 스크린샷은 예외)
- `out/patch/`의 후보/감사/실험 산출물
- `out/script/`, `outputs/`의 대규모 작업 캐시
- 빌드 재현에 필요하지 않은 독립적인 원문 덤프·대규모 검수 worklist·ROM 추출 중간물
- `PATCH_PROGRESS.md`: 원문 인용과 상세 실측 기록이 누적된 내부 개발 로그
- `legacy/`의 실제 과거 바이너리/작업 산출물
- 로컬 폰트 파일 및 라이선스가 명확하지 않은 외부 자산

`.gitignore`는 이 원칙을 기본적으로 강제하며 `out/dist`에서는 공개할 xdelta 관련 파일만 명시적으로 허용합니다.

## legacy 정책

작업이 끝난 테스트 산출물은 삭제하기보다 `legacy/` 아래 원래 상대 경로를 유지해서 로컬 보관합니다. 2026-08-15 GitHub 배포 준비 이후에는 `tools/organize_release_core_assets.py`의 강한 정리 정책을 사용하며, 대상은 `legacy/release_core_20260815/<원래 경로>` 아래에 보존합니다.

로컬 `out/patch` 최상위에는 현재 메인TIP, 활성 TBL/사전 메타와 작은 현재 릴리스 검증 요약만 남기는 것을 원칙으로 합니다. 수십 MB 규모의 runtime-contract 전체 manifest, candidate/probe report, post-promotion audit 같은 재생성 가능한 결과물은 `legacy/`로 보관하고 정본 빌드 입력으로 취급하지 않습니다. 롤백 ROM은 `out/patch/backup/`에 별도 보존합니다. `out/script`에는 `translation_sheet.csv`, `excel_translate_cache.json`, `translations_quality_all.json`, `uncovered_translation_sheet_llm_reviewed.csv`, `dialogue_readability_changes.json`, `dialogue_runtime_safety_gate.json`만 핵심 작업 파일로 남기고 배치·큐·worklist·중간 audit/export는 legacy로 이동합니다.

GitHub에는 `legacy/README.md`와 필요 시 이동 매니페스트만 포함하고, 실제 ROM/SaveRAM/대형 진단 파일은 올리지 않습니다.

워크스페이스 전체 정리는 `tools/organize_workspace_release_core.py`를 기준으로 합니다. `outputs/`, `reference/`, `retroarch_savestate/`는 정본 빌드와 분리된 로컬 이력으로 보고 legacy에 보존하되, `savebackup/`은 사용자 진행 SaveRAM 보관소이므로 정리·이동 대상에서 제외하고 현재 위치에 그대로 유지합니다. `data/`의 활성 번역/구조 사양은 **다른 사용자가 합법적으로 소유한 일본판 원본 ROM으로 동일한 패치를 재현할 수 있도록 공개 Git 이력에 포함**합니다. 다만 빌드에 필요하지 않은 원문 덤프와 review-only 중간물은 별도 로컬 영역으로 분리하는 것을 원칙으로 합니다. `PATCH_PROGRESS.md`는 내부 개발 로그로만 유지합니다. `docs/`는 현재 구조·정책·최종 승격 문서 중 공개 검토를 마친 것만 Git에 포함합니다. `assets/fonts`와 원본 8 MiB ROM은 현재 빌드/xdelta 생성에 필요하므로 로컬 작업 트리에 유지하되 Git에는 포함하지 않습니다.

## GitHub Release asset 정책

현재 v1.3.1 Release에는 `monoeye_ko_expanded_v1.3.1.xdelta`와 `SHA256SUMS_v1.3.1.txt`만 첨부합니다. `_xdelta.json`과 `_XDELTA_README.md`는 저장소의 재현/검증 자료로 유지하고, `RELEASE_NOTES_v1.3.1.md`는 Release 본문으로 사용하므로 asset으로 중복 업로드하지 않습니다.

## 메인TIP 정책

개발 머신의 정본은 `out/patch/monoeye_ko_expanded.wsc`이지만 이 ROM 자체는 GitHub에 커밋하지 않습니다. 공개 배포는 반드시 원본 ROM을 포함하지 않는 xdelta 형식으로 만들고, 적용 대상은 사용자가 **합법적으로 소유한 일본판 원본 ROM**으로 명시합니다.

릴리스 전 확인:

1. 메인TIP SHA-256 확인
2. xdelta 재생성
3. xdelta round-trip이 메인TIP과 byte-exact인지 확인
4. xdelta SHA-256 기록
5. `.gitignore`에 ROM/SaveRAM 및 `PATCH_PROGRESS.md`가 차단되는지 확인
6. `data/`에는 실제 빌드에 필요한 사양만 남고, 재현에 필요 없는 독립 원문 덤프가 섞이지 않았는지 확인
7. 공개 문서에 대량의 게임 원문/그래픽이 포함되지 않았는지 확인
8. `README.md`와 `PATCH_GUIDE.md`의 해시가 현재 배포와 일치하는지 확인
9. `docs/LEGAL_NOTICE.md`의 비공식·비제휴·권리자 안내가 유지되는지 확인

## Git 이력에 ROM이 들어간 경우

단순히 `.gitignore`를 추가하는 것만으로 이미 커밋된 ROM이 이력에서 사라지지는 않습니다. 공개 저장소를 만들기 전에 `git status`와 첫 커밋 대상 목록을 확인하고 ROM/SaveRAM/에뮬레이터 바이너리가 staging되지 않았는지 검토해야 합니다.
