# GitHub 릴리스 체크리스트

## 패치 파일

- [x] `out/patch/monoeye_ko_expanded.wsc`의 SHA-256이 현재 기준과 일치한다.
- [x] `tools/make_main_tip_xdelta.py`로 xdelta를 다시 생성했다.
- [x] xdelta round-trip 결과가 메인TIP과 byte-exact다.
- [x] VCDIFF header indicator에 `VCD_SECONDARY`가 없고 xdeltaUI/구버전 호환 형식이다.
- [x] `out/dist/SHA256SUMS_v1.3.txt`의 해시를 갱신했다.

현재 기준:

- 릴리스: **v1.3** · 기준 버전 v1.2
- 합법적으로 소유한 일본판 원본 ROM: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
- 메인TIP: `26B780799C3C9CF0A554006C1B778025EC57A6F7C3B8FD7279D2CE654350FBC9`
- WonderSwan checksum: `AFFB`
- xdelta: `0471E51F7D1796D840F82B85278F54DAD4A97D833B260A05960CED39C5C15267`
- xdelta 크기: `1,615,611` bytes
- 2026-08-19 재검증: 원본→xdelta→메인TIP round-trip PASS, runtime contract 24,925건 hard/review 0, terminology audit clean

## 공개 파일

- [x] `README.md`
- [x] `PATCH_GUIDE.md`
- [x] `VERSION` (`1.3`)
- [x] `LICENSE` (MIT)
- [x] `NOTICE.md`
- [x] `data/README.md` 및 빌드에 필요한 `data/` 활성 사양
- [x] `docs/LEGAL_NOTICE.md`
- [x] `out/dist/monoeye_ko_expanded_v1.3.xdelta`
- [x] `out/dist/monoeye_ko_expanded_v1.3_xdelta.json`
- [x] `out/dist/monoeye_ko_expanded_v1.3_XDELTA_README.md`
- [x] `out/dist/SHA256SUMS_v1.3.txt`
- [x] `RELEASE_NOTES_v1.3.md`

## 공개 금지 확인

- [x] 원본 `.wsc`가 staging되지 않았다.
- [x] 패치 완료 `.wsc`가 staging되지 않았다.
- [x] `.sav` / savestate가 staging되지 않았다.
- [x] emulator 실행 파일이 staging되지 않았다.
- [x] candidate/probe/test ROM이 staging되지 않았다.
- [x] `legacy/`의 실제 바이너리가 staging되지 않았다.
- [x] 내부 전체 로그 `PATCH_PROGRESS.md`가 staging되지 않았다.
- [ ] `data/`에는 패치 재현에 필요한 활성 사양만 포함되고, 빌드와 무관한 독립 원문 덤프가 섞이지 않았다.
- [ ] 공개 문서에 게임 대사·도감 원문을 대량으로 재현한 구간이 없는지 확인했다.

## 레거시 정리

v1.2에서 대규모 후보/probe/test 산출물을 legacy로 정리한 상태를 유지합니다. v1.3에서 새로 생긴 preemptive/kana/move-icon/후속 대사 계열의 one-off candidate·probe·promote 도구는 최종 정본이 아니므로 `.gitignore`로 공개 소스 대상에서 제외하고 로컬 forensic 이력으로만 보존합니다. 공개 소스 선정 기준은 `docs/RELEASE_SOURCE_SELECTION_v1.3.md`를 따릅니다.

- 정리 완료: 98개 파일 / 1,375,663,628 bytes (1311.9 MiB)
- WSC/WSC_BAK: 30개
- 생성 JSON: 68개
- 보존 위치: `legacy/v1_2_test_artifacts_20260817/`
- `out/patch` 최상위 WSC는 현재 메인TIP 1개만 유지
- 활성 도구가 직접 참조하는 JSON은 보존
- 정리 전후 메인TIP SHA-256 동일 확인

## GitHub 업로드 전

- [x] 현재 staging 목록이 비어 있음을 확인했다. 실제 커밋 직전에는 `git status --short`를 다시 확인한다.
- [x] README와 적용 가이드가 패치 입력을 **합법적으로 소유한 일본판 원본 ROM**으로 명확히 안내한다.
- [x] README의 입력/출력 SHA가 `SHA256SUMS_v1.3.txt`와 일치한다.
- [x] GitHub Release asset은 `monoeye_ko_expanded_v1.3.xdelta`와 `SHA256SUMS_v1.3.txt` 두 파일만 사용한다.
- [x] `_xdelta.json`, `_XDELTA_README.md`, `RELEASE_NOTES_v1.3.md`는 저장소/Release 본문에 두고 asset으로 중복 첨부하지 않는다.
- [x] ROM이나 SaveRAM을 GitHub Release asset에 첨부하지 않는다.
- [ ] 저장소 설명/README가 비공식·비제휴 프로젝트임을 명확히 표시한다.
- [ ] `LICENSE`는 표준 MIT 본문이며 프로젝트가 권리를 가진 작성물에만 적용된다는 범위가 `NOTICE.md`에 명확하다.
- [ ] 공개 라이선스가 제3자 게임 자산·상표·원문에까지 적용되는 것처럼 표현하지 않는다.
- [ ] 권리자 요청 및 보증 부인은 `NOTICE.md`와 `docs/LEGAL_NOTICE.md`에 서로 일치하게 적혀 있다.
