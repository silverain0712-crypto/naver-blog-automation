"""상품 링크 → 상품명·가격·사진. 링크 하나만 있으면 나머지를 알아서 가져온다.

네이버 쇼핑은 curl/requests 를 봇으로 보고 막는다(418). 브랜드커넥트 링크는 아예
JS 로 그려서 HTML 만 받아선 아무것도 없다. 그래서 이미 깔려 있는 playwright 로
실제 브라우저를 띄워 렌더링된 화면에서 긁는다.

네이버 로그인 프로필(USERDATA_DIR)은 쓰지 않는다 — 상품 페이지는 로그인이 필요 없고,
프로필을 같이 쓰면 발행 워커의 잡과 충돌한다.

지원: naver.me 단축, 브랜드커넥트(쇼핑커넥트 제휴), 스마트스토어, 네이버쇼핑, 일반 쇼핑몰.
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_TIMEOUT = 30000

# 로고·아이콘과 섞이지 않게 화면에 크게 그려진 것만 골라낸다.
_MIN_EDGE = 180
# 상품 갤러리 컨테이너에서 흔히 쓰는 클래스/속성 이름(카페24 xans-product-image 등).
# alt·class 텍스트에 이 패턴이 걸리면 '진짜 상품 사진'일 확률이 높다고 보고 가점한다.
_PRODUCT_HINT_RE = re.compile(r"product|prd|goods|item[-_]?img|detail[-_]?img|gallery", re.I)
_HINT_BONUS = 5000


@dataclass
class Product:
    url: str = ""
    name: str = ""
    brand: str = ""
    price: str = ""
    sale_price: str = ""
    rating: str = ""
    review_count: str = ""
    images: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [b for b in (self.brand, self.name) if b]
        head = " / ".join(bits) or "(상품명 미확인)"
        if self.sale_price:
            head += f" — {self.sale_price}"
        elif self.price:
            head += f" — {self.price}"
        return head


_PRICE_RE = re.compile(r"[\d,]{3,}\s*원")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _pick_images(page) -> list[dict]:
    """상품 사진 후보를 점수순으로 돌려준다. 각 항목은 {"src", "alt"}.

    실측(2026-08-30): og:image 를 무조건 대표컷으로 믿었더니, 일반 쇼핑몰(카페24 등)에서는
    og:image 가 브랜드 로고/공유용 배너인 경우가 흔했다(예: mongdies.com, Noble Farms).
    그 로고를 레퍼런스로 넘기면 Gemini 가 진짜 제품이 아니라 그럴싸한 다른 물건을
    지어낸다 — 세정제 정제를 유리병 오일로 만드는 식. 진짜 상품 사진은 화면에 버젓이
    있었지만 `_IMG_HOSTS`(네이버 CDN 전용) 필터에 걸려 후보에서 빠졌었다.

    그래서 호스트 제한을 없애고, 대신 (1) 표시 크기 (2) 정사각~4:5 범위의 종횡비
    (배너·상세페이지 스토리 이미지는 대개 2:1 이상으로 길쭉하다) (3) 컨테이너 class/alt 에
    'product/prd/goods' 류 힌트가 있으면 가점, 으로 점수를 매겨 정렬한다. og:image 는
    후보 목록에는 넣되 다른 신호가 없을 때만 쓰이도록 낮은 기본 점수만 준다.
    """
    raw = page.evaluate(
        """() => Array.from(document.images).map(img => ({
             src: img.currentSrc || img.src,
             w: img.naturalWidth, h: img.naturalHeight,
             dw: img.width, dh: img.height,
             alt: img.alt || '',
             cls: img.className || '',
             parentCls: img.parentElement ? (img.parentElement.className || '') : '',
           }))"""
    )

    def norm(src: str) -> str | None:
        if not src or not src.startswith("http"):
            return None
        # 네이버는 ?type=f120 같은 리사이즈 쿼리를 붙인다 — 원본 크기로 되돌린다.
        return re.sub(r"\?type=\w+$", "", src)

    scored: dict[str, dict] = {}  # src -> {"src", "alt", "score"}

    for it in raw:
        src = norm(it.get("src") or "")
        if not src or src in scored:
            continue
        w, h = it.get("w") or 0, it.get("h") or 0
        # 로고·아이콘은 한 변이 짧다. 상품 사진은 정사각에 가깝고 크다.
        if min(w, h) < _MIN_EDGE:
            continue
        if min(it.get("dw") or 0, it.get("dh") or 0) < 80:
            continue
        # 배너·이벤트 상세컷은 대개 2:1 이상으로 길쭉하다(상품 사진은 보통 4:5~1:1).
        ratio = max(w, h) / max(1, min(w, h))
        if ratio > 1.6:
            continue
        hint_text = f"{it.get('alt', '')} {it.get('cls', '')} {it.get('parentCls', '')}"
        score = min(w, h) + (_HINT_BONUS if _PRODUCT_HINT_RE.search(hint_text) else 0)
        scored[src] = {"src": src, "alt": _clean(it.get("alt") or ""), "score": score}

    og = norm(_meta(page, "og:image"))
    if og and og not in scored:
        scored[og] = {"src": og, "alt": "", "score": _MIN_EDGE}

    return sorted(scored.values(), key=lambda d: -d["score"])


def _meta(page, prop: str) -> str:
    try:
        el = page.query_selector(f'meta[property="{prop}"], meta[name="{prop}"]')
        return _clean(el.get_attribute("content")) if el else ""
    except Exception:
        return ""


def _prices(page) -> tuple[str, str]:
    """(정가, 할인가). 확신이 없으면 빈 문자열 — 틀린 가격을 넘기지 않는다.

    본문 전체를 정규식으로 훑으면 배송비·쿠폰 금액이 먼저 걸린다. 가격 영역
    엘리먼트 안에서만 찾는다.
    """
    for sel in ('[class*="lowestPrice"]', '[class*="price_area"]', '[class*="PriceArea"]',
                '[class*="total_price"]', '[class*="productPrice"]'):
        try:
            el = page.query_selector(sel)
        except Exception:
            continue
        if not el:
            continue
        found = _PRICE_RE.findall(_clean(el.inner_text()))
        if not found:
            continue
        if len(found) >= 2:
            return _clean(found[0]), _clean(found[1])
        return _clean(found[0]), ""
    return "", ""


def fetch(url: str, headless: bool = True, log=None) -> Product:
    """상품 링크를 실제 브라우저로 열어 정보를 긁는다."""
    say = log or (lambda m: None)
    p = Product()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=_UA,
            viewport={"width": 1440, "height": 1600},
            locale="ko-KR",
        )
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # 광고/트래커가 계속 물면 idle 이 안 온다 — 그려진 것만으로 진행
            page.wait_for_timeout(2000)
            p.url = page.url
            say(f"  열림: {p.url}")

            body = _clean(page.inner_text("body"))
            picked = _pick_images(page)
            p.images = [it["src"] for it in picked]

            _BAD_NAMES = ("네이버 브랜드 커넥트", "네이버쇼핑")
            # 실측(2026-08-30): og:title/page.title() 을 먼저 쓰면 사이트 태그라인
            # ("몽디에스 | 1등 아기화장품, ...")이 먼저 잡혀, 뒤 루프의 가드
            # (2개 문자열만 걸러냄)를 통과해버려 더 구체적인 h1/상품명 셀렉터를
            # 아예 시도하지 않았다. 그래서 구체적인 후보부터 순서대로 찾고,
            # 다 실패했을 때만 og:title/title() 로 내려간다.
            p.name = ""
            for sel in ("h1", '[class*="productName"]', '[class*="product_name"]',
                        "h2", "h3", '[class*="title"]'):
                el = page.query_selector(sel)
                if el:
                    t = _clean(el.inner_text())
                    if 4 < len(t) < 120 and t not in _BAD_NAMES:
                        p.name = t
                        break
            if not p.name and picked and picked[0]["alt"] and 4 < len(picked[0]["alt"]) < 120:
                # 셀렉터가 다 실패해도, 대표 사진의 alt 에 상품명이 들어 있는 경우가 많다
                # (카페24 등은 상품 이미지 alt 를 상품명으로 채운다).
                p.name = picked[0]["alt"]
            if not p.name:
                p.name = _meta(page, "og:title") or _clean(page.title())

            p.price, p.sale_price = _prices(page)

            m = re.search(r"(\d\.\d{1,2})\s*\|?\s*리뷰\s*([\d,]+)", body)
            if m:
                p.rating, p.review_count = m.group(1), m.group(2)

            say(f"  상품: {p.name[:50]} / 사진 {len(p.images)}장")
        finally:
            ctx.close()
            browser.close()
    return p


def download(image_url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(image_url, headers={"User-Agent": _UA,
                                                     "Referer": "https://shopping.naver.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()
