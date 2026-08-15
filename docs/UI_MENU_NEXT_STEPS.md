# 초기 메뉴 그래픽 소스 — 확정

업데이트: 2026-07-27 (직전: 2026-07-16)

## 결론

초기 메뉴의 세 버튼은 **뱅크 72의 4bpp 타일 아틀라스**다. 주소·포맷·화면 대응이
모두 에뮬레이터 실측으로 확정됐고, 과거 실패 실험에서 재착수 조건으로 삼았던
**단일 타일 변조 실측**도 통과했다.

| 항목 | 결과 |
|---|---|
| 팔레트 | `72:0000–007F` — WSC 16색 팔레트 4개 (32 B씩, LE16 `0x0RGB`) |
| 플레이트 | `72:0080 + n*0x280` — **80×16 px, packed 4bpp, 8×8 타일 10개/행, 20타일 = 640 B** |
| 플레이트 수 | 29개 (`720080–7248FF`) — 라벨 9종 × 상태 3–4개 |
| 압축 | **없음.** ROM 바이트가 그대로 픽셀이다 |
| 도구 | `tools/analyze_bank72_menu_atlas.py` → `out/title_menu_capture/bank72_atlas.json` |

### 폐기된 기존 판독

- `72:0000` 선두가 **LE16 오프셋 테이블처럼 보인다**던 기록은 틀렸다.
  `00 00 27 01 49 00 5A 00 …`은 12비트 색상 램프(`0x0000 0x0127 0x0049 0x005A …`)이고,
  램프가 대체로 증가하기 때문에 포인터 테이블처럼 보였을 뿐이다.
- `72:1800–1FFF` 제로화가 **불변**이었던 이유도 확정됐다. 그 대역은 플레이트 9–12,
  즉 이 화면에서 안 쓰이는 `オプション` 상태 변종 2개와 다른 화면 라벨(`ノーマル`)이다.
  "핫스팟이 6 KB"가 아니라 "초기 메뉴가 실제로 그리는 플레이트가 앞쪽 3개"가 맞다.
- 아틀라스는 6 KB에서 끝나지 않는다. 같은 규격의 플레이트가 `7248FF`까지 이어지고
  `ノーマル`·`スペシャル`·`通信モード`·`賃貸モード`·`対戦モード`·`ユニット交換`가 들어 있다.
  다른 메뉴 화면 한글화에 그대로 재사용할 수 있는 표적이다.

## 화면 대응 (플레이트 1개 제로화 실측, 11건)

부팅 직후 메뉴에서 실제로 그려지는 플레이트는 3개뿐이다.

| 플레이트 | abs | 라벨·상태 | 화면 8×8 블록 |
|---:|---|---|---|
| 1 | `720300–72057F` | `ニューゲーム` (선택 강조) | col 8–17, row 5–7 |
| 6 | `720F80–7211FF` | `コンティニュー` | col 10–19, row 8–9 |
| 7 | `721200–72147F` | `オプション` | col 12–21, row 10–12 |

플레이트 0·2·3·4·5·8·9·10을 제로화해도 title/menu 캡처 해시가 **불변**이다.
즉 미선택·비활성 상태 변종은 이 화면에서 안 쓰인다.

변화 폭이 가로 10블록(=80 px)으로 플레이트 폭과 정확히 일치한다. 이것이 80 px
가정의 독립적인 확인이다.

## 단일 타일 변조 실측 (관문 통과)

32바이트 타일 하나만 눈에 띄는 패턴으로 덮은 ROM 5종. 원본 대비 바뀐 화면 블록:

| 후보 | 변조 abs | 플레이트 타일 (col,row) | 바뀐 화면 블록 |
|---|---|---|---|
| `TILE_06_05` | `721020–72103F` | 6 / (5,0) | **col 15, row 8 — 블록 1개** |
| `TILE_01_02` | `720340–72035F` | 1 / (2,0) | col 10, row 5–6 |
| `TILE_01_05` | `7203A0–7203BF` | 1 / (5,0) | col 13, row 5–6 |
| `TILE_01_12` | `720480–72049F` | 1 / (2,1) | col 10, row 6–7 |
| `TILE_07_15` | `7213E0–7213FF` | 7 / (5,1) | col 17, row 11–12 |

타일 하나당 바뀐 픽셀은 57–61개로 8×8=64에 수렴한다(나머지는 원본 픽셀이 우연히
덮은 패턴과 같은 경우). 좌표도 전부 일관된다.

- 플레이트 1의 화면 원점이 col 8 → 타일 col 2는 화면 col 10, 타일 col 5는 col 13
- 플레이트 6은 col 10 → 타일 col 5는 col 15
- 플레이트 7은 col 12 → 타일 col 5는 col 17
- 타일 row 0 → 1로 내려가면 화면 블록도 정확히 한 칸 내려간다

플레이트 1·7은 세로 배치가 8 px 격자에 정렬되지 않아 8 px 타일이 화면 블록 2행에
걸친다. 플레이트 6은 정렬돼 있어 **블록 1개**만 바뀐다 — 관문의 가장 엄격한 형태다.

`title` 캡처는 5건 모두 불변. 타이틀 로고와 `PUSH START BUTTON`은 이 아틀라스를
쓰지 않는다(별도 소스, 미확정).

## 재현 방법

```powershell
$env:PYTHONIOENCODING = "utf-8"
# 기준 해시 (3회 연속 동일해야 통과)
python tools/run_title_menu_capture.py --runs 3 --write-baseline
# 아틀라스 해석 + 플레이트 PNG
python tools/analyze_bank72_menu_atlas.py
# 후보 조립
python tools/build_menu_tile_candidates.py --plate 1 --tile "6:5"
# 실측 + 변화 위치
python tools/run_menu_candidates.py --glob "out/patch/menu_bisect/*.wsc" --overlays
```

기준 해시: `title=BF8FD8CD1554` `menu=D144B003D040`
(`out/title_menu_capture/baseline_hashes.json`, 원본 ROM md5 `c492c640…`).
이 값은 폐기된 `run_menu_*.ps1`에 하드코딩돼 있던 `REF_TITLE`/`REF_MENU`와 같다.

## 도구

| 도구 | 용도 |
|---|---|
| `tools/bizhawk_env.py` | 저장소 기준 경로 단일 출처 · 온보딩 없는 격리 프로필 · 실행 |
| `tools/menu_capture.lua` | title/menu 결정적 캡처 (분실된 `title_trace6` 판 복원) |
| `tools/run_title_menu_capture.py` | N회 실행 결정성 게이트 · 기준 해시 기록 |
| `tools/analyze_bank72_menu_atlas.py` | 팔레트·플레이트 해석 → `bank72_atlas.json` + PNG |
| `tools/render_bank_tiles.py` | 임의 대역을 4bpp/2bpp 타일로 렌더 |
| `tools/build_menu_tile_candidates.py` | 플레이트 제로화 / 단일 타일 변조 ROM |
| `tools/run_menu_candidates.py` | 후보 일괄 실측 + 8×8 블록 단위 변화 위치 |
| `tools/diff_capture_tiles.py` | 캡처 2장의 블록 단위 diff · 오버레이 |

폐기: `tools/run_menu_slices.ps1`, `tools/run_menu_2k_slices.ps1`,
`tools/run_menu_bisect_emu.py` (죽은 절대경로 + 분실된 Lua. 실행 시 즉시 종료).

### BizHawk 실행 환경

BizHawk 2.11.1에는 `--config=` 스위치가 **없다**(`EmuHawk.exe` ·
`dll/BizHawk.Client.Common.dll` 문자열 스캔으로 확인. `--lua`, `--load-state`,
`--load-slot`, `--userdata`, `--socket*`, `--url*`, `--mmf`, `--fullscreen`,
`--chromeless`, `--audiosync`, `--luaconsole`만 존재). `config.ini`는 실행 파일과
같은 디렉터리에서만 읽는다. 그래서 `out/bizhawk_profile`에 **별도 포터블 인스턴스**를
만든다 — 무거운 하위 디렉터리는 저장소 BizHawk로 향하는 디렉터리 정션이라 약 6 MB다.
프로필 `config.ini`는 `FirstBoot=false`, `UpdateAutoCheckEnabled=false`,
`Unthrottled=true`, `SoundEnabled=false`, `DisplayMessages=false`,
`BackupSaveram=false`로 온보딩·OSD·스로틀을 끈다. 1회 실행 5–9초.

## 하지 말 것

- `tools/patch_title_menu.py` / `75:B7A4` 재패치 (반증됨)
- 메뉴를 `EC80` 대사 경로로 치환 (소스 불일치 — 메뉴는 타일, 대사는 글리프)
- 초기 메뉴에 bank40 compact 폰트 가정 (반증됨)

## 한글 적용 (2026-07-27, 인게임 확인)

아틀라스의 **29개 플레이트 전부**를 한글로 교체했다. 라벨 9종 × 상태 변종.

| 항목 | 값 |
|---|---|
| 도구 | `tools/patch_menu_plates_ko.py` (+ `tools/menu_plate_model.py`) |
| 문구 | `data/menu_plate_labels_ko.json` |
| 대상 | 플레이트 **0–28** `720080–7248FF` |
| 변경량 | 5,531 B + 체크섬 2 B. **그 밖은 1바이트도 안 건드린다**(전수 diff 확인) |
| 폰트 | **Galmuri11 @ 11 px** (`assets/fonts/galmuri_tmp/Galmuri11.ttf`) |
| 배치 | 자간 1 px, 공백 4 px, 가운데 정렬, 획 상단 `y=2`, 프레임 침범 0 |
| 캡처 | `menu=A9D34D603106`, 3회 연속 동일 · `title` 불변 |
| 팁 적용본 | `out/patch/monoeye_ko_expanded_menu_ko.wsc` (16 MiB, 체크섬 `EE27`) |

| 원문 | 한글 | 플레이트 | 초기 메뉴 |
|---|---|---|---|
| `ニューゲーム` | **새 게임** | 0–2 | 1 |
| `コンティニュー` | **계속** | 3–6 | 6 |
| `オプション` | **설정** | 7–10 | 7 |
| `ノーマル` | **노멀** | 11–13 | — |
| `スペシャル` | **스페셜** | 14–16 | — |
| `通信モード` | **통신 모드** | 17–19 | — |
| `鑑賞モード` | **감상 모드** | 20–22 | — |
| `対戦モード` | **대전 모드** | 23–25 | — |
| `ユニット交換` | **유닛 교환** | 26–28 | — |

`鑑賞`은 8×8 글리프라 판독이 애매해서 획을 세어 확정했다. 첫 글자는 왼쪽이 `金`,
오른쪽 아래가 `皿`(y11에 밑변 가로줄) → `鑑`. 둘째 글자는 위에 `⺌`(y3의 점 3개),
아래가 `貝`(y11 좌우 다리) → `賞`. ROM 텍스트·사전에는 `モード`가 한 건도 없어
문자열 쪽에서는 확인할 수 없다(그래픽 전용 라벨).

**초기 메뉴는 플레이트 11–28을 그리지 않는다.** 0–10만 넣은 빌드와 29개 전부 넣은
빌드의 `menu` 캡처 해시가 `A9D34D603106`으로 동일하다 — 변경이 초기 메뉴 밖으로
새지 않았다는 봉쇄 확인이다. 11–28이 실제로 어느 화면에 나오는지는 그 화면에
자동으로 도달할 수 없어 미확인이다.

### 배경 복원 — 이 작업의 핵심

플레이트는 글자와 그라디언트가 한 비트맵에 합성돼 있고 **글자 없는 판본이 ROM에
없다.** 두 가지 성질로 복원했다(`tools/menu_plate_model.py`).

1. **상태 그룹.** 29개 플레이트가 라벨 9종 × 상태 4종이고, 한 상태는 배경 하나를
   공유한다. 그룹은 왼쪽 두 타일 서명으로 찾는다(라벨 변종 수가 3·4·4·3·3·3·3·3·3로
   불균일해서 순서 가정은 못 쓴다).
2. **라벨은 예약 인덱스 2개만 쓴다.** 획은 최명도 `E`, 그림자는 어두운 인덱스 하나.
   둘 다 배경에 안 나온다. 그래서 참조 이미지 없이 픽셀 단위로 라벨/배경 판정이 된다.

그룹 전체의 단순 최빈값은 **안 통한다.** `モード`가 라벨 4개에 공통이라 유령 글자가
남는다. 라벨로 분류된 샘플을 뺀 최빈값이어야 한다.

측정된 사실 두 가지가 복원 품질을 결정했다.

- **상태는 4개가 아니라 2개다.** A→C는 항등사상이고 A→B는 A→D와 같다. 즉 하나의
  그라디언트를 일반/강조 두 인덱스 집합으로 그린 것이고, 그룹이 4개로 보인 이유는
  `コンティニュー`가 길어서 서명 타일까지 침범하기 때문이다. 그래서 17장 그룹에서
  복원한 배경 하나를 리맵으로 전파한다. 이 과정 없이는 `コンティニュー` 3장 모두
  같은 글자라서 라벨 아래 1,280 px 중 226 px을 표본조차 못 얻는다.
- **미해결 픽셀은 6개**뿐이고 수평 보간으로 채운다. 라벨 밖 재현 오차는 플레이트당
  0–11 px이며, 이 픽셀들은 원본 글자의 중간톤 테두리라 어차피 덮인다.

### 렌더 규격 — 8×8이 아니다

**플레이트는 자유 비트맵이다.** 대사 글리프(`40:0440`, 고정 8×8 레코드)와 달리 8 px
상한이 없다. `PATCH_PROGRESS.md` D.15의 8×8 제약은 여기 적용되지 않는다.

- **Galmuri11 @ 11 px**가 8 px보다 확실히 잘 읽힌다. 문서의 "갈무리11은 갈무리7보다
  나쁘다"는 8 px로 짜냈을 때의 이야기다. 11 px는 설계 크기다.
- **그림자 오프셋은 원본의 `(+1,+1)`을 안 쓰고 `(0,+1)`로 내렸다.** 실측 판단이다.
  한글은 자모 간 1 px 간격이 많아 `(+1,+1)`이 그 틈을 메워 비선택 버튼이 "어두운
  덩어리에 밝은 선"으로 보인다. 그림자를 없애면 밝은 판 위에서 대비를 잃는다.
  바로 아래로 내리면 원본과 같은 "밝은 획 + 어두운 받침" 대비가 유지된다.
  `--shadow-delta stock`으로 원본 오프셋을 강제할 수 있다.
- 프레임 인덱스 `0`·`F`에는 절대 안 그린다. 버튼 실루엣이 망가진다.

### 게이트

`tools/diff_stock_3way.py`에 `menu_plate_graphics` 분류를 추가했다(`720080–7248FF`,
귀속 `patch_menu_plates_ko`). 없으면 `verify_stock_noninvasion.py`가 이 대역을
UNINTENDED로 잡는다. 라벨을 더 늘리면 `MENU_PLATE_HI`도 함께 넓혀야 한다.
추가 후 팁 적용본은

| 게이트 | 결과 |
|---|---|
| `verify_stock_noninvasion` | UNINTENDED **0 B** |
| `verify_all_stages_smoke` | `overall_ok: true` |
| `verify_nondialogue_text` | `ok: true` |
| `5F` 포인터 3778/3831 (하한 3802) | **VIOLATED — 패치 전 팁과 동일한 기존 상태** |

`5F` 하한 위반은 이 작업과 무관하다. 미패치 팁에서도 3778로 같고
`PATCH_PROGRESS.md` B.6이 그 하한을 폐기 대상으로 적어 둔 항목이다.

```powershell
$env:PYTHONIOENCODING = "utf-8"
python tools/preview_menu_plate_model.py            # 배경·마스크 복원 확인
python tools/patch_menu_plates_ko.py --dry-run      # 미리보기만
python tools/patch_menu_plates_ko.py                # 원본 기준 테스트 ROM
python tools/patch_menu_plates_ko.py --rom out/patch/monoeye_ko_expanded.wsc `
  --out out/patch/monoeye_ko_expanded_menu_ko.wsc   # 팁 적용 (stock_base 자동)
python tools/run_title_menu_capture.py --rom out/patch/menu_bisect/MENU_KO.wsc `
  --runs 3 --tag MENU_KO_gate --compare
```

## 다음

1. **팁 승격 판단.** `monoeye_ko_expanded_menu_ko.wsc`는 게이트를 통과했지만
   팁(`monoeye_ko_expanded.wsc`)은 건드리지 않았다. `PATCH_PROGRESS.md` B.5의
   콜드 리빌드와 함께 넣을지 결정할 것.
2. **플레이트 11–28의 실제 화면 확인.** 정적으로는 완결됐지만 어느 화면에서 나오는지
   못 봤다. 초기 메뉴 커서가 에뮬 입력에 반응하지 않아 `オプション` 화면 등으로
   내려갈 수 없다. 인터미션과 같은 세이브스테이트 우회가 필요하다.
3. 타이틀 로고와 `PUSH START BUTTON`의 소스는 여전히 미확정. `title` 캡처가
   바뀌는 대역부터 다시 이분해야 한다.
