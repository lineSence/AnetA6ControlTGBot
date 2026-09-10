#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/../opt/tgbot/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="/opt/tgbot/bin/python"
fi
"$PY" -m compileall -q "$ROOT/tgbot"
cd "$ROOT"
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PY" - <<'PY_IMPORT_CHECK'
import tgbot.config, tgbot.printer, tgbot.safety, tgbot.ws, tgbot.handlers, tgbot.anims, tgbot.main
import tgbot.ui, tgbot.uxkit, tgbot.middlewares
from tgbot.ws import MoonrakerWS, health_loop
assert callable(health_loop)
assert MoonrakerWS is not None
print("package import check: OK")
PY_IMPORT_CHECK
if find "$ROOT/tests" -maxdepth 1 -type f \
    \( -name "test_units.py" -o -name "test_core_old.py" -o -name "test_animation.py" \) \
    -print -quit | grep -q .; then
  echo "ERROR: stale legacy test files remain" >&2
  exit 1
fi
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PY" - <<'PY_RUNTIME_IMPORT'
from tgbot.ws import MoonrakerWS, health_loop
from tgbot import main
from tgbot.main import publish_ui
from tgbot.middlewares import setup as setup_middlewares
from tgbot.uxkit import BOT_COMMANDS
assert callable(health_loop)
assert MoonrakerWS is not None
assert main.main is not None
assert callable(publish_ui)
assert callable(setup_middlewares)
assert 0 < len(BOT_COMMANDS) <= 7
print("runtime import check: OK")
PY_RUNTIME_IMPORT
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m pytest "$ROOT/tests" -q
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck "$ROOT"/install_tgbot*.sh
else
  echo "shellcheck not installed; skipped."
fi
