#!/bin/bash
# Usage: ./make_release.sh 1.1   (run from the repo folder, needs the GitHub CLI signed in)
set -e
VERSION="${1:?version number required, e.g. 1.1}"
cd "$(dirname "$0")"
rm -rf /tmp/pii-scrubber-release && mkdir -p /tmp/pii-scrubber-release/pii-scrubber
rsync -a --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '.DS_Store' ./ /tmp/pii-scrubber-release/pii-scrubber/
(cd /tmp/pii-scrubber-release && rm -f pii-scrubber.zip && zip -qr pii-scrubber.zip pii-scrubber)
git tag -a "v$VERSION" -m "v$VERSION" 2>/dev/null || true
git push origin "v$VERSION"
gh release create "v$VERSION" /tmp/pii-scrubber-release/pii-scrubber.zip --title "v$VERSION" --notes "Download pii-scrubber.zip, unzip it, and double-click the Start file. See the README for details."
echo "Released v$VERSION"
