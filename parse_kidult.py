"""
키덜트(피규어/인형) 상품 파싱
samples.json (figure_doll) → parsed_kidult.json
"""

import json
import os
import sys
import time
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """You are a kidult collectibles (figures/dolls/toys) merchandise parser. Extract structured fields from Korean product listing titles.

Output JSON only, no explanation.

Fields to extract:
- franchise: the IP/series/brand this item belongs to. Use the most common English name (e.g. "포켓몬스터"→"Pokemon", "진격의거인"→"Attack on Titan", "원피스"→"One Piece", "귀멸의칼날"→"Demon Slayer", "짱구는못말려"→"Crayon Shin-chan", "산리오"→"Sanrio", "치이카와"→"Chiikawa", "디즈니"→"Disney", "건담"→"Gundam", "베어브릭"→"Bearbrick", "카우스"→"KAWS"). null if not identifiable.
- character: the specific character depicted, in the most common English/romanized name (e.g. "리자몽"→"Charizard", "루피"→"Luffy", "쿠로미"→"Kuromi", "미카사"→"Mikasa"). null if no single character is identifiable (generic merch, mixed set, or the product line itself is the identity, e.g. a Bearbrick numbered series).
- product_line: the specific product series/release/format that groups identical items together — e.g. an Ichiban Kuji release name, "Nendoroid", "Grandista", "Bearbrick Series 48", a specific POP/scale figure line, a gacha series name, a garage kit sculptor's release. ALWAYS output in English — use the official English name if known, otherwise a romanized transliteration; never output Korean/Hangul characters in this field (e.g. "결의 시리즈"→"Resolution Series", "48탄"→"Series 48"). null if truly unidentifiable.
- source_type: classify the product format — one of: "Prize Figure" (경품/이치방쿠지/타이토/세가 프라이즈), "Scale Figure" (스케일/프리페인티드 관상형 피규어), "Nendoroid" (넨도로이드/치비류 데포르메 피규어), "Garage Kit" (레진/GK/미도색 조립식), "Gacha" (가챠/캡슐토이/블라인드박스/리멘트), "Plush" (인형/봉제/베이비), "Model Kit" (프라모델/건프라/조립식 비피규어), "Statue" (대형 스태츄/합금 다이캐스트/아트토이 베어브릭 등), "Etc"
- exclude: true if this listing should be excluded
- exclude_reason: short reason if excluded, else null

Exclusion rules:
- Bulk lot mixing unrelated franchises/characters (keywords: "일괄", "모음", "풀세트", "대량", "잡템") → exclude=true. Exception: an official same-series box/set (e.g. all characters from one Ichiban Kuji release, or a matched multi-figure set from one product line) → keep, character=null, use the set's product_line
- Franchise not identifiable → exclude=true
- Accessory-only listings (case, stand, box, sticker, packaging only, no actual figure/doll) → exclude=true
- Both character AND product_line are null after extraction → exclude=true (nothing to group by)
- Non-collectible items mistakenly in this category (e.g. plain toys, unrelated household items) → exclude=true

Important disambiguation:
- "리멘트" (Re-ment) is a Japanese miniature/gacha brand, not a franchise — classify as source_type "Gacha" and franchise is whatever series it's themed on (e.g. Chiikawa, Sanrio)
- "제일복권"/"이치방쿠지" = Ichiban Kuji, a Japanese lottery-style prize figure format → source_type "Prize Figure"
- Bearbrick/KAWS are designer art toys where the franchise IS the brand itself
- Numbered series like "48탄", "A상", "B상" describe the specific product_line/release, not the franchise"""


def parse_batch(titles: list) -> list:
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": f"Parse these kidult collectible listing titles. Return a JSON array with one object per title in order:\n\n{numbered}"
                }],
                system=SYSTEM_PROMPT,
            )
            raw = message.content[0].text.strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except Exception as e:
            print(f"  배치 오류 (시도 {attempt+1}/3): {e}", file=sys.stderr)
            if attempt < 2:
                time.sleep(2)
    return [{"franchise": None, "character": None, "product_line": None, "source_type": "Etc", "exclude": True, "exclude_reason": "parse error"} for _ in titles]


def normalize(parsed: dict) -> dict:
    if parsed.get("exclude"):
        return parsed

    franchise = (parsed.get("franchise") or "").strip() or None
    character = (parsed.get("character") or "").strip() or None
    product_line = (parsed.get("product_line") or "").strip() or None
    source_type = parsed.get("source_type") or "Etc"

    if not franchise:
        return {**parsed, "exclude": True, "exclude_reason": parsed.get("exclude_reason") or "franchise not identifiable"}

    if not character and not product_line:
        return {
            **parsed,
            "exclude": True,
            "exclude_reason": "character and product_line both missing",
            "artist_normalized": franchise,
        }

    return {
        **parsed,
        "artist_normalized": franchise,
        "member_normalized": character,
        "album_or_event_normalized": product_line or character,
        "source_type_normalized": source_type,
        "group_key": f"{franchise}||{character or ''}||{product_line or ''}||{source_type}",
    }


def main():
    with open("samples.json") as f:
        data = json.load(f)

    items = data["figure_doll"]
    total = len(items)
    print(f"전체 {total}개 파싱 시작")

    existing = {}
    try:
        with open("parsed_kidult.json") as f:
            for r in json.load(f):
                existing[r["pid"]] = r
        print(f"기존 {len(existing)}개 로드, 이어서 처리")
    except FileNotFoundError:
        pass

    results = dict(existing)
    batch_size = 25
    save_interval = 500

    pending = [item for item in items if item["pid"] not in results]
    print(f"미처리 {len(pending)}개 파싱 필요")

    processed = 0
    for i in range(0, len(pending), batch_size):
        batch_items = pending[i:i+batch_size]
        batch_titles = [it["name"] for it in batch_items]

        parsed_list = parse_batch(batch_titles)

        for item, parsed in zip(batch_items, parsed_list):
            merged = {
                "pid": item["pid"],
                "name": item["name"],
                "price": item["price"],
                "image": item.get("product_image", ""),
                **parsed
            }
            normalized = normalize(merged)
            results[item["pid"]] = normalized

        processed += len(batch_items)
        if processed % 500 == 0 or processed == len(pending):
            print(f"  {processed}/{len(pending)}")

        if processed % save_interval == 0:
            with open("parsed_kidult.json", "w", encoding="utf-8") as f:
                json.dump(list(results.values()), f, ensure_ascii=False)

    final = list(results.values())
    with open("parsed_kidult.json", "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False)

    included = [r for r in final if not r.get("exclude") and r.get("group_key")]
    groups = {}
    for r in included:
        k = r["group_key"]
        groups.setdefault(k, []).append(r)

    print(f"\n총 {len(final)}개 파싱 완료")
    print(f"포함 {len(included)}개 / 제외 {len(final)-len(included)}개")
    print(f"총 그룹 수: {len(groups)}개")
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"2개 이상 그룹: {len(multi)}개")
    print("\n상위 10개 그룹:")
    for k, v in sorted(multi.items(), key=lambda x: -len(x[1]))[:10]:
        prices = [r["price"] for r in v]
        print(f"  [{len(v)}개] {k} | 가격: {prices}")
    print("저장 완료")


if __name__ == "__main__":
    main()
