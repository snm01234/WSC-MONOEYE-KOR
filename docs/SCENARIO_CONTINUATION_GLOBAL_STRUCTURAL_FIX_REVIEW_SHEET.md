# Scenario continuation 전역 구조 수정 대표 실측 시트

후보 ROM: `out/patch/scenario_continuation_global_structural_fix_candidate.wsc`  
SHA-256: `24AA886359BB41E70161D47C66C90D683C91F0287C3BE2ECA856C7F520E7F1BF`  
paired SaveRAM: `sram/scenario_continuation_global_structural_fix_candidate.sav`

이번 후보는 single-NUL 실제 일본어 `こ` 잔류 6건을 제거하고, double-NUL structural `18 + direct E518` 2,740건을 1건 native + 2,739건 E504로 rehome한다.

## 판정 기준

- A/B: 실제 일본어 글자 `こ` 제거 경로. 선두 `18`이 없어져야 정상.
- C/D/E: structural `18` 보존 + E504 경로. `18`은 유지되지만 화면에는 `こ`로 나오면 안 됨.
- F: ordinary-native 예외 1건.
- G/H: 새 dispatcher가 기존 E51D 동작을 깨지 않았는지 빠른 회귀 확인.

## A. single-NUL 실제 こ 제거 / STAGE4 기준점

`그건 샤아 대령님을 좋아한다는` 다음 줄이 `뜻입니까！？`로 나오고 선두 `こ`가 없어야 함.

| 구분 | 주소 | bundle | 대사 | route | prefix | 다음 control | NUL |
|---|---|---|---|---|---|---|---:|
| 문맥 | `60BAE0` | `scenario_60BAE0` | 소위님은　샤아　대령님을　어떻게 | `scenario_first` | `170318` | `` | 1 |
| 문맥 | `60BAF3` | `scenario_60BAE0` | 생각하십니까？……　들려주세요！ | `scenario_continuation` | `` | `0879` | 2 |
| 문맥 | `60BB0A` | `scenario_60BB0A` | 대령님은　날　거두어　주신　분…… | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `60BB20` | `scenario_60BB0A` | 나는　그분을　위해　싸울　거예요。 | `scenario_continuation` | `` | `0856` | 2 |
| 문맥 | `60BB34` | `scenario_60BB34` | 그건　샤아　대령님을　좋아한다는 | `scenario_first` | `173418` | `` | 1 |
| **대상** | `60BB48` | `scenario_60BB34` | 뜻입니까！？ | `scenario_continuation` | `` | `` | 2 |
| 문맥 | `60BB50` | `scenario_60BB34` | 그렇다면　당신　마음속에　품고　있는 | `scenario_continuation` | `18` | `` | 1 |
| 문맥 | `60BB5E` | `scenario_60BB34` | 아무로라는　사람은　대체　누구죠！？ | `scenario_continuation` | `` | `0879` | 2 |
| 문맥 | `60BB6E` | `scenario_60BB6E` | ………윽！ | `scenario_first` | `173418` | `0856` | 2 |
| 문맥 | `60BB7A` | `scenario_60BB7A` | 샤아　대령님을　좋아한다고　하면서 | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `60BB8C` | `scenario_60BB7A` | 다른　남자를　품다니、　비겁해요！ | `scenario_continuation` | `` | `` | 2 |

대상 저장 방식:
- `60BB48`: `drop_visible_18` / body `E518723C0101` / portal `ext3`

## B. single-NUL 실제 こ 제거 + 직후 17 28

선두 `こ` 없이 현재 한글 문장이 출력되고 직후 `17 28` 제어가 노출/중단 없이 처리되어야 함.

| 구분 | 주소 | bundle | 대사 | route | prefix | 다음 control | NUL |
|---|---|---|---|---|---|---|---:|
| 문맥 | `63683B` | `scenario_63683B` | 큭……너냐！！ | `scenario_first` | `173418` | `` | 2 |
| 문맥 | `636849` | `scenario_63683B` | 아까부터　내 | `scenario_continuation` | `18` | `` | 1 |
| 문맥 | `636850` | `scenario_63683B` | 머릿속에　들어오려는　건！ | `scenario_continuation` | `` | `1728` | 2 |
| 문맥 | `636860` | `scenario_636860` | 쥬도는　적이　아니야！ | `scenario_first` | `173418` | `` | 2 |
| 문맥 | `63686C` | `scenario_636860` | 쥬도와　있으면　기분　좋다는　걸 | `scenario_continuation` | `18` | `` | 1 |
| **대상** | `63687C` | `scenario_636860` | 왜　그걸　모르는　거야！？ | `scenario_continuation` | `` | `1728` | 2 |
| 문맥 | `63688D` | `scenario_63688D` | 『……안　돼！！』 | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `63689F` | `scenario_63689F` | 큭……너냐！！ | `scenario_first` | `173418` | `` | 2 |
| 문맥 | `6368AD` | `scenario_63689F` | 아까부터　내 | `scenario_continuation` | `18` | `` | 1 |
| 문맥 | `6368B4` | `scenario_63689F` | 머릿속에　들어오려는　건！ | `scenario_continuation` | `` | `1728` | 2 |
| 문맥 | `6368C4` | `scenario_6368C4` | 『쥬도는　적이　아니야！』 | `scenario_first` | `173418` | `` | 2 |

대상 저장 방식:
- `63687C`: `drop_visible_18` / body `E5181C33010101` / portal `ext3`

## C. double-NUL structural 18 + E504 + 08xx

`과거의 나도 그랬다。`가 정상 출력되고 직후 `08 0A`가 글자로 노출되지 않으며 다음 초상/대사가 정상이어야 함.

| 구분 | 주소 | bundle | 대사 | route | prefix | 다음 control | NUL |
|---|---|---|---|---|---|---|---:|
| 문맥 | `60B400` | `scenario_60B400` | ……네？ | `scenario_first` | `173418` | `0834` | 2 |
| 문맥 | `60B40C` | `scenario_60B40C` | 아무리　어리다고　해도 | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `60B419` | `scenario_60B40C` | 눈도　보이고　생각할　머리도　있지。 | `scenario_continuation` | `` | `` | 2 |
| 문맥 | `60B42C` | `scenario_60B40C` | 아이　취급하지　않고　조리를　설하면 | `scenario_continuation` | `18` | `` | 1 |
| 문맥 | `60B43F` | `scenario_60B40C` | 납득해　주는　법이지。 | `scenario_continuation` | `` | `` | 2 |
| **대상** | `60B449` | `scenario_60B40C` | 과거의　나도　그랬다。 | `scenario_continuation` | `18` | `080A` | 2 |
| 문맥 | `60B459` | `scenario_60B459` | 하아、　그런　겁니까…… | `scenario_first` | `173418` | `0834` | 2 |
| 문맥 | `60B46C` | `scenario_60B46C` | 나도　어릴　적에는 | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `60B478` | `scenario_60B46C` | 저렇게　랄을　곤란하게　했지…… | `scenario_continuation` | `` | `080A` | 2 |
| 문맥 | `60B48A` | `scenario_60B48A` | 랄……？ | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `60B493` | `scenario_60B48A` | 아버님　이름인가요？ | `scenario_continuation` | `` | `0834` | 2 |

대상 저장 방식:
- `60B449`: `portal16` / body `E504A709010101010101` / portal `control18_portal16`

## D. double-NUL structural 18 + E504 + 17 28

대사 직후 `17 28` page/control 진행이 정상이고 반복·스킵·제어문 노출이 없어야 함.

| 구분 | 주소 | bundle | 대사 | route | prefix | 다음 control | NUL |
|---|---|---|---|---|---|---|---:|
| 문맥 | `60040B` | `scenario_60040B` | ……뭐라고！！ | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `600424` | `scenario_600424` | ……움직이지　마라！！ | `scenario_first` | `173418` | `0845` | 2 |
| 문맥 | `600434` | `scenario_600434` | 아그리파！ | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `60043C` | `scenario_600434` | 이게　어떻게　된　일입니까！！ | `scenario_continuation` | `` | `080C` | 2 |
| 문맥 | `60044B` | `scenario_60044B` | 오오……！！ | `scenario_first` | `173418` | `` | 2 |
| **대상** | `600455` | `scenario_60044B` | 이、　이런　짓、　깅가남에게는……！ | `scenario_continuation` | `18` | `1728` | 2 |
| 문맥 | `60046D` | `scenario_60046D` | 이런　일은　명령하지　않았다！ | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `600481` | `scenario_60046D` | 저、　저자가　멋대로　꾸민　일입니다！！ | `scenario_continuation` | `` | `1728` | 2 |
| 문맥 | `60049F` | `scenario_60049F` | 이런　자가　문레이스의　지도자라고 | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `6004AD` | `scenario_60049F` | 행세하고　있으니、　웃음이　나지　않나。 | `scenario_continuation` | `` | `1728` | 2 |
| 문맥 | `6004C4` | `scenario_6004C4` | ……네놈은！！ | `scenario_first` | `173418` | `1728` | 2 |

대상 저장 방식:
- `600455`: `portal16` / body `E504F00901010101010101` / portal `control18_portal16`

## E. 과거 page-merge 실패 기준 / structural 18 보존

`디아나 님！` 페이지가 독립 유지되고 이어지는 세 문장이 병합/`こ`/`亻` 없이 정상 진행되어야 함.

| 구분 | 주소 | bundle | 대사 | route | prefix | 다음 control | NUL |
|---|---|---|---|---|---|---|---:|
| 문맥 | `6017B0` | `scenario_6017B0` | ……………… | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `6017C1` | `scenario_6017C1` | 필・아카만！！ | `scenario_first` | `173418` | `` | 2 |
| 문맥 | `6017CD` | `scenario_6017C1` | 디아나　이름을　딴　부대　지휘관이 | `scenario_continuation` | `18` | `` | 1 |
| 문맥 | `6017E1` | `scenario_6017C1` | 왜　깅가남을　따라　날　거역하는가！？ | `scenario_continuation` | `` | `085E` | 2 |
| 문맥 | `6017F3` | `scenario_6017F3` | 디아나　님！ | `scenario_first` | `173418` | `` | 2 |
| **대상** | `6017FC` | `scenario_6017F3` | 저희들은　지구만을　생각하고、　달을 | `scenario_continuation` | `18` | `` | 1 |
| 문맥 | `601813` | `scenario_6017F3` | 등한시하는　폐하의　뜻에는…… | `scenario_continuation` | `` | `` | 2 |
| **대상** | `601826` | `scenario_6017F3` | 따라갈　수　없다고　말씀드렸습니다！！ | `scenario_continuation` | `18` | `0845` | 2 |
| 문맥 | `60183D` | `scenario_60183D` | ……………… | `scenario_first` | `173418` | `0828` | 2 |
| 문맥 | `601849` | `scenario_601849` | 그렇게까지　고민하고　있었나……！ | `scenario_first` | `172A18` | `1728` | 2 |
| 문맥 | `601864` | `scenario_601864` | 필　소령에게　뭐라고　사과해야　할지…… | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `601873` | `scenario_601864` | ……모르겠군。 | `scenario_continuation` | `` | `085E` | 2 |
| 문맥 | `601883` | `scenario_601883` | ……폐、　폐하！？ | `scenario_first` | `173418` | `0845` | 2 |

대상 저장 방식:
- `6017FC`: `portal16` / body `E5040D0A0101010101010101010101010101010101` / portal `control18_portal16`
- `601826`: `portal16` / body `E5047A0501010101010101010101010101` / portal `control18_portal16`

## F. ordinary-native 단일 예외

E504가 아닌 기존 native token으로 복구된 유일한 항목. 한글 문구와 직후 `17 28` 진행이 정상이어야 함.

| 구분 | 주소 | bundle | 대사 | route | prefix | 다음 control | NUL |
|---|---|---|---|---|---|---|---:|
| 문맥 | `6150A3` | `scenario_6150A3` | 으오옷……！！ | `scenario_first` | `173418` | `0878` | 2 |
| 문맥 | `6150BA` | `scenario_6150BA` | ……블랙스　준장！！ | `scenario_first` | `173418` | `0896` | 2 |
| 문맥 | `6150D4` | `scenario_6150D4` | 티탄즈　전력、　침묵！！ | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `615103` | `scenario_615103` | ……앗！ | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `61510C` | `scenario_615103` | 자、　잠깐　기다려　주십시오！！ | `scenario_continuation` | `` | `` | 2 |
| **대상** | `615115` | `scenario_615103` | 이건……！！ | `scenario_continuation` | `18` | `1728` | 2 |
| 문맥 | `615125` | `scenario_615125` | 새　적이라고！？ | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `61513B` | `scenario_61513B` | 아인　레비……큰소리치더니、 | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `615150` | `scenario_61513B` | 이　꼴인가！ | `scenario_continuation` | `` | `` | 2 |
| 문맥 | `61515B` | `scenario_61513B` | ……바스크　대령님！ | `scenario_continuation` | `18` | `1728` | 2 |
| 문맥 | `61516D` | `scenario_61516D` | ……음。 | `scenario_first` | `173418` | `1728` | 2 |

대상 저장 방식:
- `615115`: `ordinary_native` / body `F3C6010101` / portal `none`

## G. 기존 E51D parameter 회귀

기존 실측 anchor `가토오오오！！` 계열이 그대로 정상이어야 함.

| 구분 | 주소 | bundle | 대사 | route | prefix | 다음 control | NUL |
|---|---|---|---|---|---|---|---:|
| 문맥 | `6102FA` | `scenario_6102FA` | 으음！　새로운　적인가……！！ | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `610312` | `scenario_610312` | 가토……！ | `scenario_first` | `173418` | `` | 2 |
| 문맥 | `61031B` | `scenario_610312` | 이번엔　꼭　네놈을　막겠다！ | `scenario_continuation` | `18` | `1728` | 2 |
| 문맥 | `610335` | `scenario_610335` | 저지　한계까지　얼마　안　남았다！ | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `610345` | `scenario_610335` | 전　기체、　콜로니를　떨어뜨려라！！ | `scenario_continuation` | `` | `1728` | 2 |
| **대상** | `61035E` | `scenario_61035E` | 가토오오오！！ | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `61036C` | `scenario_61036C` | 에잇！　또　네놈이냐！！ | `scenario_first` | `173418` | `082D` | 2 |
| 문맥 | `61037F` | `scenario_61037F` | 네놈이　살아　있는　한、 | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `61038D` | `scenario_61037F` | 나는　끝까지　널　쫓겠다！！ | `scenario_continuation` | `` | `080F` | 2 |
| 문맥 | `6103A2` | `scenario_6103A2` | 흥！　헛수고를！！ | `scenario_first` | `173418` | `` | 2 |
| 문맥 | `6103B1` | `scenario_6103A2` | 이제는　그　누구도　저지할　수　없다！！ | `scenario_continuation` | `18` | `1728` | 2 |

대상 저장 방식:
- `61035E`: `regression_anchor` / body `E51D2D01` / portal `event_safe_native2_param`

## H. 기존 E51D fixed / STAGE22 회귀

웃소/카테지나 `……어？`와 후속 이벤트가 기존처럼 정상이어야 함.

| 구분 | 주소 | bundle | 대사 | route | prefix | 다음 control | NUL |
|---|---|---|---|---|---|---|---:|
| 문맥 | `638C6B` | `scenario_638C6B` | 이제……끝이네。 | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `638C84` | `scenario_638C84` | 웃소…… | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `638C95` | `scenario_638C95` | 카테지나……씨？ | `scenario_first` | `173418` | `1728` | 2 |
| 문맥 | `638CB1` | `scenario_638CB1` | 날　좋아하는구나、　웃소……？ | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `638CC1` | `scenario_638CB1` | 계속　날　사랑했지？ | `scenario_continuation` | `` | `0804` | 2 |
| **대상** | `638CD5` | `scenario_638CD5` | ……어？ | `scenario_first` | `173418` | `084B` | 2 |
| 문맥 | `638CED` | `scenario_638CED` | 나도　너　같은　소년에게 | `scenario_first` | `173418` | `` | 1 |
| 문맥 | `638CF9` | `scenario_638CED` | 이렇게　사랑받아　기뻐。 | `scenario_continuation` | `` | `08D2` | 2 |
| 문맥 | `638D0A` | `scenario_638D0A` | 하지만　난　너와　적대하는 | `scenario_first` | `171C18` | `` | 1 |
| 문맥 | `638D1A` | `scenario_638D0A` | 길을　선택해　버렸어…… | `scenario_continuation` | `` | `08D2` | 2 |
| 문맥 | `638D2F` | `scenario_638D2F` | ……너와　껴안을　수는　없어。 | `scenario_first` | `173418` | `088C` | 2 |

대상 저장 방식:
- `638CD5`: `regression_anchor` / body `F191E51D` / portal `event_safe_native2`

## 최소 PASS 조건

A~F에서 `こ`/제어문 노출, 페이지 병합, 초상 오판, 대사 반복·스킵, Event Error가 없어야 한다. G/H는 기존 정상 anchor가 그대로 유지되면 된다.
모두 정상이라면 `A~H PASS`처럼 알려주면 된다.
