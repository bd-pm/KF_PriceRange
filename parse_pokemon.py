"""
포켓몬 카드 파싱 (C안: 카드 번호 규칙 추출 + 세트 사전 + LLM)
samples.json (pokemon_card) → parsed_pokemon.json
그룹핑 키: Pokemon||{character}||{set_name}||{rarity}||{region}
"""

import json
import os
import re
import sys
import time
from typing import Optional, Tuple
import anthropic
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ── 세트 사전 로드 ──────────────────────────────────────────────────────────
_DICT_DIR = Path(__file__).parent / "dict"
with open(_DICT_DIR / "pokemon_sets.json", encoding="utf-8") as f:
    _SETS = json.load(f)

_BY_TOTAL   = _SETS["by_total"]    # total번호 → set
_BY_CODE    = _SETS["by_code"]     # 세트코드 → set
_BY_KEYWORD = _SETS["by_keyword"]  # 키워드 → set명 (긴 것 우선)
_GRADED_KW  = set(k.upper() for k in _SETS["graded_keywords"])
_RARITY_ALI = _SETS["rarity_aliases"]

# 키워드 길이 내림차순 정렬 (긴 것 우선 매칭)
_KW_SORTED = sorted(_BY_KEYWORD.keys(), key=lambda x: -len(x))

# ── 규칙 기반 추출 함수들 ──────────────────────────────────────────────────

_CARD_NUM_PAT = re.compile(r'(\d{1,3})/(\d{2,3})')
_SET_CODE_PAT = re.compile(r'\b(BRG\d+|CHR|SV\d+[A-Z0-9]*|SVP|SMP|SVBD|BW\d+|XY\d+|SM\d+|RS\d+)\b', re.IGNORECASE)
_REGION_PAT   = re.compile(r'(일판|일본판|일어판|japan|japanese|한판|한국판|국내판|korean|북미판|영판|영어판|english|북미)', re.IGNORECASE)
_RARITY_PAT   = re.compile(r'\b(SAR|AR|SR|UR|RR|HR|CHR|CSR|SSR|VMAX|VSTAR|EX|GX|V\b)', re.IGNORECASE)
_GRADED_PAT   = re.compile(r'\b(PSA|CGC|BGS|BCCG|KSA|AGS)\s*(\d+(?:\.\d+)?)\b', re.IGNORECASE)


def extract_set_from_name(name: str) -> Tuple[Optional[str], Optional[str]]:
    """상품명에서 (세트명, 카드번호) 규칙 기반 추출"""
    name_lower = name.lower()

    # 1. 카드 번호(NNN/TTT)로 세트 역추적
    card_num = None
    set_name = None
    m = _CARD_NUM_PAT.search(name)
    if m:
        card_num = m.group(0)
        total = m.group(2)
        if total in _BY_TOTAL:
            set_name = _BY_TOTAL[total]["name"]

    # 2. 세트 코드(BRG9, SV4A 등)로 세트명
    if not set_name:
        for cm in _SET_CODE_PAT.finditer(name):
            code = cm.group(1).upper()
            if code in _BY_CODE:
                set_name = _BY_CODE[code]["name"]
                break

    # 3. 키워드 매칭 (긴 것 우선)
    if not set_name:
        for kw in _KW_SORTED:
            if kw in name_lower:
                set_name = _BY_KEYWORD[kw]
                break

    return set_name, card_num


def extract_region(name: str) -> str:
    m = _REGION_PAT.search(name)
    if not m:
        return "Korean"
    t = m.group(1).lower()
    if any(x in t for x in ["일판", "일본", "japan"]):
        return "Japanese"
    if any(x in t for x in ["북미", "영판", "english"]):
        return "English"
    return "Korean"


def is_graded(name: str) -> bool:
    return bool(_GRADED_PAT.search(name))


def extract_rarity_from_name(name: str) -> Optional[str]:
    """상품명에서 레어도 약칭 추출 → 정식 명칭으로 변환"""
    # 우선순위: SAR > AR > SR > UR > RR > HR
    priority = ["SAR", "HR", "SR", "UR", "AR", "RR", "CHR", "CSR"]
    name_upper = name.upper()
    for r in priority:
        if re.search(r'\b' + r + r'\b', name_upper):
            return _RARITY_ALI.get(r, r)
    # VMAX/VSTAR/EX/GX/V 단독 → Double Rare 이상으로 처리
    if re.search(r'\bVMAX\b|\bVSTAR\b', name_upper):
        return "Ultra Rare"
    if re.search(r'\bEX\b|\bGX\b', name_upper):
        return "Double Rare"
    return None


# ── LLM 파싱 ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Pokemon trading card market parser. Extract structured fields from Korean product listing titles.

Output JSON only, no explanation.

Fields to extract:
- character: the Pokemon depicted on the card, in English (e.g. "피카츄"→"Pikachu", "리자몽"→"Charizard", "뮤츠"→"Mewtwo", "이브이"→"Eevee", "이벨타르"→"Yveltal", "잉어킹"→"Magikarp", "날뛰는우레"→"Raging Bolt", "기라티나"→"Giratina"). null if no single Pokemon is identifiable.
- rarity: card rarity in standard notation — one of: "Common", "Uncommon", "Rare", "Double Rare", "Illustration Rare", "Special Illustration Rare", "Ultra Rare", "Hyper Rare", "Secret Rare", "Character Rare", "Full Art", "Promo". Map abbreviations: SAR→Special Illustration Rare, AR→Illustration Rare, SR→Secret Rare, UR→Ultra Rare, RR→Double Rare, HR→Hyper Rare, CHR→Character Rare. null if not determinable.
- set_name: official English set name if you can identify it (e.g. "어비스아이"→"Abyss of the Aqua Deep", "크림슨헤이즈"→"Crimson Haze", "스텔라미라클"→"Stellar Miracle", "나이트원더러"→"Night Wanderer", "SV2A/151"→"Scarlet & Violet 151", "BRG9"→"Stellar Miracle", "BRG10"→"Abyss of the Aqua Deep"). null if not identifiable.
- exclude: true if should be excluded
- exclude_reason: short reason if excluded, else null

Exclusion rules:
- Bulk lots with multiple unrelated Pokemon or sets → exclude=true (단, 같은 캐릭터 여러 장은 OK)
- Sealed booster packs/boxes (미개봉 팩, 부스터박스, 팩 단위 판매) → exclude=true
- Search packs (서치팩) → exclude=true
- Accessories only (슬리브, 덱케이스, 바인더) → exclude=true
- Non-Pokemon cards (유희왕, 매직더개더링) → exclude=true
- PSA/CGC/BGS graded cards → exclude=true (separate graded market)

Important:
- Single cards with clear Pokemon + rarity → include even without set name
- "특일" = Japanese special illustration version (still Korean market listing)
- Card with number like "234/193" → the /193 identifies the set (Scarlet & Violet 151)"""


def parse_batch(items: list) -> list:
    """LLM으로 배치 파싱. 규칙 추출 결과도 힌트로 전달"""
    lines = []
    for i, it in enumerate(items):
        rule_set, card_num = extract_set_from_name(it["name"])
        hint = ""
        if rule_set:
            hint += f" [set hint: {rule_set}]"
        if card_num:
            hint += f" [card#: {card_num}]"
        lines.append(f"{i+1}. {it['name']}{hint}")

    numbered = "\n".join(lines)
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{"role": "user", "content": f"Parse these Pokemon card listings. Return a JSON array with one object per title:\n\n{numbered}"}],
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
    return [{"character": None, "rarity": None, "set_name": None, "exclude": True, "exclude_reason": "parse error"} for _ in items]


# ── 정규화 ────────────────────────────────────────────────────────────────

def normalize(item: dict, parsed: dict) -> dict:
    name = item["name"]

    # 그레이딩 카드 제외
    if is_graded(name):
        return {**item, **parsed, "exclude": True, "exclude_reason": "graded card (PSA/CGC/BGS)"}

    if parsed.get("exclude"):
        return {**item, **parsed}

    character = (parsed.get("character") or "").strip() or None
    rarity = (parsed.get("rarity") or "").strip() or None
    set_name = (parsed.get("set_name") or "").strip() or None

    # 규칙 기반 보완: 레어도 LLM이 못 잡은 경우
    if not rarity:
        rarity = extract_rarity_from_name(name)

    # 규칙 기반 보완: 세트명 LLM이 못 잡은 경우
    if not set_name:
        set_name, _ = extract_set_from_name(name)

    region = extract_region(name)

    # 레어도 없으면 그룹핑 불가
    if not rarity:
        return {**item, **parsed, "exclude": True, "exclude_reason": "rarity unknown"}

    # 캐릭터도 세트도 없으면 제외
    if not character and not set_name:
        return {**item, **parsed, "exclude": True, "exclude_reason": "character and set both missing"}

    char_key  = character or "Unknown"
    set_key   = set_name or "Unknown Set"
    group_key = f"Pokemon||{char_key}||{set_key}||{rarity}||{region}"

    return {
        **item,
        **parsed,
        "character": character,
        "set_name": set_name,
        "rarity": rarity,
        "region": region,
        "artist_normalized": "Pokemon",
        "member_normalized": character,
        "album_or_event_normalized": set_name or char_key,
        "source_type_normalized": rarity,
        "group_key": group_key,
        "exclude": False,
    }


# ── 메인 ──────────────────────────────────────────────────────────────────

def main():
    with open("samples.json") as f:
        data = json.load(f)

    items = data.get("pokemon_card", [])
    if not items:
        print("pokemon_card 데이터 없음. fetch_samples.py 먼저 실행하세요.")
        return

    total = len(items)
    print(f"전체 {total}개 파싱 시작")

    # 재시작 지원
    existing = {}
    try:
        with open("parsed_pokemon.json") as f:
            for r in json.load(f):
                existing[r["pid"]] = r
        print(f"기존 {len(existing)}개 로드 (전부 재파싱)")
    except FileNotFoundError:
        pass

    # 전부 재파싱 (프롬프트/사전 변경됐으므로)
    results = {}
    batch_size = 25
    save_interval = 500

    processed = 0
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        parsed_list = parse_batch(batch)

        for item, parsed in zip(batch, parsed_list):
            base = {
                "pid": item["pid"],
                "name": item["name"],
                "price": int(str(item.get("price", 0)).replace(",", "").strip() or 0),
                "image": item.get("product_image", "") or item.get("image", ""),
                "cat": "pokemon",
            }
            results[item["pid"]] = normalize(base, parsed)

        processed += len(batch)
        if processed % 500 == 0 or processed == len(items):
            print(f"  {processed}/{len(items)}")

        if processed % save_interval == 0:
            with open("parsed_pokemon.json", "w", encoding="utf-8") as f:
                json.dump(list(results.values()), f, ensure_ascii=False)

    final = list(results.values())
    with open("parsed_pokemon.json", "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False)

    # 통계
    included = [r for r in final if not r.get("exclude") and r.get("group_key")]
    groups = {}
    for r in included:
        groups.setdefault(r["group_key"], []).append(r)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}

    unknown_set = [r for r in included if "Unknown Set" in r.get("group_key", "")]
    known_set   = [r for r in included if "Unknown Set" not in r.get("group_key", "")]

    print(f"\n총 {len(final)}개 파싱 완료")
    print(f"포함 {len(included)}개 / 제외 {len(final)-len(included)}개")
    print(f"세트명 확인: {len(known_set)}개 / Unknown Set: {len(unknown_set)}개")
    print(f"그룹 {len(groups)}개 / 2개 이상 {len(multi)}개")
    print("\n상위 10개 그룹:")
    for k, v in sorted(multi.items(), key=lambda x: -len(x[1]))[:10]:
        prices = sorted([r["price"] for r in v])
        print(f"  [{len(v)}] {k}")
        print(f"       가격: {prices[:8]}{'...' if len(prices)>8 else ''}")
    print("저장 완료")


if __name__ == "__main__":
    main()
