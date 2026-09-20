"""정보성/화제성 글용 카드뉴스 이미지 생성.

2026-08-31 개편(유저 지정 스타일 가이드 반영): 크림 화이트 + 세이지 그린 + 피치 포인트의
'육아 매거진 인포그래픽' 톤. 10장 중 8장은 정보 카드(표지/타임라인/비교표/카드형/체크리스트),
2장은 생활감 있는 실사(힉스필드). 레이아웃에 변화를 줘서 10장이 다 같은 틀로 보이지 않게 한다.

2026-08-31 재개편: 정보 카드 8장을 PIL 로 직접 그리던 걸 gpt-image-1 호출로 바꿨다.
실측 결과 힉스필드는 한글을 거의 못 그리지만(그래서 8장을 PIL 로 우회했었다),
gpt-image-1 은 프롬프트에 따옴표로 준 한글 문장을 그대로, 깨끗하게 그려내고 아이콘·
일러스트 톤도 참고 이미지(유저가 GPT로 만든 아동수당 카드뉴스) 수준으로 나온다 — 그래서
텍스트가 들어가는 8장은 PIL 레이아웃 대신 gpt-image-1 한 번 호출로 완성된 카드를 통째로
받는다. 실사 2장은 여전히 힉스필드(사진 특화)를 쓴다.

2026-09-02 재개편(유저가 레퍼런스 블로그 링크로 지적): 카드뉴스(표·타임라인 등 정보 카드)는
표처럼 설명이 꼭 필요한 내용 1~2장이면 충분하고, 나머지는 상황을 그대로 보여주는 사진이어야
한다는 피드백. `lifestyle` 역할을 "힉스필드로 무조건 생성"에서 "가능하면 무료 스톡(Unsplash)
사진을 먼저 찾고, 없을 때만 힉스필드로 생성"으로 바꿨다 — 레퍼런스 블로그가 쓴 사진들이
실제 사람 얼굴이 보이는 상황 연출 스톡컷이었기 때문(직접 생성하는 실사는 기존 정책대로
얼굴 특정을 피한다). 문구 오버레이도 이 개편에서 완전히 뺐다(유저가 사진엔 글자 없이
사진만 있으면 된다고 명시).

2026-09-14 재개편(유저 피드백: 스톡 사진 인물이 외국인이라 신빙성이 없다): 스톡 검색을
image_finder.search_unsplash_korean() 으로 바꿔 후보를 비전으로 걸러 서구권 인물 사진은
버리고 한국인으로 보일 법한 사진만 채택한다. 그래도 못 찾으면(정말 없으면) AI 생성으로
넘어가는데, 이땐 기존의 "얼굴 특정 금지" 정책을 버리고 한국인 인상의 얼굴을 직접 그리도록
바꿨다(illustration_planner.py 의 bg_prompt 규칙 참고) — 얼굴을 아예 피하는 것보다
자연스러운 한국인 얼굴을 그리는 게 신빙성 문제를 더 잘 푼다는 판단.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont, ImageOps

import config

SIZE_W, SIZE_H = 1080, 1350   # 4:5 카드(블로그 본문·카드뉴스 표준 비율)
MARGIN = 72

# --- 팔레트: 크림 화이트 + 세이지 그린 + 피치 포인트 ---
CREAM = (250, 246, 239)
CREAM_DEEP = (240, 234, 223)   # 표/구획 배경용(카드 배경보다 한 톤 진하게)
SAGE = (163, 177, 148)         # 옅은 세이지(면·배지)
SAGE_DEEP = (86, 103, 78)      # 진한 세이지(텍스트/라인 대비용)
PEACH = (232, 179, 152)        # 피치(포인트 면)
PEACH_DEEP = (191, 117, 87)    # 진한 피치(강조 숫자·화살표)
INK = (58, 52, 45)             # 본문 텍스트(순수 검정 대신 다크 브라운)
INK_SOFT = (128, 119, 106)     # 보조 텍스트·캡션·각주

_BG_NEGATIVE_PROMPT = (
    "text, letters, numbers, typography, words, signage, labels, logos, brand marks, "
    "watermark, writing, captions, calendar, calendar grid, schedule, chart, graph, "
    "table, spreadsheet, clock face, numbered grid, banknotes, coins, cash, currency, "
    "handwriting, engraving text, note card, greeting card, gift tag, letter, paper with text"
)
# 실측(2026-08-30): 프롬프트 문장에 "no text" 를 덧붙이기만 하면 무시하고 가짜 글자를
# 그려 넣는다 — negative_prompt 파라미터로 따로 줘야 실제로 먹힌다. 액자·라벨·화면·달력처럼
# 원래 글자·숫자가 있는 사물은 negative_prompt로도 못 막으니 프롬프트 자체에서 피해야 한다.

_FONTS_DIR = config.BASE_DIR / "fonts"
_FALLBACK_FONTS = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
]

ROLES = ("cover", "timeline", "comparison", "card", "checklist", "lifestyle")


@dataclass
class CardSpec:
    key: str
    role: str = "card"          # cover|timeline|comparison|card|checklist|lifestyle
    badge: str = ""              # 작은 배지/구분 문구(예: "정보", "01")
    title: str = ""              # cover/timeline/comparison/checklist 의 큰 제목
    subtitle: str = ""           # cover 의 부제
    lines: list = field(default_factory=list)        # card 의 핵심 문장 1~3줄
    caption: str = ""            # card/lifestyle 의 보조 한 줄
    milestones: list = field(default_factory=list)   # timeline: [{"label","desc","highlight"}]
    rows: list = field(default_factory=list)          # comparison: [{"label","before","after"}]
    items: list = field(default_factory=list)         # checklist: [str]
    bg_prompt: str = ""          # lifestyle 전용: 스톡 검색 실패 시 힉스필드 폴백 프롬프트(영어)
    stock_query: str = ""        # lifestyle 전용: Unsplash 무료 스톡 검색어(영어, 우선 시도)
    footer_note: str = ""        # 정책 미확정 안내 등 하단 각주(있으면 모든 정보 카드에 표시)
    before_label: str = "기존"    # comparison 좌측 헤더(정책 아닌 일반 비교면 다른 말로 교체)
    after_label: str = "변경안"   # comparison 우측 헤더(강조색)
    feedback: str = ""           # 유저가 이 카드에 대해 적은 수정 요청(재생성 시에만 채워짐)

    def __post_init__(self):
        # 실측(2026-08-31): 기획 LLM 이 title/subtitle/milestone 설명 같은 '한 줄' 필드에도
        # 곧잘 줄바꿈(\n)을 끼워 넣는다. PIL 의 draw.textlength() 는 멀티라인 문자열을
        # 못 받아 ValueError 로 죽는다 — 여기서 한 번에 공백으로 접어 방지한다.
        def clean1(s):
            return " ".join(str(s or "").split())

        self.badge = clean1(self.badge)
        self.title = clean1(self.title)
        self.subtitle = clean1(self.subtitle)
        self.caption = clean1(self.caption)
        self.footer_note = clean1(self.footer_note)
        self.before_label = clean1(self.before_label) or "기존"
        self.after_label = clean1(self.after_label) or "변경안"
        self.feedback = clean1(self.feedback)
        self.stock_query = clean1(self.stock_query)
        self.lines = [clean1(x) for x in (self.lines or [])]
        self.items = [clean1(x) for x in (self.items or [])]
        self.milestones = [
            {**m, "label": clean1(m.get("label")), "desc": clean1(m.get("desc"))}
            for m in (self.milestones or [])
        ]
        self.rows = [
            {**r, "label": clean1(r.get("label")), "before": clean1(r.get("before")), "after": clean1(r.get("after"))}
            for r in (self.rows or [])
        ]


@dataclass
class Card:
    key: str
    data: bytes


class CardImageError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(config.HF_API_KEY and config.HF_API_SECRET)


def _font(size: int, prefer: str | None = None) -> ImageFont.FreeTypeFont:
    fonts = []
    if _FONTS_DIR.exists():
        fonts = [p for p in _FONTS_DIR.iterdir() if p.suffix.lower() in (".ttf", ".otf", ".ttc")]
    user = None
    if prefer:
        for p in fonts:
            if prefer.lower() in p.stem.lower():
                user = p
                break
    if not user and fonts:
        user = fonts[0]
    candidates = ([str(user)] if user else []) + _FALLBACK_FONTS
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, TypeError):
            continue
    return ImageFont.load_default()


def _system_font(size: int) -> ImageFont.FreeTypeFont:
    """제목용 굵은 서체(가나초콜릿) 대신 촘촘한 본문/라벨용 시스템 고딕."""
    for p in _FALLBACK_FONTS:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, TypeError):
            continue
    return ImageFont.load_default()


def _cover_crop(img: Image.Image, tw: int, th: int) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    scale = max(tw / w, th / h)
    nw, nh = int(w * scale + 0.5), int(h * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _ink_text(draw, xy, text, fnt, fill, anchor="mm", stroke_width=0, stroke_fill=None):
    draw.text(xy, text, font=fnt, fill=fill, anchor=anchor, stroke_width=stroke_width, stroke_fill=stroke_fill)


_MAX_TEXT_W = SIZE_W - MARGIN * 2


def _fit_font(draw, text: str, base_size: int, prefer, min_size: int = 26, max_w: int = _MAX_TEXT_W):
    """긴 줄(숫자 많은 정보성 문장)이 카드 밖으로 잘리지 않게 폭에 맞춰 글자 크기를 줄인다."""
    size = base_size
    while size > min_size:
        f = _font(size, prefer) if prefer is not False else _system_font(size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return _font(min_size, prefer) if prefer is not False else _system_font(min_size)


# ---------------------------------------------------------- gpt-image-2 ----
# 2026-09-02: gpt-image-1 은 2026-10-23 지원 종료 예정이라 gpt-image-2 로 교체.
# 실측 결과 한글 정확도는 그대로(오히려 더 안정적)이고 비용도 더 싸다(1024x1536 high
# 기준 약 $0.17 — gpt-image-1 의 $0.25 보다 저렴). 아래 _GPT_MODEL 만 바꾸면 된다.

_GPT_MODEL = "gpt-image-2"

_STYLE_BASE = (
    "A single Korean parenting-magazine infographic card, portrait orientation, no border/frame. "
    "Background: soft cream/ivory (#FAF6EF), a little paper-like texture, generous white space. "
    "Accent colors only: muted sage green (#A3B194, deeper #56674E) and warm peach (#E8B398, "
    "deeper #BF7557) — no bright primary colors, no blue, no red. "
    "Clean modern flat-vector illustration style, soft rounded shapes, friendly rounded Korean "
    "sans-serif typography with clear hierarchy (one big bold headline, smaller supporting text). "
    "Warm and trustworthy like a quality parenting magazine — NOT a stiff government public-service "
    "poster, not corporate, not an ad banner, no exclamation marks, no bright badges/starbursts."
)

_TEXT_RULE = (
    "Render every quoted Korean text exactly as given, correctly spelled, crisp and fully legible "
    "— never invent, garble, or substitute characters. Do not add any text beyond what is quoted."
)


def _gpt_client():
    if not config.OPENAI_API_KEY:
        raise CardImageError("OPENAI_API_KEY 가 없습니다(.env 확인).")
    from openai import OpenAI
    return OpenAI(api_key=config.OPENAI_API_KEY)


def _gpt_generate(prompt: str) -> bytes:
    client = _gpt_client()
    try:
        resp = client.images.generate(
            model=_GPT_MODEL,
            prompt=prompt,
            size="1024x1536",
            quality="high",
        )
    except Exception as e:
        raise CardImageError(f"gpt-image-1 생성 실패: {e}") from e
    b64 = (resp.data or [None])[0] and resp.data[0].b64_json
    if not b64:
        raise CardImageError("gpt-image-1 이 이미지를 반환하지 않았습니다.")
    return base64.b64decode(b64)


def _pad_to_card_size(img: Image.Image) -> Image.Image:
    """gpt-image-1 은 4:5 를 직접 지원하지 않아(1024x1536 이 가장 가까움) 자르는 대신
    좌우를 크림색으로 여백을 줘서 텍스트가 잘리지 않게 4:5 캔버스에 맞춘다."""
    img = img.convert("RGB")
    w, h = img.size
    scale = SIZE_H / h
    nw, nh = max(1, int(w * scale + 0.5)), SIZE_H
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (SIZE_W, SIZE_H), CREAM)
    x = max(0, (SIZE_W - nw) // 2)
    canvas.paste(img, (x, 0))
    return canvas


_FOOTER_RESERVE = (
    "Leave the bottom strip of the card (roughly the last 90px) as plain empty background "
    "color, with no text, icon or graphic there — that space is reserved and added later."
)


def _feedback_line(spec: CardSpec) -> str:
    fb = spec.feedback.strip()
    if not fb:
        return ""
    return (f'IMPORTANT — the user reviewed a previous version of this card and asked for this '
            f'change, apply it on top of everything above: "{fb}"')


def _footer(canvas: Image.Image, note: str) -> Image.Image:
    """정책 미확정 각주는 gpt-image-1 에게 맡기면 글자가 미세하게 틀릴 수 있어(실측:
    '예산'→'에산', '있음'→'있읍') 이미지 생성 시 그 자리를 비워두게 하고(_FOOTER_RESERVE)
    PIL 로 직접 얹어 100% 정확하게 만든다."""
    note = note.strip()
    if not note:
        return canvas
    draw = ImageDraw.Draw(canvas)
    size = 24
    f = _system_font(size)
    while size > 16 and draw.textlength(note, font=f) > _MAX_TEXT_W * 1.7:
        size -= 2
        f = _system_font(size)
    words = note.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=f) <= _MAX_TEXT_W or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    lines = lines[:2]
    step = size + 10
    start_y = SIZE_H - 24 - (len(lines) - 1) * step
    for i, ln in enumerate(lines):
        _ink_text(draw, (SIZE_W // 2, start_y + i * step), ln, f, INK_SOFT, anchor="mm")
    return canvas


def _gpt_card_bytes(prompt: str, footer_note: str = "") -> bytes:
    raw = _gpt_generate(prompt)
    canvas = _pad_to_card_size(Image.open(io.BytesIO(raw)))
    _footer(canvas, footer_note)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- roles ----

def _prompt_cover(spec: CardSpec) -> str:
    parts = [_STYLE_BASE]
    if spec.badge.strip():
        parts.append(f'A small pill-shaped badge near the top, peach fill, white bold Korean text: "{spec.badge}"')
    parts.append(f'The main focal point: large, bold, centered Korean headline text (the card title), exactly: "{spec.title}"')
    if spec.subtitle.strip():
        parts.append(f'Smaller Korean subtitle text right below the title, exactly: "{spec.subtitle}"')
    parts.append("Below the text, one small, cute flat-vector illustration motif related to babies, "
                  "parenting or family (e.g. baby items, a gift box, a simple family icon) — "
                  "no identifiable real people, illustration only, no faces.")
    parts.append(_FOOTER_RESERVE)
    fb = _feedback_line(spec)
    if fb:
        parts.append(fb)
    parts.append(_TEXT_RULE)
    return "\n".join(parts)


def _prompt_card(spec: CardSpec) -> str:
    lines = [l for l in (spec.lines or []) if l][:3]
    parts = [_STYLE_BASE]
    if spec.badge.strip():
        parts.append(f'A small rounded badge near the top, sage green fill, white Korean text: "{spec.badge}"')
    joined = " / ".join(lines)
    parts.append(f'The main focal point, centered: very large bold Korean text (any number/amount '
                 f'should be the largest element on the card), split across up to three short lines '
                 f'exactly as given (line breaks at the "/" marks): "{joined}"')
    parts.append("One small relevant flat-vector icon (e.g. coin, gift, calendar, baby item) in sage "
                 "or peach, placed subtly near the text, not overpowering it.")
    if spec.caption.strip():
        parts.append(f'Below the main text, smaller gray Korean caption text, exactly: "{spec.caption}"')
    parts.append(_FOOTER_RESERVE)
    fb = _feedback_line(spec)
    if fb:
        parts.append(fb)
    parts.append(_TEXT_RULE)
    return "\n".join(parts)


def _prompt_timeline(spec: CardSpec) -> str:
    parts = [_STYLE_BASE]
    if spec.title.strip():
        parts.append(f'A Korean heading near the top, exactly: "{spec.title}"')
    ms = list(spec.milestones or [])[:4]
    parts.append("A vertical timeline layout: a thin vertical line down the left side of the card, "
                 f"with {max(1, len(ms))} round dots along it marking sequential steps in order, "
                 "top to bottom.")
    for i, m in enumerate(ms, 1):
        hi = bool(m.get("highlight"))
        style = ("this dot is larger and peach-colored, its label text bold peach — it is the most "
                 "important step") if hi else "this dot is sage green"
        parts.append(f'Step {i} ({style}): bold Korean label exactly "{m.get("label", "")}", '
                     f'with smaller Korean description text beside/below it exactly "{m.get("desc", "")}"')
    parts.append(_FOOTER_RESERVE)
    fb = _feedback_line(spec)
    if fb:
        parts.append(fb)
    parts.append(_TEXT_RULE)
    return "\n".join(parts)


def _prompt_comparison(spec: CardSpec) -> str:
    parts = [_STYLE_BASE]
    if spec.title.strip():
        parts.append(f'A Korean heading near the top, exactly: "{spec.title}"')
    parts.append(f'A two-column comparison table layout below the heading: header row with left column '
                 f'Korean text "{spec.before_label}" (muted gray) and right column Korean text '
                 f'"{spec.after_label}" (peach, bold, emphasized). Thin horizontal divider lines '
                 "separate the rows below.")
    for r in list(spec.rows or [])[:4]:
        parts.append(f'A row: left-side Korean row label exactly "{r.get("label", "")}", left column '
                     f'value in muted gray exactly "{r.get("before", "")}", right column value in bold '
                     f'peach exactly "{r.get("after", "")}"')
    parts.append(_FOOTER_RESERVE)
    fb = _feedback_line(spec)
    if fb:
        parts.append(fb)
    parts.append(_TEXT_RULE)
    return "\n".join(parts)


def _prompt_checklist(spec: CardSpec) -> str:
    parts = [_STYLE_BASE]
    if spec.title.strip():
        parts.append(f'A Korean heading near the top, exactly: "{spec.title}"')
    items = [x for x in (spec.items or []) if x][:5]
    parts.append(f"A vertical checklist below the heading with {max(1, len(items))} items, evenly "
                 "spaced top to bottom: each item is a small sage-green circle with a white checkmark "
                 "inside, followed by Korean text.")
    for it in items:
        parts.append(f'Checklist item Korean text, exactly: "{it}"')
    parts.append(_FOOTER_RESERVE)
    fb = _feedback_line(spec)
    if fb:
        parts.append(fb)
    parts.append(_TEXT_RULE)
    return "\n".join(parts)


def _compose_cover(spec: CardSpec) -> bytes:
    return _gpt_card_bytes(_prompt_cover(spec), spec.footer_note)


def _compose_card(spec: CardSpec) -> bytes:
    return _gpt_card_bytes(_prompt_card(spec), spec.footer_note)


def _compose_timeline(spec: CardSpec) -> bytes:
    return _gpt_card_bytes(_prompt_timeline(spec), spec.footer_note)


def _compose_comparison(spec: CardSpec) -> bytes:
    return _gpt_card_bytes(_prompt_comparison(spec), spec.footer_note)


def _compose_checklist(spec: CardSpec) -> bytes:
    return _gpt_card_bytes(_prompt_checklist(spec), spec.footer_note)


def _generate_background(prompt: str) -> bytes:
    if not enabled():
        raise CardImageError("HF_API_KEY/HF_API_SECRET 가 없습니다(.env 확인).")
    import urllib.request
    import higgsfield_client as hc

    try:
        result = hc.subscribe(
            config.HIGGSFIELD_MODEL,
            arguments={"prompt": prompt, "negative_prompt": _BG_NEGATIVE_PROMPT},
        )
    except Exception as e:
        raise CardImageError(f"힉스필드 배경 생성 실패: {e}") from e

    images = result.get("images") or []
    if not images or not images[0].get("url"):
        raise CardImageError("힉스필드가 이미지를 반환하지 않았습니다.")
    with urllib.request.urlopen(images[0]["url"], timeout=30) as r:
        return r.read()


def _fetch_stock_photo(query: str) -> bytes | None:
    """Unsplash 무료 스톡에서 실제 사진 1장을 찾는다. 검색어가 없거나, 키가 없거나,
    결과가 없거나 실패하면 None(호출부가 힉스필드 생성으로 대체한다).

    2026-09-14: 사람이 나오는 검색은 search_unsplash_korean() 으로 — 후보를 비전으로
    걸러 서구권 인물 사진은 버리고 한국인으로 보일 법한 사진만 쓴다(image_finder 참고)."""
    query = (query or "").strip()
    if not query or not config.UNSPLASH_ACCESS_KEY:
        return None
    from modules import image_finder
    try:
        return image_finder.search_unsplash_korean(query)
    except Exception as e:
        print(f"  (스톡 검색 실패({query[:40]}), 대체 생성으로 넘어감: {str(e)[:80]})")
        return None


def _compose_lifestyle(spec: CardSpec) -> bytes:
    """실사 1장. 무료 스톡(Unsplash)을 먼저 찾고, 없으면 힉스필드로 생성한다.
    문구는 얹지 않는다(유저 요청: 사진만 있으면 된다) — 캡션은 blog 본문 쪽에서 다룬다.

    재생성(피드백 있음)일 땐 스톡을 다시 무작위로 뽑는 대신 곧장 힉스필드 생성으로
    가서 피드백을 반영한다 — 스톡 검색은 같은 검색어로도 다른 사진을 못 고르게 할 수
    있어 '이전과 다르게 해달라'는 피드백 의도에 안 맞는다."""
    fb = spec.feedback.strip()
    bg_bytes = None if fb else _fetch_stock_photo(spec.stock_query)
    if bg_bytes is None:
        prompt = spec.bg_prompt.strip()
        if not prompt:
            raise CardImageError("스톡 사진을 못 찾았고 대체 생성 프롬프트도 없습니다.")
        if fb:
            prompt = f"{prompt} Additional requested change: {fb}."
        bg_bytes = _generate_background(prompt)
    bg = ImageOps.exif_transpose(Image.open(io.BytesIO(bg_bytes)))
    canvas = _cover_crop(bg, SIZE_W, SIZE_H).convert("RGB")
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


_COMPOSERS = {
    "cover": _compose_cover,
    "card": _compose_card,
    "timeline": _compose_timeline,
    "comparison": _compose_comparison,
    "checklist": _compose_checklist,
}


def make_cards(specs: list) -> list:
    """카드 사양 목록 → 완성된 카드 이미지 목록. 개별 실패는 건너뛰고 나머지를 돌려준다.

    role in (cover/card/timeline/comparison/checklist) 은 gpt-image-1 로 텍스트까지 통째로
    받는다(OPENAI_API_KEY 필요). role == "lifestyle" 만 힉스필드 배경이 필요하다
    (enabled() 아니면 건너뛴다).
    """
    cards: list = []
    for spec in specs:
        try:
            if spec.role == "lifestyle":
                # 스톡(Unsplash)만으로도 만들 수 있으니 힉스필드 키 없다고 건너뛰지 않는다.
                # 스톡도 없고 힉스필드도 없으면 _compose_lifestyle 이 CardImageError 를 낸다.
                data = _compose_lifestyle(spec)
            else:
                composer = _COMPOSERS.get(spec.role, _compose_card)
                data = composer(spec)
        except CardImageError as e:
            print(f"  (카드 {spec.key}/{spec.role} 실패, 건너뜀: {str(e)[:100]})")
            continue
        except Exception as e:
            # 실측(2026-08-31): 여길 조용히 넘기면 개수만 줄고 로그에 원인이 안 남는다.
            print(f"  (카드 {spec.key}/{spec.role} 렌더링 오류, 건너뜀: {type(e).__name__}: {str(e)[:100]})")
            continue
        cards.append(Card(spec.key, data))
    if not cards:
        raise CardImageError("카드를 한 장도 만들지 못했습니다.")
    return cards
