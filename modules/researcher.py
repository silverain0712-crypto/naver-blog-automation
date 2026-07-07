"""웹 검색으로 블로그 글의 '사실'을 보강한다(사용자 글감만으로는 정보가 얕은 문제 해결).

동작: Claude 웹 검색 서버툴로 주제 관련 사실·절차·수치·공식 정보를 찾아 한국어 메모로 정리.
그 메모를 post_generator 가 시스템 프롬프트에 '[웹 리서치로 확인한 사실]' 로 주입해 본문에 녹인다.
모델이 스스로 판단해 '필요할 때만' 검색한다(개인 경험·감상만인 글은 '보강 불필요' 로 건너뜀).

주의: 웹 검색은 서버툴이라 한 번의 messages.create 로 검색+정리가 끝난다(클라이언트 루프 불필요).
      output_config(JSON 강제) 와 함께 못 쓰므로 리서치는 일반 텍스트 응답으로 받는다.
"""

from __future__ import annotations

import config
from modules.llm import get_client, _RETRYABLE

_SKIP_MARK = "보강 불필요"
_MAX_SEARCHES = 5

_SYSTEM = (
    "너는 네이버 블로그 글의 '사실 보강'을 맡은 리서처다. 아래 글감(주제·키워드·메모)을 보고, "
    "독자에게 실제로 도움이 되는 사실 정보를 웹에서 찾아 한국어로 간결히 정리한다.\n"
    "\n"
    "찾을 것: 절차·방법(예: 신고 절차, 접수 방법), 필요한 준비물/서류, 수치·기준(요금·기한·한도·규정), "
    "공식 창구/링크(항공사·기관 공식 안내), 최신 변경사항, 알아두면 좋은 팁·주의사항.\n"
    "규칙:\n"
    "- 반드시 웹 검색으로 확인된 사실만. 추측·불확실·광고성 내용은 넣지 마라.\n"
    "- 개인 경험·감상·문체는 쓰지 마라(그건 본문 작성 단계에서 한다). 너는 '사실 재료'만 제공한다.\n"
    "- 한국 독자 기준(한국어 출처 우선). 항목별로 짧은 불릿으로, 각 항목 끝에 (출처: 사이트명) 표기.\n"
    "- 전체 800자 내외로 압축. 핵심만.\n"
    f"- 이 글이 순수 개인 경험/감상 위주라 웹 사실 보강이 무의미하면, 다른 말 없이 정확히 '{_SKIP_MARK}' 한 줄만 출력하라."
)


def research(*, keyword: str, memo: str, structure_label: str = "", guideline: str = "") -> str:
    """글감으로 웹 검색 사실 보강 메모를 만든다. 필요 없거나 실패하면 빈 문자열."""
    if not config.ENABLE_RESEARCH or not config.ANTHROPIC_API_KEY:
        return ""

    prompt = (
        f"[글 유형] {structure_label or '(미지정)'}\n"
        f"[핵심 키워드] {keyword or '(없음 — 메모에서 주제 파악)'}\n"
        f"[사용자 메모]\n{(memo or '').strip() or '(메모 없음)'}\n"
    )
    if guideline.strip():
        prompt += f"\n[참고 자료(협찬 가이드/제품 설명서 일부)]\n{guideline.strip()[:1500]}\n"
    prompt += "\n위 주제로 블로그 독자에게 유용한 사실 정보를 웹에서 찾아 정리해줘."

    client = get_client()
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": _MAX_SEARCHES}]

    last = None
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=config.RESEARCH_MODEL,
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
            # 리서치는 부가 기능 — 실패해도 본문 생성은 계속되게 조용히 포기.
            print(f"  (웹 리서치 건너뜀: {str(e)[:80]})")
            return ""
    if last:
        print(f"  (웹 리서치 재시도 실패, 건너뜀: {str(last)[:80]})")
    return ""
