#!/usr/bin/env python3
"""Mine bank-5F dictionary for UI/menu/system/save/option/command strings."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import Dictionary, Tbl, find_rom, load_rom, encode_plaintext  # noqa: E402

UI_RE = re.compile(
    r"オプション|セーブ|ロード|コマンド|メニュー|システム|コンフィグ|"
    r"スロット|ファイル|データ|コンティニュ|コンティニュー|ニューゲーム|"
    r"はじめから|つづき|続き|決定|キャンセル|はい|いいえ|戻る|終了|"
    r"音量|ＢＧＭ|ＳＥ|ステレオ|モノラル|ウィンドウ|メッセージ|"
    r"スピード|速度|戦闘|マップ|ユニット|ターン|フェイズ|フェーズ|"
    r"攻撃|防御|移動|射程|命中|回避|ＨＰ|ＥＮ|ＩＤ|ｉｄ|"
    r"選択|選ぶ|確認|削除|上書き|読込|読込み|書込|書込み|"
    r"ＰＵＳＨ|ＳＴＡＲＴ|ＧＡＭＥ|ＯＶＥＲ|ＣＯＮＴＩＮＵＥ|"
    r"ポーズ|ＰＡＵＳＥ|ＮＥＸＴ|ＢＡＣＫ|"
    r"アイテム|スキル|精神|気力|経験|レベル|ＬＶ|"
    r"出撃|撤退|補給|改装|強化|開発|設計|"
    r"シナリオ|ステージ|ミッション|クリア|ゲーム|"
    r"名前|名称|パイロット|戦艦|母艦"
)

base = load_rom(find_rom(ROOT))
tbl = Tbl.load(ROOT / "out/patch/hangul_patch.tbl")
d = Dictionary(base)

rows = []
for idx in range(d.count):
    plain = d.expand_index(idx, tbl)
    if not plain or "<" in plain:
        continue
    if UI_RE.search(plain) or (
        2 <= len(plain) <= 16
        and re.fullmatch(r"[\u30A0-\u30FFー－Ａ-Ｚａ-ｚ０-９A-Za-z0-9　 ]+", plain)
        and re.search(
            r"メニュー|セーブ|ロード|オプション|コマンド|システム|スロット|"
            r"データ|ファイル|スピード|ウィンドウ|メッセージ|ターン|フェイズ|"
            r"マップ|ステージ|クリア|ゲーム|スタート|ポーズ",
            plain,
        )
    ):
        rows.append({"index": f"{idx:04X}", "jp": plain, "len": len(plain)})

# Also dump ALL short-medium entries that look like UI labels (kanji/kana, no sentence particles)
labelish = []
for idx in range(d.count):
    plain = d.expand_index(idx, tbl)
    if not plain or "<" in plain:
        continue
    if not (2 <= len(plain) <= 14):
        continue
    if re.search(r"[。！？]|です|ます|した|いる|する|ない|こと|もの|から|まで", plain):
        continue
    if UI_RE.search(plain):
        labelish.append({"index": f"{idx:04X}", "jp": plain})

out = {
    "ui_hits": rows,
    "ui_hit_count": len(rows),
    "labelish_extra": [x for x in labelish if x["jp"] not in {r["jp"] for r in rows}][:200],
}
path = ROOT / "out/script/ui_dict_candidates.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
md = ["# UI dict candidates", ""]
for r in rows:
    md.append(f"{r['index']}\t{r['jp']}")
(ROOT / "out/script/ui_dict_candidates.md").write_text("\n".join(md) + "\n", encoding="utf-8")
print(f"ui_hits={len(rows)} -> {path}")
for r in rows:
    print(f"{r['index']}\t{r['jp']}")
