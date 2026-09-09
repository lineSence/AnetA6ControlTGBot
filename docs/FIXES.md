# Как исправлены дефекты аудита

Документ отвечает на отчёт [docs/AUDIT.md](AUDIT.md).
Каждая строка — один дефект, его исправление и тест, который стережёт регресс.

Версия кода с исправлениями: **2.6.0**. Схема конфига: **6**.

Статус аудита: 3 блокера исправлены, 8 проблем высокого риска исправлены, 20 проблем среднего риска исправлены, 4 задачи осознанно отложены.

## Блокеры

| Код | Дефект | Исправление | Тест |
| --- | --- | --- | --- |
| P0-1 | `handlers.py` вызывал `anims.start_session`, которой не существовало. Любой GIF ронял обработчик | В `anims.py` добавлены `start_session`, `new_session`, `close_session`, `purge_sessions`, `media_ref`. Файл скачивается в свой временный каталог, формат и размер проверяются до работы | `test_start_session_is_defined`, `test_start_session_downloads_file_and_opens_session`, `test_start_session_rejects_unsupported_format` |
| P0-2 | `python: "python3"` — конвертер запускался системным Python без Pillow | Миграция конфига до версии 6 меняет системный путь на `/opt/tgbot/bin/python`. Свой путь пользователя сохраняется. Установщик проверяет импорты `yaml`, `PIL`, `aiogram`, `aiohttp` | `test_migration_switches_to_venv_python`, `test_migration_keeps_custom_python` |
| P0-3 | `errorlog.py` не закрывал соединения SQLite: 40 вызовов давали +79 дескрипторов | Все запросы обёрнуты в `contextlib.closing`. Добавлены WAL, таймаут 10 с, права `0600` и ретенция `error_log_keep` | `test_errorlog_does_not_leak_descriptors`, `test_errorlog_history_is_bounded` |

## Высокий риск

| Код | Дефект | Исправление | Тест |
| --- | --- | --- | --- |
| P1-1 | `ReadWritePaths` для файла лога без префикса `-`: сервис не стартовал без файла | `ReadWritePaths=-/var/log/tg2printer.log` в `systemd/tg2printer.service` | проверка глазами |
| P1-2 | `fail()` и `exit 1` обходили `trap on_err ERR`, откат не срабатывал | Установщик ловит `EXIT`, поэтому любой выход с ненулевым кодом запускает `rollback` | `bash -n`, ручной прогон |
| P1-3 | Не было `set -E`: ошибка внутри функции не доходила до trap | `set -Eeuo pipefail` и `trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR` | `bash -n` |
| P1-4 | Откат не восстанавливал venv | При пересоздании старый venv переносится в каталог бекапа и возвращается при сбое. Откат также возвращает код, конфиг, конвертер, `printer.cfg` и unit | ручной прогон |
| P1-5 | Один `ClientTimeout(total=15)` использовался и для заливки G-code | Ключи `http_timeout` (15 с) и `upload_timeout` (300 с). `PrinterClient.from_config` берёт их из конфига | `test_printer_client_reads_timeouts_and_api_key` |
| P1-6 | `/error <id>` шлёт `details[-6000:]`, а Telegram режет на 4096 символах | `chunk_text` и `send_long` режут текст на части по 3800 символов без потери символов | `test_chunk_text_respects_telegram_limit`, `test_chunk_text_splits_one_huge_line` |
| P1-7 | Тихие часы глушили аварийные уведомления | `should_notify(cfg, critical=True)` и ключ `critical_alerts_ignore_quiet_hours` (по умолчанию `true`) | `test_critical_alerts_ignore_quiet_hours` |
| P1-8 | `Restart=always` без лимита: битый бот крутился вечно | `StartLimitIntervalSec=300` и `StartLimitBurst=5` в секции `[Unit]` | проверка глазами |

## Средний риск

| Дефект | Исправление | Тест |
| --- | --- | --- |
| Рассинхрон версий 2.5.7 / 2.5.5 / 2.5.0 | Одна версия `2.6.0` в `tgbot/__init__.py`, установщике и документации | проверка глазами |
| `cp -f "$0"` ломался при запуске через `curl \| bash` | Установщик больше не копирует себя. Код берётся из каталога репозитория | `bash -n` |
| Копии `backups/upgrade_*` росли без предела | `rotate_backups` держит `UPGRADE_KEEP` копий (по умолчанию 5) | ручной прогон |
| Токен читался через `grep -m1 \| sed` | Конфиг читается и пишется через `yaml.safe_load` и `yaml.safe_dump`, права `600` | `bash -n` |
| `[[ "$CHAT" =~ ^[0-9-]+$ ]]` пропускал `1-2-3` | Проверки `^-?[0-9]{5,}$` для chat id и `^[0-9]{6,}:[A-Za-z0-9_-]{20,}$` для токена | ручной прогон |
| `printf … >> printer.cfg` без проверки синтаксиса | Файл проверяется парсером до и после правки; при поломке восстанавливается из бекапа | ручной прогон |
| `p:do:` выполнялся без записи в `PENDING` | `set_pending` и `take_pending` сверяют тип, действие и возраст подтверждения | `test_pending_confirmation_expires` |
| `mc:run:` не сверял номер и дайджест макроса | Сверяются и подтверждение, и актуальный список макросов | `test_pending_confirmation_expires` |
| Не проверялся `msg.from_user is None` | `allowed()` требует пользователя; `cq.message is None` даёт ответ «Сообщение устарело» | проверка глазами |
| Словари `PENDING`, `RENAMING`, `SESSIONS`, `LOCKS` росли вечно | `purge_state` и фоновая `janitor_loop` каждые 300 с; TTL задаются конфигом | `test_purge_state_drops_stale_entries`, `test_background_cleanup_is_wired` |
| `read_klippy_tail` читал весь `klippy.log` через `readlines()` | Чтение через `deque(maxlen=…)`, память ограничена | `test_errorlog_history_is_bounded` |
| `errors.db` росла без ретенции | Ключ `error_log_keep` (500) и автообрезка старых записей при каждой записи | `test_errorlog_history_is_bounded` |
| WebSocket переподключался без задержки | Экспоненциальный backoff от 3 до 60 с с записью в лог | проверка глазами |
| Нет поддержки API-ключа Moonraker | Ключ `moonraker_api_key` и заголовок `X-Api-Key`; можно задать через `MOONRAKER_API_KEY` | `test_printer_client_reads_timeouts_and_api_key` |
| `rollback_anims` чистил каталог `anims/` целиком и удалял чужие файлы | Откат трогает только свои файлы (`is_managed_name`) и те, что есть в бекапе | `test_rollback_keeps_foreign_files` |
| `snapshot_anims` падал на двух снимках в одну секунду | Штамп с микросекундами плюс числовой суффикс при коллизии | `test_snapshot_names_never_collide` |
| Видео и фото принимались только как документ `.gif` | `media_ref` берёт animation, video, document и photo. Список форматов и лимит 20 МБ проверяются заранее | `test_start_session_rejects_unsupported_format` |
| `preview_base` и `clean_preview` не использовались, превью мусорило в `/tmp` | Предпросмотр пишется в свой временный каталог и удаляется в `finally` | `test_start_session_downloads_file_and_opens_session` |
| Таймаут конвертера 120 с против `animation_ffmpeg_timeout: 45` | Лимит считается от конфига: `timeout * 2 + 30`. При таймауте возвращается код 124 и понятный текст | `test_converter_timeout_follows_config` |
| `ui.cycle` падал с `ValueError` на чужом значении | Неизвестное значение даёт первый элемент, пустой список — текущее | `test_cycle_handles_unknown_value` |
| `tune_kb` шла `u:noop`, который никто не обрабатывал | Все пустые кнопки шлют `m:noop` | `test_tune_keyboard_uses_handled_noop` |
| Опасные макросы искались подстрокой: `PRESTART` попадал в опасные | Поиск по границам слова плюс список опасных префиксов | `test_dangerous_macro_matching` |
| Гонка: печать могла начаться между `require_idle` и рестартом Klipper | Конвертация идёт до снимка; перед рестартом состояние проверяется снова, иначе — откат | `test_animation_apply_rolls_back` |
| Неудачная конвертация создавала лишние снимки бекапа | Снимок делается только перед первой записью в `anims/` | `test_animation_apply_rolls_back` |
| Ошибка «cross-device link» при `os.replace` между файловыми системами | Фолбэк на `copy2` плюс `os.replace` внутри одной ФС | проверка глазами |

## Осознанно отложено

Это новые функции, а не дефекты. Они живут в [docs/ROADMAP.md](ROADMAP.md).

| Задача | Почему позже |
| --- | --- |
| Отправка видео превью как video-сообщения | Требует ffmpeg на хосте и больше времени конвертации. Сейчас превью — GIF или PNG |
| HTTPS и TLS до Moonraker | Нужен реверс-прокси и сертификаты на хосте. API-ключ уже поддерживается |
| Роли пользователей (админ и наблюдатель) | Меняет схему конфига и все проверки доступа |
| Прогон на живом принтере | Нужен доступ к железу Anet A6 с Klipper |

## Как проверено

- `python3 -m compileall tgbot tests` — без ошибок.
- 35 тестов прошли: `test_core.py` (11), `test_fixes.py` (20), `test_safety_async.py` (4).
- `bash -n scripts/install.sh` — синтаксис чистый.
- `tests/test_converter.py` требует `/root/gif2klipper.py` и Pillow, поэтому запускается только на хосте принтера или в CI.
- Установщик на реальном принтере не проверялся. Первый запуск делайте с `NO_SERVICE=1`, потом включайте сервис.
