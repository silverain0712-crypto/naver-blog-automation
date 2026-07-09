"""사진 분석 + 사용자 입력 + 스타일 가이드로 블로그 초안을 생성한다(Claude Opus).

출력: 제목 후보 3개, 메타디스크립션, 본문(섹션별, [사진N]/[영상N] 자리표시), 해시태그,
추천 핵심 키워드, 사진/영상 배치안, 확인 필요 정보.
핵심 키워드가 비어 있으면 상품 링크/메모/사진을 바탕으로 SEO 키워드를 직접 제안한다.
필수 삽입 링크는 본문에 반드시 자연스럽게 넣는다.
"""

from __future__ import annotations

import re

import config
from modules.image_analyzer import analysis_summary_for_writer
from modules.llm import call_json
from modules.style_profiler import profile_to_prompt
from prompts.post_structures import get_structure
from prompts.style_rules import STYLE_RULES, sponsor_instruction
from prompts.edit_lessons import EDIT_LESSONS
from prompts.geo_structure import GEO_STRUCTURE

_POST_SCHEMA = {
    "type": "object",
    "properties": {
        "suggested_keywords": {"type": "array", "items": {"type": "string"}},
        "title_candidates": {"type": "array", "items": {"type": "string"}},
        "thumbnail_title": {"type": "array", "items": {"type": "string"}},  # 썸네일용 1~2줄(줄당 짧게)
        "subheadings": {"type": "array", "items": {"type": "string"}},  # 본문에 쓴 소제목들(본문과 글자까지 동일)
        "meta_description": {"type": "string"},
        "body": {"type": "string"},  # 소제목+본문. 사진=[사진N], 영상=[영상N] 표기
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "photo_placement": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "photo_number": {"type": "integer"},
                    "section": {"type": "string"},
                    "caption": {"type": "string"},
                },
                "required": ["photo_number", "section", "caption"],
                "additionalProperties": False,
            },
        },
        "video_placement": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "video_number": {"type": "integer"},
                    "section": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["video_number", "section", "note"],
                "additionalProperties": False,
            },
        },
        "confirm_needed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["item", "note"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "suggested_keywords",
        "title_candidates",
        "thumbnail_title",
        "subheadings",
        "meta_description",
        "body",
        "hashtags",
        "photo_placement",
        "video_placement",
        "confirm_needed",
    ],
    "additionalProperties": False,
}


_MEDIA_MARKER_RE = re.compile(r"\[(?:사진|영상)\s*\d+\]")

_EXPAND_SCHEMA = {
    "type": "object",
    "properties": {"body": {"type": "string"}},
    "required": ["body"],
    "additionalProperties": False,
}


def _visible_len(body: str) -> int:
    """네이버 '공백 포함' 글자수에 맞춘 본문 길이.

    - [사진N]/[영상N] 자리표시는 이미지가 되어 글자수에 안 들어가므로 제거.
    - 네이버 글자수는 공백은 세지만 줄바꿈은 세지 않는다. 비비 글은 줄바꿈이 아주 많아
      줄바꿈을 세면 실제보다 크게 부풀어 목표 미달인데도 통과된다 → 줄바꿈 제외하고 센다.
    """
    t = _MEDIA_MARKER_RE.sub("", body or "")
    return len(t.replace("\n", "").replace("\r", ""))


def _content_len(body: str) -> int:
    """공백 제외 글자수(사용자 목표 기준). [사진N]/[영상N] 자리표시·표 마크업·모든 공백을 뺀 순수 글자 수."""
    t = _MEDIA_MARKER_RE.sub("", body or "")
    t = t.replace("[표]", "").replace("[/표]", "").replace("|", "")
    return len("".join(t.split()))


def _expand_body(system: str, current_body: str, target: int, actual: int) -> str:
    """짧게 나온 본문을 목표 글자수 이상으로 늘린다(주제·사실·사진배치 유지)."""
    instruction = (
        f"아래는 네가 방금 쓴 블로그 초안 본문이다. 현재 공백 제외 약 {actual}자로 "
        f"목표({target}자)에 크게 못 미친다. 같은 주제·사실·톤·구성을 유지하되, "
        "각 섹션의 경험·과정·디테일·감상을 더 구체적으로 풀어써서 "
        f"공백 제외 최소 {target}자 이상으로 늘려라.\n"
        "- [사진N]·[영상N] 자리표시는 개수와 순서를 그대로 유지하라(지우거나 새로 만들지 마라).\n"
        "- 기존 소제목은 그대로 두고 새 소제목은 만들지 마라.\n"
        "- 없는 사실을 지어내지 말고, 같은 말 반복·의미 없는 늘리기도 금지.\n"
        "- 불릿/기호 없이 짧은 문장을 줄바꿈으로 이어가는 비비 문체를 유지하라.\n"
        "- body 만 다시 써서 반환하라(늘어난 전체 본문).\n\n"
        f"[현재 초안 본문]\n{current_body}"
    )
    # 확장(늘리기)은 창작보다 부풀리기라 Opus까지 필요 없음 → Sonnet으로 속도 확보.
    result = call_json(
        model=config.VISION_MODEL,
        system=system,
        content=[{"type": "text", "text": instruction}],
        schema=_EXPAND_SCHEMA,
        max_tokens=16000,
    )
    return result.get("body") or current_body


def _format_optional(fields: dict) -> str:
    labels = {
        "title_hint": "제목 후보",
        "must_include": "꼭 넣고 싶은 문장",
        "place_name": "방문 장소명",
        "product_name": "제품명",
        "brand": "브랜드명",
        "price": "가격",
        "location": "위치",
        "parking": "주차 정보",
        "reservation": "예약 정보",
        "pros": "좋았던 점",
        "cons": "아쉬웠던 점",
        "audience": "추천 대상",
    }
    lines = []
    for key, label in labels.items():
        val = (fields.get(key) or "").strip()
        if val:
            lines.append(f"- {label}: {val}")
    return "\n".join(lines) if lines else "(추가 입력 없음)"


def generate_post(
    *,
    structure_key: str,
    keyword: str = "",
    product_link: str = "",
    required_links: list[str] | None = None,
    sponsor_type: str,
    memo: str,
    length: int,
    photo_style: str,
    optional_fields: dict,
    style_guide: dict,
    image_analysis: dict,
    video_count: int = 0,
    video_desc: str = "",
    guideline: str = "",
    research_notes: str = "",
):
    structure = get_structure(structure_key)
    required_links = [l.strip() for l in (required_links or []) if l.strip()]
    keyword = (keyword or "").strip()
    product_link = (product_link or "").strip()
    guideline = (guideline or "").strip()
    research_notes = (research_notes or "").strip()

    # 키워드 유무에 따른 SEO 지침
    if keyword:
        keyword_rule = (
            f"핵심 키워드는 '{keyword}'다. 제목과 본문에 자연스럽게 반복하라. "
            "관련 보조 키워드도 suggested_keywords 에 3~5개 함께 제시하라."
        )
    else:
        keyword_rule = (
            "핵심 키워드가 주어지지 않았다. 상품 링크/제품명/메모/사진을 보고 네이버 검색 "
            "노출에 유리한 핵심 키워드를 네가 직접 정하라. 정한 키워드를 제목과 본문에 "
            "자연스럽게 반복하고, suggested_keywords 에 3~5개(가장 중요한 것을 맨 앞에) 제시하라."
        )

    # 링크 삽입 지침
    link_lines = []
    if product_link:
        link_lines.append(f"- 상품 링크(구매/소개 맥락에 넣기): {product_link}")
    for l in required_links:
        link_lines.append(f"- 필수 삽입 링크: {l}")
    if link_lines:
        link_rule = (
            "[본문에 반드시 넣을 링크]\n"
            + "\n".join(link_lines)
            + "\n각 링크는 흐름상 가장 자연스러운 위치에, 짧은 안내 문장과 함께 한 줄에 "
            "URL 그대로 넣어라(네이버에 붙여넣으면 링크로 인식됨). 억지로 광고처럼 쓰지 마라."
        )
    else:
        link_rule = "[링크] 본문에 넣을 링크는 없다."

    # 영상 지침
    if video_count > 0:
        vdesc = f" 영상 설명: {video_desc.strip()}" if video_desc.strip() else ""
        video_rule = (
            f"[영상] 첨부된 영상이 {video_count}개 있다.{vdesc} "
            "본문 흐름상 적절한 위치(보통 도입부 또는 핵심 장면)에 [영상1] 형식으로 배치하고, "
            "video_placement 에 각 영상의 섹션과 간단한 설명을 넣어라. "
            "영상 내용을 직접 보지 못했으니 단정하지 말고 자연스럽게 '영상으로 담아봤어요' 정도로."
        )
    else:
        video_rule = "[영상] 첨부된 영상은 없다. video_placement 는 빈 배열로 두어라."

    brand_rule = (
        "[브랜드·제품명 — 매우 중요]\n"
        "제품/물건을 소개하는 글이면, 두루뭉술한 일반명사(예: '목욕 장난감 보관함')로 쓰지 말고 "
        "반드시 '브랜드+제품명'(예: '코지 목욕 장난감 보관함')을 끌어와 제목·큰 제목(본문 첫 줄)"
        "·썸네일 제목·본문에 자연스럽게 반복하라. "
        "브랜드/제품명은 (1) 아래 추가 입력의 제품명/브랜드, (2) 상품 링크에 드러난 브랜드, "
        "(3) 사진 속 패키지·로고·라벨에 보이는 브랜드 표기에서 가져온다. "
        "확실치 않으면 사진/메모에서 보이는 표기를 그대로 쓰고, 전혀 알 수 없을 때만 일반명사를 쓴다."
    )

    # 협찬 가이드라인/제품 설명서(파일 추출 + 직접 입력) — 있으면 최우선 참고 규칙으로 주입
    if guideline:
        guideline_rule = (
            "\n\n[협찬 가이드라인 / 제품 설명서 — 최우선 참고. 이 내용이 아래 기본값·SEO 규칙과 충돌하면 이 내용을 따른다]\n"
            "아래는 광고주 가이드라인이거나 제품 설명서/스펙 자료다(둘이 섞여 있을 수도 있다). 성격에 맞게 지켜라:\n"
            "- (제품 설명서·스펙이면) 제품명·브랜드·핵심 사양/기능/성분/사용법·주의사항을 정확히 반영하되, "
            "스펙을 그대로 나열하지 말고 비비의 실사용 경험 톤으로 자연스럽게 녹여라. 자료에 없는 기능·수치를 지어내지 마라.\n"
            "- (가이드라인이면) '필수 키워드'는 제목과 본문에 그대로 넣고, 지정 횟수(명시 없으면 각 3회 이상) 반복하라.\n"
            "- 가이드에 '필수 해시태그' 목록이 있으면 그 해시태그들을 hashtags 에 그대로(#기호 없이 단어만) 넣어라. "
            "이 경우 10개 제한을 무시하고 가이드의 필수 목록을 우선한다.\n"
            "- 제목 글자수(예: 띄어쓰기 제외 30자 이내)·본문 글자수 등 수치 제한이 있으면 그 제한을 반드시 지켜라.\n"
            "- '공정위 문구/쇼핑커넥트 문구를 최상단에' 요구하면 본문 맨 위 첫 줄(별도 문단)에 그 문구를 넣어라.\n"
            "- 금지사항(타사 제품 노출 금지, 타사 비교 금지 등)을 절대 어기지 마라.\n"
            "- 가이드가 지정한 '필수 삽입 사진/영상'이 사진 분석 결과에 안 보이면 confirm_needed 로 사용자에게 알려라.\n"
            "- 제품 특장점·소구 포인트는 광고처럼 복붙하지 말고 비비의 실사용 경험 톤으로 자연스럽게 녹여라.\n"
            "- 가이드가 요구하는 삽입 링크(쇼핑커넥트 등)가 있으면 본문에 자연스럽게 배치하라(URL 은 사용자가 확인 필요 시 confirm_needed).\n"
            "\n[가이드라인 원문]\n"
            + guideline
        )
    else:
        guideline_rule = ""

    # 웹 리서치로 확인한 사실 — 있으면 본문에 정확히 녹이도록 주입(정보 밀도↑, GPT 대비 약점 보완)
    if research_notes:
        research_rule = (
            "\n\n[웹 리서치로 확인한 사실 — 본문에 정확히 활용하라]\n"
            "아래는 이 글 주제로 웹에서 확인한 사실 정보다. 독자에게 실질적 도움이 되도록 "
            "본문의 알맞은 섹션에 자연스럽게 녹여라(절차·준비물·수치·공식 창구 등). "
            "단 (1) 여기 없는 사실을 지어내지 말고, (2) 비비의 실경험 톤으로 풀되 정보 자체는 정확히, "
            "(3) 개인 경험(메모)과 충돌하면 메모를 우선하고 일반 정보는 '보통은/일반적으로' 로 구분해 써라.\n"
            + research_notes
        )
    else:
        research_rule = ""

    system = (
        STYLE_RULES
        + "\n\n"
        + EDIT_LESSONS
        + "\n\n"
        + GEO_STRUCTURE
        + "\n\n"
        + profile_to_prompt(style_guide)
        + "\n\n"
        + sponsor_instruction(sponsor_type)
        + "\n\n"
        + brand_rule
        + guideline_rule
        + research_rule
        + "\n\n[SEO — 네이버 상위 노출 최적화. SEO 전문가+블로그 컨설턴트로서 반드시 지켜라]\n"
        + keyword_rule
        + "\n- 제목: 메인 키워드를 맨 앞에 두고, 검색 의도 + 호기심 + 명확한 혜택을 담아라(낚시·과장 금지).\n"
        "- 첫 문단(도입부)에 핵심 키워드를 3회 이상 자연스럽게 넣어라(어색한 반복은 금지).\n"
        "- 소제목(H2/H3 격)에 키워드를 자연스럽게 포함하고, 리스트·정리 문단·강조로 가독성을 높여라.\n"
        "- 연관 검색어(관련 키워드)를 본문 전체에 20개 이상 자연스럽게 녹여라(맥락 없는 나열·키워드 스터핑 금지).\n"
        "- 같은 키워드를 20회 이상 반복하지 마라(과최적화는 오히려 감점). 동의어·연관어로 변주하라.\n\n"
        + link_rule
        + "\n\n"
        + video_rule
        + "\n\n[출력 형식]\n"
        "- suggested_keywords: 이 글의 추천 핵심 키워드 3~5개.\n"
        "- title_candidates: 서로 다른 각도의 제목 3개. 네이버 SEO 최적화 — 핵심 키워드를 "
        "앞쪽에 배치하고, 검색 의도(지역·제품·후기 등)를 담아 자연스럽게. 28~35자 권장, "
        "낚시성·과장 금지.\n"
        "- hashtags: 글 주제와 관련되고 네이버 SEO 를 고려한 해시태그. 기호(#) 없이 단어만, 핵심 키워드 "
        "+ 연관 검색어 조합으로. 기본은 10개, 단 협찬 가이드라인에 '필수 해시태그' 목록이 있으면 그 목록을 "
        "그대로(개수 제한 없이) 우선해 넣어라. 서로 중복되거나 너무 일반적이지 않게.\n"
        "- thumbnail_title: 썸네일에 넣을 짧은 제목을 1~2줄(배열)로. 각 줄은 10자 안팎으로 "
        "짧고 굵게. 예: ['서울형 키즈카페 신당점'] 또는 ['애착인형 언제부터?','돌 아기 시기와 종류'].\n"
        "- subheadings: 본문에 사용한 소제목들을 배열로 나열. ●·불릿·기호 없이 글자만"
        "(본문의 소제목과 글자까지 똑같이). 단 '루틴·순서·단계' 섹션의 소제목은 "
        "본문에 쓴 그대로(예: '1. 돌돌이로 침대 청소') 번호를 포함해 넣어라.\n"
        "- meta_description: 검색 노출·클릭 유도용 요약, **150자 이내**. 메인 키워드를 앞쪽에 포함하고 "
        "클릭하고 싶게 혜택/궁금증을 담아라. 이 문장이 제목 아래 회색 요약(본문 첫 줄)로 들어간다.\n"
        "- body: 소제목(간결한 문장형) + 본문. 사진 자리는 [사진N], 영상 자리는 [영상N] "
        "형식으로 표기. 사용할 사진/영상은 모두 한 번씩 본문에 배치.\n"
        "- 표: 비교·준비물·요금/기준·절차 요약처럼 표로 보여주면 훨씬 명확한 정보가 있으면 "
        "본문 해당 위치에 표를 넣어라. 형식은 아래처럼 [표]와 [/표] 사이에 한 줄이 한 행, 칸은 '|'로 구분하고 "
        "**정확히 3칸(3열)**으로 맞춰라(첫 행은 머리글, 데이터는 2행 이상). 억지로 만들지 말고 글당 0~2개만, "
        "표 안에는 [사진N]을 넣지 마라. 예:\n"
        "[표]\n구분 | 내용 | 비고\n수하물표 | 짐 추적에 필요 | 사진 보관\n신고 시점 | 입국장 나가기 전 | 필수\n[/표]\n"
        "- photo_placement / video_placement: 각 사진·영상의 섹션과 캡션/설명.\n"
        "- confirm_needed: 사진/메모로 확정할 수 없어 사용자 검수가 필요한 항목"
        "(가격, 위치, 주차, 협찬 문구, 링크 등). 없으면 빈 배열."
    )

    section_list = "\n".join(f"{i+1}. {s}" for i, s in enumerate(structure["sections"]))
    photo_summary = analysis_summary_for_writer(image_analysis)

    user_text = f"""\
[글 유형] {structure['label']}
[권장 구조 — 이 순서를 따르되 자연스럽게]
{section_list}

[핵심 키워드] {keyword or '(미입력 — 직접 제안)'}
[목표 글 길이 — 반드시 지킬 것] 본문(body)은 **공백 제외 한글 최소 {length}자 이상**(권장 1500~2000자). 절대 미달하지 마라. 분량이 모자라면 (1) 소제목을 더 만들고 (2) 각 섹션의 경험·과정·디테일·감상, 그리고 웹 리서치로 확인한 사실(절차·준비물·수치)을 더 구체적으로 풀어써서 채운다. 단, 같은 말 반복이나 의미 없는 늘리기는 금지하고, 없는 사실을 지어내지도 마라. 체크리스트나 정보 나열도 불릿 없이 짧은 문장을 줄바꿈해서 쓴다.
[사진 노출 방식] {photo_style}
[첨부 영상 수] {video_count}

[사용자 메모 — 최우선 반영]
{memo.strip() or '(메모 없음)'}

[추가 입력]
{_format_optional(optional_fields)}

[사용할 사진과 분석 결과]
{photo_summary}

위 정보를 바탕으로, 비비가 직접 쓴 것 같은 네이버 블로그 초안을 작성하라.
사진/영상은 본문 흐름에 맞게 [사진N]/[영상N]으로 배치하라.
확인되지 않은 정보는 지어내지 말고 confirm_needed 로 빼라."""

    post = call_json(
        model=config.WRITER_MODEL,
        system=system,
        content=[{"type": "text", "text": user_text}],
        schema=_POST_SCHEMA,
        max_tokens=16000,
    )

    # 목표 길이(공백 제외) 미달이면 1회만 본문을 늘려 채운다(폰 즉시성 우선).
    # 과거 최대 3회 → Opus 대용량 호출이 최대 3연타로 붙어 수 분 지연되던 것을 축소.
    actual = _content_len(post.get("body", ""))
    if actual < length:
        expanded = _expand_body(system, post.get("body", ""), length, actual)
        if _content_len(expanded) > actual:
            post["body"] = expanded

    return post
