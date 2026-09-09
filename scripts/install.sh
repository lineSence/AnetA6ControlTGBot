#!/usr/bin/env bash
# Modular installer for tg2printer (Telegram bot -> Klipper).
# Installs from this repository instead of a self-extracting archive.
#
# Usage:
#   sudo bash scripts/install.sh --token <BOT_TOKEN> --chat <TELEGRAM_ID>
#   TG_BOT_TOKEN=... TG_CHAT_ID=... sudo -E bash scripts/install.sh
set -Eeuo pipefail

BASE="${BASE:-/root/tgbot}"
VENV="${VENV:-/opt/tgbot}"
SERVICE="${SERVICE:-tg2printer}"
CONFIG_DIR="${CONFIG_DIR:-/root/printer_data/config}"
LOG_FILE="${LOG_FILE:-/var/log/tg2printer.log}"
CONVERTER="${CONVERTER:-/root/gif2klipper.py}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"

TOKEN="${TG_BOT_TOKEN:-}"
CHAT="${TG_CHAT_ID:-}"
SKIP_TESTS="${SKIP_TESTS:-0}"
NO_SERVICE="${NO_SERVICE:-0}"

log() { printf '[install] %s\n' "$*"; }
fail() { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Options:
  --token <token>   Telegram bot token (or TG_BOT_TOKEN)
  --chat <id>       allowed Telegram user id (or TG_CHAT_ID)
  --skip-tests      do not run pytest
  --no-service      do not install/start the systemd unit
  -h, --help        show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) TOKEN="${2:-}"; shift 2 ;;
    --chat) CHAT="${2:-}"; shift 2 ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --no-service) NO_SERVICE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "run as root (sudo)"
command -v python3 >/dev/null 2>&1 || fail "python3 not found"
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  fail "python 3.10 or newer is required"
fi
if [[ -n "$CHAT" ]] && ! [[ "$CHAT" =~ ^-?[0-9]+$ ]]; then
  fail "--chat must be a single numeric Telegram id"
fi
log "repository: $REPO"

# 1. Back up the current installation.
if [[ -d "$BASE/tgbot" ]]; then
  BACKUP="$BASE/backups/upgrade_$STAMP"
  log "backing up current install to $BACKUP"
  mkdir -p "$BACKUP"
  cp -a "$BASE/tgbot" "$BACKUP/tgbot"
  if [[ -f "$BASE/config.yaml" ]]; then
    cp -a "$BASE/config.yaml" "$BACKUP/config.yaml"
  fi
  if [[ -f "$CONVERTER" ]]; then
    cp -a "$CONVERTER" "$BACKUP/gif2klipper.py"
  fi
fi

# 2. Virtualenv and dependencies.
if [[ ! -x "$VENV/bin/python" ]]; then
  log "creating virtualenv in $VENV"
  python3 -m venv "$VENV" || fail "python3 -m venv failed (install python3-venv)"
fi
log "installing dependencies from requirements.lock"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$REPO/requirements.lock"

# 3. Code.
log "installing package into $BASE/tgbot"
mkdir -p "$BASE" "$BASE/backups/animations" "$CONFIG_DIR/anims"
rm -rf "$BASE/tgbot"
cp -a "$REPO/tgbot" "$BASE/tgbot"
install -m 0755 "$REPO/gif2klipper.py" "$CONVERTER"

# 4. Configuration.
if [[ -f "$BASE/config.yaml" ]]; then
  log "config.yaml already exists, keeping it"
else
  [[ -n "$TOKEN" ]] || fail "no config.yaml found and no --token given"
  [[ -n "$CHAT" ]] || fail "no config.yaml found and no --chat given"
  log "writing $BASE/config.yaml"
  install -m 0600 /dev/null "$BASE/config.yaml"
  sed -e "s|PUT-YOUR-BOT-TOKEN-HERE|$TOKEN|" \
      -e "s|^allowed_user_ids: .*|allowed_user_ids: [$CHAT]|" \
      -e "s|^notify_chat: .*|notify_chat: $CHAT|" \
      "$REPO/config.example.yaml" > "$BASE/config.yaml"
  chmod 600 "$BASE/config.yaml"
fi

# 5. Klipper include for generated animations.
PRINTER_CFG="$CONFIG_DIR/printer.cfg"
if [[ -f "$PRINTER_CFG" ]]; then
  if grep -q 'include anims/\*\.cfg' "$PRINTER_CFG"; then
    log "printer.cfg already includes anims/*.cfg"
  else
    log "adding [include anims/*.cfg] to printer.cfg (backup: printer.cfg.bak_$STAMP)"
    cp -a "$PRINTER_CFG" "$PRINTER_CFG.bak_$STAMP"
    printf '\n# tgbot animation include\n[include anims/*.cfg]\n' >> "$PRINTER_CFG"
  fi
else
  log "WARNING: $PRINTER_CFG not found, add [include anims/*.cfg] manually"
fi

# 6. Tests.
if [[ "$SKIP_TESTS" == "1" ]]; then
  log "tests skipped"
else
  log "running tests"
  ( cd "$REPO" && PYTHONPATH="$REPO" "$VENV/bin/python" -m pytest tests -q ) \
    || fail "tests failed (use --skip-tests to bypass)"
fi

# 7. Service.
if [[ "$NO_SERVICE" == "1" ]]; then
  log "service installation skipped"
else
  log "installing systemd unit $SERVICE.service"
  touch "$LOG_FILE"
  chmod 640 "$LOG_FILE"
  install -m 0644 "$REPO/systemd/tg2printer.service" "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null
  systemctl restart "$SERVICE"
  sleep 2
  if systemctl is-active --quiet "$SERVICE"; then
    log "service is running"
  else
    log "WARNING: service is not active. Check: journalctl -u $SERVICE -n 50"
  fi
fi

cat <<'WARN'
[install] done
[install] WARNING: audit blockers P0-1 and P0-3 are still open in the code.
[install]   P0-1: any GIF/photo/document raises AttributeError (anims.start_session missing)
[install]   P0-3: the SQLite error journal leaks file descriptors
[install] See docs/AUDIT.md before using this bot in production.
WARN
