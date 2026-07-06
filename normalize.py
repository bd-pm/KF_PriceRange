"""
딕셔너리 기반 정규화 레이어.
LLM이 파싱한 결과의 표기 불일치를 dict/ 파일 기준으로 교정.
"""

import json
import re
from pathlib import Path
from typing import Optional

DICT_DIR = Path(__file__).parent / "dict"

def _load(filename: str) -> dict:
    with open(DICT_DIR / filename, encoding="utf-8") as f:
        return json.load(f)

# 딕셔너리 로드
_artists = {k: v for k, v in _load("artists.json").items() if not k.startswith("_")}
_members = {k: v for k, v in _load("members.json").items() if not k.startswith("_")}
_albums = {k: v for k, v in _load("albums.json").items() if not k.startswith("_")}
_events = _load("events.json")
_units = {k: v for k, v in _load("units.json").items() if not k.startswith("_")}

# 역방향 인덱스: alias → canonical
def _build_reverse(d: dict) -> dict[str, str]:
    rev = {}
    for canonical, aliases in d.items():
        for alias in aliases:
            rev[alias.lower()] = canonical
    return rev

_artist_rev = _build_reverse(_artists)

def _build_member_rev(members_dict: dict) -> dict[str, dict[str, str]]:
    """artist → {alias_lower → canonical_member}"""
    rev = {}
    for artist, members in members_dict.items():
        rev[artist] = {}
        for canonical, aliases in members.items():
            for alias in aliases:
                rev[artist][alias.lower()] = canonical
    return rev

_member_rev = _build_member_rev(_members)

def _build_album_rev(albums_dict: dict) -> dict[str, dict[str, str]]:
    """artist → {alias_lower → canonical_album}"""
    rev = {}
    for artist, albums in albums_dict.items():
        rev[artist] = {}
        for canonical, aliases in albums.items():
            for alias in aliases:
                rev[artist][alias.lower()] = canonical
    return rev

_album_rev = _build_album_rev(_albums)

def _build_unit_rev(units_dict: dict) -> dict[str, dict[str, str]]:
    """artist → {alias_lower → canonical_unit}"""
    rev = {}
    for artist, units in units_dict.items():
        rev[artist] = {}
        for canonical, info in units.items():
            for alias in info.get("aliases", []):
                rev[artist][alias.lower()] = canonical
    return rev

_unit_rev = _build_unit_rev(_units)

# source_type 키워드 인덱스
_source_keywords: dict[str, list[str]] = _events.get("source_type_keywords", {})
_event_aliases: dict[str, str] = _events.get("event_aliases", {})

# 멤버명 → 아티스트 역추적 인덱스: member_alias_lower → artist
def _build_member_to_artist(members_dict: dict) -> dict:
    rev = {}
    for artist, members in members_dict.items():
        for canonical, aliases in members.items():
            for alias in aliases:
                key = alias.lower()
                if key not in rev:
                    rev[key] = []
                if artist not in rev[key]:
                    rev[key].append(artist)
    return rev

_member_to_artist = _build_member_to_artist(_members)


def infer_artist_from_member(raw_member: Optional[str]) -> Optional[str]:
    """멤버명만 있을 때 아티스트 역추적. 멤버명이 여러 그룹에 속하면 None."""
    if not raw_member:
        return None
    key = raw_member.strip().lower()
    candidates = _member_to_artist.get(key, [])
    if len(candidates) == 1:
        return candidates[0]
    return None  # 동명 멤버가 여러 그룹에 있으면 추론 불가


def normalize_artist(raw: Optional[str]) -> Optional[str]:
    """아티스트명 정규화. 매칭 실패시 None 반환."""
    if not raw:
        return None
    key = raw.strip().lower()
    return _artist_rev.get(key)


def normalize_member(artist: str, raw: Optional[str]) -> Optional[str]:
    """멤버명 정규화. 유닛명도 처리. 매칭 실패시 None 반환."""
    if not raw:
        return None
    key = raw.strip().lower()

    # 유닛 먼저 확인
    unit_map = _unit_rev.get(artist, {})
    if key in unit_map:
        return unit_map[key]

    # 단일 멤버 확인
    member_map = _member_rev.get(artist, {})
    if key in member_map:
        return member_map[key]

    # 멤버명이 알려진 멤버 2명 이상 포함하면 → 나열 감지
    all_known = set(member_map.values())
    found = [m for m in all_known if m in raw]
    if len(found) >= 2:
        # 공식 유닛인지 확인
        for unit_canonical, info in _units.get(artist, {}).items():
            unit_members = set(info.get("members", []))
            if unit_members and set(found) == unit_members:
                return unit_canonical
        return None  # 비공식 복수 → 제외

    return None


def normalize_album_or_event(artist: str, raw: Optional[str]) -> Optional[str]:
    """앨범/이벤트명 정규화. event_aliases도 확인."""
    if not raw:
        return None
    key = raw.strip().lower()

    # event_aliases 확인
    for alias, canonical in _event_aliases.items():
        if alias.lower() in key:
            return canonical

    # 앨범 딕셔너리 확인
    album_map = _album_rev.get(artist, {})
    if key in album_map:
        return album_map[key]

    # 부분 매칭 (앨범명이 raw에 포함된 경우)
    for alias_lower, canonical in album_map.items():
        if alias_lower in key:
            return canonical

    # 정규화 실패 → LLM이 뽑은 값 그대로 사용 (모르는 앨범일 수 있음)
    return raw.strip()


def normalize_source_type(raw: Optional[str], album_or_event: Optional[str]) -> Optional[str]:
    """source_type 정규화. album_or_event 값도 참고."""
    valid = {"Album", "Concert", "Fan Meeting", "Season's Greeting",
             "Fan Club", "Fan Sign", "Collabo", "Benefit", "Etc"}
    if raw in valid:
        return raw

    # 키워드로 재추론
    combined = " ".join(filter(None, [raw, album_or_event])).lower()
    for stype, keywords in _source_keywords.items():
        for kw in keywords:
            if kw.lower() in combined:
                return stype

    return "Etc"


def normalize(parsed: dict) -> dict:
    """
    LLM 파싱 결과 dict를 받아 정규화된 dict 반환.
    exclude=True인 항목은 그대로 반환.
    """
    if parsed.get("exclude"):
        return parsed

    raw_artist = parsed.get("artist")
    raw_member = parsed.get("member")
    raw_album = parsed.get("album_or_event")
    raw_source = parsed.get("source_type")

    artist = normalize_artist(raw_artist)
    if not artist:
        # 아티스트 직접 매칭 실패 → 멤버명으로 역추적 시도
        inferred = infer_artist_from_member(raw_member)
        if inferred:
            artist = inferred
        else:
            # 역추적도 실패 → LLM 결과 그대로 유지 (신규 그룹일 수 있음)
            artist = raw_artist

    member = normalize_member(artist, raw_member) if artist else None

    album_or_event = normalize_album_or_event(artist or "", raw_album)
    source_type = normalize_source_type(raw_source, album_or_event)

    # 멤버 없으면 제외
    if not member:
        return {
            **parsed,
            "exclude": True,
            "exclude_reason": parsed.get("exclude_reason") or "member not identifiable after normalization",
            "artist_normalized": artist,
        }

    # 앨범/이벤트 없으면 제외
    if not album_or_event:
        return {
            **parsed,
            "exclude": True,
            "exclude_reason": "album_or_event missing",
            "artist_normalized": artist,
            "member_normalized": member,
        }

    return {
        **parsed,
        "artist_normalized": artist,
        "member_normalized": member,
        "album_or_event_normalized": album_or_event,
        "source_type_normalized": source_type,
        # 그룹핑 키
        "group_key": f"{artist}||{member}||{album_or_event}||{source_type}",
    }
