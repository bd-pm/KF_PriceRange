import requests
import json
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Accept": "application/json",
}

CATEGORIES = {
    "photocard": "910100001",
    "figure_doll": "930100",
}

# 번장 검색 API는 빈 쿼리로 카테고리 전체를 브라우징할 수 없어(ERR_INVALID_PARAMETER),
# 카테고리를 커버하는 대표 키워드로 여러 번 검색해서 합친다.
QUERY_KEYWORDS = {
    "photocard": [
        "포토카드", "포카", "방탄소년단", "세븐틴", "스트레이키즈", "엔하이픈",
        "투모로우바이투게더", "에스파", "아이브", "뉴진스", "제로베이스원",
        "라이즈", "투어스", "보이넥스트도어", "에이티즈", "엔시티",
    ],
    "figure_doll": [
        "피규어", "인형", "포켓몬 피규어", "짱구 피규어", "산리오 인형",
        "카카오프렌즈 인형", "디즈니 피규어", "건담 프라모델",
    ],
}


def fetch_products(category_id, category_name, keywords, pages_per_keyword=3):
    seen_pids = set()
    results = []
    for keyword in keywords:
        for page in range(1, pages_per_keyword + 1):
            url = "https://api.bunjang.co.kr/api/1/find_v2.json"
            params = {
                "q": keyword,
                "order": "date",
                "page": page,
                "n": 100,
                "f_category_id": category_id,
            }
            try:
                res = requests.get(url, params=params, headers=HEADERS, timeout=10)
                data = res.json()
                items = data.get("list", [])
                new_items = [it for it in items if it.get("pid") not in seen_pids]
                for it in new_items:
                    seen_pids.add(it["pid"])
                results.extend(new_items)
                print(f"[{category_name}] '{keyword}' page {page}: {len(items)} items ({len(new_items)} new)")
                time.sleep(0.5)
                if not items:
                    break
            except Exception as e:
                print(f"Error on '{keyword}' page {page}: {e}")
    return results


def main():
    all_data = {}
    for name, cat_id in CATEGORIES.items():
        items = fetch_products(cat_id, name, QUERY_KEYWORDS[name], pages_per_keyword=3)
        all_data[name] = items
        print(f"[{name}] total: {len(items)} items\n")

    with open("samples.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print("Saved to samples.json")

    # 각 카테고리에서 상품명 50개씩 미리보기
    for name, items in all_data.items():
        print(f"\n=== {name} 상품명 샘플 50개 ===")
        for item in items[:50]:
            pid = item.get("pid", "")
            title = item.get("name", "")
            price = item.get("price", "")
            print(f"  [{pid}] {title} | {price}원")

if __name__ == "__main__":
    main()
