# Scenario continuation single-NUL `18 = visible Japanese こ` review

## Corrected grammar

Original scenario continuation records that start with byte `18` split into two distinct grammars by the **preceding Original record's NUL boundary**.

- predecessor `double-NUL` -> `18` is the structural continuation/page-head prefix.
- predecessor `single-NUL` -> `18` is the visible Japanese glyph `こ`.

This distinction is runtime-confirmed by the representative probe:

- `60B449`, `6017FC`, `601826` (double-NUL): preserving `18` while rehoming `E518` through `E504` works normally.
- `60BB48` (single-NUL): preserving `18` still prints `こ뜻입니까！？`, because Original is actually `ことなんですか！？`.
- the earlier narrow candidate that physically removed the source `18` at `60BB48` rendered `뜻입니까！？` normally.

## Whole-game counts on current main

- Original leading-`18` scenario continuations: **2,861**
- double-NUL structural-prefix rows: **2,847**
- single-NUL visible-`こ` rows: **14**
- double-NUL rows still using risky `18 + direct E518`: **2,740**
- single-NUL visible-`こ` rows where the translated payload still physically retains `18`: **6**

## Remaining visible-`こ` leak candidates

| Address | Current translated text without leaked source glyph | Notes |
|---|---|---|
| `608B55` | `점만큼은 확실하다。` | source starts `ことだけは...` |
| `60A47A` | `집착이 있는 모양이군。` | source starts `こだわり...` |
| `60BB48` | `뜻입니까！？` | runtime FAIL on preserved-18 portal; source `ことなんですか！？` |
| `6339D9` | `부리던 사이드３ 놈들과 똑같아！` | source starts `こき使った...` |
| `63687C` | `왜 그걸 모르는 거야！？` | source starts `ことがなぜ...` |
| `636B03` | `매달릴 여유가 없어！！` | source starts `こだわってる...` |

These six must **not** use the structural `18`-preserving E504 portal rule. Their Japanese-only leading `こ` byte must be removed/replaced as part of the Korean body while preserving record extent and following boundaries.

## Already cleaned single-NUL visible-`こ` rows

`6002F1`, `6088B3`, `60EA3A`, `612229`, `614F0A`, `61C463`, `61C506`, `626509` no longer physically retain the source `18` in the current main.

## Corrected bulk policy

1. Six single-NUL visible-`こ` residuals: remove the Japanese-only source glyph byte using the already runtime-proven same-extent shift/padding method.
2. 2,740 double-NUL structural `18 + E518` rows: preserve `18`; rehome only the body storage route after the E504 representative gate is accepted.
3. Never infer the meaning of byte `18` from `translation_sheet.prefix_hex` alone; that sheet historically stripped genuine text-initial `こ` in the 14 single-NUL rows.
