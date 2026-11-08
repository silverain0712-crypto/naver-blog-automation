"""상품 링크 → 상품 상세컷 생성. 실제 상품 사진을 레퍼런스로 넘겨 제품을 그대로 유지한다.

상품명만으로 만들면 AI 가 비슷하게 생긴 다른 제품을 지어내서 후기 글에 못 쓴다.
그래서 링크에서 실제 대표컷을 가져와(modules/product_info.py) 레퍼런스로 넘기고,
배경과 구도만 새로 만든다.

실측으로 얻은 규칙 하나: 라벨의 작은 인쇄 글씨가 뭉개지는 건 모델 한계가 아니라
**제품이 프레임에서 차지하는 크기** 문제였다. 멀리서 잡는 플랫레이는 문구가 전부
무너지고, 제품이 화면을 채우면 원본과 글자 단위로 일치한다. 그래서 모든 구도가
"제품이 프레임을 채운다"를 전제로 짜여 있다.

2026-11-04: ① 생성을 Gemini API(건당 과금) → Codex(ChatGPT 구독, 레퍼런스 사진 첨부)로 옮겼다 — 라벨 글자·
형태·그래픽이 원본과 일치함을 실측. ② 미사용(소개형) 제품 글용 '사용 장면' 컷(손만 등장)을 추가했다.
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

# 모든 상세컷에 공통으로 붙는 조건. 제품 동일성과 안전(사람 미등장)을 여기서 고정한다.
_COMMON = (
    "Keep the product itself identical to the reference image — same shape, same colors, "
    "same materials, same label text and layout. Do not redesign or restyle the product. "
    "The product fills most of the frame. Clean seamless off-white studio background, "
    "soft diffused lighting, gentle contact shadow. No people, no faces, no hands. "
    "Photorealistic commercial product photography, sharp focus, vertical 4:5 framing. "
    "If any engraving, stamp, or printed text on the product is too small or unclear in "
    "the reference image to copy exactly character-for-character, leave that area plain "
    "and unmarked rather than inventing text — a wrong engraving is worse than a blank one."
)
# 실측(2026-08-31): 라벨이 평면 인쇄물이라 레퍼런스에 크게 잡히면 글자 단위로 정확히
# 옮기지만(위 교훈), 손잡이에 새겨진 작은 각인처럼 레퍼런스 자체에서도 잘 안 보이는
# 글자는 모델이 그럴듯하게 지어내다가 오탈자를 낸다(티파니 스푼 "STERLING 925"가
# "STERLING 923"으로 나오거나, 아예 존재하지 않는 한글 각인을 만들어낸 사례). 그래서
# "안 보이면 지어내지 말고 비워둬라"를 명시적으로 지시한다.

# 사용 장면 공통 조건(2026-11-04) — 미사용(소개형) 제품 글에서 '이 제품을 이렇게 쓴다'를 보여주는 컷.
# 제품은 레퍼런스와 같게, 사람은 손만(얼굴·알아볼 수 있는 사람 금지). 레퍼런스에 모델 얼굴이
# 있어도 따라 그리지 않게 못 박는다(몽디에스 로션 레퍼런스로 실측: 손만 나왔다).
_COMMON_USE = (
    "Keep the product itself identical to the reference image — same shape, same colors, same "
    "materials, same label text and graphics. Do not redesign the product. "
    "Realistic candid lifestyle photograph in a bright, tidy Korean home or everyday Korean setting, "
    "natural window light, shallow depth of field, authentic everyday snapshot feel, vertical 4:5. "
    "Only hands or a partial body (never a face) may appear; no recognisable people, and do NOT copy "
    "any person who appears in the reference image. The product is clearly visible and large in the "
    "frame. No added text, logos, watermarks or readable signage other than what is printed on the "
    "product itself. If any small printed text on the product is unclear in the reference, leave it "
    "plain rather than inventing text."
)

# 구도. 이 순서대로 count 만큼 쓴다 — 앞 3장이 '대표·구성·사용' 기본 세트다.
# kind: detail(스튜디오 상세컷) | use(사용 장면). use 컷 캡션에는 'AI 생성'을 밝힌다(실제 사용 사진이 아니므로).
SHOTS: list[dict] = [
    {
        "key": "hero",
        "kind": "detail",
        "label": "정면 3/4 각도",
        "prompt": "Product detail photograph of {name} shown from a low three-quarter angle "
                  "so its form and thickness read clearly.",
    },
    {
        "key": "open",
        "kind": "detail",
        "label": "열린 상태 · 구성",
        "prompt": "Product detail photograph of {name} shown opened or with its contents "
                  "visible, so a buyer can see what is inside and how much is included.",
    },
    {
        "key": "use_scene",
        "kind": "use",
        "label": "사용 장면 예시 (AI 생성 이미지)",
        "prompt": "Lifestyle photograph of {name} being used the way it is normally used, in its "
                  "typical everyday setting (for baby or child products, a parent's hands use it). "
                  "If the product is too large to hold, show it in place in a realistic home or "
                  "outdoor setting with at most a hand or lower body visible.",
    },
    {
        "key": "macro",
        "kind": "detail",
        "label": "재질 매크로",
        "prompt": "Extreme macro detail photograph of {name}, filling the frame with its "
                  "surface, texture and finish. Very shallow depth of field. No packaging "
                  "text in frame.",
    },
    {
        "key": "use_hands",
        "kind": "use",
        "label": "사용 클로즈업 (AI 생성 이미지)",
        "prompt": "Close-up lifestyle photograph of hands operating {name} — pressing, opening, "
                  "pouring, holding or wearing it, whichever fits the product — with the key "
                  "feature of the product clearly in view.",
    },
    {
        "key": "scale",
        "kind": "detail",
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


def _codex_on() -> bool:
    """Codex(ChatGPT 구독) 경로 사용 가능 여부 — IMAGE_BACKEND=codex 이고 로그인돼 있을 때."""
    if config.IMAGE_BACKEND != "codex":
        return False
    from modules import codex_image
    return codex_image.available()


def enabled() -> bool:
    return _codex_on() or bool(config.GEMINI_API_KEY)


def build_prompts(product_name: str, count: int = 3, extra: str = "", include_use: bool = False) -> list[dict]:
    """구도 count 개를 상품명에 맞춰 채운다. include_use=False 면 스튜디오 상세컷만(직접 찍은
    사진이 있는 글 — AI 사용 장면이 섞이면 안 된다), True 면 사용 장면 컷을 순서대로 끼운다."""
    name = (product_name or "the product").strip()
    pool = SHOTS if include_use else [x for x in SHOTS if x.get("kind") != "use"]
    out = []
    for shot in pool[: max(1, min(count, len(pool)))]:
        common = _COMMON_USE if shot.get("kind") == "use" else _COMMON
        text = shot["prompt"].format(name=name) + " " + common
        if extra.strip():
            text += " " + extra.strip()
        out.append({"key": shot["key"], "label": shot["label"], "prompt": text})
    return out


def _generate(reference: bytes, prompt: str) -> bytes:
    """레퍼런스 + 프롬프트 → 이미지. Codex(구독)가 기본이고, 실패해도 Gemini(과금)로는
    ALLOW_GEMINI_SHOT_FALLBACK=1 일 때만 넘어간다(조용한 과금 방지)."""
    if _codex_on():
        from modules import codex_image
        try:
            return codex_image.generate(prompt, reference=reference)
        except codex_image.CodexImageError as e:
            if not (config.ALLOW_GEMINI_SHOT_FALLBACK and config.GEMINI_API_KEY):
                raise ShotError(f"Codex 이미지 생성 실패: {str(e)[:160]}") from e
            print(f"  (Codex 실패 → Gemini 로 대체: {str(e)[:80]})")
    return _call([_image_part(reference), {"text": prompt}])


def _call(parts: list[dict]) -> bytes:
    """Gemini 이미지 모델 호출 → 첫 이미지 바이트."""
    if not config.GEMINI_API_KEY:
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


def make(reference: bytes, product_name: str, count: int = 3, extra: str = "",
         include_use: bool = False) -> list[Shot]:
    """레퍼런스 사진으로 컷 count 장. 개별 실패는 건너뛰고 나머지를 돌려준다.

    Codex 는 장당 45~80초라 순차면 5장에 5분 넘게 걸린다 — 동시 실행 수는 codex_image 의
    세마포어(CODEX_IMAGE_WORKERS, 기본 2)가 제한하므로 스레드만 열고 순서는 그대로 지킨다."""
    from concurrent.futures import ThreadPoolExecutor
    specs = build_prompts(product_name, count, extra, include_use)
    errors: list[str] = []

    def _one(spec: dict):
        try:
            return Shot(spec["key"], spec["label"], spec["prompt"], _generate(reference, spec["prompt"]))
        except ShotError as e:
            errors.append(str(e))
            return None

    with ThreadPoolExecutor(max_workers=max(1, min(len(specs), 4))) as ex:
        shots = [s for s in ex.map(_one, specs) if s is not None]
    if not shots:
        raise ShotError("상세컷을 한 장도 만들지 못했습니다." + (f" ({errors[0][:140]})" if errors else ""))
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
    return _generate(reference, text)
