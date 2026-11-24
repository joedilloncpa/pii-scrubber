#!/bin/bash
# Double-click launcher for Mac. First run installs what it needs (a minute or two).
cd "$(dirname "$0")"
clear
echo "PII Scrubber"
echo "------------"

if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
  echo ""
  echo "Python 3 isn't installed yet. Opening the download page..."
  echo "Install it (click through the installer with the defaults), then double-click this file again."
  open "https://www.python.org/downloads/macos/"
  read -n 1 -s -r -p "Press any key to close."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "First run: setting things up. This takes a minute or two and only happens once."
  python3 -m venv .venv || { echo "Could not create the Python environment."; read -n 1 -s -r -p "Press any key to close."; exit 1; }
fi

if [ ! -f ".venv/.installed" ] || [ "requirements.txt" -nt ".venv/.installed" ]; then
  echo "Installing components..."
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  ./.venv/bin/python -m pip install --quiet -r requirements.txt || { echo "Install failed. Check your internet connection and try again."; read -n 1 -s -r -p "Press any key to close."; exit 1; }
  touch ".venv/.installed"
fi

# Skip Streamlit's one-time "enter your email" prompt
mkdir -p "$HOME/.streamlit"
[ -f "$HOME/.streamlit/credentials.toml" ] || printf '[general]\nemail = ""\n' > "$HOME/.streamlit/credentials.toml"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

echo ""
echo "Starting. Your browser will open in a moment."
echo "Leave this window open while you use the scrubber. Close it when you're done."
echo ""
./.venv/bin/python -m streamlit run app.py
