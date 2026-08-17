# Scenario continuation structural-18 storage worklist

Source-proven `18 + direct E518`: **2,740**
Existing ordinary native token sequence로 복구 가능: **1**
16-bit native-loop portal 필요: **2,739**
Portal helper 고유 E518 phrase: **2,680**
Bank27 helper 예상 사용량: **13,400 / 65,536 bytes**

## Proposed scalable portal probe

- magic: `E504`; semantic consumers: **0**
- bank27 all-FF: **True**
- record body: `E5 04 <low+1> <high+1>` (base-255 nonzero 16-bit index; 4 bytes, current E518 extent와 동일)
- helper: bank27 fixed-stride 5 bytes = `E5 18 xx yy 00`
- leading structural `18`, terminator, NUL/page boundary, next control은 보존

## Runtime probe before bulk build

1. `60B449` 같은 double-NUL structural-18 대표를 `18 + E504 <index>`로 바꾸고 선두 18은 보존한다.
2. 직후 08/17 control, 초상, 페이지 관계가 유지되는지 확인한다.
3. 과거 선두 18 삭제가 페이지를 합쳤던 `6017FC/601826` 계열을 함께 대표 검증한다.
4. `60BB48` 같은 single-NUL visible-source-こ 행은 이 worklist에서 제외하고 별도 same-extent glyph removal로 처리한다.
5. probe PASS 후 ordinary-native 군과 portal16 군을 한 후보에 일괄 반영한다.

