# WonderSwan Color 8MB → 16MB 용량 확장 전략

작성일: 2026-07-17  
대상: *SD Gundam G Generation: Mono-Eye Gundams* (WSC)

## 1. 플랫폼 사실

| 항목 | 값 | 출처 |
|---|---|---|
| 원본 ROM | 8 MiB (`0x800000`), 헤더 `rom_size=$08` | 실파일 |
| 매퍼 | Bandai **2001** (`mapper=$00`) | 헤더 / [WSdev ROM header](https://ws.nesdev.org/wiki/ROM_header) |
| 2001 상한 | **16 MiB** (`rom_size=$09`) | 동일 |
| 2003 상한 | 64 MiB (본 게임 불필요) | WSdev Bandai 2003 |
| 뱅크 | 64 KiB × `$C2`/`$C3` (ROM0/ROM1), 1 MiB 창 `$C0` (linear) | [Mapper](https://ws.nesdev.org/wiki/Mapper) |
| 실카트 주의 | 상용 16 MiB는 있으나, **범용 플래시카트는 대개 8 MiB 한계** | [Wonderful wiki](https://wonderful.asie.pl/wiki/doku.php?id=wswan%3Aplatform_overview) |

에뮬(BizHawk/Mednafen 등)과 매퍼 스펙상 **16 MiB 인식·실행은 가능**하다. 실기 플래시 배포는 별도 제약이다.

## 2. 왜 뒤에 붙이면 안 되는가 (결정적)

원본·패치 모두 ROM1 뱅크 전환 시:

```text
mov al, (logical_bank | 0x80)   ; 예: bank40 → AL=C0, bank5E → AL=DE
lcall 8000:DEB5                 ; OUT C3h, AL  (파일 0x78DEB5)
```

실측 `B0 xx ; 9A B5 DE 00 80` 쌍 **156곳** 전부 `AL ≥ 0x80` (상위 비트 세트).

| ROM 크기 | `AL=C0` 의미 |
|---|---|
| 8 MiB (주소 마스크 23비트) | `0xC00000 → 0x400000` (bank40 **미러**) |
| 16 MiB **append** | `0xC00000` = **신규 빈 뱅크** → 폰트/코드 전부 미스 → 즉시 파탄 |
| 16 MiB **prepend** | 원본이 `+0x800000`로 이동 → `AL=C0`가 이동한 bank40에 정확히 착지 |

리셋 시 `$C0/$C3=0xFF`도 “마지막 뱅크”를 가리키므로, **앞에 8 MiB `FF`를 붙이고** 헤더 `rom_size=$09`로 올리면 부트·스톡 뱅킹이 유지된다.  
WonderSwan 비2의제곱 ROM도 “앞쪽 패딩”이 정석이다 ([ROM header](https://ws.nesdev.org/wiki/ROM_header)).

```text
[ 신규 8MiB FF | 원본 8MiB (헤더·체크섬 포함) ]
 banks 00–7F      banks 80–FF  (= 옛 00–7F 내용)
```

- 스톡/`bank|0x80` → 뒤쪽(원본)  
- 확장 훅 `AL=00–7F` → 앞쪽(신규 빈 공간, **최대 8 MiB**)

## 3. 확장 영역에 무엇을 넣을 수 있는가

| 자산 | 가능? | 조건 |
|---|---|---|
| 한글 글리프 (16B compact) | **가능** | pad2와 동일: `OUT C3`로 확장 뱅크 맵 → 읽기 → bank40 복구. 슬롯 수 ≈ `8MiB/16` (이론상 수십만, 실사용은 코드·인덱스 예산) |
| 사전/대사 문구 | **가능** | 포인터는 뱅크 내 16비트. 확장 뱅크에 포인터표+payload를 두고, `patch_ext_dictionary`류 훅이 `AL=확장뱅크`로 전환 |
| 스크립트 bank60–6F 직접 연장 | **주의** | 순차 NUL 스캔·이벤트 스트림. “빈 뱅크를 60 뒤에 붙인다”만으로는 로더가 안 따라옴 → **명시 훅/포인터 테이블** 필요 |
| UI/유닛 문자열 spill | **가능** | 기존 spill과 동일 패턴, 대상만 확장 뱅크 |
| 타이틀 그래픽 | 별개 | 텍스트 테이블이 아님 (기존 반증 유지) |

### 권장 적재 맵 (초안)

| 영역 | 용도 |
|---|---|
| `00:0000`–`0F:FFFF` (1 MiB) | 한글 글리프 풀 (pad3+) — unique Hangul ~1.2k ≪ 예산 |
| `10:0000`–`2F:FFFF` (2 MiB) | 확장 사전/긴 KO 문장 |
| `30:0000`–`4F:FFFF` (2 MiB) | 스크립트 spill·재배치 버퍼 (훅 설계 후) |
| `50:0000`–`7F:FFFF` | 예비 / 디버그 마커 |

기존 8 MiB 안 pad1/pad2·5E ext_dict·5F spill은 **당분간 유지**하고, 글리프 overflow·긴 문장부터 확장 뱅크로 옮기는 것이 안전하다.

## 4. 툴링 계약

- `tools/expand_rom_16mb.py` — prepend + `rom_size=$09` + 체크섬
- `monoeye_rom.stock_base(rom)` — 8 MiB→0, 16 MiB→`0x800000`
- `bank_offset` / `slice_bank` / `patch_bank` — 스톡 뱅크는 `stock_base` 반영
- `expansion_bank_offset(seg, off)` — 앞쪽 `00–7F` 전용 (16 MiB만)
- 뱅크 전환 AL: 스톡 `logical|0x80`, 확장 `logical&0x7F` (고비트 **금지**)

## 5. 검증 체크리스트

1. 16 MiB 베이스로 타이틀·뉴게임·1스테이지 (스크립트만 패치본 기준)
2. 스톡 `AL=C0` 경로로 bank40 폰트 테이블 읽힘
3. 확장 `AL=0x10` 등에 넣은 마커/글리프가 훅으로 읽힘
4. 체크섬·헤더 `$09` 에뮬 인식
5. (선택) 실카트 — 8 MiB 플래시면 배포 불가, 에뮬/16 MiB 지원 카트만

## 6. 비목표

- Bandai 2003 / 32 MiB+ 전환
- 스톡 `AL|0x80`를 일괄 `AL&0x7F`로 패치 (위험·불필요; prepend가 이미 호환)
- append 방식 실험 ROM을 본선으로 승격

## 7. 구현 현황 (pad3 훅)

| 항목 | 내용 |
|---|---|
| 툴 | `tools/patch_pad3_expansion.py` |
| 입력 | `out/patch/monoeye_ko_expanded_8mb.wsc` (승격 전 8 MiB 백업) |
| 출력 | 팁 `monoeye_ko_expanded.wsc` (16 MiB; cold rebuild 시) |
| 맵 | `out/patch/hangul_char_map_pad3.json` (sticky **1186**) |

슬롯 라우팅:

| 슬롯 | 저장 | 런타임 |
|---|---|---|
| 0–95 | bank40:F9F8 | CX=3000, 뱅크 전환 없음 |
| 96–527 | bank41:E4F4 | `OUT C3 AL=C1` → 읽기 → restore `AL=C0` |
| 528–1026 | expand `00:0000+` (bank3F에서 migrate) | `OUT C3 AL=00` |
| 1027–1185 | expand (overflow 159자 bake) | 동일 |

`pad_hi` 헬퍼(`7F:FCAB`)가 pad2/pad3를 분기. primary ≤64 B·ext_dict helper(`7F:FC8C`) 유지.

## 8. 확장 사전 (bank 0x10)

| 항목 | 내용 |
|---|---|
| 툴 | `tools/patch_exp_dictionary.py` |
| ROM | 팁 `monoeye_ko_expanded.wsc`에 포함 (중간 스냅샷 삭제) |
| 토큰 | 인덱스 **3831–4095** (`FF` 페이지, 상한 `0xFFF` 동일) |
| 저장 | expand **bank10** `AL=0x10`, ptr@`0000`, phrase budget ≈**65 KB** |
| 이전 | stock bank5E `E22B+` 문구 **265슬롯 migrate** |
| apply | `apply_ext_dict_unit.py`가 16 MiB/`ext_in_expansion` 시 bank10 경로 사용 |

주의: `apply_ext_dict_unit --force-format`은 bank10을 비우고 재할당한다.  
이미 스크립트가 가리키는 migrate 문구를 유지하려면 **재포맷 없이** 쓰거나, 토큰·문구를 함께 재패치해야 한다.

### 8a. 2026-08-17 bank10 native helper 용량 재확인

STAGE21t continuation parser 회귀 분석 과정에서 확장영역의 실제 여유를 현재 메인 TIP 기준으로 다시 계측했다.

- expansion `00–7F` 전체 사용률: 약 **15.78%**
- expansion 영역 `FF` 잔여: 약 **6.74MiB**
- 완전 `FF` 64KiB bank: **103개**
- bank10 현재 phrase tail: `0x123B`
- bank10 `0x123B–0xFFFF`: 연속 `FF`, **60,869 bytes (약 59.4KiB)**

따라서 짧은 native helper phrase 추가에서 병목은 **payload 공간이 아니라 2바이트 dictionary token ID 수**다. exact continuation 잔여 9건은 `E5 18` portal 대신 원본과 같은 `18 + 2-byte dict + 2-byte dict` 문법으로 복구하는 방향을 우선한다.

현재 계획과 ID 회수 가드는 [`EXACT_CONTINUATION_NATIVE_RECOVERY_PLAN.md`](EXACT_CONTINUATION_NATIVE_RECOVERY_PLAN.md)를 정본으로 한다. 특히 bank10은 충분히 비어 있어도 기존 ext dictionary를 `--force-format`하거나 ID를 무검증 재사용해서는 안 된다.

## 9. 확장 스크립트 spill (bank 0x30+)

| 항목 | 내용 |
|---|---|
| 모드 | `overflow_mode=exp_spill` (`apply_translations_expanded` / `run_exp_script_spill.py`) |
| ROM | 팁 `monoeye_ko_expanded.wsc`에 포함 |
| 조건 | segmented far pointer (`oo oo ss` / `ss oo oo` / …) + hit ≤16 |
| 동작 | payload → expand bank30+, **seg+off 동시 갱신**, 원문 blank 안 함 |
| 1차 실측 | relocated **1562**, pointer_fixes **2054**, decode_fail **0**, bank30 ≈45 KB |
| 제외 | 순차 NUL 스캔 전용 줄 → `skipped_no_pointer` (의도적) |

## 10. 순차 스캔 대사 (ext dict 크기보존)

| 항목 | 내용 |
|---|---|
| 툴 | `tools/run_seq_ext_dict.py` → `apply_ext_dict_unit.py --only-no-pointer` |
| 입·출력 | 팁 `monoeye_ko_expanded.wsc` (in-place) |
| 포인터 판별 | `--pointer-ref-rom` = `_8mb` 백업 (spill 후 오인 방지) |
| 방식 | 순차 풀 고빈도 unique KO → bank10 슬롯 재할당 + `prefix+token+0x01` pad |
| 금지 | `--force-format`, 순차 레코드 blank, full-bank shift |
| 시드 | seed가 쓰는 ext 인덱스 6개 pin (3853–3854, 3857–3860) |
| 1차 실측 | unique **258** / lines **1561**, decode_fail **0**, seed_fail **0** |
| 천장 | 안전 슬롯 ≈264 — 순차 unique ~14k 중 **상위 빈도만** (~10% 줄) |

## 11. 팁 ROM 승격·정리 (2026-07-17, free-space 기준선)

| 파일 | 역할 |
|---|---|
| **`out/patch/monoeye_ko_expanded.wsc`** | **현재 기본 팁** — 16 MiB free-space 기준선 (플레이 검증) |
| `out/patch/monoeye_free_space_base.wsc` | 재빌드 베이스 (`60–69`←JP, expand `30–4F`←FF) |
| `out/patch/monoeye_ko_expanded_8mb.wsc` | 승격 직전 8 MiB 백업 (cold rebuild 입력) |
| `monoeye_ko_marked.wsc` / `monoeye_ko_seed.wsc` / `rom_font_*.wsc` | 베이스·폰트 파이프용 (유지) |

**삭제함 (2026-07-17 정리):** stage2 릭돔 이상을 만든 legacy tip 백업(`pre_free_space_base` 등),  
`monoeye_bisect_*.wsc` / rollback ROM·sav, `menu_bisect/`, 진단용 일회성 스크립트·리포트.  
재현은 free-space 파이프만 쓴다 — 바이섹트 ROM을 본선에 두지 않는다.

- 일상 apply 기본 경로: `monoeye_ko_expanded.wsc`
- **통합 빌드 (기본):** `tools/build_script_ko.py --placement free-space`  
  (`classify` → `free_space` → `opening_dedicated` → `verify`)  
  dry-run: `python tools/build_script_ko.py --dry-run`
- **legacy (비권장):** `--placement legacy` + `exp_spill`/`seq_dict` — 2스테이지 회귀 이력
- cold rebuild: `_8mb` → `patch_pad3` → `patch_exp_dictionary` →  
  `snapshot_free_space_base` → `build_script_ko --placement free-space`
- 8 MiB 결과물로 팁을 덮지 않도록 `apply_translations_expanded`가 가드함.
- 16 MiB에서 스크립트 abs는 항상 `stock_base + logical` (선행 `FF` 8 MiB).

대사 커버·플레이 상태 **정본:** [`SCRIPT_COVERAGE_STATUS.md`](SCRIPT_COVERAGE_STATUS.md)

## 12. 초반(~3화) 테스트 창

| 항목 | 내용 |
|---|---|
| 창 | `6040A5`–`62FFFF` (bank **60–62**) |
| 시트 | `out/script/translations_ep3_window.json` (`filter_sheet_abs.py`) |
| 적용 | `build_script_ko.py --placement free-space --sheet …_ep3_window.json` |
| 백업 | 본선 tip만 유지 — 오염 `pre_*` 스냅샷은 두지 않음 |
| 앵커 | `6040A5` 오프닝(+`6040B5`), 1스테이지 초반, 2스테이지 릭돔 |
| 한도 | sole far-ptr + opening dedicated 슬롯 — 순차 전량 KO 불가 |
| 실측 | 오프닝 OK · 1스테이지~시그/블레이드 직전 OK · 2스테이지 안정 |

```text
python tools/filter_sheet_abs.py --min-abs 6040A5 --max-abs 62FFFF
python tools/build_script_ko.py --placement free-space \
  --sheet out/script/translations_ep3_window.json
```

주의: `exp_spill`은 확장 뱅크에 **이어쓰기**해야 한다. 빈 `FF`로 통째 초기화하면
이전 spill 포인터가 고아(빈 페이로드)가 된다 — `spill_replacements_to_expansion`은
기존 bank30+를 로드한 뒤 trailing free부터 append한다.

## 12b. FF-page 사전 × 원본 UI 침범

16 MiB tip의 ext 토큰 `FF yy`는 전투/UI 뱅크 zstring에 원래 있던 바이트와 겹칠 수 있다.  
그 슬롯에 스토리 Hangul을 올리면 튜토리얼·도움말에 무관 대사가 끼인다.

원칙·스캔·수리: [`DICT_INVASION_GUARD.md`](DICT_INVASION_GUARD.md)  
(`scan_aux_ff_invasion.py`, opening/steal/safe_unit aux fail-closed).

## 13. false exp_spill 침범 (유닛/시나리오 테이블)

`exp_spill`이 bank **5C–5E / 6C–6F** 안의 우연한 `off16+seg` 바이트를 대사 far-pointer로
오인하여 expansion(seg 30–4F)으로 고치면 MS 마스터·시나리오 필드가 깨진다.

| 항목 | 내용 |
|---|---|
| 증상 | 2스테이지 게스트 MS 오표기 (야크트도가 오출현 / 우군이 Z건담으로 치환 등) |
| 원인 주소 | `6D937C`: stock `3F A6 60` → 오염 `CF 19 30` (이름 문자열 `75C89A`는 정상) |
| 검색 금지 | `POINTER_SEARCH_DENY_BANKS` = `5C–5E`, `5F`, `6C–6F` (`rebuild_script_banks.py`) |
| 추가 가드 | spill 포인터 타깃 zstring 길이 ≥2 (`MIN_SPILL_POINTER_TARGET_LEN`) — 1바이트 중간토큰 오인 방지 |
| 잔여 침범 | deny 밖 **50–5B / 6A–6B** 에도 우연 `off16+seg` 재작성 가능 |
| 복원 | `restore_false_expspill_sites.py` (deny, ref=`_8mb`) + `fix_restore_spill_search_banks.py` (50–5B/6A/6B ← `_8mb`) |
| spill 검색 | 기본 포인터 검색 뱅크를 **60–69만** (50–5B·6A–6B 제외) |
| 검증 | `6D937C == 3fa660`, tip `6A/6D` vs JP diff = 0, tip `50–5B/6A/6B` vs `_8mb` = 0 |

대화 뱅크 60–6B·사전 5F는 이 복원으로 건드리지 않는다. 사전 spill은
`write_dictionary_slots_spill`이 **미재배치 슬롯의 문구 구간을 건너뛰도록** 가드한다.

## 14. free-space 전용 한글 이전 (기본 경로 · 기준선)

바이섹트로 tip `60–69` 광역 inplace/`seq_dict`가 2스테이지 릭돔·인터미션 이상을
유발함이 확인됨. legacy `exp_spill`+`seq_dict` 대신 **빈 공간만** 쓰는 경로를 기본·기준선으로 한다.

| 항목 | 내용 |
|---|---|
| 베이스 | `tools/snapshot_free_space_base.py` — `60–69`←JP, expansion `30–4F`←FF |
| 적용 | `apply_free_space_script_ko` + **`apply_opening_dedicated`** |
| 페이로드 | bank `30–4F` trailing FF에만 append (far-ptr **sole**만) |
| 오프닝 | size-preserving 본문 토큰 (`6040A5–607000`); 시드·`OPENING_INTERSTITIALS` 병합 (`--include-seed-abs`) |
| 포인터 | segmented far-ptr **sole**(max_hits=1) → pointer allowlist |
| 금지 | legacy `exp_spill` 전역 스캔, `seq_dict` inplace(기본 OFF), 이벤트 body 쓰기 |
| 검증 | `verify_script_banks_allowlist` ∪ opening body; `smoke_free_space_static.py` |
| legacy | `--placement legacy` — 회귀용만, 본선 금지 |

### 플레이 검증 (2026-07-17, 사용자 실측)

| 구간 | 상태 |
|---|---|
| 오프닝 나레이션 (타이틀+페이스줄 `6040B5` 포함) | **한글 OK** |
| 1스테이지 ~ 시그↔블레이드 중좌 대화 직전 | **한글 OK** (그 이후 순차 줄은 슬롯 부족으로 JP 잔존 — 의도적) |
| 인터미션 → 2스테이지 릭돔 | **이상 없음** (유닛 뱅크 tip≡JP 유지) |

### 오프닝 회귀 방지

페이스/인터스티셜(`08 xx 01 17 xx 18` 접두, 예: `6040B5`)은 ep3 시트에 없고 시드·
`tools/patch_opening_narration.py`의 `OPENING_INTERSTITIALS`에만 있다.  
`apply_opening_dedicated --include-seed-abs`는 시드 행을 **대상에 병합**하고,
인터스티셜 KO를 덮어쓴다. 스모크는 `6040B5`를 필수 샘플로 검사한다.

```text
python tools/snapshot_free_space_base.py --promote-tip
python tools/build_script_ko.py --placement free-space \
  --sheet out/script/translations_ep3_window.json
# phases: classify → free_space → opening_dedicated → verify
python tools/smoke_free_space_static.py
```
