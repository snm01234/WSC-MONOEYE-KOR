import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(r"d:\monoeye")

def translate_google_ui_trick(text: str) -> str:
    if not text.strip(): return ''
    # Using the UI Label trick to force noun/UI translation
    query = f"UIラベル：{text}"
    url = f'https://translate.googleapis.com/translate_a/single?client=gtx&sl=ja&tl=ko&dt=t&q={urllib.parse.quote(query)}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                res = ''.join(item[0] for item in data[0] if item[0])
                # Clean up the prefix
                res = re.sub(r'^UI\s*라벨\s*[:：]\s*', '', res, flags=re.IGNORECASE)
                res = re.sub(r'^UI\s*Label\s*[:：]\s*', '', res, flags=re.IGNORECASE)
                return res.strip()
        except Exception:
            if attempt < 4:
                time.sleep(0.5 * (attempt + 1))
    return ''

def main():
    data_path = ROOT / "out" / "script" / "translations_quality.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    
    # Load existing overrides to avoid overwriting curated ones
    overrides_path = ROOT / "data" / "ko_quality_overrides.json"
    overrides = {}
    if overrides_path.exists():
        overrides_data = json.loads(overrides_path.read_text(encoding="utf-8"))
        for line in overrides_data.get("lines", []):
            if line.get("jp") and line.get("ko"):
                overrides[line["jp"]] = line["ko"]
                
    short_strings = set()
    for line in data.get("lines", []):
        jp = line.get("jp", "").strip()
        # Criteria for UI/Noun strings
        if jp and 1 <= len(jp) <= 8 and jp not in overrides:
            # If it's mostly punctuation or just 1 character hiragana, skip
            if not re.search(r'[A-Za-z0-9\u30A0-\u30FF\u4E00-\u9FAF]', jp) and len(jp) < 3:
                continue
            # If it contains Hiragana, only translate if it's a known short phrase
            if re.search(r'[\u3040-\u309F]', jp):
                if jp not in ["はい", "いいえ", "もどる", "つぎへ", "キャンセル", "けってい", "クリア"]:
                    continue
            short_strings.add(jp)
            
    print(f"Found {len(short_strings)} short UI/Noun strings to translate.")
    
    # Force some known bad ones that Google gets wrong even with UI trick
    hardcoded = {
        "フェイス": "페이스",
        "システム": "시스템",
        "メニュー": "메뉴",
        "部隊長": "부대장",
        "ガンダム": "건담",
        "ザク": "자쿠",
        "セーブ": "세이브",
        "ロード": "로드"
    }
    
    results = []
    
    def task(jp):
        if jp in hardcoded:
            return jp, hardcoded[jp]
        ko = translate_google_ui_trick(jp)
        return jp, ko

    completed = 0
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = {pool.submit(task, jp): jp for jp in short_strings}
        for future in as_completed(futures):
            jp, ko = future.result()
            if ko:
                results.append({"jp": jp, "ko": ko, "notes": "auto_ui_translated"})
            completed += 1
            if completed % 100 == 0:
                print(f"Progress: {completed}/{len(short_strings)}")
                
    # Save to a new overrides file
    ui_overrides_path = ROOT / "data" / "ko_ui_overrides.json"
    ui_data = {
        "description": "Auto-translated short UI strings using UI Label context trick",
        "lines": results
    }
    ui_overrides_path.write_text(json.dumps(ui_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(results)} translations to {ui_overrides_path.name}")

if __name__ == "__main__":
    main()
