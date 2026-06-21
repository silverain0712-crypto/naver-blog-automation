#!/bin/bash
# 핸드폰(같은 와이파이)에서 접속할 수 있게 앱을 실행한다.
# 사용법:  ./start_mobile.sh
cd "$(dirname "$0")"

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
PORT=8501
URL="http://$IP:$PORT"

echo ""
echo "================================================================"
echo "  핸드폰에서 접속하세요 (맥과 같은 와이파이여야 합니다):"
echo "    $URL"
echo "================================================================"
echo "  아래 QR코드를 핸드폰 카메라로 찍으면 바로 열립니다:"
echo ""

python3 - "$URL" <<'PY'
import sys, qrcode
qr = qrcode.QRCode(border=1)
qr.add_data(sys.argv[1]); qr.make()
qr.print_ascii(invert=True)
PY

echo ""
echo "  (이 창은 켜 두세요. 끄면 핸드폰에서 접속이 끊깁니다.)"
echo "  종료: Ctrl+C"
echo "================================================================"
echo ""

python3 -m streamlit run app.py \
  --server.address 0.0.0.0 \
  --server.port "$PORT" \
  --server.headless true
