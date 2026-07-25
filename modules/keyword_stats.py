"""키워드별 월 검색량 + 블로그 문서수 조회(맥에서 실행).

두 개의 네이버 API 를 쓴다(둘은 발급처가 다르다):
- 월 검색량: 네이버 검색광고 API(keywordstool). 광고주 계정 필요.
  https://manage.searchad.naver.com → 도구 → API 사용 관리
  .env: NAVER_SEARCHAD_API_KEY / NAVER_SEARCHAD_SECRET / NAVER_SEARCHAD_CUSTOMER_ID
- 블로그 문서수: 네이버 검색 OpenAPI(blog.json 의 total). 개발자센터 앱 등록.
  https://developers.naver.com → 애플리케이션 등록
  .env: NAVER_OPENAPI_CLIENT_ID / NAVER_OPENAPI_CLIENT_SECRET

키가 없으면 예외 없이 빈 값을 돌려준다(앱은 값 없으면 표시만 생략).
결과: { "키워드": {"volume": 1234, "docs": 56789}, ... } (조회된 항목만).
'포화도'는 앱에서 docs/volume 로 계산해 보여줄 수 있다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import urllib.parse
import urllib.request

_SEARCHAD_BASE = "https://api.searchad.naver.com"
_OPENAPI_BLOG = "https://openapi.naver.com/v1/search/blog.json"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def searchad_enabled() -> bool:
    return bool(_env("NAVER_SEARCHAD_API_KEY")
                and _env("NAVER_SEARCHAD_SECRET")
                and _env("NAVER_SEARCHAD_CUSTOMER_ID"))


def openapi_enabled() -> bool:
    return bool(_env("NAVER_OPENAPI_CLIENT_ID")
                and _env("NAVER_OPENAPI_CLIENT_SECRET"))


def _to_int(v) -> int | None:
    """검색광고 API 는 소량일 때 '< 10' 문자열을 준다 → 10 으로 근사, 그 외 숫자화."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").strip()
    if s.startswith("<"):
        return 10
    return int(s) if s.isdigit() else None


def _searchad_signature(ts: str, method: str, path: str, secret: str) -> str:
    msg = f"{ts}.{method}.{path}"
    digest = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def fetch_search_volume(keywords: list[str], timeout: float = 8.0) -> dict[str, int]:
    """키워드별 월 검색량(PC+모바일 합). 검색광고 keywordstool 사용.

    keywordstool 은 hintKeywords 를 최대 5개까지 받는다(공백은 제거해 보낸다).
    반환은 조회에 성공한 키워드만.
    """
    if not searchad_enabled() or not keywords:
        return {}
    api_key = _env("NAVER_SEARCHAD_API_KEY")
    secret = _env("NAVER_SEARCHAD_SECRET")
    customer = _env("NAVER_SEARCHAD_CUSTOMER_ID")

    # keywordstool 은 키워드 내 공백을 무시하므로, 공백 제거본→원본 매핑으로 되돌린다.
    nospace = {k.replace(" ", ""): k for k in keywords if k.strip()}
    hints = list(nospace.keys())[:5]
    path = "/keywordstool"
    query = urllib.parse.urlencode({"hintKeywords": ",".join(hints), "showDetail": "1"})
    ts = str(int(time.time() * 1000))
    req = urllib.request.Request(
        f"{_SEARCHAD_BASE}{path}?{query}",
        headers={
            "X-Timestamp": ts,
            "X-API-KEY": api_key,
            "X-Customer": customer,
            "X-Signature": _searchad_signature(ts, "GET", path, secret),
        },
    )
    out: dict[str, int] = {}
    try:
        import json
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        for item in data.get("keywordList", []):
            rel = str(item.get("relKeyword", "")).replace(" ", "")
            if rel not in nospace:
                continue
            pc = _to_int(item.get("monthlyPcQcCnt")) or 0
            mo = _to_int(item.get("monthlyMobileQcCnt")) or 0
            out[nospace[rel]] = pc + mo
    except Exception as e:
        print(f"  검색량 조회 실패(건너뜀): {str(e)[:100]}")
    return out


def fetch_doc_count(keyword: str, timeout: float = 8.0) -> int | None:
    """키워드의 블로그 문서수(blog.json 의 total)."""
    if not openapi_enabled() or not keyword.strip():
        return None
    query = urllib.parse.urlencode({"query": keyword, "display": "1"})
    req = urllib.request.Request(
        f"{_OPENAPI_BLOG}?{query}",
        headers={
            "X-Naver-Client-Id": _env("NAVER_OPENAPI_CLIENT_ID"),
            "X-Naver-Client-Secret": _env("NAVER_OPENAPI_CLIENT_SECRET"),
        },
    )
    try:
        import json
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(json.loads(resp.read().decode()).get("total", 0))
    except Exception as e:
        print(f"  문서수 조회 실패(건너뜀): {str(e)[:100]}")
        return None


def fetch_stats(keywords: list[str]) -> dict[str, dict]:
    """키워드 목록의 {volume, docs} 를 모아서 반환. 조회된 값만 채운다.

    검색량은 한 번의 호출로 여러 키워드를 받고, 문서수는 키워드마다 개별 호출한다.
    아무 키도 설정 안 됐으면 빈 dict.
    """
    keywords = [k for k in (keywords or []) if k and k.strip()]
    if not keywords:
        return {}
    volumes = fetch_search_volume(keywords)
    stats: dict[str, dict] = {}
    for k in keywords:
        entry = {}
        if k in volumes:
            entry["volume"] = volumes[k]
        docs = fetch_doc_count(k)
        if docs is not None:
            entry["docs"] = docs
        if entry:
            stats[k] = entry
    return stats
