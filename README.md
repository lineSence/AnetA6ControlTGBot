# AnetA6ControlTGBot

Telegram-бот для удалённого управления 3D-принтером **Anet A6** под управлением **Klipper** + **Moonraker**.
Бот работает на хосте принтера как systemd-сервис `tg2printer`.

> ## ⛔ Статус проекта
>
> Код версии **2.5.7** прошёл аудит. Найдены **3 блокера**. Ставить эту версию на принтер нельзя.
> Полный отчёт: [docs/AUDIT.md](docs/AUDIT.md). План исправления: [docs/ROADMAP.md](docs/ROADMAP.md).

## Возможности

- **Управление печатью.** Пауза, продолжение, отмена, аварийный стоп (`M112`).
- **Мониторинг.** Живое сообщение с прогрессом, температурами и оставшимся временем. Обновление приходит по WebSocket от Moonraker.
- **Камера.** Снимок с веб-камеры по запросу и при смене состояния печати.
- **Файлы.** Список G-code, запуск печати, загрузка нового файла прямо из чата.
- **Перемещение осей.** Кнопки XY и Z с настраиваемым шагом, парковка осей.
- **Макросы.** Список макросов Klipper с подтверждением опасных команд.
- **Анимации дисплея.** Конвертация GIF, фото и видео в `display_data` для экрана принтера. Предпросмотр, бекап, откат.
- **Журнал ошибок.** Ошибки Klipper пишутся в SQLite с номером. Просмотр из чата командой `/error <id>`.
- **Тихие часы.** Ночью бот не шлёт уведомления.

## Архитектура

| Модуль | Строк | Назначение |
| --- | --- | --- |
| `tgbot/main.py` | 44 | Точка входа. Создаёт бота, диспетчер, WebSocket-задачу и health-loop. |
| `tgbot/config.py` | 178 | Загрузка YAML, миграции схемы до версии 5, атомарная запись. |
| `tgbot/handlers.py` | 677 | Обработчики команд, медиа и inline-кнопок. Проверка доступа. |
| `tgbot/ui.py` | 143 | Сборка inline-клавиатур и текстов. |
| `tgbot/printer.py` | 142 | HTTP-клиент Moonraker: статус, файлы, макросы, загрузка G-code. |
| `tgbot/ws.py` | 239 | WebSocket Moonraker: живое сообщение, уведомления, health-loop. |
| `tgbot/safety.py` | 72 | Правила блокировки опасных действий во время печати. |
| `tgbot/anims.py` | 278 | Сессии анимаций, вызов конвертера, бекап и откат `anims/`. |
| `tgbot/errorlog.py` | 88 | Журнал ошибок в SQLite, чтение хвоста `klippy.log`. |
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
tests/                     тесты pytest
scripts/install.sh         установщик из репозитория
scripts/check.sh           локальная проверка перед релизом
systemd/tg2printer.service шаблон systemd-юнита
config.example.yaml        шаблон конфигурации
requirements.lock          закреплённые версии зависимостей
pyproject.toml             настройки pytest
ci/ci.yml                  пайплайн GitHub Actions (нужно перенести вручную)
CHANGELOG.md               история изменений и отличия от версии 2.5.7
docs/                      проектная документация
  AUDIT.md                 отчёт аудита версии 2.5.7
  ARCHITECTURE.md          устройство системы
  INSTALL.md               установка и обновление
  CONFIGURATION.md         описание всех ключей конфигурации
  ROADMAP.md               план исправления дефектов
```

## Требования

- Хост с Klipper и Moonraker (Raspberry Pi или аналог).
- Python 3.10 или новее, пакет `python3-venv`.
- `ffmpeg` для конвертации видео.
- Токен Telegram-бота от [@BotFather](https://t.me/BotFather).
- Ваш Telegram user id.

## Быстрый старт

```bash
git clone https://github.com/lineSence/AnetA6ControlTGBot.git
cd AnetA6ControlTGBot
sudo bash scripts/install.sh --token <BOT_TOKEN> --chat <TELEGRAM_ID>
```

Установщик создаёт окружение `/opt/tgbot`, копирует пакет в `/root/tgbot`, ставит юнит `tg2printer` и добавляет `[include anims/*.cfg]` в `printer.cfg`.
Перед установкой он бекапит предыдущую версию и гоняет тесты.
Подробности и ручная установка: [docs/INSTALL.md](docs/INSTALL.md).

## Конфигурация

Файл `/root/tgbot/config.yaml`, права `0600`. Шаблон — `config.example.yaml`.
Минимальный набор:

```yaml
config_version: 5
bot_token: "123456:ABC..."
allowed_user_ids: [123456789]
notify_chat: 123456789
moonraker: "http://127.0.0.1:7125"
```

Все ключи описаны в [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Разработка и тесты

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock
PYTHONPATH=. pytest tests -q
bash scripts/check.sh      # запускать на хосте принтера
```

Тест `tests/test_converter.py` ищет конвертер по жёсткому пути `/root/gif2klipper.py`. На машине разработчика скопируйте файл туда или пропустите этот тест.

## Непрерывная интеграция

Пайплайн лежит в `ci/ci.yml`: он гоняет `pytest` на Python 3.12 и `shellcheck` для скриптов.
GitHub Actions читает только каталог `.github/workflows/`, поэтому включите его вручную:

```bash
mkdir -p .github/workflows
git mv ci/ci.yml .github/workflows/ci.yml
git commit -m "ci: enable workflow" && git push
```

## Результаты аудита (кратко)

| Код | Дефект | Эффект |
| --- | --- | --- |
| P0-1 | Нет функции `anims.start_session`, но `handlers.py` её вызывает | Любая отправка GIF, фото или документа падает с `AttributeError`. Загрузка анимаций не работает. |
| P0-2 | `python: "python3"` — конвертер запускается системным Python без Pillow | Каждая конвертация завершается ошибкой. |
| P0-3 | `errorlog.py` не закрывает соединения SQLite | Утечка дескрипторов: 40 вызовов дают +79. Со временем `Too many open files`. |

Ещё 8 дефектов высокого риска и 20 среднего описаны в [docs/AUDIT.md](docs/AUDIT.md).

## Происхождение кода

Код извлечён из самораспаковывающегося установщика `install_tgbot_v2.5.7.sh` (3437 строк, 21 встроенный артефакт).
Модули перенесены без изменений логики. Все отклонения от оригинала перечислены в [CHANGELOG.md](CHANGELOG.md).

## Лицензия

Лицензия пока не выбрана. До её добавления код считается закрытым (все права принадлежат автору репозитория).
