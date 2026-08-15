# 개발자 가이드

이 문서는 한글패치 개발/검증용 기준선과 배포 흐름을 정리합니다. 패치 적용만 필요한 사용자는 루트의 `README.md`와 `PATCH_GUIDE.md`를 보면 됩니다.

## 현재 정본

- 메인 TIP: `out/patch/monoeye_ko_expanded.wsc`
- 크기: 16,777,216 bytes
- SHA-256: `D7543AD4A62D9E7A9687583E85005DC4CA137E6FA62238EB70E58492248985C9`
- 일본판 원본 ROM: `SD Gundam G Generation Mono-Eye Gundams.wsc`
- 원본 크기: 8,388,608 bytes
- 원본 SHA-256: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`

현재 메인TIP에서 후보 ROM을 파생하고, 구조/포인터/렌더 감사와 실화면 검증 후에만 메인TIP으로 승격합니다.

## xdelta 배포 빌드

프로젝트에 포함된 pinned xdelta3 실행 파일을 사용하는 예:

```bash
python tools/make_main_tip_xdelta.py \
  --original "SD Gundam G Generation Mono-Eye Gundams.wsc" \
  --tip out/patch/monoeye_ko_expanded.wsc \
  --out-dir out/dist \
  --name monoeye_ko_expanded \
  --xdelta3 tools/vendor/xdelta3.exe
```

빌더는 생성 후 xdelta를 **합법적으로 소유한 일본판 원본 ROM**에 다시 적용하여 결과가 메인TIP과 byte-exact인지 확인합니다.

현재 배포 xdelta:

- `out/dist/monoeye_ko_expanded.xdelta`
- SHA-256: `AE70F4BEEE218BED3D571592076828DEC87DC76DABC3BEE68C54CF95231A39B6`
- 크기: 1,600,564 bytes
- VCDIFF secondary compression: disabled (xdeltaUI/구버전 xdelta3 호환)
- round-trip: PASS

## ROM 구조 요약

현재 패치는 원본 8 MiB ROM을 16 MiB로 확장합니다. 기존 스톡 뱅크 동작을 유지하기 위해 확장 영역을 앞쪽에 배치하는 구조를 사용합니다.

주요 영역과 상세 구조는 다음 문서를 참고합니다.

- `docs/ROM_16MB_EXPANSION.md`
- `docs/CAPACITY_UNLOCKED_STRATEGIES.md`
- `docs/DICT_INVASION_GUARD.md`

## 번역/데이터 정책

- 현재 메인TIP이 최우선 정본입니다.
- 과거 translation sheet나 번역 캐시는 재적용 입력이 아니라 forensic/reference 성격으로 취급합니다.
- 새 수정은 가능한 경우 `data/*_ko.json` 등 주소/문맥이 명시된 소스에 기록합니다.
- 구조 바이트, prefix, terminator, 포인터를 의미 없이 변경하지 않습니다.
- 1줄 20셀/2줄 40셀 제약과 실게임 렌더 결과를 함께 확인합니다.

관련 문서:

- `docs/TRANSLATION_SOURCE_POLICY.md`
- `docs/SAVERAM_POLICY.md`
- `PATCH_PROGRESS.md`

## 후보/테스트 파일 정책

후보 ROM, probe ROM, savestate, screenshot, 임시 감사 결과는 공개 배포 파일이 아닙니다. 작업이 끝난 산출물은 `legacy/` 아래 원래 상대 경로를 보존해 로컬 아카이브하고 Git에서는 제외합니다.

현재 정리 도구:

```bash
python tools/organize_current_tip_legacy_assets.py
```

기본은 dry-run입니다. 실제 이동 전 반드시 출력 목록에서 메인TIP, 최신 승격 보고서, 현재 빌드에 필요한 metadata가 제외되는지 확인합니다.

## GitHub 배포 최소 구성

공개 릴리스에 필요한 핵심 파일은 다음과 같습니다.

- `README.md`
- `PATCH_GUIDE.md`
- `out/dist/monoeye_ko_expanded.xdelta`
- `out/dist/monoeye_ko_expanded_xdelta.json`
- `out/dist/SHA256SUMS.txt`

개발 소스를 공개할 경우 `tools/`, `data/`, `docs/`를 추가로 포함하되 원본/패치 완료 ROM, SaveRAM, emulator binary, candidate/test ROM은 포함하지 않습니다.
