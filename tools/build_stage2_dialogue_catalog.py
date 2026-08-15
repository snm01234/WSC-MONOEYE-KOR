#!/usr/bin/env python3
"""Emit the reviewed stage-2B dialogue/voice translation catalog.

The source population is the post-promotion stage-2A broad audit.  It deliberately
excludes:
* the 149 ``不要``/``不用`` placeholder records;
* script single-kana/data rows;
* the corrupt-looking script table at/after 60:3BE4;
* the bank-boundary byte at 60:0000.

5D/5E records retain their proven leading control unit.  ``current_text`` is
therefore the rendered body; a few bodies whose first lexical character is
represented by that control unit use a reviewed full-sense Korean phrase.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "out/patch/broad_stage2_ui_system_postpromotion_residual_audit.json"
OUT = ROOT / "data/broad_stage2_dialogue_voice_ko.json"

# Rendered-body text -> reviewed Korean.  Western spaces/punctuation are
# normalized by the candidate builder before encoding.
KO = {
    "って、おいっ……！！": "잠깐、이봐……！！",
    "総帥！": "총수！",
    "すみました。": "끝났습니다。",
    "예っ！": "예！",
    "違いますよ。": "아닙니다。",
    "すみません。": "죄송합니다。",
    "に転落した。": "로 추락했다。",
    "とが違う。": "와는 다르다。",
    "……行けっ！！": "……가라！！",
    "……甘いんだよ！！": "……어림없어！！",
    "……ムリだね。": "……무리야。",
    "こ、これは……！！": "이、이건……！！",
    "こ、これは……！？": "이、이건……！？",
    "父さん……": "아버지……",
    "……ムダです！": "……소용없습니다！",
    "撃ちます……！！": "쏘겠습니다……！！",
    "……そこです！！": "……거기입니다！！",
    "行きます……！！": "갑니다……！！",
    "……まだです！！": "……아직입니다！！",
    "来たっ！？": "왔다！？",
    "……くらった！？": "……맞았어！？",
    "情けない……！": "한심하군……！",
    "これは……": "이건……",
    "……南無三っ！！": "……이런！！",
    "ええい！": "에잇！",
    "なるほど！": "그렇군！",
    "これは……！！": "이건……！！",
    "まだだ！": "아직이다！",
    "……当たれっ！": "……맞아라！",
    "……落ちろっ！": "……떨어져라！",
    "当たれっ！！": "맞아라！！",
    "ちぃぃッ！！": "칫！！",
    "無念……！": "원통하다……！",
    "愚かな……！": "어리석군……！",
    "……しまった！": "……당했다！",
    "……しまった！！": "……당했다！！",
    "強い……！？": "강해……！？",
    "……やる！！": "……제법이군！！",
    "ま、まさか……！？": "서、설마……！？",
    "徹底的にな！！": "철저하게 해 주마！！",
    "……ったく！": "……정말！",
    "分かった！！": "알았다！！",
    "へへっ！": "헤헤！",
    "へっへ～！": "헤헤！",
    "くっ、来る……！！": "크윽、온다……！！",
    "ひるむな！": "물러서지 마！",
    "……結構。": "……좋아。",
    "あっ、コラ！": "앗、이봐！",
    "へんっ！": "흥！",
    "それそれ！！": "자、받아라！！",
    "けェ－ッ！！": "가라！！",
    "あぁぁっ！！": "아아악！！",
    "敵発見！！": "적 발견！！",
    "……おのれ！": "……이놈！",
    "……回頭せよ！": "……방향을 돌려라！",
    "……うむ。": "……음。",
    "……バカなっ！！": "……말도 안 돼！！",
    "ぬかるな……！": "방심하지 마……！",
    "……くらえっ！！": "……받아라！！",
    "やれるか……？": "할 수 있겠나……？",
    "……ダメだっ！": "……안 돼！",
    "ロ－ラっ！": "로라！",
    "……なるほど。": "……그렇군。",
    "ロ…ロ－ラ……！": "로…로라……！",
    "御曹司！！": "도련님！！",
    "うわぁっ！！": "우아악！！",
    "なにっ！？": "뭐라고！？",
    "……ひるむな！": "……물러서지 마！",
    "……当たって！！": "……맞아 줘！！",
    "そこかっ！！": "거기냐！！",
    "くっ……！": "크윽……！",
    "もらった！！": "잡았다！！",
    "しまった！": "당했다！",
    "……クソッ！": "……젠장！",
    "……なにっ！？": "……뭐라고！？",
    "くくくく……！！": "크크크크……！！",
    "まだよっ！": "아직이야！",
    "うものだ……！": "행하는 것이다……！",
    "……なめるな！": "……얕보지 마！",
    "……落ちろ！！": "……떨어져라！！",
    "……そこだ！！": "……거기다！！",
    "……見える！": "……보인다！",
    "だが！！": "하지만！！",
    "そこだ！": "거기다！",
    "わかれ！！": "흩어져라！！",
    "さ－てと……": "자、그럼……",
    "ふふふ……": "후후후……",
    "ふっ……": "훗……",
    "無念だ……！": "원통하다……！",
    "行くぞ！！": "간다！！",
    "ほお！": "호오！",
    "くっ……不覚！": "크윽……실수했다！",
    "つ、強い！！": "가、강해！！",
    "……笑止！": "……가소롭군！",
    "あぁっ……！？": "아앗……！？",
    "そこ……っ！": "거기……！",
    "見えた！": "보였다！",
    "あぁっ！！": "아앗！！",
    "なにも……": "아무것도……",
    "うまい……！": "훌륭해……！",
    "見えた……！？": "보였어……！？",
    "……来るっ！？": "……온다！？",
    "……あぁっ！？": "……아앗！？",
    "……来たっ！！": "……왔다！！",
    "っ！！": "읏！！",
    "……見えるっ！！": "……보인다！！",
    "……ムダだっ！！": "……소용없다！！",
    "見えた……！！": "보였다……！！",
    "……よしなに。": "……잘 부탁해。",
    "はっ！": "핫！",
    "……クッ！": "……크윽！",
    "……無礼な！！": "……무례하군！！",
    "……うぉっ！！": "……우옷！！",
    "ふんっ！": "흥！",
    "……殺気！": "……살기！",
    "うわわっ！？": "우와앗！？",
    "さぁ、来い！": "자、덤벼！",
    "くらった！？": "맞았어！？",
    "、強い……ッ！": "가、강해……！",
    "……見えたっ！！": "……보였다！！",
    "うわぁっ……！": "우아악……！",
    "おのれっ！！": "이놈！！",
    "……おっと！": "……이런！",
    "……そこよっ！": "……거기야！",
    "行けっ！！": "가라！！",
    "……あぁっ！！": "……아아악！！",
    "なんだと……！？": "뭐라고……！？",
    "我らの力、": "우리의 힘을、",
    "無用だ！": "소용없다！",
    "ぬかるな！": "방심하지 마！",
    "く、くそっ！": "제、젠장！",
    "く、来る……！": "크、온다……！",
    "砲門開け！！": "포문을 열어라！！",
    "力全開！": "출력 전개！",
    "対空砲！": "대공포！",
    "……やらせん！！": "……그렇게 두지 않겠다！！",
    "……単純なっ！": "……단순하군！",
    "ええい……！": "에잇……！",
    "……ほう。": "……호오。",
    "……単純な！！": "……단순하군！！",
    "くらえっ！": "받아라！",
    "は、離れろ！": "비、비켜！",
    "……うぅっ！": "……으윽！",
    "ああ……頭が……！": "아아……머리가……！",
    "……あぅっ！？": "……아윽！？",
    "……あうっ！！": "……아윽！！",
    "ま、まだ……": "아、아직……",
    "……よし！": "……좋아！",
    "まだだっ！": "아직이다！",
    "……ベぇ－だ！": "……메롱！",
    "あぁっ！？": "아앗！？",
    "あぅっ！！": "아윽！！",
    "う、うぅぅ……": "으、으으윽……",
    "おのれっ！": "이놈！",
    "この野郎……": "이 자식……",
    "ったれ！！": "망할 놈！！",
    "くそっ……！": "젠장……！",
    "うぉぉっ！！": "우오오！！",
    "野蛮人が……": "야만인 놈……",
    "ぬぬぬぬ……！": "으으으윽……！",
    "………っ！": "………윽！",
    "へっ！": "흥！",
    "……うぉぉっ！！": "……우오오！！",
    "んじゃえ！": "죽어버려！",
    "しな！！": "각오해라！！",
    "くそっ！！": "젠장！！",
    "……どうした？": "……왜 그러지？",
    "……ったれが！": "……망할 놈！",
    "消えな！！": "사라져！！",
    "チィ……っ！！": "칫……！！",
    "ぬぅっ……！！": "으윽……！！",
    "……っ！？": "……윽！？",
    "この力は……！": "이 힘은……！",
    "………っ！？": "………윽！？",
    "……そこねっ！！": "……거기구나！！",
    "……いやっ！": "……싫어！",
    "……さすがは！！": "……역시！！",
    "……ああっ！！": "……아앗！！",
    "……動けない！？": "……움직일 수 없어！？",
    "当たれ……っ！！": "맞아라……！！",
    "……来る！！": "……온다！！",
    "いけっ！！": "가라！！",
    "愚かな……": "어리석군……",
    "……死ね！": "……죽어！",
    "な、なんで！？": "왜、왜！？",
    "……ムダだね！！": "……소용없어！！",
    "そ、空が……！": "하、하늘이……！",
    "なぜだ！？": "왜냐！？",
    "雁行の陣！": "안행진！",
    "くそっ！": "젠장！",
    "いかんっ！": "안 돼！",
    "手柄さえ": "공적만이라도",
    "おのれ！": "이놈！",
    "い、いかん！": "아、안 돼！",
    "まだだっ！！": "아직이다！！",
    "くらえっ！！": "받아라！！",
    "……そこだっ！": "……거기다！",
    "……もらった！！": "……잡았다！！",
    "見える……！！": "보인다……！！",
    "バ、バカな……！": "마、말도 안 돼……！",
    "おのれ……！！": "이놈……！！",
    "バ、バカな……": "마、말도 안 돼……",
    "くっ！": "크윽！",
    "バカな！！": "말도 안 돼！！",
    "はははっ！": "하하핫！",
    "つ、強い……！": "가、강해……！",
    "撃てっ！": "쏴라！",
    "……当たれっ！！": "……맞아라！！",
    "なんのっ！": "어림없다！",
    "えろ！！": "사라져！！",
    "行けっ……！！": "가라……！！",
    "……うわぁっ！": "……우아악！",
    "……っ！！": "……윽！！",
    "……はっ！！": "……핫！！",
    "企んだなッ！！": "꾸몄구나！！",
    "もうこの船はおしまいだ－！": "이제 이 배는 끝장이야！",
    "逃げろ、逃げろ－－！！": "도망쳐、도망쳐！！",
    "ははははは……": "하하하하하……",
    "……예っ！！": "……예！！",
    "いっけぇぇぇ－－っ！！": "가라아아！！",
    "させるかぁぁぁ－－っ！！": "그렇게 둘까 보냐아아！！",
    "うわぁぁぁぁ－－っ！！": "우아아아악！！",
    "人を救ってみせろぉ－－－っ！！": "사람을 구해 내 봐라아아！！",
    "……웡・リ－！": "……웡・리！",
    "웡・リ－！！": "웡・리！！",
    "……っ！": "……윽！",
    "う……うん。": "으……응。",
    "は、예っ！！": "네、예！！",
    "……くっ！！": "……크윽！！",
    "くっ……！！": "크윽……！！",
    "………っ！！": "………윽！！"
}


def main() -> int:
    doc = json.loads(SOURCE.read_text(encoding="utf-8"))
    if doc.get("ok") is not True:
        raise SystemExit("source audit is not successful")
    rows = []
    for group in (doc.get("records") or {}).values():
        rows.extend(group or [])
    by_abs = {str(row["abs"]).upper(): row for row in rows}

    selected = []
    excluded = []
    for address, row in sorted(by_abs.items()):
        logical = int(address, 16)
        region = str(row.get("region") or "")
        current = str(row.get("current_text") or "")
        if current in {"不要", "不用"}:
            excluded.append({"abs": address, "reason": "unused_placeholder", "text": current})
            continue
        if region == "script" and (
            str(row.get("legacy_reason") or "").startswith("excluded_non_linguistic_fragment")
            or logical == 0x600000
            or logical >= 0x603BE4
        ):
            excluded.append({"abs": address, "reason": "script_data_or_fragment", "text": current})
            continue
        if region not in {"aux", "script"}:
            continue
        if current not in KO:
            raise SystemExit(f"missing reviewed Korean for {address}: {current!r}")
        selected.append(
            {
                "abs": address,
                "record_id": row.get("record_id"),
                "region": region,
                "jp_full": row.get("original_text"),
                "jp_body": current,
                "ko": KO[current],
                "body_capacity": int(row.get("body_capacity") or 0),
                "prefix_hex": row.get("prefix_hex") or "",
                "body_hex": row.get("body_hex") or "",
            }
        )

    if len(selected) != 288:
        raise SystemExit(f"selected population drifted: expected 288, got {len(selected)}")
    document = {
        "schema_version": 1,
        "generated_by": "tools/build_stage2_dialogue_catalog.py",
        "description": "Reviewed Korean for 288 proven aux/script dialogue and voice records after stage 2A UI promotion.",
        "source_audit": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "counts": {
            "selected": len(selected),
            "unique_korean": len({row["ko"] for row in selected}),
            "excluded_placeholders_or_data": len(excluded),
        },
        "lines": selected,
        "excluded": excluded,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_name(f".{OUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, OUT)
    print(json.dumps({"ok": True, "counts": document["counts"], "out": str(OUT)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
