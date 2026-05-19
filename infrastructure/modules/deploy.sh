#!/usr/bin/env bash
# Local helper: bring an ai_company checkout up on the current machine.
# Idempotent. Doesn't touch the system Python.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv}"
PORT="${PORT:-8501}"

cd "$REPO_DIR"

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "Installing requirements"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    echo "Copying .env.example → .env (fill in API keys before running tasks)"
    cp .env.example .env
  fi
fi

mkdir -p data

echo "Sanity check…"
"$VENV_DIR/bin/python" cli.py check || true

echo
echo "Done. Start the dashboard with:"
echo "  source $VENV_DIR/bin/activate"
echo "  streamlit run ui/dashboard.py --server.port $PORT"
echo
echo "Or run a task from the CLI:"
echo "  $VENV_DIR/bin/python cli.py run 'your task here'"
