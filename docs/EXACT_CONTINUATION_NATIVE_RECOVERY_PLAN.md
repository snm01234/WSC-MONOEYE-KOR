# exact continuation 잔여 9건 — 확장 bank10 native 2-token 복구 계획

작성일: 2026-08-17  
상태: **Phase A–D 정적 검증 완료 / `60D194` 실측 PASS / 사용자 요청으로 Phase F 메인 승격 완료**  
승격 전 메인 TIP SHA-256: `FC7C3A426C866F8B60F5056571349C79D6BA11A2632BEEE4209DFEBBF8A0C5E9`  
후보 및 승격 후 메인 TIP SHA-256: `F68B3261BEECC32047D17952E36BC2B891CD5D66410F9FC9293487571A0FC8E2`

## 1. 목적

STAGE21t 포우–제로, 닥터 J, 카테지나 실측에서 반복 확인된 `가케하우`/히라가나 제어행 노출 문제는 다음 구조에서 재현되었다.

- 원본: parser/control prefix 뒤에 **2바이트 native dictionary token 2개**
- 문제 패치: 같은 자리의 4바이트 본문을 **`E5 18 xx yy` ext3 portal 1개**로 교체
- 바로 다음 물리 레코드/제어행이 `0x17 ...`로 시작하는 경우, 실제 런타임에서 parser state가 어긋나 다음 제어 바이트가 일본어 글리프로 노출될 수 있음

실측으로 정상화된 대표 사례:

- `63449B`: 포우–제로 `어째서……`
- `63463A`: 카테지나 `우후후후……` scenario-first
- `635855`, `635BFB`: 닥터 J `……뭐、 승산 좋은 도박？`
- `635866`, `635C0C`: 닥터 J `그건 아니지만。` 후속 wrapper

이번 계획의 목표는 exact continuation audit에 남은 **고신뢰 9건**을 임시 blank/padding/ext3로 우회하지 않고, 가능한 한 원본과 같은 **`0x18 + 2-byte dict + 2-byte dict`** 문법으로 복구하는 것이다.

## 2. 현재 잔여 9건

아래 9건은 직전 exact audit에서 공통적으로 다음 조건을 만족한다.

1. 원본 payload가 정확히 `18 + native dictionary token + native dictionary token`
2. 현재 payload가 `18 + E5 18 xx yy`
3. 현재 한국어 의미 자체는 정상
4. 바로 다음 non-NUL 데이터가 `0x17` 제어행
5. 각 레코드의 원래 본문 길이는 5바이트이므로 **2바이트 token 2개로 복구하면 길이도 원본과 동일**

| 주소 | 의도 출력 | 현재 위험 구조 | 다음 제어행 시작 |
|---|---|---|---|
| `609A83` | `설마……` | `18 E5 18 62 58` | `17 28 08 1E ...` |
| `60D194` | `큭……` | `18 E5 18 D5 3D` | `17 28 08 1E ...` |
| `60F27C` | `후후……` | `18 E5 18 62 61` | `17 28 01 06 ...` |
| `61010E` | `명심해라……` | `18 E5 18 49 9C` | `17 28 01 06 ...` |
| `61802F` | `후후후후……` | `18 E5 18 29 93` | `17 28 01 01 06 ...` |
| `62439F` | `설마……` | `18 E5 18 21 A2` | `17 28 01 01 06 ...` |
| `628AB8` | `후후후후……` | `18 E5 18 29 93` | `17 28 01 01 06 ...` |
| `62CC7D` | `후후후후……` | `18 E5 18 29 93` | `17 28 08 1E ...` |
| `63A9F8` | `이걸로……` | `18 E5 18 3B C1` | `17 28 08 32 ...` |

### 기존 token 2개만으로 바로 복구할 수 있는가?

현재 메인 dictionary 전체의 안전한 2바이트 dictionary token 조합을 전수 대입한 결과, 위 9개 문구를 **현재 존재하는 token 2개만으로 정확히 만드는 조합은 9/9 모두 0개**였다.

따라서 한국어 의미를 유지하면서 원본 2-token 문법으로 되돌리려면 소수의 native helper phrase가 추가로 필요하다.

## 3. 16MiB 확장 영역 분석 결과

이 프로젝트의 16MiB 확장은 원본 뒤 append가 아니라 **앞쪽에 8MiB를 prepend**한다.

```text
file 0x000000–0x7FFFFF : expansion banks 00–7F
file 0x800000–0xFFFFFF : 기존 8MiB stock ROM이 이동된 영역
```

현재 메인 TIP 실측:

- expansion 영역 크기: `8,388,608` bytes
- non-`FF`: `1,324,045` bytes
- 사용률: 약 **15.78%**
- `FF` 잔여: `7,064,563` bytes, 약 **6.74MiB**
- 완전히 `FF`인 64KiB bank: **103개**

따라서 phrase payload 저장공간 자체는 부족하지 않다.

## 4. 이미 존재하는 2바이트 확장 dictionary — bank10

현재 ROM은 이미 expansion **bank `10`**을 2바이트 확장 dictionary 저장소로 사용한다.

`out/patch/exp_dictionary_meta.json` 기준:

- `stock_count = 3831` (`0x0EF7`)
- 총 dictionary count = `4096` (`0x1000`)
- 확장 slot 수 = `265`
- 확장 bank = `10`
- `ext_in_expansion = true`
- pointer table offset = `0000`

즉 `0x0EF7–0x0FFF` 범위는 기존 2바이트 dictionary token 문법을 유지하면서 phrase payload를 expansion bank10에서 읽을 수 있다. `0x0F00–0x0FFF`는 `FFxx` 형태이다.

현재 bank10 phrase 영역 실측:

- phrase 사용 시작: 약 `0x0213`
- 현재 마지막 phrase 끝: `0x123B`
- `0x123B–0xFFFF`: 연속 `FF`
- tail 여유: **60,869 bytes (약 59.4KiB)**

이번 5개 helper는 수십 바이트 수준이므로 **payload 용량은 사실상 무제한에 가깝다.**

### 중요: 공간과 token ID는 별도 자원

bank10에 59KiB가 비어 있어도, 레코드에서 이를 2바이트로 호출하려면 `FE/FF` page의 **사용 가능한 dictionary ID**가 있어야 한다.

현재 1차 조사:

- 확장 dictionary 범위에서 확실한 미사용 ID: **2개**
  - `0F59`
  - `0F6D`
- 완전 동일 phrase 중복 후보: **3쌍**
  - `0F00` / `0F70` → `……그래。`
  - `0F01` / `0F72` → `……뭐라고！？`
  - `0F07` / `0FC0` → `후후……`

중복 중 한쪽을 canonical ID로 유지하고 다른 ID를 안전하게 회수할 수 있다면:

- 기존 미사용 2개
- 중복 회수 3개
- 합계 **5개 ID**

를 확보할 수 있다.

단, 이 3개 중복 ID는 **아직 회수 확정이 아니다.** 일반 raw-byte 검색은 그래픽/바이너리에서 우연한 `FFxx` 조합도 소비자로 오인하므로, 실제 typed script/UI/aux consumer 감사를 통과해야 한다.

## 5. 필요한 helper는 5개

처음에는 6종(`설마`, `큭`, `후후`, `명심해라`, `후후후후`, `이걸로`)이 필요해 보이지만, 기존 `FF07`이 이미 정확히 `후후……`을 저장한다.

현재 사전 확인:

- `F191` raw = `02 02` → `……`
- `FF07` raw = `EC 8D E7 C0 E7 C0 02 02` → `후후……`

따라서 `후후` helper 하나를 공용으로 쓰면:

```text
후후……      = [후후 helper] + [F191 = ……]
후후후후……  = [후후 helper] + [FF07 = 후후……]
```

이 된다. 두 경우 모두 **정확히 2바이트 dictionary token 2개**다.

따라서 신규 helper는 다음 5종이면 충분하다.

1. `설마`
2. `큭`
3. `후후`
4. `명심해라`
5. `이걸로`

각 helper는 phrase 내부에서 독립적으로 Hangul run을 시작하도록 self-contained payload로 만든다. 다른 phrase의 Hangul 상태를 이어받는 wrapper 방식은 사용하지 않는다.

## 6. 목표 레코드 구성

ID는 consumer audit 이후 확정한다. 아래 `<...>`는 2바이트 helper token이다.

| 주소 | 목표 payload | 목표 출력 |
|---|---|---|
| `609A83` | `18 + <설마> + F191` | `설마……` |
| `62439F` | `18 + <설마> + F191` | `설마……` |
| `60D194` | `18 + <큭> + F191` | `큭……` |
| `60F27C` | `18 + <후후> + F191` | `후후……` |
| `61010E` | `18 + <명심해라> + F191` | `명심해라……` |
| `61802F` | `18 + <후후> + FF07` | `후후후후……` |
| `628AB8` | `18 + <후후> + FF07` | `후후후후……` |
| `62CC7D` | `18 + <후후> + FF07` | `후후후후……` |
| `63A9F8` | `18 + <이걸로> + F191` | `이걸로……` |

공통 계약:

- payload 길이: **5바이트 유지**
- 선두 `18`: control/parser prefix 유지
- direct `E5 18`: **0개**
- compact3: **0개**
- `0x01` filler: 추가하지 않음
- NUL terminator 위치: 원본/현재와 byte-exact 유지
- 다음 `0x17` 제어행: byte-exact 유지

## 7. 구현 계획

### Phase A — 2바이트 ID 회수 가능성 감사 (ROM 변경 없음)

전용 감사 도구를 먼저 만든다.

검증 대상:

- 확실한 미사용 후보 `0F59`, `0F6D`
- 중복 회수 후보 `0F70`, `0F72`, `0FC0`
- canonical 보존 대상 `0F00`, `0F01`, `0F07`

필수 검사:

1. runtime dialogue contracts의 실제 dictionary 소비자
2. script zstring의 문법적으로 유효한 `FE/FF` token 소비자
3. UI/aux known-zstring 소비자
4. dictionary nested consumer
5. pointer alias / phrase 내부 진입 pointer
6. raw-byte hit는 참고용으로만 기록하고 단독 차단 근거로 사용하지 않음

중복 ID에 실제 typed consumer가 있으면 같은 문구의 canonical ID로 **2바이트→2바이트 치환** 가능한지 검증한다. 길이가 바뀌는 소비자 수정은 금지한다.

**Phase A 통과 조건:** 안전하게 쓸 수 있는 2바이트 ID가 최소 5개 확보되어야 한다.

5개가 확보되지 않으면 ROM 수정 단계로 진행하지 않고, 추가 dead/duplicate ID 감사를 실시한다. stock bank5F의 임의 슬롯 덮어쓰기로 우회하지 않는다.

### Phase B — bank10 helper 배치 후보 생성

Phase A에서 확정된 5개 ID만 사용한다.

- bank10을 `--force-format`으로 초기화하지 않는다.
- 현재 pointer table과 기존 phrase를 byte-exact 보존한다.
- 현재 phrase tail 뒤의 연속 `FF`에서 필요한 만큼만 append한다.
- 각 helper payload는 self-contained Hangul marker + glyph + NUL 구조로 만든다.
- helper끼리 physical payload를 겹치지 않는다.
- 기존 pointer를 phrase 내부 중간으로 향하게 만들지 않는다.

권장 provisional ID pool은 `0F59`, `0F6D`, `0F70`, `0F72`, `0FC0`이지만, **Phase A audit 결과가 확정되기 전에는 매핑을 고정하지 않는다.**

### Phase C — 잔여 9레코드 native pair 복구

현재 메인 TIP을 부모로 별도 candidate를 생성한다.

변경 허용 범위:

1. 확정된 helper ID의 pointer entry 5개
2. bank10 tail의 신규 helper payload 5개
3. exact continuation 9레코드 body
4. 필요 시 duplicate-ID typed consumer의 canonical 2바이트 치환
5. WonderSwan checksum 2바이트

그 외 diff는 **0건**이어야 한다.

### Phase D — 정적 감사

필수 게이트:

1. exact continuation high-risk: **9 → 0**
2. 9레코드 rendered text: 목표와 9/9 exact match
3. 9레코드 body: `18 + 2-byte dict + 2-byte dict` 9/9
4. direct `E5 18`: 0/9
5. 다음 `0x17` control boundary: 9/9 byte-exact
6. helper ID consumer allowlist 일치
7. helper pointer가 bank10 expansion 영역만 가리킴
8. bank10 기존 phrase/pointer 비대상 영역 byte-exact
9. authoritative dialogue runtime safety: hard failure 0 / review 0
10. 기존 battle exact audit: failure 0
11. terminology audit: clean
12. live SaveRAM: 미변경

### Phase E — 실측

candidate만 생성하고 메인 승격은 하지 않는다.

실측 전에 각 9주소가 어느 stage/event에서 발생하는지 pointer/caller 기준으로 매핑한 **test matrix**를 생성한다. 사용자는 가능한 항목부터 실측한다.

최소 확인:

- 해당 대사 자체가 정상 한글
- 독립 `こ`/한자/히라가나가 붙지 않음
- 직후 `0x17` 제어행이 화면 글리프로 노출되지 않음
- 다음 초상/대사/이벤트 진행 정상
- 반복 진입 시 이벤트 replay/loop 없음

### Phase F — 승격

사용자 실측 승인 이후에만 메인 승격한다.

승격 절차:

1. 메인 SHA 고정
2. 메인 ROM 백업
3. candidate → main ROM-only 승격
4. live SaveRAM byte-exact 보존
5. Phase D 게이트를 메인 자체에 재실행
6. 실패 시 자동 롤백
7. `PATCH_PROGRESS.md`와 본 문서에 최종 ID 매핑/후보 SHA/승격 SHA 기록

## 8. 금지 사항

- 확장 bank가 넓다는 이유로 새 bank를 통째로 `FF` 초기화하지 않는다.
- `patch_exp_dictionary --force-format`을 사용하지 않는다.
- 기존 ext dictionary 265슬롯을 재할당하지 않는다.
- typed consumer가 확인되지 않은 중복 ID를 raw-byte 검색만 보고 덮어쓰지 않는다.
- 잔여 9건을 다시 `E5 18`/compact3/visible padding으로 우회하지 않는다.
- 원본 일본어 token을 그대로 되돌려 한국어를 잃는 방식은 사용하지 않는다.
- 실측 승인 없이 메인 승격하지 않는다.

## 9. 현재 판단

**저장공간 측면에서는 구현 가능성이 매우 높다.** bank10에 약 59.4KiB 연속 여유가 있으며 신규 helper payload는 수십 바이트면 충분하다.

실제 남은 결정점은 저장공간이 아니라 **5개의 2바이트 dictionary ID를 fail-closed 방식으로 회수할 수 있는가**이다. 현재 `0F59`, `0F6D` 2개는 미사용 후보이고, `0F70`, `0F72`, `0FC0`은 duplicate reclaim 후보이므로 수량상 정확히 5개가 맞는다. 그러나 이 세 duplicate ID는 UI/aux까지 포함한 typed-consumer audit가 완료되기 전에는 재사용하지 않는다.

따라서 다음 실제 작업의 첫 단계는 **ROM 수정이 아니라 Phase A ID 회수 감사 도구 작성 및 실행**이다.

## 10. 2026-08-17 구현 결과

### 10.1 Phase A — ID 회수 감사 완료

전용 read-only 감사 도구:

- `tools/audit_exact_continuation_native_recovery_ids.py`
- 결과: `out/patch/exact_continuation_native_recovery_id_audit.json`

확정 결과:

| 회수 ID | 처리 | canonical 보존 ID | typed/script 소비자 |
|---|---|---|---:|
| `0F59` | true-free 회수 | - | 0 |
| `0F6D` | true-free 회수 | - | 0 |
| `0F70` | duplicate 회수 | `0F00` | 4 |
| `0F72` | duplicate 회수 | `0F01` | 7 |
| `0FC0` | duplicate 회수 | `0F07` | 10 |

`0F70/0F72/0FC0`의 21개 소비자는 모두 script zstring 범위이며 aux/name75/nested consumer는 0건이었다. 따라서 회수 전에 동일 phrase의 canonical ID로 **2바이트→2바이트** 치환했다. raw hit는 소유권 판정 근거로 사용하지 않았다.

### 10.2 Phase B — bank10 helper 배치

요청대로 필요량에 딱 맞추지 않고 조금 넉넉하게 잡았다.

- 기존 live phrase tail: `0x123B`
- 신규 helper pool: **`0x1240–0x1340` = 0x100 bytes (256 bytes)**
- 최종 live tail: `0x133B`
- pool 시작부터 최종 live tail까지: **251 bytes**

helper는 서로 충분히 띄워 두었고 마지막 helper를 pool 끝에 가깝게 배치하여, 일반 append cursor가 중간 여백을 즉시 재사용하지 않도록 했다.

| helper ID | 문구 | bank10 pointer |
|---|---|---|
| `0F59` | `설마` | `0x1240` |
| `0F6D` | `큭` | `0x1280` |
| `0F70` | `후후` | `0x12C0` |
| `0FC0` | `이걸로` | `0x1300` |
| `0F72` | `명심해라` | `0x1330` |

모든 helper는 `EC8D` Hangul run marker를 자체 포함하는 self-contained payload이며 다른 phrase의 Hangul state를 이어받지 않는다.

### 10.3 Phase C — 9레코드 native pair 복구 후보

빌더:

- `tools/build_exact_continuation_native_recovery_candidate.py`

산출물:

- ROM: `out/patch/exact_continuation_native_recovery_candidate.wsc`
- SHA-256: `F68B3261BEECC32047D17952E36BC2B891CD5D66410F9FC9293487571A0FC8E2`
- SaveRAM snapshot: `sram/exact_continuation_native_recovery_candidate.sav`
- 빌드 보고서: `out/patch/exact_continuation_native_recovery_candidate_report.json`

최종 9개 payload:

| 주소 | native payload | 출력 |
|---|---|---|
| `609A83` | `18 FF59 F191` | `설마……` |
| `60D194` | `18 FF6D F191` | `큭……` |
| `60F27C` | `18 FF70 F191` | `후후……` |
| `61010E` | `18 FF72 F191` | `명심해라……` |
| `61802F` | `18 FF70 FF07` | `후후후후……` |
| `62439F` | `18 FF59 F191` | `설마……` |
| `628AB8` | `18 FF70 FF07` | `후후후후……` |
| `62CC7D` | `18 FF70 FF07` | `후후후후……` |
| `63A9F8` | `18 FFC0 F191` | `이걸로……` |

### 10.4 Phase D — 정적 게이트 완료

독립 감사/회귀 결과:

- exact selected high-risk: **9 → 0**
- rendered text: **9/9 exact**
- `18 + 2-byte dict + 2-byte dict`: **9/9**
- direct `E5 18`: **0/9**
- compact3: **0/9**
- 다음 `0x17` control boundary: **9/9 byte-exact**
- helper consumer allowlist: **5/5 exact**
- duplicate consumer canonical retarget: **21건**
- 허용 범위 밖 diff: **0건**
- runtime safety: **hard failure 0 / review 0** (`24,925` contracts)
- battle exact audit: **failure 0**
- terminology audit: **clean**
- 종합 static gate: **17/17 PASS**

보고서:

- `out/patch/exact_continuation_native_recovery_exact_audit.json`
- `out/patch/exact_continuation_native_recovery_runtime_contracts.json`
- `out/patch/exact_continuation_native_recovery_runtime_safety.json`
- `out/patch/exact_continuation_native_recovery_battle_audit.json`
- `out/patch/exact_continuation_native_recovery_terminology_audit.json`
- `out/patch/exact_continuation_native_recovery_static_gate.json`

#### broad scan에서 별도로 보이는 2건

fresh broad shape scan은 `624305`, `6335A6`도 같은 바이트 형태로 잡아 총 11건을 찾는다. 그러나 이 둘은 과거 v3에서 **카테지나 실제 분기라고 잘못 추정했던 duplicate hypothesis** 대상이며, v3 실측이 실제 증상을 바꾸지 못한 뒤 v4에서 v2/v3 계보를 버리고 실제 `63463A`를 수정했다.

따라서 이번 문서의 9건 범위에는 넣지 않았고, candidate에서도 두 행을 **현재 메인과 byte-exact로 보존**하는 것을 별도 게이트로 고정했다. broad 수치는 11→2지만, 본 계획의 selected risk는 정확히 **9→0**이다.

### 10.5 Phase E — 실측 준비 상태

실측 매트릭스:

- `docs/EXACT_CONTINUATION_NATIVE_RECOVERY_TEST_MATRIX.md`
- `out/patch/exact_continuation_native_recovery_test_matrix.json`

9개 모두 authoritative runtime contract의 `scenario_<bundle start>` 식별자와 일본어 문맥을 함께 기록했다. 단순 raw pointer 검색은 오탐이 많고 현재 유지되는 typed caller 자료만으로 모든 항목의 stage label을 안전하게 확정할 수 없으므로, **스테이지명은 추측해서 기입하지 않았다.**

`2026-08-17` 사용자 실측에서 `60D194 / scenario_60D17C / 큭……` 항목이 정상임을 확인했고, 이어서 사용자가 candidate 기준 TIP의 메인 승격을 명시적으로 요청했다.

### 10.6 Phase F — 메인 승격 완료

- 승격 시각: `2026-08-17T10:12:14+09:00`
- 승격 후 메인 SHA-256: `F68B3261BEECC32047D17952E36BC2B891CD5D66410F9FC9293487571A0FC8E2`
- 실측 확인 항목: `60D194` — `큭……` — **PASS**
- live SaveRAM: 승격 전후 byte-exact 보존
- 승격 보고서: `out/patch/exact_continuation_native_recovery_promotion_report.json`
- 롤백 ROM: `out/patch/backup/20260817_101213_pre_exact_continuation_native_recovery/monoeye_ko_expanded.wsc`
- 롤백 ROM SHA-256: `FC7C3A426C866F8B60F5056571349C79D6BA11A2632BEEE4209DFEBBF8A0C5E9`

나머지 8개 항목은 정적 게이트는 통과했지만 각각의 개별 실측을 완료했다는 의미는 아니다. 추후 해당 장면을 플레이할 때 test matrix에 실측 결과를 계속 누적한다.
