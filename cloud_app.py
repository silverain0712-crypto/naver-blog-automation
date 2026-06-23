"""폰용 클라우드 웹앱 — 글감 던지기 → AI 초안+썸네일 → 폰에서 수정 → 공용 저장소에 저장.

브라우저 자동화(네이버)는 없음 → Streamlit Community Cloud 등 가벼운 호스트에 배포 가능.
네이버 임시저장은 맥 포스터(mac_poster.py)가 담당.

Streamlit Cloud 에서는 secrets 가 자동으로 env 로 안 들어가므로, config 임포트 전에 옮긴다.
"""

import os

import streamlit as st

# --- Streamlit Cloud secrets → env (config 임포트 전에) ---
try:
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass

import config
from modules import store  # 가벼움(httpx). 무거운 생성 모듈은 '초안 생성' 시 지연 로드.

st.set_page_config(page_title="비비 글감·초안", page_icon="📝", layout="centered")


def _check_password():
    if not config.APP_PASSWORD or st.session_state.get("authed"):
        return
    st.title("🔒 비비 글감·초안")
    pw = st.text_input("비밀번호", type="password")
    if pw == config.APP_PASSWORD:
        st.session_state["authed"] = True
        st.rerun()
    elif pw:
        st.error("비밀번호가 틀렸습니다.")
    st.stop()


# 비밀번호 잠금 비활성화(공개). 다시 켜려면 아래 줄의 주석을 풀고 APP_PASSWORD 설정.
# _check_password()

st.title("📝 비비 글감·초안")

# --- 사이드바: 상태 + 학습 ---
with st.sidebar:
    st.header("⚙️ 상태")
    if config.ANTHROPIC_API_KEY:
        st.success("Claude 키 OK")
    else:
        st.error("ANTHROPIC_API_KEY 없음")
    if store.enabled():
        st.success("저장소 연결 OK")
    else:
        st.error("Supabase 미설정")
    st.divider()
    st.caption("📚 발행 글 학습")
    sid = st.text_input("블로그 아이디", value="bbnation", key="sync_id")
    if st.button("최신 발행 글 학습"):
        from modules import style_sync
        with st.spinner("가져오는 중..."):
            res = style_sync.sync(sid.strip(), 5)
            if config.STYLE_PROFILE_CACHE.exists():
                config.STYLE_PROFILE_CACHE.unlink()
        st.success(f"새 글 {len(res['added'])}개 학습 (스킵 {res['skipped']})")

tab_new, tab_list = st.tabs(["✍️ 새 초안", "📋 내 목록"])

# ===================== 새 초안 =====================
with tab_new:
    _fk = st.session_state.setdefault("form_gen", 0)
    if st.button("🗒️ 새로 입력", help="현재 초안을 지우고 새 글감을 입력합니다"):
        for k in ["draft", "draft_params", "title_pick", "edit_body", "edit_tags"]:
            st.session_state.pop(k, None)
        st.session_state["form_gen"] = _fk + 1
        st.rerun()

    uploaded = st.file_uploader(
        "사진 업로드 (선택 — 없으면 Pexels 스톡 자동 검색)", type=config.IMAGE_TYPES,
        accept_multiple_files=True, key=f"upload_{st.session_state['form_gen']}",
    )
    if uploaded:
        st.image([f.getvalue() for f in uploaded], width=90)

    c1, c2 = st.columns(2)
    with c1:
        post_type_label = st.selectbox("글 유형", list(config.POST_TYPES.keys()))
    with c2:
        sponsor = st.radio("협찬", config.SPONSOR_TYPES)

    keyword = st.text_input("핵심 키워드 (비우면 AI 제안)")
    product_link = st.text_input("상품 링크 (선택)")
    required_links_raw = st.text_area("필수 삽입 링크 (한 줄에 하나)", height=68)
    memo = st.text_area(
        "메모/글감 (방문이유·후기·가격·주차 등)\n"
        "💡 '이미지 만들어줘' 추가 → DALL-E 3 생성 / 사진 없으면 Pexels 스톡 자동 검색",
        height=120,
    )
    blog_id = st.text_input("내 블로그 아이디", value="bbnation", key="gen_blog")
    cc1, cc2 = st.columns(2)
    with cc1:
        body_font = st.selectbox("본문 폰트", ["나눔스퀘어", "나눔명조", "나눔고딕"], 0)
    with cc2:
        body_size = st.selectbox("본문 크기", [15, 16, 19, 24], 0)

    _rewrite = st.session_state.pop("_do_rewrite", False)
    if st.button("✨ 초안 생성", type="primary", use_container_width=True) or _rewrite:
        if not config.ANTHROPIC_API_KEY:
            st.error("ANTHROPIC_API_KEY 가 없습니다.")
        else:
            _p = st.session_state.get("draft_params") if _rewrite else None
            images = _p["images"] if _p else ([f.getvalue() for f in uploaded] if uploaded else [])
            _structure_key = (_p["structure_key"] if _p else config.POST_TYPES[post_type_label])
            _keyword      = (_p["keyword"]       if _p else keyword)
            _product_link = (_p["product_link"]  if _p else product_link)
            _req_links    = (_p["req_links"]      if _p else [l.strip() for l in required_links_raw.splitlines() if l.strip()])
            _sponsor      = (_p["sponsor"]        if _p else sponsor)
            _memo         = (_p["memo"]           if _p else memo)
            _blog_id      = (_p["blog_id"]        if _p else blog_id)
            _font         = (_p["body_font"]      if _p else body_font)
            _size         = (_p["body_size"]      if _p else int(body_size))

            # 이미지 자동 수급 여부 결정
            from modules.image_finder import detect_generation_request, clean_generation_request
            _use_dalle = detect_generation_request(_memo, _keyword)
            _need_auto_images = not images or _use_dalle  # 사진 없거나 생성 요청 시

            # 파라미터 저장 (다시 쓰기용) — 생성 요청 문구는 정제 후 저장
            _memo_clean = clean_generation_request(_memo) if _use_dalle else _memo
            st.session_state["draft_params"] = dict(
                images=images, structure_key=_structure_key, keyword=_keyword,
                product_link=_product_link, req_links=_req_links, sponsor=_sponsor,
                memo=_memo_clean, blog_id=_blog_id, body_font=_font, body_size=_size,
            )
            # 무거운 모듈(anthropic/PIL)은 여기서만 로드 → 첫 화면 로딩 가볍게.
            from modules import (
                image_analyzer, layout_planner, post_generator,
                style_profiler, thumbnail_maker,
            )
            try:
                with st.status("작업 중...", expanded=True) as s:
                    # --- 이미지 자동 수급 ---
                    if _need_auto_images:
                        from modules.image_finder import fetch_images
                        _label = "DALL-E 3 생성 중…" if _use_dalle else "Pexels 스톡 검색 중…"
                        st.write(_label)
                        auto_imgs, _src = fetch_images(_keyword or _memo_clean[:40], _memo_clean, _use_dalle)
                        images = images + auto_imgs  # 기존 사진이 있으면 앞에 유지
                        st.caption(f"자동 추가: {_src} {len(auto_imgs)}장")
                    st.write("문체 로드…")
                    guide = style_profiler.load_style_guide()
                    st.write("사진 분석…")
                    analysis = image_analyzer.analyze_images(images, _structure_key, "감성 중심")
                    st.write("초안 작성…")
                    post = post_generator.generate_post(
                        structure_key=_structure_key, keyword=_keyword,
                        product_link=_product_link, required_links=_req_links,
                        sponsor_type=_sponsor, memo=_memo_clean, length=1500,
                        photo_style="감성 중심", optional_fields={},
                        style_guide=guide, image_analysis=analysis,
                    )
                    layout = layout_planner.plan_layout(post, analysis)
                    st.write("썸네일…")
                    rep = next((p["index"] for p in analysis.get("photos", [])
                                if p.get("role") == "대표 이미지" and p.get("keep", True)), 1)
                    rep_b = images[rep - 1] if 1 <= rep <= len(images) else images[0]
                    thumb, _ = thumbnail_maker.generate_thumbnail(
                        rep_b, post.get("thumbnail_title") or [_keyword or "REVIEW"])
                    s.update(label="완료", state="complete", expanded=False)
                st.session_state["draft"] = {
                    "post": post, "layout": layout, "images": images,
                    "thumb": thumb, "blog_id": _blog_id, "font": _font,
                    "size": _size,
                }
            except Exception as e:
                st.error(f"오류: {e}")

    # --- 생성 결과 편집 + 저장 ---
    d = st.session_state.get("draft")
    if d:
        post = d["post"]
        st.divider()
        rc1, rc2 = st.columns([3, 1])
        with rc1:
            st.subheader("✏️ 수정 후 저장")
        with rc2:
            if st.button("🔄 다시 쓰기", use_container_width=True):
                st.session_state["_do_rewrite"] = True
                del st.session_state["draft"]
                st.rerun()
        if d.get("thumb"):
            st.image(d["thumb"], caption="썸네일", width=240)
        titles = post.get("title_candidates") or [""]
        st.markdown("**제목 후보 (SEO 최적화) — 하나 고른 뒤 아래에서 수정**")
        chosen = st.radio("제목 후보", titles, key="title_pick", label_visibility="collapsed")
        title = st.text_input("제목 (수정 가능)", value=chosen,
                              key=f"edit_title_{titles.index(chosen)}")
        body = st.text_area("본문", value=post.get("body", ""), height=360, key="edit_body")
        if post.get("suggested_keywords"):
            st.caption("추천 키워드: " + ", ".join(post["suggested_keywords"]))
        tags = post.get("hashtags", [])
        tag_str = " ".join("#" + str(t).lstrip("#") for t in tags)
        hashtags = st.text_input("해시태그 (글 맨 끝에 자동 삽입)", value=tag_str, key="edit_tags")

        auto = st.checkbox(
            "🚀 저장 후 맥에서 네이버 자동 임시저장 (맥 워커가 켜져 있을 때)", value=False,
            help="체크하면 맥이 켜져 있을 때 자동으로 네이버에 임시저장합니다(발행은 직접).",
        )
        if st.button("💾 저장 (목록에 추가)", type="primary", use_container_width=True):
            if not store.enabled():
                st.error("Supabase 가 설정되지 않아 저장할 수 없습니다.")
            else:
                data = {
                    "thumbnail_title": post.get("thumbnail_title", []),
                    "subheadings": post.get("subheadings", []),
                    "captions": {},
                    "font": d["font"], "size": d["size"], "blog_id": d["blog_id"],
                    "suggested_keywords": post.get("suggested_keywords", []),
                    "confirm_needed": post.get("confirm_needed", []),
                }
                final_body = body.rstrip()
                if hashtags.strip():
                    final_body += "\n\n" + hashtags.strip()
                try:
                    with st.spinner("저장 중…"):
                        store.save_draft(title=title, body=final_body, data=data,
                                         images=d["images"], thumbnail=d.get("thumb"),
                                         status=("queued" if auto else "ready"))
                    if auto:
                        st.success("저장 + 맥 자동 임시저장 요청 완료! 맥이 켜져 있으면 곧 처리됩니다.")
                    else:
                        st.success("저장 완료! '내 목록'에서 확인 / 맥에서 임시저장하세요.")
                    del st.session_state["draft"]
                except Exception as e:
                    st.error(f"저장 실패: {e}")

# ===================== 내 목록 =====================
with tab_list:
    if config.APP_PASSWORD and not st.session_state.get("list_authed"):
        pw = st.text_input("비밀번호를 입력하세요", type="password", key="list_pw")
        if st.button("확인", key="list_pw_btn"):
            if pw == config.APP_PASSWORD:
                st.session_state["list_authed"] = True
                st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다.")
        st.stop()

    if not store.enabled():
        st.info("Supabase 를 설정하면 목록이 보입니다.")
    else:
        if st.button("새로고침"):
            st.rerun()
        try:
            drafts = store.list_drafts()
        except Exception as e:
            drafts = []
            st.error(f"목록 불러오기 실패: {e}")
        badge = {"ready": "🟢 대기", "queued": "⏳ 맥 처리대기", "posting": "🔄 처리중",
                 "posted": "✅ 발행됨", "error": "⚠️ 실패", "draft": "📝 작성중"}
        for row in drafts:
            status = row.get("status")
            with st.expander(f"{badge.get(status,'')} · {row.get('title','(제목없음)')}"):
                if status == "error":
                    st.error("자동 저장 실패: " + str((row.get("data") or {}).get("error", "")))
                nt = st.text_input("제목", value=row.get("title", ""), key=f"t_{row['id']}")
                nb = st.text_area("본문", value=row.get("body", ""), height=240, key=f"b_{row['id']}")
                bc1, bc2, bc3 = st.columns(3)
                with bc1:
                    if st.button("수정 저장", key=f"s_{row['id']}"):
                        store.update_draft(row["id"], {"title": nt, "body": nb})
                        st.success("수정됨")
                with bc2:
                    if status in ("ready", "error", "posted") and st.button("🚀 맥 자동저장", key=f"q_{row['id']}"):
                        store.update_draft(row["id"], {"title": nt, "body": nb, "status": "queued"})
                        st.success("맥에 자동저장 요청함")
                        st.rerun()
                with bc3:
                    if st.button("삭제", key=f"d_{row['id']}"):
                        store.delete_draft(row["id"])
                        st.rerun()
