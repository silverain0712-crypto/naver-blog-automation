"""상위노출 벤치마킹 — 글감으로 타깃 검색 키워드를 정하고, 그 키워드의 네이버 블로그
상위노출 글(잘 쓴 글) 몇 개를 웹검색으로 찾아 '구조·제목공식·정보요소'를 분석한 뒤,
내 글이 이길 '차별화 포인트'까지 뽑아 post_generator 에 주입한다.

researcher(사실 보강)와는 역할이 다르다:
- researcher = '무엇을 쓸까'의 사실 재료(수치·절차·공식정보).
- benchmark  = '어떻게 이길까'의 구조/SEO 설계(상위글 공통 뼈대 + 차별화 갭).

동작: Claude 웹 검색 서버툴로 한 번의 messages.create 안에서 검색+분석을 끝낸다.
      (naver 스크래핑은 광고/리다이렉트로 조용히 깨지기 쉬워, 검증된 web_search 서버툴을 씀.)
      실패해도 본문 생성은 계속되도록 조용히 빈 문자열로 포기한다.
"""

from __future__ import annotations

import config
from modules.llm import get_client, _RETRYABLE

_SKIP_MARK = "벤치마킹 불필요"
_MAX_SEARCHES = 6

_SYSTEM = (
    "너는 네이버 블로그 상위노출(SEO)을 분석하는 컨설턴트다. 아래 글감을 보고 "
    "이 글이 노려야 할 '타깃 검색 키워드'를 정한 뒤, 그 키워드로 네이버 블로그에서 "
    "실제로 잘 쓰고 상위에 노출되는 글 5개 안팎을 웹 검색으로 찾아 구조를 분석한다.\n"
    "검색 팁: 키워드에 '후기/추천/비교/총정리'를 붙이거나 'site:blog.naver.com' 을 활용해 "
    "네이버 블로그 상위 글 위주로 찾아라. 정보성/장소 글이면 롱테일(세부 수식어) 조합도 검색하라.\n"
    "\n"
    "분석해서 아래 항목을 한국어로 간결히 정리한다(전체 500~750자, 항목별 짧은 불릿):\n"
    "1) [타깃 키워드] 메인 키워드 1개 + 함께 노릴 롱테일/보조 키워드 3~5개(세부 수식어·연관어).\n"
    "2) [제목 공식] 상위글 제목들의 공통 패턴 — 어떤 세부 차별 키워드를 앞에 박는지, "
    "후킹어(후기·추천·비교·총정리)는 무엇을 쓰는지. 실제 예시 제목 2개 인용.\n"
    "3) [글 구조] 상위글들이 공통으로 쓰는 소제목/섹션 구성과 순서(도입 방식 포함).\n"
    "4) [필수 정보요소] 상위글이 빠짐없이 담는 정보(스펙/수치/성분/운영시간·요금·주차 등)와 "
    "표(비교·요금·스펙) 사용 여부.\n"
    "5) [분량대] 상위글 대략 글자수(공백 제외) 범위.\n"
    "6) [차별화 기회 — 가장 중요] 상위글들이 약하거나 안 다루는 것, 겹쳐서 식상한 것. "
    "내 글이 '이것'으로 이긴다: 추가로 담을 정보·각도·표를 2~3개 구체적으로 제안.\n"
    "\n"
    "규칙:\n"
    "- 실제 웹 검색으로 확인한 상위 글 기반으로만 분석하라. 추측으로 지어내지 마라.\n"
    "- 문체·감상은 쓰지 마라. 너는 '구조 설계도'만 준다(본문 작성은 다음 단계에서 한다).\n"
    f"- 글감이 순수 개인 일기·감상이라 상위노출 경쟁이 무의미하면, 다른 말 없이 정확히 "
    f"'{_SKIP_MARK}' 한 줄만 출력하라."
)


def benchmark(*, keyword: str, memo: str, structure_label: str = "", guideline: str = "") -> str:
    """글감으로 상위노출 벤치마킹 메모를 만든다. 필요 없거나 실패하면 빈 문자열."""
    if not config.ENABLE_BENCHMARK or not config.ANTHROPIC_API_KEY:
        return ""

    prompt = (
        f"[글 유형] {structure_label or '(미지정)'}\n"
        f"[핵심 키워드] {keyword or '(없음 — 메모/제품에서 타깃 키워드를 네가 정하라)'}\n"
        f"[사용자 메모]\n{(memo or '').strip() or '(메모 없음)'}\n"
    )
    if guideline.strip():
        prompt += f"\n[참고 자료(협찬 가이드/제품명·스펙 일부)]\n{guideline.strip()[:1200]}\n"
    prompt += (
        "\n위 글감으로 네이버 블로그 상위노출 글을 검색·분석해서, 내 글이 상위에 오르도록 "
        "따라야 할 구조와 이길 차별화 포인트를 정리해줘."
    )

    client = get_client()
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": _MAX_SEARCHES}]

    last = None
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=config.BENCHMARK_MODEL,
                max_tokens=2000,
                system=_SYSTEM,
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                tools=tools,
            )
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            ).strip()
            if not text or text.startswith(_SKIP_MARK):
                return ""
            return text
        except _RETRYABLE as e:
            last = e
        except Exception as e:
            # 벤치마킹은 부가 기능 — 실패해도 본문 생성은 계속되게 조용히 포기.
            print(f"  (상위노출 벤치마킹 건너뜀: {str(e)[:80]})")
            return ""
    if last:
        print(f"  (상위노출 벤치마킹 재시도 실패, 건너뜀: {str(last)[:80]})")
    return ""
