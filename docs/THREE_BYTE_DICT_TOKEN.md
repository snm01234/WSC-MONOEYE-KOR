# 확장 사전 토큰 (E5 18 xx yy / ext3)

작성: 2026-07-19 · 갱신: 2026-07-19  
목적: 순차 대사 인덱스 천장 `0xFFF`를 넘어 스테이지2+ KO 슬롯을 확보한다.

## 인코딩

| 형태 | 의미 |
|---|---|
| `F0–FF yy` (2B) | 기존 인덱스 `0–0xFFF` (변경 없음) |
| `E5 18 xx yy` (4B) | 확장 인덱스 `0x1000 + ((xx << 8) \| yy)` |

제약: `yy != 0x00` (zstring NUL). PoC 범위 `0x1000–0x1FFF` (4096슬롯, trail0 제외).

**왜 EF가 아닌가:** 초기안 lead=`EF`는 Hangul 글리프 페이지와 충돌(스크립트 뱅크에 EF lead 수백 건).  
매직 `0xE518`은 script/aux/name75 zstring 워크에서 0건이라 포털로 사용.

## 런타임

1. 스트림 워커 두 곳 (`7A:0736`, `7A:080D`)에서 2바이트 조립 후 `DX==E518`이면  
   `xx`,`yy`를 더 읽고 WRAM `19F8`에 논리 인덱스, `19FA=1` 설정 후 `DX=0xF000`.
2. leaf `7A:06CE`: 플래그가 켜져 있으면 스톡과 동일하게 `DEB2`→`push AX`(현재 뱅크) 후  
   expand bank 맵·문구 로드 → 스트림 루프 `7A:0743` 합류(NUL 종료 시 `pop`/`DEB5`로 복원).
3. 페이로드: expand banks **`0x11`…** — 각 뱅크 LE16 ptr[4096] + zstring pool (~56KiB).  
   `bank = 0x11 + ((index - 0x1000) >> 12)` (기본 8뱅크 → 인덱스 `0x1000–0x8FFF`).

## 툴

| 파일 | 역할 |
|---|---|
| `tools/patch_3byte_dict_token.py` | 훅 설치 + bank11 포맷 |
| `tools/apply_3byte_seq_ko.py` | 순차 줄에 E518 토큰 size-preserving 적용 |
| `monoeye_rom` | `read_encoded_z` / `Dictionary.expand` / `token_from_dict_index` ext3 인식 |

## 게이트

- opening Hangul · jagd · 유닛뱅크 tip 불변
- tip 직쓰기 금지 — work ROM → 스모크 → promote
- 문구 예산(~56KiB)이 슬롯 수보다 먼저 찰 수 있음 → bank12 확장은 후속

## 비범위

- legacy `seq_dict` / 전역 spill
- `FF` lead 재사용 (UI 침범 클래스)
- Hangul lead(`E8–EF`) 재사용
