#!/usr/bin/env python3
"""Build the reviewed stage-2C complete title/map/communication UI catalog."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "out/patch/broad_stage2_dialogue_voice_residual_audit.json"
OUT = ROOT / "data/broad_stage2_title_ui_ko.json"

KO = {
    "ＭＳ０８小隊": "ＭＳ０８ 소대",
    "逆襲のシャア": "역습의 샤아",
    "その他": "기타",
    "登場作品": "등장 작품",
    "Ｙ３<E62F>ステ－タス画面": "Ｙ３<E62F>상태 화면",
    "赤い彗星": "붉은 혜성",
    "戦いへの恐怖": "전투에 대한 공포",
    "めぐりあい": "해후",
    "さっそうたるシャア": "늠름한 샤아",
    "艦隊戦Ａ（エゥ一ゴ）": "함대전 Ａ（에우고）",
    "艦隊戦Ｂ（戦闘）": "함대전 Ｂ（전투）",
    "艦隊戦Ｃ（ティタ－ンズ）": "함대전 Ｃ（티탄즈）",
    "ゼ－タの発動": "제타의 발동",
    "月のまゆ": "달의 고치",
    "サイレントヴォイス": "사일런트 보이스",
    "我が心<E62F>明鏡止水": "나의 마음<E62F>명경지수",
    "コミック・ビ－ト": "코미디・비트",
    "互いを想う": "서로를 생각하다",
    "決戦に向けて": "결전을 향해",
    "果てぬ戦略": "끝없는 전략",
    "そしてはじまり": "그리고 시작",
    "風雲": "풍운",
    "陰謀": "음모",
    "宇宙に還えれ": "우주로 돌아가라",
    "ステ－ジタイトル": "스테이지 제목",
    "さまよう武士魂": "방황하는 무사혼",
    "迫り来る敵": "다가오는 적",
    "そして戦いは始まる": "그리고 전투는 시작된다",
    "進軍": "진군",
    "ふたり": "두 사람",
    "アイン・レヴィ": "아인・레비",
    "悲しき戦い": "슬픈 전투",
    "戦いの時": "전투의 때",
    "悲しみ": "슬픔",
    "ロ－ザヴィ": "로자비",
    "フィナ－レ": "피날레",
    "戦士たち": "전사들",
    "セラとの出会い": "세라와의 만남",
    "ソ－ラ・レイ発射": "솔라・레이 발사",
    "ギレンの死": "기렌의 죽음",
    "第３軌道艦隊登場": "제３궤도 함대 등장",
    "ミアンとの再会": "미안과의 재회",
    "アクシズとの会談": "액시즈와의 회담",
    "セラ、再び": "세라、다시",
    "ヒイロとリリ－ナ": "히이로와 릴리나",
    "東方不敗、暁に死す": "동방불패、새벽에 죽다",
    "黄金の秋": "황금의 가을",
    "掌の上で": "손바닥 위에서",
    "ハマ－ンの死": "하만의 죽음",
    "シグとセラ<E62F>抱よう": "시그와 세라<E62F>포옹",
    "エンディング（セラ死亡）": "엔딩（세라 사망）",
    "ハッピ－エンド": "해피 엔드",
    "真オ－プニング": "진 오프닝",
    "エンディング（ミアン死亡）": "엔딩（미안 사망）",
    "セラの危機": "세라의 위기",
    "ケンプファ－ｖｓアレックス": "캠퍼ｖｓ알렉스",
    "ミ－シャの死": "미샤의 죽음",
    "ララァの死": "라라아의 죽음",
    "ラストシュ－ティング": "라스트 슈팅",
    "セラの死": "세라의 죽음",
    "ガト－とコウ、決着": "가토와 코우、결착",
    "フォウ救出": "포우 구출",
    "フォウの死": "포우의 죽음",
    "プルの死（前）": "플의 죽음（전）",
    "プルの死（後）": "플의 죽음（후）",
    "石破ラブラブ天驚拳": "석파 러브러브 천경권",
    "悲しみの拳": "슬픔의 주먹",
    "東方不敗死す": "동방불패 죽다",
    "マウア－の死": "마우아의 죽음",
    "エマとレコア": "에마와 레코아",
    "シャイニングフィンガ－対決": "샤이닝 핑거 대결",
    "月光蝶対決": "월광접 대결",
    "月光蝶ｖｓ光の翼": "월광접ｖｓ빛의 날개",
    "カツの死": "카츠의 죽음",
    "カミ－ユの怒り": "카미유의 분노",
    "フロスト兄弟の策謀": "프로스트 형제의 책모",
    "ハリ－奮戦": "해리 분전",
    "ディアナ奮戦（ソレイユ）": "디아나 분전（솔레이유）",
    "ディアナ奮戦（アルマイヤ－）": "디아나 분전（알마이어）",
    "ジェリドの最期": "제리드의 최후",
    "クワトロ散る": "크와트로 산화",
    "サザビ－の力": "사자비의 힘",
    "ハマ－ンの最期": "하만의 최후",
    "フロスト兄弟の最期": "프로스트 형제의 최후",
    "カテジナとの決着": "카테지나와의 결착",
    "アインの最期": "아인의 최후",
    "セラの最期": "세라의 최후",
    "ミアン散る": "미안 산화",
    "最後の戦い": "최후의 전투",
    "と<E62F><E62F><E62F><E62F><E62F>を交換します": "와<E62F><E62F><E62F><E62F><E62F>를 교환합니다",
    "交換が中断されました": "교환이 중단되었습니다",
    "交換が完了しました": "교환이 완료되었습니다",
    "通信に失敗しました": "통신에 실패했습니다",
    "交換可能なアイテムがありません": "교환 가능한 아이템이 없습니다",
    "宇宙<E62F>警戒エリア": "우주<E62F>경계 구역",
    "月航路": "달 항로",
    "テキサス宙域": "텍사스 공역",
    "暗礁宙域": "암초 공역",
    "会合ポイント": "회합 포인트",
    "地球衛星軌道上": "지구 위성 궤도상",
    "グリ－ン・ノア２": "그린・노아２",
    "ホンコン・シティ": "홍콩・시티",
    "ニュ－タイプ研究所": "뉴타입 연구소",
    "キリマンジャロ基地": "킬리만자로 기지",
    "サハラ砂漠": "사하라 사막",
    "サンクキングダム": "상크 킹덤",
    "旧ジオン鉱山基地": "구 지온 광산 기지",
    "ダカ－ル郊外": "다카르 교외",
    "地球軌道航路": "지구 궤도 항로",
    "フォン・シティ": "폰・시티",
    "突入ポイント": "돌입 포인트",
    "サイコウェ－ブ中心域": "사이코 웨이브 중심역",
    "ゼダンの門": "제단의 문",
    "ホンコン": "홍콩",
    "月面都市周辺": "월면 도시 주변",
    "地球衛星軌道": "지구 위성 궤도",
    "キリマンジャロ": "킬리만자로",
    "観測基地": "관측 기지",
    "月軌道": "달 궤도",
    "通信モ－ドを利用するにはＡＬＬクリアした": "통신 모드를 이용하려면 ＡＬＬ 클리어한",
    "バックステ－ジのデ－タが必要です。": "백스테이지 데이터가 필요합니다。",
    "所有ユニット数が２００機を越えています。": "보유 유닛 수가 ２００기를 넘었습니다。",
    "処分しないと出動できません。": "처분하지 않으면 출동할 수 없습니다。",
    "ＡＬＬクリアしたバックステ－ジの": "ＡＬＬ 클리어한 백스테이지의",
    "通信対戦が中断されました": "통신 대전이 중단되었습니다",
}


def main() -> int:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    if source.get("ok") is not True:
        raise SystemExit("source audit is not successful")
    rows = []
    for bucket in (source.get("records") or {}).values():
        rows.extend(bucket or [])
    selected = []
    excluded = []
    for row in sorted(rows, key=lambda item: int(item["logical_address"])):
        if str(row.get("region") or "") != "name75_ui":
            continue
        logical = int(row["logical_address"])
        original = str(row.get("original_text") or "")
        if logical < 0x75B8DA:
            reason = "walker_noise" if logical < 0x75B2E3 else "ambiguous_system_or_kana_index"
            excluded.append({"abs": row["abs"], "text": original, "reason": reason})
            continue
        if original not in KO:
            raise SystemExit(f"missing reviewed Korean for {row['abs']}: {original!r}")
        selected.append({
            "abs": str(row["abs"]).upper(),
            "record_id": row.get("record_id"),
            "jp": original,
            "current": row.get("current_text"),
            "ko": KO[original],
            "body_capacity": int(row.get("body_capacity") or 0),
            "prefix_hex": row.get("prefix_hex") or "",
            "body_hex": row.get("body_hex") or "",
        })
    if len(selected) != 127:
        raise SystemExit(f"selected population drifted: expected 127, got {len(selected)}")
    document = {
        "schema_version": 1,
        "generated_by": "tools/build_stage2_title_ui_catalog.py",
        "description": "Reviewed Korean for 127 complete work/stage/title/map/communication UI records after the 288 dialogue/voice candidate.",
        "source_audit": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "counts": {"selected": len(selected), "unique_korean": len({row['ko'] for row in selected}), "excluded_ui_noise_or_ambiguous": len(excluded)},
        "lines": selected,
        "excluded": excluded,
    }
    temporary = OUT.with_name(f".{OUT.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, OUT)
    print(json.dumps({"ok": True, "counts": document["counts"], "out": str(OUT.resolve())}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
