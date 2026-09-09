#!/usr/bin/env bash
# AnetA6ControlTGBot: установщик из репозитория.
# Версия 2.6.0. Закрывает дефекты аудита P1-2, P1-3, P1-4, P2-2, P2-3, P2-5, P2-6.
set -Eeuo pipefail

VERSION="2.6.0"

BASE="${BASE:-/root/tgbot}"
VENV="${VENV:-/opt/tgbot}"
PKG="$BASE/tgbot"
CONFIG_DIR="${CONFIG_DIR:-/root/printer_data/config}"
CONVERTER="${CONVERTER:-/root/gif2klipper.py}"
SERVICE="${SERVICE:-tg2printer}"
UNIT="/etc/systemd/system/${SERVICE}.service"
PRINTER_CFG="$CONFIG_DIR/printer.cfg"
ANIMS_DIR="$CONFIG_DIR/anims"
BACKUP_ROOT="$BASE/backups"
UPGRADE_KEEP="${UPGRADE_KEEP:-5}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/upgrade_${STAMP}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="/dev/null"

SKIP_TESTS="${SKIP_TESTS:-0}"
NO_SERVICE="${NO_SERVICE:-0}"
PY_SYS="python3"

ROLLBACK_ARMED=0
VENV_MOVED=0
SERVICE_WAS_ACTIVE=0

ts() { date +%H:%M:%S; }
log()  { printf '[%s] %s\n' "$(ts)" "$*" | tee -a "$LOG_FILE"; }
warn() { printf '[%s] ВНИМАНИЕ: %s\n' "$(ts)" "$*" | tee -a "$LOG_FILE" >&2; }
die()  { printf '[%s] ОШИБКА: %s\n' "$(ts)" "$*" | tee -a "$LOG_FILE" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "нет команды $1"; }

# P1-3: set -E плюс ERR даёт точную строку сбоя.
on_err() { warn "сбой на строке $1: команда «$2»"; }
trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR

# P1-2: выход через die() тоже запускает откат, потому что ловим EXIT.
on_exit() {
  local code=$?
  trap - EXIT
  if (( code == 0 )); then
    finish_ok
  else
    warn "установка прервана, код $code"
    if (( ROLLBACK_ARMED )); then
      rollback || warn "откат прошёл с ошибками"
    fi
  fi
  exit "$code"
}
trap on_exit EXIT

finish_ok() {
  if (( VENV_MOVED )) && [[ -d "$BACKUP_DIR/venv" ]]; then
    rm -rf "$BACKUP_DIR/venv"
  fi
}

# P1-4: откат возвращает и код, и venv, и printer.cfg, и unit.
rollback() {
  log "откатываю изменения из $BACKUP_DIR"
  systemctl stop "$SERVICE" >/dev/null 2>&1 || true

  if [[ -d "$BACKUP_DIR/tgbot" ]]; then
    rm -rf "$PKG"
    cp -a "$BACKUP_DIR/tgbot" "$PKG"
    log "пакет tgbot восстановлен"
  fi
  if [[ -f "$BACKUP_DIR/config.yaml" ]]; then
    cp -a "$BACKUP_DIR/config.yaml" "$BASE/config.yaml"
  fi
  if [[ -f "$BACKUP_DIR/gif2klipper.py" ]]; then
    cp -a "$BACKUP_DIR/gif2klipper.py" "$CONVERTER"
  fi
  if [[ -f "$BACKUP_DIR/printer.cfg" ]]; then
    cp -a "$BACKUP_DIR/printer.cfg" "$PRINTER_CFG"
    log "printer.cfg восстановлен"
  fi
  if [[ -f "$BACKUP_DIR/unit.service" ]]; then
    cp -a "$BACKUP_DIR/unit.service" "$UNIT"
  else
    rm -f "$UNIT"
  fi
  if (( VENV_MOVED )) && [[ -d "$BACKUP_DIR/venv" ]]; then
    rm -rf "$VENV"
    mv "$BACKUP_DIR/venv" "$VENV"
    log "venv восстановлен"
  fi

  systemctl daemon-reload >/dev/null 2>&1 || true
  if (( SERVICE_WAS_ACTIVE )); then
    systemctl start "$SERVICE" >/dev/null 2>&1 || warn "сервис не поднялся после отката"
  fi
}

require_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "запустите установщик с правами root"
}

# P2-5: строгая проверка токена и chat id. Раньше проходило значение 1-2-3.
validate_inputs() {
  TG_BOT_TOKEN="${TG_BOT_TOKEN:-}"
  TG_CHAT_ID="${TG_CHAT_ID:-}"
  if [[ -n "$TG_BOT_TOKEN" && ! "$TG_BOT_TOKEN" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{20,}$ ]]; then
    die "TG_BOT_TOKEN не похож на токен Telegram"
  fi
  if [[ -n "$TG_CHAT_ID" && ! "$TG_CHAT_ID" =~ ^-?[0-9]{5,}$ ]]; then
    die "TG_CHAT_ID должен быть целым числом, например 123456789 или -1001234567890"
  fi
}

preflight() {
  need_cmd python3
  need_cmd systemctl
  need_cmd tee
  PY_SYS="$(command -v python3)"
  "$PY_SYS" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "нужен Python 3.9 или новее"

  local f
  for f in tgbot gif2klipper.py requirements.lock config.example.yaml "systemd/${SERVICE}.service"; do
    [[ -e "$SRC_DIR/$f" ]] || die "в репозитории нет $f"
  done

  local free_mb
  free_mb="$(df -Pm / 2>/dev/null | awk 'NR==2 {print $4}')"
  if [[ -n "${free_mb:-}" ]] && (( free_mb < 200 )); then
    warn "на диске свободно ${free_mb} МБ, этого может не хватить"
  fi
}

stop_service() {
  if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    SERVICE_WAS_ACTIVE=1
    log "останавливаю $SERVICE"
    systemctl stop "$SERVICE" || warn "не удалось остановить $SERVICE"
  fi
}

backup() {
  mkdir -p "$BACKUP_DIR"
  [[ -d "$PKG" ]] && cp -a "$PKG" "$BACKUP_DIR/tgbot"
  [[ -f "$BASE/config.yaml" ]] && cp -a "$BASE/config.yaml" "$BACKUP_DIR/config.yaml"
  [[ -f "$CONVERTER" ]] && cp -a "$CONVERTER" "$BACKUP_DIR/gif2klipper.py"
  [[ -f "$PRINTER_CFG" ]] && cp -a "$PRINTER_CFG" "$BACKUP_DIR/printer.cfg"
  [[ -f "$UNIT" ]] && cp -a "$UNIT" "$BACKUP_DIR/unit.service"
  ROLLBACK_ARMED=1
  log "резервная копия: $BACKUP_DIR"
  return 0
}

install_files() {
  log "копирую код в $PKG"
  mkdir -p "$BASE" "$ANIMS_DIR" "$BACKUP_ROOT"
  rm -rf "$PKG"
  cp -a "$SRC_DIR/tgbot" "$PKG"
  find "$PKG" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  cp -a "$SRC_DIR/gif2klipper.py" "$CONVERTER"
  cp -a "$SRC_DIR/requirements.lock" "$BASE/requirements.lock"
  if [[ -d "$SRC_DIR/tests" ]]; then
    rm -rf "$BASE/tests"
    cp -a "$SRC_DIR/tests" "$BASE/tests"
  fi
  if [[ -f "$SRC_DIR/pyproject.toml" ]]; then
    cp -a "$SRC_DIR/pyproject.toml" "$BASE/pyproject.toml"
  fi
  log "код скопирован"
}

# P0-2: бот всегда работает питоном из venv, где стоит Pillow.
setup_venv() {
  if [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
    log "использую существующий venv $VENV"
  else
    if [[ -d "$VENV" ]]; then
      mkdir -p "$BACKUP_DIR"
      rm -rf "$BACKUP_DIR/venv"
      mv "$VENV" "$BACKUP_DIR/venv"
      VENV_MOVED=1
      log "старый venv отложен в $BACKUP_DIR/venv"
    fi
    log "создаю venv $VENV"
    "$PY_SYS" -m venv "$VENV" || die "не удалось создать venv"
  fi

  "$VENV/bin/python" -m pip install --upgrade pip >>"$LOG_FILE" 2>&1 || warn "pip не обновился"
  log "ставлю зависимости из requirements.lock"
  "$VENV/bin/python" -m pip install -r "$SRC_DIR/requirements.lock" >>"$LOG_FILE" 2>&1 \
    || die "не удалось установить зависимости, смотрите $LOG_FILE"
}

verify_imports() {
  "$VENV/bin/python" - <<'PY' || die "проверка зависимостей не прошла"
import importlib, sys
bad = []
for name in ("yaml", "PIL", "aiogram", "aiohttp"):
    try:
        importlib.import_module(name)
    except Exception as exc:
        bad.append(f"{name}: {exc}")
if bad:
    print("нет модулей: " + "; ".join(bad))
    sys.exit(1)
print("зависимости на месте")
PY
  log "зависимости проверены"
}

# P2-4: конфиг пишет разборщик YAML, а не grep и sed.
write_config() {
  local target="$BASE/config.yaml"
  if [[ -f "$target" ]]; then
    log "config.yaml уже есть, оставляю как есть"
    return 0
  fi
  [[ -n "$TG_BOT_TOKEN" ]] || die "нужна переменная TG_BOT_TOKEN"
  [[ -n "$TG_CHAT_ID" ]] || die "нужна переменная TG_CHAT_ID"

  cp -a "$SRC_DIR/config.example.yaml" "$target"
  TOKEN="$TG_BOT_TOKEN" CHAT="$TG_CHAT_ID" "$VENV/bin/python" - "$target" <<'PY' || die "не удалось записать config.yaml"
import os, sys, yaml
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}
cfg["bot_token"] = os.environ["TOKEN"]
cfg["allowed_user_ids"] = [int(os.environ["CHAT"])]
cfg["notify_chat"] = int(os.environ["CHAT"])
with open(path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
PY
  chmod 600 "$target"
  log "config.yaml создан, права 600"
}

check_klipper_cfg() {
  "$PY_SYS" - "$1" <<'PY'
import configparser, sys
parser = configparser.ConfigParser(strict=False, interpolation=None)
with open(sys.argv[1], encoding="utf-8") as fh:
    parser.read_file(fh)
PY
}

# P2-6: include добавляем только если файл читается до и после правки.
patch_printer_cfg() {
  if [[ ! -f "$PRINTER_CFG" ]]; then
    warn "printer.cfg не найден ($PRINTER_CFG), include не добавлен"
    return 0
  fi
  if grep -qF '[include anims/*.cfg]' "$PRINTER_CFG"; then
    log "include анимаций уже есть"
    return 0
  fi

  local was_valid=0
  if check_klipper_cfg "$PRINTER_CFG" >/dev/null 2>&1; then
    was_valid=1
  else
    warn "printer.cfg не разбирается парсером до правки, проверку после правки пропускаю"
  fi

  printf '\n[include anims/*.cfg]\n' >> "$PRINTER_CFG"

  if (( was_valid )) && ! check_klipper_cfg "$PRINTER_CFG" >/dev/null 2>&1; then
    cp -a "$BACKUP_DIR/printer.cfg" "$PRINTER_CFG"
    die "printer.cfg сломался после правки, файл восстановлен"
  fi
  log "в printer.cfg добавлен [include anims/*.cfg]"
}

run_tests() {
  if [[ "$SKIP_TESTS" == "1" ]]; then
    log "тесты пропущены (SKIP_TESTS=1)"
    return 0
  fi
  if ! "$VENV/bin/python" -c 'import pytest' >/dev/null 2>&1; then
    warn "pytest не установлен, тесты пропущены"
    return 0
  fi
  log "запускаю тесты"
  ( cd "$BASE" && PYTHONPATH="$BASE" "$VENV/bin/python" -m pytest tests -q ) \
    || die "тесты не прошли"
  log "тесты прошли"
}

install_service() {
  if [[ "$NO_SERVICE" == "1" ]]; then
    log "сервис не ставлю (NO_SERVICE=1)"
    return 0
  fi
  cp -a "$SRC_DIR/systemd/${SERVICE}.service" "$UNIT"
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null 2>&1 || warn "не удалось включить автозапуск"
  systemctl restart "$SERVICE"
  sleep 3
  if ! systemctl is-active --quiet "$SERVICE"; then
    systemctl status "$SERVICE" --no-pager -l 2>&1 | tail -n 20 | tee -a "$LOG_FILE" >&2 || true
    die "сервис $SERVICE не запустился"
  fi
  log "сервис $SERVICE запущен"
}

# P2-3: старые копии обновлений больше не растут без предела.
rotate_backups() {
  local dirs=()
  mapfile -t dirs < <(find "$BACKUP_ROOT" -maxdepth 1 -type d -name 'upgrade_*' 2>/dev/null | sort)
  local total=${#dirs[@]}
  local keep="$UPGRADE_KEEP"
  (( total > keep )) || return 0
  local drop=$(( total - keep ))
  local i
  for (( i = 0; i < drop; i++ )); do
    log "удаляю старую копию ${dirs[$i]}"
    rm -rf "${dirs[$i]}"
  done
}

main() {
  require_root
  mkdir -p "$BASE" "$BACKUP_ROOT"
  LOG_FILE="/var/log/tg2printer_install.log"
  : > "$LOG_FILE" 2>/dev/null || LOG_FILE="/dev/null"

  log "AnetA6ControlTGBot installer $VERSION"
  log "источник: $SRC_DIR"
  validate_inputs
  preflight
  mkdir -p "$CONFIG_DIR" "$ANIMS_DIR"
  stop_service
  backup
  install_files
  setup_venv
  verify_imports
  write_config
  patch_printer_cfg
  run_tests
  install_service
  rotate_backups
  log "готово, версия $VERSION"
  log "лог установки: $LOG_FILE"
}

main "$@"
