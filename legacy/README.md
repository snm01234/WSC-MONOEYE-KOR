# legacy

현재 메인 TIP 적용 경로에서 쓰이지 않는 과거 후보 ROM, 테스트 JSON, 감사 스냅샷 등은 삭제하지 않고 `legacy/` 아래 로컬 보관합니다.

- 현재 릴리스 기준: **v1.2**
- 기준 TIP: `out/patch/monoeye_ko_expanded.wsc`
- 기준 SHA-256: `C7BB4B5C936653888062F2389351C586FC483DEDACDBA209918B327E440E2131`
- 공개 요약 매니페스트: `legacy/legacy_asset_manifest.json`
- v1.2 테스트 산출물 상세 매니페스트: `legacy/v1_2_test_artifacts_20260817/manifest.json` (Git 제외)
- v1.2 생성 감사/런타임 계약 상세 매니페스트: `legacy/v1_2_generated_reports_20260817/manifest.json` (Git 제외)
- 복원: 각 상세 매니페스트의 archive/target 경로를 원래 source 경로로 되돌립니다.

실제 ROM/SaveRAM/대형 진단 산출물은 `.gitignore`에 의해 공개 저장소에서 제외합니다. Git에는 이 README와 요약 매니페스트만 남깁니다.
