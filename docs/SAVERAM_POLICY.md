# SaveRAM 운영 규칙

업데이트: 2026-08-09

## 정본

`sram/monoeye_ko_expanded.sav`는 배포용 불변 산출물이 아니라 사용자가 계속
검증에 사용하는 **실시간 SaveRAM**이다. 에뮬레이터 저장으로 내용과 SHA-256이 수시로
바뀔 수 있으며, 작업 시점에 존재하는 현재 파일을 항상 최신 정본으로 간주한다.

## 테스트 ROM 생성

별도 테스트 ROM `out/patch/<name>.wsc`를 만들 때는 생성 직전의
`sram/monoeye_ko_expanded.sav`를 `sram/<name>.sav`로 복사한다. ROM과 SaveRAM의
stem은 같아야 한다.

테스트 SaveRAM에 대해서는 과거 보존 해시와의 일치, 메인 SaveRAM과의 byte-identical
일치, 특정 체크섬을 승인 조건으로 사용하지 않는다. 빌드가 오래된 경우에는 테스트
SaveRAM을 복원하지 말고 현재 메인 SaveRAM을 다시 복사한다.

## TIP 승격

TIP 승격은 `.wsc`만 원자 교체한다. `sram/monoeye_ko_expanded.sav`는 승격 과정에서
덮어쓰거나 과거 백업으로 복원하지 않는다. 승격 전후 SaveRAM 해시는 검증 게이트가
아니며, 보고서에 기록하더라도 참고값으로만 취급한다.

ROM 롤백 역시 `.wsc`만 대상으로 한다. SaveRAM은 현재 파일을 계속 사용한다.

## 금지 사항

- 고정 SHA-256을 이유로 `sram/monoeye_ko_expanded.sav`를 과거 백업으로 자동 복원하지 않는다.
- 후보 `.sav`의 변경을 오류나 오염으로 간주하지 않는다.
- 에뮬레이터가 SaveRAM을 다시 썼다는 이유만으로 ROM 빌드·승격을 중단하지 않는다.
- 오래된 후보 `.sav`를 메인 SaveRAM에 역복사하지 않는다.

과거 복원용 `tools/restore_ui_followup_saveram.py`는 안전한 no-op으로 폐기했다.
