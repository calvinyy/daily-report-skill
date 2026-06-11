#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="${CODEX_SKILLS_DIR:-"$HOME/.codex/skills"}"
DEST_DIR="$SKILLS_DIR/daily-report"

mkdir -p "$SKILLS_DIR"

if [[ "$SOURCE_DIR" != "$DEST_DIR" ]]; then
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.pytest_cache' \
      --exclude '__pycache__' \
      --exclude '*.pyc' \
      --exclude '.DS_Store' \
      "$SOURCE_DIR/" "$DEST_DIR/"
  else
    rm -rf "$DEST_DIR"
    cp -R "$SOURCE_DIR" "$DEST_DIR"
    find "$DEST_DIR" \( -name '.pytest_cache' -o -name '__pycache__' \) -type d -prune -exec rm -rf {} +
    find "$DEST_DIR" -name '*.pyc' -type f -delete
  fi
fi

python3 "$DEST_DIR/scripts/generate_daily_report.py" --init-config
python3 "$DEST_DIR/scripts/generate_daily_report.py" --install-check

cat <<EOF

Daily Report skill installed at:
  $DEST_DIR

If Feishu auth is missing, run:
  lark-cli auth login --domain im,calendar,drive,docs,contact,minutes --no-wait --json
  lark-cli auth login --scope "search:docs:read task:task:read docs:document.comment:read" --no-wait --json

Try a preview:
  python3 "$DEST_DIR/scripts/generate_daily_report.py" --kind daily --dry-run
EOF
