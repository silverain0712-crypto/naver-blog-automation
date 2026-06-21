"""사양서 9항 형식의 최종 결과 마크다운을 만든다(복사/다운로드용)."""


def build_markdown(post: dict, layout: dict, thumbnail_note: str = "") -> str:
    out = []

    if post.get("suggested_keywords"):
        out.append("# 추천 핵심 키워드")
        out.append(", ".join(post["suggested_keywords"]))
        out.append("")

    out.append("# 제목 후보")
    for i, t in enumerate(post.get("title_candidates", []), start=1):
        out.append(f"{i}. {t}")
    out.append("")

    out.append("# 메타디스크립션")
    out.append(post.get("meta_description", ""))
    out.append("")

    out.append("# 본문")
    out.append(post.get("body", ""))
    out.append("")

    out.append("# 사진 배치안")
    if layout.get("layout"):
        for item in layout["layout"]:
            cap = f" — 캡션: {item['caption']}" if item.get("caption") else ""
            out.append(
                f"{item['photo_number']}번 사진: {item['section']} "
                f"({item['role']}){cap}"
            )
    else:
        out.append("(배치된 사진 없음)")
    for item in layout.get("video_layout", []):
        note = f" — {item['note']}" if item.get("note") else ""
        out.append(f"영상 {item['video_number']}: {item['section']}{note}")
    out.append("")

    if post.get("hashtags"):
        out.append("# 해시태그")
        out.append(" ".join(f"#{h.lstrip('#')}" for h in post["hashtags"]))
        out.append("")

    out.append("# 확인 필요 정보")
    confirm = post.get("confirm_needed", [])
    if confirm:
        for c in confirm:
            out.append(f"- {c['item']}: {c['note']}")
    else:
        out.append("- 없음")
    out.append("")

    if layout.get("warnings"):
        out.append("# 배치 경고")
        for w in layout["warnings"]:
            out.append(f"- {w}")
        out.append("")

    if thumbnail_note:
        out.append("# 썸네일")
        out.append(thumbnail_note)
        out.append("")

    out.append("# 네이버 임시저장 상태")
    out.append("- 이번 단계(MVP1)에서는 자동 임시저장을 하지 않습니다.")
    out.append("- 위 초안을 검수 후 네이버 에디터에 직접 붙여넣어 임시저장해주세요.")

    return "\n".join(out)
