# v1.4.0 릴리스 체크리스트

## 1. 메인 TIP / 승격

- [x] `dialogue_galmuri11bitmap_condensed_stemspace14_legacy_ui_sync_candidate.wsc`를 사용자 실측 승인했다.
- [x] `out/patch/monoeye_ko_expanded.wsc`를 승인 후보와 byte-exact로 승격했다.
- [x] 승격 후 canonical `out/script/dialogue_runtime_contracts.json`을 새 메인 TIP에 맞춰 갱신했다.
- [x] live SaveRAM은 승격 전후 byte-exact다.
- [x] 실패 시 ROM/SaveRAM/canonical manifest를 복구하는 rollback backup을 생성했다.

현재 정본:

- 릴리스: **v1.4.0** · 기준 버전 **v1.3.2**
- 메인 TIP SHA-256: `D1806D8E3D14B1B31246CAF745D6068022A7EE80492BF8D2485FA6458882E7FB`
- WonderSwan checksum: `27A1`
- live SaveRAM SHA-256: `B4E71F1EED3ABBBC4CEAAAEDBFBF4D6ED05E74DDF44F06D90DFFA5A1A41B2F11`

## 2. v1.3.2 이후 승인 변경

- [x] Stage 5 `빛나는 우주` 라라아–아무로 어조 교정
  - `당신은 이렇게나 잘 싸우잖아……！！`
  - `그런데 왜！？` 유지
  - `『믿지……`
  - `너와도 이렇게 서로 이해했으니까』`
- [x] 주요 한글 폰트를 Galmuri11Bitmap Condensed 기반 8×16 stemspace14로 교체
- [x] stock `7A:027C` LUT 3모드 색상 규칙 보존
- [x] 1,345개 한글 글리프 충돌 0
- [x] legacy compact UI `공/분/근/전/사`를 동일 stemspace14 렌더 경로로 동기화
- [x] 사용자 실측으로 `공`, `분`, `근전`, `사전` 정상 확인

## 3. 런타임 / 번역 검증

- [x] dialogue runtime contracts: 24,954
- [x] active checked: 7,395
- [x] quarantine checked: 17,559
- [x] hard failures: 0
- [x] review items: 0
- [x] 20셀 감사: 16,629 / over 20 = 0 / unreadable = 0
- [x] 사용자 가시 untranslated residual = 0
- [x] terminology audit = clean
- [x] runtime safety + runtime contract unit tests = 11/11 PASS
- [x] 도감 이름 audit: auto-unify candidates = 0 / review-required = 11

릴리스 검증 산출물:

- `out/patch/v1_4_0_release_runtime_gate.json`
- `out/patch/v1_4_0_release_contracts.json`
- `out/patch/v1_4_0_release_20cell.json`
- `out/patch/v1_4_0_release_untranslated.json`
- `out/patch/v1_4_0_release_terminology.json`
- `out/patch/v1_4_0_release_encyclopedia_names.json`
- `out/patch/v1_4_0_release_validation_summary.json`

## 4. xdelta

- [x] `tools/make_main_tip_xdelta.py`를 v1.4.0 / base v1.3.2로 갱신했다.
- [x] 합법적으로 소유한 일본판 원본 8 MiB ROM 기준 xdelta를 생성했다.
- [x] VCDIFF header indicator = `0x00`
- [x] secondary compression = disabled
- [x] application header = disabled
- [x] 원본 → xdelta → 메인 TIP round-trip = byte-exact PASS

정본 값:

- 원본 SHA-256: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
- xdelta: `out/dist/monoeye_ko_expanded_v1.4.0.xdelta`
- xdelta SHA-256: `0A3F4784AB39549031F0D2D7718C116735688BD9A5A57BE10EC0F0FAE6A7853D`
- xdelta 크기: `930,239` bytes
- 결과 ROM SHA-256: `D1806D8E3D14B1B31246CAF745D6068022A7EE80492BF8D2485FA6458882E7FB`

## 5. 문서 / 공개 파일

- [x] `VERSION` = `1.4.0`
- [x] `README.md`
- [x] `PATCH_GUIDE.md`
- [x] `RELEASE_NOTES_v1.4.0.md`
- [x] `docs/DEVELOPMENT.md`
- [x] `docs/REPOSITORY_POLICY.md`
- [x] `docs/RELEASE_SOURCE_SELECTION_v1.4.0.md`
- [x] `NOTICE.md`에 Galmuri OFL 1.1 고지 추가
- [x] `.gitignore`에 v1.4.0 `out/dist` 공개 파일 allowlist 추가
- [x] `out/dist/SHA256SUMS_v1.4.0.txt`

## 6. GitHub Release asset

Release asset은 아래 2개만 권장합니다.

1. `out/dist/monoeye_ko_expanded_v1.4.0.xdelta`
2. `out/dist/SHA256SUMS_v1.4.0.txt`

Release 본문은 `RELEASE_NOTES_v1.4.0.md`를 사용합니다. `_xdelta.json`과 `_XDELTA_README.md`는 저장소의 재현/검증 자료로 유지하며 asset으로 중복 첨부하지 않습니다.

## 7. 배포 금지 재확인

- [x] 일본판 원본 ROM을 배포하지 않는다.
- [x] 패치 완료 ROM을 배포하지 않는다.
- [x] SaveRAM/savestate를 배포하지 않는다.
- [x] candidate/probe/test ROM을 Release asset으로 올리지 않는다.
- [x] `PATCH_PROGRESS.md`와 대규모 forensic report는 공개 릴리스 정본에 포함하지 않는다.
