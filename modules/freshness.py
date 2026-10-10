"""오늘 날짜 기준 '최신성' 규칙 — 리서치/벤치마킹/본문 생성이 모델의 학습 시점(과거 연도)에
끌려가 옛날 연도·가격을 쓰는 문제를 막는다. 세 단계가 같은 문구를 공유한다."""

from __future__ import annotations

from datetime import date


def today_str() -> str:
    d = date.today()
    return f"{d.year}년 {d.month}월 {d.day}일"


def freshness_rule() -> str:
    d = date.today()
    return (
        f"[최신성 규칙 — 오늘은 {d.year}년 {d.month}월 {d.day}일이다]\n"
        f"- 네 학습 기억 속 연도·가격·제도·일정은 낡았을 수 있다. 올해는 {d.year}년이고 "
        f"작년({d.year - 1}년) 정보를 '올해' 것처럼 쓰면 안 된다.\n"
        f"- 가격·요금·접종/신청 기간·지원 대상·기준·운영시간처럼 해마다/수시로 바뀌는 정보는 "
        f"반드시 {d.year}년 기준 최신 출처로 확인한 값만 쓴다. 검색 쿼리에 '{d.year}'를 넣어라 "
        f"(예: '{d.year} 독감 예방접종 가격').\n"
        f"- 출처의 게시/수정 시점을 확인하라. {d.year - 1}년 이전 자료뿐이면 그 값을 현재 값으로 "
        f"단정하지 말고 '(○○년 기준, 변동 가능)'으로 연도를 밝히거나 쓰지 마라.\n"
        f"- 같은 항목에 최신·과거 값이 다르면 항상 최신 값을 쓴다.\n"
    )
