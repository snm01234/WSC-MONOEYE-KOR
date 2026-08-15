# UI / 메뉴 / 기체·무장명 한글화

업데이트: 2026-08-02 (유닛 정보·능력치 UI 133건과 `무사아→무사이` 보정 메인 반영)

대사 시트 밖의 텍스트 — 인터미션·전투 메뉴, HUD 라벨, 도움말, 기체명, 무장명.

## 갱신 이유 (2026-07-19 판이 틀린 이유)

두 가지가 겹쳐 있었다.

1. **마커 불일치.** 설치된 한글 런 마커는 `EC80`인데 `data/*_ko.json` 10개가 모두
   `"marker": "E3DB"`를 들고 있었고, 적용기 4개가 그 값을 그대로 썼다. `E3DB`는
   문자 `映`의 코드다. 즉 이 파이프로 쓴 UI 한글은 **`映`이 찍히고 한글 런 플래그가
   서지 않는다**. 마커를 `EC80`으로 옮긴 뒤 카탈로그를 갱신하지 않아 생긴 결함이다.
2. **07-27 스톡 침범 복원이 UI를 되돌렸다.** 실측: 카탈로그 434개 적용가능 슬롯 중
   **427개가 스톡 일본어**, 1개만 KO였다. 07-19의 "exact-slot 100%"는 팁에 남아 있지 않다.

교훈: **커버리지는 주장하지 말고 측정한다.** `tools/audit_nondialogue_ko.py`가 그 측정이다.

## 소스 맵

| UI | ROM 소스 | 메커니즘 |
|---|---|---|
| 기체·파일럿·함선·조직명 | bank `5F` 사전 | 공유 슬롯 rewrite |
| UI/전투/메뉴 용어 | bank `5F` 사전 | 공유 슬롯 rewrite |
| 기체·무장 **표시 테이블** | `75C000–75E800` (name75) | ext3 또는 retired 2-byte token in-place |
| 유닛 정보/도감 이름 | `5C0000–5C78FF` | exact catalog record · ext3/retired token in-place |
| 유닛 능력치·상태 UI | `75B2F3–75B424` 중심 | explicit record · ext3/retired token in-place |
| 커스텀 파츠명 | `76FD0B–76FDDA` | 별도 bank76 테이블 · private ext3 token in-place |
| 미션 대사 · 능력 해설 · 전투 보이스 | aux 뱅크 `59` `5C` `5D` `5E` | ext3 토큰 in-place (`apply_aux_ko`) |
| 옵션·도감·세이브 안내문 | `5F` spill / `75:B7C5+` | inplace (size-preserving) |
| 타이틀 뉴게임/계속/옵션 **버튼** | `72:0080–7248FF` 4bpp | 그래픽 (별 트랙, 팁 미승격) |
| 인터미션 라벨 | bank `54` 오버레이 타일 | 그래픽 (**보류** — 포커스 잔상) |

## 2026-08-02 유닛 정보·능력치 UI 후속 및 `ムサイ` 오역 보정

name75 목록에서는 이미 한국어였던 `ガンキャノン`, `ヤクト・ド－ガ`가 별도의
bank 5C 유닛 정보/도감 테이블에서는 일본어로 남아 있었다. 유닛 상태 화면의
`75B3B7 運動性`, `75B3BD 移動力`, `75B3C1 防御力`, `75B3C5 限界反応`도 각각
`運動性`, `이동力`, `옛。力`, `限界반응`으로 출력됐다. `ムサイ` 공유 사전 slot
`06C3`은 카탈로그 오역 때문에 모든 소비 지점에서 `무사아`로 표시됐다.

현재 TIP과 정확 번역 카탈로그를 대조해 bank5C 유닛 정보 111건, bank75B 이름 3건,
명시적 능력치/상태 UI 19건의 직접 레코드 **133건**을 처리했다. 2/3바이트 레코드
19건은 strong retired non-FF stock slot, 4바이트 이상 114건은 union true-free
ext3 slot으로 길이·종료자를 보존해 치환했다. `ムサイ`는 공유 slot을 `무사이`로
교정하되 nested parent 0과 외부 소비 10건을 전수 검증했다.

| 항목 | 결과 |
|---|---|
| 현재 메인 TIP | SHA-256 `1161D11C5286D353F7BC9DB1BA879284641C5EA3ED8C8101383761F7B97ED77A` · checksum `7B5E` |
| 직접 레코드 | 133/133 exact · bank5C 111 + bank75B 이름 3 + 명시 UI 19 |
| 대표 UI | `변형`, `운동성`, `이동력`, `방어력`, `한계반응`, `명`, `탄`, `명중` |
| 대표 유닛 | `건캐논`, `야크트・도가`, `무사이 후기형` |
| 공유 보정 | `06C3 무사아→무사이` · 외부 소비 10 · nested parent 0 · 실패 0 |
| 잔여 감사 | 대상 범위의 카탈로그 기반 일본어 잔여 0 |
| 구조/회귀 | bank5C 3,203 issue 0 · bank75B 942 issue 0 · false segptr 0 · 테스트 26/26 |
| 롤백 | `out/patch/backup/20260802_203324_pre_ui_unit_name_followup/monoeye_ko_expanded.wsc` |

`75B3EF 攻`은 body가 단 1바이트라 regular 2바이트 사전 token과 4바이트 ext3 token을
모두 넣을 수 없다. 인접 테이블 재배치와 모든 참조의 안전성이 별도로 증명되기 전에는
`攻→공`을 적용하지 않는다. 이 한 건은 승인 보고서에 명시적 보류로 남겼다.

정본은 `data/ui_unit_followup_ko.json`, 수정된 `data/unit_names_ko.json`,
`out/patch/ui_unit_name_followup_{analysis,approval,report,audit,gate_summary,promotion_report}.json`이다.

## 이전: 2026-08-02 name75·bank76 잔여 감사 및 메인 반영

실화면에서 `ドレン`, `セラ`, `ロべルト`, `スナイパ－ライフル`이 남는 것을 계기로
현재 TIP을 다시 전수 감사했다. 네 항목은 동일한 이유로 빠진 것이 아니었다.

- `セラ`: 기존 name75 카탈로그의 `no_translation` 누락
- `ドレン`: `드레인`이라는 잘못된 자동 번역이 존재했지만 2바이트 short record라 미적용
- `ロべルト`: `ベ`가 아니라 히라가나 `べ`가 섞인 원문 표기라 일반 키 매칭에서 누락
- `スナイパ－ライフル`: name75 범위 밖의 bank76 별도 파츠 테이블이라 파이프 미탐색

`・`를 일본어 잔여로 잘못 세지 않는 기준으로 name75 실제 잔여는 47건이었다.
`75C000`의 단독 `な`는 비표시 잡음으로 제외하고 **46건**을 처리했다. 정확한 독립
사전 항목 18개는 현재 nested parent가 0임을 확인하고 공유 slot을 번역했다. 나머지
28개 short record는 strong retired non-FF stock slot 26개를 사용해 레코드별로
2바이트 token+`01` padding 치환했다. `サラ`는 `オサラバ` 내부 합성을 보존하고,
`プル`은 다른 복합 사용을 건드리지 않도록 둘 다 local-only로 처리했다.

bank76 `76FD0B–76FDDA`에는 커스텀 파츠명 25개가 독립 테이블로 존재했다. 25개 모두
검토 번역을 만들고 union true-free ext3 slot으로 길이 보존 치환했다. 결과는 다음과
같다.

| 항목 | 결과 |
|---|---|
| 현재 메인 TIP | SHA-256 `0F991FD7AF76D2EC23CE322CB89DEE9C15E4618ED7CD46BAD41673F1A3C5AF9B` · checksum `6217` |
| name75 | 대상 46/46 한국어 · 잔여 `75C000 な` 1건은 비표시 잡음 |
| bank76 파츠 | 25/25 한국어 · 일본어 잔여 0 |
| 대표 | `세라`, `드렌`, `로베르토`, `스나이퍼 라이플` |
| 공유 사전 | 18 slot · 외부 소비 146/146 token-only JP→KO 치환 증명 |
| local short | 28 record / 26 strong retired slot · 길이/종료자 보존 |
| part76 | 25 record / 25 true-free ext3 slot |
| 정적 게이트 | 71/71 exact · 구조 48,837 issue 0 · 길이/종료자 62,201 위반 0 · false segptr 0 · 회귀 26/26 |
| 롤백 | `out/patch/backup/20260802_194956_pre_name_part_residual/monoeye_ko_expanded.wsc` |

정본은 `data/name_part_residual_ko.json`, 분석/승인/게이트는
`out/patch/name_part_residual_{analysis,approval,report,gate_summary,promotion_report}.json`이다.
실화면 이름 목록과 커스텀 파츠 화면의 육안 확인은 후속 권장 사항이다.

## 이전 측정된 커버리지 (2026-07-28, **4차 팁 승격 완료 — aux 본문 prefix-preserving**)

현재 팁 `out/patch/monoeye_ko_expanded.wsc` 체크섬 **`8F47`** ·
md5 `80F3582F95120E620F3A8F1DC4876137`.

= 3차 승격(`CF2A`) 계보를 `pre_nondialogue_ko`에서 **전체 재빌드** + aux 본문 868건
추가 + 뱅크 64–69 복원 재적용. 스테이지3 프리즈와 강화화면 아이콘 오류 수정은
3차에서 **에뮬레이터 실측 확인됨**(사용자 검수).

보관 중인 백업:

| 백업 | 체크섬 | 내용 |
|---|---|---|
| `monoeye_ko_expanded.pre_auxbody.wsc` | `CF2A` | **직전 승격 상태** (3차) — 버그 2건 수정 + 고유명사 42건 포함, 안전한 롤백 지점 |
| `monoeye_ko_expanded.pre_eventfix.wsc` | `B037` | 2차 승격 — 스테이지3 프리즈 있음, **롤백용으로 쓰지 말 것** |
| `monoeye_ko_eventfix_work.wsc` | `47AB` | 사용자 실측 통과본 (버그 2건 수정, 고유명사 없음) |
| `monoeye_ko_expanded.pre_nondialogue_ko.wsc` | `176C` | UI 작업 **이전** — 재빌드 기준점 |
| `monoeye_ko_expanded.pre_ext3.wsc` | `22B3` | **게이트 기준선** — `diff_stock_3way` / `verify_stock_noninvasion`이 읽는다. 지우면 게이트가 죽는다 |

정리 시 삭제한 것: `pre_auxko`(`20BF`, 1차 승격 상태) · `merged_cand`(팁과 md5 동일) ·
회차별 스냅샷(`ep4` · `ep5_8` · `all_stages` · `expanded_menu_ko` · `expanded_ui_ko`) ·
재생성 가능한 작업본(`ui_work` · `3byte_work` · `glyph_work` · `midgame_work` ·
`opening_ext3_work` · `rehome_work`) · `monoeye_ko_all`(`verify_all_stages_smoke`
기본 ROM, `build_monoeye_ko_all.py`로 재생성 가능). 총 **224 MB**, 28개 → 14개.

> **재빌드 주의:** `pre_nondialogue_ko.wsc`에서 다시 쌓으면 뱅크 64–69 침범이
> **되살아난다**(그 침범은 대사 파이프가 남긴 것이라 백업에 이미 들어 있다).
> 반드시 마지막에 `repair_data_bank_invasion.py --commit`을 다시 돌릴 것 —
> 절차는 [`docs/EVENT_DATA_BANK_GUARD.md`](EVENT_DATA_BANK_GUARD.md) §5.

`python tools/audit_nondialogue_ko.py --rom out/patch/monoeye_ko_expanded.wsc`

| 지표 | 비대사 작업 전 팁 | 현재 팁 `8F47` |
|---|---:|---:|
| 카탈로그 exact-slot | 1 / 434 (**0.23%**) | 585 / 587 (**99.66%**) |
| name75 레코드 | 10 / 1206 (**0.83%**) | 1104 / 1206 (**91.54%**) |
| aux 전투/해설 레코드 | 0 | **1,458** (레거시 590 + prefix 보존 868) |
| **aux 문장 단위 한글화** | 598 / 5,413 (11.05%) | **1,430 / 5,413 (26.42%)** |
| 고유명사 카탈로그 | 없음 | **42 / 42 (100%)** |
| UI 소비자 보유 슬롯 | 0 / 3181 (**0%**) | 633 / 3182 (**19.89%**) |
| `broken_word` 합성 결함 | 1898 | **1436** |
| `split_compound` | (미측정) | 158 — **전부 선재**, 별도 과제 |
| 뱅크 64–69 스톡 잔차 | 2,881 B | **0 B** |
| FF-page ext 침범 | — | 215 — 선재, 승격 전과 동일(신규 0) |
| 마커 결함(`E3DB` 잔존) | 0 | 0 |

> **게이트를 돌릴 때 `--rom` / `--target`을 반드시 지정할 것.**
> `run_title_menu_capture`의 기본 ROM은 **원본 일본어 ROM**이라 기본값으로 돌리면
> 일본어 메뉴 해시가 나온다.
> `verify_all_stages_smoke`의 기본 ROM은 `monoeye_ko_all.wsc`(다른 계보)였는데
> 2026-07-27 정리에서 **삭제했다.** 이제 기본값으로 돌리면 "Target ROM not found"로
> **즉시 죽는다** — 조용히 엉뚱한 것을 재는 예전 동작보다 안전하다. 항상 `--rom`을
> 넘길 것. 그 파일이 다시 필요하면 `tools/build_monoeye_ko_all.py`로 재생성한다.

`missing_jp` 213개는 정확일치 스톡 슬롯이 없어 **사전 경로로는 적용 불가**하다.
그중 146개가 무장 풀네임이며 name75 테이블에 평문으로 있다 (§남은 작업 1).

UI 소비자 슬롯 18.57%가 낮아 보이지만 남은 2,591개의 상위는 전부
문법 조각(`します` x407 · `この` x265 · `した` x234)이다. 번역하면 안 되는 것들이다.

## 빌드 (스크립트 파이프 **이후** 재실행 필수)

대사/steal/opening/ext3 작업이 공유 슬롯을 JP로 되돌린다.

전체 재빌드는 **한 번에** 돌린다 — 적용기를 두 번 돌리면 리포트가 덮여
게이트의 승인 근거(§`diff_stock_3way`)가 사라진다.

```powershell
# 재빌드는 반드시 UI 작업 이전 백업에서 시작한다 (팁을 다시 쌓지 않는다)
python tools/run_ui_localize.py --rom out/patch/monoeye_ko_expanded.pre_nondialogue_ko.wsc `
                               --out-rom out/patch/monoeye_ko_ui_work.wsc
python tools/patch_menu_plates_ko.py --rom out/patch/monoeye_ko_ui_work.wsc `
                                     --out out/patch/monoeye_ko_ui_work.wsc
python tools/apply_name75_ko.py --rom out/patch/monoeye_ko_ui_work.wsc `
        --out-rom out/patch/monoeye_ko_ui_work.wsc `
        --out-report out/patch/name75_ko_report.json
python tools/apply_aux_ko.py --rom out/patch/monoeye_ko_ui_work.wsc `
        --out-rom out/patch/monoeye_ko_ui_work.wsc `
        --out-report out/patch/aux_ko_report.json
```

게이트 (전부 통과해야 승격):

```powershell
python tools/verify_nondialogue_text.py --target out/patch/monoeye_ko_ui_work.wsc
python tools/verify_all_stages_smoke.py --rom out/patch/monoeye_ko_ui_work.wsc
python tools/verify_stock_noninvasion.py --target out/patch/monoeye_ko_ui_work.wsc
python tools/audit_nondialogue_ko.py --rom out/patch/monoeye_ko_ui_work.wsc
python tools/scan_mixed_script_artifacts.py --rom out/patch/monoeye_ko_ui_work.wsc
```

순서: unit → weapon → ui_system → battle → menu → menu2 → menu3 → **mined** → inplace → spill
→ 메뉴 플레이트 → **name75 레코드** → **aux 레코드**.

## 마커는 상수를 복사하지 말 것

카탈로그에서 `"marker"` 키를 **제거했다**. 적용기는 `tools/hangul_marker.py`의
`resolve_marker()`로 설치값을 읽고, 데이터가 다른 값을 선언하면 경고 후 무시한다.
카탈로그에 마커를 다시 적어 넣지 말 것 — retarget 때 조용히 낡는다.

## 조각 합성 위험 (fragment composition hazard)

사전은 압축기다. `ダメ` 슬롯은 "안 됨"이기도 하지만 **`ダメ－ジ`의 앞 절반**이기도 하다.
그 슬롯을 `불가`로 만들면 전투/UI 레코드 154곳에 `불가－ジ`가 찍힌다.

실측된 사고 4종 (모두 이번에 제거):

| 카탈로그 항목 | 깨진 단어 | 화면 결과 |
|---|---|---|
| `ダメ → 불가` | `ダメ－ジ` | `불가－ジ` |
| `リ－ → 리` | `ジ－クフリ－ド` | `ジ－クフ리ド` |
| `レイ → 레이` | `プレイヤ－` | `プ레이ヤ－` |
| `ラン → 란` | `メガバズ－カランチャ－` | `メガバズ－カ란チャ－` |

이 부류는 침범 가드에도 커버리지 감사에도 안 잡힌다. 질문이 다르다 —
가드는 "누가 이 슬롯을 읽나", 감사는 "이 슬롯이 한글이 됐나", 이쪽은
**"이 슬롯이 더 긴 단어 안에 있나"**다.

- 사전(事前): `tools/scan_fragment_composition_hazard.py` — 카탈로그 항목별 위험 판정.
  부분문자열 일치만으로는 `シグはいいの` 안의 `はい` 같은 **유령 위험**이 잡혀
  멀쩡한 항목을 지우게 된다. 그래서 슬롯이 실제로 기여한 **문자 span**으로 판정한다.
  `・`(U+30FB)는 카타카나 블록에 있지만 구분자이므로 glue에서 제외한다.
- 사후(事後): `tools/scan_mixed_script_artifacts.py` — 실제 화면 기준.
  `broken_word`(한글이 카타카나·장음에 직접 붙음) vs `particle`(한글+히라가나, 정상).
  **이쪽이 진짜 지표다.** 사전 판정은 `ダメ`(진짜 파손)와 `ネオ`(`ネオジオン`,
  옆 단어까지 번역하면 `네오지온`으로 해결) 를 구분할 수 없다. 어휘집이 필요하기 때문.
- 제거된 항목은 삭제가 아니라 `data/_quarantine_fragments.json`으로 격리한다
  (`tools/quarantine_fragment_entries.py`). 되살릴 때는 **그 조각을 합성하는 모든
  부모 슬롯의 풀네임 항목과 함께** 넣어야 한다. 부모 슬롯 페이로드를 통째로
  바꾸면 자식 참조가 사라지므로 그것이 근본 해법이다.

측정: `broken_word` 1898 → **1555**. 위 4종은 전부 소멸.
남은 1555는 0으로 못 내리고 대부분 플레이어에게 안 보인다.

| 잔여 유형 | 예 | 성격 |
|---|---|---|
| 파일럿 보이스 **내부 라벨** | `회피（大데미지）セリフ` | `セリフ`는 사전 슬롯이 아니라 카탈로그 경로로 번역 불가. 개발용 라벨 |
| **거짓 레코드** | `캐ィモプ` · `手タ툿デバニ` | 그래픽/데이터를 zstring으로 걸은 결과. 실재하지 않는 레코드 |
| 실제 부분 합성 | `뱅タ` x23 · `카미－유` x16 · `네오－` x5 | 옆 단어 풀네임 항목 추가로 해결 가능 |

## 게이트

| 게이트 | 결과 (작업본) |
|---|---|
| `verify_nondialogue_text` | **ok** — check(i) dict_only 0 / rendered 0, 큐레이트 512 인덱스가 5,652개 차이를 전부 설명 · check(ii)/(iii) ok |
| `verify_all_stages_smoke` | **overall_ok true** (unit_banks_clean · jagd · opening · hangul 전부 true) |
| `verify_stock_noninvasion` | UNINTENDED **0 B / 0 runs** · **`5F` 포인터 하한 위반** (§아래) |
| `scan_aux_ff_invasion` | 신규 ext 침범 없음 (confirmed 213→215). 신규 stock 187건은 전부 `dialogue_like=false` = 의도된 UI 명사 한글화 |

### `verify_nondialogue_text` check(i)에 의도 허용목록을 추가했다

공유 슬롯 rewrite가 **바로 그 메커니즘**이라서, 인터미션·HUD·유닛 테이블 레코드는
반드시 변한다. 그래서 check(i)를 그냥 통과시키면 안 되고, 무조건 실패시켜도 안 된다.

`load_localized_indices()`가 UI 적용 리포트에서 실제로 쓴 인덱스를 읽고,
`_HybridDictionary`가 그 인덱스만 **원본 페이로드로 되돌린** 사전을 만든다.
되돌렸을 때 원본 확장과 **정확히 일치**하면 그 레코드의 차이는 큐레이트 집합으로
완전히 설명된 것이고, 아니면 진짜 drift다. `raw_entry`를 오버라이드하므로 다른
문구 **안에 중첩된** 슬롯도 함께 치환된다 (안 하면 부모가 미설명 drift로 잡힌다).
하드코딩이 아니라 리포트에서 읽으므로 `INTERMISSION_TILES`와 같은 증거 기반 정책이다.

`--no-ui-allowlist`로 UI 적용 이전의 엄격 동일성 검사를 그대로 쓸 수 있다.

### `5F` 포인터 게이트 — 수치 하한을 **의미 기반으로 교체** (해결)

낡은 게이트는 "원본과 일치하는 포인터 ≥ 3802/3831"이었다. 이 수치는 공유 슬롯을 거의
한글화하지 않은 계보에서 측정된 것이고, `write_dictionary_slots_spill`이 슬롯 하나를
한글화할 때마다 포인터 하나를 spill로 재지정하므로 **UI를 한글화할수록 반드시 내려간다**.
승격 전 팁이 이미 3778로 위반 중이었다는 것이 이 지표가 **안전이 아니라 작업량을 재고
있었다**는 증거다.

새 게이트 `verify_stock_noninvasion.ptr_semantic_gate` — **이동한 모든 포인터가 설명돼야 한다**:

| 설명 근거 | 출처 |
|---|---:|
| 큐레이트 UI 집합 | **512** — UI 적용 리포트에서 읽음 |
| 대사 파이프의 기존 이동 | **53** — `data/dict5f_dialogue_pointer_moves.json`(1회 측정 고정) |
| 팁 실측 이동 합계 | **565** = 512 + 53 |
| **미설명** | **0** → PASS |

베이스라인을 파일로 고정하는 이유: "현재 팁과 비교"로 만들면 승격마다 설명 집합이
조용히 자라 게이트가 무력화된다.

**fail-closed 실증:** 큐레이트/베이스라인 밖 포인터 1개(`idx 0000`)를 2바이트 옮긴 ROM에서
게이트가 `unaccounted 1 → VIOLATED`로 인덱스를 지목하며 실패했다.

낡은 수치는 `--legacy-ptr-min 3802`로 병행 가능하나 기본 off다.

## 데이터

| 파일 | 행 | 비고 |
|---|---:|---|
| `unit_names_ko.json` | 208 | 기체/파일럿/함선/조직 (조각 16개 격리) |
| `weapon_names_ko.json` | 146 + frag 6 | 풀네임은 `missing_jp` — name75 평문 |
| `ui_system_ko.json` | 44 | |
| `ui_battle_terms_ko.json` | 63 | `ダメ－ジ→데미지` 추가 |
| `ui_menu_terms_ko.json` | 66 | |
| `ui_menu_terms2_ko.json` | 70 | |
| `ui_menu_terms3_ko.json` | 26 | |
| **`ui_mined_terms_ko.json`** | **129** | 신규. `mine_ui_facing_terms.py` 산출 → 수동 번역 |
| `ui_inplace_ko.json` | 11 | 고정 주소 |
| `ui_spill_ko.json` | 62 | 전부 `no_pointer` — `5F` spill 여유 0 |
| `_quarantine_fragments.json` | 19 | 격리된 조각 + 사유 |

### 신규 카탈로그를 만드는 법

```powershell
python tools/mine_ui_facing_terms.py --rom out/patch/monoeye_ko_ui_work.wsc
```

`out/script/ui_facing_term_candidates.json`에 `ko`가 빈 후보가 나온다.
`safe_candidates`만 채운다. `glued_candidates`(305개)는 옆 단어 풀네임과 **함께**
넣을 때만 쓴다.

후보 선정 규칙: aux(50–5F,76)/name75 소비자 보유 · 타깃에서 아직 일본어 ·
**히라가나 불포함**(문법 조각 시그니처) · 2자 이상. 이번 631개 `safe` 중
`ui_mined_terms_ko.json`에 넣은 것은 129개다. 나머지는 파일럿 보이스·기체 해설의
**산문 어휘**(`オレ` `貴様` `裏切` `冗談`)라서 메뉴/UI 범위가 아니다.

## 하지 말 것

- 카탈로그에 `"marker"` 다시 넣기 (낡는다 — `resolve_marker()`가 무시하고 경고한다)
- `75:B7A4` / `patch_title_menu.py` 재패치
- UI spill을 `5F:3200–3662`에 쓰기 (UI 포인터 테이블)
- 팁 재사용 슬롯을 무시하고 base-only로 강제 덮기
- `apply_safe_unit` bulk shared rewrite on tip
- `--enable-bank75-spill` (맵 로드 크래시 이력 · LE16 휴리스틱 오탐)
- `scan_fragment_composition_hazard.py`를 돌리지 않고 카탈로그에 카나 **조각** 추가
- `mine_ui_facing_terms.py`의 `glued_candidates`를 풀네임 없이 채용
- **aux 레코드를 문자 클래스 필터로 골라 쓰기** — 16,030건 중 대부분이 그래픽 바이트다
  (§aux, 실패한 방법 3개)
- **aux 뱅크에서 LE16 포인터 테이블 다시 찾기** — 두 방법 모두 0 또는 전부 오탐이었다
- **적용기를 두 번 돌려 리포트 덮기** — 게이트의 승인 근거가 사라진다
- 한국어 번역에 일본어 장음 `－`를 그대로 옮기기 (`broken_word`로 잡힌다)

## name75 풀네임 적용 (2026-07-27, `tools/apply_name75_ko.py`)

`apply_weapon_table.py`가 문서화만 해 두고 구현하지 않은 크기보존 in-place 레코드
rewrite를 ext3 토큰으로 구현했다. **973 레코드** 적용(고유 문구 967),
decode_fail 0 · **encode_fail 0**, name75 0.83% → **90.8%**.
미적용: `no_translation` **124** · `too_short` **109**.

### `encode_fail`의 진짜 원인은 문장부호가 아니라 **없는 글리프**였다

처음엔 `β` `♪` `～` 같은 기호 탓으로 봤는데, 실측하면 패치 폰트 풀에 **한글 음절 자체가
없는** 경우였다. TBL은 패치가 심은 음절 집합만 담고 있어서 새 번역이 그 밖의 음절을
쓰면 실패한다. 5자를 치환해 0으로 만들었다.

| 실패 음절 | 치환 |
|---|---|
| 숏 | 쇼트 |
| 잭 | 자크 |
| 륜 | 회전 |
| 뱀 | 구렁이 |
| 퀀 | 퀸 |

`weapon_names_ko.json`의 팬 계열은 음역어 `팬`으로 표기하고, 셰이버는 세이버로 표기한다.
aux 쪽은 쥘→넣을 · 결핍→부족.

`extend_missing_glyphs.py`로 폰트를 늘리는 쪽은 **거부**했다 — 글리프 5개 때문에
승격된 팁에 폰트 훅을 다시 설치하는 위험/이득이 맞지 않고, 그 도구는 대사 시트 모양에
맞춰져 있다. 치환은 ROM 구조를 건드리지 않는다.

### 번역 파이프 — 일본어를 다시 타이핑하지 않는다

```powershell
python tools/apply_name75_ko.py --dump-worklist out/script/name75_untranslated.json
python tools/build_name75_lexicon.py --emit-bases     # 정확한 기준 문자열 675개
#   → data/name75_base_ko_values.json 의 ordered_ko[675] 를 채운다
python tools/build_name75_lexicon.py --build
python tools/compose_name75_catalog.py                # → data/name75_terms_ko.json
python tools/apply_name75_ko.py --rom <work> --out-rom <work>
```

기준 문자열에는 오타 유발 요소가 있다 — 장음이 `－`(전각 마이너스)이고, 일부 가타카나
단어가 **히라가나 `べ`/`ぺ`**를 쓴다(`キュべレイ` · `ガ－べラ・テトラ` · `サ－ぺント`).
손으로 옮겨 적은 키는 조용히 빗나가고 컴포저는 그 행을 건너뛰어 **오류 없이 일본어로
남는다**. 그래서 일본어 측은 도구가 뽑은 것을 그대로 쓰고 한국어만 **색인 정렬**한다.
길이 불일치는 hard error다(배열이 밀리면 이후 전 항목이 오역된다).

조합 규칙은 두 가지만 자동이다 — `기준 + 트레일링 █`(랭크 마커)와
`기준 + （수식어）`. 그 밖의 불규칙형(제어 태그 `<E62F>` 포함)은 **명시적 override만**
허용한다. 미번역은 일본어로 남으며 **건너뛰기는 항상 안전하고 추측은 아니다** —
worklist에는 텍스트가 아닌 오독 데이터가 섞여 있다(`コ　にすす`,
`の………　…　풰ラふか` = 이미 패치된 대역의 한글이 번져 들어온 행).

**후보 ROM에서 잡은 실패 2건 — 반드시 기억할 것:**

1. **패딩은 `0x00`이 아니라 `0x01`(전각 공백).** `0x00`으로 채우면 zstring 종료자가
   앞으로 당겨져 레코드가 짧아지고, **순차 워크가 뒤 엔트리를 전부 밀어버린다**.
   check(iii)가 130건 위반으로 잡았다. `apply_safe_unit.padded_token_payload`가
   이 규칙과 오버사이즈·trail-00 거부를 이미 담고 있으니 **재사용**할 것.
2. **ext3 인덱스는 뱅크별 문구 여유를 봐야 한다.** 빈 포인터 슬롯만 보고 할당하면
   전부 overflow다. 대사 패스가 뱅크 `11`–`1D`를 꽉 채웠고(여유 1–30 B) 여유는
   `1E`(9,621 B)·`1F`/`20`(각 57,343 B)에만 있다. `ext3_bank_room()` 참조.

게이트 통과를 위해 `diff_stock_3way`에 `name75_ko_record` 분류를 추가했다.
`INTERMISSION_TILES`처럼 **적용 리포트에서 읽고** 리포트가 `ok`일 때만 인정하며,
바이트 변경만 면제한다 — 길이·종료자는 check(iii)가 계속 강제한다.

## aux 전투 보이스 · 능력 해설 레코드 (2026-07-27, `tools/apply_aux_ko.py`)

**590 레코드** 적용(고유 문구 530), decode_fail 0. 뱅크별
`{59: 148, 5C: 61, 5D: 238, 5E: 143}` — 59는 미션 대사, 5C는 스킬/능력 해설,
5D·5E는 파일럿 전투 보이스다. 메커니즘은 name75와 같은 **크기보존 ext3 in-place
레코드 rewrite**이고 사전 슬롯은 건드리지 않는다.

### 레코드 집합을 어떻게 확정했나 — 실패한 방법 3개를 먼저 적는다

aux 뱅크(50–5F, 76)는 텍스트와 **그래픽·고정 테이블이 섞여 있다.** 테이블 위에 ext3
토큰을 쓰면 뱅크 64–69 이벤트 오류 257/2049과 같은 부류의 고장이 난다. 그래서 "무엇이
레코드인가"를 추측이 아니라 증명으로 정해야 했다. 아래는 **다시 시도하지 말 것**.

| 시도 | 결과 |
|---|---|
| **문자 클래스 필터** ("정상 텍스트처럼 보이는 문자만") | 원본 60,396 레코드 중 16,030 통과 → **대부분 오탐**. 그래픽/테이블 바이트가 유효 카나로 디코드된다: `アさ　機ュ` · `たたたたたた…要たたた` · `がおでヤぇ試　な` · `以だ取だえない　機ッ　し`. 문자 클래스로는 판별 불가 |
| **뱅크 내 단조 증가 LE16 런 (≥16)** — 게임의 포인터 테이블 찾기 | 테이블 **0개**. 문자열이 포인터 순서로 저장돼 있지 않다 |
| **연속 LE16 워드 창 + 타깃 대다수가 정상 디코드** | 30 "테이블" · 651 레코드(뱅크 51/56/59/5C/5D) → **전부 오탐**. "포인터"는 반복 데이터 바이트(`0x1A1A` `0x2525` `0x1616` → `591A1A` `562525` `561616`)이고 우연히 정상 텍스트를 가리켰다 |

`tools/find_aux_text_tables.py`는 이 음성 결과의 기록으로 남겨 둔다. 그 안의
`coherent()` 텍스트 판정은 **알려진 실제 문장 12개로 검증했고 10/12 통과**한다
(오탐 없음, 오검 2건은 가타카나 과다 문장 `サイコガンダムのパイロット……。`). 즉
텍스트 판정은 건전하고, 깨진 것은 **주소 찾기**였다.

### 통과한 판별식 — 인접성 + 텍스트 선두 증명

`tools/find_aux_text_blocks.py`. 두 조건을 **동시에** 요구한다.

1. **연속 블록만.** 실제 문자열은 뱅크를 타일처럼 빈틈없이 채우고, 쓰레기 바이트는
   연속으로 정상 디코드되지 못한다. 그래서 레코드 단위가 아니라 **연속 런 단위**로
   판정한다. 이것이 16,030개 오탐을 걸러낸다 → 4,422 coherent 레코드.
2. **첫 바이트가 사전 토큰이거나 2바이트 문자 리드일 때만.** aux 레코드 다수가
   화자/초상 id 단일 바이트로 시작해 엉뚱한 글리프로 찍힌다(`5a`→`カ` · `5e`→`コ` ·
   `32`→`き`). 그런데 이걸 실제 선두 텍스트와 구별할 수 없다 — `80`→`機`는
   `機銃座は……`의 진짜 첫 글자다. **추측하지 않고 선두 바이트가 모호한 3,158건을
   제외**했다.

여기에 "타깃에서 아직 일본어 · 원본과 동일"을 더해 **597 적격 / 537 고유**가 남는다.
번역은 name75와 같은 방식 — 일본어를 다시 타이핑하지 않고 색인 정렬 배열을 채운다.

```powershell
python tools/find_aux_text_blocks.py                  # → out/script/aux_block_eligible.json
python tools/build_aux_catalog.py --emit               # → out/script/aux_text_ordered.json
#   → data/aux_text_ko_values.json 의 ordered_ko[537] 를 채운다 (길이 불일치는 hard error)
python tools/build_aux_catalog.py --build             # → data/aux_text_ko.json
python tools/apply_aux_ko.py --rom <work> --out-rom <work> --out-report out/patch/aux_ko_report.json
```

### 대량 aux 레코드 rewrite는 여전히 **거부** 상태다

위 590건은 구조적으로 증명된 부분집합이다. 남은 것 — 특히 선두 바이트가 모호한
3,158건 — 은 **런타임 증거(실행/읽기 워치포인트) 없이는 확정할 수 없다.**
정적 방법 3개가 모두 실패한 것이 그 근거다. 에뮬레이터 작업은 수동 트랙이므로
범위 밖이며, `docs/DICT_INVASION_GUARD.md`에 따라 계속 거부한다.

### 장음 `－`는 한국어 문장에 그대로 옮기지 말 것

`scan_mixed_script_artifacts`는 한글에 붙은 `－`를 `broken_word`로 잡는다. 일본어 외침
`ちくしょーー！！`을 `이　녀석－－！！`처럼 그대로 옮긴 5건이 그렇게 걸렸다. 스캐너가
옳다 — 장음 부호가 절단된 가타카나 단어인지 늘임 표현인지 구별할 방법이 없다.
`－`를 빼서 해소했고 `broken_word`는 aux 적용 전 값 **1449로 복귀**했다(aux 신규 0).

게이트 승인은 name75와 같은 방식이다 —
`verify_nondialogue_text.RECORD_REWRITE_REPORTS`와 `diff_stock_3way.NAME75_KO_REPORTS`가
`name75_ko_report.json` · `aux_ko_report.json` **둘 다** 읽고, 리포트가 `ok`일 때만
`name75_ko_record`로 분류한다. 면제는 바이트 변경뿐 — 길이·종료자는 check(iii)가 계속
강제한다.

## 고유명사 카탈로그 44건 (2026-07-27, `data/ui_proper_nouns_ko.json`) — **승격 보류**

승격된 팁에 대해 `mine_ui_facing_terms`를 다시 돌려 489 safe 후보 중 **고유명사·명사
44건만** 골라낸 카탈로그다. `run_ui_localize`의 `ui_names` 단계로 적용된다.

489건 전체를 안 쓰는 이유는 §남은 작업 9와 같다(공유 어휘 + 용언 어간). 하지만
**이름은 그 문제가 없다** — `다카르`는 어디에 놓여도 다카르로 읽히고 활용하지 않으며,
이미 출하된 `unit_names_ko`/`weapon_names_ko`와 같은 범주다. 대명사는 최다 빈도
(`オレ` x163 등)지만 **제외**했다: `オレ`가 `オレたち` 안에 들어가면 `나たち`가 되고,
뒤가 히라가나라서 카나 접합 검사가 `broken_word`가 아니라 `particle`로 점수를 낸다.

적용 전 4중 확인을 전부 통과했다(44 제안 / 44 통과 / 0 거부):
miner가 `safe` 판정 · `FRAG_BLOCKLIST` 아님 · 기출하 카탈로그와 `jp` 중복 아님 ·
**설치된 TBL+마커로 인코딩 가능**(name75 `encode_fail`을 만든 글리프 누락 부류).

측정 결과: `broken_word` **1449 → 1443** (반쪽만 번역돼 있던 단어가 완성됐다),
check(iii) 위반 0, `verify_all_stages_smoke` `overall_ok true`,
`verify_nondialogue_text` `ok`. 작업 ROM 체크섬 `312E`.

### `scan_fragment_composition_hazard`의 카탈로그 목록이 낡아 있었다 (고침)

`CATALOGS`가 하드코딩이라 `ui_mined_terms_ko`(129행)와 이 44행을 **한 번도 검사하지
않은 채 `ok`를 냈다**. 안전 검사로서 최악의 실패 방식이다. `discover_catalogs()`로
`data/*_ko.json`을 발견하도록 바꿨고(ext3 레코드 카탈로그는 `NON_SLOT_CATALOGS`로 제외)
검사 대상이 7 카탈로그 616항목 → **10 카탈로그 788항목**(카나 236→295)으로 늘었다.
`hazard_terms`는 98로 동일 — 즉 mined 129행과 신규 44행 모두 위험 항목이 없다.

### ~~승격을 막은 것~~ — **해결됨** (아래 A/B 참조)

뱅크 64–69 침범은 다른 세션이 원인을 찾아 되돌렸다
([`docs/EVENT_DATA_BANK_GUARD.md`](EVENT_DATA_BANK_GUARD.md)). 아래는 그 결과를
실측한 A/B다. 원래 기록은 진단 근거로 남긴다.

### 승격을 막았던 것 — 뱅크 64–69 스톡 침범 2,881 B (**선재 결함**)

`verify_stock_noninvasion`이 작업 ROM에서 UNINTENDED **2,881 B / 76 런**을 보고한다
(뱅크 66 1,524 B · 67 1,297 B · 68 36 B · 64 12 B · 65 9 B · 69 3 B).

**이 44건이 원인이 아니다.** 팁(`B037`)과 작업 ROM(`312E`)의 바이트 차이는
뱅크 `DF`(511 B)·`F5`(5 B)·`FF`(체크섬 2 B) **518 B뿐**이고 뱅크 64–69는 완전히 동일하다.
같은 게이트를 **승격된 팁에 그대로** 돌리면 동일한 2,881 B / 76 런이 나온다
(`out/patch/_r_tip.log`, `out/patch/_q3.log`).

76런 전부 `attribution: PRE` — `monoeye_ko_expanded.pre_ext3.wsc` 시점에 이미
존재한다. 즉 **대사 free-space 파이프가 남긴 것**이고 비대사 작업과 무관하다.
타깃 바이트에 **폐기된 마커 `E3DB`**가 들어 있는 런도 있다
(`66:1328` · `66:3003` · `66:4896`). `attributed_tool`은 unknown 55 ·
`padded_token_payload` 14 · `two_byte_tail_replacement` 3 · `single_byte_overwrite` 4.

뱅크 64–69는 고정 stride 데이터 테이블이고, 여기에 쓰는 것은
`docs/DICT_INVASION_GUARD.md`가 말하는 **이벤트 오류 257/2049 부류**다. 따라서
fail-closed 원칙대로 **승격하지 않았다.** 게이트를 통과시키려고 분류를 느슨하게
바꾸는 것은 더더욱 하지 않는다.

> 07-27 23:07 팁 게이트는 `out_of_band runs 0`으로 **PASS**했는데 23:28에 같은
> ROM·같은 게이트가 76런을 보고했다. 원인은 확인됐다 — 다른 세션이 23:10에
> `diff_stock_3way.DIALOGUE_HI`를 `0x69FFFF` → `0x63FFFF`로 고쳤다. 낡은 상한 때문에
> 뱅크 64–69의 **모든** 변경이 `dialogue_record`로 자동 승인되고 있었다. 즉 이전
> PASS는 **선재 결함을 놓친 통과**였고, 지금의 FAIL이 옳다.

## A/B — 뱅크 64–69 되돌림 (2026-07-27)

A = 되돌림 전 팁 `B037` · B = `out/patch/monoeye_ko_eventfix_work.wsc` `47AB`
(다른 세션이 `B037`에서 파생, 이후 name75 마커코드 수리까지 포함).
측정 스크립트는 읽기전용이고 결과는 `out/patch/_ab.log`.

| 지표 | A (되돌림 전) | B (되돌림 후) |
|---|---:|---:|
| 뱅크 64–69 스톡 잔차 | **2,881 B** (64:12 · 65:9 · 66:1524 · 67:1297 · 68:36 · 69:3) | **0 B** |
| `verify_stock_noninvasion` | **FAIL** — UNINTENDED 2,881 B / 76 런 | **PASS** — UNINTENDED 0 B |
| out-of-band 60–69 | 2,881 B / 76 런 | **0 B / 0 런** |
| `scan_false_segptr_writes` | 32건 | **0건** |
| `verify_nondialogue_text` | ok (61,646 레코드) | ok · check(iii) 0 / **62,201** |
| `verify_all_stages_smoke` | overall_ok true | overall_ok true |

A↔B 바이트 차이는 **3,268 B / 11 뱅크**다. 물리 뱅크 `E4`–`E9`가 스톡 논리
64–69(= `stock_base 0x800000` + `0x64xxxx`)이고 그 합이 정확히 2,881 B로 되돌림
분량과 일치한다. 나머지는 `E1` 3 B(`61:84E3` 오탐 사이트), `F5` 5 B(bank 75 UI
테이블 `0x01` 패딩 수정), `1F` 362 B(name75 마커코드 수리), `DF` 15 B, `FF` 2 B(체크섬).

### 되돌림의 한글 비용 — 실측 0 (문서 주장 확인)

`EVENT_DATA_BANK_GUARD.md`의 "잃는 한글은 0" 주장을 독립 검증했다. 스크립트 뱅크
60–69의 원본 레코드 **59,791개**를 A·B 양쪽에서 렌더해 비교했다.

| | 결과 |
|---|---:|
| 양쪽 모두 한글 | 15,268 |
| **B에서 사라진 한글** | **1** |
| **B에서만 한글** | **13** |

렌더 문자열 자체가 달라진 것은 **47건**이고, 그중 46건이 뱅크 64–69(이벤트 테이블·
쓰레기 바이트)다. 나머지 1건은 대사 대역 안이며 **되돌림이 고친 것**이다 —
`61:84E1`이 A에서 `'こＯＳ'`로 잘려 있던 것이 B에서
`'こねぇ、お兄ちゃん、あそぼ！！'`로 복원된다. 문서가 지적한 `61:84E3` far-pointer
오탐이 대사 페이로드 중간을 덮어써 종료자를 앞당긴 바로 그 사례다(해당 줄은 원래
미번역이라 한글 손실은 없다).

한글이 사라진 1건은 `67:94CF`이고, A에서의 내용은
`'発스테이지　２０　（映후편）　映오프닝…'`다. `映`은 **폐기 마커 `E3DB`의 잔상**이다.
즉 이것은 정상 대사가 아니라 **STG20 후편 이벤트 테이블 위에 덮어써진 한글**이며,
바로 3스테이지 프리즈와 같은 부류의 침범이다. 되돌려서 잃은 것이 아니라 **제거된
것**이다. 반대로 테이블이 복원되면서 순차 워크가 정상화돼 **13개 레코드가 새로
한글로 렌더된다.**

aux 전투 텍스트와 name75도 손실 없다 — aux 한글 레코드 A 8,212 = B 8,212,
name75 A 1,095 = B 1,095.

**결론: B가 모든 축에서 A보다 낫거나 같다. A를 유지할 이유가 없다.**

## 병합 후보 C = B + 고유명사 42건 (`monoeye_ko_merged_cand.wsc` `CF2A`)

B는 팁 `B037`에서 파생됐으므로 고유명사가 **빠져 있다**(B의 감사에 카탈로그 8개만
나온다). B 위에 `apply_proper_nouns.py`로 그 한 단계만 더한 것이 C다.
md5 `44ADD25B63E320362FFA372375960AF6`.

### 렌더 전수 비교에서 44 → 42로 줄인 이유 (`split_compound` 신규 검출)

게이트가 전부 통과한 뒤에도 **렌더 결과를 전수 비교**했다(원본 기준 뱅크 60–69
레코드 59,791개). B↔C 변경 39건 중 3건이 **복합 고유명사를 반쪽만 한글화**했다.

| 사이트 | B | C (44행) |
|---|---|---|
| `60:B57E` | `……ア・バオア・ク－要塞だ。` | `……ア・바오아・ク－要塞だ。` |
| `64:90AE` | `ア・バオア・ク－へ` | `ア・바오아・ク－へ` |
| `63:F666` | `テラ・スオ－ノ！？` | `テラ・수오노！？` |

`scan_fragment_composition_hazard`는 U+30FB `・`를 **일부러** 구분자로 본다
(`ハマ－ン・カ－ン`은 실제로 두 단어). 인명에는 맞지만 **구성요소 전부가 한 대상을
가리키는 이름**에는 틀렸다. 등장 문맥을 전수로 확인해 판단했다.

| 항목 | 전체 등장 | 복합명 안 | 단독 |
|---|---:|---:|---:|
| `バオア` | 11 | **9** (`ア・バオア・ク－`) | 2 |
| `スオ－ノ` | 8 | **8** (`テラ・スオ－ノ`) | 0 |

나머지 구성요소(`ア` · `ク－` · `テラ`)는 그 자체로 위험한 조각이라 **풀네임으로
고칠 방법이 없다.** 따라서 두 항목은 일본어로 남긴다 — 이름을 쪼개는 대가로
`broken_word` 5건을 얻는 거래이고, 가드 원칙상 그 거래는 하지 않는다.

`scan_mixed_script_artifacts`에 이 부류를 **`split_compound` 심각도로 추가**했다
(한글 런이 `・` 하나를 건너 카타카나와 만나는 경우, `ok` 판정에 포함). 조각 검사는
사전 단계라 볼 수 없고 렌더 결과에서는 명확하다.
신규 검사는 **선재 결함 169건**을 함께 드러냈다(`바스크・オム` · `카미－유` 등 기존
카탈로그 유래). B와 C 모두 169로 **동일** — 42행이 추가한 것은 0이다.

### FF-page 카운터 +1은 실제 침범이 아니다 (추적 결과)

`scan_aux_ff_invasion`에서 `ext_ff_page_confirmed`가 B 214 → C **215**로 늘어
가드 §FF-page 규칙에 따라 추적했다. 신규 인덱스는 ext `0FD2`(토큰 `FF D2`) 하나다.

| 확인 | 결과 |
|---|---|
| `0FD2` 내용 | B·C 모두 `'사기。'` — **동일**, 내가 만든 토큰이 아니다 |
| 원본 기준 참조 | aux 1건(`5B3702`) + script 5건(`6F25xx`대) |
| `5B3702` 페이로드 | **원본 · A · B · C 네 개가 바이트 동일** (`AAAAAAFFD22F…`) |
| `5B3702` 렌더 | 네 개 모두 동일 |
| **B↔C 바이트 차이** | 스톡 뱅크 `5F`(사전) 474 B + `7F`(체크섬) 2 B **뿐**. aux(50–5E,76) · name75(75) · 이벤트(64–69) **0 B** |

즉 42행은 사전 영역만 썼고 aux 레코드는 한 바이트도 건드리지 않았다. `FF D2`
침범은 대사 파이프가 남긴 **선재 상태**다. 카운터가 1 오른 것은 같은 패스에서
스톡 한글 히트가 41건 늘어(`596 → 637`) 집계가 밀린 부작용으로 판단한다 —
**카운터 증가 자체를 완전히 설명하지는 못했지만**, 사이트 단위로 바이트 동일함을
확인했으므로 신규 침범은 아니다. 앞으로 이 카운터가 오르면 **집계를 믿지 말고
해당 인덱스의 aux 참조 레코드를 바이트 비교할 것.**

| 게이트 | C 결과 |
|---|---|
| `verify_stock_noninvasion` | **PASS** · UNINTENDED **0 B** · out-of-band **0 B** · 5F 609 이동 / 609 설명(556 큐레이트 + 53 기준선) |
| `verify_nondialogue_text` | **ok** · check(i) 0 · check(ii) 0 · check(iii) **0 / 62,201** |
| `verify_all_stages_smoke` | `overall_ok true` (jagd · unit_banks_clean · hangul · opening 전부 true) |
| `scan_false_segptr_writes` | **0건** |
| `scan_mixed_script_artifacts` | `broken_word` **1448** (B 1449) · `split_compound` **169** (B 169) — 둘 다 선재 기준선, 42행이 늘린 것 0 |

| 커버리지 | B | C |
|---|---:|---:|
| 카탈로그 exact-slot | 543 / 545 (99.63%) | **585 / 587 (99.66%)** |
| `ui_proper_nouns_ko` | 없음 | **42 / 42 (100%)** |
| name75 레코드 | 1095 / 1206 (90.8%) | **1104 / 1206 (91.54%)** |
| UI 소비자 보유 슬롯 | 591 / 3182 (18.57%) | **633 / 3182 (19.89%)** |
| 마커 `EC80` 슬롯 | 565 | **607** |

**미검증:** 3스테이지 이벤트 실제 발생과 강화 화면 아이콘은 **에뮬레이터 실측**이
필요하다. 정적으로는 두 원인이 제거됐고 해당 바이트가 원본과 동일하다는 것까지만
확인했다.

### 카탈로그 하드코딩 목록 — 두 번째 사례를 고쳤다

`audit_nondialogue_ko.CATALOGS`도 하드코딩이라 `ui_proper_nouns_ko`가 **커버리지
보고서에서 조용히 빠져 있었다**(docstring은 "every `data/*_ko.json`"이라고 주장한다).
`scan_fragment_composition_hazard.py`와 같은 결함이다. 목록에 추가하고, 이 부류가
반복된다는 주석을 남겼다. 카탈로그를 새로 만들 때 **손대야 하는 곳이 3군데**다 —
`run_ui_localize.steps` + `optional`, `verify_nondialogue_text.UI_LOCALIZE_REPORTS`,
`audit_nondialogue_ko.CATALOGS`.

## aux 전체 문장 한글화 2차 — prefix-preserving (2026-07-28, **팁 승격 완료 `92B4`**)

1차 aux 작업은 "첫 바이트가 텍스트임을 증명할 수 있는" 590 레코드만 다뤘다. 그
조건이 약 2,700 레코드를 막고 있었는데, 표본을 보면 그 대부분이 **앞에 글리프
하나가 붙은 실제 전투 대사**다.

```
5D:47CF  'アほう……やるな！'      5E:B4B3  '様……落ちろ！！'
```

그래서 그 바이트를 **분류하지 않고 원본 그대로 보존한 뒤 본문만** 재작성한다.

```
원본 :  17 34 18 | いや、大したことじゃないんだが、
결과 :  17 34 18 | E5 18 xx yy 01 01 01 … 00
        ^^^^^^^^   손대지 않음                ^^ 종료자 원위치
```

두 오류가 **비대칭**이라 이 방향이 안전하다 — prefix를 **길게** 잡으면 한국어 앞에
일본어 한 글자가 남을 뿐(미관)이고, **짧게** 잡으면 화자/초상 ID를 덮어써
기능이 깨진다. 애매하면 항상 길게 잡는다.

### 1단계: prefix가 존재한다는 증명 (`tools/prove_aux_prefix.py`)

대조군은 1차에서 이미 "텍스트 선두 증명"으로 통과한 590건이다. 독립 테스트 3개.

| 테스트 | 논리 | 대조군 통과율 |
|---|---|---:|
| **A 중복 본문** | 같은 본문이 서로 다른 선두 바이트로 반복되면 그 바이트는 문자열이 아니다 | 0.0% |
| **B 불가 선두** | 렌더 첫 글자가 **무엇이 뒤따라도** 단어를 시작할 수 없는 글자(`ん` `っ` 소가나 `ー－` `゛゜`)면 앞에 뭔가 붙어 있다 | 1.4% |
| **C 분포 집중** | 선두 바이트 상위3 점유율을 대조군 첫글자 상위3(19.8%)과 비교. 자연어는 퍼지고 열거형 필드는 집중된다 | — |

증명된 레코드 **1,484건**: 59 = 1,100(뱅크 전체, C가 상위3 **80.8%** = `17`(が)x392 +
`18`(こ)x276 + `08`(は)x221, 고유값 55개, 대조군의 x4.08) · 5D = 217(A 79 ∪ B 143) ·
5E = 167(A 62 ∪ B 106) · **5C = 0**.

5C가 0인 것은 정당한 거부다. 5C는 도감 해설이 여러 레코드로 쪼개진 **연속 텍스트**라
`'ー年戦争後、…'` `'ン機関で養成された…'`처럼 불가 선두가 **실제 본문**이다.

> **실수 3개를 기록해 둔다.** (1) B의 초판이 byte 0만 단독 확장해 판정해서 2바이트
> 리드가 `<TRUNC:xx>`로 나왔고 **대조군이 100% 통과**했다 — 반드시 레코드 전체를
> 렌더한 뒤 첫 글자로 본다. (2) B의 불가 집합에 조사 `が は を に の で と も へ`를
> 넣었다가 `'でも、これは戦争だから！！'`를 오탐했다 — 조사는 문맥에서만 조사이고
> 음절로는 단어를 시작한다(`でも` `がんばれ` `にげろ`). (3) A·B에 뱅크 커버리지
> 문턱(10%)을 걸어 5D/5E의 개별 증명 141건을 버렸다 — A·B는 **레코드 단위 증명**이라
> 문턱을 걸면 안 된다. C만 뱅크 단위다.

### 2단계: prefix 길이 (`tools/measure_aux_prefix_rule.py`)

C는 "선두가 열거형 필드"임을 증명하지만 **길이는 알려주지 않는다**. 뱅크 59에는
`がせこ`(3바이트)와 `こ`(1바이트)가 섞여 있다.

**통계로 도출하려는 시도는 실패했고, 위험한 방향으로 틀렸다.** 바이트별 위치
비율을 쓰면 `こ`(0.41)·`は`(0.54)가 본문에도 흔해서 text로 분류되고, `がせこ`가
`がせ`로 **짧게** 절단된다. 5D/5E에서는 실제 단어 `キサマ` `艦長`이 prefix로
승격됐다. 근본 원인은 **prefix 바이트가 동시에 흔한 본문 글자**라는 것이다.

시퀀스 단위로 보면 갈린다.

| 선두 시퀀스 | 렌더 | 선두 등장 | **본문 내부 등장** |
|---|---|---:|---:|
| `17 34 18` | `がせこ` | 348 | **0** |
| `17 28 08` | `がけは` | 43 | **0** |

그리고 선두 바이트가 `08` / `17` / `18` — **이 프로젝트가 이미 파싱하는 스크립트
제어·스피커 바이트와 같다**. 새 포맷이 아니므로 `extract_script.split_prefix_body`
(`08 xx` 스피커 · `01` 인덴트 · `17 xx [08 xx] 18` 윈도우 · `18` 대화 마커)를 그대로
재사용한다.

| 뱅크 | 규칙 | 근거 |
|---|---|---|
| `59` | `split_prefix_body` | 위 시퀀스 측정 + 제어 바이트 일치 |
| `5D` `5E` | 1 코드유닛 | A·B가 모두 k=1에서 증명(A는 k=1 193건 vs k=2 9건, k=3 27건), 시퀀스 표본도 일치 |
| `5C` | **없음** | 연속 텍스트, 제외 |

검증 2단계: (1) **다바이트** prefix 시퀀스의 본문 내부 등장 = 0. *1바이트에는 이
검사를 쓰면 안 된다* — `10`=`－`, `18`=`こ`는 당연히 본문에도 글자로 나온다;
1바이트는 A·B의 레코드별 증명이 근거다. (2) 자른 본문이 단어 시작으로 읽히지
않으면 **fail-closed skip**(`body_bad_start`).

방어 가능한 분리 **872건**: 59 = 611 · 5D = 147 · 5E = 114. 제외는 59
`no_prefix_found` 211(선두가 제어 바이트가 아님) · `body_too_small` 273+1+4 ·
`body_bad_start` 5+69+49.

### 3단계: 적용 (`apply_aux_ko.py --prefix-rule`)

규칙 파일을 **신뢰하지 않는다.** 적용 시점에 같은 규칙으로 k를 다시 계산하고
`prefix_hex`까지 일치해야 쓴다(불일치 → `prefix_rule_mismatch`, 실측 0건).
쓰기는 `rom[at:at+k]`를 건드리지 않고 assert로 동일성을 확인한 뒤 본문만 교체하며,
패딩은 `0x01`(전각 공백) — `0x00`은 종료자를 앞당겨 이후 전 레코드를 밀어버린다.
검증은 **(a) prefix 바이트 동일 (b) 본문만 확장한 결과가 의도한 한국어와 일치**
둘 다 만족해야 하며, 하나라도 실패하면 ROM을 쓰지 않는다.

### 4단계: 번역 (`tools/build_aux_body_catalog.py`)

키는 **본문만**이다(prefix는 ROM에 일본어 바이트로 남으므로). 고유 본문 **757건**이
872 레코드를 덮는다. `--build`가 쓰기 전에 두 가지를 강제한다 — 한국어에 장음
`－` 포함 거부, 그리고 **설치된 TBL+마커로 전량 인코딩 가능 증명**. name75가 겪은
글리프 누락을 적용 시점이 아니라 카탈로그 시점에 잡는다. 실제로 3건이 걸렸다:
`팬`(→ 네오・일본) · `롯`(비롯해 → 포함해) · `깬`(잠 깬 → 잠에서 깨어).

`∀` / `タ－ンＸ`는 원본 기호를 재사용하지 않고 **턴에이 / 턴엑스**로 적는다(TBL에 없다).

### 결과 — 후보 `out/patch/monoeye_ko_aux2_work.wsc` `92B4`

`pre_nondialogue_ko`(`176C`)에서 전체 재빌드 후 뱅크 64–69 복원까지 다시 돌렸다.
apply_aux_ko **1,458 레코드** = 레거시 590 + prefix 보존 **868**, decode_fail 0.

| 게이트 | 결과 |
|---|---|
| `verify_stock_noninvasion` | **PASS** · UNINTENDED **0 B** · out-of-band 60–69 **0 B** |
| `verify_nondialogue_text` | **ok** · check(iii) **0 위반 / 62,201** |
| `verify_all_stages_smoke` | `overall_ok true` |
| `scan_false_segptr_writes` | **0건** |
| `scan_mixed_script_artifacts` | `broken_word` **1436**(팁 1448) · `split_compound` **158**(팁 169) — 둘 다 개선 |

**문장 단위 한글화율** (`tools/measure_aux_sentence_rate.py`, 신규):

| 지표 | 팁 `CF2A` | 후보 `92B4` |
|---|---:|---:|
| 전체 레코드 `ko_only` | 598 / 5,413 (**11.05%**) | **1,430 (26.42%)** |
| 뱅크 59 (미션 대사) | 11.8% | **57.9%** |
| 뱅크 5D | 12.6% | **20.1%** |
| 뱅크 5E | 11.4% | **20.5%** |
| 뱅크 5C (도감) | 9.1% | 9.1% (의도적 제외) |

> **`scan_mixed_script_artifacts`의 오탐을 고쳤다.** 처음 측정에서 `broken_word`가
> 1448 → 1525로 악화했는데, 보존한 prefix 글리프가 카타카나(`ズ` `セ` `ミ` `ュ`)라서
> 바로 뒤 한글과 붙은 것으로 집계된 탓이었다. 그 바이트는 게임이 필드로 소비하고
> 화면에 찍지 않으므로 오탐이다. 이제 `aux_ko_report.json`이 `ok`일 때 그 레코드의
> **본문만** 분석한다(`--aux-report`). 팁 기준선을 잴 때는 없는 경로를 주어야 한다 —
> 팁에는 prefix 레코드가 없으므로 리포트를 주면 엉뚱하게 잘라낸다.

### 미해결: 뱅크 59 대사가 **어디에 출력되는지** 정적으로 입증하지 못했다

사용자가 게임에서 이 대사를 찾지 못했다고 보고했고, 확인해 보니 정당한 의문이다.

`EVENT_DATA_BANK_GUARD.md`가 확정한 far pointer 규약(`oo oo bb`, `bb = 0x80 + 논리뱅크`)에
따라 뱅크 59를 가리키는 포인터는 뱅크 바이트가 `D9`다. 원본의 스테이지 이벤트
테이블(64–69)을 전수 훑어 `D9` 포인터를 모았다.

| | 값 |
|---|---:|
| `bb=D9` 3바이트 포인터 후보 | 442 (64:263 · 65:36 · 66:58 · 67:33 · 68:24 · 69:28) |
| 서로 다른 타깃 주소 | 335 |
| **번역한 759 레코드와 겹치는 타깃** | **1** (`591915`) |

759건 중 **1건**만 겹친다. 442개 후보는 앞서 스테이지3 프리즈의 원인이었던 것과 같은
**우연 일치** 부류로 보인다. 즉 이 측정은 "뱅크 59 대사가 스테이지 이벤트로 출력된다"를
지지하지 않는다.

지지하는 증거는 하나 있다 — 뱅크 59가 스크립트 뱅크 60–6F와 **완전히 같은 대사 제어
문법**(`08 xx` 스피커 · `17 xx [08 xx] 18` 윈도우 · `18` 마커)을 쓴다는 점이다. 데이터
테이블이 이런 구조를 가질 이유는 없다. 그래서 "대사 모양"인 것은 확실하지만
**도달 가능성은 미증명**이다.

가능성 두 가지, 둘 다 정적으로는 구별 불가:

1. 늦은 스테이지 전용 대사다. 번역 내용이 Z·0083·W·크로스본·턴에이 계열까지 걸쳐
   있어 초반 플레이에서는 안 나올 수 있다.
2. 미사용/중복 데이터다.

**정리 방법:** 뱅크 59 레코드에 읽기 워치포인트를 걸고 플레이하는 것. 그 전까지
이 868건은 "쓰기는 안전하지만 화면 노출은 미확인"으로 취급해야 한다. 안전성 자체는
별개로 확보돼 있다 — 게이트가 전부 통과하고, prefix 바이트는 손대지 않았고,
레코드 길이·종료자가 원본과 동일하므로 **출력되지 않더라도 잃는 것이 없다**.

### 여전히 거부하는 것

정적으로 증명할 수 없는 나머지는 계속 거부한다 — 5C 706건(연속 텍스트),
5D/5E의 `body_bad_start` 118건(자른 본문이 단어로 안 읽힘 = prefix가 1유닛보다 길
가능성), `body_too_small` 278건, 59의 `no_prefix_found` 211건. 여기를 열려면
**런타임 증거**(전투 대사 출력 루틴에 읽기 워치포인트)가 필요하다.

## 남은 작업

1. ~~**name75·bank76·유닛 정보의 카탈로그 기반 실제 표시 잔여**~~ — **2026-08-02 해결.**
   name75 46건, bank76 파츠 25건에 이어 bank5C/75B 유닛 정보·능력치 UI 133건을
   반영했다. 감사 대상 범위의 카탈로그 기반 일본어 잔여는 0이다. 단, `75B3EF 攻`은
   1바이트 레코드라 안전한 token이 물리적으로 들어가지 않아 별도 보류한다. 일반적인
   2바이트 free slot은 여전히 0이므로 이후 short record도 candidate-bound retired-slot
   증명이 있을 때만 허용한다.
2. ~~ext3 렌더 경로 실측~~ — **정적으로 해결**([`docs/EXT3_RENDER_PATH.md`](EXT3_RENDER_PATH.md)).
   ROM 전체에서 사전 포인터 테이블을 읽는 곳은 `7A:0703`(리프 `7A:06CE` 안) **한 곳**,
   그 호출자는 `7A:0740` · `7A:0818` **두 곳**뿐이고 셋 다 훅이 걸려 있다.
   **이전 판의 "뱅크 7F에 두 번째 디코더가 있다"는 서술은 틀렸다** — 뱅크 7F에는
   `7BCC` 참조가 0개, `mov al,DF`가 0개다. 그 뱅크는 MML 사운드 드라이버다.
   런타임 브레이크포인트는 필요 없다.
3. ~~`5F` 포인터 게이트 기준 결정~~ — **완료** (§위, 의미 기반 게이트).
4. ~~bank72 메뉴 플레이트~~ — **완료**. 작업본에 29장 병합.
   에뮬 대조 실험: title `EB6969549215` 팁과 **동일**, menu `4903A5F9ECD4`(JP) →
   `65DADA2B77AE`(KO), 3회 연속 결정적. `새 게임`·`계속` 육안 확인.
   병합 후 UNINTENDED 0 B 유지.
5. **bank54 인터미션 라벨은 계속 보류** — 포커스 상태 일본어 잔상 미해결.
6. **aux `no_translation` 7건** — `aux_text_ko_values.json`의 미번역 슬롯.
7. **aux 선두 바이트 모호 3,158건** — 런타임 증거 필요, 거부 유지 (§aux).
8. `뱅タ`(x23) · `카미－유`(x16) 부분 합성 해소: 옆 단어 풀네임 항목 추가.
9. **남은 UI 산문 어휘 489건은 번역하지 않기로 했다.** `mine_ui_facing_terms`가
   "safe"로 분류하지만 실제로는 **공유 어휘**라서 결과가 일본어 문법에 한국어가 박힌
   문장이 된다. 게다가 여럿이 **용언 어간**이다 — `背負` + `って` → `짊어って`.
   대신 aux 레코드 rewrite(§위)로 문장 전체를 한국어로 바꾸는 쪽을 택했다.
