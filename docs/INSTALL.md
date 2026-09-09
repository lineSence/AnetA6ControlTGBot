# Установка

Версия 2.6.0. Все дефекты [аудита](AUDIT.md) исправлены, смотрите [FIXES.md](FIXES.md).
На реальном принтере эта версия ещё не запускалась. Первый запуск делайте с `NO_SERVICE=1`.

## Требования

- Linux с systemd (проверено на Debian и Armbian для одноплатников).
- Python 3.9 или новее, пакет `python3-venv`. Установщик проверяет версию сам.
- Работающие Klipper и Moonraker на `http://127.0.0.1:7125`.
- Дисплей ST7920 128x64 в `printer.cfg` (для анимаций).
- `ffmpeg` — опционально, только для видео.
- Токен бота от @BotFather и ваш Telegram user id.
- Права root и около 200 МБ свободного места.

## Быстрая установка

```bash
git clone https://github.com/lineSence/AnetA6ControlTGBot.git
cd AnetA6ControlTGBot
sudo TG_BOT_TOKEN="123456789:AA..." TG_CHAT_ID="123456789" bash scripts/install.sh
```

Установщик читает только переменные окружения. Флагов `--token` и `--chat` больше нет.

Что делает скрипт:

1. Проверяет входные данные, версию Python, наличие файлов и свободное место.
2. Останавливает сервис и бекапит текущую установку в `/root/tgbot/backups/upgrade_<дата>`.
3. Копирует пакет в `/root/tgbot/tgbot` и конвертер в `/root/gif2klipper.py`.
4. Создаёт venv `/opt/tgbot`, ставит зависимости из `requirements.lock` и проверяет импорты `yaml`, `PIL`, `aiogram`, `aiohttp`.
5. Пишет `config.yaml` разборщиком YAML с правами `600`, если файла ещё нет.
6. Добавляет `[include anims/*.cfg]` в `printer.cfg` и проверяет файл до и после правки.
7. Гоняет тесты.
8. Ставит и запускает сервис `tg2printer`.
9. Удаляет старые резервные копии сверх `UPGRADE_KEEP`.

Любая ошибка на шагах 2–8 запускает откат: возвращаются код, `config.yaml`, конвертер, `printer.cfg`, юнит и прежний venv.
Лог установки: `/var/log/tg2printer_install.log`.

## Переменные окружения

| Переменная | По умолчанию | Смысл |
| --- | --- | --- |
| `TG_BOT_TOKEN` | — | Токен бота. Проверка: `^[0-9]{6,}:[A-Za-z0-9_-]{20,}$` |
| `TG_CHAT_ID` | — | Telegram id. Проверка: `^-?[0-9]{5,}$` |
| `SKIP_TESTS` | `0` | `1` — не запускать тесты |
| `NO_SERVICE` | `0` | `1` — не ставить и не запускать юнит |
| `UPGRADE_KEEP` | `5` | Сколько копий `backups/upgrade_*` хранить |
| `BASE` | `/root/tgbot` | Каталог установки |
| `VENV` | `/opt/tgbot` | Каталог venv |
| `CONFIG_DIR` | `/root/printer_data/config` | Конфиги Klipper |
| `CONVERTER` | `/root/gif2klipper.py` | Куда положить конвертер |
| `SERVICE` | `tg2printer` | Имя systemd-сервиса |

Токен лучше вводить с клавиатуры, чтобы он не попал в `~/.bash_history`:

```bash
read -rs TG_BOT_TOKEN && export TG_BOT_TOKEN
export TG_CHAT_ID=123456789
sudo -E bash scripts/install.sh
```

Первый запуск без включения сервиса:

```bash
sudo -E NO_SERVICE=1 bash scripts/install.sh
/opt/tgbot/bin/python -m tgbot.main    # ручной запуск, Ctrl+C для остановки
```

## Установка вручную

```bash
# 1. venv и зависимости
python3 -m venv /opt/tgbot
/opt/tgbot/bin/python -m pip install -U pip
/opt/tgbot/bin/python -m pip install -r requirements.lock

# 2. код
mkdir -p /root/tgbot
cp -a tgbot /root/tgbot/tgbot
install -m 0755 gif2klipper.py /root/gif2klipper.py
mkdir -p /root/printer_data/config/anims /root/tgbot/backups/animations

# 3. конфиг
cp config.example.yaml /root/tgbot/config.yaml
chmod 600 /root/tgbot/config.yaml
$EDITOR /root/tgbot/config.yaml   # bot_token, allowed_user_ids, notify_chat

# 4. include в printer.cfg
cp /root/printer_data/config/printer.cfg /root/printer_data/config/printer.cfg.bak
printf '\n[include anims/*.cfg]\n' >> /root/printer_data/config/printer.cfg

# 5. сервис
install -m 0644 systemd/tg2printer.service /etc/systemd/system/tg2printer.service
touch /var/log/tg2printer.log
systemctl daemon-reload
systemctl enable --now tg2printer
```

## Проверка

```bash
systemctl status tg2printer
journalctl -u tg2printer -n 50 --no-pager
tail -n 50 /var/log/tg2printer.log
tail -n 50 /var/log/tg2printer_install.log
bash scripts/check.sh
```

В Telegram отправьте `/start`. Если бот молчит, проверьте `allowed_user_ids`.

## Обновление

```bash
git pull
sudo bash scripts/install.sh
```

`config.yaml` при обновлении не перезаписывается. Схема конфига обновляется автоматически при старте бота.

## Откат вручную

```bash
ls -1 /root/tgbot/backups/           # выберите нужный upgrade_<дата>
systemctl stop tg2printer
rm -rf /root/tgbot/tgbot
cp -a /root/tgbot/backups/upgrade_<дата>/tgbot /root/tgbot/tgbot
cp -a /root/tgbot/backups/upgrade_<дата>/config.yaml /root/tgbot/config.yaml
systemctl start tg2printer
```

## Удаление

```bash
sudo systemctl disable --now tg2printer
sudo rm /etc/systemd/system/tg2printer.service
sudo systemctl daemon-reload
sudo rm -rf /root/tgbot /opt/tgbot /root/gif2klipper.py
# и уберите строку [include anims/*.cfg] из printer.cfg
```

## Типичные проблемы

| Симптом | Причина и действие |
| --- | --- |
| Сервис пять раз перезапустился и встал в `failed` | Сработал лимит `StartLimitBurst=5`. Исправьте токен или конфиг, потом `systemctl reset-failed tg2printer` |
| `не удалось установить зависимости` | Нет сети или места. Смотрите `/var/log/tg2printer_install.log`; установщик уже откатил изменения |
| `printer.cfg сломался после правки` | Файл восстановлен из бекапа. Добавьте `[include anims/*.cfg]` вручную |
| Конвертация жалуется на Pillow | В `config.yaml` ключ `python` должен указывать на `/opt/tgbot/bin/python` |
| Анимация не применилась, бот просит остановить печать | Защита `require_idle`: менять анимации можно только на свободном принтере |
| Бот молчит ночью | Тихие часы `quiet_hours`. Аварии всё равно приходят, если `critical_alerts_ignore_quiet_hours: true` |
