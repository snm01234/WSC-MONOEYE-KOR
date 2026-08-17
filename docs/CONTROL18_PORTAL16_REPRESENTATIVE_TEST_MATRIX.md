# Control18 E504 portal16 대표 실측 매트릭스

후보 ROM: `out/patch/control18_portal16_representative_probe_candidate.wsc`  
SHA-256: `DC2881DB6F00E08EFCDFFA4B11BE39CEB3C870B30C64BCDD5184A2240B8DE2A0`  
paired SaveRAM: `sram/control18_portal16_representative_probe_candidate.sav`  
SaveRAM SHA-256: `9E9CA06C74EF14F2FDD010BFEF3FDE527644B3EEAAD8A5A90E18BE919BB97F3C`

이 후보는 메인 TIP을 수정하지 않는다. 새 `E504` portal16 runtime을 한 번만 설치하고 아래 4개의 continuation body만 portal16으로 전환한다.

> **실측 결과(2026-08-17): #1 FAIL / #2·#3·#4 PASS.** 재분석 결과 #1 `60BB48`은 structural `18` 사례가 아니라 single-NUL 뒤의 실제 일본어 글자 `こ`(`ことなんですか！？`)였다. 따라서 #1은 E504 structural-prefix bulk 규칙에서 제외하며, 이 후보는 #2~#4의 double-NUL structural `18` 검증 근거로만 사용한다.

## A. STAGE4 하만 — 사용자 실측 기준점 `60BB48`

변경:

- before: `18 E5 18 72 3C 01`
- after: `18 E5 04 01 01 01`
- helper: `27:2000 = E5 18 72 3C 00`

확인 문맥:

1. `그건 샤아 대령님을 좋아한다는`
2. `뜻입니까！？`

판정:

- `こ뜻입니까！？`가 아니라 **`뜻입니까！？`**만 출력되어야 한다.
- 두 줄의 연결, 초상, 이후 이벤트 진행이 기존 정상 후보와 같아야 한다.

## B. 같은 STAGE4 — 즉시 `08 0A` 제어 인접 `60B449`

변경:

- before: `18 E5 18 93 10 ...`
- after: `18 E5 04 02 01 ...`
- helper: `27:2005 = E5 18 93 10 00`
- 직후 구조 제어: `08 0A` byte-exact 유지

확인 문맥:

1. `납득해 주는 법이지。`
2. `과거의 나도 그랬다。`
3. `하아、 그런 겁니까……`

판정:

- 두 번째 문장 앞에 `こ`가 붙지 않아야 한다.
- `08 0A`가 글자로 노출되지 않아야 한다.
- 다음 화자/초상 전환과 이벤트 진행이 정상이어야 한다.

## C. 필 교신 — 과거 선두 `18` 삭제 시 페이지 병합이 발생했던 `6017FC / 601826`

변경:

- `6017FC`: `18 E5 18 B1 FE ...` → `18 E5 04 03 01 ...`
- helper: `27:200A = E5 18 B1 FE 00`
- `601826`: `18 E5 18 3B DF ...` → `18 E5 04 04 01 ...`
- helper: `27:200F = E5 18 3B DF 00`
- 두 주소 모두 structural `18` 보존

확인 문맥:

1. **`디아나 님！`에서 독립 페이지/문장이 끝나야 한다.**
2. 다음 페이지에서 `저희들은 지구만을 생각하고、 달을`
3. `등한시하는 폐하의 뜻에는……`
4. `따라갈 수 없다고 말씀드렸습니다！！`
5. pause 후 `그렇게까지 고민하고 있었나……！`

판정:

- `디아나 님！`과 다음 문장이 한 페이지로 합쳐지면 FAIL.
- `こ저희들은...`, `こ따라갈...`, `亻` 등 제어/글리프 노출이 없어야 한다.
- `601826` 뒤 `08 45`와 pause, 후속 초상/이벤트가 정상이어야 한다.

## D. 기존 E51D 회귀 확인

새 dispatcher가 기존 E51D fixed/parameterized 분기도 함께 담당하므로 최소 한 번씩 확인한다.

- `61035E` 가토 `가토오오오！！` 구간 — 기존 parameterized E51D 대표
- `638CD5` 웃소/카테지나 `……어？` 구간 — 기존 fixed E51D 대표

판정:

- 과거 실측 PASS와 동일하게 문구와 이벤트가 정상 진행되어야 한다.

## 승격/대량 적용 기준

이 후보 자체는 메인 승격 대상이 아니라 **portal16 구조 검증용**이다. A/B/C/D가 모두 PASS하면 다음 단계에서 현재 worklist의 `18 + direct E518` 2,746건을 대상으로:

- ordinary native로 정확 복구 가능한 2건은 native route 사용
- 나머지 2,744건은 `E504` portal16 사용
- 2,685개 고유 helper를 bank27:2000부터 5바이트 고정폭으로 배치
- structural `18`, record extent, terminator, NUL/page boundary, 직후 `08/17` control은 byte-exact 보존

하는 일괄 후보를 생성한다.
