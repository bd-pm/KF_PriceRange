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

def fetch_products(category_id, category_name, pages=3):
    results = []
    for page in range(1, pages + 1):
        url = "https://api.bunjang.co.kr/api/1/find_v2.json"
        params = {
            "q": "",
            "category_id": category_id,
            "order": "date",
            "page": page,
            "n": 100,
            "f_category_id": category_id,
        }
        try:
            res = requests.get(url, params=params, headers=HEADERS, timeout=10)
            data = res.json()
            items = data.get("list", [])
            results.extend(items)
            print(f"[{category_name}] page {page}: {len(items)} items")
            time.sleep(0.5)
        except Exception as e:
            print(f"Error on page {page}: {e}")
    return results

def main():
    all_data = {}
    for name, cat_id in CATEGORIES.items():
        items = fetch_products(cat_id, name, pages=5)
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
