#!/usr/bin/env bash
# DeepSeek Chat Server - Linux installer
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SERVER_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "=============================================="
echo " DeepSeek Chat Server - Linux installation"
echo "=============================================="

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 not found. Install Python 3.12 and python3-venv, or set PYTHON_BIN." >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required; Python 3.12 is recommended")
print(f"[OK] Python {sys.version.split()[0]}")
PY

if [ ! -x ".venv/bin/python" ]; then
  echo "[..] Creating virtual environment: $SERVER_DIR/.venv"
  "$PYTHON_BIN" -m venv .venv
else
  echo "[OK] Virtual environment already exists"
fi

echo "[..] Installing dependencies"
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
echo "[OK] Dependencies installed"

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[..] Created .env from .env.example"
  echo "     IMPORTANT: edit .env and set a strong ADMIN_PASSWORD before first start."
else
  echo "[OK] .env already exists (kept unchanged)"
fi

mkdir -p data
chmod +x start.sh install.sh
echo
echo "Installation finished. Next steps:"
echo "  1. Edit .env (ADMIN_PASSWORD, HOST, PORT, COOKIE_SECURE)"
echo "  2. Run ./start.sh"
echo "  3. Open http://server-ip:8000 and log in as the admin"
echo "  4. For systemd/nginx, see docs/linux-deploy.md"
