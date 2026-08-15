# legacy

현재 메인 TIP 적용 경로에서 쓰이지 않는 과거 번역 시트·테스트 ROM·후보 JSON을 삭제하지 않고 원래 상대 경로를 유지한 채 보관한다.

- 기준 TIP: `out/patch/monoeye_ko_expanded.wsc`
- 기준 SHA-256: `D7543AD4A62D9E7A9687583E85005DC4CA137E6FA62238EB70E58492248985C9`
- 매니페스트: `legacy/legacy_asset_manifest.json`
- 복원: 매니페스트의 `archive_path`를 `path`로 되돌린다.
- `out/script/translation_sheet.csv`, `excel_translate_cache.json`, `translations_quality_all.json`은 계약/리뷰 forensic 입력이라 활성 경로에 남긴다. 적용(apply/merge/rebuild) 입력으로는 계속 차단된다.
