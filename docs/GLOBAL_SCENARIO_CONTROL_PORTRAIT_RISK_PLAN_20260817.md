# 게임 전체 시나리오 제어문 / 화자·초상 상태 위험 검토 계획

작성일: 2026-08-17  
기준 메인 TIP: `out/patch/monoeye_ko_expanded.wsc`  
SHA-256: `714200FFDCAD34D01C12C8F560B8CA71163C165803E5E9894FEB30F523E166C6`

## 1. 사용자 실측 기준점 — STAGE4 솔로몬 부근

사용자 실측에서 다음 연쇄 증상을 확인했다.

1. 브래드의 `……네？` 직후 구조 제어행이 `はせ` 계열 히라가나처럼 화면에 노출된다.
2. 그 다음 `아무리 어리다고 해도 / 눈도 보이고 생각할 머리도 있지。` 대사에서, 원래 갱신되어야 할 샤아 아즈나블의 초상 대신 이전 시그 초상이 남는다.

정확한 구조는 다음과 같다.

```text
60:B400  17 34 18 | E5 18 91 E6 | 00 00
          브래드      ……네？

60:B409  08 34 00
          ^^^^^^^^ 구조적 화자/초상 제어 레코드

60:B40C  17 34 18 | ...
          아무리 어리다고 해도
60:B419  눈도 보이고 생각할 머리도 있지。
```

원본 `60:B400`은:

```text
17 34 18 | F1 91 08 1D | 00 00 | 08 34 00 | 17 34 18 ...
```

현재 메인은:

```text
17 34 18 | E5 18 91 E6 | 00 00 | 08 34 00 | 17 34 18 ...
```

즉 `60:B409 = 08 34 00` 자체는 원본과 현재 메인이 **byte-exact**다. 이벤트/초상 제어 바이트를 잘못 덮어쓴 문제가 아니다.

가장 강한 원인은 **직전 대사 저장 경로가 원본의 mixed native/raw grammar에서 top-level direct `E5 18`로 바뀌면서 런타임 text/control 상태 전이가 달라진 것**이다. 그 결과 원래 화자/초상 제어로 실행돼야 할 `08 34 00`이 visible text 쪽으로 끌려 들어가고, 화자 갱신도 실행되지 않아 이전 시그 초상이 남는 것으로 판단한다.

이 설명은 제어문 노출과 초상 오표시를 하나의 연쇄 원인으로 설명하며, 과거의 `こ`, `がけはう`, 다음 대사 반복/스킵, 초상 잔류 문제와 같은 계열이다.

## 2. 왜 `08`이 특히 위험한가

원본 `60:B400` body는 `F191 081D`다.

- `F191`은 text body 안에서 `……`
- 뒤의 `08 1D`는 **text body 내부에서는** `は？`에 해당하는 visible source data
- 하지만 **레코드 경계에서는** `08 actor_id 00`이 화자/초상 제어 문법

따라서 `08`은 값만 보고 text/control을 판정할 수 없는 **문맥 의존 바이트**다.

번역 과정에서 `F191 081D` 전체를 하나의 direct ext3 `E5 18 xx yy`로 치환하면 화면에 보이는 문자열은 맞아도, special caller가 다음 `08 xx 00`을 처리하기 전에 내부 상태를 원본과 다르게 유지할 수 있다.

## 3. 전역 read-only 감사 결과

재현 도구:

- `tools/audit_global_scenario_control_portrait_state_risk.py`
- `out/patch/global_scenario_control_portrait_state_risk.json`

기존 별도 `08 actor_id 00` 감사:

- `tools/audit_speaker_dictlead_nul_collisions.py`
- `out/patch/stage4_global_speaker_dictlead_nul_collision_audit.json`

### 3.1 구조 자체가 파손된 것은 아님

| 항목 | 결과 |
|---|---:|
| scenario source/current NUL + next-control drift | **0** |
| 원본 `08 34 00` 위치 | 159 |
| 현재 메인에서 `08 34 00`가 바뀐 위치 | **0** |
| 기존 F0–FF actor-id/NUL collision | 27 |
| 그중 즉시 후속 dialogue | 19 |
| 현재 일본어/혼합 잔재 | **0** |

따라서 게임 전체 이벤트 제어 데이터가 광범위하게 훼손된 상태는 아니다. 문제의 중심은 **제어 데이터 자체가 아니라 그 직전 대사의 storage/runtime route**다.

### 3.2 Tier A — STAGE4와 원본 body까지 동일한 최고위험 복제군

다음 조건을 모두 만족하는 레코드는 **9건**이다.

- scenario-first / active
- 원본 body가 정확히 `F191 081D`
- 현재 body가 exact 4-byte direct `E5 18 xx yy`
- terminator 뒤 double-NUL
- 즉시 다음 데이터가 `08xx` 또는 `1728`

| 주소 | 현재 대사 | 현재 body | 다음 제어 |
|---|---|---|---|
| `606061` | `……네？` | `E51853A8` | `1728` |
| `608450` | `……네？` | `E518438D` | `0885` |
| `6093A3` | `……네？` | `E51891D3` | `0824` |
| `60A2BB` | `……네？` | `E51833CC` | `083C` |
| `60A452` | `……네？` | `E51853D9` | **`0834`** |
| **`60B400`** | **`……네？`** | `E51891E6` | **`0834`** |
| `60CA11` | `……뭐？` | `E51833CD` | `1728` |
| `611D57` | `……네？` | `E51853DA` | `1728` |
| `61A9AA` | `……네？` | `E518438F` | `084B` |

`60A452`는 실패 기준점과 **원본 body와 다음 `08 34`까지 동일**하므로 가장 가까운 잠재 재현점이다.

### 3.3 Tier B — 최근 220건에서 빠졌던 mixed exact4 위험군

최근 승격한 220건은 “원본 4바이트가 native dictionary token 2개”인 경우를 대상으로 했다.

이번 STAGE4 문제처럼 원본이 mixed/raw 문법인 exact4는 별도이며, 현재 **59건** 남아 있다.

- 다음 `08xx`: **25건**
- 다음 `1728`: **34건**

원본 2-code-unit 문법 분류:

| 원본 문법 | 건수 |
|---|---:|
| native dict + raw2 | 38 |
| native dict + **context-sensitive 08/17/18 pair** | **9** |
| raw2 + native dict | 7 |
| raw2 + raw2 | 5 |

59건 전체가 실제 버그라는 뜻은 아니다. 다만 사용자 실측으로 이 집단에서 실제 런타임 실패가 1건 확정됐으므로, 이제 단순 inventory가 아니라 **강한 구조적 suspect 집단**으로 다룬다.

### 3.4 넓은 direct E518 집단 — 자동 수정 금지

`scenario_first + direct E518 + double-NUL + immediate 08/17`까지 넓히면 현재 **2,947건**이다.

- next `17`: 1,824
- next `08`: 1,123

이 수에는 정상 동작하는 일반 ext3도 매우 많이 포함되므로 절대로 일괄 native화하지 않는다. **59건 exact4 → 9건 exact clone → runtime evidence** 순으로 우선도를 좁힌다.

### 3.5 Tier C — `18 + E518` continuation 계열

control-adjacent `scenario_continuation` 중 현재 body가 `18 E5 18...`인 것은 **646건**이다.

이 역시 전체를 버그로 간주하지 않는다.

그중 과거 오류와 가장 강하게 일치하는:

```text
Original: 18 + native token + native token
Current : 18 + E5 18 ...
```

exact 구조는 여전히 3건이다.

- `624305`
- `6253F6`
- `6335A6`

이 중 `624305`, `6335A6`은 과거 false-target 실측 이력이 있으므로 패턴만으로 자동 수정하지 않는다.

## 4. STAGE4의 저장 방식 결정

현재 native dictionary 4,096개를 전수 조합해 `……네？`를 **기존 native token 2개만으로 정확히 구성할 수 있는지** 검사했다.

결과:

- `……` native token: 존재
- `？` native token: 존재
- `네` / `네？` native token: 없음
- `……네？`를 정확히 만드는 existing native **2-token 조합: 0개**

따라서 `60B400`을 단순히 existing native 2-token으로 복구하는 방법은 없다.

새 dictionary ID를 회수하지도 않는다.

### 채택할 방향

이미 메인에 승격되어 있고 사용자 실측까지 통과한 **parameterized E51D event-safe outer route**를 재사용한다.

```text
현재 위험 body
E5 18 91 E6

후보
E5 1D <helper_id> 01
       |
       +-- bank26 helper -> 기존 번역 phrase를 nested route로 호출
```

현재 메인에는 이미 다음 인프라가 있다.

- `E5 1D <helper_id> 01` dispatcher
- bank26 pointer table `26:2100`
- helper data `26:2200+`
- Gato `61035E`에서 parameterized E51D runtime PASS
- STAGE22 `638CD5`에서 fixed E51D runtime PASS

따라서 **새 런타임 훅을 추가하지 않고 기존 dispatcher/table만 확장**한다.

helper 안에는 direct Hangul glyph/marker를 새로 넣지 않는다. 기존 번역 phrase를 nested helper로 호출하는, 이미 Gato에서 실측된 구조를 사용한다.

## 5. 수정 단계

### Phase 1 — 9개 exact clone을 한 후보에서 수정

우선 `F191081D` exact clone 9건을 한 후보 ROM에 적용한다.

핵심 조건:

- 현재 한글 문구 유지
- 4-byte body extent 유지
- terminator 주소 유지
- double-NUL 유지
- 다음 `08xx / 1728` 바이트 source-exact
- `08 actor_id 00` 자체는 절대 수정하지 않음
- 기존 parameterized E51D dispatcher 재사용
- 신규 F0–FF dictionary ID reclaim 0

실측 핵심은 두 곳이면 된다.

1. **`60B400` STAGE4 사용자 오류 장면**
   - `……네？` 정상
   - `はせ` 제어문 노출 소멸
   - 다음 샤아 아즈나블 초상 정상
   - 다음 두 줄 정상 진행
2. **`60A452`**
   - 같은 source `F191081D`
   - 같은 next `08 34`
   - 동일 계열 clone 회귀 확인

그리고 기존 runtime anchor 두 곳을 회귀 확인한다.

- `61035E` Gato parameterized E51D
- `638CD5` Uso/Katejina fixed E51D

### Phase 2 — 나머지 mixed exact4 50건

Phase 1이 정상이라면 나머지 50건을 한 번에 후보화한다.

우선순위:

1. immediate `08xx` 25건
2. 같은 source-body 반복 clone
3. punctuation/question/exclamation 계열 `F191091D`, `F191191D`, `F1912B1D`
4. immediate `1728` 34건

모두 같은 방식으로 처리하지 않고, existing native 2-token으로 정확히 재구성 가능한 것은 native로, 그렇지 않은 것은 parameterized E51D로 처리한다.

### Phase 3 — continuation 위험군은 별도 유지

646건을 일괄 변환하지 않는다.

- 이미 runtime-proven continuation ledger
- exact `18 + native + native` 3건
- 사용자 제보 장면과 caller/control 구조가 겹치는 clone

순으로만 승격 후보에 포함한다.

## 6. 영구 회귀 게이트 추가

이번 문제는 기존 감사가 “다음 control bytes가 보존됐는가”만 확인해서 놓쳤다. 앞으로는 **직전 대사 route가 그 control을 정상 실행할 수 있는가**까지 봐야 한다.

canonical runtime audit에 다음을 추가한다.

1. `scenario-first` exact4 source grammar를 저장
2. mixed source 4B → top-level direct E518 변환 여부 기록
3. double-NUL 뒤 `08 actor_id 00`가 오면 predecessor-aware 위험 판정
4. 사용자 runtime-proven 주소는 direct E518 금지 ledger에 등록
5. `08 xx 00` 자체가 source-exact인지 별도 하드 게이트
6. next portrait/speaker control을 수정하는 repair와 text-body repair를 서로 분리
7. 기존 `audit_speaker_dictlead_nul_collisions.py`의 27/19 collision audit도 계속 유지

## 7. 현재 결론

이번 STAGE4 문제는 `08 34 00` 자체가 깨진 것이 아니다.

> **직전 `……네？`를 direct E5 18로 저장한 것이 event-sensitive caller의 text/control 상태를 바꾸어, 원본에서는 실행되던 `08 34 00`을 visible text로 노출시키고 다음 초상 갱신까지 막는 구조적 storage-route 문제**로 보는 것이 현재 가장 강한 설명이다.

그리고 같은 exact source grammar를 가진 레코드가 게임 전체에 9건, 같은 mixed exact4 범주가 59건 남아 있다.

따라서 다음 실제 수정은 **9건 exact clone 일괄 후보 → 대표 2장면 + 기존 E51D 2장면 실측 → 나머지 50건 확대** 순으로 진행하는 것이 가장 안전하다.

현재 메인 TIP은 이 분석 과정에서 변경하지 않았다.
