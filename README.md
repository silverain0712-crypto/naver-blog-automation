# 비비 블로그 초안 생성기 (MVP 1단계)

사진과 메모를 넣으면 **비비(bbnation) 말투**로 네이버 블로그 초안과 사진 배치안을
만들어 주는 로컬 도구입니다.

> ⚠️ 이번 단계는 **초안 생성까지만** 합니다. 네이버 자동 입력/임시저장/발행은 하지 않으며,
> 결과를 검수한 뒤 직접 네이버 에디터에 붙여넣어 임시저장·발행해 주세요.

## 무엇을 하나요

1. 올린 사진을 분석해 피사체·용도·품질·중복 여부·추천 배치 순서를 뽑습니다.
2. 기존 글 문체(학습한 경우)와 고정 규칙(비비/뽀식이/존댓말/공정위 문구 등)에 맞춰
   제목 후보·메타디스크립션·본문·해시태그를 생성합니다.
3. 본문 흐름에 맞춰 사진 배치안과 캡션을 만들고, 확인이 필요한 정보를 따로 알려줍니다.
4. (선택) Gemini로 기존 썸네일 느낌의 대표사진을 생성합니다.

## 설치

```bash
cd "naver blog automation"
python3 -m venv .venv && source .venv/bin/activate   # 선택
pip install -r requirements.txt
```

## API 키 설정

`.env.example` 을 복사해 `.env` 로 만들고 키를 채웁니다.

```bash
cp .env.example .env
```

```
ANTHROPIC_API_KEY=sk-ant-...   # 필수 (사진 분석 + 글 작성)
GEMINI_API_KEY=...             # 선택 (썸네일 생성)
```

- Claude 키: https://console.anthropic.com/
- Gemini 키: https://aistudio.google.com/apikey

## 문체 학습 (권장)

`style_samples/` 폴더에 기존 블로그 글 본문을 `.txt` 로 2~3개 저장하면, 그 문체를 분석해
새 글에 반영합니다. 자세한 방법은 `style_samples/README.md` 참고.

## 실행

```bash
streamlit run app.py
```

브라우저가 자동으로 열립니다. 사진 업로드 → 글 유형/키워드/협찬 선택 → 메모 입력 →
**초안 생성** 을 누르면 결과가 탭으로 나옵니다.

## 폴더 구조

```
app.py                 # Streamlit UI + 전체 흐름
config.py              # 키/모델/옵션 설정
prompts/
  style_rules.py       # 비비 고정 규칙 + 협찬 문구
  post_structures.py   # 글 유형별 구조 템플릿
modules/
  llm.py               # Claude 호출/이미지 전처리 공통
  style_profiler.py    # 기존 글 문체 분석/캐시
  image_analyzer.py    # 사진 분석/중복 제거
  post_generator.py    # 초안 생성
  layout_planner.py    # 사진 배치안 정리(순수 파이썬)
  result_format.py     # 결과 마크다운 생성
  thumbnail_maker.py   # Gemini 썸네일(선택)
style_samples/         # 기존 글 본문(.txt) 저장 위치
output/                # 생성된 초안(.md) 저장
```

## 모델

- 사진 분석: `claude-sonnet-4-6` (다량·비용 효율)
- 글 작성: `claude-opus-4-8` (문체 품질)
- 썸네일: Gemini 이미지 모델 (`config.GEMINI_IMAGE_MODEL`)

## 네이버 임시저장 (MVP 2)

초안 생성 후 결과 화면 맨 아래 **"네이버 블로그 임시저장"** 섹션에서:

1. 블로그 아이디(기본 `bbnation`)와 사용할 제목을 고른 뒤 **브라우저 열어 임시저장하기** 클릭.
2. Playwright Chromium 창이 뜹니다. **처음 한 번만** 네이버에 직접 로그인(2단계 인증 포함)하면
   브라우저 프로필(`~/.naver_blog_automation/`)에 유지되어 다음부터 생략됩니다.
3. 제목/본문이 자동 입력되고 **임시저장**됩니다. 사진은 본문 `[사진N]` 위치를 참고해 배치하세요.
4. **발행은 절대 자동으로 하지 않습니다** — 검수 후 직접 발행하세요.

설치 시 Playwright 브라우저가 필요합니다:

```bash
python3 -m playwright install chromium
```

보안: 아이디/비밀번호는 코드에 저장하지 않습니다(브라우저 로그인 세션만 사용).
진행 로그와 단계별 스크린샷은 `output/naver_jobs/<시각>/` 에 저장됩니다.

> ⚠️ 네이버 에디터(SmartEditor ONE)는 구조가 자주 바뀌어 자동 입력이 깨질 수 있습니다.
> 자동 입력 실패 시 전체 본문이 클립보드에 복사되어 있어 `Cmd+V` 로 붙여넣을 수 있습니다.

## 다음 단계 (예정)

- 사진 자동 선별 고도화, 글 유형 자동 분류, 수정 결과 재학습
- 네이버 사진 자동 삽입(본문 [사진N] 위치에 정확히) 안정화
