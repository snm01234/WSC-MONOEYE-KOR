# v1.2

`v1.2`는 `v1.1` 이후 실플레이에서 추가로 발견된 메뉴 도움말 표시 문제, 전투/시나리오의 제어문·사전 토큰 구조 오류, 일부 이벤트 진행 오류와 고유명사 표기 불일치를 누적 정리한 안정화 릴리스입니다. 특히 기존 번역문 자체보다 **시나리오 continuation의 원본 문법을 보존하지 못해 생기는 런타임 오류**를 전역적으로 재검토하고, 대표 실측과 fail-closed 정적 감사를 함께 적용했습니다.

## 주요 변경 사항

### 인터미션/메뉴 도움말 후속 수정

- 인터미션 도움말의 `목록` 라우트 9곳과 설명문 51곳을 compact 전용 경로로 재정리했습니다.
- 고정 길이 설명문 뒤에 남던 visible `0x01` 패딩을 활성 라우트 기준 `0`건으로 정리했습니다.
- 과거 공용 사전 슬롯 `005E`가 `그건`으로 재사용되어 도움말에 잘못 노출될 수 있던 문제를 피하도록 private 경로를 사용했습니다.
- 총 60개 메뉴 라우트에 대해 포인터/렌더/종료 구조를 재검증했습니다.

### 전투대사 런타임 구조 안정화

- 웃소 전투대사에서 올바른 한글 본문 앞에 일본어/한자 글리프가 붙던 false visible lead를 제거했습니다.
- 하만(하이퍼) 피격 대사에서 전투용 sentinel `不要`가 `미사용`으로 잘못 번역되어 표시되던 계열을 원본 구조로 복구했습니다.
- 콜로니 레이저 네오 지온 사관 대사를 정상화했습니다.
- bank5F 전투대사 75개 레코드가 이후 재사용된 private ext3/compact 슬롯에 잘못 묶일 수 있던 문제를 재바인딩했습니다.
- 짧은 `큭！`, `젠장！` 계열은 실측에서 안정적인 기존 live 사전 슬롯을 재사용하도록 정리했습니다.

### 용어/인명 표준화

- `쿼트로`/`콰트로` 계열을 `크와트로`로 통일했습니다.
- `라 카일람` 계열을 `라 카이람`으로 통일했습니다.
- `스엣손`/`스웨손` 계열을 `스에손 스테로`/`스에손`으로 통일했습니다.
- `엠마` 계열을 `에마`로 통일했습니다.
- STAGE14n을 포함한 플/플투 관련 표기를 문맥에 맞게 `플`, `플투`로 통일하고 `플루츠`, `푸루투`, `풀투`, `플 투` 등의 변형을 제거했습니다.
- 분절되어 있던 `주、 도……？`를 `쥬、 도……？`로 교정했습니다.

### STAGE21t / STAGE22t 이벤트 구조 수정

- STAGE21t 카테지나 `우후후후……` 이후 제어문이 노출되던 continuation을 원본 native two-token 문법으로 복구했습니다.
- 닥터 J 대사의 wrapper 구간에서 한글 재시작 경계를 명시해 `그건`/일본어 글리프가 섞이던 문제를 수정했습니다.
- exact-continuation 감사에서 선별된 9개 레코드를 `18 + 2바이트 dictionary token + 2바이트 dictionary token` 원본 문법으로 복구했습니다.
- 이를 위해 bank10 helper 5개를 안전한 영역으로 분리하고, 중복 문법 소비자 21곳을 길이 보존 방식으로 canonical slot에 재지정했습니다.
- STAGE22t `638CD5` 부근에서 발생하던 `이벤트 오류 12288 / 36067`을 native2/portal 구조로 수정했으며, 이후 웃소/카테지나 대화까지 실측 정상 진행을 확인했습니다.

### 전역 이벤트/시나리오 continuation 구조 재정비

- 이벤트 런타임 위험도가 높았던 220개 continuation을 원본 native pair 또는 event-safe parameterized 경로로 재배치했습니다.
  - direct native pair: 155건
  - event-safe parameterized: 65건
- 추가 mixed exact4 시나리오 59건을 정리했습니다.
  - existing native pair: 25건
  - parameterized helper: 34건
- 이후 전역 continuation 감사에서 구조적으로 동일한 2,746건을 다시 정리했습니다.
  - double-NUL structural continuation: 2,740건
  - top-level E5 04 portal16 경로: 2,739건
  - single-NUL visible Korean leak 제거: 6건
- 대표 실측 A/B/C/G/H와 정적 구조 감사를 병행했고, terminator/경계/렌더 불일치 없이 메인에 승격했습니다.

### STAGE14n 미번역 및 잔여 일본어 검사

- 플과 플투 대화의 `でもね……どんなに不愉快でも、`가 일본어로 그대로 출력되던 누락을 `하지만……아무리 불쾌해도、`로 번역했습니다.
- 바로 다음 `아무리 미워도……`와 이어지는 문맥을 기준으로 레코드 구조와 terminator를 보존했습니다.
- v1.2 최종 메인 기준으로 번역 대상 bank59 이벤트/ID·UI/전투대사 표본 1,893건을 추가 검사해 문장형 일본어 잔여 `0`건을 확인했습니다.

## 최종 정적 검증

v1.2 최종 메인TIP에서 다음을 다시 확인했습니다.

- 메인TIP 크기: `16,777,216` bytes
- WonderSwan checksum `82D0` 유효
- terminology audit: active source / dictionary / five-bank dictionary / rendered record 잔여 `0`
- dialogue runtime contracts: `24,925`건
- active checked: `7,390`건
- quarantine checked: `17,535`건
- hard failure: `0`
- review item: `0`
- xdelta 원본 → 메인TIP round-trip: byte-exact PASS
- VCDIFF header indicator: `0x00`
- VCDIFF secondary compression/application header: 배포 호환 모드에서 비활성

## 배포 파일

- `monoeye_ko_expanded_v1.2.xdelta`
- xdelta SHA-256: `C26CF206528E33700AAEE81807889FF5EECB9B08367306A6DCCD169E19F91F28`
- 크기: `1,615,143` bytes
- xdelta round-trip: PASS

패치는 **합법적으로 소유한 일본판 원본 ROM**에 적용해야 합니다.

지원 원본 SHA-256:

`376E4C6B4B81CC3A7DCEB15DC4B7D0AF04D3E6C8B81E8572569C39D3394870A0`

정상 적용 후 ROM SHA-256:

`C7BB4B5C936653888062F2389351C586FC483DEDACDBA209918B327E440E2131`

## 세이브 호환성

v1.2는 ROM 데이터 수정이며 SaveRAM 형식 변경은 없습니다. 각 메인 승격 과정에서 live SaveRAM은 byte-exact로 보존했습니다. 기존 세이브는 그대로 사용할 수 있지만 업데이트 전 원본 ROM과 세이브 파일을 별도로 백업하는 것을 권장합니다.
