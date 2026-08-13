#!/usr/bin/env bash
# Buduje samodzielną binarkę na Linux x86-64 — bez instalowania Pythona
# ani zależności na maszynie docelowej. Wymaga tylko Dockera.
#
#   ./build-linux.sh            -> pkp-monitor-linux-amd64
#
# Uwagi:
#   * binutils jest wymagane przez PyInstaller (objdump),
#   * --collect-all curl_cffi dociąga biblioteki TLS, bez tego binarka
#     wywala się dopiero w runtime,
#   * --platform linux/amd64 pozwala budować na Macu z procesorem ARM.
set -euo pipefail

cd "$(dirname "$0")"
OUT=pkp-monitor-linux-amd64

docker run --rm --platform linux/amd64 -v "$PWD":/src -w /build python:3.12-slim bash -c '
  set -e
  apt-get -qq update && apt-get -qq install -y binutils >/dev/null
  pip -q install pyinstaller curl_cffi
  cp /src/pkp_monitor.py .
  pyinstaller --onefile --collect-all curl_cffi --name pkp-monitor pkp_monitor.py >/dev/null
  ./dist/pkp-monitor --help >/dev/null   # test dymny
  cp dist/pkp-monitor "/src/'"$OUT"'"
'

echo "gotowe: $OUT ($(du -h "$OUT" | cut -f1))"
echo "wdrożenie:  scp $OUT user@host:pkp-monitor/pkp-monitor"
