"""
전체 20,025개 포토카드 상품 파싱
samples.json → parsed_all.json
"""

import json
import os
import sys
import time
from typing import Optional
import anthropic
from dotenv import load_dotenv
from normalize import normalize

load_dotenv()

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """You are a K-pop merchandise parser. Extract structured fields from Korean product listing titles.

Output JSON only, no explanation.

Fields to extract:
- artist: group/artist name only. Use the most common Korean or English name as-is (e.g. "방탄소년단", "BTS", "스트레이키즈", "ENHYPEN"). Do NOT confuse album names or event names for artist names.
- member: single member name in Korean (e.g. "정국", "니키"). For official sub-units/units use the unit name as-is (e.g. "부석순", "이오데", "3RACHA", "늑댕즈", "제복", "목갈머", "떨차"). null if no single member or unit is identifiable.
- album_or_event: the most specific album title, concert name, collab brand, or event name found. null if truly absent. Do NOT use generic words like "포카", "양도", "판매" as album names.
- source_type: classify the origin of the photocard — one of: "Album", "Concert", "Fan Meeting", "Season's Greeting", "Fan Club", "Fan Sign", "Collabo", "Benefit", "Etc"
- exclude: true if this listing should be excluded
- exclude_reason: short reason if excluded, else null

Exclusion rules:
- Multiple unrelated members listed (e.g. "성호 리우 재현 태산 이한 운학") → exclude=true. Exception: if they form a known official unit → use unit name, exclude=false
- Bulk lot mixing different albums/events/members (keywords: "일괄", "모음", "풀셋", "대량") → exclude=true
- Artist not identifiable → exclude=true
- Non K-pop content (e.g. esports T1/faker, photocard holders/accessories) → exclude=true
- member field is null after applying above rules → exclude=true

Important disambiguation:
- "타투(Tattoo)" is an NCT 127 album, NOT an artist name
- "SMCU" is an SM Cultures Universe event, NOT an artist name
- "GBGB", "목갈머", "떨차" are TXT sub-unit/album names, not artist names
- "황춘" is a TXT membership card nickname, not a member name
- "멤버십", "팬클럽", "위버스", "키트" → source_type: "Fan Club"
- "미공포", "비공굿", "증사" → source_type: "Benefit"
- "응모", "럭키드로우" → source_type: "Benefit"
- "팬사인회", "싸인포카", "싸폴" → source_type: "Fan Sign"
- "콘서트", "공방", "투어" → source_type: "Concert"
- "콜라보", specific brand names (메디힐, 맥도날드, 크록스 etc.) → source_type: "Collabo"

album_or_event examples by source_type:
- Album: "골든", "로맨스 언톨드", "ODDINARY", "질주"
- Concert: "부산콘", "투어", specific tour name
- Fan Club: "멤버십 3기", "아미키트", "위버스 멤버십"
- Collabo: "메디힐", "크록스", brand name
- Benefit: "미공포", "특전", "알라딘 특전"
- Fan Sign: "팬사인회", "싸폴"
- Fan Meeting: specific fan meeting name"""


def parse_batch(titles: list) -> list:
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": f"Parse these K-pop product listing titles. Return a JSON array with one object per title in order:\n\n{numbered}"
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
    return [{"artist": None, "member": None, "album_or_event": None, "source_type": "Etc", "exclude": True, "exclude_reason": "parse error"} for _ in titles]


def main():
    with open("samples.json") as f:
        data = json.load(f)

    items = data["photocard"]
    total = len(items)
    print(f"전체 {total}개 파싱 시작")

    # 기존 parsed_all.json 이어서 처리 (재시작 지원)
    existing = {}
    try:
        with open("parsed_all.json") as f:
            for r in json.load(f):
                existing[r["pid"]] = r
        print(f"기존 {len(existing)}개 로드, 이어서 처리")
    except FileNotFoundError:
        pass

    results = dict(existing)
    batch_size = 25
    save_interval = 500  # 500개마다 중간 저장

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
                "image": item.get("image", ""),
                **parsed
            }
            normalized = normalize(merged)
            results[item["pid"]] = normalized

        processed += len(batch_items)
        if processed % 500 == 0 or processed == len(pending):
            print(f"  {processed}/{len(pending)}")

        # 500개마다 중간 저장
        if processed % save_interval == 0:
            with open("parsed_all.json", "w", encoding="utf-8") as f:
                json.dump(list(results.values()), f, ensure_ascii=False)

    # 최종 저장
    final = list(results.values())
    with open("parsed_all.json", "w", encoding="utf-8") as f:
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
