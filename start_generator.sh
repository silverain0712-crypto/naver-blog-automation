#!/bin/bash
# 맥 초안 생성기 실행 (폰 Vercel 앱에서 넣은 글감을 비비 문체 초안으로 생성).
# 크롬을 쓰지 않으므로 start_worker.sh 와 동시에 실행해도 됩니다.
# caffeinate -i : 도는 동안 맥이 idle 로 잠들지 않게 막음(뚜껑 닫으면 강제 sleep→멈춤, 깨면 재개).
cd "$(dirname "$0")"
echo "================================================================"
echo "  맥 초안 생성기 (Ctrl+C 로 종료)"
echo "  폰에서 글감을 넣으면 여기서 비비 문체 초안을 씁니다(draft_ready)."
echo "  네이버 임시저장은 start_worker.sh(mac_worker.py)가 담당합니다."
echo "================================================================"
caffeinate -i python3 mac_generator.py
