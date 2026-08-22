"""상품 링크 → 상품 상세컷 생성. 실제 상품 사진을 레퍼런스로 넘겨 제품을 그대로 유지한다.

상품명만으로 만들면 AI 가 비슷하게 생긴 다른 제품을 지어내서 후기 글에 못 쓴다.
그래서 링크에서 실제 대표컷을 가져와(modules/product_info.py) 레퍼런스로 넘기고,
배경과 구도만 새로 만든다.

실측으로 얻은 규칙 하나: 라벨의 작은 인쇄 글씨가 뭉개지는 건 모델 한계가 아니라
**제품이 프레임에서 차지하는 크기** 문제였다. 멀리서 잡는 플랫레이는 문구가 전부
무너지고, 제품이 화면을 채우면 원본과 글자 단위로 일치한다. 그래서 모든 구도가
"제품이 프레임을 채운다"를 전제로 짜여 있다.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

import config

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
_TIMEOUT = 180

# 모든 컷에 공통으로 붙는 조건. 제품 동일성과 안전(사람 미등장)을 여기서 고정한다.
_COMMON = (
    "Keep the product itself identical to the reference image — same shape, same colors, "
    "same materials, same label text and layout. Do not redesign or restyle the product. "
    "The product fills most of the frame. Clean seamless off-white studio background, "
    "soft diffused lighting, gentle contact shadow. No people, no faces, no hands. "
    "Photorealistic commercial product photography, sharp focus, vertical 4:5 framing."
)

# 상세컷 구도. 이 순서대로 count 만큼 쓴다.
SHOTS: list[dict] = [
    {
        "key": "hero",
        "label": "정면 3/4 각도",
        "prompt": "Product detail photograph of {name} shown from a low three-quarter angle "
                  "so its form and thickness read clearly. Label sharp and legible.",
    },
    {
        "key": "open",
        "label": "열린 상태 · 구성",
        "prompt": "Product detail photograph of {name} shown opened or with its contents "
                  "visible, so a buyer can see what is inside and how much is included.",
    },
    {
        "key": "macro",
        "label": "재질 매크로",
        "prompt": "Extreme macro detail photograph of {name}, filling the frame with its "
                  "surface, texture and finish. Very shallow depth of field. No packaging "
                  "text in frame.",
    },
    {
        "key": "scale",
        "label": "디테일 클로즈업",
        "prompt": "Close-up detail photograph of the most distinctive part of {name}, "
                  "showing build quality and finish at close range.",
    },
]


@dataclass
class Shot:
    key: str
    label: str
    prompt: str
    data: bytes


class ShotError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


def build_prompts(product_name: str, count: int = 3, extra: str = "") -> list[dict]:
    """구도 count 개를 상품명에 맞춰 채운다."""
    name = (product_name or "the product").strip()
    out = []
    for shot in SHOTS[: max(1, min(count, len(SHOTS)))]:
        text = shot["prompt"].format(name=name) + " " + _COMMON
        if extra.strip():
            text += " " + extra.strip()
        out.append({"key": shot["key"], "label": shot["label"], "prompt": text})
    return out


def _call(parts: list[dict]) -> bytes:
    """Gemini 이미지 모델 호출 → 첫 이미지 바이트."""
    if not enabled():
        raise ShotError("GEMINI_API_KEY 가 없습니다(.env 확인).")
    url = _ENDPOINT.format(model=config.GEMINI_IMAGE_MODEL, key=config.GEMINI_API_KEY)
    req = urllib.request.Request(
        url,
        data=json.dumps({"contents": [{"parts": parts}]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        payload = json.load(urllib.request.urlopen(req, timeout=_TIMEOUT))
    except urllib.error.HTTPError as e:
        raise ShotError(f"Gemini 호출 실패 {e.code}: {e.read()[:200].decode(errors='replace')}") from e

    for part in payload.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        blob = part.get("inlineData") or part.get("inline_data")
        if blob and blob.get("data"):
            return base64.b64decode(blob["data"])
    # 이미지 없이 텍스트만 오면 대개 안전 필터에 걸린 것이다.
    raise ShotError("이미지가 반환되지 않았습니다(프롬프트가 거부됐을 수 있습니다).")


def _image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}


def make(reference: bytes, product_name: str, count: int = 3, extra: str = "") -> list[Shot]:
    """레퍼런스 사진으로 상세컷 count 장. 개별 실패는 건너뛰고 나머지를 돌려준다."""
    shots: list[Shot] = []
    for spec in build_prompts(product_name, count, extra):
        try:
            data = _call([_image_part(reference), {"text": spec["prompt"]}])
        except ShotError:
            continue
        shots.append(Shot(spec["key"], spec["label"], spec["prompt"], data))
    if not shots:
        raise ShotError("상세컷을 한 장도 만들지 못했습니다.")
    return shots


def revise(reference: bytes, base_prompt: str, feedback: str) -> bytes:
    """피드백을 반영해 다시 만든다. 항상 **레퍼런스에서 새로** 만든다.

    실측 결과: 이전 결과 이미지를 편집 대상으로 넘기면 피드백은 반영되지만 제품이
    원본에서 멀어진다 — 라벨의 '40 STICK' 이 '80 STICK' 으로, 'Design by KOREA' 가
    'KIDS2' 로 바뀌었다. 후기 글에 쓰면 틀린 정보가 된다.
    레퍼런스를 근거로 두고 피드백을 프롬프트에 녹이면 라벨이 원본과 일치한다.
    대신 구도는 새로 잡히므로, 같은 컷의 미세 보정이 아니라 '다시 찍기'에 가깝다.
    """
    text = base_prompt
    if feedback.strip():
        text += f"\n\nAdditional art direction from the photographer: {feedback.strip()}"
    return _call([_image_part(reference), {"text": text}])
