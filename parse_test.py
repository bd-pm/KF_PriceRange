"""
케이팝 포토카드 상품명 파싱 테스트
LLM으로 상품명 → 구조화된 필드 추출
"""

import json
import os
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

def parse_batch(titles: list[str]) -> list[dict]:
    """상품명 배치를 LLM으로 파싱"""
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": f"Parse these K-pop product listing titles. Return a JSON array with one object per title in order:\n\n{numbered}"
            }
        ],
        system=SYSTEM_PROMPT,
    )

    raw = message.content[0].text.strip()
    # JSON 블록 추출
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw)


def main():
    with open("samples.json") as f:
        data = json.load(f)

    titles = [item["name"] for item in data["photocard"][:50]]

    print(f"파싱 대상: {len(titles)}개\n")

    # 배치 25개씩
    results = []
    for i in range(0, len(titles), 25):
        batch = titles[i:i+25]
        print(f"배치 {i//25 + 1} 처리중... ({len(batch)}개)")
        parsed = parse_batch(batch)
        results.extend(parsed)

    # 원본 상품 데이터와 합치기 + 정규화
    combined = []
    for item, parsed in zip(data["photocard"][:50], results):
        merged = {
            "pid": item["pid"],
            "name": item["name"],
            "price": item["price"],
            **parsed
        }
        normalized = normalize(merged)
        combined.append(normalized)

    # 결과 출력
    print("\n=== 파싱 + 정규화 결과 ===\n")

    included = [r for r in combined if not r.get("exclude")]
    excluded = [r for r in combined if r.get("exclude")]

    print(f"포함: {len(included)}개 / 제외: {len(excluded)}개\n")

    print("--- 포함된 상품 (그룹핑 키 포함) ---")
    for r in included:
        print(f"  KEY: {r.get('group_key')}")
        print(f"  원본: {r['name']} | {r['price']}원")
        print()

    print("--- 제외된 상품 ---")
    for r in excluded:
        print(f"  제외사유: {r.get('exclude_reason')}")
        print(f"  원본: {r['name']}")
        print()

    with open("parse_results.json", "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    print("저장 완료: parse_results.json")


if __name__ == "__main__":
    main()
