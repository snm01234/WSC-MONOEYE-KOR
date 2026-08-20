# GitHub 릴리스 체크리스트

## 패치 파일

- [x] `out/patch/monoeye_ko_expanded.wsc`가 사용자 승인 v1.3.2 최종 후보와 byte-exact다.
- [x] 메인TIP SHA-256과 WonderSwan checksum을 확인했다.
- [x] `tools/make_main_tip_xdelta.py`를 v1.3.2 / base v1.3.1로 갱신했다.
- [x] v1.3.2 xdelta를 원본 8 MiB ROM 기준으로 다시 생성했다.
- [x] xdelta round-trip 결과가 메인TIP과 byte-exact다.
- [x] VCDIFF header indicator가 `0x00`이며 secondary/application header가 배포 헤더에 없다.
- [x] `out/dist/SHA256SUMS_v1.3.2.txt`를 갱신했다.

현재 기준:

- 릴리스: **v1.3.2** · 기준 버전 v1.3.1
- 합법적으로 소유한 일본판 원본 ROM: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
- 메인TIP: `E91CDE50CBE15386561495FB53FD51C26A279AD0614AAD57811D0169EFBC0BDB`
- WonderSwan checksum: `2B76`
- xdelta: `F7651DFB452CC17F49F454A7E1601A43BAFD33302451009295CD986BFE3E3CDB`
- xdelta 크기: `1,619,507` bytes
- 원본→xdelta→메인TIP round-trip: PASS
- runtime contract: 24,954건 / hard failure 0 / review item 0
- 전역 20셀 감사: 16,629행 / over 20 0 / unreadable 0 / semantic guard failure 0
- 확정 사용자 가시 미번역 residual: 0
- terminology audit: clean
- encyclopedia name audit: 302 candidates / auto-unify 0 / review-required 11
- `tools/test_dialogue_runtime_safety_gate.py`: 5/5 PASS
- `tools/test_dialogue_runtime_contracts.py`: 6/6 PASS
- live SaveRAM: 모든 v1.3.2 승격 단계에서 byte-exact 보존

## v1.3.2 승격 체인

v1.3.1 메인 `8CDC239822B82DB874EEEFCCFD7AEBEEF67AE318B2CE32D1B1D69D6CB8C02A2C`에서 다음 사용자 승인 승격을 순서대로 적용했습니다.

- [x] Stage 4 조니 라이덴 / 가토 문맥 수정
- [x] 전역 source-boundary / 20셀 의미 복구
- [x] Stage 4 에필로그 미안·샤아·브라드 문맥 재번역
- [x] `파라스 아테네` name75 표기 통일
- [x] Stage 4 미안 장면 후속 기계번역 잔재 재번역
- [x] Stage 4 기렌–데긴 솔라 레이 장면 전면 재번역
- [x] 최종 메인TIP이 마지막 승인 후보와 byte-exact임을 확인했다.
- [x] 각 단계의 rollback TIP / SaveRAM / runtime manifest를 `out/patch/backup/`에 로컬 보존했다.
- [x] canonical `out/script/dialogue_runtime_contracts.json`을 최종 메인 기준으로 갱신했다.

## 공개 파일

- [x] `README.md`
- [x] `PATCH_GUIDE.md`
- [x] `VERSION` (`1.3.2`)
- [x] `LICENSE` (MIT)
- [x] `NOTICE.md`
- [x] `docs/LEGAL_NOTICE.md`
- [x] `docs/DEVELOPMENT.md`
- [x] `docs/REPOSITORY_POLICY.md`
- [x] `docs/RELEASE_CHECKLIST.md`
- [x] `docs/RELEASE_SOURCE_SELECTION_v1.3.2.md`
- [x] v1.3.2 활성 `data/` 번역/구조 사양
- [x] v1.3.2 공용 builder/auditor/runtime contract 도구
- [x] `out/dist/monoeye_ko_expanded_v1.3.2.xdelta`
- [x] `out/dist/monoeye_ko_expanded_v1.3.2_xdelta.json`
- [x] `out/dist/monoeye_ko_expanded_v1.3.2_XDELTA_README.md`
- [x] `out/dist/SHA256SUMS_v1.3.2.txt`
- [x] `RELEASE_NOTES_v1.3.2.md`

## 공개 금지 / forensic 분리 확인

- [x] 원본 `.wsc`는 Git 공개 대상이 아니다.
- [x] 패치 완료 `.wsc`는 Git 공개 대상이 아니다.
- [x] `.sav` / savestate는 Git 공개 대상이 아니다.
- [x] emulator 실행 파일은 Git 공개 대상이 아니다.
- [x] candidate/probe/test ROM은 Git 공개 대상이 아니다.
- [x] `PATCH_PROGRESS.md`는 내부 로그로 유지한다.
- [x] `out/patch/`의 candidate/promotion/audit/runtime manifest는 로컬 forensic 이력이다.
- [x] 개별 `promote_*` helper는 로컬 운영/롤백 도구로 분리한다.
- [x] 사용자가 채택하지 않은 전역 존대/반말 검토 시트와 policy는 공개 정본에서 제외한다.
- [x] 대량 원문 대조값을 포함한 encyclopedia review CSV는 공개 정본에서 제외한다.
- [x] 실제 반영된 `파라스 아테네` 표준화 값과 감사 도구는 공개 활성 소스로 유지한다.

## GitHub 업로드 전

- [x] README와 적용 가이드가 패치 입력을 **합법적으로 소유한 일본판 원본 ROM**으로 명확히 안내한다.
- [x] README/PATCH_GUIDE의 입력·출력·xdelta SHA를 v1.3.2 정본으로 갱신했다.
- [x] `VERSION`, xdelta 빌더 메타데이터, release notes가 모두 v1.3.2 / base v1.3.1로 일치한다.
- [x] `.gitignore`가 v1.3.2 `out/dist` 공개 파일만 허용하고 ROM/SaveRAM/forensic review 파일을 차단한다.
- [x] GitHub Release asset은 `monoeye_ko_expanded_v1.3.2.xdelta`와 `SHA256SUMS_v1.3.2.txt` 두 파일만 사용한다.
- [x] `_xdelta.json`, `_XDELTA_README.md`, `RELEASE_NOTES_v1.3.2.md`는 저장소/Release 본문에 두고 asset으로 중복 첨부하지 않는다.
- [x] ROM이나 SaveRAM을 GitHub Release asset에 첨부하지 않는다.
- [x] 저장소 설명/README가 비공식·비제휴 프로젝트임을 명확히 표시한다.
- [x] `LICENSE`는 프로젝트가 권리를 가진 작성물에 적용되고 제3자 게임 콘텐츠는 `NOTICE.md`에서 분리한다.
- [x] 공개 라이선스가 제3자 게임 자산·상표·원문에까지 적용되는 것처럼 표현하지 않는다.
- [x] 권리자 안내 및 보증 부인은 `NOTICE.md`와 `docs/LEGAL_NOTICE.md`에 유지한다.

## Release asset

GitHub Release에는 다음 두 파일만 첨부합니다.

1. `out/dist/monoeye_ko_expanded_v1.3.2.xdelta`
2. `out/dist/SHA256SUMS_v1.3.2.txt`

Release 본문은 `RELEASE_NOTES_v1.3.2.md`를 사용합니다.
