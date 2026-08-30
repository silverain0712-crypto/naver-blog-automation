"""정보성/화제성 글용 카드뉴스 이미지 생성.

실측(2026-08-30): 힉스필드(`higgsfield-ai/soul/v2/standard`)에게 "텍스트 없이"라고
프롬프트를 줘도 의미 없는 가짜 글자를 그려 넣는다(AI 이미지 모델의 공통 한계 — 한글은
더 심하게 깨진다). 그래서 힉스필드는 배경/분위기 이미지만 만들게 하고, 실제로 보여줄
한글 텍스트(핵심 사실·수치)는 썸네일과 같은 방식으로 PIL 로 직접 얹는다.

흐름: CardSpec(배경 프롬프트 + 얹을 텍스트) 여러 장 → 힉스필드로 배경 생성 →
PIL 로 하단 스크림 + 배지 + 텍스트 합성 → 카드 PNG bytes 리스트.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont, ImageOps

import config

SIZE_W, SIZE_H = 1080, 1350   # 4:5 카드(블로그 본문·카드뉴스 표준 비율)
ACCENT = (86, 138, 53)        # 비비 브랜드 그린(썸네일과 동일)
CREAM = (238, 233, 227)

_BG_NEGATIVE_PROMPT = (
    "text, letters, numbers, typography, words, signage, labels, logos, watermark, "
    "writing, captions"
)
# 실측(2026-08-30): 프롬프트 문장에 "no text" 를 덧붙이기만 하면 무시하고 가짜 글자를
# 그려 넣는다 — negative_prompt 파라미터로 따로 줘야 실제로 먹힌다. 그리고 프레임 액자·
# 제품 라벨·디지털 디스플레이처럼 '원래 글자가 있는 사물'이 장면에 있으면 negative_prompt를
# 줘도 자꾸 깨진 글자를 그려 넣는다 — bg_prompt 를 쓸 때 그런 사물은 아예 피하고, 질감·
# 손·식물·빛처럼 글자가 없는 소재로 장면을 짜야 안정적으로 나온다.

_FONTS_DIR = config.BASE_DIR / "fonts"
_FALLBACK_FONTS = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
]


@dataclass
class CardSpec:
    key: str
    bg_prompt: str              # 배경 이미지 프롬프트(영어). 액자·제품 라벨·화면·간판처럼
                                 # 원래 글자가 있는 사물은 넣지 마라(negative_prompt로도 못 막음).
    lines: list[str]            # 메인 텍스트 1~3줄(한글)
    badge: str = ""             # 작은 배지 텍스트(예: "01", "STEP 1"). 없으면 생략.
    caption: str = ""           # 보조 설명 한 줄(선택)


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


def _cover_crop(img: Image.Image, tw: int, th: int) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    scale = max(tw / w, th / h)
    nw, nh = int(w * scale + 0.5), int(h * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _vgrad(w: int, h: int, y0: int, y1: int, a0: int, a1: int) -> Image.Image:
    """y0~y1 구간에서 알파를 a0→a1로 선형 보간하는 세로 그라디언트(검은색) 오버레이."""
    grad = Image.new("L", (1, h), 0)
    for y in range(h):
        if y < y0:
            a = a0
        elif y > y1:
            a = a1
        else:
            t = (y - y0) / max(1, (y1 - y0))
            a = int(a0 + (a1 - a0) * t)
        grad.putpixel((0, y), a)
    grad = grad.resize((w, h))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.putalpha(grad)
    return overlay


def _ink_text(draw, xy, text, fnt, fill, anchor="mm", stroke_width=0, stroke_fill=None):
    draw.text(xy, text, font=fnt, fill=fill, anchor=anchor, stroke_width=stroke_width, stroke_fill=stroke_fill)


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


def _compose(bg_bytes: bytes, spec: CardSpec) -> bytes:
    bg = ImageOps.exif_transpose(Image.open(io.BytesIO(bg_bytes)))
    canvas = _cover_crop(bg, SIZE_W, SIZE_H).convert("RGBA")

    lines = [l.strip() for l in (spec.lines or []) if l.strip()][:3]
    has_caption = bool(spec.caption.strip())
    # 텍스트 블록 높이에 맞춰 스크림 시작 지점을 잡는다(짧으면 스크림도 얕게).
    block_lines = len(lines) + (1 if has_caption else 0) + (1 if spec.badge.strip() else 0)
    scrim_top = SIZE_H - 260 - block_lines * 90
    canvas.alpha_composite(_vgrad(SIZE_W, SIZE_H, scrim_top, SIZE_H, 0, 215))

    draw = ImageDraw.Draw(canvas)
    y = SIZE_H - 96

    if has_caption:
        f_cap = _font(30)
        _ink_text(draw, (SIZE_W // 2, y), spec.caption.strip(), f_cap, (230, 230, 224, 255), anchor="mm")
        y -= 56

    f_line = _font(66, "title")
    for line in reversed(lines):
        _ink_text(draw, (SIZE_W // 2 + 2, y + 2), line, f_line, (0, 0, 0, 90), anchor="mm")
        _ink_text(draw, (SIZE_W // 2, y), line, f_line, (255, 255, 255, 255), anchor="mm")
        y -= 84

    if spec.badge.strip():
        f_badge = _font(28)
        badge = spec.badge.strip()
        bb = draw.textbbox((0, 0), badge, font=f_badge)
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
        pad_x, pad_y = 20, 10
        px0 = SIZE_W // 2 - (bw + pad_x * 2) // 2
        py0 = y - bh - pad_y * 2 - 14
        draw.rounded_rectangle(
            [px0, py0, px0 + bw + pad_x * 2, py0 + bh + pad_y * 2],
            radius=(bh + pad_y * 2) // 2, fill=ACCENT + (235,),
        )
        _ink_text(draw, (SIZE_W // 2, py0 + (bh + pad_y * 2) // 2), badge, f_badge, (255, 255, 255, 255), anchor="mm")

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def make_cards(specs: list[CardSpec]) -> list[Card]:
    """카드 사양 목록 → 완성된 카드 이미지 목록. 개별 실패는 건너뛰고 나머지를 돌려준다."""
    cards: list[Card] = []
    for spec in specs:
        try:
            bg = _generate_background(spec.bg_prompt)
            data = _compose(bg, spec)
        except CardImageError:
            continue
        cards.append(Card(spec.key, data))
    if not cards:
        raise CardImageError("카드를 한 장도 만들지 못했습니다.")
    return cards
