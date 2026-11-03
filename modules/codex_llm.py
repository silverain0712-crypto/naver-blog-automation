"""ChatGPT 구독(GPT Pro)으로 글 작성·분석 — Codex CLI(`codex exec`) 를 LLM 호출 백엔드로 쓴다.

Anthropic/OpenAI API 는 호출마다 과금되지만, Codex CLI 에 'ChatGPT 로그인'을 해 두면 구독 한도에서
차감된다(modules/codex_image.py 와 같은 로그인). llm.call_json / llm.search_text 가 LLM_BACKEND=codex 일 때
이 모듈로 라우팅한다.

방식: 시스템 프롬프트 + 입력 + JSON 스키마를 하나의 프롬프트로 stdin 에 넣고, 에이전트 기능(명령 실행·파일)은
쓰지 말고 JSON 한 덩어리만 답하게 한다 → 마지막 메시지(-o 파일)를 파싱한다. 사진은 -i 로 첨부, 웹 검색은 --search.
OpenAI strict structured-output 은 optional 필드를 못 받아서(우리 스키마엔 optional 이 있다) --output-schema 는 쓰지 않고
프롬프트에 스키마를 넣은 뒤 필수 키만 직접 검증하고 어기면 1회 다시 시킨다.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import config
from modules.codex_image import _binary, available  # 같은 CLI·로그인 재사용  # noqa: F401


class CodexLLMError(RuntimeError):
    pass


# Anthropic 모델명 → Codex 모델. 본문 작성(Opus)은 가장 강한 모델, 나머지(Sonnet 급)는 작업용 모델.
_WRITER = os.getenv("CODEX_WRITER_MODEL", "gpt-6-astra")
_WORKER = os.getenv("CODEX_WORKER_MODEL", "gpt-6.1-sol")
_FAST = os.getenv("CODEX_FAST_MODEL", "gpt-6-luna")


def _map_model(model: str) -> tuple[str, str]:
    m = (model or "").lower()
    if m.startswith("gpt-") or m.startswith("codex:"):
        return m.replace("codex:", ""), "medium"
    if "opus" in m:
        return _WRITER, "high"
    if "haiku" in m:
        return _FAST, "low"
    return _WORKER, "medium"


# 동시에 몇 개 호출할지(구독 한도·속도 제한이 불확실해 보수적으로 2).
_SLOTS = threading.BoundedSemaphore(max(1, int(os.getenv("CODEX_LLM_WORKERS", "2") or 2)))


def _flatten_system(system) -> str:
    if isinstance(system, list):
        return "\n\n".join(b.get("text", "") for b in system if b.get("type", "text") == "text")
    return system or ""


def _split_content(content: list, work: Path) -> tuple[str, list[str]]:
    """Anthropic 식 content 블록 → (텍스트, 이미지 파일 경로들)."""
    texts, imgs = [], []
    for b in content or []:
        t = b.get("type")
        if t == "text":
            texts.append(b.get("text", ""))
        elif t == "image":
            src = b.get("source", {})
            ext = "png" if "png" in src.get("media_type", "") else "jpg"
            p = work / f"img_{len(imgs) + 1}.{ext}"
            p.write_bytes(base64.b64decode(src.get("data", "")))
            imgs.append(str(p))
            texts.append(f"[이미지 {len(imgs)} 첨부됨 — 위에서 {len(imgs)}번째 사진]")
    return "\n\n".join(texts), imgs


def _run(prompt: str, images: list[str], *, model: str, effort: str, search: bool, timeout: int, work: Path) -> str:
    bin_ = _binary()
    if not bin_:
        raise CodexLLMError("Codex CLI 를 찾지 못했습니다(ChatGPT 앱 설치 또는 npm i -g @openai/codex).")
    out = work / "last_message.txt"
    # --search 는 최상위 옵션이라 서브커맨드(exec) 앞에 와야 한다.
    cmd = [bin_] + (["--search"] if search else []) + ["exec", "--skip-git-repo-check", "--ephemeral", "--color", "never",
           "-s", "read-only", "-C", str(work), "-m", model,
           "-c", f'model_reasoning_effort="{effort}"', "-o", str(out)]
    for p in images:
        cmd += ["-i", p]
    try:
        r = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise CodexLLMError(f"Codex 응답이 {timeout}초 안에 끝나지 않았습니다.") from e
    err = r.stderr.decode("utf-8", "ignore")
    if r.returncode != 0:
        low = err.lower()
        if "login" in low or "unauthorized" in low or "401" in low:
            raise CodexLLMError("Codex 로그인이 만료됐습니다 — 터미널에서 `codex login`(ChatGPT 계정) 다시 하세요.")
        if "usage limit" in low or "rate limit" in low or "quota" in low:
            raise CodexLLMError(f"ChatGPT 구독 사용 한도에 걸렸습니다: {err[-160:]}")
        raise CodexLLMError(f"Codex 실행 실패(exit {r.returncode}): {err[-240:]}")
    if not out.exists():
        raise CodexLLMError("Codex 가 답변을 남기지 않았습니다.")
    return out.read_text(encoding="utf-8", errors="ignore").strip()


def _extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


def _missing_required(obj: dict, schema: dict) -> list[str]:
    miss = [k for k in schema.get("required", []) if k not in obj]
    for k, sub in (schema.get("properties") or {}).items():
        if k in obj and sub.get("type") == "array" and not isinstance(obj[k], list):
            miss.append(f"{k}(배열이어야 함)")
        if k in obj and sub.get("type") == "string" and not isinstance(obj[k], str):
            miss.append(f"{k}(문자열이어야 함)")
    return miss


_RULES = (
    "[출력 규칙] 명령 실행·파일 읽기/쓰기·코드 작성을 하지 말고(웹 검색 도구가 켜져 있을 때만 검색 허용) "
    "바로 답하라. 답은 아래 JSON 스키마를 따르는 JSON 객체 하나만 출력한다 — 설명, 머리말, 마크다운 코드펜스 금지. "
    "스키마의 required 키는 모두 채우고, 문자열 안의 줄바꿈은 \\n 으로 이스케이프한다.\n"
    "[JSON 스키마]\n{schema}"
)


def call_json(*, model: str, system, content: list, schema: dict, search: bool = False,
              timeout: int = 1200) -> dict:
    """system + content → 스키마를 따르는 dict. 실패하면 CodexLLMError."""
    cx_model, effort = _map_model(model)
    work = Path(tempfile.mkdtemp(prefix="codex_llm_"))
    try:
        user_text, images = _split_content(content, work)
        base = (f"[시스템 지침]\n{_flatten_system(system)}\n\n[입력]\n{user_text}\n\n"
                + _RULES.format(schema=json.dumps(schema, ensure_ascii=False)))
        prompt, last_err = base, None
        with _SLOTS:
            for attempt in range(2):
                raw = _run(prompt, images, model=cx_model, effort=effort, search=search,
                           timeout=timeout, work=work)
                try:
                    obj = _extract_json(raw)
                    miss = _missing_required(obj, schema)
                    if not miss:
                        return obj
                    last_err = f"필수 키 누락: {miss}"
                except json.JSONDecodeError as e:
                    last_err = f"JSON 파싱 실패: {e}"
                # 한 번 더: 무엇이 틀렸는지 알려주고 같은 입력으로 다시.
                prompt = (base + f"\n\n[이전 답변이 거부됨: {last_err}] 위 규칙대로 JSON 객체 하나만 다시 출력하라.")
        raise CodexLLMError(f"Codex 응답이 스키마를 못 지켰습니다({last_err}).")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def search_text(*, model: str, system: str, prompt: str, timeout: int = 900) -> str:
    """웹 검색을 켜고 자유 텍스트 답을 받는다(리서치·벤치마킹용). 실패하면 CodexLLMError."""
    cx_model, effort = _map_model(model)
    work = Path(tempfile.mkdtemp(prefix="codex_llm_"))
    try:
        full = (f"[시스템 지침]\n{system}\n\n[요청]\n{prompt}\n\n"
                "[출력 규칙] 웹 검색 도구로 실제 근거를 찾아 지침의 형식 그대로 답하라. 명령 실행·파일 작업·코드 작성은 하지 마라. "
                "설명이나 머리말 없이 결과 본문만 출력한다.")
        with _SLOTS:
            return _run(full, [], model=cx_model, effort=effort, search=True, timeout=timeout, work=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
