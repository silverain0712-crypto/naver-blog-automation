"""사진 → 영상 변환(image-to-video) 엔진 어댑터.

지금 구현은 Google Veo(Gemini API). 맥 데몬이 API 키로 직접 부를 수 있어야 해서
(클로드 커넥터류는 로그인 세션에 묶여 데몬에서 못 쓴다) 키 기반 엔진만 쓴다.

Veo 는 '출력 초당' 과금이라 호출 한 번이 곧 비용이다. 그래서 여기서는
- 부를지 말지를 판단하지 않고(그건 shortform.motion_worth 가 이미 정함),
- 실패하면 조용히 None 을 돌려준다 → 호출한 쪽이 켄번즈로 폴백해서 영상은 무조건 나온다.

교체하려면 generate_clip() 시그니처만 지키면 된다.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

import config

_API = "https://generativelanguage.googleapis.com/v1beta"
# Veo 가 받아주는 길이(초). 요청 길이를 여기에 맞춰 반올림한다.
_ALLOWED_SECONDS = (4, 6, 8)
_POLL_SECONDS = 10
_POLL_LIMIT = 60  # 최대 10분


def enabled() -> bool:
    return bool(config.GEMINI_API_KEY) and config.VIDEO_ENGINE.lower() == "veo"


def _post(url: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get(url: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _snap_seconds(seconds: float) -> int:
    """요청 길이를 Veo 가 받는 값으로 맞춘다(가까운 쪽, 동률이면 짧은 쪽 = 싼 쪽)."""
    return min(_ALLOWED_SECONDS, key=lambda a: (abs(a - seconds), a))


def _find_video_uri(op: dict):
    """완료된 operation 응답에서 영상 URI 또는 인라인 바이트를 찾는다.

    응답 형태가 버전마다 조금씩 달라서(generateVideoResponse / generatedSamples /
    videos …) 키를 재귀로 훑는다. 못 찾으면 (None, None).
    """
    found = {"uri": None, "b64": None}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("uri", "url") and isinstance(v, str) and not found["uri"]:
                    found["uri"] = v
                elif k in ("bytesBase64Encoded", "videoBytes") and isinstance(v, str):
                    found["b64"] = v
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(op)
    return found["uri"], found["b64"]


def generate_clip(
    image_path: str | Path,
    motion_prompt: str,
    seconds: float,
    out_path: str | Path,
    log=print,
) -> Path | None:
    """사진 1장 + 모션 지시문 → 세로(9:16) 영상 파일. 실패하면 None.

    호출한 쪽은 None 을 받으면 켄번즈로 폴백해야 한다(영상이 안 나오면 안 되므로).
    """
    if not enabled():
        return None

    image_path, out_path = Path(image_path), Path(out_path)
    dur = _snap_seconds(seconds)
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")

    payload = {
        "instances": [{
            "prompt": motion_prompt,
            "image": {"bytesBase64Encoded": b64, "mimeType": mime},
        }],
        "parameters": {
            "aspectRatio": "9:16",
            "durationSeconds": dur,
            "resolution": config.VEO_RESOLUTION,
        },
    }
    url = f"{_API}/models/{config.VEO_MODEL}:predictLongRunning?key={config.GEMINI_API_KEY}"

    try:
        op = _post(url, payload)
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode("utf-8", "ignore")
        log(f"    Veo 요청 실패({e.code}): {detail}")
        return None
    except Exception as e:
        log(f"    Veo 요청 실패: {str(e)[:120]}")
        return None

    name = op.get("name")
    if not name:
        log(f"    Veo 응답에 operation 이름이 없음: {str(op)[:150]}")
        return None
    log(f"    Veo 생성 시작({dur}초, {config.VEO_RESOLUTION}) — 기다리는 중")

    for _ in range(_POLL_LIMIT):
        time.sleep(_POLL_SECONDS)
        try:
            op = _get(f"{_API}/{name}?key={config.GEMINI_API_KEY}")
        except Exception as e:
            log(f"    Veo 폴링 오류(계속 시도): {str(e)[:80]}")
            continue
        if not op.get("done"):
            continue
        if op.get("error"):
            log(f"    Veo 생성 실패: {str(op['error'])[:200]}")
            return None

        uri, inline = _find_video_uri(op)
        try:
            if inline:
                out_path.write_bytes(base64.standard_b64decode(inline))
            elif uri:
                sep = "&" if "?" in uri else "?"
                with urllib.request.urlopen(f"{uri}{sep}key={config.GEMINI_API_KEY}",
                                            timeout=300) as r:
                    out_path.write_bytes(r.read())
            else:
                log(f"    Veo 응답에서 영상을 못 찾음: {json.dumps(op)[:250]}")
                return None
        except Exception as e:
            log(f"    Veo 영상 내려받기 실패: {str(e)[:120]}")
            return None

        if out_path.exists() and out_path.stat().st_size > 10000:
            log(f"    Veo 클립 완성: {out_path.name} ({out_path.stat().st_size // 1024}KB)")
            return out_path
        log("    Veo 영상 파일이 비었음")
        return None

    log("    Veo 생성 시간 초과(10분)")
    return None
