# GitHub 릴리스 체크리스트

## 패치 파일

- [ ] `out/patch/monoeye_ko_expanded.wsc`의 SHA-256이 현재 기준과 일치한다.
- [ ] `tools/make_main_tip_xdelta.py`로 xdelta를 다시 생성했다.
- [ ] xdelta round-trip 결과가 메인TIP과 byte-exact다.
- [ ] VCDIFF header indicator에 `VCD_SECONDARY`가 없고 xdeltaUI/구버전 호환 형식이다.
- [ ] `out/dist/SHA256SUMS.txt`의 해시를 갱신했다.

현재 기준:

- 합법적으로 소유한 일본판 원본 ROM: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
- 메인TIP: `D7543AD4A62D9E7A9687583E85005DC4CA137E6FA62238EB70E58492248985C9`
- xdelta: `AE70F4BEEE218BED3D571592076828DEC87DC76DABC3BEE68C54CF95231A39B6`

## 공개 파일

- [ ] `README.md`
- [ ] `PATCH_GUIDE.md`
- [ ] `LICENSE` (MIT)
- [ ] `NOTICE.md`
- [ ] `data/README.md` 및 빌드에 필요한 `data/` 활성 사양
- [ ] `docs/LEGAL_NOTICE.md`
- [ ] `out/dist/monoeye_ko_expanded.xdelta`
- [ ] `out/dist/monoeye_ko_expanded_xdelta.json`
- [ ] `out/dist/SHA256SUMS.txt`

## 공개 금지 확인

- [ ] 원본 `.wsc`가 staging되지 않았다.
- [ ] 패치 완료 `.wsc`가 staging되지 않았다.
- [ ] `.sav` / savestate가 staging되지 않았다.
- [ ] emulator 실행 파일이 staging되지 않았다.
- [ ] candidate/probe/test ROM이 staging되지 않았다.
- [ ] `legacy/`의 실제 바이너리가 staging되지 않았다.
- [ ] 내부 전체 로그 `PATCH_PROGRESS.md`가 staging되지 않았다.
- [ ] `data/`에는 패치 재현에 필요한 활성 사양만 포함되고, 빌드와 무관한 독립 원문 덤프가 섞이지 않았다.
- [ ] 공개 문서에 게임 대사·도감 원문을 대량으로 재현한 구간이 없는지 확인했다.

## 레거시 정리

`python tools/organize_current_tip_legacy_assets.py`로 먼저 dry-run을 확인한다.

현재 2026-08-15 배포 준비 기준 dry-run은 476개 경로, 약 2.15 GiB를 이동 대상으로 분류한다. 실제 이동 시에는 메인TIP SHA와 live SaveRAM이 이동 전후 동일해야 한다.

## GitHub 업로드 전

- [ ] 첫 커밋 전에 `git status --short`로 전체 staging 목록을 직접 확인한다.
- [ ] README와 적용 가이드가 패치 입력을 **합법적으로 소유한 일본판 원본 ROM**으로 명확히 안내한다.
- [ ] README의 입력/출력 SHA가 `SHA256SUMS.txt`와 일치한다.
- [ ] 릴리스 첨부 파일은 xdelta를 우선 사용한다.
- [ ] ROM이나 SaveRAM을 GitHub Release asset에 첨부하지 않는다.
- [ ] 저장소 설명/README가 비공식·비제휴 프로젝트임을 명확히 표시한다.
- [ ] `LICENSE`는 표준 MIT 본문이며 프로젝트가 권리를 가진 작성물에만 적용된다는 범위가 `NOTICE.md`에 명확하다.
- [ ] 공개 라이선스가 제3자 게임 자산·상표·원문에까지 적용되는 것처럼 표현하지 않는다.
- [ ] 권리자 요청 및 보증 부인은 `NOTICE.md`와 `docs/LEGAL_NOTICE.md`에 서로 일치하게 적혀 있다.
