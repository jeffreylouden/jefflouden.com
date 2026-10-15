#!/bin/sh
# Renders resume/resume.html to public/resume.pdf with headless Chrome/Chromium.
# Usage: resume/build.sh [path-to-chrome]
set -e
cd "$(dirname "$0")/.."
CHROME="${1:-${CHROME:-$(command -v chromium || command -v google-chrome || command -v chromium-browser)}}"
"$CHROME" --headless=new --no-sandbox --disable-gpu --no-pdf-header-footer \
  --virtual-time-budget=5000 \
  --print-to-pdf=public/resume.pdf \
  "file://$PWD/resume/resume.html"
echo "Wrote public/resume.pdf"
