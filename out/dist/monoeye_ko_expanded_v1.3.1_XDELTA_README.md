# monoeye_ko_expanded_v1.3.1 xdelta

- 릴리스: **v1.3.1 (release)**
- 기준 버전: **v1.3**

**합법적으로 소유한 일본판 원본 8 MiB WonderSwan ROM**에 적용하면 **16 MiB** 메인 TIP이 됩니다.

이 프로젝트는 확장 8 MiB를 **앞에 붙입니다**. IPS는 삽입이 없어
패치된 스톡 ROM 거의 전체를 패치 파일에 다시 넣게 되므로 배포용으로
쓰지 않습니다. xdelta3(VCDIFF)는 원본을 소스로 COPY하므로 원본 데이터가
패치에 들어가지 않습니다.

## 입력

- 원본: `SD Gundam G Generation Mono-Eye Gundams.wsc` · SHA-256 `376e4c6b4b81cc3a7dceb15dc4b7d0af04d3e6c8b81e8572569c39d3394870a0`
- 메인 TIP: `monoeye_ko_expanded.wsc` · SHA-256 `8cdc239822b82db874eeefccfd7aebeef67ae318b2ce32d1b1d69d6cb8c02a2c`

## 패치

- 파일: `monoeye_ko_expanded_v1.3.1.xdelta`
- xdelta SHA-256: `cc456dace99f2f25b7b2aeecd835f64f04af12aa1e0e96e944f14b2c334a078f`
- 크기: **1616690** bytes
- 원본 ROM 포함: **아니오** (`embeds_original_rom: false`)
- 8 MiB→16 MiB 라운드트립: **True**

## 적용

### GUI (Delta Patcher 등 xdelta3 프론트엔드)

1. 합법적으로 소유한 일본판 원본 8 MiB ROM 준비 및 백업
2. Original file = 합법적으로 소유한 일본판 원본 `.wsc`, XDelta patch = `monoeye_ko_expanded_v1.3.1.xdelta`, Output = 새 16 MiB `.wsc`
3. 결과 SHA-256이 `8cdc239822b82db874eeefccfd7aebeef67ae318b2ce32d1b1d69d6cb8c02a2c`인지 확인

xdelta **3.2 armor(BLAKE3)**, **VCDIFF secondary compression**,
**application header**를 모두 끄고 plain VCDIFF로 인코딩했습니다. xdeltaUI 및
구버전 xdelta3 프론트엔드 호환성을 우선한 배포 형식입니다.

### CLI

```bash
python tools/apply_main_tip_xdelta.py --original "SD Gundam G Generation Mono-Eye Gundams.wsc" --xdelta out/dist/monoeye_ko_expanded_v1.3.1.xdelta --out out/dist/monoeye_ko_from_xdelta.wsc
```

또는:

```bash
xdelta3 -d -f -s "SD Gundam G Generation Mono-Eye Gundams.wsc" out/dist/monoeye_ko_expanded_v1.3.1.xdelta out/dist/monoeye_ko_from_xdelta.wsc
```

