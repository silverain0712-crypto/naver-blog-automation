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
import tempfile
import time
from pathlib import Path

from PIL import Image

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


# 이모지/기호(📍❤️✔ 등). insert_text 가 이 문자 뒤 텍스트를 유실시키는 경우가 있다.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF\U0000FE0F\U00002764\U000023E9-\U000023FA]"
)


def _insert_text_safe(kb, text):
    """이모지가 든 줄은 insert_text 가 이모지 뒤 글자를 흘리므로 char 단위 type() 로
    안전 입력한다(느리지만 유실 없음). 이모지 없으면 빠른 insert_text.
    입력 직후 짧게 쉬어 에디터가 처리할 시간을 준다(너무 빠르면 글자/줄이 유실됨)."""
    if _EMOJI_RE.search(text):
        kb.type(text)
    else:
        kb.insert_text(text)
    time.sleep(0.05)


def _type_lines(kb, text):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            _insert_text_safe(kb, line)
        if i < len(lines) - 1:
            kb.press("Enter")
            time.sleep(0.04)


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


def _insert_divider(frame, log, style="line6"):
    """구분선 삽입. 실패 시 기본 구분선.

    네이버 구분선 값(실측): line4=◇다이아, line5=•••점선, line6=/사선, line7=|세로.
    비비 글은 사선(line6)을 쓴다.
    """
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


def _parse_table_block(inner: str) -> list[list[str]]:
    """[표] 블록 내부 텍스트 → 행(list) 목록. 각 줄은 '|' 로 칸 구분, 구분선(---)은 건너뜀."""
    rows = []
    for line in inner.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # 마크다운 헤더 구분선(| --- | --- |) 무시
        if re.fullmatch(r"[|\s:\-]+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if any(cells):
            rows.append(cells)
    return rows


def _normalize_table_rows(rows: list[list[str]]) -> list[list[str]]:
    """네이버 기본 표(3열)에 맞춰 각 행을 정확히 3칸으로. 모자라면 빈칸, 넘치면 마지막 칸에 합침."""
    norm = []
    for r in rows:
        cells = [str(c).strip() for c in r]
        if len(cells) < 3:
            cells += [""] * (3 - len(cells))
        elif len(cells) > 3:
            cells = cells[:2] + [" ".join(cells[2:])]
        norm.append(cells)
    return norm


def _insert_table(frame, page, rows, log):
    """본문 현재 위치에 네이버 표를 삽입하고 채운다(3열 고정, 행은 필요한 만큼 추가).

    실측 동작: '표 추가' 버튼은 기본 3x3 표를 커서 위치에 넣는다. Tab 셀이동은 안 되므로
    셀을 하나씩 클릭해 입력한다. 표가 문서 마지막 블록이면 뒤 문단이 없어 이어지는 본문이
    셀로 새어 들어가므로, 삽입 전에 '샌드위치'(Enter로 뒤 빈 문단 확보 → ArrowUp)로 자리를 만든다.
    """
    kb = page.keyboard
    norm = _normalize_table_rows(rows)
    R = len(norm)
    if R == 0:
        return
    # 샌드위치: 표 뒤에 남을 빈 문단을 먼저 만들고 그 위로 올라가 표를 넣는다.
    kb.press("Enter"); time.sleep(0.15)
    kb.press("Enter"); time.sleep(0.15)
    kb.press("ArrowUp"); time.sleep(0.15)
    frame.locator("button.se-table-toolbar-button").first.click(timeout=4000)
    time.sleep(1.0)
    # 행 맞추기(기본 3행 → R행): 행 컨트롤바의 '행 추가' 버튼 클릭
    cur = 3
    add_btn = "ul.se-cell-controlbar-row li.se-cell-controlbar-item button.se-cell-add-button"
    guard = 0
    while cur < R and guard < 40:
        guard += 1
        try:
            frame.locator(add_btn).last.click(timeout=2000)
            cur += 1
            time.sleep(0.2)
        except Exception as e:
            log(f"  표 행 추가 실패: {str(e)[:50]}")
            break
    # 셀 채우기(td 순서 = 행 우선). 셀은 개별 클릭 후 입력.
    cells = frame.locator(".se-component.se-table td .se-text-paragraph")
    total = cells.count()
    for i, row in enumerate(norm):
        for c in range(3):
            idx = i * 3 + c
            if idx >= total:
                break
            if not row[c]:
                continue
            try:
                cells.nth(idx).click(timeout=2000)
                time.sleep(0.08)
                kb.insert_text(row[c])
            except Exception as e:
                log(f"  표 셀({i},{c}) 입력 실패: {str(e)[:40]}")
    # 표 밖(뒤에 만든 빈 문단)으로 커서 이동 — 실제 클릭이어야 에디터 커서가 옮겨진다.
    try:
        frame.locator(".se-component.se-text .se-text-paragraph").last.click(timeout=2000)
    except Exception as e:
        log(f"  표 뒤 문단 이동 실패: {str(e)[:40]}")
    log(f"표 삽입: {R}행 3열")


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


# 업로드 진행/저장 거부를 알리는 화면 문구들(이게 떠 있으면 저장이 거부된다)
_BUSY_TEXTS = ("업로드 준비 중", "업로드 중", "요청하신 작업이 진행", "완료 후 다시")


def _img_count(frame):
    """에디터 본문에 들어간 이미지 컴포넌트 수(업로드 완료 확인용, best-effort)."""
    for sel in ("img.se-image-resource", ".se-component.se-image", ".se-image"):
        try:
            c = frame.locator(sel).count()
            if c:
                return c
        except Exception:
            pass
    return 0


def _busy_text(page):
    """업로드/작업 진행 중 문구가 화면에 있으면 그 문구를, 없으면 None.

    진행 팝업(se-popup-progress '준비 중 0/N')은 에디터 iframe 안에 있으므로
    최상위 page 뿐 아니라 모든 frame 을 검사해야 한다(안 그러면 업로드가 안 끝났는데
    '안 바쁨' 으로 잘못 읽어 저장이 먼저 나가 실패한다)."""
    for fr in page.frames:
        for kw in _BUSY_TEXTS:
            try:
                if fr.get_by_text(kw).count():
                    return kw
            except Exception:
                pass
    return None


def _wait_uploads_done(page, log, timeout=25):
    """사진 업로드/진행 중 문구가 사라질 때까지 대기. 안 끝나면 False.

    네이버 업로드가 '준비 중 0/N' 에서 멈추는 일이 잦아, 무한정 기다리지 않고
    제한시간 내 안 끝나면 False 를 돌려 빨리 실패(=정직한 error)하도록 한다.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _busy_text(page):
            return True
        time.sleep(1)
    log("경고: 업로드가 제한시간 내 끝나지 않았습니다.")
    return False


def _insert_image(frame, page, paths, log):
    """현재 커서 위치에 사진을 삽입. 업로드가 '완료될 때까지' 기다린 뒤 반환한다.

    이전 업로드가 안 끝났는데 다음 사진을 시도하면 filechooser 가 안 떠서
    타임아웃 나고 업로드가 멈춰(0/N) 저장까지 거부되므로, 한 장씩 끝까지 대기한다.
    """
    before = _img_count(frame)
    # 직전 업로드가 진행 중이면 먼저 끝나길 기다린다(멈춰 있으면 곧 포기)
    _wait_uploads_done(page, log)

    chooser = None
    for attempt in range(2):
        try:
            with page.expect_file_chooser(timeout=10000) as fc:
                frame.locator("button.se-image-toolbar-button").first.click(timeout=4000)
            chooser = fc.value
            break
        except Exception as e:
            log(f"  파일선택창 재시도 {attempt + 1}/2 ({str(e)[:60]})")
            _dismiss_continue_popup(frame, log)
            time.sleep(1)
    if chooser is None:
        raise RuntimeError("사진 파일선택창이 열리지 않음(업로드 충돌 가능).")
    chooser.set_files([str(p) for p in paths])
    time.sleep(1.5)
    # 여러 장 첨부 시 '사진 첨부 방식'(개별사진/콜라주/슬라이드) 팝업 → 개별사진
    try:
        dlg = frame.locator(":text('개별사진')")
        if dlg.count():
            dlg.first.click(timeout=3000)
    except Exception:
        pass
    # 이 업로드가 실제로 끝날 때까지 대기(진행 문구 사라짐 + 이미지 수 증가)
    _wait_uploads_done(page, log)
    target = before + len(paths)
    deadline = time.time() + 12
    while time.time() < deadline:
        if _img_count(frame) >= target:
            break
        time.sleep(1)


def _resize_for_upload(paths, log, max_side=1600, quality=85):
    """업로드 전 이미지를 max_side 이내로 축소(임시파일). 원본이 크면 네이버 업로드가
    '준비 중 0/N' 에서 멈춰 저장까지 막히므로 줄여서 올린다. 실패 시 원본 사용."""
    out = []
    tmpdir = Path(tempfile.mkdtemp(prefix="nbw_resize_"))
    for i, p in enumerate(paths):
        p = Path(p)
        if p.suffix.lower() == ".gif":
            # GIF 는 JPEG 로 변환하면 애니메이션이 죽으므로 원본 그대로 올린다.
            out.append(str(p))
            continue
        try:
            im = Image.open(p).convert("RGB")
            im.thumbnail((max_side, max_side))
            dst = tmpdir / f"{i:02d}_{p.stem}.jpg"
            im.save(dst, "JPEG", quality=quality)
            out.append(str(dst))
        except Exception as e:
            log(f"  리사이즈 실패({p.name}): {str(e)[:50]} → 원본 사용")
            out.append(str(p))
    log(f"사진 {len(out)}장 리사이즈(최대 {max_side}px).")
    return out


def _force_dismiss_popups(frame, page, log):
    """버튼 클릭을 막는 팝업/오버레이를 최대한 닫는다(이전작성글 + 일반 알림)."""
    _dismiss_continue_popup(frame, log)
    _dismiss_continue_popup(page, log)
    for ctx in (frame, page):
        for sel in ("button:has-text('확인')", "button:has-text('닫기')",
                    ".se-popup-button-close", "[class*=popup] button[class*=close]"):
            try:
                loc = ctx.locator(sel)
                if loc.count():
                    loc.first.click(timeout=1200)
            except Exception:
                pass
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def _insert_all_images(frame, page, paths, log):
    """모든 사진을 '한 번의 파일선택'으로 본문 끝에 업로드(인라인 다중삽입이 불안정해서).

    리사이즈로 업로드 멈춤을 줄이고, 팝업이 버튼을 막으면 닫고 재시도한다.
    """
    small = _resize_for_upload(paths, log)
    before = _img_count(frame)
    chooser = None
    for attempt in range(4):
        _force_dismiss_popups(frame, page, log)
        try:
            frame.locator(".se-text-paragraph").last.click(force=True, timeout=2500)
            page.keyboard.press("End")
        except Exception:
            pass
        try:
            with page.expect_file_chooser(timeout=12000) as fc:
                frame.locator("button.se-image-toolbar-button").first.click(timeout=5000)
            chooser = fc.value
            break
        except Exception as e:
            log(f"  사진 업로드 재시도 {attempt + 1}/4 ({str(e)[:50]})")
            time.sleep(2)
    if chooser is None:
        raise RuntimeError("사진 업로드 버튼이 팝업에 막혀 열리지 않음.")
    chooser.set_files(small)
    time.sleep(2)
    try:
        dlg = frame.locator(":text('개별사진')")
        if dlg.count():
            dlg.first.click(timeout=3000)
    except Exception:
        pass
    # '모든' 사진이 들어올 때까지 기다린다. before+1 로 끊으면 여러 장 업로드가
    # 스태거링되는 사이 busy 표시가 잠깐 사라지는 순간에 1장만 넣고 멈춰(5/20 등) 사진이
    # 대량 누락되던 버그 → 목표 개수(before+len) 도달 또는 '진행중 아님+개수 정체' 시 종료.
    target = before + len(small)
    deadline = time.time() + 300  # 여러 장은 서버 업로드가 오래 걸릴 수 있음
    stable = 0
    last_count = before
    while time.time() < deadline:
        cnt = _img_count(frame)
        if cnt >= target:
            break
        # 개수가 더 안 늘고(정체) busy 표시도 없으면 몇 번 확인 후 종료(무한대기 방지).
        if cnt == last_count and not _busy_text(page):
            stable += 1
            if stable >= 4:
                break
        else:
            stable = 0
        last_count = cnt
        time.sleep(3)
    done = _img_count(frame) - before
    pend = _busy_text(page)
    log(f"사진 일괄 업로드: {done}/{len(small)}장 들어감." + (f" (아직 진행중: {pend})" if pend else " 업로드 완료."))
    return done


def _find_marker_paragraph(frame, n):
    """본문에서 '[사진N]' 텍스트만 정확히 든 문단 element handle 을 찾는다(없으면 None).
    text_content() 로 비교해 화면 밖 문단도 잡고, '==' 로 사진2/사진21 오매칭을 막는다."""
    for el in frame.query_selector_all(".se-text-paragraph"):
        try:
            if (el.text_content() or "").strip() == f"[사진{n}]":
                return el
        except Exception:
            continue
    return None


def _para_text_styled(frame, text, min_px=17, min_weight=600):
    """본문에서 text 와 정확히 일치하는 문단을 찾아 실제 글자 크기/굵기를 읽어,
    본문(15px/400)보다 크거나 볼드면 True. (커서 기준이 아니라 텍스트로 문단을 찾으므로
    드롭다운 클릭으로 커서가 밀려도 정확히 검증된다.)"""
    try:
        for el in frame.query_selector_all(".se-text-paragraph"):
            if (el.text_content() or "").strip() != text:
                continue
            fs = el.evaluate(
                "e => { const t = e.querySelector('span') || e;"
                " const s = getComputedStyle(t); return s.fontSize + '|' + s.fontWeight; }"
            )
            px_s, wt_s = (fs or "0px|400").split("|")
            px = float(px_s.replace("px", "") or 0)
            wt = int(wt_s) if wt_s.isdigit() else (700 if wt_s == "bold" else 400)
            return px >= min_px or wt >= min_weight
        return False
    except Exception:
        return False


def _apply_sub_style(frame, page, sub_on, norm, log):
    """안정된 문서에서 소제목 문단을 '실제 클릭→키보드 선택→스타일' 로 입히고, 텍스트로
    실제 적용됐는지 확인해 안 먹었으면 재시도(최대 3회). JS Range 선택은 네이버 툴바가
    무시하므로 실제 클릭+키보드 선택을 쓴다."""
    kb = page.keyboard
    for attempt in range(3):
        handle = None
        for el in frame.query_selector_all(".se-text-paragraph"):
            if (el.text_content() or "").strip() == norm:
                handle = el
                break
        if handle is None:
            return False
        try:
            handle.evaluate("e => e.scrollIntoView({block: 'center'})")
            time.sleep(0.1)
            handle.click(timeout=3000, force=True)
        except Exception:
            time.sleep(0.2)
            continue
        kb.press("Home"); kb.press("Shift+End")   # 줄 전체 선택(네이버가 인식하는 실제 선택)
        time.sleep(0.1)
        sub_on()                                   # 선택 영역에 나눔명조19 볼드 적용
        kb.press("End")
        if _para_text_styled(frame, norm):
            return True
        time.sleep(0.2)
    log(f"  (소제목 스타일 실패: {norm[:16]})")
    return False


def _focus_body_end(frame, log=None):
    """이미지 삽입 뒤 커서가 편집영역을 벗어나면 이후 타이핑이 통째로 유실된다
    (네이버 에디터 변경으로 '삽입 후 커서가 이미지 뒤에 남는다'는 가정이 깨짐).
    본문 마지막 문단을 실제로 클릭해 커서를 편집영역 끝으로 되돌린다."""
    for sel in (".se-component.se-text .se-text-paragraph",
                ".se-main-container .se-text-paragraph",
                ".se-text-paragraph"):
        try:
            loc = frame.locator(sel)
            if loc.count():
                loc.last.click(timeout=2000)
                frame.page.keyboard.press("End")
                # 클릭 직후 바로 타이핑하면 커서가 안정되기 전이라 사진 뒤 첫 글자들이
                # 씹힌다(예: '생각보다 너무 편하게' 유실). 짧게 안정화 대기 후 이어쓴다.
                time.sleep(0.45)
                return True
        except Exception:
            continue
    return False


def _fill_body_with_media(frame, page, body, image_paths, log, captions=None,
                          subheadings=None, family="나눔스퀘어", size=16, hashtags=None):
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

    # [사진N] 마커를 반드시 '자기 줄'로 분리한다. 생성기가 "...했어요. [사진1]" 처럼
    # 본문과 같은 줄에 붙여 쓰면 PASS 2 가 마커 문단(정확히 '[사진N]')을 못 찾아 재삽입에
    # 실패하던 문제 해결 → 앞뒤에 줄바꿈을 넣어 마커가 독립 문단이 되게 한다.
    rest_text = re.sub(r"[ \t]*(\[사진\s*\d+\])[ \t]*", r"\n\1\n", rest_text)
    rest_text = re.sub(r"\n{3,}", "\n\n", rest_text)

    # 인트로 = 인사말 전까지. 고지문(회색) → 큰 제목(첫 블록, 나눔명조30 블랙)
    #        → 메타 요약(다음 블록, 회색). 제목/요약은 '첫 빈 줄' 기준으로 가른다.
    #        (예전엔 첫 줄 1줄만 제목이라 2줄 제목이 회색 요약으로 새어 제목이 없어 보였음)
    notice = [l.strip() for l in intro if l.strip() and any(k in l for k in _NOTICE_KEYS)]
    body_intro = [l for l in intro if not (l.strip() and any(k in l for k in _NOTICE_KEYS))]
    title_lines, summary_lines, after_gap = [], [], False
    for l in body_intro:
        if not l.strip():
            if title_lines:      # 제목 블록이 시작된 뒤의 빈 줄 → 이후는 요약
                after_gap = True
            continue
        (summary_lines if after_gap else title_lines).append(l.strip())
    # 빈 줄이 없어 안 갈렸으면 앞 1~2줄만 제목으로(나머지는 회색 요약)
    if not summary_lines and len(title_lines) > 2:
        title_lines, summary_lines = title_lines[:2], title_lines[2:]
    for s in notice:
        gray_on(); _type_lines(kb, s); kb.press("Enter"); kb.press("Enter")
    if title_lines:
        title_on(); _type_lines(kb, "\n".join(title_lines)); kb.press("Enter"); kb.press("Enter")
        log(f"큰 제목(나눔명조{_TITLE_SIZE} 블랙) {len(title_lines)}줄 적용.")
    if summary_lines:
        gray_on(); _type_lines(kb, "\n".join(summary_lines)); kb.press("Enter")
        log("메타 요약(회색) 적용.")
    # 구분선
    body_on()
    _insert_divider(frame, log, "line6")
    body_on()  # 구분선 뒤 본문 스타일 재적용(검정 나눔스퀘어)

    # ── PASS 1: 본문 텍스트를 사진 삽입 없이 '통째로' 먼저 다 입력한다. ──
    # 사진 자리는 '[사진N]' 마커를 텍스트 그대로(자기 문단) 남겨두고, PASS 2에서 그
    # 마커를 다시 '검색'해 실제 사진으로 교체한다. 이렇게 하면 사진 삽입이 커서를
    # 흔들어도 본문 텍스트가 유실되지 않고(유실 전파 없음), 사진도 제자리에 들어간다.
    inline_mode = getattr(config, "NAVER_PHOTO_INLINE", False)
    rearrange_mode = getattr(config, "NAVER_PHOTO_REARRANGE", False)
    # small_paths(리사이즈본)는 인라인 마커 교체(PASS2)에서만 쓴다. 결정적 모드는
    # 끝모음(_insert_all_images)이 내부에서 리사이즈하므로 여기선 안 만든다.
    small_paths = (_resize_for_upload(image_paths, log)
                   if (image_paths and (inline_mode or rearrange_mode)) else [])
    inserted = 0
    used = set()    # 본문에서 참조된 사진 번호(마커로 남겨둔 것)
    failed = set()  # PASS 2 재삽입 실패 → 끝에 모아 업로드
    tokens = re.split(r"(\[표\][\s\S]*?\[/표\]|\[사진\s*\d+\]|\[영상\s*\d+\])", rest_text)
    dup_caption = None  # 직전 사진의 회색 캡션 — 본문에 같은 줄이 또 오면 1회 건너뛴다
    for tok in tokens:
        mt = re.match(r"\[표\]([\s\S]*?)\[/표\]", tok)
        mp = re.match(r"\[사진\s*(\d+)\]", tok)
        mv = re.match(r"\[영상\s*(\d+)\]", tok)
        if mt:
            table_rows = _parse_table_block(mt.group(1))
            if table_rows:
                try:
                    _insert_table(frame, page, table_rows, log)
                    # 표 삽입 후 커서가 편집영역을 벗어나 '표 이후 본문 전체'가 유실되던
                    # 문제 방지 → 본문 마지막 문단으로 커서 복원(안정화 대기 포함).
                    _focus_body_end(frame, log)
                    body_on()
                except Exception as e:
                    log(f"  표 삽입 실패({str(e)[:50]}) → 텍스트로 대체")
                    _focus_body_end(frame, log)
                    body_on()
                    for r in _normalize_table_rows(table_rows):
                        _type_lines(kb, "  ".join(c for c in r if c)); kb.press("Enter")
        elif mp:
            n = int(mp.group(1))
            if 1 <= n <= len(image_paths):
                used.add(n)
                cap = captions.get(str(n)) or captions.get(n)
                # 사진 자리는 '[사진N]' 마커 텍스트만 자기 문단에 남긴다(실제 사진은 PASS 2).
                body_on(); kb.insert_text(f"[사진{n}]")
                if cap:
                    kb.press("Enter"); gray_on(); _type_lines(kb, cap); body_on()
                    # 생성기가 캡션을 본문 인라인에도 넣어 이중으로 찍히던 문제 방지:
                    # 방금 회색 캡션으로 넣은 문구가 바로 뒤 본문 줄에 또 나오면 그 줄은 건너뛴다.
                    dup_caption = str(cap).strip()
            else:
                kb.insert_text(tok)
        elif mv:
            kb.insert_text(tok)  # 영상은 마커 유지(수동)
        else:
            body_lines = tok.split("\n")
            for i, line in enumerate(body_lines):
                stripped = line.strip()
                # 캡션 중복 제거: 직전 사진의 회색 캡션과 같은 줄은 타이핑하지 않는다(1회만).
                if dup_caption and stripped == dup_caption:
                    dup_caption = None
                    continue
                # 움짤 자리 마커([gif_01_설명])는 literal 로 찍지 말고 회색 안내로 바꾼다.
                gm = re.match(r"^\[gif[_\-]?\d*[_\-]?(.*?)\]$", stripped)
                if gm:
                    desc = gm.group(1).strip()
                    hint = f"(움짤 자리 — {desc})" if desc else "(움짤 자리)"
                    gray_on(); _insert_text_safe(kb, hint); body_on()
                    if i < len(body_lines) - 1:
                        kb.press("Enter"); time.sleep(0.04)
                    continue
                norm = _strip_bullet(line)
                # 소제목도 PASS1 에선 '본문 스타일 그대로 텍스트만' 넣는다. 스타일은 본문이
                # 다 입력된 뒤(안정된 상태) 아래 별도 패스에서 JS 선택으로 입힌다 — 타이핑
                # 중에 입히면 이후 입력이 스타일을 흔들어 실행마다 일부만 먹던 문제 해결.
                if norm and norm in sub_set:
                    _insert_text_safe(kb, norm)
                elif line:
                    _insert_text_safe(kb, line)
                if i < len(body_lines) - 1:
                    kb.press("Enter")
                    time.sleep(0.04)

    # 해시태그는 '모든 사진 배치가 끝난 뒤(진짜 맨 끝)'에 넣는다. 사진 재삽입이 실패해
    # 끝에 사진이 쌓이면 그 위에 파묻혀 태그로 등록 안 되던 문제 방지.
    tags = [str(h).lstrip("#") for h in (hashtags or []) if str(h).strip()]

    # 여기서 본문 텍스트는 100% 입력 완료 → 사진 넣기 전에 유실 여부 자가진단.
    try:
        texts = frame.locator(".se-text-paragraph").all_inner_texts()
        got = len(re.sub(r"\s", "", "".join(texts)))
        expect = len(re.sub(r"\s", "", re.sub(r"\[영상\s*\d+\]", "", body)))
        mark = "✓" if got >= expect * 0.92 else "⚠ 유실 의심"
        log(f"자가진단(PASS1 텍스트): 에디터 {got}자 / 예상 {expect}자 {mark}")
    except Exception as e:
        log(f"자가진단 건너뜀: {str(e)[:40]}")

    # ── 소제목 스타일링 패스 ── 본문 텍스트가 모두 안정된 지금, 소제목만 JS 로 정확히
    # 선택해 나눔명조19 볼드를 입힌다(타이핑 중이 아니라 이후 입력이 스타일을 흔들지 않음).
    if sub_set:
        done_sub = 0
        for norm in sub_set:
            if _apply_sub_style(frame, page, sub_on, norm, log):
                done_sub += 1
        _focus_body_end(frame, log); body_on()
        log(f"소제목 스타일링 패스: {done_sub}/{len(sub_set)}개 적용")

    # 소제목이 실제로 '본문과 구분되는 스타일'을 먹었는지 컴퓨티드 스타일로 검증한다.
    # (스타일이 안 먹으면 소제목이 본문과 똑같이 보여 '안 넘어온 것처럼' 느껴짐)
    if sub_set:
        try:
            styled = 0
            for el in frame.query_selector_all(".se-text-paragraph"):
                if (el.text_content() or "").strip() not in sub_set:
                    continue
                fs = el.evaluate(
                    "e => { const t = e.querySelector('span') || e;"
                    " const s = getComputedStyle(t);"
                    " return s.fontSize + '|' + s.fontWeight; }"
                )
                px_s, wt_s = (fs or "0px|400").split("|")
                px = float(px_s.replace("px", "") or 0)
                wt = int(wt_s) if wt_s.isdigit() else (700 if wt_s == "bold" else 400)
                if px >= 17 or wt >= 600:   # 본문(15px/400)보다 크거나 볼드면 스타일 먹은 것
                    styled += 1
            log(f"소제목 실제 스타일 확인: {styled}/{len(sub_set)}개 적용됨"
                + ("" if styled >= len(sub_set) else " ⚠ 일부 미적용"))
        except Exception as e:
            log(f"소제목 스타일 확인 건너뜀: {str(e)[:40]}")

    # ── PASS 2: 사진 배치 ──
    if inline_mode:
        # (best-effort) [사진N] 마커를 '텍스트로 다시 찾아' 실제 사진으로 교체 시도.
        # 네이버 에디터가 불안정해 실행마다 일부만 성공 → 실패분은 아래 끝모음으로.
        for n in sorted(used):
            try:
                _dismiss_continue_popup(frame, log)
                # '[사진N]'만 든 문단을 query_selector_all 로 훑어 '정확히 일치'하는 걸 집는다.
                # text_content()(가시성 무관 원본 DOM 텍스트)로 비교 — inner_text 는 화면 밖
                # 문단이 ""로 나와 매칭 실패. '사진2'가 '사진21'에 걸리는 것도 == 로 방지.
                handle = None
                for el in frame.query_selector_all(".se-text-paragraph"):
                    try:
                        if (el.text_content() or "").strip() == f"[사진{n}]":
                            handle = el
                            break
                    except Exception:
                        continue
                if handle is None:
                    raise RuntimeError("마커 문단 못 찾음")
                try:
                    handle.evaluate("el => el.scrollIntoView({block: 'center'})")
                    time.sleep(0.15)
                except Exception:
                    pass
                handle.click(timeout=4000, force=True)
                kb.press("Home"); kb.press("Shift+End"); kb.press("Delete")
                _insert_image(frame, page, [small_paths[n - 1]], log)
                inserted += 1
            except Exception as e:
                log(f"  사진{n} 재삽입 실패({str(e)[:45]}) → 마커 유지, 끝에 업로드")
                failed.add(n)
        # 참조 안 됐거나 재삽입 실패한 사진만 끝에 모은다.
        leftover = [image_paths[n - 1] for n in range(1, len(image_paths) + 1)
                    if n not in used or n in failed]
        header = "(아래 사진은 자동 배치되지 않았어요. 원하는 위치로 끌어다 놓으세요.)"
    elif rearrange_mode:
        # (옵션1·재배치) 각 [사진N] 마커 자리에 커서를 놓고 '그 자리'에 사진을 바로 업로드한다.
        # 네이버 자체 DnD 는 커스텀이라 자동 드래그가 안 먹어서(진단으로 확인), 대신 공식
        # '커서 위치 사진 삽입' 기능만 쓴다 → 사람 손 없이 제자리 배치, 실패분만 끝모음.
        placed = 0
        for n in sorted(used):
            ok = False
            for attempt in range(2):
                try:
                    _dismiss_continue_popup(frame, log)
                    handle = _find_marker_paragraph(frame, n)
                    if handle is None:
                        raise RuntimeError("마커 문단 못 찾음")
                    handle.evaluate("el => el.scrollIntoView({block: 'center'})")
                    time.sleep(0.2)
                    handle.click(timeout=4000, force=True)
                    # 마커 텍스트('[사진N]')만 지우고 그 빈 자리(커서)에 사진 삽입.
                    kb.press("Home"); kb.press("Shift+End"); kb.press("Delete")
                    _insert_image(frame, page, [small_paths[n - 1]], log)
                    inserted += 1; placed += 1; ok = True
                    log(f"  사진{n}: 마커 자리에 삽입 완료")
                    break
                except Exception as e:
                    log(f"  사진{n} 삽입 재시도({attempt + 1}/2): {str(e)[:45]}")
                    _focus_body_end(frame, log)
                    time.sleep(0.5)
            if not ok:
                failed.add(n)
                log(f"  사진{n}: 마커 자리 삽입 실패 → 끝모음으로 이동")
        log(f"마커 자리 삽입: {placed}/{len(used)}장 제자리 배치"
            + ("" if placed == len(used) else " (실패분은 글 끝에 모아둠)"))
        # 참조 안 됐거나 삽입 실패한 사진만 끝에 모은다.
        leftover = [image_paths[n - 1] for n in range(1, len(image_paths) + 1)
                    if n not in used or n in failed]
        header = "(아래 사진은 자동 배치되지 않았어요. 원하는 위치로 끌어다 놓으세요.)"
    else:
        # (결정적·기본) 마커 교체는 네이버 에디터 불안정으로 실행마다 결과가 달라져서
        # 아예 안 한다. 본문의 '[사진N]' 글자 마커를 '자리 안내'로 그대로 두고, 모든 사진을
        # 순서대로(1..N) 글 끝에 모은다 → 사용자가 번호 자리로 드래그(결과가 항상 동일).
        leftover = list(image_paths)
        header = "▶ 아래 사진을 본문의 [사진N] 번호 자리로 끌어다 놓으세요 (순서대로 1, 2, 3 …)"

    if leftover:
        _focus_body_end(frame, log)
        kb.press("Enter"); kb.press("Enter")
        _insert_divider(frame, log, "line6")
        body_on(); gray_on()
        _type_lines(kb, header)
        kb.press("Enter"); body_on()
        try:
            inserted += _insert_all_images(frame, page, leftover, log)
        except Exception as e:
            log(f"남은 사진 업로드 실패({e}). 본문 [사진N] 위치에 직접 넣어주세요.")

    # 해시태그: 네이버 글쓰기(임시저장) 에디터는 본문 '#단어'를 태그 칩으로 즉시 바꾸지
    # 않는다(자동화의 합성 이벤트를 태그 변환기가 무시 — 실측으로 Space/Enter 모두 변환 안 됨).
    # 대신 '발행' 시 본문의 '#단어'가 태그로 등록된다 → 비비 발행글처럼 '한 줄에 하나씩' 맨 끝에
    # 넣어둔다(임시저장 화면엔 텍스트로 보이고, 발행하면 태그가 됨).
    if tags:
        _focus_body_end(frame, log)
        kb.press("Enter"); kb.press("Enter"); body_on()
        _type_lines(kb, "\n".join(f"#{t}" for t in tags))
        log(f"해시태그 {len(tags)}개 맨 끝에 한 줄씩 입력(발행 시 태그 등록).")

    if inline_mode:
        mode_desc = f"인라인 자리 {len(used) - len(failed)}장 + 끝모음"
    elif rearrange_mode:
        mode_desc = f"재배치(마커 자리 삽입 {len(used) - len(failed)}장 + 실패분 끝모음)"
    else:
        mode_desc = "결정적(전부 끝모음, 본문에 [사진N] 안내)"
    log(f"본문 입력 완료. 사진 {inserted}장 삽입({mode_desc}), "
        f"소제목 {len(sub_set)}개 스타일, 해시태그 {len(tags)}개.")
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


def _temp_count(frame):
    """상단 '저장 N'(임시저장 글 개수)를 읽는다. 못 읽으면 -1.

    저장이 실제로 됐는지 확인하는 진짜 증거(클릭만으로는 거짓 성공이 남).
    """
    js = r"""() => {
      const sels = ['[class*=save_count]','[class*=draft_count]','[class*=save] [class*=count]'];
      for (const s of sels) {
        for (const el of document.querySelectorAll(s)) {
          const m = (el.innerText||'').match(/\d+/);
          if (m) return parseInt(m[0]);
        }
      }
      for (const b of document.querySelectorAll('button, a, span')) {
        const t = (b.innerText||'').trim();
        if (t.startsWith('저장')) { const m = t.match(/\d+/); if (m) return parseInt(m[0]); }
      }
      return -1;
    }"""
    try:
        return frame.evaluate(js)
    except Exception:
        return -1


def _save_draft(frame, log):
    """임시저장(저장) 클릭. 발행/목록보기는 절대 클릭하지 않는다.

    버튼 종류: '저장'(임시저장), '임시저장된 글 보기, N개'(목록), '발행'.
    정확히 '저장'/'임시저장' 만 누른다.

    저장 전에 사진 업로드가 끝났는지 확인하고, 저장 후 '작업 진행 중' 거부
    배너가 뜨면 대기 후 재시도한다. 끝까지 거부되면 False(=실패) 를 반환해
    워커가 거짓으로 'posted' 표시하지 않도록 한다.
    """
    page = frame.page
    # 업로드가 안 끝났는데 저장하면 "요청하신 작업이 진행 중" 으로 거부됨 → 먼저 대기
    _wait_uploads_done(page, log)
    # '작성 중인 글이 있습니다' 등 팝업이 저장 버튼 클릭을 막으므로 먼저 닫는다
    _force_dismiss_popups(frame, page, log)

    def _find_save_btn():
        try:
            buttons = frame.get_by_role("button")
            n = buttons.count()
        except Exception:
            return None
        cands = []
        for i in range(n):
            try:
                b = buttons.nth(i)
                name = (b.get_attribute("aria-label") or b.inner_text() or "").strip()
            except Exception:
                continue
            if not name or any(f in name for f in FORBIDDEN):
                continue
            if any(x in name for x in ("보기", "목록")):
                continue
            if name in ("저장", "임시저장"):
                cands.append((0, i, name, b))
            elif "저장" in name:
                cands.append((1, i, name, b))
        cands.sort(key=lambda t: (t[0], t[1]))
        return cands[0] if cands else None

    before = _temp_count(frame)
    # 최대 3회: 팝업 닫고 저장 클릭 → 임시저장 개수가 늘었는지로 '진짜' 확인
    for attempt in range(3):
        _force_dismiss_popups(frame, page, log)
        _wait_uploads_done(page, log)
        found = _find_save_btn()
        if not found:
            log("경고: 임시저장(저장) 버튼을 찾지 못했습니다.")
            time.sleep(2)
            continue
        _, _, name, b = found
        try:
            b.click(timeout=3000)
            log(f"임시저장 클릭: '{name}' (시도 {attempt + 1}/3)")
        except Exception as e:
            log(f"  저장 클릭 막힘({str(e)[:50]}) → 팝업 닫고 재시도")
            continue
        time.sleep(3)
        after = _temp_count(frame)
        if before >= 0 and after > before:
            log(f"임시저장 확인됨(개수 {before}→{after}).")  # 진짜 증거
            return True
        if before < 0 and after < 0 and not _busy_text(page):
            # 개수를 못 읽는 환경: 배너 없으면 성공으로 본다(차선)
            log("임시저장 클릭 완료(개수 확인 불가, 거부 배너 없음).")
            return True
        log(f"  저장 미확인(개수 {before}→{after}, busy={_busy_text(page)}) → 재시도")
    log("저장 실패: 임시저장 개수가 늘지 않음(거짓 성공 방지 → error 처리).")
    return False


def _wait_closed(ctx, log):
    log("검수 후 직접 발행하세요. 브라우저 창을 닫으면 프로그램이 종료됩니다.")
    try:
        while ctx.pages:
            time.sleep(2)
    except Exception:
        pass


def _launch_browser(p, log, headless=False):
    """브라우저를 띄운다.

    headless(워커): 충돌 없는 번들 크로미움(창 없음, 안정적).
    headful(수동 mac_poster): 실제 구글 크롬 시도 → 실패 시 번들 크로미움.
    """
    common = dict(headless=headless, viewport={"width": 1440, "height": 960})
    if headless:
        return p.chromium.launch_persistent_context(str(USERDATA_DIR), **common)
    profile = config.NAVER_CHROME_PROFILE or str(USERDATA_DIR)
    if config.NAVER_CHROME_PROFILE:
        log("내 크롬 프로필 사용 — 크롬 완전 종료 후 실행하세요.")
    try:
        ctx = p.chromium.launch_persistent_context(profile, channel="chrome", **common)
        log("구글 크롬으로 실행합니다.")
        return ctx
    except Exception as e:
        log(f"구글 크롬 실행 실패({e}). 기본 브라우저로 대체합니다.")
        return p.chromium.launch_persistent_context(str(USERDATA_DIR), **common)


def run_job(job_dir: str, interactive: bool = True):
    """draft.json 을 읽어 네이버에 임시저장.

    interactive=True: 화면 있는 크롬 + 미로그인 시 사용자가 직접 로그인 대기(수동/mac_poster).
    interactive=False: headless 백그라운드 자동(워커). 미로그인이면 예외를 던진다(재로그인 필요).
    """
    job_dir = Path(job_dir)
    job = json.loads((job_dir / "draft.json").read_text(encoding="utf-8"))
    log = _logger(job_dir)

    blog_id = job["blog_id"].strip()
    title = job.get("title", "")
    body = job.get("body", "")
    image_paths = [job_dir / fn for fn in job.get("images", [])]
    image_paths = [p for p in image_paths if p.exists()]

    if interactive:
        _pbcopy(f"{title}\n\n{body}")
        log("본문을 클립보드에 복사해뒀어요(자동입력 실패 시 붙여넣기용).")

    USERDATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = _launch_browser(p, log, headless=not interactive)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        url = WRITE_URL.format(blog_id=blog_id)
        log(f"글쓰기 페이지로 이동: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"페이지 이동 경고: {e}")

        if interactive:
            if not _wait_logged_in(page, log):
                _wait_closed(ctx, log)
                return
        else:
            # 자동 모드: 로그인 안 돼 있으면 중단(2단계 인증을 자동으론 못 함)
            time.sleep(2)
            if "nid.naver.com" in page.url:
                ctx.close()
                raise RuntimeError(
                    "네이버 세션이 만료됐습니다. 맥에서 mac_poster.py 로 1회 로그인한 뒤 다시 시도하세요."
                )

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
            hashtags=job.get("hashtags") or [],
        )
        _shot(page, job_dir, "02_text_and_photos")

        saved = _save_draft(frame, log)
        _shot(page, job_dir, "04_after_save")

        if job.get("videos"):
            log("영상은 자동 업로드하지 않았어요. 본문 [영상N] 위치에 직접 올려주세요.")

        if interactive:
            log("작업 완료. 발행은 직접 검수 후 눌러주세요. (이 프로그램은 발행하지 않습니다)")
            _wait_closed(ctx, log)
        else:
            time.sleep(2)
            ctx.close()
            if not saved:
                raise RuntimeError("임시저장 버튼을 찾지 못했습니다(에디터 구조 변경 가능).")
            log("자동 임시저장 완료.")
