"""
포켓몬 카드 파싱
samples.json (pokemon_card) → parsed_pokemon.json
그룹핑 키: Pokemon||{character}||{set_name}||{rarity}
"""

import json
import os
import sys
import time
import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """You are a Pokemon trading card market parser. Extract structured fields from Korean product listing titles.

Output JSON only, no explanation.

Fields to extract:
- character: the Pokemon depicted on the card, in English (e.g. "피카츄"→"Pikachu", "리자몽"→"Charizard", "뮤츠"→"Mewtwo", "이브이"→"Eevee", "이벨타르"→"Yveltal"). null if no single Pokemon is identifiable (e.g. bulk lots, pack sales without a specific card focus, trainer cards).
- set_name: the card set/expansion name in English (e.g. "크림슨헤이즈"→"Crimson Haze", "나이트원더러"→"Night Wanderer", "어비스아이"→"Abyss of the Aqua Deep", "스텔라미라클"→"Stellar Miracle", "붉은섬광"→"Red Flash", "진화의하늘"→"Evolutions in the Sky"). Use the official English set name if known. null if not identifiable.
- rarity: the card rarity/variant in standard notation — one of: "Common", "Uncommon", "Rare", "Double Rare", "Illustration Rare", "Special Illustration Rare", "Ultra Rare", "Hyper Rare", "Secret Rare", "Promo", "Full Art" — use the closest match. null if not clear.
- region: card print region — one of: "Korean", "Japanese", "English", "Other". Default to "Korean" if not specified.
- exclude: true if should be excluded
- exclude_reason: short reason if excluded, else null

Exclusion rules:
- Bulk lots mixing unrelated cards (keywords: "일괄", "모음", "대량", multiple unrelated Pokemon listed) → exclude=true
- Booster pack / sealed product (미개봉 팩, 부스터박스) → exclude=true (we only track individual cards)
- Search pack (서치팩) → exclude=true
- Accessories only (슬리브, 덱케이스, 카드함) → exclude=true
- Price negotiation placeholders with no real card info → exclude=true
- Non-Pokemon card games (매직더개더링, 유희왕 etc.) → exclude=true

Important:
- SAR = Special Illustration Rare, AR = Illustration Rare, SR = Secret Rare, UR = Ultra Rare, RR = Double Rare, HR = Hyper Rare
- "일판" = Japanese, "한판"/"국내판" = Korean, "북미판"/"영판" = English
- A card listing with just a Pokemon name + rarity is valid even without a set name
- Single card sales with clear Pokemon + rarity are the most valuable listings to include"""


def parse_batch(titles: list) -> list:
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": f"Parse these Pokemon card listing titles. Return a JSON array with one object per title in order:\n\n{numbered}"
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
    return [{"character": None, "set_name": None, "rarity": None, "region": "Korean", "exclude": True, "exclude_reason": "parse error"} for _ in titles]


def normalize(parsed: dict) -> dict:
    if parsed.get("exclude"):
        return parsed

    character = (parsed.get("character") or "").strip() or None
    set_name = (parsed.get("set_name") or "").strip() or None
    rarity = (parsed.get("rarity") or "").strip() or None
    region = parsed.get("region") or "Korean"

    # character 또는 (set_name + rarity) 중 하나는 있어야 함
    if not character and not set_name:
        return {**parsed, "exclude": True, "exclude_reason": "character and set_name both missing"}

    # 그룹핑 키: 캐릭터 + 세트 + 레어도 + 지역
    # 레어도 없으면 그룹핑에서 제외 (너무 광범위해짐)
    if not rarity:
        return {**parsed, "exclude": True, "exclude_reason": "rarity unknown — too broad to group"}

    group_key = f"Pokemon||{character or 'Unknown'}||{set_name or 'Unknown Set'}||{rarity}||{region}"

    return {
        **parsed,
        "artist_normalized": "Pokemon",
        "member_normalized": character,
        "album_or_event_normalized": set_name or (character + " card"),
        "source_type_normalized": rarity,
        "group_key": group_key,
    }


def main():
    with open("samples.json") as f:
        data = json.load(f)

    items = data.get("pokemon_card", [])
    if not items:
        print("pokemon_card 데이터 없음. fetch_samples.py 먼저 실행하세요.")
        return

    total = len(items)
    print(f"전체 {total}개 파싱 시작")

    existing = {}
    try:
        with open("parsed_pokemon.json") as f:
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
        batch_items = pending[i:i + batch_size]
        batch_titles = [it["name"] for it in batch_items]

        parsed_list = parse_batch(batch_titles)

        for item, parsed in zip(batch_items, parsed_list):
            merged = {
                "pid": item["pid"],
                "name": item["name"],
                "price": int(str(item.get("price", 0)).replace(",", "").strip() or 0),
                "image": item.get("product_image", ""),
                **parsed,
                "cat": "pokemon",
            }
            normalized = normalize(merged)
            results[item["pid"]] = normalized

        processed += len(batch_items)
        if processed % 500 == 0 or processed == len(pending):
            print(f"  {processed}/{len(pending)}")

        if processed % save_interval == 0:
            with open("parsed_pokemon.json", "w", encoding="utf-8") as f:
                json.dump(list(results.values()), f, ensure_ascii=False)

    final = list(results.values())
    with open("parsed_pokemon.json", "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False)

    included = [r for r in final if not r.get("exclude") and r.get("group_key")]
    groups = {}
    for r in included:
        groups.setdefault(r["group_key"], []).append(r)

    print(f"\n총 {len(final)}개 파싱 완료")
    print(f"포함 {len(included)}개 / 제외 {len(final)-len(included)}개")
    print(f"총 그룹 수: {len(groups)}개")
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"2개 이상 그룹: {len(multi)}개")
    print("\n상위 10개 그룹:")
    for k, v in sorted(multi.items(), key=lambda x: -len(x[1]))[:10]:
        prices = [r["price"] for r in v]
        print(f"  [{len(v)}개] {k} | 가격: {sorted(prices)}")
    print("저장 완료")


if __name__ == "__main__":
    main()
