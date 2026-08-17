# 에마 신 용어 표준화 작업 시트 — 2026-08-17

## 표준

- 일본어: `エマ・シ－ン` / `エマ・シーン` / `エマ`
- canonical Korean: `에마 신`
- 단독 호칭: `에마`
- 금지 표기: `엠마 신`, `엠마`
- 결정: user_confirmed

표준 데이터: `data/gundam_terminology_standard_ko.json`

## 활성 UI override

`data/ko_ui_overrides.json`의 다음 6개 활성 표기를 갱신했다.

| 기존 | 표준화 |
|---|---|
| `엠마 신.` | `에마 신.` |
| `… … 엠마 중위!` | `… … 에마 중위!` |
| `엠마` | `에마` |
| `… … 엠마 중위?` | `… … 에마 중위?` |
| `엠마 설득: 교신` | `에마 설득: 교신` |
| `엠마 중위…` | `에마 중위…` |

정책상 `data/dialogue_legacy_mt_literal_batch014.json`, `out/script/excel_translate_cache.json` 등 과거/격리 MT 스냅샷은 활성 적용 입력이 아니므로 역사 증거로 보존하며 수정하지 않았다.

## ROM dictionary phrase 변경

부모 ROM: `out/patch/global_scenario_mixed_exact4_59_candidate.wsc`

부모 SHA-256: `3B5CC0DE88874A1138D6336262C8CCC10F844B34D6779B9E7D4BBBABC5B642E7`

`엠마` direct Hangul sequence는 부모 ROM 전체에서 정확히 5곳이었다. 동일 길이 `에마` sequence로 in-place 치환했다. dictionary pointer, record extent, control/portrait bytes, runtime hook은 변경하지 않았다.

| dictionary index | entry abs | 기존 렌더 | 변경 렌더 |
|---|---:|---|---|
| `04C8F` | `024E4BB` | `티탄즈의 엠마 신 중위입니다。` | `티탄즈의 에마 신 중위입니다。` |
| `01295` | `0117763` | `……엠마 신 중위로군。` | `……에마 신 중위로군。` |
| `012A3` | `0117941` | `엠마 신。` | `에마 신。` |
| `0D35E` | `01D86F7` | `……엠마 중위！` | `……에마 중위！` |
| `0216F` | `0124D38` | `아아……에、 엠마 중위。` | `아아……에、 에마 중위。` |

위 5개 phrase는 canonical runtime contract에서 총 15개 record가 재사용한다. 따라서 시나리오/이벤트/도감·UI 계열 소비처는 별도 중복 raw string을 만들지 않고 같은 표준화 phrase를 공유한다.

## mixed exact4 runtime 검수 근거

사용자 실측 PASS:

- `60B400` — F191081D 동일 / STAGE4: 정상
- `6184FD` — `08xx` 대표: 정상
- `61AA81` — `1728` 대표: 정상

따라서 `global_scenario_mixed_exact4_59_candidate.wsc`의 59개 고유 rehome을 runtime-approved 부모로 사용했다.

## 승격

최종 후보:

- `out/patch/global_scenario_mixed_exact4_59_ema_candidate.wsc`
- SHA-256: `CFB90AAA7AF2B9336FB63C70A8E7EC760AC51425D80017D5DAF82E6118D86BCA`
- checksum: `327A`

메인 승격:

- 시각: `2026-08-17T13:13:38+09:00`
- 승격 후 `out/patch/monoeye_ko_expanded.wsc` SHA-256: `CFB90AAA7AF2B9336FB63C70A8E7EC760AC51425D80017D5DAF82E6118D86BCA`
- 롤백: `out/patch/backup/20260817_131338_pre_global_scenario_mixed_exact4_59_ema/monoeye_ko_expanded.wsc`
- 승격 전 SHA-256: `714200FFDCAD34D01C12C8F560B8CA71163C165803E5E9894FEB30F523E166C6`
- live SaveRAM SHA-256: `A4AB19105107D3AE16139B4D4397DF6355B06B1DA341CA5F20B35C0E10FF5FAC` — 승격 전후 byte-exact

## 승격 후 게이트

- canonical runtime contracts: 24,925
- hard failures: 0
- review items: 0
- terminology audit:
  - active source hits: 0
  - dictionary hits: 0
  - five-bank dictionary hits: 0
  - rendered record hits: 0
- speaker/control audit:
  - current Japanese/mixed residual: 0
  - over-20: 0
- mixed exact4 risk:
  - exact4 mixed control-adjacent: 0
  - F191081D clone residual: 0
  - next `08xx` residual: 0
  - next `17xx` residual: 0
  - `08 34 00` changed: 0
- whole-ROM direct `엠마` raw sequence: 0

관련 보고서:

- `out/patch/global_scenario_mixed_exact4_59_ema_report.json`
- `out/patch/global_scenario_mixed_exact4_59_ema_promotion_report.json`
- `out/patch/global_scenario_mixed_exact4_59_ema_postpromotion_terminology_audit.json`
- `out/patch/global_scenario_mixed_exact4_59_ema_postpromotion_speaker_audit.json`
- `out/patch/global_scenario_mixed_exact4_59_ema_postpromotion_risk_audit.json`
