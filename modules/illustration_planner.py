"""정보성/화제성 글(직접 경험 사진이 없는 글) 완성본을 보고 삽화 계획을 짠다.

카드뉴스(힉스필드 배경 + PIL 텍스트, modules/card_images.py) 6~7장 +
무료 스톡 사진(Unsplash, modules/image_finder.py) 3~4장, 총 10장 안팎을 기본으로 한다.
각 카드는 실제 소제목/핵심 문장에서 뽑아 쓰므로, 본문이 다 써진 뒤에 호출해야 한다.
"""

from __future__ import annotations

import config
from modules.llm import call_json

_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subheading": {"type": "string"},  # 이 카드가 붙을 본문 소제목(본문 글자와 동일해야 함)
                    "badge": {"type": "string"},        # 짧은 배지 문구(예: "정보", "STEP 1", "01")
                    "lines": {"type": "array", "items": {"type": "string"}},  # 카드에 얹을 한글 1~3줄
                    "bg_prompt": {"type": "string"},    # 영어 배경 이미지 프롬프트
                },
                "required": ["subheading", "badge", "lines", "bg_prompt"],
                "additionalProperties": False,
            },
        },
        "stock_queries": {"type": "array", "items": {"type": "string"}},  # 영어 스톡 검색어
    },
    "required": ["cards", "stock_queries"],
    "additionalProperties": False,
}

_SYSTEM = """\
너는 정보성/화제성 네이버 블로그 글에 들어갈 삽화를 기획하는 아트 디렉터다.
이 글은 필자가 직접 겪은 경험이 아니라 정보를 정리한 글이라, 실사 후기 사진이 없다.
대신 (1) 카드뉴스 이미지와 (2) 무료 스톡 사진으로 본문을 시각적으로 채운다.

[카드뉴스 6~7장]
- 본문에 실제로 있는 소제목 중 정보 밀도가 높은 것들을 골라 하나씩 카드로 만든다.
  subheading 은 본문에 쓰인 소제목과 토씨 하나까지 동일해야 한다(찾아서 그 아래 삽입할 것이므로).
- lines 는 그 소제목 아래 문장에서 실제로 있는 핵심 사실/수치를 1~3줄 짧게 뽑는다.
  본문에 없는 숫자·사실을 지어내지 마라.
- badge 는 "정보", "STEP n", "01" 처럼 아주 짧게.
- bg_prompt 는 영어로, 그 소제목 내용과 어울리는 장면을 묘사한다. 절대 액자·포스터·간판·
  제품 라벨·화면(모니터/계기판)처럼 원래 글자가 있을 법한 사물을 넣지 마라(이미지 생성
  모델이 그 자리에 깨진 가짜 글자를 그려 넣는다). 질감, 손, 식물, 빛, 사물의 실루엣처럼
  글자 없는 소재로 장면을 짜라. "no text/no people's faces" 같은 문구는 안 넣어도 된다
  (시스템이 자동으로 처리한다).

[스톡 사진 3~4장]
- 카드로 다루지 않은 나머지 분위기/맥락을 무료 스톡 사진으로 채운다.
- stock_queries 는 Unsplash 검색에 쓸 영어 키워드 2~3단어 조합으로, 카드와 겹치지 않게.
"""


def plan(title: str, body: str, structure_label: str) -> dict:
    user_text = f"[글 유형] {structure_label}\n[제목] {title}\n\n[본문]\n{body}"
    return call_json(
        model=config.VISION_MODEL,
        system=_SYSTEM,
        content=[{"type": "text", "text": user_text}],
        schema=_SCHEMA,
        max_tokens=4000,
    )
