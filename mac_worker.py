"""맥 자동 포스터 워커 — 폰에서 '배포(자동 저장)' 요청한 글을 백그라운드로 네이버에 임시저장.

동작: Supabase 의 status='queued' 글을 주기적으로 폴링 → headless 크롬으로 네이버에
임시저장(서식 전부 반영) → status='posted' (실패 시 'error' + 메시지).

맥이 깨어있는 동안만 동작한다. 잠들면 멈췄다가 깨어나면 이어서 처리한다.
실행: ./start_worker.sh  (또는 python3 mac_worker.py)
종료: Ctrl+C
"""

import datetime
import json
import time

import config
from modules import store
from modules.naver_blog_writer import run_job

POLL_SECONDS = 30


def _build_job_dir(row: dict):
    data = row.get("data") or {}
    job_dir = config.OUTPUT_DIR / "naver_jobs" / ("auto_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    job_dir.mkdir(parents=True, exist_ok=True)

    img_names = []
    for i, path in enumerate(row.get("images") or [], start=1):
        # 저장 경로의 확장자를 보존한다(GIF 는 애니메이션 유지 위해 .gif 로).
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else "jpg"
        if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
            ext = "jpg"
        fn = f"{i}.{ext}"
        try:
            (job_dir / fn).write_bytes(store.download(path))
            img_names.append(fn)
        except Exception as e:
            print(f"  사진 {i} 내려받기 실패: {e}")

    # 해시태그(SEO 10개)를 본문 끝에 붙여 네이버 글에 실제로 들어가게 한다.
    # (폰 편집 본문에는 없고 저장 시점에만 추가 → 매 글 빠짐없이 반영)
    body = row.get("body", "")
    hashtags = data.get("hashtags") or []
    if hashtags:
        tag_line = " ".join(f"#{h.lstrip('#')}" for h in hashtags)
        body = body.rstrip() + "\n\n" + tag_line

    draft = {
        "blog_id": data.get("blog_id", "bbnation"),
        "title": row.get("title", ""),
        "body": body,
        "images": img_names,
        "videos": [],
        "captions": data.get("captions", {}),
        "subheadings": data.get("subheadings", []),
        "font": data.get("font", "나눔스퀘어"),
        "size": data.get("size", 15),
    }
    (job_dir / "draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return job_dir


def process(row: dict):
    job_dir = _build_job_dir(row)
    run_job(str(job_dir), interactive=False)


def main():
    if not store.enabled():
        print("Supabase 미설정(.env 확인). 종료합니다.")
        return
    print("🟢 맥 자동 포스터 워커 시작 — 폰에서 '맥 자동저장' 요청한 글을 처리합니다.")
    print(f"   {POLL_SECONDS}초마다 확인 / 종료: Ctrl+C")
    while True:
        try:
            rows = store.list_drafts("queued")
            for row in rows:
                title = row.get("title", "")[:30]
                print(f"\n[{datetime.datetime.now():%H:%M:%S}] 처리 시작: {title}")
                store.update_draft(row["id"], {"status": "posting"})
                try:
                    process(row)
                    store.update_draft(row["id"], {"status": "posted"})
                    print(f"  ✅ 임시저장 완료: {title}")
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
        print("\n워커 종료.")
