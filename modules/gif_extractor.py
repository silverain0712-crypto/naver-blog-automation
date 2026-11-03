"""동영상 → GIF 변환 오케스트레이션.

블로그 글쓰기 파이프라인(mac_generator.py/modules/post_generator.py/prompts/*)과는
완전히 무관하다 — 이 모듈은 오직 mac_clip_generator.py 에서만 호출된다.

실제 변환은 modules/gif_maker.py(무수정)가 한다. 여기서는 draft 저장소(Supabase)와
잇는 글루만 한다: 원본 영상 다운로드 → GIF 변환 → images[]에 새 사진으로 추가 →
data.videos[i].gif_photo_number 기록.

**왜 초안 생성(status='generating') 전에 끝나야 하는가**: modules/image_analyzer.py
(생성 파이프라인의 일부, 무수정 대상)는 그 시점의 images[] 만 분석해서 작가 LLM에게
넘긴다. 이 GIF가 images[]에 이미 들어가 있어야 다른 사진과 똑같이(코드가 확장자를
신경 안 씀) 분석·배치된다 — web/app/page.tsx 가 이 순서를 보장한다(제출 전 GIF 변환
완료를 기다린 뒤에야 status=generating 으로 전환).
"""

from __future__ import annotations

from modules import gif_maker, store

# 네이버 블로그에 첨부 가능한 GIF 용량 상한(web/app/page.tsx 의 GIF_MAX_BYTES 와 동일
# 기준). 이보다 크면 네이버 에디터 업로드 단계에서 조용히 실패할 수 있어, 여기서
# 미리 화질/프레임을 낮춰가며 그 안에 들어오는 조합을 찾는다(길이는 유저가 고른
# 구간 그대로 유지 — 화질만 낮춘다).
GIF_MAX_MB = 10.0
_QUALITY_STEPS = [(480, 10), (480, 8), (360, 8), (320, 6), (240, 6)]


def _convert_under_limit(video_bytes: bytes, start: float, end: float) -> tuple[bytes, float]:
    gif_bytes, size_mb = b"", 0.0
    for width, fps in _QUALITY_STEPS:
        gif_bytes, size_mb = gif_maker.convert_to_gif(video_bytes, start, end, width=width, fps=fps)
        if size_mb <= GIF_MAX_MB:
            return gif_bytes, size_mb
    raise RuntimeError(
        f"이 구간은 가장 낮은 화질로도 {size_mb:.1f}MB라 {GIF_MAX_MB:.0f}MB를 넘어요 — "
        "구간을 더 짧게 잘라주세요."
    )


def extract(row: dict) -> None:
    """data.gif_job.status=='requested' 인 draft 하나를 처리한다.

    성공/실패 여부와 무관하게 data.gif_job 을 최종 상태(done/error)로 남긴다.
    """
    draft_id = row["id"]
    data = row.get("data") or {}
    job = data.get("gif_job") or {}

    job = {**job, "status": "running"}
    data = {**data, "gif_job": job}
    store.update_draft(draft_id, {"data": data})

    try:
        video_index = job.get("video_index")
        videos = list(data.get("videos") or [])
        if not isinstance(video_index, int) or not (0 <= video_index < len(videos)):
            raise RuntimeError("대상 영상을 찾을 수 없습니다.")
        video = videos[video_index]

        start = float(job.get("start", video.get("start", 0)) or 0)
        end = float(job.get("end", video.get("end", 0)) or 0)
        if end <= start:
            raise RuntimeError("트림 구간(끝-시작)이 올바르지 않습니다.")

        video_bytes = store.download(video["path"])
        gif_bytes, size_mb = _convert_under_limit(video_bytes, start, end)

        images = list(row.get("images") or [])
        n = len(images) + 1
        gif_path = store.upload_bytes(f"{draft_id}/{n}.gif", gif_bytes, "image/gif")
        images.append(gif_path)

        videos[video_index] = {**video, "start": start, "end": end, "gif_photo_number": n}
        data = {
            **data,
            "videos": videos,
            "gif_job": {**job, "status": "done", "error": ""},
        }
        store.update_draft(draft_id, {"images": images, "data": data})
        print(f"  ✅ GIF 추출 완료: {draft_id[:8]} (사진{n}, {size_mb:.1f}MB)")
    except Exception as e:
        msg = str(e)[:300]
        data = {**data, "gif_job": {**job, "status": "error", "error": msg}}
        store.update_draft(draft_id, {"data": data})
        print(f"  ⚠️ GIF 추출 실패: {msg}")
