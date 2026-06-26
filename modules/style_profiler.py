"""기존 블로그 글 샘플을 읽어 '스타일 가이드'를 만든다.

style_samples/ 폴더의 .txt 본문을 Claude 로 1회 분석해 문체 특징을 뽑아 캐시한다.
샘플이 없으면 고정 규칙(style_rules)만으로 동작한다.
캐시는 샘플 파일 구성/수정시각이 바뀌면 자동 무효화된다.
"""

import hashlib
import json
import re

import config
from modules.llm import call_json

_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "tone": {"type": "string"},  # 전반적 말투/분위기 요약
        "sentence_style": {"type": "string"},  # 문장 길이/리듬 특징
        "paragraph_style": {"type": "string"},  # 문단 나누기 특징
        "subheading_style": {"type": "string"},  # 소제목 패턴
        "frequent_expressions": {"type": "array", "items": {"type": "string"}},
        "emoji_usage": {"type": "string"},  # 이모티콘/특수표현 사용 빈도
        "summary": {"type": "string"},  # 한 문단 종합 요약
    },
    "required": [
        "tone",
        "sentence_style",
        "paragraph_style",
        "subheading_style",
        "frequent_expressions",
        "emoji_usage",
        "summary",
    ],
    "additionalProperties": False,
}


def _samples() -> list[tuple[str, str]]:
    """style_samples/*.txt, *.md 의 (파일명, 본문) 목록."""
    out = []
    if not config.STYLE_SAMPLES_DIR.exists():
        return out
    for p in sorted(config.STYLE_SAMPLES_DIR.iterdir()):
        if p.name.startswith("_") or p.name.lower() == "readme.md":
            continue
        if p.suffix.lower() in (".txt", ".md"):
            text = p.read_text(encoding="utf-8").strip()
            if text:
                out.append((p.name, text))
    return out


def _fingerprint(samples: list[tuple[str, str]]) -> str:
    h = hashlib.sha256()
    for name, text in samples:
        h.update(name.encode())
        h.update(str(len(text)).encode())
    return h.hexdigest()


def _recent_example(samples: list[tuple[str, str]]) -> str:
    """가장 최근 발행 글(published_<logno>) 본문을 예시로 고른다. 없으면 가장 긴 샘플."""
    pub = []
    for name, text in samples:
        m = re.search(r"published_(\d+)", name)
        if m:
            pub.append((int(m.group(1)), text))
    if pub:
        pub.sort(reverse=True)
        return pub[0][1]
    return max((t for _, t in samples), key=len, default="")


def load_style_guide() -> dict:
    """{'has_samples', 'profile', 'sample_count', 'recent_example'} 반환."""
    samples = _samples()
    if not samples:
        return {"has_samples": False, "profile": None, "sample_count": 0,
                "recent_example": ""}

    fp = _fingerprint(samples)
    example = _recent_example(samples)

    # 캐시 확인
    if config.STYLE_PROFILE_CACHE.exists():
        try:
            cached = json.loads(config.STYLE_PROFILE_CACHE.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fp:
                return {
                    "has_samples": True,
                    "profile": cached["profile"],
                    "sample_count": len(samples),
                    "recent_example": example,
                }
        except (json.JSONDecodeError, KeyError):
            pass

    # 캐시 없거나 무효 → Claude 로 재분석
    joined = "\n\n".join(
        f"=== 글 {i+1}: {name} ===\n{text}" for i, (name, text) in enumerate(samples)
    )
    system = (
        "너는 한국어 블로그 문체 분석가다. 주어진 같은 블로거의 글들을 보고 "
        "문체 특징을 구체적으로 추출하라. 추측 금지, 실제 글에서 보이는 패턴만."
    )
    profile = call_json(
        model=config.WRITER_MODEL,
        system=system,
        content=[{"type": "text", "text": f"다음은 같은 블로거의 글들이다:\n\n{joined}"}],
        schema=_PROFILE_SCHEMA,
        max_tokens=4000,
    )

    config.STYLE_PROFILE_CACHE.write_text(
        json.dumps({"fingerprint": fp, "profile": profile}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"has_samples": True, "profile": profile, "sample_count": len(samples),
            "recent_example": example}


def profile_to_prompt(guide: dict) -> str:
    """스타일 가이드를 글 작성 프롬프트에 넣을 텍스트로 변환."""
    if not guide.get("has_samples") or not guide.get("profile"):
        return (
            "[문체 참고] 기존 글 샘플이 없어 고정 규칙만 따른다. "
            "비비 특유의 담백한 후기체를 유지하라."
        )
    p = guide["profile"]
    exprs = ", ".join(p.get("frequent_expressions", [])) or "(특이사항 없음)"
    out = (
        "[기존 글에서 학습한 문체 — 최대한 비슷하게 써라]\n"
        f"- 전반 말투: {p.get('tone','')}\n"
        f"- 문장 스타일: {p.get('sentence_style','')}\n"
        f"- 문단 스타일: {p.get('paragraph_style','')}\n"
        f"- 소제목 스타일: {p.get('subheading_style','')}\n"
        f"- 자주 쓰는 표현: {exprs}\n"
        f"- 이모티콘/특수표현: {p.get('emoji_usage','')}\n"
        f"- 종합: {p.get('summary','')}"
    )
    example = (guide.get("recent_example") or "").strip()
    if example:
        out += (
            "\n\n[가장 최근 발행 글 예시 — 이 톤·호흡·줄바꿈, 그리고 이 정도 분량·"
            "소제목 개수를 그대로 따라해라]\n"
            "```\n" + example[:2600] + "\n```"
        )
    return out
