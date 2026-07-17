"""환경 설정과 상수. .env 에서 API 키를 읽고, 모델 ID와 글 유형/협찬 옵션을 정의한다."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 아이폰 HEIC 사진을 PIL 로 열 수 있게 등록(설치돼 있을 때만). 폰에서 원본이 올라와도
# image_analyzer/naver_blog_writer 가 처리 가능. 미설치면 조용히 통과(JPEG 만 지원).
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass

# --- API 키 ---------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()        # DALL-E 3 이미지 생성
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()  # 무료 스톡 사진 검색
# 핸드폰/네트워크 접속 시 보호용 비밀번호(설정하면 잠금, 비우면 잠금 없음)
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()

# 공용 저장소(Supabase) — 폰 클라우드 앱 ↔ 맥 포스터 데이터 다리
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "media").strip()

# 네이버 자동화용 브라우저: 실제 구글 크롬 사용(기본). 내 크롬 프로필(확장앱·로그인)을
# 쓰려면 NAVER_CHROME_PROFILE 에 프로필 폴더 경로를 넣는다(단, 실행 전 크롬 완전 종료 필요).
#   예: ~/Library/Application Support/Google/Chrome
NAVER_CHROME_PROFILE = os.path.expanduser(os.getenv("NAVER_CHROME_PROFILE", "").strip())

# --- 모델 ID --------------------------------------------------------------
# 사진 분석: 장수가 많아 비용 효율적인 Sonnet, 글 작성: 문체 품질이 중요해 Opus.
VISION_MODEL = "claude-sonnet-4-6"
WRITER_MODEL = "claude-opus-4-8"
# 사실 보강 리서치(웹 검색): 비용 효율적인 Sonnet. 웹검색 서버툴을 이 모델로 돌린다.
RESEARCH_MODEL = "claude-sonnet-4-6"
# 웹 검색 사실 보강 기능 on/off (끄면 리서치 단계 건너뜀). env 로 덮어쓸 수 있다.
ENABLE_RESEARCH = os.getenv("ENABLE_RESEARCH", "1").strip() not in ("0", "false", "False", "")
# 상위노출 벤치마킹(웹 검색으로 경쟁 상위글 구조 분석): 리서처와 같은 Sonnet.
BENCHMARK_MODEL = "claude-sonnet-4-6"
# 벤치마킹 기능 on/off (끄면 벤치마킹 단계 건너뜀). env 로 덮어쓸 수 있다.
ENABLE_BENCHMARK = os.getenv("ENABLE_BENCHMARK", "1").strip() not in ("0", "false", "False", "")
# 네이버 사진 배치 방식. 기본 0(결정적): 마커 교체 안 하고 [사진N] 글자 마커를 자리 안내로
# 남긴 뒤 모든 사진을 순서대로 글 끝에 모아 사용자가 드래그. 1이면 best-effort 인라인 시도
# (네이버 에디터가 불안정해 실행마다 결과가 달라짐).
NAVER_PHOTO_INLINE = os.getenv("NAVER_PHOTO_INLINE", "0").strip() in ("1", "true", "True")
# 1이면 '재배치 모드': 본문 텍스트를 먼저 전부 넣은 뒤, 각 [사진N] 마커 자리에 커서를
# 놓고 그 자리에 사진을 바로 업로드한다(네이버 공식 '커서 위치 삽입'만 사용 — 자체 커스텀
# DnD 라 자동 드래그는 안 먹는 걸 확인함). 삽입 실패분만 글 끝에 모으므로 최악의 경우가
# 결정적 모드와 동일해 안전하다. inline 모드보다 우선. (기본 ON — 간단글·실제 복잡글
# 25장/표/소제목 모두 제자리 배치 검증됨. 끄려면 NAVER_PHOTO_REARRANGE=0)
NAVER_PHOTO_REARRANGE = os.getenv("NAVER_PHOTO_REARRANGE", "1").strip() in ("1", "true", "True")
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
POST_LENGTHS = [1500, 2000, 2500]

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
