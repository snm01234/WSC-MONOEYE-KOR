# 전투 팝업 글리프 구조와 한글 샘플

## 공용 renderer 구조

실측 없이도 공용 renderer의 descriptor directory를 정적으로 해독해 문자 팝업 전체를
byte-exact하게 구분할 수 있다.

- resource directory: `107554-107575`
- 문자 popup descriptor: `106458-107553`
- raw 4bpp atlas 기준점: `107572`
- tile 주소 공식: `107572 + tile_id × 0x20`
- 문자 전용 공용 풀: `107F52-108811`, 70타일/2,240바이트
- 16 MiB 현재 TIP의 물리 주소는 위 논리 주소에 모두 `+800000`
- 한글 샘플 작성 직전 현재 TIP의 descriptor와 tile pool은 원본과 byte-exact 일치

| Record | 식별된 요소 | 사용 tile ID |
|---|---|---|
| `106458-106611` | `Ｉフィールド` | `4F-58` |
| `106612-106883` | `ＩＦキャンセラー` | `4F,50 / 59-63 / 54` |
| `106884-1069EF` | `Ｆバリア` | `59,5A / 64-69` |
| `1069F0-106C6B` | `Ｐディフェンサー` | `6A-6D / 53 / 51,52 / 6E / 5E,5F / 6F,70 / 54` |
| `106C6C-106E51` | `ビームコート` | `71,72 / 54 / 73-76 / 58 / 77` |
| `106E52-107093` | `バイオフィールド` | `64,65 / 78-7B / 51-58` |
| `107094-107121` | `分身` | `7C-7F` |
| `107122-1072F9` | `クリティカル!` | `80,81 / 66,67 / 82 / 6D / 53 / 83,84 / 55,56 / 85,86` |
| `1072FA-10738F` | `ミス!` | `87-8A / 85,86` |
| `107390-10746F` | `月光蝶` | `8B-90` |
| `107470-107553` | `光発動` | `8D,8E / 91-94` |

특히 개별 원시는 다음처럼 확정된다.

- `分身`: `1084F2-108571`, 4타일/128바이트
- `ミス`: `108652-1086D1`
- `!`: `108612`, `108632` — `クリティカル!`과 공유
- `月光蝶`: `1086D2-108791`
- `光発動`: 공유 `光=108712,108732` + `発動=108792-108811`

중요한 정정 사항:

- 인접성만으로 잡았던 `ビームコート=108092…` 추정은 틀렸다. 실제 source 순서는
  `108392`, `1083B2`, `107FF2`, `1083D2`, `1083F2`, `108412`, `108432`,
  `108072`, `108452`다.
- `1086D2-108811`은 저장상 연속이지만 descriptor상 하나의 `月光蝶発動` record가
  아니다. `月光蝶`와 `光発動` 두 record이며 `光` 두 타일을 공유한다.

## Galmuri7 한글 메인 적용 (2026-08-12)

사용자 요청에 따라 의미식 재명명보다 원래 발음과 게임 용어를 유지했다.

| 원문 | 샘플 표기 |
|---|---|
| `Ｉフィールド` | `I-필드` |
| `ＩＦキャンセラー` | `IF캔슬러` |
| `Ｆバリア` | `F배리어` |
| `Ｐディフェンサー` | `P디펜서` |
| `ビームコート` | `빔코트` |
| `バイオフィールド` | `바이오필드` |
| `分身` | `분신` |
| `クリティカル!` | `크리티컬!` |
| `ミス!` | `미스!` |
| `月光蝶` | `월광접` |
| `光発動` | `빛발동` |

### 렌더링 사양

- 폰트: `assets/fonts/Galmuri7.ttf`, 8px
- 전경 palette index: `3`
- 그림자 palette index: `1`, 오프셋 `(+1,+1)`
- 8×16 OBJ 셀의 하단 8px에 Galmuri7의 본래 8px 글리프를 배치
- 원본에서 하단 tile이 없는 장음 `ー` 셀은 의도적인 단어 간격으로 사용
- 모든 mapping/animation descriptor tail은 부모와 byte-exact 보존

각 record의 로컬 타일 출력 순서는 그대로 둔 채 source-list 압축 경계만 다시 묶었다.
11종이 요구하는 패턴을 shortest-supersequence 방식으로 61타일에 수납했으며, 문서화된
`4F-94` 70타일 풀 밖의 데이터는 사용하지 않는다. 남은 9타일은 투명 blank다.

### 메인 ROM과 산출물

- 현재 메인 ROM: `out/patch/monoeye_ko_expanded.wsc`
  - SHA-256 `27321BDD4ED7FD6B35D56F80745D47946E2B517AADD83689D34C31B59694A483`
  - WonderSwan checksum `6E71`
- 현재 live SaveRAM: `sram/monoeye_ko_expanded.sav`
  - ROM-only 승격 전후 byte-exact
  - SHA-256 `D8D5E4B95A7C7E2761F78F7163DF2A17108989B1B03FBB36F033CC92086204C3`
- 승격 전 테스트 ROM `out/patch/battle_popup_glyphs_ko_galmuri7_sample.wsc`와 짝
  SaveRAM은 승격 후 정리됨
- 롤백 ROM:
  `out/patch/backup/20260812_104417_pre_battle_popup_glyphs_ko_galmuri7/monoeye_ko_expanded.wsc`
- 실기 변경 후보 SaveRAM 보존본:
  `out/patch/backup/20260812_104417_pre_battle_popup_glyphs_ko_galmuri7/battle_popup_glyphs_ko_galmuri7_sample_runtime.sav`
- 번역·배치 사양: `data/battle_popup_glyph_translations_ko.json`
- 빌더: `tools/build_battle_popup_glyphs_ko_sample.py`
- 독립 감사: `tools/audit_battle_popup_glyphs_ko_sample.py`
- 빌드 보고서: `out/patch/battle_popup_glyphs_ko_galmuri7_sample_report.json`
- 감사 보고서: `out/patch/battle_popup_glyphs_ko_galmuri7_sample_audit.json`
- 전체 원본/한글 비교:
  `out/patch/battle_popup_glyphs_ko_galmuri7_sample_previews/all_11_before_after.png`
- 한글 결과 모음:
  `out/patch/battle_popup_glyphs_ko_galmuri7_sample_previews/all_11_korean_sample.png`
- 사용자 승인: `out/patch/battle_popup_glyphs_ko_galmuri7_user_validation.json`
- 승격 도구: `tools/promote_battle_popup_glyphs_ko_galmuri7_candidate.py`
- 승격 보고서: `out/patch/battle_popup_glyphs_ko_galmuri7_promotion_report.json`
- 사후 독립 감사: `out/patch/battle_popup_glyphs_ko_galmuri7_postpromotion_audit.json`

### 검증 상태

- 11/11 record의 런타임 source sequence가 독립 Galmuri7 재렌더 결과와 exact 일치
- 11/11 mapping·animation tail 부모 byte-exact
- 모든 remap tile ID가 `4F-94` 안에 있음
- 대상 source-list, 70타일 pool, checksum 외 예상하지 않은 ROM diff 0
- 변경량: checksum 포함 1,580바이트
- checksum 정상, 현재 메인 TIP SHA-256이 승인 후보와 exact
- ROM-only 승격으로 live SaveRAM byte-exact 보존
- 승격 전 TIP `42051B18…`은 검증된 롤백 ROM으로 보존

## Galmuri11 Condensed 11px 비교 후보 (2026-08-12, 미선택·정리됨)

Galmuri7 결과가 원본 글자보다 작아 보인다는 사용자 피드백에 따라 같은 11종과 같은 원음
표기를 `Galmuri11-Condensed.ttf`의 네이티브 11px로 다시 렌더했다.

### 렌더링·수납 사양

- 폰트: `assets/fonts/galmuri_tmp/Galmuri11-Condensed.ttf`, 11px
- 기본 glyph top: `y=1`
- 전경 palette index: `3`
- 그림자 palette index: `1`, 오프셋 `(+1,0)`
- 원본에서 상·하단 source tile이 모두 존재하는 8×16 셀에만 11px 글리프 배치
- `I-필드`의 하이픈은 하단 전용 셀 안 `y=9`에 별도 배치
- mapping/animation descriptor tail은 Galmuri7 후보와 마찬가지로 부모 byte-exact

세로 그림자 `(+1,+1)`를 유지하면 최적 재분할 후에도 74타일이 필요해 70타일 풀을 넘는다.
인접 그래픽을 침범하거나 renderer를 훅하지 않기 위해 그림자를 우측 `(+1,0)`로 바꾸고,
11px 전용 source-list 분할을 별도 최적화했다. 결과는 **70/70타일**에 정확히 수납된다.

11px 글리프는 상·하단 두 타일을 모두 필요로 한다. 원본의 작은 가나·장음 칸 중에는 한쪽
타일만 존재하는 곳이 있으므로 `IF캔슬러`, `P디펜서`, `빔코트`, `바이오필드`,
`크리티컬!` 일부 글자 사이에 의도적인 빈 칸이 생긴다. 이 후보는 글자 크기·가독성과
간격의 trade-off를 실제 화면에서 비교하기 위한 테스트 ROM이다.

### 승격 전 테스트 ROM과 보존 산출물

- 정리된 ROM: `out/patch/battle_popup_glyphs_ko_galmuri11_condensed_sample.wsc`
  - SHA-256 `88FA56B83B919CC15475DAD35284CD2A1DD678120BB7E01E66E48F6B97066ED7`
  - WonderSwan checksum `7D1A`
- 정리된 SaveRAM: `sram/battle_popup_glyphs_ko_galmuri11_condensed_sample.sav`
  - 생성 시점 live SaveRAM byte-exact copy였음
  - SHA-256 `D8D5E4B95A7C7E2761F78F7163DF2A17108989B1B03FBB36F033CC92086204C3`
- 보존 SaveRAM:
  `out/patch/backup/20260812_104417_pre_battle_popup_glyphs_ko_galmuri7/battle_popup_glyphs_ko_galmuri11_condensed_sample_runtime.sav`
- 번역·배치 사양:
  `data/battle_popup_glyph_translations_ko_galmuri11_condensed.json`
- 공용 빌더: `tools/build_battle_popup_glyphs_ko_sample.py`
- 공용 독립 감사: `tools/audit_battle_popup_glyphs_ko_sample.py`
- 빌드 보고서:
  `out/patch/battle_popup_glyphs_ko_galmuri11_condensed_sample_report.json`
- 감사 보고서:
  `out/patch/battle_popup_glyphs_ko_galmuri11_condensed_sample_audit.json`
- 전체 원본/11px 비교:
  `out/patch/battle_popup_glyphs_ko_galmuri11_condensed_sample_previews/all_11_before_after.png`
- 11px 한글 결과 모음:
  `out/patch/battle_popup_glyphs_ko_galmuri11_condensed_sample_previews/all_11_korean_sample.png`

### 검증 상태

- 빌더 self-check와 독립 감사 모두 `ok=true`
- 11/11 런타임 source sequence가 독립 Condensed 11px 재렌더와 exact
- 11/11 mapping·animation tail 부모 byte-exact
- remap tile ID 70/70이 모두 `4F-94` 안에 있음
- source-list, 70타일 pool, checksum 외 예상 diff 0
- 변경량: checksum 포함 1,565바이트
- checksum 정상, 후보 SaveRAM은 생성 시점 live SaveRAM과 exact
- 사용자 선택에서 Galmuri7이 승인되어 Condensed ROM·짝 SaveRAM은 정리됨

이 파일은 정적 구조와 적용 이력의 정본이다. 사용자 선택에 따라 Galmuri7 8px가 메인 TIP에
승격됐고, Galmuri11 Condensed 11px 비교 ROM은 미선택 후보로 정리됐다.
