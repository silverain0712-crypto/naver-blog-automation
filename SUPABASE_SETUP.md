# 공용 저장소(Supabase) 설정 — 폰 ↔ 맥 데이터 다리

폰 클라우드 앱(`cloud_app.py`)에서 저장한 글을 맥 포스터(`mac_poster.py`)가 읽어
네이버에 임시저장합니다. 그 사이 데이터(드래프트 텍스트·사진·상태)를 Supabase에 둡니다.

## 1) 프로젝트 생성
1. https://supabase.com 가입 → **New project** 생성(무료 플랜).
2. 생성되면 **Project URL** 과 **service_role 키**를 확인:
   - Settings → API → `Project URL`
   - Settings → API → `service_role` (secret) 키  ※ anon 말고 service_role

## 2) 테이블 만들기
좌측 **SQL Editor** 에서 아래를 실행:

```sql
create table if not exists drafts (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz default now(),
  status text default 'ready',   -- ready(대기) / posted(발행됨) / draft(작성중)
  title text,
  body text,
  data jsonb,                    -- 썸네일제목/소제목/캡션/폰트/크기/블로그id 등
  images jsonb,                  -- Storage 사진 경로 목록
  thumbnail text                 -- Storage 썸네일 경로
);
```

## 3) Storage 버킷 만들기
좌측 **Storage → New bucket** → 이름 `media` (Private 로 둬도 됩니다. service_role 키로 접근).

## 4) 키 넣기
- **맥**: 프로젝트 폴더 `.env` 에
  ```
  SUPABASE_URL=https://xxxx.supabase.co
  SUPABASE_KEY=<service_role 키>
  SUPABASE_BUCKET=media
  ```
- **폰 클라우드 앱(Streamlit Cloud)**: 앱 Settings → **Secrets** 에 같은 값 +
  `ANTHROPIC_API_KEY`, (선택)`GEMINI_API_KEY`, `APP_PASSWORD` 를 TOML 형식으로:
  ```toml
  ANTHROPIC_API_KEY = "sk-ant-..."
  SUPABASE_URL = "https://xxxx.supabase.co"
  SUPABASE_KEY = "<service_role 키>"
  SUPABASE_BUCKET = "media"
  APP_PASSWORD = "원하는비밀번호"
  ```

## 보안 메모
- service_role 키는 DB 전체 권한이라, 폰 앱은 반드시 **APP_PASSWORD 잠금 + URL 비공개**로.
- 네이버 로그인 세션은 여기 저장되지 않습니다(맥에만 있음). Supabase엔 글·사진만.

## 실행
- 폰(클라우드): `cloud_app.py` 를 Streamlit Community Cloud에 배포(아래 DEPLOY 참고).
- 맥: `python3 -m streamlit run mac_poster.py` → '대기' 글을 네이버에 임시저장.
