# v1.4.0

`v1.4.0`은 `v1.3.2` 이후의 대사 문맥 교정과 **주요 한글 렌더링 폰트 교체**를 묶은 릴리스입니다. 기존 Galmuri7 8×8 원본을 세로 2배 확대하던 주 대사 글꼴을 Galmuri11Bitmap Condensed 기반의 네이티브 8×16 렌더로 교체하고, 원본 게임의 LUT/색상 규칙을 그대로 유지하도록 렌더 경로를 재구성했습니다. SaveRAM 형식은 변경하지 않았습니다.

## 주요 변경 사항

### 1. 주요 한글 폰트: Galmuri11Bitmap Condensed stemspace14

기존 대사 한글은 Galmuri7 8×8 글리프를 게임의 stock renderer가 세로 방향으로 2배 확대하는 구조였습니다. 화면 셀 자체가 16×16이라고 가정한 초기 POC는 글자 좌표와 색상 모두 어긋났고, 실제 렌더 구조를 다시 추적한 결과 주 대사 셀은 **8×16 / 64바이트 4bpp**임을 확인했습니다.

v1.4.0은 다음 방식을 사용합니다.

- 폰트: `Galmuri11Bitmap-Condensed-2.40.3.ttf`
- 실제 출력 셀: **8×16**
- 가로 리샘플링: 없음
- Condensed 원본 11행 메트릭을 유지하면서 출력 높이를 14행으로 보정
- 긴 가로획을 반복하지 않고 세로획 또는 내부 공백 행을 선택적으로 반복하는 `stemspace14` 방식
- 1,345개 한글 글리프 전수 충돌 검사: **0건**

이 방식은 `브`, `드`, `령` 등에서 특정 가로획만 과도하게 굵어지는 문제와 모음 꺾임에 임의 픽셀이 생기던 이전 POC 문제를 피하면서, 기존 Galmuri7보다 세부 형태를 더 많이 유지합니다.

### 2. 원본 LUT/색상 규칙 보존

초기 16×16 POC에서 글자색이 자홍색·갈색 등으로 바뀌는 문제가 있었기 때문에, v1.4.0은 게임의 원본 `7A:027C` 변환과 LUT를 직접 분석해 동일한 행 변환 규칙을 사용합니다.

- 원본 LUT 3모드 보존
- 기본 / `0x0100` / `0x0200` 스타일별 8×16 글리프 변형을 미리 생성
- 8×8 원본 글리프를 기존 방식으로 확대했을 때 stock renderer 결과와 3모드 × 128개, 총 **384건 byte-exact** 동치 확인
- 실화면 비교에서 후보 전용 색상 0개 확인

따라서 폰트 형태만 바뀌며 기존 텍스트의 전경색·배경색·강조색 정책은 유지합니다.

### 3. 과거 특수 UI 글리프 `공/분/근전/사전` 폰트 동기화

과거 짧은 고정폭 UI를 번역하면서 일반 한글 marker 경로에 넣을 수 없었던 글자들은 별도 compact 8×8 글리프를 도난/복사하는 방식으로 구현돼 있었습니다.

- `C6` → `공`
- `DF` → `분`
- `E511` 계열 compact glyph → `근`
- `E51B` 계열 compact glyph → `전`
- `E51C` 계열 compact glyph → `사`

이 때문에 일반 대사는 새 stemspace14 글꼴인데 `공`, `분`, `근전`, `사전`만 기존 Galmuri7처럼 보이는 문제가 생겼습니다.

v1.4.0은 해당 UI 레코드와 사전 payload를 바꾸지 않고, 렌더 시 위 5개 특수 glyph index만 일반 한글의 stemspace14 슬롯으로 매핑합니다. 따라서 기존 UI 구조/폭/색상은 유지하면서 글꼴만 다른 한글과 통일됩니다.

사용자 실측으로 `공`, `분`, `근전`, `사전` 모두 정상 표시를 확인했습니다.

### 4. Stage 5 `빛나는 우주` 라라아–아무로 문맥 교정

v1.3.2 이후 Stage 5의 라라아–아무로 뉴타입 교감 장면을 화자 어조에 맞춰 좁게 재번역했습니다.

- `당신도 잘 싸우잖아요……！！` → `당신은 이렇게나 잘 싸우잖아……！！`
- `그런데 왜！？`는 사용자 확인에 따라 기존 간결한 번역을 유지
- `『믿어……` → `『믿지……`
- `이렇게 자네와도 서로 이해했으니까』` → `너와도 이렇게 서로 이해했으니까』`

라라아의 주변 반말 문맥과 아무로의 젊고 친밀한 말투를 맞추되 관련 없는 Stage 5 대사는 변경하지 않았습니다.

## 검증

v1.4.0 메인 TIP에서 다음을 확인했습니다.

- 메인 TIP SHA-256: `D1806D8E3D14B1B31246CAF745D6068022A7EE80492BF8D2485FA6458882E7FB`
- WonderSwan checksum: `27A1`
- dialogue runtime contracts: **24,954**
- active checked: **7,395**
- quarantine checked: **17,559**
- hard failure: **0**
- review item: **0**
- 20셀 감사: **16,629행 / 초과 0 / unreadable 0**
- 확정 사용자 가시 미번역 residual: **0**
- terminology audit: **clean**
- runtime safety + runtime contract unit tests: **11 / 11 PASS**
- 도감 이름 감사: auto-unify candidate **0**, review-required **11**
- SaveRAM: 승격 전후 byte-exact

## 배포 파일

- `monoeye_ko_expanded_v1.4.0.xdelta`
- xdelta SHA-256: `0A3F4784AB39549031F0D2D7718C116735688BD9A5A57BE10EC0F0FAE6A7853D`
- xdelta 크기: **930,239 bytes**
- VCDIFF secondary compression: disabled
- VCDIFF application header: disabled
- 원본 8 MiB ROM → xdelta → v1.4.0 메인TIP round-trip: **byte-exact PASS**

지원 원본 SHA-256:

`376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`

## 세이브 호환성

v1.4.0은 ROM 렌더링/텍스트 데이터 수정이며 SaveRAM 형식 변경은 없습니다. 기존 세이브를 그대로 사용할 수 있습니다. 업데이트 전에는 원본 ROM과 세이브 파일을 별도로 백업하는 것을 권장합니다.

## Galmuri 폰트 라이선스

빌드에 사용한 Galmuri 폰트는 Lee Minseo가 제작한 폰트 소프트웨어이며 **SIL Open Font License 1.1** 적용 대상입니다. 현재 프로젝트 정책상 폰트 바이너리는 공개 Git/Release asset에 포함하지 않으며, 로컬 작업 트리의 `assets/fonts/galmuri_tmp/LICENSE.txt`에서 사용한 폰트의 라이선스를 확인했습니다. 프로젝트 루트의 MIT License는 Galmuri 폰트 파일에 적용되지 않습니다.
