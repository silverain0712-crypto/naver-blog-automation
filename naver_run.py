"""네이버 임시저장 실행기(별도 프로세스).

Streamlit 이 만든 job 폴더를 받아 브라우저를 띄우고 자동 입력 + 임시저장한다.
사용법: python3 naver_run.py <job_dir>
"""

import sys

from modules.naver_blog_writer import run_job

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 naver_run.py <job_dir>")
        sys.exit(1)
    run_job(sys.argv[1])
