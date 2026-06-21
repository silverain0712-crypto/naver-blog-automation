"""본문의 [사진N]/[영상N] 자리와 사진 분석을 맞춰 최종 배치안을 만든다(순수 파이썬).

- 본문에 실제로 등장한 [사진N]/[영상N] 순서를 신뢰값으로 삼아 배치 순서를 정한다.
- 생성기의 photo_placement/video_placement 와 분석기의 role/caption 을 합쳐 채운다.
- 본문에 빠진 사진/영상, 분석에 없는 번호 등 불일치를 경고로 모은다.
"""

import re

_PHOTO_RE = re.compile(r"\[사진\s*(\d+)\]")
_VIDEO_RE = re.compile(r"\[영상\s*(\d+)\]")


def _ordered_unique(pattern, text):
    seen = []
    for m in pattern.finditer(text):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def plan_layout(post: dict, image_analysis: dict, num_videos: int = 0) -> dict:
    body = post.get("body", "")
    placement = {p["photo_number"]: p for p in post.get("photo_placement", [])}
    vplacement = {v["video_number"]: v for v in post.get("video_placement", [])}
    photos = {p["index"]: p for p in image_analysis.get("photos", [])}
    kept = {idx for idx, p in photos.items() if p.get("keep", True)}

    in_body = _ordered_unique(_PHOTO_RE, body)
    videos_in_body = _ordered_unique(_VIDEO_RE, body)

    # 사진 배치
    layout = []
    for order, n in enumerate(in_body, start=1):
        pl = placement.get(n, {})
        ph = photos.get(n, {})
        layout.append(
            {
                "order": order,
                "photo_number": n,
                "section": pl.get("section") or ph.get("role", "본문"),
                "role": ph.get("role", "본문"),
                "caption": pl.get("caption") or ph.get("caption", ""),
            }
        )

    # 영상 배치
    video_layout = []
    for order, n in enumerate(videos_in_body, start=1):
        vp = vplacement.get(n, {})
        video_layout.append(
            {
                "order": order,
                "video_number": n,
                "section": vp.get("section", "본문"),
                "note": vp.get("note", ""),
            }
        )

    warnings = []
    for n in in_body:
        if n not in photos:
            warnings.append(f"본문의 [사진{n}]에 해당하는 분석 결과가 없습니다.")
    for n in sorted(kept):
        if n not in in_body:
            warnings.append(f"사진 {n}이(가) 본문에 배치되지 않았습니다.")
    for n in in_body:
        if n in photos and not photos[n].get("keep", True):
            warnings.append(f"사진 {n}은(는) 중복으로 제외 권장인데 본문에 사용됐습니다.")

    # 영상 경고
    for n in videos_in_body:
        if n > num_videos:
            warnings.append(f"본문의 [영상{n}]에 해당하는 업로드 영상이 없습니다.")
    for n in range(1, num_videos + 1):
        if n not in videos_in_body:
            warnings.append(f"영상 {n}이(가) 본문에 배치되지 않았습니다.")

    return {"layout": layout, "video_layout": video_layout, "warnings": warnings}
