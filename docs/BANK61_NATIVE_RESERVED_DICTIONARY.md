# bank61 시그 시나리오 native 2-byte 예약 사전

작성: 2026-08-08  
상태: **테스트 후보 — 사용자 런타임 확인 전 메인 승격 금지**

## 1. 배경

사용자 실화면에서 시그 시나리오가 다음처럼 깨졌다.

- `장난치지 마라！`
- `こ세라를 죽여놓고선、`
- 다음 창에 `み`만 표시
- 이후 대사가 나오지 않고 이벤트 종료

실제 원본/현재 ROM 주소는 `611DF0 -> 611DF8 -> 611E05 -> 611E13`으로 결속된다.

- `611DF8` 원본: `18 | F4 29 ...` — `18`은 둘째 줄 marker이며 본문은 native 2-byte 사전 토큰/글리프 문법이다.
- 현재 메인: `18 | E5 18 91 99 | 01...`
- `611E05` 원본: `F6 19 ...` — bare continuation 본문.
- 현재 메인: `E5 18 01 69 | 01...`

이 구간의 레코드 시작/종료/NUL 경계는 원본과 현재 메인이 동일하다. 따라서 단순 레코드 밀림 문제가 아니다.

## 2. 폐기된 bank61 source-bank shadow 방식

`bank61_shadow_dictionary_candidate.wsc`는 ROM1 현재 bank가 stock bank61(E1)일 때만
native token을 expansion bank26/27의 shadow 사전으로 보내는 방식이었다.

정적 감사는 통과했으나 사용자 실측에서:

- `こ`가 그대로 남음
- 이어서 `み`가 출력됨
- 이후 이벤트가 종료됨

따라서 **source-bank 판정이 이 이벤트의 실제 mixed continuation/load 경로에 대한 안전한 판별식이 아님**이 확인됐다.
이 후보와 그 런타임 설계는 메인 승격 금지/폐기 대상으로 남긴다.

## 3. 새 설계 — source-independent native reserved indices

새 후보는 신규 토큰 문법을 만들지 않는다. 게임의 원래 `F0–FE yy` 2-byte native dictionary
문법을 그대로 사용하고, 현재 Working ROM에서 완전히 도달 불가능한 인덱스 20개만 전역 예약한다.

예약 범위:

- `0D4C–0D52` — 7개
- `0DB3–0DB8` — 6개
- `0E81–0E87` — 7개
- 합계 **20개**

현재 메인 기준 각 예약 인덱스는 다음을 모두 만족한다.

- Working 외부 consumer = 0
- current native dictionary nested parent = 0
- current ext3 65,536-slot phrase 내부 nested reference = 0

Original-only 과거 consumer는 provenance로만 기록하며, 현재 실행 그래프에서는 도달하지 않는다.

## 4. 런타임

현행 native dictionary load 경로:

`7A:0700 -> near 7A:FFED -> far 7F:FC8C`

현행 `7F:FC8C` helper와 ext3 runtime은 그대로 둔다.

새 후보에서 `7A:FFED`만 다음 wrapper를 호출하도록 변경한다.

- 이전: `9A 8C FC 00 F0 C3`
- 후보: `9A 18 FF 00 F0 C3`

새 wrapper: `7F:FF18`, 53 bytes.

입력 `SI=index*2`가 세 예약 범위 중 하나면 expansion bank `26`을 map하고
`ES:[SI]`의 pointer를 반환한다. 예약 범위가 아니면 기존 `7F:FC8C` helper를 그대로 호출한다.

따라서:

- source bank61/E1 판정 없음
- ROM0/ROM1 중 어느 이벤트 로더가 원문 위치를 공급했는지에 의존하지 않음
- 기존 stock/bank10 native indices 4,076개의 의미 유지
- 기존 ext3 `E5 18` runtime byte-exact 유지

## 5. expansion bank26 형식

bank26 전체 64 KiB를 후보 전용 native phrase store로 사용한다.

- `0000–1FFF`: 4096-entry LE16 pointer table
- 예약되지 않은 entry: `FFFF`
- `2000+`: 20개 phrase pool
- 후보 phrase end: `22A9`

각 phrase는 부모 메인의 현재 ext3 phrase raw bytes를 **byte-exact 복사**한다. 번역문을 다시
인코딩/재번역하지 않는다.

## 6. 시그 이벤트 20개 일괄 전환

대상은 같은 연속 이벤트 구간 `611D7A–611F79` 안에서 원본/manifest prefix가 0 또는 1바이트이고
현재 body가 `E5 18`로 시작하는 20개 레코드다.

`611DF8`과 `611E05`를 포함해 20개 모두:

- prefix byte-exact 보존
- payload 길이 보존
- NUL terminator 주소 보존
- 다음 레코드 시작 주소 보존
- 현재 ext3 phrase raw 의미 보존
- body의 `E5 18 xx yy` 4B를 reserved native token 2B + `01 01` 추가 padding으로 교체

대표:

```text
611DF8
before: 18 E5 18 91 99 01 01 01 01 01 01 01
candidate: 18 FD 4F 01 01 01 01 01 01 01 01 01

611E05
before: E5 18 01 69 01 01 01 01 01
candidate: FD 50 01 01 01 01 01 01 01

611E20
before: 18 E5 18 01 6A 01 01 01 01 01 01
candidate: 18 FD 51 01 01 01 01 01 01 01 01
```

`611DF8`의 `18`은 삭제하지 않는다. 원본 이벤트 문법상 둘째 줄 marker이므로 그대로 유지한다.
그 뒤의 body만 정상 native token으로 복구한다.

## 7. 후보와 게이트

후보:

- `out/patch/sig_scenario_native20_candidate.wsc`
- SHA-256 `CCBF9CE92ACEFDFACF11ACECB7470DC14CA7D3560C5AA5E3190B64E08EC1C58C`
- WonderSwan checksum `3E94`
- whole-ROM diff: 50 runs / 847 bytes
- paired SaveRAM은 후보 생성 시 현재 live SaveRAM 복사

독립 감사:

- 20/20 target exact
- reserved index consumer 20/20 exact-one
- reserved native nested 0
- reserved ext3 nested 0
- local non-target record change 0
- local risk residual 0
- cannon 4/4 exact
- checksum exact
- false segmented-pointer 0

관련 파일:

- builder: `tools/build_sig_scenario_native20_candidate.py`
- audit: `tools/audit_sig_scenario_native20_candidate.py`
- report: `out/patch/sig_scenario_native20_report.json`
- audit report: `out/patch/sig_scenario_native20_audit.json`
- false-segptr: `out/patch/sig_scenario_native20_false_segptr.json`
- target manifest: `out/script/sig_scenario_native20_targets.json`

## 8. 런타임 테스트 게이트

동일 시그 장면에서 반드시 확인한다.

1. `장난치지 마라！` 정상
2. 다음 줄이 `こ` 없이 `세라를 죽여놓고선、`으로 표시
3. 다음 창이 단독 `み`가 아니라 원래 한글 continuation으로 정상 표시
4. 이벤트가 조기 종료되지 않고 `에？…… 죽였다고요？` 및 이후 대사까지 진행
5. 후속 같은 이벤트의 두 번째/세 번째 줄들도 일본어 1글자 누출 없이 정상

이 실측 전에는 메인 TIP으로 승격하지 않는다.
