"""맥 초안 생성기 — 폰(Vercel 앱)에서 넣은 글감을 비비 문체로 초안 작성.

동작: Supabase 의 status='generating' 글을 주기적으로 폴링 → app.py 와 똑같은 순서로
  (1) 문체 가이드 로드 → (2) 사진 분석 → (3) 초안 생성 → status='draft_ready' + 결과 저장.
폰은 draft_ready 가 되면 초안을 보여주고 사용자가 수정 후 '맥 자동저장'(status='queued')하면
  기존 mac_worker.py 가 네이버 임시저장한다.

브라우저(크롬)를 쓰지 않으므로 mac_worker.py / mac_poster.py 와 동시에 실행해도 안전하다.
실행: ./start_generator.sh  (또는 python3 mac_generator.py)   종료: Ctrl+C
"""

import datetime
import time

import config
from modules import store
from modules import style_profiler, image_analyzer, post_generator, style_sync
from modules.llm import _RETRYABLE

POLL_SECONDS = 15
GEN_ATTEMPTS = 2  # 간헐 네트워크 끊김 대비, 생성 전체 재시도 횟수

DEFAULT_FONT = "나눔스퀘어"
DEFAULT_SIZE = 15


def _download_images(row: dict) -> list[bytes]:
    images: list[bytes] = []
    for i, path in enumerate(row.get("images") or [], start=1):
        try:
            images.append(store.download(path))
        except Exception as e:
            print(f"  사진 {i} 내려받기 실패: {e}")
    return images


def _captions_from_placement(photo_placement: list[dict]) -> dict:
    """photo_placement → {사진번호(str): 캡션} (worker 가 쓰는 captions 형태)."""
    caps: dict[str, str] = {}
    for p in photo_placement or []:
        num = p.get("photo_number")
        cap = (p.get("caption") or "").strip()
        if num is not None and cap:
            caps[str(num)] = cap
    return caps


def generate(row: dict) -> None:
    req = (row.get("data") or {}).get("request") or {}
    structure_key = req.get("structure_key", "free")
    photo_style = req.get("photo_style", config.PHOTO_STYLES[0])

    images = _download_images(row)

    # 지속 학습(elevate): 생성 직전 최근 발행 글을 style_samples 로 수집한다.
    # 새 글이 쌓이면 load_style_guide 가 프로필을 재분석하고 가장 최근 발행 글을
    # few-shot 예시로 인용해, 쓸수록 비비 문체·분량에 가까워진다. 실패해도 생성은 계속.
    blog_id = req.get("blog_id", "bbnation")
    try:
        res = style_sync.sync(blog_id, limit=5)
        if res.get("added"):
            print(f"  📚 발행 글 {len(res['added'])}편 새로 학습에 반영: {', '.join(res['added'])[:80]}")
    except Exception as e:
        print(f"  (발행 글 학습 건너뜀: {str(e)[:60]})")

    # app.py 와 동일 순서: 문체 가이드 → 사진 분석 → 초안 생성
    style_guide = style_profiler.load_style_guide()
    analysis = image_analyzer.analyze_images(images, structure_key, photo_style)
    post = post_generator.generate_post(
        structure_key=structure_key,
        keyword=req.get("keyword", ""),
        product_link=req.get("product_link", ""),
        required_links=req.get("required_links", []),
        sponsor_type=req.get("sponsor_type", "내돈내산"),
        memo=req.get("memo", ""),
        length=int(req.get("length", 1500)),
        photo_style=photo_style,
        optional_fields=req.get("optional_fields", {}),
        style_guide=style_guide,
        image_analysis=analysis,
        video_count=0,
        video_desc="",
    )

    titles = post.get("title_candidates") or ["(제목 미정)"]
    data = {
        "request": req,
        "suggested_keywords": post.get("suggested_keywords", []),
        "title_candidates": titles,
        "thumbnail_title": post.get("thumbnail_title", []),
        "subheadings": post.get("subheadings", []),
        "meta_description": post.get("meta_description", ""),
        "hashtags": post.get("hashtags", []),
        "photo_placement": post.get("photo_placement", []),
        "video_placement": post.get("video_placement", []),
        "confirm_needed": post.get("confirm_needed", []),
        # 아래는 mac_worker._build_job_dir 가 쓰는 값
        "blog_id": req.get("blog_id", "bbnation"),
        "captions": _captions_from_placement(post.get("photo_placement", [])),
        "font": DEFAULT_FONT,
        "size": DEFAULT_SIZE,
    }

    store.update_draft(row["id"], {
        "status": "draft_ready",
        "title": titles[0],
        "body": post.get("body", ""),
        "data": data,
    })


def handle_learn_requests() -> None:
    """폰 '발행 글 학습' 버튼(status='learn')을 처리 — 최근 발행 글을 style_samples 로 수집.

    새 글이 추가되면 style_profiler 캐시가 fingerprint 변경으로 자동 무효화되어
    다음 생성부터 재학습된다. 결과(added 편수)는 row 에 담아 폰이 보여준다.
    """
    for row in store.list_drafts("learn"):
        req = (row.get("data") or {}).get("request") or {}
        blog_id = req.get("blog_id", "bbnation")
        print(f"[{datetime.datetime.now():%H:%M:%S}] 📚 발행 글 학습 요청: {blog_id}")
        try:
            res = style_sync.sync(blog_id, limit=10)
            print(f"  ✅ 학습 완료: 새 글 {len(res['added'])}편 (기존 {res['skipped']}편)")
        except Exception as e:
            res = {"added": [], "skipped": 0, "errors": [], "error": str(e)[:200]}
            print(f"  ⚠️ 학습 실패: {str(e)[:80]}")
        store.update_draft(row["id"], {
            "status": "learn_done",
            "data": {**(row.get("data") or {}), "result": res},
        })


def main():
    if not store.enabled():
        print("Supabase 미설정(.env 확인). 종료합니다.")
        return
    print("🟢 맥 초안 생성기 시작 — 폰에서 넣은 글감을 비비 문체로 초안 작성합니다.")
    print(f"   {POLL_SECONDS}초마다 확인 / 종료: Ctrl+C")
    while True:
        try:
            handle_learn_requests()
            rows = store.list_drafts("generating")
            for row in rows:
                title_hint = ((row.get("data") or {}).get("request") or {}).get("memo", "")[:30]
                print(f"\n[{datetime.datetime.now():%H:%M:%S}] 초안 생성 시작: {title_hint}")
                try:
                    for attempt in range(1, GEN_ATTEMPTS + 1):
                        try:
                            generate(row)
                            print("  ✅ 초안 완성 (draft_ready)")
                            break
                        except _RETRYABLE as e:
                            if attempt < GEN_ATTEMPTS:
                                print(f"  ↻ 네트워크 끊김({str(e)[:60]}) — {attempt}회차 재시도 대기…")
                                time.sleep(20)
                            else:
                                raise
                except Exception as e:
                    msg = str(e)[:300]
                    data = {**(row.get("data") or {}), "error": msg}
                    store.update_draft(row["id"], {"status": "error", "data": data})
                    print(f"  ⚠️ 실패: {msg}")
        except Exception as e:
            print(f"폴링 오류(재시도 예정): {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n생성기 종료.")
