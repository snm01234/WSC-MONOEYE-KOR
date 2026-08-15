# bank61 전용 2바이트 Shadow Dictionary

> **폐기됨 — 2026-08-08 런타임 실측 실패.** 사용자가 `bank61_shadow_dictionary_candidate.wsc`에서 동일 시그 장면을 재현한 결과 `こ`가 남고, 이어서 `み`가 출력된 뒤 이벤트가 조기 종료됐다. source ROM1 bank=E1 판정에 의존한 이 방식은 메인 승격 금지다. 후속 정본은 [`BANK61_NATIVE_RESERVED_DICTIONARY.md`](BANK61_NATIVE_RESERVED_DICTIONARY.md)의 source-independent native reserved 방식이다.

작성: 2026-08-08  
상태: **폐기 / 실측 실패 / 메인 승격 금지**

## 1. 목적

bank61 시나리오에는 원본부터 `prefix 0바이트` 또는 `prefix 1바이트`인 continuation
레코드가 많다. 이 레코드를 일반 4바이트 확장 포털 `E5 18 xx yy`로 바꾸면 일부 이벤트
경로에서 포털 전체가 원자적으로 소비되지 않는다.

사용자 실화면으로 결속된 대표 사례:

- `611DF0`: `장난치지 마라！` — 정상
- `611DF8`: 원본 prefix `18` + `セラを殺しておいて、`
  - 기존 한글화: `18 | E5 18 91 99 ...`
  - 실화면: `こ세라를 죽여놓고선、`
  - `18`은 TBL에서 실제 글자 `こ`
- `611E05`: 원본 `よくものうのうと！！`
  - 기존 한글화: `E5 18 01 69 ...`
  - 다음 창 실화면: `み`
  - `69`는 TBL에서 실제 글자 `み`

즉 번역문 문제가 아니라 **원본 continuation 문법에 4바이트 portal을 넣은 저장 형식 문제**다.

## 2. 왜 16 MiB 확장만으로 기존 2바이트 슬롯이 늘지 않는가

기존 native dictionary token은 `F0–FF xx`의 2바이트이며 논리 인덱스는
`0x000–0xFFF`, 총 4,096개로 고정된다. ROM에 빈 bank를 추가해도 이 12-bit 인덱스
주소공간 자체는 늘지 않는다.

전역 2바이트 슬롯을 재정의하면 UI/name75/다른 스크립트 bank가 같은 token을 사용하므로
과거 FF-page 침범과 같은 회귀 위험이 생긴다.

따라서 **source bank가 61일 때만 별도 사전을 우선 조회하는 shadow lookup**을 사용한다.
다른 bank에서는 같은 2바이트 token이 기존 stock dictionary 의미를 유지한다.

## 3. 런타임 의미

native token index `i`를 읽을 때:

```text
if current ROM1 source bank != E1(stock bank61):
    stock dictionary(i)
else:
    shadow = shadow_pointer(i)
    if shadow == FFFF:
        stock dictionary(i)
    else:
        bank61 shadow phrase(i)
```

shadow phrase 내부에 다시 native dictionary token이 있어도 문제없다. shadow phrase를 읽는
동안 ROM1은 expansion bank `26/27/...`이므로 source bank가 `E1`이 아니고, nested token은
자동으로 기존 stock dictionary로 fallback한다.

## 4. 저장 레이아웃

12-bit native index를 4개 그룹으로 분리한다.

```text
group = index >> 10       # 0..3
local = index & 0x03FF    # 0..1023
```

| group | expansion bank | pointer table | phrase pool |
|---:|---:|---:|---:|
| 0 | `26` | `0000–07FF` | `0800–FFFF` |
| 1 | `27` | `0000–07FF` | `0800–FFFF` |
| 2 | `28` | `0000–07FF` | `0800–FFFF` |
| 3 | `29` | `0000–07FF` | `0800–FFFF` |

각 pointer는 LE16이며 `FFFF`가 **shadow 없음 → stock fallback** sentinel이다.

신규 shadow allocation 정책:

- 현재 bank61 전체 zstring에서 직접 사용 중인 native index는 금지
- `0x000–0xEFF`만 사용 (`FF xx` page 신규 할당 금지)
- token trail `00`이 되는 index 금지
- 같은 raw phrase는 동일 shadow index를 공유
- record prefix/terminator/총 payload 길이는 보존
- 기존 `E5 18` body는 `2-byte native token + 01 padding`으로 교체

## 5. 현재 후보의 전수 대상

정본 `out/patch/main_p1_base_manifest.json`과 현재 메인을 재대조한 규칙:

```text
region=script
bank=61
manifest/original prefix 길이 <= 1
현재 body 시작 = E5 18
```

결과:

- 대상 record: **1,811**
  - prefix 0: 1,202
  - prefix 1: 609
- 기존 ext3 slot: 1,573 unique
- raw phrase: **1,572 unique**
- 현재 bank61 native 직접 사용 index: **161**
- whole-bank ext3-aware native scan과 manifest scan 집합: **exact 일치**
- 후보 적용 후 위 위험 규칙의 `E5 18` 잔여: **0**

실제 배치:

- bank26: 1,001 phrase · 58,045 payload bytes · cursor `EABD`
- bank27: 571 phrase · 16,681 payload bytes · cursor `4929`
- bank28: 0 phrase, 예약
- bank29: 0 phrase, 예약

## 6. 런타임 훅

현재 승인 runtime의 non-ext3 leaf branch:

```text
7F:FF0D  55 8B EC 83 EC 08 EA D4 06 00 A0
```

후보에서는:

```text
7F:FF0D  EA 18 FF 00 F0 90 90 90 90 90 90
              └─ far jump F000:FF18
```

새 handler:

- logical `7F:FF18`
- 길이 **103 bytes**
- 부모의 `7F:FF18–FFEF`가 all-FF임을 빌드 전 확인
- `DEB2`로 현재 ROM1 bank 취득
- `AL == E1`일 때만 shadow lookup
- hit: expansion bank `26 + group` map → pointer fetch → `FAD0` → 기존 phrase loop `7A:0743`
- miss: saved source bank 복원 → 기존 stock leaf `7A:06E2`
- hit의 saved source-bank stack은 기존 `7A:074C pop ax / 074D DEB5`가 복원

기존 ext3/alias runtime 자체는 다른 주소에서 그대로 유지된다.

## 7. 시그 실화면 결속 레코드의 후보 형태

```text
611DF0  17 34 18 E5 18 B0 96
         기존 정상 line, 변경 없음

611DF8  18 F5 BE 01 01 01 01 01 01 01 01 01
         prefix 18 유지
         F5 BE = shadow index 05BE
         기대 출력: 세라를 죽여놓고선、

611E05  F3 AE 01 01 01 01 01 01 01
         F3 AE = shadow index 03AE
         기대 출력: 뻔뻔하게 잘도 살아 숨 쉬는구나！！

611E13  기존 ext3 유지
         기대 출력: 에？…… 죽였다고요？
```

따라서 실측 핵심은 `611DF8`의 선두 `こ`와 다음 창 단독 `み`가 사라지는지다.

## 8. 정적 디코더 정책

전역 `Dictionary.expand()`에는 shadow 의미를 넣지 않는다. 같은 token index가 source bank에
따라 의미가 달라지므로 전역 변경은 다른 bank를 오독하게 만든다.

대신:

- `tools/bank61_shadow_dictionary.py`
  - `runtime_installed()`
  - `shadow_raw_entry()`
  - `expand_source_body()`
  - `expand_logical_body()`

를 사용한다. 이 후보가 메인 승격되면 **bank61 record를 정적으로 렌더하는 신규 감사/빌더는
source-bank-aware helper를 사용해야 한다.**

## 9. 현재 게이트

후보: `out/patch/bank61_shadow_dictionary_candidate.wsc`

- shadow target 1,811/1,811 raw phrase/render/prefix/terminator/size exact
- bank61 manifest 비대상 record 변경 0
- whole-bank native-token collision 0
- `prefix<=1 + E5 18` 위험 잔여 0
- 허용 범위 밖 변경 0 bytes
- false segmented-pointer 0
- checksum stored/calculated exact (`0AA8`)
- 잘못 추정했던 bank59 E006 `5942F3/5942FC/594318`은 부모 메인과 byte/terminator exact
- 무장 `카논→캐논` 4건은 동일 후보에 유지, record bytes/terminator exact

범용 `verify_hook_contract.py`는 현재 메인과 후보에서 **동일한 역사적 FAIL 집합**을 낸다.
이는 오래된 ext3 metadata의 cave accounting/WRAM operand 경고이며 후보 신규 차이가 아니다.
별도 shadow audit가 신규 handler/target/비대상/체크섬을 독립 검증한다.

## 10. 승격 조건

메인 승격 전 BizHawk에서 최소 다음을 확인한다.

1. 동일 시그 장면:
   - `장난치지 마라！`
   - `세라를 죽여놓고선、` 앞에 `こ` 없음
   - 다음 창에 `み` 단독 출력 없음
   - `뻔뻔하게 잘도 살아 숨 쉬는구나！！`
   - 이어지는 `에？…… 죽였다고요？` 정상
2. 같은 bank61의 다른 시나리오 대사 몇 장면에서 일반 native/ext3 대사가 정상
3. 무장 목록에서 `캐논` 표기 정상

사용자 실측 승인 전에는 메인 TIP으로 승격하지 않는다.
