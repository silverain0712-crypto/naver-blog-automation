"""발행한 네이버 블로그 글을 가져와 style_samples 로 저장(지속 학습용).

네이버는 WebFetch 가 막혀 있어 curl(모바일/RSS)로 가져온다.
RSS 로 최근 글 목록을 얻고, 각 글은 모바일 페이지에서 본문(se-text-paragraph)을 추출한다.
저장 파일명: published_<logno>.txt  (이미 있으면 건너뜀)
"""

import html
import re
import subprocess

import config

_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def _curl(url: str) -> str:
    try:
        r = subprocess.run(
            ["curl", "-sL", "-A", _UA, "--max-time", "25", url],
            capture_output=True,
        )
        return r.stdout.decode("utf-8", "ignore")
    except Exception:
        return ""


def _cdata(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, flags=re.S)
    if not m:
        return ""
    v = m.group(1).strip()
    cd = re.match(r"<!\[CDATA\[(.*?)\]\]>", v, flags=re.S)
    return (cd.group(1) if cd else v).strip()


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).replace("​", "").strip()


def list_recent(blog_id: str, limit: int = 10) -> list[tuple[str, str]]:
    """RSS 에서 (logno, title) 최근 목록."""
    raw = _curl(f"https://rss.blog.naver.com/{blog_id}.xml")
    out = []
    for block in re.findall(r"<item>(.*?)</item>", raw, flags=re.S):
        link = _cdata(block, "link")
        title = _clean(_cdata(block, "title"))
        m = re.search(r"/(\d{6,})", link)
        if m:
            out.append((m.group(1), title))
    return out[:limit]


def fetch_body(blog_id: str, logno: str) -> str:
    """모바일 글 페이지에서 본문 텍스트 추출."""
    raw = _curl(f"https://m.blog.naver.com/{blog_id}/{logno}")
    paras = re.findall(
        r'<p[^>]*class="[^"]*se-text-paragraph[^"]*"[^>]*>(.*?)</p>', raw, flags=re.S
    )
    lines = [t for t in (_clean(p) for p in paras) if t]
    return "\n".join(lines)


def sync(blog_id: str, limit: int = 5) -> dict:
    """최근 발행 글을 style_samples 로 저장. 이미 있는 글은 건너뜀.

    반환: {'added': [제목...], 'skipped': n, 'errors': [logno...]}
    """
    config.STYLE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    added, skipped, errors = [], 0, []
    for logno, title in list_recent(blog_id, limit):
        path = config.STYLE_SAMPLES_DIR / f"published_{logno}.txt"
        if path.exists():
            skipped += 1
            continue
        body = fetch_body(blog_id, logno)
        if len(body) >= 200:
            path.write_text(body, encoding="utf-8")
            added.append(title or logno)
        else:
            errors.append(logno)
    return {"added": added, "skipped": skipped, "errors": errors}
