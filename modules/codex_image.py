"""ChatGPT 구독(GPT Pro)으로 이미지를 만든다 — Codex CLI 의 image_generation 도구 사용.

OpenAI API 는 장당 크레딧이 나가지만, Codex CLI 에 'ChatGPT 로그인'을 해 두면 이미지 생성이 구독
한도에서 차감된다(2026-11-04 실측: 한글 문구 카드 정확, 4:5 = 1122x1402, 장당 약 80초).

흐름: `codex exec` 를 비대화로 실행 → 이미지 생성 도구가 ~/.codex/generated_images/<스레드ID>/ 에
PNG 를 저장 → 그 파일을 읽어 bytes 로 돌려주고 흔적을 지운다.

전제(맥에서 1회): ChatGPT 앱(Codex CLI 번들) 또는 `npm i -g @openai/codex` 설치 + `codex login`(ChatGPT 계정).
로그인이 만료되면 CodexImageError 에 그렇게 적어 돌려준다 — 다시 `codex login` 하면 된다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import config


class CodexImageError(RuntimeError):
    pass


_BUNDLED = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex")
_GENERATED_DIR = Path.home() / ".codex" / "generated_images"

# Codex 한 번 호출 = 약 80초. 동시에 몇 개까지 돌릴지(구독 한도·속도 제한이 불확실해 기본은 보수적으로 2).
_SLOTS = threading.BoundedSemaphore(max(1, int(os.getenv("CODEX_IMAGE_WORKERS", "2") or 2)))

_WRAPPER = (
    "Use the built-in image generation tool to create exactly ONE image from the IMAGE PROMPT below. "
    "Do not write any code, do not copy/move/rename files, and do not generate more than one image. "
    "When the image is generated, reply with just the word done.\n\nIMAGE PROMPT:\n{prompt}"
)


def _binary() -> str | None:
    env = (os.getenv("CODEX_BIN") or "").strip()
    if env and Path(env).exists():
        return env
    if _BUNDLED.exists():
        return str(_BUNDLED)
    return shutil.which("codex")


def available() -> bool:
    """Codex CLI 가 있고 ChatGPT 로그인(auth.json)이 돼 있는지."""
    if not _binary():
        return False
    auth = Path.home() / ".codex" / "auth.json"
    try:
        return json.loads(auth.read_text()).get("auth_mode") == "chatgpt"
    except Exception:
        return False


def _thread_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if line.startswith("{") and "thread.started" in line:
            try:
                return json.loads(line).get("thread_id")
            except Exception:
                return None
    return None


def _run_once(prompt: str, timeout: int) -> bytes:
    bin_ = _binary()
    if not bin_:
        raise CodexImageError("Codex CLI 를 찾지 못했습니다(ChatGPT 앱 설치 또는 npm i -g @openai/codex).")
    work = tempfile.mkdtemp(prefix="codex_img_")
    tid = None
    try:
        cmd = [
            bin_, "exec", "--skip-git-repo-check", "--json", "--color", "never",
            "-s", "read-only", "-C", work,
            _WRAPPER.format(prompt=prompt),
        ]
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            tid = _thread_id((e.stdout or b"").decode("utf-8", "ignore"))
            raise CodexImageError(f"Codex 이미지 생성이 {timeout}초 안에 끝나지 않았습니다.") from e
        out = r.stdout.decode("utf-8", "ignore")
        err = r.stderr.decode("utf-8", "ignore")
        tid = _thread_id(out)
        if r.returncode != 0:
            low = (out + err).lower()
            if "login" in low or "unauthorized" in low or "401" in low:
                raise CodexImageError("Codex 로그인이 만료됐습니다 — 터미널에서 `codex login`(ChatGPT 계정) 다시 하세요.")
            raise CodexImageError(f"Codex 실행 실패(exit {r.returncode}): {(err or out)[-200:]}")
        if not tid:
            raise CodexImageError("Codex 응답에서 스레드 ID 를 못 찾았습니다.")
        pngs = sorted((_GENERATED_DIR / tid).glob("*.png"), key=lambda p: p.stat().st_mtime)
        if not pngs:
            raise CodexImageError("Codex 가 이미지를 만들지 못했습니다(한도 소진이거나 요청이 거절됐을 수 있음).")
        return pngs[0].read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if tid:
            shutil.rmtree(_GENERATED_DIR / tid, ignore_errors=True)


def generate(prompt: str, *, timeout: int = 420, retries: int = 1) -> bytes:
    """프롬프트 → PNG bytes. 실패하면 retries 만큼 다시 시도한 뒤 CodexImageError."""
    last: Exception | None = None
    with _SLOTS:
        for attempt in range(retries + 1):
            try:
                return _run_once(prompt, timeout)
            except CodexImageError as e:
                last = e
                if "로그인이 만료" in str(e):
                    break
                time.sleep(3)
    raise CodexImageError(str(last))
