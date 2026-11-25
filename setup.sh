#!/bin/bash
# Newton MCQ Bot - one-time setup for macOS (also works on Linux).
# Run from Terminal:  ./setup.sh   (or:  bash setup.sh)

cd "$(dirname "$0")" || exit 1

echo "============================================"
echo "  Newton MCQ Bot - one-time setup"
echo "============================================"
echo

fail() {
  echo
  echo "Setup stopped: $1"
  echo "Check your internet connection and run ./setup.sh again."
  echo "If it still fails, send a screenshot of this window."
  exit 1
}

# Find Python 3.10 or newer (macOS's built-in python3 is often 3.9, which is too old)
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 &&
     "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer was not found."
  echo "Install it from https://www.python.org/downloads/macos/"
  echo "or with Homebrew:  brew install python@3.12"
  echo "Then run ./setup.sh again."
  exit 1
fi
echo "Using $("$PY" --version) ($(command -v "$PY"))"

echo
echo "[1/4] Creating the virtual environment in .venv ..."
if [ ! -x ".venv/bin/python" ]; then
  "$PY" -m venv .venv || fail "could not create .venv"
fi
VPY=".venv/bin/python"

echo
echo "[2/4] Installing Python packages ..."
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -r requirements.txt || fail "package installation failed"

echo
echo "[3/4] Installing the Chromium browser for Playwright ..."
"$VPY" -m playwright install chromium || fail "Chromium installation failed"

echo
echo "[4/4] Testing the installation with input/sample_mcqs.docx ..."
"$VPY" -c "import docx, playwright, pydantic, openpyxl; print('All packages OK')" \
  || fail "packages did not import"
"$VPY" main.py check "input/sample_mcqs.docx"

chmod +x run.sh 2>/dev/null
echo
echo "============================================"
echo "  Setup complete. Start the bot with:  ./run.sh"
echo "============================================"