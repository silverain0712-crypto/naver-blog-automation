"""NAEO 인용 조건 자동 검수 + 수치 밀도 보정.

prompts/naeo_rules.py 는 LLM 에게 '이렇게 써라'라고 시킬 뿐, 지켰는지는 세어보기 전엔 모른다.
실측(2026-08-15 NAEO 브리핑, sub=육아)에서 나온 조건 중 기계로 셀 수 있는 것만 여기서 검수하고,
미달이면 본문을 한 번 보정한다.

검수 항목(육아 소분류 처방):
- 숫자가 든 문장 비율 20% 이상(표가 있으면 10% 이상)  ← 가장 강한 인용 신호
- 표 최소 1개
- 소제목 3~6개
- 제목에 구체 숫자 1개
- 공백 제외 분량

보정(densify)은 '자료에 이미 있는 수치'로만 문장을 단정형으로 고쳐 쓴다.
없는 수치를 만들어내면 거짓 글이 되고 인용도 실패하므로, 재료를 함께 넘겨 그 안에서만 쓰게 한다.
"""

from __future__ import annotations

import re

import config
from modules.llm import call_json

_MEDIA_RE = re.compile(r"\[(?:사진|영상)\s*\d+\]")
_TABLE_RE = re.compile(r"\[표\].*?\[/표\]", re.DOTALL)
_QUOTE_TAG_RE = re.compile(r"\[/?인용\]")
_DIGIT_RE = re.compile(r"\d")

# 문장으로 세기엔 너무 짧은 조각(소제목·감탄사)은 분모에서 뺀다.
_MIN_SENTENCE_CHARS = 6

_RATIO_WITH_TABLE = 0.10
_RATIO_NO_TABLE = 0.20

# 상담형(증상·발달·문제해결) 글의 숫자 목표. v4 실측 근거:
# 네이버 메이트 4인 중 누적 인용 1위(39.2만)인 신생아 증상 글은 숫자 문장이 7%뿐이었다.
# 이런 글의 인용은 수치가 아니라 [전문가 소견 + 메커니즘 설명 + 경계선]이 만든다.
# 그래서 숫자 기준을 낮추는 대신 그 세 축이 실제로 있는지를 따로 센다.
_RATIO_CONSULT = 0.08

# 의료·발달 상담형인지 알아보는 신호어(본문에 있으면 그 성격의 글로 본다).
_CONSULT_HINTS = ("소아과", "신경외과", "정형외과", "이비인후과", "진료", "영유아검진",
                  "의사", "선생님께", "처방", "증상", "발달")
# 전문가 소견을 실제로 옮겨 적었는지.
# '라고/다고/이라고 하셨어요'를 모두 잡으려면 어미 앞을 열어둬야 한다.
_SOURCE_HINTS = ("고 하셨", "고 안내", "고 들었", "고 설명", "소견", "진단", "확인받")
# '이럴 땐 전문가에게' 경계선을 제시했는지.
_BOUNDARY_HINTS = ("진료를 받", "병원에 가", "병원을 방문", "전문가에게", "다시 진료",
                   "검진 때 물어", "상담을 받")

_SUBHEADING_MIN = 3
_SUBHEADING_MAX = 6


def _strip_markers(body: str) -> str:
    """자리표시 마커만 제거한다(인용구는 태그만 지우고 안의 문장은 본문으로 남긴다)."""
    return _QUOTE_TAG_RE.sub("", _MEDIA_RE.sub("", body or ""))


def content_len(body: str) -> int:
    """공백 제외 글자수. [사진N]/[영상N]/[인용] 자리표시·표 마크업·모든 공백을 뺀 순수 글자 수."""
    t = _strip_markers(body)
    t = t.replace("[표]", "").replace("[/표]", "").replace("|", "")
    return len("".join(t.split()))


def sentences(body: str) -> list[str]:
    """본문을 '문장' 단위로 쪼갠다(표 안 텍스트는 제외).

    비비 글은 한 줄에 한 생각을 쓰고 줄바꿈이 잦다. 그래서 줄을 1차 단위로 삼고,
    한 줄에 종결부호로 여러 문장이 들어간 경우만 더 쪼갠다.
    표 안의 숫자는 '문장 안 수치'가 아니므로 분자·분모 모두에서 뺀다.
    """
    t = _TABLE_RE.sub("", _strip_markers(body))
    out: list[str] = []
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?])\s+", line):
            part = part.strip()
            if len(part.replace(" ", "")) >= _MIN_SENTENCE_CHARS:
                out.append(part)
    return out


def is_consult(body: str) -> bool:
    """증상·발달·문제해결 상담형 글인지. 의료·발달 신호어가 2개 이상이면 그렇게 본다."""
    text = body or ""
    return sum(1 for h in _CONSULT_HINTS if h in text) >= 2


def audit(post: dict, *, length: int = 0) -> dict:
    """생성된 초안이 NAEO 인용 조건을 충족하는지 센다. issues 가 비어 있으면 통과."""
    body = post.get("body", "") or ""
    sents = sentences(body)
    numeric = [s for s in sents if _DIGIT_RE.search(s)]
    has_table = "[표]" in body and "[/표]" in body
    has_quote = "[인용]" in body and "[/인용]" in body
    consult = is_consult(body)
    has_source = any(h in body for h in _SOURCE_HINTS)
    has_boundary = any(h in body for h in _BOUNDARY_HINTS)
    # 숫자 기준을 깎아주는 건 '다른 축으로 인용을 만들었을 때'뿐이다.
    # 상담형이라고 선언만 하고 전문가 소견이 없으면, 그냥 수치도 근거도 없는 글이다.
    earned = consult and has_source
    target = _RATIO_CONSULT if earned else (_RATIO_WITH_TABLE if has_table else _RATIO_NO_TABLE)
    ratio = (len(numeric) / len(sents)) if sents else 0.0

    subs = [s for s in (post.get("subheadings") or []) if str(s).strip()]
    titles = [t for t in (post.get("title_candidates") or []) if str(t).strip()]
    titles_without_number = [t for t in titles if not _DIGIT_RE.search(t)]

    issues: list[str] = []
    if not has_table:
        issues.append("표 없음 — 조건·비교·가격·시간 중 하나를 [표]로 넣어야 한다")
    if not has_quote:
        issues.append("인용구 없음 — 서론 직후 핵심 결론 선요약을 [인용]으로 넣어야 한다")
    if ratio < target:
        issues.append(
            f"숫자 문장 비율 {ratio:.0%} (목표 {target:.0%}) — "
            f"{len(sents)}문장 중 {len(numeric)}개만 수치를 담고 있다"
        )
    if consult:
        if not has_source:
            issues.append(
                "전문가 소견 없음 — 증상·발달 글은 '소아과에서는 ~라고 하셨어요'처럼 "
                "누가 무엇을 말했는지 옮겨 적어야 인용된다"
            )
        if not has_boundary:
            issues.append(
                "경계선 없음 — '이럴 땐 병원에 가보세요' 조건을 2~4개 넣어야 한다. "
                "괜찮다고만 쓴 글은 답변으로 쓰이지 못한다"
            )

    if subs and len(subs) > _SUBHEADING_MAX:
        issues.append(f"소제목 {len(subs)}개 — {_SUBHEADING_MAX}개 이하로 합쳐야 한다")
    if subs and len(subs) < _SUBHEADING_MIN:
        issues.append(f"소제목 {len(subs)}개 — 최소 {_SUBHEADING_MIN}개 필요")
    if titles and len(titles_without_number) == len(titles):
        issues.append("제목 후보에 구체 숫자(금액·월령·개수·연도)가 하나도 없다")

    clen = content_len(body)
    if length and clen < length:
        issues.append(f"분량 {clen}자 (목표 {length}자)")

    return {
        "content_len": clen,
        "sentence_count": len(sents),
        "numeric_sentences": len(numeric),
        "consult": consult,
        "consult_source": has_source,
        "numeric_ratio": round(ratio, 3),
        "numeric_target": target,
        "has_table": has_table,
        "has_quote": has_quote,
        "subheading_count": len(subs),
        "titles_with_number": len(titles) - len(titles_without_number),
        "issues": issues,
    }


_DENSIFY_SCHEMA = {
    "type": "object",
    "properties": {"body": {"type": "string"}},
    "required": ["body"],
    "additionalProperties": False,
}


def densify(
    *,
    system: str,
    body: str,
    issues: list[str],
    sources: str,
    need_table: bool,
    need_numbers: bool,
    need_quote: bool = False,
) -> str:
    """NAEO 조건 미달 본문을 한 번 보정한다. 실패하면 원문을 그대로 돌려준다."""
    if not issues:
        return body

    asks = ["아래 [지적사항]만 고쳐라. 나머지 문장·구성·톤·분량은 그대로 둔다."]
    if need_numbers:
        asks.append(
            "핵심: 감상·뭉개기 문장을 '조건 + 수치 + 결과'가 한 문장에 다 들어간 단정형 문장으로 "
            "바꿔라. 예) '금방 익더라구요' → '중불에서 4분 끓이니 고기가 다 익었어요'. "
            "바꿀 문장은 각 섹션에서 가장 정보성이 큰 것부터 고른다."
        )
    if need_table:
        asks.append(
            "본문에서 표로 보면 명확한 정보(비교·가격·시간·월령 기준·구성품)를 찾아 "
            "[표]...[/표] 블록 1개를 알맞은 위치에 넣어라. 형식은 정확히 3칸(3열), "
            "첫 행은 머리글, 데이터 2행 이상. 표 안에 [사진N]을 넣지 마라. "
            "표에 넣을 근거가 아래 [사용 가능한 재료]에 없으면 표를 만들지 말고 넘어가라."
        )
    if need_quote:
        asks.append(
            "도입부(첫 소제목 직전)에 [인용]...[/인용] 블록 1개를 넣어라. 내용은 이 글의 "
            "핵심 결론을 먼저 밝히는 1~2문장이고, 가능하면 수치를 포함한다"
            "(예: '[인용]119,000원짜리 둑티그는 조립에 1시간 30분 걸렸지만, "
            "16개월 뽀식이가 매일 아침 먼저 달려가는 장난감이 됐어요.[/인용]'). "
            "본문에 이미 있는 문장을 옮겨오지 말고, 결론을 새로 압축해서 써라."
        )

    instruction = (
        "아래는 네가 방금 쓴 블로그 초안 본문이다. NAEO 인용 조건 검수에서 걸린 항목이 있다.\n"
        + "\n".join(f"- {a}" for a in asks)
        + "\n\n[절대 규칙]\n"
        "- 아래 [사용 가능한 재료]에 없는 수치·사실을 절대 지어내지 마라. "
        "재료에 근거가 없으면 그 문장은 손대지 말고 그대로 둬라(억지로 숫자를 만들면 거짓 글이 된다).\n"
        "- [사진N]·[영상N] 자리표시는 개수와 순서를 그대로 유지하라(지우거나 새로 만들지 마라).\n"
        "- 소제목은 개수·문구를 그대로 유지하라(새 소제목 금지).\n"
        "- 의료·발달·효능·안전 판단은 계속 단정하지 마라. 단정형으로 바꿀 대상은 "
        "가격·시간·크기·용량·월령·횟수·절차 같은 '확인 가능한 사실'뿐이다.\n"
        "- 같은 종결어미가 3문장 연속되지 않게 하라.\n"
        "- body 만 다시 써서 반환하라(전체 본문).\n\n"
        f"[지적사항]\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"
        f"[사용 가능한 재료 — 수치는 여기서만 가져온다]\n{sources.strip() or '(재료 없음)'}\n\n"
        f"[현재 초안 본문]\n{body}"
    )

    try:
        result = call_json(
            model=config.NAEO_AUDIT_MODEL,
            system=system,
            content=[{"type": "text", "text": instruction}],
            schema=_DENSIFY_SCHEMA,
            max_tokens=16000,
        )
    except Exception as e:
        # 보정은 부가 단계 — 실패해도 초안은 그대로 살린다.
        print(f"  (NAEO 밀도 보정 건너뜀: {str(e)[:80]})")
        return body

    return result.get("body") or body


def format_issues(result: dict) -> str:
    """앱/로그에 보여줄 한 줄 요약."""
    if not result.get("issues"):
        return (
            f"NAEO 검수 통과 (숫자 문장 {result['numeric_ratio']:.0%}"
            f"/목표 {result['numeric_target']:.0%}, 표 "
            f"{'있음' if result['has_table'] else '없음'}, 인용구 "
            f"{'있음' if result.get('has_quote') else '없음'}, "
            f"소제목 {result['subheading_count']}개, {result['content_len']}자)"
        )
    return "NAEO 검수 미달: " + " / ".join(result["issues"])
