# 번역 출처 정책

제정: 2026-08-03

## 목적

기존 프로젝트에는 서로 다른 시기에 생성된 번역이 한 시트와 파생 JSON에 섞여 있다.
특히 `out/script/excel_translate_cache.json`은 `engine: bing`으로 기록되어 있고,
`tools/excel_batch_translate.py`는 Excel/Azure/Bing/Google Translate를,
`tools/translate_splits_llm.py`는 이름과 달리 Google Translate API를 사용한다.
이 데이터는 LLM 문맥 번역이나 사람 검수와 출처를 구분할 수 없으므로 더 이상 정본
번역으로 취급하지 않는다.

## 현재 정본

- 실행 기준선은 항상 승격된 `out/patch/monoeye_ko_expanded.wsc`다.
- 일반 패치 작업은 이 메인 TIP에서 새 후보를 파생한다.
- 신규 번역은 범위가 좁고 주소가 명시된 `data/*_ko.json` 사양으로 적용한다.
- 사용자 실화면 검수 또는 독립 감사에 통과한 후보만 메인 TIP으로 승격한다.

현재 메인 TIP을 과거 전체 번역 시트로 다시 빌드해 덮어쓰지 않는다. 과거 시트에는
이미 해결한 구조 오인 번역, 구형 기계번역, 최신 수정 이전 문구가 섞여 있기 때문이다.

## 격리 대상

정확한 목록은 `data/translation_source_policy.json`과
`out/patch/translation_source_policy_audit.json`에 기록한다.

대표 격리 대상은 다음과 같다.

- `out/script/excel_translate_cache.json`
- `out/script/translation_sheet.csv` (주소/JP forensic 입력으로 활성 경로에 유지, 적용 차단)
- `out/script/translations_quality_all.json` (런타임 계약 주소 목록 forensic 입력, 적용 차단)
- 중복 파생본은 `legacy/out/script/`로 물리 이동: `translation_sheet_partial.csv`,
  `translation_sheet_probe.csv`, `translations_apply_all.json`,
  `translations_ep3_window.json`, `translations_quality.json`
- `out/script/splits/`
- 위 시트에서 생성된 `translations_full*`, `translations_quality*`, `translations_ep*`
- `tools/excel_batch_translate.py`
- `tools/translate_splits_llm.py`
- `tools/apply_translate_cache.py`
- `tools/archive/retranslate_quality_json.py`

파일은 주소·일본어 원문·과거 상태를 확인하는 감사 증거로만 보존한다. 번역 병합,
ROM 적용, 메인 빌드 입력으로는 사용할 수 없다.

## 강제 규칙

`tools/translation_source_policy.py`가 중앙 정책을 읽어 다음 동작을 차단한다.

1. 기존 `translation_sheet.csv`를 `sheet_to_translations.py`로 변환
2. 기존 `out/script/splits/`를 `merge_csv.py`로 재병합
3. 기존 시트를 `build_monoeye_ko_all.py`의 통합 빌드 입력으로 사용
4. Bing/Excel/Google 캐시를 `apply_translate_cache.py`로 시트에 병합
5. `excel_batch_translate.py`와 `translate_splits_llm.py`로 새 기계번역 생성

격리 파일을 단순히 이름만 바꿔 우회하지 않는다. `out/script/translation_sheet*`와
`out/script/translations*` 계열은 승인된 미래 정본 시트를 제외하고 기본 거부한다.

## 새 LLM/검수 시트 규격

전체 시트 재구축이 필요할 때는 다음 파일만 사용한다.

`out/script/translation_sheet_llm_reviewed.csv`

이 시트에서 생성하는 중간·적용 JSON도 기존 `translations_full*` 계보를 덮어쓰지 않고
`out/script/translations_llm_reviewed_*` 이름만 사용한다.

모든 비어 있지 않은 `ko` 행에는 다음 열이 필수다.

| 열 | 허용 값 | 의미 |
|---|---|---|
| `translation_source` | `human`, `llm`, `user_verified`, `curated_project_data` | 번역 생성 출처 |
| `review_status` | `approved`, `user_verified` | ROM 적용 승인 여부 |

출처나 승인 상태가 비어 있거나 허용 값이 아니면 변환과 빌드를 중단한다. 가능하면
`source_model`, `reviewed_at`, `review_note` 같은 보조 열도 추가한다.

## 이행 절차

1. 격리 시트에서는 주소와 일본어 원문만 참고한다.
2. 필요한 행을 LLM으로 새로 번역하거나 사람이 직접 교정한다.
3. 새 정본 시트에 번역 출처와 승인 상태를 기록한다.
4. 필요하면 `out/script/llm_reviewed_splits/`만 명시적으로 병합한다. 기존
   `out/script/splits/`는 사용하지 않는다.
5. `python tools/audit_translation_sources.py`를 실행한다.
6. 감사 결과가 깨끗한 경우에만 전체 재빌드를 검토한다.
7. 그 전까지는 메인 TIP 기반의 좁은 후보 패치 방식을 유지한다.

## 삭제 정책

구형 기계번역 파일은 현재 ROM을 재현하거나 오염 경로를 추적하는 증거가 될 수 있어
즉시 물리 삭제하지 않는다. 대신 실행 경로에서 격리하고 참조를 차단한다. 향후 새 LLM
정본 시트가 완성되고 필요한 주소·원문이 모두 이전되면, 별도 정리 보고서와 백업을 남긴
뒤 격리 파일을 일괄 삭제할 수 있다.
