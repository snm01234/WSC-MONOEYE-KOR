# P1 Event Native-Pair Batch01 런타임 테스트

작성일: 2026-08-17  
기준 메인 SHA-256: `FBD7AD5F36D1248AAB27B9A3A1E90B4EF2EC0676567B6BB42B76979E3C9B3260`  
후보: `out/patch/p1_event_native_pair_batch01_candidate.wsc`  
후보 SHA-256: `53D2180E31D0C05D862482E1629CFC5581B717FD38425865DBF2AF700A1CC0AE`

## 목적

게임 전체 P1 위험군 가운데 가장 보수적인 첫 배치다. 두 대상 모두:

- scenario-first
- 원본 body = 정확히 native dictionary token 2개
- 현재 메인 body = exact-fit `E5 18` 4바이트
- terminator 뒤 `00 00`
- 다음 제어 = `17 28 01 06`
- 원본 body를 그대로 복구해도 현재 dictionary가 동일한 한국어를 렌더

따라서 신규 portal, dictionary reclaim, dictionary write, runtime hook 수정 없이 원본 문법만 복구한다.

## 변경 대상

| 주소 | 메인 body | 후보/원본 body | 현재 렌더 | terminator | 다음 제어 |
|---|---|---|---|---|---|
| `6256CC` | `E5 18 19 99` | `F5 89 F1 91` | `도몬……` | `6256D3` | `6256D5 = 17 28 01 06` |
| `625730` | `E5 18 19 99` | `F5 89 F1 91` | `도몬……` | `625737` | `625739 = 17 28 01 06` |

`F589 = 도몬`, `F191 = ……`이므로 후보 body는 원본과 byte-exact이면서 한국어 출력도 유지한다.

## 실측 1 — `6256CC`

주변 문맥:

1. `……레인！！`
2. `우……`
3. `저, 저는……`
4. `……아무 말도 하지 마라, 레인.`
5. **`도몬……` ← 대상 `6256CC`**
6. `마침내 그 끔찍한 악몽이 종막을 고했군요……`
7. `……그래.`

확인:

- [ ] `도몬……`이 정상 한글로 표시
- [ ] 초상/화자 이상 없음
- [ ] 일본어/제어 글리프 노출 없음
- [ ] 대사 replay 없음
- [ ] 조기 이벤트 종료 없음
- [ ] Event Error 없음
- [ ] 다음 `마침내 ...` 대사로 정상 진행

## 실측 2 — `625730`

주변 문맥:

1. `이걸로 저 흉악한 데빌 건담 소체가 ...`
2. `지구 대지 위에서 두 번 다시 부활하는 비극은 없을 걸세…….`
3. `아버지, 어머니…… 보고 계시나요.`
4. **`도몬……` ← 대상 `625730`**
5. `『……더 이상 가면 안 돼！！』`
6. `……어, 무슨 영적 주파수지！？`
7. 이후 주도 관련 대화

확인:

- [ ] 두 번째 `도몬……`도 정상 한글
- [ ] 직후 `『……더 이상 가면 안 돼！！』` 정상
- [ ] 초상/화자/이벤트 상태 정상
- [ ] Event Error 없음
- [ ] 이후 이벤트 정상 진행

## 정적 게이트

- target 2/2 Original body byte-exact 복구
- target prefix/extent 보존
- double-NUL 2/2 보존
- 다음 `17 28 01 06` 2/2 보존
- runtime contract audit: hard failure 0 / review 0
- battle audit: failure 0
- terminology audit: clean
- 전역 exact4 suspect `220 -> 218`
- 전역 P1 suspect `137 -> 135`
- 전역 terminator drift 0
- main TIP / live SaveRAM 미변경

사용자 실측 PASS 전에는 메인 승격하지 않는다.
