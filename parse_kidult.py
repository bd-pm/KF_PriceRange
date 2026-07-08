"""
키덜트(피규어/인형) 상품 파싱 (C안: 규칙 기반 추출 + 사전 + LLM)
samples.json (figure_doll) → parsed_kidult.json
그룹핑 키: {franchise}||{character}||{product_line}||{series_num}||{source_type}
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

# ── 사전 로드 ─────────────────────────────────────────────────────────────────
_DICT_DIR = Path(__file__).parent / "dict"
with open(_DICT_DIR / "kidult_products.json", encoding="utf-8") as f:
    _KP = json.load(f)

_PL_KW   = _KP["product_line_keywords"]   # keyword → product_line
_PT_KW   = _KP["prize_tier_keywords"]     # keyword → prize_tier
_FR_KW   = _KP["franchise_keywords"]      # keyword → franchise

# 긴 키워드 우선 정렬
_PL_SORTED = sorted((k for k in _PL_KW if not k.startswith("_")), key=lambda x: -len(x))
_FR_SORTED = sorted((k for k in _FR_KW if not k.startswith("_")), key=lambda x: -len(x))
_PT_SORTED = sorted((k for k in _PT_KW if not k.startswith("_")), key=lambda x: -len(x))

# 탄수 패턴: 48탄, 제48탄, Series 48 등
_SERIES_PAT = re.compile(r'(?:제?\s*(\d{1,3})\s*탄|series\s*(\d{1,3})|시리즈\s*(\d{1,3}))', re.IGNORECASE)
# 건프라 등급 패턴
_GUNPLA_PAT = re.compile(r'\b(HG|MG|RG|PG|SD|EG)\b', re.IGNORECASE)
# 스케일 패턴
_SCALE_PAT  = re.compile(r'1/(\d{1,2})\s*(?:스케일|scale)?', re.IGNORECASE)


# ── 규칙 기반 추출 ────────────────────────────────────────────────────────────

def extract_product_line(name: str) -> Optional[str]:
    """상품명에서 제품라인 규칙 기반 추출"""
    name_lower = name.lower()
    for kw in _PL_SORTED:
        if kw in name_lower:
            pl = _PL_KW[kw]
            # 건프라 등급 보완
            if pl == "Gunpla":
                gm = _GUNPLA_PAT.search(name)
                if gm:
                    return f"Gunpla {gm.group(1).upper()}"
            return pl
    return None


def extract_series_num(name: str) -> Optional[str]:
    """'48탄', 'Series 48' 등 시리즈 회차 추출"""
    m = _SERIES_PAT.search(name)
    if m:
        num = m.group(1) or m.group(2) or m.group(3)
        return f"Series {num}"
    return None


def extract_prize_tier(name: str) -> Optional[str]:
    """이치방쿠지 상품 등급 추출 (A상, B상, 라스트원 등)"""
    name_lower = name.lower()
    for kw in _PT_SORTED:
        if kw in name_lower:
            return _PT_KW[kw]
    return None


def extract_franchise(name: str) -> Optional[str]:
    """상품명에서 프랜차이즈 규칙 기반 추출"""
    name_lower = name.lower()
    for kw in _FR_SORTED:
        if kw in name_lower:
            return _FR_KW[kw]
    return None


# ── LLM 파싱 ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a kidult collectibles (figures/dolls/toys) merchandise parser. Extract structured fields from Korean product listing titles.

Output JSON only, no explanation.

Fields to extract:
- franchise: the IP/series/brand this item belongs to. Use the most common English name (e.g. "포켓몬스터"→"Pokemon", "진격의거인"→"Attack on Titan", "원피스"→"One Piece", "귀멸의칼날"→"Demon Slayer", "짱구는못말려"→"Crayon Shin-chan", "산리오"→"Sanrio", "치이카와"→"Chiikawa", "디즈니"→"Disney", "건담"→"Gundam", "베어브릭"→"Bearbrick", "카우스"→"KAWS"). null if not identifiable.
- character: the specific character depicted, in the most common English/romanized name (e.g. "리자몽"→"Charizard", "루피"→"Luffy", "쿠로미"→"Kuromi", "미카사"→"Mikasa"). null if no single character is identifiable.
- product_line: the specific product series/release/format — e.g. "Ichiban Kuji", "Nendoroid", "Pop Mart", "Scale World", "Moncolle", "Converge", a specific gacha series name. ALWAYS output in English. null if truly unidentifiable. Do NOT repeat what's already in [hint] fields — just confirm or refine.
- series_num: the specific release/series number if present (e.g. "Series 48", "Series 9"). null if not present.
- prize_tier: for Ichiban Kuji items, the prize tier (e.g. "A Prize", "B Prize", "Last One"). null otherwise.
- source_type: one of: "Prize Figure", "Scale Figure", "Nendoroid", "Garage Kit", "Gacha", "Plush", "Model Kit", "Statue", "Etc"
- exclude: true if this listing should be excluded
- exclude_reason: short reason if excluded, else null

Exclusion rules:
- Bulk lot mixing unrelated franchises/characters → exclude=true. Exception: official same-series set → keep, character=null
- Franchise not identifiable → exclude=true
- Accessory-only listings (case, stand, box, sticker only) → exclude=true
- Both character AND product_line are null → exclude=true

Important:
- "리멘트" (Re-ment) = source_type "Gacha", not a franchise
- "이치방쿠지"/"제일복권" = Ichiban Kuji → source_type "Prize Figure"
- Numbered series "48탄", "A상", "B상" → series_num / prize_tier fields
- Use hint fields as strong signals but correct if clearly wrong"""


def parse_batch(items: list) -> list:
    """LLM으로 배치 파싱. 규칙 추출 결과도 힌트로 전달"""
    lines = []
    for i, it in enumerate(items):
        name = it["name"]
        rule_pl = extract_product_line(name)
        rule_sn = extract_series_num(name)
        rule_pt = extract_prize_tier(name)
        rule_fr = extract_franchise(name)

        hints = []
        if rule_fr:
            hints.append(f"franchise hint: {rule_fr}")
        if rule_pl:
            hints.append(f"product_line hint: {rule_pl}")
        if rule_sn:
            hints.append(f"series_num hint: {rule_sn}")
        if rule_pt:
            hints.append(f"prize_tier hint: {rule_pt}")

        hint_str = (" [" + ", ".join(hints) + "]") if hints else ""
        lines.append(f"{i+1}. {name}{hint_str}")

    numbered = "\n".join(lines)
    for attempt in range(3):
        try:
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": f"Parse these kidult collectible listing titles. Return a JSON array with one object per title:\n\n{numbered}"
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
    return [{"franchise": None, "character": None, "product_line": None,
             "series_num": None, "prize_tier": None,
             "source_type": "Etc", "exclude": True, "exclude_reason": "parse error"} for _ in items]


# ── 정규화 ────────────────────────────────────────────────────────────────────

def normalize(item: dict, parsed: dict) -> dict:
    name = item["name"]

    if parsed.get("exclude"):
        return {**item, **parsed}

    franchise   = (parsed.get("franchise")    or "").strip() or None
    character   = (parsed.get("character")    or "").strip() or None
    product_line = (parsed.get("product_line") or "").strip() or None
    series_num  = (parsed.get("series_num")   or "").strip() or None
    prize_tier  = (parsed.get("prize_tier")   or "").strip() or None
    source_type = parsed.get("source_type") or "Etc"

    # 규칙 기반 보완
    if not franchise:
        franchise = extract_franchise(name)
    if not product_line:
        product_line = extract_product_line(name)
    if not series_num:
        series_num = extract_series_num(name)
    if not prize_tier and product_line and "Ichiban" in product_line:
        prize_tier = extract_prize_tier(name)

    if not franchise:
        return {**item, **parsed, "exclude": True, "exclude_reason": "franchise not identifiable"}

    if not character and not product_line:
        return {**item, **parsed, "exclude": True,
                "exclude_reason": "character and product_line both missing",
                "artist_normalized": franchise}

    # 이치방쿠지: product_line에 prize_tier 포함시켜 더 세분화
    pl_key = product_line or ""
    if prize_tier and product_line and "Ichiban" in product_line:
        pl_key = f"{product_line} {prize_tier}"

    # series_num이 있으면 product_line에 포함
    if series_num and pl_key:
        pl_key = f"{pl_key} {series_num}"
    elif series_num:
        pl_key = series_num

    char_key = character or ""
    group_key = f"{franchise}||{char_key}||{pl_key}||{source_type}"

    return {
        **item,
        **parsed,
        "franchise": franchise,
        "character": character,
        "product_line": product_line,
        "series_num": series_num,
        "prize_tier": prize_tier,
        "artist_normalized": franchise,
        "member_normalized": character,
        "album_or_event_normalized": pl_key or char_key,
        "source_type_normalized": source_type,
        "group_key": group_key,
        "exclude": False,
    }


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    with open("samples.json") as f:
        data = json.load(f)

    items = data.get("figure_doll", [])
    if not items:
        print("figure_doll 데이터 없음. fetch_samples.py 먼저 실행하세요.")
        return

    total = len(items)
    print(f"전체 {total}개 파싱 시작 (전부 재파싱)")

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
                "cat": "kidult",
            }
            results[item["pid"]] = normalize(base, parsed)

        processed += len(batch)
        if processed % 500 == 0 or processed == len(items):
            print(f"  {processed}/{len(items)}")

        if processed % save_interval == 0:
            with open("parsed_kidult.json", "w", encoding="utf-8") as f:
                json.dump(list(results.values()), f, ensure_ascii=False)

    final = list(results.values())
    with open("parsed_kidult.json", "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False)

    # 통계
    included = [r for r in final if not r.get("exclude") and r.get("group_key")]
    groups = {}
    for r in included:
        groups.setdefault(r["group_key"], []).append(r)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}

    print(f"\n총 {len(final)}개 파싱 완료")
    print(f"포함 {len(included)}개 / 제외 {len(final)-len(included)}개")
    print(f"총 그룹 수: {len(groups)}개")
    print(f"2개 이상 그룹: {len(multi)}개")
    print("\n상위 15개 그룹:")
    for k, v in sorted(multi.items(), key=lambda x: -len(x[1]))[:15]:
        prices = sorted([r["price"] for r in v])
        print(f"  [{len(v)}] {k}")
        print(f"       가격: {prices[:6]}{'...' if len(prices)>6 else ''}")
    print("저장 완료")


if __name__ == "__main__":
    main()
