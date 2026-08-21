# 한글패치 적용 가이드

이 문서는 **v1.4.0** `monoeye_ko_expanded_v1.4.0.xdelta`를 원본 WonderSwan Color ROM에 적용하는 방법을 설명합니다.

## 1. 준비물

- **합법적으로 소유한 일본판 원본 ROM**: `SD Gundam G Generation Mono-Eye Gundams.wsc`
- 패치 파일: `out/dist/monoeye_ko_expanded_v1.4.0.xdelta`
- xdelta 패치를 적용할 프로그램
  - GUI: Delta Patcher 등 xdelta3 호환 프론트엔드
  - CLI: xdelta3

패치 파일에는 원본 ROM이 포함되어 있지 않습니다.

## 2. 원본 ROM 확인

지원하는 원본은 **8 MiB (8,388,608 bytes)** 입니다.

SHA-256:

`376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`

Windows PowerShell에서는 다음처럼 확인할 수 있습니다.

```powershell
Get-FileHash ".\SD Gundam G Generation Mono-Eye Gundams.wsc" -Algorithm SHA256
```

값이 다르면 다른 덤프/수정본일 가능성이 있으므로 그대로 패치하지 않는 것을 권장합니다.

## 3. GUI로 적용

Delta Patcher/xdeltaUI 계열 프로그램에서는 보통 다음과 같이 지정합니다. 현재 배포 xdelta는 구버전 호환을 위해 VCDIFF secondary compression(LZMA)을 사용하지 않습니다.

- **Original file / Source**: **합법적으로 소유한 일본판 원본 `.wsc`**
- **XDelta patch**: `monoeye_ko_expanded_v1.4.0.xdelta`
- **Output file**: 새 파일 이름의 `.wsc`

원본 파일 자체를 덮어쓰기보다 새 출력 파일을 만드는 것을 권장합니다.

정상 적용 후 결과 ROM은 다음 조건을 만족해야 합니다.

- 크기: **16 MiB (16,777,216 bytes)**
- SHA-256: `D1806D8E3D14B1B31246CAF745D6068022A7EE80492BF8D2485FA6458882E7FB`

## 4. CLI로 적용

xdelta3를 직접 사용하는 경우:

```bash
xdelta3 -d -f -s "SD Gundam G Generation Mono-Eye Gundams.wsc" \
  "monoeye_ko_expanded_v1.4.0.xdelta" \
  "monoeye_ko_expanded.wsc"
```

프로젝트 소스 전체를 받은 개발 환경에서는 다음 도구도 사용할 수 있습니다.

```bash
python tools/apply_main_tip_xdelta.py \
  --original "SD Gundam G Generation Mono-Eye Gundams.wsc" \
  --xdelta out/dist/monoeye_ko_expanded_v1.4.0.xdelta \
  --out monoeye_ko_expanded.wsc
```

## 5. 패치 결과 확인

PowerShell:

```powershell
Get-FileHash ".\monoeye_ko_expanded.wsc" -Algorithm SHA256
```

정상 결과:

`D1806D8E3D14B1B31246CAF745D6068022A7EE80492BF8D2485FA6458882E7FB`

xdelta 파일 자체의 SHA-256:

`0A3F4784AB39549031F0D2D7718C116735688BD9A5A57BE10EC0F0FAE6A7853D`

## 6. 에뮬레이터에서 실행

프로젝트 실측은 주로 BizHawk 2.11.1의 WonderSwan 계열 코어에서 진행했습니다.

패치 결과 ROM이 16 MiB이므로, 확장된 WonderSwan ROM을 정상적으로 읽을 수 있는 에뮬레이터를 사용해 주세요.

기존 세이브 파일을 사용할 경우에는 먼저 백업해 두는 것을 권장합니다. ROM 패치 작업 자체는 세이브 파일을 변경하지 않습니다.

## 자주 발생하는 문제

### xdelta가 원본을 거부합니다

원본 ROM의 해시가 지원 값과 같은지 확인하세요. 이미 패치된 ROM이나 다른 버전의 ROM에 다시 적용하면 실패할 수 있습니다.

### 패치는 됐는데 실행되지 않습니다

결과 ROM의 크기가 16,777,216 bytes인지, SHA-256이 정상 출력값과 일치하는지 먼저 확인하세요. 해시가 다르면 패치 과정에서 다른 파일을 사용했을 가능성이 큽니다.

### 화면이나 대사가 이상합니다

패치된 ROM의 SHA-256이 위 정상 값과 같은데도 문제가 발생한다면 버그일 수 있습니다. 장면 캡처, 에뮬레이터 버전, 재현 절차를 함께 제보해 주세요.

### 세이브가 다른 에뮬레이터에서 인식되지 않습니다

에뮬레이터에 따라 SaveRAM 파일의 헤더나 저장 형식이 다를 수 있습니다. ROM 패치 문제와 세이브 형식 문제는 별개이므로 원본 세이브를 보존한 상태에서 변환 여부를 확인해 주세요.

## 배포 및 권리 원칙

공개 배포에는 `.xdelta` 패치와 공개 검토를 마친 문서/검증 정보만 포함합니다. 원본 ROM, 패치 완료 ROM, 세이브 파일, 에뮬레이터 바이너리, 게임 원문을 대량 추출한 번역 데이터셋은 포함하지 않습니다.

이 프로젝트는 비공식·비상업 팬 프로젝트이며 게임 제작사·유통사·플랫폼 권리자와 제휴·후원·승인 관계가 있음을 주장하지 않습니다. 게임과 관련 명칭·상표·원저작물에 관한 권리는 각 권리자에게 있습니다. xdelta 형식의 차이 패치를 사용한다는 사실만으로 모든 저작권 문제가 자동으로 해소되는 것은 아니며, 사용자는 **합법적으로 소유한 일본판 원본 ROM**에만 패치를 적용하고 원본 ROM이나 패치 완료 ROM을 재배포하지 않아야 합니다.

자세한 고지와 권리자 요청 절차는 [`docs/LEGAL_NOTICE.md`](docs/LEGAL_NOTICE.md)를 참고하세요.
