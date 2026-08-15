# 이벤트/데이터 뱅크(64–69) 침범과 순차 워크 밀림

작성: 2026-07-27
대상: `out/patch/monoeye_ko_eventfix_work.wsc` (팁 `B037`에서 파생 → `A473`)

> 이 문서는 팁 승격 전에 `PATCH_PROGRESS.md`로 접어 넣을 것. 작성 시점에
> 다른 세션이 `PATCH_PROGRESS.md`와 팁을 동시에 쓰고 있어 별도 파일로 뒀다.

버그 2건을 실측으로 잡았다. 둘 다 **게이트 사각지대**라 기존
`verify_stock_noninvasion` / `verify_nondialogue_text`가 통과시켰다.

| 증상 | 근본 원인 | 조치 |
|---|---|---|
| 3스테이지에서 이벤트가 발생하지 않고 프리즈 | free-space 재배치기의 far-pointer **오탐**이 이벤트 스트림/테이블에 3바이트씩 덮어썼다 | 뱅크 64–69를 원본과 바이트 동일하게 복원 + 탐색기 포맷 수정 |
| 유닛 강화 화면에서 유닛명 옆 아이콘이 옆 칸 것으로 표시 | `apply_ui_inplace.py`가 짧아진 레코드를 `0x00`으로 채워 종료자가 앞으로 당겨지고 **팬텀 빈 레코드**가 생겨 bank 75 UI 라벨 테이블의 순차 워크가 밀렸다 | 패딩을 `0x01`(전각 공백)로 바꿔 종료자를 원위치 고정 |

---

## 1. 게임의 far pointer 규약 (측정값)

이 카트는 뱅크 레지스터 값을 그대로 저장한다. 16 MiB 레이아웃에서 스톡 논리
뱅크 `S`의 레지스터 값은 `(stock_base >> 16) + S` = `0x80 + S`다.

| 사이트 | 원본 바이트 | 해석 |
|---|---|---|
| `66:1322` | `b2 13 e6 00` | `off16=0x13B2`, bank `0xE6` → `66:13B2` = `ＳＴＧ１５Ｔオ－プニング` |
| `65:6025` | `bc 60 e5 00` | `off16=0x60BC`, bank `0xE5` → `65:60BC` = `ネオ・ジオン撒退` |
| `64:4458` | `61 44 e4` | `off16=0x4461`, bank `0xE4` → `64:4461` (이벤트 스트림 내부 분기) |

즉 형태는 `oo oo bb [00]`이고 `bb`는 **`0x6x`가 아니라 `0xEx`**다. 8 MiB 원본에서
논리 뱅크 `0x61`을 가리키는 bare `0x61` 뱅크 바이트는 존재할 수 없다.

`rebuild_script_banks.discover_pointer_hits`는 세 형태를 bare 뱅크 번호로 찾고
있었다.

| 형태 | 조건 | 판정 |
|---|---|---|
| `off16_seg8` | `oo oo ss` (`ss` == bare 뱅크) | 뱅크 바이트를 `0x80+seg`로 고쳐 **유지** |
| `off16_00_seg8` | `oo oo 00 ss` | **제거** — 게임 형태는 `oo oo bb 00`으로 순서가 반대고, `off16_seg8`가 이미 포함한다 |
| `seg8_off16` | `ss oo oo` | **제거** — ROM에 실측 사례 0건. 히트 전부 우연 |

우연 일치의 실증 (원본 바이트):

- `61:84E3` `18 f2 60 07 35 e0` — **대사 페이로드 중간**. 여기에
  `30 00 00`을 쓰면 `0x00`이 종료자가 되어 `61:84E1`
  `こねぇ、お兄ちゃん、あそぼ！！`가 `こＯＳ`로 잘린다.
- `68:2747` `c0 66 66 64 60 66 66 66` — **4bpp 타일 데이터**.
- `64:4458` `15 19 61 44 e4 00` — 이벤트 스트림. 도구의 프레이밍이 게임의 진짜
  포인터 `61 44 e4`(=`64:4461`)와 **겹쳐서**, 재지정 후 뱅크 바이트가 `0x06`이
  됐다. 인터프리터가 없는 곳으로 점프 → **3스테이지 프리즈**.

팁 실측 32건: `61:84E3` 1건 + 뱅크 64–69 31건.
스캐너: `tools/scan_false_segptr_writes.py` → `out/patch/false_segptr_writes.json`.

## 2. 뱅크 64–69는 스테이지 이벤트 테이블이다

구조는 `(이벤트명 포인터, 이벤트 바디 포인터)` 페어 배열이고, 각 스테이지 블록
끝에 `ＳＴＧnn…オ－プニング` 헤더 레코드가 붙는다. 헤더 40개의 위치가 스테이지
블록의 경계다.

```
66:1322 -> 66:13B2 'ＳＴＧ１５Ｔオ－プニング'
[  2] -> 66:13C5 '自軍全滅'        [  6] -> 66:13D1 'ゼクス登場'
[ 12] -> 66:13E3 '裏切りのレコア１'  [ 14] -> 66:13EC 'シャアとレコア戦闘前'
```

3스테이지 테이블은 `64:4F79`(헤더 `64:4FF1` = `ＳＴＧ３<E62F>オ－プニング`),
바디는 `64:3300`–`64:43B6`에 있다. `64:4458`은 그 블록 안이다.

추가로 뱅크 66/67에는 **구 마커 `E3DB` + 토큰 + `0x01` 패딩**이 2,788 B 남아
있었다. `original_to_pre_ext3`에 이미 있던 것이라 ext3 세대의 "64–69 되돌림"이
건드리지 않았다. 파괴 대상은 STG15T/15N, 16T/16N, 17T/17N, 19(後),
20(前編/後編), 21T/21N 테이블이다(헤더 `6613B2`, `6630F9`, `66495E`, `666876`,
`668E44`, `66A107`, `671A18`, `6789D6`, `679538`, `67AEC0`, `67C0A2`).

**뱅크 64/65/68/69의 변경분은 전부 3바이트 오탐뿐이고 66/67도 오탐 + `E3DB`
침범뿐**이므로, 64–69 전체 복원으로 잃는 한글은 0이다. 재배치된 한글 사본은
확장 뱅크에 그대로 남고 참조만 끊긴다(해당 줄만 일본어로 복귀).

## 3. 순차 워크 밀림 (bank 75 UI 라벨 테이블)

`0x75B690+`는 back-to-back NUL 종료 zstring 테이블이다. `apply_ui_inplace.py`가

```python
patch = enc + b"\x00" + bytes(span - need)   # 종료자가 앞으로 당겨진다
```

로 써서 `75B6A6` / `75B7C5` / `75B7CD` / `75BA40` 네 레코드가 짧아지고
`75B6AD` / `75B7CC` / `75B7D4`에 **팬텀 빈 레코드**가 생겼다.
`0x75B690–0x75B7F0` 워크 결과 원본 48 → 팁 51 레코드, `75B6AE` 이후 전 항목이
+1(이후 +2, +3) 밀린다.

밀린 구간에 아이콘 레코드가 있다.

```
[ 7] 75B6C8 '<E598>'   [ 8] 75B6CB '<E646>'   [ 9] 75B6CE 'ＶＰ'
[23] 75B716 'ＭＡＰ<E62F>ＳＥＬＥＣＴ'
```

`<E598>`/`<E646>`는 단일 코드 1글자 레코드, 즉 아이콘이다. 인덱스가 밀려 옆
아이콘이 찍히고, `ＭＡＰ…`처럼 긴 라벨이 2칸 필드에 들어오면 `ＭＡ`만 잘려
보인다.

수정: 원본 길이까지 `0x01`(전각 공백)로 채운 **뒤** 종료자를 둔다
(`apply_safe_unit.padded_token_payload`·`apply_name75_ko`와 같은 규칙).
사전 문구 데이터(`5F:3662–5F:99B9`)만 예외로 `0x00`을 유지한다 — 거기서
전각 공백은 그 조각을 합성하는 모든 문자열에 끼어든다. 쓰기 후 종료자가 원위치가
아니면 일본어로 롤백하고 `terminator_moved`로 보고한다.

---

## 4. 왜 게이트가 못 잡았나 (수정 포함)

| 게이트 | 사각지대 | 수정 |
|---|---|---|
| `diff_stock_3way` | `DIALOGUE_HI = 0x69FFFF`. 적용 대역 상한은 `0x63FFFF`로 줄었는데 이 상수가 안 따라와, 뱅크 64–69의 모든 변경이 `dialogue_record` → `INTENDED_APPROVED`로 자동 승인됐다 | `DIALOGUE_HI = 0x63FFFF`, `UNINTENDED_BAND_NAMES`에 `(0x64, 0x69, "data_table_bank_64_69")` |
| `verify_stock_noninvasion` | `out_of_band`가 `0x6040A5` **아래**만 셌다 | 새 카테고리를 포함해 대역 위쪽도 센다 |
| `verify_nondialogue_text` | 스캔 범위가 aux 50–5F + 76 과 `NAME75_RANGES`(`75C000+`)뿐. `75B6xx`는 **어디에도 없었다**. 게다가 그 5개 주소가 `UI_APPROVED`라 check(iii) 길이/종료자까지 면제됐다 | `UI_TABLE_RANGES = ((0x75B000, 0x75C000),)`를 check(iii) 전용 워크에 추가. 길이/종료자 waiver는 `GRAPHICS_APPROVED_RANGES`(인터미션 라벨 타일)로만 한정 — 실제 레코드는 절대 면제하지 않는다 |

**원칙: 길이·종료자 면제는 "레코드가 아닌 것"(그래픽 셀)에만 준다.**
바이트가 바뀐 사실은 면제해도, 종료자가 움직인 사실은 면제하면 안 된다.

## 5. 재현 절차

```powershell
# 진단 (읽기 전용)
python tools/scan_false_segptr_writes.py --target <rom> --lo-bank 0x60 --hi-bank 0x6B

# 복원: 뱅크 64-69 원본 동일 + 60-63의 오탐 사이트
python tools/repair_data_bank_invasion.py --target <rom>            # dry-run
python tools/repair_data_bank_invasion.py --target <rom> --commit

# UI 인플레이스 재적용 (0x01 패딩)
python tools/apply_ui_inplace.py --rom <rom> --out-rom <rom> `
  --out-report out/patch/ui_inplace_report.json

# 게이트
python tools/verify_stock_noninvasion.py --target <rom> --out <report>
python tools/verify_nondialogue_text.py  --target <rom> --out <report>
python tools/verify_all_stages_smoke.py  --rom <rom> --report <report>
```

`repair_data_bank_invasion.py`의 fail-closed: 16 MiB 아님·`stock_base` 기하 불일치
거부, 60–63 사이트는 **원본 레코드 경계** 기준으로 그 레코드가 3바이트 사이트
밖에서 원본과 다르면 거부(정당하게 재작성된 레코드를 되돌려 한글을 깨뜨리지
않기 위함), `--dry-run` 기본, `--commit` 시 `out/patch/backup/<timestamp>/` 백업.

## 6. 측정 결과 (작업 ROM `A473`)

| 게이트 | 결과 |
|---|---|
| `verify_stock_noninvasion` | **PASS** · UNINTENDED **0 B** · out-of-band 60–69 **0 B** · 5F 포인터 565 이동 전부 설명됨 |
| `verify_nondialogue_text` | **ok** · check(i) 0 · check(ii) 0 · check(iii) **0 위반 / 62,201 레코드** |
| `verify_all_stages_smoke` | `overall_ok true` · `jagd_ok true` · `unit_banks_clean true` · violation 0 B |
| 뱅크 64–69 | 원본과 **바이트 동일** (residual 0) |
| `ＳＴＧ` 헤더 40개 | 전부 intact |
| `scan_false_segptr_writes` | **0건** |
| bank 75 순차 워크 | `75B600–75BB00` 원본 163 == 작업본 163, 오프셋 동일 |
| bank 5F UI 워크 | `5F2E00–5F3000` 51 == 51, 오프셋 동일 |

**미검증:** 3스테이지 이벤트 실제 발생과 강화 화면 아이콘은 **에뮬레이터 실측이
필요하다.** 정적으로는 두 원인 모두 제거됐고 해당 바이트가 원본과 동일하다는
것까지만 확인했다.

## 7. 남은 과제

- **팁 승격 미실시.** 다른 세션이 팁(`monoeye_ko_expanded.wsc`)에 aux 전투대사를
  직접 쓰고 있어(23:03 `B037`), 승격은 그 작업이 끝난 뒤 §5 절차를 그 시점 팁에
  다시 돌리는 방식으로 해야 한다. 작업 ROM을 그대로 덮으면 그쪽 변경이 날아간다.
- **재배치 커버리지 손실.** `seg8_off16`/`off16_00_seg8` 제거와 64–69 deny로
  free-space 재배치가 찾는 포인터가 줄어든다. 줄어든 만큼은 원래 우연 일치였고,
  64–69에서만 참조되는 레코드는 이제 일본어로 남는다(fail-closed). 이벤트 구동
  대사를 한글화하려면 `off16_seg8`(`oo oo bb`, `bb = 0x80+seg`) 형태로 진짜
  포인터를 찾은 뒤, 그 사이트가 64–69에 있어도 되도록 게이트에 **증거 기반
  allowlist**를 추가하는 별도 작업이 필요하다.
- 이 문서를 `PATCH_PROGRESS.md`와 `docs/DICT_INVASION_GUARD.md` §1(침범 유형)에
  접어 넣을 것. 새 유형 2개: **G. bare 세그먼트 far-pointer 오탐**,
  **H. NUL 패딩으로 인한 순차 워크 밀림**.

---

# 부록 — 강화 아이콘의 진짜 원인 (2차, 실측으로 확정)

§3의 순차 워크 밀림은 실재하는 결함이었고 고쳤지만, **강화 아이콘 증상의 원인은
아니었다.** 에뮬 실측에서 증상이 그대로였다. 진짜 원인은 **tbl의 역방향 매핑이
서로 다른 코드를 한 글자로 뭉갠 것**이다.

## 원인

`monoeye.tbl`은 서로 다른 ROM 코드 여러 개를 같은 유니코드 문자로 디코드한다.
추출기가 이름을 못 붙인 글자에 같은 자리표시자를 준 결과다.

```
'█' <- E6C5, E6C9, E736     ->  Tbl.char_to_code['█'] == E6C5
'ｅ' <- E5A1, E63B, E63E, E641, E72A, E730
'Ｆ' <- E2B3, E721   ·   'Ｒ' <- E483, E63D   ·   '◎' <- E60B, E60D   (총 23개 그룹)
```

`Tbl.char_to_code`는 역방향 맵이라 그룹을 **가장 낮은 코드로 접어버린다**. 그래서
레코드를 텍스트로 디코드한 뒤 다시 인코드하는 모든 경로는, 원본이 최저 코드가 아닌
코드를 썼을 때 **글리프를 조용히 바꿔놓는다**.

`'█'` 그룹은 장식 문자가 아니라 **유닛 리스트에서 이름 바로 뒤에 찍히는 상태
아이콘 계열**이다. 그래서 "강화 아이콘이 깨진 ＭＡ 아이콘으로 보인다"로 나타났다.

팁 실측 — name75 레코드 181개가 `'█'` 계열 코드를 갖고 있었고 전부 틀어져 있었다.

| 원본 코드 | 팁에 쓰인 코드 | 레코드 |
|---|---|---:|
| `E736` | `E6C5` | 155 |
| `E6C5, E6C9` | `E6C5, E6C5` | 13 |
| `E6C5, E6C9, E736` | `E6C5, E6C5, E6C5` | 13 |

`compose_name75_catalog.py`가 기준 번역에 `'█'`를 **문자로** 재부착하고
`apply_name75_ko.py`가 그걸 인코드하면서 `E6C5`가 나갔다. PATCH_PROGRESS의
"`█`(랭크 마커)는 기준 번역에 그대로 재부착한다"가 바로 이 손실 지점이다 —
**문자를 재부착하면 안 되고 바이트를 재부착해야 한다.**

## 조치

`tools/tbl_code_prefs.py` (신규, 공용)

| 함수 | 역할 |
|---|---|
| `ambiguous_chars(tbl)` | 코드가 2개 이상인 문자 → 코드 목록 |
| `marker_codes(tbl)` | `'█'` 계열 코드 집합 (하드코딩 아님, tbl에서 읽음) |
| `flatten_codes(payload, dic)` | 사전 토큰을 문구 바이트로 펼친다 — 아이콘 코드가 인라인이 아니라 스톡 사전 문구를 거쳐 들어오는 경우가 있다 |
| `retag_with_original_codes(ko, flat, tbl)` | ko의 n번째 모호 문자를 원본의 n번째 코드로 `<XXXX>` 이스케이프로 고정 |

`normalize_ko_text.encode_ko_text`가 이미 `<XXXX>` 이스케이프를 이해하므로 새 인코더
경로는 필요 없었다.

`tools/apply_name75_ko.py`

- 인코드 전에 `retag_with_original_codes`로 그 레코드 자신의 코드에 고정한다.
- **fail-closed**: 인코드 결과의 `'█'` 계열 코드 나열이 원본과 다르면
  `marker_code_lost`로 건너뛴다. 라운드트립 디코드 검증은 이 결함을 절대 못 잡는다 —
  `E6C5`와 `E736`이 **같은 문자로 디코드**되기 때문이다. 그래서 바이트 수준 검사가
  따로 필요하다.
- 리포트에 레코드별 `code_prefs`를 남긴다.

`tools/repair_name75_marker_codes.py` (신규)

이미 적용된 레코드는 재적용할 수 없다(적용기가 원본 바이트가 아닌 레코드를
`already_changed`로 거부). 코드 폭이 같으니 ext3 **문구 안에서 2바이트만 제자리
치환**한다. 문구 길이·레코드 바디·포인터 테이블은 건드리지 않는다.

fail-closed: 16 MiB·ext3 설치 확인, 대상 레코드가 `E5 18 xx yy` + `0x01` 패딩이
아니면 거부, 문구의 아이콘 코드 **개수**가 원본과 다르면 위치 대응이 증명되지 않으므로
거부, 한 ext3 인덱스를 서로 다른 코드가 필요한 레코드가 공유하면 거부,
`--dry-run` 기본 · `--commit` 시 백업 · 쓰기 후 재검증.

```powershell
python tools/repair_name75_marker_codes.py --target <rom>            # dry-run
python tools/repair_name75_marker_codes.py --target <rom> --commit
```

## 측정 결과 (작업 ROM `A473` → `47AB`)

| 항목 | 값 |
|---|---|
| 복구한 문구 | **181** · 재작성 코드 **194** (`E6C5→E736` 168, `E6C5→E6C9` 26) |
| 거부 | 0 · 공유 인덱스 충돌 0 · 사후 검증 실패 0 |
| 재실행 | 멱등 — 2회차 `already correct 181 / repaired 0` |
| aux(51/53/54/59/5C/5D/5E/76) 같은 결함 | **0건** — 다른 세션의 전투대사 패스는 영향 없음 |
| `verify_stock_noninvasion` | PASS · UNINTENDED 0 B (문구는 확장 뱅크라 스톡 diff 불변) |
| `verify_nondialogue_text` | ok · check i/ii/iii 전부 0 |
| `verify_all_stages_smoke` | `overall_ok true` · `unit_banks_clean true` |

## 교훈

**손실 있는 디코드를 거친 텍스트를 다시 인코드하지 말 것.** 코드→문자가 다대일이면
문자→코드는 정보를 잃는다. 라운드트립 검증(디코드 비교)은 이 결함에 **구조적으로
눈이 멀어** 있다. 재인코드가 필요하면 원본 레코드의 코드를 근거로 고정하고,
검증은 **바이트 수준**에서 해야 한다.

`docs/DICT_INVASION_GUARD.md` §1에 추가할 유형: **I. 다대일 tbl 역매핑으로 인한
글리프 치환** (`'█'` 아이콘 계열 → 강화 유닛 아이콘 오표시).
