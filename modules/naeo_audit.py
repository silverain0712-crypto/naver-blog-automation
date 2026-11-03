"""NAEO 인용 조건 자동 검수 + 수치 밀도 보정.

prompts/naeo_rules.py 는 LLM 에게 '이렇게 써라'라고 시킬 뿐, 지켰는지는 세어보기 전엔 모른다.
실측(2026-08-15 NAEO 브리핑, sub=육아)에서 나온 조건 중 기계로 셀 수 있는 것만 여기서 검수하고,
미달이면 본문을 한 번 보정한다.

검수 항목(육아 소분류 처방):
- 숫자가 든 문장 비율 20% 이상(표가 있으면 10% 이상)  ← 가장 강한 인용 신호
- 표는 개수 요구 없음 — 있다면 범용 머리글·본문 중복·1행짜리 같은 저품질 표만 지적(prompts/table_rules.py)
- 소제목 3~6개
- 제목: 후보 구조 다양성·길이·낚시어·가격 점검(숫자는 선택 — prompts/title_rules.py)
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
# 머리글이 이 단어들뿐이면 '무엇을 비교하는지' 안 보이는 범용 표다.
_GENERIC_HEADERS = {"구분", "항목", "내용", "비고", "설명", "참고", "특징", "정보", "종류"}


# 메이트 17편 실측(2026-11-03): 문장 유사쌍 평균 0.4(최대 1) vs 내 최근 30편 평균 3.2(최대 10).
_META_RE = re.compile(r"결론부터 말하면|(적어|정리해|풀어|알려|소개해)\s?(볼게요|둘게요|드릴게요|드려요)")
_TIC_LIMITS = (
    (re.compile(r"더라(구|고)요"), 4, "'~더라고요/더라구요'"),
    (re.compile(r"ㅎㅎ|ㅋㅋ"), 3, "'ㅎㅎ/ㅋㅋ'"),
    (re.compile(r"답니다"), 1, "'~답니다'"),
)


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"[^가-힣0-9]", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def repetition_problems(body: str) -> list[str]:
    """중언부언·AI 티 신호를 센다(prompts/naeo_rules.py [V7] 과 같은 기준).

    · 같은 말을 다시 한 문장(요약·섹션 재요약·마무리 재요약) 수
    · 예고·메타 문장 횟수 · 특정 어미/추임새 남발
    """
    problems: list[str] = []
    text = _strip_markers(_TABLE_RE.sub(" ", body or ""))
    flat = re.sub(r"\s+", " ", text)
    ss = [s.strip() for s in re.split(r"(?<=[요다죠까음])[\s.!?]+", flat) if len(re.sub(r"\s", "", s)) >= 25]
    bgs = [_bigrams(s) for s in ss]
    pairs = []
    for i in range(len(ss)):
        for j in range(i + 2, len(ss)):
            m = min(len(bgs[i]), len(bgs[j]))
            if m and len(bgs[i] & bgs[j]) / m >= 0.5:
                pairs.append((ss[i][:28], ss[j][:28]))
    if len(pairs) >= 2:
        ex = " / ".join(f"'{a}…'↔'{b}…'" for a, b in pairs[:2])
        problems.append(
            f"같은 말을 다시 하는 문장 {len(pairs)}쌍(메이트 평균 0.4쌍) — 요약·섹션 재요약·마무리 재요약을 "
            f"지우고 한 곳에만 남겨라. 예: {ex}"
        )
    metas = _META_RE.findall(flat)
    if len(metas) > 1:
        problems.append(f"예고·메타 문장 {len(metas)}회('적어볼게요/정리해둘게요/결론부터 말하면') — 서두 예고 1회만 남겨라")
    for rx, limit, label in _TIC_LIMITS:
        n = len(rx.findall(flat))
        if n > limit:
            problems.append(f"{label} {n}회(상한 {limit}) — 다른 종결로 바꾸거나 지워라")
    return problems


_TITLE_HYPE = ("필수템", "인생템", "국민템", "무조건", "충격", "비밀", "정체", "대방출", "역대급", "기적")
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _title_shape(t: str) -> str:
    """제목 구조 분류(title_rules T5 의 A~F). 위에서부터 먼저 맞는 것."""
    if t.startswith("["):
        return "말머리"
    if re.search(r"[|ㅣ]", t):
        return "파이프"
    if re.search(r"[(（]", t):
        return "괄호"
    if re.search(r"[?]|까요|일까|할까|나요|언제|어떻게", t):
        return "질문"
    if "," in t:
        return "쉼표"
    return "평문"


def title_problems(titles: list[str]) -> list[str]:
    """제목 후보 검수(prompts/title_rules.py T1~T6 과 같은 기준). 본문 보정으로 못 고치므로 경고용이다.

    메이트 192개 제목 실측: 공백 제외 중앙 29자(26~36), 쉼표 39%·'|' 26%·괄호 27%.
    내 최근 30편은 쉼표형 87% — 후보 3개가 같은 틀이면 양산형으로 읽힌다.
    """
    problems: list[str] = []
    ts = [t.strip() for t in titles if str(t).strip()]
    if not ts:
        return problems
    for t in ts:
        n = len(re.sub(r"\s", "", t))
        long_ok = 45 if re.search(r"[|ㅣ(（]", t) else 36
        if n < 20 or n > long_ok:
            problems.append(f"제목 길이 {n}자('{t[:18]}…') — 26~36자(나열형 45자)로")
        if re.search(r"\d[\d,]*\s?원", t):
            problems.append(f"제목에 가격 금지('{t[:18]}…')")
        if _EMOJI_RE.search(t):
            problems.append(f"제목에 이모지·장식 기호 금지('{t[:18]}…')")
        hype = [w for w in _TITLE_HYPE if w in t]
        if hype:
            problems.append(f"제목 낚시·과장어 {hype}('{t[:18]}…')")
    shapes = [_title_shape(t) for t in ts]
    if len(ts) >= 3 and len(set(shapes)) == 1:
        problems.append(f"제목 후보 3개가 모두 같은 구조({shapes[0]}형) — 쉼표·파이프·괄호·질문·평문·말머리 중 서로 다른 3종으로(T5)")
    elif len(ts) >= 3 and shapes.count("쉼표") >= 3:
        problems.append("제목 후보가 쉼표형 일색 — 다른 구조를 섞어라(T5)")
    return problems


def table_problems(body: str) -> list[str]:
    """[표] 블록마다 쓸모 없는 표인지 센다. 문제 없으면 빈 리스트.

    기준은 prompts/table_rules.py 와 같다: 범용 머리글, 데이터 2행 미만, 칸이 문장,
    표 속 값이 표 바로 앞뒤 문단에 그대로 반복됨.
    """
    problems: list[str] = []
    for m in _TABLE_RE.finditer(body or ""):
        raw = m.group(0).replace("[표]", "").replace("[/표]", "")
        rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in raw.splitlines() if ln.strip()]
        rows = [r for r in rows if any(r) and not all(set(c) <= set("-: ") for c in r)]
        if len(rows) < 3:
            problems.append("표 데이터 행이 2개 미만")
            continue
        header, data = rows[0], rows[1:]
        if sum(1 for h in header if h in _GENERIC_HEADERS) >= max(1, len(header) - 1):
            problems.append(f"범용 머리글 표({'/'.join(header)}) — 비교 기준이나 순서가 머리글에 드러나야 한다")
        if any(len(c) > 20 for r in data for c in r):
            problems.append("표 칸이 문장 길이 — 수치·단어 단위로 줄여야 한다")
        if not any(re.search(r"\d", c) for r in data for c in r):
            problems.append("표에 수치가 하나도 없음 — 비교·순서의 근거가 없다")
        ctx = (body[max(0, m.start() - 300): m.start()] + body[m.end(): m.end() + 300])
        cells = [c for r in data for c in r if len(c) >= 3]
        if cells and sum(1 for c in cells if c in ctx) / len(cells) >= 0.6:
            problems.append("표 값이 앞뒤 문단에 그대로 반복됨 — 표나 문단 중 하나를 지워라")
    return problems
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
# v5(2026-09-26): NAEO 실측 — 인용 상위 글은 인용구를 도입부 1개로 안 끝내고
# 섹션마다 흩어 2~4개 쓴다. 최소 기준만 여기서 강제하고(2개), 상한은 프롬프트에만 둔다.
_QUOTE_MIN = 2  # v7: 서론 선요약이 아니라 섹션별 핵심 한 문장


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
    bad_tables = table_problems(body) if has_table else []
    # 쓸모 없는 표는 숫자 목표를 깎아줄 근거가 못 된다(표를 숫자 면제용으로 쓰는 걸 막는다).
    has_table = has_table and not bad_tables
    quote_count = body.count("[인용]")
    has_quote = quote_count > 0 and "[/인용]" in body
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
    for prob in dict.fromkeys(bad_tables):
        issues.append(f"쓸모 없는 표: {prob}")
    if not has_quote:
        issues.append("인용구 없음 — 섹션의 핵심 판단 한 문장을 [인용]으로 본문 섹션에 넣어야 한다(서론 선요약 아님)")
    elif quote_count < _QUOTE_MIN:
        issues.append(
            f"인용구 {quote_count}개(목표 {_QUOTE_MIN}개 이상) — 본문 섹션마다 핵심 한 문장씩 흩어 넣어야 한다"
        )
    repeats = repetition_problems(body)
    for prob in repeats:
        issues.append(f"중언부언: {prob}")
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
    issues.extend(title_problems(titles))
    if len(titles) >= 3 and len(titles_without_number) == 0:
        issues.append("제목 후보 3개가 모두 숫자를 품음 — 숫자는 본문에 실제 있는 월령·규격일 때 0~2개만(T3)")

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
        "bad_tables": bad_tables,
        "repetition": repeats,
        "has_quote": has_quote,
        "quote_count": quote_count,
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
    system: str | list,
    body: str,
    issues: list[str],
    sources: str,
    need_table: bool,
    need_numbers: bool,
    need_quote: bool = False,
    need_dedupe: bool = False,
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
            "지적된 쓸모 없는 [표]를 고쳐라. 같은 기준 수치로 대상을 견주는 비교표(머리글=비교 기준, "
            "행=대상) 또는 날짜·단계 순서표(첫 열=순서, 시간순 정렬)로 만들 재료가 [사용 가능한 재료]에 "
            "있으면 3칸 표로 다시 짓고, 없으면 [표]...[/표] 블록을 통째로 지우고 그 내용이 본문 문장에 "
            "이미 있는지만 확인하라(없으면 한 문장으로 옮긴다). 새 표를 추가하지 마라. "
            "칸은 수치·단어 15자 이내, 표 안에 [사진N]을 넣지 마라."
        )
    if need_quote:
        asks.append(
            "본문 섹션 중 핵심 판단이 나오는 곳에 [인용]...[/인용] 블록을 넣어라(서론 직후 요약 금지). "
            "내용은 그 섹션에서 새로 말하는 핵심 한 문장이고, 가능하면 수치를 포함한다"
            "(예: '[인용]발 130에는 140을 신기니 두꺼운 양말을 신어도 앞코가 남았어요.[/인용]'). "
            "다른 문단에 이미 있는 문장을 그대로 옮기지 마라 — 그 자리의 원문을 인용구로 바꾸는 식으로 넣어라."
        )
    if need_dedupe:
        asks.append(
            "중언부언 정리: 같은 결론·수치를 다시 말하는 문장(서두 요약, 섹션 끝 재요약, 마무리 재요약)을 찾아 "
            "가장 구체적인 한 곳만 남기고 나머지는 통째로 삭제하라. 삭제로 분량이 줄어도 된다(메우지 마라). "
            "남기는 문장은 바꾸지 않는다. 예고·메타 문장('결론부터 말하면', '적어볼게요')은 서두 1회만 남기고 지운다. "
            "지적된 어미·추임새는 다른 종결로 바꾸거나 지운다."
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
