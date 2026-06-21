"""비비(bbnation) 고정 썸네일 템플릿을 Pillow 로 합성한다.

레퍼런스 썸네일 실측값(1080x1080) 기준:
- 배경 (238,233,227) 크림
- 좌상단 "@bbnation" + 가로선, 우상단 "REVIEW" (녹색 86,138,53)
- 사진을 아치형(폭 714, y 125~855, 가운데)으로 크롭해 배치
- 하단 제목 녹색(65,102,27), 약 84px, 가운데, 1~2줄

폰트는 fonts/ 폴더에 사용자 서체(.ttf/.otf)를 넣으면 그것을 우선 사용한다.
(미리캔버스에서 쓴 폰트를 fonts/title.ttf 로 저장 → 동일하게 매칭)
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont, ImageOps

import config

# --- 실측 디자인 상수 ---
SIZE = 1080
MARGIN = 48
BG = (238, 233, 227)        # 크림 배경
ACCENT = (86, 138, 53)      # @bbnation / REVIEW / 가로선
TITLE_C = (65, 102, 27)     # 제목 녹색(진한 올리브)

ARCH_W = 714                # 아치 폭
ARCH_TOP = 125
ARCH_BOTTOM = 855

# 사용자 지정(가나초콜릿체) 크기 — 미리캔버스 1080 캔버스 기준
TITLE_SIZE = 53            # 가운데 하단 제목
TITLE_STEP = 86            # 제목 줄 간격
TITLE_CENTER_Y = 965       # 제목 블록 세로 중심
HEADER_Y = 67              # @bbnation / REVIEW / 라인 세로 위치
BRAND_SIZE = 19            # 좌상단 @bbnation (19.1)
REVIEW_SIZE = 25           # 우상단 REVIEW

# 사용자 폰트 우선, 없으면 macOS 기본 한글 폰트
_FONTS_DIR = config.BASE_DIR / "fonts"
_FALLBACK_FONTS = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
]


def is_available() -> bool:
    return True


def _find_user_font(prefer: str | None) -> str | None:
    """fonts/ 폴더에서 사용자 폰트를 찾는다. prefer(예: 'title')에 맞는 파일 우선."""
    if not _FONTS_DIR.exists():
        return None
    fonts = [p for p in _FONTS_DIR.iterdir()
             if p.suffix.lower() in (".ttf", ".otf", ".ttc")]
    if not fonts:
        return None
    if prefer:
        for p in fonts:
            if prefer.lower() in p.stem.lower():
                return str(p)
    return str(fonts[0])  # 아무 폰트나 첫 번째


def _font(size: int, prefer: str | None = None) -> ImageFont.FreeTypeFont:
    user = _find_user_font(prefer)
    candidates = ([user] if user else []) + _FALLBACK_FONTS
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


def _arch_mask(w: int, h: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.pieslice([0, 0, w, w], 180, 360, fill=255)   # 상단 반원
    d.rectangle([0, w // 2, w, h], fill=255)        # 하단 직사각형
    return mask


def _wrap_title(title) -> list[str]:
    if isinstance(title, list):
        lines = [str(t).strip() for t in title if str(t).strip()]
        if lines:
            return lines[:2]
        title = ""
    text = str(title).strip()
    if not text:
        return ["REVIEW"]
    if len(text) <= 12:
        return [text]
    words = text.split()
    if len(words) > 1:
        best, bestdiff = None, 1e9
        for i in range(1, len(words)):
            a, b = " ".join(words[:i]), " ".join(words[i:])
            if abs(len(a) - len(b)) < bestdiff:
                best, bestdiff = (a, b), abs(len(a) - len(b))
        return list(best)
    mid = len(text) // 2
    return [text[:mid], text[mid:]]


def generate_thumbnail(photo_bytes: bytes, title) -> tuple[bytes | None, str]:
    try:
        canvas = Image.new("RGB", (SIZE, SIZE), BG)
        draw = ImageDraw.Draw(canvas)

        # 1) 헤더: @bbnation + 가로선 + REVIEW
        f_brand = _font(BRAND_SIZE, "brand")
        f_review = _font(REVIEW_SIZE, "brand")
        draw.text((MARGIN, HEADER_Y), "@bbnation", font=f_brand, fill=ACCENT,
                  anchor="lm", stroke_width=1, stroke_fill=ACCENT)
        bb = draw.textbbox((MARGIN, HEADER_Y), "@bbnation", font=f_brand, anchor="lm")
        draw.text((SIZE - MARGIN, HEADER_Y), "REVIEW", font=f_review, fill=ACCENT,
                  anchor="rm", stroke_width=1, stroke_fill=ACCENT)
        rb = draw.textbbox((SIZE - MARGIN, HEADER_Y), "REVIEW", font=f_review, anchor="rm")
        ls, le = bb[2] + 18, rb[0] - 22
        if le > ls:
            draw.line([(ls, HEADER_Y), (le, HEADER_Y)], fill=ACCENT, width=3)

        # 2) 아치 사진
        x0 = (SIZE - ARCH_W) // 2
        box_h = ARCH_BOTTOM - ARCH_TOP
        photo = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes)))
        ph = _cover_crop(photo, ARCH_W, box_h)
        canvas.paste(ph, (x0, ARCH_TOP), _arch_mask(ARCH_W, box_h))

        # 3) 제목 (녹색, 가운데, 1~2줄)
        lines = _wrap_title(title)
        f_title = _font(TITLE_SIZE, "title")
        n = len(lines)
        start = TITLE_CENTER_Y - (n - 1) * TITLE_STEP / 2
        for i, ln in enumerate(lines):
            y = int(start + i * TITLE_STEP)
            draw.text((SIZE // 2, y), ln, font=f_title, fill=TITLE_C,
                      anchor="mm", stroke_width=2, stroke_fill=TITLE_C)

        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue(), "썸네일 생성 완료 (bbnation 템플릿)."
    except Exception as e:
        return None, f"썸네일 생성 실패: {e}"
