# GitHub 릴리스 체크리스트

## 패치 파일

- [x] `out/patch/monoeye_ko_expanded.wsc`가 사용자 승인 v1.3.1 후보와 byte-exact다.
- [x] 메인TIP SHA-256과 WonderSwan checksum을 확인했다.
- [x] `tools/make_main_tip_xdelta.py`로 v1.3.1 xdelta를 다시 생성했다.
- [x] xdelta round-trip 결과가 메인TIP과 byte-exact다.
- [x] VCDIFF header indicator에 `VCD_SECONDARY`/application header가 없고 xdeltaUI/구버전 호환 형식이다.
- [x] `out/dist/SHA256SUMS_v1.3.1.txt`의 해시를 갱신했다.

현재 기준:

- 릴리스: **v1.3.1** · 기준 버전 v1.3
- 합법적으로 소유한 일본판 원본 ROM: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
- 메인TIP: `8CDC239822B82DB874EEFCCFD7AEBEEF67AE318B2CE32D1B1D69D6CB8C02A2C`
- WonderSwan checksum: `CA9E`
- xdelta: `CC456DACE99F2F25B7B2AEECD835F64F04AF12AA1E0E96E944F14B2C334A078F`
- xdelta 크기: `1,616,690` bytes
- 원본→xdelta→메인TIP round-trip: PASS
- runtime contract: 24,925건 / hard failure 0 / review item 0
- terminology audit: clean
- `tools/test_dialogue_runtime_contracts.py`: 6/6 PASS
- live SaveRAM: 승격 전후 byte-exact 보존

## 공개 파일

- [x] `README.md`
- [x] `PATCH_GUIDE.md`
- [x] `VERSION` (`1.3.1`)
- [x] `LICENSE` (MIT)
- [x] `NOTICE.md`
- [x] `data/README.md` 및 기존 활성 사양
- [x] `docs/LEGAL_NOTICE.md`
- [x] `docs/DEVELOPMENT.md`
- [x] `docs/REPOSITORY_POLICY.md`
- [x] `docs/RELEASE_SOURCE_SELECTION_v1.3.1.md`
- [x] `out/dist/monoeye_ko_expanded_v1.3.1.xdelta`
- [x] `out/dist/monoeye_ko_expanded_v1.3.1_xdelta.json`
- [x] `out/dist/monoeye_ko_expanded_v1.3.1_XDELTA_README.md`
- [x] `out/dist/SHA256SUMS_v1.3.1.txt`
- [x] `RELEASE_NOTES_v1.3.1.md`

## 공개 금지 확인

- [x] 원본 `.wsc`는 Git 공개 대상이 아니다.
- [x] 패치 완료 `.wsc`는 Git 공개 대상이 아니다.
- [x] `.sav` / savestate는 Git 공개 대상이 아니다.
- [x] emulator 실행 파일은 Git 공개 대상이 아니다.
- [x] candidate/probe/test ROM은 Git 공개 대상이 아니다.
- [x] `PATCH_PROGRESS.md`는 내부 로그로 유지한다.
- [x] v1.3.1의 `兄` 문맥 시트와 대규모 검수 시트는 공개 소스에서 제외한다.
- [x] v1.3.1 one-off candidate/promote 도구는 로컬 forensic 이력으로 분리한다.
- [x] v1.3.1에서는 `data/`를 추가 변경하지 않았으며, 기존 v1.3의 공개 활성 사양 정책을 그대로 유지한다.
- [x] v1.3.1 신규 공개 문서는 변경 요약과 기술 메타데이터 중심이며, 대규모 게임 원문 검수 시트는 `.gitignore`로 제외했다.

## v1.3.1 승격 검증

- [x] 사용자 실측 승인 후보: `stage3_reflow_idhelp_followup_candidate.wsc`
- [x] 후보 SHA-256: `8CDC239822B82DB874EEFCCFD7AEBEEF67AE318B2CE32D1B1D69D6CB8C02A2C`
- [x] 메인TIP을 후보와 byte-exact하게 승격했다.
- [x] rollback용 기존 v1.3 메인TIP과 live SaveRAM을 `out/patch/backup/`에 보존했다.
- [x] post-promotion terminology audit clean.
- [x] post-promotion dialogue runtime safety hard/review 0.
- [x] canonical `out/script/dialogue_runtime_contracts.json`을 v1.3.1 메인 기준으로 갱신했다.
- [x] 오래된 generated-file 의존 단위테스트를 self-contained 현재 contract 테스트로 정비하고 6/6 PASS를 확인했다.

## GitHub 업로드 전

- [x] `git status --short`에서 공개 대상 문서·검증 도구·v1.3.1 배포 파일만 남아 있음을 확인했다.
- [x] README와 적용 가이드가 패치 입력을 **합법적으로 소유한 일본판 원본 ROM**으로 명확히 안내한다.
- [x] README/PATCH_GUIDE의 입력·출력·xdelta SHA를 v1.3.1 정본으로 갱신했다.
- [x] GitHub Release asset은 `monoeye_ko_expanded_v1.3.1.xdelta`와 `SHA256SUMS_v1.3.1.txt` 두 파일만 사용한다.
- [x] `_xdelta.json`, `_XDELTA_README.md`, `RELEASE_NOTES_v1.3.1.md`는 저장소/Release 본문에 두고 asset으로 중복 첨부하지 않는다.
- [x] ROM이나 SaveRAM을 GitHub Release asset에 첨부하지 않는다.
- [x] 저장소 설명/README가 비공식·비제휴 프로젝트임을 명확히 표시한다.
- [x] `LICENSE`는 프로젝트가 권리를 가진 작성물에 적용되고 제3자 게임 콘텐츠는 `NOTICE.md`에서 분리한다.
- [x] 공개 라이선스가 제3자 게임 자산·상표·원문에까지 적용되는 것처럼 표현하지 않는다.
- [x] 권리자 안내 및 보증 부인은 `NOTICE.md`와 `docs/LEGAL_NOTICE.md`에 유지한다.

## Release asset

GitHub Release에는 다음 두 파일만 첨부합니다.

1. `out/dist/monoeye_ko_expanded_v1.3.1.xdelta`
2. `out/dist/SHA256SUMS_v1.3.1.txt`

Release 본문은 `RELEASE_NOTES_v1.3.1.md`를 사용합니다.
