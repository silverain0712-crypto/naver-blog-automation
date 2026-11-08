"""협찬 가이드 → 체크리스트(하드 제약)로 뽑아 주입하고, 작성 뒤 실제로 지켰는지 세어서 어기면 고쳐 쓴다.

배경(2026-11-08): 가이드 원문을 프롬프트에 '참고'로만 넣었더니 규칙이 흩어진 20K자 프롬프트(V7·제목규칙·SEO)에
묻혀 반영이 불안정했다. 실측 사례 — 전화신점 체험단: 제목 필수 키워드(정확도·질문리스트)를 빼먹었고 제목 금지어를 점검하지 않았다.
방식: (1) 원문에서 '지켜야 할 것'만 구조화 추출 → (2) 시스템·사용자 프롬프트 양쪽에 체크리스트로 재주입 →
(3) 결과를 코드로 검수(제목/본문 필수·금지 키워드, 해시태그, 글자수, 필수 문구·링크) → (4) 위반이 있으면 1회 수정 호출 →
(5) 그래도 남으면 confirm_needed 로 사용자에게 올린다(조용히 넘기지 않는다).
"""

from __future__ import annotations

import re

import config
from modules.llm import call_json

_SCHEMA = {
    "type": "object",
    "properties": {
        "title_required_keywords": {"type": "array", "items": {"type": "string"}},
        "title_required_any": {"type": "array", "items": {"type": "string"}},
        "title_forbidden_words": {"type": "array", "items": {"type": "string"}},
        "title_max_chars": {"type": "integer"},
        "body_required_keywords": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"word": {"type": "string"}, "min_count": {"type": "integer"}},
                "required": ["word", "min_count"],
            },
        },
        "body_forbidden_words": {"type": "array", "items": {"type": "string"}},
        "required_hashtags": {"type": "array", "items": {"type": "string"}},
        "body_min_chars": {"type": "integer"},
        "body_max_chars": {"type": "integer"},
        "required_phrases": {"type": "array", "items": {"type": "string"}},
        "required_links": {"type": "array", "items": {"type": "string"}},
        "subheading_order": {"type": "array", "items": {"type": "string"}},
        "other_rules": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title_required_keywords", "title_required_any", "title_forbidden_words", "title_max_chars", "body_required_keywords",
        "body_forbidden_words", "required_hashtags", "body_min_chars", "body_max_chars",
        "required_phrases", "required_links", "subheading_order", "other_rules",
    ],
}

_EXTRACT_SYSTEM = (
    "너는 블로그 체험단 가이드라인 분석가다. 아래 원문에서 작성자가 '반드시 지켜야 하는 것'만 빠짐없이 뽑아 JSON 으로 낸다.\n"
    "- 원문에 있는 것만. 없으면 빈 배열/0. 추측·보충 금지. 홍보 문구·업체 소개는 뽑지 마라.\n"
    "- title_required_keywords: 제목에 꼭 들어가야 하는 단어('필수 키워드', '제목에 포함' 등). 예시 문장(ex.)에서 만든 것이 아니라 지시된 단어만.\n"
    "- title_required_any: 후보 키워드 목록 중 '1개 이상' 골라 제목에 넣으라는 경우의 후보 키워드들(전부 넣으라는 게 아님). '필수'라도 '이 중 1개 이상'이면 여기로.\n"
    "- title_forbidden_words: 제목에 쓰면 안 되는 단어('금지 키워드' 등). 본문 금지어는 body_forbidden_words 로.\n"
    "- title_max_chars: 제목 글자수 제한(없으면 0). 띄어쓰기 제외/포함 구분 없이 숫자만.\n"
    "- body_required_keywords: 본문에 N회 넣으라는 단어와 횟수(횟수 불명이면 3).\n"
    "- required_hashtags: 가이드가 지정한 필수 해시태그(# 없이 단어만).\n"
    "- body_min_chars/body_max_chars: 본문 글자수 제한(없으면 0).\n"
    "- required_phrases: 원문이 '이 문구를 그대로 넣어라'고 지정한 고정 문구(공정위 문구·협찬 표기 등)만, 원문 글자 그대로. 의미만 있는 요구(예: '~를 언급')는 여기 말고 other_rules 로.\n"
    "- required_links: 본문에 넣으라는 링크/URL.\n"
    "- subheading_order: 소제목 구성·순서를 지정했으면 그 순서대로.\n"
    "- other_rules: 위에 안 맞는 필수·금지 사항을 한 줄씩(예: '타사 비교 금지', '소제목2에 들은 멘트를 인용구로 강조', "
    "'실제 경험한 항목이 있으면 사진과 함께 어필'). 최대 12개, 각 120자 이내."
)


def extract(guideline: str) -> dict:
    """가이드 원문 → 체크리스트 dict. 실패하면 빈 dict(작성 자체는 계속 진행)."""
    g = (guideline or "").strip()
    if not g:
        return {}
    try:
        out = call_json(
            model=config.NAEO_AUDIT_MODEL,
            system=_EXTRACT_SYSTEM,
            content=[{"type": "text", "text": g[:12000]}],
            schema=_SCHEMA,
            max_tokens=4000,
        )
    except Exception as e:  # 추출 실패해도 원문 주입은 그대로 간다
        print(f"  (가이드 체크리스트 추출 실패, 원문만 사용: {str(e)[:80]})")
        return {}
    return _clean(out)


def _clean(c: dict) -> dict:
    def strs(k):
        return [s.strip() for s in (c.get(k) or []) if isinstance(s, str) and s.strip()]

    kws = []
    for it in c.get("body_required_keywords") or []:
        if isinstance(it, dict) and str(it.get("word", "")).strip():
            kws.append({"word": it["word"].strip(), "min_count": max(1, int(it.get("min_count") or 3))})
    return {
        "title_required_keywords": strs("title_required_keywords"),
        "title_required_any": strs("title_required_any"),
        "title_forbidden_words": strs("title_forbidden_words"),
        "title_max_chars": int(c.get("title_max_chars") or 0),
        "body_required_keywords": kws,
        "body_forbidden_words": strs("body_forbidden_words"),
        "required_hashtags": [h.lstrip("#") for h in strs("required_hashtags")],
        "body_min_chars": int(c.get("body_min_chars") or 0),
        "body_max_chars": int(c.get("body_max_chars") or 0),
        "required_phrases": strs("required_phrases"),
        "required_links": strs("required_links"),
        "subheading_order": strs("subheading_order"),
        "other_rules": strs("other_rules")[:12],
    }


def is_empty(c: dict) -> bool:
    return not c or not any(v for v in c.values())


def to_prompt(c: dict) -> str:
    """체크리스트 → 프롬프트 블록(시스템·사용자 프롬프트 양쪽에 넣는다)."""
    if is_empty(c):
        return ""
    L = ["[협찬 가이드 체크리스트 — 하드 제약. 글을 내기 전에 항목마다 지켰는지 확인하라. "
         "SEO·제목 규칙(T1~T6)·글 길이 권장값과 충돌하면 이 체크리스트가 이긴다]"]
    if c.get("title_required_keywords"):
        L.append("- 제목 필수 키워드(3개 후보 제목 모두에 포함): " + ", ".join(c["title_required_keywords"]))
    if c.get("title_required_any"):
        L.append("- 제목에 아래 중 1개 이상 포함(3개 후보 제목 모두, 앞부분 권장): " + ", ".join(c["title_required_any"]))
    if c.get("title_forbidden_words"):
        L.append("- 제목 금지어(제목에 절대 쓰지 마라): " + ", ".join(c["title_forbidden_words"]))
    if c.get("title_max_chars"):
        L.append(f"- 제목 글자수 상한: {c['title_max_chars']}자(이 값이 T2 길이 규칙보다 우선)")
    for k in c.get("body_required_keywords", []):
        L.append(f"- 본문에 '{k['word']}' {k['min_count']}회 이상")
    if c.get("body_forbidden_words"):
        L.append("- 본문 금지어: " + ", ".join(c["body_forbidden_words"]))
    if c.get("required_hashtags"):
        L.append("- 필수 해시태그(hashtags 에 모두, #없이): " + ", ".join(c["required_hashtags"]))
    if c.get("body_min_chars") or c.get("body_max_chars"):
        L.append(f"- 본문 글자수: {c.get('body_min_chars') or '제한없음'} ~ {c.get('body_max_chars') or '제한없음'}자")
    for p in c.get("required_phrases", []):
        L.append(f"- 본문에 그대로 넣을 문구: {p}")
    for u in c.get("required_links", []):
        L.append(f"- 본문에 넣을 링크: {u}")
    if c.get("subheading_order"):
        L.append("- 소제목 구성·순서(문구는 다듬어도 되지만 순서·내용은 이대로): " + " → ".join(c["subheading_order"]))
    for r in c.get("other_rules", []):
        L.append(f"- {r}")
    L.append("- 이 가이드로 쓰는 글이라 가이드에 없는 임의 구조(소제목·섹션)로 바꾸지 마라. 확인 못 하는 항목은 confirm_needed 로.")
    return "\n".join(L)


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def verify(post: dict, c: dict) -> list[str]:
    """코드로 셀 수 있는 위반만 모아 돌려준다(빈 리스트 = 통과)."""
    if is_empty(c):
        return []
    v: list[str] = []
    body = post.get("body", "") or ""
    titles = [t for t in (post.get("title_candidates") or []) if str(t).strip()]
    for t in titles:
        tn = _nospace(t).lower()
        miss = [w for w in c.get("title_required_keywords", []) if _nospace(w).lower() not in tn]
        if miss:
            v.append(f"제목 '{t}'에 필수 키워드 누락: {', '.join(miss)}")
        anyk = c.get("title_required_any", [])
        if anyk and not any(_nospace(w).lower() in tn for w in anyk):
            v.append(f"제목 '{t}'에 필수 키워드(택1) 없음: {', '.join(anyk[:6])}")
        bad = [w for w in c.get("title_forbidden_words", []) if _nospace(w).lower() in tn]
        if bad:
            v.append(f"제목 '{t}'에 금지어 포함: {', '.join(bad)}")
        lim = c.get("title_max_chars") or 0
        if lim and len(_nospace(t)) > lim:
            v.append(f"제목 '{t}' {len(_nospace(t))}자 > 상한 {lim}자")
    bn = _nospace(body).lower()
    for k in c.get("body_required_keywords", []):
        n = bn.count(_nospace(k["word"]).lower())
        if n < k["min_count"]:
            v.append(f"본문 '{k['word']}' {n}회 < 최소 {k['min_count']}회")
    for w in c.get("body_forbidden_words", []):
        if _nospace(w).lower() in bn:
            v.append(f"본문에 금지어 '{w}' 포함")
    tags = {_nospace(h).lstrip("#").lower() for h in (post.get("hashtags") or [])}
    miss_tags = [h for h in c.get("required_hashtags", []) if _nospace(h).lower() not in tags]
    if miss_tags:
        v.append("필수 해시태그 누락: " + ", ".join(miss_tags))
    plain = len(re.sub(r"\[[^\]]*\]|\s", "", body))
    if c.get("body_min_chars") and plain < c["body_min_chars"]:
        v.append(f"본문 {plain}자 < 최소 {c['body_min_chars']}자")
    if c.get("body_max_chars") and plain > c["body_max_chars"]:
        v.append(f"본문 {plain}자 > 최대 {c['body_max_chars']}자")
    for u in c.get("required_links", []):
        core = re.sub(r"^https?://", "", u).rstrip("/")
        if core and core not in body:
            v.append(f"필수 링크 누락: {u}")
    for p in c.get("required_phrases", []):
        head = re.split(r"[（(]", p)[0].strip()
        if head and _nospace(head) not in _nospace(body):
            v.append(f"필수 문구 누락: {head[:50]}")
    return v


_FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "title_candidates": {"type": "array", "items": {"type": "string"}},
        "body": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title_candidates", "body", "hashtags"],
}


def repair(post: dict, c: dict, violations: list[str], system) -> dict:
    """위반 항목만 고쳐 쓴다(나머지 본문·톤은 그대로). 실패하면 원본 post 를 그대로 돌려준다."""
    try:
        out = call_json(
            model=config.NAEO_AUDIT_MODEL,
            system=system,
            content=[{"type": "text", "text": (
                to_prompt(c)
                + "\n\n[위반 목록 — 이것만 고쳐라]\n" + "\n".join(f"- {x}" for x in violations)
                + "\n\n[현재 제목 후보]\n" + "\n".join(post.get("title_candidates") or [])
                + "\n\n[현재 해시태그]\n" + ", ".join(post.get("hashtags") or [])
                + "\n\n[현재 본문]\n" + (post.get("body") or "")
                + "\n\n위반 항목만 최소한으로 수정해 title_candidates·body·hashtags 전체를 다시 출력하라. "
                  "위반이 아닌 부분(문체·사실·[사진N] 마커·[인용]·[표] 블록)은 한 글자도 바꾸지 마라. "
                  "제목 후보는 3개를 유지하되 모두 가이드 제약을 만족해야 한다."
            )}],
            schema=_FIX_SCHEMA,
            max_tokens=16000,
        )
    except Exception as e:
        print(f"  (가이드 위반 수정 실패: {str(e)[:80]})")
        return post
    fixed = {**post}
    if out.get("title_candidates"):
        fixed["title_candidates"] = out["title_candidates"]
    if out.get("hashtags"):
        fixed["hashtags"] = out["hashtags"]
    nb = out.get("body") or ""
    ob = post.get("body") or ""
    # 요약해버린 보정본은 버린다(분량 90% 미만)
    if nb and len(_nospace(nb)) >= len(_nospace(ob)) * 0.9:
        fixed["body"] = nb
    return fixed
