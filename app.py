"""네이버 블로그 글 작성 자동화 — MVP 1단계 (Streamlit).

사진 업로드 + 메모 입력 → 사진 분석 → 초안 생성 → 사진 배치안 → 복사용 결과.
이번 단계에서는 네이버 자동 입력/임시저장은 하지 않는다(검수 후 직접 발행).
"""

import datetime
import io
import json
import subprocess
import sys

import streamlit as st
from PIL import Image

import config
from modules import image_analyzer, post_generator, style_profiler, thumbnail_maker
from modules.layout_planner import plan_layout
from modules.result_format import build_markdown

st.set_page_config(page_title="비비 블로그 초안 생성기", page_icon="📝", layout="wide")

def _check_password():
    """APP_PASSWORD 가 설정돼 있으면 비밀번호 확인(핸드폰/네트워크 접속 보호)."""
    if not config.APP_PASSWORD or st.session_state.get("authed"):
        return
    st.title("🔒 비비 블로그 도구")
    pw = st.text_input("비밀번호", type="password")
    if pw == config.APP_PASSWORD:
        st.session_state["authed"] = True
        st.rerun()
    elif pw:
        st.error("비밀번호가 틀렸습니다.")
    st.stop()


_check_password()

st.title("📝 비비 블로그 초안 생성기")
st.caption(
    "사진과 메모를 넣으면 비비 말투로 초안과 사진 배치안을 만들어 드려요. "
    "**발행/임시저장은 하지 않습니다 — 검수 후 직접 네이버에 붙여넣어 주세요.**"
)

# --- 사이드바: 상태 ---------------------------------------------------------
with st.sidebar:
    st.header("⚙️ 상태")
    if config.ANTHROPIC_API_KEY:
        st.success("Claude API 키 감지됨")
    else:
        st.error("ANTHROPIC_API_KEY 없음 — .env 를 설정해주세요")
    st.success("썸네일: bbnation 템플릿 사용 (로컬 합성)")

    guide_state = st.session_state.get("style_guide")
    if guide_state and guide_state.get("has_samples"):
        st.success(f"문체 샘플 {guide_state['sample_count']}개 학습됨")
    else:
        st.warning("style_samples/ 에 기존 글을 넣으면 문체가 더 비슷해져요")

    st.divider()
    st.caption("📚 발행 글 학습")
    sync_id = st.text_input("블로그 아이디", value="bbnation", key="sync_blog_id")
    sync_n = st.slider("가져올 최신 글 수", 1, 15, 5, key="sync_n")
    if st.button("내가 발행한 최신 글 학습하기"):
        from modules import style_sync, style_profiler as _sp
        with st.spinner("발행 글을 가져와 학습 중..."):
            res = style_sync.sync(sync_id.strip(), sync_n)
            # 캐시 무효화 후 재분석
            if config.STYLE_PROFILE_CACHE.exists():
                config.STYLE_PROFILE_CACHE.unlink()
            st.session_state["style_guide"] = _sp.load_style_guide()
        if res["added"]:
            st.success(f"{len(res['added'])}개 새 글 학습 완료")
            for t in res["added"]:
                st.caption(f"· {t}")
        else:
            st.info(f"새로 추가된 글 없음 (이미 학습됨 {res['skipped']}개)")
        if res["errors"]:
            st.caption(f"본문 추출 실패: {len(res['errors'])}개")

# --- 입력 영역 -------------------------------------------------------------
st.subheader("1. 사진 · 영상 업로드")
uploaded = st.file_uploader(
    "사진을 올려주세요 (네이버는 보통 10장 내외 권장 · 업로드 순서가 기본 흐름이 됩니다)",
    type=config.IMAGE_TYPES,
    accept_multiple_files=True,
)
if uploaded:
    st.image([f.getvalue() for f in uploaded], width=110)

uploaded_videos = st.file_uploader(
    "영상을 올려주세요 (선택 · 보통 1개. 내용 분석은 안 되고 본문에 [영상N]으로 배치됩니다)",
    type=config.VIDEO_TYPES,
    accept_multiple_files=True,
)
if uploaded_videos:
    st.caption(f"영상 {len(uploaded_videos)}개 업로드됨: " + ", ".join(v.name for v in uploaded_videos))
video_desc = st.text_input(
    "영상 한 줄 설명 (선택)", placeholder="예: 뽀식이가 놀이공간에서 노는 모습"
)

st.subheader("2. 글 유형 · 협찬")
c1, c2 = st.columns(2)
with c1:
    post_type_label = st.selectbox("글 유형", list(config.POST_TYPES.keys()))
with c2:
    sponsor_type = st.radio("협찬 여부", config.SPONSOR_TYPES, horizontal=False)

st.subheader("3. 키워드 · 링크 (모두 선택)")
keyword = st.text_input(
    "핵심 키워드 (비우면 AI가 SEO 키워드를 제안)",
    placeholder="예: 분당 아기랑 갈만한 카페 — 비워두셔도 됩니다",
)
product_link = st.text_input(
    "상품 링크", placeholder="https://... (있으면 SEO 키워드 추론에 활용)"
)
required_links_raw = st.text_area(
    "필수 삽입 링크 (한 줄에 하나, 본문에 반드시 삽입)",
    height=80,
    placeholder="본문에 꼭 들어갈 링크를 한 줄에 하나씩.\n예: https://map.naver.com/...",
)

st.subheader("4. 메모 (선택이지만 권장)")
memo = st.text_area(
    "방문/구매 이유, 실제 사용 후기, 가격·위치·주차·준비물, 협찬 여부 등",
    height=120,
    placeholder="아는 정보를 자유롭게 적어주세요. 적을수록 글이 정확해집니다.",
)

with st.expander("5. 추가 입력 (선택)"):
    o1, o2, o3 = st.columns(3)
    with o1:
        title_hint = st.text_input("제목 후보")
        place_name = st.text_input("방문 장소명")
        price = st.text_input("가격")
        parking = st.text_input("주차 정보")
    with o2:
        product_name = st.text_input("제품명")
        brand = st.text_input("브랜드명")
        location = st.text_input("위치")
        reservation = st.text_input("예약 정보")
    with o3:
        pros = st.text_input("좋았던 점")
        cons = st.text_input("아쉬웠던 점")
        audience = st.text_input("추천 대상")
    must_include = st.text_area("꼭 넣고 싶은 문장", height=70)

    s1, s2 = st.columns(2)
    with s1:
        length = st.selectbox("글 길이(자)", config.POST_LENGTHS, index=1)
    with s2:
        photo_style = st.selectbox("사진 노출 방식", config.PHOTO_STYLES)

make_thumbnail = st.checkbox(
    "대표 썸네일도 생성 (bbnation 템플릿: 아치 크롭 + 녹색 제목)",
    value=True,
)

# 추가 입력은 expander 밖에서 기본값이 필요 → get 처리
optional_fields = {
    "title_hint": locals().get("title_hint", ""),
    "must_include": locals().get("must_include", ""),
    "place_name": locals().get("place_name", ""),
    "product_name": locals().get("product_name", ""),
    "brand": locals().get("brand", ""),
    "price": locals().get("price", ""),
    "location": locals().get("location", ""),
    "parking": locals().get("parking", ""),
    "reservation": locals().get("reservation", ""),
    "pros": locals().get("pros", ""),
    "cons": locals().get("cons", ""),
    "audience": locals().get("audience", ""),
}
length = locals().get("length", 1500)
photo_style = locals().get("photo_style", config.PHOTO_STYLES[0])

st.divider()
go = st.button("✨ 초안 생성", type="primary", use_container_width=True)

# --- 실행 ------------------------------------------------------------------
if go:
    if not config.ANTHROPIC_API_KEY:
        st.error("ANTHROPIC_API_KEY 가 없어 실행할 수 없습니다. .env 를 설정해주세요.")
        st.stop()
    if not uploaded:
        st.error("사진을 한 장 이상 올려주세요.")
        st.stop()

    images = [f.getvalue() for f in uploaded]
    video_files = [(v.name, v.getvalue()) for v in (uploaded_videos or [])]
    videos = [b for _, b in video_files]
    required_links = [l.strip() for l in required_links_raw.splitlines() if l.strip()]
    structure_key = config.POST_TYPES[post_type_label]

    try:
        with st.status("작업 중...", expanded=True) as status:
            st.write("기존 글 문체 분석 중...")
            style_guide = style_profiler.load_style_guide()
            st.session_state["style_guide"] = style_guide

            st.write(f"사진 {len(images)}장 분석 중...")
            analysis = image_analyzer.analyze_images(images, structure_key, photo_style)

            st.write("초안 작성 중... (가장 오래 걸리는 단계)")
            post = post_generator.generate_post(
                structure_key=structure_key,
                keyword=keyword,
                product_link=product_link,
                required_links=required_links,
                sponsor_type=sponsor_type,
                memo=memo,
                length=length,
                photo_style=photo_style,
                optional_fields=optional_fields,
                style_guide=style_guide,
                image_analysis=analysis,
                video_count=len(videos),
                video_desc=video_desc,
            )

            st.write("사진·영상 배치안 정리 중...")
            layout = plan_layout(post, analysis, num_videos=len(videos))

            thumb_bytes, thumb_note = None, ""
            if make_thumbnail:
                st.write("썸네일 생성 중...")
                # 대표 이미지로 분류된 사진을 우선, 없으면 첫 사진
                rep_idx = next(
                    (p["index"] for p in analysis.get("photos", [])
                     if p.get("role") == "대표 이미지" and p.get("keep", True)),
                    None,
                ) or next(
                    (p["index"] for p in analysis.get("photos", []) if p.get("keep", True)),
                    1,
                )
                rep_bytes = images[rep_idx - 1] if 1 <= rep_idx <= len(images) else images[0]
                thumb_title = post.get("thumbnail_title") or [
                    keyword or (post.get("title_candidates") or ["REVIEW"])[0]
                ]
                thumb_bytes, thumb_note = thumbnail_maker.generate_thumbnail(
                    rep_bytes, thumb_title
                )

            status.update(label="완료!", state="complete", expanded=False)

        # 결과 저장 및 표시
        st.session_state["result"] = {
            "post": post,
            "layout": layout,
            "analysis": analysis,
            "images": images,
            "video_files": video_files,
            "thumb_bytes": thumb_bytes,
            "thumb_note": thumb_note,
        }
    except Exception as e:
        st.error(f"오류가 발생했습니다: {e}")
        st.stop()

# --- 결과 표시 -------------------------------------------------------------
result = st.session_state.get("result")
if result:
    post = result["post"]
    layout = result["layout"]
    analysis = result["analysis"]
    images = result["images"]

    st.success("초안이 생성되었습니다. 검수 후 네이버 에디터에 직접 붙여넣어 주세요.")

    tabs = st.tabs(
        ["제목/메타", "본문", "사진 배치안", "확인 필요", "썸네일", "전체 마크다운"]
    )

    with tabs[0]:
        if post.get("suggested_keywords"):
            st.markdown("**추천 핵심 키워드**")
            st.write(", ".join(post["suggested_keywords"]))
        st.markdown("**제목 후보**")
        for i, t in enumerate(post.get("title_candidates", []), start=1):
            st.markdown(f"{i}. {t}")
        st.markdown("**메타디스크립션**")
        st.write(post.get("meta_description", ""))
        if post.get("hashtags"):
            st.markdown("**해시태그**")
            st.write(" ".join(f"#{h.lstrip('#')}" for h in post["hashtags"]))

    with tabs[1]:
        st.caption("복사해서 네이버 에디터에 붙여넣으세요. [사진N]은 해당 사진 자리입니다.")
        st.text_area("본문", post.get("body", ""), height=500)

    with tabs[2]:
        if layout.get("warnings"):
            for w in layout["warnings"]:
                st.warning(w)
        photos_by_idx = {p["index"]: p for p in analysis.get("photos", [])}
        for item in layout.get("layout", []):
            n = item["photo_number"]
            cols = st.columns([1, 3])
            with cols[0]:
                if 1 <= n <= len(images):
                    st.image(images[n - 1], width=140)
            with cols[1]:
                st.markdown(
                    f"**{n}번 사진** · {item['section']} ({item['role']})"
                )
                if item.get("caption"):
                    st.caption(f"캡션: {item['caption']}")
        for v in layout.get("video_layout", []):
            note = f" — {v['note']}" if v.get("note") else ""
            st.markdown(f"**🎬 영상 {v['video_number']}** · {v['section']}{note}")
        if analysis.get("excluded"):
            st.info(
                "중복으로 제외 권장된 사진 번호: "
                + ", ".join(map(str, analysis["excluded"]))
            )

    with tabs[3]:
        confirm = post.get("confirm_needed", [])
        if confirm:
            for c in confirm:
                st.markdown(f"- **{c['item']}**: {c['note']}")
        else:
            st.write("확인이 필요한 항목이 없습니다.")

    with tabs[4]:
        if result.get("thumb_bytes"):
            st.image(result["thumb_bytes"], caption="생성된 썸네일", width=400)
            st.download_button(
                "썸네일 내려받기",
                data=result["thumb_bytes"],
                file_name="thumbnail.png",
                mime="image/png",
            )
        note = result.get("thumb_note")
        if note:
            st.caption(note)
        if not result.get("thumb_bytes") and not note:
            st.write("썸네일을 생성하지 않았습니다. (체크박스를 켜고 다시 생성하세요.)")

    with tabs[5]:
        md = build_markdown(post, layout, result.get("thumb_note", ""))
        st.text_area("전체 결과 (마크다운)", md, height=400)
        config.OUTPUT_DIR.mkdir(exist_ok=True)
        fname = f"draft_{datetime.datetime.now():%Y%m%d_%H%M%S}.md"
        (config.OUTPUT_DIR / fname).write_text(md, encoding="utf-8")
        st.download_button(
            "마크다운 내려받기",
            data=md,
            file_name=fname,
            mime="text/markdown",
        )
        st.caption(f"output/{fname} 에도 저장되었습니다.")

    # --- 네이버 임시저장 ---------------------------------------------------
    st.divider()
    st.subheader("📤 네이버 블로그 임시저장")
    st.caption(
        "브라우저가 열립니다. 처음 한 번만 네이버에 직접 로그인하면 다음부터는 유지됩니다. "
        "**발행은 하지 않고 임시저장까지만** 진행하며, 검수/발행은 직접 하세요."
    )
    nc1, nc2 = st.columns([1, 2])
    with nc1:
        blog_id = st.text_input("블로그 아이디", value="bbnation")
    with nc2:
        chosen_title = st.selectbox("사용할 제목", post.get("title_candidates", []))
    fc1, fc2 = st.columns(2)
    with fc1:
        body_font = st.selectbox(
            "본문 폰트", ["나눔스퀘어", "나눔명조", "나눔고딕", "기본서체"], index=0
        )
    with fc2:
        body_size = st.selectbox(
            "본문 글자 크기", [15, 16, 19, 24], index=1,
            help="비비 글은 보통 16~19. 기존 글과 맞을 때까지 조정하세요.",
        )

    if st.button("🌐 브라우저 열어 임시저장하기", type="primary"):
        if not blog_id.strip():
            st.error("블로그 아이디를 입력해주세요.")
        else:
            job_dir = config.OUTPUT_DIR / "naver_jobs" / datetime.datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            job_dir.mkdir(parents=True, exist_ok=True)
            # 사진 저장 (JPEG 정규화)
            img_names = []
            for i, b in enumerate(result["images"], start=1):
                try:
                    im = Image.open(io.BytesIO(b)).convert("RGB")
                    name = f"{i}.jpg"
                    im.save(job_dir / name, "JPEG", quality=90)
                    img_names.append(name)
                except Exception:
                    pass
            # 영상 저장(원본 확장자)
            vid_names = []
            for i, (vname, vb) in enumerate(result.get("video_files", []), start=1):
                ext = (vname.rsplit(".", 1)[-1] if "." in vname else "mp4").lower()
                name = f"v{i}.{ext}"
                (job_dir / name).write_bytes(vb)
                vid_names.append(name)
            # draft.json
            draft = {
                "blog_id": blog_id.strip(),
                "title": chosen_title,
                "body": post.get("body", ""),
                "images": img_names,
                "videos": vid_names,
                "font": body_font,
                "size": int(body_size),
                "captions": {
                    str(p["photo_number"]): p["caption"]
                    for p in post.get("photo_placement", [])
                    if p.get("caption")
                },
                "subheadings": post.get("subheadings", []),
            }
            (job_dir / "draft.json").write_text(
                json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # 별도 프로세스로 브라우저 실행(headed)
            subprocess.Popen(
                [sys.executable, "naver_run.py", str(job_dir)],
                cwd=str(config.BASE_DIR),
            )
            st.success(
                "브라우저를 실행했습니다. 화면에 네이버 글쓰기 창이 곧 뜹니다.\n"
                "- 처음이면 네이버 로그인을 직접 해주세요(로그인되면 자동으로 이어집니다).\n"
                "- 제목/본문이 자동 입력되고 임시저장됩니다. 사진은 [사진N] 위치에 맞게 확인/배치하세요.\n"
                "- 발행은 직접 검수 후 눌러주세요."
            )
            st.caption(f"진행 로그: {job_dir / 'status.log'}  ·  스크린샷도 같은 폴더에 저장됩니다.")
