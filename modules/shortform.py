"""발행한 블로그 글 1편 → 숏폼 대본(원소스 멀티유즈의 1단계).

블로그 글은 이미 씬 단위로 쪼개져 있다(소제목 + [사진N] 마커 + 회색 캡션).
그래서 대본을 새로 짜내는 게 아니라 '재배열'한다 — 품질이 안정적으로 나오는 이유.

출력 한 벌로 세 채널을 다 덮는다:
  - 영상 대본(훅 + 씬 + CTA)  → modules/video_maker.py 가 렌더
  - 유튜브 쇼츠 메타(제목/설명/태그)
  - 네이버 클립 메타(제목/태그)
  - 쓰레드 게시글(블로그로 유입시키는 짧은 글)

씬마다 motion_worth 를 판정한다. AI 영상 변환(image-to-video)은 초당 과금이라
전 컷을 움직이면 편당 비용이 몇 배로 뛴다. '움직임이 정보를 만드는' 컷만
high 로 잡아 거기에만 쓴다 → 체감 품질은 그대로, 비용은 1/3.
"""

from __future__ import annotations

import re

import config
from modules.llm import call_json

# 한 영상에서 AI 영상 변환을 쓸 최대 컷 수. 비용 상한선이자 연출 상한선이기도 하다
# (전 컷이 움직이면 오히려 산만해진다).
MAX_MOTION_SCENES = 2

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hook", "scenes", "cta", "youtube", "clip", "threads"],
    "properties": {
        "hook": {
            "type": "string",
            "description": "0~2초에 뜨는 훅 자막 한 줄. 20자 이내.",
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["image", "caption", "narration", "seconds",
                             "motion_worth", "motion_prompt"],
                "properties": {
                    "image": {
                        "type": "integer",
                        "description": "이 씬에 쓸 사진 번호([사진N] 의 N). 마땅한 게 없으면 0.",
                    },
                    "caption": {
                        "type": "string",
                        "description": "화면에 박히는 자막. 한 줄 12자 내외, 최대 2줄(\\n 으로 구분).",
                    },
                    "narration": {
                        "type": "string",
                        "description": "성우가 읽을 대사. 자막보다 자연스러운 구어체 한두 문장.",
                    },
                    "seconds": {"type": "number", "description": "이 씬 길이(4~8초)."},
                    "motion_worth": {
                        "type": "string",
                        "enum": ["high", "low"],
                        "description": "움직임이 설득력을 만드는 컷이면 high, 정보 전달용 정지컷이면 low.",
                    },
                    "motion_prompt": {
                        "type": "string",
                        "description": "high 일 때만 채운다. 사진을 어떻게 움직일지 영어로 한 문장"
                                       "(예: gentle water ripples around the speaker, subtle steam rising)."
                                       " low 면 빈 문자열.",
                    },
                },
            },
        },
        "cta": {"type": "string", "description": "마지막 화면 자막. 블로그로 유도. 20자 이내."},
        "youtube": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "description", "tags"],
            "properties": {
                "title": {"type": "string", "description": "쇼츠 제목. 40자 이내."},
                "description": {"type": "string", "description": "설명. 블로그 링크 자리는 {link} 로 둔다."},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "12개 이하."},
            },
        },
        "clip": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "tags"],
            "properties": {
                "title": {"type": "string", "description": "네이버 클립 제목. 30자 이내."},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "'#' 없이 단어만. 10개 이하."},
            },
        },
        "threads": {
            "type": "string",
            "description": "쓰레드 게시글. 3~5줄, 줄바꿈 포함. 블로그 링크 자리는 {link}.",
        },
    },
}

_SYSTEM = """너는 육아·살림 리뷰 블로그 '비비'의 숏폼 연출가다.
이미 발행된 블로그 글을 30~45초 세로 숏폼(9:16) 대본으로 옮긴다.

[대원칙]
- 글을 요약하지 마라. 글에서 '가장 보여줄 만한 장면'만 골라 재배열한다.
- 새로운 사실을 지어내지 마라. 숫자·가격·기간은 글에 있는 것만 쓴다.
- 존댓말. 비비 특유의 담백하고 솔직한 톤. 과장·낚시·감탄사 남발 금지.

[훅 — 첫 2초가 전부다]
- 결론이나 문제를 먼저 던진다. "15일 만에 곰팡이 슬었어요" 처럼 구체적인 숫자·사건.
- "안녕하세요", "오늘은 ~를 소개합니다" 같은 인사말로 시작하지 마라.

[씬]
- 4~7개. 씬당 4~8초. 전체 30~45초.
- 각 씬은 글의 [사진N] 마커 중 실제로 그 내용을 보여주는 사진을 골라 image 에 번호로 넣는다.
- 자막(caption)은 화면에 박히는 글자다. 한 줄 12자 내외, 최대 2줄. 문장을 그대로 넣지 말고 끊어라.
- 나레이션(narration)은 귀로 듣는 말이라 자막보다 자연스러운 구어체로 쓴다. 자막을 그대로 읽지 마라.

[motion_worth — 제품이 주인공인 컷에만 준다]
AI 영상 변환은 '제품을 생동감 있게 보여주려고' 쓴다. 한 영상에 최대 2개까지만 high.

- high: 제품 자체가 화면의 주인공이고, 움직임이 제품의 성능·질감을 보여주는 컷
  예) UV 램프가 켜지며 빛이 번지는 살균기, 물에 뜬 방수 스피커 주변의 물결,
      트레이에 맺힌 물방울이 또르르 흐르는 장면, 김이 피어오르는 음식,
      흐르는 세제·로션의 질감, 천천히 돌아가는 제품

- low: 아래는 무조건 low 다
  · 사람(특히 아기)이 화면의 주인공인 컷 — 사람은 AI로 만들지 않는다.
    실제로 찍은 모습이 진짜고, 없던 장면을 지어내면 내돈내산 후기가 아니게 된다.
  · 구성품 나열, 표, 패키지 정면, 설명서, 텍스트가 중요한 화면
  · 이미 움직이는 소재(움짤)가 있는 자리

애매하면 low 다. high 를 남발하면 비용만 오르고 영상은 산만해진다.
motion_prompt 는 영어 한 문장으로, 카메라가 아니라 '제품이 어떻게 움직이는지'를 쓴다.
원본 사진에 없던 사물·사람을 추가로 등장시키는 지시는 절대 쓰지 마라.

[채널별 메타]
- youtube.title: 검색되는 제목. 핵심 키워드를 앞에.
- clip.title: 네이버 클립은 더 짧고 대화체가 잘 먹는다.
- threads: 정보를 다 주지 말고 궁금하게 남겨 블로그로 넘긴다. 해시태그 1~2개까지만."""


def _photo_index(body: str) -> list[int]:
    """본문에 실제로 등장하는 [사진N] 번호(순서 유지, 중복 제거)."""
    seen, out = set(), []
    for m in re.finditer(r"\[사진\s*(\d+)\]", body):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _cap_motion(scenes: list[dict]) -> list[dict]:
    """high 판정이 MAX_MOTION_SCENES 를 넘으면 뒤쪽 것부터 low 로 낮춘다.

    모델이 규칙을 어겨도 비용이 새지 않게 하는 마지막 방어선. 앞 씬을 남기는 건
    숏폼에서 앞부분 이탈률이 가장 높아 초반 컷에 투자하는 게 이득이기 때문이다.
    """
    budget = MAX_MOTION_SCENES
    for sc in scenes:
        if sc.get("motion_worth") != "high":
            sc["motion_worth"] = "low"
            sc["motion_prompt"] = ""
            continue
        if budget > 0 and (sc.get("motion_prompt") or "").strip():
            budget -= 1
        else:
            sc["motion_worth"] = "low"
            sc["motion_prompt"] = ""
    return scenes


def make_script(
    *,
    title: str,
    body: str,
    captions: dict | None = None,
    subheadings: list[str] | None = None,
    blog_url: str = "",
) -> dict:
    """발행 글 한 편으로 숏폼 대본 + 채널별 메타를 만든다.

    captions 는 draft.json 의 {"3": "사진 캡션"} 형태(사진 번호 → 회색 캡션).
    반환 dict 에 scenes[].n(1부터)과 total_seconds 를 채워서 돌려준다.
    """
    captions = captions or {}
    photos = _photo_index(body)
    cap_lines = [f"  [사진{n}] {captions.get(str(n)) or captions.get(n) or '(캡션 없음)'}"
                 for n in photos]

    prompt = [
        "[블로그 글 제목]", title, "",
        "[본문]", body, "",
        "[쓸 수 있는 사진 번호와 캡션]",
        "\n".join(cap_lines) if cap_lines else "  (본문에 [사진N] 마커가 없음 — image 는 전부 null)",
        "",
        f"[소제목] {' / '.join(subheadings or []) or '(없음)'}",
        "",
        "위 글을 30~45초 세로 숏폼 대본으로 옮겨라. image 에는 위 목록에 있는 번호만 쓴다.",
    ]

    result = call_json(
        model=config.SHORTFORM_MODEL,
        system=_SYSTEM,
        content=[{"type": "text", "text": "\n".join(prompt)}],
        schema=_SCHEMA,
        max_tokens=8000,
    )

    scenes = _cap_motion(result.get("scenes") or [])
    for i, sc in enumerate(scenes, start=1):
        sc["n"] = i
        # 0(= 마땅한 사진 없음)과, 모델이 지어낸 없는 번호는 버린다(렌더에서 파일을 못 찾는다).
        if sc.get("image") not in photos:
            sc["image"] = None
        sc["seconds"] = max(3.0, min(9.0, float(sc.get("seconds") or 5)))
    result["scenes"] = scenes
    result["total_seconds"] = round(sum(sc["seconds"] for sc in scenes), 1)
    result["motion_scenes"] = [sc["n"] for sc in scenes if sc["motion_worth"] == "high"]

    if blog_url:
        for key in ("threads",):
            result[key] = (result.get(key) or "").replace("{link}", blog_url)
        yt = result.get("youtube") or {}
        yt["description"] = (yt.get("description") or "").replace("{link}", blog_url)
        result["youtube"] = yt
    return result
