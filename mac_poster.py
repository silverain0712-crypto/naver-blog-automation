"""맥 포스터 — 공용 저장소(Supabase)의 '대기' 글을 네이버에 임시저장.

폰에서 저장한 드래프트를 불러와, 가나초콜릿 썸네일·나눔명조 제목·구분선·회색 캡션 등
서식을 전부 입혀 네이버에 임시저장한다(발행은 직접). 맥에서 실행한다.
"""

import datetime
import json

import streamlit as st

import config
from modules import store

st.set_page_config(page_title="비비 네이버 포스터", page_icon="📤", layout="centered")
st.title("📤 비비 네이버 포스터 (맥 전용)")
st.caption("폰에서 저장한 글을 불러와 네이버에 **임시저장**합니다. 발행은 검수 후 직접 하세요.")

if not store.enabled():
    st.error("Supabase 가 설정되지 않았습니다. .env 에 SUPABASE_URL / SUPABASE_KEY 를 넣어주세요.")
    st.stop()

if st.button("새로고침"):
    st.rerun()

try:
    drafts = store.list_drafts("ready")
except Exception as e:
    st.error(f"목록 불러오기 실패: {e}")
    st.stop()

if not drafts:
    st.info("임시저장할 '대기' 상태 글이 없습니다. 폰에서 글을 저장하면 여기 나타납니다.")
    st.stop()

labels = {r["id"]: f"{r.get('title','(제목없음)')}" for r in drafts}
chosen_id = st.selectbox("임시저장할 글", list(labels.keys()),
                         format_func=lambda i: labels[i])
row = next(r for r in drafts if r["id"] == chosen_id)
data = row.get("data") or {}

st.text_area("본문 미리보기", row.get("body", ""), height=240, disabled=True)
blog_id = st.text_input("블로그 아이디", value=data.get("blog_id", "bbnation"))

if st.button("🌐 브라우저 열어 네이버 임시저장", type="primary"):
    import subprocess
    import sys

    job_dir = config.OUTPUT_DIR / "naver_jobs" / datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    job_dir.mkdir(parents=True, exist_ok=True)

    # 사진 내려받기
    img_names = []
    for i, path in enumerate(row.get("images") or [], start=1):
        try:
            b = store.download(path)
            name = f"{i}.jpg"
            (job_dir / name).write_bytes(b)
            img_names.append(name)
        except Exception as e:
            st.warning(f"사진 {i} 내려받기 실패: {e}")
    # 썸네일도 내려받아 두기(대표사진 수동 지정용)
    if row.get("thumbnail"):
        try:
            (job_dir / "thumbnail.png").write_bytes(store.download(row["thumbnail"]))
        except Exception:
            pass

    draft = {
        "blog_id": blog_id.strip(),
        "title": row.get("title", ""),
        "body": row.get("body", ""),
        "images": img_names,
        "videos": [],
        "captions": data.get("captions", {}),
        "subheadings": data.get("subheadings", []),
        "font": data.get("font", "나눔스퀘어"),
        "size": data.get("size", 16),
    }
    (job_dir / "draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    subprocess.Popen([sys.executable, "naver_run.py", str(job_dir)], cwd=str(config.BASE_DIR))
    st.session_state["posting_id"] = chosen_id
    st.success(
        "브라우저를 실행했습니다. 처음이면 네이버 로그인 후 자동으로 진행됩니다.\n"
        f"썸네일은 {job_dir/'thumbnail.png'} 에 저장됨(대표사진으로 직접 지정).\n"
        "임시저장이 끝나면 아래 '발행됨으로 표시'를 눌러주세요."
    )

if st.session_state.get("posting_id"):
    if st.button("✅ 발행됨으로 표시 (목록에서 내림)"):
        store.update_draft(st.session_state["posting_id"], {"status": "posted"})
        del st.session_state["posting_id"]
        st.success("처리 완료")
        st.rerun()
