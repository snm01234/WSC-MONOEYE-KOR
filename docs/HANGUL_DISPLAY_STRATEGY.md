# 한글 표시 전략 재검토 (2026-07-13)

## 1. 확정된 사실 (BizHawk)

| # | 실험 | 결과 |
|---|---|---|
| A | 원본 ROM | 정상 진행 |
| B | 스크립트+사전만 (폰트 원본) `07_script_only` | **1스테이지까지 통과**. 오프닝 한글 코드는 있으나 글리프가 JP라 글자 형태는 깨짐/일본어 모양 |
| C | E740+ nonempty 글리프를 한글로 덮어씀 (초기 시드) | **오프닝에서 한글이 보임** → 나레이션 직후 JP 대화에서 **화면 깨짐/크래시** |
| D | JP 텍스트 usage==0 인 E740+ 만 덮어씀 (text-safe) | 여전히 직후 크래시 (nonempty UI 타일로 추정) |
| E | bank40 끝 FF 패딩 + 문자코드 `EE****` (tail-pad) | 진행 OK, **한글 안 보임** |
| F | E0–E7 빈(00/FF) 슬롯 8칸 (`e7-blank`, 상당수가 E740 미만) | 진행 OK, **한글 안 보임** (사용자 확인) |

### 결론 요약

1. **대사/사전 패치 경로(dict spill + 토큰 치환)는 안전**하고 게임 진행과 양립한다.
2. **한글이 화면에 그려지려면 문자 코드가 E740 근처(검증된 표시 경로)여야 한다.**  
   - `7A:0610`: `code >= E000` 이면 `index = code - DF20`, 오프셋 `40:0440 + index*16`.  
   - 과거 E740+ 덮어쓰기 시 **한글이 실제로 보였다** → 이 경로 자체는 유효.
3. **기존 nonempty 글리프 픽셀을 덮어쓰면 안 된다.** UI/맵이 같은 슬롯을 쓴다. 텍스트 usage==0 이어도 안전하지 않다.
4. **`EE****` / bank 끝 패딩에만 쓰고 코드를 EE로 두면 진행은 되지만 표시가 안 된다.**  
   (페이지/매핑 한계 또는 고인덱스 미사용으로 추정. 수식상 오프셋은 FA60이 나오나 실기에서 비가시.)
5. E740–E7FF 안에서 **완전히 빈 슬롯은 4개뿐** (`E78C/E78D/E799/E7C3`). 용량이 절대적으로 부족하고, F 실험도 실패해 “빈 슬롯만”으로는 실용 불가.

---

## 2. 아키텍처 복기

```
대사 바이트 ──(F0–FE 토큰)──► 사전(5F) KO 바이트열
                                      │
                                      ▼
                              문자 코드 (예: E740 = '가')
                                      │
                                      ▼
                         7A:0610  index = code - DF20
                                      │
                                      ▼
                         bank40: 0440 + index*16  (16B packed 2bpp)
                                      │
                                      ▼
                                   화면 16×16
```

- 단일 바이트 `00–DF`, 확장 `E0xx–E7xx`(실사용 표시), 사전 `F0–FE`.
- 길이 워커: lead `>= E0` 이면 2바이트(단 F0–FE는 사전).

---

## 3. 왜 지금까지 한글이 안 보였는가

| 시도 | 의도 | 실패 이유 |
|---|---|---|
| 초기 E740 대량 덮어쓰기 | 표시 | 표시는 됨, **진행 파괴** |
| text-safe usage0 | 진행+표시 | usage0 nonempty = **UI 글리프** → 진행 파괴 |
| tail-pad `EE****` | 진행+표시 | **표시 경로가 EE를 실효 렌더하지 않음** |
| e7-blank 8칸 | 진행+표시 | 상당수 코드가 **E740 미만**; 빈 E740 슬롯 부족; 실측 비가시 |

즉 “진행 가능한 폰트 쓰기”와 “보이는 E740 표시 경로”를 **동시에** 만족하는 슬롯이 ROM 기본 구조만으로는 거의 없다.

---

## 4. 한글 출력 방안 (우선순위)

### 방안 A — 폰트 로더 훅 (권장, 본질 해결)

**아이디어:** 대사/TBL은 계속 `E740+` 코드를 쓰고, **픽셀은 bank40 FF 패딩(또는 다른 여유 ROM)에 두며, `7A:0610`에서 Hangul 인덱스만 대체 베이스로 읽게 한다.**

- 장점: 표시 경로(E740) 유지 + UI 글리프 미훼손 + 패딩에 ~96자(1544B)까지 가능, 추가 뱅크면 더 확장
- 단점: 어셈블리 훅 필요, 세이브/체크섬/회귀 테스트 필요
- 구현 스케치 (`7A:0610` 직후):
  1. `cmp ax, E000` / `sub ax, DF20` 기존 유지 → `index` in AX  
  2. `index`가 Hangul 구간(`[0x820, 0x820+N)`)이면  
     `offset = 0xF9F8 + (index-0x820)*16`, 세그먼트는 기존 bank40 매핑과 동일  
  3. 아니면 기존 `0440 + index*16`
- PoC: N=8~16자만 훅 → 오프닝 한 줄이라도 한글 가시 + 1스테이지 회귀

### 방안 B — 빈 E740 슬롯만 (비권장, 용량 불가)

- E740–E7FF 빈 칸 4개. 전체 번역 불가. F에서 이미 비가시/실용성 없음.

### 방안 C — nonempty 덮어쓰기 후 UI 복구 (비권장)

- 어떤 글리프가 UI용인지 전부 지도화 후 안전 집합만 회수. 비용 크고 위험.

### 방안 D — 다른 뱅크에 폰트 테이블 신설 + far read

- A와 유사하나 훅이 더 큼. A 성공 후 확장용.

### 방안 E — 타일맵/이미지로 오프닝만 교체

- 오프닝 전용. 본편 대사에는 반복 불가. 임시 데모용.

---

## 5. 안전한 현재 베이스라인

| 파일 | 역할 |
|---|---|
| `out/patch/bisect/07_script_only_stage1_ok.wsc` | **진행 검증 완료** (폰트 원본 + 시드 대사) |
| `data/translations_seed.json` / `_textsafe` / `_e7blank` / `_tailpad` | 실험용 번역 데이터 |
| `tools/apply_translations.py` | 검증된 대사 삽입 |
| `tools/build_hangul_font.py` | 글리프 빌더 (훅 전까지 표시용으로 E740 덮어쓰기 금지) |

권장 작업 ROM: 당분간 **07_script_only** 를 expanded로 두고, 표시는 **방안 A PoC**로만 실험 ROM에서 검증.

---

## 6. 방안 A PoC 상태 (2026-07-13 → 2026-07-14)

### 6.1 표시 경로 정정

실제 대사/UI 블리터는 `7A:0618`이 아니라 **`7A:0513` 경로**다.

```
1A6E[pos] = glyph_index
7A:0513  mov dx,0440 / mov cx,3000
7A:0521  dx += index*16   ← 픽셀 far-read 직전
```

`7A:0618`은 보조 헬퍼라서 여기만 훅하면 진행은 되지만 한글은 안 보인다 (`bisect/08`).

### 6.2 primary 훅 실패 (뉴게임 소프트락)

| ROM | primary `7A0521` | secondary `7A0618` | 뉴게임 |
|---|---|---|---|
| `bisect/07_script_only` | 원본 | 원본 | **OK (1스테이지)** |
| `bisect/08_hook_pad_poc` | 원본 | 훅 | OK, 한글 비가시 |
| `bisect/09_primary_hook_pad_poc` | **훅** | 원본 | **실패 (사용자 확인)** |

원인: 훅이 `index ∈ [0x820,0x87F]` (= 코드 `E740–E79F`) 읽기를 패딩으로 돌린다.
해당 구간의 stock 글리프는 **96칸 중 93칸이 nonempty**이며 UI/공용이 이미 쓰고 있다.
픽셀을 덮어쓸 때와 같이, **같은 인덱스의 읽기만 바꿔도** 뉴게임 전환 UI가 깨지며 진행이 멈춘다.

분석 스크립트: `tools/analyze_newgame_softlock.py`

### 6.3 결론 (방안 A 수정)

- “E740 코드 + 패딩 픽셀 + 인덱스 리맵”은 **인덱스 공간이 UI와 충돌**해서 불가.
- 표시와 진행을 동시에 만족하려면 다음 중 하나가 필요하다.
  1. **UI가 쓰지 않는 인덱스 창**을 먼저 확보한 뒤 그 창만 리맵/기록
  2. UI 글리프를 다른 곳으로 대피시킨 뒤 E740 창을 한글로 회수
  3. 대화 전용 렌더 경로/플래그가 있을 때만 리맵 (미확인)

현재 플레이 가능 베이스: `bisect/07_script_only_stage1_ok.wsc`  
`09` / primary-훅 `monoeye_ko_seed`는 진단용이며 본선 아님.

### 6.4 UI 격리형 마커 PoC (2026-07-14)

UI 글리프를 물리적으로 복제·이동하는 대신 **출처 태깅**으로 격리한다.

```
KO 사전: E3DB E740  E3DB E741 ...
                  │
7A:073C / 0818    ├─ E3DB → WRAM 19FF=1 (소비, 글리프 없음)
                  └─ 다음 문자 → 06CE 디코드
7A:07A0 store     19FF=1 이면 SI|=8000 후 [bx+1A6E]=SI
7A:0521 blitter (via 7A:FFB5 far→7F:FC4C dual-pad primary):
  bit15=0 → 원본 CX=3000 DX=0440+index*16 (UI/JP 완전 유지)
  bit15=1, slot<96  → CX=3000 DX=F9F8+slot*16   (bank40 pad)
  bit15=1, slot>=96 → CX=2F00 DX=C5CE+(slot-96)*16 (bank3F pad, 가설)
```

핵심 차이는 숫자 `E740` 자체를 리맵하지 않는다는 점이다. 기존 UI가 같은
`0x820` 인덱스를 사용해도 태그가 없으므로 항상 원본 글리프를 읽는다.

구현:

- `tools/build_hangul_font.py --padding-store --padding-marker-code E3DB --padding-max 1027`
- `tools/apply_translations.py --hangul-marker E3DB`
- `tools/patch_font_hangul_hook.py`
  - primary logic: `7F:FC4C` (CS=`F000`, **≤64B** — `7F:FC8C` ext_dict P1 예약)
  - trampoline `7A:FFB5`
  - marker dispatch + store: `7A:FFBA…` (near-callable)
  - marker sites: `7A:073C`, `7A:0818`
- 정적 검증: `tools/verify_marked_hangul_hook.py`
- 산출물: `out/patch/bisect/10_marked_ui_isolation_poc.wsc`,
  `out/patch/monoeye_ko_marked.wsc` (1027자 dual-pad 베이스)

용량 (2026-07-16 P2 — pad2를 bank3F로 이전):

- bank40 `F9F8` 96 + bank3F `C5CE` 931 = **1027자** (마커 `E3DB` 유지, primary cave 63B로 P1과 공존)
- 구 pad2 bank41 `E4F4`(432)는 폐기 — cave를 키우지 않고 슬롯만 확장
- bank3F 창 세그먼트 `CX=2F00` = `(bank-0x10)<<8` 가설 (emu 미검증)
- 시트 unique Hangul 1186 중 고빈도 **1027** 수용 (잔여 ~159는 overflow)
- 삽입: `apply_safe_unit` cluster-unanimous + sole + reclaim-by-exclude
- 추가 삽입 병목은 **완전 커버되지 않은 공유 JP 슬롯** (부분 번역만으로 회수 불가)

정적 검증 결과:

- stock `E740–E79F` 글리프 96개 byte-identical
- 패딩 한글 96자 존재
- 29줄/한글 259회 모두 `EFF3` 마커 선행
- 마커 소비 및 tagged index→padding 주소 계산 일치
- untagged UI index→stock 주소 불변
- 체크섬 정상

**수동 실측 (2026-07-14, 오프닝 시드 `@6040A5…`):**  
대사 **문구는 바뀜**(dict 치환 OK)이나 화면 글자는 **한글로 식별되지 않고 점묘화/흩뿌린 도트**처럼 보임.

**진단 결론 (정적 재분석 후 훅 개정):**

1. 문구 변경 = 사전/스크립트 OK. 점묘화 = stock `E740` **UI 겸용 글리프**가 그려짐 (패딩 한글 미사용).
2. 구 태그 cave는 디코드 후 `[bp-8]/[si-4]`로 마커를 재확인했는데, 중첩 `06CE` 반환 타이밍에 의존해 **실기에서 bit15가 안 붙는 것**이 유력.
3. 메인 텍스트 진입은 `A000:07AC`(parser B)이고, 문자마다 `7A:0818 → 06CE`를 직접 호출한다. dict 확장은 `073C`를 타지만 태그 경로가 깨지면 동일 증상.
4. **수정 (flag-at-store):**  
   - `EFF3` 소비 시 WRAM `1A6D=1`  
   - `7A:07A0` store에서 플래그면 `SI |= 8000` 후 기록  
   - `073C` + `0818` 모두 dispatch 훅  
   - blitter `0521`는 기존처럼 bit15 → `40:F9F8`

재빌드 ROM: `out/patch/bisect/10_marked_ui_isolation_poc.wsc` (정적 PASS).  
수동 확인: 나레이션에서 `『사이드　３、독립　선언』` 가독 여부.  
선택: Lua `tools/bizhawk_hangul_tag_probe.lua` — `1A6E` 인덱스에 `T`(bit15) 표시.

이 PoC의 `monoeye_ko_seed.wsc`는 계속 `07_script_only`와 동일하게 유지한다.

---

## 8. PoC 이후 로드맵 (2026-07-14)

| 단계 | 목표 | 도구/산출물 | 상태 |
|---|---|---|---|
| A | 정적 안전성 (UI byte-identical, marker/padding) | `verify_marked_hangul_hook.py` | **PASS** |
| B | 런타임 진행 (부팅→뉴게임→**1스테이지**) | BizHawk 수동 (§8.1) | **PASS (2026-07-14)** — JP 표시, 소프트락 없음 |
| C | 한글 **가시** (오프닝~1스테이지 전: `@6040A5`…) | BizHawk 수동 | **대기** — 시드 교체 후 재빌드 |
| D | 개발 베이스 승격 | `promote_marked_poc.py` → `monoeye_ko_marked.wsc` | C 후 |
| E | 번역 확장 (29→배치) | `apply_safe_unit.py` on marked base | D 후 |
| F | 96자 초과 폰트 | 2nd padding bank / JP rare recycle + marker | E 병행 |

**수동 실측 (2026-07-14):** `10` ROM(구 시드 `@600005…`)으로 뉴게임→1스테이지까지 **일본어 그대로** 진행 확인.  
구 시드는 시트 abs 선두(Turn A 계열)라 초반에 한글이 안 보이는 것이 정상.  
**신규 시드:** `6040A5`–`604492`(사이드3 독립선언~시그/블레이드 대화) = 오프닝~1스테이지 이전 경로. KO는 96자 한도 맞추며 축약.

**지금 할 일 (C):**

1. `10` 재빌드 후 뉴게임→나레이션에서 **`『사이드　3,　독립　선언』`(`@6040A5`)** 등 한글 가시 확인
2. 1스테이지까지 진행 회귀 유지
3. 통과 시 `python tools/promote_marked_poc.py --skip-runtime-check` 로 개발 ROM 고정
4. `monoeye_ko_seed.wsc`(=07)는 회귀용 안전 ROM으로 유지

**하지 말 것:** C 통과 전 `monoeye_ko_seed.wsc`를 `10`으로 교체하지 않는다.  
에이전트/스크립트가 BizHawk를 자동 실행하지 않는다 (`--runtime` 비활성).  
`@600005`(`……뭐지？`)를 **초반/오프닝 한글 판정 조건으로 쓰지 않는다.**

### 8.1 수동 BizHawk 테스트 절차

**사전 준비 (에뮬 불필요):**

```powershell
$env:PYTHONIOENCODING='utf-8'
python tools/run_hook_pad_poc.py          # PoC 재빌드 (필요 시)
python tools/run_marked_poc_verify.py     # 정적 검증만 (--runtime 사용 금지)
```

**테스트 ROM:**

| 용도 | 경로 |
|---|---|
| 검증 대상 | `out/patch/bisect/10_marked_ui_isolation_poc.wsc` |
| 회귀/안전 | `out/patch/bisect/07_script_only_stage1_ok.wsc` 또는 `out/patch/monoeye_ko_seed.wsc` |

**BizHawk 설정:**

1. WonderSwan Color 코어로 위 ROM 로드
2. (선택) Tools → Lua Console → `tools/bizhawk_marked_poc_check.lua` 로드 — 진행 로그/스크린샷용. **자동 입력은 하지 않음.**

**체크리스트 — 진행 회귀 (B, 완료):**

| # | 확인 항목 | 기대 결과 | 실측 |
|---|---|---|---|
| 1 | 타이틀 부팅 | 로고·타이틀 정상 | PASS |
| 2 | Start → 메뉴 | `ニューゲーム` 등 **일본어 UI** | PASS |
| 3 | **뉴게임 선택 후 진행** | 소프트락 없이 다음 화면 | **PASS** |
| 4 | 프롤로그·나레이션·1스테이지 | JP 대사로 07과 동일 수준 진행 | **PASS** |

**체크리스트 — 한글 가시 (C):**

| # | 확인 항목 | 기대 결과 | 비고 |
|---|---|---|---|
| 5 | 나레이션 `@6040A5` | **`『사이드　３、독립　선언』`** 한글 | 오프닝~1스테이지 전 구간 선두 |
| 6 | 이어지는 나레이션·시그 대화 | 시드 줄 한글 유지 | `@604435` 시그 등장 등 |
| — | (구) `@600005` `……뭐지？` | **초반 판정에 사용 금지** | 시트 선두·후반 계열 |

**결과 기록:**

- 통과/실패를 `PATCH_PROGRESS.md` 또는 이 문서 §8 표에 반영

**C 통과 후 승격:**

```powershell
python tools/promote_marked_poc.py --skip-runtime-check
```

→ `out/patch/monoeye_ko_marked.wsc` 생성. `monoeye_ko_seed.wsc`는 07 유지.

타이틀/초기 메뉴의 폐기된 실험 기록은 현재 정본에 필요한 결론만
[`UI_MENU_NEXT_STEPS.md`](UI_MENU_NEXT_STEPS.md)에 통합했다.

---

## 7. 하지 말 것 (재확인)

- nonempty bank40 글리프 대량 덮어쓰기  
- 사전 전체 rebuild / bulk inplace  
- `EE****`만으로 “표시됐다”고 가정  
- usage==0 = 안전 가정  
- 가드 없는 spill (포인터 히트 없거나, 오프셋당 히트 과다 → 오탐 패치)
- bank64+ 저품질 시트 KO 맹목적 spill

---

## 8. 확장 진행 (2026-07-14, 오프라인)

**베이스:** `monoeye_ko_marked.wsc` → `apply_safe_unit` → spill bank `60–63`만

| 지표 | 값 |
|---|---|
| matching_old_abs | **1185** (+스티키 inplace) |
| 시드 decode | **fail=0** |
| 사전 free 슬롯 | **0** (sole/cluster로 소진) |
| spill 재배치 (60–63) | 포인터 가드 유지 |
| 글리프 | 1027 (marker `E3DB`, dual-pad bank40+3F, **sticky run**) |

**도구 안전장치**

- spill: 포인터 필수 + `MAX_SPILL_POINTER_HITS=16` (bank+0000 등 오탐 차단)
- `skipped_no_pointer`는 패치로 집계하지 않음
- KO 품질 필터 강화 (조사 선두·가나 잔여·저다양성)
- `run_spill_banks.py` 기본 범위 `60–63`

**병목:** 사전 free 슬롯 0 + 토큰 공간 상한(~3840) + 시퀀셜 대사는 spill 불가.

### 압축·시퀀셜 치환 실현성 (2026-07-14)

| 접근 | 실현성 | 실측 |
|---|---|---|
| 공유 구문 → body inplace | △ | 새 슬롯 256개 가정 시 fit≈3%, 1024개여도 ≈10%. **슬롯 고갈 상태에서는 사실상 불가** |
| dict 토큰 시퀀셜 치환 | ○ | 이미 본선. free=0이라 추가 배치 정체 |
| reclaim-by-exclude | △ | plain body 대사가 많아 제외해도 슬롯이 거의 안 빔 |
| **스티키 마커(런 단위)** | ◎ | 구현됨 (`store` cave + `hangul_marker_mode=run`). 마커 오버헤드 절감 |
| 스티키 inplace | ○ | 크기 보존으로 **수십 줄** 추가 가능 (abs 안정) |
| full-bank shift | △ | 용량만 맞으면 가능하나 **뱅크 전체 abs 이동** → 기본 비활성(`--allow-shift`) |

도구: `tools/apply_seq_unit.py` (스티키 업그레이드 + dict densify + inplace).

### 확장 사전 훅 PoC (2026-07-16)

| 항목 | 값 |
|---|---|
| 패치 사이트 | `7A:0700` (`mov ax, es:[si+7BCC]`) |
| 헬퍼 | `7F:FC8C` (Hangul primary 뒤), trampoline `7A:FFED` |
| 데이터 | bank `5E:E22B` 포인터 + 문구 |
| 슬롯 | **265** (index 3831–4095) — 토큰 하드캡 (`0xFFF`) 도달 |
| 적용 (265) | 265 unique → **1654줄**, matching **2828**, collisions_repaired=2 |
| 시드 | fail=0 |
| 토큰 상한 | index ≤ `0xFFF` → 확장 최대 **265** 슬롯 (더 이상은 포맷/훅 확장 필요) |

| 단계 | matching | lines_patched | repaired |
|---|---:|---:|---:|
| 64 | 2005 | 890 | 2 |
| 128 | 2356 | 1182 | 2 |
| 256 | 2801 | 1627 | 2 |
| **265** | **2828** | **1654** | **2** |

슬롯 증설 시 기존 ext ROM 위에 포인터만 늘리면 안 된다.  
`install_ext_dict_hook(..., force_format=True)` 또는 `apply_ext_dict_unit --slots N`  
(슬롯 증가 시 자동 force_format)으로 `5E:E22B+`를 비운 뒤 재할당한다.  
원샷: `python tools/run_ext_dict_expand.py 256`

도구: `tools/patch_ext_dictionary.py`, `tools/apply_ext_dict_unit.py`

### 실행 불가 사고 (2026-07-16) — 수정됨

1) `apply_seq_unit` sticky inplace가 시트 오탐 abs에서
`read_encoded_z`가 수 KB~수십 KB “가짜 레코드”를 읽은 뒤 body 잔여를
`00`으로 패딩 → bank `6A`/`6E` 대량 소거.
가드: `MAX_SAFE_RECORD_LEN` / inplace `60-63` / 큰 zero-pad 거부.

2) spill 포인터 검색이 `0x40–0x7F`를 훑으며 bank `74`/`78` 코드·그래픽
바이트를 far-pointer로 오인 후 재기록 → 부팅 즉시 깨짐(빨간 노이즈).
가드: 검색·패치를 `0x50–0x6F` (dict `5F` 제외)로 제한.

### 고유명사 우선 PoC (2026-07-16)

시트 전체 없이 bank 5F **정확 일치** 슬롯만 spill 치환
(시그/사이드/지온/샤아/아무로/디아나/건담/브라이트/모노아이 등 **14슬롯**).
도구: `tools/apply_proper_nouns.py` + `data/proper_nouns_ko.json`
시드 fail=0, 부팅 인트로 OK. 공유 토큰이라 JP 대사 중간에 한글 이름이 섞일 수 있음.

### 기체·유닛·파일럿 이름 (2026-07-16)

번역 시트(`60–6F` 대사)에는 유닛 데이터 테이블이 없다.  
기체/파일럿/함선/조직 표기는 **bank 5F 사전 슬롯**에 exact-match로 들어 있다.

| 항목 | 내용 |
|---|---|
| 분석 | `tools/mine_unit_names.py` → `out/script/unit_name_dict_candidates.*` |
| 목록 | `data/unit_names_ko.json` — MS/파일럿/함선/조직/용어 **125개** |
| 적용 | `apply_proper_nouns.py --names data/unit_names_ko.json --base-rom <원본>` |
| 산출 ROM | `out/patch/monoeye_ko_units.wsc` + `unit_names_report.json` |
| 검증 | decode_fail=0, seed_fail=0, encode_fail=[] |

**하지 말 것:** 대사 밖 뱅크 바이트 스캔 임의 패치(오탐). 메뉴 버튼은 `72:0000–17FF` 그래픽.

### 시스템·옵션·세이브·전투 UI (2026-07-16)

| 계층 | 소스 | 한글화 |
|---|---|---|
| UI 용어 사전 | 5F dict (`ＩＤ`/`コマンド`/`出撃`/`選択`…) | `data/ui_system_ko.json` → spill (**41슬롯**) |
| 옵션/도감/통신/세이브 안내 | **`5F:2E00+` 고정 문자열** + LE16 포인터(`5F:3500+`) | `data/ui_spill_ko.json` → `5F:ADC4+` spill (**60줄**) |
| 전투 HUD 라벨 | `75:B600+` | 일부 dict 공유로 간접 반영; 전용 spill은 후속 |
| 타이틀 뉴게임/계속/옵션 버튼 | `72:0000–17FF` | **그래픽 아틀라스** — 텍스트 치환 불가 |

파이프라인: `tools/run_ui_localize.py` → `out/patch/monoeye_ko_ui.wsc`  
도구: `mine_ui_strings.py`, `analyze_ui_blocks.py`, `apply_ui_spill.py`

검증: 옵션 메뉴 포인터 `5F:3540` → `옵션　메뉴` 디코드 OK.  
타이틀 버튼 그래픽·bank75 전투 전용 라벨·능력치 상세는 잔여 작업.

### P3 시트 KO 품질 정리 (2026-07-16)

P1(ext_dict)·P2(글리프)와 분리: ROM/폰트/훅은 건드리지 않음.

| 항목 | 내용 |
|---|---|
| 필터 | `tools/normalize_ko_text.py` `is_low_quality_ko` — Bing 메타·`<FF>`·학교 스캐폴드·조사 잔여·고빈도 스텁 강화. 짧은 실대사(`로라！`/`……그래。`)는 유지 |
| 감사 | `tools/audit_ko_quality.py` → `out/script/ko_quality_report.json` |
| 정제본 | `out/script/translations_quality.json` (저품질 KO 공백, 오버라이드 적용) |
| 수동 교정 | `data/ko_quality_overrides.json` — 오프닝 시드 26줄 + 일환/짧은 대사 등 |
| 병합 | `tools/apply_translate_cache.py` — 캐시 저품질 스킵 + abs 오버라이드 |

실측(전체 32710줄): ok≈18600 / low≈14100. 오프닝 밴드 `6040A5–605200`는 오버라이드 후 대부분 ok.

메뉴 UI는 [`UI_MENU_NEXT_STEPS.md`](UI_MENU_NEXT_STEPS.md) 참고.
`75:B7A4` 문자열·compact 폰트는 반증. 메뉴는 **그래픽**이며 핫스팟은
**`72:0000–17FF`** (2KB 이분으로 화면 변화 확인).

### 오프닝 첫 줄 JP / 둘째 줄 KO (2026-07-16)

**원인:** 시드가 제목(`6040A5`)·본문(`6040CB`)만 넣고, 사이 나레이션
`6040B5`(`08 xx 01 17 xx 18` + `ジオン・ズム・ダイクンの主導により`)를 누락.
`split_prefix_body`가 `01 17 18`을 본문으로 오인해 시트가 `がらこ…` 쓰레기로
보여 시드 후보에서 빠짐.

**조치:**
- `extract_script.split_prefix_body` — speaker 뒤 `01`+`17/18` 접두 인식
- `tools/patch_opening_narration.py` — interstitial 4줄(미사용 dict 슬롯) 적용
- densify가 시드 dict 슬롯을 each→run으로 줄이지 않도록 보호

화면에서 제목 다음 줄이 한국어로 나와야 함. 잔여 interstitial은 슬롯 확보 후 추가.

플레이 스모크에서 오프닝~1스테이지 전 대화 중 **이벤트에러 2573 51983**.

| 값 | hex | 해석 |
|---|---|---|
| 51983 | `0xCB0F` | **`65:CB0F`** — 이벤트/제어 바이트를 대세로 오인 → ext_dict가 `FF 7C`+제로패딩으로 파괴 |
| 2573 | `0x0A0D` | `69:0A0D` 레코드는 **미변경**. 부가 파라미터/다른 컨텍스트로 보임 |

**원인:** 오프닝 시드 26줄 부분 한글화가 아님(시드 decode 26/26 OK).  
시트 오분류 + ext_dict size-preserving 치환이 이벤트 스트림을 깨뜨림.

추가 핫스팟(2026-07-16 재발):
- bank69 `02 80 xx` 제어열이 시트에서 `…機な/を/は` 등으로 오분류 → ext 토큰으로 치환
- `69:0A19` 등 `0A0D` 주변이 깨지면 동일 이벤트 에러 재발
- `(xx 02)+ … 42` 짧은 바이너리도 `…ろ…` OCR 쓰레기로 오분류됨

시그/블레이드 대화 직후 크래시(2026-07-16):
- 원인: ext index **`0xF00`(3840)** 토큰이 **`FF 00`** → zstring NUL과 충돌
- 시드 다음 줄 `6044F9` `……そう。`가 `17 34 18 FF 00 | 00…`로 조기 종료되며 이벤트 스트림 파괴
- `dict_token_safe_in_zstring` / `padded_token_payload`가 trail `00` 거부
- `apply_ext_dict_unit`은 `(index & 0xFF)==0` 슬롯을 할당하지 않음
- `repair_ext_false_dialogue`가 `nul_token` 18건 marked 복구 (해당 줄은 일시 JP)

**조치:**
- `tools/event_record_heuristics.py` — `01 0C 01`, `02 80 xx`, paired-`02` 템플릿 차단
- `apply_ext_dict_unit` — marked 기준 event body abs 제외 + `0xF00` 슬롯 금지
- `repair_ext_false_dialogue.py` — marked 바이트 복구 (정상 KO 패드는 건드리지 않음)
- 복구 후: `65:CB0F`·`69:0A0D/0A19`·`6044F9` 정상, seed_fail=0
