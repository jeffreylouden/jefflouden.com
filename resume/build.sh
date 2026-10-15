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
# Chrome sets only the title. Add the author and subject when pypdf is available (pip install pypdf).
if python3 -c "import pypdf" 2>/dev/null; then
  python3 - <<'PY'
from pypdf import PdfReader, PdfWriter
r = PdfReader("public/resume.pdf")
w = PdfWriter(clone_from=r)
w.add_metadata({"/Author": "Jeffrey Louden", "/Subject": "Résumé", "/Title": "Jeffrey Louden · Résumé"})
w.write("public/resume.pdf")
PY
else
  echo "pypdf not installed; skipped the PDF author and subject"
fi
echo "Wrote public/resume.pdf"
