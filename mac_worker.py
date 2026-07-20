"""맥 자동 포스터 워커 — 폰에서 '배포(자동 저장)' 요청한 글을 백그라운드로 네이버에 임시저장.

동작: Supabase 의 status='queued' 글을 주기적으로 폴링 → headless 크롬으로 네이버에
임시저장(서식 전부 반영) → status='posted' (실패 시 'error' + 메시지).

맥이 깨어있는 동안만 동작한다. 잠들면 멈췄다가 깨어나면 이어서 처리한다.
실행: ./start_worker.sh  (또는 python3 mac_worker.py)
종료: Ctrl+C
"""

import datetime
import json
import os
import re
import signal
import subprocess
import time

import config
from modules import store
from modules.naver_blog_writer import run_job

POLL_SECONDS = 30
# 크롬 프로필(~/.naver_blog_automation/userdata)은 잡 하나만 쓸 수 있다. 다른 naver_run.py 가
# 이 시간보다 오래 떠 있으면 죽은 잡으로 보고 정리한다(정상 잡은 보통 몇 분 안에 끝난다).
STALE_JOB_SECONDS = 30 * 60


def _parse_etime(etime: str) -> int:
    """ps 의 ELAPSED([[dd-]hh:]mm:ss)를 초로 바꾼다. macOS ps 는 etimes 를 지원하지 않는다."""
    m = re.match(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$", etime.strip())
    if not m:
        return 0
    days, hours, mins, secs = (int(g or 0) for g in m.groups())
    return ((days * 24 + hours) * 60 + mins) * 60 + secs


def _running_naver_jobs() -> list[tuple[int, int]]:
    """돌고 있는 다른 naver_run.py 프로세스의 (pid, 경과초) 목록."""
    try:
        out = subprocess.run(["ps", "-eo", "pid,etime,command"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        print(f"  프로세스 확인 실패(가드 건너뜀): {e}")
        return []

    me = os.getpid()
    jobs = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid = int(parts[0])
        if pid != me and _is_naver_run(parts[2]):
            jobs.append((pid, _parse_etime(parts[1])))
    return jobs


def _is_naver_run(command: str) -> bool:
    """'python ... naver_run.py' 형태인지 본다.

    명령줄 전체에 부분문자열로 찾으면 이 스크립트를 열어둔 편집기나, 명령줄에 경로가
    섞인 셸까지 걸려서 엉뚱한 프로세스를 죽이게 된다.
    """
    argv = command.split()
    if not argv or not os.path.basename(argv[0]).lower().startswith("python"):
        return False
    return any(a.endswith("naver_run.py") for a in argv[1:])


def _profile_is_free() -> bool:
    """크롬 프로필이 비었는지 확인하고, 멈춰 있는 잡은 정리한다.

    다른 잡이 아직 정상 실행 중이면 False 를 돌려 이번 폴링을 건너뛴다. 같은 프로필을
    두 잡이 동시에 물면 브라우저가 닫혀 'Target page ... has been closed' 로 실패한다.
    """
    jobs = _running_naver_jobs()
    if not jobs:
        return True

    live = [(pid, age) for pid, age in jobs if age < STALE_JOB_SECONDS]
    for pid, age in jobs:
        if age >= STALE_JOB_SECONDS:
            print(f"  멈춘 잡 정리: pid {pid} ({age // 60}분째) — 종료합니다.")
            _kill(pid)

    if live:
        pid, age = live[0]
        print(f"  다른 네이버 잡 실행 중(pid {pid}, {age // 60}분째) — 이번 차례는 건너뜁니다.")
        return False
    return True


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception as e:
        print(f"    종료 실패: {e}")
        return
    for _ in range(10):
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


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

    # 해시태그는 본문에 섞지 않고 따로 넘긴다 → writer 가 '맨 끝'(미배치 사진 뒤)에 넣어
    # 사진 더미에 파묻히지 않게 한다.
    draft = {
        "blog_id": data.get("blog_id", "bbnation"),
        "title": row.get("title", ""),
        "body": row.get("body", ""),
        "hashtags": data.get("hashtags") or [],
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
                if not _profile_is_free():
                    break  # queued 로 두고 다음 폴링에서 다시 시도한다
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
