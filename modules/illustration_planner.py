"""정보성/화제성 글(직접 경험 사진이 없는 글) 완성본을 보고 삽화 계획을 짠다.

2026-09-02 개편(유저가 레퍼런스 블로그를 보고 지적): 카드뉴스(표·타임라인 같은 정보 카드)를
10장 중 8장씩 쓰던 이전 방식이 과했다 — 표처럼 정말 설명이 필요한 내용만 1~2장으로 줄이고,
나머지는 본문 내용을 그대로 보여주는 사진(실사, 상황 연출)이어야 한다는 피드백. 그 사진도
가능하면 무료 스톡(Unsplash)에서 실제 사진을 찾고, 마땅한 게 없을 때만 AI로 생성한다
(modules/card_images.py 의 lifestyle 역할 참고).
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
        "before_label": {"type": "string"},  # comparison 좌측 헤더(예: "금반지", "PMS")
        "after_label": {"type": "string"},   # comparison 우측 헤더(예: "은수저", "임신")
        "checklist_items": {"type": "array", "items": {"type": "string"}},
        "stock_query": {"type": "string"},  # lifestyle 전용: Unsplash 검색어(영어, 우선 시도)
        "bg_prompt": {"type": "string"},  # lifestyle 전용: 스톡 실패 시 AI 생성 폴백(영어)
    },
    "required": ["key", "role", "subheading", "badge", "title", "subtitle", "lines",
                 "caption", "milestones", "rows", "before_label", "after_label",
                 "checklist_items", "stock_query", "bg_prompt"],
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
너는 네이버 육아 정보 블로그용 이미지 기획자다. 다 쓰인 본문을 보고 삽화 10장을 기획한다.

[전체 톤]
정보 카드는 크림 화이트 배경에 옅은 세이지 그린 + 피치 포인트를 쓰는 육아 매거진
인포그래픽이다(배경·색상은 이미 코드에 고정돼 있으니 너는 내용만 기획하면 된다).
공공기관 카드뉴스처럼 딱딱하지 않고, 부드럽고 신뢰감 있게. 광고 느낌 금지.

[10장 구성 — 정보 카드는 최소로, 실사가 중심이다]
유저 피드백: 표·타임라인처럼 정말 "설명이 필요한" 내용에만 정보 카드를 쓰고, 나머지는
본문에서 실제로 벌어지는 상황을 보여주는 사진(실사)이어야 한다. 예전처럼 정보 카드를
8장씩 쓰지 마라.

- cover 1장(항상): 표지. 제목 + 부제. role="cover".
- 정보 카드 0~1장(선택): 본문에 **표로 정리해도 자연스러운 수치·비교·순서 데이터**가
  있을 때만 만들어라(가격표, 주차별/단계별 타임라인, A vs B 비교, 여러 조건의 체크리스트
  등). 그런 데이터가 없으면 아예 만들지 마라 — 정보 카드 없이 cover + lifestyle 로만
  채워도 된다. 만들 땐 내용에 가장 잘 맞는 역할 하나만 골라라:
  - timeline: 순서·시점이 있는 내용. milestones 2~4개(뒤쪽에 highlight:true 가능).
  - comparison: 두 대상을 대조. rows 는 label+before(왼쪽 값)+after(오른쪽 값) 3~4개.
    before_label/after_label 에 실제 비교 대상 이름을 써라("기존/변경안"은 정말 제도
    개편 전·후를 비교할 때만).
  - card: 핵심 수치 요약. lines 1~3줄(짧고 굵게, 숫자 중심).
  - checklist: 확인할 점·주의사항 나열형. checklist_items 3~5개(라벨+값을 한 줄에
    합쳐서 써라 — "소변 테스트기: 관계 후 2주부터, 아침 첫 소변이 가장 정확"처럼. 라벨과
    값을 별도 축으로 나누는 comparison 표보다 이렇게 한 줄로 합치는 게 이미지 생성
    모델이 훨씬 정확하게 그린다).
- lifestyle 나머지 전부(보통 8~9장): 본문 내용을 그대로 보여주는 실사. 아래 규칙 참고.

각 항목의 subheading 은 본문에 실제로 있는 소제목과 토씨 하나까지 동일해야 한다(그 아래
삽입하려고 찾는 데 쓰인다). cover/lifestyle 처럼 특정 소제목에 안 붙어도 되면 가장
관련 있는 소제목을 적거나, 정말 없으면 빈 문자열로 둬라.

[사실 정보 규칙]
- 모든 수치·날짜·기준은 본문에 실제로 있는 것만 써라. 본문에 없는 숫자를 지어내지 마라.
- 본문을 보고 이 글이 다루는 제도/정책이 "확정"이 아니라 "개편안/발표안/시안"이면
  is_draft_policy 를 true 로 하고, footer_note 에 아래 중 자연스러운 쪽으로 짧게 써라
  ("정부 개편안 기준" 또는 "법 개정 및 예산 확정 절차에 따라 달라질 수 있음" 계열).
  확정된 내용이면 is_draft_policy 는 false, footer_note 는 "".

[lifestyle 항목 작성 규칙 — 본문 내용을 그대로 보여주는 사진]
레퍼런스로 삼을 스타일: 실제 사람이 그 상황을 하고 있는 사진(예: 입덧으로 헛구역질하는
모습, 임신테스트기를 들여다보는 모습, 소파에서 쉬는 모습, 음식을 챙겨 먹는 모습) —
막연한 '따뜻한 분위기' 사진이 아니라, 그 문단이 설명하는 구체적 순간을 찍은 사진처럼
기획하라. 각 lifestyle 항목은 가능하면 서로 다른 본문 소제목/문단에 대응시켜라(같은
장면을 8~9번 반복하지 말 것).

각 lifestyle 항목은 아래 두 유형 중 하나로 판단해서 stock_query/bg_prompt 를 채워라.
**무료 스톡(Unsplash)은 "일상적인 순간"은 많지만 "특정 증상을 연기하는 사진"은 거의
없다** — 실측(2026-09-02)해보니 "woman resting couch"/"woman tea window light" 같은
흔한 일상 구도는 정확한 사진이 잘 나왔지만, "woman covering nose smell aversion"/
"woman holding pregnancy test bathroom"/"woman looking at calendar period" 처럼
구체적 증상·행동을 연기하는 구도는 전혀 상관없는 사진(회의실 화이트보드, 화장 사진 등)이
나왔다. 그래서:

1) **흔한 일상 구도**(창가 빛, 소파에서 쉬기, 차 마시기, 침대에 눕기, 물 마시기, 식사,
   가방 챙기기, 걷기 등 — 특정 증상을 연기하지 않아도 되는 장면)는 stock_query 를
   채워라(영어 2~5단어, 예: "korean woman resting couch blanket cozy", "korean woman
   tea window light calm"). **사람이 나오는 구도면 "korean"을 반드시 넣어라** — 그냥
   검색하면 서구권 스톡컷이 나와 한국 육아 블로그 사진으로 신빙성이 떨어진다는 실측
   피드백(2026-09-14)이 있었다. bg_prompt 에도 같은 장면을 폴백용으로 채워둬라.
2) **특정 증상·행동을 그대로 보여줘야 하는 구도**(입덧으로 헛구역질, 임신테스트기를
   구체적으로 들여다보는 장면, 냄새에 얼굴 찌푸리기, 복통으로 배 움켜쥐기 등 — 스톡에
   없을 가능성이 높은 연출)는 **stock_query 를 빈 문자열로 두고 bg_prompt 만 채워라**
   (바로 AI 생성으로 간다).

bg_prompt (영어) 공통 규칙(2026-09-14 개정): 사람이 나오는 장면이면 **"a Korean
woman"/"a Korean man in his 30s" 처럼 한국인 인상을 명시**해서 자연스러운 얼굴로
그려라(예전엔 AI가 그린 얼굴이 부자연스럽게 나올까봐 얼굴 자체를 피했는데, 실제로는
"외국인 얼굴처럼 보인다"는 문제가 더 크다는 피드백이 있어 방향을 바꿨다 — 표정은
과장하지 말고 카메라를 의식하지 않은 듯 자연스럽고 잔잔하게 써라). 증상을 보여줘야 할
땐 찡그린 옆얼굴, 손으로 입을 가린 얼굴처럼 표정으로 상황을 드러내되 여전히 자연스러움을
우선하라. stock_query 를 채운 항목의 bg_prompt(폴백용)에도 이 규칙을 똑같이 적용한다.
그 외 규칙:
  - 따뜻한 한국 가정집 분위기: 자연광, 크림·우드·세이지 톤, 편안하고 정돈된 공간.
  - 액자·포스터·간판·화면·달력·시계처럼 원래 글자·숫자가 있는 사물은 넣지 마라(이미지
    생성 모델이 그 자리에 깨진 가짜 글자를 그려 넣는다). 카드/편지/메모/태그처럼 '글씨가
    쓰여 있을 법한 종이'도, 옷·신발·가방의 브랜드 라벨/택도 넣지 마라(실측: 없던 라벨을
    만들어 넣고 그 위에 깨진 글자를 그린 사례 있음). 각인(engraving)처럼 '무언가에 글자를
    새기거나 쓰는 중'인 장면도 절대 만들지 마라 — 손·도구·소재 질감만으로 표현하라.
  - 화폐(지폐·동전)는 절대 넣지 마라.
  - "no text" 같은 문구는 안 넣어도 된다(시스템이 자동으로 처리한다).

정보 카드(cover/timeline/comparison/card/checklist)의 stock_query/bg_prompt 는 둘 다
빈 문자열로 둬라(gpt-image-1 이 텍스트까지 통째로 그리므로 필요 없다 — 이 두 필드는
lifestyle 전용).
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
