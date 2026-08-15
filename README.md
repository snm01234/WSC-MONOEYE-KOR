# SD Gundam G Generation: Mono-Eye Gundams 한글패치

WonderSwan Color용 **SD Gundam G Generation: Mono-Eye Gundams** 비공식 한국어 패치 프로젝트입니다.

이 저장소는 원본 게임 ROM을 포함하지 않습니다. 배포 파일은 사용자가 보유한 정상 원본 ROM에 적용하는 **xdelta 패치**입니다.

## 가장 빠른 적용 방법

1. 정상 원본 `.wsc` ROM을 준비합니다.
2. 원본 ROM의 SHA-256이 아래 값과 같은지 확인합니다.
   - `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`
3. `out/dist/monoeye_ko_expanded.xdelta`를 받습니다.
4. Delta Patcher 같은 xdelta 호환 프로그램에서 원본 ROM에 패치를 적용합니다.
5. 결과 ROM은 **16 MiB (16,777,216 bytes)** 가 되어야 합니다.
6. 패치된 ROM의 SHA-256이 아래 값이면 정상입니다.
   - `D7543AD4A62D9E7A9687583E85005DC4CA137E6FA62238EB70E58492248985C9`

처음 적용하거나 오류가 발생한다면 [`PATCH_GUIDE.md`](PATCH_GUIDE.md)를 확인해 주세요.

## 현재 배포 파일

- `out/dist/monoeye_ko_expanded.xdelta` — 실제 배포용 패치
- `out/dist/monoeye_ko_expanded_xdelta.json` — 원본/출력/xdelta 해시와 빌드 정보
- `out/dist/monoeye_ko_expanded_XDELTA_README.md` — 자동 생성된 xdelta 기술 정보
- `out/dist/SHA256SUMS.txt` — 확인용 SHA-256 목록

현재 xdelta 자체의 SHA-256:

`AE70F4BEEE218BED3D571592076828DEC87DC76DABC3BEE68C54CF95231A39B6`

xdelta 생성 후 원본 ROM에 다시 적용하여 **현재 메인 TIP과 byte-exact로 동일한 결과가 나오는 것까지 검증**합니다. 현재 배포 xdelta는 xdeltaUI/구버전 xdelta3 호환을 위해 VCDIFF secondary compression(LZMA)을 사용하지 않습니다.

## 주의사항

- 이미 패치된 ROM에 다시 적용하지 마세요.
- 원본 ROM의 용량이나 해시가 다르면 정상 적용을 보장하지 않습니다.
- 원본 ROM과 세이브 파일은 반드시 별도로 백업해 두세요.
- 이 패치는 ROM을 8 MiB에서 16 MiB로 확장합니다.
- 프로젝트 실측은 주로 BizHawk 2.11.1의 WonderSwan 계열 코어에서 진행했습니다.

## 문제 제보 시 필요한 정보

오류를 발견하면 가능하면 다음 정보를 함께 남겨 주세요.

- 문제가 발생한 장면 또는 스테이지
- 화면 캡처
- 보이는 대사/메뉴 문구
- 사용한 에뮬레이터와 버전
- 재현 절차
- 패치된 ROM의 SHA-256

같은 문구라도 도감, 시나리오, 전투대사, UI가 서로 다른 데이터 경로를 사용할 수 있어 화면 캡처가 있으면 원인 추적에 큰 도움이 됩니다.

## 개발자용 문서

패치 사용자가 아니라 개발/검증에 참여하려면 다음 문서를 참고하세요.

- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — 현재 메인TIP 및 빌드/검증 흐름
- [`docs/REPOSITORY_POLICY.md`](docs/REPOSITORY_POLICY.md) — GitHub에 포함할 파일과 제외할 파일 정책
- [`PATCH_PROGRESS.md`](PATCH_PROGRESS.md) — 누적 수정 및 원인 분석 기록
- [`docs/TRANSLATION_SOURCE_POLICY.md`](docs/TRANSLATION_SOURCE_POLICY.md) — 번역 소스 관리 정책
- [`docs/SAVERAM_POLICY.md`](docs/SAVERAM_POLICY.md) — SaveRAM 취급 정책

## 저작권 안내

이 프로젝트는 팬 번역/패치 프로젝트이며 원본 게임 ROM을 배포하지 않습니다. 게임 및 관련 저작권은 각 권리자에게 있습니다. 패치는 정당하게 보유한 원본 게임 데이터에 적용하는 용도로만 사용해 주세요.
"# WSC-MONOEYE-KOR" 
