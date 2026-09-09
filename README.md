# AnetA6ControlTGBot

Telegram-бот для удалённого управления 3D-принтером **Anet A6** под управлением **Klipper** + **Moonraker**.
Бот работает на хосте принтера как systemd-сервис `tg2printer`.

> ## Статус проекта
>
> Версия **2.6.0**. Все дефекты аудита версии 2.5.7 исправлены: 3 блокера, 8 проблем высокого риска, 20 среднего.
> Отчёт аудита: [docs/AUDIT.md](docs/AUDIT.md). Таблица «дефект → исправление → тест»: [docs/FIXES.md](docs/FIXES.md).
> Код проходит 35 тестов. На реальном принтере версия ещё не запускалась, поэтому первый запуск делайте с `NO_SERVICE=1`.

## Возможности

- **Управление печатью.** Пауза, продолжение, отмена, аварийный стоп (`M112`).
- **Мониторинг.** Живое сообщение с прогрессом, температурами и оставшимся временем. Обновление приходит по WebSocket от Moonraker.
- **Камера.** Снимок с веб-камеры по запросу и при смене состояния печати.
- **Файлы.** Список G-code, запуск печати, загрузка нового файла прямо из чата.
- **Перемещение осей.** Кнопки XY и Z с настраиваемым шагом, парковка осей.
- **Макросы.** Список макросов Klipper с подтверждением опасных команд.
- **Анимации дисплея.** Конвертация GIF, фото и видео в `display_data` для экрана принтера. Предпросмотр, бекап, откат.
- **Журнал ошибок.** Ошибки Klipper пишутся в SQLite с номером. Просмотр из чата командой `/error <id>`.
- **Тихие часы.** Ночью бот молчит. Аварийные сообщения проходят всегда.

## Архитектура

| Модуль | Строк | Назначение |
| --- | --- | --- |
| `tgbot/main.py` | 45 | Точка входа. Бот, диспетчер, WebSocket-задача, health-loop и фоновая уборка состояния. |
| `tgbot/config.py` | 214 | Загрузка YAML, миграции схемы до версии 6, проверка значений, атомарная запись. |
| `tgbot/handlers.py` | 771 | Обработчики команд, медиа и inline-кнопок. Доступ, подтверждения с TTL, нарезка длинных ответов. |
| `tgbot/ui.py` | 150 | Сборка inline-клавиатур и текстов. |
| `tgbot/printer.py` | 165 | HTTP-клиент Moonraker: статус, файлы, макросы, загрузка G-code, API-ключ. |
| `tgbot/ws.py` | 254 | WebSocket Moonraker: живое сообщение, уведомления, backoff, health-loop. |
| `tgbot/safety.py` | 72 | Правила блокировки опасных действий во время печати. |
| `tgbot/anims.py` | 396 | Сессии анимаций, предпросмотр, вызов конвертера, бекап и откат `anims/`. |
| `tgbot/errorlog.py` | 107 | Журнал ошибок в SQLite с ретенцией, чтение хвоста `klippy.log`. |
| `tgbot/logsetup.py` | 22 | Ротация файлового лога и вывод в journald. |
| `gif2klipper.py` | 872 | Автономный конвертер изображений и видео в конфиг Klipper. |

Поток данных:

```text
Telegram  <--aiogram-->  handlers  -->  printer (HTTP)  -->  Moonraker  -->  Klipper
                            |
                            +-->  anims  -->  gif2klipper.py  -->  printer_data/config/anims/*.cfg
                            |
     ws.py  <--WebSocket--  Moonraker   (статус печати, ошибки, уведомления)
```

Подробное устройство: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Структура репозитория

```text
tgbot/                     пакет бота (10 модулей)
gif2klipper.py             конвертер анимаций (ставится в /root/gif2klipper.py)
tests/                     тесты pytest (test_core, test_fixes, test_safety_async, test_converter)
scripts/install.sh         установщик из репозитория
scripts/check.sh           локальная проверка перед релизом
systemd/tg2printer.service шаблон systemd-юнита
config.example.yaml        шаблон конфигурации (схема 6)
requirements.lock          закреплённые версии зависимостей
pyproject.toml             настройки pytest
ci/ci.yml                  пайплайн GitHub Actions (нужно перенести вручную)
CHANGELOG.md               история изменений
docs/                      проектная документация
  AUDIT.md                 отчёт аудита версии 2.5.7
  FIXES.md                 что и как исправлено в 2.6.0
  ARCHITECTURE.md          устройство системы
  INSTALL.md               установка и обновление
  CONFIGURATION.md         описание всех ключей конфигурации
  ROADMAP.md               план дальнейших работ
```

## Требования

- Хост с Klipper и Moonraker (Raspberry Pi или аналог).
- Python 3.9 или новее, пакет `python3-venv`. CI проверяет на 3.12.
- `ffmpeg` для конвертации видео.
- Токен Telegram-бота от [@BotFather](https://t.me/BotFather).
- Ваш Telegram user id.

## Быстрый старт

```bash
git clone https://github.com/lineSence/AnetA6ControlTGBot.git
cd AnetA6ControlTGBot
sudo TG_BOT_TOKEN="123456789:AA..." TG_CHAT_ID="123456789" bash scripts/install.sh
```

Установщик создаёт окружение `/opt/tgbot`, копирует пакет в `/root/tgbot`, ставит юнит `tg2printer` и добавляет `[include anims/*.cfg]` в `printer.cfg`.
Перед работой он делает резервную копию, а при любой ошибке возвращает прежнее состояние: код, конфиг, конвертер, `printer.cfg`, юнит и venv.

Полезные переменные: `NO_SERVICE=1` (не включать сервис), `SKIP_TESTS=1` (не гонять тесты), `UPGRADE_KEEP=5` (сколько резервных копий хранить).
Подробности и ручная установка: [docs/INSTALL.md](docs/INSTALL.md).

## Конфигурация

Файл `/root/tgbot/config.yaml`, права `0600`. Шаблон — `config.example.yaml`.
Минимальный набор:

```yaml
config_version: 6
bot_token: "123456789:AA..."
allowed_user_ids: [123456789]
notify_chat: 123456789
moonraker: "http://127.0.0.1:7125"
python: "/opt/tgbot/bin/python"
```

Все ключи описаны в [docs/CONFIGURATION.md](docs/CONFIGURATION.md). Конфиг старой схемы обновляется сам при старте.

## Разработка и тесты

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock
PYTHONPATH=. pytest tests -q
bash scripts/check.sh      # запускать на хосте принтера
```

Сейчас в наборе 35 тестов: 11 в `tests/test_core.py`, 20 в `tests/test_fixes.py`, 4 в `tests/test_safety_async.py`.
Тест `tests/test_converter.py` ищет конвертер по пути `/root/gif2klipper.py`. На машине разработчика скопируйте файл туда или пропустите этот тест.

## Непрерывная интеграция

Пайплайн лежит в `ci/ci.yml`: он гоняет `pytest` на Python 3.12 и `shellcheck` для скриптов.
GitHub Actions читает только каталог `.github/workflows/`, поэтому включите его вручную:

```bash
mkdir -p .github/workflows
git mv ci/ci.yml .github/workflows/ci.yml
git commit -m "ci: enable workflow" && git push
```

## Результаты аудита

Аудит версии 2.5.7 нашёл 3 блокера, 8 проблем высокого риска и 20 среднего. В версии 2.6.0 закрыты все.

| Код | Дефект | Что сделано |
| --- | --- | --- |
| P0-1 | `handlers.py` вызывал отсутствующую `anims.start_session`, любое медиа роняло обработчик | Функция и весь жизненный цикл сессии реализованы, формат и размер файла проверяются заранее |
| P0-2 | `python: "python3"` — конвертер запускался системным Python без Pillow | Миграция схемы 6 переводит путь на `/opt/tgbot/bin/python`, установщик проверяет импорты |
| P0-3 | `errorlog.py` не закрывал соединения SQLite (40 вызовов = +79 дескрипторов) | Все запросы через `contextlib.closing`, добавлены WAL, права `0600` и ретенция журнала |
| P1 (8 шт.) | Таймауты, тихие часы, лимит перезапусков, откат установщика, длинные сообщения | Разные таймауты запросов и загрузки, `critical_alerts_ignore_quiet_hours`, `StartLimitBurst`, полный откат, нарезка сообщений |
| P2 (20 шт.) | Версии, состояние без TTL, подтверждения кнопок, backoff, откат анимаций, чистка копий | Смотрите таблицу в [docs/FIXES.md](docs/FIXES.md) |

Отложены четыре задачи: превью как video-сообщение, HTTPS до Moonraker, роли пользователей, прогон на железе. Они в [docs/ROADMAP.md](docs/ROADMAP.md).

## Происхождение кода

Код извлечён из самораспаковывающегося установщика `install_tgbot_v2.5.7.sh` (3437 строк, 21 встроенный артефакт).
При импорте логика не менялась, все правки логики сделаны отдельно в версии 2.6.0. История: [CHANGELOG.md](CHANGELOG.md).

## Лицензия

Лицензия пока не выбрана. До её добавления код считается закрытым (все права принадлежат автору репозитория).
