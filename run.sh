#!/usr/bin/env bash
# Lance l'interface web de PrintPro (installe les dépendances au besoin).
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if ! $PY -c "import PIL, numpy, scipy, fastapi, uvicorn, multipart" 2>/dev/null; then
  echo "Installation des dépendances…"
  $PY -m pip install -r requirements.txt
fi

exec $PY -m printpro serve --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
