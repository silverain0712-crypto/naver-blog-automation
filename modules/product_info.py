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

# 상품 사진 호스트. 로고·아이콘과 섞이지 않게 화면에 크게 그려진 것만 골라낸다.
_IMG_HOSTS = ("pstatic.net", "phinf", "naver.net")
_MIN_EDGE = 180


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


def _pick_images(page) -> list[str]:
    """상품 사진 URL 을 대표컷부터 돌려준다.

    og:image 가 대표컷이다. 나머지는 DOM 순서를 따른다 — 갤러리가 상세페이지보다
    먼저 그려지므로, 면적순으로 정렬하면 오히려 상세페이지 배너가 앞으로 온다.
    """
    raw = page.evaluate(
        """() => Array.from(document.images).map(img => ({
             src: img.currentSrc || img.src,
             w: img.naturalWidth, h: img.naturalHeight,
             dw: img.width, dh: img.height,
           }))"""
    )
    out: list[str] = []
    seen: set[str] = set()

    def add(src: str) -> None:
        if not src or not src.startswith("http"):
            return
        # 네이버는 ?type=f120 같은 리사이즈 쿼리를 붙인다 — 원본 크기로 되돌린다.
        src = re.sub(r"\?type=\w+$", "", src)
        if src in seen:
            return
        seen.add(src)
        out.append(src)

    add(_meta(page, "og:image"))
    for it in raw:
        src = it.get("src") or ""
        if not any(h in src for h in _IMG_HOSTS):
            continue
        # 로고·배너는 한 변이 짧다(400x80 등). 상품 사진은 정사각에 가깝고 크다.
        if min(it.get("w") or 0, it.get("h") or 0) < _MIN_EDGE:
            continue
        if min(it.get("dw") or 0, it.get("dh") or 0) < 80:
            continue
        add(src)
    return out


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

            p.name = _meta(page, "og:title") or _clean(page.title())
            # 브랜드커넥트/스마트스토어는 og 태그가 없다 — 화면 텍스트에서 뽑는다.
            for sel in ("h1", "h2", "h3", '[class*="productName"]', '[class*="product_name"]',
                        '[class*="title"]'):
                if p.name and p.name not in ("네이버 브랜드 커넥트", "네이버쇼핑"):
                    break
                el = page.query_selector(sel)
                if el:
                    t = _clean(el.inner_text())
                    if 4 < len(t) < 120:
                        p.name = t

            p.price, p.sale_price = _prices(page)

            m = re.search(r"(\d\.\d{1,2})\s*\|?\s*리뷰\s*([\d,]+)", body)
            if m:
                p.rating, p.review_count = m.group(1), m.group(2)

            p.images = _pick_images(page)
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
