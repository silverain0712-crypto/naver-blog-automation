"""Claude 구독(Max/Pro)으로 글 작성·분석 — Claude Code CLI(`claude -p`) 를 LLM 호출 백엔드로 쓴다.

API 키 과금 대신 `claude` 에 로그인된 구독 한도에서 차감된다. llm.call_json / llm.search_text 가
LLM_BACKEND=claude 일 때 이 모듈로 라우팅한다(codex_llm 과 같은 인터페이스).

- 구조화 출력: --json-schema → 결과 JSON 의 structured_output 사용(필수 키 검증 + 1회 재시도)
- 사진: 임시 폴더에 저장하고 Read 도구만 허용해 읽게 한다
- 웹 검색: WebSearch/WebFetch 도구만 허용
- 중요: ANTHROPIC_API_KEY 를 자식 프로세스 환경에서 지운다(남아 있으면 구독 대신 API 과금됨)
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path


class ClaudeLLMError(RuntimeError):
    pass


_SLOTS = threading.BoundedSemaphore(max(1, int(os.getenv("CLAUDE_LLM_WORKERS", "2") or 2)))


def _binary() -> str | None:
    return os.getenv("CLAUDE_BIN") or shutil.which("claude") or (
        str(Path.home() / ".local/bin/claude") if (Path.home() / ".local/bin/claude").exists() else None)


def available() -> bool:
    return bool(_binary())


def _map_model(model: str) -> tuple[str, str | None]:
    """config 의 claude-* 모델명을 그대로 쓰고, 본문 작성(Opus)만 추론 노력 high."""
    m = (model or "").lower()
    if "opus" in m:
        return model, "high"
    if m.startswith("claude"):
        return model, None
    return os.getenv("CLAUDE_WORKER_MODEL", "claude-sonnet-5-5"), None


def _flatten_system(system) -> str:
    if isinstance(system, list):
        return "\n\n".join(b.get("text", "") for b in system if b.get("type", "text") == "text")
    return system or ""


def _split_content(content: list, work: Path) -> tuple[str, int]:
    texts, n = [], 0
    for b in content or []:
        t = b.get("type")
        if t == "text":
            texts.append(b.get("text", ""))
        elif t == "image":
            src = b.get("source", {})
            ext = "png" if "png" in src.get("media_type", "") else "jpg"
            n += 1
            p = work / f"img_{n}.{ext}"
            p.write_bytes(base64.b64decode(src.get("data", "")))
            texts.append(f"[이미지 {n}: Read 도구로 {p} 를 열어 확인 — 위에서 {n}번째 사진]")
    return "\n\n".join(texts), n


def _run(prompt: str, *, system: str, model: str, effort: str | None, tools: str,
         schema: dict | None, timeout: int, work: Path) -> dict:
    bin_ = _binary()
    if not bin_:
        raise ClaudeLLMError("Claude Code CLI(claude)를 찾지 못했습니다.")
    cmd = [bin_, "-p", "--model", model, "--tools", tools, "--no-session-persistence",
           "--output-format", "json", "--system-prompt", system or "당신은 한국어 블로그 글 작성 도우미입니다."]
    if tools:
        cmd += ["--allowedTools", tools, "--permission-mode", "dontAsk"]
    if effort:
        cmd += ["--effort", effort]
    if schema:
        cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        r = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, timeout=timeout,
                           cwd=str(work), env=env)
    except subprocess.TimeoutExpired as e:
        raise ClaudeLLMError(f"Claude 응답이 {timeout}초 안에 끝나지 않았습니다.") from e
    out = r.stdout.decode("utf-8", "ignore")
    err = r.stderr.decode("utf-8", "ignore")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        low = (out + err).lower()
        if "login" in low or "unauthorized" in low or "401" in low:
            raise ClaudeLLMError("Claude 로그인이 만료됐습니다 — 터미널에서 `claude` 실행 후 /login 하세요.")
        raise ClaudeLLMError(f"Claude 실행 실패(exit {r.returncode}): {(err or out)[-240:]}")
    if data.get("is_error"):
        msg = str(data.get("result") or err)[-240:]
        low = msg.lower()
        if "limit" in low or "quota" in low:
            raise ClaudeLLMError(f"Claude 구독 사용 한도에 걸렸습니다: {msg}")
        raise ClaudeLLMError(f"Claude 오류: {msg}")
    return data


def _missing_required(obj: dict, schema: dict) -> list[str]:
    miss = [k for k in schema.get("required", []) if k not in obj]
    for k, sub in (schema.get("properties") or {}).items():
        if k in obj and sub.get("type") == "array" and not isinstance(obj[k], list):
            miss.append(f"{k}(배열이어야 함)")
        if k in obj and sub.get("type") == "string" and not isinstance(obj[k], str):
            miss.append(f"{k}(문자열이어야 함)")
    return miss


def call_json(*, model: str, system, content: list, schema: dict, search: bool = False,
              timeout: int = 1200) -> dict:
    cl_model, effort = _map_model(model)
    work = Path(tempfile.mkdtemp(prefix="claude_llm_"))
    try:
        user_text, n_img = _split_content(content, work)
        tools = ",".join((["Read"] if n_img else []) + (["WebSearch", "WebFetch"] if search else []))
        base = user_text + "\n\n[출력 규칙] 스키마의 required 키를 모두 채운 구조화 결과만 낸다."
        prompt, last_err = base, None
        with _SLOTS:
            for _ in range(2):
                data = _run(prompt, system=_flatten_system(system), model=cl_model, effort=effort,
                            tools=tools, schema=schema, timeout=timeout, work=work)
                obj = data.get("structured_output")
                if obj is None:
                    try:
                        obj = json.loads(data.get("result") or "")
                    except json.JSONDecodeError as e:
                        last_err = f"구조화 결과 없음/파싱 실패: {e}"
                        prompt = base + f"\n\n[이전 시도 실패: {last_err}] 스키마대로 다시 답하라."
                        continue
                miss = _missing_required(obj, schema) if isinstance(obj, dict) else ["객체 아님"]
                if not miss:
                    return obj
                last_err = f"필수 키 누락: {miss}"
                prompt = base + f"\n\n[이전 답변이 거부됨: {last_err}] 스키마대로 다시 답하라."
        raise ClaudeLLMError(f"Claude 응답이 스키마를 못 지켰습니다({last_err}).")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def search_text(*, model: str, system: str, prompt: str, timeout: int = 900) -> str:
    cl_model, effort = _map_model(model)
    work = Path(tempfile.mkdtemp(prefix="claude_llm_"))
    try:
        full = (f"{prompt}\n\n[출력 규칙] 웹 검색으로 실제 근거를 찾아 지침의 형식 그대로 답하라. "
                "설명이나 머리말 없이 결과 본문만 출력한다.")
        with _SLOTS:
            data = _run(full, system=system, model=cl_model, effort=effort, tools="WebSearch,WebFetch",
                        schema=None, timeout=timeout, work=work)
        return str(data.get("result") or "").strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)
