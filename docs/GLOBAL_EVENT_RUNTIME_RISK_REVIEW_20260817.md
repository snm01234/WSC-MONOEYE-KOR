# 게임 전체 Event Error / 이벤트-민감 텍스트 런타임 위험 검토

작성일: 2026-08-17  
기준 ROM: `out/patch/stage22t_uso_katejina_event8ce3_native2_portal_v3_candidate.wsc`  
SHA-256: `FBD7AD5F36D1248AAB27B9A3A1E90B4EF2EC0676567B6BB42B76979E3C9B3260`

> 2026-08-17 v3 최종 상태: v2의 STAGE22t 런타임 성공 구조는 그대로 유지하고, 전역 native dictionary와 충돌하던 `E51B` portal ID만 semantic ownership 0인 `E51D`로 교체했다. 사용자 실측에서 Event Error 소멸, `……어？`, 직후 웃소 대사, 이후 카테지나 이벤트가 모두 정상임을 재확인했고 2026-08-17 11:23 KST 메인 TIP으로 승격했다. 현재 메인 SHA-256은 `FBD7AD5F36D1248AAB27B9A3A1E90B4EF2EC0676567B6BB42B76979E3C9B3260`이다.

## 1. 런타임 확인된 사실

STAGE22t 웃소/카테지나 이벤트에서 기존 메인 TIP은 `63:8CD5`의 exact-fit direct `E5 18` 뒤에서 Event Error `12288 / 36067` (`3000:8CE3`)가 발생했다.

v2 후보에서는:

- `63:8CD5`를 `17 34 18 | F1 91 E5 1B`로 변경
- `E51B`는 expansion bank26 helper로 연결
- helper는 direct Hangul byte가 아니라 `F36A F16E` (`어` + `？`)의 nested-native-only 구조
- `63:8CDC` terminator와 `63:8CE3` 이후 이벤트 제어열은 byte-exact 유지

사용자 실측 결과:

- Event Error가 사라짐
- `……어？` 정상
- 다음 웃소 대사 깨짐도 v2에서 사라짐
- 이후 이벤트가 정상 진행

따라서 **이벤트-민감 caller에서 direct `E5 18` 대신 2-byte native-loop portal + nested-native-only helper를 사용하면 런타임 상태를 보존할 수 있다**는 강한 실측 근거가 생겼다.

## 2. 전역 정적 감사 결과

재현 도구:

- `tools/audit_global_event_runtime_risk_v2.py`
- `out/patch/global_event_runtime_risk_v2.json`

### 2.1 안전이 확인된 항목

| 항목 | 결과 |
|---|---:|
| runtime contracts | 24,925 |
| 원본 대비 terminator 이동 | **0** |
| `E5 18 xx yy` 내부 NUL (`xx==00` 또는 `yy==00`) | **0** |
| bank64–69 미분류 event/data diff | **0** |
| bank64–69 차이 | 기존 fixed-label/event-name allowlist 범위만 존재 |

즉 현재 게임 전체에서 **Event Error의 전형적인 원인인 terminator 이동, 이벤트 bank opcode 변조, ext3 middle-NUL**은 발견되지 않았다.

## 3. 잔여 구조적 위험군

### 3.1 control-adjacent direct `E5 18`

`E5 18`로 시작하는 대사 뒤 첫 non-NUL 데이터가 `08/17/18` 이벤트 제어인 레코드는 **8,591개**다.

이 수치 자체는 위험 건수와 동일하지 않다. 정상 동작하는 일반 ext3 대사도 포함한다. 따라서 전부 native화하는 것은 금지한다.

### 3.2 가장 강한 구조 일치군 — 220건

다음 조건을 모두 만족하는 레코드가 **220건** 남아 있다.

1. 원본 body가 정확히 4바이트
2. 원본이 `2-byte native dictionary token + 2-byte native dictionary token`
3. 후보가 정확히 4바이트 `E5 18 xx yy`
4. 원본/후보 terminator 동일
5. terminator 뒤 NUL run = 2
6. 다음 이벤트 데이터가 `08` 또는 `17`

이 구조는 STAGE22t `638CD5` 및 과거 runtime-proven `scenario_first native-only` 오류와 가장 가깝다.

그러나 **220건 모두 실제 오류라고 단정할 수 없다.** caller/이벤트 문법에 따라 ext3가 정상인 경우도 있기 때문이다. 따라서 자동 일괄 변환 대신 단계적 분류가 필요하다.

### 3.3 continuation `18 + native + native` → `18 + E5 18` 잔여 — 3건

더 강한 기존 continuation 규칙과 일치하는 잔여는 3건이다.

- `624305`
- `6253F6`
- `6335A6`

`624305`, `6335A6`은 과거 카테지나 문제의 잘못된 위치 가설로 이미 실측 반증된 이력이 있으므로 단순 패턴만으로 수정하지 않는다. `6253F6`도 caller 증거 없이 자동 수정하지 않는다.

## 4. 현재 v2 자체의 전역 승격 blocker — `E51B`

STAGE22t 실측은 통과했지만 `E51B`를 글로벌 2-byte portal로 잡은 현재 v2에는 별도 전역 충돌이 있다.

현재 native dictionary 내부에 ordinary glyph pair `E51B`를 포함하는 문구가 2개 존재한다.

| index | token | raw | 외부 consumer |
|---|---|---|---|
| `0B68` | `FB68` | `E511 E51B` | `75:B3FD` name75 |
| `0C47` | `FC47` | `E51C E51B` | `75:B401` name75 |

즉 이 두 native dictionary entry가 실행되면 마지막 `E51B`가 새 portal로 오인될 수 있다.

**결론: 현재 v2 ROM은 STAGE22t 실측 PASS지만 그대로 메인 승격 금지.**

## 5. 기존 dictionary ID 회수 없이 2-byte helper 공간을 늘리는 방향

promoted parent main을 대상으로 다음 semantic ownership을 합쳐 `E5xx` code unit을 전수 감사했다.

- script banks 60–6F
- aux text banks 50–5E, 76
- name75
- native/bank10 dictionary phrase
- ext3 expansion phrase

그 결과 `E518`/`E519`를 제외하고 현재 의미 소비자가 0인 `E5xx` trail이 **106개** 존재한다.

따라서 F0–FF dictionary slot을 회수하지 않고도, 아래처럼 **sparse 2-byte helper-ID namespace**를 만들 수 있다.

```text
E5 xx  -> special helper ID
          |
          +-- expansion bank26 pointer table
                 |
                 +-- nested-native-only phrase
```

중요한 차이는 `E5xx` 전체를 새 문법으로 쓰는 것이 아니라, **전역 semantic consumer가 0으로 증명된 pair만 개별 예약**한다는 것이다.

첫 교체 probe 후보는 `E51D`가 적합하다.

- 현재 script/aux/name75 consumer = 0
- native dictionary phrase 내부 consumer = 0
- ext3 phrase 내부 consumer = 0
- 기존 `E518` ext3 및 disabled `E519`와 분리

raw ROM 안의 비텍스트 우연 바이트는 존재할 수 있으므로, 예약 기준은 단순 raw-pair scan이 아니라 typed/semantic ownership union을 정본으로 한다. 단 raw hit는 진단 보고에는 계속 남긴다.

## 6. helper 저장 문법 규칙

v1/v2 비교로 새 invariant를 확정한다.

### 금지

```text
expansion helper = direct Hangul glyph/marker bytes
```

v1의 `E786 1D` helper는 Event Error 자체는 없앴지만 다음 웃소 표시를 깨뜨렸다.

### 허용

```text
expansion helper = native dictionary token(s) + native punctuation/token
```

v2의 `F36A F16E`는 실측 정상이다.

따라서 special 2-byte event-safe portal의 helper는 **nested-native-only**를 하드 가드로 둔다.

## 7. 수정 로드맵

### Phase 0 — v2 portal 충돌 제거: **v3 실측 PASS / 메인 승격 완료**

완료 사항:

1. `E51B` 대신 semantic-zero `E51D`로 magic 교체
2. bank26 helper `F36A F16E` 유지
3. `638CD5` 마지막 token만 `E51B -> E51D`
4. 두 walker compare constant도 `E51B -> E51D`
5. v2→v3 실질 diff는 위 3개 portal trail 바이트 + 체크섬 1바이트, 총 4바이트뿐
6. builder에 script/aux/name75뿐 아니라 native 4096 + ext3 65,536 phrase ownership scan을 추가
7. `E51D` semantic ownership: script 0 / aux 0 / name75 0 / native dictionary 0 / ext3 phrase 0
8. event bank unknown diff 0 / terminator drift 0 / unsafe ext3 middle-NUL 0

v3 후보:

- ROM: `out/patch/stage22t_uso_katejina_event8ce3_native2_portal_v3_candidate.wsc`
- SHA-256: `FBD7AD5F36D1248AAB27B9A3A1E90B4EF2EC0676567B6BB42B76979E3C9B3260`
- checksum: `98B5`
- paired SaveRAM: `sram/stage22t_uso_katejina_event8ce3_native2_portal_v3_candidate.sav`
- build report: `out/patch/stage22t_uso_katejina_event8ce3_native2_portal_v3_report.json`
- global audit: `out/patch/global_event_runtime_risk_v3.json`

Phase 0는 사용자 런타임 재확인과 전역 정적 게이트를 모두 통과해 메인 승격까지 완료했다. 롤백은 `out/patch/backup/20260817_112312_pre_stage22t_event8ce3_native2_portal_v3/monoeye_ko_expanded.wsc`, 승격 보고서는 `out/patch/stage22t_uso_katejina_event8ce3_native2_portal_v3_promotion_report.json`이다.

### Phase 1 — sparse 2-byte portal 정식화

1. semantic-zero `E5xx` pair를 data file로 예약 관리
2. 각 reserved pair에 expansion bank26 helper pointer를 대응
3. build-time union ownership audit 추가
4. 예약 pair가 script/aux/name75/native/ext3 어느 곳에 새로 생기면 build fail
5. helper payload에 direct Hangul marker/glyph가 있으면 build fail
6. portal 사용 target 외의 code/runtime hook 변경을 allowlist로 고정

106개의 현재 semantic-zero pair는 이론상 106개의 독립 2-byte helper를 제공할 수 있다. 실제 사용 수는 필요한 위험 레코드만큼만 최소화한다.

### Phase 2 — 223개 구조적 suspect triage

정본 worklist:

- builder: `tools/build_global_event_runtime_risk_worklist.py`
- 결과: `out/patch/global_event_runtime_risk_priority_worklist.json`
- exact4 220건 중 **155건은 기존 native 2-byte token 두 개만으로 현재 한글 의미를 정확히 재구성 가능**
- 나머지 65건만 기존 native pair로 직접 복구 불가
- control18 3건 중 native pair 직접 복구 가능은 1건(`624305`)이나, 과거 false-target 실측 이력이 있으므로 자동 수정 금지

우선순위:

- **P1 137건**: active scenario-first + double NUL + 바로 `17 28`
- **P2 57건**: active scenario-first + double NUL + `08 xx`
- **P3 26건**: quarantine continuation
- **P0 3건**: control18 패턴. 주소 패턴만으로 고치지 않고 caller/history 우선 검토

v3는 사용자 런타임 PASS 후 메인 승격 완료됐다. 처음에는 P1을 소량 stage/bundle 후보로 나누려 했으나, 사용자가 220건 개별/소량 실측은 지나치게 오래 걸린다고 판단하여 **exact4 220건 전체를 한 후보에 일괄 rehome하고 대표 구간만 실측하는 방식**으로 변경했다. 기존 batch01은 whole-game 후보에 흡수되며 별도 실측하지 않는다.

일괄 후보:

- ROM: `out/patch/global_event_native_rehome_220_candidate.wsc`
- SHA-256: `714200FFDCAD34D01C12C8F560B8CA71163C165803E5E9894FEB30F523E166C6`
- 155건: 기존 native 2-token으로 직접 복구
- 65건: 4-byte `E5 1D <helper_id> 01` event-safe wrapper
- 65건의 기존 ext3 번역은 bank26 nested helper에서 재사용하며 unique helper는 58개
- 기존 STAGE22 two-byte E51D는 helper index 0으로 계속 호환
- generalized dispatcher는 원래 all-FF였던 bank7E `7E:FD83–FE07`에 배치

독립 감사 결과:

- runtime contract `24,925`, hard failure `0`, review `0`
- exact4 strong suspect **220 -> 0**
- 모든 220개 현재 한글 render mismatch `0`
- terminator/double-NUL/following control drift `0`
- bank64–69 unknown event/data diff `0`
- battle audit failure `0`
- STAGE22 fixed E51D regression `PASS`

별도 control18 3건(`624305`, `6253F6`, `6335A6`)은 이 220건에 포함되지 않으며 기존 false-target 이력 때문에 미변경한다.

실측은 `docs/GLOBAL_EVENT_NATIVE_REHOME_220_TEST_MATRIX.md`의 대표 구간을 사용했다. 사용자 실측에서 **#2 Gato/콜로니 `가토오오오！！` parameterized E51D 구간**과 **#6 STAGE22t 웃소/카테지나 기존 index0 E51D 구간**이 모두 PASS했다. 사용자는 전수 정적 게이트와 이 두 핵심 런타임 대표 경로면 승격 근거로 충분하다고 판단했고, 2026-08-17 12:10 KST에 후보를 메인 TIP으로 승격했다.

승격 후 메인 SHA-256은 `714200FFDCAD34D01C12C8F560B8CA71163C165803E5E9894FEB30F523E166C6`, rollback은 `out/patch/backup/20260817_121019_pre_global_event_native_rehome_220/monoeye_ko_expanded.wsc`이다. canonical runtime contract 감사 24,925건은 hard failure 0 / review 0, battle audit failure 0, terminology audit clean을 다시 확인했다. `61035E`은 parameterized E51D의 user-runtime-proven 대표 anchor로, 나머지 동일 경로는 개별 실측으로 과장하지 않고 promoted-static으로 기록한다.

### Phase 3 — 자동 회귀 게이트

향후 모든 candidate/main build에서 다음을 강제한다.

- dialogue terminator source-exact
- NUL run 축소 금지
- bank64–69 non-allowlist diff = 0
- unsafe ext3 middle-NUL = 0
- runtime-native-only ledger에 direct `E5 18` 금지
- reserved special portal pair ownership 충돌 = 0
- special helper direct Hangul = 0
- following `08/17` control bytes source-exact

## 8. 현재 판단

게임 전체가 Event Error 위험에서 완전히 자유롭다고 판정할 수는 없다.

그러나 현재 전수 감사에서는 **이벤트 opcode 자체 손상이나 terminator 손실 같은 광범위 구조 파손은 없다.** 잔여 위험은 주로 특정 scenario caller에서 `direct E5 18`의 런타임 상태 전이가 맞지 않는 **storage-route 문제**로 좁혀졌다.

따라서 가장 안전한 방향은:

> 이벤트 bank나 레코드 경계를 다시 건드리지 않고, runtime-proven 위험 레코드만 2-byte sparse portal + nested-native-only expansion helper로 단계적으로 rehome한다.

현재 v2는 이 방향의 런타임 타당성을 증명했지만 `E51B` 전역 충돌 때문에 그대로 승격하지 않는다.
