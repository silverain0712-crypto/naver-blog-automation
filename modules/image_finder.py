"""주제/글감으로 Unsplash 스톡 사진 검색 또는 DALL-E 3 이미지 생성.

사용 흐름:
  - 사진 없음 + 생성 트리거 없음 → Unsplash 스톡 검색 (무료)
  - 사진 없음 + "이미지 만들어줘" 등 트리거   → DALL-E 3 생성 (유료)
  - 사진 있음 + 트리거                         → DALL-E 3 추가 생성 후 기존에 append
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

import config

# 메모/키워드에서 DALL-E 생성 요청을 감지할 문구
_GENERATE_TRIGGERS = [
    "이미지 만들어", "이미지 생성", "이미지 추가", "이미지 그려",
    "그림 만들어", "사진 만들어", "사진 생성", "ai 이미지", "달리", "dall-e",
]

# 생성 요청 문구를 메모에서 제거할 패턴
_CLEAN_PATTERNS = [
    r"이미지\s*(만들어|생성|추가|그려)\s*(주세요|부탁해?|해줘|줘|해)?",
    r"그림\s*만들어\s*(주세요|부탁해?|해줘|줘|해)?",
    r"사진\s*(만들어|생성)\s*(주세요|부탁해?|해줘|줘|해)?",
    r"(ai|달리|dall-?e)\s*이미지",
]


def detect_generation_request(memo: str, keyword: str) -> bool:
    """메모/키워드에 DALL-E 생성 요청이 포함되어 있는지 확인."""
    text = (memo + " " + keyword).lower()
    return any(t in text for t in _GENERATE_TRIGGERS)


def clean_generation_request(text: str) -> str:
    """메모에서 이미지 생성 요청 문구를 제거한다."""
    for pattern in _CLEAN_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


def _call_claude_text(prompt: str, max_tokens: int = 100) -> str:
    from modules.llm import get_client
    client = get_client()
    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def _unsplash_keywords(keyword: str, memo: str) -> str:
    """Claude로 Unsplash 검색용 영어 키워드 2~3개 생성."""
    return _call_claude_text(
        f"다음 블로그 글감에서 Unsplash 스톡 사진 검색에 쓸 영어 키워드 2~3개를 추출하라.\n"
        f"키워드: {keyword}\n글감: {memo[:300]}\n\n"
        "감성적이고 라이프스타일 느낌의 영어 단어만 공백으로 구분해서 반환하라. 예: baby cafe dessert",
        max_tokens=60,
    )


def _dalle_prompt(keyword: str, memo: str) -> str:
    """Claude로 DALL-E 3용 영어 프롬프트 생성."""
    return _call_claude_text(
        f"다음 한국 육아 라이프스타일 블로그 글감으로 DALL-E 3 이미지 생성 프롬프트를 영어로 작성하라.\n"
        f"키워드: {keyword}\n글감: {memo[:300]}\n\n"
        "따뜻하고 자연스러운 사진 스타일, 고품질, 텍스트·워터마크 없음. 200자 이내 영어로만 반환하라.",
        max_tokens=220,
    )


def _download(url: str, timeout: int = 20) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def search_unsplash_query(query: str) -> bytes | None:
    """이미 정해진 영어 검색어 하나로 Unsplash 사진 1장(bytes). 결과 없으면 None.

    search_unsplash() 와 달리 Claude 로 검색어를 다시 뽑지 않는다 — 호출부가 이미
    구체적인 영어 쿼리를 갖고 있을 때(예: modules/illustration_planner.py) 쓴다.
    """
    if not config.UNSPLASH_ACCESS_KEY:
        raise ValueError("UNSPLASH_ACCESS_KEY 가 설정되지 않았습니다.")
    url = (
        "https://api.unsplash.com/search/photos"
        f"?query={urllib.parse.quote(query)}&per_page=1&orientation=portrait&content_filter=high"
    )
    req = urllib.request.Request(url, headers={
        "Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}",
        "Accept-Version": "v1",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    results = data.get("results", [])
    if not results:
        return None
    return _download(results[0]["urls"]["regular"])


_KOREAN_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "has_person": {"type": "boolean"},
        "looks_korean_or_asian": {"type": "boolean"},
    },
    "required": ["has_person", "looks_korean_or_asian"],
    "additionalProperties": False,
}


def _looks_korean_or_no_person(image_bytes: bytes) -> bool:
    """사진에 사람이 없거나, 있다면 동아시아(한국인으로 보일 법한) 인상이면 True.

    2026-09-14: 육아 블로그용 실사 스톡이 서구권 인물 위주로 나온다는 피드백 →
    검색어에 "korean"을 붙이는 것만으론 보장이 안 돼(Unsplash에 인종 필터가 없다),
    후보 사진을 비전으로 한 번 더 걸러서 명백히 서구권 인물인 사진은 버린다."""
    from modules.llm import call_json, prepare_image_block
    try:
        result = call_json(
            model=config.VISION_MODEL,
            system=(
                "이 사진에 사람이 뚜렷한 주요 피사체로 등장하는지 보고, 등장한다면 "
                "동아시아인(한국인처럼 보일 법한) 인상인지 판단하라. 사람이 없거나 "
                "멀리 흐릿하게만 보이면 has_person=false. 서구권 백인/흑인 등 명백히 "
                "동아시아인으로 보기 어려운 인물이 주요 피사체면 looks_korean_or_asian=false."
            ),
            content=[prepare_image_block(image_bytes), {"type": "text", "text": "판단하라."}],
            schema=_KOREAN_CHECK_SCHEMA,
            max_tokens=50,
        )
        return (not result.get("has_person")) or bool(result.get("looks_korean_or_asian"))
    except Exception:
        return True  # 판정 실패 시 통과(스톡 자체를 못 쓰게 막지는 않는다)


def search_unsplash_korean(query: str, candidates: int = 2) -> bytes | None:
    """search_unsplash_query() 의 인종 필터 버전. 후보 여러 장을 받아 비전으로

    '사람이 없거나 동아시아인으로 보이는' 첫 사진을 고른다. 전부 탈락하거나 결과가
    없으면 None(호출부가 AI 생성으로 대체 — 이땐 얼굴을 한국인으로 직접 그린다).

    2026-09-20: 후보 4→2 로 낮췄다(카드 1장당 비전 판정 비용 절반). 검색어에 이미
    "korean"을 강제로 붙이고 있어(위) 후보 자체가 애초에 한국 관련 사진 위주로 오므로,
    2장으로도 판정 성공률은 크게 안 떨어질 걸로 본다 — 못 찾으면 AI 생성으로 자연스럽게
    폴백된다(다른 API라 이 비용 절감과는 무관)."""
    if not config.UNSPLASH_ACCESS_KEY:
        return None
    query = query if re.search(r"korean|south korea|\basian\b", query, re.IGNORECASE) else f"{query} korean"
    url = (
        "https://api.unsplash.com/search/photos"
        f"?query={urllib.parse.quote(query)}&per_page={candidates}&orientation=portrait&content_filter=high"
    )
    req = urllib.request.Request(url, headers={
        "Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}",
        "Accept-Version": "v1",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    for p in data.get("results", [])[:candidates]:
        img = _download(p["urls"]["regular"])
        if _looks_korean_or_no_person(img):
            return img
    return None


def search_unsplash(keyword: str, memo: str, count: int = 3) -> list[bytes]:
    """Unsplash 스톡 사진 검색 후 이미지 bytes 리스트 반환."""
    if not config.UNSPLASH_ACCESS_KEY:
        raise ValueError("UNSPLASH_ACCESS_KEY 가 설정되지 않았습니다. .env 또는 Streamlit secrets에 추가해주세요.")

    en_kw = _unsplash_keywords(keyword, memo)
    query = urllib.parse.quote(en_kw)
    url = (
        f"https://api.unsplash.com/search/photos"
        f"?query={query}&per_page={count}&orientation=landscape&content_filter=high"
    )

    req = urllib.request.Request(url, headers={
        "Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}",
        "Accept-Version": "v1",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    results_data = data.get("results", [])
    if not results_data:
        raise ValueError(f"Unsplash에서 '{en_kw}' 결과가 없습니다. 키워드를 바꿔 보세요.")

    # regular: 1080px급 / full: 원본 — 블로그용으로 regular 적합
    return [_download(p["urls"]["regular"]) for p in results_data[:count]]


def generate_dalle(keyword: str, memo: str, count: int = 2) -> list[bytes]:
    """DALL-E 3로 블로그용 이미지 생성 후 bytes 리스트 반환."""
    if not config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY 가 설정되지 않았습니다. .env 또는 Streamlit secrets에 추가해주세요.")

    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    prompt = _dalle_prompt(keyword, memo)
    results = []
    for _ in range(min(count, 2)):  # DALL-E 3는 n=1만 지원 → 루프로 처리
        resp = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            n=1,
            size="1024x1024",
            quality="standard",
        )
        results.append(_download(resp.data[0].url, timeout=30))
    return results


def fetch_images(keyword: str, memo: str, use_dalle: bool) -> tuple[list[bytes], str]:
    """스톡 검색 또는 DALL-E 3 생성으로 이미지를 가져온다.

    Returns:
        (images_bytes_list, source_label)  # source_label: UI 표시용
    """
    if use_dalle:
        imgs = generate_dalle(keyword, memo, count=2)
        return imgs, "DALL-E 3 생성"
    else:
        imgs = search_unsplash(keyword, memo, count=3)
        return imgs, "Unsplash 스톡"
