"""정보성/화제성 글용 카드뉴스 이미지 생성.

2026-08-31 개편(유저 지정 스타일 가이드 반영): 크림 화이트 + 세이지 그린 + 피치 포인트의
'육아 매거진 인포그래픽' 톤. 10장 중 8장은 정보 카드(표지/타임라인/비교표/카드형/체크리스트),
2장은 생활감 있는 실사(힉스필드). 레이아웃에 변화를 줘서 10장이 다 같은 틀로 보이지 않게 한다.

실측(2026-08-30): 힉스필드에게 "텍스트 없이"라고 프롬프트를 줘도 의미 없는 가짜 글자를
그려 넣는다(한글은 더 심하게 깨짐). 그래서 정보 카드 8장은 애초에 힉스필드를 쓰지 않고
전부 PIL 로만 그린다(사진이 필요 없으니 이 문제 자체가 없다) — 힉스필드는 실사 2장에만 쓰고,
거기서도 얹는 문구는 짧은 캡션 한 줄 정도로 최소화한다.
"""

from __future__ import annotations

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
    "table, spreadsheet, clock face, numbered grid, banknotes, coins, cash, currency"
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
    bg_prompt: str = ""          # lifestyle 전용: 힉스필드 장면 프롬프트(영어)
    footer_note: str = ""        # 정책 미확정 안내 등 하단 각주(있으면 모든 정보 카드에 표시)

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


def _vgrad(w: int, h: int, y0: int, y1: int, a0: int, a1: int, color=(0, 0, 0)) -> Image.Image:
    """y0~y1 구간에서 알파를 a0→a1로 선형 보간하는 세로 그라디언트 오버레이."""
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
    overlay = Image.new("RGBA", (w, h), color + (0,))
    overlay.putalpha(grad)
    return overlay


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


def _badge(draw, cx: int, cy: int, text: str, fill, text_fill=(255, 255, 255)):
    f = _system_font(28)
    bb = draw.textbbox((0, 0), text, font=f)
    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    pad_x, pad_y = 22, 12
    x0 = cx - (bw + pad_x * 2) // 2
    y0 = cy - (bh + pad_y * 2) // 2
    draw.rounded_rectangle([x0, y0, x0 + bw + pad_x * 2, y0 + bh + pad_y * 2],
                            radius=(bh + pad_y * 2) // 2, fill=fill)
    _ink_text(draw, (cx, cy), text, f, text_fill, anchor="mm")
    return y0 + bh + pad_y * 2  # 배지 아래쪽 y좌표


def _accent_blob(canvas: Image.Image, color, cx: int, cy: int, r: int, alpha: int = 60):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (alpha,))
    canvas.alpha_composite(layer)


def _base_canvas() -> Image.Image:
    """정보 카드 공통 배경: 크림 화이트 + 모서리의 은은한 세이지·피치 원형 포인트(시리즈 통일감)."""
    canvas = Image.new("RGBA", (SIZE_W, SIZE_H), CREAM + (255,))
    _accent_blob(canvas, SAGE, SIZE_W + 60, -40, 260, alpha=70)
    _accent_blob(canvas, PEACH, -80, SIZE_H + 60, 240, alpha=55)
    return canvas


def _footer(draw, note: str):
    if not note.strip():
        return
    f = _system_font(22)
    _ink_text(draw, (SIZE_W // 2, SIZE_H - 44), note.strip(), f, INK_SOFT, anchor="mm")


def _wrap_lines(draw, text: str, font_size: int, prefer, max_w: int, max_lines: int = 2) -> list:
    words = text.split()
    lines, cur = [], ""
    f = _font(font_size, prefer) if prefer is not False else _system_font(font_size)
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
        if len(lines) == max_lines - 1:
            pass
    if cur:
        lines.append(cur)
    return lines[:max_lines]


# ---------------------------------------------------------------- roles ----

def _compose_cover(spec: CardSpec) -> bytes:
    canvas = _base_canvas()
    draw = ImageDraw.Draw(canvas)
    y = 360
    if spec.badge.strip():
        _badge(draw, SIZE_W // 2, 220, spec.badge.strip(), PEACH + (255,), text_fill=(255, 255, 255))

    lines = _wrap_lines(draw, spec.title or "", 82, "title", _MAX_TEXT_W, max_lines=3)
    step = 100
    start = y - (len(lines) - 1) * step / 2
    for i, ln in enumerate(lines):
        f = _fit_font(draw, ln, 82, "title", min_size=46)
        _ink_text(draw, (SIZE_W // 2, start + i * step), ln, f, SAGE_DEEP, anchor="mm")

    if spec.subtitle.strip():
        f_sub = _fit_font(draw, spec.subtitle.strip(), 34, False, min_size=22)
        _ink_text(draw, (SIZE_W // 2, start + len(lines) * step + 40), spec.subtitle.strip(),
                   f_sub, INK_SOFT, anchor="mm")

    draw.rounded_rectangle([SIZE_W // 2 - 60, SIZE_H - 140, SIZE_W // 2 + 60, SIZE_H - 132],
                            radius=4, fill=SAGE)
    _footer(draw, spec.footer_note)
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _compose_card(spec: CardSpec) -> bytes:
    canvas = _base_canvas()
    draw = ImageDraw.Draw(canvas)
    lines = [l.strip() for l in (spec.lines or []) if l.strip()][:3]

    y = SIZE_H // 2 + 40
    if spec.badge.strip():
        by = _badge(draw, SIZE_W // 2, SIZE_H // 2 - 220, spec.badge.strip(), SAGE + (255,))
    step = 96
    start = y - (len(lines) - 1) * step / 2
    for i, ln in enumerate(lines):
        f = _fit_font(draw, ln, 62, "title", min_size=32)
        _ink_text(draw, (SIZE_W // 2, start + i * step), ln, f, INK, anchor="mm")

    if spec.caption.strip():
        f_cap = _fit_font(draw, spec.caption.strip(), 30, False, min_size=20)
        _ink_text(draw, (SIZE_W // 2, start + len(lines) * step + 44), spec.caption.strip(),
                   f_cap, INK_SOFT, anchor="mm")

    _footer(draw, spec.footer_note)
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _compose_timeline(spec: CardSpec) -> bytes:
    canvas = _base_canvas()
    draw = ImageDraw.Draw(canvas)
    top = 200
    if spec.title.strip():
        f_t = _fit_font(draw, spec.title.strip(), 46, "title", min_size=30)
        _ink_text(draw, (SIZE_W // 2, 130), spec.title.strip(), f_t, SAGE_DEEP, anchor="mm")

    ms = list(spec.milestones or [])[:4]
    if not ms:
        ms = [{"label": "", "desc": l} for l in (spec.lines or [])][:4]
    n = max(1, len(ms))
    slot_h = (SIZE_H - top - 220) / n
    line_x = MARGIN + 30
    draw.line([(line_x, top), (line_x, top + slot_h * n)], fill=SAGE, width=4)

    for i, m in enumerate(ms):
        cy = int(top + slot_h * i + slot_h / 2)
        hi = bool(m.get("highlight"))
        r = 16 if hi else 12
        fill = PEACH_DEEP if hi else SAGE_DEEP
        draw.ellipse([line_x - r, cy - r, line_x + r, cy + r], fill=fill)
        tx = line_x + 56
        label = str(m.get("label", "")).strip()
        desc = str(m.get("desc", "")).strip()
        if label:
            f_l = _fit_font(draw, label, 40, "title", min_size=26, max_w=SIZE_W - tx - MARGIN)
            _ink_text(draw, (tx, cy - 22), label, f_l, fill, anchor="lm")
        if desc:
            f_d = _fit_font(draw, desc, 28, False, min_size=20, max_w=SIZE_W - tx - MARGIN)
            _ink_text(draw, (tx, cy + (22 if label else 0)), desc, f_d, INK, anchor="lm")

    _footer(draw, spec.footer_note)
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _compose_comparison(spec: CardSpec) -> bytes:
    canvas = _base_canvas()
    draw = ImageDraw.Draw(canvas)
    top = 190
    if spec.title.strip():
        f_t = _fit_font(draw, spec.title.strip(), 44, "title", min_size=28)
        _ink_text(draw, (SIZE_W // 2, 120), spec.title.strip(), f_t, SAGE_DEEP, anchor="mm")

    col_before_x = SIZE_W // 2 - 40
    col_after_x = SIZE_W - MARGIN - 20
    f_head = _system_font(26)
    _ink_text(draw, (col_before_x, top), "기존", f_head, INK_SOFT, anchor="rm")
    _ink_text(draw, (col_after_x, top), "변경안", f_head, PEACH_DEEP, anchor="rm")
    draw.line([(MARGIN, top + 30), (SIZE_W - MARGIN, top + 30)], fill=CREAM_DEEP, width=3)

    rows = list(spec.rows or [])[:4]
    row_h = (SIZE_H - top - 60 - 240) / max(1, len(rows))
    for i, r in enumerate(rows):
        ry = int(top + 30 + row_h * i + row_h / 2)
        label = str(r.get("label", "")).strip()
        before = str(r.get("before", "")).strip()
        after = str(r.get("after", "")).strip()
        f_label = _fit_font(draw, label, 28, False, min_size=20, max_w=SIZE_W // 2 - MARGIN - 60)
        _ink_text(draw, (MARGIN, ry), label, f_label, INK, anchor="lm")
        f_val = _fit_font(draw, before, 30, "title", min_size=20, max_w=SIZE_W // 2 - 60)
        _ink_text(draw, (col_before_x, ry), before, f_val, INK_SOFT, anchor="rm")
        f_after = _fit_font(draw, after, 32, "title", min_size=20, max_w=SIZE_W // 2 - 60)
        _ink_text(draw, (col_after_x, ry), after, f_after, PEACH_DEEP, anchor="rm")
        if i < len(rows) - 1:
            yy = int(top + 30 + row_h * (i + 1))
            draw.line([(MARGIN, yy), (SIZE_W - MARGIN, yy)], fill=CREAM_DEEP, width=2)

    _footer(draw, spec.footer_note)
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _compose_checklist(spec: CardSpec) -> bytes:
    canvas = _base_canvas()
    draw = ImageDraw.Draw(canvas)
    if spec.title.strip():
        f_t = _fit_font(draw, spec.title.strip(), 46, "title", min_size=30)
        _ink_text(draw, (SIZE_W // 2, 190), spec.title.strip(), f_t, SAGE_DEEP, anchor="mm")

    items = [str(x).strip() for x in (spec.items or []) if str(x).strip()][:5]
    top = 340
    row_h = (SIZE_H - top - 200) / max(1, len(items))
    for i, it in enumerate(items):
        cy = int(top + row_h * i + row_h / 2)
        r = 20
        cx = MARGIN + r
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SAGE)
        f_chk = _system_font(24)
        _ink_text(draw, (cx, cy - 1), "✓", f_chk, (255, 255, 255), anchor="mm")
        f_it = _fit_font(draw, it, 32, False, min_size=22, max_w=SIZE_W - (cx + r + 32) - MARGIN)
        _ink_text(draw, (cx + r + 32, cy), it, f_it, INK, anchor="lm")

    _footer(draw, spec.footer_note)
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


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


def _compose_lifestyle(spec: CardSpec) -> bytes:
    bg_bytes = _generate_background(spec.bg_prompt)
    bg = ImageOps.exif_transpose(Image.open(io.BytesIO(bg_bytes)))
    canvas = _cover_crop(bg, SIZE_W, SIZE_H).convert("RGBA")
    cap = spec.caption.strip()
    if cap:
        canvas.alpha_composite(_vgrad(SIZE_W, SIZE_H, SIZE_H - 190, SIZE_H, 0, 235))
        draw = ImageDraw.Draw(canvas)
        f_cap = _fit_font(draw, cap, 34, "title", min_size=24)
        _ink_text(draw, (SIZE_W // 2 + 2, SIZE_H - 78 + 2), cap, f_cap, (0, 0, 0, 90), anchor="mm")
        _ink_text(draw, (SIZE_W // 2, SIZE_H - 78), cap, f_cap, (255, 255, 255, 255), anchor="mm")
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
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

    role in (cover/card/timeline/comparison/checklist) 은 힉스필드 없이 PIL 로만 그린다.
    role == "lifestyle" 만 힉스필드 배경이 필요하다(enabled() 아니면 건너뛴다).
    """
    cards: list = []
    for spec in specs:
        try:
            if spec.role == "lifestyle":
                if not enabled():
                    continue
                data = _compose_lifestyle(spec)
            else:
                composer = _COMPOSERS.get(spec.role, _compose_card)
                data = composer(spec)
        except CardImageError:
            continue
        except Exception:
            continue
        cards.append(Card(spec.key, data))
    if not cards:
        raise CardImageError("카드를 한 장도 만들지 못했습니다.")
    return cards
