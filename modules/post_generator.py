"""사진 분석 + 사용자 입력 + 스타일 가이드로 블로그 초안을 생성한다(Claude Opus).

출력: 제목 후보 3개, 메타디스크립션, 본문(섹션별, [사진N]/[영상N] 자리표시), 해시태그,
추천 핵심 키워드, 사진/영상 배치안, 확인 필요 정보.
핵심 키워드가 비어 있으면 상품 링크/메모/사진을 바탕으로 SEO 키워드를 직접 제안한다.
필수 삽입 링크는 본문에 반드시 자연스럽게 넣는다.
"""

from __future__ import annotations

import config
from modules.image_analyzer import analysis_summary_for_writer
from modules.llm import call_json
from modules.style_profiler import profile_to_prompt
from prompts.post_structures import get_structure
from prompts.style_rules import STYLE_RULES, sponsor_instruction

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
):
    structure = get_structure(structure_key)
    required_links = [l.strip() for l in (required_links or []) if l.strip()]
    keyword = (keyword or "").strip()
    product_link = (product_link or "").strip()

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

    system = (
        STYLE_RULES
        + "\n\n"
        + profile_to_prompt(style_guide)
        + "\n\n"
        + sponsor_instruction(sponsor_type)
        + "\n\n"
        + brand_rule
        + "\n\n[SEO]\n"
        + keyword_rule
        + "\n\n"
        + link_rule
        + "\n\n"
        + video_rule
        + "\n\n[출력 형식]\n"
        "- suggested_keywords: 이 글의 추천 핵심 키워드 3~5개.\n"
        "- title_candidates: 서로 다른 각도의 제목 3개. 네이버 SEO 최적화 — 핵심 키워드를 "
        "앞쪽에 배치하고, 검색 의도(지역·제품·후기 등)를 담아 자연스럽게. 28~35자 권장, "
        "낚시성·과장 금지.\n"
        "- hashtags: 글 주제와 관련되고 네이버 SEO 를 고려한 해시태그 정확히 10개. 기호(#) 없이 "
        "단어만, 핵심 키워드 + 연관 검색어 조합으로. 너무 일반적이거나 서로 중복되지 않게.\n"
        "- thumbnail_title: 썸네일에 넣을 짧은 제목을 1~2줄(배열)로. 각 줄은 10자 안팎으로 "
        "짧고 굵게. 예: ['서울형 키즈카페 신당점'] 또는 ['애착인형 언제부터?','돌 아기 시기와 종류'].\n"
        "- subheadings: 본문에 사용한 소제목들을 배열로 나열. ●·번호·불릿·기호 없이 글자만"
        "(본문의 소제목에도 기호를 붙이지 말 것).\n"
        "- meta_description: 검색 노출용 2~3문장 요약.\n"
        "- body: 소제목(간결한 문장형) + 본문. 사진 자리는 [사진N], 영상 자리는 [영상N] "
        "형식으로 표기. 사용할 사진/영상은 모두 한 번씩 본문에 배치. "
        "[목표 글자 수] 공백 포함 최소 1400자 이상 작성할 것. 짧은 초안은 허용되지 않는다.\n"
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
[목표 글 길이] 공백 포함 반드시 {length}자 이상. 짧게 끝내지 말고 각 섹션을 실제 경험담으로 구체적으로 풀어써라. 쓰고 나서 너무 짧다 싶으면 마지막 섹션을 더 살려라.
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

    return call_json(
        model=config.WRITER_MODEL,
        system=system,
        content=[{"type": "text", "text": user_text}],
        schema=_POST_SCHEMA,
        max_tokens=16000,
    )
