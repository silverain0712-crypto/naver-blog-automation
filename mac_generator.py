"""맥 초안 생성기 — 폰(Vercel 앱)에서 넣은 글감을 비비 문체로 초안 작성.

동작: Supabase 의 status='generating' 글을 주기적으로 폴링 → app.py 와 똑같은 순서로
  (1) 문체 가이드 로드 → (2) 사진 분석 → (3) 초안 생성 → status='draft_ready' + 결과 저장.
폰은 draft_ready 가 되면 초안을 보여주고 사용자가 수정 후 '맥 자동저장'(status='queued')하면
  기존 mac_worker.py 가 네이버 임시저장한다.

브라우저(크롬)를 쓰지 않으므로 mac_worker.py / mac_poster.py 와 동시에 실행해도 안전하다.
실행: ./start_generator.sh  (또는 python3 mac_generator.py)   종료: Ctrl+C
"""

import datetime
import time

import config
from modules import store
from modules import style_profiler, image_analyzer, post_generator, style_sync, guideline_parser, researcher, benchmark, keyword_stats
from modules import product_info, product_shots
from modules import illustration_planner, card_images, image_finder
from prompts.post_structures import get_structure
from modules.llm import _RETRYABLE

# 정보성/화제성 글(직접 경험 사진이 없는 글 유형)에 자동으로 카드뉴스+스톡 사진을 채운다.
_AUTO_ILLUSTRATE_TYPES = ("info", "hompiid")
_MAX_CARDS = 7
_MAX_STOCK = 4

POLL_SECONDS = 15
GEN_ATTEMPTS = 2  # 간헐 네트워크 끊김 대비, 생성 전체 재시도 횟수

DEFAULT_FONT = "나눔스퀘어"
DEFAULT_SIZE = 15


def _download_images(row: dict) -> list[bytes]:
    images: list[bytes] = []
    for i, path in enumerate(row.get("images") or [], start=1):
        try:
            images.append(store.download(path))
        except Exception as e:
            print(f"  사진 {i} 내려받기 실패: {e}")
    return images


def _captions_from_placement(photo_placement: list[dict]) -> dict:
    """photo_placement → {사진번호(str): 캡션} (worker 가 쓰는 captions 형태)."""
    caps: dict[str, str] = {}
    for p in photo_placement or []:
        num = p.get("photo_number")
        cap = (p.get("caption") or "").strip()
        if num is not None and cap:
            caps[str(num)] = cap
    return caps


def _line_index(lines: list[str], text: str) -> int:
    t = text.strip()
    for i, ln in enumerate(lines):
        if ln.strip() == t:
            return i
    return -1


def _insert_after_line(lines: list[str], target: str, marker: str) -> bool:
    """target 과 정확히 일치하는 줄 뒤에 marker 를 새 줄로 끼워 넣는다. 못 찾으면 False."""
    i = _line_index(lines, target)
    if i < 0:
        return False
    lines[i + 1:i + 1] = ["", marker]
    return True


def _insert_end_of_section(lines: list[str], subheadings: list[str], target: str, marker: str) -> bool:
    """target 소제목 섹션의 '끝'(다음 소제목 직전, 마지막 섹션이면 본문 끝)에 marker 를 넣는다.

    카드가 이미 그 소제목 위에 자리 잡았을 때 스톡 사진을 같은 섹션 아래쪽에 자연스럽게
    보태기 위한 것 — _insert_after_line 처럼 소제목 바로 아래에 겹쳐 넣지 않는다.
    """
    start = _line_index(lines, target)
    if start < 0:
        return False
    end = len(lines)
    if target in subheadings:
        for nxt in subheadings[subheadings.index(target) + 1:]:
            p = _line_index(lines, nxt)
            if p > start:
                end = p
                break
    if end < len(lines):
        # end 자리가 다음 소제목 줄 — 그 앞에는 이미 여백 줄이 있으므로 마커 뒤에만 여백을 둔다.
        lines[end:end] = [marker, ""]
    else:
        # 본문 맨 끝 — 앞쪽 여백이 없으므로 직접 넣는다.
        lines[end:end] = ["", marker]
    return True


def _auto_illustrate(row: dict, post: dict, structure_key: str, title: str) -> list[str]:
    """사진 없는 정보성/화제성 글에 카드뉴스+스톡 사진을 만들어 본문에 꽂아 넣는다.

    실패해도 조용히 건너뛴다(사진 없이도 글 자체는 완성된 상태라 실패가 전체를 막으면 안 됨).
    post["body"]/post["photo_placement"] 를 제자리에서 갱신하고, 업로드된 이미지 경로를
    돌려준다(store.update_draft 의 images 컬럼에 써야 하므로).
    """
    try:
        plan = illustration_planner.plan(title, post.get("body", ""), get_structure(structure_key).get("label", ""))
    except Exception as e:
        print(f"  (삽화 기획 실패, 건너뜀: {str(e)[:80]})")
        return []

    body = post.get("body", "")
    lines = body.split("\n")
    subheadings = list(post.get("subheadings") or [])

    plan_cards = list(plan.get("cards") or [])[:_MAX_CARDS]
    # 실제 본문에 있는 소제목만 채택 — 모델이 소제목을 살짝 바꿔 적으면 자리를 못 찾는다.
    valid_cards = [c for c in plan_cards if any(s.strip() == c.get("subheading", "").strip() for s in subheadings)]

    specs = [
        card_images.CardSpec(
            key=str(i), bg_prompt=c.get("bg_prompt", ""),
            lines=[str(x) for x in (c.get("lines") or [])][:3],
            badge=c.get("badge", ""),
        )
        for i, c in enumerate(valid_cards)
    ]
    cards = []
    if specs and card_images.enabled():
        try:
            cards = card_images.make_cards(specs)
        except Exception as e:
            print(f"  (카드뉴스 생성 실패, 건너뜀: {str(e)[:80]})")

    stock_images: list[bytes] = []
    if config.UNSPLASH_ACCESS_KEY:
        for q in list(plan.get("stock_queries") or [])[:_MAX_STOCK]:
            try:
                img = image_finder.search_unsplash_query(q)
                if img:
                    stock_images.append(img)
            except Exception as e:
                print(f"  (스톡 사진 '{q}' 실패, 건너뜀: {str(e)[:60]})")

    if not cards and not stock_images:
        return []

    key_to_bytes = {c.key: c.data for c in cards}
    n = 0
    photo_placement: list[dict] = []
    uploaded_paths: list[str] = []

    def _upload_next(data: bytes, mime: str, section: str, caption: str) -> None:
        nonlocal n
        n += 1
        ext = "png" if mime == "image/png" else "jpg"
        path = store.upload_bytes(f"{row['id']}/{n}.{ext}", data, mime)
        uploaded_paths.append(path)
        photo_placement.append({"photo_number": n, "section": section, "caption": caption})

    # 먼저 업로드해 번호를 확정한 뒤 그 번호로 마커를 만들어야 순서가 맞는다.
    for i, c in enumerate(valid_cards):
        data = key_to_bytes.get(str(i))
        if not data:
            continue
        n_before = n
        _upload_next(data, "image/png", c.get("subheading", ""), " ".join(c.get("lines") or []))
        _insert_after_line(lines, c["subheading"], f"[사진{n_before + 1}]")

    # 스톡 사진은 소제목 순회하며 각 섹션 '끝'에 하나씩 고르게 분배한다(카드가 이미 있는
    # 소제목이어도 아래쪽에 자연스럽게 보탠다). 소제목이 아예 없을 때만 인사말 뒤로 몰아 넣는다.
    leftover: list[bytes] = []
    for i, data in enumerate(stock_images):
        if subheadings:
            target = subheadings[i % len(subheadings)]
            n_before = n
            _upload_next(data, "image/jpeg", target, "")
            _insert_end_of_section(lines, subheadings, target, f"[사진{n_before + 1}]")
        else:
            leftover.append(data)

    for data in leftover:
        n_before = n
        _upload_next(data, "image/jpeg", "인사말", "")
        marker = f"[사진{n_before + 1}]"
        gi = next((i for i, l in enumerate(lines) if "안녕하세요" in l and "비비" in l), -1)
        if gi >= 0:
            lines.insert(gi + 1, "")
            lines.insert(gi + 2, marker)
        else:
            lines.append("")
            lines.append(marker)

    post["body"] = "\n".join(lines)
    post["photo_placement"] = photo_placement
    print(f"  🖼️ 삽화 자동 생성: 카드 {len(cards)}장 + 스톡 {len(stock_images)}장")
    return uploaded_paths


def _collect_guideline(req: dict) -> str:
    """협찬 가이드/제품 설명서를 하나의 텍스트로 합친다.

    (1) 업로드된 파일들(guideline_paths, 구버전은 단일 guideline_path)을 각각 내려받아
        텍스트 추출, (2) 폰에서 직접 붙여넣은 텍스트(guideline_text) 순으로 이어붙인다.
    파싱 실패한 파일은 건너뛰고 나머지로 계속 진행한다.
    """
    paths = list(req.get("guideline_paths") or [])
    if not paths and req.get("guideline_path"):
        paths = [req["guideline_path"]]
    names = req.get("guideline_names") or []

    parts: list[str] = []
    for i, p in enumerate(paths):
        if not p:
            continue
        name = names[i] if i < len(names) else (req.get("guideline_name") or p)
        try:
            gbytes = store.download(p)
            text = guideline_parser.extract_text(gbytes, name)
            if text.strip():
                parts.append(f"[첨부 {i + 1}: {name}]\n{text.strip()}")
        except Exception as e:
            print(f"  (가이드/설명서 '{name}' 파싱 실패, 무시하고 진행: {str(e)[:60]})")

    pasted = (req.get("guideline_text") or "").strip()
    if pasted:
        parts.append(f"[직접 입력한 가이드/설명]\n{pasted}")

    return "\n\n".join(parts)


def generate(row: dict) -> None:
    data0 = row.get("data") or {}
    req = data0.get("request") or {}
    structure_key = req.get("structure_key", "info")
    photo_style = req.get("photo_style", config.PHOTO_STYLES[0])

    # 수정 재생성: 폰에서 '수정 요청'을 넣으면 data.revision_request 에 담겨 온다.
    # 이 경우 기존 초안(body)을 토대로 요청만 반영해 다시 쓴다(리서치·벤치마킹은 이미
    # 반영돼 있으므로 건너뛰어 빠르게). 처리 후 새 data 에는 안 담겨 자동으로 비워진다.
    revision_request = (data0.get("revision_request") or "").strip()
    previous_body = row.get("body", "") if revision_request else ""
    if revision_request:
        print(f"  ✏️ 수정 요청 반영 재생성: {revision_request[:60]}")

    images = _download_images(row)

    # 지속 학습(elevate): 생성 직전 최근 발행 글을 style_samples 로 수집한다.
    # 새 글이 쌓이면 load_style_guide 가 프로필을 재분석하고 가장 최근 발행 글을
    # few-shot 예시로 인용해, 쓸수록 비비 문체·분량에 가까워진다. 실패해도 생성은 계속.
    blog_id = req.get("blog_id", "bbnation")
    try:
        res = style_sync.sync(blog_id, limit=5)
        if res.get("added"):
            print(f"  📚 발행 글 {len(res['added'])}편 새로 학습에 반영: {', '.join(res['added'])[:80]}")
    except Exception as e:
        print(f"  (발행 글 학습 건너뜀: {str(e)[:60]})")

    # 협찬 가이드 · 제품 설명서 — 파일 여러 개(있으면) + 폰에서 직접 붙여넣은 텍스트를
    # 하나로 합쳐 생성 프롬프트에 주입한다.
    guideline_text = _collect_guideline(req)
    if guideline_text:
        print(f"  📋 가이드/설명서 반영: {len(guideline_text)}자")

    # 웹 검색으로 사실 보강(자동 — 모델이 필요할 때만 검색). 실패해도 생성은 계속.
    # 수정 재생성이면 이미 반영돼 있으므로 건너뛴다(빠르게 요청만 반영).
    research_notes = "" if revision_request else researcher.research(
        keyword=req.get("keyword", ""),
        memo=req.get("memo", ""),
        structure_label=(get_structure(structure_key) or {}).get("label", ""),
        guideline=guideline_text,
    )
    if research_notes:
        print(f"  🔎 웹 리서치 사실 보강: {len(research_notes)}자")

    # 상위노출 벤치마킹(자동 — 경쟁 상위글 구조 분석 + 차별화 포인트). 실패해도 생성은 계속.
    benchmark_notes = "" if revision_request else benchmark.benchmark(
        keyword=req.get("keyword", ""),
        memo=req.get("memo", ""),
        structure_label=(get_structure(structure_key) or {}).get("label", ""),
        guideline=guideline_text,
    )
    if benchmark_notes:
        print(f"  🏆 상위노출 벤치마킹: {len(benchmark_notes)}자")

    # app.py 와 동일 순서: 문체 가이드 → 사진 분석 → 초안 생성
    style_guide = style_profiler.load_style_guide()
    analysis = image_analyzer.analyze_images(images, structure_key, photo_style)
    post = post_generator.generate_post(
        structure_key=structure_key,
        keyword=req.get("keyword", ""),
        product_link=req.get("product_link", ""),
        required_links=req.get("required_links", []),
        memo=req.get("memo", ""),
        length=int(req.get("length", 1500)),
        photo_style=photo_style,
        optional_fields=req.get("optional_fields", {}),
        style_guide=style_guide,
        image_analysis=analysis,
        video_count=0,
        video_desc="",
        guideline=guideline_text,
        research_notes=research_notes,
        benchmark_notes=benchmark_notes,
        revision_request=revision_request,
        previous_body=previous_body,
    )

    titles = post.get("title_candidates") or ["(제목 미정)"]

    # 정보성/화제성 글은 직접 겪은 경험이 아니라 사진이 없다 — 카드뉴스+스톡 사진을
    # 자동으로 만들어 채운다. 사용자 사진이 있으면(예: 협찬 제품 사진) 건드리지 않는다.
    new_images: list[str] = []
    if structure_key in _AUTO_ILLUSTRATE_TYPES and not images and not revision_request:
        new_images = _auto_illustrate(row, post, structure_key, titles[0])

    suggested = post.get("suggested_keywords", [])
    # 메인 키워드(맨 앞) 중심으로 검색량·문서수 조회. API 키 없으면 빈 dict(표시만 생략).
    kw_stats = keyword_stats.fetch_stats(suggested)
    data = {
        "request": req,
        "suggested_keywords": suggested,
        "keyword_stats": kw_stats,
        "title_candidates": titles,
        "thumbnail_title": post.get("thumbnail_title", []),
        "thumbnail_title_options": post.get("thumbnail_title_options", []),
        "subheadings": post.get("subheadings", []),
        "meta_description": post.get("meta_description", ""),
        "hashtags": post.get("hashtags", []),
        "photo_placement": post.get("photo_placement", []),
        "video_placement": post.get("video_placement", []),
        "confirm_needed": post.get("confirm_needed", []),
        # 아래는 mac_worker._build_job_dir 가 쓰는 값
        "blog_id": req.get("blog_id", "bbnation"),
        "captions": _captions_from_placement(post.get("photo_placement", [])),
        "font": DEFAULT_FONT,
        "size": DEFAULT_SIZE,
    }

    update_fields = {
        "status": "draft_ready",
        "title": titles[0],
        "body": post.get("body", ""),
        "data": data,
    }
    if new_images:
        update_fields["images"] = new_images
    store.update_draft(row["id"], update_fields)


def handle_learn_requests() -> None:
    """폰 '발행 글 학습' 버튼(status='learn')을 처리 — 최근 발행 글을 style_samples 로 수집.

    새 글이 추가되면 style_profiler 캐시가 fingerprint 변경으로 자동 무효화되어
    다음 생성부터 재학습된다. 결과(added 편수)는 row 에 담아 폰이 보여준다.
    """
    for row in store.list_drafts("learn"):
        req = (row.get("data") or {}).get("request") or {}
        blog_id = req.get("blog_id", "bbnation")
        print(f"[{datetime.datetime.now():%H:%M:%S}] 📚 발행 글 학습 요청: {blog_id}")
        try:
            res = style_sync.sync(blog_id, limit=10)
            print(f"  ✅ 학습 완료: 새 글 {len(res['added'])}편 (기존 {res['skipped']}편)")
        except Exception as e:
            res = {"added": [], "skipped": 0, "errors": [], "error": str(e)[:200]}
            print(f"  ⚠️ 학습 실패: {str(e)[:80]}")
        store.update_draft(row["id"], {
            "status": "learn_done",
            "data": {**(row.get("data") or {}), "result": res},
        })



def _product_reference(row: dict, link: str) -> tuple[bytes, str]:
    """상품 링크에서 대표 사진과 상품명을 확보한다. 한 번 받아오면 버킷에 캐시한다.

    피드백으로 다시 만들 때마다 상품 페이지를 다시 긁을 이유가 없다(느리고, 네이버에
    불필요한 요청을 보낸다). 그래서 {draft_id}/ref.jpg 로 저장해 두고 재사용한다.
    """
    data = row.get("data") or {}
    cached = data.get("product_ref") or {}
    if cached.get("path"):
        try:
            return store.download(cached["path"]), cached.get("name", "")
        except Exception as e:
            print(f"  캐시된 레퍼런스 사용 실패({str(e)[:60]}) — 다시 가져옵니다")

    print(f"  상품 링크 확인: {link[:70]}")
    info = product_info.fetch(link, log=lambda m: print(f"  {m}"))
    if not info.images:
        raise RuntimeError("상품 페이지에서 사진을 찾지 못했습니다.")
    ref = product_info.download(info.images[0])
    path = store.upload_bytes(f"{row['id']}/ref.jpg", ref, "image/jpeg")
    store.update_draft(row["id"], {"data": {
        **data, "product_ref": {"path": path, "name": info.name, "url": info.url},
    }})
    row["data"] = {**data, "product_ref": {"path": path, "name": info.name, "url": info.url}}
    return ref, info.name


def _finish_shot_job(row: dict, patch: dict) -> None:
    store.update_draft(row["id"], {"data": {**(row.get("data") or {}), **patch}})


def handle_shot_requests() -> None:
    """폰 '상품 사진 생성' 버튼(data.image_job.status='requested')을 처리.

    초안 status 는 건드리지 않는다 — 글은 그대로 두고 사진만 만든다.
    target 이 있으면 그 사진 한 장만 피드백을 반영해 다시 만든다.
    """
    for row in store.list_image_jobs("requested"):
        data = row.get("data") or {}
        job = data.get("image_job") or {}
        link = ((data.get("request") or {}).get("product_link") or "").strip()
        target = job.get("target")
        stamp = f"[{datetime.datetime.now():%H:%M:%S}]"
        print(f"\n{stamp} 🖼  상품 사진 요청: {row['id'][:8]}"
              f"{' (재생성 #' + str(target) + ')' if target is not None else ''}")

        # running 표시를 row 사본에도 반영해야 한다. 아래 단계들이 row["data"] 를 기준으로
        # 다시 update_draft 를 부르는데, 사본이 옛날 값이면 status 가 requested 로 되돌아가
        # 다음 폴링에서 같은 잡을 또 처리한다(사진이 두 번 생성된다).
        job = {**job, "status": "running"}
        data = {**data, "image_job": job}
        row["data"] = data
        _finish_shot_job(row, {"image_job": job})
        try:
            if not product_shots.enabled():
                raise RuntimeError("GEMINI_API_KEY 가 없습니다(.env 확인).")
            if not link:
                raise RuntimeError("상품 링크가 없습니다. 글감에 링크를 넣어주세요.")

            ref, name = _product_reference(row, link)
            data = row.get("data") or {}
            shots = list(data.get("product_shots") or [])

            if target is not None and 0 <= target < len(shots):
                # 피드백 반영 재생성 — 해당 한 장만 교체한다.
                old = shots[target]
                # 피드백은 쌓는다 — '배경 밝게' 뒤에 '각도 위에서' 를 주면 둘 다 반영된다.
                # 서로 충돌하면 나중에 적은 쪽이 뒤에 붙어 우선한다.
                prev_fb = (old.get("feedback") or "").strip()
                new_fb = (job.get("feedback") or "").strip()
                feedback = f"{prev_fb} {new_fb}".strip() if prev_fb else new_fb
                new_bytes = product_shots.revise(ref, old.get("prompt", ""), feedback)
                # 같은 경로에 덮어쓰면 스토리지 캐시가 물려 폰에 옛 사진이 계속 보인다.
                # 그래서 회차를 경로에 넣고, 성공한 뒤 이전 파일을 지운다.
                rev = int(old.get("rev", 0)) + 1
                path = store.upload_bytes(f"{row['id']}/shot_{target + 1}_r{rev}.png",
                                          new_bytes, "image/png")
                if old.get("path") and old["path"] != path:
                    try:
                        store.remove_paths([old["path"]])
                    except Exception as e:
                        print(f"  이전 사진 정리 실패(무시): {str(e)[:60]}")
                shots[target] = {**old, "path": path, "feedback": feedback, "rev": rev}
                print(f"  ✅ #{target + 1} 재생성 완료: {feedback[:40]}")
            else:
                count = int(job.get("count") or 3)
                made = product_shots.make(ref, name, count)
                shots = []
                for i, shot in enumerate(made, start=1):
                    path = store.upload_bytes(f"{row['id']}/shot_{i}.png", shot.data, "image/png")
                    shots.append({"path": path, "key": shot.key, "label": shot.label,
                                  "prompt": shot.prompt, "feedback": "", "rev": 0})
                print(f"  ✅ 상세컷 {len(shots)}장 완성 ({name[:30]})")

            _finish_shot_job(row, {
                "product_shots": shots,
                "image_job": {**job, "status": "done", "error": ""},
            })
        except Exception as e:
            msg = str(e)[:300]
            _finish_shot_job(row, {"image_job": {**job, "status": "error", "error": msg}})
            print(f"  ⚠️ 사진 생성 실패: {msg}")


def main():
    if not store.enabled():
        print("Supabase 미설정(.env 확인). 종료합니다.")
        return
    print("🟢 맥 초안 생성기 시작 — 폰에서 넣은 글감을 비비 문체로 초안 작성합니다.")
    print(f"   {POLL_SECONDS}초마다 확인 / 종료: Ctrl+C")
    while True:
        try:
            handle_learn_requests()
            handle_shot_requests()
            rows = store.list_drafts("generating")
            for row in rows:
                title_hint = ((row.get("data") or {}).get("request") or {}).get("memo", "")[:30]
                print(f"\n[{datetime.datetime.now():%H:%M:%S}] 초안 생성 시작: {title_hint}")
                try:
                    for attempt in range(1, GEN_ATTEMPTS + 1):
                        try:
                            generate(row)
                            print("  ✅ 초안 완성 (draft_ready)")
                            break
                        except _RETRYABLE as e:
                            if attempt < GEN_ATTEMPTS:
                                print(f"  ↻ 네트워크 끊김({str(e)[:60]}) — {attempt}회차 재시도 대기…")
                                time.sleep(20)
                            else:
                                raise
                except Exception as e:
                    msg = str(e)[:300]
                    data = {**(row.get("data") or {}), "error": msg, "error_stage": "generate"}
                    store.update_draft(row["id"], {"status": "error", "data": data})
                    print(f"  ⚠️ 실패: {msg}")
        except Exception as e:
            print(f"폴링 오류(재시도 예정): {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n생성기 종료.")
