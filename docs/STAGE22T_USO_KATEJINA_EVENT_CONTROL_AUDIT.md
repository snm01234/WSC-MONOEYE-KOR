# STAGE22t 웃소–카테지나 Event Error `12288 / 36067` 제어 바이트 감사

작성일: 2026-08-17  
대상 메인 TIP SHA-256: `F68B3261BEECC32047D17952E36BC2B891CD5D66410F9FC9293487571A0FC8E2`  
원본 ROM SHA-256: `376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`

## 결론

**원본 대비 메인TIP에서 해당 웃소–카테지나 이벤트의 제어 바이트가 변경된 흔적은 발견되지 않았다.**

- Event Error `12288 / 36067` = `0x3000 : 0x8CE3`
- 기존 프로젝트의 Event Error 해석 관례대로 low offset `0x8CE3`을 대응시키면, 이 장면의 `63:8CE3` 제어행과 정확히 일치한다.
- `63:8CE3`부터의 제어열은 원본과 메인TIP이 byte-exact이다.
- 웃소–카테지나 연속 구간 `63:8C6B–63:8FE2`의 41개 대사 본문을 제외한 모든 prefix / NUL terminator / 이벤트 제어 바이트를 전수 비교한 결과 차이는 **0바이트**였다.
- 41개 대사의 terminator 위치도 전부 원본과 동일하다.
- 이벤트/데이터 뱅크 `64–69` 전수 비교에서도 실행 이벤트 바디의 비허용 차이는 **0건**이다. `68`, `69`는 원본과 완전 동일하고, `64–67`의 차이는 기존에 분류된 고정 이벤트명/라벨 18개 범위뿐이다.

따라서 이번 오류는 **제어 opcode 자체가 번역 패치로 변조된 문제라기보다, 직전 번역 대사의 저장 문법이 이벤트 인터프리터 상태에 영향을 주는 문제**로 보는 것이 가장 타당하다.

## 오류 위치 `63:8CE3`

원본과 메인TIP 모두 다음과 같다.

```text
63:8CE3  17 28 08 1E 00 17 1C 08 1D 00 17 34 18
```

즉 보고된 low offset과 겹치는 `17 28 ...` 제어행 자체에는 단 1바이트의 변경도 없다.

## 가장 강한 정적 원인 후보 — `63:8CD5`

오류 제어열 바로 앞의 대사는 `……え？` → `……어？` 레코드다.

원본:

```text
63:8CD5  17 34 18 F1 91 2B 1D 00 00
```

현재 메인TIP:

```text
63:8CD5  17 34 18 E5 18 52 F1 00 00
```

구조적으로 보면:

- prefix `17 34 18`: 원본과 동일
- terminator 위치 `63:8CDC`: 원본과 동일
- 원본 body: `F1 91 2B 1D` — native stock 문법
- 현재 body: `E5 18 52 F1` — direct ext3 portal
- 그 직후 `08`/`17` 이벤트 제어열은 원본과 동일
- 해당 제어열 안에 보고된 `63:8CE3 = 17 28`이 존재

이 프로젝트에서는 과거에도 **exact-fit `E5 18` scenario record 직후의 `17 xx` 제어행에서 parser state가 어긋나 글리프 노출·이벤트 진행 이상이 발생**한 실측 사례가 여러 번 있었다. 따라서 `63:8CD5`는 이번 오류의 가장 강한 정적 원인 후보이다.

단, 이것은 아직 **정적 진단**이며 `63:8CD5`를 native 문법으로 복구한 후보 ROM을 실제로 실행해 Event Error가 사라지는지 확인하기 전까지 런타임 원인 확정으로 취급하지 않는다.

## 이벤트/데이터 뱅크 `64–69` 전수 비교

| bank | diff runs | diff bytes | 판정 |
|---|---:|---:|---|
| `64` | 5 | 10 | 기존 허용 고정명 token만 존재 |
| `65` | 1 | 2 | 기존 허용 fixed label token만 존재 |
| `66` | 5 | 10 | 기존 허용 고정명 token만 존재 |
| `67` | 7 | 26 | 기존 허용 고정명/게임오버 문자열만 존재 |
| `68` | 0 | 0 | 원본 byte-exact |
| `69` | 0 | 0 | 원본 byte-exact |

`64–67`의 잔여 18개 범위는 기존 `build_event_bank_false_replacement_cleanup_candidate.py`에서 fail-closed allowlist로 분류된 이벤트명/고정 라벨 영역과 정확히 동일하다. 이번 stage22t 오류와 연결되는 새로운 실행 바디 변경은 발견되지 않았다.

## 재현 가능한 감사 도구

- `tools/audit_stage22t_uso_katejina_event_control.py`
- 결과: `out/patch/stage22t_uso_katejina_event_control_audit.json`

최종 정적 게이트:

- event bank unknown diff: `0`
- conversation dialogue rows: `41`
- conversation non-dialogue diff: `0`
- terminator failure: `0`
- `63:8CE3` byte-exact: `true`
- 가장 강한 원인 후보: `63:8CD5`

## 2-byte 확장 portal 후보

기존 `F0–FF` native dictionary ID는 2바이트 인코딩상 총 4096개가 하드캡이므로, expansion bank의 물리 여유만으로 신규 native ID를 추가할 수는 없다. 기존 ID 회수를 피하기 위해 이번 테스트에서는 **현재 typed text에서 미사용인 `E5 1B` 2바이트 unit을 전용 portal로 예약**했다.

- 기존 dictionary ID 회수: **0개**
- `E5 1B` typed script/name75/aux consumer: **0건**
- expansion 8MiB 내 기존 raw `E5 1B`: **0건**
- expansion bank `26`: 후보 생성 전 전체 64KiB `FF`
- helper: `26:2000 = E7 86 1D 00` = `어？`
- `26:2000–20FF`: 256바이트를 같은 계열용 논리 예약 영역으로 두고 미사용 바이트는 `FF` 유지

대상은 다음처럼 바뀐다.

```text
Main      63:8CD5  17 34 18 | E5 18 52 F1 | 00
Candidate 63:8CD5  17 34 18 | F1 91 E5 1B | 00
```

`F191`은 기존 native dictionary token `……`이고, `E51B`는 `어？` helper를 호출한다. `E51B`는 ext3 leaf로 보내지 않고 walker에서 `DX=F000`으로 바꾼 뒤 **기존 stock/native dictionary leaf와 phrase loop를 그대로 사용**한다. special flag는 native dictionary helper 진입까지만 사용하고 즉시 지운다. 기존 `E518` walker body, ext3 leaf, bank10 dictionary helper는 byte-exact 유지한다.

후보:

- `out/patch/stage22t_uso_katejina_event8ce3_native2_portal_candidate.wsc`
- SHA-256 `94D13DB7C821001C6EA0B1C026801FA28CB827450A5FE0602C37C0FA17FAFAE7`
- checksum `987C`
- paired SaveRAM: `sram/stage22t_uso_katejina_event8ce3_native2_portal_candidate.sav`
- builder: `tools/build_stage22t_uso_katejina_event8ce3_native2_portal_candidate.py`
- report: `out/patch/stage22t_uso_katejina_event8ce3_native2_portal_report.json`

정적 검증:

1. `17 34 18` prefix byte-exact 유지
2. 총 레코드 extent와 `63:8CDC` terminator 위치 유지
3. `63:8CE3` 제어열 byte-exact 유지
4. 기존 dictionary ID 재사용/회수 0
5. unexpected whole-ROM diff 0
6. battle exact audit failure 0
7. terminology audit clean
8. main TIP / live SaveRAM 미변경

## 실측 게이트

이 후보는 메인 승격 금지 상태다. 다음을 실측한다.

1. `……어？`가 `E51B` 글리프나 깨진 문자 없이 정상 한글로 출력되는지
2. Event Error `12288 / 36067` (`3000:8CE3`)이 사라지는지
3. 다음 카테지나 대사와 이벤트가 정상 진행되는지
4. 제어문 글리프 노출이나 replay/loop가 생기지 않는지

이 실측을 통과한 경우에만 신규 2-byte expansion portal 방식을 유효한 해결책으로 인정하고 메인 반영을 검토한다.
