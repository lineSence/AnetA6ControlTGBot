# Установка

> Предупреждение. В текущем коде есть три блокера из [аудита](AUDIT.md). Анимации не работают, бот течёт дескрипторами. Ставьте только для тестов и разработки.

## Требования

- Linux с systemd (проверено на Debian/Armbian для одноплатников).
- Python 3.10 или новее, пакет `python3-venv`.
- Работающие Klipper и Moonraker на `http://127.0.0.1:7125`.
- Дисплей ST7920 128x64 в `printer.cfg` (для анимаций).
- `ffmpeg` — опционально, только для видео.
- Токен бота от @BotFather и ваш Telegram user id.

## Быстрая установка

```bash
git clone https://github.com/lineSence/AnetA6ControlTGBot.git
cd AnetA6ControlTGBot
sudo bash scripts/install.sh --token 123456:ABC-DEF --chat 123456789
```

Что делает скрипт:

1. Бекапит текущую установку в `/root/tgbot/backups/upgrade_<дата>`.
2. Создаёт venv `/opt/tgbot` и ставит зависимости из `requirements.lock`.
3. Копирует пакет в `/root/tgbot/tgbot` и конвертер в `/root/gif2klipper.py`.
4. Пишет `config.yaml` с правами 0600, если его ещё нет.
5. Добавляет `[include anims/*.cfg]` в `printer.cfg` (с бекапом).
6. Гоняет тесты.
7. Ставит и запускает сервис `tg2printer`.

Полезные флаги:

| Флаг | Смысл |
| --- | --- |
| `--token <токен>` | Токен бота (или переменная `TG_BOT_TOKEN`) |
| `--chat <id>` | Разрешённый Telegram id (или `TG_CHAT_ID`) |
| `--skip-tests` | Не запускать тесты |
| `--no-service` | Не ставить systemd-юнит |

Токен лучше передавать через переменную, чтобы он не попал в `~/.bash_history`:

```bash
read -rs TG_BOT_TOKEN && export TG_BOT_TOKEN
sudo -E bash scripts/install.sh --chat 123456789
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
bash scripts/check.sh
```

В Telegram отправьте `/start`. Если бот молчит, проверьте `allowed_user_ids`.

## Обновление

```bash
git pull
sudo bash scripts/install.sh
sudo systemctl restart tg2printer
```

`config.yaml` при обновлении не перезаписывается.

## Удаление

```bash
sudo systemctl disable --now tg2printer
sudo rm /etc/systemd/system/tg2printer.service
sudo systemctl daemon-reload
sudo rm -rf /root/tgbot /opt/tgbot /root/gif2klipper.py
# и уберите строку [include anims/*.cfg] из printer.cfg
```

## Типичные проблемы

| Симптом | Причина |
| --- | --- |
| Сервис перезапускается каждые 5 с | Неверный токен или битый `config.yaml`; см. аудит P1-8 |
| Сервис не стартует с `ReadWritePaths` | Нет файла лога; см. аудит P1-1 |
| «Нужен Pillow» при конвертации | `python: python3` вместо venv; см. аудит P0-2 |
| Ошибка на любом GIF | Нет `anims.start_session`; см. аудит P0-1 |
