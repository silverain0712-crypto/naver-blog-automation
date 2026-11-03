"""맥 클립 생성기 — 블로그 글의 사진/캡션으로 네이버 클립(세로 숏폼) mp4 를 만든다.
동영상 → GIF 변환(modules/gif_extractor.py)도 같은 프로세스가 처리한다.

동작: Supabase 의 data.gif_job/clip_script_job/clip_job.status='requested' 인 글을
  주기적으로 폴링 → 각각 modules/gif_extractor.extract() / modules/clip_generator.plan()
  / modules/clip_generator.generate() 가 처리.

mac_generator.py(블로그 초안 생성)/mac_worker.py(네이버 발행)와 완전히 별개 프로세스다.
분리한 이유: AI 영상 변환(video_engine, Veo)은 씬 하나에 최대 10분까지 폴링할 수 있어,
같은 폴링 루프에 얹으면 그동안 블로그 글 생성이 멈춘다 — 그래서 절대 같은 루프에 넣지 않는다.
GIF 변환은 유저가 글 작성 화면에서 제출 전에 실시간으로 기다리는 잡이라 gif_job 을
루프 맨 앞에서 먼저 확인한다(응답성 우선).

실행: ./start_clip_generator.sh (또는 python3 mac_clip_generator.py)   종료: Ctrl+C
"""

import datetime
import time

from modules import clip_generator, gif_extractor, store

POLL_SECONDS = 10


def main():
    if not store.enabled():
        print("Supabase 미설정(.env 확인). 종료합니다.")
        return
    print("🎬 맥 클립 생성기 시작 — 블로그 글의 사진/캡션으로 네이버 클립을 만듭니다.")
    print(f"   {POLL_SECONDS}초마다 확인 / 종료: Ctrl+C")
    while True:
        try:
            for row in store.list_gif_jobs("requested"):
                title_hint = (row.get("title") or "")[:30]
                print(f"\n[{datetime.datetime.now():%H:%M:%S}] GIF 변환 시작: {title_hint}")
                gif_extractor.extract(row)
            for row in store.list_clip_script_jobs("requested"):
                title_hint = (row.get("title") or "")[:30]
                print(f"\n[{datetime.datetime.now():%H:%M:%S}] 클립 대본 기획 시작: {title_hint}")
                clip_generator.plan(row)
            for row in store.list_clip_jobs("requested"):
                title_hint = (row.get("title") or "")[:30]
                print(f"\n[{datetime.datetime.now():%H:%M:%S}] 클립 생성 시작: {title_hint}")
                clip_generator.generate(row)
        except Exception as e:
            print(f"폴링 오류(재시도 예정): {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n클립 생성기 종료.")
