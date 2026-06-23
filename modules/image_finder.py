"""주제/글감으로 Pexels 스톡 사진 검색 또는 DALL-E 3 이미지 생성.

사용 흐름:
  - 사진 없음 + 생성 트리거 없음 → Pexels 스톡 검색 (무료)
  - 사진 없음 + "이미지 만들어줘" 등 트리거   → DALL-E 3 생성 (유료)
  - 사진 있음 + 트리거                         → DALL-E 3 추가 생성 후 기존에 append
"""

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


def _pexels_keywords(keyword: str, memo: str) -> str:
    """Claude로 Pexels 검색용 영어 키워드 2~3개 생성."""
    return _call_claude_text(
        f"다음 블로그 글감에서 Pexels 스톡 사진 검색에 쓸 영어 키워드 2~3개를 추출하라.\n"
        f"키워드: {keyword}\n글감: {memo[:300]}\n\n"
        "영어 단어만 공백으로 구분해서 반환하라. 예: baby cafe dessert",
        max_tokens=60,
    )


def _dalle_prompt(keyword: str, memo: str) -> str:
    """Claude로 DALL-E 3용 영어 프롬프트 생성."""
    return _call_claude_text(
        f"다음 한국 라이프스타일 블로그 글감으로 DALL-E 3 이미지 생성 프롬프트를 영어로 작성하라.\n"
        f"키워드: {keyword}\n글감: {memo[:300]}\n\n"
        "자연스럽고 따뜻한 사진 스타일, 텍스트·워터마크 없음. 200자 이내 영어로만 반환하라.",
        max_tokens=220,
    )


def _download(url: str, timeout: int = 20) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def search_pexels(keyword: str, memo: str, count: int = 3) -> list[bytes]:
    """Pexels 스톡 사진 검색 후 이미지 bytes 리스트 반환."""
    if not config.PEXELS_API_KEY:
        raise ValueError("PEXELS_API_KEY 가 설정되지 않았습니다. .env 또는 Streamlit secrets에 추가해주세요.")

    en_kw = _pexels_keywords(keyword, memo)
    query = urllib.parse.quote(en_kw)
    url = f"https://api.pexels.com/v1/search?query={query}&per_page={count}&orientation=landscape"

    req = urllib.request.Request(url, headers={"Authorization": config.PEXELS_API_KEY})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    photos = data.get("photos", [])
    if not photos:
        raise ValueError(f"Pexels에서 '{en_kw}' 결과가 없습니다. 키워드를 바꿔 보세요.")

    return [_download(p["src"]["large"]) for p in photos[:count]]


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
        imgs = search_pexels(keyword, memo, count=3)
        return imgs, "Pexels 스톡"
