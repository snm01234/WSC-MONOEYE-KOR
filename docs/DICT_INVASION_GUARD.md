# 사전·원본 데이터 침범 방지 원칙

작성: 2026-07-17  
대상 tip: `out/patch/monoeye_ko_expanded.wsc` (16 MiB · FF-page ext dict)

대사/UI에 **다른 장면의 한글**이 끼는 사고는 대부분 “슬롯 소유권을 좁게 보고 덮어쓴 뒤, 원본 바이트·공유 소비자를 방치”한 결과다.  
이 문서는 **원본 데이터 침범**과 **풀라인 overshare**를 한 세트로 다룬다.

관련: `.cursor/rules/dict-invasion-guard.mdc` · `docs/ROM_16MB_EXPANSION.md` §13 (false exp_spill)

---

## 1. 침범 유형

| 유형 | 메커니즘 | 대표 증상 |
|---|---|---|
| **A. FF-page × 원본 UI** | ext 토큰 `FF xx`(idx `0xF00\|xx`)가 전투/UI 뱅크(50–5F, 76) zstring에 **원래부터** 들어 있음. 바닐라에선 BADDICT/비대사, tip에 Hangul을 올리면 **도움말·튜토리얼에 스토리 대사 삽입** | 튜토리얼 전투 2번째 줄에 「갑자기 애칭으로…」(구 FF63) |
| **B. full-line overshare** | 한 Hangul 슬롯이 여러 **서로 다른 JP** 대사의 순수 토큰 body | 초반 KO가 중후반에 재등장 |
| **C. sole residue** | script-only 스캔으로 “sole” 판정 → stock 슬롯 덮기. aux/name75 소비자 잔존 | dict[21] 등 전투 HUD에 장문 대사 |
| **D. false exp_spill** | 포인터 오인으로 유닛/시나리오·이벤트 테이블 이동 (deny 밖 50–5B/6A–6B 포함) | 잘못된 게스트 MS (야크트도가↔Z건담 등) — §13 |
| **E. 데이터 영역을 대사로 오인** (2026-07-27) | 추출기가 **뱅크 64–69**를 zstring으로 걸어 레코드를 만든다. 실제로는 고정 간격 데이터 테이블이고, 원본 텍스트가 한 골격을 반복한다(`をん…買の…`, `…校の…`, `…尊の…`, `…俵の…`). 그 위에 `prefix + 토큰 + 0x01 패딩`을 쓰면 이벤트 인터프리터가 패딩을 옵코드로 걷는다 | 뉴게임 **이벤트 에러 257(`0x0101`)/2049(`0x0801`)**, 유닛 이동 시 프레임 글리프 깨짐 + 엉뚱한 일본어 |
| **F. 마커 코드 × 원본 문자** (2026-07-27) | 폰트 훅의 한글 런 마커가 실제 문자 코드와 같으면, 그 문자를 포함한 **원본** 문자열에서 공유 훅이 sticky 한글 플래그를 세운다. 구 마커 `E3DB` = `映`, 원본 텍스트 뱅크에 10곳 | 유닛·파일럿 설명 화면과 전투 대사의 글리프 깨짐 |

E의 판별 기준: 변경 레코드의 **디코드된 원본 텍스트 시작 2글자** 분포를 뱅크별로 본다. 정상 대사 뱅크(60–63)는 최다가 `……` 14–15%인데, 64–67은 단일 골격 `をん`이 23–38%를 차지한다. `tools/scan_table_motif_records.py`.

F의 대응: 마커는 **원본 텍스트 뱅크 출현 0회**인 2바이트쌍이어야 한다(리드 `0xE0`–`0xEF`, 패딩 글리프 대역 밖, ext3 매직 아님). `tools/retarget_hangul_marker.py`가 조건을 재검사하고 원본 자신의 출현 사이트를 보호한다. 현재 마커는 `EC80`이며 값은 `tools/hangul_marker.py`를 통해 `hangul_char_map.json`에서 읽는다 — 상수를 복사하지 말 것.

A는 “소비자 restore”만으로는 안 고쳐진다. tip·원본 **페이로드 바이트가 동일**하고, tip 사전 훅이 `FF`를 확장 페이지로 해석하기 때문이다.

### 소비자 스캔은 원본 ROM 기준으로 (2026-07-27 실측)

C형 가드가 이미 aux/name75를 포함하고 있었는데도 공유 슬롯 458개가 하이재킹된 이유가 밝혀졌다. **소비자 목록을 작업 ROM 기준으로 만들었기 때문**이다. 작업 ROM에는 스톡 침범으로 zstring 종료자가 깨진 aux 레코드가 있었고(`51:CAF5` 등 34바이트), aux 워크가 그 뒤 레코드를 놓쳐 공유 슬롯이 sole로 보였다. 침범이 가드를 무력화하고, 무력화된 가드가 UI를 깨는 연쇄다.

따라서 reclaim/steal/shared rewrite의 참조 스캔은 **원본 8 MiB와 작업 ROM의 합집합**으로 판정한다(`apply_sole_reclaim_early.py --ref-rom`, 기본값이 원본). 한쪽에서라도 참조가 보이면 후보에서 제외한다.

같은 이유로 **복원 기준을 `monoeye_ko_expanded_8mb.wsc`로 삼지 않는다.** 침범이 그 백업에 박혀 있어 모든 재빌드가 상속했다. 기준은 항상 원본 8 MiB다.

---

## 2. 스캔 스냅샷 (2026-07-17)

```bash
python tools/scan_aux_ff_invasion.py
python tools/scan_invasion_full_line_tokens.py
```

| 지표 (`aux_ff_invasion_scan.json`) | 수 |
|---|---:|
| ext Hangul + aux 토큰 히트 (raw) | 242 |
| tip KO가 aux expand에 **확정 삽입** | **236** (FF-page 230) |
| bank 53 확정 | 135 |
| stock Hangul + aux 확정 | 265 (sole-style 108) |
| full-line early+other (품질 시트) | 수리 후 **0** (별도 스캔) |

확정 조건: tip KO 조각이 aux expand에 포함되고, 대사형 tip 또는 JP/Hangul stew / stock-only BADDICT.

**최악 예 (ext):** FF0F「……알겠습니다！！」aux×114 · FF01「어서 오세요…」×97 · FFFE「알겠습니다！」×92 · 튜토리얼 대역 `530337` 근처 FFF6/FF9F 등.

---

## 3. 하드 원칙 (작업 전 체크리스트)

1. **참조 스캔은 항상** `regions=("script","name75","aux")`  
   (`expand_dictionary.DEFAULT_REF_REGIONS`, `AUX_TOKEN_BANKS` = 50–5F + 76).  
   script-only sole/steal/rewrite **금지**.

2. **Fail-closed**  
   - aux/name75 히트 → sole/free 아님.  
   - keeper 밖 script 소비자 남음 → rewrite/steal 거부 (`slot_rewrite_refuse_reason`).  
   - late restore / preserve-retarget 실패 → **ROM 커밋 금지**.  
   - Original은 vanilla token 규칙, ext3-enabled Working은 runtime 우선순위(`E5 19` compact3 → `E5 18` ext3 → 2-byte token → glyph)로 각각 스캔한다. `E5 18 FE FB`의 tail `FE FB`를 index `0EFB` 소비자로 중복 집계하면 안 된다.

3. **FF-page에 스토리 Hangul을 올릴 때**  
   - 해당 idx가 aux zstring에 이미 있으면 **대화 전용 KO 금지**.  
   - UI/튜토리얼에 필요한 조각만 두거나, 슬롯을 **무력화(공백 등)** 하고 스토리 라인은 **충돌 없는 슬롯으로 이동**.  
   - “aux raw에 FF trail이 있다”만으로 전 ext 금지하지 말 것 — **파싱된 zstring 소비자**가 기준.  
   - 확정 침범: tip KO ⊆ aux expand 이고 stock-only가 BADDICT/JP stew.

4. **풀라인 overshare**  
   - 초반 대역(`≈0x6040A5–0x607000`) keeper 고정.  
   - 후반·타 JP 소비자는 JP 복원 또는 **별도 슬롯**.  
   - 동일 JP인데 tip KO만 틀리면 슬롯 rewrite.

5. **pair-steal 우선** (curated): S←새 KO, T←옛 payload, 모든 former(script/name75/aux)→T.  
   preserve 실패 시 abort.

6. **false pointer / spill**  
   deny 뱅크·유닛 테이블을 대사 far-pointer로 쓰지 말 것 (`ROM_16MB_EXPANSION.md` §13).

7. **수리 도구**  
   - A/UI stew: 스토리 라인 이사 + 충돌 슬롯 무력화/UI 조각 복구 (애칭·FF63 사례).  
   - B: `tools/repair_dict_overshare.py`  
   - C: `tools/repair_unsafe_sole_owner_ko.py`  
   - 진단: `scan_aux_ff_invasion.py`, `scan_invasion_full_line_tokens.py`, `scan_dict_invasion.py`

---

## 4. 코드 게이트 (쓰기 경로)

모든 Hangul 사전 쓰기는 아래를 통과해야 한다.

| 계층 | 동작 |
|---|---|
| `guard_hangul_slot_writes` | Hangul(marker) + aux/name75 live → **기본 거부**. marker는 `hangul_marker.marker_code()`가 단일 출처이며 현재 `EC80`; legacy `E3DB`도 fail-closed 감지 |
| `write_dictionary_slots_spill` / `write_exp_dictionary_slots` / `write_ext_dictionary_slots` | 위 게이트 내장 (`allow_aux_consumers=True`만 예외). P2 stock spill은 교정된 Original+Working full-union `locs`를 writer에 직접 전달해야 함 |
| `verify_stock_noninvasion --approved-stock-report` | stock pointer 신규 이동은 candidate SHA-bound 증거가 union true-free, all-FF tail, 선택 pointer 정확 일치, 비선택 pointer/payload 보존, 5F approved extent를 모두 증명할 때만 허용. 자식 후보의 부모-bound 승인은 해당 pointer/payload가 부모와 byte-identical일 때만 상속 |
| `verify_stock_noninvasion --approved-detachment-report` | 동일-payload duplicate 및 retired-slot 회수는 candidate/parent SHA에 결속한다. duplicate는 모든 역사적·현재 소비자, detachment-only old-ref 0, former-render 보존을 요구한다. retired slot은 Original/current pointer·payload 동일, Original nested 0, current external/nested/raw-pair 0, 역사적 소비자 전수 회계를 추가로 요구한다. 누적 자식 승인은 이전 stock pointer/payload와 detachment range가 부모→자식에서 byte-identical일 때만 ownership/range를 상속 |
| `verify_nondialogue_text` / `verify_all_stages_smoke` | detachment/retired range를 전역 allowlist로 넣지 않는다. candidate-bound 승인과 부모 후보를 함께 받아 former consumer의 부모-vs-자식 렌더링 동일성 및 정확한 byte range만 독립 검증. retired 신규 name75 target은 승인 파일의 정확한 body range와 target record ID만 별도 허용한다. nested parent 변경은 해당 사전 payload의 렌더·길이·terminator와 역참조 간접 consumer까지 검증 |
| `scan_script_record_structure --approved-local-expansion-report` | regular ext3 local 확장은 candidate/parent SHA, Original·부모의 연속 NUL 2개, `new_term=old_term+1`, `next_start=new_term+1`, 다음 레코드 byte-identical proof가 있을 때만 정확한 `+1` terminator 이동을 허용. 다른 구조 변화는 계속 실패 |
| `filter_story_safe_indices` | ext/stock 할당 풀에서 aux live idx 제거 |

예외(`allow_aux_consumers=True`): UI·고유명사 공유, 모든 former consumer를 먼저 detach하고 별도 후보-bound proof로 보존한 curated 작업, repair 툴. 단순 aux hit 무시는 예외가 아니다.

## 5. 도구별 기본 자세

| 도구 | 원칙 |
|---|---|
| `steal_late_ext_to_early` | full-region 스캔 · restore_fail abort |
| `apply_safe_unit` | aux hit 슬롯 shared/sole 거부 · tip에서 shared rewrite 신중 |
| `apply_opening_dedicated` | free pool에서 aux/name75 참조 idx 제외 |
| `apply_curated_abs_batch` | preserve_retarget 실패 시 abort · write는 allow_aux(이어서 migrate) |
| `apply_sole_reclaim_early` | DEFAULT_REF_REGIONS 강제 · tip에서 기본 off · spill 게이트 |
| `apply_ext_dict_unit` | **safe_indices에서 aux live 제거** · stock reclaim `require_free` |
| `apply_stock_extra_reclaim` | free stock `require_free` 재검증 |
| `build_p2_stock_spill_candidate` | full 5F rebuild 금지 · 검증된 all-FF tail에 append · 선택 pointer만 이동 · candidate-bound approval 생성 |
| `build_p2_duplicate_detach_candidate` | 동일 raw payload 그룹 하나만 처리 · former consumer를 keeper로 먼저 치환 · detachment-only old-ref 0/렌더 보존 후에만 reclaim slot payload/pointer 변경 · 부모 stock/detachment 승인을 candidate SHA와 byte-identical pointer/payload/range 검증으로 누적 상속 |
| `analyze/build_p2_duplicate_batch` | inherited keeper/owned slot을 보호하고 payload 그룹당 reclaim 하나만 선택 · zero-nested 또는 명시적으로 bounded된 nested parent의 token site 비중첩을 증명 · nested 물리 범위를 덮는 모든 alias 및 parent 역참조 폐쇄를 확인 · 모든 reclaim을 한 detachment-only 상태에서 old-ref 0/전체 direct·alias·indirect 렌더 보존 후에만 일괄 spill · cumulative approval은 전체 stock/range를 자식 SHA에 재결속 |
| `analyze/build_p2_local_ext3_expansion` | 기존 regular `E5 18 xx yy`만 사용 · body 3 뒤 Original/부모에서 안정된 `00 00`이 있고 다음 레코드가 두 번째 NUL 다음에서 시작할 때만 첫 NUL을 token byte로 소비 · true-free ext3 slot의 선택 pointer/append payload만 쓰고 runtime·compact3·stock 5F는 변경 금지 · 다음 레코드 전체 payload/terminator 보존 필수 |
| `analyze/build_p2_retired_slot_reclaim` | Original에서는 참조됐지만 현재 candidate에서 external/nested/raw-pair가 모두 0인 stock 슬롯만 분리. Original/current pointer·payload byte-identical, Original nested 0, 누적 ownership 비중첩을 증명하고 역사적 consumer를 전수 회계한 뒤 선택 pointer만 all-FF 5F tail로 이동. ordinary 2-byte token만 사용하며 runtime·FF-page·terminator 변경 금지. `75:B000–75:C000` bank-75 UI/stage table도 반드시 current raw-pair/external 검사에 포함 |
| `build_p2_slot0208_stage_name_repair_candidate` | 숨은 bank-75 소비자로 오염된 shared slot `0208`을 pre-reclaim `공역` payload로 복구하고, 사용되지 않은 strong retired `033F`가 기존 orphan `오오！` payload를 가리키게 한다. `오오！` 3레코드만 `F208→F33F`; 신규 payload/runtime/terminator/FF-page/far-pointer 쓰기 0. 후보 SHA-bound proof에서 스테이지명 4곳과 대사 3곳을 모두 재검증 |
| `analyze_p2_remaining_routes` | retired-current-unreachable 분류 도입 전의 중간 NO-GO 보고서. non-FF true-free/pair-steal/duplicate/FF-page/compact3/pointer 경로 비교 기록으로 보존하되 최종 P2-1 결정은 retired-slot candidate-bound 승인으로 대체 |
| `mixed_residual_reference_union` | Original+Working 전체 영역과 nested parent 합집합. script/legacy name75/aux뿐 아니라 순차 bank-75 UI·스테이지명 테이블 `75:B000–75:C000`도 name75 scope로 포함한다. `iter_token_refs_with_offsets`는 Original vanilla / Working ext3 runtime 우선순위와 정확한 token offset을 분리해 ext3 portal tail raw pair를 쓰기 대상으로 삼지 않음 |

---

## 6. 인코딩 메모 (FF 페이지)

| 토큰 | 인덱스 | 비고 |
|---|---|---|
| `F0–FE yy` | `0x000–0xEFF` | stock / 저확장 |
| `FF yy` | `0xF00–0xFFF` | **16 MiB tip ext**. 원본 UI 바이트와 자주 충돌 |
| trail `00` | — | zstring NUL과 충돌 → `dict_token_safe_in_zstring` false |

바닐라 8 MiB Dictionary는 `FF yy`를 BADDICT로 두는 경우가 많다. tip에서 Hangul을 넣으면 **같은 바이트가 갑자기 “문장”이 된다**.

---

## 7. 작업 후 최소 검증

1. `python tools/scan_invasion_full_line_tokens.py` → early+other ≈ 0  
2. `python tools/scan_aux_ff_invasion.py` → 신규 high 증가 없는지 확인  
3. 시드/오프닝 Hangul · `dict[21]` 등 기지 sole 슬롯  
4. 튜토리얼 전투·교신 도움말(bank 53 부근) 실측: 스토리 애칭/무관 장문이 안 섞일 것  

보고서: `out/patch/aux_ff_invasion_scan.json`, `out/patch/invasion_full_line_tokens.json`, `out/patch/invasion_audit_summary.json`.
