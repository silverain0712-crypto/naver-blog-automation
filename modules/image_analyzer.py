"""업로드된 사진을 Claude 비전으로 분석한다.

사진별 피사체/용도/품질/중복 그룹/캡션을 뽑고, 중복 그룹에서는 품질 최고 1장만 남긴다.
사진 노출 방식과 글 유형 배치 기준을 고려해 추천 순서/역할을 매긴다.
"""

import config
from modules.llm import call_json, prepare_image_block
from prompts.post_structures import get_structure

_PHOTO_SCHEMA = {
    "type": "object",
    "properties": {
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},  # 1부터 시작하는 사진 번호
                    "subject": {"type": "string"},  # 주요 피사체 한 줄
                    "role": {"type": "string", "enum": config.PHOTO_ROLES},
                    "brightness": {"type": "integer"},  # 1(어두움)~5(밝음)
                    "sharpness": {"type": "integer"},  # 1(흔들림)~5(선명)
                    "composition": {"type": "integer"},  # 1(나쁨)~5(좋음)
                    "duplicate_group": {"type": "integer"},  # 같은 장면이면 같은 번호, 단독이면 0
                    "has_baby": {"type": "boolean"},  # 아기 등장 여부
                    "caption": {"type": "string"},  # 블로그용 캡션 제안
                    "order_hint": {"type": "integer"},  # 글 흐름상 추천 배치 순서(1=먼저)
                },
                "required": [
                    "index",
                    "subject",
                    "role",
                    "brightness",
                    "sharpness",
                    "composition",
                    "duplicate_group",
                    "has_baby",
                    "caption",
                    "order_hint",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["photos"],
    "additionalProperties": False,
}


def analyze_images(images: list[bytes], structure_key: str, photo_style: str) -> dict:
    """images: 업로드된 사진 bytes 리스트(업로드 순서대로).

    반환: {'photos': [...분석...], 'excluded': [중복으로 제외된 index 들]}
    각 photo dict 에 'keep'(bool) 플래그를 추가한다.
    """
    if not images:
        return {"photos": [], "excluded": []}

    structure = get_structure(structure_key)

    system = (
        "너는 네이버 블로그용 사진 큐레이터다. 업로드된 사진들을 분석해 각 사진의 "
        "주요 피사체, 블로그 글에서의 용도, 품질(밝기/흔들림/구도), 중복 여부, "
        "추천 배치 순서, 캡션을 제안하라. "
        "사진 속 패키지·라벨·로고에 브랜드나 제품명이 보이면 subject 에 그대로 정확히 "
        "포함하라(예: '코지키즈 메시 수납백'). "
        "사진만 보고 알 수 없는 정보(가격, 정확한 장소명 등)는 추측하지 마라. "
        "아이가 나오면 개인정보는 캡션에 절대 쓰지 말고, 아이는 '뽀식이'로만 지칭하라."
    )

    instruction = (
        f"이 글의 유형은 '{structure['label']}'이고, 사진 노출 방식은 '{photo_style}'이다.\n"
        f"섹션별 사진 배치 기준: {structure['photo_guide']}\n\n"
        "각 사진을 위 기준에 맞춰 분석하라. "
        "거의 같은 장면(연사/비슷한 구도)은 같은 duplicate_group 번호로 묶어라(단독이면 0). "
        "order_hint 는 글 흐름상 배치 순서를 1부터 매겨라(대표/도입부가 앞)."
    )

    content: list = [{"type": "text", "text": instruction}]
    for i, img in enumerate(images, start=1):
        content.append({"type": "text", "text": f"[사진 {i}]"})
        content.append(prepare_image_block(img))

    result = call_json(
        model=config.VISION_MODEL,
        system=system,
        content=content,
        schema=_PHOTO_SCHEMA,
        max_tokens=8000,
    )

    photos = result.get("photos", [])

    # 중복 그룹별로 품질 점수 최고 1장만 keep, 나머지는 제외 후보
    best_in_group: dict[int, int] = {}  # group -> index of best
    scores: dict[int, int] = {}
    for p in photos:
        scores[p["index"]] = (
            p.get("brightness", 3) + p.get("sharpness", 3) + p.get("composition", 3)
        )
    for p in photos:
        g = p.get("duplicate_group", 0)
        if g == 0:
            continue
        cur = best_in_group.get(g)
        if cur is None or scores[p["index"]] > scores[cur]:
            best_in_group[g] = p["index"]

    excluded = []
    for p in photos:
        g = p.get("duplicate_group", 0)
        keep = g == 0 or best_in_group.get(g) == p["index"]
        p["keep"] = keep
        if not keep:
            excluded.append(p["index"])

    return {"photos": photos, "excluded": excluded}


def analysis_summary_for_writer(analysis: dict) -> str:
    """글 작성 프롬프트에 넣을, 사용할 사진들의 요약 텍스트."""
    lines = []
    for p in analysis.get("photos", []):
        if not p.get("keep", True):
            continue
        lines.append(
            f"- 사진 {p['index']}: {p['subject']} "
            f"(용도: {p['role']}, 추천순서: {p.get('order_hint','?')})"
        )
    if not lines:
        return "(사용할 사진 없음)"
    return "\n".join(lines)
