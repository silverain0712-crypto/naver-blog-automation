"""Claude 호출 공통 헬퍼: 클라이언트 생성, 이미지 전처리, 구조화 JSON 호출."""

import base64
import io
import json

import anthropic
from PIL import Image, ImageOps

import config

# Claude 비전 권장 최대 변(픽셀). 너무 크면 토큰만 낭비되므로 리사이즈.
_MAX_EDGE = 1568


def get_client() -> anthropic.Anthropic:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY 가 설정되지 않았습니다. .env 파일을 확인해주세요."
        )
    # 큰 비전 요청/느린 본문 생성에서 간헐 연결 끊김(Connection error)에 견디도록
    # 재시도·타임아웃을 넉넉히. (SDK 는 연결오류/429/5xx 를 자동 재시도)
    return anthropic.Anthropic(
        api_key=config.ANTHROPIC_API_KEY,
        max_retries=4,
        timeout=180.0,
    )


def prepare_image_block(image_bytes: bytes) -> dict:
    """업로드 이미지를 EXIF 회전 보정 + 리사이즈 후 JPEG base64 content block 으로 변환."""
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)  # 세로로 찍은 사진 회전 보정
    if img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    longest = max(w, h)
    if longest > _MAX_EDGE:
        scale = _MAX_EDGE / longest
        img = img.resize((int(w * scale), int(h * scale)))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
    }


def call_json(
    *,
    model: str,
    system: str,
    content: list,
    schema: dict,
    max_tokens: int = 16000,
) -> dict:
    """구조화 출력(output_config.format) 으로 JSON 을 강제하고 파싱해서 dict 로 돌려준다."""
    client = get_client()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)
