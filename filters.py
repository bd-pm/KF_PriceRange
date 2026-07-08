"""
공통 사전 필터: LLM 호출 전에 규칙으로 제외할 항목을 걸러냄.
판매 글이 아닌 구매/구함 글 등을 제거.
"""

import re

# 구매 의사 표현 패턴 (제목 앞쪽에 주로 등장)
_BUY_PATTERNS = [
    r"구입\s*합니다",
    r"구입\s*해요",
    r"구입\s*원",
    r"구매\s*합니다",
    r"구매\s*해요",
    r"구매\s*원",
    r"삽니다",
    r"사요",
    r"살게요",
    r"살거에요",
    r"살께요",
    r"살겁니다",
    r"찾습니다",
    r"찾아요",
    r"찾고\s*있",
    r"구합니다",
    r"구해요",
    r"구하고\s*있",
    r"매입\s*합니다",
    r"매입\s*해요",
    r"매입\s*원",
    r"급구",
    r"급하게\s*구",
    r"급히\s*구",
    r"WTB\b",           # Want To Buy
    r"\bwtb\b",
    r"구입가",          # "구입가 이하" 등 (구입가 기재 후 되팜도 있어서 주의)
    r"교환\s*원합니다",  # 판매 아닌 교환 구함
    r"교환\s*구합니다",
]

_BUY_RE = re.compile("|".join(_BUY_PATTERNS), re.IGNORECASE)


def is_wanted_post(name: str) -> bool:
    """구매/구함 글이면 True"""
    return bool(_BUY_RE.search(name))


def pre_filter(item: dict):
    """
    LLM 전 사전 필터. 제외 대상이면 exclude=True dict 반환, 통과면 None.
    반환값이 None이 아니면 LLM 호출 건너뜀.
    """
    name = item.get("name", "")
    if is_wanted_post(name):
        return {
            **item,
            "exclude": True,
            "exclude_reason": "wanted post (구매/구함 글)",
        }
    return None
