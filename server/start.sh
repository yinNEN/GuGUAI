#!/usr/bin/env bash
# Start DeepSeek Chat Server on Linux
set -euo pipefail
SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SERVER_DIR"
if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Run ./install.sh first." >&2
  exit 1
fi
exec .venv/bin/python run.py "$@"
