"""정보성/화제성 글(직접 경험 사진이 없는 글) 완성본을 보고 삽화 계획을 짠다.

유저 지정 스타일 가이드(2026-08-31): '크림 화이트 + 세이지 그린 + 피치 포인트'의 육아
매거진 인포그래픽 톤으로 10장을 짠다. 8장은 정보 카드(표지/타임라인/비교표/카드형/
체크리스트, modules/card_images.py 가 PIL 로만 그림 — 힉스필드 텍스트 렌더링 문제와
무관), 2장은 생활감 있는 실사(힉스필드). 10장이 다 같은 틀로 보이지 않게 레이아웃에
변화를 준다(기본 배분: 표지1·타임라인1·비교표2·카드형3·실사2·체크리스트1).
"""

from __future__ import annotations

import config
from modules.llm import call_json

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "role": {"type": "string", "enum": ["cover", "timeline", "comparison", "card", "checklist", "lifestyle"]},
        "subheading": {"type": "string"},  # 이 카드가 붙을 본문 소제목(본문 글자와 동일). 없으면 "".
        "badge": {"type": "string"},
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "lines": {"type": "array", "items": {"type": "string"}},
        "caption": {"type": "string"},
        "milestones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"}, "desc": {"type": "string"}, "highlight": {"type": "boolean"},
                },
                "required": ["label", "desc", "highlight"],
                "additionalProperties": False,
            },
        },
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"}, "before": {"type": "string"}, "after": {"type": "string"},
                },
                "required": ["label", "before", "after"],
                "additionalProperties": False,
            },
        },
        "checklist_items": {"type": "array", "items": {"type": "string"}},
        "bg_prompt": {"type": "string"},  # lifestyle 전용, 영어
    },
    "required": ["key", "role", "subheading", "badge", "title", "subtitle", "lines",
                 "caption", "milestones", "rows", "checklist_items", "bg_prompt"],
    "additionalProperties": False,
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_draft_policy": {"type": "boolean"},  # 아직 확정 안 된 제도/정책/발표안이면 true
        "footer_note": {"type": "string"},        # is_draft_policy 면 각주 문구, 아니면 ""
        "items": {"type": "array", "items": _ITEM_SCHEMA},
    },
    "required": ["is_draft_policy", "footer_note", "items"],
    "additionalProperties": False,
}

_SYSTEM = """\
너는 네이버 육아 정보 블로그용 이미지 기획자다. 다 쓰인 본문을 보고 카드뉴스 10장을 기획한다.

[전체 톤]
크림 화이트 배경에 옅은 세이지 그린 + 피치 포인트를 쓰는 육아 매거진 인포그래픽이다
(배경·색상은 이미 코드에 고정돼 있으니 너는 내용만 기획하면 된다). 공공기관 카드뉴스처럼
딱딱하지 않고, 부드럽고 신뢰감 있게. 광고 느낌 금지.

[10장 구성 — role 로 지정. 레이아웃이 다 같아 보이지 않게 반드시 섞어라]
기본 배분(주제에 맞게 ±1장 조정 가능, 그래도 다양성은 유지):
- cover 1장: 표지. 제목 + 부제.
- timeline 1장: 시행 기준/적용 시점처럼 순서·시점이 있는 내용. milestones 2~4개
  (예: "2027년 6월 30일까지" → "기존 체계 유지", "2027년 7월 1일부터" → "새 제도 적용",
  뒤쪽 milestone 에 highlight:true).
- comparison 2장: 기존 vs 변경안처럼 대조되는 내용. rows 는 label(항목명)+before(기존 값)+
  after(변경 값) 3~4개.
- card 3장: 핵심 금액/혜택/조건 요약. lines 1~3줄(짧고 굵게, 숫자 중심).
- checklist 1장: 신청 방법, 확인할 점, 주의사항처럼 나열형 내용. checklist_items 3~5개.
- lifestyle 2장: 힉스필드로 만들 생활감 사진(사람 등장 가능). caption 은 짧은 한 줄이거나
  생략 가능.

각 항목의 subheading 은 본문에 실제로 있는 소제목과 토씨 하나까지 동일해야 한다(그 아래
삽입하려고 찾는 데 쓰인다). cover/checklist/lifestyle 처럼 특정 소제목에 안 붙어도 되면
가장 관련 있는 소제목을 적거나, 정말 없으면 빈 문자열로 둬라.

[사실 정보 규칙]
- 모든 수치·날짜·기준은 본문에 실제로 있는 것만 써라. 본문에 없는 숫자를 지어내지 마라.
- 본문을 보고 이 글이 다루는 제도/정책이 "확정"이 아니라 "개편안/발표안/시안"이면
  is_draft_policy 를 true 로 하고, footer_note 에 아래 중 자연스러운 쪽으로 짧게 써라
  ("정부 개편안 기준" 또는 "법 개정 및 예산 확정 절차에 따라 달라질 수 있음" 계열).
  확정된 내용이면 is_draft_policy 는 false, footer_note 는 "".

[lifestyle 2장의 bg_prompt 작성 규칙 — 영어로]
- 따뜻한 한국 가정집 분위기: 자연광, 크림·우드·세이지 톤, 편안하고 정돈된 공간.
  한국인으로 보이는 부모·아이가 등장해도 되지만, 특정 인물처럼 식별되지 않게 뒷모습·
  손 클로즈업·옆모습·아웃포커스처럼 얼굴이 뚜렷하게 특정되지 않는 구도로 써라.
- 액자·포스터·간판·화면·달력·시계처럼 원래 글자·숫자가 있는 사물은 넣지 마라(이미지
  생성 모델이 그 자리에 깨진 가짜 글자를 그려 넣는다).
- 화폐(지폐·동전)는 절대 넣지 마라.
- "no text" 같은 문구는 안 넣어도 된다(시스템이 자동으로 처리한다).

나머지 role(cover/timeline/comparison/card/checklist)의 bg_prompt 는 빈 문자열로 둬라
(전부 PIL 로만 그린다).
"""


def plan(title: str, body: str, structure_label: str) -> dict:
    user_text = f"[글 유형] {structure_label}\n[제목] {title}\n\n[본문]\n{body}"
    return call_json(
        model=config.VISION_MODEL,
        system=_SYSTEM,
        content=[{"type": "text", "text": user_text}],
        schema=_SCHEMA,
        max_tokens=5000,
    )
