"""환경 설정과 상수. .env 에서 API 키를 읽고, 모델 ID와 글 유형/협찬 옵션을 정의한다."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- API 키 ---------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# 핸드폰/네트워크 접속 시 보호용 비밀번호(설정하면 잠금, 비우면 잠금 없음)
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()

# 공용 저장소(Supabase) — 폰 클라우드 앱 ↔ 맥 포스터 데이터 다리
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "media").strip()

# --- 모델 ID --------------------------------------------------------------
# 사진 분석: 장수가 많아 비용 효율적인 Sonnet, 글 작성: 문체 품질이 중요해 Opus.
VISION_MODEL = "claude-sonnet-4-6"
WRITER_MODEL = "claude-opus-4-8"
# Gemini 이미지 생성 모델(Nano Banana 계열). 사용 불가 시 thumbnail_maker 가 친절히 안내.
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"

# --- 경로 -----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STYLE_SAMPLES_DIR = BASE_DIR / "style_samples"
OUTPUT_DIR = BASE_DIR / "output"
STYLE_PROFILE_CACHE = STYLE_SAMPLES_DIR / "_profile.json"

# --- 입력 옵션 ------------------------------------------------------------
# 글 유형 (key: post_structures 의 구조 키와 일치)
POST_TYPES = {
    "제품 후기": "product",
    "아기랑 방문 후기": "baby_visit",
    "맛집/카페 후기": "restaurant",
    "정보성 육아 글": "parenting_info",
    "자유 후기": "free",
}

# 협찬 여부
SPONSOR_TYPES = [
    "내돈내산",
    "제품제공",
    "원고료 제공",
    "네이버 쇼핑 커넥트 포함",
]

# 글 길이(자)
POST_LENGTHS = [1000, 1500, 2000]

# 업로드 허용 확장자
IMAGE_TYPES = ["jpg", "jpeg", "png", "webp"]
VIDEO_TYPES = ["mp4", "mov", "m4v", "webm", "avi"]

# 사진 노출 방식
PHOTO_STYLES = [
    "정보 중심",
    "감성 중심",
    "시간순",
    "제품 디테일 중심",
]

# 사진 용도 분류 (image_analyzer 와 layout_planner 가 공유)
PHOTO_ROLES = [
    "대표 이미지",
    "도입부용",
    "정보용",
    "디테일용",
    "실제 사용 장면",
    "마무리용",
]
