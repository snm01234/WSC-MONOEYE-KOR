# v1.3.1

`v1.3.1`은 `v1.3` 이후 실플레이 검수에서 추가로 확인된 시나리오 글리프 손상, 2행 대사 20셀 경계 잘림, 행 경계 중복, 일부 말투·용어, ID 커맨드 도움말 잔여 일본어를 정리한 안정화 릴리스입니다. SaveRAM 형식은 변경하지 않았으며, 메인 승격 과정에서도 live SaveRAM을 byte-exact로 보존했습니다.

## 주요 변경 사항

### 시나리오 글리프 손상 구조 수정

- 세라 대사 뒤 시그의 `아니……`가 깨져 표시되던 문제를 수정했습니다.
- 원인은 일부 native dictionary 문구가 한글 렌더링 진입 마커 없이 사용되는 구조였습니다.
- 사용자 실측으로 정상 출력이 확인된 marker-safe native phrase 방식으로 동일 구조를 전수 검사하고 **7건**을 정리했습니다.
- 같은 계열의 `아니……`, 시나리오/전투대사 문구를 포함하며, 구조 제어 바이트와 terminator는 보존했습니다.

### Stage 1~3 시나리오 교정

- Stage 1 브라드 중위의 `……그렇군요。`를 문맥에 맞게 **`……그렇군。`**으로 교정했습니다.
- Stage 2의 `에르메스` 표기를 **`엘메스`**로 통일했습니다.
- Stage 3 프로스트 형제 문맥에서 `……시작됐네、 오빠。`를 **`……시작됐네、 형。`**으로 수정했습니다.
- Stage 3 세라가 아인에게 반말을 사용하는 문맥에 맞춰 `심술궂군요 아인은`을 **`심술궂군 아인`**으로 수정했습니다.

### 인접 행 중복 40건 정리

- 과거 reflow 과정에서 다음 행의 첫 단어 또는 일부 음절이 이전 행 끝에도 중복 삽입된 사례를 전수 검사했습니다.
- 실제 구조적 중복으로 판정한 **40개 레코드**를 수정했습니다.
- 대표적으로 `미노프스키`가 1행 끝과 2행 시작에 반복되던 대사를 정상화했습니다.
- 정상적인 형태소 경계, 고유명사 반복, 원문 자체의 의도된 반복은 유지했습니다.

### 2행 대사 20셀 경계 잘림 전수 복구

- 전체 40셀을 넘지 않더라도 첫 행을 개별적으로 20셀에 맞추는 과정에서 단어 뒷부분이 실제 번역문에서 삭제된 구조를 확인했습니다.
- 유지 중인 번역 원문과 현재 runtime contract를 대조해 같은 구조의 **58개 2행 그룹 / 116개 표시 행**을 재검수했습니다.
- **52개 그룹**은 비공백 문자를 삭제하지 않는 재배치로 복구했습니다.
- 전체가 40셀을 소폭 초과하는 **6개 그룹**은 원문 의미를 유지하면서 2×20셀에 맞게 축약했습니다.
- 최종 후보 및 승격 메인에서 해당 **116개 행 모두 20셀 이하**임을 확인했습니다.
- 대표 수정:
  - `세레인 익스페리 소위가 저 기체에` / `탑승하는 건 이미 결정된 사항입니다。`
  - `……누군가가 나를 단단히 잡아주지` / `않으면 견딜 수 없을 것 같아。`
  - `외부 세계는 본 적도 없고、` / `다른 사람과 접한 적도 거의 없어。`

### ID 커맨드 도움말 잔여 일본어 정리

- `맞혀 주마！！` ID 커맨드에서 하단 설명창에 일본어가 남던 별도 도움말 경로를 확인했습니다.
- bank 5C 후반부에서 같은 계열로 남은 혼합 일본어 도움말 **10건**을 한글화했습니다.
- 명중, 공격력, 명중·회피, 반응 상승 및 HP 회복 설명을 기존 한글 도움말 표현과 통일했습니다.
- 후보 ROM 재검사에서 해당 혼합 도움말 잔여를 **0건**으로 확인했습니다.

### runtime contract 테스트 정비

- 권위 있는 dialogue runtime safety gate와 달리 오래된 단위테스트가 삭제된 생성 파일과 과거 battle anchor를 전제로 하던 문제를 수정했습니다.
- 단위테스트를 현재 runtime-visible anchor 및 현재 메인 contract manifest 기준으로 갱신했습니다.
- 최종 `tools/test_dialogue_runtime_contracts.py`: **6/6 PASS**.

## 최종 검증

v1.3.1 메인TIP에서 다음을 확인했습니다.

- 메인TIP 크기: `16,777,216` bytes
- 메인TIP SHA-256: `8CDC239822B82DB874EEFCCFD7AEBEEF67AE318B2CE32D1B1D69D6CB8C02A2C`
- WonderSwan checksum: `CA9E` · 유효
- dialogue runtime contracts: `24,925`건
- active checked: `7,395`건
- quarantine checked: `17,530`건
- hard failure: `0`
- review item: `0`
- terminology audit: active source / dictionary / five-bank dictionary / rendered record 잔여 `0`
- dialogue runtime contract 단위테스트: `6 / 6 PASS`
- xdelta 원본 → 메인TIP round-trip: byte-exact PASS
- VCDIFF header indicator: `0x00`
- VCDIFF secondary compression/application header: 배포 호환 모드에서 비활성

## 배포 파일

- `monoeye_ko_expanded_v1.3.1.xdelta`
- xdelta SHA-256: `CC456DACE99F2F25B7B2AEECD835F64F04AF12AA1E0E96E944F14B2C334A078F`
- 크기: `1,616,690` bytes
- xdelta round-trip: PASS

패치는 **합법적으로 소유한 일본판 원본 ROM**에 적용해야 합니다.

지원 원본 SHA-256:

`376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`

정상 적용 후 ROM SHA-256:

`8CDC239822B82DB874EEFCCFD7AEBEEF67AE318B2CE32D1B1D69D6CB8C02A2C`

## 세이브 호환성

v1.3.1은 ROM 데이터 수정이며 SaveRAM 형식 변경은 없습니다. v1.3에서 사용하던 세이브를 그대로 사용할 수 있습니다. 실제 메인 승격에서도 live SaveRAM SHA-256이 승격 전후 동일함을 확인했습니다. 업데이트 전에는 원본 ROM과 세이브 파일을 별도로 백업하는 것을 권장합니다.

## 참고

v1.3에서 기록한 ID 커맨드 `선제` 우측 하단 잔여 타일 관련 알려진 이슈는 이번 v1.3.1의 수정 범위에 포함되지 않습니다. 해당 내용은 `RELEASE_NOTES_v1.3.md`를 참고해 주세요.
