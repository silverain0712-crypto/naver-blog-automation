"""네이버 블로그 에디터(SmartEditor ONE) 자동 입력 + 임시저장 (Playwright).

원칙:
- 아이디/비번을 코드에 저장하지 않는다. 브라우저 프로필(persistent context)에 1회 로그인.
- '발행' 버튼은 절대 누르지 않는다. '임시저장(저장)'까지만.
- 깨지기 쉬운 부분이라 각 단계 스크린샷과 로그를 남기고, 자동 입력 실패 시
  전체 본문을 클립보드에 복사 + 사진 폴더를 열어 수동 보완이 가능하게 한다.

run_job(job_dir): job_dir/draft.json 을 읽어 실행한다.
draft.json = {blog_id, title, body, images:[파일명...], videos:[파일명...]}
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import config

USERDATA_DIR = Path.home() / ".naver_blog_automation" / "userdata"
FORBIDDEN = ("발행",)  # 절대 클릭 금지 텍스트

WRITE_URL = "https://blog.naver.com/{blog_id}?Redirect=Write"


def _logger(job_dir: Path):
    logpath = job_dir / "status.log"

    def log(msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with logpath.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    return log


def _pbcopy(text: str):
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    except Exception:
        pass


def _shot(page_or_frame, job_dir: Path, name: str):
    try:
        # frame 에는 screenshot 이 없을 수 있어 page 우선
        page = getattr(page_or_frame, "page", None) or page_or_frame
        page.screenshot(path=str(job_dir / f"{name}.png"))
    except Exception:
        pass


def _wait_logged_in(page, log, timeout_s: int = 600):
    """nid.naver.com 로그인 페이지면 사용자가 직접 로그인할 때까지 대기."""
    start = time.time()
    warned = False
    while time.time() - start < timeout_s:
        url = page.url
        if "nid.naver.com" not in url and "blog.naver.com" in url:
            return True
        if not warned:
            log("로그인이 필요합니다. 열린 브라우저 창에서 네이버에 로그인해주세요. "
                "(2단계 인증 포함, 직접 진행) — 로그인되면 자동으로 이어집니다.")
            warned = True
        time.sleep(2)
    log("로그인 대기 시간이 초과됐습니다. 다시 시도해주세요.")
    return False


def _editor_frame(page, log, timeout_s: int = 60):
    """SmartEditor 본문이 든 프레임을 '내용'으로 탐지한다.

    iframe 의 name 속성이 로딩 직후엔 비어 있어 page.frame(name='mainFrame') 은
    실패할 수 있다. 그래서 .se-documentTitle 이 들어있는 프레임을 직접 찾는다.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in page.frames:
            try:
                if f.locator(".se-documentTitle").count() > 0:
                    if f.name:
                        log(f"에디터 프레임 발견 (name={f.name}).")
                    else:
                        log("에디터 프레임 발견 (내용 기준).")
                    return f
            except Exception:
                pass
        time.sleep(1)
    log("경고: 에디터 프레임을 찾지 못했습니다. 페이지 구조가 다를 수 있어요.")
    return page


def _dismiss_continue_popup(scope, log):
    """'작성 중인 글이 있습니다 이어서 작성하시겠어요?' 팝업이 뜨면 '취소'(새 글)."""
    candidates = [
        "button:has-text('취소')",
        "button:has-text('새로 작성')",
        ".se-popup-button-cancel",
    ]
    for sel in candidates:
        try:
            loc = scope.locator(sel)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=2000)
                log("이전 작성글 팝업: 취소(새 글로 시작).")
                return
        except Exception:
            continue


def _fill_title(frame, title, log):
    candidates = [
        ".se-section-documentTitle .se-text-paragraph",
        ".se-documentTitle .se-text-paragraph",
        ".se-title-text",
        "span.se-placeholder",
    ]
    for sel in candidates:
        try:
            loc = frame.locator(sel).first
            if loc.count():
                loc.click(timeout=3000)
                frame.page.keyboard.insert_text(title)
                log(f"제목 입력 성공 (selector: {sel}).")
                return True
        except Exception:
            continue
    log("경고: 제목 입력란을 찾지 못했습니다.")
    return False


def _fill_body(frame, body, log):
    candidates = [
        ".se-section-text .se-text-paragraph",
        ".se-main-container .se-text-paragraph",
        ".se-component.se-text .se-text-paragraph",
    ]
    target = None
    for sel in candidates:
        try:
            loc = frame.locator(sel)
            if loc.count():
                # 제목 섹션이 아닌 본문 첫 문단을 찾는다(보통 마지막 후보)
                target = loc.last
                target.click(timeout=3000)
                break
        except Exception:
            continue
    if target is None:
        log("경고: 본문 입력란을 찾지 못했습니다. (클립보드에 본문을 복사해뒀어요)")
        return False

    kb = frame.page.keyboard
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line:
            kb.insert_text(line)
        if i < len(lines) - 1:
            kb.press("Enter")
    log("본문 입력 성공.")
    return True


def _clear_format_toggles(frame, log):
    """본문 입력 전, 켜져 있는 서식 토글(취소선/굵게/기울임/밑줄)을 끈다.

    네이버 에디터는 토글 상태가 프로필에 남아, 켜진 채면 입력 글에 그대로 적용된다.
    활성 표시는 class 에 'se-is-selected'.
    """
    toggles = [
        ("취소선", "button.se-strikethrough-toolbar-button"),
        ("굵게", "button.se-bold-toolbar-button"),
        ("기울임", "button.se-italic-toolbar-button"),
        ("밑줄", "button.se-underline-toolbar-button"),
    ]
    for name, sel in toggles:
        try:
            b = frame.locator(sel).first
            if b.count() and "se-is-selected" in (b.get_attribute("class") or ""):
                b.click(timeout=2000)
                log(f"서식 해제: {name}")
        except Exception:
            continue


def _set_text_style(frame, family, size, log):
    """본문 글자 크기/폰트를 비비 글에 맞게 설정(커서 위치 기준, 이후 입력에 적용).

    size: 정수(예: 16, 19). family: 폰트 표시명(예: '나눔스퀘어').
    """
    if size:
        try:
            btn = frame.locator("button.se-font-size-code-toolbar-button").first
            btn.click(timeout=2000)
            time.sleep(0.3)
            opt = frame.locator(f"button[data-value='fs{size}']")
            if opt.count():
                opt.first.click(timeout=2000)
                log(f"글자 크기 {size} 적용")
            else:
                btn.click()  # 닫기
        except Exception as e:
            log(f"글자 크기 설정 실패: {e}")
    if family:
        try:
            btn = frame.locator("button.se-font-family-toolbar-button").first
            btn.click(timeout=2000)
            time.sleep(0.3)
            opt = frame.locator(".se-toolbar-option-font-family button", has_text=family)
            if not opt.count():
                opt = frame.get_by_role("button", name=family)
            if opt.count():
                opt.first.click(timeout=2000)
                log(f"폰트 '{family}' 적용")
            else:
                btn.click()  # 닫기
        except Exception as e:
            log(f"폰트 설정 실패: {e}")


def _set_color(frame, color, log):
    """글자색 지정. color=None 이면 '색상 없음'(기본 검정). 예: '#999999'(회색)."""
    try:
        btn = frame.locator("button.se-font-color-toolbar-button").first
        btn.click(timeout=2000)
        time.sleep(0.3)
        if color is None:
            sw = frame.locator("button.se-color-palette-no-color")
        else:
            sw = frame.locator(f"button.se-color-palette[data-color='{color}']")
        if sw.count():
            sw.first.click(timeout=2000)
            log(f"글자색 {'없음(검정)' if color is None else color} 적용")
        else:
            btn.click()  # 못 찾으면 닫기
    except Exception as e:
        log(f"글자색 설정 실패: {e}")


def _type_lines(kb, text):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            kb.insert_text(line)
        if i < len(lines) - 1:
            kb.press("Enter")


def _set_bold(frame, on, log):
    """볼드 토글을 원하는 상태로 맞춘다."""
    try:
        b = frame.locator("button.se-bold-toolbar-button").first
        if not b.count():
            return
        active = "se-is-selected" in (b.get_attribute("class") or "")
        if active != on:
            b.click(timeout=2000)
    except Exception as e:
        log(f"볼드 설정 실패: {e}")


def _insert_divider(frame, log, style="line5"):
    """구분선 삽입(기본 사선 line5). 실패 시 기본 구분선."""
    try:
        opt = frame.locator(
            "button.se-document-toolbar-select-option-button[data-name='horizontal-line']"
        ).first
        if opt.count():
            opt.click(timeout=2000)
            time.sleep(0.4)
            sv = frame.locator(f"button[data-value='{style}']")
            if sv.count():
                sv.first.click(timeout=2000)
                log(f"구분선({style}) 삽입")
                time.sleep(1)
                return
        frame.locator("button.se-insert-horizontal-line-default-button").first.click(timeout=2000)
        log("기본 구분선 삽입")
        time.sleep(1)
    except Exception as e:
        log(f"구분선 삽입 실패: {e}")


def _ensure_strike_off(frame, log):
    """취소선이 켜져 있으면 끈다(프로필에 켜진 채 남아 글에 적용되는 문제 방지)."""
    try:
        b = frame.locator("button.se-strikethrough-toolbar-button").first
        if b.count() and "se-is-selected" in (b.get_attribute("class") or ""):
            b.click(timeout=2000)
            log("취소선 해제")
    except Exception:
        pass


_BULLET_RE = re.compile(r"^[\s​]*[●○◦•·▪◾◆■※★☆*\-–]+\s*")


def _strip_bullet(s: str) -> str:
    return _BULLET_RE.sub("", s).strip()


# 스타일 상수 (비비 글 기준)
_GRAY_FONT = "나눔명조"
_GRAY_SIZE = 11
_GRAY_COLOR = "#999999"
_TITLE_FONT = "나눔명조"   # 큰 제목
_TITLE_SIZE = 30
_SUB_FONT = "나눔명조"     # 소제목 폰트
_SUB_SIZE = 19            # 소제목(검정 볼드)
_NOTICE_KEYS = ("수수료", "제공받아", "제공 받아", "원고료", "협찬",
                "쇼핑 커넥트", "쇼핑커넥트", "무상으로", "대가를")


def _insert_image(frame, page, paths, log):
    """현재 커서 위치에 사진을 삽입. 여러 장이면 '개별사진' 레이아웃 선택."""
    with page.expect_file_chooser(timeout=8000) as fc:
        frame.locator("button.se-image-toolbar-button").first.click(timeout=4000)
    fc.value.set_files([str(p) for p in paths])
    time.sleep(2)
    # 여러 장 첨부 시 '사진 첨부 방식'(개별사진/콜라주/슬라이드) 팝업 → 개별사진
    try:
        dlg = frame.locator(":text('개별사진')")
        if dlg.count():
            dlg.first.click(timeout=3000)
    except Exception:
        pass
    time.sleep(4)


def _fill_body_with_media(frame, page, body, image_paths, log, captions=None,
                          subheadings=None, family="나눔스퀘어", size=16):
    """본문을 비비 글 형식으로 입력한다.

    구조: (협찬 고지문 회색) → 큰 제목(나눔명조24 볼드) → 요약(회색 나눔명조11)
          → 구분선 → 안녕하세요+본문(나눔스퀘어) → [사진N] 위치 사진+회색 캡션.
    소제목은 검정 볼드 19, [영상N]은 마커로 남긴다.
    """
    target = None
    for sel in [".se-section-text .se-text-paragraph",
                ".se-main-container .se-text-paragraph",
                ".se-component.se-text .se-text-paragraph"]:
        try:
            loc = frame.locator(sel)
            if loc.count():
                target = loc.last
                target.click(timeout=3000)
                break
        except Exception:
            continue
    if target is None:
        log("경고: 본문 입력란을 찾지 못했습니다. (클립보드에 본문을 복사해뒀어요)")
        return False

    time.sleep(0.6)  # 툴바가 현재 서식 상태(프로필 취소선 등)를 반영하도록 잠깐 대기
    _clear_format_toggles(frame, log)
    kb = page.keyboard
    captions = captions or {}
    # 소제목 비교는 불릿/기호 제거 후 정규화해서 매칭
    sub_set = {_strip_bullet(s) for s in (subheadings or []) if _strip_bullet(s)}

    def title_on():
        _set_text_style(frame, _TITLE_FONT, _TITLE_SIZE, log)
        _set_color(frame, None, log)
        _set_bold(frame, True, log)
        _ensure_strike_off(frame, log)

    def gray_on():
        _set_bold(frame, False, log)
        _set_text_style(frame, _GRAY_FONT, _GRAY_SIZE, log)
        _set_color(frame, _GRAY_COLOR, log)
        _ensure_strike_off(frame, log)

    def sub_on():
        _set_text_style(frame, _SUB_FONT, _SUB_SIZE, log)
        _set_color(frame, None, log)
        _set_bold(frame, True, log)
        _ensure_strike_off(frame, log)

    def body_on():
        _set_bold(frame, False, log)
        _set_color(frame, None, log)
        _set_text_style(frame, family, size, log)
        _ensure_strike_off(frame, log)

    # 인사말 기준 분리
    lines = body.split("\n")
    g_idx = next((i for i, l in enumerate(lines) if "안녕하세요" in l and "비비" in l), None)
    intro = lines[:g_idx] if g_idx is not None else []
    rest_text = "\n".join(lines[g_idx:]) if g_idx is not None else body

    # 인트로: 고지문(회색) → 큰 제목(첫 비고지 줄) → 요약(나머지, 회색)
    notice = [s.strip() for s in intro if s.strip() and any(k in s for k in _NOTICE_KEYS)]
    content = [s.strip() for s in intro if s.strip() and not any(k in s for k in _NOTICE_KEYS)]
    for s in notice:
        gray_on(); _type_lines(kb, s); kb.press("Enter"); kb.press("Enter")
    if content:
        title_on(); _type_lines(kb, content[0]); kb.press("Enter"); kb.press("Enter")
        log("큰 제목(나눔명조24 볼드) 적용.")
        if content[1:]:
            gray_on(); _type_lines(kb, "\n".join(content[1:])); kb.press("Enter")
            log("요약 블록(회색) 적용.")
    # 구분선
    body_on()
    _insert_divider(frame, log, "line5")
    body_on()  # 구분선 뒤 본문 스타일 재적용(검정 나눔스퀘어)

    inserted = 0
    tokens = re.split(r"(\[사진\s*\d+\]|\[영상\s*\d+\])", rest_text)
    for tok in tokens:
        mp = re.match(r"\[사진\s*(\d+)\]", tok)
        mv = re.match(r"\[영상\s*(\d+)\]", tok)
        if mp:
            n = int(mp.group(1))
            if 1 <= n <= len(image_paths):
                try:
                    kb.press("Enter")
                    _insert_image(frame, page, [image_paths[n - 1]], log)
                    _clear_format_toggles(frame, log)
                    inserted += 1
                    log(f"[사진{n}] 위치에 삽입 완료.")
                    cap = captions.get(str(n)) or captions.get(n)
                    if cap:
                        gray_on(); _type_lines(kb, cap); log(f"[사진{n}] 캡션(회색) 입력.")
                    body_on()
                except Exception as e:
                    log(f"[사진{n}] 삽입 실패({e}). 마커로 남깁니다.")
                    kb.insert_text(tok)
            else:
                kb.insert_text(tok)
        elif mv:
            kb.insert_text(tok)  # 영상은 자동 업로드 안 함 → 마커 유지
        else:
            body_lines = tok.split("\n")
            for i, line in enumerate(body_lines):
                norm = _strip_bullet(line)
                if norm and norm in sub_set:
                    # 소제목: ● 등 불릿 제거 + 나눔명조 19 검정 볼드
                    sub_on(); kb.insert_text(norm); body_on()
                elif line:
                    kb.insert_text(line)
                if i < len(body_lines) - 1:
                    kb.press("Enter")
    log(f"본문 입력 완료. 사진 {inserted}장 삽입, 소제목 {len(sub_set)}개 스타일.")
    return True


def _upload_photos(frame, image_paths, log):
    """best-effort: 사진 툴바 버튼 → 파일 선택기로 전체 사진 업로드(본문 끝에 추가됨).

    실패해도 흐름을 막지 않는다. 위치는 본문의 [사진N] 마커로 표시되어 있으니
    사용자가 드래그로 옮기면 된다.
    """
    if not image_paths:
        return
    btn_candidates = [
        "button[data-name='image']",
        "button.se-image-toolbar-button",
        "button[aria-label*='사진']",
        ".se-toolbar-item-image button",
    ]
    for sel in btn_candidates:
        try:
            btn = frame.locator(sel).first
            if not btn.count():
                continue
            with frame.page.expect_file_chooser(timeout=5000) as fc:
                btn.click(timeout=3000)
            fc.value.set_files([str(p) for p in image_paths])
            log(f"사진 {len(image_paths)}장 업로드 시도(본문 끝에 추가). 위치는 [사진N] 마커 참고.")
            return
        except Exception:
            continue
    log("사진 자동 업로드는 건너뜀(에디터 버전 차이). 본문 [사진N] 위치에 직접 넣어주세요.")


def _save_draft(frame, log):
    """임시저장(저장) 클릭. 발행/목록보기는 절대 클릭하지 않는다.

    버튼 종류: '저장'(임시저장), '임시저장된 글 보기, N개'(목록), '발행'.
    정확히 '저장'/'임시저장' 만 누른다.
    """
    try:
        buttons = frame.get_by_role("button")
        n = buttons.count()
    except Exception:
        n = 0
    candidates = []  # (우선순위, 인덱스, 이름, locator)
    for i in range(n):
        try:
            b = buttons.nth(i)
            name = (b.get_attribute("aria-label") or b.inner_text() or "").strip()
        except Exception:
            continue
        if not name:
            continue
        if any(f in name for f in FORBIDDEN):
            continue  # 발행류 제외
        if any(x in name for x in ("보기", "목록")):
            continue  # '임시저장된 글 보기' 제외
        if name in ("저장", "임시저장"):
            candidates.append((0, i, name, b))
        elif "저장" in name:
            candidates.append((1, i, name, b))
    candidates.sort(key=lambda t: (t[0], t[1]))
    if candidates:
        _, _, name, b = candidates[0]
        try:
            b.click(timeout=3000)
            log(f"임시저장 클릭: '{name}'.")
            time.sleep(2)
            return True
        except Exception as e:
            log(f"저장 버튼 클릭 실패: {e}")
    log("경고: 임시저장(저장) 버튼을 자동으로 찾지 못했습니다. 창에서 직접 '저장'을 눌러주세요. "
        "(발행은 누르지 마세요)")
    return False


def _wait_closed(ctx, log):
    log("검수 후 직접 발행하세요. 브라우저 창을 닫으면 프로그램이 종료됩니다.")
    try:
        while ctx.pages:
            time.sleep(2)
    except Exception:
        pass


def _launch_browser(p, log):
    """실제 구글 크롬으로 띄운다. NAVER_CHROME_PROFILE 설정 시 내 크롬 프로필 사용."""
    common = dict(headless=False, viewport={"width": 1440, "height": 960})
    use_my_profile = bool(config.NAVER_CHROME_PROFILE)
    profile = config.NAVER_CHROME_PROFILE if use_my_profile else str(USERDATA_DIR)
    if use_my_profile:
        log("내 크롬 프로필 사용 — 크롬이 켜져 있으면 실패합니다(완전 종료 후 재시도).")
    try:
        ctx = p.chromium.launch_persistent_context(profile, channel="chrome", **common)
        log("구글 크롬으로 실행합니다.")
        return ctx
    except Exception as e:
        log(f"구글 크롬 실행 실패({e}). 기본 브라우저(Chromium)로 대체합니다.")
        return p.chromium.launch_persistent_context(str(USERDATA_DIR), **common)


def run_job(job_dir: str):
    job_dir = Path(job_dir)
    job = json.loads((job_dir / "draft.json").read_text(encoding="utf-8"))
    log = _logger(job_dir)

    blog_id = job["blog_id"].strip()
    title = job.get("title", "")
    body = job.get("body", "")
    image_paths = [job_dir / fn for fn in job.get("images", [])]
    image_paths = [p for p in image_paths if p.exists()]

    # 안전망: 전체 본문을 클립보드에 복사(자동입력 실패 시 Cmd+V)
    _pbcopy(f"{title}\n\n{body}")
    log("본문을 클립보드에 복사해뒀어요(자동입력 실패 시 붙여넣기용).")

    USERDATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = _launch_browser(p, log)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        url = WRITE_URL.format(blog_id=blog_id)
        log(f"글쓰기 페이지로 이동: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"페이지 이동 경고: {e}")

        if not _wait_logged_in(page, log):
            _wait_closed(ctx, log)
            return

        # 로그인 후 글쓰기 페이지 보장
        if "Redirect=Write" not in page.url:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass

        time.sleep(3)
        frame = _editor_frame(page, log)
        _shot(page, job_dir, "01_editor_loaded")

        _dismiss_continue_popup(frame, log)
        _dismiss_continue_popup(page, log)

        _fill_title(frame, title, log)
        _fill_body_with_media(
            frame, page, body, image_paths, log,
            captions=job.get("captions") or {},
            subheadings=job.get("subheadings") or [],
            family=job.get("font", "나눔스퀘어"),
            size=job.get("size", 16),
        )
        _shot(page, job_dir, "02_text_and_photos")

        _save_draft(frame, log)
        _shot(page, job_dir, "04_after_save")

        if job.get("videos"):
            log("영상은 자동 업로드하지 않았어요. 본문 [영상N] 위치에 직접 올려주세요.")

        log("작업 완료. 발행은 직접 검수 후 눌러주세요. (이 프로그램은 발행하지 않습니다)")
        _wait_closed(ctx, log)
