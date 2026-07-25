"""네이버 로그인 전용 — 브라우저를 띄워 1회 로그인하고 세션을 저장한다(맥에서 실행).

자동 워커가 쓰는 프로필(USERDATA_DIR)에 네이버 로그인 세션을 심어둔다.
실행: python3 naver_login.py  → 열린 구글 크롬에서 네이버 로그인 → 자동 감지 후 종료.
"""

import time

from playwright.sync_api import sync_playwright

from modules.naver_blog_writer import USERDATA_DIR


def main():
    USERDATA_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        # 충돌 없는 번들 크로미움으로 로그인(세션은 워커와 같은 프로필에 저장)
        ctx = p.chromium.launch_persistent_context(
            str(USERDATA_DIR), headless=False, viewport={"width": 1280, "height": 900}
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded")
        print("\n👉 열린 크롬에서 네이버에 로그인하세요 (2단계 인증 포함). 로그인되면 자동 종료됩니다.\n")
        ok = False
        for _ in range(300):  # 최대 10분 대기
            time.sleep(2)
            try:
                cookies = ctx.cookies()
            except Exception:
                cookies = []
            # NID_SES 는 로그인 페이지만 열어도 일찍 생겨서, 이걸로 판정하면 인증 완료 전에
            # 성공으로 오인한다. 실제 인증 토큰은 NID_AUT 다 — 이게 있어야 글쓰기 페이지가 통과된다.
            if any(c.get("name") == "NID_AUT" for c in cookies):
                ok = True
                break
        # NID_AUT 가 잡혀도 실제 글쓰기 페이지가 로그인으로 튕기지 않는지 최종 확인한다.
        if ok:
            try:
                page.goto("https://blog.naver.com/bbnation?Redirect=Write",
                          wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
                if "nid.naver.com" in page.url:
                    ok = False
                    print("⚠️ 쿠키는 있으나 글쓰기 페이지가 로그인으로 튕깁니다. 로그인을 완전히 끝내주세요.")
            except Exception:
                pass
        time.sleep(2)
        ctx.close()
    print("✅ 로그인 세션 저장 완료. 이제 워커를 켜면 됩니다." if ok
          else "⏳ 로그인이 감지되지 않았습니다. 다시 시도해주세요.")


if __name__ == "__main__":
    main()
