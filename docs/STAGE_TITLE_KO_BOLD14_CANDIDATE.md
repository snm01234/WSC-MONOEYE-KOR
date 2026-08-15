# 스테이지 타이틀 Galmuri11-Bold 14px 실측 후보 및 승격 기록

작성일: 2026-08-10

## 결과

사용자가 기존 11px 후보의 한글 표시 자체는 정상임을 실측했지만, 원본보다 작고 얇으며
`만남→민남`, `각성→각싱`처럼 오인될 수 있다고 확인했다. 검토 결과에 따라 43개 실제
타이틀 전체를 다음 규격으로 다시 만들었다.

- 폰트: `assets/fonts/galmuri_tmp/Galmuri11-Bold.ttf`
- 폰트 SHA-256: `5265B2F437FE81F0C8095B44C0173DD9A276B58A42552BF983F21C0E69E6E8AF`
- 크기: 14px
- 자간: 0px
- 2행 간격: 4px
- 세로 오프셋: 0px

이 후보는 2026-08-10 사용자 실측에서 이상 없음으로 승인되어 같은 날 메인 TIP으로
승격되었다. 아래 후보 정보와 검증 항목은 승격 전 정적 게이트의 역사적 기록이다.

## 파일과 식별값

- 부모 TIP: `out/patch/monoeye_ko_expanded.wsc`
  - 16,777,216 bytes
  - SHA-256 `9402F7EFC1C557746015EB6352799A79F7F66FEBF1EB0AD4039734028A16A9F2`
- 후보 ROM: `out/patch/stage_title_ko_bold14_candidate.wsc`
  - 16,777,216 bytes
  - SHA-256 `87BD754D3F4AF65F3D02A274D94E962E0BF2F0313C491096407DFC9C8D1A4F93`
  - WonderSwan checksum `95F5`
- 짝 SaveRAM: `sram/stage_title_ko_bold14_candidate.sav`
- 사양: `data/stage_title_translations_ko_bold14.json`
- 보고서: `out/patch/stage_title_ko_bold14_candidate_report.json`
- 미리보기: `out/patch/stage_title_ko_bold14_candidate_previews/stage_title_ko_contact.png`

## 문자 호환 수정

`Galmuri11-Bold.ttf`는 일본식 가운뎃점 `・`(U+30FB)을 지원하지 않아 미지원 글리프
상자로 표시한다. STAGE06 한글 표기만 지원되는 한국어 가운뎃점 `·`(U+00B7)으로 바꿨다.

- 변경 전: `우주 요새 아・바오아・쿠`
- 변경 후: `우주 요새 아·바오아·쿠`

사용자 요청에 따라 SPECIAL03 `蒼を継ぐ者`의 번역도 `푸름을 잇는 자`에서
`블루를 계승하는 자`로 수정했다.

43개 한글 타이틀에서 사용하는 공백 포함 148개 문자를 Bold 14px로 전수 검사했으며,
변경 후 미지원 코드포인트는 `0`개다. 일본어 원문 필드는 변경하지 않았다.

## 정적 검증

| 패키지 | 타이틀 | 그래픽 용량 | 후보 할당 | 잔여 타일 |
|---|---:|---:|---:|---:|
| `4ABD0C` | 15 | 449 | 382 | 67 |
| `4DB4B8` | 15 | 488 | 417 | 71 |
| `53B9F4` | 13 | 393 | 368 | 25 |
| 합계 | 43 | 1,330 | 1,167 | 163 |

- 3개 패키지와 43개 타이틀 후보 재파싱 성공
- 목표 Bold 14px 마스크 대 ROM 재구성 화면 픽셀 차이 모두 `0`
- 디스크립터 포인터, 레이아웃 마커, 꼬리 메타데이터 byte-exact 보존
- 변경 범위는 3개 디스크립터 블록, 3개 그래픽 블록, 최종 체크섬뿐
- 범위 밖 변경 런 `0`
- 총 변경 바이트 `26,525`, 변경 런 `5,781`
- WonderSwan 체크섬 유효
- 빌드 후 부모 TIP SHA-256 불변

## 실측 항목

1. STAGE01 `만남`: `ㅏ/ㅣ`, 받침 `ㄴ/ㅁ` 판독
2. STAGE03 `각성`: `성`이 `싱`처럼 보이지 않는지
3. STAGE06 `우주 요새 아·바오아·쿠`: 가운뎃점과 전체 폭
4. EX01·EX02: 2행 전편/후편과 행 간격
5. STAGE15n: `석파천경권!` / `결투 마스터 아시아` 2행
6. STAGE16n `다카르의 등불`: 복합 받침과 긴 제목 판독
7. STAGE19n·19t·20t 및 STAGE20n·21n: 기본/전/후 변형
8. STAGE23, SPECIAL07: 긴 한 행 제목의 잘림 여부
9. SPECIAL03: `블루를 계승하는 자` 번역과 중앙 정렬

각 화면에서 글자 잘림, 중앙 정렬, 팔레트, 페이드, 다음 화면 그래픽 오염을 함께 확인했고,
사용자가 **“실측 이상 없습니다”**라고 승인했다.

## 메인 TIP 승격

- 승격 시각: 2026-08-10 14:41:53 +09:00
- 승격 후 메인: `out/patch/monoeye_ko_expanded.wsc`
- 메인 SHA-256:
  `87BD754D3F4AF65F3D02A274D94E962E0BF2F0313C491096407DFC9C8D1A4F93`
- WonderSwan 체크섬: `95F5`
- 롤백 ROM:
  `out/patch/backup/20260810_144153_pre_stage_title_ko_bold14/monoeye_ko_expanded.wsc`
- 승격 보고서: `out/patch/stage_title_ko_bold14_promotion_report.json`
- 사후 감사: `out/patch/stage_title_ko_bold14_postpromotion_audit.json`

기존 메인 ROM은 SHA-256
`9402F7EFC1C557746015EB6352799A79F7F66FEBF1EB0AD4039734028A16A9F2`로 백업되었다.
`data/stage_title_translations_ko.json`도 Bold 14px 승인 사양으로 갱신했으며, 이전 11px
정본 사양은 위 백업 폴더에 함께 보존했다.

메인 SaveRAM은 교체하지 않았고 승격 전후 SHA-256
`589F47D18CBE245E544F62A92542EEDAED87895794AAF072B3071D7442CDE4A4`로 동일하다.
실측 과정에서 갱신된 `stage_title_ko_bold14_candidate.sav`는 런타임 상태 유실을 막기 위해
짝 ROM과 함께 보존했다.
