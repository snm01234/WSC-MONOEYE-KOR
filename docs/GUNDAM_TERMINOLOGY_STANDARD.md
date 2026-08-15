# 건담 명칭 한국어 표준화 기준

작성: 2026-08-08  
대상: SD Gundam G Generation Mono-Eye Gundams 한국어 패치 전 영역  
기계 기준: `data/gundam_terminology_standard_ko.json`  
감사기: `tools/audit_gundam_terminology_standard.py`  
후보 빌더: `tools/build_gundam_terminology_candidate.py`

## 1. 적용 원칙

1. 사용자가 확정한 표기를 최우선 정본으로 사용한다.
2. 애매한 항목만 최신 한국어 공식 자료/현재 한국어 문서명을 교차확인한다.
3. 대사만이 아니라 시나리오, 인물명, 유닛명, 무장명, 도감, UI, name75 카탈로그와 런타임 사전을 같은 기준으로 묶는다.
4. `source_text`, `current`, `before`, `review_notes` 등 과거 원문/감사 증거 필드는 증거 보존을 위해 옛 표기를 남길 수 있다. 실제 적용되는 `ko`, `after_rows`, 카탈로그 값과 현재 TIP 렌더링에는 금지 표기가 없어야 한다.
5. 화면 폭 때문에 전각 공백을 사용하는 경우 일반 공백과 동등한 표기로 감사한다.
6. 새 번역을 적용할 때 아래 금지 표기가 다시 들어오면 감사기를 실패시켜 재발을 막는다.

## 2. 확정 표준명

| 분류 | 금지/구 표기 | 표준 표기 |
|---|---|---|
| 유닛 | 갸프란 | **갸프랑** |
| 인물 | 콰트로 | **크와트로** |
| 유닛 | 하이자크 | **하이잭** |
| 유닛 | 시스크드 | **시스쿠드** |
| 유닛 | GP02A 사이사리스 | **GP02A 사이살리스** |
| 게임 내 무장 | 핵 바주카 / 원자 바주카 | **아토믹 바주카** |
| 유닛 | 켐퍼 | **캠퍼** |
| 인물 | 에기유 델라즈 | **에규 데라즈** |
| 인물 | 브렉스 포라 / 블렉스 포라 | **블랙스 포라** |
| 인물 | 오르바 프로스트 | **올바 프로스트** |
| 인물 | 몬샤 | **몬시아** |
| 인물 | 아볼리 | **아폴리** |
| 인물 | 마우어 | **마우아** |
| 인물 | 셀레인 | **세레인** |
| 조직 | 미리샤 | **밀리샤** |
| 인물 | 자빈느 | **자비네** (샤르) |
| 유닛 | 지・오 | **디・오** (대사는 **디　오**) |
| 유닛 | 토르기스 | **톨기스** |
| 인물 | 레일라 / 레이먼드 | **레이라 레이몬드** |
| 유닛 | 테라 수오노 | **테라・스오노** (대사는 **테라　스오노**) |
| 인물 | 세일러 | **세이라** |
| 인물 | 아르테시아 | **아르테이시아** |
| 인물 | 배닝 | **버닝** (전체 이름 **사우스 버닝**) |
| 함선 | 라디시 / 라 디슈 | **래디시** |
| 인물 | 긴가남 / 김 긴가남 | **깅가남 / 김 깅가남** |
| 유닛 | 턴 X | **턴X** |
| 인명 요소 | 피스 크래프트 | **피스크래프트** |
| 인물 | 제나 미아 | **제나 자비** |

복합 이름이 문장 속에서 분리되어 사용되는 경우에도 같은 기준을 적용한다.

이번 기준에서 `아폴리`, `마우아`, `세레인`은 사용자 확정 권장 표기로 고정한다. 공식 페이지에서 관찰된 다른 표기(예: `아볼리`)는 출처 관찰값으로만 보존하고, 현재 번역·TIP 권장값에는 사용하지 않는다. `세레인 익스페리`처럼 전체 이름이 필요한 문맥은 전체 이름을 유지하되, 이름 요소 `세레인`은 동일하게 고정한다.

건담 W 도감은 `젝스 마키스`, `리리나`, `톨기스`, `엔드리스 월츠`, `트레즈 크슈리나다`, `윙 제로 커스텀`(공식 장문 `윙 건담 0(EW)`는 시트/문서만), `닥터 J`를 기준 표기로 추가했다. 원문 주소별 대조표는 [GUNDAM_W_CATALOG_REVIEW.md](/D:/monoeye/docs/GUNDAM_W_CATALOG_REVIEW.md)에 기록한다.

∀ 계열은 `밀리샤`, 크로스본 계열은 `자비네 샤르`, UC 유닛명은 `디・오`, 기렌의 야망 계열은 `레이라 레이몬드`로 통일한다. 도감·유닛 데이터·대사가 서로 다른 음역을 쓰지 않는다. `테라・스오노`는 첨자만 `스오노`로 고정하고, 대사의 전각 공백은 유지한다.

- `에기유` → `에규`
- `델라즈` → `데라즈`
- `브렉스` / `블렉스` → `블랙스`
- `오르바` → `올바`
- `콰트로` → `크와트로`
- `몬샤` → `몬시아`

## 3. 활성 번역 소스 표준화

이번 작업에서 다음 활성 데이터 계층을 전수 검사하고 필요한 표기를 수정했다.

- `data/name75_base_ko_values.json`
- `data/name75_base_ko.json`
- `data/name75_terms_ko.json`
- `data/unit_names_ko.json`
- `data/weapon_names_ko.json`
- `data/encyclopedia_ms_batch01_ko.json`
- `data/encyclopedia_ms_batch02_ko.json`
- `data/encyclopedia_character_batch01_ko.json`
- `data/encyclopedia_character_batch01_ko_part1.json`~`part3.json`
- `data/broad_stage2_title_ui_ko.json`
- `data/ko_ui_overrides.json`
- `data/dialogue_singleton_rewrite_batch002.json`
- `data/bank59_opening_batch01_ko.json`
- `data/mixed_residual_translations.json`
- `data/mixed_residual_values/aux_001.json`, `aux_003.json`, `aux_018.json`
- `out/script/dialogue_readability_changes.json`의 활성 `after_rows`

`name75_base_ko.json`과 `name75_terms_ko.json`은 각각 원천값에서 재생성한 결과와 **완전 일치**하는 것도 확인했다. 따라서 향후 카탈로그 재생성으로 구 표기가 되살아나지 않는다.

### 소스 감사 결과

초기 전수 감사:

- 활성 소스 금지 표기: **86건**
- 현재 메인 TIP 사전 금지 표기: **100건**
- 현재 메인 TIP 렌더 레코드 금지 표기: **90건**

활성 소스 수정 후:

- 활성 소스 금지 표기: **0건**
- 현재 메인 TIP 사전 금지 표기: **100건**
- 현재 메인 TIP 렌더 레코드 금지 표기: **90건**

즉 소스 표준화와 현재 실행 TIP의 런타임 표준화는 분리해서 관리한다. 현재 메인을 직접 덮지 않고 별도 후보에서 런타임 수정 구조를 검증했다.

## 4. 현재 TIP 런타임 구조와 수정 방식

기준 메인 TIP:

- `out/patch/monoeye_ko_expanded.wsc`
- SHA-256 `BE5CDB102A589FAECD487780B99D3C30DD358E938E66CDB5AEB76EBCC8F4959C`

### 4.1 stock 사전

금지 표기의 기본 이름은 다음 5개 stock 사전 항목에서 우선 교정한다.

| index | 현재 | 표준 |
|---:|---|---|
| `093B` | 델라즈 | 데라즈 |
| `0B82` | 에기유 | 에규 |
| `0C82` | 브렉스 | 블랙스 |
| `0716` | 오르바 | 올바 |
| `0B96` | 콰트로 | 크와트로 |

`0B96`만 8바이트→10바이트로 늘어나므로 bank 5F 끝의 검증된 `FF` 여유 `0xFFE9..0xFFFF`에 새 payload를 쓰고 pointer만 재지정한다. 나머지는 기존 슬롯 안에서 수정 가능하다.

`096B`의 `오르바여、나머지는 맡긴다……`처럼 하위 stock 이름을 중첩 참조하는 문구는 `0716` 교정만으로 자동으로 `올바`가 되므로 독립 재작성하지 않는다.

### 4.2 ext3 사전

stock 기본 이름 교정 뒤에도 ext3에 금지 표기가 있는 논리 인덱스 **94개**가 남으며, 물리 payload는 **93그룹**이다.

장문 문구를 전부 새로 인코딩해 append하면 bank 19가 57바이트 부족해진다. 따라서 인물명은 이미 존재하는 stock 사전 토큰을 재사용한다.

- `크와트로` → stock `0B96` / `FB96`
- `데라즈` → `093B` / `F93B`
- `에규` → `0B82` / `FB82`
- `블랙스` → `0C82` / `FC82`
- `올바` → `0716` / `F716`
- `몬시아` → 기존 정본 `08D3` / `F8D3`

그 결과:

- ext3 물리 그룹: **93**
- 제자리 수정: **91**
- append + pointer 재지정: **2**
  - `0C7C4` / bank `1C`: `핵 바주카를 장비했으며、` → `아토믹 바주카를 장비했으며、`
  - `0FFC2` / bank `1F`: `원자 바주카` → `아토믹 바주카`
- bank 19 재배치/확장: **불필요**

## 5. `하이잭` / `아토믹` 신규 글리프 처리

현재 TBL에는 새 표기에 필요한 `잭`, `믹` 두 음절이 없었다.

실제 메인 TIP의 glyph-store cave를 바이너리와 `build_store_cave()`로 역검증한 결과 sticky Hangul 범위는 정확히 **1344슬롯**, 즉 `E740..EC7F`다. 바로 다음 `EC80`은 현재 Hangul-run marker이므로 단순히 범위를 늘리면 marker와 glyph가 충돌한다.

따라서 후보는 프로젝트의 기존 marker-retarget 안전 규칙을 그대로 따른다.

1. 현재 marker `EC80`을 원본 텍스트 뱅크 출현 **0회**인 `EC8D`로 이동한다.
2. marker 치환은 확장영역과 정의된 text bank에만 수행하고, 코드 영역은 검증된 `cmp cx, marker` 1곳(`7A:FFBA`)만 바꾼다.
3. 비워진 `EC80=잭`, `EC81=믹`으로 두 글리프를 pad3에 굽는다.
4. sticky 범위는 **1344→1346**으로만 확장한다. 전체 font hook을 재설치하지 않는다.
5. ext3 런타임 cave/site는 부모 메인과 byte-identical임을 검증한다.

현재 메인 SHA에 대해 marker population도 고정 게이트로 둔다.

- expansion marker 치환: **99,620건**
- stock text-bank marker 치환: **3,332건**
- original `EC80` 충돌 보호 사이트: **0건**

## 6. 독립 후보 검증 및 메인 승격 결과

`tools/build_gundam_terminology_candidate.py`를 현재 메인에서 임시 경로로 실행한 결과:

- 후보 SHA-256: `2FA34B87F1C975291C8BD60AFA7DF7FD4A92983FB84296F6216E01AD1F5FAFEF`
- stock 기본 이름 수정: **5개**
- ext3 물리 그룹 수정: **93개**
  - 제자리: **91**
  - append/repoint: **2**
- 신규 글리프: `잭=EC80`, `믹=EC81`
- marker: `EC80→EC8D`
- 표준명 인코딩 실패: **0건**
- known record 길이/NUL terminator 변화: **0건**
- 직접 record 변화는 길이 보존 marker 치환뿐: **2개 known record**
- ext3 runtime guard 변화: **0건**
- 후보 SaveRAM: 현재 `monoeye_ko_expanded.sav`와 **byte-identical**

후보를 별도 `tools/audit_gundam_terminology_standard.py`로 다시 검사한 결과:

- 활성 소스 금지 표기: **0건**
- 후보 사전 금지 표기: **0건**
- 후보 렌더 레코드 금지 표기: **0건**
- `status=clean`

2026-08-08 21:05 사용자가 메인 TIP에 먼저 반영한 뒤 실측하고 문제가 있으면 롤백하는 방식을 승인했다. `tools/promote_gundam_terminology_candidate.py`가 후보를 다시 빌드·독립 감사한 뒤 TIP/TBL/한글 marker 메타데이터를 한 트랜잭션으로 승격했다.

- 1차 승격 후 메인 SHA-256: `2FA34B87F1C975291C8BD60AFA7DF7FD4A92983FB84296F6216E01AD1F5FAFEF`
- 설치 marker: `EC8D`
- 승격 후 독립 감사: 활성 소스/사전/렌더 **0 / 0 / 0**, `status=clean`
- false segmented-pointer (`5D–75`): **0건**
- live SaveRAM: 승격 전후 byte-identical
- 롤백 세트: `out/patch/backup/20260808_210528_pre_gundam_terminology/`
- 승격 보고서: `out/patch/gundam_terminology_promotion_report.json`

### 중복 TBL raw-code 보존 핫픽스

1차 승격 실측에서 갸프랑의 MA 아이콘 좌/우 타일이 `E6C5 E6C9`가 아니라 `E6C5 E6C5`로
평탄화된 사실을 확인했다. 원인은 `E6C5`, `E6C9`, `E736`이 TBL 감사 표현에서는 모두 같은 `█`로
디코드되어, decode→replace→encode 시 첫 번째 매핑으로 재인코딩됐기 때문이다. 텍스트가 같다는 사실만으로
raw 코드가 같다고 취급하면 안 된다.

전수 raw 감사 결과 영향 범위는 8개 논리 항목, 실제 복원은 15바이트였다. 빌더는 이제 **같은 표시문자에
복수의 TBL 코드가 존재하면 부모 엔트리의 해당 raw-code 순서를 그대로 보존**한다. 또한
`tools/audit_ambiguous_tbl_code_preservation.py`로 부모/후보의 중복 매핑 코드 정체성을 독립 비교한다.

2026-08-08 21:23 ROM-only 핫픽스를 메인에 승격했다.

- 현재 메인 SHA-256: `B192AD1ED2E24B709BFA14E5AE7D72405E58A3EAC8AE746F41864961148D2746`
- 변경량: raw UI 타일 **15바이트** + checksum **2바이트**
- raw-code 감사: 검사 750개 항목, mismatch **0**
- 명칭 감사: 활성 소스/사전/렌더 **0 / 0 / 0**
- false segmented-pointer (`5D–75`): **0건**
- marker `EC8D`, TBL, 한글 맵, live SaveRAM은 모두 불변
- 롤백 ROM: `out/patch/backup/20260808_212338_pre_gundam_icon_code_hotfix/monoeye_ko_expanded.wsc`
- 승격 보고서: `out/patch/gundam_icon_code_hotfix_promotion_report.json`

현재 상태는 **raw-code 핫픽스까지 메인 승격 완료 / 사용자 에뮬레이터 재실측 대기**다.

## 6.1 2026-08-13 최신 용어집 재동기화 후속 승격

대량 시나리오 재베이스 뒤 일부 구표기가 현재 메인 사전에 다시 유입된 것을 확인해 최신
`main_translation_glossary_ko.json`과 본 표준을 다시 결속했다.

- 확장 활성 소스 전수 검사: 61건 / 사전 134건 / 확인 가능한 렌더 레코드 11건
- 승격 후: 활성 소스 0건 / 사전 0건 / 렌더 레코드 0건
- stock 물리 그룹 9개, ext3 물리 그룹 125개 수정
- 부모 대비 1,992바이트 / 255 runs, allowlist 밖 변경 0
- 중복 TBL raw-code 733항목 검사, mismatch 0
- false segmented-pointer 0건
- 메인 SHA-256 `4E1453F0D6BC1AD7BE1431B617BE8DA772104F1A9A49D31261897ACD332584DB`
- WonderSwan checksum `EFBF`

`엔드리스 월츠`는 일반 표준이다. 다만 bank 1C 도감 고정 문구는 3바이트 성장 공간이 없고
같은 bank에 검증 가능한 dead/중복 저장소도 없어, 음역을 바로잡은 붙여쓰기 `엔드리스월츠`를
폭 압축 예외로 허용한다.

향후 대량 시나리오 재베이스에서는
`data/main_translation_terminology_overrides_ko.json`의 130개 주소별 override를 검수 CSV 뒤에
적용한다. 원본 검수 CSV와 과거 증거 열은 변경하지 않으며, 14,374행 전체에서 20셀 위반 0을
확인했다. `aux_body`, `bank59_enc5c_name75`, 20셀 배치 및 readability output도 활성 소스
감사 범위에 포함해 재생성 경로의 금지 표기 0건을 확인했다.

## 7. 향후 재발 방지 절차

명칭/번역 데이터를 추가 또는 수정한 뒤에는 최소 다음 순서를 지킨다.

1. `data/gundam_terminology_standard_ko.json`에 표준/금지 표기를 먼저 반영한다.
2. 활성 원천 데이터를 수정한다. 과거 `source_text`/`before` 증거를 정리 목적으로 덮지 않는다.
3. `tools/audit_gundam_terminology_standard.py`로 활성 소스 금지 표기 0을 확인한다.
4. 현재 메인에서 `tools/build_gundam_terminology_candidate.py`로 별도 후보를 만든다.
5. 후보 ROM + 후보 TBL을 다시 독립 감사해 source/dictionary/rendered 모두 0을 확인한다.
6. `tools/audit_ambiguous_tbl_code_preservation.py`로 중복 TBL 표시문자의 부모→후보 raw-code mismatch가 **0**인지 확인한다. `█`처럼 같은 감사 문자를 여러 코드가 공유하는 경우 렌더 문자열 동일성만으로 통과시키지 않는다.
7. 원칙적으로 에뮬레이터 실측 승인 뒤 메인 TIP을 승격한다. 사용자가 명시적으로 먼저 메인 승격 후 실측을 승인한 경우에는 TIP/TBL/marker 메타데이터 전체 롤백 세트를 만든 뒤 트랜잭션으로 승격한다.
