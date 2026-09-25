"""맥 초안 생성기 — 폰(Vercel 앱)에서 넣은 글감을 비비 문체로 초안 작성.

동작: Supabase 의 status='generating' 글을 주기적으로 폴링 → app.py 와 똑같은 순서로
  (1) 문체 가이드 로드 → (2) 사진 분석 → (3) 초안 생성 → status='draft_ready' + 결과 저장.
폰은 draft_ready 가 되면 초안을 보여주고 사용자가 수정 후 '맥 자동저장'(status='queued')하면
  기존 mac_worker.py 가 네이버 임시저장한다.

브라우저(크롬)를 쓰지 않으므로 mac_worker.py / mac_poster.py 와 동시에 실행해도 안전하다.
실행: ./start_generator.sh  (또는 python3 mac_generator.py)   종료: Ctrl+C
"""

import dataclasses
import datetime
import time

import config
from modules import store
from modules import style_profiler, image_analyzer, post_generator, style_sync, guideline_parser, researcher, benchmark, keyword_stats
from modules import product_info, product_shots
from modules import illustration_planner, card_images
from prompts.post_structures import get_structure
from modules.llm import _RETRYABLE, call_json

# 정보성/화제성 글(직접 경험 사진이 없는 글 유형)에 자동으로 카드뉴스 10장을 채운다.
_AUTO_ILLUSTRATE_TYPES = ("info", "hompiid")

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


def _intro_boundary(lines: list[str]) -> int:
    """큰 제목/요약 블록이 끝나는 빈 줄의 인덱스(없으면 -1).

    2026-09-02: "안녕하세요, 비비입니다" 인사말을 없앤 뒤로, 요약 다음의 빈 줄이
    인트로/본문 경계가 된다(prompts/style_rules.py 지시). 다만 모델이 가끔 키워드
    한 줄 뒤에도 빈 줄을 넣어서, '본문 줄이 최소 2줄(키워드+요약 한 줄 이상) 나온
    뒤의 빈 줄'만 경계로 인정한다 — modules/naver_blog_writer.py 의 intro/rest_text
    분리와 동일한 기준.
    """
    seen = 0
    for i, l in enumerate(lines):
        if not l.strip():
            if seen >= 2:
                return i
        else:
            seen += 1
    return -1


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


def _place_product_shots(shots: list[dict], subheadings: list[str]) -> dict[str, str]:
    """상품샷마다 어느 소제목 아래 놓을지 LLM 으로 판단한다(2026-09-08, 적재적소 배치).

    상품샷은 illustration_planner 와 달리 애초에 '이 소제목용'이라는 기획이 없고
    hero/open/macro/scale 처럼 고정된 역할 라벨만 있어서(modules/product_shots.py),
    본문이 실제로 완성된 뒤에야 어느 소제목과 어울리는지 알 수 있다. 실패해도(키
    없음/네트워크 등) 조용히 빈 dict — 호출부가 기존 방식(도입부 뒤 모아넣기)으로
    완전히 폴백한다.
    반환: {shot["key"]: 배정된 소제목} — 뚜렷하게 안 어울리면 그 key는 아예 빠진다.
    """
    if not shots or not subheadings:
        return {}
    try:
        shot_lines = "\n".join(f"- {s.get('key','')}: {s.get('label','')}" for s in shots)
        sub_lines = "\n".join(f"- {s}" for s in subheadings)
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["assignments"],
            "properties": {
                "assignments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["key", "subheading"],
                        "properties": {
                            "key": {"type": "string"},
                            "subheading": {"type": ["string", "null"],
                                           "description": "아래 소제목 목록 중 하나를 정확히 그대로. 뚜렷하게 어울리는 게 없으면 null."},
                        },
                    },
                },
            },
        }
        result = call_json(
            model=config.RESEARCH_MODEL,
            system=(
                "너는 블로그 편집자다. 상품 상세컷 사진 몇 장을 본문 소제목 중 내용이 "
                "가장 잘 맞는 자리에 배치하려 한다. 각 사진(key: 역할, label: 어떤 컷인지)이 "
                "아래 소제목 목록 중 어디 내용과 가장 관련 있는지 하나씩 골라라. 억지로 "
                "끼워맞추지 말고, 뚜렷하게 어울리는 소제목이 없으면 null로 남겨라."
            ),
            content=[{"type": "text", "text": f"[상품 사진 목록]\n{shot_lines}\n\n[소제목 목록]\n{sub_lines}"}],
            schema=schema,
            max_tokens=500,
        )
        valid = set(subheadings)
        return {
            a["key"]: a["subheading"]
            for a in (result.get("assignments") or [])
            if a.get("key") and a.get("subheading") in valid
        }
    except Exception:
        return {}


def _embed_product_shots(data0: dict, images: list[str], body: str, subheadings: list = None):
    """상품 사진(생성 완료분)이 있으면 본문에 [사진N] 마커로 심고 images 배열에도 반영한다.

    유저 요청(2026-09-04): "사진들 그냥 글에 바로 반영" — 예전엔 편집 화면 '상품 사진
    생성' 패널에만 보이고 본문엔 안 들어갔다. 처음엔 cover 이미지와 같은 자리(도입부
    바로 뒤)에 전부 몰아넣었는데, 유저 요청(2026-09-08)으로 각 사진이 내용상 어울리는
    소제목 아래로 흩어 배치되도록 바꿨다(`_place_product_shots`로 LLM이 배정) —
    배정 실패/불가한 것만 예전처럼 도입부 뒤에 모아 넣는다(안전 폴백).

    본문은 수정 요청/다시쓰기 때마다 통째로 새로 생성돼 마커가 사라지므로, 상품 사진이
    있는 한 본문을 새로 쓸 때마다(`generate()`) 이 함수를 다시 불러 마커를 되살려야
    한다 — 그래서 '이미 심었는지'를 텍스트 존재 여부로 매번 확인한다.

    자리(base index)는 최초 1회 확보하면 계속 재사용한다 — 그래야 피드백으로 사진 한
    장만 다시 찍어도(`handle_shot_requests`) 같은 [사진N] 번호가 그 새 사진을 가리킨다.
    반환: (새 본문, 새 images 배열, base index, captions 패치dict)
    """
    shots = data0.get("product_shots") or []
    if not shots:
        return body, list(images), data0.get("product_shots_img_base"), {}
    images = list(images)
    base = data0.get("product_shots_img_base")
    if base is None:
        base = len(images)
        images.extend(s["path"] for s in shots)
    else:
        while len(images) < base + len(shots):
            images.append("")
        for i, s in enumerate(shots):
            images[base + i] = s["path"]  # 피드백 재생성으로 경로가 바뀌었을 수 있어 항상 동기화

    markers = [f"[사진{base + i + 1}]" for i in range(len(shots))]
    captions_patch = {str(base + i + 1): s["label"] for i, s in enumerate(shots) if s.get("label")}

    # 이미 있는 마커는 건드리지 않는다(유저가 편집화면에서 마커 하나만 손으로 지운
    # 경우, 없어진 것만 다시 심는다 — 있는 것까지 다 같이 넣으면 중복이 생긴다).
    missing = [(m, s) for m, s in zip(markers, shots) if not (body and m in body)]
    if not missing:
        return body, images, base, captions_patch

    lines = body.split("\n") if body else [""]
    placement = _place_product_shots([s for _, s in missing], subheadings or [])

    leftover = []
    for marker, shot in missing:
        sub = placement.get(shot.get("key"))
        if sub and _insert_after_line(lines, sub, marker):
            continue
        leftover.append(marker)

    if leftover:
        gi = _intro_boundary(lines)
        insert_at = gi + 1 if gi >= 0 else 0  # cover 이미지와 동일한 자리(도입부 바로 뒤)
        lines[insert_at:insert_at] = leftover + [""]
    return "\n".join(lines), images, base, captions_patch


def _card_spec_from_item(i: int, it: dict, footer_note: str) -> "card_images.CardSpec":
    role = it.get("role") if it.get("role") in card_images.ROLES else "card"
    return card_images.CardSpec(
        key=str(i), role=role,
        badge=it.get("badge", ""),
        title=it.get("title", ""),
        subtitle=it.get("subtitle", ""),
        lines=[str(x) for x in (it.get("lines") or [])][:3],
        caption=it.get("caption", ""),
        milestones=list(it.get("milestones") or [])[:4],
        rows=list(it.get("rows") or [])[:4],
        before_label=it.get("before_label", "") or "기존",
        after_label=it.get("after_label", "") or "변경안",
        items=[str(x) for x in (it.get("checklist_items") or [])][:5],
        stock_query=it.get("stock_query", ""),
        bg_prompt=it.get("bg_prompt", ""),
        footer_note=footer_note if role != "lifestyle" else "",
    )


def _auto_illustrate(row: dict, post: dict, structure_key: str, title: str) -> list[str]:
    """사진 없는 정보성/화제성 글에 카드뉴스 10장(표지/타임라인/비교표/카드형/체크리스트/실사)을
    만들어 본문에 꽂아 넣는다.

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
    footer_note = plan.get("footer_note", "") if plan.get("is_draft_policy") else ""

    plan_items = list(plan.get("items") or [])
    specs = [_card_spec_from_item(i, it, footer_note) for i, it in enumerate(plan_items)]
    if not specs:
        return []
    if not card_images.enabled():
        print("  (힉스필드 키 없음 — 삽화 생성 건너뜀)")
        return []

    try:
        cards = card_images.make_cards(specs)
    except Exception as e:
        print(f"  (카드뉴스 생성 실패, 건너뜀: {str(e)[:80]})")
        return []
    if not cards:
        return []

    key_to_item = {str(i): it for i, it in enumerate(plan_items)}
    key_to_card = {c.key: c for c in cards}
    key_to_spec = {s.key: s for s in specs}
    n = 0
    photo_placement: list[dict] = []
    uploaded_paths: list[str] = []
    card_specs: list[dict] = []

    def _upload_next(key: str, data: bytes, section: str, caption: str) -> str:
        nonlocal n
        n += 1
        path = store.upload_bytes(f"{row['id']}/{n}.png", data, "image/png")
        uploaded_paths.append(path)
        photo_placement.append({"photo_number": n, "section": section, "caption": caption})
        spec = key_to_spec.get(key)
        if spec is not None:
            # 재생성용 원본 스펙 저장 — 이미지 피드백('이 카드만 다시 만들기')이 여길 읽어
            # 같은 내용으로 다시 만들고 feedback 만 얹는다.
            card_specs.append({"photo_number": n, "spec": dataclasses.asdict(spec),
                                "feedback": "", "rev": 0})
        return f"[사진{n}]"

    def _caption_of(it: dict) -> str:
        return it.get("caption") or " ".join(it.get("lines") or []) or it.get("title", "")

    # 1) cover 는 항상 맨 앞(큰 제목/요약 블록 바로 뒤, 도입부 앞) — 이 글의 대표 이미지.
    gi = _intro_boundary(lines)
    for key, it in list(key_to_item.items()):
        if it.get("role") != "cover" or key not in key_to_card:
            continue
        marker = _upload_next(key, key_to_card[key].data, "도입부", _caption_of(it))
        if gi >= 0:
            lines[gi + 1:gi + 1] = [marker, ""]
        else:
            lines[0:0] = [marker, ""]
        key_to_item.pop(key)
        key_to_card.pop(key)

    # 2) 나머지는 subheading 이 본문과 정확히 일치하면 그 아래에 바로 꽂는다.
    unplaced: list[tuple] = []
    for key, it in key_to_item.items():
        card = key_to_card.get(key)
        if not card:
            continue
        sub = (it.get("subheading") or "").strip()
        if sub and sub in subheadings:
            marker = _upload_next(key, card.data, sub, _caption_of(it))
            _insert_after_line(lines, sub, marker)
        else:
            unplaced.append((key, it, card))

    # 3) subheading 매칭 실패분은 소제목을 고르게 순회하며 섹션 '끝'에 넣는다(그마저 없으면
    #    도입부 앞으로). 카드형이 이미 있는 소제목이어도 아래쪽에 자연스럽게 보탠다.
    for i, (key, it, card) in enumerate(unplaced):
        if subheadings:
            target = subheadings[i % len(subheadings)]
            marker = _upload_next(key, card.data, target, _caption_of(it))
            _insert_end_of_section(lines, subheadings, target, marker)
        else:
            marker = _upload_next(key, card.data, "도입부", _caption_of(it))
            gi2 = _intro_boundary(lines)
            if gi2 >= 0:
                lines[gi2 + 1:gi2 + 1] = [marker, ""]
            else:
                lines += ["", marker]

    post["body"] = "\n".join(lines)
    post["photo_placement"] = photo_placement
    post["card_specs"] = sorted(card_specs, key=lambda c: c["photo_number"])
    print(f"  🖼️ 삽화 자동 생성: {len(cards)}장 "
          f"({', '.join(sorted({s.role for s in specs}))})")
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
        is_sponsored=bool(req.get("is_sponsored")),
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
        "card_specs": post.get("card_specs", []),
        "video_placement": post.get("video_placement", []),
        "confirm_needed": post.get("confirm_needed", []),
        # 아래는 mac_worker._build_job_dir 가 쓰는 값
        "blog_id": req.get("blog_id", "bbnation"),
        "captions": _captions_from_placement(post.get("photo_placement", [])),
        "font": DEFAULT_FONT,
        "size": DEFAULT_SIZE,
    }
    # 상품 사진 관련 필드는 재생성 때 새 data 에 안 담겨 사라지면 안 되므로 이어받는다
    # (handle_shot_requests 가 같은 폴링 루프에서 방금 채웠을 수도, 예전부터 있을 수도 있다).
    for k in ("product_shots", "product_ref", "product_shots_img_base"):
        if k in data0:
            data[k] = data0[k]

    # 상품 사진이 있으면 본문에 [사진N] 마커로 심는다 — 본문을 새로 쓸 때마다 마커가
    # 사라지므로 매번 다시 심어야 한다.
    base_images = new_images if new_images else list(row.get("images") or [])
    body_text, embed_images, embed_base, captions_patch = _embed_product_shots(
        data0, base_images, post.get("body", ""), post.get("subheadings") or [])
    if embed_base is not None:
        data["product_shots_img_base"] = embed_base
    if captions_patch:
        data["captions"] = {**data.get("captions", {}), **captions_patch}

    update_fields = {
        "status": "draft_ready",
        "title": titles[0],
        "body": body_text,
        "data": data,
    }
    if new_images or embed_images != (row.get("images") or []):
        update_fields["images"] = embed_images
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
            # 본문이 이미 완성돼 있으면(status != generating) generate() 가 다시 안 도니까
            # 여기서 바로 마커를 심는다. 아직 작성 중(generating)이면 generate() 완료 시
            # 알아서 심긴다.
            # 실측(2026-09-04, "에이블 트라이크" 글): 예전엔 status == "draft_ready" 일
            # 때만 심었는데, 사진 생성이 끝나기 전에 draft_ready → posted 로 이미 넘어가
            # 버리는 경우가 있었다 — 그 경우 사진이 Storage 에는 저장됐지만 본문/images
            # 배열엔 영영 반영되지 않았다(image_job 은 done 인데 사진이 안 보이는 버그).
            fresh = store.get_draft(row["id"])
            if fresh and fresh.get("status") != "generating":
                fdata = {**(fresh.get("data") or {}), "product_shots": shots}
                body_text, embed_images, embed_base, captions_patch = _embed_product_shots(
                    fdata, list(fresh.get("images") or []), fresh.get("body") or "",
                    fdata.get("subheadings") or [])
                if embed_base is not None:
                    fdata["product_shots_img_base"] = embed_base
                if captions_patch:
                    fdata["captions"] = {**fdata.get("captions", {}), **captions_patch}
                store.update_draft(row["id"], {
                    "body": body_text, "images": embed_images, "data": fdata,
                })
                print("  🖼 본문에 상품 사진 반영 완료")
        except Exception as e:
            msg = str(e)[:300]
            _finish_shot_job(row, {"image_job": {**job, "status": "error", "error": msg}})
            print(f"  ⚠️ 사진 생성 실패: {msg}")


def handle_card_requests() -> None:
    """폰 '이 카드 다시 만들기' 버튼(data.card_job.status='requested')을 처리.

    handle_shot_requests() 와 같은 패턴 — 초안 status 는 안 건드리고 그 사진 한 장만
    data.card_specs 에 저장해둔 원본 스펙에 피드백을 얹어 다시 만든다(내용·구도는 그대로,
    피드백만 반영). 같은 경로에 덮어쓰면 Supabase 캐시가 물려 폰에 옛 사진이 보이므로
    [[product-shot-generation]] 과 동일하게 `_r{rev}` 파일명으로 새로 올리고 이전 파일은 지운다.
    """
    for row in store.list_card_jobs("requested"):
        data = row.get("data") or {}
        job = data.get("card_job") or {}
        target = job.get("target")
        stamp = f"[{datetime.datetime.now():%H:%M:%S}]"
        print(f"\n{stamp} 🖼  카드 재생성 요청: {row['id'][:8]} (#{target})")

        job = {**job, "status": "running"}
        data = {**data, "card_job": job}
        row["data"] = data
        store.update_draft(row["id"], {"data": data})
        try:
            specs = list(data.get("card_specs") or [])
            entry = next((s for s in specs if s.get("photo_number") == target), None)
            if entry is None:
                raise RuntimeError("이 사진은 자동 생성된 카드가 아니라 다시 만들 수 없습니다.")
            if not card_images.enabled() and (entry.get("spec") or {}).get("role") == "lifestyle":
                raise RuntimeError("HF_API_KEY/HF_API_SECRET 가 없습니다(.env 확인).")

            prev_fb = (entry.get("feedback") or "").strip()
            new_fb = (job.get("feedback") or "").strip()
            feedback = f"{prev_fb} {new_fb}".strip() if prev_fb else new_fb

            spec_fields = dict(entry.get("spec") or {})
            spec_fields["feedback"] = feedback
            spec = card_images.CardSpec(**spec_fields)
            cards = card_images.make_cards([spec])
            if not cards:
                raise RuntimeError("이미지를 만들지 못했습니다.")

            rev = int(entry.get("rev", 0)) + 1
            path = store.upload_bytes(f"{row['id']}/{target}_r{rev}.png", cards[0].data, "image/png")

            images = list(row.get("images") or [])
            idx = target - 1
            old_path = images[idx] if 0 <= idx < len(images) else None
            if 0 <= idx < len(images):
                images[idx] = path
            else:
                images.append(path)
            if old_path and old_path != path:
                try:
                    store.remove_paths([old_path])
                except Exception as e:
                    print(f"  이전 카드 정리 실패(무시): {str(e)[:60]}")

            new_specs = [
                {**s, "feedback": feedback, "rev": rev} if s.get("photo_number") == target else s
                for s in specs
            ]
            store.update_draft(row["id"], {
                "images": images,
                "data": {**data, "card_specs": new_specs, "card_job": {**job, "status": "done", "error": ""}},
            })
            print(f"  ✅ 카드 #{target} 재생성 완료: {feedback[:40]}")
        except Exception as e:
            msg = str(e)[:300]
            store.update_draft(row["id"], {"data": {**data, "card_job": {**job, "status": "error", "error": msg}}})
            print(f"  ⚠️ 카드 재생성 실패: {msg}")


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
            handle_card_requests()
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
