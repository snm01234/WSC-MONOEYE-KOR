# 용량 제약 전략 재검토 (16 MiB pad3 이후)

작성: 2026-07-17  
전제: `monoeye_ko_expanded.wsc` (16 MiB 팁) **free-space 기준선** — 오프닝·1스테이지 초반·2스테이지 사용자 실측 OK
(본선 빌드: `build_script_ko.py --placement free-space`)

## 0. 한 줄 결론

| 예전 병목 | 16 MiB 이후 |
|---|---|
| 한글 글리프 (96→528→1027, 잔여 159) | **해소** — sticky 1186 + bank00에 수천 슬롯 여유 |
| 긴 KO / dict free=0 / trail spill 초과 | **공간상 가능** — 확장 뱅크 훅만 설계하면 됨 |
| 이벤트 스트림·순차 NUL·UI 충돌·토큰 `0xFFF` | **그대로 막힘** — 용량과 무관 |

---

## 1. 용량으로 막혔던 것 → 재평가

### 1.1 글리프 (해소됨 ✅)

| 예전 전략 | 당시 한계 | 지금 |
|---|---|---|
| 빈 E740만 / primary 192 | 4~192칸 | 불필요 |
| pad1 96 / dual-pad 528 | ≪ 1186 | pad1+pad2 유지용 |
| bank3F 1027 | overflow 159 | pad3로 **1186 완료** |
| JP 슬롯 회수·E8 실험 | 위험 우회 | **우선순위 하락** (공간 여유) |

**남는 글리프 작업:** 슬롯≥528 오프닝 가시 확인, overflow 159자를 `apply_*`/TBL 파이프에 연결.  
추가 글리프가 더 필요해도 bank00만으로 수만 슬롯 여유.

### 1.2 사전·긴 문장 (공간 열림 🟡 — 훅 필요)

| 예전 전략 | 당시 한계 | 지금 |
|---|---|---|
| stock 5F free 슬롯 | **0** 소진 | 5F는 그대로지만 **확장 bank `10–2F`에 문구 풀** 가능 |
| ext_dict 265슬롯 (`0xF00–0xFFF`) | 토큰 하드캡 | 공간은 충분, **인덱스 인코딩 한도는 동일** |
| 공유 구문 압축 → inplace | free=0이면 불가 | 확장 사전에 문구 두고 토큰 치환 가능 |
| KO phrase ~40 KB 예산 | 5F 안 | 확장 2 MiB면 예산 여유 |

**주의:** 토큰 상한 `0xFFF`(총 4096)는 용량과 별개. 그 이상은  
- 별도 lead(예: 3바이트 토큰), 또는  
- 스크립트 본문에 직접 KO 바이트(길이 증가 → spill/훅)  
중 하나가 필요하다.

**권장:** `patch_ext_dictionary` 패턴을 확장 뱅크용으로 복제  
(`AL=bank_al_expansion(0x10)` + 포인터표 + payload). stock 5F **전체 rebuild 금지**는 유지.

### 1.3 스크립트 overflow / spill (부분 개방 🟡)

| 예전 전략 | 당시 한계 | 지금 |
|---|---|---|
| bank60 trail spill | trail **~2.4 KB**, 용량 초과 drop | 확장 `30–4F`에 **수 MiB** 가능 |
| `lines_skipped_no_capacity` (~2900) | 8 MiB trail 부족 | 공간상 해소 가능 |
| full-bank **shift** | abs 파괴 | **여전히 비권장** (용량 문제 아님) |
| blank-without-pointer | Event Error | **여전히 금지** |

**핵심:** 순차 NUL 스캔 로더는 “뒤에 빈 뱅크”를 자동으로 안 본다.  
확장 영역 사용 = **명시 포인터/훅**이 필수. 빈 공간만으로는 불충분.

안전한 단계:
1. 소량: in-bank spill + 포인터 가드 (기존 60–63)
2. 대량: 확장 뱅크에 레코드 복사 → 포인터만 새 뱅크를 가리키게 패치  
   (또는 로드 사이트에서 `OUT C3` 후 읽기)

### 1.4 UI / 유닛 문자열 (개방 🟡)

| 예전 | 지금 |
|---|---|
| 5F trail ~18 KB 잔여로 버팀 | 근접 시 확장 뱅크로 spill 이전 가능 |
| bank75 weapon spill OFF (크래시) | 용량과 무관 — 휴리스틱 수정 전 비활성 유지 |

### 1.5 코드 cave (변화 적음)

| 제약 | 상태 |
|---|---|
| Hangul primary ≤64B (ext_dict 공존) | 동일 — pad_hi 분기로 이미 우회 |
| 확장 훅용 코드 공간 | bank7F 잔여 FF + 확장 뱅크에 코드 배치 가능(원거리 콜) |

---

## 2. 용량과 무관하게 여전히 금지/비권장

| 항목 | 이유 |
|---|---|
| nonempty E740 덮어쓰기 | UI 겸용 → 크래시 |
| marker 없는 EE/빈 슬롯 표시 | 비가시 |
| 사전 **전체** rebuild | 오프닝/1스테이지 파괴 |
| 이벤트 바이트를 대세로 오인 | Event Error |
| ext token `FF 00` (`0xF00`) | zstring NUL |
| 타이틀 뉴게임/계속 버튼 | `72:0000` **그래픽**, 텍스트 아님 |
| 공유 JP 슬롯 부분 회수 | 미번역 줄이 같은 슬롯 참조 |

---

## 3. 권장 로드맵 (공간 해금 순서)

```text
[완료] 16MiB prepend + pad3 글리프 1186 + 부팅 정상
   │
   ▼
① 런타임 가시 게이트
   오프닝에서 슬롯≥528(pad3) 한글이 실제로 그려지는지
   │
   ▼
② 번역 파이프 ↔ pad3 맵
   hangul_patch_pad3.tbl / char_map_pad3 를 apply_* 기본으로
   overflow 159자가 대사에 등장 가능하게
   │
   ▼
③ 확장 사전 풀 (bank 0x10+)          ← 완료 (migrate)
   tools/patch_exp_dictionary.py
   팁 ROM에 포함 (인덱스 3831–4095, payload bank10)
   │
   ▼
④ 스크립트 대량 overflow          ← 1차 완료
   `overflow_mode=exp_spill` → bank 0x30+
   팁에 포함 (1562 relocated / 2054 pointer fixes)
   │
   ▼
⑤ 순차 스캔 + 팁 승격 + 초반 우선 할당  ← 진행 중
   tip: monoeye_ko_expanded.wsc
   early-abs + stock-reclaim → early_tut ~45% KO
   정본: docs/SCRIPT_COVERAGE_STATUS.md
   │
   ▼
⑥ 커버 확장 (순차 슬롯 한도 안)
   sole-fit 회수 · 창 좁히기 · 잔여 pointer spill(append)
   │
   ▼
⑦ (중기) 토큰 공간 훅 / UI·타이틀 별 트랙
```

### apply TBL
주요 `apply_*.py` 기본 TBL을 `hangul_patch_pad3.tbl`로 전환함.

### 우선하지 말 것
- JP 글리프 회수 / E8 실험 (이제 이득 ≪ 위험)
- 5F 전체 rebuild
- bank60 전역 shift
- bank75 spill 재활성 (원인 수정 전)

---

## 4. 숫자 요약

| 자원 | 8 MiB 시절 | 16 MiB pad3 후 |
|---|---|---|
| 한글 sticky 슬롯 | 96 → 528 → 1027 (잔여 159) | **1186** (+ bank00 수천 여유) |
| 확장 ROM | 0 | **8 MiB** (`00–7F`) |
| dict 토큰 주소 | ≤0xFFF (4096) | **동일 하드캡** |
| script trail (60–6F) | ~72 KB 분산, 60 타이트 | 스톡 동일 + **확장 spill 가능** |
| 시트 대사 | 32 739줄 / unique JP 22 289 | 동일 — 병목은 인코딩·훅 |

---

## 5. 실무 판단

부팅이 정상인 지금, **다음 진짜 병목은 “빈 공간”이 아니라 “안전하게 가리키는 훅”**이다.

- 글리프: 공간 문제 종료 → **표시 검증 + 파이프 연결**
- 대사: 공간은 열림 → **확장 사전 → 포인터형 스크립트 spill** 순
- 금지 목록(이벤트/rebuild/덮어쓰기)은 16 MiB와 무관하게 유지
